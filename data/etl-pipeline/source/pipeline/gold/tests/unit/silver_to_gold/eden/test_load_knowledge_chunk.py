"""Unit tests for gold.src.eden.load_knowledge_chunk (file → Gold).

knowledge_chunk reads a NDJSON/JSON-array source file and upserts each record
into gold_eden.knowledge_chunk.  Vectors are NOT stored in Mongo (they live in
the dedicated vector DB); the loader writes only the chunk text + metadata plus
the vector-linkage scalars embeddingModel / embeddingVersion / vectorSyncedAt.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import pytest
from pymongo.errors import AutoReconnect, BulkWriteError, OperationFailure

from gold.tests.unit.silver_to_gold.conftest import (
    GOLD_TS,
    make_bulk_result,
    make_cfg,
    make_collection,
)

pytestmark = [pytest.mark.unit, pytest.mark.eden]

MOD = "gold.src.eden.load_knowledge_chunk"


@pytest.fixture
def mod():
    m = importlib.import_module(MOD)
    importlib.reload(m)
    m._shutdown = False
    return m


def _cfg(mod, tmp_path, **over):
    src = over.pop("source_path", tmp_path / "src.ndjson")
    return make_cfg(mod, source_path=src, **over)


def _record(**over):
    base = {
        "chunkId": "C-1",
        "sourceType": "sop",
        "sourceDocId": "D-1",
        "sourceTitle": "Title",
        "text": "hello world",
        "metadata": {"k": "v"},
    }
    base.update(over)
    return base


class TestFromArgs:
    def test_defaults(self, mod, monkeypatch):
        monkeypatch.delenv("TENANT_ID", raising=False)
        cfg = mod.LoaderConfig.from_args_and_env(["--tenant", "T1", "--source", "/tmp/x.json"])
        assert cfg.tenantid == "T1" and cfg.db_name == "gold_eden"
        assert cfg.batch_size == 200
        assert cfg.dry_run is False

    def test_flags_and_env(self, mod, monkeypatch):
        monkeypatch.setenv("TENANT_ID", "ENVT")
        cfg = mod.LoaderConfig.from_args_and_env(
            ["--dry-run", "--batch-size", "5", "--db", "gx",
             "--source", "/tmp/y.json"]
        )
        assert cfg.tenantid == "ENVT" and cfg.dry_run is True
        assert cfg.batch_size == 5 and cfg.db_name == "gx"

    def test_missing_source_raises(self, mod, monkeypatch):
        monkeypatch.delenv("SOURCE_PATH", raising=False)
        with pytest.raises(RuntimeError):
            mod.LoaderConfig.from_args_and_env(["--tenant", "T1"])


class TestMaskUri:
    def test_with_password_and_port(self, mod):
        masked = mod._mask_uri("postgresql://user:secret@host:5432/db")
        assert "secret" not in masked and "***" in masked and ":5432" in masked

    def test_with_password_no_port(self, mod):
        masked = mod._mask_uri("postgresql://user:secret@host/db")
        assert "secret" not in masked and "***" in masked

    def test_no_password_returned_asis(self, mod):
        assert mod._mask_uri("mongodb://host:27017") == "mongodb://host:27017"

    def test_exception_path_falls_back_to_regex(self, mod, monkeypatch):
        def boom(_):
            raise ValueError("bad uri")

        monkeypatch.setattr(mod, "urlparse", boom)
        masked = mod._mask_uri("postgresql://user:secret@host/db")
        assert "secret" not in masked and "***" in masked


class TestIterSourceRecords:
    def test_json_array(self, mod, tmp_path):
        f = tmp_path / "arr.json"
        f.write_text(json.dumps([_record(), _record(chunkId="C-2")]), encoding="utf-8")
        out = list(mod.iter_source_records(f))
        assert len(out) == 2 and out[1]["chunkId"] == "C-2"

    def test_ndjson(self, mod, tmp_path):
        f = tmp_path / "x.ndjson"
        f.write_text(
            json.dumps(_record()) + "\n\n" + json.dumps(_record(chunkId="C-2")),
            encoding="utf-8",
        )
        out = list(mod.iter_source_records(f))
        assert len(out) == 2  # blank line skipped

    def test_ndjson_decode_error(self, mod, tmp_path):
        f = tmp_path / "bad.ndjson"
        f.write_text("{not json}", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            list(mod.iter_source_records(f))


class TestShapeDoc:
    def test_full_record(self, mod):
        doc = mod.shape_doc(_record(), "DEMO-001", GOLD_TS)
        assert doc["tenantId"] == "DEMO-001"
        assert doc["chunkId"] == "C-1"
        assert doc["_grain"] == "knowledge_chunk_rag"
        assert doc["sourceType"] == "sop"
        # Vector is no longer stored in Mongo — only the linkage scalars.
        assert "contentEmbedding" not in doc
        assert doc["embeddingModel"] == mod.EMBEDDING_MODEL
        assert doc["embeddingVersion"] == mod.EMBEDDING_VERSION
        assert doc["vectorSyncedAt"] is None
        assert doc["metadata"] == {"k": "v"}
        assert doc["isDeleted"] is False

    def test_optional_fields_default(self, mod):
        rec = {"chunkId": "C-9", "sourceType": "faq"}
        doc = mod.shape_doc(rec, "T", GOLD_TS)
        assert doc["sourceDocId"] is None and doc["sourceTitle"] is None
        assert doc["text"] is None and doc["metadata"] == {}

    def test_embedding_model_from_env(self, mod, monkeypatch):
        # The vector-DB model/version are recorded from env config.
        monkeypatch.setattr(mod, "EMBEDDING_MODEL", "text-embedding-ada-002")
        monkeypatch.setattr(mod, "EMBEDDING_VERSION", "2")
        doc = mod.shape_doc(_record(), "T", GOLD_TS)
        assert doc["embeddingModel"] == "text-embedding-ada-002"
        assert doc["embeddingVersion"] == "2"


class TestBulkWrite:
    def test_success(self, mod, tmp_path):
        coll = make_collection(make_bulk_result(upserted=2, modified=1, matched=1))
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], _cfg(mod, tmp_path), stats)
        assert stats.upserted == 2 and stats.modified == 1
        assert stats.matched == 1 and stats.batches == 1

    def test_bulk_write_error_counts_failures(self, mod, tmp_path):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError(
            {"writeErrors": [{"e": 1}, {"e": 2}]}
        )
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], _cfg(mod, tmp_path), stats)
        assert stats.failed == 2 and stats.batches == 1

    def test_bulk_write_error_no_details(self, mod, tmp_path):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError({})
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], _cfg(mod, tmp_path), stats)
        assert stats.failed == 0 and stats.batches == 1

    def test_transient_then_success(self, mod, tmp_path):
        coll = make_collection()
        coll.bulk_write.side_effect = [AutoReconnect("x"), make_bulk_result(upserted=1)]
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], _cfg(mod, tmp_path, max_retries=3), stats)
        assert stats.upserted == 1

    def test_transient_exhausted_raises(self, mod, tmp_path):
        coll = make_collection()
        coll.bulk_write.side_effect = AutoReconnect("down")
        with pytest.raises(AutoReconnect):
            mod.bulk_write_with_retry(
                coll, [object()], _cfg(mod, tmp_path, max_retries=2), mod.WriteStats()
            )

    def test_operation_failure_raises(self, mod, tmp_path):
        coll = make_collection()
        coll.bulk_write.side_effect = OperationFailure("bad")
        with pytest.raises(OperationFailure):
            mod.bulk_write_with_retry(
                coll, [object()], _cfg(mod, tmp_path), mod.WriteStats()
            )


def _write_src(tmp_path, records, name="src.ndjson"):
    f = tmp_path / name
    f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return f


class TestRun:
    def test_source_not_found_returns_2(self, mod, tmp_path):
        cfg = _cfg(mod, tmp_path, source_path=tmp_path / "missing.ndjson")
        assert mod.run(cfg) == 2

    def test_dry_run_success(self, mod, tmp_path):
        f = _write_src(tmp_path, [_record(), _record(chunkId="C-2")])
        cfg = _cfg(mod, tmp_path, source_path=f, dry_run=True, batch_size=1)
        assert mod.run(cfg) == 0

    def test_wet_run_writes_and_verifies(self, mod, tmp_path, patch_mongo):
        f = _write_src(tmp_path, [_record()])
        coll = patch_mongo(mod)
        cfg = _cfg(mod, tmp_path, source_path=f, dry_run=False)
        assert mod.run(cfg) == 0
        coll.bulk_write.assert_called_once()
        coll.count_documents.assert_called_once()

    def test_json_decode_error_returns_3(self, mod, tmp_path):
        f = tmp_path / "bad.ndjson"
        f.write_text("{broken", encoding="utf-8")
        cfg = _cfg(mod, tmp_path, source_path=f, dry_run=True)
        assert mod.run(cfg) == 3

    def test_transient_during_flush_returns_3(self, mod, tmp_path, patch_mongo):
        f = _write_src(tmp_path, [_record()])
        coll = make_collection()
        coll.bulk_write.side_effect = AutoReconnect("lost")
        patch_mongo(mod, coll=coll)
        cfg = _cfg(mod, tmp_path, source_path=f, dry_run=False, max_retries=1)
        assert mod.run(cfg) == 3

    def test_bulk_failures_return_3(self, mod, tmp_path, patch_mongo):
        f = _write_src(tmp_path, [_record()])
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": 1}]})
        patch_mongo(mod, coll=coll)
        cfg = _cfg(mod, tmp_path, source_path=f, dry_run=False)
        assert mod.run(cfg) == 3

    def test_shutdown_breaks_loop(self, mod, tmp_path):
        f = _write_src(tmp_path, [_record(), _record(chunkId="C-2")])
        mod._shutdown = True
        cfg = _cfg(mod, tmp_path, source_path=f, dry_run=True)
        try:
            assert mod.run(cfg) == 0
        finally:
            mod._shutdown = False


class TestMainAndSignals:
    def test_main_success(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)
        monkeypatch.setattr(
            mod.LoaderConfig, "from_args_and_env",
            staticmethod(lambda argv=None: _cfg(mod, tmp_path)),
        )
        monkeypatch.setattr(mod, "run", lambda cfg: 0)
        assert mod.main([]) == 0

    def test_main_config_error_returns_2(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)

        def raise_cfg(argv=None):
            raise RuntimeError("missing source")

        monkeypatch.setattr(mod.LoaderConfig, "from_args_and_env", staticmethod(raise_cfg))
        assert mod.main([]) == 2

    def test_install_signal_handlers_and_handler(self, mod, monkeypatch):
        captured = {}
        monkeypatch.setattr(mod.signal, "signal", lambda sig, h: captured.__setitem__(sig, h))
        mod._install_signal_handlers()
        assert captured
        mod._shutdown = False
        list(captured.values())[0](2, None)
        assert mod._shutdown is True
        mod._shutdown = False

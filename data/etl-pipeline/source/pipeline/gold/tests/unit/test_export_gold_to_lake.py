"""Unit tests for the Gold → ADLS lake (Parquet) exporter (pure helpers)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gold.src.export.export_gold_to_lake import (  # noqa: E402
    normalize_doc, partition_path, _storage_options, EXPORTABLE,
)


class TestScope:
    def test_only_bpe_is_exportable(self):
        # QA / others must NOT be exported to the lake (serving-only).
        assert EXPORTABLE == ["bpe"]
        assert "qa_recommendation" not in EXPORTABLE


class TestNormalizeDoc:
    def test_drops_mongo_id(self):
        assert "_id" not in normalize_doc({"_id": "x", "answer_id": 1})

    def test_scalars_kept_as_is(self):
        d = normalize_doc({"answer_id": 1, "status": "open", "ok": True, "x": 1.5, "n": None})
        assert d == {"answer_id": 1, "status": "open", "ok": True, "x": 1.5, "n": None}

    def test_nested_dict_json_encoded(self):
        d = normalize_doc({"features": {"a": 1, "b": [2, 3]}})
        assert isinstance(d["features"], str)
        assert json.loads(d["features"]) == {"a": 1, "b": [2, 3]}

    def test_nested_list_json_encoded(self):
        d = normalize_doc({"bom_items": [{"id": 1}, {"id": 2}]})
        assert json.loads(d["bom_items"]) == [{"id": 1}, {"id": 2}]

    def test_datetime_in_nested_serialized(self):
        d = normalize_doc({"meta": {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}})
        assert "2026-01-01" in d["meta"]   # default=str handled it


class TestPartitionPath:
    def test_format(self):
        p = partition_path("/lake", "bpe", "2026-06-12", "tn1")
        assert p == "/lake/bpe/snapshot_date=2026-06-12/tenant=tn1.parquet"

    def test_trailing_slash_stripped(self):
        p = partition_path("abfs://gold@acct.dfs.core.windows.net/zone/", "qa_recommendation",
                           "2026-06-12", "tn9")
        assert p == ("abfs://gold@acct.dfs.core.windows.net/zone/qa_recommendation"
                     "/snapshot_date=2026-06-12/tenant=tn9.parquet")


class TestStorageOptions:
    def test_local_path_no_options(self):
        assert _storage_options("/tmp/lake") is None
        assert _storage_options("C:/tmp/lake") is None

    def test_blob_az_supplies_account(self, monkeypatch):
        # Azure Blob Storage (az:// → blob endpoint via adlfs)
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "key123")
        assert _storage_options("az://gold/zone") == {"account_name": "acct", "account_key": "key123"}

    def test_other_azure_protocols_supported(self, monkeypatch):
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "a")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "k")
        for url in ("abfs://c@a.dfs.core.windows.net/z", "wasbs://c@a.blob.core.windows.net/z"):
            assert _storage_options(url) == {"account_name": "a", "account_key": "k"}

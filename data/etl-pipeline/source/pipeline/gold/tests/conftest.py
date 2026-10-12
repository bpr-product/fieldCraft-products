"""Shared pytest fixtures for gold pipeline tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure repo root and shared are importable in all test processes
_ROOT = Path(__file__).resolve().parents[2]
_SHARED_SRC = str(_ROOT / "shared")

for _p in (str(_ROOT), _SHARED_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# pyflink stub — only when pyflink is genuinely absent
# ---------------------------------------------------------------------------
# apache-flink is deliberately NOT installed here (see
# src/datapipeline/requirements.txt: "the control plane never imports pyflink --
# only the ported job scripts"). But the Flink job modules import it at module
# scope, so any test touching gold_eden_stream / gold_user_permissions_stream /
# kafka_offsets died with ModuleNotFoundError before it could assert anything --
# 9 collection errors, leaving the router, the debounce, and the offsets
# dispatch untested.
#
# Guarding the import inside the job modules was rejected: those scripts run ON
# a Flink cluster, and a module that silently degrades to no-op stubs when
# pyflink is missing would start, look healthy and process nothing. Failing
# loudly there is correct. So the stub lives in test scaffolding instead.
#
# It is deliberately EXPLICIT rather than a blanket MagicMock: only the symbols
# the job modules actually import are defined, so if one of them disappears or
# is renamed upstream the tests raise AttributeError instead of quietly passing
# against a fake that auto-creates anything asked of it.
#
# If pyflink IS importable (a real Flink image, or CI that installs it), this
# block is skipped entirely and tests run against the real library.
def _install_pyflink_stub() -> None:
    try:
        import pyflink  # noqa: F401
        return                      # real pyflink present — leave it alone
    except ImportError:
        pass

    import types

    def _mod(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    class _Base:
        """Stand-in for the UDF base classes; tests subclass/instantiate these."""
        def open(self, *a, **k): pass
        def close(self, *a, **k): pass

    class _Types:
        @staticmethod
        def PICKLED_BYTE_ARRAY(): return "PICKLED_BYTE_ARRAY"
        @staticmethod
        def LONG(): return "LONG"
        @staticmethod
        def INT(): return "INT"
        @staticmethod
        def STRING(): return "STRING"

    class _ValueStateDescriptor:
        def __init__(self, name, type_info=None):
            self.name, self.type_info = name, type_info

    pyflink = _mod("pyflink")

    common = _mod("pyflink.common")
    common.Types = _Types
    common.WatermarkStrategy = type("WatermarkStrategy", (), {
        "for_bounded_out_of_orderness": staticmethod(lambda d: None)})
    common.Duration = type("Duration", (), {"of_seconds": staticmethod(lambda s: s)})
    pyflink.common = common

    serialization = _mod("pyflink.common.serialization")
    serialization.SimpleStringSchema = type("SimpleStringSchema", (), {})
    common.serialization = serialization

    restart = _mod("pyflink.common.restart_strategy")
    restart.RestartStrategies = type("RestartStrategies", (), {
        "fixed_delay_restart": staticmethod(lambda *a: None)})
    common.restart_strategy = restart

    datastream = _mod("pyflink.datastream")
    datastream.StreamExecutionEnvironment = type("StreamExecutionEnvironment", (), {})
    datastream.CheckpointingMode = type("CheckpointingMode", (), {
        "EXACTLY_ONCE": "EXACTLY_ONCE", "AT_LEAST_ONCE": "AT_LEAST_ONCE"})
    pyflink.datastream = datastream

    ckpt = _mod("pyflink.datastream.checkpoint_storage")
    ckpt.FileSystemCheckpointStorage = type("FileSystemCheckpointStorage", (), {})
    datastream.checkpoint_storage = ckpt

    functions = _mod("pyflink.datastream.functions")
    for _n in ("FlatMapFunction", "MapFunction", "KeyedProcessFunction",
               "RuntimeContext", "ProcessFunction"):
        setattr(functions, _n, type(_n, (_Base,), {}))
    datastream.functions = functions

    state = _mod("pyflink.datastream.state")
    state.ValueStateDescriptor = _ValueStateDescriptor
    datastream.state = state

    connectors = _mod("pyflink.datastream.connectors")
    datastream.connectors = connectors
    kafka = _mod("pyflink.datastream.connectors.kafka")
    for _n in ("KafkaSource", "KafkaOffsetsInitializer", "KafkaOffsetResetStrategy",
               "KafkaSink", "DeliveryGuarantee"):
        setattr(kafka, _n, type(_n, (), {}))
    connectors.kafka = kafka


_install_pyflink_stub()


@pytest.fixture(autouse=True)
def stub_secret_resolver(monkeypatch):
    """Patch secret_resolver so unit tests never hit Key Vault."""
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("SILVER_PG_DSN", "postgresql://localhost/fieldcraft_silver2")

"""Unit tests for configurable Gold Kafka starting-offsets (R3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import gold.src.flink.kafka_offsets as ko  # noqa: E402


class TestMode:
    def test_default_latest(self, monkeypatch):
        monkeypatch.delenv("KAFKA_STARTING_OFFSETS", raising=False)
        assert ko.starting_offsets_mode() == "latest"

    @pytest.mark.parametrize("val,expected", [
        ("earliest", "earliest"),
        ("EARLIEST", "earliest"),
        ("  committed ", "committed"),
        ("latest", "latest"),
        ("garbage", "latest"),   # unknown → safe default
        ("", "latest"),
    ])
    def test_normalization(self, monkeypatch, val, expected):
        monkeypatch.setenv("KAFKA_STARTING_OFFSETS", val)
        assert ko.starting_offsets_mode() == expected


class _FakeInit:
    calls: list = []

    @classmethod
    def earliest(cls):
        cls.calls.append("earliest"); return "E"

    @classmethod
    def latest(cls):
        cls.calls.append("latest"); return "L"

    @classmethod
    def committed_offsets(cls, strat):
        cls.calls.append(("committed", strat)); return "C"


class _FakeStrategy:
    LATEST = "LATEST"


@pytest.fixture
def _patched(monkeypatch):
    _FakeInit.calls = []
    monkeypatch.setattr(
        "pyflink.datastream.connectors.kafka.KafkaOffsetsInitializer", _FakeInit, raising=False
    )
    monkeypatch.setattr(
        "pyflink.datastream.connectors.kafka.KafkaOffsetResetStrategy", _FakeStrategy, raising=False
    )
    return _FakeInit


class TestDispatch:
    def test_latest(self, monkeypatch, _patched):
        monkeypatch.setenv("KAFKA_STARTING_OFFSETS", "latest")
        assert ko.make_offsets_initializer() == "L"
        assert _patched.calls == ["latest"]

    def test_earliest(self, monkeypatch, _patched):
        monkeypatch.setenv("KAFKA_STARTING_OFFSETS", "earliest")
        assert ko.make_offsets_initializer() == "E"
        assert _patched.calls == ["earliest"]

    def test_committed_falls_back_to_latest_reset(self, monkeypatch, _patched):
        monkeypatch.setenv("KAFKA_STARTING_OFFSETS", "committed")
        assert ko.make_offsets_initializer() == "C"
        assert _patched.calls == [("committed", "LATEST")]

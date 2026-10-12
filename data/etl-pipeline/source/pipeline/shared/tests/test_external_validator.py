import pytest
from unittest.mock import MagicMock

from shared.external_utils.validator import validate_record
from shared.dlq.errors import ErrorType


def _cfg(id_field="id"):
    cfg = MagicMock()
    cfg.id_field = id_field
    cfg.source_name = "test_src"
    cfg.source_entity = "order"
    return cfg


def test_valid_record_passes():
    ok, err_type, msg = validate_record({"id": "1", "amount": 100}, _cfg())
    assert ok is True
    assert err_type is None
    assert msg == ""


def test_none_record_fails():
    ok, err_type, msg = validate_record(None, _cfg())
    assert ok is False
    assert err_type == ErrorType.MALFORMED_JSON
    assert "not a dict" in msg


def test_non_dict_record_fails():
    ok, err_type, msg = validate_record("just a string", _cfg())
    assert ok is False
    assert err_type == ErrorType.MALFORMED_JSON


def test_empty_dict_fails():
    ok, err_type, msg = validate_record({}, _cfg())
    assert ok is False
    assert err_type == ErrorType.MALFORMED_JSON
    assert "empty" in msg


def test_all_null_values_fails():
    ok, err_type, msg = validate_record({"id": None, "amount": None}, _cfg())
    assert ok is False
    assert err_type == ErrorType.MALFORMED_JSON
    assert "all null" in msg


def test_id_field_missing_when_explicitly_configured():
    ok, err_type, msg = validate_record({"amount": 100}, _cfg(id_field="order_id"))
    assert ok is False
    assert err_type == ErrorType.NO_PK


def test_default_id_field_missing_is_ok():
    ok, err_type, msg = validate_record({"amount": 100}, _cfg(id_field="id"))
    assert ok is True


def test_oversized_record_fails():
    big = {"id": "1", "data": "x" * (2 * 1024 * 1024)}
    ok, err_type, msg = validate_record(big, _cfg())
    assert ok is False
    assert err_type == ErrorType.MALFORMED_JSON
    assert "too large" in msg


def test_non_default_id_field_present_and_valid_passes():
    ok, err_type, msg = validate_record({"order_id": "ORD-001", "amount": 100}, _cfg(id_field="order_id"))
    assert ok is True
    assert err_type is None


def test_whitespace_only_id_field_fails():
    ok, err_type, msg = validate_record({"order_id": "   ", "amount": 100}, _cfg(id_field="order_id"))
    assert ok is False
    assert err_type == ErrorType.NO_PK


def test_partial_null_values_passes():
    ok, err_type, msg = validate_record({"id": None, "amount": 100}, _cfg())
    assert ok is True

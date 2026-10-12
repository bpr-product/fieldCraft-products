import json
from unittest.mock import MagicMock, patch
from external.config.schema import ConnectorConfig
from external.connectors.base import BaseConnector


def _cfg() -> ConnectorConfig:
    return ConnectorConfig(
        source_name="test_src",
        source_entity="order",
        connector_type="rest_api",
        id_field="order_id",
    )


class _FakeConnector(BaseConnector):
    def __init__(self, config, records):
        super().__init__(config)
        self._records = records

    def fetch(self):
        yield from self._records


def test_bad_record_goes_to_dlq_good_record_goes_to_silver():
    cfg = _cfg()
    records = [
        None,                                          # invalid — not a dict
        {"order_id": "ORD-001", "amount": 150.0},     # valid
        {},                                            # invalid — empty
        {"order_id": "ORD-002", "amount": 200.0},     # valid
    ]
    conn = _FakeConnector(cfg, records)

    silver_written = []
    dlq_written = []

    with patch("external.writers.kafka_writer.KafkaWriter") as MockKafka, \
         patch("external.writers.iceberg_writer.IcebergWriter"), \
         patch("external.writers.silver_writer.SilverDirectWriter") as MockSilver:

        mock_silver = MagicMock()
        mock_silver.write.side_effect = lambda b: silver_written.extend(b)
        mock_silver.write_dlq.side_effect = lambda b: dlq_written.extend(b)
        MockSilver.return_value = mock_silver
        MockKafka.return_value.write = MagicMock()

        result = conn.run()

    assert result["records_processed"] == 2
    assert result["dlq_count"] == 2
    assert mock_silver.write_dlq.called
    assert len(dlq_written) == 2
    assert all(d["error_type"] in ("MALFORMED_JSON", "NO_PK", "UNKNOWN") for d in dlq_written)


def test_all_valid_records_no_dlq_write():
    cfg = _cfg()
    records = [
        {"order_id": "ORD-001", "amount": 100},
        {"order_id": "ORD-002", "amount": 200},
    ]
    conn = _FakeConnector(cfg, records)

    with patch("external.writers.kafka_writer.KafkaWriter"), \
         patch("external.writers.iceberg_writer.IcebergWriter"), \
         patch("external.writers.silver_writer.SilverDirectWriter") as MockSilver:

        mock_silver = MagicMock()
        MockSilver.return_value = mock_silver

        result = conn.run()

    assert result["records_processed"] == 2
    assert result["dlq_count"] == 0
    mock_silver.write_dlq.assert_not_called()


def test_all_invalid_records_nothing_to_silver():
    cfg = _cfg()
    records = [None, {}, {"a": None, "b": None}]
    conn = _FakeConnector(cfg, records)

    with patch("external.writers.kafka_writer.KafkaWriter"), \
         patch("external.writers.iceberg_writer.IcebergWriter"), \
         patch("external.writers.silver_writer.SilverDirectWriter") as MockSilver:

        mock_silver = MagicMock()
        MockSilver.return_value = mock_silver

        result = conn.run()

    assert result["records_processed"] == 0
    assert result["dlq_count"] == 3
    mock_silver.write.assert_not_called()

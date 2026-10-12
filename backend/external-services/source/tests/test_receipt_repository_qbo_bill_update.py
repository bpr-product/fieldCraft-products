from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from source.dal.ReceiptRepository import ReceiptRepository


def test_update_receipt_from_qbo_persists_prepared_two_way_bill_items():
    repo = ReceiptRepository()

    current_additional_info = (
        '[{"total":"371.69","receiptInfo":"['
        '{\\"description\\":\\"Aluminum Drip Edge 1.85 Linen\\",'
        '\\"quantity\\":31,\\"price\\":\\"11.99\\",\\"totalPrice\\":371.69}'
        ']"}]'
    )
    incoming_additional_info = (
        '[{"total":"417.69","receiptInfo":"['
        '{\\"description\\":\\"Aluminum Drip Edge 1.85 Linen\\",'
        '\\"quantity\\":31,\\"price\\":\\"11.99\\",\\"totalPrice\\":371.69},'
        '{\\"description\\":\\"materials now\\",\\"totalPrice\\":46.0,'
        '\\"_quickbooksImported\\":true,\\"_quickbooksLineId\\":\\"2\\"}'
        ']"}]'
    )

    row = SimpleNamespace(
        id=212,
        created_by="8aef1564-cd04-4b85-9633-49ad6f4f3231",
        receipt_additional_info=current_additional_info,
        description="Other",
        file_name="QBO Bill ll-88855.json",
        invoice_date=None,
        invoice_number="ll-88855",
        name="VendorBill",
        order_date=None,
        order_number="ll-88855",
        status="Approve",
        vendor_id="76",
        updated_at=None,
        chart_of_account_id=3,
        customer_name="Buma P",
        project_id=2206,
        stock_reference=None,
        paid_date=None,
        due_date=None,
    )

    session = MagicMock()
    session.get.return_value = row

    @contextmanager
    def fake_tenant_session_scope(tenant_id):
        assert tenant_id == "tn59395620"
        yield session

    repo._normalize_receipt_row = lambda saved_row: {
        "id": saved_row.id,
        "receipt_additional_info": saved_row.receipt_additional_info,
    }

    payload = {
        "receipt_additional_info": incoming_additional_info,
        "updated_at": None,
    }

    with patch("source.dal.ReceiptRepository.tenant_session_scope", fake_tenant_session_scope):
        result = repo.update_receipt_from_qbo(
            "tn59395620",
            receipt_id=212,
            payload=payload,
        )

    assert row.receipt_additional_info == incoming_additional_info
    assert result["receipt_additional_info"] == incoming_additional_info
    session.commit.assert_called_once()

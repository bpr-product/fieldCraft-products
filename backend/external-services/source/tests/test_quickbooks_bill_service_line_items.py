import json

from source.quickbooks.QuickBooksBillService import QuickBooksBillService


class FakeReceiptRepository:
    def __init__(self, receipt_additional_info, created_by="FIELDCRAFT", mapping_reason=None, receipt_name=None):
        self.receipt_additional_info = receipt_additional_info
        self.created_by = created_by
        self.mapping_reason = mapping_reason
        self.receipt_name = receipt_name

    def get_current_receipt_by_id(self, tenant_id, receipt_id):
        return {
            "id": receipt_id,
            "receipt_additional_info": self.receipt_additional_info,
            "created_by": self.created_by,
            "name": self.receipt_name,
        }

    def get_current_mapping_by_receipt_id(self, tenant_id, service_id, receipt_id):
        return {
            "receipt_id": receipt_id,
            "service_id": service_id,
            "reason": self.mapping_reason,
        }

    def parse_additional_info(self, raw):
        try:
            data = raw if isinstance(raw, (dict, list)) else json.loads(str(raw))
        except Exception:
            return {}
        if isinstance(data, list):
            return data[0] if data else {}
        return data if isinstance(data, dict) else {}


def make_service(receipt_items, created_by="FIELDCRAFT", mapping_reason=None, receipt_name=None):
    service = QuickBooksBillService()
    additional_info = json.dumps([{"receiptInfo": json.dumps(receipt_items)}])
    service.receipt_repo = FakeReceiptRepository(
        additional_info,
        created_by=created_by,
        mapping_reason=mapping_reason,
        receipt_name=receipt_name,
    )
    return service


def managed_reason(*, item_count, state, amount=None, description=None):
    current = {"state": state}
    if amount is not None:
        current["amount"] = amount
    if description is not None:
        current["description"] = description
    return json.dumps({
        "message": "Bill updated to match the latest changes from QuickBooks.",
        "fieldcraftManagedBillLine": {
            "amount": 362.45,
            "descriptionFingerprint": "baseline",
            "itemCount": item_count,
            "id": "1",
            "qboCurrent": current,
        },
    })


def test_get_bill_line_items_parses_qbo_account_lines_and_skips_zero_amount():
    service = make_service([
        {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": "Roof material",
            "Amount": "125.50",
        },
        {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": "Zero line",
            "Amount": "0",
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [{
        "description": "Roof material",
        "productCode": None,
        "quantity": None,
        "unitPrice": None,
        "amount": 125.50,
        "lineKind": "category",
    }]


def test_get_bill_line_items_includes_negative_qbo_account_amounts():
    service = make_service([
        {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": "Discount",
            "Amount": "-25.00",
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert len(rows) == 1
    assert rows[0]["description"] == "Discount"
    assert rows[0]["amount"] == -25.00
    assert rows[0]["lineKind"] == "category"


def test_get_bill_line_items_parses_qbo_item_lines_with_item_ref():
    service = make_service([
        {
            "DetailType": "ItemBasedExpenseLineDetail",
            "Description": "Starter shingles",
            "Amount": "300",
            "ItemBasedExpenseLineDetail": {
                "ItemRef": {"value": "I-1", "name": "SHINGLE"},
                "Qty": "3",
                "UnitPrice": "100",
            },
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [{
        "description": "Starter shingles",
        "productCode": "SHINGLE",
        "quantity": 3.0,
        "unitPrice": 100.0,
        "amount": 300.0,
        "lineKind": "item",
    }]


def test_get_bill_line_items_parses_qbo_item_lines_without_item_ref():
    service = make_service([
        {
            "DetailType": "ItemBasedExpenseLineDetail",
            "Description": "Labor",
            "Amount": "75",
            "ItemBasedExpenseLineDetail": {
                "Qty": "1.5",
                "UnitPrice": "50",
            },
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [{
        "description": "Labor",
        "productCode": None,
        "quantity": 1.5,
        "unitPrice": 50.0,
        "amount": 75.0,
        "lineKind": "item",
    }]


def test_get_bill_line_items_parses_fieldcraft_total_price_and_computed_amount():
    service = make_service([
        {
            "description": "Permit",
            "productCode": "PERMIT",
            "quantity": "1",
            "price": "40",
            "totalPrice": "40",
        },
        {
            "description": "Fasteners",
            "quantity": "4",
            "price": "2.50",
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [
        {
            "description": "Permit",
            "productCode": "PERMIT",
            "quantity": 1.0,
            "unitPrice": 40.0,
            "amount": 40.0,
            "lineKind": "item",
        },
        {
            "description": "Fasteners",
            "productCode": None,
            "quantity": 4.0,
            "unitPrice": 2.50,
            "amount": 10.0,
            "lineKind": "item",
        },
    ]


def test_get_bill_line_items_supports_mixed_formats():
    service = make_service([
        {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": "Category line",
            "Amount": "50",
        },
        {
            "DetailType": "ItemBasedExpenseLineDetail",
            "Description": "Product line",
            "Amount": "20",
            "ItemBasedExpenseLineDetail": {"ItemRef": {"name": "ITEM"}},
        },
        {
            "description": "FIELDCRAFT line",
            "quantity": "2",
            "price": "5",
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert [row["description"] for row in rows] == ["Category line", "Product line", "FIELDCRAFT line"]
    assert [row["amount"] for row in rows] == [50.0, 20.0, 10.0]
    assert [row["lineKind"] for row in rows] == ["category", "item", "item"]


def test_get_bill_line_items_returns_empty_list_for_malformed_json():
    service = QuickBooksBillService()
    service.receipt_repo = FakeReceiptRepository("{not-valid-json")

    assert service.get_bill_line_items("tenant-1", 10) == []


def test_stock_debit_bill_line_items_use_buying_values_only():
    service = make_service([
        {
            "description": "Pipe",
            "code": "awse123",
            "quantity": 10,
            "unitCost": 3.78,
            "amount": 37.8,
            "sellPrice": 123,
            "sellAmount": 1230,
            "price": 999,
            "totalPrice": 9990,
        },
    ], receipt_name="StockDebitBill")

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [{
        "description": "Pipe",
        "productCode": "awse123",
        "quantity": 10.0,
        "unitPrice": 3.78,
        "amount": 37.8,
        "lineKind": "item",
    }]


def test_stock_credit_bill_line_items_use_buying_values_only():
    service = make_service([
        {
            "description": "Pipe",
            "code": "awse123",
            "quantity": 2,
            "unitCost": 95.35,
            "amount": 190.7,
            "sellPrice": 123,
            "sellAmount": 246,
            "price": 999,
            "totalPrice": 1998,
        },
    ], receipt_name="StockCreditBill")

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [{
        "description": "Pipe",
        "productCode": "awse123",
        "quantity": 2.0,
        "unitPrice": 95.35,
        "amount": 190.7,
        "lineKind": "item",
    }]


def test_stock_movement_line_items_fall_back_to_quantity_times_unit_cost():
    for receipt_name in ("StockDebitBill", "StockCreditBill"):
        service = make_service([
            {
                "description": "Pipe",
                "code": "awse123",
                "quantity": 2,
                "unitCost": 95.35,
                "sellPrice": 123,
                "sellAmount": 246,
            },
        ], receipt_name=receipt_name)

        assert service.get_bill_line_items("tenant-1", 10) == [{
            "description": "Pipe",
            "productCode": "awse123",
            "quantity": 2.0,
            "unitPrice": 95.35,
            "amount": 190.7,
            "lineKind": "item",
        }]


def test_stock_movement_line_items_do_not_fall_back_to_selling_values():
    service = make_service([
        {
            "description": "Pipe",
            "code": "awse123",
            "quantity": 2,
            "sellPrice": 123,
            "sellAmount": 246,
        },
    ], receipt_name="StockCreditBill")

    assert service.get_bill_line_items("tenant-1", 10) == []


def test_get_bill_line_items_supports_new_line_items_shape_with_unit_cost():
    service = QuickBooksBillService()
    additional_info = json.dumps([{
        "lineItems": json.dumps([
            {
                "materialName": "Roof Shingle",
                "description": "Black architectural bundle",
                "quantity": "3",
                "unitCost": "25.00",
            }
        ])
    }])
    service.receipt_repo = FakeReceiptRepository(additional_info)

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [{
        "description": "Roof Shingle - Black architectural bundle",
        "productCode": None,
        "quantity": 3.0,
        "unitPrice": 25.0,
        "amount": 75.0,
        "lineKind": "item",
    }]


def test_get_bill_line_items_moves_stock_quantity_suffix_into_quantity_columns():
    service = make_service([
        {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": "Material - 3-Tab Shingles closed x 500",
            "Amount": "60500",
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert rows == [{
        "description": "Material - 3-Tab Shingles closed",
        "productCode": None,
        "quantity": 500.0,
        "unitPrice": 121.0,
        "amount": 60500.0,
        "lineKind": "item",
    }]

def test_get_bill_line_items_uses_qbo_detail_type_for_imported_account_lines_with_quantity_suffix():
    service = make_service([
        {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": "Prepay Fuel x 1; Power Bars x 1",
            "Amount": "20",
        },
    ], created_by="QuickBooks")

    rows = service.get_bill_line_items("tenant-1", 10)

    assert len(rows) == 1
    assert rows[0]["lineKind"] == "category"
    assert rows[0]["quantity"] == 1.0
    assert rows[0]["unitPrice"] == 20.0
    assert rows[0]["amount"] == 20.0


def test_fieldcraft_origin_multi_item_summary_does_not_use_last_item_quantity_for_whole_line():
    service = make_service([
        {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Description": "Material A x 12; Material B x 15",
            "Amount": "545.13",
        },
    ], created_by="FIELDCRAFT")

    rows = service.get_bill_line_items("tenant-1", 10)

    assert len(rows) == 1
    assert rows[0]["description"] == "Material A x 12; Material B x 15"
    assert rows[0]["quantity"] is None
    assert rows[0]["unitPrice"] is None
    assert rows[0]["amount"] == 545.13

def test_modified_managed_summary_does_not_replace_detailed_fieldcraft_view():
    service = make_service([
        {
            "description": 'Tool Metal Bender 3-1/2" Blade #85028',
            "quantity": 11,
            "price": 32.95,
            "totalPrice": 362.45,
        },
        {
            "description": "hi materials! x 2",
            "totalPrice": 5555.0,
            "_quickbooksImported": True,
            "_quickbooksLineId": "2",
        },
        {
            "description": "COGS Supplies and Materials",
            "totalPrice": 33.0,
            "_quickbooksImported": True,
            "_quickbooksLineId": "3",
        },
    ], mapping_reason=managed_reason(
        item_count=1,
        state="modified",
        amount=420.0,
        description='Tool Metal Bender 3-1/2" Blade #85028 x 11 changed',
    ))

    rows = service.get_bill_line_items("tenant-1", 10, "6")

    assert [row["description"] for row in rows] == [
        'Tool Metal Bender 3-1/2" Blade #85028',
        "hi materials! x 2",
        "COGS Supplies and Materials",
    ]
    assert [row["amount"] for row in rows] == [362.45, 5555.0, 33.0]
    assert rows[0]["quantity"] == 11.0
    assert rows[0]["unitPrice"] == 32.95


def test_missing_managed_summary_does_not_hide_preserved_fieldcraft_rows():
    service = make_service([
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
        {
            "description": "Replacement line from QuickBooks",
            "totalPrice": 400.0,
            "_quickbooksImported": True,
            "_quickbooksLineId": "9",
        },
    ], mapping_reason=managed_reason(item_count=2, state="missing"))

    rows = service.get_bill_line_items("tenant-1", 10, "6")

    assert [row["description"] for row in rows] == [
        "Material A",
        "Material B",
        "Replacement line from QuickBooks",
    ]
    assert [row["amount"] for row in rows] == [200.0, 300.0, 400.0]


def test_unchanged_managed_summary_keeps_detailed_fieldcraft_material_view():
    service = make_service([
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ], mapping_reason=managed_reason(
        item_count=2,
        state="unchanged",
        amount=500.0,
        description="Material A x 2; Material B x 3",
    ))

    rows = service.get_bill_line_items("tenant-1", 10, "6")

    assert [row["description"] for row in rows] == ["Material A", "Material B"]
    assert [row["amount"] for row in rows] == [200.0, 300.0]


def test_legacy_synthetic_adjustment_is_not_shown():
    service = make_service([
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {
            "description": "QuickBooks adjustment to FIELDCRAFT bill",
            "quantity": 1,
            "price": 50,
            "totalPrice": 50,
            "_quickbooksImported": True,
            "_quickbooksSynthetic": True,
        },
    ])

    rows = service.get_bill_line_items("tenant-1", 10)

    assert len(rows) == 1
    assert rows[0]["description"] == "Material A"


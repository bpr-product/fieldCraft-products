import json
from unittest.mock import MagicMock

import pytest

from source.quickbooks.QuickBooksBillPaymentExportService import (
    QuickBooksBillPaymentExportService,
)
from source.quickbooks.QuickBooksBillPaymentImportService import (
    QuickBooksBillPaymentImportService,
)
from source.quickbooks.QuickBooksContractorVendorExportService import (
    QuickBooksContractorVendorExportService,
)
from source.quickbooks.QuickBooksEmployeeExportService import (
    QuickBooksEmployeeExportService,
)


def test_employee_mapping_rejects_explicit_other_realm_but_accepts_legacy_blank():
    service = QuickBooksEmployeeExportService()

    service._assert_mapping_realm({"external_account_id": ""}, "realm-1")
    with pytest.raises(ValueError, match="different QuickBooks company"):
        service._assert_mapping_realm(
            {"external_account_id": "realm-2"}, "realm-1"
        )


def test_contractor_duplicate_lookup_is_realm_aware():
    service = QuickBooksContractorVendorExportService()
    service.employee_repo = MagicMock()
    service.employee_repo.get_by_external_vendor_id.return_value = None
    service.vendor_map_repo = MagicMock()
    service.vendor_map_repo.get_by_external_id.return_value = None

    service._assert_external_vendor_is_not_mapped_elsewhere(
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        qbo_vendor_id="vendor-1",
        user_account_id=42,
    )

    service.employee_repo.get_by_external_vendor_id.assert_called_once_with(
        "tn1",
        "6",
        "vendor-1",
        external_account_id="realm-1",
    )


def test_bill_payment_export_preserves_account_metadata_and_records_realm():
    service = QuickBooksBillPaymentExportService()
    mapping = {
        "additional_info": json.dumps(
            {"qboAccountId": "bank-1", "custom": "preserve-me"}
        )
    }
    context = {
        "qbo_payment_account_id": "bank-1",
        "qbo_payment_account_name": "Checking",
        "qbo_payment_account_type": "Bank",
        "qbo_payment_account_detail_type": "Checking",
        "pay_type": "Check",
    }

    result = json.loads(
        service._build_payment_mapping_info(
            mapping=mapping,
            context=context,
            realm_id="realm-1",
        )
    )

    assert result["qboRealmId"] == "realm-1"
    assert result["qboAccountId"] == "bank-1"
    assert result["custom"] == "preserve-me"


def test_bill_payment_export_rejects_mapping_from_other_realm():
    service = QuickBooksBillPaymentExportService()

    with pytest.raises(ValueError, match="different QuickBooks company"):
        service._assert_payment_mapping_realm(
            {"additional_info": json.dumps({"qboRealmId": "realm-2"})},
            "realm-1",
        )


def test_bill_payment_import_additional_info_records_realm_and_account():
    result = QuickBooksBillPaymentImportService._build_account_additional_info(
        {
            "external_account_id": "cc-1",
            "account_name": "Corporate Card",
            "account_type": "Credit Card",
            "account_detail_type": "CreditCard",
        },
        "realm-1",
    )

    payload = json.loads(result)
    assert payload["qboRealmId"] == "realm-1"
    assert payload["qboAccountId"] == "cc-1"
    assert payload["eligiblePayType"] == "CreditCard"


def test_bill_payment_import_rejects_mapping_from_other_realm_and_accepts_legacy_blank():
    service = QuickBooksBillPaymentImportService()

    service._assert_payment_mapping_realm(
        {"additional_info": json.dumps({"qboAccountId": "bank-1"})},
        "realm-1",
    )
    with pytest.raises(ValueError, match="different QuickBooks company"):
        service._assert_payment_mapping_realm(
            {"additional_info": json.dumps({"qboRealmId": "realm-2"})},
            "realm-1",
        )

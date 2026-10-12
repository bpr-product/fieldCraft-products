from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils import constants as const
from source.dal.CustomerPaymentRepository import CustomerPaymentRepository
from source.quickbooks.QuickBooksPaymentExportService import QuickBooksPaymentExportService


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {}
        self.text = ""

    def json(self):
        return self._payload


def _install_mock_export_lock(repo):
    def claim_export_mapping(*, tenant_id, service_id, mapping_id, source_fingerprint):
        del tenant_id, service_id, source_fingerprint
        for candidate in repo.get_export_candidates.return_value or []:
            mapping = candidate.get("mapping") or {}
            if int(mapping.get("id") or 0) == int(mapping_id):
                return {"claimed": True, "reason": "claimed", "mapping": dict(mapping)}
        raise ValueError(f"Payment external mapping {mapping_id} was not found.")

    repo.claim_export_mapping.side_effect = claim_export_mapping


def make_service():
    service = QuickBooksPaymentExportService()
    service.payment_repo = MagicMock()
    _install_mock_export_lock(service.payment_repo)
    service.customer_repo = MagicMock()
    service.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "71",
        "external_account_id": "REALM",
    }
    service.project_repo = MagicMock()
    return service


def session():
    return {
        "access_token": "TOKEN",
        "base_url": "https://sandbox-quickbooks.api.intuit.com",
        "realm_id": "REALM",
        "env": "sandbox",
    }


def payment(**overrides):
    row = {
        "id": 100,
        "customer_uid": "CUST-1",
        "amount": Decimal("200000.000"),
        "suspense_amount": Decimal("200000.000"),
        "received_time": datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc),
        "pay_method": "check",
        "payment_reference_no": "RCPT-100",
        "chart_of_account_id": 2,
        "project_id": None,
    }
    row.update(overrides)
    return row


def entry(entry_id=1, **overrides):
    row = {
        "id": entry_id,
        "payment_receives_id": 100,
        "project_id": 2290,
        "reference_no": "INV-1001",
        "payment_amount": Decimal("60000.00000000"),
        "interest_paid": Decimal("0"),
        "late_fee_paid": Decimal("0"),
    }
    row.update(overrides)
    return row


def configure_invoice(service, *, invoice_no="INV-1001", qbo_invoice_id="850", customer_ref="71"):
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {"id": 1200, "invoice_no": invoice_no, "project_id": 2290, "customer_uid": "CUST-1"},
        "mapping": {"id": 501, "external_invoice_id": qbo_invoice_id, "external_account_id": "REALM", "sync_status": "SYNCED"},
    }
    service.get_qbo_invoice = MagicMock(return_value={"Id": qbo_invoice_id, "CustomerRef": {"value": customer_ref}})


def test_unapplied_receipt_builds_payment_without_lines():
    service = make_service()
    service.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "71",
        "external_account_id": "REALM",
    }

    allocation = service._resolve_allocations(
        tenant_id="t1", service_id="6", session=session(), payment=payment(), entries=[]
    )
    payload = service.build_payment_payload(payment=payment(), allocation=allocation)

    assert payload["CustomerRef"] == {"value": "71"}
    assert payload["TotalAmt"] == 200000.0
    assert payload["PaymentRefNum"] == "FIELDCRAFT-Pay-100"
    assert "Line" not in payload
    assert allocation["unapplied_total"] == Decimal("200000.00")


def test_single_invoice_allocation_builds_linked_transaction_line():
    service = make_service()
    configure_invoice(service)
    receipt = payment(suspense_amount=Decimal("140000.000"))

    allocation = service._resolve_allocations(
        tenant_id="t1", service_id="6", session=session(), payment=receipt, entries=[entry()]
    )
    payload = service.build_payment_payload(payment=receipt, allocation=allocation)

    assert payload["CustomerRef"] == {"value": "71"}
    assert payload["Line"] == [{"Amount": 60000.0, "LinkedTxn": [{"TxnId": "850", "TxnType": "Invoice"}]}]
    assert allocation["unapplied_total"] == Decimal("140000.00")


def test_multiple_entries_for_same_invoice_are_grouped_into_one_qbo_line():
    service = make_service()
    configure_invoice(service)
    receipt = payment(amount=Decimal("100.00"), suspense_amount=Decimal("0"))
    entries = [
        entry(1, payment_amount=Decimal("40")),
        entry(2, payment_amount=Decimal("60")),
    ]

    allocation = service._resolve_allocations(
        tenant_id="t1", service_id="6", session=session(), payment=receipt, entries=entries
    )

    assert allocation["lines"] == [{"Amount": 100.0, "LinkedTxn": [{"TxnId": "850", "TxnType": "Invoice"}]}]
    assert allocation["linked_invoice_count"] == 1


def test_same_customer_payment_can_span_different_project_sub_customers():
    service = make_service()
    service.payment_repo.resolve_invoice.side_effect = [
        {
            "invoice": {"id": 1, "invoice_no": "INV-1", "project_id": 2290, "customer_uid": "CUST-1"},
            "mapping": {"external_invoice_id": "850", "external_account_id": "REALM", "sync_status": "SYNCED"},
        },
        {
            "invoice": {"id": 2, "invoice_no": "INV-2", "project_id": 2291, "customer_uid": "CUST-1"},
            "mapping": {"external_invoice_id": "851", "external_account_id": "REALM", "sync_status": "SYNCED"},
        },
    ]
    service.get_qbo_invoice = MagicMock(side_effect=[
        {"Id": "850", "CustomerRef": {"value": "501"}},
        {"Id": "851", "CustomerRef": {"value": "502"}},
    ])
    service.project_repo.get_current_mapping_by_project_id.side_effect = [
        {"external_project_id": "501", "external_account_id": "REALM", "sync_status": "SYNCED"},
        {"external_project_id": "502", "external_account_id": "REALM", "sync_status": "SYNCED"},
    ]
    service.project_repo.get_mapping_sync_target.side_effect = [
        const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
        const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
    ]
    receipt = payment(amount=Decimal("100"), suspense_amount=Decimal("0"))

    allocation = service._resolve_allocations(
        tenant_id="t1",
        service_id="6",
        session=session(),
        payment=receipt,
        entries=[
            entry(1, project_id=2290, reference_no="INV-1", payment_amount=Decimal("40")),
            entry(2, project_id=2291, reference_no="INV-2", payment_amount=Decimal("60")),
        ],
    )
    payload = service.build_payment_payload(payment=receipt, allocation=allocation)

    # The Payment remains on the real/parent customer while each line links its
    # project-specific Invoice. This is what lets one receipt cover two projects.
    assert payload["CustomerRef"] == {"value": "71"}
    assert payload["Line"] == [
        {"Amount": 40.0, "LinkedTxn": [{"TxnId": "850", "TxnType": "Invoice"}]},
        {"Amount": 60.0, "LinkedTxn": [{"TxnId": "851", "TxnType": "Invoice"}]},
    ]


def test_qbo_project_invoice_uses_project_ref_when_customer_ref_differs():
    service = make_service()
    service.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "123",
        "external_account_id": "REALM",
    }
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {
            "id": 1074,
            "invoice_no": "INV-20260828-1057",
            "project_id": 2405,
            "customer_uid": "CUST-1",
        },
        "mapping": {
            "external_invoice_id": "121",
            "external_account_id": "REALM",
            "sync_status": "SYNCED",
        },
    }
    service.get_qbo_invoice = MagicMock(return_value={
        "Id": "121",
        "CustomerRef": {"value": "PROJECT-CUSTOMER-ENTITY"},
        "ProjectRef": {"value": "811480559"},
    })
    service.project_repo.get_current_mapping_by_project_id.return_value = {
        "external_project_id": "811480559",
        "external_account_id": "REALM",
        "sync_status": "SYNCED",
    }
    service.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    receipt = payment(
        id=1031,
        amount=Decimal("17845.00"),
        suspense_amount=Decimal("15181.94"),
    )

    allocation = service._resolve_allocations(
        tenant_id="tn59395620",
        service_id="6",
        session=session(),
        payment=receipt,
        entries=[
            entry(
                project_id=2405,
                reference_no="INV-20260828-1057",
                payment_amount=Decimal("2663.06"),
            )
        ],
    )
    payload = service.build_payment_payload(payment=receipt, allocation=allocation)

    assert payload["CustomerRef"] == {"value": "123"}
    assert payload["Line"] == [
        {"Amount": 2663.06, "LinkedTxn": [{"TxnId": "121", "TxnType": "Invoice"}]}
    ]
    assert allocation["unapplied_total"] == Decimal("15181.94")


def test_qbo_project_invoice_rejects_wrong_project_ref_when_customer_ref_differs():
    service = make_service()
    service.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "123",
        "external_account_id": "REALM",
    }
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {
            "id": 1074,
            "invoice_no": "INV-20260828-1057",
            "project_id": 2405,
            "customer_uid": "CUST-1",
        },
        "mapping": {
            "external_invoice_id": "121",
            "external_account_id": "REALM",
            "sync_status": "SYNCED",
        },
    }
    service.get_qbo_invoice = MagicMock(return_value={
        "Id": "121",
        "CustomerRef": {"value": "PROJECT-CUSTOMER-ENTITY"},
        "ProjectRef": {"value": "DIFFERENT-PROJECT"},
    })
    service.project_repo.get_current_mapping_by_project_id.return_value = {
        "external_project_id": "811480559",
        "external_account_id": "REALM",
        "sync_status": "SYNCED",
    }
    service.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT

    with pytest.raises(ValueError, match="different QuickBooks customer"):
        service._resolve_allocations(
            tenant_id="tn59395620",
            service_id="6",
            session=session(),
            payment=payment(id=1031, amount=Decimal("2663.06"), suspense_amount=Decimal("0")),
            entries=[
                entry(
                    project_id=2405,
                    reference_no="INV-20260828-1057",
                    payment_amount=Decimal("2663.06"),
                )
            ],
        )


def test_invoice_customer_ref_must_match_parent_or_its_mapped_project_sub_customer():
    service = make_service()
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {"id": 1, "invoice_no": "INV-1", "project_id": 2290, "customer_uid": "CUST-1"},
        "mapping": {"external_invoice_id": "850", "external_account_id": "REALM", "sync_status": "SYNCED"},
    }
    service.get_qbo_invoice = MagicMock(return_value={"Id": "850", "CustomerRef": {"value": "999"}})
    service.project_repo.get_current_mapping_by_project_id.return_value = {
        "external_project_id": "501",
        "external_account_id": "REALM",
        "sync_status": "SYNCED",
    }
    service.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER

    with pytest.raises(ValueError, match="different QuickBooks customer"):
        service._resolve_allocations(
            tenant_id="t1",
            service_id="6",
            session=session(),
            payment=payment(amount=Decimal("100"), suspense_amount=Decimal("0")),
            entries=[entry(payment_amount=Decimal("100"))],
        )


def test_reconciled_payment_keeps_parent_customer_ref_for_project_sub_customer_invoice():
    service = make_service()
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {"id": 1, "invoice_no": "INV-1001", "project_id": 2290, "customer_uid": "CUST-1"},
        "mapping": {"external_invoice_id": "850", "external_account_id": "REALM", "sync_status": "SYNCED"},
    }
    service.get_qbo_invoice = MagicMock(return_value={"Id": "850", "CustomerRef": {"value": "501"}})
    service.project_repo.get_current_mapping_by_project_id.return_value = {
        "external_project_id": "501",
        "external_account_id": "REALM",
        "sync_status": "SYNCED",
    }
    service.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
    receipt = payment(amount=Decimal("60"), suspense_amount=Decimal("0"))

    allocation = service._resolve_allocations(
        tenant_id="t1", service_id="6", session=session(), payment=receipt, entries=[entry(payment_amount=Decimal("60"))]
    )
    payload = service.build_payment_payload(payment=receipt, allocation=allocation)

    assert payload["CustomerRef"] == {"value": "71"}
    assert payload["Line"] == [{"Amount": 60.0, "LinkedTxn": [{"TxnId": "850", "TxnType": "Invoice"}]}]


def test_allocation_customer_must_match_receipt_customer():
    service = make_service()
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {"id": 1, "invoice_no": "INV-1001", "project_id": 2290, "customer_uid": "OTHER"},
        "mapping": {"external_invoice_id": "850", "external_account_id": "REALM", "sync_status": "SYNCED"},
    }

    with pytest.raises(ValueError, match="different FIELDCRAFT customer"):
        service._resolve_allocations(
            tenant_id="t1",
            service_id="6",
            session=session(),
            payment=payment(amount=Decimal("60"), suspense_amount=Decimal("0")),
            entries=[entry(payment_amount=Decimal("60"))],
        )


def test_overallocated_receipt_fails_before_qbo_write():
    service = make_service()
    configure_invoice(service)

    with pytest.raises(ValueError, match="exceed the received amount"):
        service._resolve_allocations(
            tenant_id="t1",
            service_id="6",
            session=session(),
            payment=payment(amount=Decimal("3766.995"), suspense_amount=Decimal("0")),
            entries=[
                entry(1, payment_amount=Decimal("3766.990")),
                entry(2, payment_amount=Decimal("3766.990")),
                entry(3, payment_amount=Decimal("0.005")),
            ],
        )


def test_explicit_zero_suspense_rejects_unexplained_remainder():
    service = make_service()
    configure_invoice(service)

    with pytest.raises(ValueError, match="do not reconcile"):
        service._resolve_allocations(
            tenant_id="t1",
            service_id="6",
            session=session(),
            payment=payment(amount=Decimal("100"), suspense_amount=Decimal("0")),
            entries=[entry(payment_amount=Decimal("60"))],
        )


def test_null_suspense_allows_unapplied_remainder():
    service = make_service()
    configure_invoice(service)
    allocation = service._resolve_allocations(
        tenant_id="t1",
        service_id="6",
        session=session(),
        payment=payment(amount=Decimal("100"), suspense_amount=None),
        entries=[entry(payment_amount=Decimal("60"))],
    )
    assert allocation["unapplied_total"] == Decimal("40.00")


def test_nonzero_interest_or_late_fee_is_rejected_until_accounting_rule_exists():
    service = make_service()

    with pytest.raises(ValueError, match="interest or late-fee"):
        service._resolve_allocations(
            tenant_id="t1",
            service_id="6",
            session=session(),
            payment=payment(amount=Decimal("100"), suspense_amount=Decimal("0")),
            entries=[entry(payment_amount=Decimal("100"), interest_paid=Decimal("1"))],
        )


def test_project_only_entry_remains_unapplied_in_qbo():
    service = make_service()
    service.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "71",
        "external_account_id": "REALM",
    }
    receipt = payment(amount=Decimal("100"), suspense_amount=Decimal("0"))
    project_only = entry(reference_no=None, payment_amount=Decimal("100"))

    allocation = service._resolve_allocations(
        tenant_id="t1", service_id="6", session=session(), payment=receipt, entries=[project_only]
    )

    assert allocation["lines"] == []
    assert allocation["unapplied_total"] == Decimal("100.00")


def test_existing_external_payment_id_updates_same_qbo_payment():
    service = make_service()
    service.update_payment_in_qbo = MagicMock(return_value={"Id": "550", "SyncToken": "4"})

    result, action = service._upsert_payment(
        session=session(),
        payment=payment(),
        mapping={"external_payment_id": "550", "external_account_id": "REALM", "sync_token": "3"},
        payload={"CustomerRef": {"value": "71"}, "TotalAmt": 200000.0},
    )

    assert result["Id"] == "550"
    assert action == "updatedInQbo"
    service.update_payment_in_qbo.assert_called_once()


def test_missing_mapped_payment_recovers_by_stable_payment_reference():
    service = make_service()
    service.update_payment_in_qbo = MagicMock(
        side_effect=[qbo_err.QuickBooksAPIError("not found", status_code=404), {"Id": "551", "SyncToken": "2"}]
    )
    service.find_payment_candidates_by_ref = MagicMock(return_value=[
        {
            "Id": "551",
            "SyncToken": "1",
            "CustomerRef": {"value": "71"},
            "PaymentRefNum": "FIELDCRAFT-Pay-100",
            "TotalAmt": 200000.0,
        }
    ])

    result, action = service._upsert_payment(
        session=session(),
        payment=payment(),
        mapping={"external_payment_id": "550", "external_account_id": "REALM", "sync_token": "1"},
        payload={"CustomerRef": {"value": "71"}, "TotalAmt": 200000.0},
    )

    assert result["Id"] == "551"
    assert action == "mappedFromQbo"


def test_duplicate_identity_candidates_fail_instead_of_guessing():
    service = make_service()
    service.find_payment_candidates_by_ref = MagicMock(return_value=[
        {"Id": "551", "CustomerRef": {"value": "71"}},
        {"Id": "552", "CustomerRef": {"value": "71"}},
    ])

    with pytest.raises(ValueError, match="Multiple QuickBooks payments"):
        service._upsert_payment(
            session=session(),
            payment=payment(),
            mapping={},
            payload={"CustomerRef": {"value": "71"}, "TotalAmt": 200000.0},
        )


def test_stale_synctoken_is_refreshed_and_update_retried(monkeypatch):
    service = make_service()
    posts = [
        FakeResponse(400, {"Fault": {"Error": [{"Message": "Stale Object Error", "Detail": "Stale Object Error"}]}}),
        FakeResponse(200, {"Payment": {"Id": "550", "SyncToken": "8"}}),
    ]
    monkeypatch.setattr(qbo_err, "qbo_post", MagicMock(side_effect=posts))
    service.get_qbo_payment = MagicMock(return_value={"Id": "550", "SyncToken": "7"})

    result = service.update_payment_in_qbo(
        session=session(),
        payment_id="550",
        payload={"CustomerRef": {"value": "71"}, "TotalAmt": 100.0},
        known_sync_token="6",
    )

    assert result["SyncToken"] == "8"
    assert qbo_err.qbo_post.call_count == 2
    assert qbo_err.qbo_post.call_args_list[1].kwargs["json"]["SyncToken"] == "7"


def test_source_fingerprint_changes_when_reconciliation_entry_changes():
    repo = CustomerPaymentRepository()
    receipt = payment(amount=Decimal("100"), suspense_amount=Decimal("40"))
    before = repo.source_fingerprint(receipt, [entry(payment_amount=Decimal("60"))])
    after = repo.source_fingerprint(receipt, [entry(payment_amount=Decimal("70"))])
    assert before != after


def test_export_success_marks_existing_mapping_synced_with_qbo_identity():
    service = make_service()
    receipt = payment(amount=Decimal("100"), suspense_amount=Decimal("40"))
    allocations = [entry(payment_amount=Decimal("60"))]
    service.payment_repo.get_export_candidates.return_value = [{
        "payment": receipt,
        "entries": allocations,
        "mapping": {"id": 9001, "external_payment_id": None, "external_account_id": None, "sync_token": None},
    }]
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {"id": 1200, "invoice_no": "INV-1001", "project_id": 2290, "customer_uid": "CUST-1"},
        "mapping": {"external_invoice_id": "850", "external_account_id": "REALM", "sync_status": "SYNCED"},
    }
    service.payment_repo.source_fingerprint.return_value = "f" * 64
    service.get_qbo_invoice = MagicMock(return_value={"Id": "850", "CustomerRef": {"value": "71"}})
    service._open_qbo_session = MagicMock(return_value=session())
    service.find_payment_candidates_by_ref = MagicMock(return_value=[])
    service.create_payment_in_qbo = MagicMock(return_value={"Id": "550", "SyncToken": "0"})

    result = service.export_payments(tenant_id="t1", service_id="6")

    assert result["ok"] is True
    assert result["createdInQbo"] == 1
    assert result["failed"] == 0
    service.payment_repo.mark_mapping_synced.assert_called_once()
    kwargs = service.payment_repo.mark_mapping_synced.call_args.kwargs
    assert kwargs["external_payment_id"] == "550"
    assert kwargs["external_account_id"] == "REALM"
    assert kwargs["sync_token"] == "0"


def test_export_reloads_mapping_inside_lock_before_qbo_upsert():
    service = make_service()
    receipt = payment(amount=Decimal("100"), suspense_amount=Decimal("40"))
    allocations = [entry(payment_amount=Decimal("60"))]
    service.payment_repo.get_export_candidates.return_value = [{
        "payment": receipt,
        "entries": allocations,
        "mapping": {
            "id": 9001,
            "external_payment_id": None,
            "external_account_id": None,
            "sync_token": None,
        },
    }]
    service.payment_repo.resolve_invoice.return_value = {
        "invoice": {
            "id": 1200,
            "invoice_no": "INV-1001",
            "project_id": 2290,
            "customer_uid": "CUST-1",
        },
        "mapping": {
            "external_invoice_id": "850",
            "external_account_id": "REALM",
            "sync_status": "SYNCED",
        },
    }
    service.payment_repo.source_fingerprint.return_value = "f" * 64
    service.get_qbo_invoice = MagicMock(
        return_value={"Id": "850", "CustomerRef": {"value": "71"}}
    )
    service._open_qbo_session = MagicMock(return_value=session())
    service.update_payment_in_qbo = MagicMock(
        return_value={"Id": "550", "SyncToken": "4"}
    )
    service.create_payment_in_qbo = MagicMock()

    events = []

    def claimed_mapping(**_kwargs):
        events.append("mapping_claimed")
        return {
            "claimed": True,
            "reason": "claimed",
            "mapping": {
                "id": 9001,
                "external_payment_id": "550",
                "external_account_id": "REALM",
                "sync_token": "3",
                "sync_status": "SYNCED",
            },
        }

    service.payment_repo.claim_export_mapping.side_effect = claimed_mapping
    original_upsert = service._upsert_payment

    def tracked_upsert(**kwargs):
        events.append("upsert")
        assert kwargs["mapping"]["external_payment_id"] == "550"
        return original_upsert(**kwargs)

    service._upsert_payment = tracked_upsert

    result = service.export_payments(tenant_id="t1", service_id="6")

    assert result["ok"] is True
    assert result["createdInQbo"] == 0
    assert result["updatedInQbo"] == 1
    assert events[:2] == ["mapping_claimed", "upsert"]
    service.update_payment_in_qbo.assert_called_once()
    service.create_payment_in_qbo.assert_not_called()


def test_payment_ref_precedence_reference_then_transaction_then_stable_fallback():
    service = make_service()

    assert service._payment_ref(payment(reference_id=9090, fieldcraft_transaction_id="TXN-123")) == "9090"
    assert service._payment_ref(payment(reference_id=None, fieldcraft_transaction_id="TXN-123")) == "TXN-123"
    assert service._payment_ref(payment(reference_id=None, fieldcraft_transaction_id=None)) == "FIELDCRAFT-Pay-100"


def test_legacy_payment_ref_lookup_runs_only_after_current_reference_has_no_candidates():
    service = make_service()
    service.find_payment_candidates_by_ref = MagicMock(side_effect=[
        [],
        [{
            "Id": "551",
            "SyncToken": "1",
            "CustomerRef": {"value": "71"},
            "PaymentRefNum": "FIELDCRAFTPAY:100",
            "TotalAmt": 200000.0,
        }],
    ])
    service.update_payment_in_qbo = MagicMock(return_value={"Id": "551", "SyncToken": "2"})

    result, action = service._upsert_payment(
        session=session(),
        payment=payment(reference_id=None, fieldcraft_transaction_id=None),
        mapping={},
        payload={"CustomerRef": {"value": "71"}, "TotalAmt": 200000.0},
    )

    assert result["Id"] == "551"
    assert action == "mappedFromQbo"
    assert [call.kwargs["payment_ref"] for call in service.find_payment_candidates_by_ref.call_args_list] == [
        "FIELDCRAFT-Pay-100",
        "FIELDCRAFTPAY:100",
    ]


def test_legacy_payment_ref_lookup_is_not_used_when_current_reference_has_candidates():
    service = make_service()
    service.find_payment_candidates_by_ref = MagicMock(return_value=[{
        "Id": "551",
        "SyncToken": "1",
        "CustomerRef": {"value": "71"},
        "PaymentRefNum": "FIELDCRAFT-Pay-100",
        "TotalAmt": 200000.0,
    }])
    service.update_payment_in_qbo = MagicMock(return_value={"Id": "551", "SyncToken": "2"})

    result, action = service._upsert_payment(
        session=session(),
        payment=payment(reference_id=None, fieldcraft_transaction_id=None),
        mapping={},
        payload={"CustomerRef": {"value": "71"}, "TotalAmt": 200000.0},
    )

    assert result["Id"] == "551"
    assert action == "mappedFromQbo"
    assert service.find_payment_candidates_by_ref.call_count == 1
    assert service.find_payment_candidates_by_ref.call_args.kwargs["payment_ref"] == "FIELDCRAFT-Pay-100"




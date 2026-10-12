"""Unit tests for Eden AI Assistant Gold loader shape_doc functions.

All tests run without external connections (no Mongo, no PostgreSQL).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "T-10001"
_PROJECT = "proj-abc-123"


# ─────────────────────────────────────────────────────────────────────────────
# project_health
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectHealthShapeDoc:
    def _import(self):
        from gold.src.eden.load_project_health import shape_doc
        return shape_doc

    def test_basic_shape(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "order_no": "PRJ-001",
            "total_task_count": 26, "done_task_count": 10, "overdue_task_count": 3,
            "completion_pct": 38.46,
            "estimated_cost": 33852.00, "material_cost": 18500.00,
            "worker_cost": 7920.00, "tool_cost": 840.00,
            "overhead_cost": 2430.00, "margin": 4162.00,
            "committed_spend": 28100.00,
            "schedule_revised_count": 2, "slip_days": 4,
            "job_startdate": "2024-03-01", "actual_job_startdate": "2024-03-05",
            "job_enddate": "2024-04-30", "actual_job_enddate": "2024-05-04",
        }
        doc = shape_doc(row, _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["projectId"] == _PROJECT
        assert doc["_grain"] == "eden_project_health"
        assert doc["_schemaVersion"] == 1
        assert doc["completionPct"] == 38.46
        assert doc["totalTaskCount"] == 26
        assert doc["overdueTaskCount"] == 3
        assert doc["scheduleRisk"] == "at_risk"
        assert doc["slipDays"] == 4
        assert doc["remainingBudget"] == pytest.approx(33852.00 - 28100.00)

    def test_no_schedule_is_unknown(self):
        # Row carries no project_schedule signal (no version, no job dates, no
        # schedule watermark) → must be "unknown", not the old false-negative
        # "on_track".
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "total_task_count": 10, "done_task_count": 5, "overdue_task_count": 0,
            "completion_pct": 50.0, "estimated_cost": None,
            "committed_spend": 0, "slip_days": 0,
        }
        doc = shape_doc(row, _TS)
        assert doc["scheduleRisk"] == "unknown"
        assert doc["remainingBudget"] is None

    def test_no_slip_with_schedule_is_on_track(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "total_task_count": 10, "done_task_count": 5, "overdue_task_count": 0,
            "completion_pct": 50.0, "estimated_cost": None,
            "committed_spend": 0, "slip_days": 0,
            "schedule_revised_count": 1, "job_startdate": "2024-03-01",
        }
        doc = shape_doc(row, _TS)
        assert doc["scheduleRisk"] == "on_track"

    def test_null_cost_fields(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "total_task_count": 0, "done_task_count": 0, "overdue_task_count": 0,
        }
        doc = shape_doc(row, _TS)
        assert doc["estimatedCost"] is None
        assert doc["completionPct"] is None


# ─────────────────────────────────────────────────────────────────────────────
# invoice_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestInvoiceSummaryShapeDoc:
    def _import(self):
        from gold.src.eden.load_invoice_summary import shape_doc
        return shape_doc

    def test_basic_shape(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "total_billed": 18500.00, "total_received": 5000.00,
            "total_outstanding": 13500.00,
            "overdue_invoices": [{"invoiceNo": "INV-001", "daysOverdue": 29}],
            "upcoming_invoices": [],
            "last_payment_amount": 5000.00,
            "last_payment_date": "2024-02-01",
            "last_payment_method": "ACH",
        }
        doc = shape_doc(row, _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["projectId"] == _PROJECT
        assert doc["_grain"] == "eden_invoice_summary"
        assert doc["_schemaVersion"] == 1
        assert doc["totalBilled"] == pytest.approx(18500.00)
        assert doc["totalOutstanding"] == pytest.approx(13500.00)
        assert len(doc["overdueInvoices"]) == 1
        assert doc["lastPayment"]["payMethod"] == "ACH"

    def test_no_payments(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "total_billed": 5000.00, "total_received": 0,
            "total_outstanding": 5000.00,
            "overdue_invoices": None, "upcoming_invoices": None,
            "last_payment_amount": None, "last_payment_date": None,
            "last_payment_method": None,
        }
        doc = shape_doc(row, _TS)
        assert doc["lastPayment"] is None
        assert doc["overdueInvoices"] == []
        assert doc["upcomingInvoices"] == []


# ─────────────────────────────────────────────────────────────────────────────
# task_intelligence
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskIntelligenceShapeDoc:
    def _import(self):
        from gold.src.eden.load_task_intelligence import shape_doc
        return shape_doc

    def test_basic_shape(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "total_task_count": 26, "done_task_count": 10,
            "overdue_task_count": 3, "unassigned_task_count": 1,
            "overdue_tasks": [
                {"taskId": "t-1", "taskName": "Install ridge cap", "daysLate": 6,
                 "assignees": ["John Smith"], "reason": "Material delayed"}
            ],
            "unassigned_tasks": [{"taskId": "t-4", "taskName": "Inspection"}],
            "most_delayed_task_id": "t-1",
            "most_delayed_task_name": "Install ridge cap",
            "most_delayed_days": 6,
        }
        doc = shape_doc(row, _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["_grain"] == "eden_task_intelligence"
        assert doc["_schemaVersion"] == 1
        assert doc["overdueTaskCount"] == 3
        assert doc["unassignedTaskCount"] == 1
        assert doc["mostDelayedTask"]["taskId"] == "t-1"
        assert doc["mostDelayedTask"]["daysLate"] == 6
        assert len(doc["overdueTasks"]) == 1

    def test_no_overdue_tasks(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "total_task_count": 10, "done_task_count": 10,
            "overdue_task_count": 0, "unassigned_task_count": 0,
            "overdue_tasks": None, "unassigned_tasks": None,
            "most_delayed_task_id": None,
        }
        doc = shape_doc(row, _TS)
        assert doc["overdueTasks"] == []
        assert doc["mostDelayedTask"] is None


# ─────────────────────────────────────────────────────────────────────────────
# procurement_status
# ─────────────────────────────────────────────────────────────────────────────

class TestProcurementStatusShapeDoc:
    def _import(self):
        from gold.src.eden.load_procurement_status import shape_doc
        return shape_doc

    def test_basic_shape(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "bom_total_value": 18500.00,
            "ordered_value": 12000.00,
            "unordered_value": 6500.00,
            "unordered_items": [
                {"materialName": "Ridge cap", "quantity": 12, "unit": "bundle",
                 "unitCost": 45.00, "totalCost": 540.00, "vendorName": "ABC Supply"}
            ],
            "pending_approvals": [
                {"poNumber": "PO-001", "vendor": "ABC Supply",
                 "total": 4200.00, "status": "PENDING_APPROVAL"}
            ],
        }
        doc = shape_doc(row, _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["_grain"] == "eden_procurement_status"
        assert doc["_schemaVersion"] == 1
        assert doc["bomTotalValue"] == pytest.approx(18500.00)
        assert doc["unorderedValue"] == pytest.approx(6500.00)
        assert len(doc["unorderedItems"]) == 1
        assert len(doc["pendingApprovals"]) == 1

    def test_empty_procurement(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "bom_total_value": 0, "ordered_value": 0, "unordered_value": 0,
            "unordered_items": None, "pending_approvals": None,
        }
        doc = shape_doc(row, _TS)
        assert doc["unorderedItems"] == []
        assert doc["pendingApprovals"] == []


# ─────────────────────────────────────────────────────────────────────────────
# bid_context
# ─────────────────────────────────────────────────────────────────────────────

class TestBidContextShapeDoc:
    def _import(self):
        from gold.src.eden.load_bid_context import shape_doc, _parse_answers
        return shape_doc, _parse_answers

    def test_basic_shape(self):
        shape_doc, _ = self._import()
        row = {
            "tenantid": _TENANT, "project_id": _PROJECT,
            "question_template_version": "v3.1",
            "total_questions": 21,
            "answers_data": [
                {"itemOid": "I.1", "value": "Smith Residence"},
                {"itemOid": "I.2", "value": 2400},
            ],
            # Raw ODM/SDM template blob — shape_doc parses this (no per-item cols).
            "question_template_data": {"SDM": {"Building": {"TemplateVersion": [{
                "ItemGroupDef": [{"ItemRef": [
                    {"ItemOID": "I.1", "Mandatory": "Yes"},
                    {"ItemOID": "I.2", "Mandatory": "Yes"},
                    {"ItemOID": "I.3", "Mandatory": "No"},
                ]}],
                "ItemDef": [
                    {"OID": "I.1", "Question": {"TranslatedText": {"text": "Property name"}}, "DataType": "text"},
                    {"OID": "I.2", "Question": {"TranslatedText": {"text": "Area"}}, "DataType": "number"},
                    {"OID": "I.3", "Question": {"TranslatedText": {"text": "Stories"}}, "DataType": "number"},
                ],
            }]}}},
        }
        doc = shape_doc(row, _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["_grain"] == "eden_bid_context"
        assert doc["_schemaVersion"] == 1
        assert doc["questionTemplateVersion"] == "v3.1"
        assert doc["answeredCount"] == 2
        assert len(doc["visibleUnanswered"]) == 1
        assert doc["visibleUnanswered"][0]["itemOid"] == "I.3"
        assert doc["nextRequiredItem"]["itemOid"] == "I.3"

    def test_parse_answers_list(self):
        _, _parse_answers = self._import()
        result = _parse_answers([
            {"itemOid": "I.1", "value": "Smith"},
            {"itemOid": "I.2", "value": 2400},
        ])
        assert "I.1" in result
        assert result["I.2"]["value"] == 2400

    def test_parse_answers_empty(self):
        _, _parse_answers = self._import()
        assert _parse_answers(None) == {}
        assert _parse_answers("") == {}


# ─────────────────────────────────────────────────────────────────────────────
# tenant_dashboard
# ─────────────────────────────────────────────────────────────────────────────

class TestTenantDashboardShapeDoc:
    def _import(self):
        from gold.src.eden.load_tenant_dashboard import shape_doc
        return shape_doc

    def test_basic_shape(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT,
            "project_count": 5,
            "at_risk_count": 2,
            "total_outstanding": 28700.00,
            "active_projects": [
                {"projectId": _PROJECT, "orderNo": "PRJ-001",
                 "status": "estimation", "completionPct": 38.0,
                 "overdueTaskCount": 3, "scheduleRisk": "at_risk",
                 "outstandingAmt": 13500.00}
            ],
        }
        doc = shape_doc(row, _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["_grain"] == "eden_tenant_dashboard"
        assert doc["_schemaVersion"] == 1
        assert doc["projectCount"] == 5
        assert doc["atRiskCount"] == 2
        assert doc["totalOutstanding"] == pytest.approx(28700.00)
        assert len(doc["activeProjects"]) == 1

    def test_no_projects(self):
        shape_doc = self._import()
        row = {
            "tenantid": _TENANT,
            "project_count": 0, "at_risk_count": 0,
            "total_outstanding": None, "active_projects": None,
        }
        doc = shape_doc(row, _TS)
        assert doc["projectCount"] == 0
        assert doc["activeProjects"] == []
        assert doc["totalOutstanding"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Every Silver-sourced Eden doc carries the standard Gold watermark/lineage block
# ─────────────────────────────────────────────────────────────────────────────

_SRC_TS = datetime(2024, 5, 30, 8, 0, 0, tzinfo=timezone.utc)


def _eden_watermark_cases():
    from gold.src.eden import (
        load_bid_context, load_invoice_summary, load_procurement_status,
        load_project_health, load_task_intelligence, load_tenant_dashboard,
    )
    pid = {"tenantid": _TENANT, "project_id": _PROJECT, "project_silver_ts": _SRC_TS}
    return [
        (load_project_health.shape_doc, {
            **pid, "p_silver_ts": _SRC_TS,  # project_health aliases project ts as p_silver_ts
            "total_task_count": 1, "done_task_count": 0,
            "overdue_task_count": 0, "completion_pct": 0.0,
            "committed_spend": 0, "slip_days": 0}),
        (load_invoice_summary.shape_doc, {
            **pid, "total_billed": 0, "total_received": 0, "total_outstanding": 0,
            "overdue_invoices": None, "upcoming_invoices": None,
            "last_payment_amount": None, "last_payment_date": None,
            "last_payment_method": None}),
        (load_task_intelligence.shape_doc, {
            **pid, "total_task_count": 0, "done_task_count": 0,
            "overdue_task_count": 0, "unassigned_task_count": 0,
            "overdue_tasks": None, "unassigned_tasks": None,
            "most_delayed_task_id": None}),
        (load_procurement_status.shape_doc, {
            **pid, "bom_total_value": 0, "ordered_value": 0, "unordered_value": 0,
            "unordered_items": None, "pending_approvals": None}),
        (load_bid_context.shape_doc, {
            **pid, "question_template_version": "v1", "total_questions": 0,
            "answers_data": [], "template_items": []}),
        (load_tenant_dashboard.shape_doc, {
            "tenantid": _TENANT, "project_silver_ts": _SRC_TS,
            "project_count": 0, "at_risk_count": 0,
            "total_outstanding": None, "active_projects": None}),
    ]


@pytest.mark.parametrize("shape_doc,row", _eden_watermark_cases())
def test_every_eden_doc_has_watermark_columns(shape_doc, row):
    from gold.pipeline.lineage import LINEAGE_SET_KEYS

    doc = shape_doc(row, _TS)
    # All lineage keys present
    assert set(LINEAGE_SET_KEYS) <= set(doc.keys())
    # Provenance defaults to SOURCE
    assert doc["dataOrigin"] == "SOURCE"
    # sourceUpdatedAt is populated from the project watermark (not null)
    assert doc["sourceUpdatedAt"] is not None
    assert doc["sourceUpdatedAt"] == doc["_silverWatermarks"]["project"]
    # platform watermark fields present (goldUpdatedAt is the sole gold-side
    # write-time stamp — was _etlUpdatedAt until 2026-07-12, see
    # gold/pipeline/lineage.py's "Consolidation" note)
    assert "_silverWatermarks" in doc and "goldUpdatedAt" in doc

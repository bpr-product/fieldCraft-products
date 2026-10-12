"""Shared Silver row factories for Gold integration and unit tests.

All tenant IDs use T-TEST-* prefix — never real customer IDs.
All monetary values use round numbers for easy assertion.
"""

from __future__ import annotations

from datetime import datetime, timezone

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_project_row(
    tenantid: str = "T-TEST-001",
    project_id: str = "proj-test-001",
    **overrides,
) -> dict:
    base = {
        "tenantid":            tenantid,
        "project_id":          project_id,
        "project_uid":         f"PRJ-{project_id}",
        "order_no":            f"ORD-{project_id}",
        "trade":               "roofing",
        "trade_oid":           "P.6",
        "trade_type":          "residential",
        "project_type":        "residential",
        "status":              "estimation",
        "description":         "Test project",
        "location":            "Austin, TX",
        "zipcode":             "78701",
        "customer_uid":        "cust-001",
        "asset_uid":           "asset-001",
        "created_by":          "test-user",
        "created_at":          _NOW,
        "updated_at":          _NOW,
        "reference_project_id": None,
        "margin_material_pct":  12.0,
        "margin_resource_pct":  18.0,
        "margin_tool_pct":      10.0,
        "overhead_material_pct": 5.0,
        "overhead_resource_pct": 8.0,
        "overhead_tool_pct":    4.0,
        "tax_pct":              8.25,
        "estimate_id":         "est-001",
        "estimation":          30000.0,
        "material_cost":       18000.0,
        "worker_cost":          7000.0,
        "tool_cost":              500.0,
        "overhead_cost":        2000.0,
        "estimate_margin":      2500.0,
        "estimate_total_cost": 30000.0,
        "estimate_reason":     "standard",
        "estimate_version":    1,
        "bom_line_count":       5,
        "bom_total_cost":      18000.0,
        "resource_line_count":  3,
        "resource_total_cost":  7000.0,
        "tool_line_count":       2,
        "tool_total_cost":       500.0,
        "sq_ft":               2400.0,
        "answers_data":        None,
        "question_template_version": 3,
        "answered_count":       0,
        "services_json":       None,
        "is_deleted":          False,
        "silver_lsn":          1000,
    }
    return {**base, **overrides}


def make_project_health_row(
    tenantid: str = "T-TEST-001",
    project_id: str = "proj-test-001",
    **overrides,
) -> dict:
    base = {
        "tenantid":               tenantid,
        "project_id":             project_id,
        "order_no":               "ORD-001",
        "project_name":           "Test Project",
        "status":                 "estimation",
        "total_task_count":       10,
        "done_task_count":         5,
        "overdue_task_count":      2,
        "unassigned_task_count":   1,
        "completion_pct":         50.0,
        "estimated_cost":      30000.0,
        "estimate_version":        1,
        "material_cost":       18000.0,
        "worker_cost":          7000.0,
        "tool_cost":             500.0,
        "overhead_cost":        2000.0,
        "margin":               2500.0,
        "committed_pos":       15000.0,
        "committed_invoices":   5000.0,
        "committed_spend":     20000.0,
        "outstanding_invoices_total": 5000.0,
        "slip_days":               0,
        "schedule_revised_count":  1,
        "job_startdate":           _NOW,
        "actual_job_startdate":    _NOW,
        "job_enddate":             _NOW,
        "actual_job_enddate":      _NOW,
        "is_deleted":             False,
        "silver_lsn":             2000,
    }
    return {**base, **overrides}


def make_invoice_summary_row(
    tenantid: str = "T-TEST-001",
    project_id: str = "proj-test-001",
    **overrides,
) -> dict:
    base = {
        "tenantid":            tenantid,
        "project_id":          project_id,
        "total_billed":        10000.0,
        "total_received":       5000.0,
        "total_outstanding":    5000.0,
        "overdue_invoices":    None,
        "upcoming_invoices":   None,
        "last_payment_amount":  5000.0,
        "last_payment_date":    _NOW,
        "last_payment_method":  "ACH",
        "is_deleted":          False,
        "silver_lsn":          3000,
    }
    return {**base, **overrides}


def make_task_intelligence_row(
    tenantid: str = "T-TEST-001",
    project_id: str = "proj-test-001",
    **overrides,
) -> dict:
    base = {
        "tenantid":              tenantid,
        "project_id":            project_id,
        "total_task_count":      10,
        "done_task_count":        5,
        "overdue_task_count":     2,
        "unassigned_task_count":  1,
        "overdue_tasks":         None,
        "unassigned_tasks":      None,
        "most_delayed_task_id":  None,
        "most_delayed_task_name": None,
        "most_delayed_days":     None,
        "is_deleted":            False,
        "silver_lsn":            4000,
    }
    return {**base, **overrides}

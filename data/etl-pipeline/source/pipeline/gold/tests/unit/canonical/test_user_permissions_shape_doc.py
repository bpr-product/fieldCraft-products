"""Unit tests for the Canonical Gold user_permissions loader's shape_doc().

All tests run without external connections (no Mongo, no PostgreSQL).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TS = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "DEMO-T1"


def _import():
    from gold.src.canonical.load_user_permissions import shape_doc
    return shape_doc


def _base_row(**overrides) -> dict:
    row = {
        "tenantid": _TENANT,
        "user_account_id": 42,
        "user_uid": "oid-abc-123",
        "email": "syn.user@example.com",
        "firstname": "Syn",
        "middlename": None,
        "lastname": "User",
        "active": True,
        "deleted": False,
        "status": "ACTIVE",
        "roles": ["foreman"],
        "permissions": ["TAS.CRE"],
        "project_ids": ["9001"],
        "silver_lsn": 100,
        "is_deleted": False,
        "deleted_at": None,
        "ua_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "role_silver_ts": None,
        "perm_silver_ts": None,
        "resource_silver_ts": None,
        "member_silver_ts": None,
    }
    row.update(overrides)
    return row


class TestUserPermissionsShapeDoc:
    def test_basic_shape(self):
        shape_doc = _import()
        doc = shape_doc(_base_row(), _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["user_uid"] == "oid-abc-123"
        assert doc["user_account_id"] == 42
        assert doc["displayName"] == "Syn User"
        assert doc["roles"] == ["foreman"]
        assert doc["permissions"] == ["TAS.CRE"]
        assert doc["project_ids"] == ["9001"]
        assert doc["_grain"] == "user_permissions"
        assert doc["_schemaVersion"] == 1
        assert doc["isActive"] is True

    def test_roles_and_permissions_are_deduped_and_sorted(self):
        shape_doc = _import()
        row = _base_row(
            roles=["foreman", "foreman", "admin"],
            permissions=["TAS.CRE", "DOC.MAN", "TAS.CRE"],
        )
        doc = shape_doc(row, _TS)
        assert doc["roles"] == ["admin", "foreman"]
        assert doc["permissions"] == ["DOC.MAN", "TAS.CRE"]

    def test_user_with_no_resource_has_empty_project_ids(self):
        """65/80 users in the verified live schema have no `resource` row and
        therefore cannot appear as project members — this must degrade to an
        empty list, not an error, per docs/canonical/user_access_management_model.md."""
        shape_doc = _import()
        doc = shape_doc(_base_row(project_ids=[]), _TS)
        assert doc["project_ids"] == []
        # Roles/permissions are independent of project membership.
        assert doc["roles"] == ["foreman"]

    def test_inactive_user_is_active_false(self):
        shape_doc = _import()
        doc = shape_doc(_base_row(active=False), _TS)
        assert doc["isActive"] is False

    def test_deleted_user_is_active_false(self):
        shape_doc = _import()
        doc = shape_doc(_base_row(deleted=True), _TS)
        assert doc["isActive"] is False

    def test_no_middle_name_does_not_add_double_space(self):
        shape_doc = _import()
        doc = shape_doc(_base_row(firstname="Syn", middlename=None, lastname="User"), _TS)
        assert doc["displayName"] == "Syn User"

    def test_lineage_fields_present(self):
        # featureName was dropped from lineage_fields() 2026-07-12 (no functional
        # reader — see gold/pipeline/lineage.py's "Consolidation" note); the
        # gold-side write timestamp is now goldUpdatedAt.
        shape_doc = _import()
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["dataOrigin"] == "SOURCE"
        assert doc["sourceUpdatedAt"] is not None

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_user_permissions import _contract_validator
        shape_doc = _import()
        doc = shape_doc(_base_row(), _TS)
        assert _contract_validator.check(doc) is None


class TestAdminRoleIgnoresAssignEndDate:
    """Business rule (2026-07-30): admin/superAdmin role assignments are
    treated as still-effective past their assign_end_date — tn24047750's only
    admin grant had a 1-day assign_end_date window that expired months ago,
    silently dropping the tenant's sole admin from this collection."""

    def test_query_bypasses_end_date_for_admin_and_superadmin(self):
        from gold.src.canonical.load_user_permissions import _SILVER_QUERY
        assert "LOWER(r.name) IN ('admin', 'superadmin')" in _SILVER_QUERY

    def test_start_date_still_enforced(self):
        # The exception only drops the upper bound — a not-yet-started admin
        # assignment must still be excluded.
        from gold.src.canonical.load_user_permissions import _SILVER_QUERY
        assert "urm.assign_start_date <= NOW()" in _SILVER_QUERY

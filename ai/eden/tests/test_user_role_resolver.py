"""Unit tests for UserRoleResolver — Gold-based role lookup for response
shaping. The load-bearing property: FAIL CLOSED — every failure/unknown path
must land on the customer-safe view, never the internal one."""
from unittest.mock import MagicMock, patch

from source.resolvers.user_role_resolver import UserRoleResolver


def _mock_collection(doc: dict | None):
    collection = MagicMock()
    collection.find_one.return_value = doc
    return collection


def test_internal_role_is_not_customer():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["projectManager"]}),
    ):
        assert UserRoleResolver().is_customer("t1", "u1") is False


def test_customer_role_is_customer():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["customer"]}),
    ):
        assert UserRoleResolver().is_customer("t1", "u1") is True


def test_customer_marker_matched_case_insensitively_within_role_name():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["CustomerViewer"]}),
    ):
        assert UserRoleResolver().is_customer("t1", "u1") is True


def test_unknown_user_fails_closed_to_customer():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection(None),
    ):
        assert UserRoleResolver().is_customer("t1", "u1") is True


def test_empty_roles_fails_closed_to_customer():
    # Real Gold data: 17% of users have empty roles -- they get the safe view.
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": []}),
    ):
        assert UserRoleResolver().is_customer("t1", "u1") is True


def test_lookup_failure_fails_closed_to_customer():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        side_effect=Exception("timeout"),
    ):
        assert UserRoleResolver().is_customer("t1", "u1") is True


def test_admin_role_is_admin():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["admin"]}),
    ):
        assert UserRoleResolver().is_admin("t1", "u1") is True


def test_superadmin_is_not_admin():
    # v1 scope is exactly "admin" -- constraints doc #32: superAdmin is
    # deliberately NOT included yet.
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["superAdmin"]}),
    ):
        assert UserRoleResolver().is_admin("t1", "u1") is False


def test_sales_role_is_not_admin():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["sales"]}),
    ):
        assert UserRoleResolver().is_admin("t1", "u1") is False


def test_admin_matched_case_insensitively():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["Admin"]}),
    ):
        assert UserRoleResolver().is_admin("t1", "u1") is True


def test_unknown_user_fails_closed_to_not_admin():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection(None),
    ):
        assert UserRoleResolver().is_admin("t1", "u1") is False


def test_empty_roles_fails_closed_to_not_admin():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": []}),
    ):
        assert UserRoleResolver().is_admin("t1", "u1") is False


def test_lookup_failure_fails_closed_to_not_admin():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        side_effect=Exception("timeout"),
    ):
        assert UserRoleResolver().is_admin("t1", "u1") is False


# ── is_margin_visible ────────────────────────────────────────────────────
# 2026-09-10: rewritten from a role-name check to a real EST.MOD permission
# check (see its own docstring in user_role_resolver.py for the full
# reasoning) -- confirmed live against the actual source role_permission
# table, all 3 prod tenants: EST.MOD is held by admin/sales/superAdmin only.
# is_admin() itself is untouched above (it also gates the unrelated
# EDEN_ADMIN_ONLY pilot restriction, which must not silently widen with
# this). These tests now mock the "permissions" field, not "roles" -- this
# method no longer calls get_roles() at all.

def test_admin_permission_margin_visible():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": ["EST.MOD"]}),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is True


def test_sales_permission_margin_visible():
    """2026-09-10 fix: sales genuinely holds EST.MOD in the real product
    (confirmed live) -- must now see margin, unlike the old role-name gate
    which refused sales identically to customer."""
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": ["EST.MOD"]}),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is True


def test_superadmin_permission_margin_visible():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": ["EST.MOD"]}),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is True


def test_est_rea_alone_is_not_margin_visible():
    """Regression guard for the exact leak this permission choice avoids:
    EST.REA (held by customer/sales/purchaseOfficer too) must NOT be
    sufficient on its own -- only EST.MOD gates margin."""
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": ["EST.REA"]}),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is False


def test_customer_permissions_margin_not_visible():
    """customer holds EST.REA/EST.CRE/etc. (per real data) but never
    EST.MOD -- confirmed live, all 3 tenants."""
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": ["EST.REA", "EST.CRE"]}),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is False


def test_purchaseofficer_permissions_margin_not_visible():
    """purchaseOfficer holds EST.REA (confirmed live) but not EST.MOD --
    the exact role this permission choice must still refuse."""
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": ["EST.REA"]}),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is False


def test_unknown_user_fails_closed_to_margin_not_visible():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection(None),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is False


def test_empty_permissions_fails_closed_to_margin_not_visible():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": []}),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is False


def test_lookup_failure_fails_closed_to_margin_not_visible():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        side_effect=Exception("timeout"),
    ):
        assert UserRoleResolver().is_margin_visible("t1", "u1") is False


def test_get_roles_returns_list_on_success():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"roles": ["superAdmin", "estimator"]}),
    ):
        assert UserRoleResolver().get_roles("t1", "u1") == ["superAdmin", "estimator"]


def test_get_project_ids_returns_list_on_success():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"project_ids": ["9000001", "9000003"]}),
    ):
        assert UserRoleResolver().get_project_ids("t1", "u1") == ["9000001", "9000003"]


def test_get_project_ids_empty_on_missing_document():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection(None),
    ):
        assert UserRoleResolver().get_project_ids("t1", "u1") == []


def test_get_project_ids_empty_on_lookup_failure():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        side_effect=Exception("timeout"),
    ):
        assert UserRoleResolver().get_project_ids("t1", "u1") == []


def test_get_permissions_returns_list_on_success():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection({"permissions": ["PRO.REA", "JBL.REA"]}),
    ):
        assert UserRoleResolver().get_permissions("t1", "u1") == ["PRO.REA", "JBL.REA"]


def test_get_permissions_empty_on_missing_document():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_collection(None),
    ):
        assert UserRoleResolver().get_permissions("t1", "u1") == []


def test_get_permissions_empty_on_lookup_failure():
    with patch(
        "source.resolvers.user_role_resolver.get_collection",
        side_effect=Exception("timeout"),
    ):
        assert UserRoleResolver().get_permissions("t1", "u1") == []


# --- isDeleted exclusion --------------------------------------------------
# Real gap, fixed: Gold's user_permissions loader carries isDeleted/
# deletedAt through as data instead of filtering a deactivated user's
# account out at the pipeline layer (same pattern as project_health/
# invoice_summary/task_intelligence). None of these queries excluded it,
# so a deactivated employee's account kept resolving its old roles/
# permissions/project scoping via EDEN. Safe to filter despite
# get_project_ids' own fail-OPEN convention -- see that method's own
# docstring for why: a deleted user's roles going empty makes is_customer
# fail CLOSED first, so the internal fail-open branch is never reached.

def test_get_roles_excludes_deleted():
    collection = _mock_collection({"roles": ["admin"]})
    with patch("source.resolvers.user_role_resolver.get_collection", return_value=collection):
        UserRoleResolver().get_roles("t1", "u1")
    call_filter = collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_get_permissions_excludes_deleted():
    collection = _mock_collection({"permissions": ["PRO.REA"]})
    with patch("source.resolvers.user_role_resolver.get_collection", return_value=collection):
        UserRoleResolver().get_permissions("t1", "u1")
    call_filter = collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_get_project_ids_excludes_deleted():
    collection = _mock_collection({"project_ids": ["1"]})
    with patch("source.resolvers.user_role_resolver.get_collection", return_value=collection):
        UserRoleResolver().get_project_ids("t1", "u1")
    call_filter = collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_resolve_tenant_id_excludes_deleted():
    collection = MagicMock()
    collection.distinct.return_value = ["t1"]
    with patch("source.resolvers.user_role_resolver.get_collection", return_value=collection):
        UserRoleResolver().resolve_tenant_id("u1")
    call_filter = collection.distinct.call_args.args[1]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_deleted_user_gets_empty_roles_and_fails_closed_to_customer():
    # End-to-end: a deleted user's doc is invisible, so is_customer must
    # still correctly fail closed (not error, not silently grant internal
    # access) -- confirms the isDeleted exclusion and the existing
    # fail-closed behavior compose correctly for this real scenario.
    collection = MagicMock()
    collection.find_one.return_value = None  # deleted user's doc excluded by the query
    with patch("source.resolvers.user_role_resolver.get_collection", return_value=collection):
        resolver = UserRoleResolver()
        assert resolver.get_roles("t1", "deleted-user") == []
        assert resolver.is_customer("t1", "deleted-user") is True
        assert resolver.is_admin("t1", "deleted-user") is False

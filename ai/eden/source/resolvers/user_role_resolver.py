import logging
import os

from source.utils.mongo_client import get_collection

logger = logging.getLogger(__name__)

# Per the CTO directive recorded in the Gold Layer Reference (datateam.md):
# all RBAC/entity-resolution reads go through Gold, not direct Silver/PG.
# This resolver reads the resolved-roles document Gold already maintains
# (30s-fresh via CDC) instead of re-deriving the Postgres join the way
# rbac_resolver.py does. Separate env var because this collection lives in
# its own database, not the eden read-model one.
PERMISSIONS_DB = os.getenv("MONGODB_PERMISSIONS_DB", "gold_user_permissions")
USER_PERMISSIONS_COLLECTION = "user_permissions"


class UserRoleResolver:
    """Answers "what roles does this user hold?" for response-shaping —
    which VIEW of the data a user gets (customer-safe vs. internal) — as
    opposed to rbac_resolver's access-denial checks. Deliberately not
    gated behind SKIP_RBAC_CHECK: even a pilot with permission checks
    bypassed must never show internal financials to a customer role."""

    def get_roles(self, tenant_id: str, user_id: str) -> list[str]:
        """The user's resolved role names from Gold. Empty list on any
        failure or missing document — callers must treat "no roles" as
        least-privilege, never as internal access."""
        try:
            collection = get_collection(
                USER_PERMISSIONS_COLLECTION, db_name=PERMISSIONS_DB
            )
            doc = collection.find_one(
                {
                    "tenantId": tenant_id, "user_uid": user_id,
                    "isDeleted": {"$ne": True},
                },
                {"roles": 1, "_id": 0},
            )
            if not doc:
                return []
            return [r for r in (doc.get("roles") or []) if isinstance(r, str)]
        except Exception as e:
            logger.error(
                f"Role lookup failed for tenant='{tenant_id}' user='{user_id}': {e}"
            )
            return []

    def is_customer(self, tenant_id: str, user_id: str) -> bool:
        """True when this user should get the customer-safe view. FAIL
        CLOSED: unknown user, empty roles, or a lookup failure all count as
        customer — least privilege means degrading to the view that shows
        LESS, never more. (Real Gold data today: roles are populated for
        83% of users; the rest correctly land here.)"""
        roles = self.get_roles(tenant_id, user_id)
        if not roles:
            return True
        return any("customer" in role.lower() for role in roles)

    def is_admin(self, tenant_id: str, user_id: str) -> bool:
        """True only when this user's resolved role is exactly "admin" --
        the v1 scope for profit-margin visibility (see
        EDEN_CustomerSupport_Reasoning_Constraints.md Q3: role_permission
        has no field-level permission distinguishing "can read an estimate"
        from "can read its margin" -- customer AND sales both hold the same
        EST.REA as admin -- so this gate is intentionally narrower than
        is_customer's "not customer": superAdmin, sales, projectManager,
        etc. are all internal-but-NOT-admin and must still be refused.
        There is no "Manager" role in the schema; admin is the stand-in
        until/unless a real Manager role is introduced. FAIL CLOSED: unknown
        user, empty roles, or a lookup failure all count as NOT admin --
        same least-privilege direction as is_customer, just a stricter
        gate (refuse by default, not just "show the safe view")."""
        roles = self.get_roles(tenant_id, user_id)
        if not roles:
            return False
        return any(role.lower() == "admin" for role in roles)

    def is_margin_visible(self, tenant_id: str, user_id: str) -> bool:
        """True when this user holds the real `EST.MOD` permission --
        gates ONLY profit-margin/estimatedCost visibility (Q3,
        resolve_margin_response in insight_agent.py). Deliberately a
        SEPARATE method from is_admin(), not a widened is_admin(): is_admin()
        also gates navigation_api.py's EDEN_ADMIN_ONLY pilot restriction,
        which carries its own explicit, unrelated product directive ("right
        now it should only [work] for admin role" -- see
        _enforce_admin_only's docstring) that this must not touch.

        2026-09-10: rewritten from a role-name check ("admin"/"superAdmin"
        only) to a real permission check, after tracing how the actual
        fieldcraft-server product itself decides margin-column visibility in
        its own Estimate Preview table (EstimatePreviewUtils.ts's `perm:
        true` columns, gated by `isUserHasPermission(estimate.MODIFY)` --
        i.e. the real EST.MOD permission, not a role name). Confirmed live
        against the actual source role_permission table across all 3 prod
        tenants: EST.MOD is held by admin, sales, AND superAdmin -- not
        customer, not purchaseOfficer, not the other 5 roles. This is
        DIFFERENT from EST.REA (which total_cost uses) -- EST.REA is also
        held by customer/purchaseOfficer, which is why it can't gate margin;
        EST.MOD draws exactly the right line.

        This is a real behavior change from the previous role-based rule:
        `sales` now correctly sees margin (previously refused, treated
        identically to customer). That previous "admin only" v1 scope
        (constraint #32) was reasoning from "no Manager role exists yet in
        the schema," not a verified decision that sales should see less
        margin than the real product actually shows them -- and per the
        source app's own EstimatePreview table, it doesn't. FAIL CLOSED:
        unknown user, empty roles, or a lookup failure all count as NOT
        visible."""
        permissions = self.get_permissions(tenant_id, user_id)
        return "EST.MOD" in permissions

    def get_permissions(self, tenant_id: str, user_id: str) -> list[str]:
        """The user's flattened permission codes (RESOURCE.ACTION form, e.g.
        `PRO.REA`) from Gold's user_permissions.permissions -- exists in
        the data but was never read anywhere in this codebase until now.

        Deliberately NOT used to gate any agent yet: proposal.md's actual
        access model is role-based (customer vs. internal, via is_customer),
        not permission-code based -- there is no documented mapping of
        "permission code X required for agent Y" anywhere to implement
        against. Inventing one here would be the same mistake as an unvetted
        business rule (see insight_agent.py's INSIGHT_*_THRESHOLD comments
        for the same discipline) -- fabricating a gate nobody's signed off
        on. This method exists so permission codes are at least readable
        when/if a real per-permission gating rule is specified; until then,
        role-based gating (is_customer) remains the only enforced check."""
        try:
            collection = get_collection(
                USER_PERMISSIONS_COLLECTION, db_name=PERMISSIONS_DB
            )
            doc = collection.find_one(
                {
                    "tenantId": tenant_id, "user_uid": user_id,
                    "isDeleted": {"$ne": True},
                },
                {"permissions": 1, "_id": 0},
            )
            if not doc:
                return []
            return [p for p in (doc.get("permissions") or []) if isinstance(p, str)]
        except Exception as e:
            logger.error(
                f"Permissions lookup failed for tenant='{tenant_id}' user='{user_id}': {e}"
            )
            return []

    def get_project_ids(self, tenant_id: str, user_id: str) -> list[str]:
        """The specific projects this user is scoped to, per Gold's
        user_permissions.project_ids. Empty list on any failure or missing
        document -- same as get_roles.

        IMPORTANT caveat callers must respect: an empty list here is
        AMBIGUOUS, not a clean "user has no projects" signal -- real Gold
        data has project_ids empty for effectively all users today (a known,
        separately-tracked data gap), and a user genuinely assigned zero
        projects looks identical to one whose scoping data simply hasn't
        populated yet. Callers must therefore treat an empty list as "no
        scoping data available, don't filter" (fail OPEN to unscoped, not
        fail closed to nothing) -- the opposite of is_customer's fail-closed
        rule, and deliberately so: failing closed here would make Phase 1
        unusable the moment this field started populating for some users but
        not others, and unusable outright while it's 100% empty. Only a
        genuinely non-empty list is ever treated as real, enforceable
        scoping.

        isDeleted exclusion is safe despite this method's own fail-OPEN
        convention: a deleted user's doc disappearing here makes get_roles
        return [] too, which is_customer treats as fail-CLOSED (least
        privilege, customer view) -- so a deleted account is reclassified
        as a customer before AgentOrchestrator._scoped_project_ids' internal
        fail-open branch is ever reached, not routed into it."""
        try:
            collection = get_collection(
                USER_PERMISSIONS_COLLECTION, db_name=PERMISSIONS_DB
            )
            doc = collection.find_one(
                {
                    "tenantId": tenant_id, "user_uid": user_id,
                    "isDeleted": {"$ne": True},
                },
                {"project_ids": 1, "_id": 0},
            )
            if not doc:
                return []
            return [p for p in (doc.get("project_ids") or []) if isinstance(p, str)]
        except Exception as e:
            logger.error(
                f"project_ids lookup failed for tenant='{tenant_id}' user='{user_id}': {e}"
            )
            return []

    def resolve_tenant_id(self, user_uid: str) -> str | None:
        """Maps a verified JWT `oid` to its FIELDCRAFT tenant id. Azure AD B2C
        tokens carry no tenant claim at all (confirmed against a real issued
        token -- see docs/eden/AUTH_IMPLEMENTATION_PLAN.md) -- this DB lookup is
        the only source of that mapping, mirroring the mono repo's
        TenantResolver.resolve(oid) pattern. Returns None if the user maps
        to zero or more than one tenant (ambiguous -- caller must reject,
        never guess)."""
        try:
            collection = get_collection(
                USER_PERMISSIONS_COLLECTION, db_name=PERMISSIONS_DB
            )
            tenant_ids = collection.distinct(
                "tenantId", {"user_uid": user_uid, "isDeleted": {"$ne": True}}
            )
            if len(tenant_ids) != 1:
                logger.error(
                    f"Tenant resolution ambiguous for user_uid='{user_uid}': "
                    f"found {len(tenant_ids)} tenant(s)"
                )
                return None
            return tenant_ids[0]
        except Exception as e:
            logger.error(f"Tenant resolution failed for user_uid='{user_uid}': {e}")
            return None

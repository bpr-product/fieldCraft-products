import logging
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException

from source.resolvers.user_role_resolver import UserRoleResolver
from source.utils.jwt_service import TokenValidationError, resolve_environment, validate_token

logger = logging.getLogger(__name__)
_role_resolver = UserRoleResolver()

# Nonprod-only gate (see nonprod-multi-env-scoping skill): when set, only
# requests whose token resolves to THIS environment are served -- everything
# else gets a clear "not available yet" response instead of being processed.
# Unset (dev/prod default) -- no gating, every environment/token passes
# through exactly as before this existed.
_REQUIRED_ENVIRONMENT = os.getenv("EDEN_REQUIRED_ENVIRONMENT") or None


@dataclass
class AuthContext:
    """The ONLY trusted source of identity for a request -- user_id/tenant_id
    come exclusively from a verified JWT (see jwt_service.validate_token).
    Routes must never accept userId/tenantId from request params/body as an
    alternative source of truth (see docs/eden/AUTH_IMPLEMENTATION_PLAN.md s.3
    for the legacy-assistant bug this pattern exists to avoid)."""
    user_id: str
    tenant_id: str
    roles: list[str]
    permissions: list[str]
    environment: str | None = None


def get_authenticated_subject(authorization: str | None = Header(default=None)) -> AuthContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = validate_token(token)
    except TokenValidationError as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token") from e

    # Azure AD B2C tokens carry no tenant claim (confirmed against a real
    # issued token) -- tenant is resolved via DB lookup on the verified oid,
    # never trusted from the token or any client-supplied value.
    user_id = claims["oid"]
    tenant_id = _role_resolver.resolve_tenant_id(user_id)
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Unable to resolve tenant for user")

    roles = _role_resolver.get_roles(tenant_id, user_id)
    permissions = _role_resolver.get_permissions(tenant_id, user_id)
    environment = resolve_environment(claims)
    logger.info(
        f"Authenticated request: user_id={user_id} tenant_id={tenant_id} "
        f"roles={roles} permissions={permissions} environment={environment}"
    )

    # Fail closed: an unmapped audience resolves to None, which never equals
    # a configured _REQUIRED_ENVIRONMENT string -- refused the same as any
    # other non-matching environment, never silently let through.
    if _REQUIRED_ENVIRONMENT and environment != _REQUIRED_ENVIRONMENT:
        raise HTTPException(
            status_code=503,
            detail="EDEN isn't available in this environment yet.",
        )

    return AuthContext(
        user_id=user_id, tenant_id=tenant_id, roles=roles, permissions=permissions,
        environment=environment,
    )

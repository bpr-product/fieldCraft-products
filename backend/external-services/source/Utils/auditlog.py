# source/service/audit_log_service.py

import logging
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Dict

from source.dal.dbconnector import postgres_connection

logger = logging.getLogger(__name__)


class AuditlogService:
    """
    Inserts into public.audit_log (tenant DB)

    Table columns:
      created_at, action, ip_address, log_id, user_id, context, email,
      resource_id, resource_type, source, user_agent, status, user_role
    """

    def __init__(self, default_source: str = "FIELDCRAFT"):
        self.default_source = default_source

    def _best_effort_ip(self) -> str:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "0.0.0.0"

    def log(
        self,
        tenant_id: str,
        action: str,
        user_id: Optional[str] = None,
        *,
        context: Optional[str] = None,
        email: Optional[str] = None,
        resource_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        source: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: Optional[str] = None,
        user_role: Optional[str] = None,
        ip_address: Optional[str] = None,
        created_at: Optional[datetime] = None,
        log_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Returns log_id on success, None on failure.
        """
        conn = None
        cur = None
        # [SECURITY] Removed: print of full audit log parameters — contains user email, IP address, and PII, not permitted per QuickBooks compliance policy.
        try:
            conn = postgres_connection(tenant_id)
            if conn is None:
                logger.warning(
                    "QuickBooks audit log skipped because tenant DB connection is unavailable | tenant=%s action=%s",
                    tenant_id,
                    action,
                )
                return None
            # [SECURITY] Removed: print of DB name from connection parameters — exposes database connection info, not permitted per QuickBooks compliance policy.
            cur = conn.cursor()

            sql = """
                INSERT INTO public.audit_log
                    (created_at, action, ip_address, log_id, user_id, context, email,
                     resource_id, resource_type, source, user_agent, status, user_role)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s)
            """

            ts = created_at or datetime.now(timezone.utc)  # timestamptz ok
            ip = ip_address or self._best_effort_ip()
            lid = log_id or str(uuid.uuid4())
            src = source or self.default_source

            # context column is VARCHAR(64) — truncate to avoid DB errors
            ctx = str(context)[:64] if context else None

            cur.execute(
                sql,
                (
                    ts,
                    action,
                    ip,
                    lid,
                    user_id,
                    ctx,
                    email,
                    resource_id,
                    resource_type,
                    src,
                    user_agent,
                    status,
                    user_role,
                ),
            )

            conn.commit()
            return lid

        except Exception:
            logger.exception("QuickBooks audit log write failed")
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            return None

        finally:
            try:
                if cur:
                    cur.close()
            except Exception:
                pass
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

_audit = AuditlogService(default_source="QuickBooks")


def safe_audit_log(
    *,
    tenant_id: str,
    action: str,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    user_role: Optional[str] = None,
    status: str = "success",
    context: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_type: str = "quickbooks_service",
) -> None:
    """Safe wrapper around _audit.log().

    Audit log failures are isolated — they never raise or affect the caller.
    Use this in every QuickBooks service instead of calling _audit.log() directly.
    """
    try:
        _audit.log(
            tenant_id=tenant_id,
            action=action,
            user_id=user_id,
            email=email,
            user_role=user_role,
            status=status,
            context=context,
            resource_id=resource_id,
            resource_type=resource_type,
            source="QuickBooks",
        )
    except Exception as _e:
        # Audit failure must never break business logic.
        print(f"[AUDIT] safe_audit_log failed (non-blocking): action={action} error={_e}")

def _resolve_audit(payload: Any, request: Optional[Any] = None) -> Dict[str, Any]:
    email = getattr(payload, "email", None)
    user_role = getattr(payload, "user_role", None)
    user_agent = getattr(payload, "user_agent", None)
    source = getattr(payload, "source", None) or "Web"

    if request:
        user_agent = user_agent or request.headers.get("user-agent")
        source = source or request.headers.get("x-source") or "Web"
        email = email or request.headers.get("x-user-email")
        user_role = user_role or request.headers.get("x-user-role")

    return {"email": email, "user_role": user_role, "user_agent": user_agent, "source": source}

def audit_log(
    *,
    tenant_id: str,
    action: str,
    user_id: Optional[str] = None,
    payload: Any,
    request: Optional[Any] = None,
    status: str = "Started",
    context: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_type: str = "quickbooks_service",
) -> None:
    a = _resolve_audit(payload, request)
    # [SECURITY] Removed: print of audit log fields — contains user email, user_id, user_agent, and PII, not permitted per QuickBooks compliance policy.
    _audit.log(
        tenant_id=tenant_id,
        action=action,
        user_id=user_id,
        context=context,
        email=a.get("email"),
        user_role=a.get("user_role"),
        user_agent=a.get("user_agent"),
        source=a.get("source"),
        resource_id=resource_id,
        resource_type=resource_type,
        status=status,
    )
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from source.dal.dbconnector import postgres_connection


class PaymentRepository:
    """Repository for tenantdb.payment_external_map."""

    def get_by_external_payment_id(self, tenant_id: str, external_payment_id: str) -> Optional[Dict[str, Any]]:
        conn = postgres_connection(tenant_id)
        if not conn or isinstance(conn, str):
            raise Exception(f"Customer DB connection failed: {conn}")
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, customer_id, tenant_id, input_source,
                       external_payment_id, external_invoice_id, external_account_id,
                       payment_id,
                       sync_status, last_sync_at, version, invalid, created_at, updated_at
                FROM payment_external_map
                WHERE tenant_id = %s AND external_payment_id = %s
                LIMIT 1
                """,
                (tenant_id, external_payment_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [
                "id",
                "customer_id",
                "tenant_id",
                "input_source",
                "external_payment_id",
                "external_invoice_id",
                "external_account_id",
                "payment_id",
                "sync_status",
                "last_sync_at",
                "version",
                "invalid",
                "created_at",
                "updated_at",
            ]
            return dict(zip(cols, row))
        finally:
            cur.close()
            conn.close()

    def get_payment_mapping_by_payment_id(self, tenant_id: str, payment_id: int) -> Optional[Dict[str, Any]]:
        """Return mapping row by local tenant payment_receives PK (payment_id)."""
        conn = postgres_connection(tenant_id)
        if not conn or isinstance(conn, str):
            raise Exception(f"Tenant DB connection failed: {conn}")
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, customer_id, tenant_id, input_source,
                       external_payment_id, external_invoice_id, external_account_id,
                       payment_id,
                       sync_status, last_sync_at, version, invalid, created_at, updated_at
                FROM payment_external_map
                WHERE tenant_id = %s AND payment_id = %s
                  AND (invalid IS NULL OR invalid = false)
                ORDER BY id DESC
                LIMIT 1
                """,
                (tenant_id, payment_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [
                "id",
                "customer_id",
                "tenant_id",
                "input_source",
                "external_payment_id",
                "external_invoice_id",
                "external_account_id",
                "payment_id",
                "sync_status",
                "last_sync_at",
                "version",
                "invalid",
                "created_at",
                "updated_at",
            ]
            return dict(zip(cols, row))
        finally:
            cur.close()
            conn.close()

    def insert_payment_mapping(
            self,
            *,
            customer_id: int,
            tenant_id: str,
            input_source: str,
            external_payment_id: str,
            external_invoice_id: Optional[str] = None,
            external_account_id: Optional[str] = None,
            payment_id: Optional[int] = None,
            sync_status: str = "EXPORTED",
    ) -> Dict[str, Any]:
        """Insert a mapping row.

        We do a small dedupe check by (tenant_id, external_payment_id) to avoid duplicates.
        """
        if not external_payment_id:
            raise Exception("external_payment_id is required")

        # Dedupe by payment_id first (strongest link), then by external_payment_id.
        if payment_id is not None:
            existing_by_pid = self.get_payment_mapping_by_payment_id(tenant_id, int(payment_id))
            if existing_by_pid:
                return {"inserted": False, "row": existing_by_pid}

        existing = self.get_by_external_payment_id(tenant_id, external_payment_id)
        if existing:
            return {"inserted": False, "row": existing}

        conn = postgres_connection(tenant_id)
        if not conn or isinstance(conn, str):
            raise Exception(f"Customer DB connection failed: {conn}")
        cur = conn.cursor()
        try:
            now = datetime.now(timezone.utc)
            cur.execute(
                """
                INSERT INTO payment_external_map
                    (customer_id, tenant_id, input_source,
                     external_payment_id, external_invoice_id, external_account_id,
                     payment_id,
                     sync_status, last_sync_at,
                     version, invalid,
                     created_at, updated_at)
                VALUES
                    (%s, %s, %s,
                     %s, %s, %s,
                     %s,
                     %s, %s,
                     %s, %s,
                     %s, %s)
                RETURNING id
                """,
                (
                    customer_id,
                    tenant_id,
                    input_source,
                    external_payment_id,
                    external_invoice_id,
                    external_account_id,
                    int(payment_id) if payment_id is not None else None,
                    sync_status,
                    now,
                    0,  # version
                    False,  # invalid
                    now,
                    now,
                ),
            )
            new_id = cur.fetchone()[0]
            return {"inserted": True, "id": new_id}
        except Exception:
            raise Exception(f"Failed to insert payment mapping: {traceback.format_exc()}")
        finally:
            cur.close()
            conn.close()

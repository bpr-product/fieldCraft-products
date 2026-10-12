import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from source.dal.dbconnector import postgres_connection

class PaymentExternalMapRepository:
    """Repository for `payment_external_map` in the tenant DB.

    Stores mapping between FIELDCRAFT (tenant/customer) and QuickBooks Payment.
    """

    TABLE = "payment_external_map"

    COLUMNS = [
        'id',
        'customer_id',
        'tenant_id',
        'input_source',
        'external_payment_id',
        'external_invoice_id',
        'external_account_id',
        'sync_status',
        'last_sync_at',
        'payment_id',
        'version',
        'invalid',
        'created_at',
        'updated_at',
    ]
    SELECT_COLUMNS = ', '.join(COLUMNS)

    def get_by_external_payment_id(self, tenant_id: str, external_payment_id: str) -> Optional[Dict[str, Any]]:
        conn = postgres_connection(tenant_id)
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                SELECT {self.SELECT_COLUMNS}
                FROM {self.TABLE}
                WHERE tenant_id = %s
                  AND external_payment_id = %s
                  AND (invalid IS NULL OR invalid = false)
                ORDER BY id DESC
                LIMIT 1
                """,
                (tenant_id, external_payment_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = self.COLUMNS
            return dict(zip(cols, row))
        finally:
            cur.close()
            conn.close()

    def get_by_payment_id(self, tenant_id: str, payment_id: int) -> Optional[Dict[str, Any]]:
        """Fetch mapping row by local payment_receives.id."""
        conn = postgres_connection(tenant_id)
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                SELECT {self.SELECT_COLUMNS}
                FROM {self.TABLE}
                WHERE tenant_id = %s
                  AND payment_id = %s
                  AND (invalid IS NULL OR invalid = false)
                ORDER BY id DESC
                LIMIT 1
                """,
                (tenant_id, int(payment_id)),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = self.COLUMNS
            return dict(zip(cols, row))
        finally:
            cur.close()
            conn.close()

    def insert_mapping(
            self,
            *,
            tenant_id: str,
            customer_id: int,
            input_source: str,
            external_payment_id: str,
            external_invoice_id: Optional[str] = None,
            external_account_id: Optional[str] = None,
            sync_status: Optional[str] = None,
            payment_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Insert a mapping row.

        Idempotency:
          - If external_payment_id is provided: dedupe by (tenant_id, external_payment_id)
          - If payment_id is provided: dedupe by (tenant_id, payment_id)
        """

        if external_payment_id:
            existing = self.get_by_external_payment_id(tenant_id, external_payment_id)
            if existing:
                return {"inserted": False, "row": existing}
        if payment_id is not None:
            existing2 = self.get_by_payment_id(tenant_id, int(payment_id))
            if existing2:
                return {"inserted": False, "row": existing2}

        conn = postgres_connection(tenant_id)
        cur = conn.cursor()
        try:
            now = datetime.now(timezone.utc)
            cur.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    customer_id,
                    tenant_id,
                    input_source,
                    external_payment_id,
                    external_invoice_id,
                    external_account_id,
                    sync_status,
                    last_sync_at,
                    payment_id,
                    version,
                    invalid,
                    created_at,
                    updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    int(customer_id),
                    tenant_id,
                    input_source,
                    external_payment_id,
                    external_invoice_id,
                    external_account_id,
                    sync_status,
                    now,
                    int(payment_id) if payment_id is not None else None,
                    1,
                    False,
                    now,
                    now,
                ),
            )
            new_id = cur.fetchone()[0]
            return {"inserted": True, "id": new_id}
        except Exception as e:
            print(f"[PaymentExternalMapRepository.insert_mapping] Error: {e}")
            print(traceback.format_exc())
            raise
        finally:
            cur.close()
            conn.close()

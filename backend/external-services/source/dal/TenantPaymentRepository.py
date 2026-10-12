import os
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from source.dal.dbconnector import postgres_connection


class TenantPaymentRepository:
    """Read/write payments in a tenant DB (payment_receives)."""

    def get_payments_for_customer(
        self,
        tenant_db: str,
        customer_uid: str,
        limit: int = 500,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        conn = postgres_connection(tenant_db)
        if not conn:
            raise Exception(f"Tenant DB connection failed for db={tenant_db}")
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, amount, currency_code, customer_uid, received_time,
                       description, pay_method, reference_no, reference_type,
                       status, additional_info, fieldcraft_transaction_id,
                       payment_receipt_id, payment_reference_no, payment_transaction_id
                FROM public.payment_receives
                WHERE customer_uid = %s
                ORDER BY received_time ASC, id ASC
                LIMIT %s OFFSET %s
                """,
                (customer_uid, limit, offset),
            )
            rows = cur.fetchall() or []
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "id": r[0],
                        "amount": float(r[1]) if r[1] is not None else None,
                        "currency_code": r[2],
                        "customer_uid": r[3],
                        "received_time": r[4].isoformat() if r[4] else None,
                        "description": r[5],
                        "pay_method": r[6],
                        "reference_no": r[7],
                        "reference_type": r[8],
                        "status": r[9],
                        "additional_info": r[10],
                        "fieldcraft_transaction_id": r[11],
                        "payment_receipt_id": r[12],
                        "payment_reference_no": r[13],
                        "payment_transaction_id": r[14],
                    }
                )
            return out
        finally:
            cur.close()
            conn.close()

    def insert_payment_receive_from_qbo(
        self,
        tenant_db: str,
        *,
        customer_uid: str,
        amount: float,
        received_time: datetime,
        currency_code: Optional[str] = None,
        pay_method: Optional[str] = None,
        reference_no: Optional[str] = None,
        status: Optional[str] = None,
        additional_info: Optional[str] = None,
        payment_transaction_id: Optional[str] = None,
        payment_reference_no: Optional[str] = None,
        payment_receipt_id: Optional[str] = None,
        fieldcraft_transaction_id: Optional[str] = None,
    ) -> int:
        """Insert into payment_receives.

        IMPORTANT: payment_receives.chart_of_account_id is NOT NULL in your schema.
        We use DEFAULT_PAYMENT_CHART_OF_ACCOUNT_ID env var as a safe default.
        """
        chart_id_env = os.getenv("DEFAULT_PAYMENT_CHART_OF_ACCOUNT_ID")
        try:
            chart_of_account_id = int(chart_id_env) if chart_id_env else 1
        except Exception:
            chart_of_account_id = 1

        conn = postgres_connection(tenant_db)
        if not conn:
            raise Exception(f"Tenant DB connection failed for db={tenant_db}")
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO public.payment_receives (
                    amount,
                    currency_code,
                    customer_uid,
                    received_time,
                    pay_method,
                    reference_no,
                    status,
                    additional_info,
                    chart_of_account_id,
                    payment_transaction_id,
                    payment_reference_no,
                    payment_receipt_id,
                    updated_at,
                    fieldcraft_transaction_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    Decimal(str(amount)),
                    currency_code,
                    customer_uid,
                    received_time,
                    pay_method,
                    reference_no,
                    status,
                    additional_info,
                    chart_of_account_id,
                    payment_transaction_id,
                    payment_reference_no,
                    payment_receipt_id,
                    datetime.now(timezone.utc),
                    fieldcraft_transaction_id
                ),
            )
            new_id = cur.fetchone()[0]
            return int(new_id)
        except Exception as e:
            print(f"[TenantPaymentRepository.insert_payment_receive_from_qbo] Error: {e}")
            print(traceback.format_exc())
            raise
        finally:
            cur.close()
            conn.close()

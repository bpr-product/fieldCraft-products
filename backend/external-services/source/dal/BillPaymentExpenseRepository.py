from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Text, and_, cast, func, or_, select, union

from source.dao.baseDao import with_retry
from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
from source.dal.ReceiptRepository import ReceiptRepository
from source.dal.bill_payment_models import FinalPaymentExternalMapModel, PaymentExpenseModel
from source.dal.db_session import tenant_session_scope
from source.dal.project_models import ProjectModel
from source.dal.receipt_models import ReceiptModel

logger = logging.getLogger(__name__)


class BillPaymentExpenseRepository:
    STATUS_ASSIGNMENT_NEEDED = "ASSIGNMENT_NEEDED"
    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    STATUS_INVALID = "INVALID"

    def __init__(self) -> None:
        self.receipt_repo = ReceiptRepository()
        self.coa_repo = ChartOfAccountRepository()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _service_id_int(self, service_id: Any) -> int:
        return int(self._clean(service_id) or 0)

    def _to_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = self._clean(value).replace(",", "")
        if not text:
            return None
        cleaned = ''.join(ch for ch in text if ch.isdigit() or ch in '.-')
        if cleaned in {"", ".", "-", "-."}:
            return None
        try:
            return float(cleaned)
        except Exception:
            return None

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            try:
                dt = datetime.fromisoformat(raw)
            except Exception:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _to_yyyy_mm_dd(self, value: Any) -> Optional[str]:
        dt = self._parse_timestamp(value)
        if dt is not None:
            return dt.date().isoformat()
        raw = self._clean(value)
        if len(raw) >= 10 and raw[4] == '-' and raw[7] == '-':
            return raw[:10]
        return None

    def _normalize_payment_row(self, row: PaymentExpenseModel | Dict[str, Any] | None) -> Dict[str, Any]:
        if row is None:
            return {}
        if isinstance(row, dict):
            return dict(row)
        return {
            "id": int(row.id),
            "amount": float(row.amount) if row.amount is not None else None,
            "codes": row.codes,
            "created_at": row.created_at,
            "currency_code": row.currency_code,
            "description": row.description,
            "exchange_rate": row.exchange_rate,
            "payee_account_no": row.payee_account_no,
            "payee_id": int(row.payee_id) if row.payee_id is not None and str(row.payee_id).strip() else None,
            "reference_id": int(row.reference_id) if row.reference_id is not None and str(row.reference_id).strip() else None,
            "reference_no": row.reference_no,
            "reference_type": row.reference_type,
            "status": row.status,
            "updated_at": row.updated_at,
            "user_id": row.user_id,
            "chart_of_account_id": int(row.chart_of_account_id) if row.chart_of_account_id is not None and str(row.chart_of_account_id).strip() else None,
            "project_id": int(row.project_id) if row.project_id is not None else None,
        }

    def _mapping_to_dict(self, row: FinalPaymentExternalMapModel | None) -> Dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": int(row.id),
            "customer_id": int(row.customer_id) if row.customer_id is not None else None,
            "tenant_id": row.tenant_id,
            "external_payment_id": row.external_payment_id,
            "external_invoice_id": row.external_invoice_id,
            "external_account_id": row.external_account_id,
            "additional_info": row.additional_info,
            "sync_status": row.sync_status,
            "version": int(row.version) if row.version is not None else 1,
            "updated_at": row.updated_at,
            "payment_receives_id": int(row.payment_receives_id) if row.payment_receives_id is not None else None,
            "payment_expense_id": int(row.payment_expense_id) if row.payment_expense_id is not None else None,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "reason": row.reason,
            "sync_token": row.sync_token,
            "invalid": bool(row.invalid),
        }

    def _sync_projection(self, payment: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "amount": self._to_float(payment.get("amount")),
            "reference_id": payment.get("reference_id"),
            "reference_no": self._clean(payment.get("reference_no")),
            "status": self._clean(payment.get("status")),
            "chart_of_account_id": payment.get("chart_of_account_id"),
            "project_id": payment.get("project_id"),
            "date": self._to_yyyy_mm_dd(payment.get("updated_at") or payment.get("created_at")),
        }

    def sync_projection_changed(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return self._sync_projection(left) != self._sync_projection(right)

    def _build_reason_payload(self, message: str, **extra: Any) -> str:
        payload: Dict[str, Any] = {"message": self._clean(message)}
        for key, value in extra.items():
            if value is None:
                continue
            text = self._clean(value) if isinstance(value, str) else value
            if text in ("", None, [], {}):
                continue
            payload[key] = text
        return json.dumps(payload)

    def _parse_reason_payload(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = self._clean(value)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _build_additional_info_payload(self, **extra: Any) -> str:
        payload: Dict[str, Any] = {}
        for key, value in extra.items():
            if value is None:
                continue
            text = self._clean(value) if isinstance(value, str) else value
            if text in ("", None, [], {}):
                continue
            payload[key] = text
        return json.dumps(payload) if payload else ""

    @staticmethod
    def _build_reason(message: str, **extra: Any) -> str:
        payload: Dict[str, Any] = {"message": message}
        for key, value in extra.items():
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            payload[key] = value
        return json.dumps(payload)

    def _parse_additional_info_payload(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = self._clean(value)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _resolve_assignment_meta(self, mapping: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw_additional_info = (mapping or {}).get("additional_info")
        info = self._parse_additional_info_payload(raw_additional_info)
        account_id = self._clean(info.get("qboAccountId"))
        logger.debug(f"[DEBUG][_resolve_assignment_meta] mapping_id={( mapping or {}).get('id')} raw_additional_info={raw_additional_info!r} resolved_qboAccountId={account_id!r} external_account_id={(mapping or {}).get('external_account_id')!r}")
        return {
            "qboAccountId": account_id or None,
            "qboAccountName": self._clean(info.get("qboAccountName")) or None,
            "qboAccountType": self._clean(info.get("qboAccountType")) or None,
            "qboAccountDetailType": self._clean(info.get("qboAccountDetailType")) or None,
            "eligiblePayType": self._clean(info.get("eligiblePayType")) or None,
        }

    def _has_assigned_qbo_account(self, mapping: Optional[Dict[str, Any]]) -> bool:
        meta = self._resolve_assignment_meta(mapping)
        return bool(meta.get("qboAccountId"))

    def _effective_sync_status(self, mapping: Optional[Dict[str, Any]]) -> str:
        raw_status = self._clean((mapping or {}).get("sync_status")).upper()
        if raw_status in {self.STATUS_SYNCED, self.STATUS_FAILED, self.STATUS_IMPORT_FAILED, self.STATUS_INVALID}:
            logger.debug(f"[DEBUG][_effective_sync_status] mapping_id={(mapping or {}).get('id')} raw_status={raw_status!r} → direct return")
            return raw_status
        has_account = self._has_assigned_qbo_account(mapping)
        effective = self.STATUS_PENDING_PUSH if has_account else self.STATUS_ASSIGNMENT_NEEDED
        logger.debug(f"[DEBUG][_effective_sync_status] mapping_id={(mapping or {}).get('id')} raw_status={raw_status!r} has_account={has_account} → {effective}")
        return effective

    def get_payment_latest_local_timestamp(self, payment: Optional[Dict[str, Any]], mapping: Optional[Dict[str, Any]] = None) -> Optional[datetime]:
        candidates: List[datetime] = []
        for row in (payment or {}, mapping or {}):
            for key in ("updated_at", "endorsement_effective_date", "version_effective_date", "created_at"):
                dt = self._parse_timestamp(row.get(key))
                if dt is not None:
                    candidates.append(dt)
        return max(candidates) if candidates else None

    @with_retry
    def get_payment_expense_by_id(self, tenant_id: str, payment_expense_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(PaymentExpenseModel, int(payment_expense_id))
            return self._normalize_payment_row(row) if row else None

    @with_retry
    def find_existing_payment_expense(self, tenant_id: str, *, receipt_id: int, amount: Optional[float], txn_date: Optional[str] = None, reference_no: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(PaymentExpenseModel)
                .where(PaymentExpenseModel.reference_id == int(receipt_id))
                .order_by(PaymentExpenseModel.updated_at.desc().nullslast(), PaymentExpenseModel.id.desc())
            )
            rows = session.execute(stmt).scalars().all()
            if not rows:
                return None
            target_amount = self._to_float(amount)
            for row in rows:
                if target_amount is not None and self._to_float(row.amount) is not None and abs(float(row.amount) - target_amount) > 0.009:
                    continue
                if txn_date:
                    row_date = self._to_yyyy_mm_dd(row.updated_at or row.created_at)
                    if row_date and row_date != txn_date:
                        continue
                if reference_no and self._clean(row.reference_no) and self._clean(row.reference_no) != self._clean(reference_no):
                    continue
                return self._normalize_payment_row(row)
            return None

    @with_retry
    def create_payment_expense_for_tenant(self, tenant_id: str, payment_data: Dict[str, Any]) -> int:
        logger.debug(f"[DEBUG][create_payment_expense_for_tenant] tenant={tenant_id} reference_id={payment_data.get('reference_id')} amount={payment_data.get('amount')} reference_no={payment_data.get('reference_no')!r}")
        now = payment_data.get("updated_at") or payment_data.get("created_at") or self._now()
        with tenant_session_scope(tenant_id) as session:
            row = PaymentExpenseModel(
                amount=self._to_float(payment_data.get("amount")) or 0.0,
                codes=payment_data.get("codes"),
                created_at=payment_data.get("created_at") or now,
                currency_code=self._clean(payment_data.get("currency_code")) or None,
                description=self._clean(payment_data.get("description")) or "Bill",
                exchange_rate=self._clean(payment_data.get("exchange_rate")) or None,
                payee_account_no=self._clean(payment_data.get("payee_account_no")) or None,
                payee_id=payment_data.get("payee_id"),
                reference_id=int(payment_data["reference_id"]) if payment_data.get("reference_id") is not None else None,
                reference_no=self._clean(payment_data.get("reference_no")) or None,
                reference_type=self._clean(payment_data.get("reference_type")) or "Bill",
                status=self._clean(payment_data.get("status")) or "Paid",
                updated_at=payment_data.get("updated_at") or now,
                user_id=self._clean(payment_data.get("user_id")) or "qbo-import",
                chart_of_account_id=int(payment_data["chart_of_account_id"]) if payment_data.get("chart_of_account_id") is not None else None,
                project_id=int(payment_data["project_id"]) if payment_data.get("project_id") is not None else None,
            )
            session.add(row)
            session.flush()
            row_id = int(row.id)
            session.commit()
            logger.debug(f"[DEBUG][create_payment_expense_for_tenant] → created payment_expense_id={row_id}")
            return row_id

    @with_retry
    def update_payment_expense_from_qbo(self, tenant_id: str, *, payment_expense_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"[DEBUG][update_payment_expense_from_qbo] tenant={tenant_id} payment_expense_id={payment_expense_id} amount={payload.get('amount')} reference_no={payload.get('reference_no')!r}")
        with tenant_session_scope(tenant_id) as session:
            row = session.get(PaymentExpenseModel, int(payment_expense_id))
            if not row:
                raise ValueError(f"Payment expense {payment_expense_id} not found")
            amount = self._to_float(payload.get("amount"))
            if amount is not None:
                row.amount = amount
            row.currency_code = self._clean(payload.get("currency_code")) or row.currency_code
            row.description = self._clean(payload.get("description")) or row.description or "Bill"
            row.reference_no = self._clean(payload.get("reference_no")) or row.reference_no
            row.reference_type = self._clean(payload.get("reference_type")) or row.reference_type or "Bill"
            row.status = self._clean(payload.get("status")) or row.status or "Paid"
            if payload.get("chart_of_account_id") is not None:
                row.chart_of_account_id = int(payload["chart_of_account_id"])
            if payload.get("project_id") is not None:
                row.project_id = int(payload["project_id"])
            if payload.get("reference_id") is not None:
                row.reference_id = int(payload["reference_id"])
            row.updated_at = payload.get("updated_at") or self._now()
            session.add(row)
            session.flush()
            result = self._normalize_payment_row(row)
            session.commit()
            return result

    def build_payment_payload_from_qbo(self, qbo_bill_payment: Dict[str, Any], *, receipt_id: int, chart_of_account_id: Optional[int], project_id: Optional[int], source_updated_at: Optional[datetime]) -> Dict[str, Any]:
        return {
            "amount": self._to_float(qbo_bill_payment.get("TotalAmt")),
            "currency_code": ((qbo_bill_payment.get("CurrencyRef") or {}).get("value") if isinstance(qbo_bill_payment.get("CurrencyRef"), dict) else None),
            "description": "Bill",
            "reference_id": int(receipt_id),
            "reference_no": self._clean(qbo_bill_payment.get("DocNumber") or qbo_bill_payment.get("PaymentRefNum")) or None,
            "reference_type": "Bill",
            "status": "Paid",
            "updated_at": source_updated_at or self._now(),
            "user_id": "qbo-import",
            "chart_of_account_id": chart_of_account_id,
            "project_id": project_id,
        }

    def _get_next_mapping_version(self, session, *, tenant_id: str, service_id: Any, payment_expense_id: Optional[int], external_payment_id: Optional[str]) -> int:
        stmt = select(func.coalesce(func.max(FinalPaymentExternalMapModel.version), 0) + 1).where(
            FinalPaymentExternalMapModel.tenant_id == tenant_id,
            FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
            FinalPaymentExternalMapModel.payment_receives_id.is_(None),
        )
        if payment_expense_id is not None:
            stmt = stmt.where(FinalPaymentExternalMapModel.payment_expense_id == int(payment_expense_id))
        elif self._clean(external_payment_id):
            stmt = stmt.where(FinalPaymentExternalMapModel.external_payment_id == self._clean(external_payment_id))
        return int(session.execute(stmt).scalar() or 1)

    def _create_mapping(self, session, *, tenant_id: str, service_id: Any, payment_expense_id: Optional[int], external_payment_id: Optional[str], external_invoice_id: Optional[str], external_account_id: Optional[str], additional_info: Optional[str], sync_status: str, reason: Optional[str], version: Optional[int] = None, effective_at: Optional[datetime] = None, updated_at: Optional[datetime] = None) -> FinalPaymentExternalMapModel:
        effective_ts = effective_at or updated_at or self._now()
        updated_ts = updated_at or effective_ts
        row = FinalPaymentExternalMapModel(
            customer_id=None,
            tenant_id=tenant_id,
            external_payment_id=self._clean(external_payment_id) or None,
            external_invoice_id=self._clean(external_invoice_id) or None,
            external_account_id=self._clean(external_account_id) or None,
            additional_info=self._clean(additional_info) or None,
            sync_status=sync_status,
            version=int(version or self._get_next_mapping_version(session, tenant_id=tenant_id, service_id=service_id, payment_expense_id=payment_expense_id, external_payment_id=external_payment_id)),
            updated_at=updated_ts,
            payment_receives_id=None,
            payment_expense_id=int(payment_expense_id) if payment_expense_id is not None else None,
            service_id=self._service_id_int(service_id),
            endorsement_effective_date=effective_ts,
            endorsement_expiry_date=None,
            version_effective_date=None,
            version_expiry_date=None,
            reason=self._clean(reason) or None,
        )
        session.add(row)
        session.flush()
        return row

    @with_retry
    def get_current_mapping_by_payment_expense_id(self, tenant_id: str, service_id: Any, payment_expense_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(FinalPaymentExternalMapModel)
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                    FinalPaymentExternalMapModel.payment_expense_id == int(payment_expense_id),
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(FinalPaymentExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    @with_retry
    def get_current_mapping_by_external_payment_id(self, tenant_id: str, service_id: Any, external_payment_id: str) -> Optional[Dict[str, Any]]:
        external_id = self._clean(external_payment_id)
        if not external_id:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(FinalPaymentExternalMapModel)
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                    FinalPaymentExternalMapModel.external_payment_id == external_id,
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(FinalPaymentExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    @with_retry
    def get_payment_expense_rows_by_ids(self, tenant_id: str, payment_expense_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        clean_ids = [int(pid) for pid in payment_expense_ids if str(pid).strip()]
        if not clean_ids:
            return {}
        with tenant_session_scope(tenant_id) as session:
            stmt = select(PaymentExpenseModel).where(PaymentExpenseModel.id.in_(clean_ids))
            rows = session.execute(stmt).scalars().all()
            return {int(row.id): self._normalize_payment_row(row) for row in rows}

    @with_retry
    def assign_qbo_payment_account(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        payment_expense_ids: List[int],
        external_account_id: str,
        qbo_account_name: Optional[str] = None,
        qbo_account_type: Optional[str] = None,
        qbo_account_detail_type: Optional[str] = None,
        assigned_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_external_account_id = self._clean(external_account_id)
        if not clean_external_account_id:
            raise ValueError("external_account_id is required")
        normalized_ids = sorted({int(pid) for pid in payment_expense_ids if str(pid).strip()})
        if not normalized_ids:
            raise ValueError("At least one payment_expense_id is required")
        existing_payments = self.get_payment_expense_rows_by_ids(tenant_id, normalized_ids)
        missing_ids = [pid for pid in normalized_ids if pid not in existing_payments]
        if missing_ids:
            raise ValueError(f"Payment expense not found for ids: {', '.join(str(pid) for pid in missing_ids[:10])}")

        additional_info = self._build_additional_info_payload(
            qboAccountId=clean_external_account_id,
            qboAccountName=qbo_account_name,
            qboAccountType=qbo_account_type,
            qboAccountDetailType=qbo_account_detail_type,
            eligiblePayType=("CreditCard" if self._clean(qbo_account_type).lower() == "credit card" else "Check"),
            assignedBy=assigned_by,
            source="assignment",
        )
        reason_str = self._build_reason(
            "QuickBooks payment account assigned",
            qboAccountId=clean_external_account_id,
            qboAccountName=qbo_account_name,
            qboAccountType=qbo_account_type,
            qboAccountDetailType=qbo_account_detail_type,
        )
        assigned_rows: List[Dict[str, Any]] = []
        with tenant_session_scope(tenant_id) as session:
            for payment_expense_id in normalized_ids:
                stmt = (
                    select(FinalPaymentExternalMapModel)
                    .where(
                        FinalPaymentExternalMapModel.tenant_id == tenant_id,
                        FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                        FinalPaymentExternalMapModel.payment_expense_id == int(payment_expense_id),
                        FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                        func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    )
                    .order_by(FinalPaymentExternalMapModel.id.desc())
                )
                current = session.execute(stmt).scalars().first()
                now = self._now()
                if current is not None:
                    current.external_account_id = clean_external_account_id
                    current.additional_info = additional_info or None
                    current.sync_status = self.STATUS_PENDING_PUSH
                    current.reason = reason_str
                    current.endorsement_effective_date = now
                    current.updated_at = now
                    session.add(current)
                    session.flush()
                    assigned_rows.append(self._mapping_to_dict(current))
                else:
                    row = self._create_mapping(
                        session,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        payment_expense_id=payment_expense_id,
                        external_payment_id=None,
                        external_invoice_id=None,
                        external_account_id=clean_external_account_id,
                        additional_info=additional_info,
                        sync_status=self.STATUS_PENDING_PUSH,
                        reason=reason_str,
                        effective_at=now,
                        updated_at=now,
                    )
                    assigned_rows.append(self._mapping_to_dict(row))
            session.commit()
        return {
            "assignedCount": len(assigned_rows),
            "paymentExpenseIds": normalized_ids,
            "externalAccountId": clean_external_account_id,
            "rows": assigned_rows,
        }

    @with_retry
    def get_export_candidates(self, tenant_id: str, service_id: Any, *, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        logger.debug(f"[DEBUG][get_export_candidates] tenant={tenant_id} service_id={service_id} limit={limit} offset={offset}")
        results: List[Dict[str, Any]] = []
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(FinalPaymentExternalMapModel)
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    FinalPaymentExternalMapModel.payment_expense_id.is_not(None),
                    FinalPaymentExternalMapModel.additional_info.is_not(None),
                    func.length(func.trim(func.coalesce(FinalPaymentExternalMapModel.additional_info, ""))) > 0,
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")).in_([self.STATUS_PENDING_PUSH, self.STATUS_FAILED]),
                )
                .order_by(FinalPaymentExternalMapModel.id.asc())
                .offset(int(offset))
                .limit(int(limit))
            )
            rows = session.execute(stmt).scalars().all()
            for mapping_row in rows:
                payment_row = session.get(PaymentExpenseModel, int(mapping_row.payment_expense_id)) if mapping_row.payment_expense_id is not None else None
                if not payment_row:
                    logger.debug(f"[DEBUG][get_export_candidates] mapping_id={mapping_row.id} payment_expense_id={mapping_row.payment_expense_id} → payment_row not found, skipping")
                    continue
                results.append({"payment": self._normalize_payment_row(payment_row), "mapping": self._mapping_to_dict(mapping_row)})
        logger.debug(f"[DEBUG][get_export_candidates] → found {len(results)} candidate(s): {[r['mapping'].get('id') for r in results]}")
        return results

    @with_retry
    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_payment_id: Optional[str],
        external_invoice_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        additional_info: Optional[str] = None,
        reason: Optional[str] = None,
        sync_status: Optional[str] = None,
        payment_expense_id: Optional[int] = None,
    ) -> None:
        logger.debug(f"[DEBUG][mark_mapping_synced] mapping_id={mapping_id} external_payment_id={external_payment_id!r} external_account_id={external_account_id!r} payment_expense_id={payment_expense_id!r} additional_info={additional_info!r} sync_status={sync_status!r}")
        with tenant_session_scope(tenant_id) as session:
            row = session.get(FinalPaymentExternalMapModel, int(mapping_id))
            if not row:
                logger.debug(f"[DEBUG][mark_mapping_synced] mapping_id={mapping_id} -> row NOT FOUND, skipping")
                return
            if external_payment_id is not None:
                row.external_payment_id = self._clean(external_payment_id) or row.external_payment_id
            if external_invoice_id is not None:
                row.external_invoice_id = self._clean(external_invoice_id) or row.external_invoice_id
            if external_account_id is not None:
                row.external_account_id = self._clean(external_account_id) or None
            if payment_expense_id is not None:
                row.payment_expense_id = int(payment_expense_id)
            if additional_info is not None:
                row.additional_info = self._clean(additional_info) or None
            row.sync_status = sync_status or self.STATUS_SYNCED
            row.updated_at = self._now()
            row.reason = self._clean(reason) or None
            session.add(row)
            session.commit()
            logger.debug(f"[DEBUG][mark_mapping_synced] -> mapping_id={mapping_id} saved sync_status={row.sync_status!r} payment_expense_id={row.payment_expense_id!r} external_payment_id={row.external_payment_id!r} external_account_id={row.external_account_id!r} additional_info={row.additional_info!r}")

    @with_retry
    def mark_mapping_failed(self, *, tenant_id: str, mapping_id: int, status: Optional[str] = None, reason: Optional[str] = None) -> None:
        logger.debug(f"[DEBUG][mark_mapping_failed] mapping_id={mapping_id} status={status!r} reason={reason!r}")
        with tenant_session_scope(tenant_id) as session:
            row = session.get(FinalPaymentExternalMapModel, int(mapping_id))
            if not row:
                logger.debug(f"[DEBUG][mark_mapping_failed] mapping_id={mapping_id} → row NOT FOUND, skipping")
                return
            row.sync_status = status or self.STATUS_FAILED
            row.updated_at = self._now()
            row.reason = self._clean(reason) or None
            session.add(row)
            session.commit()
            logger.debug(f"[DEBUG][mark_mapping_failed] → mapping_id={mapping_id} saved sync_status={row.sync_status!r}")

    @with_retry
    def close_mapping_as_invalid(self, tenant_id: str, mapping_id: int) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(FinalPaymentExternalMapModel, int(mapping_id))
            if not row:
                return
            now = self._now()
            row.sync_status = self.STATUS_INVALID
            row.endorsement_expiry_date = now
            row.updated_at = now
            session.add(row)
            session.commit()

    @with_retry
    def rotate_mapping_for_payment_expense(self, *, tenant_id: str, service_id: Any, payment_expense_id: Optional[int], external_payment_id: Optional[str], external_invoice_id: Optional[str], external_account_id: Optional[str], additional_info: Optional[str] = None, sync_status: str = "", reason: Optional[str] = None, effective_at: Optional[datetime] = None) -> Dict[str, Any]:
        logger.debug(f"[DEBUG][rotate_mapping_for_payment_expense] tenant={tenant_id} payment_expense_id={payment_expense_id} external_payment_id={external_payment_id!r} external_account_id={external_account_id!r} additional_info={additional_info!r} sync_status={sync_status!r}")
        with tenant_session_scope(tenant_id) as session:
            existing = None
            if payment_expense_id is not None:
                stmt = (
                    select(FinalPaymentExternalMapModel)
                    .where(
                        FinalPaymentExternalMapModel.tenant_id == tenant_id,
                        FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                        FinalPaymentExternalMapModel.payment_expense_id == int(payment_expense_id),
                        FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                        func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    )
                    .order_by(FinalPaymentExternalMapModel.id.desc())
                )
                existing = session.execute(stmt).scalars().first()
            elif self._clean(external_payment_id):
                stmt = (
                    select(FinalPaymentExternalMapModel)
                    .where(
                        FinalPaymentExternalMapModel.tenant_id == tenant_id,
                        FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                        FinalPaymentExternalMapModel.external_payment_id == self._clean(external_payment_id),
                        FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                        func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    )
                    .order_by(FinalPaymentExternalMapModel.id.desc())
                )
                existing = session.execute(stmt).scalars().first()

            effective_ts = effective_at or self._now()
            if existing is not None:
                logger.debug(f"[DEBUG][rotate_mapping_for_payment_expense] closing existing mapping_id={existing.id} (sync_status={existing.sync_status!r} additional_info={existing.additional_info!r}) → INVALID")
                existing.sync_status = self.STATUS_INVALID
                existing.endorsement_expiry_date = effective_ts
                existing.updated_at = effective_ts
                session.add(existing)
                session.flush()
            else:
                logger.debug(f"[DEBUG][rotate_mapping_for_payment_expense] no existing mapping found, creating fresh")

            row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                payment_expense_id=payment_expense_id,
                external_payment_id=external_payment_id,
                external_invoice_id=external_invoice_id,
                external_account_id=external_account_id,
                additional_info=additional_info,
                sync_status=sync_status,
                reason=reason,
                effective_at=effective_ts,
                updated_at=effective_ts,
            )
            result = self._mapping_to_dict(row)
            session.commit()
            logger.debug(f"[DEBUG][rotate_mapping_for_payment_expense] → new mapping_id={result.get('id')} sync_status={result.get('sync_status')!r} additional_info={result.get('additional_info')!r}")
            return result

    @with_retry
    def create_pending_push_mapping(self, tenant_id: str, service_id: Any, payment_expense_id: int, external_payment_id: Optional[str] = None, external_invoice_id: Optional[str] = None, external_account_id: Optional[str] = None, additional_info: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        has_assignment = bool(self._parse_additional_info_payload(additional_info).get("qboAccountId"))
        return self.rotate_mapping_for_payment_expense(
            tenant_id=tenant_id,
            service_id=service_id,
            payment_expense_id=payment_expense_id,
            external_payment_id=external_payment_id,
            external_invoice_id=external_invoice_id,
            external_account_id=external_account_id,
            additional_info=additional_info,
            sync_status=self.STATUS_PENDING_PUSH if has_assignment else self.STATUS_ASSIGNMENT_NEEDED,
            reason=reason,
        )

    @with_retry
    def upsert_import_failed_mapping(self, *, tenant_id: str, service_id: Any, external_payment_id: str, external_invoice_id: Optional[str] = None, external_account_id: Optional[str] = None, payment_expense_id: Optional[int] = None, receipt_id: Optional[int] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        logger.debug(f"[DEBUG][upsert_import_failed_mapping] tenant={tenant_id} service_id={service_id} external_payment_id={external_payment_id!r} external_invoice_id={external_invoice_id!r} payment_expense_id={payment_expense_id} receipt_id={receipt_id} reason={reason!r}")
        external_payment_id = self._clean(external_payment_id)
        if not external_payment_id:
            raise ValueError("external_payment_id is required for IMPORT_FAILED mapping")

        resolved_receipt_id: Optional[int] = int(receipt_id) if receipt_id is not None else None
        if resolved_receipt_id is None and external_invoice_id:
            receipt_mapping = self.receipt_repo.get_current_mapping_by_external_receipt_id(
                tenant_id=tenant_id,
                service_id=service_id,
                external_receipt_id=external_invoice_id,
            )
            if receipt_mapping and receipt_mapping.get("receipt_id") is not None:
                resolved_receipt_id = int(receipt_mapping["receipt_id"])

        if payment_expense_id is not None:
            return self.rotate_mapping_for_payment_expense(
                tenant_id=tenant_id,
                service_id=service_id,
                payment_expense_id=payment_expense_id,
                external_payment_id=external_payment_id,
                external_invoice_id=external_invoice_id,
                external_account_id=external_account_id,
                sync_status=self.STATUS_IMPORT_FAILED,
                reason=reason,
            )

        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(FinalPaymentExternalMapModel)
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                    FinalPaymentExternalMapModel.external_payment_id == external_payment_id,
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(FinalPaymentExternalMapModel.id.desc())
            )
            existing = session.execute(stmt).scalars().first()
            if existing is None:
                now = self._now()
                if resolved_receipt_id is None:
                    # Linked QBO Bill is not mapped to a FIELDCRAFT receipt yet.
                    # Store an orphan IMPORT_FAILED mapping only; do not create payment_expense
                    # because payment_expense.reference_id must be a real receipt PK.
                    row = self._create_mapping(
                        session,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        payment_expense_id=None,
                        external_payment_id=external_payment_id,
                        external_invoice_id=external_invoice_id,
                        external_account_id=external_account_id,
                        additional_info=None,
                        sync_status=self.STATUS_IMPORT_FAILED,
                        reason=reason,
                        effective_at=now,
                        updated_at=now,
                    )
                    result = self._mapping_to_dict(row)
                    session.commit()
                    logger.debug(f"[DEBUG][upsert_import_failed_mapping] -> new orphan IMPORT_FAILED mapping_id={result.get('id')} external_payment_id={external_payment_id!r} external_invoice_id={external_invoice_id!r}")
                    return result

                stub = PaymentExpenseModel(
                    amount=0.0,
                    description="Bill",
                    reference_id=resolved_receipt_id,
                    reference_type="Bill",
                    status="import_pending",
                    user_id="qbo-import",
                    created_at=now,
                    updated_at=now,
                )
                session.add(stub)
                session.flush()
                stub_id = int(stub.id)
                row = self._create_mapping(
                    session,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    payment_expense_id=stub_id,
                    external_payment_id=external_payment_id,
                    external_invoice_id=external_invoice_id,
                    external_account_id=external_account_id,
                    additional_info=None,
                    sync_status=self.STATUS_IMPORT_FAILED,
                    reason=reason,
                )
                result = self._mapping_to_dict(row)
                session.commit()
                logger.debug(f"[DEBUG][upsert_import_failed_mapping] -> new IMPORT_FAILED mapping_id={result.get('id')} linked to stub payment_expense_id={stub_id} reference_id={resolved_receipt_id}")
                return result

            now = self._now()
            existing.sync_status = self.STATUS_IMPORT_FAILED
            existing.reason = self._clean(reason) or None
            existing.updated_at = now
            if external_invoice_id is not None:
                existing.external_invoice_id = self._clean(external_invoice_id) or existing.external_invoice_id
            if external_account_id is not None:
                existing.external_account_id = self._clean(external_account_id) or None
            session.add(existing)
            session.commit()
            result = self._mapping_to_dict(existing)
            logger.debug(f"[DEBUG][upsert_import_failed_mapping] updated existing mapping_id={existing.id} to IMPORT_FAILED (preserved payment_expense_id={existing.payment_expense_id})")
            return result

    @with_retry
    def get_open_import_failed_external_payment_ids(self, tenant_id: str, service_id: Any) -> List[str]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(FinalPaymentExternalMapModel.external_payment_id)
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == self._service_id_int(service_id),
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    FinalPaymentExternalMapModel.external_payment_id.is_not(None),
                    FinalPaymentExternalMapModel.sync_status == self.STATUS_IMPORT_FAILED,
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(FinalPaymentExternalMapModel.id.desc())
            )
            values = session.execute(stmt).scalars().all()
            seen: List[str] = []
            for value in values:
                text = self._clean(value)
                if text and text not in seen:
                    seen.append(text)
            return seen

    @with_retry
    def get_bill_payment_cdc_page(self, tenant_id: str, service_id: Any, *, page: int = 1, page_size: int = 25, search: Optional[str] = None, sync_status: Optional[str] = None, mapped_only: bool = False) -> Dict[str, Any]:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_term = self._clean(search)
        normalized_status = self._clean(sync_status).upper()

        with tenant_session_scope(tenant_id) as session:
            max_eed_mapped = (
                select(
                    FinalPaymentExternalMapModel.payment_expense_id.label("payment_expense_id"),
                    func.max(FinalPaymentExternalMapModel.endorsement_effective_date).label("max_eed"),
                )
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == service_id_int,
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    FinalPaymentExternalMapModel.payment_expense_id.is_not(None),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(FinalPaymentExternalMapModel.payment_expense_id)
                .subquery()
            )
            latest_mapped_ids = (
                select(func.max(FinalPaymentExternalMapModel.id).label("map_id"))
                .join(
                    max_eed_mapped,
                    (FinalPaymentExternalMapModel.payment_expense_id == max_eed_mapped.c.payment_expense_id)
                    & (FinalPaymentExternalMapModel.endorsement_effective_date == max_eed_mapped.c.max_eed),
                )
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == service_id_int,
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    FinalPaymentExternalMapModel.payment_expense_id.is_not(None),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(FinalPaymentExternalMapModel.payment_expense_id)
            )
            latest_orphan_ids = (
                select(func.max(FinalPaymentExternalMapModel.id).label("map_id"))
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == service_id_int,
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    FinalPaymentExternalMapModel.payment_expense_id.is_(None),
                    FinalPaymentExternalMapModel.external_payment_id.is_not(None),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(FinalPaymentExternalMapModel.external_payment_id)
            )
            combined_ids = union(latest_mapped_ids, latest_orphan_ids).subquery()

            base_stmt = (
                select(FinalPaymentExternalMapModel, PaymentExpenseModel, ReceiptModel, ProjectModel)
                .where(FinalPaymentExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .outerjoin(PaymentExpenseModel, PaymentExpenseModel.id == FinalPaymentExternalMapModel.payment_expense_id)
                .outerjoin(ReceiptModel, ReceiptModel.id == PaymentExpenseModel.reference_id)
                .outerjoin(
                    ProjectModel,
                    or_(
                        ProjectModel.id == PaymentExpenseModel.project_id,
                        and_(PaymentExpenseModel.project_id.is_(None), ProjectModel.id == ReceiptModel.project_id),
                    ),
                )
            )
            if normalized_status and normalized_status != "ALL":
                if normalized_status == self.STATUS_ASSIGNMENT_NEEDED:
                    base_stmt = base_stmt.where(
                        or_(FinalPaymentExternalMapModel.additional_info.is_(None), func.length(func.trim(func.coalesce(FinalPaymentExternalMapModel.additional_info, ""))) == 0),
                        func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_IMPORT_FAILED,
                    )
                elif normalized_status == self.STATUS_PENDING_PUSH:
                    base_stmt = base_stmt.where(func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) == self.STATUS_PENDING_PUSH, FinalPaymentExternalMapModel.additional_info.is_not(None), func.length(func.trim(func.coalesce(FinalPaymentExternalMapModel.additional_info, ""))) > 0)
                else:
                    base_stmt = base_stmt.where(func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) == normalized_status)
            if mapped_only:
                # Keep orphan IMPORT_FAILED rows visible. They do not have a
                # payment_expense_id until the linked QBO bill is mapped in FIELDCRAFT.
                base_stmt = base_stmt.where(
                    or_(
                        FinalPaymentExternalMapModel.payment_expense_id.is_not(None),
                        func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) == self.STATUS_IMPORT_FAILED,
                    )
                )
            if search_term:
                like_term = f"%{search_term.lower()}%"
                project_search_fields = (
                    func.lower(func.coalesce(ProjectModel.description, "")),
                    func.lower(func.coalesce(ProjectModel.order_no, "")),
                    func.lower(func.coalesce(ProjectModel.location, "")),
                    func.lower(func.coalesce(ProjectModel.trade, "")),
                    func.lower(func.coalesce(cast(ProjectModel.id, Text), "")),
                    func.lower(func.coalesce(cast(ProjectModel.project_uid, Text), "")),
                    func.lower(func.coalesce(cast(ProjectModel.customer_uid, Text), "")),
                )
                base_stmt = base_stmt.where(
                    or_(
                        func.lower(func.coalesce(PaymentExpenseModel.reference_no, "")).like(like_term),
                        func.lower(func.coalesce(PaymentExpenseModel.description, "")).like(like_term),
                        func.lower(func.coalesce(cast(PaymentExpenseModel.reference_id, Text), "")).like(like_term),
                        func.lower(func.coalesce(cast(PaymentExpenseModel.id, Text), "")).like(like_term),
                        func.lower(func.coalesce(cast(PaymentExpenseModel.amount, Text), "")).like(like_term),
                        func.lower(func.coalesce(FinalPaymentExternalMapModel.external_payment_id, "")).like(like_term),
                        func.lower(func.coalesce(FinalPaymentExternalMapModel.external_invoice_id, "")).like(like_term),
                        func.lower(func.coalesce(FinalPaymentExternalMapModel.external_account_id, "")).like(like_term),
                        func.lower(func.coalesce(FinalPaymentExternalMapModel.additional_info, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.invoice_number, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.order_number, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.description, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.name, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.receipt_additional_info, "")).like(like_term),
                        func.lower(func.coalesce(cast(ReceiptModel.receipt_uuid, Text), "")).like(like_term),
                        *[field.like(like_term) for field in project_search_fields],
                    )
                )

            total_records = int(session.execute(select(func.count()).select_from(base_stmt.subquery())).scalar() or 0)
            paged_stmt = base_stmt.order_by(FinalPaymentExternalMapModel.updated_at.desc(), FinalPaymentExternalMapModel.id.desc()).offset(offset).limit(page_size)
            records = session.execute(paged_stmt).all()

            rows: List[Dict[str, Any]] = []
            for mapping_row, payment_row, receipt_row, project_row in records:
                mapping = self._mapping_to_dict(mapping_row)
                payment = self._normalize_payment_row(payment_row) if payment_row else {}
                linked_receipt_info = self.receipt_repo.parse_additional_info(receipt_row.receipt_additional_info) if receipt_row else {}
                receipt_id = payment.get("reference_id")
                bill_display_name = (
                    self._clean(receipt_row.invoice_number) if receipt_row else None
                ) or self._clean(linked_receipt_info.get("billNo")) or (
                    f"#{receipt_id}" if receipt_id is not None else (f"QBO Bill #{mapping.get('external_invoice_id')}" if mapping.get("external_invoice_id") else None)
                )
                project_name = self._clean(project_row.description) or self._clean(project_row.order_no) or None if project_row else None
                display_name = self._clean(payment.get("reference_no")) or (str(payment.get("id")) if payment_row and payment.get("id") is not None else f"QBO #{mapping.get('external_payment_id')}")
                assignment_meta = self._resolve_assignment_meta(mapping)
                effective_status = self._effective_sync_status(mapping)
                rows.append({
                    "paymentExpenseId": payment.get("id"),
                    "displayName": display_name,
                    "receiptId": receipt_id,
                    "billDisplayName": bill_display_name,
                    "billName": receipt_row.name if receipt_row else None,
                    "billInvoiceNumber": receipt_row.invoice_number if receipt_row else None,
                    "vendorId": receipt_row.vendor_id if receipt_row else None,
                    "vendorCompany": self._clean(linked_receipt_info.get("companyName")) or None,
                    "amount": payment.get("amount"),
                    "status": payment.get("status"),
                    "paymentDate": payment.get("updated_at") or payment.get("created_at"),
                    "chartOfAccountId": payment.get("chart_of_account_id"),
                    "projectId": payment.get("project_id") or (receipt_row.project_id if receipt_row else None),
                    "projectName": project_name,
                    "mappingId": mapping.get("id"),
                    "externalPaymentId": mapping.get("external_payment_id"),
                    "externalInvoiceId": mapping.get("external_invoice_id"),
                    "externalAccountId": assignment_meta.get("qboAccountId") or mapping.get("external_account_id"),
                    "syncStatus": effective_status,
                    "mappingVersion": mapping.get("version"),
                    "mappingUpdatedAt": mapping.get("updated_at"),
                    "linked": payment_row is not None,
                    "reason": mapping.get("reason"),
                    "assignedAccountName": assignment_meta.get("qboAccountName"),
                    "assignedAccountType": assignment_meta.get("qboAccountType"),
                    "assignedAccountDetailType": assignment_meta.get("qboAccountDetailType"),
                    "assignedEligiblePayType": assignment_meta.get("eligiblePayType"),
                    "source": "QuickBooks" if payment_row is None or self._clean(payment.get("user_id")) == "qbo-import" else "FIELDCRAFT",
                })

            # ── Summary: counts from the combined mapping set (unfiltered) ──────
            # Mirrors the vendor/bills pattern: always reflects global totals so
            # metric cards stay stable regardless of the current filter or page.
            summary_stmt = (
                select(FinalPaymentExternalMapModel.sync_status, func.count())
                .where(FinalPaymentExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .group_by(FinalPaymentExternalMapModel.sync_status)
            )
            raw_counts = session.execute(summary_stmt).all()
            status_counts: Dict[str, int] = {}
            for status, count in raw_counts:
                key = (status or "UNKNOWN").upper()
                status_counts[key] = int(count or 0)

            total_payments = int(sum(status_counts.values()))

            assigned_count = int(session.execute(
                select(func.count())
                .where(
                    FinalPaymentExternalMapModel.id.in_(select(combined_ids.c.map_id)),
                    FinalPaymentExternalMapModel.additional_info.is_not(None),
                    func.length(func.trim(func.coalesce(FinalPaymentExternalMapModel.additional_info, ""))) > 0,
                )
            ).scalar() or 0)
            unassigned_count = int(session.execute(
                select(func.count())
                .where(
                    FinalPaymentExternalMapModel.id.in_(select(combined_ids.c.map_id)),
                    or_(FinalPaymentExternalMapModel.additional_info.is_(None), func.length(func.trim(func.coalesce(FinalPaymentExternalMapModel.additional_info, ""))) == 0),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_IMPORT_FAILED,
                )
            ).scalar() or 0)

            ready_to_sync_count = int(session.execute(
                select(func.count())
                .where(
                    FinalPaymentExternalMapModel.id.in_(select(combined_ids.c.map_id)),
                    FinalPaymentExternalMapModel.additional_info.is_not(None),
                    func.length(func.trim(func.coalesce(FinalPaymentExternalMapModel.additional_info, ""))) > 0,
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) == self.STATUS_PENDING_PUSH,
                )
            ).scalar() or 0)

            return {
                "rows": rows,
                "summary": {
                    "totalBillPayments": total_payments,
                    "mappedBillPayments": total_payments,
                    "unmappedBillPayments": 0,
                    "syncedBillPayments": status_counts.get("SYNCED", 0),
                    "pendingPushBillPayments": status_counts.get("PENDING_PUSH", 0),
                    "assignmentNeededBillPayments": status_counts.get(self.STATUS_ASSIGNMENT_NEEDED, 0),
                    "failedBillPayments": status_counts.get("FAILED", 0),
                    "importFailedBillPayments": status_counts.get("IMPORT_FAILED", 0),
                    "assignedBillPayments": assigned_count,
                    "unassignedBillPayments": unassigned_count,
                    "readyToSyncBillPayments": ready_to_sync_count,
                },
                "pagination": {
                    "page": page,
                    "pageSize": page_size,
                    "totalRecords": total_records,
                    "totalPages": (total_records + page_size - 1) // page_size if total_records else 0,
                },
            }

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Text, and_, case, cast, func, or_, select, union

from source.Utils import constants as const
from source.dal.bill_payment_models import FinalPaymentExternalMapModel, PaymentExpenseModel
from source.dal.db_session import tenant_session_scope
from source.dal.project_models import ProjectModel
from source.dal.receipt_models import ReceiptExternalMapModel, ReceiptModel


logger = logging.getLogger(__name__)


class ReceiptRepository:
    """Receipt (Bill) repository aligned to the redesigned vendor sync pattern.

    Business truth:
      - receipt
    Integration truth:
      - receipt_external_map

    Receipt rows are updated in-place.
    Mapping rows are append-only logical history driven by sync_status.
    """

    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_SYNCED_INCOMPLETE = "SYNCED_INCOMPLETE"
    STATUS_FAILED = "FAILED"
    STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"
    STATUS_INVALID = "INVALID"

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _clean_lower(self, value: Any) -> str:
        return self._clean(value).lower()

    def _is_nullish(self, value: Any) -> bool:
        return value is None or self._clean(value).upper() == "NULL"

    def _optional_int(self, value: Any) -> Optional[int]:
        if self._is_nullish(value):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def is_stock_receipt(self, receipt: Dict[str, Any]) -> bool:
        """Return whether the receipt is explicitly an inventory transaction.

        A missing project alone is not enough to classify a bill as inventory:
        ordinary overhead bills can also be unassigned. Stock receipts must carry
        an explicit stock receipt type or stock reference.
        """
        receipt = receipt or {}
        receipt_type = self._normalize_receipt_type(receipt.get("name") or receipt.get("receipt_type"))
        if receipt_type in {
            "stockbill",
            "stockpurchasebill",
            "inventorybill",
            "stockdebitbill",
            "stockcreditbill",
        }:
            return True
        return bool(self._clean(receipt.get("stock_reference")))

    def _normalize_receipt_type(self, value: Any) -> str:
        return "".join(ch for ch in self._clean(value).lower() if ch.isalnum())

    def _service_id_int(self, service_id: Any) -> int:
        try:
            return int(service_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid service_id value: {service_id!r}") from exc

    def _normalize_doc_number(self, value: Any) -> str:
        return self._clean(value)

    def _safe_uuid_string(self, value: Any) -> Optional[str]:
        raw = self._clean(value)
        if not raw:
            return None
        try:
            return str(uuid.UUID(raw))
        except Exception:
            return raw

    def _safe_uuid_obj(self, value: Any):
        raw = self._clean(value)
        if not raw:
            return None
        try:
            return uuid.UUID(raw)
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
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _parse_date_only(self, value: Any) -> Optional[datetime]:
        raw = self._clean(value)
        if not raw:
            return None
        try:
            if len(raw) == 10 and raw[4] == '-' and raw[7] == '-':
                return datetime.fromisoformat(f"{raw}T00:00:00+00:00")
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def _to_yyyy_mm_dd(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            normalized = text.replace("Z", "+00:00")
            if "T" not in normalized and " " in normalized:
                normalized = normalized.replace(" ", "T", 1)
            return datetime.fromisoformat(normalized).date().isoformat()
        except Exception:
            if len(text) >= 10 and text[4] == "-" and text[7] == "-":
                return text[:10]
        return None

    def _to_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        cleaned = ''.join(ch for ch in text if ch.isdigit() or ch in '.-')
        if cleaned in {"", ".", "-", "-."}:
            return None
        try:
            return float(cleaned)
        except Exception:
            return None

    def parse_additional_info(self, raw: Any) -> Dict[str, Any]:
        if raw is None:
            return {}
        try:
            obj = raw if isinstance(raw, (dict, list)) else json.loads(str(raw).strip())
            if isinstance(obj, list):
                return obj[0] if obj else {}
            if isinstance(obj, dict):
                return obj
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return {}

    def build_local_additional_info(
        self,
        *,
        total: Any,
        bill_date: Any,
        bill_no: Any,
        company_name: Any = None,
        category: Any = None,
        due_date: Any = None,
        paid: Optional[bool] = None,
        receipt_info: Optional[str] = None,
        tax_infos: Optional[str] = None,
        payment_method: Any = "",
        payment_type: Any = "",
        reason: str = "Approve",
    ) -> str:
        payload = {
            "reason": reason,
            "expenseType": "",
            "total": str(total or ""),
            "companyName": self._clean(company_name) or None,
            "receiptInfo": receipt_info or "[]",
            "taxInfos": tax_infos or "[]",
            "paid": bool(paid) if paid is not None else False,
            "paymentMethod": self._clean(payment_method),
            "billDate": self._clean(bill_date) or None,
            "category": self._clean(category) or " ",
            "billNo": self._clean(bill_no) or None,
            "paymentType": self._clean(payment_type),
        }
        cleaned_due_date = self._clean(due_date)
        if cleaned_due_date:
            payload["dueDate"] = cleaned_due_date
        return json.dumps([payload], default=str)

    def _normalize_receipt_row(self, row: ReceiptModel | Dict[str, Any] | None) -> Dict[str, Any]:
        if row is None:
            return {}
        if isinstance(row, dict):
            data = dict(row)
            for key in ("vendor_id", "document_id", "project_id", "purchase_order_id", "chart_of_account_id"):
                data[key] = self._optional_int(data.get(key))
        else:
            data = {
                "id": int(row.id),
                "ai_result": row.ai_result,
                "comment": row.comment,
                "created_at": row.created_at,
                "created_by": row.created_by,
                "customer_name": row.customer_name,
                "description": row.description,
                "file_name": row.file_name,
                "invoice_date": row.invoice_date,
                "invoice_number": row.invoice_number,
                "name": row.name,
                "order_date": row.order_date,
                "order_number": row.order_number,
                "status": row.status,
                "vendor_id": self._optional_int(row.vendor_id),
                "document_id": self._optional_int(row.document_id),
                "project_id": self._optional_int(row.project_id),
                "purchase_order_id": self._optional_int(row.purchase_order_id),
                "receipt_additional_info": row.receipt_additional_info,
                "invalid": row.invalid,
                "updated_at": row.updated_at,
                "version": int(row.version) if row.version is not None else None,
                "chart_of_account_id": self._optional_int(row.chart_of_account_id),
                "receipt_uuid": str(row.receipt_uuid) if row.receipt_uuid is not None else None,
                "stock_reference": row.stock_reference,
                "paid_date": row.paid_date,
                "due_date": row.due_date,
            }
        info = self.parse_additional_info(data.get("receipt_additional_info"))
        data["doc_number"] = self._pick_doc_number(data, info)
        data["txn_date"] = self._pick_txn_date(data, info)
        data["due_date"] = self._pick_due_date(data, info)
        data["paid_date"] = data.get("paid_date")
        data["stock_reference"] = self._clean(data.get("stock_reference")) or None
        data["receipt_type"] = self._clean(data.get("name")) or None
        normalized_type = self._normalize_receipt_type(data.get("name"))
        data["is_stock_debit_bill"] = normalized_type == "stockdebitbill"
        data["is_stock_credit_bill"] = normalized_type == "stockcreditbill"
        data["amount"] = (
            self._pick_stock_movement_buying_amount(info)
            if data["is_stock_debit_bill"] or data["is_stock_credit_bill"]
            else self._pick_total_amount(info)
        )
        data["is_stock_receipt"] = self.is_stock_receipt(data)
        return data

    def _mapping_to_dict(self, row: ReceiptExternalMapModel | Dict[str, Any] | None) -> Dict[str, Any]:
        if row is None:
            return {}
        if isinstance(row, dict):
            data = dict(row)
            return {
                "id": int(data["id"]),
                "receipt_id": int(data["receipt_id"]) if data.get("receipt_id") is not None else None,
                "receipt_uuid": str(data.get("receipt_uuid")) if data.get("receipt_uuid") is not None else None,
                "tenant_id": data.get("tenant_id"),
                "service_id": int(data["service_id"]) if data.get("service_id") is not None else None,
                "external_receipt_id": data.get("external_receipt_id"),
                "external_account_id": data.get("external_account_id"),
                "sync_status": data.get("sync_status"),
                "version": int(data.get("version") or 1),
                "version_effective_date": data.get("version_effective_date"),
                "version_expiry_date": data.get("version_expiry_date"),
                "endorsement_effective_date": data.get("endorsement_effective_date"),
                "endorsement_expiry_date": data.get("endorsement_expiry_date"),
                "reason": data.get("reason"),
                "updated_at": data.get("updated_at"),
                "sync_token": data.get("sync_token"),
            }
        return {
            "id": int(row.id),
            "receipt_id": int(row.receipt_id) if row.receipt_id is not None else None,
            "receipt_uuid": str(row.receipt_uuid) if row.receipt_uuid is not None else None,
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "external_receipt_id": row.external_receipt_id,
            "external_account_id": row.external_account_id,
            "sync_status": row.sync_status,
            "version": int(row.version or 1),
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "reason": row.reason,
            "updated_at": row.updated_at,
            "sync_token": row.sync_token,
        }

    def _sync_projection(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        info = self.parse_additional_info(receipt.get("receipt_additional_info"))
        return {
            "vendor_id": self._optional_int(receipt.get("vendor_id")),
            "chart_of_account_id": self._optional_int(receipt.get("chart_of_account_id")),
            "project_id": self._optional_int(receipt.get("project_id")),
            "stock_reference": self._clean(receipt.get("stock_reference")) or None,
            "invoice_number": self._clean(receipt.get("invoice_number") or info.get("billNo") or info.get("billNumber")),
            "invoice_date": self._to_yyyy_mm_dd(receipt.get("invoice_date")),
            "order_date": self._to_yyyy_mm_dd(receipt.get("order_date")),
            "bill_date": self._pick_txn_date(receipt, info),
            "due_date": self._pick_due_date(receipt, info),
            "paid_date": self._to_yyyy_mm_dd(receipt.get("paid_date")),
            "amount": self._pick_total_amount(info),
            "status": self._clean(receipt.get("status")),
            "name": self._clean(receipt.get("name")),
            "paid": bool(info.get("paid") or receipt.get("paid_date")),
        }

    def sync_projection_changed(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return self._sync_projection(left) != self._sync_projection(right)

    def get_receipt_latest_local_timestamp(self, receipt: Optional[Dict[str, Any]], mapping: Optional[Dict[str, Any]] = None) -> Optional[datetime]:
        candidates: List[datetime] = []
        for row in (receipt or {}, mapping or {}):
            for key in ("updated_at", "endorsement_effective_date", "version_effective_date"):
                value = row.get(key)
                dt = self._parse_timestamp(value)
                if dt is not None:
                    candidates.append(dt)
        return max(candidates) if candidates else None

    def get_bill_receipt_names(self) -> List[str]:
        raw = os.getenv(const.ENV_QBO_BILL_RECEIPT_NAMES, const.QBO_BILL_DEFAULT_RECEIPT_NAMES)
        names = [item.strip() for item in str(raw or "").split(",") if item and item.strip()]
        if names:
            return names
        return [item.strip() for item in const.QBO_BILL_DEFAULT_RECEIPT_NAMES.split(",") if item.strip()]

    # ------------------------------------------------------------------
    # Receipt business rows
    # ------------------------------------------------------------------

    def get_current_receipt_by_id(self, tenant_id: str, receipt_id: int) -> Optional[Dict[str, Any]]:
        if receipt_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = select(ReceiptModel).where(
                ReceiptModel.id == int(receipt_id),
                func.coalesce(ReceiptModel.invalid, False) == False,
            )
            row = session.execute(stmt).scalars().first()
            return self._normalize_receipt_row(row) if row else None

    def get_receipt_id_by_doc_number(self, tenant_id: str, *, doc_number: str, vendor_id: Optional[int] = None) -> Optional[int]:
        doc = self._normalize_doc_number(doc_number)
        if not doc:
            return None
        names = self.get_bill_receipt_names()
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ReceiptModel)
                .where(
                    func.coalesce(ReceiptModel.invalid, False) == False,
                    ReceiptModel.invoice_number == doc,
                    ReceiptModel.name.in_(names),
                )
                .order_by(ReceiptModel.updated_at.desc().nullslast(), ReceiptModel.id.desc())
            )
            if vendor_id is not None:
                stmt = stmt.where(ReceiptModel.vendor_id == str(vendor_id))
            row = session.execute(stmt).scalars().first()
            return int(row.id) if row else None

    def create_receipt_for_tenant(self, tenant_id: str, receipt_data: Dict[str, Any], created_by: str = "QuickBooks") -> int:
        now = self._now()
        receipt_uuid = self._safe_uuid_obj(receipt_data.get("receipt_uuid") or uuid.uuid4())
        with tenant_session_scope(tenant_id) as session:
            row = ReceiptModel(
                created_at=receipt_data.get("created_at") or now,
                created_by=receipt_data.get("created_by") or created_by,
                description=self._clean(receipt_data.get("description")) or None,
                file_name=self._clean(receipt_data.get("file_name")) or None,
                invoice_date=receipt_data.get("invoice_date"),
                invoice_number=self._clean(receipt_data.get("invoice_number")) or None,
                name=self._clean(receipt_data.get("name")) or "VendorBill",
                order_date=receipt_data.get("order_date"),
                order_number=self._clean(receipt_data.get("order_number")) or None,
                status=self._clean(receipt_data.get("status")) or "Approve",
                vendor_id=str(receipt_data["vendor_id"]) if receipt_data.get("vendor_id") is not None else None,
                receipt_additional_info=receipt_data.get("receipt_additional_info"),
                invalid=bool(receipt_data.get("invalid", False)),
                updated_at=receipt_data.get("updated_at") or now,
                version=int(receipt_data.get("version") or 1),
                chart_of_account_id=self._optional_int(receipt_data.get("chart_of_account_id")),
                receipt_uuid=receipt_uuid,
                customer_name=self._clean(receipt_data.get("customer_name")) or None,
                document_id=self._optional_int(receipt_data.get("document_id")),
                project_id=self._optional_int(receipt_data.get("project_id")),
                purchase_order_id=self._optional_int(receipt_data.get("purchase_order_id")),
                stock_reference=self._clean(receipt_data.get("stock_reference")) or None,
                paid_date=self._parse_timestamp(receipt_data.get("paid_date")),
                due_date=self._parse_timestamp(receipt_data.get("due_date")),
                comment=self._clean(receipt_data.get("comment")) or None,
                ai_result=receipt_data.get("ai_result"),
            )
            session.add(row)
            session.flush()
            row_id = int(row.id)
            session.commit()
            return row_id

    def mark_receipt_paid(self, tenant_id: str, receipt_id: int, paid_date: Any = None) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ReceiptModel, int(receipt_id))
            if not row:
                return
            row.status = "paid"
            row.paid_date = self._parse_timestamp(paid_date) or row.paid_date or self._now()
            try:
                raw = row.receipt_additional_info
                parsed = json.loads(raw) if raw else []
                if isinstance(parsed, dict):
                    parsed = [parsed]
                elif not isinstance(parsed, list):
                    parsed = []
            except Exception:
                parsed = []
            if parsed:
                parsed[0]["paid"] = True
            else:
                parsed = [{"paid": True}]
            row.receipt_additional_info = json.dumps(parsed, default=str)
            row.updated_at = self._now()
            session.add(row)
            session.commit()

    def update_receipt_from_qbo(self, tenant_id: str, *, receipt_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ReceiptModel, int(receipt_id))
            if not row:
                raise ValueError(f"Receipt {receipt_id} not found")
            row.description = self._clean(payload.get("description")) or row.description
            row.file_name = self._clean(payload.get("file_name")) or row.file_name
            row.invoice_date = payload.get("invoice_date") or row.invoice_date
            row.invoice_number = self._clean(payload.get("invoice_number")) or row.invoice_number
            row.name = self._clean(payload.get("name")) or row.name or "VendorBill"
            row.order_date = payload.get("order_date") or row.order_date
            row.order_number = self._clean(payload.get("order_number")) or row.order_number
            row.status = self._clean(payload.get("status")) or row.status or "Approve"
            row.vendor_id = str(payload["vendor_id"]) if payload.get("vendor_id") is not None else row.vendor_id
            row.receipt_additional_info = payload.get("receipt_additional_info") or row.receipt_additional_info
            row.updated_at = payload.get("updated_at") or self._now()
            row.chart_of_account_id = self._optional_int(payload.get("chart_of_account_id")) if payload.get("chart_of_account_id") is not None else row.chart_of_account_id
            row.customer_name = self._clean(payload.get("customer_name")) or row.customer_name
            if "project_id" in payload:
                row.project_id = self._optional_int(payload.get("project_id"))
            row.stock_reference = self._clean(payload.get("stock_reference")) or row.stock_reference
            row.paid_date = self._parse_timestamp(payload.get("paid_date")) or row.paid_date
            row.due_date = self._parse_timestamp(payload.get("due_date")) or row.due_date
            session.add(row)
            session.flush()
            result = self._normalize_receipt_row(row)
            session.commit()
            return result

    def set_receipt_document_id(self, tenant_id: str, receipt_id: int, document_id: int) -> None:
        """Set document_id on a receipt only if it is not already set."""
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ReceiptModel, receipt_id)
            if not row:
                raise ValueError(f"Receipt {receipt_id} not found")
            if row.document_id is not None:
                logger.debug(
                    "receipt document link already exists | receipt_id=%s document_id=%s",
                    receipt_id,
                    row.document_id,
                )
                return
            row.document_id = str(document_id)
            row.updated_at = self._now()
            session.add(row)
            session.commit()
        logger.info(
            "receipt document linked | receipt_id=%s document_id=%s",
            receipt_id,
            document_id,
        )

    def build_receipt_payload_from_qbo(self, qbo_bill: Dict[str, Any], *, vendor_id: Optional[int], chart_of_account_id: Optional[int], project_id: Optional[int] = None, qbo_last_updated_at=None) -> Dict[str, Any]:
        doc_number = self._clean(qbo_bill.get("DocNumber")) or None
        txn_date = self._to_yyyy_mm_dd(qbo_bill.get("TxnDate"))
        due_date = self._to_yyyy_mm_dd(qbo_bill.get("DueDate"))
        total = qbo_bill.get("TotalAmt")
        vendor_ref = qbo_bill.get("VendorRef") or {}
        private_note = self._clean(qbo_bill.get("PrivateNote")) or None
        vendor_name = ""
        if isinstance(vendor_ref, dict):
            vendor_name = self._clean(vendor_ref.get("name"))
        line_items = qbo_bill.get("Line") or []
        is_paid = self._to_float(qbo_bill.get("Balance")) == 0 if qbo_bill.get("Balance") is not None else False
        additional_info = self.build_local_additional_info(
            total=total,
            bill_date=f"{txn_date}T00:00Z" if txn_date else None,
            bill_no=doc_number,
            company_name=vendor_name,
            category=private_note or " ",
            due_date=f"{due_date}T00:00Z" if due_date else None,
            paid=is_paid,
            receipt_info=json.dumps(line_items, default=str)[:65000],
            tax_infos="[]",
            payment_method="",
            payment_type="",
            reason="Approve",
        )
        return {
            "description": private_note,
            "file_name": f"QBO Bill {doc_number}.json" if doc_number else None,
            "invoice_date": None,
            "invoice_number": doc_number,
            "name": "VendorBill",
            "order_date": None,
            "order_number": doc_number,
            "status": "paid" if is_paid else "Approve",
            "vendor_id": vendor_id,
            "receipt_additional_info": additional_info,
            "invalid": False,
            "updated_at": qbo_last_updated_at or self._now(),
            "version": 1,
            "chart_of_account_id": chart_of_account_id,
            "project_id": project_id,
            "stock_reference": None,
            "paid_date": qbo_last_updated_at if is_paid else None,
            "due_date": f"{due_date}T00:00Z" if due_date else None,
            "customer_name": None,
        }

    # ------------------------------------------------------------------
    # Mapping rows
    # ------------------------------------------------------------------

    def _get_receipt_uuid(self, session, receipt_id: Optional[int]):
        if receipt_id is None:
            return None
        receipt = session.get(ReceiptModel, int(receipt_id))
        return receipt.receipt_uuid if receipt and receipt.receipt_uuid is not None else None

    def _get_next_mapping_version(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        receipt_id: Optional[int],
        external_receipt_id: Optional[str],
    ) -> int:
        stmt = (
            select(func.coalesce(func.max(ReceiptExternalMapModel.version), 0) + 1)
            .where(
                ReceiptExternalMapModel.tenant_id == tenant_id,
                ReceiptExternalMapModel.service_id == self._service_id_int(service_id),
            )
        )
        if receipt_id is not None:
            stmt = stmt.where(ReceiptExternalMapModel.receipt_id == int(receipt_id))
        elif self._clean(external_receipt_id):
            stmt = stmt.where(ReceiptExternalMapModel.external_receipt_id == self._clean(external_receipt_id))
        return int(session.execute(stmt).scalar() or 1)

    def _create_mapping(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        receipt_id: Optional[int],
        receipt_uuid: Any,
        external_receipt_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str],
        reason: Optional[str],
        version: Optional[int] = None,
        effective_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ) -> ReceiptExternalMapModel:
        effective_ts = effective_at or updated_at or self._now()
        updated_ts = updated_at or effective_ts
        next_version = int(version or self._get_next_mapping_version(
            session,
            tenant_id=tenant_id,
            service_id=service_id,
            receipt_id=receipt_id,
            external_receipt_id=external_receipt_id,
        ))
        row = ReceiptExternalMapModel(
            receipt_id=int(receipt_id) if receipt_id is not None else None,
            receipt_uuid=self._safe_uuid_obj(receipt_uuid) if receipt_uuid else None,
            tenant_id=tenant_id,
            service_id=self._service_id_int(service_id),
            external_receipt_id=self._clean(external_receipt_id) or None,
            external_account_id=self._clean(external_account_id) or None,
            sync_status=sync_status,
            version=next_version,
            version_effective_date=None,
            version_expiry_date=None,
            endorsement_effective_date=effective_ts,
            endorsement_expiry_date=None,
            reason=self._clean(reason) or None,
            updated_at=updated_ts,
            sync_token=self._clean(sync_token) or None,
        )
        session.add(row)
        session.flush()
        return row

    def get_current_mapping_by_receipt_id(self, tenant_id: str, service_id: Any, receipt_id: int) -> Optional[Dict[str, Any]]:
        if receipt_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ReceiptExternalMapModel)
                .where(
                    ReceiptExternalMapModel.receipt_id == int(receipt_id),
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == self._service_id_int(service_id),
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(ReceiptExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_current_mapping_by_external_receipt_id(
        self,
        tenant_id: str,
        service_id: Any,
        external_receipt_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the current mapping for a QuickBooks Bill ID.

        StockDebitBill mappings point to QuickBooks JournalEntry records. Because
        receipt_external_map has no entity-type column, linked StockDebitBill rows
        are excluded by the receipt business type. Orphan rows are Bill-import
        failures and remain eligible.
        """
        external_value = self._clean(external_receipt_id)
        if not external_value:
            return None
        with tenant_session_scope(tenant_id) as session:
            normalized_receipt_name = func.lower(
                func.replace(
                    func.replace(
                        func.replace(func.coalesce(ReceiptModel.name, ""), " ", ""),
                        "-",
                        "",
                    ),
                    "_",
                    "",
                )
            )
            stmt = (
                select(ReceiptExternalMapModel)
                .outerjoin(ReceiptModel, ReceiptModel.id == ReceiptExternalMapModel.receipt_id)
                .where(
                    ReceiptExternalMapModel.external_receipt_id == external_value,
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == self._service_id_int(service_id),
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    or_(
                        ReceiptExternalMapModel.receipt_id.is_(None),
                        normalized_receipt_name != "stockdebitbill",
                    ),
                )
                .order_by(ReceiptExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_export_candidates(self, tenant_id: str, service_id: Any, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ReceiptExternalMapModel)
                .where(
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == self._service_id_int(service_id),
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")).in_([
                        self.STATUS_PENDING_PUSH,
                        self.STATUS_FAILED,
                        self.STATUS_SYNC_FAILED,
                    ]),
                )
                .order_by(ReceiptExternalMapModel.id.asc())
                .offset(int(offset))
                .limit(int(limit))
            )
            rows = session.execute(stmt).scalars().all()
            for mapping_row in rows:
                receipt = session.get(ReceiptModel, int(mapping_row.receipt_id)) if mapping_row.receipt_id is not None else None
                if not receipt:
                    continue
                results.append({
                    "receipt": self._normalize_receipt_row(receipt),
                    "mapping": self._mapping_to_dict(mapping_row),
                })
        return results

    def get_export_candidate_by_mapping_id(self, tenant_id: str, service_id: Any, mapping_id: int) -> Optional[Dict[str, Any]]:
        if mapping_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            mapping_row = session.get(ReceiptExternalMapModel, int(mapping_id))
            if not mapping_row:
                return None
            if mapping_row.tenant_id != tenant_id:
                return None
            if int(mapping_row.service_id or 0) != self._service_id_int(service_id):
                return None
            if self._clean(mapping_row.sync_status).upper() == self.STATUS_INVALID:
                return None
            receipt = session.get(ReceiptModel, int(mapping_row.receipt_id)) if mapping_row.receipt_id is not None else None
            if not receipt:
                return None
            return {
                "receipt": self._normalize_receipt_row(receipt),
                "mapping": self._mapping_to_dict(mapping_row),
            }

    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_receipt_id: Optional[str],
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ReceiptExternalMapModel, int(mapping_id))
            if not row:
                return
            if external_receipt_id is not None:
                row.external_receipt_id = self._clean(external_receipt_id) or row.external_receipt_id
            if external_account_id is not None:
                row.external_account_id = self._clean(external_account_id) or None
            if sync_token is not None:
                row.sync_token = self._clean(sync_token) or None
            row.sync_status = sync_status or self.STATUS_SYNCED
            row.reason = self._clean(reason) or None
            row.updated_at = self._now()
            session.add(row)
            session.commit()

    def mark_mapping_failed(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        status: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ReceiptExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = status or self.STATUS_FAILED
            row.reason = self._clean(reason) or None
            row.updated_at = self._now()
            session.add(row)
            session.commit()

    def close_mapping_as_invalid(self, tenant_id: str, mapping_id: int) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ReceiptExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = self.STATUS_INVALID
            row.updated_at = self._now()
            session.add(row)
            session.commit()

    def rotate_mapping_for_receipt(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        receipt_id: Optional[int],
        external_receipt_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            receipt_uuid = None
            effective_at = now
            if receipt_id is not None:
                receipt = session.get(ReceiptModel, int(receipt_id))
                if receipt:
                    receipt_uuid = receipt.receipt_uuid
                    effective_at = receipt.updated_at or now
            existing_stmt = (
                select(ReceiptExternalMapModel)
                .where(
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == self._service_id_int(service_id),
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(ReceiptExternalMapModel.id.desc())
            )
            if receipt_id is not None:
                existing_stmt = existing_stmt.where(ReceiptExternalMapModel.receipt_id == int(receipt_id))
            elif self._clean(external_receipt_id):
                existing_stmt = existing_stmt.where(ReceiptExternalMapModel.external_receipt_id == self._clean(external_receipt_id))
            existing_rows = session.execute(existing_stmt).scalars().all()
            for existing in existing_rows:
                existing.sync_status = self.STATUS_INVALID
                existing.updated_at = now
                session.add(existing)
            new_row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                receipt_id=receipt_id,
                receipt_uuid=receipt_uuid,
                external_receipt_id=external_receipt_id,
                external_account_id=external_account_id,
                sync_status=sync_status,
                sync_token=sync_token,
                reason=reason,
                effective_at=effective_at,
                updated_at=now,
            )
            result = self._mapping_to_dict(new_row)
            session.commit()
            return result

    def create_pending_push_mapping(
        self,
        tenant_id: str,
        service_id: Any,
        receipt_id: int,
        external_receipt_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        receipt = self.get_current_receipt_by_id(tenant_id, int(receipt_id))
        if not receipt:
            raise ValueError(f"Receipt not found for receipt_id={receipt_id}")

        existing = self.get_current_mapping_by_receipt_id(
            tenant_id=tenant_id,
            service_id=service_id,
            receipt_id=receipt_id,
        )
        if existing:
            if not external_receipt_id:
                external_receipt_id = existing.get("external_receipt_id")
            if external_account_id is None:
                external_account_id = existing.get("external_account_id")
            if sync_token is None:
                sync_token = existing.get("sync_token")

        return self.rotate_mapping_for_receipt(
            tenant_id=tenant_id,
            service_id=service_id,
            receipt_id=receipt_id,
            external_receipt_id=external_receipt_id,
            external_account_id=external_account_id,
            sync_status=self.STATUS_PENDING_PUSH,
            sync_token=sync_token,
            reason=reason,
        )

    def upsert_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_receipt_id: str,
        external_account_id: Optional[str] = None,
        receipt_id: Optional[int] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        external_value = self._clean(external_receipt_id)
        if not external_value:
            raise ValueError("external_receipt_id is required for failed mapping")
        failed_status = self._clean(sync_status).upper() or self.STATUS_IMPORT_FAILED
        if failed_status not in {self.STATUS_IMPORT_FAILED, self.STATUS_SYNC_FAILED, self.STATUS_FAILED}:
            failed_status = self.STATUS_IMPORT_FAILED
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            current = session.execute(
                select(ReceiptExternalMapModel)
                .where(
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == self._service_id_int(service_id),
                    ReceiptExternalMapModel.external_receipt_id == external_value,
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(ReceiptExternalMapModel.id.desc())
            ).scalars().first()
            if current:
                if receipt_id is not None:
                    current.receipt_id = int(receipt_id)
                    current.receipt_uuid = self._get_receipt_uuid(session, receipt_id) or current.receipt_uuid
                if external_account_id is not None:
                    current.external_account_id = self._clean(external_account_id) or None
                if sync_token is not None:
                    current.sync_token = self._clean(sync_token) or None
                current.sync_status = failed_status
                current.reason = self._clean(reason) or None
                current.updated_at = now
                session.add(current)
                session.flush()
                result = self._mapping_to_dict(current)
                session.commit()
                return result

            receipt_uuid = self._get_receipt_uuid(session, receipt_id) if receipt_id is not None else None
            row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                receipt_id=receipt_id,
                receipt_uuid=receipt_uuid,
                external_receipt_id=external_value,
                external_account_id=external_account_id,
                sync_status=failed_status,
                sync_token=sync_token,
                reason=reason,
                effective_at=now,
                updated_at=now,
            )
            result = self._mapping_to_dict(row)
            session.commit()
            return result

    def get_open_import_failed_external_receipt_ids(self, tenant_id: str, service_id: Any) -> List[str]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ReceiptExternalMapModel.external_receipt_id)
                .where(
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == self._service_id_int(service_id),
                    ReceiptExternalMapModel.sync_status == self.STATUS_IMPORT_FAILED,
                    ReceiptExternalMapModel.external_receipt_id.is_not(None),
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(ReceiptExternalMapModel.updated_at.asc(), ReceiptExternalMapModel.id.asc())
            )
            values = []
            for row in session.execute(stmt).all():
                if row and row[0]:
                    values.append(str(row[0]))
            return values

    # ------------------------------------------------------------------
    # UI helper / debugging
    # ------------------------------------------------------------------

    def get_bill_cdc_page(
        self,
        tenant_id: str,
        service_id: Any,
        *,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
        mapped_only: bool = False,
        project_search: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_direction: Optional[str] = None,
    ) -> Dict[str, Any]:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_term = self._clean(search)
        project_search_term = self._clean(project_search)
        normalized_status = self._clean(sync_status).upper()
        normalized_sort_by = self._clean(sort_by).upper() or "UPDATED_AT"
        normalized_sort_direction = self._clean(sort_direction).upper()
        sort_desc = normalized_sort_direction != "ASC"

        with tenant_session_scope(tenant_id) as session:
            max_eed_mapped = (
                select(
                    ReceiptExternalMapModel.receipt_id.label("receipt_id"),
                    func.max(ReceiptExternalMapModel.endorsement_effective_date).label("max_eed"),
                )
                .where(
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == service_id_int,
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    ReceiptExternalMapModel.receipt_id.is_not(None),
                )
                .group_by(ReceiptExternalMapModel.receipt_id)
                .subquery()
            )
            latest_mapped_ids = (
                select(func.max(ReceiptExternalMapModel.id).label("map_id"))
                .join(
                    max_eed_mapped,
                    (ReceiptExternalMapModel.receipt_id == max_eed_mapped.c.receipt_id)
                    & (ReceiptExternalMapModel.endorsement_effective_date == max_eed_mapped.c.max_eed),
                )
                .where(
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == service_id_int,
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    ReceiptExternalMapModel.receipt_id.is_not(None),
                )
                .group_by(ReceiptExternalMapModel.receipt_id)
            )
            latest_orphan_ids = (
                select(func.max(ReceiptExternalMapModel.id).label("map_id"))
                .where(
                    ReceiptExternalMapModel.tenant_id == tenant_id,
                    ReceiptExternalMapModel.service_id == service_id_int,
                    ReceiptExternalMapModel.receipt_id.is_(None),
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(ReceiptExternalMapModel.external_receipt_id)
            )
            combined_ids = union(latest_mapped_ids, latest_orphan_ids).subquery()

            # Bill payments are separate QuickBooks entities. Surface their latest
            # sync state on the bill row so the UI can tell a user when a paid bill
            # still needs its payment synchronized. This is read-only metadata and
            # does not alter either bill or bill-payment sync behavior.
            payment_max_eed = (
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
            latest_payment_mapping_ids = (
                select(
                    FinalPaymentExternalMapModel.payment_expense_id.label("payment_expense_id"),
                    func.max(FinalPaymentExternalMapModel.id).label("map_id"),
                )
                .join(
                    payment_max_eed,
                    and_(
                        FinalPaymentExternalMapModel.payment_expense_id == payment_max_eed.c.payment_expense_id,
                        FinalPaymentExternalMapModel.endorsement_effective_date == payment_max_eed.c.max_eed,
                    ),
                )
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == service_id_int,
                    FinalPaymentExternalMapModel.payment_receives_id.is_(None),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(FinalPaymentExternalMapModel.payment_expense_id)
                .subquery()
            )
            payment_sync_state = (
                select(
                    PaymentExpenseModel.reference_id.label("receipt_id"),
                    func.count(PaymentExpenseModel.id).label("payment_count"),
                    func.sum(
                        case(
                            (func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) == self.STATUS_SYNCED, 1),
                            else_=0,
                        )
                    ).label("synced_payment_count"),
                )
                .select_from(PaymentExpenseModel)
                .outerjoin(
                    latest_payment_mapping_ids,
                    latest_payment_mapping_ids.c.payment_expense_id == PaymentExpenseModel.id,
                )
                .outerjoin(
                    FinalPaymentExternalMapModel,
                    FinalPaymentExternalMapModel.id == latest_payment_mapping_ids.c.map_id,
                )
                .where(
                    PaymentExpenseModel.reference_id.is_not(None),
                    func.lower(func.coalesce(PaymentExpenseModel.reference_type, "")) == "bill",
                )
                .group_by(PaymentExpenseModel.reference_id)
                .subquery()
            )

            base_stmt = (
                select(
                    ReceiptExternalMapModel,
                    ReceiptModel,
                    ProjectModel,
                    payment_sync_state.c.payment_count,
                    payment_sync_state.c.synced_payment_count,
                )
                .where(ReceiptExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .outerjoin(ReceiptModel, ReceiptModel.id == ReceiptExternalMapModel.receipt_id)
                .outerjoin(ProjectModel, ProjectModel.id == ReceiptModel.project_id)
                .outerjoin(payment_sync_state, payment_sync_state.c.receipt_id == ReceiptModel.id)
            )
            if normalized_status and normalized_status != "ALL":
                base_stmt = base_stmt.where(
                    func.upper(func.coalesce(ReceiptExternalMapModel.sync_status, "")) == normalized_status
                )
            project_search_fields = (
                func.lower(func.coalesce(ProjectModel.description, "")),
                func.lower(func.coalesce(ProjectModel.order_no, "")),
                func.lower(func.coalesce(ProjectModel.location, "")),
                func.lower(func.coalesce(ProjectModel.trade, "")),
                func.lower(func.coalesce(cast(ProjectModel.id, Text), "")),
                func.lower(func.coalesce(cast(ProjectModel.project_uid, Text), "")),
                func.lower(func.coalesce(cast(ProjectModel.customer_uid, Text), "")),
            )
            if search_term:
                like_term = f"%{search_term.lower()}%"
                base_stmt = base_stmt.where(
                    or_(
                        func.lower(func.coalesce(ReceiptModel.invoice_number, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.order_number, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.description, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptModel.name, "")).like(like_term),
                        func.lower(func.coalesce(ReceiptExternalMapModel.external_receipt_id, "")).like(like_term),
                        func.lower(func.coalesce(cast(ReceiptModel.receipt_uuid, Text), "")).like(like_term),
                        *[field.like(like_term) for field in project_search_fields],
                    )
                )
            if project_search_term:
                project_like_term = f"%{project_search_term.lower()}%"
                base_stmt = base_stmt.where(
                    or_(*[field.like(project_like_term) for field in project_search_fields])
                )

            total_records = int(session.execute(select(func.count()).select_from(base_stmt.subquery())).scalar() or 0)

            sort_expression_by_name = {
                "PROJECT": func.lower(func.coalesce(ProjectModel.description, "")),
                "BILL": func.lower(func.coalesce(ReceiptModel.invoice_number, ReceiptModel.name, ReceiptModel.description, "")),
                "INVOICE_DATE": ReceiptModel.invoice_date,
                "UPDATED_AT": ReceiptExternalMapModel.updated_at,
            }
            sort_expression = sort_expression_by_name.get(normalized_sort_by, ReceiptExternalMapModel.updated_at)
            primary_sort = sort_expression.desc() if sort_desc else sort_expression.asc()
            paged_stmt = (
                base_stmt
                .order_by(primary_sort, ReceiptExternalMapModel.updated_at.desc(), ReceiptExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            )
            records = session.execute(paged_stmt).all()

            rows: List[Dict[str, Any]] = []
            for mapping_row, receipt_row, project_row, payment_count, synced_payment_count in records:
                mapping = self._mapping_to_dict(mapping_row)
                receipt = self._normalize_receipt_row(receipt_row) if receipt_row else {}
                row_info = self.parse_additional_info(receipt.get("receipt_additional_info"))
                qbo_id = self._clean(mapping.get("external_receipt_id"))
                vendor_name = self._clean(row_info.get("companyName")) or None
                bill_category = self._clean(row_info.get("category")) or None

                # Source detection:
                # created_by = "QuickBooks"  → bill was imported from QuickBooks into FIELDCRAFT
                # anything else / no receipt → bill originated in FIELDCRAFT (or no receipt yet)
                is_qbo_origin = (
                    receipt_row is None and bool(qbo_id)
                ) or self._clean(receipt.get("created_by")) == const.SERVICE_NAME_QUICKBOOKS

                # Display name priority:
                # 1. invoice_number from receipts table
                # 2. billNo from receipt_additional_info
                # 3. receipt id (primary key) as final fallback
                # For orphan rows with no linked receipt, show the QBO ID.
                if receipt_row is not None:
                    invoice_number = self._clean(receipt.get("invoice_number"))
                    bill_no = self._clean(row_info.get("billNo"))
                    display_name = (
                        invoice_number
                        or bill_no
                        or f"#{receipt.get('id')}"
                    )
                else:
                    display_name = (f"QBO #{qbo_id}" if qbo_id else f"Mapping #{mapping['id']}")

                # Both stock movements export as journal entries, never as bills:
                # StockDebitBill consumes inventory, StockCreditBill returns it.
                qbo_entity_type = (
                    const.QBO_RECEIPT_ENTITY_TYPE_JOURNAL_ENTRY
                    if bool(receipt.get("is_stock_debit_bill")) or bool(receipt.get("is_stock_credit_bill"))
                    else const.QBO_RECEIPT_ENTITY_TYPE_BILL
                )
                is_paid = bool(row_info.get("paid")) or str(receipt.get("status") or "").lower() == "paid"
                payment_count_value = int(payment_count or 0)
                synced_payment_count_value = int(synced_payment_count or 0)
                bill_payment_sync_required = (
                    is_paid
                    and bool(qbo_id)
                    and qbo_entity_type == const.QBO_RECEIPT_ENTITY_TYPE_BILL
                    and payment_count_value > 0
                    and synced_payment_count_value < payment_count_value
                )

                rows.append({
                    "receiptId": receipt.get("id"),
                    "receiptUuid": receipt.get("receipt_uuid"),
                    "displayName": display_name,
                    "source": "QuickBooks" if is_qbo_origin else "FIELDCRAFT",
                    "billName": receipt.get("name"),
                    "receiptType": receipt.get("receipt_type") or receipt.get("name"),
                    "isStockDebitBill": bool(receipt.get("is_stock_debit_bill")),
                    "isStockCreditBill": bool(receipt.get("is_stock_credit_bill")),
                    "invoiceNumber": receipt.get("invoice_number"),
                    "vendorCompany": self._clean(row_info.get("companyName")) or None,
                    "category": self._clean(row_info.get("category")) or None,
                    "invoiceDate": (
                        receipt.get("invoice_date")
                        or self._to_yyyy_mm_dd(row_info.get("billDate"))
                    ),
                    "projectId": receipt.get("project_id"),
                    "projectName": self._clean(project_row.description) or None if project_row else None,
                    "isStockBill": bool(receipt.get("is_stock_receipt")),
                    "stockReference": receipt.get("stock_reference"),
                    "vendorId": receipt.get("vendor_id"),
                    "chartOfAccountId": receipt.get("chart_of_account_id"),
                    "status": receipt.get("status"),
                    "isPaid": is_paid,
                    "billPaymentCount": payment_count_value,
                    "syncedBillPaymentCount": synced_payment_count_value,
                    "billPaymentSyncRequired": bill_payment_sync_required,
                    "amount": receipt.get("amount"),
                    "mappingId": mapping.get("id"),
                    "externalReceiptId": mapping.get("external_receipt_id"),
                    "qboEntityType": qbo_entity_type,
                    "qboBillId": (
                        mapping.get("external_receipt_id")
                        if qbo_entity_type == const.QBO_RECEIPT_ENTITY_TYPE_BILL
                        else None
                    ),
                    "qboJournalEntryId": (
                        mapping.get("external_receipt_id")
                        if qbo_entity_type == const.QBO_RECEIPT_ENTITY_TYPE_JOURNAL_ENTRY
                        else None
                    ),
                    "externalAccountId": mapping.get("external_account_id"),
                    "syncStatus": mapping.get("sync_status") or self.STATUS_IMPORT_FAILED,
                    "syncToken": mapping.get("sync_token"),
                    "mappingVersion": mapping.get("version"),
                    "endorsementEffectiveDate": mapping.get("endorsement_effective_date"),
                    "mappingUpdatedAt": mapping.get("updated_at"),
                    "linked": receipt_row is not None,
                    "reason": mapping.get("reason"),
                })

            summary_stmt = (
                select(ReceiptExternalMapModel.sync_status, func.count())
                .where(ReceiptExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .group_by(ReceiptExternalMapModel.sync_status)
            )
            raw_counts = session.execute(summary_stmt).all()
            status_counts: Dict[str, int] = {}
            for status, count in raw_counts:
                key = (status or "UNKNOWN").upper()
                status_counts[key] = int(count or 0)

            total_bills = int(sum(status_counts.values()))
            return {
                "rows": rows,
                "summary": {
                    "totalBills": total_bills,
                    "mappedBills": total_bills,
                    "unmappedBills": 0,
                    "syncedBills": status_counts.get("SYNCED", 0),
                    "pendingPushBills": status_counts.get("PENDING_PUSH", 0),
                    "failedBills": status_counts.get("FAILED", 0),
                    "importFailedBills": status_counts.get("IMPORT_FAILED", 0),
                    "syncFailedBills": status_counts.get("SYNC_FAILED", 0),
                },
                "pagination": {
                    "page": page,
                    "pageSize": page_size,
                    "totalRecords": total_records,
                    "totalPages": (total_records + page_size - 1) // page_size if total_records else 0,
                },
            }

    # ------------------------------------------------------------------
    # Bill-specific helpers
    # ------------------------------------------------------------------

    def _pick_txn_date(self, receipt: Dict[str, Any], info: Dict[str, Any]) -> Optional[str]:
        # QuickBooks Bill.TxnDate is the bill date. Do not use created_at,
        # and do not backfill receipts.invoice_date/order_date from it.
        for value in (
            info.get("billDate"),
            receipt.get("invoice_date"),
            receipt.get("order_date"),
        ):
            parsed = self._to_yyyy_mm_dd(value)
            if parsed:
                return parsed
        return None

    def _pick_due_date(self, receipt: Dict[str, Any], info: Dict[str, Any]) -> Optional[str]:
        # Due date is optional. Prefer the promoted receipts.due_date column,
        # then the legacy receiptAdditionalInfo dueDate value. Never fall back
        # to billDate/TxnDate.
        for value in (receipt.get("due_date"), info.get("dueDate")):
            parsed = self._to_yyyy_mm_dd(value)
            if parsed:
                return parsed
        return None

    def _pick_doc_number(self, receipt: Dict[str, Any], info: Dict[str, Any]) -> Optional[str]:
        for value in (receipt.get("invoice_number"), info.get("billNo"), info.get("billNumber"), receipt.get("order_number")):
            text = self._clean(value)
            if text:
                return text
        return None

    def _pick_stock_movement_buying_amount(self, info: Dict[str, Any]) -> Optional[float]:
        buying_total = self._to_float(info.get("buyingTotal"))
        if buying_total is not None and buying_total > 0:
            return round(buying_total, 2)

        items = info.get("receiptInfo")
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        if not isinstance(items, list) or not items:
            return None

        total = 0.0
        for item in items:
            if not isinstance(item, dict):
                return None
            amount = self._to_float(item.get("amount"))
            if amount is None:
                return None
            total += amount
        return round(total, 2) if total > 0 else None

    def _pick_total_amount(self, info: Dict[str, Any]) -> Optional[float]:
        for value in (
            info.get("total"),
            info.get("billAmount"),
            info.get("amount"),
            info.get("subTotal"),
            info.get("totalCost"),
        ):
            amount = self._to_float(value)
            if amount is not None:
                return amount
        return None

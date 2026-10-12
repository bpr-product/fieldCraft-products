from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy import String, and_, case, func, or_, select

from source.dal.bill_payment_models import FinalPaymentExternalMapModel
from source.dal.customer_payment_models import CustomerPaymentEntryModel, CustomerPaymentReceiveModel
from source.dal.db_session import tenant_session_scope
from source.dal.invoice_models import InvoiceExternalMapModel, InvoiceModel
from source.dal.project_models import ProjectModel
from source.dal.CustomerRepository import CustomerRepository
from source.dal.ProjectRepository import ProjectRepository


class CustomerPaymentRepository:
    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"
    STATUS_INVALID = "INVALID"
    STATUS_NOT_READY = "NOT_READY"

    NON_EXPORTABLE_STATUSES = {"cancelled", "canceled", "void", "voided", "failed", "rejected"}

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value or 0))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value: {value!r}") from exc

    @staticmethod
    def _service_id_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid service_id value: {value!r}") from exc

    def _is_current_mapping_clause(self):
        now = func.now()
        return and_(
            func.coalesce(FinalPaymentExternalMapModel.invalid, False).is_(False),
            func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
            or_(
                FinalPaymentExternalMapModel.endorsement_expiry_date.is_(None),
                FinalPaymentExternalMapModel.endorsement_expiry_date > now,
            ),
        )

    @staticmethod
    def _is_current_invoice_map_clause():
        now = func.now()
        return and_(
            func.coalesce(InvoiceExternalMapModel.invalid, False).is_(False),
            func.upper(func.coalesce(InvoiceExternalMapModel.sync_status, "")) != "INVALID",
            or_(
                InvoiceExternalMapModel.endorsement_expiry_date.is_(None),
                InvoiceExternalMapModel.endorsement_expiry_date > now,
            ),
        )

    def is_exportable_payment(self, payment: Dict[str, Any]) -> bool:
        if self._clean(payment.get("status")).lower() in self.NON_EXPORTABLE_STATUSES:
            return False
        return self._decimal(payment.get("amount")) > 0

    def _payment_to_dict(self, row: CustomerPaymentReceiveModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "chart_of_account_id": int(row.chart_of_account_id),
            "project_id": int(row.project_id) if row.project_id is not None else None,
            "customer_uid": self._clean(row.customer_uid),
            "reference_id": int(row.reference_id) if row.reference_id is not None else None,
            "payment_reference_no": row.payment_reference_no,
            "fieldcraft_transaction_id": row.fieldcraft_transaction_id,
            "payment_receipt_id": row.payment_receipt_id,
            "payment_transaction_id": row.payment_transaction_id,
            "amount": row.amount,
            "suspense_amount": row.suspense_amount,
            "currency_code": row.currency_code,
            "exchange_rate": row.exchange_rate,
            "pay_method": row.pay_method,
            "description": row.description,
            "status": row.status,
            "received_time": row.received_time,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _entry_to_dict(row: CustomerPaymentEntryModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "payment_receives_id": int(row.payment_receives_id),
            "project_id": int(row.project_id) if row.project_id is not None else None,
            "payment_amount": row.payment_amount,
            "interest_paid": row.interest_paid,
            "late_fee_paid": row.late_fee_paid,
            "reference_no": row.reference_no,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _mapping_to_dict(row: FinalPaymentExternalMapModel | None) -> Dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": int(row.id),
            "customer_id": int(row.customer_id) if row.customer_id is not None else None,
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "payment_receives_id": int(row.payment_receives_id) if row.payment_receives_id is not None else None,
            "payment_expense_id": int(row.payment_expense_id) if row.payment_expense_id is not None else None,
            "external_payment_id": row.external_payment_id,
            "external_invoice_id": row.external_invoice_id,
            "external_account_id": row.external_account_id,
            "sync_status": row.sync_status,
            "sync_token": row.sync_token,
            "version": int(row.version or 1),
            "reason": row.reason,
            "additional_info": row.additional_info,
            "updated_at": row.updated_at,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "invalid": bool(row.invalid),
        }

    @staticmethod
    def _parse_additional_info(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = str(value or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}

    def source_fingerprint(self, payment: Dict[str, Any], entries: List[Dict[str, Any]]) -> str:
        projection = {
            "customer_uid": self._clean(payment.get("customer_uid")),
            "amount": str(self._decimal(payment.get("amount"))),
            "received_time": payment.get("received_time").isoformat() if isinstance(payment.get("received_time"), datetime) else self._clean(payment.get("received_time")),
            "pay_method": self._clean(payment.get("pay_method")),
            "reference_id": payment.get("reference_id"),
            "fieldcraft_transaction_id": self._clean(payment.get("fieldcraft_transaction_id")),
            "payment_reference_no": self._clean(payment.get("payment_reference_no")),
            "description": self._clean(payment.get("description")),
            "currency_code": self._clean(payment.get("currency_code")),
            "suspense_amount": str(self._decimal(payment.get("suspense_amount"))),
            "chart_of_account_id": payment.get("chart_of_account_id"),
            "project_id": payment.get("project_id"),
            "entries": [
                {
                    "id": entry.get("id"),
                    "reference_no": self._clean(entry.get("reference_no")),
                    "project_id": entry.get("project_id"),
                    "payment_amount": str(self._decimal(entry.get("payment_amount"))),
                    "interest_paid": str(self._decimal(entry.get("interest_paid"))),
                    "late_fee_paid": str(self._decimal(entry.get("late_fee_paid"))),
                }
                for entry in sorted(entries, key=lambda item: int(item.get("id") or 0))
            ],
        }
        raw = json.dumps(projection, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _entries_for_payment_ids(self, session, payment_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        grouped: Dict[int, List[Dict[str, Any]]] = {int(pid): [] for pid in payment_ids}
        if not payment_ids:
            return grouped
        rows = session.execute(
            select(CustomerPaymentEntryModel)
            .where(CustomerPaymentEntryModel.payment_receives_id.in_(payment_ids))
            .order_by(CustomerPaymentEntryModel.payment_receives_id.asc(), CustomerPaymentEntryModel.id.asc())
        ).scalars().all()
        for row in rows:
            grouped.setdefault(int(row.payment_receives_id), []).append(self._entry_to_dict(row))
        return grouped

    def get_export_candidates(self, *, tenant_id: str, service_id: Any, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(FinalPaymentExternalMapModel, CustomerPaymentReceiveModel)
                .join(CustomerPaymentReceiveModel, CustomerPaymentReceiveModel.id == FinalPaymentExternalMapModel.payment_receives_id)
                .where(
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == service_id_int,
                    FinalPaymentExternalMapModel.payment_receives_id.is_not(None),
                    FinalPaymentExternalMapModel.payment_expense_id.is_(None),
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, "")).in_(
                        [self.STATUS_PENDING_PUSH, self.STATUS_FAILED, self.STATUS_SYNC_FAILED]
                    ),
                )
                .order_by(FinalPaymentExternalMapModel.id.asc())
                .offset(max(int(offset or 0), 0))
                .limit(max(int(limit or 500), 1))
            ).all()
            ids = [int(payment.id) for _, payment in rows]
            entries_by_payment = self._entries_for_payment_ids(session, ids)
            return [
                {
                    "mapping": self._mapping_to_dict(mapping),
                    "payment": self._payment_to_dict(payment),
                    "entries": entries_by_payment.get(int(payment.id), []),
                }
                for mapping, payment in rows
                if self.is_exportable_payment(self._payment_to_dict(payment))
            ]

    def resolve_invoice(self, *, tenant_id: str, service_id: Any, invoice_no: str, project_id: int) -> Dict[str, Any]:
        service_id_int = self._service_id_int(service_id)
        invoice_no = self._clean(invoice_no)
        with tenant_session_scope(tenant_id) as session:
            invoice = session.execute(
                select(InvoiceModel)
                .where(
                    InvoiceModel.invoice_no == invoice_no,
                    InvoiceModel.project_id == int(project_id),
                    func.coalesce(InvoiceModel.invalid, False).is_(False),
                )
                .order_by(InvoiceModel.version.desc(), InvoiceModel.id.desc())
            ).scalars().first()
            if not invoice:
                raise ValueError(f"FIELDCRAFT invoice {invoice_no} was not found for project {project_id}.")
            mapping = session.execute(
                select(InvoiceExternalMapModel)
                .where(
                    InvoiceExternalMapModel.invoice_id == int(invoice.id),
                    InvoiceExternalMapModel.tenant_id == tenant_id,
                    InvoiceExternalMapModel.service_id == service_id_int,
                    self._is_current_invoice_map_clause(),
                )
                .order_by(InvoiceExternalMapModel.version.desc(), InvoiceExternalMapModel.id.desc())
            ).scalars().first()
            if not mapping or not self._clean(mapping.external_invoice_id):
                raise ValueError(f"Invoice {invoice_no} has not been synced to QuickBooks. Sync Invoices first.")
            if self._clean(mapping.sync_status).upper() != self.STATUS_SYNCED:
                raise ValueError(
                    f"Invoice {invoice_no} is not fully synced to QuickBooks (status={self._clean(mapping.sync_status) or 'unknown'}). Sync Invoices first."
                )
            return {
                "invoice": {
                    "id": int(invoice.id),
                    "invoice_no": invoice.invoice_no,
                    "project_id": int(invoice.project_id),
                    "customer_uid": self._clean(invoice.customer_uid),
                },
                "mapping": {
                    "id": int(mapping.id),
                    "external_invoice_id": mapping.external_invoice_id,
                    "external_account_id": mapping.external_account_id,
                    "sync_status": mapping.sync_status,
                },
            }

    EXPORT_CLAIM_KEY = "exportClaim"
    EXPORT_CLAIM_TTL_SECONDS = 600

    def _active_export_claim(
        self, additional_info: Dict[str, Any], now: datetime
    ) -> Optional[Dict[str, Any]]:
        claim = additional_info.get(self.EXPORT_CLAIM_KEY)
        if not isinstance(claim, dict):
            return None
        claimed_at_raw = self._clean(claim.get("claimedAt"))
        if not claimed_at_raw:
            return None
        try:
            claimed_at = datetime.fromisoformat(claimed_at_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        age_seconds = (now - claimed_at.astimezone(timezone.utc)).total_seconds()
        if 0 <= age_seconds < self.EXPORT_CLAIM_TTL_SECONDS:
            return dict(claim)
        return None

    def claim_export_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
        source_fingerprint: str,
    ) -> Dict[str, Any]:
        """Claim one payment mapping without holding DB resources during QBO I/O."""
        service_id_int = self._service_id_int(service_id)
        mapping_id_int = int(mapping_id)
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(FinalPaymentExternalMapModel)
                .where(
                    FinalPaymentExternalMapModel.id == mapping_id_int,
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == service_id_int,
                )
                .with_for_update()
            ).scalars().first()
            if not row:
                raise ValueError(f"Payment external mapping {mapping_id_int} was not found.")

            payment_row = session.execute(
                select(CustomerPaymentReceiveModel).where(
                    CustomerPaymentReceiveModel.id == int(row.payment_receives_id)
                )
            ).scalars().first()
            if not payment_row:
                raise ValueError(
                    f"FIELDCRAFT customer payment {row.payment_receives_id} was not found "
                    f"for mapping {mapping_id_int}."
                )
            current_entries = self._entries_for_payment_ids(
                session, [int(row.payment_receives_id)]
            ).get(int(row.payment_receives_id), [])
            current_fingerprint = self.source_fingerprint(
                self._payment_to_dict(payment_row), current_entries
            )
            if self._clean(current_fingerprint) != self._clean(source_fingerprint):
                return {
                    "claimed": False,
                    "reason": "source_changed",
                    "mapping": self._mapping_to_dict(row),
                }

            additional_info = self._parse_additional_info(row.additional_info)
            if (
                self._clean(row.sync_status).upper() == self.STATUS_SYNCED
                and self._clean(additional_info.get("sourceFingerprint"))
                == self._clean(source_fingerprint)
            ):
                return {
                    "claimed": False,
                    "reason": "already_synced",
                    "mapping": self._mapping_to_dict(row),
                }

            active_claim = self._active_export_claim(additional_info, now)
            if active_claim:
                return {
                    "claimed": False,
                    "reason": "in_progress",
                    "mapping": self._mapping_to_dict(row),
                }

            additional_info[self.EXPORT_CLAIM_KEY] = {
                "claimedAt": now.isoformat(),
                "sourceFingerprint": self._clean(source_fingerprint),
            }
            row.additional_info = json.dumps(
                additional_info, separators=(",", ":"), sort_keys=True
            )
            row.updated_at = now
            mapping = self._mapping_to_dict(row)
            return {
                "claimed": True,
                "reason": "claimed",
                "mapping": mapping,
            }

    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_payment_id: str,
        external_account_id: str,
        sync_token: Optional[str],
        additional_info: Dict[str, Any],
        reason: Optional[str] = None,
    ) -> None:
        compact = json.dumps(additional_info, separators=(",", ":"), sort_keys=True)
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(FinalPaymentExternalMapModel)
                .where(FinalPaymentExternalMapModel.id == int(mapping_id))
                .with_for_update()
            ).scalars().first()
            if not row:
                raise ValueError(f"Payment external mapping {mapping_id} was not found.")
            row.external_payment_id = self._clean(external_payment_id) or None
            row.external_invoice_id = None
            row.external_account_id = self._clean(external_account_id) or None
            row.sync_token = self._clean(sync_token) or None
            row.sync_status = self.STATUS_SYNCED
            row.additional_info = compact
            row.reason = self._clean(reason)[:255] or None
            row.updated_at = self._now()
            row.invalid = False

    def mark_mapping_failed(self, *, tenant_id: str, mapping_id: int, reason: str) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(FinalPaymentExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = self.STATUS_SYNC_FAILED
            row.reason = self._clean(reason)[:255] or "Customer payment sync failed."
            additional_info = self._parse_additional_info(row.additional_info)
            if additional_info.pop(self.EXPORT_CLAIM_KEY, None) is not None:
                row.additional_info = json.dumps(
                    additional_info, separators=(",", ":"), sort_keys=True
                )
            row.updated_at = self._now()

    def get_payment_cdc_page(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        normalized_status = self._clean(sync_status).upper()
        search_term = self._clean(search).lower()

        exportable = and_(
            CustomerPaymentReceiveModel.amount > 0,
            func.lower(func.trim(func.coalesce(CustomerPaymentReceiveModel.status, ""))).notin_(sorted(self.NON_EXPORTABLE_STATUSES)),
        )
        display_status = case(
            (~exportable, self.STATUS_NOT_READY),
            else_=func.upper(func.coalesce(FinalPaymentExternalMapModel.sync_status, self.STATUS_PENDING_PUSH)),
        )
        # Keep summary counters stable across status/search filters, matching the
        # other QuickBooks modules. Row/pagination filters are applied separately.
        base_filters = [
            FinalPaymentExternalMapModel.payment_receives_id.is_not(None),
            FinalPaymentExternalMapModel.payment_expense_id.is_(None),
            FinalPaymentExternalMapModel.tenant_id == tenant_id,
            FinalPaymentExternalMapModel.service_id == service_id_int,
            self._is_current_mapping_clause(),
        ]
        row_filters = list(base_filters)
        if search_term:
            like = f"%{search_term}%"
            project_name_match = select(ProjectModel.id).where(
                ProjectModel.id == CustomerPaymentReceiveModel.project_id,
                or_(
                    func.lower(func.coalesce(ProjectModel.description, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.location, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.trade, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.trade_type, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.order_no, "")).like(like),
                ),
            ).exists()
            search_conditions = [
                func.cast(CustomerPaymentReceiveModel.id, String).like(like),
                func.lower(func.coalesce(CustomerPaymentReceiveModel.payment_reference_no, "")).like(like),
                func.lower(func.coalesce(CustomerPaymentReceiveModel.customer_uid, "")).like(like),
                func.lower(func.coalesce(CustomerPaymentReceiveModel.status, "")).like(like),
                project_name_match,
            ]
            customer_uids = CustomerRepository().find_customer_uids_by_display_name(tenant_id, search_term)
            if customer_uids:
                search_conditions.append(CustomerPaymentReceiveModel.customer_uid.in_(sorted(customer_uids)))
            row_filters.append(or_(*search_conditions))
        if normalized_status and normalized_status != "ALL":
            if normalized_status == "NEEDS_ATTENTION":
                row_filters.append(display_status.in_([self.STATUS_FAILED, self.STATUS_SYNC_FAILED]))
            else:
                row_filters.append(display_status == normalized_status)

        with tenant_session_scope(tenant_id) as session:
            summary_rows = session.execute(
                select(display_status.label("display_status"), func.count(FinalPaymentExternalMapModel.id))
                .select_from(FinalPaymentExternalMapModel)
                .join(CustomerPaymentReceiveModel, CustomerPaymentReceiveModel.id == FinalPaymentExternalMapModel.payment_receives_id)
                .where(*base_filters)
                .group_by(display_status)
            ).all()
            counts = {self._clean(status).upper(): int(count or 0) for status, count in summary_rows}
            summary_total = sum(counts.values())
            total_records = int(
                session.execute(
                    select(func.count(FinalPaymentExternalMapModel.id))
                    .select_from(FinalPaymentExternalMapModel)
                    .join(CustomerPaymentReceiveModel, CustomerPaymentReceiveModel.id == FinalPaymentExternalMapModel.payment_receives_id)
                    .where(*row_filters)
                ).scalar()
                or 0
            )
            rows_data = session.execute(
                select(CustomerPaymentReceiveModel, FinalPaymentExternalMapModel, display_status.label("display_status"))
                .select_from(FinalPaymentExternalMapModel)
                .join(CustomerPaymentReceiveModel, CustomerPaymentReceiveModel.id == FinalPaymentExternalMapModel.payment_receives_id)
                .where(*row_filters)
                .order_by(FinalPaymentExternalMapModel.updated_at.desc().nullslast(), FinalPaymentExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            ).all()
            payment_ids = [int(row.id) for row, _, _ in rows_data]
            entries_by_payment = self._entries_for_payment_ids(session, payment_ids)
            payment_dicts = [self._payment_to_dict(row) for row, _, _ in rows_data]
            customer_names = CustomerRepository().get_customer_display_names(
                tenant_id, (payment.get("customer_uid") for payment in payment_dicts)
            )
            project_names = ProjectRepository().get_project_display_names(
                tenant_id=tenant_id,
                project_ids=(payment.get("project_id") for payment in payment_dicts),
                customer_names=customer_names,
            )
            rows: List[Dict[str, Any]] = []
            for (payment_row, mapping_row, row_status), payment in zip(rows_data, payment_dicts):
                mapping = self._mapping_to_dict(mapping_row)
                entries = entries_by_payment.get(int(payment_row.id), [])
                applied = sum((self._decimal(entry.get("payment_amount")) for entry in entries), Decimal("0"))
                amount = self._decimal(payment.get("amount"))
                rows.append({
                    "paymentId": payment["id"],
                    "paymentReferenceNo": payment.get("payment_reference_no") or payment.get("fieldcraft_transaction_id"),
                    "customerUid": payment.get("customer_uid"),
                    "customerName": customer_names.get(payment.get("customer_uid")) or "",
                    "projectId": payment.get("project_id"),
                    "projectName": project_names.get(payment.get("project_id")) or "",
                    "paymentStatus": payment.get("status"),
                    "receivedTime": payment.get("received_time"),
                    "amount": float(amount),
                    "appliedAmount": float(applied),
                    "unappliedAmount": float(max(amount - applied, Decimal("0"))),
                    "allocationCount": len(entries),
                    "mappingId": mapping.get("id"),
                    "externalPaymentId": mapping.get("external_payment_id"),
                    "externalAccountId": mapping.get("external_account_id"),
                    "syncStatus": self._clean(row_status).upper() or self.STATUS_NOT_READY,
                    "mappingUpdatedAt": mapping.get("updated_at"),
                    "reason": mapping.get("reason"),
                    "source": "FIELDCRAFT",
                })
        return {
            "summary": {
                "total": summary_total,
                "synced": counts.get(self.STATUS_SYNCED, 0),
                "pending": counts.get(self.STATUS_PENDING_PUSH, 0),
                "failed": counts.get(self.STATUS_FAILED, 0) + counts.get(self.STATUS_SYNC_FAILED, 0),
                "notReady": counts.get(self.STATUS_NOT_READY, 0),
            },
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "totalRecords": total_records,
                "totalPages": (total_records + page_size - 1) // page_size if page_size else 0,
            },
            "rows": rows,
        }

    def get_payment_detail(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Return a mapped customer receipt and all of its reconciliation allocations."""
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(FinalPaymentExternalMapModel, CustomerPaymentReceiveModel)
                .join(
                    CustomerPaymentReceiveModel,
                    CustomerPaymentReceiveModel.id == FinalPaymentExternalMapModel.payment_receives_id,
                )
                .where(
                    FinalPaymentExternalMapModel.id == int(mapping_id),
                    FinalPaymentExternalMapModel.tenant_id == tenant_id,
                    FinalPaymentExternalMapModel.service_id == service_id_int,
                    FinalPaymentExternalMapModel.payment_receives_id.is_not(None),
                    FinalPaymentExternalMapModel.payment_expense_id.is_(None),
                    self._is_current_mapping_clause(),
                )
            ).first()
            if not row:
                return None

            mapping_row, payment_row = row
            mapping = self._mapping_to_dict(mapping_row)
            payment = self._payment_to_dict(payment_row)
            entries = self._entries_for_payment_ids(session, [int(payment_row.id)]).get(int(payment_row.id), [])
            customer_names = CustomerRepository().get_customer_display_names(
                tenant_id, [payment.get("customer_uid")]
            )
            all_project_ids = [payment.get("project_id")] + [entry.get("project_id") for entry in entries]
            project_names = ProjectRepository().get_project_display_names(
                tenant_id=tenant_id,
                project_ids=all_project_ids,
                customer_names=customer_names,
            )
            payment["customer_name"] = customer_names.get(payment.get("customer_uid")) or ""
            payment["project_name"] = project_names.get(payment.get("project_id")) or ""

            allocations: List[Dict[str, Any]] = []
            for entry in entries:
                allocation = dict(entry)
                invoice_no = self._clean(entry.get("reference_no"))
                project_id = entry.get("project_id")
                invoice_row = None
                invoice_map = None
                if invoice_no and project_id is not None:
                    invoice_row = session.execute(
                        select(InvoiceModel)
                        .where(
                            InvoiceModel.invoice_no == invoice_no,
                            InvoiceModel.project_id == int(project_id),
                            func.coalesce(InvoiceModel.invalid, False).is_(False),
                        )
                        .order_by(InvoiceModel.version.desc(), InvoiceModel.id.desc())
                    ).scalars().first()
                    if invoice_row:
                        invoice_map = session.execute(
                            select(InvoiceExternalMapModel)
                            .where(
                                InvoiceExternalMapModel.invoice_id == int(invoice_row.id),
                                InvoiceExternalMapModel.tenant_id == tenant_id,
                                InvoiceExternalMapModel.service_id == service_id_int,
                                self._is_current_invoice_map_clause(),
                            )
                            .order_by(InvoiceExternalMapModel.version.desc(), InvoiceExternalMapModel.id.desc())
                        ).scalars().first()

                allocation.update({
                    "project_name": project_names.get(project_id) or "",
                    "fieldcraft_invoice_id": int(invoice_row.id) if invoice_row else None,
                    "invoice_status": invoice_row.status if invoice_row else None,
                    "external_invoice_id": invoice_map.external_invoice_id if invoice_map else None,
                    "invoice_sync_status": invoice_map.sync_status if invoice_map else None,
                })
                allocations.append(allocation)

        applied = sum((self._decimal(item.get("payment_amount")) for item in entries), Decimal("0"))
        amount = self._decimal(payment.get("amount"))
        return {
            "mapping": {
                **mapping,
                "additional_info": self._parse_additional_info(mapping.get("additional_info")),
            },
            "payment": payment,
            "allocations": allocations,
            "totals": {
                "amount": float(amount),
                "applied_amount": float(applied),
                "unapplied_amount": float(max(amount - applied, Decimal("0"))),
                "allocation_count": len(entries),
            },
        }

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import String, and_, case, func, or_, select

from source.dal.db_session import tenant_session_scope
from source.dal.invoice_models import InvoiceExternalMapModel, InvoiceModel
from source.dal.customer_payment_models import CustomerPaymentEntryModel, CustomerPaymentReceiveModel
from source.dal.project_models import ProjectModel
from source.dal.CustomerRepository import CustomerRepository
from source.dal.ProjectRepository import ProjectRepository

logger = logging.getLogger(__name__)


class InvoiceRepository:
    """Tenant invoice + QuickBooks mapping repository.

    FIELDCRAFT owns invoices. QuickBooks is an export destination. Invoice edits in FIELDCRAFT
    create a new invoice row/version with the same invoice number, so mapping
    continuity is resolved by (invoice_no, project_id), not only invoice.id.
    """

    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"
    STATUS_INVALID = "INVALID"
    STATUS_NOT_READY = "NOT_READY"

    EXPORTABLE_FIELDCRAFT_STATUSES = {
        "un paid",
        "paid",
        "paid approved",
        "over paid",
        "partially paid",
        "verified",
    }

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def _service_id_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid service_id value: {value!r}") from exc

    @staticmethod
    def parse_json_list(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        raw = str(value or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [dict(item) for item in parsed if isinstance(item, dict)]

    @staticmethod
    def parse_json_dict(value: Any) -> Dict[str, Any]:
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

    def _is_current_mapping_clause(self):
        now = func.now()
        return and_(
            func.coalesce(InvoiceExternalMapModel.invalid, False).is_(False),
            func.upper(func.coalesce(InvoiceExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
            or_(
                InvoiceExternalMapModel.endorsement_expiry_date.is_(None),
                InvoiceExternalMapModel.endorsement_expiry_date > now,
            ),
        )

    @staticmethod
    def _is_current_invoice_clause():
        return func.coalesce(InvoiceModel.invalid, False).is_(False)

    def is_exportable_status(self, status: Any) -> bool:
        return self._clean(status).lower() in self.EXPORTABLE_FIELDCRAFT_STATUSES

    def is_exportable_invoice(self, invoice: Any) -> bool:
        """Return whether a current FIELDCRAFT invoice represents an accounting transaction to export."""
        if isinstance(invoice, dict):
            status = invoice.get("status")
            amount = invoice.get("invoiced_amount")
        else:
            status = getattr(invoice, "status", None)
            amount = getattr(invoice, "invoiced_amount", None)
        if not self.is_exportable_status(status):
            return False
        try:
            return amount is not None and float(amount) > 0
        except (TypeError, ValueError):
            return False

    def _invoice_to_dict(self, row: InvoiceModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "invoice_no": row.invoice_no,
            "customer_uid": self._clean(row.customer_uid),
            "project_id": int(row.project_id),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "issue_at": row.issue_at,
            "due_date": row.due_date,
            "status": row.status,
            "invoice_message": row.invoice_message,
            "term": row.term,
            "balance": row.balance,
            "tax_amount": row.tax_amount,
            "invoiced_amount": row.invoiced_amount,
            "amount_before_tax": (row.invoiced_amount - (row.tax_amount or 0)) if row.invoiced_amount is not None else row.total_amount,
            "total_amount": row.total_amount,
            "items": self.parse_json_list(row.items),
            "tax_info": self.parse_json_list(row.tax_info),
            "additional_info": self.parse_json_dict(row.additional_info),
            "invalid": bool(row.invalid),
            "version": int(row.version or 0),
            "type": row.type,
            "document_id": int(row.document_id) if row.document_id is not None else None,
        }

    @staticmethod
    def _fingerprint_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {
                str(key): InvoiceRepository._fingerprint_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple)):
            return [InvoiceRepository._fingerprint_value(item) for item in value]
        return str(value)

    def source_fingerprint(self, invoice: Any) -> str:
        """Fingerprint only fields that affect the exported QuickBooks Invoice.

        Payment/status-only updates do not change the QuickBooks Invoice. Invoice item
        details are intentionally excluded because the QBO representation is one summary
        sales line for the authoritative FIELDCRAFT invoice total.
        """
        data = invoice if isinstance(invoice, dict) else self._invoice_to_dict(invoice)
        projection = {
            "invoice_no": self._clean(data.get("invoice_no")),
            "customer_uid": self._clean(data.get("customer_uid")),
            "project_id": data.get("project_id"),
            "txn_date": data.get("issue_at") or data.get("created_at"),
            "due_date": data.get("due_date"),
            "invoice_message": self._clean(data.get("invoice_message")),
            "term": self._clean(data.get("term")),
            "invoiced_amount": data.get("invoiced_amount"),
            "tax_amount": data.get("tax_amount"),
            "tax_info": data.get("tax_info") or [],
            "document_id": data.get("document_id"),
        }
        encoded = json.dumps(
            self._fingerprint_value(projection),
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _mapping_to_dict(self, row: InvoiceExternalMapModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "invoice_id": int(row.invoice_id),
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id),
            "external_invoice_id": row.external_invoice_id,
            "external_account_id": row.external_account_id,
            "sync_status": row.sync_status,
            "version": int(row.version or 1),
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "reason": row.reason,
            "sync_token": row.sync_token,
            "updated_at": row.updated_at,
            "additional_info": self.parse_json_dict(row.additional_info),
            "invalid": bool(row.invalid),
        }

    def get_current_invoice_by_id(self, *, tenant_id: str, invoice_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceModel).where(
                    InvoiceModel.id == int(invoice_id),
                    self._is_current_invoice_clause(),
                )
            ).scalars().first()
            return self._invoice_to_dict(row) if row else None

    def get_mapping_by_id(
        self, *, tenant_id: str, service_id: Any, mapping_id: int
    ) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceExternalMapModel).where(
                    InvoiceExternalMapModel.id == int(mapping_id),
                    InvoiceExternalMapModel.tenant_id == tenant_id,
                    InvoiceExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_current_mapping_by_invoice_id(
        self, *, tenant_id: str, service_id: Any, invoice_id: int
    ) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceExternalMapModel)
                .where(
                    InvoiceExternalMapModel.tenant_id == tenant_id,
                    InvoiceExternalMapModel.service_id == self._service_id_int(service_id),
                    InvoiceExternalMapModel.invoice_id == int(invoice_id),
                    self._is_current_mapping_clause(),
                )
                .order_by(InvoiceExternalMapModel.updated_at.desc(), InvoiceExternalMapModel.id.desc())
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_current_mapping_by_external_invoice_id(
        self, *, tenant_id: str, service_id: Any, external_invoice_id: str
    ) -> Optional[Dict[str, Any]]:
        external_id = self._clean(external_invoice_id)
        if not external_id:
            return None
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceExternalMapModel)
                .where(
                    InvoiceExternalMapModel.tenant_id == tenant_id,
                    InvoiceExternalMapModel.service_id == self._service_id_int(service_id),
                    InvoiceExternalMapModel.external_invoice_id == external_id,
                    self._is_current_mapping_clause(),
                )
                .order_by(InvoiceExternalMapModel.updated_at.desc(), InvoiceExternalMapModel.id.desc())
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_export_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        exclude_mapping_ids: Optional[List[int]] = None,
        include_mapping_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(InvoiceExternalMapModel, InvoiceModel)
                .join(InvoiceModel, InvoiceModel.id == InvoiceExternalMapModel.invoice_id)
                .where(
                    InvoiceExternalMapModel.tenant_id == tenant_id,
                    InvoiceExternalMapModel.service_id == service_id_int,
                    self._is_current_mapping_clause(),
                    self._is_current_invoice_clause(),
                    func.upper(func.coalesce(InvoiceExternalMapModel.sync_status, "")).in_(
                        [self.STATUS_PENDING_PUSH, self.STATUS_FAILED, self.STATUS_SYNC_FAILED]
                    ),
                )
            )
            included = sorted({int(value) for value in (include_mapping_ids or []) if value is not None})
            if included:
                stmt = stmt.where(InvoiceExternalMapModel.id.in_(included))
            excluded = sorted({int(value) for value in (exclude_mapping_ids or []) if value is not None})
            if excluded:
                stmt = stmt.where(InvoiceExternalMapModel.id.notin_(excluded))
            rows = session.execute(
                stmt
                .order_by(InvoiceExternalMapModel.id.asc())
                .offset(max(int(offset or 0), 0))
                .limit(max(int(limit or 500), 1))
            ).all()
            return [
                {"mapping": self._mapping_to_dict(mapping), "invoice": self._invoice_to_dict(invoice)}
                for mapping, invoice in rows
                if self.is_exportable_invoice(invoice)
            ]

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
        """Claim one invoice mapping without holding DB resources during QBO I/O."""
        service_id_int = self._service_id_int(service_id)
        mapping_id_int = int(mapping_id)
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceExternalMapModel)
                .where(
                    InvoiceExternalMapModel.id == mapping_id_int,
                    InvoiceExternalMapModel.tenant_id == tenant_id,
                    InvoiceExternalMapModel.service_id == service_id_int,
                )
                .with_for_update()
            ).scalars().first()
            if not row:
                raise ValueError(f"Invoice external mapping {mapping_id_int} was not found")

            invoice_row = session.execute(
                select(InvoiceModel).where(
                    InvoiceModel.id == int(row.invoice_id),
                    self._is_current_invoice_clause(),
                )
            ).scalars().first()
            if not invoice_row:
                raise ValueError(
                    f"FIELDCRAFT invoice {row.invoice_id} was not found for mapping {mapping_id_int}"
                )
            current_fingerprint = self.source_fingerprint(invoice_row)
            if self._clean(current_fingerprint) != self._clean(source_fingerprint):
                return {
                    "claimed": False,
                    "reason": "source_changed",
                    "mapping": self._mapping_to_dict(row),
                }

            additional_info = self.parse_json_dict(row.additional_info)
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
        external_invoice_id: str,
        external_account_id: str,
        sync_token: Optional[str],
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceExternalMapModel)
                .where(InvoiceExternalMapModel.id == int(mapping_id))
                .with_for_update()
            ).scalars().first()
            if not row:
                raise ValueError(f"Invoice external mapping {mapping_id} was not found")
            row.external_invoice_id = self._clean(external_invoice_id) or None
            row.external_account_id = self._clean(external_account_id) or None
            row.sync_token = self._clean(sync_token) or None
            row.sync_status = self.STATUS_SYNCED
            row.reason = reason
            if additional_info is not None:
                row.additional_info = json.dumps(
                    additional_info, separators=(",", ":"), sort_keys=True
                )
            row.updated_at = self._now()
            row.invalid = False

    def mark_mapping_failed(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        reason: str,
        status: str = STATUS_FAILED,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceExternalMapModel)
                .where(InvoiceExternalMapModel.id == int(mapping_id))
                .with_for_update()
            ).scalars().first()
            if not row:
                return
            row.sync_status = self._clean(status).upper() or self.STATUS_FAILED
            row.reason = reason
            additional_info = self.parse_json_dict(row.additional_info)
            if additional_info.pop(self.EXPORT_CLAIM_KEY, None) is not None:
                row.additional_info = json.dumps(additional_info, separators=(",", ":"), sort_keys=True)
            row.updated_at = self._now()

    def get_invoice_cdc_page(
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

        exportable_clause = and_(
            func.lower(func.trim(func.coalesce(InvoiceModel.status, ""))).in_(
                sorted(self.EXPORTABLE_FIELDCRAFT_STATUSES)
            ),
            InvoiceModel.invoiced_amount > 0,
        )
        display_status = case(
            (~exportable_clause, self.STATUS_NOT_READY),
            else_=func.upper(func.coalesce(InvoiceExternalMapModel.sync_status, self.STATUS_PENDING_PUSH)),
        )
        # Summary counters describe the whole mapped module and must not change
        # when the user switches a status/search filter. Row/pagination filters
        # are kept separate, matching the existing Vendor/Project CDC pattern.
        base_filters = [
            InvoiceExternalMapModel.tenant_id == tenant_id,
            InvoiceExternalMapModel.service_id == service_id_int,
            self._is_current_mapping_clause(),
            self._is_current_invoice_clause(),
        ]
        row_filters = list(base_filters)
        if search_term:
            like = f"%{search_term}%"
            project_name_match = select(ProjectModel.id).where(
                ProjectModel.id == InvoiceModel.project_id,
                or_(
                    func.lower(func.coalesce(ProjectModel.description, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.location, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.trade, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.trade_type, "")).like(like),
                    func.lower(func.coalesce(ProjectModel.order_no, "")).like(like),
                ),
            ).exists()
            search_conditions = [
                func.lower(func.coalesce(InvoiceModel.invoice_no, "")).like(like),
                func.lower(func.coalesce(InvoiceModel.status, "")).like(like),
                func.lower(func.coalesce(InvoiceModel.customer_uid, "")).like(like),
                func.cast(InvoiceModel.project_id, String).like(like),
                project_name_match,
            ]
            customer_uids = CustomerRepository().find_customer_uids_by_display_name(tenant_id, search_term)
            if customer_uids:
                search_conditions.append(InvoiceModel.customer_uid.in_(sorted(customer_uids)))
            row_filters.append(or_(*search_conditions))

        if normalized_status and normalized_status != "ALL":
            if normalized_status == "NEEDS_ATTENTION":
                row_filters.append(
                    display_status.in_([self.STATUS_FAILED, self.STATUS_SYNC_FAILED])
                )
            else:
                row_filters.append(display_status == normalized_status)

        with tenant_session_scope(tenant_id) as session:
            summary_rows = session.execute(
                select(display_status.label("display_status"), func.count(InvoiceExternalMapModel.id))
                .select_from(InvoiceExternalMapModel)
                .join(InvoiceModel, InvoiceModel.id == InvoiceExternalMapModel.invoice_id)
                .where(*base_filters)
                .group_by(display_status)
            ).all()
            counts = {self._clean(status).upper(): int(count or 0) for status, count in summary_rows}
            summary_total = sum(counts.values())
            total_records = int(
                session.execute(
                    select(func.count(InvoiceExternalMapModel.id))
                    .select_from(InvoiceExternalMapModel)
                    .join(InvoiceModel, InvoiceModel.id == InvoiceExternalMapModel.invoice_id)
                    .where(*row_filters)
                ).scalar()
                or 0
            )

            rows_data = session.execute(
                select(InvoiceModel, InvoiceExternalMapModel, display_status.label("display_status"))
                .select_from(InvoiceExternalMapModel)
                .join(InvoiceModel, InvoiceModel.id == InvoiceExternalMapModel.invoice_id)
                .where(*row_filters)
                .order_by(InvoiceExternalMapModel.updated_at.desc().nullslast(), InvoiceExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            ).all()

            invoice_dicts = [self._invoice_to_dict(invoice) for invoice, _, _ in rows_data]
            customer_names = CustomerRepository().get_customer_display_names(
                tenant_id, (invoice.get("customer_uid") for invoice in invoice_dicts)
            )
            project_names = ProjectRepository().get_project_display_names(
                tenant_id=tenant_id,
                project_ids=(invoice.get("project_id") for invoice in invoice_dicts),
                customer_names=customer_names,
            )

            rows: List[Dict[str, Any]] = []
            for (invoice, mapping, row_status), inv in zip(rows_data, invoice_dicts):
                mp = self._mapping_to_dict(mapping)
                normalized_row_status = self._clean(row_status).upper() or self.STATUS_NOT_READY
                rows.append(
                    {
                        "invoiceId": inv["id"],
                        "invoiceNo": inv["invoice_no"],
                        "customerUid": inv["customer_uid"],
                        "customerName": customer_names.get(inv["customer_uid"]) or "",
                        "projectId": inv["project_id"],
                        "projectName": project_names.get(inv["project_id"]) or "",
                        "invoiceStatus": inv["status"],
                        "invoiceDate": (inv["issue_at"] or inv["created_at"]),
                        "dueDate": inv["due_date"],
                        "amount": float(inv["invoiced_amount"]) if inv["invoiced_amount"] is not None else None,
                        "amountBeforeTax": float(inv["amount_before_tax"]) if inv["amount_before_tax"] is not None else None,
                        "baseAmount": float(inv["amount_before_tax"]) if inv["amount_before_tax"] is not None else None,
                        "taxAmount": float(inv["tax_amount"]) if inv["tax_amount"] is not None else None,
                        "mappingId": mp.get("id"),
                        "externalInvoiceId": mp.get("external_invoice_id"),
                        "externalAccountId": mp.get("external_account_id"),
                        "syncStatus": normalized_row_status,
                        "syncToken": mp.get("sync_token"),
                        "mappingVersion": mp.get("version"),
                        "mappingUpdatedAt": mp.get("updated_at"),
                        "reason": mp.get("reason") or (
                            "Invoice is not yet in an issued/payment state or has no positive amount to export."
                            if normalized_row_status == self.STATUS_NOT_READY
                            else None
                        ),
                        "source": "FIELDCRAFT",
                    }
                )

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

    def get_invoice_detail(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Return the current mapped Invoice and its FIELDCRAFT detail snapshot.

        The external-map row is intentionally the lookup root. A source Invoice that has no
        current map is not part of the QuickBooks workspace and therefore cannot be opened from
        this endpoint.
        """
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(InvoiceExternalMapModel, InvoiceModel)
                .join(InvoiceModel, InvoiceModel.id == InvoiceExternalMapModel.invoice_id)
                .where(
                    InvoiceExternalMapModel.id == int(mapping_id),
                    InvoiceExternalMapModel.tenant_id == tenant_id,
                    InvoiceExternalMapModel.service_id == service_id_int,
                    self._is_current_mapping_clause(),
                )
            ).first()
            if not row:
                return None

            mapping_row, invoice_row = row
            invoice = self._invoice_to_dict(invoice_row)
            mapping = self._mapping_to_dict(mapping_row)
            customer_names = CustomerRepository().get_customer_display_names(
                tenant_id, [invoice.get("customer_uid")]
            )
            project_names = ProjectRepository().get_project_display_names(
                tenant_id=tenant_id,
                project_ids=[invoice.get("project_id")],
                customer_names=customer_names,
            )
            invoice["customer_name"] = customer_names.get(invoice.get("customer_uid")) or ""
            invoice["project_name"] = project_names.get(invoice.get("project_id")) or ""

            paid_amount = session.execute(
                select(func.coalesce(func.sum(CustomerPaymentEntryModel.payment_amount), 0))
                .join(
                    CustomerPaymentReceiveModel,
                    CustomerPaymentReceiveModel.id == CustomerPaymentEntryModel.payment_receives_id,
                )
                .where(
                    CustomerPaymentEntryModel.reference_no == invoice.get("invoice_no"),
                    CustomerPaymentEntryModel.project_id == invoice.get("project_id"),
                    func.lower(func.trim(func.coalesce(CustomerPaymentReceiveModel.status, ""))).notin_(
                        ["cancelled", "canceled", "void", "voided", "failed", "rejected"]
                    ),
                )
            ).scalar_one()
            paid_amount = Decimal(str(paid_amount or 0))
            invoice_total = invoice.get("invoiced_amount")
            if invoice_total is None:
                invoice_total = invoice.get("total_amount")
            total_decimal = Decimal(str(invoice_total or 0))
            invoice["paid_amount"] = paid_amount
            invoice["balance_due"] = max(total_decimal - paid_amount, Decimal("0"))

        document = None
        if invoice.get("document_id") is not None:
            from source.dal.DocumentRepository import DocumentRepository

            document = DocumentRepository().get_document_by_id(tenant_id, int(invoice["document_id"]))

        return {
            "mapping": mapping,
            "invoice": invoice,
            "document": document,
        }


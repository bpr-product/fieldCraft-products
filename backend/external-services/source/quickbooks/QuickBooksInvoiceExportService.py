from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.CustomerRepository import CustomerRepository
from source.dal.InvoiceRepository import InvoiceRepository
from source.dal.ProjectRepository import ProjectRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ServiceRoleRepository import ServiceRoleRepository
from source.quickbooks.QBOInvoiceAttachmentService import QBOInvoiceAttachmentService
from source.quickbooks.QuickBooksAuthentication import (
    QuickBooksOAuthClient,
    QuickBooksReconnectRequiredError,
)
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)


class InvoicePrerequisiteError(ValueError):
    """A user-actionable prerequisite failure that applies to invoice rows."""


class QuickBooksInvoiceExportService:
    """Export issued FIELDCRAFT invoices to QuickBooks Online.

    FIELDCRAFT is the source of truth. Customer and project identities are resolved from
    their existing external-map records. Invoice sales lines use the QuickBooks
    Service Item mapped from the active FIELDCRAFT ``labour`` role.
    """

    INVOICE_ITEM_ROLE_NAME = "labour"

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.invoice_repo = InvoiceRepository()
        self.customer_repo = CustomerRepository()
        self.project_repo = ProjectRepository()
        self.role_repo = ServiceRoleRepository()
        self.attachment_service = QBOInvoiceAttachmentService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _money(value: Any) -> Decimal:
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid money value: {value!r}") from exc

    @staticmethod
    def _date_string(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        text = str(value).strip()
        if not text:
            return None
        return text[:10]

    @staticmethod
    def _build_reason(message: str, **extra: Any) -> str:
        payload: Dict[str, Any] = {"message": message}
        for key, value in extra.items():
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _mark_prerequisite_failure(
        self,
        *,
        tenant_id: str,
        candidates: List[Dict[str, Any]],
        summary: Dict[str, Any],
        message: str,
    ) -> Dict[str, Any]:
        """Mark candidate invoices as needing attention without failing the whole job."""
        reason = self._build_reason(message)
        for candidate in candidates:
            invoice = candidate.get("invoice") or {}
            mapping = candidate.get("mapping") or {}
            mapping_id = mapping.get("id")
            detail: Dict[str, Any] = {
                "fieldcraftInvoiceId": invoice.get("id"),
                "invoiceNo": invoice.get("invoice_no"),
                "projectId": invoice.get("project_id"),
                "customerUid": invoice.get("customer_uid"),
                "mappingId": mapping_id,
                const.KEY_STATUS: "failed",
                const.KEY_ACTION: "prerequisite required",
                const.KEY_ERROR: message,
                const.KEY_REASON: reason,
            }
            if invoice.get("invoiced_amount") is not None:
                detail["amount"] = float(invoice.get("invoiced_amount"))

            if mapping_id:
                try:
                    self.invoice_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(mapping_id),
                        reason=reason,
                        status=self.invoice_repo.STATUS_SYNC_FAILED,
                    )
                except Exception:
                    logger.exception(
                        "[InvoiceExport] failed to persist prerequisite failure | mapping_id=%s",
                        mapping_id,
                    )

            summary["syncFailed"] += 1
            summary[const.KEY_RESULTS].append(detail)
            summary[const.KEY_FAILURES].append(dict(detail))

        summary[const.KEY_MESSAGE] = (
            f"Invoice sync completed with {summary['syncFailed']} invoice(s) that need attention."
        )
        return summary

    def _resolve_invoice_item_mapping(
        self,
        *,
        tenant_id: str,
        service_id: str,
    ) -> Dict[str, Any]:
        role = self.role_repo.get_active_role_by_name(
            tenant_id=tenant_id,
            name=self.INVOICE_ITEM_ROLE_NAME,
        )
        if not role or not role.get("id"):
            raise InvoicePrerequisiteError(
                f"Service Role '{self.INVOICE_ITEM_ROLE_NAME}' must be set up and synced to QuickBooks "
                "before this invoice can be synced."
            )

        mapping = self.role_repo.get_current_mapping_by_role_id(
            tenant_id=tenant_id,
            service_id=service_id,
            role_id=int(role["id"]),
        )
        if not mapping:
            raise InvoicePrerequisiteError(
                f"Service Role '{self.INVOICE_ITEM_ROLE_NAME}' must be synced to QuickBooks "
                "before this invoice can be synced. Sync Service Roles, then try again."
            )

        sync_status = self._clean(mapping.get("sync_status")).upper()
        if sync_status != self.role_repo.STATUS_SYNCED:
            logger.info(
                "[InvoiceExport] invoice prerequisite not ready | role=%s sync_status=%s",
                self.INVOICE_ITEM_ROLE_NAME,
                sync_status or "unknown",
            )
            raise InvoicePrerequisiteError(
                f"Service Role '{self.INVOICE_ITEM_ROLE_NAME}' must be synced to QuickBooks "
                "before this invoice can be synced. Sync Service Roles, then try again."
            )

        item_id = self._clean(mapping.get("external_service_id"))
        if not item_id:
            raise InvoicePrerequisiteError(
                f"Service Role '{self.INVOICE_ITEM_ROLE_NAME}' needs to be synced again before "
                "this invoice can be synced. Sync Service Roles, then try again."
            )
        if len(item_id) > 128:
            raise InvoicePrerequisiteError(
                f"Service Role '{self.INVOICE_ITEM_ROLE_NAME}' needs attention before this invoice "
                "can be synced. Sync Service Roles, then try again."
            )

        return {
            "role": role,
            "mapping": mapping,
            "item_id": item_id,
        }

    @staticmethod
    def _headers(access_token: str, *, json_content: bool = False) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _assert_mapping_realm(self, mapping: Optional[Dict[str, Any]], realm_id: str, label: str) -> None:
        if not mapping:
            return
        mapped_realm = self._clean(mapping.get("external_account_id"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise InvoicePrerequisiteError(
                f"{label} is linked to a different QuickBooks company. "
                "Sync the related QuickBooks module again, then retry this invoice."
            )

    def _open_qbo_session(self, *, tenant_id: str, service_id: str) -> Dict[str, str]:
        connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
        config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}

        status = self._clean(connection.get(const.KEY_STATUS)).lower()
        realm_id = self._clean(connection.get(const.KEY_REALM_ID))
        refresh_token = self._clean(connection.get(const.KEY_REFRESH_TOKEN))
        env = self._clean(connection.get(const.KEY_ENV) or config.get(const.KEY_ENV) or "sandbox").lower()
        client_id = self._clean(config.get(const.KEY_CLIENT_ID))
        client_secret = self._clean(config.get(const.KEY_CLIENT_SECRET))

        if status != const.STATUS_CONNECTED:
            raise ValueError(f"QuickBooks is not connected (status={status or 'unknown'}).")
        if not realm_id or not refresh_token:
            raise ValueError("QuickBooks connection is missing realmId or refreshToken.")
        if not client_id or not client_secret:
            raise ValueError("QuickBooks client credentials are missing (ClientId/ClientSecret).")

        access_token = QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(tenant_id),
            service_id=str(service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
            debug_context={"operation": "invoice_export", "env": env},
        )

        from source.Utils import QBODiscoveryClient

        return {
            "access_token": access_token,
            "realm_id": realm_id,
            "env": env,
            "base_url": QBODiscoveryClient.get_qbo_api_base(env).rstrip("/"),
        }

    def _validate_invoice_item(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        item_id: str,
    ) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/item/{item_id}"
        response = qbo_err.qbo_get(url, headers=self._headers(access_token), timeout=30)
        qbo_err.raise_for_qbo_response(
            response,
            fallback_message=f"Mapped QuickBooks invoice Service Item {item_id} could not be loaded.",
        )
        item = (qbo_err.safe_json(response).get("Item") or {})
        if not self._clean(item.get("Id")):
            raise InvoicePrerequisiteError(
                "The QuickBooks Service Role used for invoices needs attention. "
                "Sync Service Roles, then try this invoice again."
            )
        if item.get("Active") is False:
            raise InvoicePrerequisiteError(
                "The QuickBooks Service Role used for invoices is inactive. "
                "Update or sync Service Roles, then try this invoice again."
            )
        if self._clean(item.get("Type")).lower() != "service":
            raise InvoicePrerequisiteError(
                "The QuickBooks item linked to the invoice Service Role is not a Service item. "
                "Sync Service Roles, then try this invoice again."
            )
        return item

    def _resolve_references(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        invoice: Dict[str, Any],
    ) -> Dict[str, Any]:
        customer_uid = self._clean(invoice.get("customer_uid"))
        project_id = invoice.get("project_id")
        if not customer_uid:
            raise ValueError("Invoice customer_uid is missing.")
        if project_id is None:
            raise ValueError("Invoice project_id is missing.")

        project = self.project_repo.get_active_project_by_id(
            tenant_id=tenant_id,
            project_id=int(project_id),
        )
        if not project:
            raise ValueError("The invoice project does not exist or is inactive in FIELDCRAFT.")
        project_customer_uid = self._clean(project.get("customer_uid"))
        if project_customer_uid and project_customer_uid != customer_uid:
            raise ValueError(
                "Invoice customer does not match the customer assigned to its FIELDCRAFT project. "
                "Correct the invoice/project relationship before syncing."
            )

        customer_mapping = self.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid(
            tenant_id,
            service_id,
            customer_uid,
            external_account_id=realm_id,
        )
        if not customer_mapping or not self._clean(customer_mapping.get("external_customer_id")):
            raise ValueError(
                "The invoice customer is not mapped to QuickBooks. Run Customer sync before Invoice sync."
            )
        self._assert_mapping_realm(customer_mapping, realm_id, "Customer mapping")

        project_mapping = self.project_repo.get_current_mapping_by_project_id(
            tenant_id=tenant_id,
            service_id=service_id,
            project_id=int(project_id),
        )
        if not project_mapping or not self._clean(project_mapping.get("external_project_id")):
            raise ValueError(
                "The invoice project is not mapped to QuickBooks. Run Project sync before Invoice sync."
            )
        self._assert_mapping_realm(project_mapping, realm_id, "Project mapping")

        parent_customer_id = self._clean(customer_mapping.get("external_customer_id"))
        external_project_id = self._clean(project_mapping.get("external_project_id"))
        project_target = self.project_repo.get_mapping_sync_target(project_mapping)

        if project_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
            return {
                "customer_ref": parent_customer_id,
                "project_ref": external_project_id,
                "project_sync_target": project_target,
                "project_name": self._clean(project.get("description")),
                "customer_mapping_id": customer_mapping.get("id"),
                "project_mapping_id": project_mapping.get("id"),
            }

        if project_target == const.PROJECT_SYNC_TARGET_SUB_CUSTOMER:
            return {
                "customer_ref": external_project_id,
                "project_ref": None,
                "project_sync_target": project_target,
                "project_name": self._clean(project.get("description")),
                "customer_mapping_id": customer_mapping.get("id"),
                "project_mapping_id": project_mapping.get("id"),
            }

        raise ValueError(
            "The invoice project mapping has no supported QuickBooks sync target. "
            "Run Project sync before Invoice sync."
        )

    def _item_line(
        self,
        *,
        amount: Decimal,
        item_id: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        line: Dict[str, Any] = {
            "Amount": float(amount),
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {"ItemRef": {"value": item_id}},
        }
        if self._clean(description):
            line["Description"] = self._clean(description)[:4000]
        return line

    def _tax_description(self, invoice: Dict[str, Any]) -> str:
        tax_amount = self._money(invoice.get("tax_amount") or 0)
        labels: List[str] = []
        for tax in invoice.get("tax_info") or []:
            if not isinstance(tax, dict):
                continue
            status = self._clean(tax.get("status")).lower()
            if status in {"deleted", "invalid", "inactive"}:
                continue
            name = self._clean(tax.get("name"))
            percentage = self._clean(tax.get("percentage"))
            label = name
            if percentage:
                label = f"{label} {percentage}%".strip()
            if label and label not in labels:
                labels.append(label)

        tax_label = ", ".join(labels) or "Tax"
        return f"{tax_label}: ${tax_amount:.2f}"

    def _build_invoice_line_description(
        self,
        *,
        invoice: Dict[str, Any],
        total_amount: Decimal,
    ) -> str:
        parts = [f"FIELDCRAFT Invoice {self._clean(invoice.get('invoice_no'))}"]
        term = self._clean(invoice.get("term"))
        if term:
            parts.append(f"Term: {term}")
        parts.append(self._tax_description(invoice))
        parts.append(f"Total: ${total_amount:.2f}")
        return " | ".join(parts)[:4000]

    def _build_lines(self, *, invoice: Dict[str, Any], item_id: str) -> List[Dict[str, Any]]:
        target_total = self._money(invoice.get("invoiced_amount"))
        if target_total <= 0:
            raise ValueError("Invoice amount must be greater than zero to export to QuickBooks.")

        return [
            self._item_line(
                amount=target_total,
                item_id=item_id,
                description=self._build_invoice_line_description(
                    invoice=invoice,
                    total_amount=target_total,
                ),
            )
        ]

    def _attach_invoice_document(
        self,
        *,
        tenant_id: str,
        access_token: str,
        base_url: str,
        realm_id: str,
        qbo_invoice_id: str,
        invoice: Dict[str, Any],
    ) -> Dict[str, Any]:
        document_id = invoice.get("document_id")
        if document_id is None:
            return {"status": "not_present", "documentId": None}

        return self.attachment_service.attach_invoice_document(
            tenant_id=tenant_id,
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            invoice_id=qbo_invoice_id,
            document_id=int(document_id),
            invoice_project_id=(
                int(invoice["project_id"]) if invoice.get("project_id") is not None else None
            ),
        )

    def build_invoice_payload(
        self,
        *,
        invoice: Dict[str, Any],
        item_id: str,
        references: Dict[str, Any],
    ) -> Dict[str, Any]:
        invoice_no = self._clean(invoice.get("invoice_no"))
        if not invoice_no:
            raise ValueError("Invoice number is missing.")

        payload: Dict[str, Any] = {
            "CustomerRef": {"value": references["customer_ref"]},
            "DocNumber": invoice_no,
            "Line": self._build_lines(invoice=invoice, item_id=item_id),
        }
        if references.get("project_ref"):
            payload["ProjectRef"] = {"value": references["project_ref"]}

        txn_date = self._date_string(invoice.get("issue_at") or invoice.get("created_at"))
        due_date = self._date_string(invoice.get("due_date"))
        if txn_date:
            payload["TxnDate"] = txn_date
        if due_date:
            payload["DueDate"] = due_date

        customer_message = self._clean(invoice.get("invoice_message"))
        if customer_message:
            payload["CustomerMemo"] = {"value": customer_message[:1000]}

        note_parts = [f"FIELDCRAFT Invoice: {invoice_no}"]
        project_name = self._clean(references.get("project_name"))
        if project_name:
            note_parts.append(f"Project: {project_name}")
        payload["PrivateNote"] = " | ".join(note_parts)[:4000]
        return payload

    def get_invoice_in_qbo(
        self, *, access_token: str, base_url: str, realm_id: str, invoice_id: str
    ) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/invoice/{invoice_id}"
        response = qbo_err.qbo_get(url, headers=self._headers(access_token), timeout=30)
        qbo_err.raise_for_qbo_response(response, fallback_message="QuickBooks invoice lookup failed.")
        return qbo_err.safe_json(response).get("Invoice") or {}

    def find_invoice_candidates_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice_no: str,
    ) -> List[Dict[str, Any]]:
        escaped = escape_qbo_literal(invoice_no)
        query = f"select * from Invoice where DocNumber = '{escaped}' STARTPOSITION 1 MAXRESULTS 20"
        url = f"{base_url}/v3/company/{realm_id}/query"
        response = qbo_err.qbo_get(
            url,
            headers=self._headers(access_token),
            params={"query": query},
            timeout=30,
        )
        qbo_err.raise_for_qbo_response(response, fallback_message="QuickBooks invoice candidate lookup failed.")
        rows = (qbo_err.safe_json(response).get("QueryResponse") or {}).get("Invoice") or []
        if isinstance(rows, dict):
            return [rows]
        return rows if isinstance(rows, list) else []

    def _candidate_matches_references(self, candidate: Dict[str, Any], references: Dict[str, Any]) -> bool:
        customer_id = self._clean((candidate.get("CustomerRef") or {}).get("value"))
        project_id = self._clean((candidate.get("ProjectRef") or {}).get("value"))
        if customer_id != self._clean(references.get("customer_ref")):
            return False
        expected_project = self._clean(references.get("project_ref"))
        return project_id == expected_project if expected_project else not project_id

    def create_invoice_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/invoice"
        response = qbo_err.qbo_post(
            url,
            headers=self._headers(access_token, json_content=True),
            json=payload,
            timeout=30,
        )
        qbo_err.raise_for_qbo_response(
            response,
            success_statuses=(200, 201),
            fallback_message="QuickBooks invoice creation failed.",
        )
        return qbo_err.safe_json(response).get("Invoice") or {}

    @staticmethod
    def _is_sync_token_error(exc: Exception) -> bool:
        text = " ".join(
            str(value or "")
            for value in (
                exc,
                getattr(exc, "detail", None),
            )
        ).lower()
        return "stale object" in text or "synctoken" in text or "sync token" in text

    @staticmethod
    def _is_missing_qbo_entity_error(exc: Exception) -> bool:
        if isinstance(exc, qbo_err.QuickBooksAPIError) and exc.status_code == 404:
            return True
        text = " ".join(
            str(value or "")
            for value in (exc, getattr(exc, "detail", None))
        ).lower()
        return "object not found" in text or "not found" in text or "does not exist" in text

    def _post_invoice_update(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice_id: str,
        sync_token: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/invoice"
        body = {
            "Id": str(invoice_id),
            "SyncToken": str(sync_token),
            "sparse": True,
            **payload,
        }
        response = qbo_err.qbo_post(
            url,
            headers=self._headers(access_token, json_content=True),
            json=body,
            timeout=30,
        )
        qbo_err.raise_for_qbo_response(
            response,
            success_statuses=(200, 201),
            fallback_message="QuickBooks invoice update failed.",
        )
        return qbo_err.safe_json(response).get("Invoice") or {}

    def update_invoice_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice_id: str,
        sync_token: Optional[str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        token = self._clean(sync_token)
        if token:
            try:
                return self._post_invoice_update(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    invoice_id=invoice_id,
                    sync_token=token,
                    payload=payload,
                )
            except Exception as exc:
                if not self._is_sync_token_error(exc):
                    raise

        current = self.get_invoice_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            invoice_id=invoice_id,
        )
        current_token = self._clean(current.get("SyncToken"))
        if not current_token:
            raise ValueError("QuickBooks invoice update failed because SyncToken is missing.")
        return self._post_invoice_update(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            invoice_id=invoice_id,
            sync_token=current_token,
            payload=payload,
        )

    def _upsert_invoice(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice: Dict[str, Any],
        mapping: Dict[str, Any],
        payload: Dict[str, Any],
        references: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str]:
        mapped_id = self._clean(mapping.get("external_invoice_id"))
        if mapped_id:
            self._assert_mapping_realm(mapping, realm_id, "Invoice mapping")
            try:
                updated = self.update_invoice_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    invoice_id=mapped_id,
                    sync_token=mapping.get("sync_token"),
                    payload=payload,
                )
                return updated, "updated in QBO"
            except Exception as exc:
                if not self._is_missing_qbo_entity_error(exc):
                    raise
                logger.warning(
                    "[InvoiceExport] mapped QBO invoice is missing; attempting identity recovery "
                    "| fieldcraft_invoice_id=%s qbo_invoice_id=%s",
                    invoice.get("id"),
                    mapped_id,
                )

        candidates = self.find_invoice_candidates_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            invoice_no=self._clean(invoice.get("invoice_no")),
        )
        matches = [row for row in candidates if self._candidate_matches_references(row, references)]
        if len(matches) > 1:
            raise ValueError(
                "Multiple QuickBooks invoices match this FIELDCRAFT invoice number/customer/project. "
                "Resolve the duplicates in QuickBooks before retrying."
            )
        if len(matches) == 1:
            existing = matches[0]
            existing_id = self._clean(existing.get("Id"))
            if not existing_id:
                raise ValueError("Matched QuickBooks invoice has no Id.")
            updated = self.update_invoice_in_qbo(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                invoice_id=existing_id,
                sync_token=existing.get("SyncToken"),
                payload=payload,
            )
            return updated, "mapped existing QBO invoice"

        created = self.create_invoice_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            payload=payload,
        )
        return created, "created in QBO"

    def export_invoices(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int = 500,
        offset: int = 0,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
        exclude_mapping_ids: Optional[List[int]] = None,
        include_mapping_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        if not tenant_id or not service_id:
            return {const.KEY_OK: False, const.KEY_MESSAGE: "tenant_id and service_id are required."}

        safe_audit_log(
            tenant_id=tenant_id,
            action="invoice_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_invoice",
        )

        try:
            candidates = self.invoice_repo.get_export_candidates(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
                exclude_mapping_ids=exclude_mapping_ids,
                include_mapping_ids=include_mapping_ids,
            )
        except Exception as exc:
            logger.exception("[InvoiceExport] failed to load export candidates")
            return {const.KEY_OK: False, const.KEY_MESSAGE: f"Unable to load FIELDCRAFT invoices for export: {exc}"}

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "requested": len(candidates),
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "mappedFromQbo": 0,
            "attachmentFailed": 0,
            "skippedAlreadySynced": 0,
            "skippedInProgress": 0,
            "skippedSourceChanged": 0,
            "failed": 0,
            "syncFailed": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
        }
        if not candidates:
            return summary

        try:
            item_source = self._resolve_invoice_item_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
            )
        except InvoicePrerequisiteError as exc:
            logger.info("[InvoiceExport] prerequisite validation failed | reason=%s", exc)
            return self._mark_prerequisite_failure(
                tenant_id=tenant_id,
                candidates=candidates,
                summary=summary,
                message=str(exc),
            )

        try:
            session = self._open_qbo_session(tenant_id=tenant_id, service_id=service_id)
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            logger.exception("[InvoiceExport] QuickBooks connection validation failed")
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = str(exc)
            return summary

        try:
            item_mapping = item_source["mapping"]
            self._assert_mapping_realm(item_mapping, session["realm_id"], "Service Role 'labour'")
            if not self._clean(item_mapping.get("external_account_id")):
                raise InvoicePrerequisiteError(
                    "Service Role 'labour' needs to be synced again before this invoice can be synced. "
                    "Sync Service Roles, then try again."
                )
            invoice_item_id = item_source["item_id"]
            invoice_item = self._validate_invoice_item(
                access_token=session["access_token"],
                base_url=session["base_url"],
                realm_id=session["realm_id"],
                item_id=invoice_item_id,
            )
        except InvoicePrerequisiteError as exc:
            logger.info("[InvoiceExport] prerequisite validation failed | reason=%s", exc)
            return self._mark_prerequisite_failure(
                tenant_id=tenant_id,
                candidates=candidates,
                summary=summary,
                message=str(exc),
            )
        except Exception as exc:
            logger.exception("[InvoiceExport] invoice item validation failed")
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = str(exc)
            return summary

        summary["env"] = session["env"]
        summary["realmIdPresent"] = True
        summary["invoiceItem"] = {
            "id": invoice_item_id,
            "name": invoice_item.get("Name"),
            "type": invoice_item.get("Type"),
            "sourceRoleId": item_source["role"].get("id"),
            "sourceRoleName": item_source["role"].get("name"),
            "roleMappingId": item_mapping.get("id"),
        }

        for candidate in candidates:
            invoice = candidate.get("invoice") or {}
            mapping = candidate.get("mapping") or {}
            mapping_id = mapping.get("id")
            detail: Dict[str, Any] = {
                "fieldcraftInvoiceId": invoice.get("id"),
                "invoiceNo": invoice.get("invoice_no"),
                "projectId": invoice.get("project_id"),
                "customerUid": invoice.get("customer_uid"),
                "mappingId": mapping_id,
                "amount": float(invoice.get("invoiced_amount")) if invoice.get("invoiced_amount") is not None else None,
            }
            try:
                if not mapping_id:
                    raise ValueError("Invoice external mapping is missing.")
                if not self.invoice_repo.is_exportable_invoice(invoice):
                    raise ValueError("Invoice is not in an exportable state.")

                references = self._resolve_references(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=session["realm_id"],
                    invoice=invoice,
                )
                payload = self.build_invoice_payload(
                    invoice=invoice,
                    item_id=invoice_item_id,
                    references=references,
                )
                source_fingerprint = self.invoice_repo.source_fingerprint(invoice)
                claim = self.invoice_repo.claim_export_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=int(mapping_id),
                    source_fingerprint=source_fingerprint,
                )
                if not claim.get("claimed"):
                    claim_reason = self._clean(claim.get("reason"))
                    current_mapping = claim.get("mapping") or {}
                    if claim_reason == "already_synced":
                        summary["skippedAlreadySynced"] += 1
                        action = "alreadySynced"
                    elif claim_reason == "source_changed":
                        summary["skippedSourceChanged"] += 1
                        action = "sourceChangedRetry"
                    else:
                        summary["skippedInProgress"] += 1
                        action = "syncAlreadyInProgress"
                    detail.update(
                        {
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: action,
                            "qboInvoiceId": self._clean(current_mapping.get("external_invoice_id")) or None,
                        }
                    )
                    summary[const.KEY_RESULTS].append(detail)
                    continue

                # The row lock is released when claim_export_mapping returns. No DB
                # transaction or advisory-lock connection is held during QBO HTTP I/O.
                locked_mapping = claim.get("mapping") or {}
                qbo_invoice, action = self._upsert_invoice(
                    access_token=session["access_token"],
                    base_url=session["base_url"],
                    realm_id=session["realm_id"],
                    invoice=invoice,
                    mapping=locked_mapping,
                    payload=payload,
                    references=references,
                )
                qbo_invoice_id = self._clean(qbo_invoice.get("Id"))
                if not qbo_invoice_id:
                    raise ValueError("QuickBooks returned an invoice without an Id.")

                document_id = invoice.get("document_id")
                attachment: Dict[str, Any] = {
                    "status": "pending" if document_id is not None else "not_present",
                    "documentId": int(document_id) if document_id is not None else None,
                }
                sync_metadata = {
                    "invoiceItemId": invoice_item_id,
                    "invoiceItemRole": self.INVOICE_ITEM_ROLE_NAME,
                    "invoiceItemRoleId": item_source["role"].get("id"),
                    "invoiceItemRoleMappingId": item_mapping.get("id"),
                    "projectSyncTarget": references.get("project_sync_target"),
                    "customerMappingId": references.get("customer_mapping_id"),
                    "projectMappingId": references.get("project_mapping_id"),
                    "sourceFingerprint": source_fingerprint,
                    "attachment": attachment,
                }

                # Persist the QBO identity immediately after the HTTP operation. The
                # repository uses a short row-lock transaction for this write.
                initial_sync_metadata = {
                    **sync_metadata,
                    "attachment": dict(attachment),
                }
                self.invoice_repo.mark_mapping_synced(
                    tenant_id=tenant_id,
                    mapping_id=int(mapping_id),
                    external_invoice_id=qbo_invoice_id,
                    external_account_id=session["realm_id"],
                    sync_token=self._clean(qbo_invoice.get("SyncToken")) or None,
                    reason=self._build_reason(
                        "FIELDCRAFT invoice exported to QuickBooks.",
                        action=action,
                        projectSyncTarget=references.get("project_sync_target"),
                        invoiceItemId=invoice_item_id,
                        invoiceItemRole=self.INVOICE_ITEM_ROLE_NAME,
                        invoiceItemRoleMappingId=item_mapping.get("id"),
                        attachmentStatus=attachment.get("status"),
                        documentId=attachment.get("documentId"),
                    ),
                    additional_info=initial_sync_metadata,
                )

                # The QBO invoice identity is durable before the optional attachment
                # transfer. Attachment I/O also runs with no database lock held.
                attachment_error: Optional[str] = None
                try:
                    attachment = self._attach_invoice_document(
                        tenant_id=tenant_id,
                        access_token=session["access_token"],
                        base_url=session["base_url"],
                        realm_id=session["realm_id"],
                        qbo_invoice_id=qbo_invoice_id,
                        invoice=invoice,
                    )
                except Exception as exc:
                    attachment_error = self._clean(str(exc)) or "Invoice attachment sync failed."
                    attachment = {
                        "status": "failed",
                        "documentId": int(document_id) if document_id is not None else None,
                        "error": attachment_error[:1000],
                    }
                    summary["attachmentFailed"] += 1
                    logger.exception(
                        "[InvoiceExport] invoice attachment failed after QBO invoice sync "
                        "| tenant_id=%s invoice_id=%s qbo_invoice_id=%s document_id=%s",
                        tenant_id,
                        invoice.get("id"),
                        qbo_invoice_id,
                        document_id,
                    )

                sync_metadata["attachment"] = attachment
                final_reason = self._build_reason(
                    (
                        "FIELDCRAFT invoice exported to QuickBooks; attachment requires attention."
                        if attachment_error
                        else "FIELDCRAFT invoice exported to QuickBooks."
                    ),
                    action=action,
                    projectSyncTarget=references.get("project_sync_target"),
                    invoiceItemId=invoice_item_id,
                    invoiceItemRole=self.INVOICE_ITEM_ROLE_NAME,
                    invoiceItemRoleMappingId=item_mapping.get("id"),
                    attachmentStatus=attachment.get("status"),
                    attachmentError=attachment_error,
                    documentId=attachment.get("documentId"),
                    attachableId=attachment.get("attachableId"),
                )
                try:
                    self.invoice_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=int(mapping_id),
                        external_invoice_id=qbo_invoice_id,
                        external_account_id=session["realm_id"],
                        sync_token=self._clean(qbo_invoice.get("SyncToken")) or None,
                        reason=final_reason,
                        additional_info=sync_metadata,
                    )
                except Exception as exc:
                    # The QBO invoice identity was already persisted above. Keep
                    # the accounting sync successful even if only the final
                    # attachment sub-status could not be refreshed.
                    logger.exception(
                        "[InvoiceExport] failed to persist final attachment metadata "
                        "| tenant_id=%s mapping_id=%s",
                        tenant_id,
                        mapping_id,
                    )
                    detail["attachmentMetadataWarning"] = self._clean(str(exc))[:255]

                if action == "created in QBO":
                    summary["createdInQbo"] += 1
                elif action == "mapped existing QBO invoice":
                    summary["mappedFromQbo"] += 1
                else:
                    summary["updatedInQbo"] += 1
                detail.update(
                    {
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: action,
                        "qboInvoiceId": qbo_invoice_id,
                        const.KEY_SYNC_TOKEN: qbo_invoice.get("SyncToken"),
                        "projectSyncTarget": references.get("project_sync_target"),
                        "attachment": attachment,
                    }
                )
                summary[const.KEY_RESULTS].append(detail)
            except Exception as exc:
                logger.exception(
                    "[InvoiceExport] invoice export failed | tenant_id=%s invoice_id=%s invoice_no=%s",
                    tenant_id,
                    invoice.get("id"),
                    invoice.get("invoice_no"),
                )
                error_message = str(exc)
                if mapping_id:
                    try:
                        self.invoice_repo.mark_mapping_failed(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            reason=self._build_reason(
                                error_message,
                                intuitTid=getattr(exc, "intuit_tid", None),
                            ),
                            status=self.invoice_repo.STATUS_SYNC_FAILED,
                        )
                    except Exception:
                        logger.exception(
                            "[InvoiceExport] failed to persist invoice mapping failure | mapping_id=%s",
                            mapping_id,
                        )
                detail.update(
                    {
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "invoice export failed",
                        const.KEY_ERROR: error_message,
                    }
                )
                if isinstance(exc, qbo_err.QuickBooksAPIError):
                    if exc.intuit_tid:
                        detail[const.KEY_INTUIT_TID] = exc.intuit_tid
                    if exc.status_code is not None:
                        detail[const.KEY_STATUS_CODE] = exc.status_code
                summary["syncFailed"] += 1
                summary[const.KEY_RESULTS].append(detail)
                summary[const.KEY_FAILURES].append(dict(detail))

        if summary["syncFailed"]:
            summary["partialSuccess"] = bool(
                summary["createdInQbo"] or summary["updatedInQbo"] or summary["mappedFromQbo"]
            )
            summary[const.KEY_MESSAGE] = (
                f"Invoice sync completed with {summary['syncFailed']} invoice(s) that need attention."
            )

        safe_audit_log(
            tenant_id=tenant_id,
            action="invoice_export_completed",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="completed" if not summary["syncFailed"] else "partial",
            resource_type="quickbooks_invoice",
            context=(
                f"created={summary['createdInQbo']} updated={summary['updatedInQbo']} "
                f"mapped={summary['mappedFromQbo']} failed={summary['syncFailed']}"
            ),
        )
        return summary

    def export_all_invoices(
        self,
        *,
        tenant_id: str,
        service_id: str,
        batch_size: int = 500,
    ) -> Dict[str, Any]:
        """Export each current candidate at most once during one scheduled run."""
        batch_size = min(max(int(batch_size or 500), 1), 1000)
        aggregate: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "requested": 0,
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "mappedFromQbo": 0,
            "failed": 0,
            "syncFailed": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
        }
        attempted_mapping_ids: set[int] = set()

        while True:
            batch = self.export_invoices(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=batch_size,
                offset=0,
                exclude_mapping_ids=sorted(attempted_mapping_ids),
            )
            if not batch.get(const.KEY_OK, True):
                return batch

            batch_requested = int(batch.get("requested") or 0)
            aggregate["requested"] += batch_requested
            for key in ("createdInQbo", "updatedInQbo", "mappedFromQbo", "failed", "syncFailed"):
                aggregate[key] += int(batch.get(key) or 0)
            batch_results = batch.get(const.KEY_RESULTS) or []
            aggregate[const.KEY_RESULTS].extend(batch_results)
            aggregate[const.KEY_FAILURES].extend(batch.get(const.KEY_FAILURES) or [])
            for row in batch_results:
                if not isinstance(row, dict):
                    continue
                mapping_id = row.get("mappingId")
                if mapping_id is not None:
                    attempted_mapping_ids.add(int(mapping_id))
            if batch.get("env"):
                aggregate["env"] = batch.get("env")
            if batch.get("invoiceItem"):
                aggregate["invoiceItem"] = batch.get("invoiceItem")

            if batch_requested == 0 or batch_requested < batch_size:
                break

        if aggregate["syncFailed"]:
            aggregate["partialSuccess"] = bool(
                aggregate["createdInQbo"] or aggregate["updatedInQbo"] or aggregate["mappedFromQbo"]
            )
            aggregate[const.KEY_MESSAGE] = (
                f"Invoice sync completed with {aggregate['syncFailed']} invoice(s) that need attention."
            )
        return aggregate


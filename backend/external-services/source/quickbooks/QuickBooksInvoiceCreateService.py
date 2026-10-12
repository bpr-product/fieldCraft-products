from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.InvoiceRepository import InvoiceRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksReconnectRequiredError
from source.quickbooks.QuickBooksInvoiceExportService import QuickBooksInvoiceExportService

logger = logging.getLogger(__name__)


class QuickBooksInvoiceCreateService:
    """Real-time export for one application-approved FIELDCRAFT invoice.

    The application sends the current ``invoice_external_map`` id after approval.
    This service validates that exact mapping and then delegates to the regular
    invoice exporter, so real-time and scheduled/manual invoice sync share the
    same QuickBooks payload, prerequisite, recovery, attachment, and retry logic.
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.invoice_repo = InvoiceRepository()
        self.invoice_exporter = QuickBooksInvoiceExportService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def create_invoice(
        self,
        *,
        tenant_id: str,
        service_id: Optional[str],
        invoice_external_map_id: int,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        mapping_id = int(invoice_external_map_id or 0)

        if not tenant_id:
            return {const.KEY_OK: False, const.KEY_MESSAGE: "tenant_id is required"}
        if not mapping_id:
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "invoice_external_map_id is required",
            }
        if not service_id:
            service_id = self._clean(
                self.qbo_repo.getServiceIdByName(const.SERVICE_NAME_QUICKBOOKS)
            )
        if not service_id:
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "QuickBooks service_id could not be resolved",
            }

        safe_audit_log(
            tenant_id=tenant_id,
            action="invoice_create_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_invoice",
        )

        result: Dict[str, Any] = {const.KEY_OK: False}
        invoice_id: Optional[int] = None
        try:
            mapping = self.invoice_repo.get_mapping_by_id(
                tenant_id=tenant_id,
                service_id=service_id,
                mapping_id=mapping_id,
            )
            if not mapping:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Invoice external map was not found or is no longer active.",
                    "invoiceExternalMapId": mapping_id,
                }
                return result

            invoice_id = int(mapping.get("invoice_id") or 0)
            sync_status = self._clean(mapping.get("sync_status")).upper()
            external_invoice_id = self._clean(mapping.get("external_invoice_id"))

            if sync_status == self.invoice_repo.STATUS_SYNCED and external_invoice_id:
                result = {
                    const.KEY_OK: True,
                    const.KEY_MESSAGE: "Invoice is already synced to QuickBooks.",
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "invoiceId": invoice_id,
                    "invoiceExternalMapId": mapping_id,
                    "qboInvoiceId": external_invoice_id,
                    const.KEY_STATUS: "already_synced",
                }
                return result

            allowed_statuses = {
                self.invoice_repo.STATUS_PENDING_PUSH,
                self.invoice_repo.STATUS_FAILED,
                self.invoice_repo.STATUS_SYNC_FAILED,
            }
            if sync_status not in allowed_statuses:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "Invoice is not eligible for real-time export. "
                        f"Expected sync_status in {sorted(allowed_statuses)}, "
                        f"got '{sync_status or 'EMPTY'}'."
                    ),
                    "invoiceId": invoice_id or None,
                    "invoiceExternalMapId": mapping_id,
                    "syncStatus": sync_status or None,
                }
                return result

            invoice = self.invoice_repo.get_current_invoice_by_id(
                tenant_id=tenant_id,
                invoice_id=invoice_id,
            )
            if not invoice:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: f"Invoice was not found for invoice_id={invoice_id}.",
                    "invoiceExternalMapId": mapping_id,
                }
                return result
            if not self.invoice_repo.is_exportable_invoice(invoice):
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "Invoice is not ready for QuickBooks export. It must be a current, "
                        "approved accounting invoice with an amount greater than zero."
                    ),
                    "invoiceId": invoice_id,
                    "invoiceExternalMapId": mapping_id,
                    "invoiceStatus": invoice.get("status"),
                }
                return result

            result = self.invoice_exporter.export_invoices(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=1,
                offset=0,
                object_id=object_id,
                email=email,
                user_role=user_role,
                include_mapping_ids=[mapping_id],
            )
            result["invoiceId"] = invoice_id
            result["invoiceExternalMapId"] = mapping_id

            if int(result.get("requested") or 0) == 0:
                latest = self.invoice_repo.get_mapping_by_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                )
                latest_external_id = self._clean((latest or {}).get("external_invoice_id"))
                latest_status = self._clean((latest or {}).get("sync_status")).upper()
                if latest_status == self.invoice_repo.STATUS_SYNCED and latest_external_id:
                    result.update(
                        {
                            const.KEY_OK: True,
                            const.KEY_MESSAGE: "Invoice is already synced to QuickBooks.",
                            "qboInvoiceId": latest_external_id,
                            const.KEY_STATUS: "already_synced",
                        }
                    )
                    return result
                result.update(
                    {
                        const.KEY_OK: False,
                        const.KEY_MESSAGE: "Invoice is no longer eligible for real-time export.",
                    }
                )
                return result

            sync_failed = int(result.get("syncFailed") or 0)
            failed = int(result.get("failed") or 0)
            result[const.KEY_OK] = bool(result.get(const.KEY_OK, True)) and sync_failed == 0 and failed == 0
            if result[const.KEY_OK]:
                result.setdefault(const.KEY_MESSAGE, "Invoice exported to QuickBooks.")
            else:
                result.setdefault(
                    const.KEY_MESSAGE,
                    "Invoice export to QuickBooks needs attention. Review the invoice sync status and retry.",
                )
            return result
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            logger.exception(
                "[InvoiceCreate] real-time invoice export failed | tenant_id=%s mapping_id=%s",
                tenant_id,
                mapping_id,
            )
            result = {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "Real-time QuickBooks invoice export failed.",
                const.KEY_ERROR: str(exc),
                "invoiceId": invoice_id,
                "invoiceExternalMapId": mapping_id,
            }
            return result
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=(
                    "invoice_create_completed"
                    if result.get(const.KEY_OK)
                    else "invoice_create_failed"
                ),
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status="success" if result.get(const.KEY_OK) else "failed",
                resource_type="quickbooks_invoice",
                context=f"mapping_id={mapping_id}",
            )

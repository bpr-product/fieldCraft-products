from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.CustomerRepository import CustomerRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksReconnectRequiredError
from source.quickbooks.QuickBooksCustomerExportService import QuickBooksCustomerExportService

logger = logging.getLogger(__name__)


class QuickBooksCustomerCreateService:
    """Real-time export for one current FIELDCRAFT customer mapping.

    The application sends the current ``customer_external_map`` id after the
    mapping becomes ``PENDING_PUSH``. This service validates that exact mapping
    and delegates to the normal Customer exporter so manual and real-time sync
    share the same create, match, update, conflict, and mapping behavior.
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.customer_repo = CustomerRepository()
        self.customer_exporter = QuickBooksCustomerExportService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def create_customer(
        self,
        *,
        tenant_id: str,
        service_id: Optional[str],
        customer_external_map_id: int,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        mapping_id = int(customer_external_map_id or 0)

        if not tenant_id:
            return {const.KEY_OK: False, const.KEY_MESSAGE: "tenant_id is required"}
        if not mapping_id:
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "customer_external_map_id is required",
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
            action="customer_create_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_customer",
        )

        result: Dict[str, Any] = {const.KEY_OK: False}
        customer_id: Optional[int] = None
        try:
            mapping = self.customer_repo.get_mapping_by_id(
                tenant_id=tenant_id,
                service_id=service_id,
                mapping_id=mapping_id,
            )
            if not mapping:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Customer external map was not found or is no longer active.",
                    "customerExternalMapId": mapping_id,
                }
                return result

            customer_id = int(mapping.get("customer_id") or 0)
            if not customer_id:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Customer external map is not linked to a FIELDCRAFT customer.",
                    "customerExternalMapId": mapping_id,
                }
                return result

            current_mapping = self.customer_repo.get_current_mapping_by_customer_id(
                tenant_id=tenant_id,
                service_id=service_id,
                customer_id=customer_id,
            )
            if not current_mapping or int(current_mapping.get("id") or 0) != mapping_id:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Customer external map is no longer the current mapping.",
                    "customerId": customer_id,
                    "customerExternalMapId": mapping_id,
                }
                return result

            sync_status = self._clean(mapping.get("sync_status")).upper()
            external_customer_id = self._clean(mapping.get("external_customer_id"))

            if sync_status == self.customer_repo.STATUS_SYNCED and external_customer_id:
                result = {
                    const.KEY_OK: True,
                    const.KEY_MESSAGE: "Customer is already synced to QuickBooks.",
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "customerId": customer_id,
                    "customerExternalMapId": mapping_id,
                    "qboCustomerId": external_customer_id,
                    const.KEY_STATUS: "already_synced",
                }
                return result

            if sync_status not in self.customer_repo.EXPORTABLE_SYNC_STATUSES:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "Customer is not eligible for real-time export. "
                        f"Expected sync_status in {sorted(self.customer_repo.EXPORTABLE_SYNC_STATUSES)}, "
                        f"got '{sync_status or 'EMPTY'}'."
                    ),
                    "customerId": customer_id,
                    "customerExternalMapId": mapping_id,
                    "syncStatus": sync_status or None,
                }
                return result

            customer = self.customer_repo.get_current_customer_by_id(customer_id)
            if not customer:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: f"Current customer was not found for customer_id={customer_id}.",
                    "customerExternalMapId": mapping_id,
                }
                return result

            result = self.customer_exporter.export_customers(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=1,
                offset=0,
                include_mapping_ids=[mapping_id],
                object_id=object_id,
                email=email,
                user_role=user_role,
            )
            result["customerId"] = customer_id
            result["customerExternalMapId"] = mapping_id

            if int(result.get("requested") or 0) == 0:
                latest = self.customer_repo.get_mapping_by_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                )
                latest_external_id = self._clean((latest or {}).get("external_customer_id"))
                latest_status = self._clean((latest or {}).get("sync_status")).upper()
                if latest_status == self.customer_repo.STATUS_SYNCED and latest_external_id:
                    result.update(
                        {
                            const.KEY_OK: True,
                            const.KEY_MESSAGE: "Customer is already synced to QuickBooks.",
                            "qboCustomerId": latest_external_id,
                            const.KEY_STATUS: "already_synced",
                        }
                    )
                    return result
                result.update(
                    {
                        const.KEY_OK: False,
                        const.KEY_MESSAGE: "Customer is no longer eligible for real-time export.",
                    }
                )
                return result

            if int(result.get("skippedInProgress") or 0) > 0:
                result.update(
                    {
                        const.KEY_OK: True,
                        const.KEY_STATUS: "sync_in_progress",
                        const.KEY_MESSAGE: "Customer sync is already in progress.",
                    }
                )
                return result

            if int(result.get("skippedAlreadySynced") or 0) > 0:
                latest = self.customer_repo.get_mapping_by_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                )
                result.update(
                    {
                        const.KEY_OK: True,
                        const.KEY_STATUS: "already_synced",
                        const.KEY_MESSAGE: "Customer is already synced to QuickBooks.",
                        "qboCustomerId": self._clean((latest or {}).get("external_customer_id")) or None,
                    }
                )
                return result

            if int(result.get("skippedNotEligible") or 0) > 0:
                result.update(
                    {
                        const.KEY_OK: False,
                        const.KEY_MESSAGE: "Customer is no longer eligible for real-time export.",
                    }
                )
                return result

            failed = int(result.get("failed") or 0)
            result[const.KEY_OK] = bool(result.get(const.KEY_OK, True)) and failed == 0
            if result[const.KEY_OK]:
                result.setdefault(const.KEY_MESSAGE, "Customer exported to QuickBooks.")
            else:
                result.setdefault(
                    const.KEY_MESSAGE,
                    "Customer export to QuickBooks needs attention. Review the customer sync status and retry.",
                )
            return result
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            logger.exception(
                "[CustomerCreate] real-time customer export failed | tenant_id=%s mapping_id=%s",
                tenant_id,
                mapping_id,
            )
            result = {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "Real-time QuickBooks customer export failed.",
                const.KEY_ERROR: str(exc),
                "customerId": customer_id,
                "customerExternalMapId": mapping_id,
            }
            return result
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=(
                    "customer_create_completed"
                    if result.get(const.KEY_OK)
                    else "customer_create_failed"
                ),
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status="success" if result.get(const.KEY_OK) else "failed",
                resource_type="quickbooks_customer",
                context=f"mapping_id={mapping_id}",
            )

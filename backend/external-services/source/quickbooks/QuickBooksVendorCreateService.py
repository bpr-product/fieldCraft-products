from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.VendorRepository import VendorRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksReconnectRequiredError
from source.quickbooks.QuickBooksVendorExportService import QuickBooksVendorExportService

logger = logging.getLogger(__name__)


class QuickBooksVendorCreateService:
    """Real-time export for one current FIELDCRAFT vendor mapping."""

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.vendor_repo = VendorRepository()
        self.vendor_exporter = QuickBooksVendorExportService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def create_vendor(
        self,
        *,
        tenant_id: str,
        service_id: Optional[str],
        vendor_external_map_id: int,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        mapping_id = int(vendor_external_map_id or 0)

        if not tenant_id:
            return {const.KEY_OK: False, const.KEY_MESSAGE: "tenant_id is required"}
        if not mapping_id:
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "vendor_external_map_id is required",
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
            action="vendor_create_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_vendor",
        )

        result: Dict[str, Any] = {const.KEY_OK: False}
        vendor_id: Optional[int] = None
        try:
            mapping = self.vendor_repo.get_mapping_by_id(
                tenant_id=tenant_id,
                service_id=service_id,
                mapping_id=mapping_id,
            )
            if not mapping:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Vendor external map was not found.",
                    "vendorExternalMapId": mapping_id,
                }
                return result

            vendor_id = int(mapping.get("vendor_id") or 0)
            if not vendor_id:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Vendor external map is not linked to a FIELDCRAFT vendor.",
                    "vendorExternalMapId": mapping_id,
                }
                return result

            current_mapping = self.vendor_repo.get_current_mapping_by_vendor_id(
                tenant_id=tenant_id,
                service_id=service_id,
                vendor_id=vendor_id,
            )
            if not current_mapping or int(current_mapping.get("id") or 0) != mapping_id:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Vendor external map is no longer the current mapping.",
                    "vendorId": vendor_id,
                    "vendorExternalMapId": mapping_id,
                }
                return result

            sync_status = self._clean(mapping.get("sync_status")).upper()
            external_vendor_id = self._clean(mapping.get("external_vendor_id"))

            if sync_status == self.vendor_repo.STATUS_SYNCED and external_vendor_id:
                result = {
                    const.KEY_OK: True,
                    const.KEY_MESSAGE: "Vendor is already synced to QuickBooks.",
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "vendorId": vendor_id,
                    "vendorExternalMapId": mapping_id,
                    "qboVendorId": external_vendor_id,
                    const.KEY_STATUS: "already_synced",
                }
                return result

            if sync_status not in self.vendor_repo.EXPORTABLE_SYNC_STATUSES:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "Vendor is not eligible for real-time export. "
                        f"Expected sync_status in {sorted(self.vendor_repo.EXPORTABLE_SYNC_STATUSES)}, "
                        f"got '{sync_status or 'EMPTY'}'."
                    ),
                    "vendorId": vendor_id,
                    "vendorExternalMapId": mapping_id,
                    "syncStatus": sync_status or None,
                }
                return result

            vendor = self.vendor_repo.get_current_vendor_by_id(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
            )
            if not vendor:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: f"Current vendor was not found for vendor_id={vendor_id}.",
                    "vendorExternalMapId": mapping_id,
                }
                return result

            result = self.vendor_exporter.export_vendors(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=1,
                offset=0,
                include_mapping_ids=[mapping_id],
                object_id=object_id,
                email=email,
                user_role=user_role,
                use_mapping_change_timestamp=True,
            )
            result["vendorId"] = vendor_id
            result["vendorExternalMapId"] = mapping_id

            if int(result.get("requested") or 0) == 0:
                latest = self.vendor_repo.get_mapping_by_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                )
                latest_external_id = self._clean((latest or {}).get("external_vendor_id"))
                latest_status = self._clean((latest or {}).get("sync_status")).upper()
                if latest_status == self.vendor_repo.STATUS_SYNCED and latest_external_id:
                    result.update(
                        {
                            const.KEY_OK: True,
                            const.KEY_MESSAGE: "Vendor is already synced to QuickBooks.",
                            "qboVendorId": latest_external_id,
                            const.KEY_STATUS: "already_synced",
                        }
                    )
                    return result
                result.update(
                    {
                        const.KEY_OK: False,
                        const.KEY_MESSAGE: "Vendor is no longer eligible for real-time export.",
                    }
                )
                return result

            if int(result.get("skippedAlreadySynced") or 0) > 0:
                latest = self.vendor_repo.get_mapping_by_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                )
                result.update(
                    {
                        const.KEY_OK: True,
                        const.KEY_STATUS: "already_synced",
                        const.KEY_MESSAGE: "Vendor is already synced to QuickBooks.",
                        "qboVendorId": self._clean((latest or {}).get("external_vendor_id")) or None,
                    }
                )
                return result

            if int(result.get("skippedNotEligible") or 0) > 0:
                result.update(
                    {
                        const.KEY_OK: False,
                        const.KEY_MESSAGE: "Vendor is no longer eligible for real-time export.",
                    }
                )
                return result

            failed = int(result.get("failed") or 0)
            result[const.KEY_OK] = bool(result.get(const.KEY_OK, True)) and failed == 0
            if result[const.KEY_OK]:
                latest = self.vendor_repo.get_mapping_by_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                )
                result.setdefault(
                    "qboVendorId",
                    self._clean((latest or {}).get("external_vendor_id")) or None,
                )
                result.setdefault(const.KEY_MESSAGE, "Vendor exported to QuickBooks.")
            else:
                result.setdefault(
                    const.KEY_MESSAGE,
                    "Vendor export to QuickBooks needs attention. Review the vendor sync status and retry.",
                )
            return result
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            logger.exception(
                "[VendorCreate] real-time vendor export failed | tenant_id=%s mapping_id=%s",
                tenant_id,
                mapping_id,
            )
            result = {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "Real-time QuickBooks vendor export failed.",
                const.KEY_ERROR: str(exc),
                "vendorId": vendor_id,
                "vendorExternalMapId": mapping_id,
            }
            return result
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=(
                    "vendor_create_completed"
                    if result.get(const.KEY_OK)
                    else "vendor_create_failed"
                ),
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status="success" if result.get(const.KEY_OK) else "failed",
                resource_type="quickbooks_vendor",
                context=f"mapping_id={mapping_id}",
            )

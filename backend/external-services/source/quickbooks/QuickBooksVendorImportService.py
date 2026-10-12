from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.dal.EmployeeRepository import EmployeeRepository
from source.dal.VendorRepository import VendorRepository
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.Utils.QuickBooksLogging import log_cdc_debug
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)



class QuickBooksVendorImportService:
    """QuickBooks -> FIELDCRAFT vendor reconciliation against the production vendor design."""

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None, **extra: Any) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        for key, value in extra.items():
            if value is not None:
                data[key] = value
        return json.dumps(data)

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.vendor_repo = VendorRepository()
        self.vendor_map_repo = VendorExternalMapRepository()
        self.qbo_service = QuickBooksService()
        self.scheduled_job_repo = ScheduledJobRepository()
        self.employee_repo = EmployeeRepository()

    def import_vendors(
        self,
        *,
        tenant_id: str,
        service_id: str,
        page_size: int = 100,
        start_position: int = 1,
        max_vendors: Optional[int] = None,
        dry_run: bool = False,
        changed_since_override: Optional[Any] = None,
        force_full: bool = False,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info(f"[VendorImport] import_vendors called | tenant_id={tenant_id} service_id={service_id} page_size={page_size} start_position={start_position} max_vendors={max_vendors} dry_run={dry_run} changed_since_override={changed_since_override}")
        safe_audit_log(
            tenant_id=tenant_id,
            action="vendor_import_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_vendor",
        )
        # Track final outcome so the finally block logs it regardless of which exit path is taken.
        _audit_action = "vendor_import_failed"
        _audit_status = "failed"
        _audit_context: Optional[str] = None
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "linkedExistingInFieldCraft": 0,
            "created in FIELDCRAFT": 0,
            "updatedInFIELDCRAFT": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedDeleted": 0,
            "skippedWorkerVendor": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "qboCdcEntities": ["Vendor"],
        }
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            self._tenant_id = tenant_id
            self._service_id = service_id
            self._connection = connection

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            logger.info(f"[VendorImport] Connection info | realmId_present={bool(realm_id)} env={env} has_refresh_token={bool(refresh_token)}")
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            if conn_status and conn_status != const.STATUS_CONNECTED:
                logger.warning(f"[VendorImport] Aborting — QBO not connected (status={conn_status})")
                _audit_action, _audit_context = "vendor_import_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                logger.warning(f"[VendorImport] Aborting — missing refreshToken or realmId")
                _audit_action, _audit_context = "vendor_import_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected (missing refreshToken/realmId)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                logger.warning(f"[VendorImport] Aborting — missing ClientId or ClientSecret")
                _audit_action, _audit_context = "vendor_import_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            logger.info(f"[VendorImport] Refreshing OAuth token...")
            access_token = self._refresh_access_token(client_id, client_secret, refresh_token)
            logger.info(f"[VendorImport] OAuth token refreshed OK")
            base_url = self._get_qbo_api_base(env)
            logger.info(f"[VendorImport] QBO base_url={base_url}")
            changed_since, watermark_source = self._resolve_changed_since(
                tenant_id=tenant_id,
                service_id=service_id,
                changed_since_override=changed_since_override,
                force_full=force_full,
            )
            use_cdc = changed_since is not None
            if use_cdc and self._is_cdc_too_old(changed_since):
                logger.warning(f"[VendorImport] CDC watermark too old ({changed_since}) — falling back to FULL mode")
                use_cdc = False
                watermark_source = None
                changed_since = None
            summary["mode"] = "CDC" if use_cdc else "FULL"
            summary["changedSince"] = changed_since.isoformat() if changed_since else None
            summary["watermarkSource"] = watermark_source
            logger.info(f"[VendorImport] Import mode={summary['mode']} changedSince={summary['changedSince']} watermarkSource={summary['watermarkSource']}")
            log_cdc_debug(logger,
                "vendor import mode",
                tenantId=tenant_id,
                serviceId=service_id,
                mode=summary["mode"],
                changedSince=summary["changedSince"],
                watermarkSource=watermark_source,
                changedSinceOverride=changed_since_override,
                forceFull=force_full,
            )

            pending_import_failed_ids = self.vendor_map_repo.get_open_import_failed_external_vendor_ids(tenant_id=tenant_id, service_id=service_id)
            logger.info(f"[VendorImport] Pending IMPORT_FAILED retry IDs: {pending_import_failed_ids}")
            if pending_import_failed_ids:
                summary["persistentRetry"] = self.import_vendors_by_ids(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    vendor_ids=pending_import_failed_ids,
                )

            if use_cdc:
                logger.info(f"[VendorImport] Fetching vendors via CDC | changed_since={changed_since}")
                qbo_vendors, intuit_tid = self._fetch_qbo_vendors_cdc(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    changed_since=changed_since,
                )
                if max_vendors is not None:
                    qbo_vendors = qbo_vendors[: int(max_vendors)]
                logger.info(f"[VendorImport] CDC fetched {len(qbo_vendors)} vendors | intuit_tid={intuit_tid}")
                log_cdc_debug(logger,
                    "vendor CDC result",
                    count=len(qbo_vendors),
                    vendor1099Count=sum(1 for vendor in qbo_vendors if vendor.get("Vendor1099") is True),
                    ids=[str(vendor.get(const.QBO_KEY_ID) or "").strip() for vendor in qbo_vendors],
                    vendor1099Ids=[str(vendor.get(const.QBO_KEY_ID) or "").strip() for vendor in qbo_vendors if vendor.get("Vendor1099") is True],
                    intuitTid=intuit_tid,
                )
                summary["intuitTid"] = intuit_tid
                for qbo_vendor in qbo_vendors:
                    summary["processed"] += 1
                    self._process_qbo_vendor(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        qbo_vendor=qbo_vendor,
                        dry_run=dry_run,
                    )
                _audit_action = "vendor_import_completed"
                _audit_status = "success"
                _audit_context = f"exported={summary.get('created in FIELDCRAFT', 0) + summary.get('updatedInFIELDCRAFT', 0)} failed={summary.get('failed', 0)}"
                return summary

            pos = max(1, int(start_position or 1))
            processed = 0
            while True:
                if max_vendors is not None and processed >= int(max_vendors):
                    break
                batch_size = int(page_size or 100)
                if max_vendors is not None:
                    batch_size = min(batch_size, int(max_vendors) - processed)
                query_result = self._query_qbo_vendors(
                    base_url=base_url,
                    realm_id=str(realm_id),
                    access_token=access_token,
                    start_position=pos,
                    max_results=batch_size,
                )
                if isinstance(query_result, tuple) and len(query_result) == 2:
                    qbo_vendors, intuit_tid = query_result
                else:
                    qbo_vendors, intuit_tid = (query_result or [], None)
                if not qbo_vendors:
                    logger.info(f"[VendorImport] FULL mode — no more vendors at position {pos}, stopping")
                    break
                logger.info(f"[VendorImport] FULL mode — fetched {len(qbo_vendors)} vendors at position {pos} | intuit_tid={intuit_tid}")
                log_cdc_debug(logger,
                    "vendor FULL page fetched",
                    startPosition=pos,
                    maxResults=batch_size,
                    count=len(qbo_vendors),
                    vendor1099Count=sum(1 for vendor in qbo_vendors if vendor.get("Vendor1099") is True),
                    ids=[str(vendor.get(const.QBO_KEY_ID) or "").strip() for vendor in qbo_vendors],
                    intuitTid=intuit_tid,
                )
                summary["intuitTid"] = intuit_tid
                for qbo_vendor in qbo_vendors:
                    processed += 1
                    summary["processed"] += 1
                    self._process_qbo_vendor(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        qbo_vendor=qbo_vendor,
                        dry_run=dry_run,
                    )
                pos += len(qbo_vendors)
                if len(qbo_vendors) < batch_size:
                    break
            logger.info(f"[VendorImport] Import complete | processed={summary.get('processed')} created={summary.get('created in FIELDCRAFT')} updated={summary.get('updatedInFIELDCRAFT')} linked={summary.get('linkedExistingInFieldCraft')} skippedAlreadyCurrent={summary.get('skippedAlreadyCurrent')} skippedLocalNewer={summary.get('skippedLocalNewer')} skippedDeleted={summary.get('skippedDeleted')} failed={summary.get('failed')}")
            _audit_action = "vendor_import_completed"
            _audit_status = "success"
            _audit_context = f"exported={summary.get('created in FIELDCRAFT', 0) + summary.get('updatedInFIELDCRAFT', 0)} failed={summary.get('failed', 0)}"
            return summary
        except Exception as e:
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected QuickBooks sync error."
            _audit_action = "vendor_import_failed"
            _audit_status = "failed"
            _audit_context = str(e)[:64]
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
            # [AUDIT] Fires on every exit path — early return, success, or exception.
            safe_audit_log(
                tenant_id=tenant_id,
                action=_audit_action,
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status=_audit_status,
                context=_audit_context,
                resource_type="quickbooks_vendor",
            )

    def import_vendors_by_ids(self, *, tenant_id: str, service_id: str, vendor_ids: List[str]) -> Dict[str, Any]:
        logger.info(f"[VendorImport] import_vendors_by_ids called | tenant_id={tenant_id} service_id={service_id} vendor_ids={vendor_ids}")
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "linkedExistingInFieldCraft": 0,
            "created in FIELDCRAFT": 0,
            "updatedInFIELDCRAFT": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedDeleted": 0,
            "skippedWorkerVendor": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            self._tenant_id = tenant_id
            self._service_id = service_id
            self._connection = connection
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            if not refresh_token or not realm_id:
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected (missing refreshToken/realmId)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self._refresh_access_token(client_id, client_secret, refresh_token)
            base_url = self._get_qbo_api_base(env)
            for vendor_id in vendor_ids or []:
                logger.info(f"[VendorImport] import_vendors_by_ids — fetching qbo_vendor_id={vendor_id}")
                qbo_vendor = self._fetch_qbo_vendor_by_id(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    vendor_id=str(vendor_id),
                )
                summary["processed"] += 1
                self._process_qbo_vendor(
                    summary=summary,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=str(realm_id),
                    qbo_vendor=qbo_vendor,
                    dry_run=False,
                )
            return summary
        except Exception:
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected QuickBooks sync error."
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()

    def _process_qbo_vendor(
        self,
        *,
        summary: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_vendor: Dict[str, Any],
        dry_run: bool,
    ) -> None:
        qbo_vendor_id = str(qbo_vendor.get(const.QBO_KEY_ID) or "").strip()
        display_name = (qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME) or "").strip()
        logger.info(f"[VendorImport] _process_qbo_vendor | qbo_vendor_id={qbo_vendor_id} display_name={display_name!r}")
        detail: Dict[str, Any] = {
            const.KEY_QBO_VENDOR_ID: qbo_vendor_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_FIELDCRAFT_VENDOR_ID: None,
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }
        try:
            # 1099-tracked vendors are imported as FIELDCRAFT 1099 employees by the employee
            # sync — never as FIELDCRAFT vendors. Exclude them here so the same contractor
            # doesn't end up represented twice in FIELDCRAFT.
            if qbo_vendor.get("Vendor1099") is True:
                reason = self._build_reason(
                    "QuickBooks Vendor has 1099 tracking, so it is imported as a FIELDCRAFT 1099 employee by the employee sync, not as a FIELDCRAFT vendor.",
                    code="QBO_VENDOR_1099_HANDLED_BY_EMPLOYEE_SYNC",
                    qbo_vendor_id=qbo_vendor_id,
                )
                summary["skipped1099Vendor"] = int(summary.get("skipped1099Vendor") or 0) + 1
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "1099Vendor", const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                logger.info("[VendorImport] qbo_vendor_id=%s → SKIP (Vendor1099=true; handled by employee sync)", qbo_vendor_id)
                return

            worker_mapping = self.employee_repo.get_by_external_vendor_id(tenant_id, service_id, qbo_vendor_id)
            if worker_mapping:
                reason = self._build_reason(
                    "QuickBooks Vendor is already mapped to a FIELDCRAFT 1099/contractor worker, so vendor import skipped creating a duplicate FIELDCRAFT vendor.",
                    code="QBO_VENDOR_MAPPED_TO_FIELDCRAFT_WORKER",
                    qbo_vendor_id=qbo_vendor_id,
                    user_account_id=worker_mapping.get("user_account_id"),
                )
                summary["skippedWorkerVendor"] = int(summary.get("skippedWorkerVendor") or 0) + 1
                detail.update({
                    const.KEY_STATUS: "skipped",
                    const.KEY_ACTION: "workerVendor",
                    const.KEY_FIELDCRAFT_EMPLOYEE_ID: worker_mapping.get("user_account_id"),
                    const.KEY_QBO_WORKER_ENTITY_TYPE: const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
                    const.KEY_REASON: reason,
                })
                summary[const.KEY_RESULTS].append(detail)
                logger.info(
                    "[VendorImport] qbo_vendor_id=%s → SKIP (mapped to FIELDCRAFT worker user_account_id=%s)",
                    qbo_vendor_id,
                    worker_mapping.get("user_account_id"),
                )
                return

            current_mapping = self.vendor_map_repo.get_by_external_id(tenant_id, service_id, qbo_vendor_id)
            current_mapping_is_import_failed = bool(current_mapping and current_mapping.get("sync_status") == self.vendor_repo.STATUS_IMPORT_FAILED)
            if self._is_deleted_qbo_vendor(qbo_vendor):
                if current_mapping_is_import_failed and current_mapping.get("id"):
                    self.vendor_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
                logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → SKIP (deleted in QBO)")
                summary["skippedDeleted"] += 1
                skip_reason = "This vendor was deleted in QuickBooks and has been skipped."
                if current_mapping_is_import_failed:
                    skip_reason = "This vendor was deleted in QuickBooks. A previous import error has been resolved."
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "deletedInQbo", const.KEY_REASON: self._build_reason(skip_reason)})
                summary[const.KEY_RESULTS].append(detail)
                return

            payload = self.vendor_repo.build_vendor_payload_from_qbo(qbo_vendor)
            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_vendor)
            logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} | current_mapping={'found vendor_id=' + str(current_mapping.get('vendor_id')) + ' status=' + str(current_mapping.get('sync_status')) if current_mapping else 'none'} import_failed={current_mapping_is_import_failed}")
            if current_mapping and not current_mapping_is_import_failed:
                current_vendor = self.vendor_repo.get_current_vendor_by_id(tenant_id, current_mapping["vendor_id"]) if current_mapping.get("vendor_id") else None
                # Use only the vendor row's timestamps (data-change time), NOT the
                # mapping's updated_at. The mapping is updated by sync operations
                # (mark_mapping_synced), which inflates its timestamp and causes
                # the conflict check to incorrectly treat QBO as older.
                local_latest_at = self.vendor_repo.get_vendor_latest_local_timestamp(current_vendor, None)
                if current_vendor and not self.vendor_repo.sync_projection_changed(current_vendor, payload):
                    logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → SKIP (alreadyCurrent, no data change)")
                    self.vendor_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=current_mapping["id"],
                        external_vendor_id=qbo_vendor_id,
                        external_account_id=realm_id,
                        sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                        reason=self._build_reason("No changes detected — this vendor is already up to date."),
                    )
                    summary["skippedAlreadyCurrent"] += 1
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "alreadyCurrent", const.KEY_FIELDCRAFT_VENDOR_ID: current_mapping.get("vendor_id"), const.KEY_REASON: self._build_reason("No changes detected — this vendor is already up to date.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                    logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → SKIP (localNewer | qbo_updated={qbo_last_updated_at} local_latest={local_latest_at})")
                    summary["skippedLocalNewer"] += 1
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_FIELDCRAFT_VENDOR_ID: current_mapping.get("vendor_id"), const.KEY_REASON: self._build_reason("Your record is more recent than QuickBooks. No changes were imported to protect your latest edits.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if dry_run:
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_VENDOR_ID: current_mapping.get("vendor_id"), const.KEY_REASON: self._build_reason("Preview only — no changes have been saved.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → UPDATE existing FIELDCRAFT vendor_id={current_mapping['vendor_id']}")
                updated_vendor = self.vendor_repo.update_vendor_from_qbo(tenant_id=tenant_id, vendor_id=current_mapping["vendor_id"], qbo_vendor=qbo_vendor)
                self.vendor_map_repo.rotate_mapping_for_vendor(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    vendor_id=updated_vendor["id"],
                    external_vendor_id=qbo_vendor_id,
                    external_account_id=realm_id,
                    sync_status=self.vendor_repo.STATUS_SYNCED,
                    sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    reason=self._build_reason("Vendor details updated to match the latest changes from QuickBooks."),
                )
                logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → updated in FIELDCRAFT | new_vendor_id={updated_vendor['id']}")
                summary["updatedInFIELDCRAFT"] += 1
                detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "updated in FIELDCRAFT", const.KEY_FIELDCRAFT_VENDOR_ID: updated_vendor["id"], const.KEY_REASON: self._build_reason("Vendor details updated to match the latest changes from QuickBooks.")})
                summary[const.KEY_RESULTS].append(detail)
                return

            if current_mapping_is_import_failed and current_mapping.get("vendor_id"):
                tracked_vendor = self.vendor_repo.get_current_vendor_by_id(tenant_id, current_mapping["vendor_id"])
                if dry_run:
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_VENDOR_ID: current_mapping.get("vendor_id"), const.KEY_REASON: self._build_reason("Preview only — no changes have been saved (retry of a previous import error).")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if tracked_vendor and not self.vendor_repo.sync_projection_changed(tracked_vendor, payload):
                    self.vendor_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=current_mapping["id"],
                        external_vendor_id=qbo_vendor_id,
                        external_account_id=realm_id,
                        sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                        reason=self._build_reason("Previous import error resolved. Vendor is already up to date — no changes."),
                    )
                    detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "resolved import failure", const.KEY_FIELDCRAFT_VENDOR_ID: current_mapping.get("vendor_id"), const.KEY_REASON: self._build_reason("A previous import error was resolved successfully.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if tracked_vendor:
                    tracked_vendor = self.vendor_repo.update_vendor_from_qbo(tenant_id=tenant_id, vendor_id=current_mapping["vendor_id"], qbo_vendor=qbo_vendor)
                target_vendor_id = tracked_vendor["id"] if tracked_vendor else current_mapping["vendor_id"]
                self.vendor_map_repo.rotate_mapping_for_vendor(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    vendor_id=target_vendor_id,
                    external_vendor_id=qbo_vendor_id,
                    external_account_id=realm_id,
                    sync_status=self.vendor_repo.STATUS_SYNCED,
                    sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    reason=self._build_reason("A previous import error was resolved and the vendor has been updated."),
                )
                self.vendor_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
                summary["updatedInFIELDCRAFT"] += 1
                detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "resolved import failure", const.KEY_FIELDCRAFT_VENDOR_ID: target_vendor_id, const.KEY_REASON: self._build_reason("A previous import error was resolved and the vendor has been updated.")})
                summary[const.KEY_RESULTS].append(detail)
                return

            existing_vendor_id = self.vendor_repo.get_vendor_id_by_email(tenant_id=tenant_id, email=(payload.get(const.KEY_EMAIL) or ""))
            logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} | email lookup for {payload.get(const.KEY_EMAIL)!r} → existing_vendor_id={existing_vendor_id}")
            if existing_vendor_id:
                if dry_run:
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_VENDOR_ID: existing_vendor_id, const.KEY_REASON: self._build_reason("Preview only — no changes have been saved.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                current_vendor = self.vendor_repo.get_current_vendor_by_id(tenant_id, existing_vendor_id)
                local_latest_at = self.vendor_repo.get_vendor_latest_local_timestamp(current_vendor, None)
                has_change = bool(current_vendor and self.vendor_repo.sync_projection_changed(current_vendor, payload))
                if has_change and self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                    sync_status = self.vendor_repo.STATUS_PENDING_PUSH
                    reason = "Matched to an existing vendor. Your local version is newer and will be pushed to QuickBooks."
                    action = "linked existing pending push"
                    target_vendor_id = existing_vendor_id
                    summary["skippedLocalNewer"] += 1
                else:
                    sync_status = self.vendor_repo.STATUS_SYNCED
                    reason = "Matched and linked to an existing vendor."
                    action = "linked existing in FIELDCRAFT"
                    if has_change:
                        updated_vendor = self.vendor_repo.update_vendor_from_qbo(tenant_id=tenant_id, vendor_id=existing_vendor_id, qbo_vendor=qbo_vendor)
                        target_vendor_id = updated_vendor["id"]
                    else:
                        target_vendor_id = existing_vendor_id
                self.vendor_map_repo.rotate_mapping_for_vendor(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    vendor_id=target_vendor_id,
                    external_vendor_id=qbo_vendor_id,
                    external_account_id=realm_id,
                    sync_status=sync_status,
                    sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    reason=self._build_reason(
                        reason,
                        qbo_vendor_id=qbo_vendor_id,
                        display_name=display_name or None,
                        email=payload.get(const.KEY_EMAIL),
                        qbo_email=payload.get(const.KEY_EMAIL),
                    ),
                )
                if current_mapping_is_import_failed and current_mapping:
                    self.vendor_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
                logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → {action} | fieldcraft_vendor_id={target_vendor_id} sync_status={sync_status}")
                summary["linkedExistingInFieldCraft"] += 1
                detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: action, const.KEY_FIELDCRAFT_VENDOR_ID: target_vendor_id, const.KEY_REASON: self._build_reason(reason)})
                summary[const.KEY_RESULTS].append(detail)
                return

            if dry_run:
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_REASON: self._build_reason("Preview only — no changes have been saved.")})
                summary[const.KEY_RESULTS].append(detail)
                return

            missing_fields = self._get_missing_required_fields(payload)
            if missing_fields:
                _field_labels = {
                    "email": "email address",
                    "phone": "phone number",
                    "address": "street address",
                    "city": "city",
                    "state": "state",
                    "zip_code": "ZIP code",
                }
                friendly_fields = [_field_labels.get(f, f) for f in missing_fields]
                vendor_label = f'"{display_name}"' if display_name else f"with QuickBooks ID {qbo_vendor_id}"
                reason = (
                    f"Vendor {vendor_label} couldn't be imported — the following required fields are missing in QuickBooks: "
                    f"{', '.join(friendly_fields)}. Please update the vendor in QuickBooks and try again."
                )
                logger.warning(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → IMPORT_FAILED ({reason})")
                self._persist_import_failed_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_vendor_id=qbo_vendor_id,
                    sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    payload=payload,
                    current_mapping=locals().get("current_mapping"),
                    reason=self._build_reason(
                        reason,
                        qbo_vendor_id=qbo_vendor_id,
                        display_name=display_name or None,
                        email=payload.get(const.KEY_EMAIL),
                        qbo_email=payload.get(const.KEY_EMAIL),
                    ),
                )
                summary["failed"] += 1
                qbo_api.append_failure(summary, {
                    const.KEY_QBO_VENDOR_ID: qbo_vendor_id,
                    const.KEY_DISPLAY_NAME: display_name,
                    const.KEY_ERROR: reason,
                    const.KEY_DETAILS: reason,
                })
                detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "missingRequired", const.KEY_ERROR: reason, const.KEY_REASON: self._build_reason(reason)})
                summary[const.KEY_RESULTS].append(detail)
                return

            logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → no existing mapping or email match; creating new FIELDCRAFT vendor display_name={display_name!r}")
            vendor_id = self.vendor_repo.create_vendor_for_tenant(
                tenant_id=tenant_id,
                vendor_data={
                    **payload,
                    "vendor_uid": None,
                    "type": "otherVendors",
                    "categories": "[]",
                },
                created_by=const.SERVICE_NAME_QUICKBOOKS,
            )
            self.vendor_map_repo.rotate_mapping_for_vendor(
                tenant_id=tenant_id,
                service_id=service_id,
                vendor_id=vendor_id,
                external_vendor_id=qbo_vendor_id,
                external_account_id=realm_id,
                sync_status=self.vendor_repo.STATUS_SYNCED,
                sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                reason=self._build_reason("Vendor imported from QuickBooks."),
            )
            if current_mapping_is_import_failed and current_mapping:
                self.vendor_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
            logger.info(f"[VendorImport] qbo_vendor_id={qbo_vendor_id} → created in FIELDCRAFT | new_vendor_id={vendor_id}")
            summary["created in FIELDCRAFT"] += 1
            detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "created in FIELDCRAFT", const.KEY_FIELDCRAFT_VENDOR_ID: vendor_id, const.KEY_REASON: self._build_reason("Vendor imported from QuickBooks.")})
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            _intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
            logger.error(f"[VendorImport] ERROR processing qbo_vendor_id={qbo_vendor_id} display_name={display_name!r} | {exc}")
            self._persist_import_failed_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_vendor_id=qbo_vendor_id,
                sync_token=str(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                payload=locals().get("payload"),
                current_mapping=locals().get("current_mapping"),
                reason=self._build_reason(
                    exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc),
                    intuit_tid=_intuit_tid,
                    qbo_vendor_id=qbo_vendor_id,
                    display_name=display_name or None,
                    email=(locals().get("payload") or {}).get(const.KEY_EMAIL),
                    qbo_email=(locals().get("payload") or {}).get(const.KEY_EMAIL),
                ),
            )
            summary["failed"] += 1
            failure = {
                const.KEY_QBO_VENDOR_ID: qbo_vendor_id,
                const.KEY_DISPLAY_NAME: display_name,
                const.KEY_ERROR: str(exc),
                            }
            qbo_api.append_failure(summary, failure)
            detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: str(exc), const.KEY_REASON: self._build_reason("This vendor couldn't be imported and has been queued for automatic retry.", intuit_tid=_intuit_tid)})
            summary[const.KEY_RESULTS].append(detail)

    def _persist_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_vendor_id: str,
        sync_token: Optional[str],
        payload: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            target_vendor_id = None
            if current_mapping and current_mapping.get("vendor_id"):
                target_vendor_id = current_mapping.get("vendor_id")
            elif payload:
                email_lookup = (payload.get(const.KEY_EMAIL) or "").strip()
                if email_lookup:
                    target_vendor_id = self.vendor_repo.get_vendor_id_by_email(tenant_id=tenant_id, email=email_lookup)
            return self.vendor_map_repo.upsert_import_failed_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                external_vendor_id=qbo_vendor_id,
                external_account_id=realm_id,
                vendor_id=target_vendor_id,
                sync_token=sync_token,
                reason=reason or self._build_reason(
                    "This vendor couldn't be imported. It will be retried automatically on the next sync.",
                    qbo_vendor_id=qbo_vendor_id,
                    display_name=(payload or {}).get("display_name") or None,
                    email=(payload or {}).get(const.KEY_EMAIL),
                    qbo_email=(payload or {}).get(const.KEY_EMAIL),
                ),
            )
        except Exception:
            return None

    def _get_missing_required_fields(self, payload: Dict[str, Any]) -> List[str]:
        missing = []
        if not (payload.get(const.KEY_EMAIL) or "").strip():
            missing.append("email")
        if not (payload.get(const.KEY_PHONE) or "").strip():
            missing.append("phone")
        if not (payload.get("address") or "").strip():
            missing.append("address")
        if not (payload.get("city") or "").strip():
            missing.append("city")
        if not (payload.get("state") or "").strip():
            missing.append("state")
        if not (payload.get("zip_code") or "").strip():
            missing.append("zip_code")
        return missing

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

    def _get_qbo_last_updated_at(self, qbo_vendor: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_vendor.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

    def _is_deleted_qbo_vendor(self, qbo_vendor: Dict[str, Any]) -> bool:
        status = str(qbo_vendor.get("status") or qbo_vendor.get("Status") or "").strip().lower()
        if status == "deleted":
            return True

        deleted_suffix = "(deleted)"
        name_fields = [
            qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME),
            qbo_vendor.get("CompanyName"),
            qbo_vendor.get("PrintOnCheckName"),
        ]
        for value in name_fields:
            normalized = str(value or "").strip().lower()
            if normalized.endswith(deleted_suffix):
                return True
        return False

    def _should_skip_import_update(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at is None or local_latest_at is None:
            return False
        return qbo_last_updated_at <= local_latest_at

    def _is_cdc_too_old(self, changed_since: datetime) -> bool:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        return changed_since < (datetime.now(timezone.utc) - timedelta(days=29))

    def _normalize_changed_since(self, changed_since: Optional[datetime]) -> Optional[datetime]:
        if changed_since is None:
            return None
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        # Small overlap to avoid missing boundary updates. Vendor import is mapping-first and idempotent.
        return changed_since - timedelta(minutes=1)

    def _get_vendor_module_changed_since(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        try:
            last_sync_start = self.scheduled_job_repo.get_last_successful_module_sync_start(
                tenant_id=tenant_id,
                job_type="QBO_VENDOR_SYNC",
                service_id=str(service_id),
            )
            log_cdc_debug(logger,
                "vendor scheduled-job watermark lookup",
                tenantId=tenant_id,
                serviceId=service_id,
                rawValue=last_sync_start,
            )
            return self._normalize_changed_since(last_sync_start)
        except Exception as exc:
            log_cdc_debug(logger,
                "vendor scheduled-job watermark lookup failed",
                tenantId=tenant_id,
                serviceId=service_id,
                error=str(exc),
            )
            return None

    def _get_vendor_manual_changed_since(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        try:
            additional_info = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            raw_last_vendor_sync_at = additional_info.get("lastVendorSyncAt")
            log_cdc_debug(logger,
                "vendor manual watermark lookup",
                tenantId=tenant_id,
                serviceId=service_id,
                rawValue=raw_last_vendor_sync_at,
            )
            return self._normalize_changed_since(
                self._parse_timestamp(raw_last_vendor_sync_at)
            )
        except Exception as exc:
            log_cdc_debug(logger,
                "vendor manual watermark lookup failed",
                tenantId=tenant_id,
                serviceId=service_id,
                error=str(exc),
            )
            return None

    def _resolve_changed_since(self, tenant_id: str, service_id: str, changed_since_override: Optional[Any] = None, force_full: bool = False) -> Tuple[Optional[datetime], Optional[str]]:
        if force_full:
            return None, "forceFull"

        if changed_since_override is not None:
            parsed_override = self._parse_timestamp(changed_since_override)
            if parsed_override is not None:
                return self._normalize_changed_since(parsed_override), "override"

        module_changed_since = self._get_vendor_module_changed_since(tenant_id=tenant_id, service_id=service_id)
        if module_changed_since is not None:
            return module_changed_since, "scheduledJob"

        manual_changed_since = self._get_vendor_manual_changed_since(tenant_id=tenant_id, service_id=service_id)
        if manual_changed_since is not None:
            return manual_changed_since, "lastVendorSyncAt"

        return None, None

    def _fetch_qbo_vendors_cdc(self, *, access_token: str, base_url: str, realm_id: str, changed_since: datetime) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "text/plain",
        }
        params = {"entities": "Vendor", "changedSince": changed_since_value}
        log_cdc_debug(logger,
            "QBO Vendor CDC request",
            url=url,
            params=params,
        )
        resp = qbo_err.qbo_get(url, headers=headers, params=params, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO vendor CDC failed")
        data = qbo_err.safe_json(resp)
        vendors_by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for vendor in query_response.get("Vendor") or []:
                    vendor_id = str(vendor.get(const.QBO_KEY_ID) or "").strip()
                    if vendor_id:
                        vendors_by_id[vendor_id] = vendor
        vendors = list(vendors_by_id.values())
        log_cdc_debug(logger,
            "QBO Vendor CDC parsed",
            count=len(vendors),
            vendor1099Count=sum(1 for vendor in vendors if vendor.get("Vendor1099") is True),
            ids=list(vendors_by_id.keys()),
            vendor1099Ids=[str(vendor.get(const.QBO_KEY_ID) or "").strip() for vendor in vendors if vendor.get("Vendor1099") is True],
            intuitTid=qbo_api.extract_intuit_tid(resp),
        )
        return vendors, qbo_api.extract_intuit_tid(resp)

    def _get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _refresh_access_token(self, client_id: str, client_secret: str, refresh_token: str) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(self._tenant_id),
            service_id=str(self._service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=getattr(self, "_connection", None),
        )

    def _query_qbo_vendors(
        self,
        *,
        base_url: str,
        realm_id: str,
        access_token: str,
        start_position: int,
        max_results: int,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Vendor STARTPOSITION {int(start_position)} MAXRESULTS {int(max_results)}"
        resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO vendor query failed")
        data = qbo_err.safe_json(resp)
        vendors = (data.get("QueryResponse") or {}).get("Vendor") or []
        return (vendors if isinstance(vendors, list) else [], qbo_api.extract_intuit_tid(resp))

    def _fetch_qbo_vendor_by_id(self, *, access_token: str, base_url: str, realm_id: str, vendor_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/vendor/{vendor_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_err.qbo_get(url, headers=headers, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get vendor failed")
        data = qbo_err.safe_json(resp)
        return data.get("Vendor") or {}


    def _qbo_display_name(self, vendor: Dict[str, Any]) -> str:
        return self.vendor_repo.build_vendor_payload_from_qbo(vendor).get("display_name") or f"Vendor {vendor.get(const.QBO_KEY_ID)}"

    def _qbo_email(self, vendor: Dict[str, Any]) -> str:
        return self.vendor_repo.build_vendor_payload_from_qbo(vendor).get(const.KEY_EMAIL) or ""

    def _qbo_phone(self, vendor: Dict[str, Any]) -> str:
        return self.vendor_repo.build_vendor_payload_from_qbo(vendor).get(const.KEY_PHONE) or ""

    def _qbo_website(self, vendor: Dict[str, Any]) -> str:
        return self.vendor_repo.build_vendor_payload_from_qbo(vendor).get("website") or ""

    def _qbo_bill_addr(self, vendor: Dict[str, Any]) -> Dict[str, str]:
        payload = self.vendor_repo.build_vendor_payload_from_qbo(vendor)
        return {
            "address": payload.get("address") or "",
            "city": payload.get("city") or "",
            "state": payload.get("state") or "",
            "zip_code": payload.get("zip_code") or "",
            "country": payload.get("country") or "",
        }

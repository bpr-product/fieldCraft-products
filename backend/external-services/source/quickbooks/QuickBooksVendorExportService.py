from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.dal.VendorRepository import VendorRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)


class QuickBooksVendorExportService:
    """Delta-oriented vendor export using the new vendor domain design."""

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        return json.dumps(data)

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.vendor_repo = VendorRepository()
        self.vendor_map_repo = VendorExternalMapRepository()

    def export_vendors(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int = 500,
        offset: int = 0,
        include_mapping_ids: Optional[List[int]] = None,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
        use_mapping_change_timestamp: bool = False,
    ) -> Dict[str, Any]:
        logger.info(f"[VendorExport] export_vendors called | tenant_id={tenant_id} service_id={service_id} limit={limit} offset={offset}")
        safe_audit_log(
            tenant_id=tenant_id,
            action="vendor_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_vendor",
        )
        # Track final outcome so the finally block logs it regardless of which exit path is taken.
        _audit_action = "vendor_export_failed"
        _audit_status = "failed"
        _audit_context: Optional[str] = None
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            self._tenant_id = tenant_id
            self._service_id = service_id
            self._connection = connection

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            logger.info(f"[VendorExport] Connection info | realmId_present={bool(realm_id)} env={env} status={conn_status} has_refresh_token={bool(refresh_token)}")
            if conn_status and conn_status != const.STATUS_CONNECTED:
                logger.warning(f"[VendorExport] Aborting - QBO not connected (status={conn_status})")
                _audit_action, _audit_context = "vendor_export_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                logger.warning("[VendorExport] Aborting - missing realmId or refreshToken")
                _audit_action, _audit_context = "vendor_export_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                logger.warning("[VendorExport] Aborting - missing ClientId or ClientSecret")
                _audit_action, _audit_context = "vendor_export_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            logger.info("[VendorExport] Refreshing OAuth token...")
            access_token = self.refresh_access_token(client_id, client_secret, refresh_token)
            logger.info("[VendorExport] OAuth token refreshed OK")
            base_url = self.get_qbo_api_base(env)
            logger.info(f"[VendorExport] QBO base_url={base_url}")

            candidates = self.vendor_repo.get_export_candidates(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
                include_mapping_ids=include_mapping_ids,
            )
            logger.info(f"[VendorExport] get_export_candidates returned {len(candidates) if isinstance(candidates, list) else 'non-list'} items")
            if not isinstance(candidates, list):
                candidates = []

            summary: Dict[str, Any] = {
                const.KEY_OK: True,
                "tenantId": tenant_id,
                "serviceId": str(service_id),
                "realmIdPresent": bool(realm_id),
                "env": env,
                "requested": len(candidates),
                "skippedAlreadyMapped": 0,
                "skippedAlreadySynced": 0,
                "skippedNotEligible": 0,
                "skippedQboNewer": 0,
                "mapped from QBO": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "failed": 0,
                const.KEY_FAILURES: [],
                const.KEY_RESULTS: [],
            }

            if not candidates:
                logger.info("[VendorExport] No pending vendor export candidates found")
                summary[const.KEY_MESSAGE] = "No pending vendor export candidates found."
                _audit_action, _audit_status, _audit_context = "vendor_export_completed", "success", "no_candidates"
                return summary

            logger.info(f"[VendorExport] Starting export loop - {len(candidates)} candidates")
            for item in candidates:
                mapping = item.get("mapping") or {}
                mapping_id = int(mapping.get("id") or 0)
                if not mapping_id:
                    self._process_export_candidate(
                        item=item,
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        use_mapping_change_timestamp=use_mapping_change_timestamp,
                    )
                    continue

                with self.vendor_repo.export_mapping_lock(tenant_id, service_id, mapping_id):
                    locked_item = self.vendor_repo.get_export_candidate_by_mapping_id(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        mapping_id=mapping_id,
                    )
                    if locked_item is None:
                        latest_mapping = self.vendor_repo.get_mapping_by_id(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            mapping_id=mapping_id,
                        ) or {}
                        latest_status = self._clean(latest_mapping.get("sync_status")).upper()
                        latest_external_id = self._clean(latest_mapping.get("external_vendor_id"))
                        if latest_status == self.vendor_repo.STATUS_SYNCED and latest_external_id:
                            summary["skippedAlreadyMapped"] += 1
                            summary["skippedAlreadySynced"] += 1
                        else:
                            summary["skippedNotEligible"] += 1
                        continue
                    # Unit tests may use a mocked repository method. In production this
                    # method returns either a candidate dict or None.
                    if isinstance(locked_item, dict):
                        item = locked_item

                    self._process_export_candidate(
                        item=item,
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        use_mapping_change_timestamp=use_mapping_change_timestamp,
                    )

            logger.info(
                f"[VendorExport] Export complete | requested={summary.get('requested')} skippedAlreadyMapped={summary.get('skippedAlreadyMapped')} "
                f"mappedFromQbo={summary.get('mapped from QBO')} createdInQbo={summary.get('createdInQbo')} "
                f"updatedInQbo={summary.get('updatedInQbo')} failed={summary.get('failed')}"
            )
            _audit_action = "vendor_export_completed"
            _audit_status = "success"
            _audit_context = f"exported={summary.get('createdInQbo', 0) + summary.get('updatedInQbo', 0)} failed={summary.get('failed', 0)}"
            return summary
        except Exception as e:
            _audit_action = "vendor_export_failed"
            _audit_status = "failed"
            _audit_context = str(e)[:64]
            return {const.KEY_OK: False, const.KEY_MESSAGE: "Vendor export failed.", const.KEY_ERROR: "Unexpected internal error. See server logs for details."}
        finally:
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

    def _process_export_candidate(
        self,
        *,
        item: Dict[str, Any],
        summary: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        access_token: str,
        base_url: str,
        realm_id: str,
        use_mapping_change_timestamp: bool = False,
    ) -> None:
        vendor = item["vendor"]
        mapping = item["mapping"] or {}
        vendor_id = int(vendor["id"])
        display_name = self.resolve_vendor_create_display_name(vendor)
        logger.info(
            f"[VendorExport] Processing vendor_id={vendor_id} display_name={display_name!r} "
            f"sync_status={mapping.get('sync_status')} external_vendor_id={mapping.get('external_vendor_id')}"
        )
        detail = {
            const.KEY_FIELDCRAFT_VENDOR_ID: vendor_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_EMAIL: vendor.get(const.KEY_EMAIL),
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_QBO_VENDOR_ID: mapping.get("external_vendor_id") or None,
            const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }
        try:
            mapping_id = mapping.get("id")
            if not mapping_id:
                raise Exception("Missing vendor external map row for export candidate")
            if self._is_blank(display_name):
                raise Exception("Vendor display name is missing. Please add a display name to this vendor before exporting to QuickBooks.")

            if mapping.get("external_id") and not mapping.get("external_vendor_id"):
                mapping["external_vendor_id"] = mapping.get("external_id")

            external_vendor_id = self._clean(mapping.get("external_vendor_id"))
            current_status = self._clean(mapping.get("sync_status")).upper()
            if external_vendor_id and current_status not in (self.vendor_repo.STATUS_PENDING_PUSH, self.vendor_repo.STATUS_FAILED, ""):
                logger.info(f"[VendorExport] vendor_id={vendor_id} -> SKIP (already mapped, status={current_status})")
                summary["skippedAlreadyMapped"] += 1
                return

            stale_mapping_detected = False
            if external_vendor_id:
                logger.info(f"[VendorExport] vendor_id={vendor_id} -> UPDATE in QBO (external_vendor_id={external_vendor_id})")
                try:
                    # Conflict detection: fetch current QBO state to check for
                    # independent QBO changes using SyncToken as the indicator.
                    stored_sync_token = self._clean(mapping.get("sync_token")) or None
                    qbo_current = self.get_vendor_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        vendor_id=external_vendor_id,
                    )
                    qbo_current_sync_token = self._clean(qbo_current.get(const.QBO_KEY_SYNC_TOKEN)) or None

                    # Determine if QBO was independently updated since the last sync.
                    # Primary signal: SyncToken — QBO increments it on every write.
                    # Fallback when stored token is null: pure timestamp comparison.
                    qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_current)
                    if use_mapping_change_timestamp and current_status == self.vendor_repo.STATUS_PENDING_PUSH:
                        # A fresh PENDING_PUSH mapping represents the current FIELDCRAFT edit.
                        # Vendor.updated_at can lag behind that mapping, which otherwise
                        # makes the first real-time update after a QBO write look stale.
                        fieldcraft_updated_at = self._parse_timestamp(
                            self.vendor_repo.get_vendor_latest_local_timestamp(vendor, mapping)
                        )
                    else:
                        # Keep manual sync and FAILED retry conflict behavior unchanged.
                        fieldcraft_updated_at = self._parse_timestamp(vendor.get("updated_at"))

                    if stored_sync_token and qbo_current_sync_token:
                        qbo_changed_independently = stored_sync_token != qbo_current_sync_token
                        conflict_basis = f"syncToken({stored_sync_token}→{qbo_current_sync_token})"
                    else:
                        # No stored token — cannot use SyncToken comparison.
                        # QBO itself always requires a SyncToken for updates (we pass
                        # qbo_current_sync_token below), but we still need to decide
                        # which side wins. Fall back to timestamp comparison.
                        qbo_changed_independently = bool(
                            qbo_last_updated_at and fieldcraft_updated_at and qbo_last_updated_at > fieldcraft_updated_at
                        )
                        conflict_basis = f"timestamp-fallback(no stored token)"

                    if qbo_changed_independently:
                        logger.info(
                            f"[VendorExport] vendor_id={vendor_id} -> conflict detected via {conflict_basis} "
                            f"| fieldcraft_updated={fieldcraft_updated_at} qbo_updated={qbo_last_updated_at}"
                        )
                        if qbo_last_updated_at and fieldcraft_updated_at and qbo_last_updated_at > fieldcraft_updated_at:
                            # QBO is newer — skip export; import phase in this same sync
                            # will apply the QBO change to FIELDCRAFT.
                            logger.info(
                                f"[VendorExport] vendor_id={vendor_id} -> SKIP export (QBO newer); "
                                f"import will apply QBO change to FIELDCRAFT"
                            )
                            self.vendor_map_repo.mark_mapping_synced(
                                tenant_id=tenant_id,
                                mapping_id=mapping_id,
                                external_vendor_id=external_vendor_id,
                                external_account_id=str(realm_id),
                                sync_token=qbo_current_sync_token,
                                reason=self._build_reason("Skipped export: QBO has a newer update; import will apply QBO change to FIELDCRAFT"),
                            )
                            summary["skippedQboNewer"] += 1
                            detail.update(
                                {
                                    const.KEY_STATUS: "skipped",
                                    const.KEY_ACTION: "skippedQboNewer",
                                    const.KEY_QBO_VENDOR_ID: external_vendor_id,
                                    const.KEY_SYNC_TOKEN: qbo_current_sync_token,
                                    const.KEY_REASON: self._build_reason(f"QBO is newer (qbo={qbo_last_updated_at}, fieldcraft={fieldcraft_updated_at}); import will apply QBO change to FIELDCRAFT"),
                                }
                            )
                            summary[const.KEY_RESULTS].append(detail)
                            return
                        # FIELDCRAFT is newer — fall through to export below.
                        logger.info(
                            f"[VendorExport] vendor_id={vendor_id} -> FIELDCRAFT is newer despite QBO change; proceeding with export"
                        )

                    updated = self.update_vendor_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        vendor_id=external_vendor_id,
                        payload=self.build_qbo_vendor_payload(vendor, for_create=False),
                        sync_token=qbo_current_sync_token or stored_sync_token,
                    )
                    updated_id = self._clean((updated or {}).get(const.QBO_KEY_ID)) or external_vendor_id
                    updated_token = self._clean((updated or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
                    self.vendor_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=mapping_id,
                        external_vendor_id=updated_id,
                        external_account_id=str(realm_id),
                        sync_token=updated_token,
                        reason=self._build_reason("Vendor version exported to QuickBooks"),
                    )
                    logger.info(f"[VendorExport] vendor_id={vendor_id} -> updated in QBO | qbo_vendor_id={updated_id} sync_token={updated_token}")
                    summary["updatedInQbo"] += 1
                    detail.update(
                        {
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "updated in QBO",
                            const.KEY_QBO_VENDOR_ID: updated_id,
                            const.KEY_SYNC_TOKEN: updated_token,
                            const.KEY_REASON: self._build_reason("Current vendor version exported to QuickBooks"),
                        }
                    )
                    summary[const.KEY_RESULTS].append(detail)
                    return
                except Exception as exc:
                    if self._is_qbo_stale_reference_error(str(exc)):
                        logger.info(
                            f"[VendorExport] vendor_id={vendor_id} -> stale mapped QBO vendor_id={external_vendor_id}; switching to remap/create path"
                        )
                        stale_mapping_detected = True
                        external_vendor_id = ""
                    else:
                        raise

            logger.info(f"[VendorExport] vendor_id={vendor_id} -> checking QBO for an existing vendor DisplayName")
            qbo_candidates = self.find_vendor_candidates_in_qbo(
                access_token=access_token,
                base_url=base_url,
                realm_id=str(realm_id),
                vendor=vendor,
            )
            matched_vendor, _match_score = self.choose_best_matching_vendor(
                vendor=vendor, candidates=qbo_candidates
            )
            logger.info(
                f"[VendorExport] vendor_id={vendor_id} -> qbo_candidates={len(qbo_candidates)} "
                f"matched_vendor_id={self._clean((matched_vendor or {}).get(const.QBO_KEY_ID)) or 'none'}"
            )

            if matched_vendor and matched_vendor.get(const.QBO_KEY_ID):
                existing_qbo_vendor_id = self._clean(matched_vendor.get(const.QBO_KEY_ID))
                existing_display_name = self._clean(matched_vendor.get(const.QBO_KEY_DISPLAY_NAME)) or display_name
                existing_sync_token = self._clean(matched_vendor.get(const.QBO_KEY_SYNC_TOKEN)) or None
                existing_mapping = self.vendor_map_repo.get_by_external_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    external_id=existing_qbo_vendor_id,
                )
                existing_fieldcraft_vendor_id = existing_mapping.get(const.KEY_VENDOR_ID) if existing_mapping else None
                if existing_fieldcraft_vendor_id and int(existing_fieldcraft_vendor_id) != vendor_id:
                    raise Exception(self._vendor_display_name_mapped_elsewhere_message(existing_display_name))

                if existing_mapping and existing_mapping.get("id") and not existing_fieldcraft_vendor_id and int(existing_mapping.get("id")) != int(mapping_id):
                    self.vendor_map_repo.close_mapping_as_invalid(tenant_id, int(existing_mapping["id"]))

                link_reason = self._build_reason("Vendor already exists in QuickBooks and was linked to this FIELDCRAFT vendor.")
                self.vendor_map_repo.mark_mapping_synced(
                    tenant_id=tenant_id,
                    mapping_id=mapping_id,
                    external_vendor_id=existing_qbo_vendor_id,
                    external_account_id=str(realm_id),
                    sync_token=existing_sync_token,
                    reason=link_reason,
                )
                logger.info(
                    f"[VendorExport] vendor_id={vendor_id} -> linked existing QBO vendor | "
                    f"qbo_vendor_id={existing_qbo_vendor_id} sync_token={existing_sync_token}"
                )
                summary["mapped from QBO"] += 1
                detail.update(
                    {
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "mapped from QBO",
                        const.KEY_QBO_VENDOR_ID: existing_qbo_vendor_id,
                        const.KEY_SYNC_TOKEN: existing_sync_token,
                        const.KEY_REASON: link_reason,
                    }
                )
                summary[const.KEY_RESULTS].append(detail)
                return

            create_reason = "Vendor created in QuickBooks and mapping synced"
            logger.info(f"[VendorExport] vendor_id={vendor_id} -> creating in QBO | display_name={display_name!r}")
            create_payload = self.build_qbo_vendor_payload(vendor, for_create=True)
            try:
                created = self.create_vendor_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    payload=create_payload,
                )
            except Exception as exc:
                if self._is_qbo_duplicate_name_error(str(exc)):
                    raise Exception(self._vendor_display_name_exists_message(create_payload.get(const.QBO_KEY_DISPLAY_NAME) or display_name)) from exc
                raise

            created_id = self._clean((created or {}).get(const.QBO_KEY_ID))
            if not created_id:
                raise Exception(f"Create Vendor returned unexpected response: {created}")
            created_token = self._clean((created or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
            self.vendor_map_repo.mark_mapping_synced(
                tenant_id=tenant_id,
                mapping_id=mapping_id,
                external_vendor_id=created_id,
                external_account_id=str(realm_id),
                sync_token=created_token,
                reason=self._build_reason(create_reason),
            )
            logger.info(f"[VendorExport] vendor_id={vendor_id} -> created in QBO | qbo_vendor_id={created_id} sync_token={created_token}")
            summary["createdInQbo"] += 1
            detail.update(
                {
                    const.KEY_STATUS: "success",
                    const.KEY_ACTION: "created in QBO",
                    const.KEY_QBO_VENDOR_ID: created_id,
                    const.KEY_SYNC_TOKEN: created_token,
                    const.KEY_REASON: self._build_reason(create_reason),
                }
            )
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            _intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
            error_message = exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc)
            if self._is_qbo_duplicate_name_error(error_message):
                error_message = self._vendor_display_name_exists_message(display_name)
            logger.error(f"[VendorExport] ERROR processing vendor_id={vendor_id} | {error_message}")
            if mapping.get("id"):
                self.vendor_map_repo.mark_mapping_failed(
                    tenant_id=tenant_id,
                    mapping_id=mapping_id,
                    reason=self._build_reason(error_message, intuit_tid=_intuit_tid),
                )
            summary["failed"] += 1
            failure = {
                const.KEY_VENDOR_ID: vendor_id,
                "externalVendorId": mapping.get("external_vendor_id") or None,
                const.KEY_ERROR: error_message,
            }
            qbo_api.append_failure(summary, failure)
            detail.update(
                {
                    const.KEY_STATUS: "failed",
                    const.KEY_ACTION: "error",
                    const.KEY_ERROR: error_message,
                    const.KEY_REASON: self._build_reason(error_message, intuit_tid=_intuit_tid),
                }
            )
            summary[const.KEY_RESULTS].append(detail)


    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient

        return QBODiscoveryClient.get_qbo_api_base(env)

    def refresh_access_token(self, client_id: str, client_secret: str, refresh_token: str) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(self._tenant_id),
            service_id=str(self._service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=getattr(self, "_connection", None),
        )

    def get_vendor_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, vendor_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/vendor/{vendor_id}"
        resp = qbo_err.qbo_get(url, headers=headers, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get vendor failed")
        data = qbo_err.safe_json(resp)
        return data.get("Vendor") or {}

    def find_vendor_candidates_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, vendor: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Search QBO for an existing vendor by DisplayName.

        DisplayName is the mandatory unique field enforced by QBO, making it
        the only reliable identifier to match on. Company name is not unique,
        and PrimaryEmailAddr is not queryable in QBO IDS.
        """
        display_name = self.resolve_vendor_create_display_name(vendor)
        if not display_name:
            return []
        return self._query_vendors_by_field(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            field_name=const.QBO_KEY_DISPLAY_NAME,
            field_value=display_name,
        )

    def choose_best_matching_vendor(
        self,
        *,
        vendor: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> tuple[Optional[Dict[str, Any]], int]:
        """Return one unambiguous QBO vendor match based on the unique DisplayName."""
        expected_name = self._clean(self.resolve_vendor_create_display_name(vendor)).casefold()
        if not expected_name:
            return None, 0
        exact_matches = [
            candidate
            for candidate in candidates or []
            if self._clean(candidate.get(const.QBO_KEY_DISPLAY_NAME)).casefold() == expected_name
        ]
        if len(exact_matches) == 1:
            return exact_matches[0], 100
        return None, 0

    def _query_vendors_by_field(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        field_name: str,
        field_value: str,
    ) -> List[Dict[str, Any]]:
        value = self._clean(field_value)
        if not value:
            return []
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Vendor where {field_name} = '{self._escape_qbo_literal(value)}'"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QuickBooks vendor query failed")
        data = qbo_err.safe_json(resp)
        vendors = (data.get("QueryResponse") or {}).get("Vendor") or []
        return vendors if isinstance(vendors, list) else []

    def create_vendor_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/vendor"
        body = dict(payload or {})
        web_addr = body.get("WebAddr") or {}
        uri = (web_addr.get("URI") or "").strip() if isinstance(web_addr, dict) else ""
        if uri:
            normalized = self.normalize_website_url(uri)
            if normalized:
                body["WebAddr"] = {"URI": normalized}
            else:
                body.pop("WebAddr", None)
        resp = qbo_err.qbo_post(url, headers=headers, json=body, timeout=30)
        if resp.status_code not in (200, 201):
            if body.get("WebAddr"):
                retry_body = dict(body)
                retry_body.pop("WebAddr", None)
                resp_retry = qbo_err.qbo_post(url, headers=headers, json=retry_body, timeout=30)
                if resp_retry.status_code in (200, 201):
                    return qbo_err.safe_json(resp_retry).get("Vendor") or {}
            qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO create vendor failed")
        data = qbo_err.safe_json(resp)
        return data.get("Vendor") or {}

    def update_vendor_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        vendor_id: str,
        payload: Dict[str, Any],
        sync_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        token_value = self._clean(sync_token) or None
        if token_value:
            try:
                return self._post_vendor_update(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    vendor_id=vendor_id,
                    sync_token=token_value,
                    payload=payload,
                )
            except Exception as exc:
                if self._is_qbo_stale_reference_error(str(exc)):
                    raise
                if not self._is_qbo_sync_token_error(str(exc)):
                    raise
                logger.info(
                    f"[VendorExport] update_vendor_in_qbo | vendor_id={vendor_id} stored sync token rejected; refetching current SyncToken"
                )

        existing = self.get_vendor_in_qbo(access_token=access_token, base_url=base_url, realm_id=realm_id, vendor_id=vendor_id)
        current_sync_token = self._clean(existing.get(const.QBO_KEY_SYNC_TOKEN))
        if not current_sync_token:
            raise Exception("QBO vendor update failed: missing SyncToken")
        return self._post_vendor_update(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            vendor_id=vendor_id,
            sync_token=current_sync_token,
            payload=payload,
        )

    def _post_vendor_update(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        vendor_id: str,
        sync_token: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/vendor"
        body = {const.QBO_KEY_ID: str(vendor_id), const.QBO_KEY_SYNC_TOKEN: str(sync_token), "sparse": True, **(payload or {})}
        resp = qbo_err.qbo_post(url, headers=headers, json=body, timeout=30)
        qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO update vendor failed")
        data = qbo_err.safe_json(resp)
        return data.get("Vendor") or {}

    def get_vendor_display_name(self, vendor: Dict[str, Any]) -> str:
        return self._clean(vendor.get("display_name"))

    def resolve_vendor_create_display_name(self, vendor: Dict[str, Any]) -> str:
        return self.get_vendor_display_name(vendor) or self.build_display_name(vendor)

    def resolve_vendor_update_display_name(self, vendor: Dict[str, Any]) -> str:
        return self.get_vendor_display_name(vendor)

    def build_display_name(self, vendor: Dict[str, Any]) -> str:
        base_name = vendor.get("company_name") or vendor.get("check_name") or vendor.get("display_name") or "Vendor"
        return qbo_api.build_qbo_display_name(
            base_name,
            const.QBO_ENTITY_TYPE_VENDOR,
            vendor.get("vendor_uid"),
        )

    def build_unique_display_name(self, vendor: Dict[str, Any]) -> str:
        return self.build_display_name(vendor)

    def build_qbo_vendor_payload(
        self,
        vendor: Dict[str, Any],
        display_name_override: Optional[str] = None,
        *,
        for_create: bool = True,
    ) -> Dict[str, Any]:
        display_name = self._clean(display_name_override)
        if not display_name:
            display_name = self.resolve_vendor_create_display_name(vendor) if for_create else self.resolve_vendor_update_display_name(vendor)

        payload: Dict[str, Any] = {}
        if display_name:
            payload[const.QBO_KEY_DISPLAY_NAME] = self._truncate_display_name(display_name)
        elif for_create:
            raise Exception("Vendor display name is missing. Please add a display name to this vendor before exporting to QuickBooks.")
        company_name = self._clean(vendor.get("company_name"))
        check_name = self._clean(vendor.get("check_name"))
        email = self._clean(vendor.get(const.KEY_EMAIL))
        phone = self._clean(vendor.get(const.KEY_PHONE) or vendor.get("primary_mobile_number"))
        address = self._clean(vendor.get("address"))
        city = self._clean(vendor.get("city"))
        state = self._clean(vendor.get("state"))
        zip_code = self._clean(vendor.get("zip_code"))
        country = self._clean(vendor.get("country"))
        website = self._clean(vendor.get("website"))
        description = self._clean(vendor.get("description"))
        account_no = self._clean(vendor.get("account_no"))
        if company_name:
            payload["CompanyName"] = company_name
        if check_name:
            payload["PrintOnCheckName"] = check_name
        if email:
            payload["PrimaryEmailAddr"] = {"Address": email}
        if phone:
            payload["PrimaryPhone"] = {"FreeFormNumber": phone}
        bill_addr: Dict[str, str] = {}
        if address:
            bill_addr["Line1"] = address
        if city:
            bill_addr["City"] = city
        if state:
            bill_addr["CountrySubDivisionCode"] = state
        if zip_code:
            bill_addr["PostalCode"] = zip_code
        if country:
            bill_addr["Country"] = country
        if bill_addr:
            payload["BillAddr"] = bill_addr
        if website:
            normalized = self.normalize_website_url(website)
            if normalized:
                payload["WebAddr"] = {"URI": normalized}
        if description:
            payload["Notes"] = description
        if account_no:
            payload["AcctNum"] = account_no
        return payload

    def normalize_website_url(self, value: str) -> Optional[str]:
        raw = self._clean(value)
        if not raw:
            return None
        candidate = raw if re.match(r"^https?://", raw, flags=re.IGNORECASE) else f"https://{raw}"
        try:
            parsed = urlparse(candidate)
            if not parsed.netloc:
                return None
            return candidate
        except Exception:
            return None

    def _vendor_display_name_mapped_elsewhere_message(self, display_name: Any) -> str:
        name = self._clean(display_name) or "this display name"
        return (
            f'QuickBooks already has a vendor named "{name}", and that QuickBooks vendor is already linked to another FIELDCRAFT vendor. '
            "Use a different display name or review the existing vendor mapping before syncing."
        )

    def _vendor_display_name_exists_message(self, display_name: Any) -> str:
        name = self._clean(display_name)
        if name:
            return (
                f'QuickBooks already has a vendor with the display name "{name}". '
                "Please enter a unique vendor display name in FIELDCRAFT, then run vendor sync again."
            )
        return "QuickBooks already has a vendor with this display name. Please enter a unique vendor display name in FIELDCRAFT, then run vendor sync again."

    def _truncate_display_name(self, value: str, max_length: int = 100) -> str:
        text = self._clean(value)
        if len(text) <= max_length:
            return text
        return text[:max_length].rstrip()

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _same_value(self, left: Any, right: Any) -> bool:
        left_value = self._clean(left).lower()
        right_value = self._clean(right).lower()
        return bool(left_value and right_value and left_value == right_value)

    def _same_email(self, left: Any, right: Any) -> bool:
        return self._same_value(left, right)

    def _normalize_phone(self, value: Any) -> str:
        return re.sub(r"\D+", "", self._clean(value))

    def _same_phone(self, left: Any, right: Any) -> bool:
        left_value = self._normalize_phone(left)
        right_value = self._normalize_phone(right)
        return bool(left_value and right_value and left_value == right_value)

    def _is_qbo_stale_reference_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "[610]" in text or "object not found" in text or "made inactive" in text

    def _is_qbo_sync_token_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return (
            "synctoken" in text
            or "stale object" in text
            or "[5010]" in text
            or ("required field missing" in text and "synctoken" in text)
        )

    def _is_qbo_duplicate_name_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "duplicate name" in text or "[6240]" in text or ("display name" in text and "exists" in text)

    def _escape_qbo_literal(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        from datetime import timezone
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

    def _is_blank(self, value: Optional[str]) -> bool:
        return not self._clean(value)

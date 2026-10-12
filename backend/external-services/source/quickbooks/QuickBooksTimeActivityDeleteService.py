from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.TimelogRepository import TimelogRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)


class QuickBooksTimeActivityDeleteService:
    """Delete FIELDCRAFT-mapped QuickBooks TimeActivity records and requeue their mappings.

    The service deliberately deletes only QuickBooks TimeActivity IDs currently
    stored in ``timelog_external_map`` for the requested tenant and service. It
    does not query and delete unrelated or manually-created QuickBooks time
    entries.
    """

    MINOR_VERSION = "75"

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.timelog_repo = TimelogRepository()
        self.oauth_client = QuickBooksOAuthClient(repository=self.qbo_repo)

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _fault_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        errors = ((payload.get("Fault") or {}).get("Error") or []) if isinstance(payload, dict) else []
        if isinstance(errors, dict):
            return [errors]
        return [row for row in errors if isinstance(row, dict)] if isinstance(errors, list) else []

    @classmethod
    def _is_not_found_response(cls, response: Any) -> bool:
        if response is None:
            return False
        if int(getattr(response, "status_code", 0) or 0) == 404:
            return True

        payload = qbo_api.safe_json(response)
        for row in cls._fault_rows(payload):
            code = cls._clean(row.get("code") or row.get("Code"))
            message = " ".join(
                part
                for part in (
                    cls._clean(row.get("Message")),
                    cls._clean(row.get("Detail")),
                )
                if part
            ).lower()
            if code == "610" or "object not found" in message or "not found" in message:
                return True
        return False

    def _context(self, tenant_id: str, service_id: str) -> Dict[str, str]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not service_id:
            raise ValueError("service_id is required")

        connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
        config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}

        realm_id = self._clean(connection.get(const.KEY_REALM_ID))
        refresh_token = self._clean(connection.get(const.KEY_REFRESH_TOKEN))
        env = self._clean(connection.get(const.KEY_ENV) or config.get("Env") or "sandbox").lower()
        client_id = self._clean(config.get(const.KEY_CLIENT_ID))
        client_secret = self._clean(config.get(const.KEY_CLIENT_SECRET))

        missing = []
        if not realm_id:
            missing.append("realmId")
        if not refresh_token:
            missing.append("refreshToken")
        if not client_id:
            missing.append("ClientId")
        if not client_secret:
            missing.append("ClientSecret")
        if missing:
            raise ValueError("QuickBooks connection/config is missing: " + ", ".join(missing))

        access_token = self.oauth_client.refresh_access_token(
            tenant_id=tenant_id,
            service_id=service_id,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
            debug_context={"operation": "delete_mapped_time_activities"},
        )

        from source.Utils import QBODiscoveryClient

        return {
            "tenantId": tenant_id,
            "serviceId": service_id,
            "realmId": realm_id,
            "env": env,
            "baseUrl": QBODiscoveryClient.get_qbo_api_base(env),
            "accessToken": access_token,
        }

    @staticmethod
    def _headers(access_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _read_time_activity(
        self,
        *,
        base_url: str,
        realm_id: str,
        access_token: str,
        time_activity_id: str,
    ) -> Any:
        url = f"{base_url}/v3/company/{realm_id}/timeactivity/{time_activity_id}"
        logger.debug("[TimeActivityDelete] READ request")
        response = qbo_api.qbo_get(
            url,
            headers=self._headers(access_token),
            params={"minorversion": self.MINOR_VERSION},
            timeout=30,
        )
        logger.info(
            "[TimeActivityDelete] READ response | status=%s intuit_tid_present=%s",
            response.status_code,
            bool(qbo_api.extract_intuit_tid(response)),
        )
        return response

    def _delete_time_activity(
        self,
        *,
        base_url: str,
        realm_id: str,
        access_token: str,
        time_activity_id: str,
        sync_token: str,
    ) -> Any:
        url = f"{base_url}/v3/company/{realm_id}/timeactivity"
        payload = {
            "Id": time_activity_id,
            "SyncToken": sync_token,
        }
        logger.debug("[TimeActivityDelete] DELETE request")
        response = qbo_api.qbo_post(
            url,
            headers=self._headers(access_token),
            params={"operation": "delete", "minorversion": self.MINOR_VERSION},
            json=payload,
            timeout=30,
        )
        logger.info(
            "[TimeActivityDelete] DELETE response | status=%s intuit_tid_present=%s",
            response.status_code,
            bool(qbo_api.extract_intuit_tid(response)),
        )
        return response

    def delete_all_mapped_time_activities(
        self,
        *,
        tenant_id: str,
        service_id: str,
        confirm_delete: bool,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if not confirm_delete and not dry_run:
            raise ValueError("confirm_delete must be true to delete QuickBooks TimeActivity records")

        mappings = self.timelog_repo.list_mapped_time_activities(
            tenant_id=tenant_id,
            service_id=service_id,
        )

        # One QBO TimeActivity ID can be referenced by more than one mapping row
        # after historical/manual fixes. Delete the QBO object once, then reset all
        # current mapping rows that point to it.
        by_external_id: Dict[str, List[Dict[str, Any]]] = {}
        for mapping in mappings:
            external_id = self._clean(mapping.get("external_time_activity_id"))
            if external_id:
                by_external_id.setdefault(external_id, []).append(mapping)

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": self._clean(tenant_id),
            "serviceId": self._clean(service_id),
            "dryRun": bool(dry_run),
            "scope": "FIELDCRAFT_MAPPED_TIME_ACTIVITIES_ONLY",
            "mappedRows": len(mappings),
            "uniqueTimeActivities": len(by_external_id),
            "deleted": 0,
            "alreadyMissing": 0,
            "failed": 0,
            "resetMappings": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
        }

        if dry_run:
            summary[const.KEY_RESULTS] = [
                {
                    "timeActivityId": external_id,
                    "mappingIds": [int(row["id"]) for row in rows],
                    "action": "would_delete",
                }
                for external_id, rows in by_external_id.items()
            ]

        if dry_run:
            return summary

        if not by_external_id:
            summary[const.KEY_MESSAGE] = "No mapped QuickBooks Accounting TimeActivity records were found."
            return summary

        ctx = self._context(tenant_id, service_id)
        successfully_absent_ids: Set[str] = set()

        for external_id, rows in by_external_id.items():
            mapping_ids = [int(row["id"]) for row in rows]
            fallback_sync_token = next(
                (self._clean(row.get("sync_token")) for row in rows if self._clean(row.get("sync_token"))),
                "",
            )
            detail: Dict[str, Any] = {
                "timeActivityId": external_id,
                "mappingIds": mapping_ids,
            }

            try:
                read_response = self._read_time_activity(
                    base_url=ctx["baseUrl"],
                    realm_id=ctx["realmId"],
                    access_token=ctx["accessToken"],
                    time_activity_id=external_id,
                )

                if self._is_not_found_response(read_response):
                    successfully_absent_ids.add(external_id)
                    summary["alreadyMissing"] += 1
                    detail["action"] = "already_missing"
                    detail["intuitTid"] = qbo_api.extract_intuit_tid(read_response)
                    summary[const.KEY_RESULTS].append(detail)
                    continue

                if read_response.status_code != 200:
                    raise RuntimeError(
                        qbo_api.build_response_error(
                            read_response,
                            f"Unable to read QuickBooks TimeActivity {external_id}",
                        )
                    )

                saved = qbo_api.safe_json(read_response).get("TimeActivity") or {}
                sync_token = self._clean(saved.get("SyncToken")) or fallback_sync_token
                if not sync_token:
                    raise RuntimeError(
                        f"QuickBooks TimeActivity {external_id} did not return a SyncToken"
                    )

                delete_response = self._delete_time_activity(
                    base_url=ctx["baseUrl"],
                    realm_id=ctx["realmId"],
                    access_token=ctx["accessToken"],
                    time_activity_id=external_id,
                    sync_token=sync_token,
                )

                if delete_response.status_code == 200 or self._is_not_found_response(delete_response):
                    successfully_absent_ids.add(external_id)
                    if delete_response.status_code == 200:
                        summary["deleted"] += 1
                        detail["action"] = "deleted"
                    else:
                        summary["alreadyMissing"] += 1
                        detail["action"] = "already_missing"
                    detail["intuitTid"] = qbo_api.extract_intuit_tid(delete_response)
                    summary[const.KEY_RESULTS].append(detail)
                    continue

                raise RuntimeError(
                    qbo_api.build_response_error(
                        delete_response,
                        f"Unable to delete QuickBooks TimeActivity {external_id}",
                    )
                )
            except Exception as exc:
                logger.exception(
                    "[TimeActivityDelete] failed | time_activity_id=%s mapping_ids=%s",
                    external_id,
                    mapping_ids,
                )
                summary["failed"] += 1
                summary[const.KEY_FAILURES].append(
                    {
                        **detail,
                        "action": "delete_failed",
                        "error": str(exc),
                    }
                )

        if successfully_absent_ids:
            reset_count = self.timelog_repo.reset_mapped_time_activities_for_reexport(
                tenant_id=tenant_id,
                service_id=service_id,
                external_time_activity_ids=sorted(successfully_absent_ids),
                reason=(
                    '{"message":"QuickBooks TimeActivity was deleted and this timelog was queued '
                    'for export again.","code":"TIME_ACTIVITY_DELETED_FOR_RETEST"}'
                ),
            )
            summary["resetMappings"] += int(reset_count)

        summary[const.KEY_OK] = summary["failed"] == 0
        if summary["failed"]:
            summary[const.KEY_MESSAGE] = (
                "Some QuickBooks TimeActivity records could not be deleted. "
                "Only successfully deleted or already-missing mappings were reset."
            )
        else:
            summary[const.KEY_MESSAGE] = (
                "Mapped QuickBooks TimeActivity records were deleted and their FIELDCRAFT mappings "
                "were reset to PENDING_PUSH."
            )
        return summary

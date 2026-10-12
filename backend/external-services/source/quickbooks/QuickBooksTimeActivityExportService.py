from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from source.Utils import constants as const
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.dal.ProjectRepository import ProjectRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.TimelogRepository import TimelogRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.QuickBooksCapabilityService import QuickBooksCapabilityService
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)


class QuickBooksTimeActivityExportService:
    """Export approved FIELDCRAFT timelogs to QuickBooks Online TimeActivity.

    W-2 workers are exported with EmployeeRef and 1099 workers with VendorRef.
    This service uses only the normal QuickBooks Online Accounting OAuth token.
    """

    EXTERNAL_ENTITY_TYPE = "TIME_ACTIVITY"
    TARGET = "QUICKBOOKS_ACCOUNTING_TIME_ACTIVITY"
    MINOR_VERSION = 75

    def __init__(
        self,
        *,
        qbo_repo: Optional[QuickBooksRepository] = None,
        timelog_repo: Optional[TimelogRepository] = None,
        project_repo: Optional[ProjectRepository] = None,
        qbo_service: Optional[QuickBooksService] = None,
        capability_service: Optional[QuickBooksCapabilityService] = None,
    ) -> None:
        self.qbo_repo = qbo_repo or QuickBooksRepository()
        self.timelog_repo = timelog_repo or TimelogRepository()
        self.project_repo = project_repo or ProjectRepository()
        self.qbo_service = qbo_service or QuickBooksService()
        self.capability_service = capability_service or QuickBooksCapabilityService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _build_reason(cls, message: str, **extra: Any) -> str:
        payload: Dict[str, Any] = {
            "message": message,
            "externalEntityType": cls.EXTERNAL_ENTITY_TYPE,
            "target": cls.TARGET,
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        return json.dumps(payload, default=str, separators=(",", ":"))

    @classmethod
    def worker_entity_type(cls, candidate: Dict[str, Any]) -> str:
        resource_type = " ".join(cls._clean(candidate.get("resource_type")).split()).casefold()
        contractor_types = {
            " ".join(cls._clean(value).split()).casefold()
            for value in const.CONTRACTOR_RESOURCE_TYPES
        }
        if resource_type in contractor_types:
            return const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
        if resource_type == const.EMPLOYEE_TYPE_W2.casefold():
            return const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE

        explicit = cls._clean(candidate.get("external_entity_type")).upper()
        if explicit in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
            return explicit
        if cls._clean(candidate.get("qbo_vendor_id")) and not cls._clean(
            candidate.get("qbo_employee_id")
        ):
            return const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
        return const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE

    @classmethod
    def is_contractor_candidate(cls, candidate: Dict[str, Any]) -> bool:
        return cls.worker_entity_type(candidate) == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR

    @classmethod
    def worker_type_label(cls, candidate: Dict[str, Any]) -> str:
        return "1099" if cls.is_contractor_candidate(candidate) else "W2"

    @staticmethod
    def _duration_minutes(candidate: Dict[str, Any]) -> Optional[int]:
        start_time = candidate.get("start_time")
        end_time = candidate.get("end_time")
        if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
            return None
        if end_time <= start_time:
            return None
        seconds = int((end_time - start_time).total_seconds())
        return max(1, seconds // 60) if seconds > 0 else None

    @staticmethod
    def _txn_date(value: Any) -> Optional[str]:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            try:
                return date.fromisoformat(raw[:10]).isoformat()
            except ValueError:
                return None

    def _load_employee_sync_config(self, tenant_id: Optional[str], service_id: Optional[str]) -> Dict[str, Any]:
        configured = getattr(self, "_employee_sync_config_override", None)
        if isinstance(configured, dict):
            return configured
        raw: Dict[str, Any] = {}
        getter = getattr(self.qbo_repo, "getEmployeeSyncConfig", None)
        if callable(getter) and tenant_id and service_id:
            raw = getter(tenant_id, service_id) or {}
        return raw

    @staticmethod
    def _hourly_cost(candidate: Dict[str, Any], config: Dict[str, Any]) -> Optional[float]:
        raw_cost = candidate.get("resource_cost")
        if raw_cost in (None, ""):
            return None
        try:
            cost = Decimal(str(raw_cost))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if cost <= 0:
            return None

        unit = str(candidate.get("resource_cost_unit") or const.EMPLOYEE_COST_UNIT_HOURLY).strip().lower()
        hours_by_unit = dict(const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT)
        configured_hours = config.get(const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS)
        if isinstance(configured_hours, dict):
            hours_by_unit.update(configured_hours)
        try:
            hours = Decimal(str(hours_by_unit.get(unit, 1)))
        except (InvalidOperation, TypeError, ValueError):
            hours = Decimal("1")
        if hours <= 0:
            hours = Decimal("1")
        return float((cost / hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _description(
        self,
        candidate: Dict[str, Any],
        *,
        hourly_cost: Optional[float],
        include_internal_cost: bool,
    ) -> str:
        parts = [f"FIELDCRAFT Timelog ID: {int(candidate.get('timelog_id') or 0)}"]
        task_name = self._clean(candidate.get("task_name"))
        if task_name:
            parts.append(f"Task: {task_name}")
        role_name = self._clean(candidate.get("role_name"))
        if role_name:
            parts.append(f"Service: {role_name}")
        if include_internal_cost and hourly_cost is not None:
            parts.append(f"FIELDCRAFT internal labor cost: ${hourly_cost:.2f}/hour.")
        return " | ".join(parts)[:4000]

    def _project_sync_target(self, candidate: Dict[str, Any]) -> str:
        mapping = {
            const.KEY_PROJECT_SYNC_TARGET: candidate.get(const.KEY_PROJECT_SYNC_TARGET),
            "reason": candidate.get("project_mapping_reason"),
            "external_project_id": candidate.get("qbo_project_id"),
        }
        return (
            self.project_repo.get_mapping_sync_target(mapping)
            or const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
        )

    def validate_candidate(
        self,
        candidate: Dict[str, Any],
        *,
        tenant_id: str,
        service_id: str,
    ) -> Optional[str]:
        sync_status = self._clean(candidate.get("map_sync_status")).upper()
        mapped_external_id = self._clean(TimelogRepository.mapping_external_id(candidate))
        retryable_statuses = {
            TimelogRepository.STATUS_PENDING_PUSH,
            TimelogRepository.STATUS_SYNC_FAILED,
        }
        if sync_status not in retryable_statuses and not (
            sync_status == TimelogRepository.STATUS_SYNCED and not mapped_external_id
        ):
            return "Only pending or retryable timelog mappings can be exported."
        if self._clean(candidate.get("approval_status")).upper() != TimelogRepository.APPROVAL_STATUS_APPROVED:
            return "Only approved timelogs can be exported."
        if bool(candidate.get("timelog_invalid")):
            return "Invalid timelogs cannot be exported."
        if self._clean(candidate.get("timelog_status")).lower() != "completed":
            return "Only completed timelogs can be exported."
        if not self._duration_minutes(candidate):
            return "Timelog duration must be greater than zero."
        worker_type = self.worker_type_label(candidate)
        if self._clean(candidate.get("employee_sync_status")).upper() != TimelogRepository.STATUS_SYNCED:
            return f"{worker_type} worker must be synced before exporting time."
        if self.is_contractor_candidate(candidate):
            if not self._clean(candidate.get("qbo_vendor_id")):
                return "1099 contractor is not synced to QuickBooks Vendor."
        elif not self._clean(candidate.get("qbo_employee_id")):
            return "W2 employee is not synced to QuickBooks Employee."
        if self._clean(candidate.get("project_sync_status")).upper() != TimelogRepository.STATUS_SYNCED:
            return "Project must be synced before exporting time."
        if not self._clean(candidate.get("qbo_project_id")):
            return "QuickBooks project or sub-customer mapping is missing."
        if not self._clean(candidate.get("role_name")):
            return "Task assignee role is missing."
        if self._clean(candidate.get("service_sync_status")).upper() != TimelogRepository.STATUS_SYNCED:
            return "Service role must be synced before exporting time."
        if not self._clean(candidate.get("qbo_service_id")):
            return "QuickBooks service item mapping is missing."

        if self._project_sync_target(candidate) == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
            if not self._clean(candidate.get("qbo_parent_customer_id")):
                return "Parent customer must be synced before exporting project time."
            if not self.capability_service.projects_available(tenant_id, service_id, refresh=True):
                return "QuickBooks Projects are not enabled for this company."
        return None

    def build_payload(
        self,
        candidate: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        minutes = self._duration_minutes(candidate)
        txn_date = self._txn_date(candidate.get("start_time"))
        if not minutes or not txn_date:
            raise ValueError("Timelog duration and date are required.")

        override = getattr(self, "_employee_sync_config", None)
        config = override if isinstance(override, dict) else self._load_employee_sync_config(tenant_id, service_id)
        hourly_cost = self._hourly_cost(candidate, config)
        qbo_cost_field = self._clean(
            config.get(const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD)
            or const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
        )
        use_customer_bill_rate = (
            bool(config.get(const.KEY_EMPLOYEE_SYNC_COST_ENABLED, True))
            and qbo_cost_field == const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE
            and hourly_cost is not None
        )

        payload: Dict[str, Any] = {
            "TxnDate": txn_date,
            "ItemRef": {"value": self._clean(candidate.get("qbo_service_id"))},
            "Hours": minutes // 60,
            "Minutes": minutes % 60,
            "Description": self._description(
                candidate,
                hourly_cost=hourly_cost,
                include_internal_cost=not use_customer_bill_rate,
            ),
        }
        if self.is_contractor_candidate(candidate):
            payload.update(
                {
                    "NameOf": "Vendor",
                    "VendorRef": {"value": self._clean(candidate.get("qbo_vendor_id"))},
                }
            )
        else:
            payload.update(
                {
                    "NameOf": "Employee",
                    "EmployeeRef": {"value": self._clean(candidate.get("qbo_employee_id"))},
                }
            )

        project_target = self._project_sync_target(candidate)
        if project_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
            payload["CustomerRef"] = {
                "value": self._clean(candidate.get("qbo_parent_customer_id"))
            }
            payload["ProjectRef"] = {
                "value": self._clean(candidate.get("qbo_project_id"))
            }
        else:
            payload["CustomerRef"] = {
                "value": self._clean(candidate.get("qbo_project_id"))
            }

        if use_customer_bill_rate:
            payload["HourlyRate"] = hourly_cost
            payload["BillableStatus"] = "Billable"
        return payload

    def accounting_context(
        self,
        *,
        tenant_id: str,
        service_id: str,
        connection: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        connection = connection or self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
        refresh_token = self._clean(connection.get(const.KEY_REFRESH_TOKEN))
        realm_id = self._clean(connection.get(const.KEY_REALM_ID))
        env = self._clean(connection.get(const.KEY_ENV) or "sandbox").lower()
        if not refresh_token or not realm_id:
            raise ValueError("QuickBooks Accounting connection is missing realmId or refreshToken.")

        config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
        client_id = self._clean(config.get(const.KEY_CLIENT_ID))
        client_secret = self._clean(config.get(const.KEY_CLIENT_SECRET))
        if not client_id or not client_secret:
            raise ValueError("QuickBooks client credentials are missing.")

        access_token = QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=tenant_id,
            service_id=service_id,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
        )
        from source.Utils import QBODiscoveryClient

        return {
            "realm_id": realm_id,
            "base_url": QBODiscoveryClient.get_qbo_api_base(env),
            "access_token": access_token,
            "env": env,
        }

    @staticmethod
    def _headers(access_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _trace_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
        safe_headers: Dict[str, Any] = {}
        for key, value in headers.items():
            if str(key).lower() in {"authorization", "cookie", "set-cookie"}:
                safe_headers[str(key)] = "<redacted>"
            else:
                safe_headers[str(key)] = value
        return safe_headers

    @classmethod
    def _log_http_request(
        cls,
        *,
        operation: str,
        method: str,
        url: str,
        headers: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.info(
            "\n[QBO_ACCOUNTING_TIME_ACTIVITY] >>> REQUEST\n"
            "operation: %s\n"
            "method: %s\n"
            "url: %s\n"
            "headers: %s\n"
            "params: %s\n"
            "payload_raw: %s\n"
            "[QBO_ACCOUNTING_TIME_ACTIVITY] >>> END REQUEST",
            operation,
            method,
            url,
            json.dumps(cls._trace_headers(headers), indent=2, sort_keys=True, default=str),
            json.dumps(params or {}, indent=2, sort_keys=True, default=str),
            json.dumps(payload, indent=2, sort_keys=True, default=str) if payload is not None else "null",
        )

    @classmethod
    def _log_http_response(cls, *, operation: str, response: Any) -> None:
        response_headers = cls._trace_headers(dict(getattr(response, "headers", {}) or {}))
        try:
            response_body = response.text
        except Exception:
            response_body = "<unable to read response body>"
        logger.info(
            "\n[QBO_ACCOUNTING_TIME_ACTIVITY] <<< QUICKBOOKS RAW RESPONSE\n"
            "operation: %s\n"
            "http_status: %s\n"
            "http_reason: %s\n"
            "url: %s\n"
            "response_headers_exact: %s\n"
            "response_body_raw: %s\n"
            "[QBO_ACCOUNTING_TIME_ACTIVITY] <<< END QUICKBOOKS RAW RESPONSE",
            operation,
            getattr(response, "status_code", None),
            getattr(response, "reason", ""),
            getattr(response, "url", ""),
            json.dumps(response_headers, indent=2, sort_keys=True, default=str),
            response_body,
        )

    @classmethod
    def _endpoint(cls, base_url: str, realm_id: str) -> str:
        params: Dict[str, Any] = {"minorversion": cls.MINOR_VERSION}
        return f"{base_url}/v3/company/{realm_id}/timeactivity?{urlencode(params)}"

    @staticmethod
    def _use_time_entry_enabled(value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().casefold()
        if normalized in {"usetimeentry", "true", "1", "yes", "y"}:
            return True
        if normalized in {"donotusetimeentry", "notset", "false", "0", "no", "n"}:
            return False
        return None

    def get_employee_time_entry_setting(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        employee_id: str,
    ) -> Dict[str, Any]:
        """Read the employee's QuickBooks payroll time-entry setting.

        Intuit requires Employee.UseTimeEntry for TimeActivity data to flow to
        payroll. The value is diagnostic only here; existing export behavior is
        intentionally not blocked when the setting is disabled or unavailable.
        """
        employee_id = self._clean(employee_id)
        if not employee_id:
            raise ValueError("QuickBooks employee id is required to check UseTimeEntry.")

        cache = getattr(self, "_employee_time_entry_setting_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._employee_time_entry_setting_cache = cache
        cache_key = (self._clean(realm_id), employee_id)
        if cache_key in cache:
            return dict(cache[cache_key])

        url = (
            f"{base_url}/v3/company/{realm_id}/employee/{employee_id}"
            f"?minorversion={self.MINOR_VERSION}"
        )
        headers = self._headers(access_token)
        response = qbo_err.qbo_get(url, headers=headers, timeout=30)
        qbo_err.raise_for_qbo_response(
            response,
            fallback_message=f"Unable to read QuickBooks Employee {employee_id}",
        )
        employee = qbo_err.safe_json(response).get("Employee") or {}
        raw_value = employee.get("UseTimeEntry")
        enabled = self._use_time_entry_enabled(raw_value)
        result = {
            "employeeId": employee_id,
            "useTimeEntry": enabled,
            "useTimeEntryValue": raw_value,
        }
        cache[cache_key] = dict(result)
        logger.info(
            "[QBO_ACCOUNTING_TIME_ACTIVITY] Employee payroll-time setting | "
            "employee_id=%s use_time_entry=%s raw_value=%s",
            employee_id,
            enabled,
            raw_value,
        )
        return result

    def get_time_activity(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        time_activity_id: str,
    ) -> Optional[Dict[str, Any]]:
        url = (
            f"{base_url}/v3/company/{realm_id}/timeactivity/{time_activity_id}"
            f"?minorversion={self.MINOR_VERSION}"
        )
        headers = self._headers(access_token)
        self._log_http_request(
            operation="get TimeActivity",
            method="GET",
            url=url,
            headers=headers,
        )
        response = qbo_err.qbo_get(url, headers=headers, timeout=30)
        self._log_http_response(operation="get TimeActivity", response=response)
        if response.status_code == 404:
            return None
        qbo_err.raise_for_qbo_response(
            response,
            fallback_message=f"Unable to read QuickBooks TimeActivity {time_activity_id}",
        )
        return qbo_err.safe_json(response).get("TimeActivity") or {}

    def create_time_activity(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = self._endpoint(base_url, realm_id)
        headers = self._headers(access_token)
        self._log_http_request(
            operation="create TimeActivity",
            method="POST",
            url=url,
            headers=headers,
            payload=payload,
        )
        response = qbo_err.qbo_post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )
        self._log_http_response(operation="create TimeActivity", response=response)
        qbo_err.raise_for_qbo_response(
            response,
            success_statuses=(200, 201),
            fallback_message="QuickBooks TimeActivity was not exported.",
        )
        return qbo_err.safe_json(response).get("TimeActivity") or {}

    def update_time_activity(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        time_activity_id: str,
        sync_token: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        update_payload = dict(payload)
        update_payload.update(
            {
                "Id": str(time_activity_id),
                "SyncToken": str(sync_token),
                "sparse": True,
            }
        )
        url = self._endpoint(base_url, realm_id)
        headers = self._headers(access_token)
        self._log_http_request(
            operation="update TimeActivity",
            method="POST",
            url=url,
            headers=headers,
            payload=update_payload,
        )
        response = qbo_err.qbo_post(
            url,
            headers=headers,
            json=update_payload,
            timeout=30,
        )
        self._log_http_response(operation="update TimeActivity", response=response)
        qbo_err.raise_for_qbo_response(
            response,
            success_statuses=(200, 201),
            fallback_message=f"Unable to update QuickBooks TimeActivity {time_activity_id}",
        )
        return qbo_err.safe_json(response).get("TimeActivity") or {}

    def delete_time_activity(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        time_activity_id: str,
    ) -> bool:
        current = self.get_time_activity(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            time_activity_id=time_activity_id,
        )
        if not current:
            return False
        sync_token = self._clean(current.get(const.QBO_KEY_SYNC_TOKEN))
        if not sync_token:
            raise ValueError(
                f"QuickBooks TimeActivity {time_activity_id} did not return a SyncToken."
            )
        url = f"{base_url}/v3/company/{realm_id}/timeactivity"
        headers = self._headers(access_token)
        payload = {"Id": str(time_activity_id), "SyncToken": sync_token}
        params = {"operation": "delete", "minorversion": self.MINOR_VERSION}
        self._log_http_request(
            operation="delete TimeActivity",
            method="POST",
            url=url,
            headers=headers,
            params=params,
            payload=payload,
        )
        response = qbo_err.qbo_post(
            url,
            headers=headers,
            params=params,
            json=payload,
            timeout=30,
        )
        self._log_http_response(operation="delete TimeActivity", response=response)
        qbo_err.raise_for_qbo_response(
            response,
            success_statuses=(200, 201),
            fallback_message=f"Unable to delete QuickBooks TimeActivity {time_activity_id}",
        )
        return True

    def process_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        base_url: str,
        access_token: str,
        dry_run: bool,
        summary: Dict[str, Any],
    ) -> None:
        map_id = int(candidate.get("map_id") or 0)
        timelog_id = int(candidate.get("timelog_id") or 0)
        worker_type = self.worker_type_label(candidate)
        detail: Dict[str, Any] = {
            "fieldcraftTimelogId": timelog_id,
            "mapId": map_id,
            "workerType": worker_type,
            "destination": self.TARGET,
        }
        try:
            candidate = dict(candidate)
            entity_type = TimelogRepository.mapping_external_entity_type(candidate)
            mapped_external_id = self._clean(TimelogRepository.mapping_external_id(candidate))
            mapped_realm = self._clean(candidate.get("external_account_id"))
            if mapped_realm and mapped_realm != self._clean(realm_id):
                raise ValueError("This timelog mapping belongs to a different QuickBooks company.")

            validation_error = self.validate_candidate(
                candidate,
                tenant_id=tenant_id,
                service_id=service_id,
            )
            if validation_error:
                raise ValueError(validation_error)

            payload = self.build_payload(
                candidate,
                tenant_id=tenant_id,
                service_id=service_id,
            )
            logger.info(
                "[QBO_TIME_ACTIVITY_EXPORT] prepared payload | tenant_id=%s service_id=%s "
                "timelog_id=%s worker_type=%s payload=%s",
                tenant_id,
                service_id,
                timelog_id,
                worker_type,
                json.dumps(payload, sort_keys=True, default=str),
            )
            if dry_run:
                detail.update({"status": "dry_run", "payload": payload})
                summary["skipped"] += 1
                summary[const.KEY_RESULTS].append(detail)
                return

            if mapped_external_id and entity_type == "TIMESHEET":
                raise ValueError(
                    "This timelog is mapped to a legacy QuickBooks Time timesheet. "
                    "Reset that legacy mapping before exporting it as a TimeActivity."
                )

            external_id = (
                self._clean(candidate.get("external_time_activity_id"))
                if entity_type == self.EXTERNAL_ENTITY_TYPE
                else ""
            )
            action = "created"
            result: Dict[str, Any]
            if external_id:
                current = self.get_time_activity(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    time_activity_id=external_id,
                )
                if current:
                    sync_token = self._clean(current.get(const.QBO_KEY_SYNC_TOKEN))
                    if not sync_token:
                        raise ValueError(
                            f"QuickBooks TimeActivity {external_id} did not return a SyncToken."
                        )
                    result = self.update_time_activity(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        time_activity_id=external_id,
                        sync_token=sync_token,
                        payload=payload,
                    )
                    action = "updated"
                else:
                    external_id = ""

            if not external_id:
                result = self.create_time_activity(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    payload=payload,
                )
                action = "created"

            time_activity_id = self._clean(result.get(const.QBO_KEY_ID))
            sync_token = self._clean(result.get(const.QBO_KEY_SYNC_TOKEN)) or None
            if not time_activity_id:
                raise ValueError("QuickBooks TimeActivity response did not include an Id.")

            mapped_elsewhere = self.timelog_repo.get_current_mapping_by_external_id(
                tenant_id=tenant_id,
                service_id=service_id,
                external_time_activity_id=time_activity_id,
                external_account_id=realm_id,
            )
            if mapped_elsewhere and int(mapped_elsewhere.get("timelog_id") or 0) != timelog_id:
                raise ValueError(
                    f"QuickBooks TimeActivity {time_activity_id} is already mapped to another timelog."
                )

            self.timelog_repo.mark_synced(
                tenant_id=tenant_id,
                map_id=map_id,
                external_time_activity_id=time_activity_id,
                external_account_id=realm_id,
                sync_token=sync_token,
                external_entity_type=self.EXTERNAL_ENTITY_TYPE,
                reason=self._build_reason(
                    f"QuickBooks Online TimeActivity {action}.",
                    qboEmployeeId=self._clean(candidate.get("qbo_employee_id")) or None,
                    qboVendorId=self._clean(candidate.get("qbo_vendor_id")) or None,
                    qboProjectId=self._clean(candidate.get("qbo_project_id")),
                    qboServiceItemId=self._clean(candidate.get("qbo_service_id")),
                    workerType=worker_type,
                ),
            )
            key = "updated" if action == "updated" else "created"
            for summary_key in (
                f"{key}InQbo",
                f"{key}InAccounting",
                f"{key}TimeActivities",
            ):
                summary[summary_key] = int(summary.get(summary_key) or 0) + 1
            detail.update(
                {
                    "status": "success",
                    "action": action,
                    "quickBooksTimeActivityId": time_activity_id,
                    "externalEntityType": self.EXTERNAL_ENTITY_TYPE,
                }
            )
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            logger.exception(
                "[TimeActivityExport] candidate failed | tenant_id=%s service_id=%s timelog_id=%s map_id=%s",
                tenant_id,
                service_id,
                timelog_id,
                map_id,
            )
            if map_id:
                self.timelog_repo.mark_failed(
                    tenant_id=tenant_id,
                    map_id=map_id,
                    status=TimelogRepository.STATUS_SYNC_FAILED,
                    reason=self._build_reason(str(exc), code="QBO_TIME_ACTIVITY_EXPORT_FAILED"),
                )
            detail.update({"status": "failed", "error": str(exc)})
            summary["syncFailed"] += 1
            summary[const.KEY_FAILURES].append(detail)

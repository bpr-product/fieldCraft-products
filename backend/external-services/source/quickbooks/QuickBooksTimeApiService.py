"""QuickBooks Time REST API client for users, jobcodes, and timesheets."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.quickbooks import QuickBooksTimeApiTrace as raw_trace

logger = logging.getLogger(__name__)


class QuickBooksTimeApiError(RuntimeError):
    """Raised when QuickBooks Time rejects a request or returns invalid data."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_payload = response_payload

    @property
    def is_not_found(self) -> bool:
        if self.status_code == 404:
            return True
        text = str(self).lower()
        return "not found" in text or "does not exist" in text


class QuickBooksTimeApiService:
    DEFAULT_BASE_URL = "https://rest.tsheets.com/api/v1"
    MAX_PAGE_SIZE = 200
    CREATE_CONFIRMATION = "CREATE_QUICKBOOKS_TIME_TIMESHEET"
    APPROVAL_CONFIRMATION = "APPROVE_ALL_USER_TIME_THROUGH_DATE"

    def __init__(
        self,
        *,
        access_token: Optional[str] = None,
        base_url: Optional[str] = None,
        token_source: Optional[str] = None,
    ) -> None:
        self.access_token = self._clean(
            access_token if access_token is not None else os.getenv("QBO_TIME_API_ACCESS_TOKEN")
        )
        self.base_url = self._clean(
            base_url if base_url is not None else os.getenv("QBO_TIME_API_BASE_URL")
        ) or self.DEFAULT_BASE_URL
        self.base_url = self.base_url.rstrip("/")
        self.token_source = self._clean(token_source) or (
            "tenant_oauth" if access_token is not None else "QBO_TIME_API_ACCESS_TOKEN"
        )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _date_text(value: Any) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value or "").strip()
        if not text:
            raise ValueError("txn_date is required")
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError("txn_date must use YYYY-MM-DD format") from exc

    def configured(self) -> bool:
        return bool(self.access_token)

    def public_status(self) -> Dict[str, Any]:
        return {
            "configured": self.configured(),
            "baseUrl": self.base_url,
            "rawHttpTrace": raw_trace.is_enabled(),
            "tokenSource": self.token_source if self.configured() else None,
            "warning": (
                "QuickBooks Time uses its own API authorization. The QuickBooks Online "
                "Accounting access token is not substituted automatically."
            ),
        }

    def _require_token(self) -> None:
        if not self.access_token:
            raise QuickBooksTimeApiError(
                "QuickBooks Time API access token is missing. Set "
                "QBO_TIME_API_ACCESS_TOKEN for the connected QuickBooks Time company."
            )

    def _headers(self) -> Dict[str, str]:
        self._require_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        payload: Any = None,
        operation: str,
    ) -> Dict[str, Any]:
        method = method.upper()
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = self._headers()
        raw_trace.print_request(
            operation=operation,
            method=method,
            url=url,
            headers=headers,
            params=params,
            payload=payload,
        )
        kwargs: Dict[str, Any] = {
            "headers": headers,
            "params": dict(params or {}),
            "timeout": 30,
        }
        if payload is not None:
            kwargs["json"] = payload

        if method == "GET":
            response = qbo_api.qbo_get(url, **kwargs)
        elif method == "POST":
            response = qbo_api.qbo_post(url, **kwargs)
        elif method == "PUT":
            response = qbo_api.qbo_put(url, **kwargs)
        else:
            response = qbo_api.qbo_request(method, url, **kwargs)

        raw_trace.print_response(operation=operation, response=response)
        if response.status_code < 200 or response.status_code >= 300:
            raise QuickBooksTimeApiError(
                qbo_api.build_response_error(
                    response,
                    f"QuickBooks Time {operation} failed",
                ),
                status_code=response.status_code,
                response_payload=None,
            )

        payload_json = qbo_api.safe_json(response)
        if not payload_json and response.text.strip():
            raise QuickBooksTimeApiError(
                f"QuickBooks Time {operation} returned an invalid response."
            )
        return payload_json

    @classmethod
    def _entities(cls, payload: Mapping[str, Any], entity_name: str) -> List[Dict[str, Any]]:
        results = payload.get("results") if isinstance(payload, Mapping) else None
        container = results.get(entity_name) if isinstance(results, Mapping) else None
        if isinstance(container, list):
            return [dict(item) for item in container if isinstance(item, Mapping)]
        if isinstance(container, Mapping):
            entities: List[Dict[str, Any]] = []
            for key, item in container.items():
                if not isinstance(item, Mapping):
                    continue
                row = dict(item)
                if not row.get("id") and str(key).isdigit():
                    row["id"] = int(key)
                entities.append(row)
            return entities
        return []

    @staticmethod
    def _status_code(row: Mapping[str, Any]) -> Optional[int]:
        raw = row.get("_status_code")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _require_entity_success(
        cls,
        rows: Iterable[Mapping[str, Any]],
        *,
        operation: str,
    ) -> None:
        failures = []
        for row in rows:
            status_code = cls._status_code(row)
            if status_code is not None and not 200 <= status_code < 300:
                failures.append(
                    {
                        "id": row.get("id"),
                        "statusCode": status_code,
                        "message": row.get("_status_message"),
                        "extra": row.get("_status_extra"),
                    }
                )
        if failures:
            raise QuickBooksTimeApiError(
                f"QuickBooks Time {operation} returned object-level failures: {failures}"
            )

    def status(self) -> Dict[str, Any]:
        response = self._request(
            "GET",
            "/current_user",
            operation="current user",
        )
        current_users = self._entities(response, "current_user")
        if not current_users:
            # Some QuickBooks Time responses expose the user under `users`.
            current_users = self._entities(response, "users")
        return {
            "ok": True,
            **self.public_status(),
            "currentUser": current_users[0] if current_users else None,
            "rawResponse": response,
        }

    def _list_all(
        self,
        *,
        path: str,
        entity_name: str,
        params: Optional[Mapping[str, Any]],
        operation: str,
    ) -> Dict[str, Any]:
        page = 1
        all_rows: List[Dict[str, Any]] = []
        raw_pages: List[Dict[str, Any]] = []
        while True:
            page_params = dict(params or {})
            page_params.setdefault("limit", self.MAX_PAGE_SIZE)
            page_params["page"] = page
            response = self._request(
                "GET",
                path,
                params=page_params,
                operation=f"{operation} page {page}",
            )
            raw_pages.append(response)
            all_rows.extend(self._entities(response, entity_name))
            if not bool(response.get("more")):
                break
            page += 1
            if page > 1000:
                raise QuickBooksTimeApiError(
                    f"QuickBooks Time {operation} pagination exceeded the safe page limit."
                )
        return {
            "ok": True,
            "count": len(all_rows),
            entity_name: all_rows,
            "pages": len(raw_pages),
            "rawPages": raw_pages,
        }

    def users(self, *, active: str = "both") -> Dict[str, Any]:
        return self._list_all(
            path="/users",
            entity_name="users",
            params={"active": active},
            operation="list users",
        )

    def projects(self, *, active: str = "both") -> Dict[str, Any]:
        """Return QuickBooks Time projects and their time-tracking jobcode IDs."""
        return self._list_all(
            path="/projects",
            entity_name="projects",
            params={"active": active},
            operation="list projects",
        )

    def create_project(
        self,
        *,
        name: str,
        parent_jobcode_id: int,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create one QuickBooks Time project and return its generated jobcode ID."""
        project_name = self._clean(name)[:64]
        if not project_name:
            raise ValueError("QuickBooks Time project name is required")

        parent_id = int(parent_jobcode_id)
        if parent_id <= 0:
            raise ValueError("QuickBooks Time parent_jobcode_id must be greater than zero")

        row: Dict[str, Any] = {
            "name": project_name,
            "parent_jobcode_id": parent_id,
        }
        clean_description = self._clean(description)
        if clean_description:
            row["description"] = clean_description[:300]

        response = self._request(
            "POST",
            "/projects",
            payload={"data": [row]},
            operation="create project",
        )
        rows = self._entities(response, "projects")
        self._require_entity_success(rows, operation="create project")
        if not rows:
            raise QuickBooksTimeApiError(
                "QuickBooks Time create project response did not include a project."
            )

        created = rows[0]
        if not created.get("id"):
            raise QuickBooksTimeApiError(
                "QuickBooks Time create project response did not include a project ID."
            )
        if not created.get("jobcode_id"):
            raise QuickBooksTimeApiError(
                "QuickBooks Time create project response did not include a jobcode ID."
            )
        return {
            "project": created,
            "rawResponse": response,
        }

    def jobcodes(
        self,
        *,
        active: str = "both",
        jobcode_type: str = "all",
        parent_ids: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"active": active, "type": jobcode_type}
        if self._clean(parent_ids):
            params["parent_ids"] = self._clean(parent_ids)
        return self._list_all(
            path="/jobcodes",
            entity_name="jobcodes",
            params=params,
            operation="list jobcodes",
        )

    def customfields(self) -> Dict[str, Any]:
        return self._list_all(
            path="/customfields",
            entity_name="customfields",
            params={"active": "both"},
            operation="list custom fields",
        )

    def customfielditems(self, *, customfield_id: Optional[int] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"active": "both"}
        if customfield_id:
            params["customfield_id"] = int(customfield_id)
        return self._list_all(
            path="/customfielditems",
            entity_name="customfielditems",
            params=params,
            operation="list custom field items",
        )

    def customfielditem_jobcode_filters(
        self,
        *,
        jobcode_ids: Iterable[int],
    ) -> Dict[str, Any]:
        """Return custom-field choices allowed for the supplied jobcodes."""
        ids = [int(value) for value in jobcode_ids]
        if not ids:
            return {
                "ok": True,
                "count": 0,
                "customfielditem_jobcode_filters": [],
                "pages": 0,
                "rawPages": [],
            }
        return self._list_all(
            path="/customfielditem_jobcode_filters",
            entity_name="customfielditem_jobcode_filters",
            params={"jobcode_ids": ",".join(str(value) for value in ids)},
            operation="list custom field item jobcode filters",
        )

    def customfielditem_user_filters(
        self,
        *,
        user_id: int,
        include_user_group: bool = True,
    ) -> Dict[str, Any]:
        """Return custom-field choices allowed for a user and optionally its group."""
        return self._list_all(
            path="/customfielditem_user_filters",
            entity_name="customfielditem_user_filters",
            params={
                "user_id": int(user_id),
                "include_user_group": "true" if include_user_group else "false",
            },
            operation="list custom field item user filters",
        )

    def get_user(self, user_id: int) -> Dict[str, Any]:
        response = self._request(
            "GET",
            "/users",
            params={"ids": int(user_id)},
            operation=f"read user {int(user_id)}",
        )
        rows = self._entities(response, "users")
        if not rows:
            raise QuickBooksTimeApiError(
                f"QuickBooks Time user {int(user_id)} was not returned."
            )
        return rows[0]

    def get_timesheet(self, timesheet_id: int) -> Dict[str, Any]:
        response = self._request(
            "GET",
            "/timesheets",
            params={"ids": int(timesheet_id)},
            operation=f"read timesheet {int(timesheet_id)}",
        )
        rows = self._entities(response, "timesheets")
        if not rows:
            raise QuickBooksTimeApiError(
                f"QuickBooks Time timesheet {int(timesheet_id)} was not returned."
            )
        return rows[0]


    def timesheets(
        self,
        *,
        start_date: Any,
        end_date: Any,
        user_ids: Optional[Iterable[int]] = None,
        jobcode_ids: Optional[Iterable[int]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "start_date": self._date_text(start_date),
            "end_date": self._date_text(end_date),
        }
        if user_ids:
            params["user_ids"] = ",".join(str(int(value)) for value in user_ids)
        if jobcode_ids:
            params["jobcode_ids"] = ",".join(str(int(value)) for value in jobcode_ids)
        return self._list_all(
            path="/timesheets",
            entity_name="timesheets",
            params=params,
            operation="list timesheets",
        )

    def all_timesheets(self) -> Dict[str, Any]:
        """Return every current timesheet in the connected QuickBooks Time company.

        QuickBooks Time defaults ``on_the_clock`` to ``no``. Explicitly requesting
        ``both`` is required so an administrative cleanup also finds users who are
        currently clocked in. Supplemental data is excluded because the delete flow
        only needs the timesheet IDs.
        """
        return self._list_all(
            path="/timesheets",
            entity_name="timesheets",
            params={
                "start_date": "1900-01-01",
                "on_the_clock": "both",
                "supplemental_data": "no",
            },
            operation="list all timesheets",
        )

    def try_get_timesheet(self, timesheet_id: int) -> Optional[Dict[str, Any]]:
        try:
            return self.get_timesheet(timesheet_id)
        except QuickBooksTimeApiError as exc:
            if exc.is_not_found:
                return None
            raise

    @staticmethod
    def build_manual_timesheet_payload(
        *,
        user_id: int,
        jobcode_id: int,
        txn_date: Any,
        hours: int,
        minutes: int,
        notes: Optional[str] = None,
        customfields: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        total_minutes = int(hours) * 60 + int(minutes)
        if total_minutes <= 0:
            raise ValueError("Time entry duration must be greater than zero.")
        if total_minutes > 24 * 60:
            raise ValueError("Time entry duration cannot exceed 24 hours.")
        row: Dict[str, Any] = {
            "user_id": int(user_id),
            "jobcode_id": int(jobcode_id),
            "type": "manual",
            "duration": total_minutes * 60,
            "date": QuickBooksTimeApiService._date_text(txn_date),
        }
        clean_notes = str(notes or "").strip()
        if clean_notes:
            row["notes"] = clean_notes[:2000]
        if customfields:
            row["customfields"] = {
                str(key): str(value)
                for key, value in customfields.items()
                if str(key).strip() and value not in (None, "")
            }
        return {"data": [row]}

    def create_manual_timesheet(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/timesheets",
            payload=payload,
            operation="create timesheet",
        )
        rows = self._entities(response, "timesheets")
        self._require_entity_success(rows, operation="create timesheet")
        if not rows:
            raise QuickBooksTimeApiError(
                "QuickBooks Time create response did not include a timesheet."
            )
        created = rows[0]
        timesheet_id = created.get("id")
        if not timesheet_id:
            raise QuickBooksTimeApiError(
                "QuickBooks Time create response did not include a timesheet ID."
            )
        return {
            "timesheet": created,
            "rawResponse": response,
        }


    def delete_timesheets(self, timesheet_ids: Iterable[int]) -> Dict[str, Any]:
        """Delete up to 50 QuickBooks Time timesheets in one documented batch.

        QuickBooks Time reports success or failure per object through
        ``_status_code`` fields. Callers must inspect those rows before clearing
        local mappings.
        """
        ids = sorted({int(value) for value in timesheet_ids if int(value) > 0})
        if not ids:
            raise ValueError("At least one timesheet_id is required")
        if len(ids) > 50:
            raise ValueError("QuickBooks Time deletes support at most 50 timesheets per request")
        response = self._request(
            "DELETE",
            "/timesheets",
            params={"ids": ",".join(str(value) for value in ids)},
            operation=f"delete {len(ids)} timesheet(s)",
        )
        rows = self._entities(response, "timesheets")
        if not rows:
            raise QuickBooksTimeApiError(
                "QuickBooks Time delete response did not include object-level timesheet statuses."
            )
        return {
            "timesheets": rows,
            "rawResponse": response,
        }

    def delete_timesheet(self, timesheet_id: int) -> Dict[str, Any]:
        """Delete one QuickBooks Time timesheet using the batch delete endpoint."""
        timesheet_id = int(timesheet_id)
        if timesheet_id <= 0:
            raise ValueError("timesheet_id must be greater than zero")
        result = self.delete_timesheets([timesheet_id])
        row = next(
            (item for item in result["timesheets"] if int(item.get("id") or 0) == timesheet_id),
            None,
        )
        if row is None:
            raise QuickBooksTimeApiError(
                f"QuickBooks Time delete response did not include timesheet {timesheet_id}."
            )
        status_code = self._status_code(row)
        if status_code == 404:
            raise QuickBooksTimeApiError(
                f"QuickBooks Time timesheet {timesheet_id} was not found.",
                status_code=404,
                response_payload=row,
            )
        if status_code is not None and not 200 <= status_code < 300:
            raise QuickBooksTimeApiError(
                f"QuickBooks Time delete failed for timesheet {timesheet_id}: "
                f"{row.get('_status_message') or row.get('_status_extra') or status_code}",
                status_code=status_code,
                response_payload=row,
            )
        return {
            "timesheetId": timesheet_id,
            "timesheet": row,
            "rawResponse": result["rawResponse"],
        }


    def update_manual_timesheet(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        rows_in = list((payload or {}).get("data") or [])
        if not rows_in or not isinstance(rows_in[0], Mapping) or not rows_in[0].get("id"):
            raise ValueError("QuickBooks Time update payload must include a timesheet id.")
        response = self._request(
            "PUT",
            "/timesheets",
            payload=payload,
            operation="update timesheet",
        )
        rows = self._entities(response, "timesheets")
        self._require_entity_success(rows, operation="update timesheet")
        if not rows:
            raise QuickBooksTimeApiError(
                "QuickBooks Time update response did not include a timesheet."
            )
        return {"timesheet": rows[0], "rawResponse": response}

    @classmethod
    def build_manual_timesheet_update_payload(
        cls,
        *,
        timesheet_id: int,
        jobcode_id: int,
        txn_date: Any,
        hours: int,
        minutes: int,
        notes: Optional[str] = None,
        customfields: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = cls.build_manual_timesheet_payload(
            user_id=1,  # user_id is immutable and removed below for updates.
            jobcode_id=jobcode_id,
            txn_date=txn_date,
            hours=hours,
            minutes=minutes,
            notes=notes,
            customfields=customfields,
        )
        row = dict(payload["data"][0])
        row.pop("user_id", None)
        row["id"] = int(timesheet_id)
        return {"data": [row]}

    @staticmethod
    def build_user_payload(
        *,
        first_name: Optional[str],
        last_name: Optional[str],
        email: Optional[str] = None,
        username: Optional[str] = None,
        mobile_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the POST /users body. QuickBooks Time requires first_name, last_name,
        and a unique username; the worker's email is the natural unique username, with
        a name-based handle as a fallback when no email is on file.
        """
        first = str(first_name or "").strip()
        last = str(last_name or "").strip()
        if not first and not last:
            raise ValueError("A first or last name is required to create a QuickBooks Time user.")
        email_clean = str(email or "").strip()
        resolved_username = (
            str(username or "").strip()
            or email_clean
            or f"{first}.{last}".strip(".").lower().replace(" ", "")
        )
        row: Dict[str, Any] = {
            "username": resolved_username,
            "first_name": first or last,
            "last_name": last or first,
            "active": True,
        }
        if email_clean:
            row["email"] = email_clean
        mobile_clean = "".join(
            character for character in str(mobile_number or "") if character.isdigit()
        )
        if mobile_clean:
            row["mobile_number"] = mobile_clean
        return {"data": [row]}

    def create_user(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/users",
            payload=payload,
            operation="create user",
        )
        rows = self._entities(response, "users")
        self._require_entity_success(rows, operation="create user")
        if not rows:
            raise QuickBooksTimeApiError("QuickBooks Time create user response did not include a user.")
        created = rows[0]
        if not created.get("id"):
            raise QuickBooksTimeApiError("QuickBooks Time create user response did not include a user Id.")
        return {
            "user": created,
            "rawResponse": response,
        }

    def approve_user_through_date(
        self,
        *,
        user_id: int,
        approved_to: Any,
        confirmation: str,
    ) -> Dict[str, Any]:
        if self._clean(confirmation) != self.APPROVAL_CONFIRMATION:
            raise ValueError(
                "Approval requires approval_confirmation="
                f"{self.APPROVAL_CONFIRMATION}. This approves and locks all time for "
                "the QuickBooks Time user through the selected date."
            )
        approved_to_text = self._date_text(approved_to)
        payload = {
            "data": [
                {
                    "id": int(user_id),
                    "approved_to": approved_to_text,
                }
            ]
        }
        response = self._request(
            "PUT",
            "/users",
            payload=payload,
            operation=f"approve user {int(user_id)} through {approved_to_text}",
        )
        rows = self._entities(response, "users")
        self._require_entity_success(rows, operation="approve user time")
        user = rows[0] if rows else self.get_user(int(user_id))
        return {
            "user": user,
            "rawResponse": response,
        }

    @staticmethod
    def _date_on_or_after(actual: Any, expected: str) -> bool:
        actual_text = str(actual or "").strip()
        if not actual_text:
            return False
        try:
            return date.fromisoformat(actual_text[:10]) >= date.fromisoformat(expected)
        except ValueError:
            return False

    def create_timesheet_test(
        self,
        *,
        user_id: int,
        jobcode_id: int,
        txn_date: Any,
        hours: int,
        minutes: int,
        notes: Optional[str] = None,
        service_custom_field_id: Optional[int] = None,
        service_custom_field_item_id: Optional[int] = None,
        dry_run: bool = True,
        confirm_create: bool = False,
        create_confirmation: Optional[str] = None,
        approve_through_date: bool = False,
        approval_confirmation: Optional[str] = None,
    ) -> Dict[str, Any]:
        if bool(service_custom_field_id) != bool(service_custom_field_item_id):
            raise ValueError(
                "service_custom_field_id and service_custom_field_item_id must be supplied together."
            )
        customfields = None
        if service_custom_field_id and service_custom_field_item_id:
            customfields = {
                str(int(service_custom_field_id)): int(service_custom_field_item_id)
            }
        payload = self.build_manual_timesheet_payload(
            user_id=int(user_id),
            jobcode_id=int(jobcode_id),
            txn_date=txn_date,
            hours=int(hours),
            minutes=int(minutes),
            notes=notes,
            customfields=customfields,
        )
        txn_date_text = payload["data"][0]["date"]

        result: Dict[str, Any] = {
            "ok": True,
            "dryRun": bool(dry_run),
            "target": "QUICKBOOKS_TIME_TIMESHEET",
            "requestPayload": payload,
            "approvalRequested": bool(approve_through_date),
            "approvalWarning": (
                "QuickBooks Time approval is user/date based. Approving this test "
                "also approves and locks all of this user's time through the same date."
            ),
            "configuration": self.public_status(),
        }
        if dry_run:
            result["message"] = (
                "Dry run only. No QuickBooks Time timesheet was created and no time was approved."
            )
            return result

        if not confirm_create or self._clean(create_confirmation) != self.CREATE_CONFIRMATION:
            raise ValueError(
                "Live creation requires confirm_create=true and create_confirmation="
                f"{self.CREATE_CONFIRMATION}."
            )

        created_result = self.create_manual_timesheet(payload)
        created = created_result["timesheet"]
        timesheet_id = int(created["id"])
        saved_timesheet = self.get_timesheet(timesheet_id)
        saved_user = self.get_user(int(user_id))

        approval_result = None
        if approve_through_date:
            approval_result = self.approve_user_through_date(
                user_id=int(user_id),
                approved_to=txn_date_text,
                confirmation=self._clean(approval_confirmation),
            )
            saved_user = self.get_user(int(user_id))

        approval_verified = self._date_on_or_after(
            saved_user.get("approved_to"),
            txn_date_text,
        )
        result.update(
            {
                "timesheetId": timesheet_id,
                "createdTimesheet": created,
                "savedTimesheet": saved_timesheet,
                "savedUser": saved_user,
                "approvalVerified": approval_verified,
                "createResponse": created_result["rawResponse"],
                "approvalResponse": approval_result["rawResponse"] if approval_result else None,
                "message": (
                    "QuickBooks Time timesheet created and approval verified."
                    if approval_verified
                    else "QuickBooks Time timesheet created. Approval was not verified."
                ),
            }
        )
        return result

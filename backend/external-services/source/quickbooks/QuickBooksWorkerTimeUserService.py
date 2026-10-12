from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from source.Utils import constants as const
from source.dal.EmployeeRepository import EmployeeRepository
from source.quickbooks.QuickBooksTimeApiService import QuickBooksTimeApiService
from source.quickbooks.QuickBooksTimeMatchingService import QuickBooksTimeMatchingService
from source.service.QuickBooksTimeAuthService import QuickBooksTimeAuthService

logger = logging.getLogger(__name__)


class QuickBooksWorkerTimeUserService:
    """Backfill QuickBooks Time user IDs for synced FIELDCRAFT workers.

    Accounting Employee/Vendor IDs and QuickBooks Time user IDs are separate
    identifiers. This service runs after worker sync and links only a unique,
    active Time user. Ambiguous records remain pending rather than being guessed.
    """

    def __init__(
        self,
        *,
        employee_repo: Optional[EmployeeRepository] = None,
        time_auth: Optional[QuickBooksTimeAuthService] = None,
    ) -> None:
        self.employee_repo = employee_repo or EmployeeRepository()
        self.time_auth = time_auth or QuickBooksTimeAuthService()

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split())

    def sync_missing_user_ids(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int = 5000,
        create_missing_users: bool = True,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not service_id:
            raise ValueError("service_id is required")

        candidates = self.employee_repo.get_time_user_backfill_candidates(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=limit,
        )
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            const.KEY_MESSAGE: "No synced workers require a QuickBooks Time user ID.",
            "requested": len(candidates),
            "stored": 0,
            "created": 0,
            "pending": 0,
            "failed": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
        }
        logger.info(
            "[WorkerTimeUser] backfill candidates loaded | tenant_id=%s service_id=%s count=%s",
            tenant_id,
            service_id,
            len(candidates),
        )
        if not candidates:
            return summary

        time_api = self.time_auth.time_api_client(tenant_id, service_id)
        matcher = QuickBooksTimeMatchingService(
            time_api=time_api,
            tenant_id=tenant_id,
            service_id=service_id,
        )

        for candidate in candidates:
            mapping = candidate.get("mapping") or {}
            employee = candidate.get("employee") or {}
            mapping_id = int(mapping.get("id") or 0)
            user_account_id = employee.get("id") or mapping.get("user_account_id")
            first_name = self._clean(employee.get("firstname"))
            last_name = self._clean(employee.get("lastname"))
            email = self._clean(employee.get("email"))
            display_name = self._clean(f"{first_name} {last_name}")
            detail = {
                "mappingId": mapping_id,
                "employeeId": user_account_id,
                "employeeName": display_name or None,
                "email": email or None,
            }

            try:
                resolve_args = {
                    "email": email or None,
                    "first_name": first_name or None,
                    "last_name": last_name or None,
                    "external_display_name": (
                        self._clean(mapping.get("external_display_name")) or None
                    ),
                    "mobile_number": (
                        self._clean(employee.get("mobile_no") or employee.get("mobileNo"))
                        or None
                    ),
                    "external_worker_ids": (
                        mapping.get("external_employee_id"),
                        mapping.get("external_vendor_id"),
                    ),
                }

                def resolve_user(*, refresh: bool = False) -> Dict[str, Any]:
                    if refresh:
                        matcher.refresh_users()
                    return matcher.resolve_user_match(**resolve_args)

                match = resolve_user()
                time_user_id = self._clean(match.get("user_id"))

                if not time_user_id and match.get("status") == "not_found":
                    match = resolve_user(refresh=True)
                    time_user_id = self._clean(match.get("user_id"))

                if (
                    not time_user_id
                    and match.get("status") == "not_found"
                    and create_missing_users
                ):
                    created_by_service = False
                    payload = QuickBooksTimeApiService.build_user_payload(
                        first_name=first_name or None,
                        last_name=last_name or None,
                        email=email or None,
                        mobile_number=resolve_args["mobile_number"],
                    )
                    try:
                        created = time_api.create_user(payload)
                        time_user_id = self._clean(
                            ((created or {}).get("user") or {}).get("id")
                        )
                        created_by_service = bool(time_user_id)
                    except Exception:
                        # The Accounting-to-Time bridge can create the same user
                        # concurrently. Re-read before reporting a failure so a
                        # harmless uniqueness race never leaves the mapping empty.
                        match = resolve_user(refresh=True)
                        time_user_id = self._clean(match.get("user_id"))
                        if not time_user_id:
                            raise

                    if created_by_service:
                        summary["created"] += 1
                        detail["timeUserCreated"] = True

                if not time_user_id:
                    summary["pending"] += 1
                    detail.update(
                        {
                            "status": "pending",
                            "reason": (
                                "More than one active QuickBooks Time user matches this worker. "
                                "No user was created or linked because the match is ambiguous."
                                if match.get("status") == "ambiguous"
                                else "A QuickBooks Time user could not be found or created for this worker."
                            ),
                            "matchStatus": match.get("status"),
                            "matchMethod": match.get("method"),
                            "candidateCount": int(match.get("candidate_count") or 0),
                        }
                    )
                    summary[const.KEY_RESULTS].append(detail)
                    logger.warning(
                        "[WorkerTimeUser] user remains unresolved | tenant_id=%s service_id=%s "
                        "mapping_id=%s employee_id=%s email=%r name=%r",
                        tenant_id,
                        service_id,
                        mapping_id,
                        user_account_id,
                        email,
                        display_name,
                    )
                    continue

                self.employee_repo.set_time_user_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                    time_user_id=time_user_id,
                )
                summary["stored"] += 1
                detail.update(
                    {
                        "status": "stored",
                        "quickBooksTimeUserId": str(time_user_id),
                    }
                )
                summary[const.KEY_RESULTS].append(detail)
                logger.info(
                    "[WorkerTimeUser] QuickBooks Time user stored | tenant_id=%s service_id=%s "
                    "mapping_id=%s employee_id=%s time_user_id=%s",
                    tenant_id,
                    service_id,
                    mapping_id,
                    user_account_id,
                    time_user_id,
                )
            except Exception as exc:
                summary["failed"] += 1
                failure = dict(detail)
                failure.update({"status": "failed", const.KEY_ERROR: str(exc)})
                summary[const.KEY_FAILURES].append(failure)
                logger.exception(
                    "[WorkerTimeUser] failed to store QuickBooks Time user | "
                    "tenant_id=%s service_id=%s mapping_id=%s employee_id=%s",
                    tenant_id,
                    service_id,
                    mapping_id,
                    user_account_id,
                )

        summary[const.KEY_OK] = summary["failed"] == 0 and summary["pending"] == 0
        summary["partialSuccess"] = bool(summary["stored"] and (summary["pending"] or summary["failed"]))
        if summary["failed"]:
            summary[const.KEY_MESSAGE] = (
                "QuickBooks Time user sync completed with failures. Review the failed workers and retry."
            )
        elif summary["pending"]:
            summary[const.KEY_MESSAGE] = (
                f"Stored {summary['stored']} QuickBooks Time user ID(s); "
                f"{summary['pending']} worker(s) remain unresolved."
            )
        else:
            summary[const.KEY_MESSAGE] = (
                f"Stored {summary['stored']} QuickBooks Time user ID(s)"
                + (
                    f", including {summary['created']} newly created Time user(s)."
                    if summary["created"]
                    else "."
                )
            )
        return summary

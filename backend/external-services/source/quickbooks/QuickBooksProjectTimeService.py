from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from source.dal.ProjectRepository import ProjectRepository
from source.quickbooks.QuickBooksTimeMatchingService import QuickBooksTimeMatchingService

logger = logging.getLogger(__name__)


class QuickBooksProjectTimeService:
    """Create or link a QuickBooks Time project and store its jobcode ID.

    QuickBooks Accounting and QuickBooks Time use separate project identifiers.
    After the Accounting project is created, this service first looks for an
    existing Time project under the correct customer jobcode. If none exists,
    it creates the Time project and stores the generated ``jobcode_id`` under
    ``project_external_map.additional_info.quickbooksTime.jobCodeId``.
    """

    CREATE_RETRY_DELAYS: Sequence[float] = (0.0, 1.0, 2.0)
    BACKFILL_RETRY_DELAYS: Sequence[float] = (0.0,)

    def __init__(
        self,
        *,
        project_repo: Optional[ProjectRepository] = None,
        time_auth: Optional[Any] = None,
        sleep_fn=time.sleep,
    ) -> None:
        self.project_repo = project_repo or ProjectRepository()
        if time_auth is None:
            from source.service.QuickBooksTimeAuthService import QuickBooksTimeAuthService

            time_auth = QuickBooksTimeAuthService()
        self.time_auth = time_auth
        self._sleep = sleep_fn

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _unique(cls, values: Iterable[Any]) -> List[str]:
        result: List[str] = []
        seen = set()
        for value in values:
            cleaned = cls._clean(value)
            normalized = cleaned.casefold()
            if not cleaned or normalized in seen:
                continue
            seen.add(normalized)
            result.append(cleaned)
        return result

    @classmethod
    def project_names(cls, project: Mapping[str, Any]) -> List[str]:
        name = cls._clean(project.get("description"))
        if not name:
            name = cls._clean(project.get("order_no")) or f"Project {project.get('id')}"
        return cls._unique((name, name[:64]))

    @classmethod
    def customer_jobcode_names(cls, project: Mapping[str, Any]) -> List[str]:
        customer = project.get("customer")
        if not isinstance(customer, Mapping):
            customer = {}

        first_name = cls._clean(
            customer.get("firstName")
            or customer.get("firstname")
            or customer.get("first_name")
        )
        middle_name = cls._clean(
            customer.get("middleName")
            or customer.get("middlename")
            or customer.get("middle_name")
        )
        last_name = cls._clean(
            customer.get("lastName")
            or customer.get("lastname")
            or customer.get("last_name")
        )
        exported_name = cls._clean(" ".join(part for part in (first_name, last_name) if part))
        full_name = cls._clean(
            " ".join(part for part in (first_name, middle_name, last_name) if part)
        )

        phone_value = (
            customer.get("mobileNo")
            or customer.get("mobile_no")
            or customer.get("phone")
            or customer.get("phoneNo")
            or ""
        )
        phone = "".join(character for character in str(phone_value) if character.isdigit())[:20]
        exported_display_name = f"{exported_name}-{phone}" if exported_name and phone else exported_name

        return cls._unique(
            (
                exported_display_name,
                customer.get("displayName"),
                customer.get("display_name"),
                customer.get("companyName"),
                customer.get("company_name"),
                customer.get("businessName"),
                customer.get("name"),
                exported_name,
                full_name,
            )
        )

    @classmethod
    def _project_from_row(cls, row: Mapping[str, Any]) -> Dict[str, Any]:
        project = row.get("project")
        if isinstance(project, Mapping):
            return dict(project)
        return {
            "id": row.get("project_id"),
            "description": row.get("description"),
            "order_no": row.get("order_no"),
            "customer": row.get("customer") or {},
        }

    def _resolve_parent_jobcode(
        self,
        *,
        matcher: QuickBooksTimeMatchingService,
        project: Mapping[str, Any],
        jobcodes_available: bool,
    ) -> tuple[Optional[str], Optional[str]]:
        customer_names = self.customer_jobcode_names(project)
        if not customer_names:
            return None, "The project customer does not have enough information to resolve its QuickBooks Time jobcode."
        if not jobcodes_available:
            return None, "QuickBooks Time customer jobcodes could not be loaded."

        parent_jobcode_id = matcher.resolve_parent_jobcode(names=customer_names)
        if not parent_jobcode_id:
            return None, "The QuickBooks Time customer jobcode is not available yet."
        return str(parent_jobcode_id), None

    def _resolve_existing_project_jobcode(
        self,
        *,
        matcher: QuickBooksTimeMatchingService,
        project: Mapping[str, Any],
        parent_jobcode_id: Optional[str],
        projects_available: bool,
        jobcodes_available: bool,
    ) -> Optional[str]:
        project_names = self.project_names(project)
        jobcode_id: Optional[str] = None
        if projects_available:
            jobcode_id = matcher.resolve_project_jobcode(
                names=project_names,
                parent_jobcode_id=parent_jobcode_id,
            )
        if not jobcode_id and jobcodes_available:
            jobcode_id = matcher.resolve_jobcode(
                names=project_names,
                short_code=self._clean(project.get("order_no")) or None,
                parent_jobcode_id=parent_jobcode_id,
            )
        return str(jobcode_id) if jobcode_id else None

    def _create_time_project(
        self,
        *,
        time_api: Any,
        project: Mapping[str, Any],
        parent_jobcode_id: str,
        qbo_project_id: Optional[str],
    ) -> str:
        project_name = self.project_names(project)[0][:64]
        description_parts = [f"FIELDCRAFT Project ID: {project.get('id')}"]
        clean_qbo_project_id = self._clean(qbo_project_id)
        if clean_qbo_project_id:
            description_parts.append(f"QuickBooks Project ID: {clean_qbo_project_id}")
        clean_order_no = self._clean(project.get("order_no"))
        if clean_order_no:
            description_parts.append(f"Order No: {clean_order_no}")

        response = time_api.create_project(
            name=project_name,
            parent_jobcode_id=int(parent_jobcode_id),
            description=" | ".join(description_parts),
        )
        created = response.get("project") if isinstance(response, Mapping) else None
        if not isinstance(created, Mapping):
            raise ValueError("QuickBooks Time project create response is missing the project object")
        jobcode_id = self._clean(created.get("jobcode_id"))
        if not jobcode_id:
            raise ValueError("QuickBooks Time project create response is missing jobcode_id")
        return jobcode_id

    def _store_jobcode(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        jobcode_id: str,
    ) -> None:
        self.project_repo.set_time_jobcode_id(
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            jobcode_id=jobcode_id,
        )

    def sync_mappings(
        self,
        *,
        tenant_id: str,
        service_id: str,
        rows: Iterable[Mapping[str, Any]],
        retry_delays: Sequence[float] = BACKFILL_RETRY_DELAYS,
    ) -> Dict[int, Dict[str, Any]]:
        """Resolve or create Time projects using one authenticated API client."""
        pending: Dict[int, Dict[str, Any]] = {}
        for raw_row in rows:
            row = dict(raw_row)
            mapping_id = row.get("mapping_id")
            if mapping_id is None and isinstance(row.get("mapping"), Mapping):
                mapping_id = row["mapping"].get("id")
            if mapping_id is None:
                continue
            normalized_id = int(mapping_id)
            row["mapping_id"] = normalized_id
            row["project"] = self._project_from_row(row)
            pending[normalized_id] = row

        if not pending:
            return {}

        time_api = self.time_auth.time_api_client(tenant_id, service_id)
        matcher = QuickBooksTimeMatchingService(
            time_api=time_api,
            tenant_id=tenant_id,
            service_id=service_id,
        )
        results: Dict[int, Dict[str, Any]] = {}
        last_messages: Dict[int, str] = {}
        delays = tuple(retry_delays or (0.0,))

        for attempt, delay_seconds in enumerate(delays, start=1):
            if not pending:
                break
            if delay_seconds > 0:
                self._sleep(float(delay_seconds))

            projects_available = True
            jobcodes_available = True
            try:
                matcher.refresh_projects()
            except Exception as exc:
                projects_available = False
                logger.warning(
                    "[ProjectTime] QuickBooks Time projects could not be loaded | "
                    "tenant_id=%s service_id=%s attempt=%s error=%s",
                    tenant_id,
                    service_id,
                    attempt,
                    exc,
                )
            try:
                matcher.refresh_jobcodes()
            except Exception as exc:
                jobcodes_available = False
                logger.warning(
                    "[ProjectTime] QuickBooks Time jobcodes could not be loaded | "
                    "tenant_id=%s service_id=%s attempt=%s error=%s",
                    tenant_id,
                    service_id,
                    attempt,
                    exc,
                )

            for mapping_id, row in list(pending.items()):
                project = row["project"]

                # Always try to retrieve an existing Time project first. Reading
                # an existing project does not require the customer jobcode and
                # is safe because the matcher accepts only one exact active match.
                # This lets every project sync backfill additional_info even when
                # the customer hierarchy has not been mirrored to Time yet.
                jobcode_id = self._resolve_existing_project_jobcode(
                    matcher=matcher,
                    project=project,
                    parent_jobcode_id=None,
                    projects_available=projects_available,
                    jobcodes_available=jobcodes_available,
                )
                action = "linkedMirroredTimeProject"
                parent_jobcode_id: Optional[str] = None

                if jobcode_id:
                    # Parent resolution is optional for an existing project. It
                    # is included in the response when available, but it must not
                    # block persistence of the project jobcode.
                    parent_jobcode_id, _ = self._resolve_parent_jobcode(
                        matcher=matcher,
                        project=project,
                        jobcodes_available=jobcodes_available,
                    )
                else:
                    # A parent customer jobcode is required only when we need to
                    # create a missing QuickBooks Time project.
                    parent_jobcode_id, parent_error = self._resolve_parent_jobcode(
                        matcher=matcher,
                        project=project,
                        jobcodes_available=jobcodes_available,
                    )
                    if not parent_jobcode_id:
                        last_messages[mapping_id] = (
                            parent_error or "QuickBooks Time customer jobcode is pending."
                        )
                        continue

                    # The global lookup can be ambiguous when different customers
                    # use the same project name. Once the customer parent is known,
                    # retry with the parent as part of the identity.
                    jobcode_id = self._resolve_existing_project_jobcode(
                        matcher=matcher,
                        project=project,
                        parent_jobcode_id=parent_jobcode_id,
                        projects_available=projects_available,
                        jobcodes_available=jobcodes_available,
                    )

                if not jobcode_id:
                    if not projects_available:
                        last_messages[mapping_id] = (
                            "QuickBooks Time projects could not be loaded, so project creation was skipped to avoid duplicates."
                        )
                        continue
                    try:
                        jobcode_id = self._create_time_project(
                            time_api=time_api,
                            project=project,
                            parent_jobcode_id=parent_jobcode_id,
                            qbo_project_id=(
                                row.get("qbo_project_id")
                                or row.get("external_project_id")
                            ),
                        )
                        action = "createdTimeProject"
                    except Exception as exc:
                        last_messages[mapping_id] = str(exc)
                        logger.warning(
                            "[ProjectTime] QuickBooks Time project create failed | "
                            "tenant_id=%s service_id=%s mapping_id=%s qbo_project_id=%s "
                            "parent_jobcode_id=%s attempt=%s error=%s",
                            tenant_id,
                            service_id,
                            mapping_id,
                            row.get("qbo_project_id") or row.get("external_project_id"),
                            parent_jobcode_id,
                            attempt,
                            exc,
                        )
                        continue

                try:
                    self._store_jobcode(
                        tenant_id=tenant_id,
                        mapping_id=mapping_id,
                        jobcode_id=jobcode_id,
                    )
                except Exception as exc:
                    last_messages[mapping_id] = str(exc)
                    logger.warning(
                        "[ProjectTime] failed to store jobcode | tenant_id=%s mapping_id=%s "
                        "jobcode_id=%s error=%s",
                        tenant_id,
                        mapping_id,
                        jobcode_id,
                        exc,
                    )
                    continue

                results[mapping_id] = {
                    "ok": True,
                    "action": action,
                    "jobCodeId": jobcode_id,
                    "parentJobCodeId": parent_jobcode_id,
                    "attempt": attempt,
                }
                pending.pop(mapping_id, None)
                logger.info(
                    "[ProjectTime] QuickBooks Time project jobcode stored | tenant_id=%s "
                    "service_id=%s mapping_id=%s qbo_project_id=%s jobcode_id=%s "
                    "action=%s attempt=%s",
                    tenant_id,
                    service_id,
                    mapping_id,
                    row.get("external_project_id") or row.get("qbo_project_id"),
                    jobcode_id,
                    action,
                    attempt,
                )

        for mapping_id, row in pending.items():
            message = last_messages.get(
                mapping_id,
                "The QuickBooks Time project could not be linked or created. Retry project sync.",
            )
            results[mapping_id] = {
                "ok": False,
                "action": "timeProjectPending",
                "jobCodeId": None,
                "parentJobCodeId": None,
                "message": message,
                "attempts": len(delays),
            }
            logger.info(
                "[ProjectTime] QuickBooks Time project remains pending | tenant_id=%s "
                "service_id=%s mapping_id=%s qbo_project_id=%s attempts=%s reason=%s",
                tenant_id,
                service_id,
                mapping_id,
                row.get("external_project_id") or row.get("qbo_project_id"),
                len(delays),
                message,
            )
        return results

    def sync_mapping(
        self,
        *,
        tenant_id: str,
        service_id: str,
        mapping_id: int,
        project: Mapping[str, Any],
        qbo_project_id: Optional[str] = None,
        retry_delays: Sequence[float] = CREATE_RETRY_DELAYS,
    ) -> Dict[str, Any]:
        results = self.sync_mappings(
            tenant_id=tenant_id,
            service_id=service_id,
            rows=(
                {
                    "mapping_id": int(mapping_id),
                    "project": dict(project),
                    "qbo_project_id": qbo_project_id,
                },
            ),
            retry_delays=retry_delays,
        )
        return results[int(mapping_id)]

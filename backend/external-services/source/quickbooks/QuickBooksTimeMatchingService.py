from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

from source.quickbooks.QuickBooksTimeApiService import QuickBooksTimeApiService


logger = logging.getLogger(__name__)


class QuickBooksTimeMatchingService:
    """Resolve FIELDCRAFT names to existing QuickBooks Time identifiers.

    Matching is deliberately strict. Only one active, exact normalized-name
    match is accepted; ambiguous matches are left unresolved for a later retry
    or manual correction instead of linking the wrong external record.
    """

    SERVICE_ITEM_FIELD_NAMES = {
        "service item",
        "service items",
        "service",
        "services",
    }
    SERVICE_ITEM_FIELD_SHORT_CODES = {
        "serviceitem",
        "serviceitems",
        "service",
        "services",
    }
    BILLABLE_FIELD_NAMES = {
        "billable",
        "billable time",
        "is billable",
    }
    BILLABLE_FIELD_SHORT_CODES = {
        "billable",
        "billabletime",
        "isbillable",
        "bill",
    }
    BILLABLE_POSITIVE_VALUES = {
        "1",
        "bill",
        "billable",
        "chargeable",
        "true",
        "y",
        "yes",
    }
    BILLABLE_NEGATIVE_VALUES = {
        "0",
        "false",
        "n",
        "no",
        "non billable",
        "nonbillable",
        "not billable",
        "unbillable",
    }

    def __init__(
        self,
        *,
        time_api: QuickBooksTimeApiService,
        tenant_id: str,
        service_id: str,
    ) -> None:
        self.time_api = time_api
        self.tenant_id = str(tenant_id or "").strip()
        self.service_id = str(service_id or "").strip()
        self._users: Optional[List[Dict[str, Any]]] = None
        self._projects: Optional[List[Dict[str, Any]]] = None
        self._jobcodes: Optional[List[Dict[str, Any]]] = None
        self._customfields: Optional[List[Dict[str, Any]]] = None
        self._customfield_items: Dict[str, List[Dict[str, Any]]] = {}

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _normalized_name(cls, value: Any) -> str:
        return cls._clean(value).casefold()

    @staticmethod
    def _is_active(row: Mapping[str, Any]) -> bool:
        value = row.get("active")
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().casefold() in {"1", "true", "yes", "active"}

    @staticmethod
    def _external_id(row: Mapping[str, Any]) -> Optional[str]:
        value = row.get("id")
        resolved = str(value or "").strip()
        return resolved or None

    def _active_exact_matches(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        expected_name: str,
    ) -> List[Dict[str, Any]]:
        normalized_expected = self._normalized_name(expected_name)
        if not normalized_expected:
            return []
        return [
            dict(row)
            for row in rows
            if self._is_active(row)
            and self._external_id(row)
            and self._normalized_name(row.get("name")) == normalized_expected
        ]

    def _single_match_id(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        expected_name: str,
        resource_name: str,
    ) -> Optional[str]:
        matches = self._active_exact_matches(rows, expected_name=expected_name)
        unique_ids = sorted({self._external_id(row) for row in matches if self._external_id(row)})
        if len(unique_ids) == 1:
            return unique_ids[0]
        if len(unique_ids) > 1:
            logger.warning(
                "[QuickBooksTimeMatcher] ambiguous %s name; leaving unresolved | tenant_id=%s service_id=%s name=%r matches=%s",
                resource_name,
                self.tenant_id,
                self.service_id,
                self._clean(expected_name),
                len(unique_ids),
            )
        return None

    def _load_users(self) -> List[Dict[str, Any]]:
        if self._users is None:
            payload = self.time_api.users(active="both") or {}
            rows = payload.get("users") or []
            self._users = [dict(row) for row in rows if isinstance(row, Mapping)]
            logger.info(
                "[QuickBooksTimeMatcher] users loaded | tenant_id=%s service_id=%s count=%s",
                self.tenant_id,
                self.service_id,
                len(self._users),
            )
        return self._users

    def refresh_users(self) -> List[Dict[str, Any]]:
        self._users = None
        return self._load_users()

    @classmethod
    def _normalized_legacy_name(cls, value: Any) -> str:
        normalized = cls._normalized_name(value)
        return re.sub(r"[-\s]+\d{6,}$", "", normalized).strip()

    @staticmethod
    def _normalized_phone(value: Any) -> str:
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    def resolve_user_match(
        self,
        *,
        email: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        external_display_name: Optional[str] = None,
        mobile_number: Optional[str] = None,
        external_worker_ids: Iterable[Optional[str]] = (),
    ) -> Dict[str, Any]:
        """Describe the best active QuickBooks Time user match.

        Strong identifiers are evaluated before weaker name matches. Ambiguous
        strong matches are narrowed by the remaining identifiers when possible.
        The final status lets callers distinguish an ambiguous existing user
        from a genuinely missing user and prevents duplicate Time users.
        """
        active_rows = [
            dict(row)
            for row in self._load_users()
            if self._is_active(row) and self._external_id(row)
        ]
        ambiguity: Optional[Dict[str, Any]] = None

        def evaluate(
            rows: Iterable[Mapping[str, Any]],
            *,
            reason: str,
        ) -> Optional[Dict[str, Any]]:
            nonlocal ambiguity
            ids = {self._external_id(row) for row in rows if self._external_id(row)}
            if len(ids) == 1:
                resolved = next(iter(ids))
                logger.info(
                    "[QuickBooksTimeMatcher] user resolved | tenant_id=%s service_id=%s method=%s time_user_id=%s",
                    self.tenant_id,
                    self.service_id,
                    reason,
                    resolved,
                )
                return {
                    "status": "matched",
                    "user_id": resolved,
                    "method": reason,
                    "candidate_count": 1,
                }
            if len(ids) > 1:
                ambiguity = {
                    "status": "ambiguous",
                    "user_id": None,
                    "method": reason,
                    "candidate_count": len(ids),
                }
            return None

        candidates = active_rows
        worker_ids = {self._clean(value) for value in external_worker_ids if self._clean(value)}
        if worker_ids:
            payroll_rows = [
                row for row in active_rows
                if self._clean(row.get("payroll_id")) in worker_ids
            ]
            result = evaluate(payroll_rows, reason="payroll_id")
            if result:
                return result
            if payroll_rows:
                candidates = payroll_rows

        normalized_email = self._normalized_name(email)
        email_rows = [
            row for row in candidates
            if normalized_email and normalized_email in {
                self._normalized_name(row.get("email")),
                self._normalized_name(row.get("username")),
            }
        ]
        if email_rows:
            candidates = email_rows

        normalized_mobile = self._normalized_phone(mobile_number)
        if normalized_mobile:
            mobile_rows = [
                row for row in candidates
                if self._normalized_phone(row.get("mobile_number")) == normalized_mobile
            ]
            result = evaluate(mobile_rows, reason="mobile_number")
            if result:
                return result
            if mobile_rows:
                candidates = mobile_rows

        normalized_display = self._normalized_name(external_display_name)
        if normalized_display:
            display_rows = [
                row for row in candidates
                if normalized_display in {
                    self._normalized_name(row.get("display_name")),
                    self._normalized_name(
                        f"{self._clean(row.get('first_name'))} {self._clean(row.get('last_name'))}"
                    ),
                }
            ]
            result = evaluate(display_rows, reason="external_display_name")
            if result:
                return result
            if display_rows:
                candidates = display_rows

        normalized_full_name = self._normalized_name(
            f"{self._clean(first_name)} {self._clean(last_name)}"
        )
        normalized_legacy_display = self._normalized_legacy_name(external_display_name)
        expected_names = {
            value for value in (normalized_full_name, normalized_legacy_display) if value
        }
        if expected_names:
            name_rows = []
            for row in candidates:
                candidate_names = {
                    self._normalized_name(row.get("name")),
                    self._normalized_name(row.get("display_name")),
                    self._normalized_name(
                        f"{self._clean(row.get('first_name'))} {self._clean(row.get('last_name'))}"
                    ),
                    self._normalized_legacy_name(row.get("display_name")),
                    self._normalized_legacy_name(
                        f"{self._clean(row.get('first_name'))} {self._clean(row.get('last_name'))}"
                    ),
                }
                if expected_names.intersection(candidate_names):
                    name_rows.append(row)
            result = evaluate(name_rows, reason="exact_name")
            if result:
                return result
            if name_rows:
                candidates = name_rows

        result = evaluate(email_rows, reason="unique_email")
        if result:
            return result

        if ambiguity:
            logger.warning(
                "[QuickBooksTimeMatcher] user match ambiguous | tenant_id=%s service_id=%s method=%s candidates=%s",
                self.tenant_id,
                self.service_id,
                ambiguity["method"],
                ambiguity["candidate_count"],
            )
            return ambiguity

        logger.info(
            "[QuickBooksTimeMatcher] user not found | tenant_id=%s service_id=%s email_matches=%s",
            self.tenant_id,
            self.service_id,
            len(email_rows),
        )
        return {
            "status": "not_found",
            "user_id": None,
            "method": None,
            "candidate_count": 0,
        }

    def resolve_user_id(
        self,
        *,
        email: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        external_display_name: Optional[str] = None,
        mobile_number: Optional[str] = None,
        external_worker_ids: Iterable[Optional[str]] = (),
    ) -> Optional[str]:
        """Return the matched Time user ID, preserving the legacy interface."""
        result = self.resolve_user_match(
            email=email,
            first_name=first_name,
            last_name=last_name,
            external_display_name=external_display_name,
            mobile_number=mobile_number,
            external_worker_ids=external_worker_ids,
        )
        return self._clean(result.get("user_id")) or None

    @staticmethod
    def _project_jobcode_id(row: Mapping[str, Any]) -> Optional[str]:
        value = row.get("jobcode_id")
        resolved = str(value or "").strip()
        return resolved or None

    def _load_projects(self) -> List[Dict[str, Any]]:
        if self._projects is None:
            payload = self.time_api.projects(active="both") or {}
            rows = payload.get("projects") or []
            self._projects = [dict(row) for row in rows if isinstance(row, Mapping)]
        return self._projects

    def refresh_projects(self) -> List[Dict[str, Any]]:
        """Reload Time projects so a recently mirrored project can be discovered."""
        self._projects = None
        return self._load_projects()

    @staticmethod
    def _parent_jobcode_id(row: Mapping[str, Any]) -> Optional[str]:
        value = row.get("parent_jobcode_id")
        resolved = str(value or "").strip()
        return resolved or None

    def resolve_project_jobcode(
        self,
        *,
        names: Iterable[str],
        parent_jobcode_id: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve the jobcode ID exposed directly by a QuickBooks Time project.

        When a customer parent is known, it is part of the identity. This prevents
        projects with the same name under different customers from being linked.
        """
        normalized_names = {
            self._normalized_name(name)
            for name in names
            if self._normalized_name(name)
        }
        if not normalized_names:
            return None

        expected_parent_id = self._clean(parent_jobcode_id)
        matched_ids = {
            self._project_jobcode_id(row)
            for row in self._load_projects()
            if self._is_active(row)
            and self._project_jobcode_id(row)
            and self._normalized_name(row.get("name")) in normalized_names
            and (
                not expected_parent_id
                or self._parent_jobcode_id(row) == expected_parent_id
            )
        }
        matched_ids.discard(None)
        if len(matched_ids) == 1:
            return next(iter(matched_ids))
        if len(matched_ids) > 1:
            logger.warning(
                "[QuickBooksTimeMatcher] ambiguous Time project match; leaving unresolved | "
                "tenant_id=%s service_id=%s names=%s parent_jobcode_id=%s matches=%s",
                self.tenant_id,
                self.service_id,
                sorted(normalized_names),
                expected_parent_id or None,
                len(matched_ids),
            )
        return None

    def _load_jobcodes(self) -> List[Dict[str, Any]]:
        if self._jobcodes is None:
            payload = self.time_api.jobcodes(active="both", jobcode_type="all") or {}
            rows = payload.get("jobcodes") or []
            self._jobcodes = [dict(row) for row in rows if isinstance(row, Mapping)]
        return self._jobcodes

    def refresh_jobcodes(self) -> List[Dict[str, Any]]:
        """Reload jobcodes so recently mirrored QBO projects can be discovered."""
        self._jobcodes = None
        return self._load_jobcodes()

    def resolve_parent_jobcode(self, *, names: Iterable[str]) -> Optional[str]:
        """Resolve one active top-level customer jobcode by exact name."""
        normalized_names = {
            self._normalized_name(name)
            for name in names
            if self._normalized_name(name)
        }
        if not normalized_names:
            return None

        matched_ids = {
            self._external_id(row)
            for row in self._load_jobcodes()
            if self._is_active(row)
            and self._external_id(row)
            and str(row.get("parent_id") or "0").strip() == "0"
            and self._normalized_name(row.get("name")) in normalized_names
        }
        matched_ids.discard(None)
        if len(matched_ids) == 1:
            return next(iter(matched_ids))
        if len(matched_ids) > 1:
            logger.warning(
                "[QuickBooksTimeMatcher] ambiguous parent customer jobcode; leaving unresolved | "
                "tenant_id=%s service_id=%s names=%s matches=%s",
                self.tenant_id,
                self.service_id,
                sorted(normalized_names),
                len(matched_ids),
            )
        return None

    def resolve_jobcode(
        self,
        *,
        names: Iterable[str],
        short_code: Optional[str] = None,
        parent_jobcode_id: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve one active jobcode using strict, unambiguous identifiers.

        QuickBooks may expose a project using either the submitted project name or
        a fully-qualified customer/project name. Exact name matches are preferred.
        An exact ``short_code`` match is used only when it identifies one jobcode.
        """
        rows = self._load_jobcodes()
        normalized_names = {
            self._normalized_name(name)
            for name in names
            if self._normalized_name(name)
        }

        expected_parent_id = self._clean(parent_jobcode_id)
        matched_ids = {
            self._external_id(row)
            for row in rows
            if self._is_active(row)
            and self._external_id(row)
            and self._normalized_name(row.get("name")) in normalized_names
            and (
                not expected_parent_id
                or str(row.get("parent_id") or "0").strip() == expected_parent_id
            )
        }
        matched_ids.discard(None)
        if len(matched_ids) == 1:
            return next(iter(matched_ids))
        if len(matched_ids) > 1:
            logger.warning(
                "[QuickBooksTimeMatcher] ambiguous jobcode name match; leaving unresolved | "
                "tenant_id=%s service_id=%s names=%s parent_jobcode_id=%s matches=%s",
                self.tenant_id,
                self.service_id,
                sorted(normalized_names),
                expected_parent_id or None,
                len(matched_ids),
            )
            return None

        normalized_short_code = self._normalized_name(short_code)
        if normalized_short_code:
            short_code_ids = {
                self._external_id(row)
                for row in rows
                if self._is_active(row)
                and self._external_id(row)
                and self._normalized_name(row.get("short_code")) == normalized_short_code
                and (
                    not expected_parent_id
                    or str(row.get("parent_id") or "0").strip() == expected_parent_id
                )
            }
            short_code_ids.discard(None)
            if len(short_code_ids) == 1:
                return next(iter(short_code_ids))
            if len(short_code_ids) > 1:
                logger.warning(
                    "[QuickBooksTimeMatcher] ambiguous jobcode short_code; leaving unresolved | "
                    "tenant_id=%s service_id=%s short_code=%r matches=%s",
                    self.tenant_id,
                    self.service_id,
                    self._clean(short_code),
                    len(short_code_ids),
                )
        return None

    def resolve_jobcode_by_name(self, name: str) -> Optional[str]:
        """Backward-compatible exact-name jobcode lookup."""
        return self.resolve_jobcode(names=[name])

    def _load_customfields(self) -> List[Dict[str, Any]]:
        if self._customfields is None:
            payload = self.time_api.customfields() or {}
            rows = payload.get("customfields") or []
            self._customfields = [dict(row) for row in rows if isinstance(row, Mapping)]
            logger.info(
                "[QuickBooksTimeMatcher] custom fields loaded | tenant_id=%s service_id=%s count=%s fields=%s",
                self.tenant_id,
                self.service_id,
                len(self._customfields),
                [
                    {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "short_code": row.get("short_code"),
                        "type": row.get("type"),
                        "applies_to": row.get("applies_to"),
                        "active": row.get("active"),
                    }
                    for row in self._customfields
                ],
            )
        return self._customfields

    def refresh_service_items(self) -> None:
        """Clear cached custom fields and choices before one immediate retry."""
        self._customfields = None
        self._customfield_items = {}

    def refresh_customfields(self) -> None:
        """Clear all cached timesheet custom fields and managed-list choices."""
        self.refresh_service_items()

    def get_user_by_id(self, user_id: Any) -> Optional[Dict[str, Any]]:
        """Return one QuickBooks Time user row without weakening identity matching."""
        expected_id = self._clean(user_id)
        if not expected_id:
            return None
        rows = [
            dict(row)
            for row in self._load_users()
            if self._external_id(row) == expected_id
        ]
        return rows[0] if len(rows) == 1 else None

    def _is_managed_timesheet_field(self, row: Mapping[str, Any]) -> bool:
        if not self._is_active(row) or not self._external_id(row):
            return False
        field_type = self._normalized_name(row.get("type"))
        applies_to = self._normalized_name(row.get("applies_to"))
        return field_type in {"", "managed-list"} and applies_to in {"", "timesheet"}

    def _is_named_service_item_field(self, row: Mapping[str, Any]) -> bool:
        normalized_name = self._normalized_name(row.get("name"))
        normalized_short_code = re.sub(
            r"[^a-z0-9]",
            "",
            self._normalized_name(row.get("short_code")),
        )
        return (
            normalized_name in self.SERVICE_ITEM_FIELD_NAMES
            or normalized_short_code in self.SERVICE_ITEM_FIELD_SHORT_CODES
        )

    def _is_named_billable_field(self, row: Mapping[str, Any]) -> bool:
        if not self._is_active(row) or not self._external_id(row):
            return False
        applies_to = self._normalized_name(row.get("applies_to"))
        if applies_to not in {"", "timesheet"}:
            return False
        normalized_name = self._normalized_name(row.get("name"))
        normalized_short_code = re.sub(
            r"[^a-z0-9]",
            "",
            self._normalized_name(row.get("short_code")),
        )
        return (
            normalized_name in self.BILLABLE_FIELD_NAMES
            or normalized_short_code in self.BILLABLE_FIELD_SHORT_CODES
        )

    def _billable_choice_score(self, row: Mapping[str, Any]) -> int:
        values = {
            self._normalized_name(row.get("name")),
            self._normalized_name(row.get("short_code")),
        }
        values.discard("")
        if values & self.BILLABLE_POSITIVE_VALUES:
            return 100
        if values & self.BILLABLE_NEGATIVE_VALUES:
            return -100
        joined = " ".join(sorted(values))
        if "not billable" in joined or "non billable" in joined or "nonbillable" in joined:
            return -100
        if "billable" in joined and not any(token in joined for token in ("not", "non", "un")):
            return 80
        if "yes" in joined or "true" in joined:
            return 70
        return 0

    def resolve_billable_customfield(self) -> Dict[str, Any]:
        """Resolve the account's Billable timesheet field to its positive value.

        QuickBooks Time represents Billable as an account-specific custom field.
        The field may be renamed through its short code and its positive choice
        may be named ``Yes`` or ``Billable``. Only one unambiguous active field
        and one unambiguous positive choice are accepted.
        """
        fields = [
            dict(row)
            for row in self._load_customfields()
            if self._is_named_billable_field(row)
        ]
        if not fields:
            return {
                "status": "not_found",
                "customfield_id": None,
                "item_id": None,
                "value": None,
                "field_name": None,
                "required": None,
            }
        field_ids = {self._external_id(row) for row in fields if self._external_id(row)}
        if len(field_ids) != 1:
            return {
                "status": "ambiguous",
                "customfield_id": None,
                "item_id": None,
                "value": None,
                "field_name": None,
                "required": None,
            }

        field = fields[0]
        field_id = self._external_id(field)
        field_type = self._normalized_name(field.get("type"))
        result = {
            "customfield_id": field_id,
            "field_name": self._clean(field.get("name")) or "Billable",
            "required": bool(field.get("required")),
        }
        if field_type == "free-form":
            return {
                **result,
                "status": "matched",
                "item_id": None,
                "value": "Yes",
            }

        items = [
            dict(row)
            for row in self._load_customfield_items(str(field_id))
            if self._is_active(row) and self._external_id(row) and self._clean(row.get("name"))
        ]
        scored = [(self._billable_choice_score(row), row) for row in items]
        positive = [row for score, row in scored if score > 0]
        highest = max((score for score, _row in scored), default=0)
        highest_rows = [row for score, row in scored if score == highest and score > 0]
        if len(highest_rows) == 1:
            choice = highest_rows[0]
            return {
                **result,
                "status": "matched",
                "item_id": self._external_id(choice),
                "value": self._clean(choice.get("name")),
            }

        negative = [row for score, row in scored if score < 0]
        neutral = [row for score, row in scored if score == 0]
        if not positive and len(negative) == 1 and len(neutral) == 1:
            choice = neutral[0]
            return {
                **result,
                "status": "matched",
                "item_id": self._external_id(choice),
                "value": self._clean(choice.get("name")),
            }

        return {
            **result,
            "status": "ambiguous" if items else "not_found",
            "item_id": None,
            "value": None,
        }

    def _load_customfield_items(self, customfield_id: str) -> List[Dict[str, Any]]:
        cached = self._customfield_items.get(customfield_id)
        if cached is not None:
            return cached
        payload = self.time_api.customfielditems(customfield_id=int(customfield_id)) or {}
        rows = payload.get("customfielditems") or []
        result = [dict(row) for row in rows if isinstance(row, Mapping)]
        self._customfield_items[customfield_id] = result
        logger.info(
            "[QuickBooksTimeMatcher] custom field items loaded | tenant_id=%s service_id=%s customfield_id=%s count=%s",
            self.tenant_id,
            self.service_id,
            customfield_id,
            len(result),
        )
        return result

    def _service_item_pairs(
        self,
        *,
        fields: Iterable[Mapping[str, Any]],
        expected_names: Iterable[str],
    ) -> List[Dict[str, str]]:
        normalized_expected = {
            self._normalized_name(value)
            for value in expected_names
            if self._normalized_name(value)
        }
        if not normalized_expected:
            return []

        pairs: List[Dict[str, str]] = []
        for field in fields:
            customfield_id = self._external_id(field)
            if not customfield_id:
                continue
            matched_items: Dict[str, str] = {}
            for item in self._load_customfield_items(customfield_id):
                item_id = self._external_id(item)
                item_name = self._clean(item.get("name"))
                if (
                    self._is_active(item)
                    and item_id
                    and item_name
                    and self._normalized_name(item_name) in normalized_expected
                ):
                    matched_items[str(item_id)] = item_name
            for item_id in sorted(matched_items):
                pairs.append(
                    {
                        "customfield_id": customfield_id,
                        "item_id": item_id,
                        "item_name": matched_items[item_id],
                    }
                )
        return pairs

    def resolve_service_item(
        self,
        *,
        names: Iterable[str],
    ) -> Dict[str, Optional[str]]:
        """Resolve a service choice without assuming the custom-field label.

        QuickBooks Time service choices are managed-list custom-field items. The
        standard field is commonly named ``Service Item`` or ``Service Items``,
        but company administrators can rename fields. Prefer the known field
        identity first, then safely inspect other active managed-list timesheet
        fields and accept a result only when one field/item pair matches exactly.
        """
        expected_names = [self._clean(value) for value in names if self._clean(value)]
        if not expected_names:
            return {
                "status": "not_found",
                "customfield_id": None,
                "item_id": None,
                "item_name": None,
                "method": None,
            }

        fields = [
            row for row in self._load_customfields()
            if self._is_managed_timesheet_field(row)
        ]
        named_fields = [row for row in fields if self._is_named_service_item_field(row)]

        named_pairs = self._service_item_pairs(
            fields=named_fields,
            expected_names=expected_names,
        )
        unique_named_pairs = {
            (row["customfield_id"], row["item_id"]): row["item_name"]
            for row in named_pairs
        }
        if len(unique_named_pairs) == 1:
            (field_id, item_id), item_name = next(iter(unique_named_pairs.items()))
            return {
                "status": "matched",
                "customfield_id": field_id,
                "item_id": item_id,
                "item_name": item_name,
                "method": "named_service_field",
            }
        if len(unique_named_pairs) > 1:
            logger.warning(
                "[QuickBooksTimeMatcher] ambiguous service item in named fields | tenant_id=%s service_id=%s names=%s matches=%s",
                self.tenant_id,
                self.service_id,
                expected_names,
                len(unique_named_pairs),
            )
            return {
                "status": "ambiguous",
                "customfield_id": None,
                "item_id": None,
                "item_name": None,
                "method": "named_service_field",
            }

        fallback_fields = [row for row in fields if row not in named_fields]
        fallback_pairs = self._service_item_pairs(
            fields=fallback_fields,
            expected_names=expected_names,
        )
        unique_fallback_pairs = {
            (row["customfield_id"], row["item_id"]): row["item_name"]
            for row in fallback_pairs
        }
        if len(unique_fallback_pairs) == 1:
            (field_id, item_id), item_name = next(iter(unique_fallback_pairs.items()))
            logger.info(
                "[QuickBooksTimeMatcher] service item resolved from renamed managed-list field | tenant_id=%s service_id=%s customfield_id=%s item_id=%s",
                self.tenant_id,
                self.service_id,
                field_id,
                item_id,
            )
            return {
                "status": "matched",
                "customfield_id": field_id,
                "item_id": item_id,
                "item_name": item_name,
                "method": "managed_list_item_name",
            }
        if len(unique_fallback_pairs) > 1:
            logger.warning(
                "[QuickBooksTimeMatcher] service item name appears in multiple managed-list fields | tenant_id=%s service_id=%s names=%s matches=%s",
                self.tenant_id,
                self.service_id,
                expected_names,
                len(unique_fallback_pairs),
            )
            return {
                "status": "ambiguous",
                "customfield_id": None,
                "item_id": None,
                "item_name": None,
                "method": "managed_list_item_name",
            }

        named_field_ids = sorted(
            {
                self._external_id(row)
                for row in named_fields
                if self._external_id(row)
            }
        )
        return {
            "status": "not_found",
            "customfield_id": named_field_ids[0] if len(named_field_ids) == 1 else None,
            "item_id": None,
            "item_name": None,
            "method": "named_service_field" if named_field_ids else None,
        }

    def resolve_service_item_by_name(self, name: str) -> Dict[str, Optional[str]]:
        """Backward-compatible exact-name service-item lookup."""
        return self.resolve_service_item(names=[name])

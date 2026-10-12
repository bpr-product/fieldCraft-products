from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from source.Utils import QuickBooksAPIHandler as qbo_api


logger = logging.getLogger(__name__)


class QuickBooksProjectApiClient:
    """GraphQL client for the QuickBooks Project Management API."""

    PROJECT_STATUS_OPEN = "OPEN"
    DEFAULT_PAGE_SIZE = 100

    def graphql_endpoint(self, env: str) -> str:
        normalized = (env or "sandbox").strip().lower()
        if normalized == "production":
            return "https://qb.api.intuit.com/graphql"
        return "https://qb-sandbox.api.intuit.com/graphql"

    def create_project(
        self,
        *,
        access_token: str,
        env: str,
        realm_id: str,
        name: str,
        description: Optional[str],
        customer_id: str,
        assignee_id: Optional[str] = None,
        client_entity_id: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_assignee_id = str(assignee_id or "").strip()

        query = self._create_project_mutation(
            include_assignee=bool(resolved_assignee_id),
        )
        variables: Dict[str, Any] = {
            "name": str(name or "").strip(),
            "description": str(description).strip() if description else None,
            "status": self.PROJECT_STATUS_OPEN,
            "customer": {"id": str(customer_id)},
            "account": {"id": str(realm_id)},
        }
        if resolved_assignee_id:
            variables["assignee"] = {"id": resolved_assignee_id}
        resolved_client_entity_id = str(client_entity_id or "").strip()
        if resolved_client_entity_id:
            variables["client"] = {"id": resolved_client_entity_id}
        self._add_project_dates(variables, start_date=start_date, due_date=due_date)

        data = self._execute(
            access_token=access_token,
            env=env,
            query=query,
            variables=variables,
        )
        project = (data or {}).get("projectManagementCreateProject") or {}
        return project if isinstance(project, dict) else {}

    @staticmethod
    def _create_project_mutation(*, include_assignee: bool) -> str:
        assignee_variable = ""
        assignee_input = ""
        if include_assignee:
            assignee_variable = ",\n          $assignee: ProjectManagement_PersonaInput!"
            assignee_input = ",\n            assignee: $assignee"

        return f"""
        mutation ProjectManagementCreateProject(
          $name: String!,
          $description: String,
          $startDate: DateTime,
          $dueDate: DateTime,
          $status: ProjectManagement_Status,
          $customer: ProjectManagement_CustomerInput,
          $account: ProjectManagement_CompanyInput!,
          $client: ProjectManagement_ClientInput{assignee_variable}
        ) {{
          projectManagementCreateProject(input: {{
            name: $name,
            description: $description,
            startDate: $startDate,
            dueDate: $dueDate,
            status: $status,
            customer: $customer,
            account: $account,
            client: $client{assignee_input}
          }}) {{
            ... on ProjectManagement_Project {{
              id
              name
              description
              startDate
              dueDate
              status
              customer {{ id }}
              account {{ id }}
              client {{ id }}
              assignee {{ id }}
            }}
          }}
        }}
        """

    def get_projects_page(
        self,
        *,
        access_token: str,
        env: str,
        first: int = DEFAULT_PAGE_SIZE,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return one page of projects with only fields needed for assignee discovery."""
        page_size = min(max(int(first or self.DEFAULT_PAGE_SIZE), 1), self.DEFAULT_PAGE_SIZE)
        query = """
        query ProjectManagementProjectsForAssignee(
          $first: PositiveInt!,
          $after: String,
          $filter: ProjectManagement_ProjectFilter!,
          $orderBy: [ProjectManagement_OrderBy!]
        ) {
          projectManagementProjects(
            first: $first,
            after: $after,
            filter: $filter,
            orderBy: $orderBy
          ) {
            edges {
              node {
                id
                client { id }
                assignee { id }
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
        """
        variables: Dict[str, Any] = {
            "first": page_size,
            "filter": {},
            "orderBy": [],
        }
        resolved_after = str(after or "").strip()
        if resolved_after:
            variables["after"] = resolved_after

        data = self._execute(
            access_token=access_token,
            env=env,
            query=query,
            variables=variables,
        )
        connection = (data or {}).get("projectManagementProjects") or {}
        return connection if isinstance(connection, dict) else {}

    def can_access_projects(self, *, access_token: str, env: str) -> bool:
        self.get_projects_page(
            access_token=access_token,
            env=env,
            first=1,
        )
        return True

    def update_project(
        self,
        *,
        access_token: str,
        env: str,
        realm_id: str,
        project_id: str,
        name: str,
        description: Optional[str],
        customer_id: str,
        client_entity_id: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = """
        mutation ProjectManagementUpdateProject(
          $id: ID!,
          $name: String,
          $description: String,
          $status: ProjectManagement_Status,
          $startDate: DateTime,
          $dueDate: DateTime,
          $customer: ProjectManagement_CustomerInput,
          $account: ProjectManagement_CompanyInput,
          $client: ProjectManagement_ClientInput
        ) {
          projectManagementUpdateProject(input: {
            id: $id,
            name: $name,
            description: $description,
            status: $status,
            startDate: $startDate,
            dueDate: $dueDate,
            customer: $customer,
            account: $account,
            client: $client
          }) {
            ... on ProjectManagement_Project {
              id
              name
              description
              startDate
              dueDate
              status
              customer { id }
              account { id }
              client { id }
            }
          }
        }
        """
        variables: Dict[str, Any] = {
            "id": str(project_id),
            "name": str(name or "").strip() or None,
            "description": str(description).strip() if description else None,
            "status": self.PROJECT_STATUS_OPEN,
            "customer": {"id": str(customer_id)},
            "account": {"id": str(realm_id)},
        }
        resolved_client_entity_id = str(client_entity_id or "").strip()
        if resolved_client_entity_id:
            variables["client"] = {"id": resolved_client_entity_id}
        self._add_project_dates(variables, start_date=start_date, due_date=due_date)

        data = self._execute(
            access_token=access_token,
            env=env,
            query=query,
            variables=variables,
        )
        project = (data or {}).get("projectManagementUpdateProject") or {}
        return project if isinstance(project, dict) else {}

    def _execute(
        self,
        *,
        access_token: str,
        env: str,
        query: str,
        variables: Dict[str, Any],
    ) -> Dict[str, Any]:
        endpoint = self.graphql_endpoint(env)
        operation_name = self._operation_name(query)
        prepared_request = qbo_api.prepare_json_request(
            "POST",
            endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json_body={"query": query, "variables": variables},
        )

        logger.debug(
            "[QBOProjectAPI] request | operation=%s endpoint=%s",
            operation_name,
            endpoint,
        )
        response = qbo_api.send_prepared_request(prepared_request, timeout=30)
        parsed_payload = qbo_api.safe_json(response)
        payload = parsed_payload if isinstance(parsed_payload, dict) else {}

        self._log_full_response(
            operation_name=operation_name,
            response=response,
            parsed_payload=parsed_payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(response, "QBO Project API request failed"))

        errors = payload.get("errors") or []
        data = payload.get("data") or {}
        if isinstance(data, dict) and data.get("errors"):
            errors = data.get("errors")
        if errors:
            logger.error(
                "[QBOProjectAPI] GraphQL error | operation=%s intuit_tid=%s errors=%s",
                operation_name,
                self._response_header(response, "intuit_tid")
                or self._response_header(response, "intuit-tid"),
                json.dumps(errors, default=str)[:2000],
            )
            raise Exception(self._format_graphql_errors(errors))

        logger.debug(
            "[QBOProjectAPI] response | operation=%s status=%s",
            operation_name,
            response.status_code,
        )
        return data if isinstance(data, dict) else {}

    def _log_full_response(
        self,
        *,
        operation_name: str,
        response: Any,
        parsed_payload: Any,
    ) -> None:
        """Temporarily log the complete QuickBooks Project API response.

        This is intentionally centralized in ``_execute`` so it covers every
        Project GraphQL operation, including list/access checks, create, and
        update. OAuth tokens and request headers are never included.
        """
        response_body: str
        if parsed_payload is not None:
            try:
                response_body = json.dumps(
                    parsed_payload,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            except (TypeError, ValueError):
                response_body = str(parsed_payload)
        else:
            raw_body = getattr(response, "text", "")
            response_body = str(raw_body or "")

        status_code = getattr(response, "status_code", None)
        intuit_tid = (
            self._response_header(response, "intuit_tid")
            or self._response_header(response, "intuit-tid")
        )

        logger.info(
            "[QBOProjectAPI][FULL_RESPONSE_TEMP] operation=%s status=%s "
            "intuit_tid=%s response_body=\n%s",
            operation_name,
            status_code,
            intuit_tid,
            response_body,
        )

        if operation_name == "ProjectManagementCreateProject":
            logger.info(
                "[QBOProjectCreateAPI][FULL_RESPONSE_TEMP] status=%s "
                "intuit_tid=%s response_body=\n%s",
                status_code,
                intuit_tid,
                response_body,
            )

    @staticmethod
    def _response_header(response: Any, name: str) -> Optional[str]:
        headers = getattr(response, "headers", {}) or {}
        try:
            value = headers.get(name) or headers.get(name.lower()) or headers.get(name.title())
        except Exception:
            return None
        return str(value) if value is not None else None

    def _operation_name(self, query: str) -> str:
        raw = str(query or "")
        for marker in ("mutation ", "query "):
            idx = raw.find(marker)
            if idx >= 0:
                tail = raw[idx + len(marker):].strip()
                name = tail.split("(", 1)[0].split("{", 1)[0].strip()
                return name or "unknown"
        return "unknown"

    def _format_graphql_errors(self, errors: Any) -> str:
        if not isinstance(errors, list):
            return f"QBO Project API returned an error: {errors}"

        messages = []
        for item in errors:
            if not isinstance(item, dict):
                continue

            message = str(item.get("message") or "").strip()
            extensions = item.get("extensions") or {}
            error_code = extensions.get("errorCode")
            error_detail = str(extensions.get("errorDetail") or "").strip()

            parts = []
            if isinstance(error_code, str) and error_code.strip():
                parts.append(error_code.strip())
            elif isinstance(error_code, list):
                for code_item in error_code:
                    if not isinstance(code_item, dict):
                        continue
                    category = str(code_item.get("category") or "").strip()
                    detail = str(code_item.get("detail") or "").strip()
                    code_text = ": ".join(part for part in (category, detail) if part)
                    if code_text:
                        parts.append(code_text)

            if error_detail:
                parts.append(error_detail)
            if message and message.lower() != "errorcode":
                parts.insert(0, message)

            if parts:
                messages.append(" - ".join(parts))
            elif message:
                messages.append(message)

        return " | ".join(messages) or f"QBO Project API returned an error: {json.dumps(errors, default=str)[:500]}"

    def _add_project_dates(
        self,
        variables: Dict[str, Any],
        *,
        start_date: Optional[str],
        due_date: Optional[str],
    ) -> None:
        start_date_value = self._project_date(start_date)
        if start_date_value is not None:
            variables["startDate"] = start_date_value

        due_date_value = self._project_date(due_date)
        if due_date_value is not None:
            variables["dueDate"] = due_date_value

    def _project_date(self, value: Optional[str]) -> Optional[str]:
        parsed = self._parse_datetime(value)
        if parsed is None:
            return None
        return parsed.isoformat().replace("+00:00", "Z")

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

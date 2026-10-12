from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from source.quickbooks.QuickBooksProjectApiClient import QuickBooksProjectApiClient


logger = logging.getLogger(__name__)


class QuickBooksProjectAssigneeResolver:
    """Resolve a reusable assignee from existing QuickBooks projects.

    The connected account's projects are loaded at most once per resolver
    instance. A project belonging to the same client is preferred; otherwise,
    the first valid company assignee is reused.
    """

    PAGE_SIZE = 100
    MAX_PAGES = 100

    def __init__(self, project_api_client: QuickBooksProjectApiClient) -> None:
        self.project_api_client = project_api_client
        self._account_snapshots: Dict[
            Tuple[str, str],
            Tuple[Dict[str, str], str],
        ] = {}

    def resolve(
        self,
        *,
        access_token: str,
        env: str,
        preferred_client_entity_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> Optional[str]:
        normalized_env = str(env or "sandbox").strip().lower()
        normalized_account_id = str(account_id or "").strip()
        preferred_client_id = str(preferred_client_entity_id or "").strip()
        cache_key = (normalized_env, normalized_account_id)

        snapshot = self._account_snapshots.get(cache_key) if normalized_account_id else None
        if snapshot is None:
            snapshot = self._load_assignee_snapshot(
                access_token=access_token,
                env=normalized_env,
            )
            # Cache only a usable snapshot and only when the account is known.
            # Missing results are rechecked on a later call, which supports a user
            # assigning an existing project and immediately retrying.
            if normalized_account_id and snapshot[1]:
                self._account_snapshots[cache_key] = snapshot

        assignees_by_client, fallback_assignee_id = snapshot
        if preferred_client_id and preferred_client_id in assignees_by_client:
            logger.info(
                "[ProjectAssigneeResolver] selected assignee from matching client"
            )
            return assignees_by_client[preferred_client_id]

        if fallback_assignee_id and preferred_client_id:
            logger.info(
                "[ProjectAssigneeResolver] no assignee found for the matching client; using an existing company assignee"
            )
        return fallback_assignee_id or None

    def _load_assignee_snapshot(
        self,
        *,
        access_token: str,
        env: str,
    ) -> Tuple[Dict[str, str], str]:
        after: Optional[str] = None
        visited_cursors = set()
        assignees_by_client: Dict[str, str] = {}
        fallback_assignee_id = ""

        for _page_number in range(1, self.MAX_PAGES + 1):
            page = self.project_api_client.get_projects_page(
                access_token=access_token,
                env=env,
                first=self.PAGE_SIZE,
                after=after,
            )
            for edge in page.get("edges") or []:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    continue

                assignee = node.get("assignee") or {}
                assignee_id = str(assignee.get("id") or "").strip()
                if not assignee_id:
                    continue
                if not fallback_assignee_id:
                    fallback_assignee_id = assignee_id

                client = node.get("client") or {}
                client_id = str(client.get("id") or "").strip()
                if client_id and client_id not in assignees_by_client:
                    assignees_by_client[client_id] = assignee_id

            page_info = page.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break

            next_cursor = str(page_info.get("endCursor") or "").strip()
            if not next_cursor or next_cursor in visited_cursors:
                logger.warning(
                    "[ProjectAssigneeResolver] stopped project pagination because the next cursor was missing or repeated"
                )
                break
            visited_cursors.add(next_cursor)
            after = next_cursor
        else:
            logger.warning(
                "[ProjectAssigneeResolver] stopped after the safety limit of %s pages",
                self.MAX_PAGES,
            )

        return assignees_by_client, fallback_assignee_id

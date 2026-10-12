import logging
import time
from typing import Iterator

import requests

from external.connectors.base import BaseConnector
from external.config.schema import ConnectorConfig
from shared.external_utils.cursor_store import CursorStore

log = logging.getLogger(__name__)

_MAX_RETRIES = 3


class RestApiConnector(BaseConnector):
    def __init__(self, config: ConnectorConfig, cursor_store: CursorStore | None = None) -> None:
        super().__init__(config)
        self._store = cursor_store or CursorStore()
        self._token: str | None = None
        self._token_expiry: float = 0.0
        # Populated during fetch(); committed by _commit_cursor() after routing succeeds.
        self._current_cursor: str = ""
        self._new_cursor: str = ""
        self._cursor_tenant: str = ""

    @staticmethod
    def _cursor_gt(a: str, b: str) -> bool:
        """Return True if a > b, preferring numeric comparison for epoch values."""
        try:
            return float(a) > float(b)
        except (ValueError, TypeError):
            return a > b

    def _commit_cursor(self) -> None:
        """Persist the new cursor after all routing for this fetch has succeeded."""
        if self._new_cursor != self._current_cursor:
            self._store.set(
                self.config.source_name, self.config.source_entity,
                self._cursor_tenant, self._new_cursor,
            )
            log.info("Cursor advanced: %s → %s", self._current_cursor, self._new_cursor)

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        auth = self.config.auth
        resp = requests.post(
            auth.token_url,
            data={
                "grant_type":    "client_credentials",
                "client_id":     auth.client_id,
                "client_secret": auth.client_secret,
                "scope":         " ".join(auth.scopes),
            },
            timeout=self.config.poll.request_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        return self._token

    def _headers(self) -> dict:
        auth = self.config.auth
        if auth.type == "oauth2":
            return {"Authorization": f"Bearer {self._get_token()}"}
        if auth.type == "api_key":
            return {auth.api_key_header: auth.api_key_value}
        return {}

    def _get_page(self, params: dict, attempt: int = 0) -> requests.Response:
        try:
            resp = requests.get(
                self.config.poll.base_url,
                params=params,
                headers=self._headers(),
                timeout=self.config.poll.request_timeout_s,
            )
            if resp.status_code == 429 and attempt < _MAX_RETRIES:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                log.warning("Rate limited — waiting %ds (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                return self._get_page(params, attempt + 1)
            if resp.status_code == 401 and self.config.auth.type == "oauth2" and attempt == 0:
                self._token = None
                return self._get_page(params, attempt + 1)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt < _MAX_RETRIES:
                time.sleep(2 ** attempt)
                return self._get_page(params, attempt + 1)
            raise RuntimeError(f"API request failed after {_MAX_RETRIES} retries: {exc}") from exc

    def _extract_items(self, data: dict) -> list[dict]:
        field = self.config.poll.results_field
        for part in field.split("."):
            if not isinstance(data, dict):
                return []
            data = data.get(part, [])
        return data if isinstance(data, list) else []

    def _has_next(self, data: dict, page: int) -> bool:
        field = self.config.poll.next_page_field
        if field:
            return bool(data.get(field))
        return len(self._extract_items(data)) == self.config.poll.page_size

    def fetch(self) -> Iterator[dict]:
        poll   = self.config.poll
        tenant = self.config.resolved_tenantid
        cursor = self._store.get(
            self.config.source_name, self.config.source_entity, tenant,
            fallback=poll.initial_cursor,
        )
        self._current_cursor = cursor
        self._new_cursor     = cursor
        self._cursor_tenant  = tenant
        page = 1

        while page <= poll.max_pages:
            params = {
                poll.cursor_param:    cursor,
                poll.page_param:      page,
                poll.page_size_param: poll.page_size,
            }
            resp  = self._get_page(params)
            data  = resp.json()
            items = self._extract_items(data)

            for item in items:
                ts_val = str(item.get(poll.incremental_field or self.config.ts_field, ""))
                if ts_val and self._cursor_gt(ts_val, self._new_cursor):
                    self._new_cursor = ts_val
                yield item

            if not self._has_next(data, page):
                break
            page += 1

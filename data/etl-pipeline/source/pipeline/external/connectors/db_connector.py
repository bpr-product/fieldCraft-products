import logging
from typing import Iterator

from external.connectors.base import BaseConnector
from external.config.schema import ConnectorConfig
from shared.external_utils.cursor_store import CursorStore

log = logging.getLogger(__name__)


class DbConnector(BaseConnector):
    def __init__(self, config: ConnectorConfig, cursor_store: CursorStore | None = None) -> None:
        super().__init__(config)
        self._store = cursor_store or CursorStore()
        self._engine = None
        # Populated during fetch(); committed by _commit_cursor() after routing succeeds.
        self._current_wm: str = ""
        self._new_wm: str = ""
        self._wm_tenant: str = ""

    def _get_engine(self):
        if self._engine is None:
            from sqlalchemy import create_engine
            from sqlalchemy.engine import URL
            db = self.config.db
            # Build via URL.create rather than an f-string — SQLAlchemy's URL
            # object redacts the password in its repr/str/logging output by
            # default, whereas a plain string renders credentials verbatim in
            # any engine/exception traceback that logs the URL.
            url = URL.create(
                drivername=db.driver,
                username=db.username,
                password=db.password,
                host=db.host,
                port=db.port,
                database=db.database,
            )
            self._engine = create_engine(url, pool_pre_ping=True)
        return self._engine

    @staticmethod
    def _wm_gt(a: str, b: str) -> bool:
        """Return True if a > b, preferring numeric comparison for epoch watermarks."""
        try:
            return float(a) > float(b)
        except (ValueError, TypeError):
            return a > b

    def _commit_cursor(self) -> None:
        """Persist the new watermark after all routing for this fetch has succeeded."""
        if self._new_wm != self._current_wm:
            self._store.set(
                self.config.source_name, self.config.source_entity,
                self._wm_tenant, self._new_wm,
            )
            log.info("Watermark advanced: %s → %s", self._current_wm, self._new_wm)

    def fetch(self) -> Iterator[dict]:
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        db     = self.config.db
        tenant = self.config.resolved_tenantid
        wm     = self._store.get(
            self.config.source_name, self.config.source_entity, tenant,
            fallback=db.initial_watermark,
        )
        self._current_wm = wm
        self._new_wm     = wm
        self._wm_tenant  = tenant

        session = Session(self._get_engine())
        try:
            result = session.execute(text(db.query), params={"watermark": wm})
            cols   = list(result.keys())
            for row in result:
                record = dict(zip(cols, row))
                wm_val = str(record.get(db.watermark_field, ""))
                if wm_val and self._wm_gt(wm_val, self._new_wm):
                    self._new_wm = wm_val
                yield record
        finally:
            session.close()

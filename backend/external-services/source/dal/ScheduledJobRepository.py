import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

from source.dal.dbconnector import postgres_connection

from source.dal.QuickBooksRepository import QboBlobResultStore


logger = logging.getLogger(__name__)

def result_from_blob(*, tenant_id: str, result_value: Any) -> Any:
    """If scheduled_job.result contains a blob path, fetch and return the JSON payload."""
    if result_value is None:
        return None
    if isinstance(result_value, (dict, list)):
        return result_value
    if isinstance(result_value, str) and result_value.strip():
        try:
            return QboBlobResultStore().get_json(tenant_id=tenant_id, stored_path=result_value)
        except Exception:
            logger.exception(
                "scheduled job result could not be loaded | tenant_id=%s",
                tenant_id,
            )
            return {
                "ok": False,
                "message": "The sync result could not be loaded. Please retry the sync or contact support.",
                "errorCode": "JOB_RESULT_UNAVAILABLE",
            }
    return result_value


class ScheduledJobRepository:
    """Persist long-running sync status + last result in tenant DB (public.scheduled_job).

    This is best-effort because some tenant schemas might not have the table.
    """

    ACTIVE_STATUSES = ("RUNNING", "IN_PROGRESS", "STARTED", "PENDING")
    DONE_STATUSES = ("COMPLETED", "COMPLETED_WITH_ERRORS", "DONE", "FINISHED", "FAILED")
    WATERMARK_STATUSES = ("COMPLETED", "COMPLETED_WITH_ERRORS", "DONE", "FINISHED", "SUCCESS")

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _connect(self, tenant_id: str):
        conn = postgres_connection(tenant_id)
        if conn is None:
            raise Exception(f"Unable to connect to tenant database tenant_id={tenant_id}")
        return conn

    def _json_payload(self, payload: Any) -> Any:
        """Return a psycopg2-safe payload for json/jsonb columns (with fallback)."""
        try:
            return Json(payload)
        except Exception:
            return json.dumps(payload, default=str)

    def _maybe_parse_json(self, val: Any) -> Any:
        """Normalize json/jsonb/text-json fields to Python objects.

        Many tenant DB connections do not register psycopg2 JSON adapters, so
        json/jsonb columns may be returned as strings.
        """
        if val is None:
            return None

        if isinstance(val, (dict, list)):
            return val

        if isinstance(val, (bytes, bytearray)):
            try:
                val = val.decode("utf-8", errors="ignore")
            except Exception:
                return val

        if isinstance(val, str):
            s = val.strip()
            if not s:
                return val
            if s[0] in ("{", "[", '"'):
                try:
                    return json.loads(s)
                except Exception:
                    return val
        return val

    def create_job(
            self,
            *,
            tenant_id: str,
            created_by: str,
            job_type: str,
            additional_data: Optional[Dict[str, Any]] = None,
            status: str = "RUNNING",
            priority: int = 5,
            job_uuid: Optional[str] = None,
    ) -> int:
        conn = self._connect(tenant_id)
        cur = conn.cursor()
        now = self._now()
        job_uuid = (job_uuid or str(uuid4())).strip()
        try:
            cur.execute(
                """
                INSERT INTO scheduled_job (
                    additional_data,
                    created_by,
                    created_time,
                    job_type,
                    job_uuid,
                    priority,
                    start_time,
                    status,
                    scheduled_start_time,
                    repeated_job
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self._json_payload(additional_data or {}),
                    (created_by or ""),
                    now,
                    (job_type or ""),
                    job_uuid,
                    int(priority),
                    now,
                    (status or "RUNNING"),
                    now,
                    False,
                ),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                raise Exception("scheduled_job insert returned no id")
            return int(row[0])
        except psycopg2.Error:
            logger.exception(
                "scheduled job creation failed | tenant_id=%s job_type=%s",
                tenant_id,
                job_type,
            )
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def create_or_get_active_job(
            self,
            *,
            tenant_id: str,
            created_by: str,
            job_type: str,
            service_id: str,
            additional_data: Optional[Dict[str, Any]] = None,
            status: str = "RUNNING",
            priority: int = 5,
            job_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically reuse or create one active job per tenant/service/job type."""
        conn = self._connect(tenant_id)
        cur = conn.cursor()
        now = self._now()
        requested_uuid = (job_uuid or str(uuid4())).strip()
        service_text = str(service_id or "").strip()
        service_like = f'%"serviceId": "{service_text}"%'
        service_like_compact = f'%"serviceId":"{service_text}"%'
        lock_key = f"qbo-sync:{job_type}:{service_text}"
        try:
            conn.autocommit = False
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
            cur.execute(
                """
                SELECT id, status, created_time, job_uuid
                FROM scheduled_job
                WHERE job_type = %s
                  AND UPPER(status) = ANY(%s)
                  AND (CAST(additional_data AS TEXT) LIKE %s OR CAST(additional_data AS TEXT) LIKE %s)
                ORDER BY created_time DESC
                LIMIT 1
                """,
                (job_type, list(self.ACTIVE_STATUSES), service_like, service_like_compact),
            )
            active = cur.fetchone()
            if active:
                conn.commit()
                return {
                    "id": int(active[0]),
                    "status": active[1],
                    "createdTime": active[2].isoformat() if active[2] else None,
                    "jobUuid": active[3],
                    "reused": True,
                }

            cur.execute(
                """
                INSERT INTO scheduled_job (
                    additional_data, created_by, created_time, job_type, job_uuid,
                    priority, start_time, status, scheduled_start_time, repeated_job
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self._json_payload(additional_data or {}),
                    created_by or "",
                    now,
                    job_type or "",
                    requested_uuid,
                    int(priority),
                    now,
                    status or "RUNNING",
                    now,
                    False,
                ),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                raise Exception("scheduled_job insert returned no id")
            conn.commit()
            return {
                "id": int(row[0]),
                "status": status or "RUNNING",
                "createdTime": now.isoformat(),
                "jobUuid": requested_uuid,
                "reused": False,
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def update_job(
            self,
            *,
            tenant_id: str,
            job_id: int,
            status: Optional[str] = None,
            result: Optional[Any] = None,
            completed_time: Optional[datetime] = None,
    ) -> None:
        conn = self._connect(tenant_id)
        cur = conn.cursor()
        try:
            sets = []
            vals = []
            if status is not None:
                sets.append(sql.SQL("status = %s"))
                vals.append(status)
            if result is not None:
                sets.append(sql.SQL("result = %s"))
                if isinstance(result, (dict, list)):
                    vals.append(self._json_payload(result))
                else:
                    # Prefer storing the raw string for text columns; fallback below handles json/jsonb.
                    vals.append(str(result))
            if completed_time is not None:
                sets.append(sql.SQL("completed_time = %s"))
                vals.append(completed_time)

            if not sets:
                return

            vals.append(int(job_id))
            q = sql.SQL("UPDATE scheduled_job SET ") + sql.SQL(", ").join(sets) + sql.SQL(" WHERE id = %s")
            cur.execute(q, tuple(vals))
        except psycopg2.Error:
            # Retry storing result as text
            try:
                sets = []
                vals = []
                if status is not None:
                    sets.append("status = %s")
                    vals.append(status)
                if result is not None:
                    sets.append("result = %s")
                    # If result is a string and the column is json/jsonb, json.dumps wraps it correctly.
                    if isinstance(result, (dict, list)):
                        vals.append(json.dumps(result, default=str))
                    else:
                        vals.append(json.dumps(str(result), default=str))
                if completed_time is not None:
                    sets.append("completed_time = %s")
                    vals.append(completed_time)
                if not sets:
                    return
                vals.append(int(job_id))
                query = sql.SQL("UPDATE scheduled_job SET ") + sql.SQL(", ").join(
                    sql.SQL(fragment) for fragment in sets
                ) + sql.SQL(" WHERE id = %s")
                cur.execute(query, tuple(vals))
            except Exception:
                logger.exception(
                    "scheduled job update failed | tenant_id=%s job_id=%s",
                    tenant_id,
                    job_id,
                )
                raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def get_job_by_id(self, *, tenant_id: str, job_id: int) -> Optional[Dict[str, Any]]:
        conn = self._connect(tenant_id)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, job_type, status, created_by, created_time, start_time, completed_time, additional_data, result, job_uuid
                FROM scheduled_job
                WHERE id = %s
                """,
                (int(job_id),),
            )
            row = cur.fetchone()
            if not row:
                return None

            return {
                "id": int(row[0]),
                "jobType": row[1],
                "status": row[2],
                "createdBy": row[3],
                "createdTime": row[4].isoformat() if row[4] else None,
                "startTime": row[5].isoformat() if row[5] else None,
                "completedTime": row[6].isoformat() if row[6] else None,
                "additionalData": self._maybe_parse_json(row[7]),
                "result": self._maybe_parse_json(row[8]),
                "jobUuid": row[9],
            }
        except Exception:
            # Fallback if schema does not have job_uuid.
            try:
                cur.execute(
                    """
                    SELECT id, job_type, status, created_by, created_time, start_time, completed_time, additional_data, result
                    FROM scheduled_job
                    WHERE id = %s
                    """,
                    (int(job_id),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": int(row[0]),
                    "jobType": row[1],
                    "status": row[2],
                    "createdBy": row[3],
                    "createdTime": row[4].isoformat() if row[4] else None,
                    "startTime": row[5].isoformat() if row[5] else None,
                    "completedTime": row[6].isoformat() if row[6] else None,
                    "additionalData": self._maybe_parse_json(row[7]),
                    "result": self._maybe_parse_json(row[8]),
                    "jobUuid": None,
                }
            except Exception:
                logger.exception(
                    "scheduled job lookup failed | tenant_id=%s job_id=%s",
                    tenant_id,
                    job_id,
                )
                raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def get_active_job(self, *, tenant_id: str, job_type: str, created_by: str, service_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        conn = self._connect(tenant_id)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, status, created_time
                FROM scheduled_job
                WHERE job_type = %s
                  AND UPPER(status) = ANY(%s)
                ORDER BY created_time DESC
                LIMIT 1
                """,
                (job_type, list(self.ACTIVE_STATUSES)),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": int(row[0]),
                "status": row[1],
                "createdTime": row[2].isoformat() if row[2] else None,
            }
        except Exception:
            # Some schemas may store status differently. Try a simpler query.
            try:
                cur.execute(
                    """
                    SELECT id, status, created_time
                    FROM scheduled_job
                    WHERE job_type = %s
                      AND created_by = %s
                      AND status IN ('RUNNING','IN_PROGRESS','STARTED','PENDING')
                    ORDER BY created_time DESC
                    LIMIT 1
                    """,
                    (job_type, created_by),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": int(row[0]),
                    "status": row[1],
                    "createdTime": row[2].isoformat() if row[2] else None,
                }
            except Exception:
                logger.exception(
                    "active scheduled job lookup failed | tenant_id=%s job_type=%s",
                    tenant_id,
                    job_type,
                )
                raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def get_last_done_job(self, *, tenant_id: str, job_type: str, created_by: str) -> Optional[Dict[str, Any]]:
        conn = self._connect(tenant_id)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, status, completed_time, created_time, result
                FROM scheduled_job
                WHERE job_type = %s
                  AND UPPER(status) = ANY(%s)
                ORDER BY COALESCE(completed_time, created_time) DESC
                LIMIT 1
                """,
                (job_type, list(self.DONE_STATUSES)),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": int(row[0]),
                "status": row[1],
                "completedTime": (row[2] or row[3]).isoformat() if (row[2] or row[3]) else None,
                "result": self._maybe_parse_json(row[4]),
            }
        except Exception:
            logger.exception(
                "last completed scheduled job lookup failed | tenant_id=%s job_type=%s",
                tenant_id,
                job_type,
            )
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
    def get_last_job_start_time(
            self,
            *,
            tenant_id: str,
            job_type: str,
            service_id: str,
            exclude_job_id: Optional[int] = None,
            statuses: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Return the latest matching sync start time as an ISO string.

        This is the CDC watermark. We use the module sync start time instead of
        completed time so records changed during a running sync are still picked
        up on the next run.
        """
        conn = self._connect(tenant_id)
        cur = conn.cursor()
        status_values = [s.upper() for s in (statuses or list(self.WATERMARK_STATUSES))]
        service_id_text = str(service_id or "").strip()
        service_like = f'%"serviceId": "{service_id_text}"%'
        service_like_compact = f'%"serviceId":"{service_id_text}"%'
        try:
            query = """
                SELECT COALESCE(start_time, created_time)
                FROM scheduled_job
                WHERE job_type = %s
                  AND UPPER(status) = ANY(%s)
            """
            params = [job_type, status_values]

            if exclude_job_id is not None:
                query += " AND id <> %s"
                params.append(int(exclude_job_id))

            if service_id_text:
                query += " AND (CAST(additional_data AS TEXT) LIKE %s OR CAST(additional_data AS TEXT) LIKE %s)"
                params.extend([service_like, service_like_compact])

            query += " ORDER BY COALESCE(start_time, created_time) DESC LIMIT 1"
            cur.execute(query, tuple(params))
            row = cur.fetchone()
            if row and row[0]:
                return row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])

            fallback_query = """
                SELECT COALESCE(start_time, created_time)
                FROM scheduled_job
                WHERE job_type = %s
                  AND UPPER(status) = ANY(%s)
            """
            fallback_params = [job_type, status_values]
            if exclude_job_id is not None:
                fallback_query += " AND id <> %s"
                fallback_params.append(int(exclude_job_id))
            fallback_query += " ORDER BY COALESCE(start_time, created_time) DESC LIMIT 1"

            cur.execute(fallback_query, tuple(fallback_params))
            row = cur.fetchone()
            if row and row[0]:
                return row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])
            return None
        except Exception:
            logger.exception(
                "last scheduled job start lookup failed | tenant_id=%s job_type=%s",
                tenant_id,
                job_type,
            )
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def update_job_additional_data(
            self,
            *,
            tenant_id: str,
            job_id: int,
            updates: Dict[str, Any],
    ) -> None:
        """Deep-merge `updates` into scheduled_job.additional_data for the given job.

        Fetches the current value, merges at one level deep (nested dicts are merged,
        not replaced), then writes back. Best-effort — silently swallows errors so
        timing updates never break the sync flow.
        """
        if not updates:
            return
        conn = None
        cur = None
        try:
            conn = self._connect(tenant_id)
            cur = conn.cursor()
            cur.execute(
                "SELECT additional_data FROM scheduled_job WHERE id = %s",
                (int(job_id),),
            )
            row = cur.fetchone()
            current: Dict[str, Any] = {}
            if row and row[0]:
                current = self._maybe_parse_json(row[0]) or {}
            if not isinstance(current, dict):
                current = {}

            # Deep-merge one level: nested dicts are merged, scalars are overwritten.
            for key, value in updates.items():
                if isinstance(value, dict) and isinstance(current.get(key), dict):
                    current[key] = {**current[key], **value}
                else:
                    current[key] = value

            try:
                cur.execute(
                    "UPDATE scheduled_job SET additional_data = %s WHERE id = %s",
                    (self._json_payload(current), int(job_id)),
                )
            except psycopg2.Error:
                cur.execute(
                    "UPDATE scheduled_job SET additional_data = %s WHERE id = %s",
                    (json.dumps(current, default=str), int(job_id)),
                )
        except Exception:
            logger.exception(
                "scheduled job metadata update failed | tenant_id=%s job_id=%s",
                tenant_id,
                job_id,
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def get_last_successful_module_sync_start(self, *, tenant_id: str, job_type: str, service_id: str) -> Optional[datetime]:
        """Return the latest successful module-sync start time for a tenant/service."""
        last_start_time = self.get_last_job_start_time(
            tenant_id=tenant_id,
            job_type=job_type,
            service_id=service_id,
        )
        if not last_start_time:
            return None
        if isinstance(last_start_time, datetime):
            return last_start_time
        try:
            return datetime.fromisoformat(str(last_start_time).replace("Z", "+00:00"))
        except Exception:
            return None

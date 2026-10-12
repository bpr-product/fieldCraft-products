from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator

logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def acquire_transaction_lock(session: Session, lock_key: str) -> None:
    """Serialize one logical operation for the current PostgreSQL transaction.

    PostgreSQL advisory transaction locks require no table, column, index, or
    migration. The lock is released automatically when the transaction ends.
    Non-PostgreSQL test databases skip the lock.

    Only for critical sections that stay inside the database. When the section
    makes outbound HTTP calls, use :func:`tenant_advisory_lock` instead so the
    transaction is not held open for the duration of the network round trip.
    """
    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        return
    bind = get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": str(lock_key)},
    )


def acquire_transaction_locks(session: Session, lock_keys: Iterable[str]) -> None:
    """Acquire multiple transaction locks in deterministic order.

    Mapping claim paths use more than one logical identity lock. Sorting and
    de-duplicating the keys in one shared helper keeps lock ordering consistent
    across customer and worker claims and avoids avoidable deadlock risk.
    """
    normalized_keys = sorted({str(key or "").strip() for key in lock_keys if str(key or "").strip()})
    for lock_key in normalized_keys:
        acquire_transaction_lock(session, lock_key)


_customer_engine = None
_customer_session_local = None
_service_engine = None
_service_session_local = None
_tenant_engines: Dict[str, object] = {}
_tenant_session_factories: Dict[str, object] = {}


def _database_port() -> int:
    return int((os.getenv("DATABASE_PORT") or "5432").strip())


def _build_customer_db_url() -> URL:
    user = (os.getenv("CUSTOMER_DB_USERNAME") or "").strip()
    password = os.getenv("CUSTOMER_DB_PASSWORD") or ""
    host = (os.getenv("CUSTOMER_DB_URL") or "localhost").strip()
    dbname = (os.getenv("CUSTOMER_DATABASE") or "").strip()
    if not dbname:
        raise RuntimeError("CUSTOMER_DATABASE is not configured")

    logger.debug("[SQLAlchemy][customer] host=%s user=%s database=%s port=%s", host, user, dbname, _database_port())

    return URL.create(
        drivername="postgresql+psycopg2",
        username=user,
        password=password,
        host=host,
        port=_database_port(),
        database=dbname,
    )


def _build_service_db_url() -> URL:
    user = (os.getenv("SERVICE_DB_USERNAME") or "").strip()
    password = os.getenv("SERVICE_DB_PASSWORD") or ""
    host = (os.getenv("SERVICE_DB_URL") or "localhost").strip()
    dbname = (os.getenv("SERVICE_DATABASE") or "").strip()
    if not dbname:
        raise RuntimeError("SERVICE_DATABASE is not configured")

    logger.debug("[SQLAlchemy][service] host=%s user=%s database=%s port=%s", host, user, dbname, _database_port())

    return URL.create(
        drivername="postgresql+psycopg2",
        username=user,
        password=password,
        host=host,
        port=_database_port(),
        database=dbname,
    )


def get_service_engine():
    global _service_engine
    if _service_engine is None:
        logger.info("[SQLAlchemy] Creating service engine")
        _service_engine = create_engine(
            _build_service_db_url(),
            future=True,
            pool_pre_ping=True,
            echo=False,
        )
    return _service_engine


def get_service_session_factory():
    global _service_session_local
    if _service_session_local is None:
        _service_session_local = sessionmaker(
            bind=get_service_engine(),
            autoflush=False,
            autocommit=False,
            future=True,
        )
    return _service_session_local


@contextmanager
def service_session_scope() -> Iterator[Session]:
    session = get_service_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build_tenant_db_url(tenant_id: str) -> URL:
    tenant_name = (tenant_id or "").strip()
    if not tenant_name:
        raise RuntimeError("tenant_id is required")

    user = (os.getenv("TENANT_DB_USERNAME") or "").strip()
    password = os.getenv("TENANT_DB_PASSWORD") or ""
    host = (os.getenv("TENANT_DB_URL") or "localhost").strip()

    logger.debug("[SQLAlchemy][tenant] host=%s user=%s database=%s port=%s", host, user, tenant_name, _database_port())

    return URL.create(
        drivername="postgresql+psycopg2",
        username=user,
        password=password,
        host=host,
        port=_database_port(),
        database=tenant_name,
    )


def get_customer_engine():
    global _customer_engine
    if _customer_engine is None:
        logger.info("[SQLAlchemy] Creating customer engine")
        _customer_engine = create_engine(
            _build_customer_db_url(),
            future=True,
            pool_pre_ping=True,
            echo=False,
        )
    return _customer_engine


def get_customer_session_factory():
    global _customer_session_local
    if _customer_session_local is None:
        _customer_session_local = sessionmaker(
            bind=get_customer_engine(),
            autoflush=False,
            autocommit=False,
            future=True,
        )
    return _customer_session_local


@contextmanager
def customer_session_scope() -> Iterator[Session]:
    session = get_customer_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_tenant_engine(tenant_id: str):
    tenant_name = (tenant_id or "").strip()
    if not tenant_name:
        raise RuntimeError("tenant_id is required")
    engine = _tenant_engines.get(tenant_name)
    if engine is None:
        logger.info("[SQLAlchemy] Creating tenant engine for %s", tenant_name)
        engine = create_engine(
            _build_tenant_db_url(tenant_name),
            future=True,
            pool_pre_ping=True,
            echo=False,
        )
        _tenant_engines[tenant_name] = engine
    return engine


def get_tenant_session_factory(tenant_id: str):
    tenant_name = (tenant_id or "").strip()
    if not tenant_name:
        raise RuntimeError("tenant_id is required")
    factory = _tenant_session_factories.get(tenant_name)
    if factory is None:
        factory = sessionmaker(
            bind=get_tenant_engine(tenant_name),
            autoflush=False,
            autocommit=False,
            future=True,
        )
        _tenant_session_factories[tenant_name] = factory
    return factory


@contextmanager
def tenant_session_scope(tenant_id: str) -> Iterator[Session]:
    session = get_tenant_session_factory(tenant_id)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_ADVISORY_LOCK_SQL = "SELECT pg_advisory_lock(hashtextextended(:lock_key, 0))"
_ADVISORY_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"


@contextmanager
def tenant_advisory_lock(tenant_id: str, lock_key: str) -> Iterator[None]:
    """Serialize one logical operation without holding a transaction open.

    Companion to :func:`acquire_transaction_lock` for callers whose critical
    section makes outbound HTTP calls. The lock is *session* scoped rather than
    transaction scoped: it is taken on a dedicated connection whose transaction is
    committed immediately, so the caller body runs with no open transaction, no
    held snapshot, and no row locks, while still being mutually exclusive against
    other workers. PostgreSQL releases the lock if the connection dies, so a
    crashed worker cannot strand it. Non-PostgreSQL test databases skip the lock.
    """
    engine = get_tenant_engine(tenant_id)
    if getattr(getattr(engine, "dialect", None), "name", "") != "postgresql":
        yield
        return

    params = {"lock_key": str(lock_key)}
    connection = engine.connect()
    locked = False
    try:
        connection.execute(text(_ADVISORY_LOCK_SQL), params)
        locked = True
        # A session-scoped advisory lock outlives this commit; the transaction does not.
        connection.commit()
        yield
    finally:
        released = False
        if locked:
            try:
                connection.execute(text(_ADVISORY_UNLOCK_SQL), params)
                connection.commit()
                released = True
            except Exception:
                logger.exception("[SQLAlchemy] advisory unlock failed | lock_key=%s", lock_key)
        if locked and not released:
            # Never hand a connection that may still own the lock back to the pool.
            connection.invalidate()
        connection.close()

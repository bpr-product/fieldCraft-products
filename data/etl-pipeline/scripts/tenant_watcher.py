#!/usr/bin/env python3
"""
tenant_watcher.py — Auto-onboard new tenants into the pipeline.

Polls fieldcraft_provider.public.tenant every POLL_INTERVAL_S seconds.
When a new Active tenant appears it:
  1. Deploys a Debezium connector for the tenant DB (via Kafka Connect REST).
  2. Runs Silver DDL to create any missing silver.* tables from the new tenant.

Designed to run as a long-lived Docker service or Kubernetes Deployment.
Safe to restart — all operations are idempotent.

Environment variables:
  POSTGRES_HOST / PORT / USER / PASSWORD / SSL_MODE  — Azure PostgreSQL
  SILVER_PG_HOST / PORT / DB / USER / PASSWORD       — Silver fieldcraft_silver
  KAFKA_CONNECT_URL                                  — Kafka Connect REST
  ETL_ENV                                            — local / staging / prod
  TENANT_WATCHER_POLL_INTERVAL_S                     — default 300 (5 min)
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg2
import requests

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))
from shared.pipeline_config import PG_SCHEMA  # noqa: E402

# Multi-env config (optional — graceful fallback when missing).
#
# IMPORTANT: this watcher's own run() loop below is single-env-only in
# practice, despite loading this config -- it always calls _get_active_tenants()
# /_deploy_connector() with no env_name, so it only ever polls whichever
# environment POSTGRES_HOST/ETL_ENV happens to point at. _build_connector_config()
# DOES correctly honor env_name when one is passed (see its env_name-aware
# branch below), which is what makes it *look* multi-env-capable from this
# import alone -- but nothing in this file ever passes one. If you need a
# poll-based watcher that covers every enabled environment, either add a loop
# here over _env_cfg.enabled_envs() (threading env_name through the calls
# above the same way), or use tenant_onboarding_job.py instead, which already
# does this correctly by threading env_name = event.get('_s_env') or ETL_ENV
# per real-time CDC event rather than polling.
try:
    from shared.env_config.schema import load_environments
    from shared.pipeline_config import ENVIRONMENTS_CONFIG_PATH
    _env_cfg = load_environments(os.environ.get("ENVIRONMENTS_CONFIG_PATH", ENVIRONMENTS_CONFIG_PATH))
    _HAS_ENV_CONFIG = True
except Exception:
    _env_cfg = None
    _HAS_ENV_CONFIG = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# Load .env.local if present (local dev)
_ENV_FILE = _HERE.parent / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

POLL_INTERVAL_S = int(os.environ.get("TENANT_WATCHER_POLL_INTERVAL_S", "300"))
# ── Debezium heartbeat (WAL-retention control) ───────────────────────────────
# Shared by every connector this file builds. See the long comment at the first
# use site for why disabling heartbeats caused unbounded WAL growth on the
# source Postgres.
_HEARTBEAT_MS = os.environ.get("ETL_DEBEZIUM_HEARTBEAT_MS", "30000")
# Matches the debezium_heartbeat table that already exists in the tenant
# databases: (id integer, last_heartbeat timestamptz, heartbeat_count bigint,
# created_at timestamptz) -- a single-row counter, one row per database, keyed
# on id. Debezium executes this string VERBATIM (no placeholder expansion), so
# it must match the real column names exactly or every heartbeat errors.
#
# One row per database is sufficient: a logical replication slot belongs to a
# single database, so the row's only job is to generate WAL in that database so
# the slot's restart_lsn can advance. It doubles as a liveness view --
# last_heartbeat is the last beat, heartbeat_count counts them.
#
# Override with ETL_DEBEZIUM_HEARTBEAT_QUERY if a database's table differs.
# "{slot}" (if present) is substituted with the connector's slot name.
_HEARTBEAT_QUERY_TEMPLATE = os.environ.get(
    "ETL_DEBEZIUM_HEARTBEAT_QUERY",
    "INSERT INTO public.debezium_heartbeat (id, last_heartbeat, heartbeat_count, created_at) "
    "VALUES (1, now(), 1, now()) "
    "ON CONFLICT (id) DO UPDATE SET "
    "last_heartbeat = now(), "
    "heartbeat_count = public.debezium_heartbeat.heartbeat_count + 1",
)


def _heartbeat_config(slot_name: str, etl_env: str) -> dict:
    """Full heartbeat block for one connector.

    heartbeat.action.query is OMITTED unless a target table is known to exist.
    public.debezium_heartbeat is NOT created by this pipeline -- the source
    databases are owned elsewhere (note flyway_schema_history alongside it in
    BASELINE_TABLE_EXCLUDES), so we consume the table where it already exists
    and never issue DDL against them.

    That matters because Debezium runs the action query verbatim on every
    heartbeat: pointing it at a missing table turns each beat into an error.
    Set ETL_DEBEZIUM_HEARTBEAT_QUERY="" for a deployment whose databases lack
    the table -- heartbeats still fire and still advance the slot, they just
    stop writing the extra row. The action query is the belt-and-braces half,
    only strictly needed when the captured tables are completely idle.
    """
    cfg = {"heartbeat.interval.ms": _HEARTBEAT_MS}
    query = _heartbeat_query(slot_name)
    if query.strip():
        cfg["heartbeat.action.query"] = query
    cfg.update(_heartbeat_route(etl_env))
    return cfg


def _heartbeat_query(slot_name: str) -> str:
    """Heartbeat statement for one connector.

    Debezium performs NO placeholder expansion inside heartbeat.action.query --
    it executes the string verbatim -- so any "{slot}" in an overriding
    ETL_DEBEZIUM_HEARTBEAT_QUERY is substituted here, at deploy time.

    The default query does not use {slot}: the tenant databases' existing
    debezium_heartbeat table is a single-row counter keyed on id, and a logical
    replication slot belongs to exactly one database, so one row per database
    already gives one row per slot in practice.
    """
    return _HEARTBEAT_QUERY_TEMPLATE.replace("{slot}", slot_name)


def _heartbeat_route(etl_env: str) -> dict:
    r"""RegexRouter rule collapsing every __debezium-heartbeat.* topic onto one hub.

    The main `route` transform only matches r"(.*)\.public\.(.*)", which a
    heartbeat topic never does -- so without this each connector would create
    its own hub and hit the Event Hubs Standard 10-hub cap. Bronze skips this
    topic automatically (its _EXCLUDE_TOPIC regex covers r'\.pipeline\.').
    """
    return {
        "transforms.hbroute.type": "org.apache.kafka.connect.transforms.RegexRouter",
        "transforms.hbroute.regex": r"__debezium-heartbeat\.(.*)",
        "transforms.hbroute.replacement": f"{etl_env}.pipeline.heartbeat",
    }

KAFKA_CONNECT_URL = os.environ.get("KAFKA_CONNECT_URL", "http://localhost:8083")
ETL_ENV = os.environ.get("ETL_ENV", "local")

try:
    from shared.env_config.schema import source_creds as _sc
    _src = _sc()
    PG_HOST, PG_PORT, PG_USER = _src.host, _src.port, _src.user
    PG_PASSWORD, PG_SSL = _src.password, _src.ssl_mode
    _PG_DBNAME = _src.provider_db
except Exception:
    PG_HOST = os.environ["POSTGRES_HOST"]
    PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
    PG_USER = os.environ["POSTGRES_USER"]
    PG_PASSWORD = os.environ["POSTGRES_PASSWORD"]
    PG_SSL = os.environ.get("POSTGRES_SSL_MODE", "require")
    _PG_DBNAME = os.environ.get("PG_DBNAME", "")
_TENANT_TABLE_STATUS = os.environ.get("TENANT_ACTIVE_STATUS", "Active")


_CONNECT_TIMEOUT = int(os.environ.get("SOURCE_PG_CONNECT_TIMEOUT_S", "15"))


def _provider_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        dbname=_PG_DBNAME,
        sslmode=PG_SSL,
        connect_timeout=_CONNECT_TIMEOUT,
    )


def _get_active_tenants() -> set[str]:
    conn = _provider_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT tenant_id FROM {PG_SCHEMA}.tenant WHERE status = %s",
        (_TENANT_TABLE_STATUS,),
    )
    tenants = {r[0] for r in cur.fetchall()}
    cur.close()
    conn.close()
    return tenants


def _get_deployed_connectors() -> set[str]:
    try:
        r = requests.get(f"{KAFKA_CONNECT_URL}/connectors", timeout=10)
        if r.status_code < 400:
            return set(r.json())
    except Exception as exc:
        log.warning("Could not reach Kafka Connect: %s", exc)
    return set()


def _connector_name(tenant_id: str, env_name: str = "") -> str:
    env_label = env_name or ETL_ENV
    return f"fieldcraft-{env_label}-tenant-{tenant_id}"


def _build_connector_config(tenant_id: str, env_name: str = "") -> dict:
    env_label = env_name or ETL_ENV
    connector_name = _connector_name(tenant_id, env_name)
    slot_name = f"fieldcraft_slot_{env_label}_{tenant_id}"[:63]

    if _HAS_ENV_CONFIG and env_name and _env_cfg:
        extra = _env_cfg.connector_props(env_name)
        env_spec = _env_cfg.environments.get(env_name)
        db_host = env_spec.source.host if env_spec else PG_HOST
        db_port = env_spec.source.port if env_spec else PG_PORT
        db_user = env_spec.source.user if env_spec else PG_USER
        db_password = env_spec.source.password if env_spec else PG_PASSWORD
        ssl_mode = (env_spec.source.ssl_mode if env_spec else None) or PG_SSL
    else:
        extra = {
            "table.exclude.list": (
                "public.flyway_schema_history,public.debezium_heartbeat,"
                "public.invoice_id,public.tenant"
            ),
            "column.exclude.list": "public.trade.trade_logo,public.tenant_configuration.data",
            "snapshot.mode": "initial",
        }
        db_host = PG_HOST
        db_port = PG_PORT
        db_user = PG_USER
        db_password = PG_PASSWORD
        ssl_mode = PG_SSL

    config: dict = {
        "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
        "database.hostname": db_host,
        "database.port": str(db_port),
        "database.user": db_user,
        # NEVER send the plaintext password in this config PUT — Kafka Connect's
        # REST API echoes connector configs (including secrets) back verbatim on
        # GET /connectors/{name}/config to any caller who can reach it, and this
        # PUT itself has no Authorization header. Use a Kafka Connect
        # FileConfigProvider reference instead; the real secret must be
        # provisioned out of band into the Connect worker's
        # /opt/kafka/connect-secrets.properties file (never sent over this API).
        "database.password": f"${{file:/opt/kafka/connect-secrets.properties:{connector_name}_db_password}}",
        "database.dbname": tenant_id,
        "database.server.name": f"{env_label}.{tenant_id}",
        "plugin.name": "pgoutput",
        "publication.name": "fieldcraft_cdc_publication",
        "publication.autocreate.mode": "all_tables",
        "slot.name": slot_name,
        "key.converter": "org.apache.kafka.connect.json.JsonConverter",
        "key.converter.schemas.enable": "false",
        "value.converter": "org.apache.kafka.connect.json.JsonConverter",
        "value.converter.schemas.enable": "false",
        "transforms": "unwrap,dropTenantId,addTenantId,addEnv,route,hbroute",
        "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
        "transforms.unwrap.add.fields": "op,source.lsn:lsn,source.ts_ms:ts_ms,source.table:source_table,source.db:source_db",
        "transforms.unwrap.add.fields.prefix": "",
        "transforms.unwrap.delete.handling.mode": "rewrite",
        "transforms.unwrap.drop.tombstones": "true",
        "transforms.dropTenantId.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
        "transforms.dropTenantId.exclude": "tenantid",
        "transforms.addTenantId.type": "org.apache.kafka.connect.transforms.InsertField$Value",
        "transforms.addTenantId.static.field": "tenantid",
        "transforms.addTenantId.static.value": tenant_id,
        "transforms.addEnv.type": "org.apache.kafka.connect.transforms.InsertField$Value",
        "transforms.addEnv.static.field": "_s_env",
        "transforms.addEnv.static.value": env_label,
        "transforms.route.type": "org.apache.kafka.connect.transforms.RegexRouter",
        "transforms.route.regex": r"(.*)\.public\.(.*)",
        "transforms.route.replacement": f"{ETL_ENV}.cdc.events",
        "topic.prefix": f"{ETL_ENV}.{env_label}.{tenant_id}",
        "decimal.handling.mode": "string",
        "time.precision.mode": "connect",
        # See the long note in deploy_connectors.py:_build_env_provider_connector.
        # Disabled heartbeats let the slot's restart_lsn freeze while the WAL
        # behind it grew unbounded (11 GB pinned with only 16 MB of decode lag,
        # measured 2026-08-17). hbroute collapses every connector's heartbeat
        # topic onto one shared hub, so this no longer costs an Event Hub each.
        **_heartbeat_config(slot_name, ETL_ENV),
        "database.sslmode": ssl_mode,
        "tasks.max": "1",
        "producer.override.max.request.size": "1000000",
        "producer.override.batch.size": "131072",
        "producer.override.linger.ms": "10",
        **extra,
    }
    return {"name": _connector_name(tenant_id, env_name), "config": config}


def _deploy_connector(tenant_id: str, env_name: str = "") -> bool:
    name = _connector_name(tenant_id, env_name)
    config = _build_connector_config(tenant_id, env_name)
    try:
        r = requests.put(
            f"{KAFKA_CONNECT_URL}/connectors/{name}/config",
            json=config["config"],
            timeout=15,
        )
        if r.status_code in (200, 201):
            # KIP-875: a previously-STOPPED connector stays STOPPED after a config PUT.
            # Resume un-stops/un-pauses it; restart picks up any failed tasks.
            try:
                requests.put(f"{KAFKA_CONNECT_URL}/connectors/{name}/resume", timeout=10)
                requests.post(
                    f"{KAFKA_CONNECT_URL}/connectors/{name}/restart?includeTasks=true&onlyFailed=true",
                    timeout=10,
                )
            except requests.RequestException as exc:
                log.warning("Could not ensure %s is RUNNING: %s", name, exc)
            log.info("Deployed and resumed connector: %s", name)
            return True
        log.error("Connector deploy failed %s: %s %s", name, r.status_code, r.text[:200])
    except Exception as exc:
        log.error("Connector deploy error %s: %s", name, exc)
    return False


def _run_silver_ddl() -> None:
    ddl_script = _HERE.parent / "silver" / "ddl" / "create_silver_tables.py"
    log.info("Running Silver DDL for new tenant schema...")
    result = subprocess.run(
        [sys.executable, str(ddl_script)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        log.info("Silver DDL complete:\n%s", result.stdout[-1000:])
    else:
        log.error("Silver DDL failed:\n%s\n%s", result.stdout[-500:], result.stderr[-500:])


def run() -> None:
    log.info("Tenant watcher starting — poll interval=%ds", POLL_INTERVAL_S)
    known: set[str] = set()

    # Seed known from currently deployed connectors so we don't re-deploy on restart.
    # Match both legacy "fieldcraft-tenant-{tid}" and new "fieldcraft-{env}-tenant-{tid}" patterns.
    deployed = _get_deployed_connectors()
    env_label = ETL_ENV
    _new_prefix = f"fieldcraft-{env_label}-tenant-"
    _legacy_prefix = "fieldcraft-tenant-"
    for c in deployed:
        if c.startswith(_new_prefix):
            known.add(c[len(_new_prefix):])
        elif c.startswith(_legacy_prefix):
            known.add(c[len(_legacy_prefix):])

    log.info("Already deployed tenant connectors: %s", sorted(known))

    while True:
        try:
            active = _get_active_tenants()
            new = active - known
            removed = known - active

            if new:
                log.info("New tenants detected: %s", sorted(new))
                for tenant_id in sorted(new):
                    if _deploy_connector(tenant_id):
                        known.add(tenant_id)
                # Re-run Silver DDL once for all new tenants
                _run_silver_ddl()

            if removed:
                log.info(
                    "Tenants no longer Active (connectors NOT removed automatically): %s",
                    sorted(removed),
                )
                known -= removed

        except Exception as exc:
            log.error("Watcher poll error: %s", exc)

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    run()

"""
shared/env_config/schema.py — Pydantic model for config/environments.yaml.

One declarative file controls every environment and every CDC rule.
Editing it (and re-running the connector deploy) is the entire workflow
for adding an environment, turning CDC on/off, and configuring table/field rules.

Usage:
    from shared.env_config.schema import load_environments, EnvironmentsConfig

    cfg = load_environments()          # reads ENVIRONMENTS_CONFIG_PATH
    for env_name, spec in cfg.enabled_envs():
        excludes = cfg.env_table_excludes(env_name)
        col_excludes = cfg.env_column_excludes(env_name)
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

from shared.config_utils import resolve_dict

# ── System-baseline excludes — always merged, cannot be dropped ───────────────
BASELINE_TABLE_EXCLUDES: frozenset[str] = frozenset({
    "flyway_schema_history",
    "debezium_heartbeat",
    "invoice_id",
    "tenant",          # provider-authoritative — never captured from tenant DBs
})

# table -> columns always excluded regardless of env config
BASELINE_COLUMN_EXCLUDES: dict[str, list[str]] = {
    "trade":                ["trade_logo"],          # 1.47 MB binary > 1 MB Event Hubs cap
    "tenant_configuration": ["data"],                # can exceed 1 MB
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class SourceSpec(BaseModel):
    host:             str
    port:             int = 5432
    user:             str
    password:         str
    provider_db:      str = "fieldcraft_provider"
    ssl_mode:         str = "require"
    publication:      str = "fieldcraft_cdc_publication"
    slot_prefix:      str = "fieldcraft_slot"


class OperationsSpec(BaseModel):
    skip: List[str] = Field(default_factory=list)
    # valid values: insert | update | delete | truncate


class TenantsSpec(BaseModel):
    include: List[str] = Field(default_factory=list)
    # empty = all Active tenants


class ColumnRules(BaseModel):
    drop:     List[str]              = Field(default_factory=list)
    mask:     Dict[str, int]         = Field(default_factory=dict)  # col -> asterisk length
    hash:     List[str]              = Field(default_factory=list)
    truncate: Dict[str, int]         = Field(default_factory=dict)  # col -> kept chars


class TablesSpec(BaseModel):
    include:     List[str]                    = Field(default_factory=list)
    exclude:     List[str]                    = Field(default_factory=list)
    row_filters: Dict[str, str]               = Field(default_factory=dict)
    columns:     Dict[str, ColumnRules]       = Field(default_factory=dict)

    @model_validator(mode="after")
    def _include_exclude_mutex(self) -> "TablesSpec":
        if self.include and self.exclude:
            import warnings
            warnings.warn(
                "tables.include and tables.exclude are mutually exclusive — "
                "include wins; exclude is ignored.",
                stacklevel=2,
            )
        return self


class EnvSpec(BaseModel):
    cdc_enabled:    bool
    snapshot_mode:  str          = "initial"
    source:         Optional[SourceSpec] = None
    operations:     OperationsSpec       = Field(default_factory=OperationsSpec)
    tenants:        TenantsSpec          = Field(default_factory=TenantsSpec)
    tables:         TablesSpec           = Field(default_factory=TablesSpec)

    @model_validator(mode="after")
    def _source_required_when_enabled(self) -> "EnvSpec":
        if self.cdc_enabled and self.source is None:
            raise ValueError("source is required when cdc_enabled is true")
        return self


class EnvironmentsConfig(BaseModel):
    tier:         str
    hash_salt:    Optional[str]           = None
    environments: Dict[str, EnvSpec]

    # ── Convenience helpers ───────────────────────────────────────────────────

    def enabled_envs(self) -> list[tuple[str, EnvSpec]]:
        """Return (name, spec) pairs for all envs where cdc_enabled=true."""
        return [(n, s) for n, s in self.environments.items() if s.cdc_enabled]

    def env_table_excludes(self, env_name: str, is_provider: bool = False) -> list[str]:
        """Baseline table excludes merged with this env's exclude list.

        When tables.include (allowlist) is set, returns an empty list because
        Debezium forbids both include and exclude simultaneously — baseline
        tables simply stay out of the allowlist.

        "tenant" is provider-authoritative (see BASELINE_TABLE_EXCLUDES) — it
        must be excluded from tenant-DB connectors but NOT from the provider
        connector itself, or nothing ever captures it. Pass is_provider=True
        when building the provider connector's config.
        """
        spec = self.environments[env_name]
        if spec.tables.include:
            return []   # include wins; exclude not emitted
        baseline = BASELINE_TABLE_EXCLUDES - {"tenant"} if is_provider else BASELINE_TABLE_EXCLUDES
        combined = baseline | set(spec.tables.exclude)
        return sorted(combined)

    def env_column_excludes(self, env_name: str) -> dict[str, list[str]]:
        """Per-table column drop lists: baseline merged with env drop rules.

        Returns { table: [col, ...] } for every table that has at least one
        excluded column. Only `drop` rules are returned here — mask/hash/truncate
        are separate Debezium SMT properties (see connector_props()).
        """
        spec = self.environments[env_name]
        result: dict[str, list[str]] = {}

        # start with baseline
        for table, cols in BASELINE_COLUMN_EXCLUDES.items():
            result[table] = list(cols)

        # merge env drop rules
        for table, rules in spec.tables.columns.items():
            existing = result.get(table, [])
            merged = sorted(set(existing) | set(rules.drop))
            if merged:
                result[table] = merged

        return result

    def connector_props(self, env_name: str, is_provider: bool = False) -> dict[str, str]:
        """Build the Debezium connector property dict for this env.

        Covers: table.include/exclude.list, column.exclude.list,
        column.mask.with.N.chars, column.mask.hash.v2.SHA-256.with.salt,
        column.truncate.to.N.chars, skipped.operations, snapshot.mode.
        Caller merges these on top of the base connector config.

        is_provider=True must be passed when building the provider (main)
        connector's config, so "tenant" is not excluded from it — see
        env_table_excludes().
        """
        spec = self.environments[env_name]
        props: dict[str, str] = {}

        # snapshot mode
        props["snapshot.mode"] = spec.snapshot_mode

        # skipped operations  (Debezium uses single-char codes: c/u/d/t)
        _op_map = {"insert": "c", "update": "u", "delete": "d", "truncate": "t"}
        if spec.operations.skip:
            skipped = ",".join(
                _op_map[op] for op in spec.operations.skip if op in _op_map
            )
            if skipped:
                props["skipped.operations"] = skipped

        # table include/exclude
        t = spec.tables
        if t.include:
            props["table.include.list"] = ",".join(
                f"public.{tbl}" for tbl in t.include
            )
        else:
            excludes = self.env_table_excludes(env_name, is_provider=is_provider)
            if excludes:
                props["table.exclude.list"] = ",".join(
                    f"public.{tbl}" for tbl in excludes
                )

        # column drop (exclude)
        col_excludes = self.env_column_excludes(env_name)
        if col_excludes:
            parts = []
            for table, cols in col_excludes.items():
                for col in cols:
                    parts.append(f"public.{table}.{col}")
            props["column.exclude.list"] = ",".join(parts)

        # column mask / hash / truncate SMT properties
        for table, rules in spec.tables.columns.items():
            for col, length in rules.mask.items():
                key = f"column.mask.with.{length}.chars"
                existing = props.get(key, "")
                entry = f"public.{table}.{col}"
                props[key] = f"{existing},{entry}".lstrip(",")

            for col in rules.hash:
                salt = self.hash_salt or ""
                key = f"column.mask.hash.v2.SHA-256.with.salt.{salt}"
                existing = props.get(key, "")
                entry = f"public.{table}.{col}"
                props[key] = f"{existing},{entry}".lstrip(",")

            for col, length in rules.truncate.items():
                key = f"column.truncate.to.{length}.chars"
                existing = props.get(key, "")
                entry = f"public.{table}.{col}"
                props[key] = f"{existing},{entry}".lstrip(",")

        return props


# ── Convenience accessor ──────────────────────────────────────────────────────

def source_creds(env_name: Optional[str] = None) -> "SourceSpec":
    """Return source PostgreSQL credentials for the given environment.

    Resolution order:
      1. environments.yaml → environments[env_name].source
      2. environments.yaml → first enabled env's source
      3. Direct env vars (POSTGRES_HOST / POSTGRES_USER / POSTGRES_PASSWORD /
         PG_DBNAME) as a backward-compat fallback when the YAML is unavailable.

    Scripts should call this instead of reading POSTGRES_HOST directly so that
    all source credentials flow through environments.yaml.
    """
    if env_name is None:
        env_name = os.environ.get("ETL_ENV", "local")
    try:
        cfg = load_environments()
        spec = cfg.environments.get(env_name)
        if spec and spec.source:
            return spec.source
        # Requested env not found or disabled — fall back to first enabled env.
        enabled = cfg.enabled_envs()
        if enabled:
            return enabled[0][1].source  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        pass
    # Last resort: direct env vars (pre-multi-env scripts, CI without YAML).
    # Password is required here, not defaulted to "" — an ad-hoc empty-string
    # default would silently mask a missing secret and could let the connect
    # attempt succeed against a DB that allows trust/empty-password auth.
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "POSTGRES_PASSWORD is not set — cannot resolve source Postgres "
            "credentials fallback (environments.yaml unavailable/disabled and "
            "no env var fallback provided)"
        )
    return SourceSpec(
        host=os.environ.get("POSTGRES_HOST", ""),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER") or os.environ.get("DEBEZIUM_DB_USER", ""),
        password=password,
        provider_db=os.environ.get("PG_DBNAME") or os.environ.get("POSTGRES_DB", "fieldcraft_provider"),
        ssl_mode=os.environ.get("POSTGRES_SSL_MODE", "require"),
    )


# ── Loader ────────────────────────────────────────────────────────────────────

def load_environments(path: Optional[str] = None) -> EnvironmentsConfig:
    """Load and validate config/environments.yaml.

    Path defaults to ENVIRONMENTS_CONFIG_PATH env var, then
    config/environments.yaml relative to the repo root.
    """
    if path is None:
        path = os.environ.get(
            "ENVIRONMENTS_CONFIG_PATH",
            str(Path(__file__).resolve().parents[2] / "config" / "environments.yaml"),
        )
    raw = yaml.safe_load(Path(path).read_text())
    # strict=False at parse time: disabled envs legitimately have unset ${VAR}
    # credentials (they're never deployed) and must not crash the load.
    # Credential completeness is validated at deploy time in deploy_connectors.py
    # (resolve_dict strict=True is called there on enabled envs only).
    resolved = resolve_dict(raw, strict=False)
    return EnvironmentsConfig.model_validate(resolved)

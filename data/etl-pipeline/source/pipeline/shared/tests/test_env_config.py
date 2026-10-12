"""
shared/tests/test_env_config.py — Unit tests for EnvironmentsConfig.

Covers: YAML load, ${VAR} substitution, baseline merging, on/off,
connector_props output, include/exclude mutex, hash/mask/truncate props.
"""
import os
import textwrap
from pathlib import Path

import pytest
import yaml

from shared.env_config.schema import (
    BASELINE_COLUMN_EXCLUDES,
    BASELINE_TABLE_EXCLUDES,
    EnvironmentsConfig,
    load_environments,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(yaml_text: str) -> EnvironmentsConfig:
    """Parse a YAML string into EnvironmentsConfig (no env-var resolution)."""
    return EnvironmentsConfig.model_validate(yaml.safe_load(textwrap.dedent(yaml_text)))


def _minimal(extra: str = "") -> str:
    return f"""
        tier: nonprod
        environments:
          dev:
            cdc_enabled: true
            source:
              host: dev-host
              port: 5432
              user: dev-user
              password: dev-pass
            {extra}
    """


# ── Basic load ────────────────────────────────────────────────────────────────

class TestLoad:
    def test_minimal_parses(self):
        cfg = _cfg(_minimal())
        assert cfg.tier == "nonprod"
        assert "dev" in cfg.environments
        assert cfg.environments["dev"].cdc_enabled is True

    def test_disabled_env_no_source_required(self):
        cfg = _cfg("""
            tier: nonprod
            environments:
              sandbox:
                cdc_enabled: false
        """)
        assert cfg.environments["sandbox"].source is None

    def test_enabled_env_requires_source(self):
        with pytest.raises(Exception):
            _cfg("""
                tier: nonprod
                environments:
                  dev:
                    cdc_enabled: true
            """)

    def test_load_environments_from_file(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""
            tier: local
            environments:
              local:
                cdc_enabled: true
                source:
                  host: localhost
                  port: 5432
                  user: testuser
                  password: testpass
        """)
        f = tmp_path / "environments.yaml"
        f.write_text(yaml_content)
        cfg = load_environments(str(f))
        assert cfg.tier == "local"

    def test_load_env_var_substitution(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("TEST_PG_HOST", "myhost")
        monkeypatch.setenv("TEST_PG_USER", "myuser")
        monkeypatch.setenv("TEST_PG_PASS", "mypass")
        yaml_content = textwrap.dedent("""
            tier: nonprod
            environments:
              dev:
                cdc_enabled: true
                source:
                  host: ${TEST_PG_HOST}
                  port: 5432
                  user: ${TEST_PG_USER}
                  password: ${TEST_PG_PASS}
        """)
        f = tmp_path / "environments.yaml"
        f.write_text(yaml_content)
        cfg = load_environments(str(f))
        assert cfg.environments["dev"].source.host == "myhost"

    def test_missing_env_var_warns_not_raises(self, tmp_path: Path):
        # load_environments uses strict=False so disabled/unconfigured envs
        # with unset ${VAR} credentials don't crash startup.  A UserWarning is
        # issued; strict enforcement happens at deploy time in deploy_connectors.py.
        yaml_content = textwrap.dedent("""
            tier: nonprod
            environments:
              dev:
                cdc_enabled: true
                source:
                  host: ${NONEXISTENT_VAR_XYZ}
                  port: 5432
                  user: u
                  password: p
        """)
        f = tmp_path / "environments.yaml"
        f.write_text(yaml_content)
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg = load_environments(str(f))
        assert any("NONEXISTENT_VAR_XYZ" in str(w.message) for w in caught)
        assert cfg.environments["dev"].source.host == ""


# ── enabled_envs ─────────────────────────────────────────────────────────────

class TestEnabledEnvs:
    def test_returns_only_enabled(self):
        cfg = _cfg("""
            tier: nonprod
            environments:
              dev:
                cdc_enabled: true
                source: { host: h, port: 5432, user: u, password: p }
              sandbox:
                cdc_enabled: false
        """)
        names = [n for n, _ in cfg.enabled_envs()]
        assert "dev" in names
        assert "sandbox" not in names

    def test_all_disabled_returns_empty(self):
        cfg = _cfg("""
            tier: nonprod
            environments:
              sandbox:
                cdc_enabled: false
        """)
        assert cfg.enabled_envs() == []


# ── env_table_excludes ────────────────────────────────────────────────────────

class TestTableExcludes:
    def test_baseline_always_present(self):
        cfg = _cfg(_minimal())
        excludes = cfg.env_table_excludes("dev")
        for baseline in BASELINE_TABLE_EXCLUDES:
            assert baseline in excludes

    def test_env_exclude_merged_with_baseline(self):
        cfg = _cfg("""
            tier: nonprod
            environments:
              dev:
                cdc_enabled: true
                source: { host: h, port: 5432, user: u, password: p }
                tables:
                  exclude: [audit_log]
        """)
        excludes = cfg.env_table_excludes("dev")
        assert "audit_log" in excludes
        assert "flyway_schema_history" in excludes

    def test_include_wins_returns_empty_exclude(self):
        cfg = _cfg("""
            tier: nonprod
            environments:
              dev:
                cdc_enabled: true
                source: { host: h, port: 5432, user: u, password: p }
                tables:
                  include: [project, task]
        """)
        assert cfg.env_table_excludes("dev") == []

    def test_no_extra_excludes_returns_baseline_only(self):
        cfg = _cfg(_minimal())
        excludes = set(cfg.env_table_excludes("dev"))
        assert excludes == BASELINE_TABLE_EXCLUDES

    def test_is_provider_false_excludes_tenant(self):
        # Default (tenant-DB connector) path: "tenant" stays excluded so a
        # tenant DB's own public.tenant copy never dual-writes silver.tenant.
        cfg = _cfg(_minimal())
        assert "tenant" in cfg.env_table_excludes("dev")
        assert "tenant" in cfg.env_table_excludes("dev", is_provider=False)

    def test_is_provider_true_does_not_exclude_tenant(self):
        # Provider connector must capture "tenant" -- it's the sole
        # authoritative source per the CLAUDE.md tenant-registry section.
        cfg = _cfg(_minimal())
        excludes = cfg.env_table_excludes("dev", is_provider=True)
        assert "tenant" not in excludes
        # every other baseline exclude is still present -- only "tenant" is carved out
        assert (BASELINE_TABLE_EXCLUDES - {"tenant"}).issubset(set(excludes))

    def test_is_provider_true_with_include_list_still_empty(self):
        # include (allowlist) wins regardless of is_provider -- Debezium
        # forbids include+exclude simultaneously.
        cfg = _cfg("""
            tier: nonprod
            environments:
              dev:
                cdc_enabled: true
                source: { host: h, port: 5432, user: u, password: p }
                tables:
                  include: [project, task]
        """)
        assert cfg.env_table_excludes("dev", is_provider=True) == []


# ── env_column_excludes ───────────────────────────────────────────────────────

class TestColumnExcludes:
    def test_baseline_columns_always_present(self):
        cfg = _cfg(_minimal())
        col_ex = cfg.env_column_excludes("dev")
        assert "trade_logo" in col_ex.get("trade", [])
        assert "data" in col_ex.get("tenant_configuration", [])

    def test_env_drop_merged_with_baseline(self):
        cfg = _cfg(_minimal("""
            tables:
              columns:
                worker:
                  drop: [ssn, date_of_birth]
        """))
        col_ex = cfg.env_column_excludes("dev")
        assert "ssn" in col_ex.get("worker", [])
        assert "date_of_birth" in col_ex.get("worker", [])
        assert "trade_logo" in col_ex.get("trade", [])

    def test_no_env_drops_returns_baseline_only(self):
        cfg = _cfg(_minimal())
        col_ex = cfg.env_column_excludes("dev")
        assert set(col_ex.keys()) == set(BASELINE_COLUMN_EXCLUDES.keys())


# ── connector_props ───────────────────────────────────────────────────────────

class TestConnectorProps:
    def test_snapshot_mode_present(self):
        cfg = _cfg(_minimal("snapshot_mode: never"))
        props = cfg.connector_props("dev")
        assert props["snapshot.mode"] == "never"

    def test_default_snapshot_mode(self):
        cfg = _cfg(_minimal())
        props = cfg.connector_props("dev")
        assert props["snapshot.mode"] == "initial"

    def test_skipped_operations(self):
        cfg = _cfg(_minimal("""
            operations:
              skip: [delete, insert]
        """))
        props = cfg.connector_props("dev")
        skipped = set(props["skipped.operations"].split(","))
        assert "d" in skipped
        assert "c" in skipped

    def test_table_include_list(self):
        cfg = _cfg(_minimal("""
            tables:
              include: [project, task]
        """))
        props = cfg.connector_props("dev")
        assert "table.include.list" in props
        assert "table.exclude.list" not in props
        assert "public.project" in props["table.include.list"]

    def test_table_exclude_list_contains_baseline(self):
        cfg = _cfg(_minimal())
        props = cfg.connector_props("dev")
        assert "table.exclude.list" in props
        assert "public.flyway_schema_history" in props["table.exclude.list"]

    def test_table_exclude_list_omits_tenant_for_provider(self):
        # connector_props(is_provider=True) must thread through to
        # env_table_excludes so the provider connector's table.exclude.list
        # never drops public.tenant (see CLAUDE.md's tenant-registry section
        # and shared/env_config/schema.py's env_table_excludes docstring).
        cfg = _cfg(_minimal())
        provider_props = cfg.connector_props("dev", is_provider=True)
        tenant_props = cfg.connector_props("dev", is_provider=False)
        assert "public.tenant" not in provider_props["table.exclude.list"]
        assert "public.tenant" in tenant_props["table.exclude.list"]

    def test_column_exclude_list(self):
        cfg = _cfg(_minimal("""
            tables:
              columns:
                worker:
                  drop: [ssn]
        """))
        props = cfg.connector_props("dev")
        assert "public.worker.ssn" in props["column.exclude.list"]
        assert "public.trade.trade_logo" in props["column.exclude.list"]

    def test_mask_prop_generated(self):
        cfg = _cfg(_minimal("""
            tables:
              columns:
                worker:
                  mask: { home_address: 12 }
        """))
        props = cfg.connector_props("dev")
        assert "column.mask.with.12.chars" in props
        assert "public.worker.home_address" in props["column.mask.with.12.chars"]

    def test_hash_prop_generated(self):
        cfg = _cfg("""
            tier: nonprod
            hash_salt: testsalt
            environments:
              dev:
                cdc_enabled: true
                source: { host: h, port: 5432, user: u, password: p }
                tables:
                  columns:
                    worker:
                      hash: [email]
        """)
        props = cfg.connector_props("dev")
        hash_key = "column.mask.hash.v2.SHA-256.with.salt.testsalt"
        assert hash_key in props
        assert "public.worker.email" in props[hash_key]

    def test_truncate_prop_generated(self):
        cfg = _cfg(_minimal("""
            tables:
              columns:
                worker:
                  truncate: { phone: 4 }
        """))
        props = cfg.connector_props("dev")
        assert "column.truncate.to.4.chars" in props
        assert "public.worker.phone" in props["column.truncate.to.4.chars"]

    def test_include_exclude_mutex_warning(self):
        with pytest.warns(UserWarning, match="mutually exclusive"):
            _cfg("""
                tier: nonprod
                environments:
                  dev:
                    cdc_enabled: true
                    source: { host: h, port: 5432, user: u, password: p }
                    tables:
                      include: [project]
                      exclude: [audit_log]
            """)


class TestMultiEnvSilverIntegration:
    """Unit tests for multi-env _s_env behaviour in the Silver merge job logic."""

    def test_normalise_stamps_s_env_from_event(self):
        """_normalise sets _s_env from the event field when present."""
        import sys, os as _os
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        _os.environ.setdefault("ETL_ENV", "local")
        from silver.src.jobs.universal_silver_merge import SilverRoutingProcess
        event = {"op": "c", "tenantid": "t1", "_s_env": "qa", "lsn": 1, "ts_ms": 0}
        result = SilverRoutingProcess._normalise(event)
        assert result["_s_env"] == "qa"

    def test_normalise_stamps_etl_env_when_missing(self):
        """_normalise falls back to ETL_ENV when _s_env is absent."""
        import sys, os as _os
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        _os.environ["ETL_ENV"] = "local"
        from silver.src.jobs.universal_silver_merge import SilverRoutingProcess
        event = {"op": "c", "tenantid": "t1", "lsn": 1, "ts_ms": 0}
        result = SilverRoutingProcess._normalise(event)
        assert result["_s_env"] == "local"

    def test_route_key_includes_env(self):
        """_route_key encodes env|tenantid|entity so same-env events are co-located."""
        import sys, json as _json
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from silver.src.jobs.universal_silver_merge import _route_key
        msg = _json.dumps({"_s_env": "qa", "tenantid": "t1", "source_table": "project"})
        key = _route_key(msg)
        assert key.startswith("qa|"), f"Expected key to start with 'qa|', got: {key}"
        assert "t1" in key
        assert "project" in key

    def test_build_ddl_includes_s_env_column(self):
        """build_ddl generates _s_env as a plain discriminator column (NOT a
        partition key) with NOT NULL DEFAULT ''. One physical table per
        entity across all environments/tenants — _s_env/_s_tenantid are just
        columns callers filter on, not a PARTITION BY LIST axis."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from silver.ddl.create_silver_tables import build_ddl
        schema = {"columns": {"name": "TEXT"}, "pks": ["id"], "source_tenants": ["t1"]}
        # Add id to columns so PK is valid
        schema["columns"]["id"] = "TEXT"
        ddl = build_ddl("project", schema)
        assert "_s_env" in ddl
        assert "DEFAULT ''" in ddl
        assert "PARTITION BY LIST" not in ddl
        assert "PRIMARY KEY (_s_env, _s_tenantid, id)" in ddl

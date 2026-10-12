-- ============================================================================
-- azure_setup_silver_db.sql
--
-- Minimal, side-effect-free provisioning of the Silver layer on the existing
-- Azure PostgreSQL server.
--
-- This script ONLY:
--   1. Creates a new database `fieldcraft_silver`
--   2. Creates two roles `silver_writer` and `silver_reader`
--   3. Grants those roles privileges INSIDE `fieldcraft_silver` only
--
-- This script does NOT touch any other database. No source database is
-- referenced, queried, or modified. Their ACLs, tables, data, and roles
-- remain exactly as they were.
--
-- Why source data is still safe without any REVOKE on source DBs:
--   silver_writer has no GRANTs on any table in any source database.
--   Without table-level grants, SELECT/INSERT/UPDATE/DELETE on source
--   tables return "permission denied" — Postgres' default.
--
-- ============================================================================
-- HOW TO RUN (PowerShell, single shot)
-- ============================================================================
--
--   $env:PGPASSWORD = '<fieldcraft_demo_user password>'
--   $AZ_PG = 'localhost'
--
--   psql "host=$AZ_PG port=5432 dbname=postgres user=fieldcraft_demo_user sslmode=require" `
--        -v silver_writer_pwd="<writer-password>" `
--        -v silver_reader_pwd="<reader-password>" `
--        -f silver/ddl/azure_setup_silver_db.sql
--
-- Passwords are passed as psql variables, not stored in this file.
-- This script is single-shot — re-running after a successful first run will
-- error on "database fieldcraft_silver already exists" or "role already exists".
-- That's expected; the script is meant to be run once during provisioning.
-- ============================================================================


-- ───── STEP 1: Create the Silver database ──────────────────────────────────
CREATE DATABASE fieldcraft_silver;
COMMENT ON DATABASE fieldcraft_silver IS
  'Silver layer (CDC merge target). Written by silver_writer, read by silver_reader. '
  'Provisioned alongside source DBs on the same Azure PG server; source DBs untouched.';


-- ───── STEP 2: Create the writer + reader roles ────────────────────────────
-- psql substitutes :'silver_writer_pwd' / :'silver_reader_pwd' with the
-- properly-quoted password values passed via -v on the command line.
CREATE ROLE silver_writer WITH LOGIN PASSWORD :'silver_writer_pwd';
CREATE ROLE silver_reader WITH LOGIN PASSWORD :'silver_reader_pwd';


-- ───── STEP 3: Database-level grants (fieldcraft_silver only) ─────────────────────
GRANT CONNECT, CREATE ON DATABASE fieldcraft_silver TO silver_writer;
GRANT CONNECT             ON DATABASE fieldcraft_silver TO silver_reader;


-- ───── STEP 4: Switch into the new DB for schema-level grants ─────────────
\connect fieldcraft_silver

-- Create the silver schema up front so the GRANT statements below succeed
-- even before pg_restore runs. pg_restore is idempotent here — it issues
-- CREATE SCHEMA IF NOT EXISTS or simply uses the existing schema.
CREATE SCHEMA IF NOT EXISTS silver;

GRANT USAGE, CREATE ON SCHEMA silver TO silver_writer;
GRANT USAGE            ON SCHEMA silver TO silver_reader;

-- Existing tables (none yet, but the same block can be re-run after pg_restore
-- creates them to pick up the grants — see Step 4 in azure_restore_silver_dump.md).
GRANT ALL    ON ALL TABLES    IN SCHEMA silver TO silver_writer;
GRANT ALL    ON ALL SEQUENCES IN SCHEMA silver TO silver_writer;
GRANT SELECT ON ALL TABLES    IN SCHEMA silver TO silver_reader;

-- Default privileges for FUTURE tables created by silver_writer (so all
-- subsequent DDL automatically picks up the same grants — including the
-- tables restored from the Silver dump in the next step of the runbook).
ALTER DEFAULT PRIVILEGES FOR ROLE silver_writer IN SCHEMA silver
  GRANT ALL    ON TABLES    TO silver_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE silver_writer IN SCHEMA silver
  GRANT ALL    ON SEQUENCES TO silver_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE silver_writer IN SCHEMA silver
  GRANT SELECT ON TABLES    TO silver_reader;


-- ───── STEP 5: Enable Row-Level Security on every Silver entity table ─────
-- Mirrors silver/ddl/create_silver_tables.py::_apply_row_level_security(),
-- which the Silver DDL job also runs automatically whenever
-- SILVER_ENABLE_RLS=true (the default as of this fix — see that file).
-- Folding the same policy shape into provisioning means a fresh
-- fieldcraft_silver database is tenant-isolated from the moment tables exist,
-- instead of depending on the DDL job having been run with the right
-- env var at least once.
--
-- Tenant isolation contract:
--   - silver_writer is the table owner and bypasses RLS by default (no
--     FORCE ROW LEVEL SECURITY), so ingest writes are unaffected.
--   - silver_reader is subject to RLS; callers MUST run
--         SET LOCAL app.tenant_id = '<tenantid>';
--     at the start of every transaction/session, or SELECTs return zero
--     rows (fail-closed).
--   - Policies use _s_tenantid as the discriminator.
--   - Internal pipeline tables (silver._dlq, silver._cdc_watermark,
--     silver._external_dlq, ...) are intentionally excluded — they
--     span all tenants for operator visibility.
--
-- No entity tables exist yet on a fresh run of this script (before
-- pg_restore) — this block is then a no-op. It IS idempotent
-- (ENABLE ROW LEVEL SECURITY is a no-op if already enabled; the policy is
-- dropped and recreated), so re-run it — see azure_restore_silver_dump.md
-- Step 4 — once the restored tables actually exist.
DO $$
DECLARE
    tbl RECORD;
BEGIN
    FOR tbl IN
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'silver'
          AND table_type = 'BASE TABLE'
          AND table_name NOT LIKE '\_%' ESCAPE '\'
    LOOP
        EXECUTE format('GRANT SELECT ON silver.%I TO silver_reader', tbl.table_name);
        EXECUTE format('ALTER TABLE silver.%I ENABLE ROW LEVEL SECURITY', tbl.table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON silver.%I', tbl.table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON silver.%I '
            'FOR SELECT TO silver_reader '
            'USING (_s_tenantid = current_setting(''app.tenant_id'', true))',
            tbl.table_name
        );
    END LOOP;
END
$$;

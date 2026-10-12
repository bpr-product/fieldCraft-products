-- ============================================================================
-- azure_verify_isolation.sql
--
-- Confirms that:
--   1. silver_writer and silver_reader exist and can access fieldcraft_silver
--   2. silver_writer cannot read any tables in source DBs (no GRANTs)
--   3. silver_reader is SELECT-only on silver.*
--   4. Row-Level Security is actually enabled on every Silver entity table,
--      with a tenant_isolation policy in place, AND a live query confirms
--      silver_reader cannot see other tenants' rows (sections 5-8 below)
--
-- Table/grant checks alone (sections 1-4) are NOT sufficient to claim tenant
-- isolation is verified: they pass identically whether or not RLS is enabled,
-- because SILVER_ENABLE_RLS defaults to false in create_silver_tables.py
-- unless explicitly set. An all-green result on sections 1-4 with RLS off
-- means silver_reader can run `SELECT * FROM silver.<entity>` and get every
-- tenant's rows in one query — sections 5-8 are what actually catch that.
--
-- Run as fieldcraft_demo_user against the `postgres` admin database for
-- sections 1-2, then connect to fieldcraft_silver for sections 3, 5, 6, 7, then run
-- the live boundary tests (section 4) and the live cross-tenant leakage test
-- (section 8) as the silver roles.
-- ============================================================================


-- ───── 1. Roles exist ───────────────────────────────────────────────────────
SELECT rolname, rolcanlogin
FROM pg_roles
WHERE rolname IN ('silver_writer', 'silver_reader')
ORDER BY rolname;
-- Expected: two rows, both rolcanlogin=t


-- ───── 2. silver_writer + silver_reader have CONNECT on fieldcraft_silver ──────────
SELECT
    has_database_privilege('silver_writer', 'fieldcraft_silver', 'CONNECT') AS writer_can_connect,
    has_database_privilege('silver_reader', 'fieldcraft_silver', 'CONNECT') AS reader_can_connect;
-- Expected: both = t


-- ───── 3. Schema-level table privileges inside fieldcraft_silver ──────────────────
-- Run while connected to fieldcraft_silver:
--   \connect fieldcraft_silver
SELECT
    grantee,
    privilege_type,
    COUNT(*) AS tables_with_priv
FROM information_schema.role_table_grants
WHERE table_schema = 'silver'
  AND grantee IN ('silver_writer','silver_reader')
GROUP BY grantee, privilege_type
ORDER BY grantee, privilege_type;
-- Expected:
--   silver_writer: INSERT/UPDATE/DELETE/SELECT/TRUNCATE/REFERENCES/TRIGGER on every table
--   silver_reader: SELECT only


-- ───── 4. Live boundary tests (run from your shell, not psql) ──────────────
--
-- Confirm silver_writer CANNOT read tables in source DBs even if connection
-- is technically allowed (no table-level GRANTs means SELECT fails):
--
--   PGPASSWORD='<SILVER_WRITER_PWD>' psql "host=<HOST> port=5432 dbname=fieldcraft_provider user=silver_writer sslmode=require" `
--     -c "SELECT * FROM public.task LIMIT 1"
--   -- Expected: ERROR: permission denied for table task
--
-- Repeat with dbname=tn35643604 / tn70619321 / tn78010693 etc.
-- All should fail with "permission denied for table ...".
--
--
-- Confirm silver_writer CAN read/write inside fieldcraft_silver:
--
--   PGPASSWORD='<SILVER_WRITER_PWD>' psql "host=<HOST> port=5432 dbname=fieldcraft_silver user=silver_writer sslmode=require" `
--     -c "SELECT COUNT(*) FROM silver.task"
--   -- Expected: a row count (e.g., 38637)
--
--
-- Confirm silver_reader is SELECT-only:
--
--   PGPASSWORD='<SILVER_READER_PWD>' psql "host=<HOST> port=5432 dbname=fieldcraft_silver user=silver_reader sslmode=require" `
--     -c "DELETE FROM silver.task"
--   -- Expected: ERROR: permission denied for table task


-- ───── 5. Row-Level Security is enabled on every Silver entity table ──────
-- Run while connected to fieldcraft_silver: \connect fieldcraft_silver
SELECT relname AS table_name, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relnamespace = 'silver'::regnamespace
  AND relkind = 'r'
  AND relname NOT LIKE '\_%' ESCAPE '\'
ORDER BY relname;
-- Expected: relrowsecurity = t for every row. relforcerowsecurity is expected
-- to be f — silver_writer owns the tables and intentionally bypasses RLS by
-- default (see silver/ddl/create_silver_tables.py::_apply_row_level_security);
-- that is not a gap, only silver_reader is meant to be policy-restricted.


-- ───── 6. tenant_isolation policy exists on every entity table ─────────────
SELECT tablename, policyname, roles, cmd, qual
FROM pg_policies
WHERE schemaname = 'silver'
ORDER BY tablename;
-- Expected: one 'tenant_isolation' row per entity table, roles = {silver_reader},
-- cmd = 'SELECT', qual referencing
-- _s_tenantid = current_setting('app.tenant_id', true)


-- ───── 7. Any Silver entity table WITHOUT RLS enabled (should be empty) ────
SELECT relname AS table_missing_rls
FROM pg_class
WHERE relnamespace = 'silver'::regnamespace
  AND relkind = 'r'
  AND relname NOT LIKE '\_%' ESCAPE '\'
  AND NOT relrowsecurity;
-- Expected: 0 rows. Any row here means SILVER_ENABLE_RLS was never applied to
-- that table (or it was added after the last RLS run) — silver_reader can
-- currently read every tenant's data in it with a plain unfiltered SELECT.
-- Do NOT hand out silver_reader credentials while this query returns rows.


-- ───── 8. Live cross-tenant leakage test (run from your shell, not psql) ───
--
-- Pick a table + tenant known to have rows (e.g. silver.task / tn78010693)
-- and confirm silver_reader sees ZERO other-tenant rows once app.tenant_id
-- is set to that tenant — RLS should silently filter every other tenant's
-- rows out regardless of the WHERE clause:
--
--   PGPASSWORD='<SILVER_READER_PWD>' psql "host=<HOST> port=5432 dbname=fieldcraft_silver user=silver_reader sslmode=require" -c "
--     BEGIN;
--     SET LOCAL app.tenant_id = 'tn78010693';
--     SELECT COUNT(*) FROM silver.task WHERE _s_tenantid <> 'tn78010693';
--     COMMIT;
--   "
--   -- Expected: count = 0
--
-- And confirm a session with app.tenant_id left UNSET returns zero rows at
-- all (fail-closed — no default tenant, no default visibility):
--
--   PGPASSWORD='<SILVER_READER_PWD>' psql "host=<HOST> port=5432 dbname=fieldcraft_silver user=silver_reader sslmode=require" `
--     -c "SELECT COUNT(*) FROM silver.task"
--   -- Expected: count = 0

import os, psycopg2

host = os.environ.get("SILVER_PG_HOST") or os.environ.get("PG_HOST")
if not host:
    raise SystemExit("Set SILVER_PG_HOST (or PG_HOST) in .env.local before running.")
pwd = os.environ.get("SILVER_WRITER_PWD") or os.environ.get("SILVER_PG_PASSWORD")
if not pwd:
    raise SystemExit("Set SILVER_WRITER_PWD (or SILVER_PG_PASSWORD) in .env.local.")

conn = psycopg2.connect(
    host=host,
    port=int(os.environ.get("SILVER_PG_PORT", "5432")),
    dbname=os.environ.get("SILVER_PG_DB", ""),
    user=os.environ.get("SILVER_PG_USER", "silver_writer"),
    password=pwd,
    sslmode=os.environ.get("SILVER_PG_SSL_MODE", "require"),
)
cur = conn.cursor()

for tbl in ['task', 'audit_log', 'project', 'analytical_data', 'task_additional_info']:
    cur.execute(f"SELECT _s_tenantid, COUNT(*) FROM silver.{tbl} GROUP BY _s_tenantid ORDER BY 2 DESC")
    print(f"\n{tbl}:")
    for r in cur.fetchall():
        print(f"  {r[0]:<15} {r[1]:>6}")

conn.close()

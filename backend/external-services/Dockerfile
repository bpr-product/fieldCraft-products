FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq-dev builds psycopg2. The postgresql-client packages supply the pg_dump /
# pg_restore / psql BINARIES the tenant-reset service shells out to.
#
# BOTH 16 AND 17 ARE INSTALLED, because reading an archive and writing to a
# server have OPPOSITE version requirements and one client cannot satisfy both:
#
#   * A client can only read an archive written by ITSELF OR OLDER. pg_dump 17
#     writes archive format 1.16, which a 16 client refuses outright:
#     "unsupported version (1.16) in file header". Every baseline taken before
#     this image was pinned to 16 is format 1.16 -- so a 16-only image cannot
#     read its own history. Hit on PROD 2026-09-02.
#   * A client NEWER than the server breaks on write. pg_restore 17 emits
#     "SET transaction_timeout = 0" in its preamble; that GUC arrived in
#     PostgreSQL 17, so a 16 server rejects it and the restore aborts. Hit on
#     dev 2026-08-31.
#
# So the roles are split, and the split is what makes both directions work:
#
#   PG_RESTORE = 17  -- converts an archive to SQL. NEVER touches a database, so
#                       the newer client is free, and it can read every archive
#                       format up to its own.
#   PSQL       = 17  -- applies that SQL. Must be new enough to PARSE what
#                       pg_dump wrote, which is a DIFFERENT requirement from
#                       matching the server. Recent pg_dump minors emit the
#                       \restrict / \unrestrict meta-commands (a security
#                       backport); psql 16.8 answers
#                       'invalid command \restrict' and the restore dies six
#                       lines in. Proven live 2026-09-02: psql 16.8 failed on
#                       that file, psql 17.7 applied it to the same 16.14 server.
#   PG_DUMP    = 16  -- writes NEW baselines in format 1.15, readable by both.
#
# The applier being NEWER than the server is safe because the SQL it sends is
# plain text we control: pgtool strips the PostgreSQL-17-only GUCs before psql
# sees them. That is why converting to SQL beats pg_restore talking to the server
# directly -- pg_restore picks its own preamble and we cannot edit it, whereas a
# text file we can.
#
# Debian bookworm ships 15, so the PGDG repo is required for both.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev curl ca-certificates \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl --fail -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 postgresql-client-17 \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Explicit paths: Debian's pg_wrapper resolves a bare `psql` to the NEWEST
# installed client, which would silently hand the applier version 17 and
# reintroduce the transaction_timeout failure.
ENV PG_DUMP=/usr/lib/postgresql/16/bin/pg_dump \
    PG_RESTORE=/usr/lib/postgresql/17/bin/pg_restore \
    PSQL=/usr/lib/postgresql/17/bin/psql

WORKDIR /usr/src/FIELDCRAFT-ExternalService

# Build context is the external-integrations PARENT dir (see
# docker-compose-integrations.yml), so paths below are relative to that parent —
# NOT to this Dockerfile's own directory.
COPY fieldcraft-external-services/requirements.txt ./
RUN pip install --upgrade "pip>=24.3" "setuptools>=78.1.1" \
 && pip install -r requirements.txt

COPY fieldcraft-external-services/. .

# CalBridge API package, composed from the sibling service at BUILD TIME, placed
# INSIDE source/ (peer to api/, service/, dal/, Utils/) — not at the container
# root — so it is genuinely part of this service's source tree. Resolves via the
# existing `sys.path.append("source/.")` in MeasurementServiceAPI.py; no import
# changes needed on the calbridge side. Single source of truth is
# fieldcraft-calbridge/calbridge/{api,shared} — never committed here. Only
# api/ + shared/ are needed (the API tier); worker/ + background_worker/ run in
# their own containers built directly from fieldcraft-calbridge.
COPY fieldcraft-calbridge/calbridge/__init__.py ./source/calbridge/__init__.py
COPY fieldcraft-calbridge/calbridge/api ./source/calbridge/api
COPY fieldcraft-calbridge/calbridge/shared ./source/calbridge/shared

# Tenant Reset service, composed from the sibling at BUILD TIME exactly as
# CalBridge is above. Single source of truth is
# fieldcraft-tenant-reset/tenant_reset — never committed here. Resolves via the
# existing `sys.path.append("source/.")`, so no import changes on either side.
COPY fieldcraft-tenant-reset/tenant_reset ./source/tenant_reset

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
 && chown -R appuser:appuser /usr/src/FIELDCRAFT-ExternalService
USER appuser

EXPOSE 5001
CMD ["python", "main.py"]

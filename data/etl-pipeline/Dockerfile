FROM python:3.11.15-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11.15-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Non-root user — never run application processes as root in production
RUN groupadd -r datapipeline && useradd -r -g datapipeline -d /app -s /sbin/nologin datapipeline

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=datapipeline:datapipeline . .

# source/pipeline/{bronze,silver,external,shared,dlq_watcher,ingestion,config}
# keep their original top-level cross-imports (e.g. "from shared.alerting...")
# unchanged from tenant-data-etl — see main.py for why this must be on the path.
ENV PYTHONPATH=/app/source/pipeline

USER datapipeline

EXPOSE 8030

# Public health check — no auth (see source/api/router.py::etl_public_router).
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8030/health || exit 1

CMD ["python", "main.py"]

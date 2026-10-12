FROM python:3.11.15-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11.15-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — never run application processes as root in production
RUN groupadd -r eden && useradd -r -g eden -d /app -s /sbin/nologin eden

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=eden:eden . .

# WORKDIR created /app as root above; COPY --chown only chowns the files it
# copies, not the pre-existing directory itself -- so eden (uid 999, home=
# /app) couldn't create new top-level entries like .cache/huggingface for
# fastembed's model downloads, and silently fell back to tfidf/regex instead
# of the Qdrant-backed retrieval/entity resolution. chown the directory
# itself so eden can write its own cache under its home.
RUN chown eden:eden /app

USER eden

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

EXPOSE 8020

# main.py's own uvicorn.run() reads APP_PORT (default 8020) and APP_RELOAD
# (default off) from the environment — see main.py's __main__ block.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8020/health || exit 1

CMD ["python", "main.py"]

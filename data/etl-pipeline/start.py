"""
Data pipeline FastAPI application factory — CORS, request-ID middleware, auth-enabled
boot check, and the control-plane router (health/status/DLQ) over the
Bronze/Silver/External data pipeline. Modeled on src/bpe/start.py.
"""
import logging
import os
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from source.api.router import etl_public_router, etl_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="FIELDCRAFT Data Pipeline Control Plane", version="1.0.0")

# CORS allow-list, never wildcard — same convention as every other service
# (REC_ALLOWED_ORIGINS / BPE_ALLOWED_ORIGINS / GUARDRAILS_ALLOWED_ORIGINS).
_raw_origins = os.environ.get("ETL_ALLOWED_ORIGINS", "").strip()
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
if not _allowed_origins or "*" in _allowed_origins:
    raise RuntimeError(
        "ETL_ALLOWED_ORIGINS must be set with explicit origins, no wildcard. "
        f"Got: {_raw_origins!r}"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def _check_auth_enabled() -> None:
    auth_enabled = os.getenv("AUTH_ENABLED", "").strip().lower() == "true"
    dev_bypass = os.getenv("AUTH_DEV_BYPASS", "").strip().lower() == "true"
    if not auth_enabled and not dev_bypass:
        raise RuntimeError(
            "AUTH_ENABLED must be 'true' to start the data pipeline control plane. "
            "Set AUTH_DEV_BYPASS=true only in trusted dev environments."
        )


_check_auth_enabled()


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(etl_public_router)
app.include_router(etl_router)


@app.on_event("startup")
async def startup_event():
    logger.info("Data pipeline control plane started")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Data pipeline control plane shutting down")

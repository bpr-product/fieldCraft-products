##----------------------------------------------------------------------------
## Description : Api Layer
##----------------------------------------------------------------------------
## Author: Bala Pradeep R
## Copyright: FieldCraft
## License: {license}
## Version: 1.0
##----------------------------------------------------------------------------
## History
## createdDate: 19-03-2025
##----------------------------------------------------------------------------
## Revision History
## -----------------------------------------------------------------------------
## Requirement/
## Issue/WorkItem |    DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
## -----------------------------------------------------------------------------
##  Feature-2745  | 19-03-2025 | BALA PRADEEP R  | Implement a API
##  Calbridge API  | 30-07-2026 | Shanmugapriyan  | resolved merge conflicts and added calbridge routes

import os
import sys
from sqlalchemy import text as sa_text
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

sys.path.append("source/.")

from fastapi.responses import JSONResponse
import logging
from source.externalService.MeasurementServices import MeasurementServices
from source.service.EagleviewService import EagleviewService
from source.api.ImportExportSyncAPI import QBO_API, QBO_PUBLIC_API
from source.api.quickbooksCallbackAPI import QBO_CALLBACK_API
from source.api.ABCSupplyAPI import ABC_SUPPLY_API
from source.Utils.authenticationService import verify_bearer_token
from source.Utils.security import verify_api_key
from source.dal.db_session import service_session_scope, customer_session_scope

logger = logging.getLogger(__name__)

APIverison = {
    "version": "0.1.0",
    "buildDate":"Aug,28,2024"
}

def get_env(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


def _measurement_internal_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail="The measurement operation failed. Please retry or contact support.",
    )

app = FastAPI()

# Both auth layers (bearer token + api-secret) enforced at the router level —
# same pattern as ABC_SUPPLY_API — so every route added here inherits both
# checks automatically instead of relying on each route to declare them.
MEASUREMENT_API = APIRouter(
    tags=["Measurements"],
    dependencies=[Depends(verify_api_key), Depends(verify_bearer_token)],
)

app.include_router(QBO_CALLBACK_API)
app.include_router(QBO_PUBLIC_API)
app.include_router(QBO_API)
app.include_router(ABC_SUPPLY_API)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_env("CORS_ALLOWED_ORIGINS"),
    allow_credentials=False,
    allow_methods=get_env("CORS_ALLOWED_METHODS", "GET,POST,OPTIONS"),
    allow_headers=get_env(
        "CORS_ALLOWED_HEADERS",
        "Authorization,Content-Type,api-secret,x-api-secret,x-tenant,x-actor,x-tenant-id,tenant-id,tenant-api-token,x-tenant-api-token,roofmaxx-api-token,x-roofmaxx-api-token,x-object-id,x-user-email,x-user-role",
    ),
)

@app.post("/extsvc/api/v1/Measurements/placeOrder", dependencies=[Depends(verify_bearer_token)])
def place_order(
        request_uid: str = Body(),
        tenant_id: str = Body(),
        measurement_type: str = Body(None),
        delivery_type: str = Body(None),
        measurement_structure_type: str = Body(None),
        is_modified: str = Body(None),
        asset_id: str = Body(None),
        service_id: int = Body(None),
        user_id: str = Body(None),
        subscriber_id: int = Body(None)
):
    try:
        measurement_service = MeasurementServices(
            request_uid, user_id, tenant_id, asset_id,service_id,
            measurement_type, delivery_type, measurement_structure_type,
            is_modified,subscriber_id)
        return measurement_service.place_order()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Measurement order creation failed", exc_info=exc)
        raise _measurement_internal_error() from exc

@app.post("/extsvc/api/v1/Measurements/CancelOrder", dependencies=[Depends(verify_bearer_token)])
def cancel_order(reportId: str = Body(), tenant_id: str = Body(None), user_id: str = Body(None)):
    try:
        ealgeview_service = EagleviewService()
        cancel_Order = ealgeview_service.CancelOrder(reportId,tenant_id,user_id)
        if cancel_Order:
            return cancel_Order
        raise HTTPException(status_code=404, detail="Order not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Measurement order cancellation failed", exc_info=exc)
        raise _measurement_internal_error() from exc

@app.post("/extsvc/api/v1/Measurements/GetReport", dependencies=[Depends(verify_bearer_token)])
def get_report(report_refs: list[dict[str, str]] = Body(),tenant_id: str = Body(),user_id: str = Body(None)):
    try:
        eagleview_service = EagleviewService()
        report = eagleview_service.get_Status(report_refs=report_refs,tenant_id=tenant_id,user_id=user_id)
        if report:
            return report
        raise HTTPException(status_code=404, detail="Report not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Measurement report retrieval failed", exc_info=exc)
        raise _measurement_internal_error() from exc

@app.get("/extsvc/api/v1/GetAvailableProducts", dependencies=[Depends(verify_bearer_token)])
def get_available_products():
    try:
        eagleview_service = EagleviewService()
        return eagleview_service.get_available_products()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Measurement product retrieval failed", exc_info=exc)
        raise _measurement_internal_error() from exc

app.include_router(MEASUREMENT_API)
_READY_CHECK_QUERY = sa_text("SELECT 1")

@app.get("/health", tags=["ops"], include_in_schema=False)
async def health():
    """Liveness probe — returns 200 if the process is running. No dependency checks."""
    return {"status": "ok"}


def _check_service_db() -> bool:
    try:
        with service_session_scope() as session:
            session.execute(_READY_CHECK_QUERY)
        return True
    except Exception:
        logging.exception("readiness: service DB check failed")
        return False

def _check_customer_db() -> bool:
    try:
        with customer_session_scope() as session:
            session.execute(_READY_CHECK_QUERY)
        return True
    except Exception:
        logging.exception("readiness: customer DB check failed")
        return False

@app.get("/ready", tags=["ops"], include_in_schema=False)
async def ready():
    """Readiness probe.

    Only this gateway's OWN DB connectivity (service DB, customer DB) gates the
    HTTP status code. This focused gateway exposes QuickBooks, EagleView and ABC Supply readiness only.
    """
    service_ok = await run_in_threadpool(_check_service_db)
    customer_ok = await run_in_threadpool(_check_customer_db)

    checks = {
        "service_db": "ok" if service_ok else "unreachable",
        "customer_db": "ok" if customer_ok else "unreachable",
    }
    overall_ok = service_ok and customer_ok
    return JSONResponse(
        status_code=200 if overall_ok else 503,
        content={"status": "ok" if overall_ok else "unavailable", "checks": checks},
    )

@app.post("/extsvc/api/version")
def pingo_testing():
    return None
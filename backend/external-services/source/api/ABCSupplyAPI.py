"""Protected ABC Supply integration endpoints."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from source.Utils.authenticationService import verify_bearer_token
from source.Utils.security import verify_api_key
from source.Utils.handler import get_bearer_token
from source.service.ABCSupplyService import (
    ABCSupplyAPIError,
    ABCSupplyAuthError,
    ABCSupplyConfigError,
    ABCSupplyService,
)
from source.service.ABCSupplyFIELDCRAFTImportService import ABCMaterialImportOptions, ABCSupplyFIELDCRAFTImportService

logger = logging.getLogger(__name__)

ABC_SUPPLY_API = APIRouter(
    tags=["ABC Supply"],
    dependencies=[Depends(verify_api_key), Depends(verify_bearer_token)],
)


class ABCSupplySearchItemsRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    query: Optional[str] = None
    item_number: Optional[str] = None
    branch_number: Optional[str] = None
    items_per_page: int = Field(default=20, ge=1, le=1000)
    page_number: int = Field(default=1, ge=1)
    embed_branches: bool = False
    include_family_items: bool = False
    raw_filters: Optional[List[Dict[str, Any]]] = None


class ABCSupplyGetItemRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    item_number: str
    include_price: bool = True
    quantity: float = Field(default=1, ge=0)
    uom: Optional[str] = None
    branch_number: Optional[str] = None
    ship_to_number: Optional[str] = None


class ABCSupplyItemAvailabilityRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    item_number: str


class ABCSupplyPriceItemsRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    lines: List[Dict[str, Any]]
    branch_number: Optional[str] = None
    ship_to_number: Optional[str] = None
    purpose: str = "estimating"
    request_id: Optional[str] = None


class ABCSupplyRecentItemsRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    bill_to_number: Optional[str] = None
    branch_number: Optional[str] = None
    items_per_page: int = Field(default=100, ge=1, le=1000)
    page_number: int = Field(default=1, ge=1)


class ABCSupplyAccountItemsRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    list_type: str = "recent"
    bill_to_number: Optional[str] = None
    branch_number: Optional[str] = None
    items_per_page: int = Field(default=100, ge=1, le=1000)
    page_number: int = Field(default=1, ge=1)


class ABCSupplyAllItemsRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    items_per_page: int = Field(default=100, ge=1, le=1000)
    page_number: int = Field(default=1, ge=1)
    embed_branches: bool = False
    embed_variations: bool = False
    include_family_items: bool = False
    since_last_modified_date_time: Optional[str] = None


class ABCSupplySearchAccountsRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    account_type: str = "Ship-To"
    account_number: Optional[str] = None
    state: Optional[str] = None
    storefront: Optional[str] = "abc"
    items_per_page: int = Field(default=20, ge=1, le=1000)
    page_number: int = Field(default=1, ge=1)


class ABCSupplyConfigStatusRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None


class ABCSupplyConnectionSaveRequest(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str
    branch_number: Optional[str] = None
    ship_to_number: Optional[str] = None
    bill_to_number: Optional[str] = None


class ABCSupplyConnectionStatusRequest(BaseModel):
    tenant_id: str


class ABCSupplyConnectionDisconnectRequest(BaseModel):
    tenant_id: str
    clear_credentials: bool = False


class ABCSupplyImportToFIELDCRAFTRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    source: str = "recent"
    query: Optional[str] = None
    item_numbers: Optional[List[str]] = None
    bill_to_number: Optional[str] = None
    branch_number: Optional[str] = None
    ship_to_number: Optional[str] = None
    items_per_page: int = Field(default=100, ge=1, le=1000)
    page_number: int = Field(default=1, ge=1)
    max_pages: int = Field(default=1, ge=1, le=20)
    max_items: int = Field(default=500, ge=1, le=10000)
    fieldcraft_vendor_id: int
    fieldcraft_vendor_name: str = "ABC Supply"
    default_trade: str = "Roofing"
    default_grade: str = "a"
    default_base_unit: str = "EA"
    default_conversion_factor: str = "1"
    default_cost: str = "0"
    default_manufacturer: str = "Unknown"
    allow_decimal: bool
    in_stock: bool
    lead_time: Optional[int] = None
    fetch_prices: bool = True
    require_pricing: bool = True
    dry_run: bool = False
    batch_size: int = Field(default=50, ge=1, le=100)


class ABCSupplyImportSelectedToFIELDCRAFTRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    materials: List[Dict[str, Any]]
    branch_number: Optional[str] = None
    ship_to_number: Optional[str] = None
    fieldcraft_vendor_id: int
    fieldcraft_vendor_name: str = "ABC Supply"
    default_trade: str = "Roofing"
    default_grade: str = "a"
    default_base_unit: str = "EA"
    default_conversion_factor: str = "1"
    default_cost: str = "0"
    default_manufacturer: str = "Unknown"
    allow_decimal: bool
    in_stock: bool
    lead_time: Optional[int] = None
    fetch_prices: bool = True
    require_pricing: bool = True
    dry_run: bool = False
    batch_size: int = Field(default=50, ge=1, le=100)


class ABCSupplyImportedMaterialsRequest(BaseModel):
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    external_item_ids: Optional[List[str]] = None
    branch_number: Optional[str] = None
    ship_to_number: Optional[str] = None
    bill_to_number: Optional[str] = None
    fieldcraft_vendor_id: Optional[int] = None
    search: Optional[str] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=100, ge=1, le=500)


def _current_user_id(claims: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(claims, dict):
        return None
    for key in ("objectId", "oid", "sub", "userId", "user_id", "id"):
        value = claims.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _handle_error(exc: Exception):
    if isinstance(exc, (ABCSupplyConfigError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, ABCSupplyAuthError):
        # Credentials were rejected (invalid_client/invalid_grant) — distinct from a transient
        # upstream outage, so this must NOT be reported as "temporarily unavailable".
        logger.warning("[ABCSupplyAPI] ABC Supply rejected credentials: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="Invalid Client ID or Client Secret. Please use valid credentials.",
        ) from exc
    if isinstance(exc, ABCSupplyAPIError):
        # str(exc) is an internal diagnostic string (label/status/raw response) already
        # logged by ABCSupplyService; never surface it to the user.
        logger.error("[ABCSupplyAPI] ABC Supply upstream error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="ABC Supply is temporarily unavailable. Please try again in a moment.",
        ) from exc
    logger.exception("[ABCSupplyAPI] Unexpected error")
    raise HTTPException(status_code=500, detail="Internal Server Error") from exc


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/config/status")
def config_status(request: ABCSupplyConfigStatusRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /config/status tenant_id={request.tenant_id!r} service_id={request.service_id!r}")
        logger.info("[ABC-SUPPLY-API] POST /config/status tenant_id=%s service_id=%s", request.tenant_id, request.service_id)
        return ABCSupplyService().get_config_status(tenant_id=request.tenant_id, service_id=request.service_id)
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/connection/save")
def save_connection(request: ABCSupplyConnectionSaveRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /connection/save tenant_id={request.tenant_id!r} branch_number={request.branch_number!r} ship_to_number={request.ship_to_number!r}")
        logger.info("[ABC-SUPPLY-API] POST /connection/save tenant_id=%s", request.tenant_id)
        return ABCSupplyService().connect_tenant(
            tenant_id=request.tenant_id,
            client_id=request.client_id,
            client_secret=request.client_secret,
            branch_number=request.branch_number,
            ship_to_number=request.ship_to_number,
            bill_to_number=request.bill_to_number,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/connection/status")
def connection_status(request: ABCSupplyConnectionStatusRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /connection/status tenant_id={request.tenant_id!r}")
        logger.info("[ABC-SUPPLY-API] POST /connection/status tenant_id=%s", request.tenant_id)
        return ABCSupplyService().get_connection_status(tenant_id=request.tenant_id)
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/connection/disconnect")
def disconnect_connection(request: ABCSupplyConnectionDisconnectRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /connection/disconnect tenant_id={request.tenant_id!r} clear_credentials={request.clear_credentials}")
        logger.info("[ABC-SUPPLY-API] POST /connection/disconnect tenant_id=%s clear_credentials=%s", request.tenant_id, request.clear_credentials)
        return ABCSupplyService().disconnect_tenant(
            tenant_id=request.tenant_id,
            clear_credentials=request.clear_credentials,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/accounts/search")
def search_accounts(request: ABCSupplySearchAccountsRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /accounts/search account_type={request.account_type!r}, account_number={request.account_number!r}, state={request.state!r}")
        return ABCSupplyService().search_accounts(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            account_type=request.account_type,
            account_number=request.account_number,
            state=request.state,
            storefront=request.storefront,
            items_per_page=request.items_per_page,
            page_number=request.page_number,
        )
    except Exception as exc:
        _handle_error(exc)



@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/materials/import-to-fieldcraft")
def import_materials_to_fieldcraft(request_body: ABCSupplyImportToFIELDCRAFTRequest, request: Request, claims: Dict[str, Any] = Depends(verify_bearer_token)):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /materials/import-to-fieldcraft source={request_body.source!r}, tenant_id={request_body.tenant_id!r}, fieldcraft_vendor_id={request_body.fieldcraft_vendor_id}, dry_run={request_body.dry_run}")
        options = ABCMaterialImportOptions(
            tenant_id=request_body.tenant_id,
            service_id=request_body.service_id,
            tenant_api_token=get_bearer_token(request),
            created_by=_current_user_id(claims),
            source=request_body.source,
            query=request_body.query,
            item_numbers=request_body.item_numbers,
            bill_to_number=request_body.bill_to_number,
            branch_number=request_body.branch_number,
            ship_to_number=request_body.ship_to_number,
            items_per_page=request_body.items_per_page,
            page_number=request_body.page_number,
            max_pages=request_body.max_pages,
            max_items=request_body.max_items,
            fieldcraft_vendor_id=request_body.fieldcraft_vendor_id,
            fieldcraft_vendor_name=request_body.fieldcraft_vendor_name,
            default_trade=request_body.default_trade,
            default_grade=request_body.default_grade,
            default_base_unit=request_body.default_base_unit,
            default_conversion_factor=request_body.default_conversion_factor,
            default_cost=request_body.default_cost,
            default_manufacturer=request_body.default_manufacturer,
            allow_decimal=request_body.allow_decimal,
            in_stock=request_body.in_stock,
            lead_time=request_body.lead_time,
            fetch_prices=request_body.fetch_prices,
            require_pricing=request_body.require_pricing,
            dry_run=request_body.dry_run,
            batch_size=request_body.batch_size,
        )
        return ABCSupplyFIELDCRAFTImportService().import_materials(options)
    except Exception as exc:
        _handle_error(exc)



@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/materials/import-selected-to-fieldcraft")
def import_selected_materials_to_fieldcraft(request_body: ABCSupplyImportSelectedToFIELDCRAFTRequest, request: Request, claims: Dict[str, Any] = Depends(verify_bearer_token)):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /materials/import-selected-to-fieldcraft {len(request_body.materials)} materials, tenant_id={request_body.tenant_id!r}, fieldcraft_vendor_id={request_body.fieldcraft_vendor_id}, dry_run={request_body.dry_run}")
        options = ABCMaterialImportOptions(
            tenant_id=request_body.tenant_id,
            service_id=request_body.service_id,
            tenant_api_token=get_bearer_token(request),
            created_by=_current_user_id(claims),
            source="selected",
            branch_number=request_body.branch_number,
            ship_to_number=request_body.ship_to_number,
            max_items=len(request_body.materials),
            fieldcraft_vendor_id=request_body.fieldcraft_vendor_id,
            fieldcraft_vendor_name=request_body.fieldcraft_vendor_name,
            default_trade=request_body.default_trade,
            default_grade=request_body.default_grade,
            default_base_unit=request_body.default_base_unit,
            default_conversion_factor=request_body.default_conversion_factor,
            default_cost=request_body.default_cost,
            default_manufacturer=request_body.default_manufacturer,
            allow_decimal=request_body.allow_decimal,
            in_stock=request_body.in_stock,
            lead_time=request_body.lead_time,
            fetch_prices=request_body.fetch_prices,
            require_pricing=request_body.require_pricing,
            dry_run=request_body.dry_run,
            batch_size=request_body.batch_size,
        )
        return ABCSupplyFIELDCRAFTImportService().import_selected_materials(request_body.materials, options)
    except Exception as exc:
        _handle_error(exc)



@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/materials/imported")
def imported_materials(request: ABCSupplyImportedMaterialsRequest):
    try:
        logger.debug(
            "[ABC-SUPPLY-API] POST /materials/imported tenant_id=%r count=%s page=%s",
            request.tenant_id,
            len(request.external_item_ids or []),
            request.page,
        )
        return ABCSupplyFIELDCRAFTImportService().list_imported_materials(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            external_item_ids=request.external_item_ids,
            branch_number=request.branch_number,
            ship_to_number=request.ship_to_number,
            bill_to_number=request.bill_to_number,
            vendor_id=request.fieldcraft_vendor_id,
            search=request.search,
            page=request.page,
            limit=request.limit,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/materials/search")
@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/items/search")
def search_items(request: ABCSupplySearchItemsRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /items/search query={request.query!r}, item_number={request.item_number!r}, branch_number={request.branch_number!r}, page={request.page_number}")
        return ABCSupplyService().search_items(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            query=request.query,
            item_number=request.item_number,
            branch_number=request.branch_number,
            items_per_page=request.items_per_page,
            page_number=request.page_number,
            embed_branches=request.embed_branches,
            include_family_items=request.include_family_items,
            raw_filters=request.raw_filters,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/items/all")
def all_items(request: ABCSupplyAllItemsRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /items/all page={request.page_number}, items_per_page={request.items_per_page}")
        return ABCSupplyService().get_all_items(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            items_per_page=request.items_per_page,
            page_number=request.page_number,
            embed_branches=request.embed_branches,
            embed_variations=request.embed_variations,
            include_family_items=request.include_family_items,
            since_last_modified_date_time=request.since_last_modified_date_time,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/items/account-materials")
def account_materials(request: ABCSupplyAccountItemsRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /items/account-materials list_type={request.list_type!r}, bill_to_number={request.bill_to_number!r}, branch_number={request.branch_number!r}, page={request.page_number}")
        return ABCSupplyService().get_account_items(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            list_type=request.list_type,
            bill_to_number=request.bill_to_number,
            branch_number=request.branch_number,
            items_per_page=request.items_per_page,
            page_number=request.page_number,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/materials/item")
@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/items/get")
def get_item(request: ABCSupplyGetItemRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /items/get item_number={request.item_number!r}, include_price={request.include_price}, quantity={request.quantity}, uom={request.uom!r}")
        service = ABCSupplyService()
        if request.include_price:
            return service.get_item_with_price(
                tenant_id=request.tenant_id,
                service_id=request.service_id,
                item_number=request.item_number,
                quantity=request.quantity,
                uom=request.uom,
                branch_number=request.branch_number,
                ship_to_number=request.ship_to_number,
            )
        return service.get_item(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            item_number=request.item_number,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/items/availability")
def item_availability(request: ABCSupplyItemAvailabilityRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /items/availability item_number={request.item_number!r}")
        return ABCSupplyService().get_item_availability(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            item_number=request.item_number,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/materials/price")
@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/items/price")
def price_items(request: ABCSupplyPriceItemsRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /items/price {len(request.lines)} lines, branch_number={request.branch_number!r}, ship_to_number={request.ship_to_number!r}")
        return ABCSupplyService().price_items(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            lines=request.lines,
            branch_number=request.branch_number,
            ship_to_number=request.ship_to_number,
            purpose=request.purpose,
            request_id=request.request_id,
        )
    except Exception as exc:
        _handle_error(exc)


@ABC_SUPPLY_API.post("/extsvc/api/v1/abc-supply/items/recent")
def recent_items(request: ABCSupplyRecentItemsRequest):
    try:
        logger.debug(f"[ABC-SUPPLY-API] POST /items/recent bill_to_number={request.bill_to_number!r}, branch_number={request.branch_number!r}, page={request.page_number}")
        return ABCSupplyService().get_recent_items(
            tenant_id=request.tenant_id,
            service_id=request.service_id,
            bill_to_number=request.bill_to_number,
            branch_number=request.branch_number,
            items_per_page=request.items_per_page,
            page_number=request.page_number,
        )
    except Exception as exc:
        _handle_error(exc)

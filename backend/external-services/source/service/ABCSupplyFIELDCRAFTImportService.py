"""Import ABC Supply materials into FIELDCRAFT through the existing Tenant Material API.

This service intentionally keeps ABC as the source of truth for retrieval and
FIELDCRAFT as the source of truth for BOM selection. Material rows are created only
through the Tenant Material API. After a successful import, this service stores
the generic material_external_map row so ABC search results can show tracking
and already-added status.
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

from source.dal.ABCSupplyRepository import ABCSupplyRepository
from source.dal.MaterialExternalMapRepository import MaterialExternalMapRepository
from source.service.ABCSupplyService import ABCSupplyService
from source.service.TenantApiClient import TenantApiClient
from source.service.ABCSupplyUomMappingConfig import ABCSupplyUomMappingConfig

logger = logging.getLogger(__name__)


class ABCMaterialImportSkip(ValueError):
    def __init__(self, reason: str, reason_code: str):
        super().__init__(reason)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ABCMaterialImportOptions:
    tenant_id: Optional[str] = None
    service_id: Optional[str] = None
    tenant_api_token: Optional[str] = None
    created_by: Optional[str] = None
    source: str = "recent"
    query: Optional[str] = None
    item_numbers: Optional[List[str]] = None
    bill_to_number: Optional[str] = None
    branch_number: Optional[str] = None
    ship_to_number: Optional[str] = None
    items_per_page: int = 100
    page_number: int = 1
    max_pages: int = 1
    max_items: int = 500
    fieldcraft_vendor_id: Optional[int] = None
    fieldcraft_vendor_name: str = "ABC Supply"
    default_trade: str = "Roofing"
    default_grade: str = "a"
    default_base_unit: str = "EA"
    default_conversion_factor: str = "1"
    default_cost: str = "0"
    default_manufacturer: str = "Unknown"
    allow_decimal: bool = True
    in_stock: bool = True
    lead_time: Optional[int] = None
    fetch_prices: bool = True
    require_pricing: bool = True
    dry_run: bool = False
    batch_size: int = 50


class ABCSupplyFIELDCRAFTImportService:
    """Orchestrates ABC material retrieval and FIELDCRAFT material import."""

    ABC_TO_FIELDCRAFT_UOM = {
        "EA": "EA",
        "EACH": "EA",
        "PC": "PCS",
        "PCS": "PCS",
        "PIECE": "PCS",
        "BD": "BD",
        "BNDL": "BD",
        "BUNDLE": "BD",
        "SQ": "SQ",
        "SQR": "SQ",
        "SF": "SF",
        "LF": "LF",
        "GAL": "GAL",
        "GL": "GAL",
        "BX": "BX",
        "BOX": "BX",
        "RL": "ROLL",
        "ROLL": "ROLL",
        "TB": "TB",
        "TUBE": "TB",
        "PLT": "PLT",
        "PALLET": "PLT",
        "LB": "LB",
        "DZ": "DZ",
        "PR": "PR",
        "SET": "SET",
    }

    def __init__(
        self,
        abc_service: Optional[ABCSupplyService] = None,
        tenant_client: Optional[TenantApiClient] = None,
        material_map_repository: Optional[MaterialExternalMapRepository] = None,
        uom_mapping_config: Optional[ABCSupplyUomMappingConfig] = None,
    ):
        self.abc_service = abc_service or ABCSupplyService()
        self.tenant_client = tenant_client or TenantApiClient()
        self.material_map_repository = material_map_repository or MaterialExternalMapRepository()
        self.uom_mapping_config = uom_mapping_config or ABCSupplyUomMappingConfig()
        self.abc_repository = ABCSupplyRepository()

    def import_materials(self, options: ABCMaterialImportOptions) -> Dict[str, Any]:
        """Fetch ABC materials by source and import them into FIELDCRAFT."""
        logger.debug(f"[ABC-SUPPLY-IMPORT] import_materials: source={options.source!r}, tenant_id={options.tenant_id!r}, max_items={options.max_items}, dry_run={options.dry_run}")
        self._validate_options(options)
        fetched_items = self._fetch_abc_items(options)
        logger.debug(f"[ABC-SUPPLY-IMPORT] import_materials: fetched {len(fetched_items)} items from ABC Supply")
        return self._import_items(fetched_items, options)

    def import_selected_materials(
        self,
        selected_items: List[Dict[str, Any]],
        options: ABCMaterialImportOptions,
    ) -> Dict[str, Any]:
        """Import only the ABC materials explicitly selected by the user.

        This is the preferred MVP flow: search ABC live, select only the
        required materials, persist those selected items into FIELDCRAFT, then BOM can
        use them like normal FIELDCRAFT materials.
        """
        logger.debug(f"[ABC-SUPPLY-IMPORT] import_selected_materials: {len(selected_items)} items submitted, tenant_id={options.tenant_id!r}, dry_run={options.dry_run}")
        if not selected_items:
            raise ValueError("materials are required")

        safe_items = [item for item in selected_items if isinstance(item, dict)]
        if not safe_items:
            raise ValueError("materials must contain valid ABC item objects")

        logger.debug(f"[ABC-SUPPLY-IMPORT] import_selected_materials: {len(safe_items)} valid item dicts")
        selected_options = replace(options, source="selected")
        self._validate_options(selected_options)
        return self._import_items(safe_items, selected_options)

    def _import_items(
        self,
        fetched_items: List[Dict[str, Any]],
        options: ABCMaterialImportOptions,
    ) -> Dict[str, Any]:
        options = self._with_resolved_service_id(options)
        unique_items, duplicate_count = self._dedupe_by_item_number(fetched_items)
        logger.debug(f"[ABC-SUPPLY-IMPORT] _import_items: {len(fetched_items)} fetched -> {len(unique_items)} unique ({duplicate_count} duplicates removed)")
        if options.max_items and len(unique_items) > options.max_items:
            unique_items = unique_items[: options.max_items]

        price_by_item_number: Dict[str, Dict[str, Any]] = {}
        price_errors: List[Dict[str, Any]] = []
        if options.fetch_prices and unique_items:
            price_by_item_number, price_errors = self._fetch_prices(unique_items, options)

        result: Dict[str, Any] = {
            "ok": True,
            "source": options.source,
            "dryRun": options.dry_run,
            "counts": {
                "fetched": len(fetched_items),
                "duplicateFromSource": duplicate_count,
                "prepared": 0,
                "created": 0,
                "linkedExisting": 0,
                "skippedExisting": 0,
                "skippedMissingPrice": 0,
                "skippedInvalid": 0,
                "failed": 0,
                "priceErrors": len(price_errors),
            },
            "created": [],
            "linkedExisting": [],
            "skipped": [],
            "failed": [],
            "priceErrors": price_errors[:25],
        }

        records_to_create: List[Dict[str, Any]] = []
        uom_mapping = self._load_uom_mapping()
        valid_fieldcraft_uom_codes: Optional[set[str]] = None
        uom_validation_error: Optional[str] = None

        for item in unique_items:
            item_number = self._item_number(item)
            price_line = price_by_item_number.get(item_number or "")

            existing_external_map = self._find_existing_external_map(item_number, options)
            if existing_external_map:
                result["counts"]["skippedExisting"] += 1
                result["skipped"].append(
                    {
                        "itemNumber": item_number,
                        "name": self._item_name(item) or existing_external_map.get("external_display_name"),
                        "reasonCode": "ALREADY_IMPORTED",
                        "reason": "This ABC Supply material is already linked to your material list.",
                        "materialExternalMapId": existing_external_map.get("id"),
                        "mapping": existing_external_map,
                    }
                )
                continue

            try:
                if valid_fieldcraft_uom_codes is None and uom_validation_error is None:
                    try:
                        valid_fieldcraft_uom_codes = self._get_valid_fieldcraft_uom_codes(options)
                    except Exception:
                        logger.exception(
                            "[ABCSupplyFIELDCRAFTImportService._import_items] FIELDCRAFT UOM validation is unavailable"
                        )
                        uom_validation_error = (
                            "We could not check your FIELDCRAFT UOM setup right now. Please try again, or contact FIELDCRAFT support."
                        )
                if uom_validation_error:
                    raise ABCMaterialImportSkip(uom_validation_error, "FIELDCRAFT_UOM_VALIDATION_UNAVAILABLE")

                material = self._build_fieldcraft_material_payload(
                    item=item,
                    options=options,
                    price_line=price_line,
                    uom_mapping=uom_mapping,
                    valid_fieldcraft_uom_codes=valid_fieldcraft_uom_codes or set(),
                )
            except ABCMaterialImportSkip as exc:
                count_key = "skippedMissingPrice" if exc.reason_code == "MISSING_PRICING" else "skippedInvalid"
                result["counts"][count_key] += 1
                result["skipped"].append(
                    {
                        "itemNumber": item_number,
                        "name": self._item_name(item),
                        "reasonCode": exc.reason_code,
                        "reason": str(exc),
                    }
                )
                continue
            except ValueError as exc:
                result["counts"]["skippedInvalid"] += 1
                result["skipped"].append(
                    {
                        "itemNumber": item_number,
                        "name": self._item_name(item),
                        "reasonCode": "INVALID_MATERIAL",
                        "reason": str(exc),
                    }
                )
                continue

            result["counts"]["prepared"] += 1

            existing_fieldcraft_material = self._find_existing_fieldcraft_material(material, options)
            if existing_fieldcraft_material:
                if options.tenant_id and options.service_id and not existing_fieldcraft_material.get("duplicateCheckFailed"):
                    mapped = self._save_material_external_map(
                        material=material,
                        item=item,
                        price_line=price_line,
                        options=options,
                        fieldcraft_material=existing_fieldcraft_material,
                        reason="ABC Supply item linked to an existing FIELDCRAFT vendor material.",
                    )
                    result["counts"]["linkedExisting"] += 1
                    result["linkedExisting"].append(
                        {
                            **self._created_summary(material),
                            "status": "LINKED_EXISTING",
                            **mapped,
                        }
                    )
                else:
                    result["counts"]["skippedExisting"] += 1
                    result["skipped"].append(
                        {
                            "itemNumber": material["itemCode"],
                            "name": material["name"],
                            "reasonCode": "DUPLICATE_MATERIAL",
                            "reason": "This material already exists in your FIELDCRAFT list for this vendor and grade and was not added again.",
                        }
                    )
                continue

            records_to_create.append(
                {
                    "material": material,
                    "abcItem": item,
                    "priceLine": price_line,
                }
            )

        if options.dry_run:
            result["materials"] = [record["material"] for record in records_to_create[:100]]
            return result

        created, failed = self._create_materials_in_fieldcraft(records_to_create, options)
        result["created"] = created[:100]
        result["failed"] = failed[:100]
        result["counts"]["created"] = len(created)
        result["counts"]["failed"] += len(failed)
        if created or result.get("linkedExisting"):
            self._audit_material_import(options, result, created, failed)
        if failed:
            result["ok"] = False
        return result

    def _audit_material_import(
        self,
        options: ABCMaterialImportOptions,
        result: Dict[str, Any],
        created: List[Dict[str, Any]],
        failed: List[Dict[str, Any]],
    ) -> None:
        if not options.tenant_id:
            return
        try:
            from source.Utils.auditlog import AuditlogService

            counts = result.get("counts") or {}
            context = (
                f"src={options.source};created={len(created)};"
                f"skipped={len(result.get('skipped') or [])};failed={len(failed)}"
            )
            AuditlogService(default_source="ABC Supply").log(
                tenant_id=str(options.tenant_id),
                action="ABC_SUPPLY_MATERIAL_IMPORT",
                context=context,
                resource_type="abc_supply_material_import",
                resource_id=str(options.fieldcraft_vendor_id or ""),
                source="ABC Supply",
                status="success" if not failed else "partial_failed",
            )
            logger.debug(
                "[ABCSupplyFIELDCRAFTImportService] audit logged for ABC material import: created=%s skipped=%s failed=%s priceErrors=%s",
                len(created),
                len(result.get("skipped") or []),
                len(failed),
                counts.get("priceErrors"),
            )
        except Exception:
            logger.warning(
                "[ABCSupplyFIELDCRAFTImportService] Audit log failed; import itself succeeded",
                exc_info=True,
            )

    def _validate_options(self, options: ABCMaterialImportOptions) -> None:
        source = (options.source or "").strip().lower()
        allowed = {"recent", "recents", "frequent", "frequents", "favorite", "favorites", "all", "search", "item_numbers", "selected"}
        if source not in allowed:
            raise ValueError("source must be one of: recent, frequent, favorite, all, search, item_numbers, selected")
        if source == "search" and not options.query:
            raise ValueError("query is required when source is search")
        if source == "item_numbers" and not options.item_numbers:
            raise ValueError("item_numbers is required when source is item_numbers")
        if options.items_per_page < 1 or options.items_per_page > 1000:
            raise ValueError("items_per_page must be between 1 and 1000")
        if options.page_number < 1:
            raise ValueError("page_number must be greater than 0")
        if options.max_pages < 1:
            raise ValueError("max_pages must be greater than 0")
        if not options.fieldcraft_vendor_id:
            raise ValueError("fieldcraft_vendor_id is required. Create/select ABC Supply as a FIELDCRAFT vendor first.")
        if options.batch_size < 1 or options.batch_size > 100:
            raise ValueError("batch_size must be between 1 and 100")

    def _fetch_abc_items(self, options: ABCMaterialImportOptions) -> List[Dict[str, Any]]:
        source = (options.source or "recent").strip().lower()
        items: List[Dict[str, Any]] = []
        logger.debug(f"[ABC-SUPPLY-IMPORT] _fetch_abc_items: source={source!r}, max_pages={options.max_pages}, items_per_page={options.items_per_page}")

        if source == "item_numbers":
            for item_number in options.item_numbers or []:
                item_number = str(item_number or "").strip()
                if not item_number:
                    continue
                item = self.abc_service.get_item(
                    tenant_id=options.tenant_id,
                    service_id=options.service_id,
                    item_number=item_number,
                )
                items.append(item)
            return items

        for offset in range(options.max_pages):
            page_number = options.page_number + offset
            if source == "all":
                response = self.abc_service.get_all_items(
                    tenant_id=options.tenant_id,
                    service_id=options.service_id,
                    items_per_page=options.items_per_page,
                    page_number=page_number,
                    embed_branches=True,
                    embed_variations=True,
                )
            elif source == "search":
                response = self.abc_service.search_items(
                    tenant_id=options.tenant_id,
                    service_id=options.service_id,
                    query=options.query,
                    branch_number=options.branch_number,
                    items_per_page=options.items_per_page,
                    page_number=page_number,
                    embed_branches=True,
                )
            else:
                response = self.abc_service.get_account_items(
                    tenant_id=options.tenant_id,
                    service_id=options.service_id,
                    list_type=source,
                    bill_to_number=options.bill_to_number,
                    branch_number=options.branch_number,
                    items_per_page=options.items_per_page,
                    page_number=page_number,
                )

            page_items = response.get("items") or []
            logger.debug(f"[ABC-SUPPLY-IMPORT] _fetch_abc_items: page={page_number}, got {len(page_items)} items (total so far: {len(items) + len([i for i in page_items if isinstance(i, dict)])})")
            if not isinstance(page_items, list):
                break
            items.extend([item for item in page_items if isinstance(item, dict)])
            if len(page_items) < options.items_per_page:
                break
            if options.max_items and len(items) >= options.max_items:
                break

        return items[: options.max_items] if options.max_items else items

    def _fetch_prices(
        self,
        items: List[Dict[str, Any]],
        options: ABCMaterialImportOptions,
    ) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        price_by_item: Dict[str, Dict[str, Any]] = {}
        errors: List[Dict[str, Any]] = []
        lines: List[Dict[str, Any]] = []
        logger.debug(f"[ABC-SUPPLY-IMPORT] _fetch_prices: building price lines for {len(items)} items")

        for item in items:
            item_number = self._item_number(item)
            if not item_number:
                continue
            lines.append(
                {
                    "id": str(len(lines) + 1),
                    "itemNumber": item_number,
                    "quantity": 1,
                    "uom": self._abc_uom(item) or options.default_base_unit,
                }
            )

        for batch in self._chunks(lines, 50):
            try:
                logger.debug(f"[ABC-SUPPLY-IMPORT] _fetch_prices: pricing batch of {len(batch)} items")
                response = self.abc_service.price_items(
                    tenant_id=options.tenant_id,
                    service_id=options.service_id,
                    branch_number=options.branch_number,
                    ship_to_number=options.ship_to_number,
                    lines=batch,
                    purpose="estimating",
                    request_id="fieldcraft-abc-material-import",
                )
                priced_lines = response.get("lines") or []
                logger.debug(f"[ABC-SUPPLY-IMPORT] _fetch_prices: received {len(priced_lines)} priced lines from ABC Supply")
                for line in priced_lines:
                    item_number = str(line.get("itemNumber") or "").strip()
                    if item_number:
                        price_by_item[item_number] = line
            except Exception as exc:
                logger.exception("[ABCSupplyFIELDCRAFTImportService._fetch_prices] pricing batch failed")
                errors.append(
                    {
                        "itemNumbers": [line.get("itemNumber") for line in batch],
                        "reason": str(exc),
                    }
                )
        return price_by_item, errors

    def _build_fieldcraft_material_payload(
        self,
        item: Dict[str, Any],
        options: ABCMaterialImportOptions,
        price_line: Optional[Dict[str, Any]] = None,
        uom_mapping: Optional[Dict[str, str]] = None,
        valid_fieldcraft_uom_codes: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        item_number = self._item_number(item)
        if not item_number:
            raise ValueError("ABC item is missing itemNumber")

        name = self._trim(self._first_text(item, ["itemDescription", "description", "name"]) or item_number, 1024)
        if not name:
            raise ValueError("ABC item is missing itemDescription/name")

        manufacturer = self._trim(
            self._first_text(item, ["manufacturerName", "supplierName", "manufacturer", "brand"])
            or options.default_manufacturer,
            64,
        )
        abc_uom = self._abc_uom(item)
        unit_code = self._resolve_fieldcraft_uom(
            item=item,
            abc_uom=abc_uom,
            uom_mapping=uom_mapping or self._load_uom_mapping(),
            valid_fieldcraft_uom_codes=valid_fieldcraft_uom_codes or set(),
        )
        price_value = self._first_text(price_line or {}, ["unitPrice", "netPrice", "price", "customerPrice"])
        if options.require_pricing:
            cost = self._required_price_text(price_value)
            if cost is None:
                raise ABCMaterialImportSkip(
                    "ABC Supply did not return a price for this material. FIELDCRAFT requires pricing before saving a material to your list.",
                    "MISSING_PRICING",
                )
        else:
            cost = self._decimal_text(price_value, options.default_cost)
        category = self._first_text(item, ["categoryName", "productTypeName", "materialTypeName"])
        tags = self._trim(options.default_trade or category or "Roofing", 512)

        return {
            "vendorId": int(options.fieldcraft_vendor_id),
            "companyName": options.fieldcraft_vendor_name or "ABC Supply",
            "name": name,
            "itemCode": self._trim(item_number, 512),
            "tags": tags,
            "manufacturer": manufacturer or options.default_manufacturer,
            "unit": unit_code,
            "baseUnit": unit_code,
            "conversionFactor": self._decimal_text(options.default_conversion_factor, "1"),
            "grade": self._trim(options.default_grade or "a", 64),
            "versionStartdate": self._now_iso(),
            "cost": cost,
            "inStock": bool(options.in_stock),
            "allowDecimal": bool(options.allow_decimal),
            "leadTime": options.lead_time,
            "status": "Active",
            "serviceId": int(options.service_id) if options.service_id and str(options.service_id).isdigit() else None
        }

    def _exists_in_fieldcraft(self, material: Dict[str, Any], options: ABCMaterialImportOptions) -> bool:
        return bool(self._find_existing_fieldcraft_material(material, options))

    def _find_existing_fieldcraft_material(
        self,
        material: Dict[str, Any],
        options: ABCMaterialImportOptions,
    ) -> Optional[Dict[str, Any]]:
        try:
            existing = self.tenant_client.get_vendor_material(
                options.tenant_api_token,
                {
                    "name": material.get("name"),
                    "grade": material.get("grade"),
                    "vendorId": material.get("vendorId"),
                },
            )
            return existing if isinstance(existing, dict) and existing else None
        except Exception:
            logger.exception(
                "[ABCSupplyFIELDCRAFTImportService._find_existing_fieldcraft_material] duplicate check failed; treating as existing to avoid duplicate creation for itemCode=%s",
                material.get("itemCode"),
            )
            return {"duplicateCheckFailed": True}

    def _create_materials_in_fieldcraft(
        self,
        material_records: List[Dict[str, Any]],
        options: ABCMaterialImportOptions,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        created: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        logger.debug(
            "[ABC-SUPPLY-IMPORT] _create_materials_in_fieldcraft: %s materials to create, batch_size=%s",
            len(material_records),
            options.batch_size,
        )
        for batch in self._chunks(material_records, options.batch_size):
            batch_materials = [record["material"] for record in batch]
            try:
                logger.debug(
                    "[ABC-SUPPLY-IMPORT] _create_materials_in_fieldcraft: sending batch of %s to FIELDCRAFT TenantAPI",
                    len(batch_materials),
                )
                self.tenant_client.add_materials(options.tenant_api_token, batch_materials)
                resolved_by_record = self._resolve_created_materials(batch, options)
                logger.debug(
                    "[ABC-SUPPLY-IMPORT] _create_materials_in_fieldcraft: batch created OK, items=%s, resolved_ids=%s",
                    [m.get("itemCode") for m in batch_materials],
                    sum(1 for value in resolved_by_record if value),
                )
                for index, record in enumerate(batch):
                    created.append(self._created_result(record, options, resolved_by_record[index]))
            except Exception as batch_exc:
                logger.warning(
                    "[ABCSupplyFIELDCRAFTImportService._create_materials_in_fieldcraft] batch failed, retrying one-by-one: %s",
                    batch_exc,
                )
                for record in batch:
                    material = record["material"]
                    try:
                        self.tenant_client.add_materials(options.tenant_api_token, [material])
                        resolved = self._resolve_created_materials([record], options)[0]
                        created.append(self._created_result(record, options, resolved))
                    except Exception as item_exc:
                        failed.append(
                            {
                                "itemNumber": material.get("itemCode"),
                                "name": material.get("name"),
                                "reason": str(item_exc),
                            }
                        )
        return created, failed

    def _resolve_created_materials(
        self,
        records: List[Dict[str, Any]],
        options: ABCMaterialImportOptions,
    ) -> List[Optional[Dict[str, Any]]]:
        resolved: List[Optional[Dict[str, Any]]] = [None] * len(records)
        if not records or not options.tenant_id or not options.fieldcraft_vendor_id:
            return resolved

        try:
            materials = [record.get("material") or {} for record in records]
            vendor_materials = self.material_map_repository.find_vendor_materials_for_mapping(
                tenant_id=str(options.tenant_id),
                vendor_id=options.fieldcraft_vendor_id,
                materials=materials,
            )
            return self._match_created_materials(vendor_materials, records)
        except Exception:
            logger.warning(
                "[ABCSupplyFIELDCRAFTImportService._resolve_created_materials] batch material id lookup failed; falling back to per-item Tenant API lookup",
                exc_info=True,
            )
            return resolved

    def _match_created_materials(
        self,
        created_materials: List[Dict[str, Any]],
        records: List[Dict[str, Any]],
    ) -> List[Optional[Dict[str, Any]]]:
        matched: List[Optional[Dict[str, Any]]] = [None] * len(records)
        if not created_materials or not records:
            return matched

        remaining_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for material in created_materials:
            normalized = self._normalize_fieldcraft_material(material)
            for key in self._fieldcraft_material_match_keys(normalized):
                remaining_by_key.setdefault(key, []).append(normalized)

        for index, record in enumerate(records):
            keys = self._fieldcraft_material_match_keys(record.get("material") or {})
            for key in keys:
                candidates = remaining_by_key.get(key) or []
                if candidates:
                    matched[index] = candidates.pop(0)
                    break
        return matched

    @classmethod
    def _normalize_fieldcraft_material(cls, material: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        normalized = dict(material or {})
        if normalized.get("materialId") in (None, ""):
            normalized["materialId"] = normalized.get("material_id") or normalized.get("id")
        if normalized.get("vendorMaterialMapId") in (None, ""):
            normalized["vendorMaterialMapId"] = (
                normalized.get("vendor_material_map_id")
                or normalized.get("vendor_material_id")
                or normalized.get("vendorMapId")
            )
        if normalized.get("materialCostId") in (None, ""):
            normalized["materialCostId"] = normalized.get("material_cost_id") or normalized.get("costId")
        if normalized.get("itemCode") in (None, ""):
            normalized["itemCode"] = normalized.get("item_code") or normalized.get("itemNumber")
        if normalized.get("vendorId") in (None, ""):
            normalized["vendorId"] = normalized.get("vendor_id")
        return normalized

    @classmethod
    def _has_fieldcraft_material_ids(cls, material: Optional[Dict[str, Any]]) -> bool:
        return cls._local_id(
            material,
            "vendorMaterialMapId",
            "vendor_material_map_id",
            "vendor_material_id",
            "vendorMapId",
        ) is not None

    @classmethod
    def _fieldcraft_material_match_keys(cls, material: Dict[str, Any]) -> List[str]:
        if not isinstance(material, dict):
            return []

        keys: List[str] = []
        item_code = str(material.get("itemCode") or material.get("item_code") or material.get("itemNumber") or "").strip().lower()
        if item_code:
            keys.append(f"item:{item_code}")

        name = str(material.get("name") or "").strip().lower().replace("\n", " ")
        grade = str(material.get("grade") or "").strip().lower()
        vendor_id = str(material.get("vendorId") or material.get("vendor_id") or "").strip().lower()
        if name or grade or vendor_id:
            keys.append(f"material:{name}|{grade}|{vendor_id}")
        return keys

    def _created_result(
        self,
        record: Dict[str, Any],
        options: ABCMaterialImportOptions,
        resolved_material: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        material = record["material"]
        resolved = resolved_material if self._has_fieldcraft_material_ids(resolved_material) else None
        if resolved is None:
            resolved = self._find_existing_fieldcraft_material(material, options)
        mapped = self._save_material_external_map(
            material=material,
            item=record.get("abcItem") or {},
            price_line=record.get("priceLine"),
            options=options,
            fieldcraft_material=resolved,
            reason="ABC Supply item imported into FIELDCRAFT material list.",
        )
        return {
            **self._created_summary(material),
            "status": "ADDED",
            **mapped,
        }

    def _save_material_external_map(
        self,
        *,
        material: Dict[str, Any],
        item: Dict[str, Any],
        price_line: Optional[Dict[str, Any]],
        options: ABCMaterialImportOptions,
        fieldcraft_material: Optional[Dict[str, Any]],
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = str(options.tenant_id or "").strip()
        service_id = str(options.service_id or "").strip()
        item_number = str(material.get("itemCode") or self._item_number(item) or "").strip()
        vendor_material_map_id = self._local_id(
            fieldcraft_material,
            "vendorMaterialMapId",
            "vendor_material_map_id",
            "vendor_material_id",
            "id",
        )
        material_id = self._local_id(fieldcraft_material, "materialId", "material_id")
        material_cost_id = self._local_id(fieldcraft_material, "materialCostId", "material_cost_id")

        if not tenant_id or not service_id or not item_number:
            return {"mappingStatus": "SKIPPED", "mappingReason": "Missing tenant_id, service_id, or external item id."}
        if vendor_material_map_id is None:
            return {"mappingStatus": "SKIPPED", "mappingReason": "FIELDCRAFT vendor material id was not returned by Tenant API."}

        external_account_id = None
        additional_info = {
            "branchNumber": options.branch_number,
            "shipToNumber": options.ship_to_number,
            "billToNumber": options.bill_to_number,
        }

        try:
            mapping = self.material_map_repository.upsert_current_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                source_entity_type="vendor_material_map",
                source_entity_id=vendor_material_map_id,
                external_item_id=item_number,
                external_account_id=external_account_id,
                external_display_name=material.get("name") or self._item_name(item),
                external_item_type="MATERIAL",
                sync_status="SYNCED",
                reason=reason,
                additional_info=additional_info,
                created_by=options.created_by,
            )
            row = mapping.get("row") or {}
            return {
                "mappingStatus": "SYNCED",
                "materialExternalMapId": row.get("id"),
                "materialId": material_id,
                "vendorMaterialMapId": vendor_material_map_id,
                "materialCostId": material_cost_id,
                "externalAccountId": external_account_id,
            }
        except Exception as exc:
            logger.exception(
                "[ABCSupplyFIELDCRAFTImportService._save_material_external_map] failed for itemNumber=%s",
                item_number,
            )
            return {"mappingStatus": "FAILED", "mappingReason": str(exc)}

    def _find_existing_external_map(
        self,
        item_number: Optional[str],
        options: ABCMaterialImportOptions,
    ) -> Optional[Dict[str, Any]]:
        if not item_number or not options.tenant_id or not options.service_id:
            return None
        try:
            return self.material_map_repository.get_current_by_external_item(
                tenant_id=str(options.tenant_id),
                service_id=options.service_id,
                external_item_id=item_number,
                external_account_id=None,
            )
        except Exception:
            logger.warning(
                "[ABCSupplyFIELDCRAFTImportService._find_existing_external_map] lookup failed for itemNumber=%s; continuing with FIELDCRAFT duplicate check",
                item_number,
                exc_info=True,
            )
            return None

    def list_imported_materials(
        self,
        *,
        tenant_id: Optional[str],
        service_id: Optional[str] = None,
        external_item_ids: Optional[List[str]] = None,
        branch_number: Optional[str] = None,
        ship_to_number: Optional[str] = None,
        bill_to_number: Optional[str] = None,
        vendor_id: Optional[Any] = None,
        search: Optional[str] = None,
        limit: int = 100,
        page: int = 1,
    ) -> Dict[str, Any]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        resolved_service_id = self._resolve_service_id(service_id)
        offset = max(0, int(page or 1) - 1) * max(1, int(limit or 100))
        rows = self.material_map_repository.list_imported_materials(
            tenant_id=str(tenant_id),
            service_id=resolved_service_id,
            external_item_ids=external_item_ids,
            external_account_id=None,
            vendor_id=vendor_id,
            search=search,
            limit=limit,
            offset=offset,
        )
        total = self.material_map_repository.count_imported_materials(
            tenant_id=str(tenant_id),
            service_id=resolved_service_id,
            external_item_ids=external_item_ids,
            external_account_id=None,
            vendor_id=vendor_id,
            search=search,
        )
        limit_value = max(1, int(limit or 100))
        page_value = max(1, int(page or 1))
        return {
            "items": [self._format_imported_material(row) for row in rows],
            "page": page_value,
            "limit": limit_value,
            "total": total,
            "totalPages": (total + limit_value - 1) // limit_value if total else 0,
            "serviceId": resolved_service_id,
        }

    def _with_resolved_service_id(self, options: ABCMaterialImportOptions) -> ABCMaterialImportOptions:
        if options.service_id:
            return options
        if not options.tenant_id:
            return options
        try:
            return replace(options, service_id=self._resolve_service_id(options.service_id))
        except Exception:
            logger.warning(
                "[ABCSupplyFIELDCRAFTImportService._with_resolved_service_id] could not resolve ABC service_id; continuing without map tracking",
                exc_info=True,
            )
            return options

    def _resolve_service_id(self, service_id: Optional[str] = None) -> str:
        return self.abc_repository.resolve_service_id(service_id)

    @staticmethod
    def _external_account_id(options: ABCMaterialImportOptions) -> Optional[str]:
        return None

    @staticmethod
    def _local_id(source: Optional[Dict[str, Any]], *keys: str) -> Optional[int]:
        if not isinstance(source, dict):
            return None
        for key in keys:
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _safe_json_value(value: Any) -> Any:
        if value is None:
            return None
        try:
            json.dumps(value, default=str)
            return value
        except Exception:
            return str(value)

    @staticmethod
    def _format_imported_material(row: Dict[str, Any]) -> Dict[str, Any]:
        info = row.get("additional_info") if isinstance(row.get("additional_info"), dict) else {}
        return {
            "materialExternalMapId": row.get("mapping_id"),
            "serviceId": row.get("service_id"),
            "status": row.get("sync_status"),
            "externalItemId": row.get("external_item_id"),
            "externalAccountId": row.get("external_account_id"),
            "externalDisplayName": row.get("external_display_name"),
            "externalItemType": row.get("external_item_type"),
            "materialId": row.get("material_id") or info.get("materialId"),
            "materialCostId": row.get("material_cost_id") or info.get("materialCostId"),
            "vendorMaterialMapId": row.get("source_entity_id") or info.get("vendorMaterialMapId"),
            "vendorId": row.get("vendor_id") or info.get("vendorId"),
            "vendorName": row.get("vendor_name") or info.get("vendorName"),
            "name": row.get("material_name") or row.get("external_display_name"),
            "itemNumber": row.get("external_item_id") or row.get("item_code") or info.get("itemNumber"),
            "itemCode": row.get("item_code") or row.get("external_item_id"),
            "manufacturer": row.get("manufacturer") or info.get("manufacturer"),
            "category": info.get("category"),
            "trade": row.get("tags"),
            "unit": row.get("cost_unit") or row.get("material_unit") or info.get("uom"),
            "grade": row.get("grade"),
            "cost": row.get("cost") if row.get("cost") is not None else info.get("price"),
            "branchNumber": info.get("branchNumber"),
            "shipToNumber": info.get("shipToNumber"),
            "billToNumber": info.get("billToNumber"),
            "createdBy": row.get("created_by"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "reason": row.get("reason"),
            "additionalInfo": info,
        }

    @staticmethod
    def _created_summary(material: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "itemNumber": material.get("itemCode"),
            "name": material.get("name"),
            "cost": material.get("cost"),
            "unit": material.get("unit"),
        }

    @staticmethod
    def _dedupe_by_item_number(items: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        seen = set()
        unique: List[Dict[str, Any]] = []
        duplicate_count = 0
        for item in items:
            item_number = ABCSupplyFIELDCRAFTImportService._item_number(item)
            key = item_number or str(item)
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            unique.append(item)
        return unique, duplicate_count

    @staticmethod
    def _item_number(item: Dict[str, Any]) -> str:
        return str(item.get("itemNumber") or item.get("sku") or item.get("externalId") or "").strip()

    @staticmethod
    def _item_name(item: Dict[str, Any]) -> str:
        return str(item.get("itemDescription") or item.get("description") or item.get("name") or "").strip()

    @staticmethod
    def _abc_uom(item: Dict[str, Any]) -> str:
        return str(item.get("uom") or item.get("unitOfMeasure") or item.get("uomCode") or "").strip()

    def _load_uom_mapping(self) -> Dict[str, str]:
        configured_map = self.uom_mapping_config.get_uom_map()
        fallback_map = {
            self._normalize_uom(external_uom): self._normalize_uom(fieldcraft_uom)
            for external_uom, fieldcraft_uom in self.ABC_TO_FIELDCRAFT_UOM.items()
            if self._normalize_uom(external_uom) and self._normalize_uom(fieldcraft_uom)
        }
        # Existing code mapping remains as fallback. Blob config can add or override entries.
        return {**fallback_map, **configured_map}

    def _get_valid_fieldcraft_uom_codes(self, options: ABCMaterialImportOptions) -> set[str]:
        units = self.tenant_client.get_units_of_measure(options.tenant_api_token)
        codes = {
            self._normalize_uom(unit.get("code") if isinstance(unit, dict) else unit)
            for unit in units
        }
        codes.discard("")
        if not codes:
            raise ValueError("FIELDCRAFT UOM list is empty")
        return codes

    def _resolve_fieldcraft_uom(
        self,
        item: Dict[str, Any],
        abc_uom: Any,
        uom_mapping: Dict[str, str],
        valid_fieldcraft_uom_codes: set[str],
    ) -> str:
        original_uom = str(abc_uom or "").strip()
        normalized_abc_uom = self._normalize_uom(original_uom)
        if not normalized_abc_uom:
            raise ABCMaterialImportSkip(
                "This ABC Supply material does not include a unit of measure (UOM), so it cannot be added. Please contact FIELDCRAFT support if you need help importing this item.",
                "ABC_UOM_MISSING",
            )

        mapped_uom = uom_mapping.get(normalized_abc_uom)
        if not mapped_uom:
            raise ABCMaterialImportSkip(
                f'This ABC Supply material uses UOM "{original_uom}", which is not supported for import yet. Please contact FIELDCRAFT support to add this UOM and try again.',
                "UOM_MAPPING_MISSING",
            )

        mapped_uom = self._normalize_uom(mapped_uom)
        if mapped_uom not in valid_fieldcraft_uom_codes:
            raise ABCMaterialImportSkip(
                f'This ABC Supply material uses UOM "{original_uom}". To add this item, please add UOM "{mapped_uom}" in your account and try again, or contact FIELDCRAFT support.',
                "FIELDCRAFT_UOM_MISSING",
            )
        return mapped_uom

    @staticmethod
    def _normalize_uom(value: Any) -> str:
        text = str(value or "").strip()
        if " - " in text:
            text = text.split(" - ", 1)[0].strip()
        return text.upper().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _first_text(source: Dict[str, Any], keys: List[str]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        for key in keys:
            value = source.get(key)
            if value not in (None, "", []):
                return str(value).strip()
        return None

    @staticmethod
    def _decimal_text(value: Any, default: str) -> str:
        candidate = default if value in (None, "", []) else value
        try:
            return str(Decimal(str(candidate)).quantize(Decimal("0.0001")))
        except (InvalidOperation, ValueError):
            return str(Decimal(str(default)).quantize(Decimal("0.0001")))

    @staticmethod
    def _required_price_text(value: Any) -> Optional[str]:
        if value in (None, "", []):
            return None
        try:
            price = Decimal(str(value)).quantize(Decimal("0.0001"))
        except (InvalidOperation, ValueError):
            return None
        if price <= Decimal("0"):
            return None
        return str(price)

    @staticmethod
    def _trim(value: Any, limit: int) -> str:
        text = str(value or "").strip()
        return text[:limit]

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _chunks(items: List[Any], size: int) -> Iterable[List[Any]]:
        for idx in range(0, len(items), size):
            yield items[idx : idx + size]

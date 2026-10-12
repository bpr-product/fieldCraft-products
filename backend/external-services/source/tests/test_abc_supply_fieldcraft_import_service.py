from source.service.ABCSupplyFIELDCRAFTImportService import ABCMaterialImportOptions, ABCSupplyFIELDCRAFTImportService


class FakeABCService:
    def __init__(self):
        self.price_calls = []

    def get_account_items(self, **kwargs):
        return {
            "items": [
                {
                    "itemNumber": "01CT25CS",
                    "itemDescription": "CertainTeed XT 25 Algae Resistant Shingles Colonial Slate",
                    "manufacturerName": "CertainTeed",
                    "categoryName": "Steep Slope Roofing",
                    "uom": "BD",
                },
                {
                    "itemNumber": "01CT25CS",
                    "itemDescription": "Duplicate Should Be Deduped",
                    "manufacturerName": "CertainTeed",
                    "uom": "BD",
                },
                {
                    "itemNumber": "02GASTZ3WW",
                    "itemDescription": "GAF Timberline Shingle Weathered Wood",
                    "manufacturerName": "GAF",
                    "productTypeName": "Shingles",
                    "uom": "SQ",
                },
            ]
        }

    def price_items(self, **kwargs):
        self.price_calls.append(kwargs)
        return {
            "lines": [
                {"itemNumber": "01CT25CS", "unitPrice": 42.25},
                {"itemNumber": "02GASTZ3WW", "unitPrice": 126.75},
            ]
        }

    def search_items(self, **kwargs):
        return {"items": []}

    def get_all_items(self, **kwargs):
        return {"items": []}

    def get_item(self, **kwargs):
        return {
            "itemNumber": kwargs.get("item_number"),
            "itemDescription": "Single ABC Item",
            "manufacturerName": "Unknown",
            "uom": "EA",
        }


class FakeTenantClient:
    def __init__(self, existing_item_codes=None, uom_codes=None):
        self.existing_item_codes = set(existing_item_codes or [])
        self.uom_codes = list(uom_codes or [
            "EA", "PCS", "BD", "SQ", "SF", "LF", "GAL", "BX", "ROLL", "TB", "PLT", "LB", "DZ", "PR", "SET"
        ])
        self.created_batches = []
        self.checked = []
        self.uom_calls = 0

    def get_vendor_material(self, token, material_dto):
        self.checked.append(material_dto)
        # Simulate existing by material name for the CertainTeed item.
        if material_dto.get("name", "").startswith("CertainTeed") and "01CT25CS" in self.existing_item_codes:
            return {"vendorMaterialMapId": 123}
        return None

    def get_units_of_measure(self, token):
        self.uom_calls += 1
        return [{"code": code, "name": code} for code in self.uom_codes]

    def add_materials(self, token, materials):
        self.created_batches.append(materials)
        return "Inserted"


def test_import_recent_materials_builds_fieldcraft_payload_and_creates_new_materials():
    tenant = FakeTenantClient(existing_item_codes={"01CT25CS"})
    service = ABCSupplyFIELDCRAFTImportService(abc_service=FakeABCService(), tenant_client=tenant)

    result = service.import_materials(
        ABCMaterialImportOptions(
            fieldcraft_vendor_id=99,
            tenant_api_token="TOKEN",
            source="recent",
            default_trade="Roofing",
            default_grade="a",
        )
    )

    assert result["ok"] is True
    assert result["counts"]["fetched"] == 3
    assert result["counts"]["duplicateFromSource"] == 1
    assert result["counts"]["skippedExisting"] == 1
    assert result["counts"]["created"] == 1

    created = tenant.created_batches[0][0]
    assert created["vendorId"] == 99
    assert created["companyName"] == "ABC Supply"
    assert created["name"] == "GAF Timberline Shingle Weathered Wood"
    assert created["itemCode"] == "02GASTZ3WW"
    assert created["manufacturer"] == "GAF"
    assert created["unit"] == "SQ"
    assert created["baseUnit"] == "SQ"
    assert created["cost"] == "126.7500"
    assert created["conversionFactor"] == "1.0000"
    assert created["grade"] == "a"
    assert created["tags"] == "Roofing"
    assert created["allowDecimal"] is True
    assert created["inStock"] is True


def test_dry_run_returns_payload_without_calling_tenant_api():
    tenant = FakeTenantClient()
    service = ABCSupplyFIELDCRAFTImportService(abc_service=FakeABCService(), tenant_client=tenant)

    result = service.import_materials(
        ABCMaterialImportOptions(
            fieldcraft_vendor_id=77,
            source="recent",
            dry_run=True,
            fetch_prices=True,
        )
    )

    assert result["dryRun"] is True
    assert result["counts"]["prepared"] == 2
    assert len(result["materials"]) == 2
    assert tenant.created_batches == []


def test_item_numbers_source_skips_items_without_pricing():
    tenant = FakeTenantClient()
    service = ABCSupplyFIELDCRAFTImportService(abc_service=FakeABCService(), tenant_client=tenant)

    result = service.import_materials(
        ABCMaterialImportOptions(
            fieldcraft_vendor_id=55,
            source="item_numbers",
            item_numbers=["ABC123"],
            fetch_prices=False,
        )
    )

    assert result["counts"]["created"] == 0
    assert result["counts"]["skippedMissingPrice"] == 1
    assert result["skipped"][0]["itemNumber"] == "ABC123"
    assert result["skipped"][0]["reasonCode"] == "MISSING_PRICING"
    assert tenant.created_batches == []


def test_requires_fieldcraft_vendor_id():
    service = ABCSupplyFIELDCRAFTImportService(abc_service=FakeABCService(), tenant_client=FakeTenantClient())

    try:
        service.import_materials(ABCMaterialImportOptions())
    except ValueError as exc:
        assert "fieldcraft_vendor_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_import_selected_materials_uses_selected_items_without_fetching_source():
    tenant = FakeTenantClient()
    abc = FakeABCService()
    service = ABCSupplyFIELDCRAFTImportService(abc_service=abc, tenant_client=tenant)

    result = service.import_selected_materials(
        [
            {
                "itemNumber": "01CT25CS",
                "itemDescription": "CertainTeed XT 25 Algae Resistant Shingles Colonial Slate",
                "manufacturerName": "CertainTeed",
                "uom": "BD",
            }
        ],
        ABCMaterialImportOptions(
            fieldcraft_vendor_id=88,
            tenant_api_token="TOKEN",
            fetch_prices=True,
            source="selected",
        ),
    )

    assert result["source"] == "selected"
    assert result["counts"]["fetched"] == 1
    assert result["counts"]["created"] == 1
    assert abc.price_calls
    created = tenant.created_batches[0][0]
    assert created["vendorId"] == 88
    assert created["itemCode"] == "01CT25CS"
    assert created["unit"] == "BD"


def test_import_selected_materials_requires_materials():
    service = ABCSupplyFIELDCRAFTImportService(abc_service=FakeABCService(), tenant_client=FakeTenantClient())

    try:
        service.import_selected_materials([], ABCMaterialImportOptions(fieldcraft_vendor_id=88))
    except ValueError as exc:
        assert "materials are required" in str(exc)
    else:
        raise AssertionError("expected ValueError")


class FakeUomMappingConfig:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def get_uom_map(self):
        return dict(self.mapping)


class FakeABCServiceWithMissingPrice(FakeABCService):
    def price_items(self, **kwargs):
        self.price_calls.append(kwargs)
        return {
            "lines": [
                {"itemNumber": "01CT25CS", "unitPrice": 42.25},
            ]
        }


def test_import_selected_materials_skips_materials_without_abc_pricing():
    tenant = FakeTenantClient()
    abc = FakeABCServiceWithMissingPrice()
    service = ABCSupplyFIELDCRAFTImportService(abc_service=abc, tenant_client=tenant)

    result = service.import_selected_materials(
        [
            {
                "itemNumber": "01CT25CS",
                "itemDescription": "CertainTeed XT 25 Algae Resistant Shingles Colonial Slate",
                "manufacturerName": "CertainTeed",
                "uom": "BD",
            },
            {
                "itemNumber": "02GASTZ3WW",
                "itemDescription": "GAF Timberline Shingle Weathered Wood",
                "manufacturerName": "GAF",
                "uom": "SQ",
            },
        ],
        ABCMaterialImportOptions(
            fieldcraft_vendor_id=88,
            tenant_api_token="TOKEN",
            fetch_prices=True,
            source="selected",
            branch_number="001",
            ship_to_number="SHIP-1",
            bill_to_number="BILL-1",
        ),
    )

    assert result["counts"]["created"] == 1
    assert result["counts"]["skippedMissingPrice"] == 1
    assert result["created"][0]["itemNumber"] == "01CT25CS"
    assert result["skipped"][0]["itemNumber"] == "02GASTZ3WW"
    assert result["skipped"][0]["reasonCode"] == "MISSING_PRICING"
    created = tenant.created_batches[0][0]
    assert created["cost"] == "42.2500"




class FakeMaterialMapRepository:
    def __init__(self, resolved_materials=None):
        self.resolved_materials = list(resolved_materials or [])
        self.resolve_calls = []
        self.mappings = []

    def get_current_by_external_item(self, **kwargs):
        return None

    def find_vendor_materials_for_mapping(self, **kwargs):
        self.resolve_calls.append(kwargs)
        return self.resolved_materials

    def upsert_current_mapping(self, **kwargs):
        self.mappings.append(kwargs)
        return {"row": {"id": 501}}


class FailingTenantClient(FakeTenantClient):
    def get_vendor_material(self, token, material_dto):
        raise RuntimeError("Tenant API unavailable")


def test_exists_in_fieldcraft_fails_closed_on_duplicate_check_error():
    service = ABCSupplyFIELDCRAFTImportService(abc_service=FakeABCService(), tenant_client=FailingTenantClient())

    exists = service._exists_in_fieldcraft(
        {"name": "GAF Timberline Shingle Weathered Wood", "grade": "a", "vendorId": 99, "itemCode": "02GASTZ3WW"},
        ABCMaterialImportOptions(fieldcraft_vendor_id=99, tenant_api_token="TOKEN"),
    )

    assert exists is True


def test_created_material_mapping_uses_batch_db_lookup_without_post_create_tenant_lookup():
    tenant = FakeTenantClient()
    repo = FakeMaterialMapRepository(
        resolved_materials=[
            {
                "materialId": 2001,
                "vendorMaterialMapId": 3001,
                "materialCostId": 4001,
                "vendorId": 88,
                "itemCode": "01CT25CS",
                "name": "CertainTeed XT 25 Algae Resistant Shingles Colonial Slate",
                "grade": "a",
            }
        ]
    )
    service = ABCSupplyFIELDCRAFTImportService(
        abc_service=FakeABCService(),
        tenant_client=tenant,
        material_map_repository=repo,
    )

    result = service.import_selected_materials(
        [
            {
                "itemNumber": "01CT25CS",
                "itemDescription": "CertainTeed XT 25 Algae Resistant Shingles Colonial Slate",
                "manufacturerName": "CertainTeed",
                "uom": "BD",
            }
        ],
        ABCMaterialImportOptions(
            tenant_id="tn1",
            service_id="6",
            fieldcraft_vendor_id=88,
            tenant_api_token="TOKEN",
            fetch_prices=True,
            source="selected",
            branch_number="001",
            ship_to_number="SHIP-1",
            bill_to_number="BILL-1",
        ),
    )

    assert result["counts"]["created"] == 1
    assert len(tenant.checked) == 1  # duplicate check only; no extra post-create lookup
    assert len(repo.resolve_calls) == 1
    assert repo.resolve_calls[0]["vendor_id"] == 88
    assert repo.mappings[0]["source_entity_type"] == "vendor_material_map"
    assert repo.mappings[0]["source_entity_id"] == 3001
    assert repo.mappings[0]["external_account_id"] is None
    assert repo.mappings[0]["additional_info"] == {
        "branchNumber": "001",
        "shipToNumber": "SHIP-1",
        "billToNumber": "BILL-1",
    }
    assert result["created"][0]["mappingStatus"] == "SYNCED"
    assert result["created"][0]["externalAccountId"] is None


class FakeABCServiceWithCustomUom(FakeABCService):
    def __init__(self, uom):
        super().__init__()
        self.uom = uom

    def price_items(self, **kwargs):
        self.price_calls.append(kwargs)
        return {"lines": [{"itemNumber": "ABC-UOM-1", "unitPrice": 25}]}


def test_blob_uom_mapping_overrides_code_fallback_before_fieldcraft_import():
    tenant = FakeTenantClient(uom_codes=["BX"])
    service = ABCSupplyFIELDCRAFTImportService(
        abc_service=FakeABCServiceWithCustomUom("BOX"),
        tenant_client=tenant,
        uom_mapping_config=FakeUomMappingConfig({"BOX": "BX"}),
    )

    result = service.import_selected_materials(
        [
            {
                "itemNumber": "ABC-UOM-1",
                "itemDescription": "Box Mapped Material",
                "manufacturerName": "ABC",
                "uom": "BOX",
            }
        ],
        ABCMaterialImportOptions(fieldcraft_vendor_id=88, tenant_api_token="TOKEN", fetch_prices=True),
    )

    assert result["counts"]["created"] == 1
    created = tenant.created_batches[0][0]
    assert created["unit"] == "BX"
    assert created["baseUnit"] == "BX"


def test_unknown_abc_uom_without_blob_or_fallback_mapping_is_skipped_with_clear_reason():
    tenant = FakeTenantClient(uom_codes=["EA"])
    service = ABCSupplyFIELDCRAFTImportService(
        abc_service=FakeABCServiceWithCustomUom("PALLET_CASE"),
        tenant_client=tenant,
        uom_mapping_config=FakeUomMappingConfig({}),
    )

    result = service.import_selected_materials(
        [
            {
                "itemNumber": "ABC-UOM-1",
                "itemDescription": "Unmapped UOM Material",
                "manufacturerName": "ABC",
                "uom": "PALLET_CASE",
            }
        ],
        ABCMaterialImportOptions(fieldcraft_vendor_id=88, tenant_api_token="TOKEN", fetch_prices=True),
    )

    assert result["counts"]["created"] == 0
    assert result["counts"]["skippedInvalid"] == 1
    assert result["skipped"][0]["reasonCode"] == "UOM_MAPPING_MISSING"
    assert "PALLET_CASE" in result["skipped"][0]["reason"]
    assert "contact FIELDCRAFT support" in result["skipped"][0]["reason"]
    assert tenant.created_batches == []


def test_mapped_uom_missing_in_fieldcraft_is_skipped_before_add_materials_call():
    tenant = FakeTenantClient(uom_codes=["EA", "SQ"])
    service = ABCSupplyFIELDCRAFTImportService(
        abc_service=FakeABCServiceWithCustomUom("BOX"),
        tenant_client=tenant,
        uom_mapping_config=FakeUomMappingConfig({"BOX": "BX"}),
    )

    result = service.import_selected_materials(
        [
            {
                "itemNumber": "ABC-UOM-1",
                "itemDescription": "Missing FIELDCRAFT UOM Material",
                "manufacturerName": "ABC",
                "uom": "BOX",
            }
        ],
        ABCMaterialImportOptions(fieldcraft_vendor_id=88, tenant_api_token="TOKEN", fetch_prices=True),
    )

    assert result["counts"]["created"] == 0
    assert result["counts"]["skippedInvalid"] == 1
    assert result["skipped"][0]["reasonCode"] == "FIELDCRAFT_UOM_MISSING"
    assert 'add UOM "BX" in your account' in result["skipped"][0]["reason"]
    assert tenant.created_batches == []


def test_code_fallback_uom_mapping_uses_canonical_targets_when_blob_unavailable():
    tenant = FakeTenantClient(uom_codes=["BX", "ROLL"])
    service = ABCSupplyFIELDCRAFTImportService(
        abc_service=FakeABCServiceWithCustomUom("BOX"),
        tenant_client=tenant,
        uom_mapping_config=FakeUomMappingConfig({}),
    )

    result = service.import_selected_materials(
        [
            {
                "itemNumber": "ABC-UOM-1",
                "itemDescription": "Fallback Box Material",
                "manufacturerName": "ABC",
                "uom": "BOX",
            }
        ],
        ABCMaterialImportOptions(fieldcraft_vendor_id=88, tenant_api_token="TOKEN", fetch_prices=True),
    )

    assert result["counts"]["created"] == 1
    created = tenant.created_batches[0][0]
    assert created["unit"] == "BX"
    assert created["baseUnit"] == "BX"

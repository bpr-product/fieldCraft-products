package com.fieldcraft.tenant.service;

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Purpose      : Unit tests for MaterialService
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *      28503     | 31-Aug-2026 |  Surya J     | Tests for getmaterialsByNameAndGrade's Purchase-
 *                |             |              | Unit-to-Base-Unit cost correction
 */

import com.fieldcraft.shared.error.BadRequestException;
import com.fieldcraft.shared.error.NotFoundException;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.shared.utils.ErrorParser;
import com.fieldcraft.tenant.dal.repos.MaterialRepository;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.data.constant.CodeConstant;
import com.fieldcraft.tenant.data.domain.Material;
import com.fieldcraft.tenant.data.domain.UnitOfMeasure;
import com.fieldcraft.tenant.data.model.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.*;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.*;
import static com.fieldcraft.shared.error.ErrorMessages.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class MaterialServiceTest {

    @Mock
    private MaterialRepository materialRepository;

    @Mock
    private VendorMaterialMapService vendorMaterialMapService;

    @Mock
    private MaterialCostService materialCostService;

    @Mock
    private CodesService codesService;

    @Mock
    private UserAccountService userAccountService;

    @Mock
    private ServiceMasterRepository serviceMasterRepository;

    @Mock
    private UnitOfMeasureService unitOfMeasureService;

    @InjectMocks
    private MaterialService materialService;

    @BeforeEach
    void setup() {
        CodeConstant realConstant = new CodeConstant();
        realConstant.setMaterials("MATERIAL_CODE");

        ReflectionTestUtils.setField(codesService, "constant", realConstant);
    }

    @Test
    void createMaterial_shouldInsertSuccessfully() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");
        dto.setVendorId(1L);
        dto.setAllowDecimal(false);
        dto.setBaseUnit("KG");
        dto.setUnit("KG");
        UnitOfMeasure uom = new UnitOfMeasure();
        uom.setCode("KG");
        uom.setName("Kilogram");
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(uom);
        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenReturn(null);
        Material savedMaterial = new Material();
        savedMaterial.setId(100L);
        when(materialRepository.save(any())).thenReturn(savedMaterial);
        when(vendorMaterialMapService.create(any())).thenReturn(200L);
        when(materialCostService.create(any(), any())).thenReturn(300L);
        String result = materialService.createMaterial(List.of(dto));
        assertEquals("Inserted", result);
    }

    @Test
    void createMaterial_shouldThrowDuplicateServerException() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");

        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenThrow(new RuntimeException("Duplicate"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(true);

            assertThrows(ServerException.class, () ->
                    materialService.createMaterial(List.of(dto))
            );
        }
    }

    @Test
    void createMaterial_shouldUseExistingMaterial() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");
        dto.setVendorId(1L);

        Material existing = new Material();
        existing.setId(50L);

        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenReturn(existing);

        when(vendorMaterialMapService.create(any())).thenReturn(200L);
        when(materialCostService.create(any(), any())).thenReturn(300L);

        String result = materialService.createMaterial(List.of(dto));

        assertEquals("Inserted", result);
    }

    @Test
    void createMaterial_shouldRethrowServerException() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");

        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenThrow(new ServerException("ERR", "desc"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(false);

            assertThrows(ServerException.class, () ->
                    materialService.createMaterial(List.of(dto))
            );
        }
    }

    @Test
    void createMaterial_shouldWrapGenericException() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");

        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenThrow(new RuntimeException("DB error"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(false);

            ServerException ex = assertThrows(ServerException.class, () ->
                    materialService.createMaterial(List.of(dto))
            );

            assertEquals(MATERIAL_ADD_ERROR, ex.getErrorCode());
        }
    }

    @Test
    void createMaterial_shouldSkipCostCreation_whenVendorMapIsNull() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");
        dto.setVendorId(1L);
        dto.setAllowDecimal(false);
        dto.setBaseUnit("KG");
        dto.setUnit("KG");
        UnitOfMeasure uom = new UnitOfMeasure();
        uom.setCode("KG");
        uom.setName("Kilogram");
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(uom);

        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenReturn(null);
        Material savedMaterial = new Material();
        savedMaterial.setId(100L);
        when(materialRepository.save(any())).thenReturn(savedMaterial);
        when(vendorMaterialMapService.create(any())).thenReturn(null);
        String result = materialService.createMaterial(List.of(dto));
        assertEquals("Inserted", result);
        verify(materialCostService, never()).create(any(), any());
    }

    @Test
    void createMaterial_shouldHandleExistingMaterial_whenVendorMapIsNull() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");
        dto.setVendorId(1L);
        Material existing = new Material();
        existing.setId(50L);
        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenReturn(existing);
        when(vendorMaterialMapService.create(any())).thenReturn(null);
        String result = materialService.createMaterial(List.of(dto));
        assertEquals("Inserted", result);
        verify(materialCostService, never()).create(any(), any());
    }

    @Test
    void createMaterial_shouldProcessMultipleMaterials() {
        MaterialDTO dto1 = new MaterialDTO();
        dto1.setName("Steel");
        dto1.setGrade("A");
        dto1.setAllowDecimal(false);
        dto1.setBaseUnit("KG");
        dto1.setUnit("KG");
        MaterialDTO dto2 = new MaterialDTO();
        dto2.setName("Cement");
        dto2.setGrade("B");
        dto2.setAllowDecimal(false);
        dto2.setBaseUnit("KG");
        dto2.setUnit("KG");
        UnitOfMeasure uom = new UnitOfMeasure();
        uom.setCode("KG");
        uom.setName("Kilogram");
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(uom);
        when(materialRepository.findMaterialNameAndGrade(anyString(), any()))
                .thenReturn(null);
        Material saved = new Material();
        saved.setId(1L);
        when(materialRepository.save(any())).thenReturn(saved);
        when(vendorMaterialMapService.create(any())).thenReturn(10L);
        when(materialCostService.create(any(), any())).thenReturn(20L);
        String result = materialService.createMaterial(List.of(dto1, dto2));
        assertEquals("Inserted", result);
        verify(materialRepository, times(2)).save(any());
    }

    @Test
    void findAll_shouldReturnMappedDTOList() {
        Material material = new Material();
        material.setId(1L);
        material.setName("Steel");

        when(materialRepository.findAll(any(Sort.class)))
                .thenReturn(List.of(material));

        List<MaterialDTO> result = materialService.findAll();

        assertEquals(1, result.size());
        assertEquals("Steel", result.get(0).getName());
    }

    @Test
    void get_shouldReturnMaterial() {
        Material material = new Material();
        material.setId(1L);
        material.setName("Steel");

        when(materialRepository.findById(1L))
                .thenReturn(Optional.of(material));

        MaterialDTO result = materialService.get(1L);

        assertEquals("Steel", result.getName());
    }

    @Test
    void get_shouldThrowNotFound() {
        when(materialRepository.findById(1L))
                .thenReturn(Optional.empty());

        assertThrows(NotFoundException.class, () -> materialService.get(1L));
    }

    @Test
    void findBySearchCriteria_shouldReturnResults() {
        Pageable pageable = PageRequest.of(0, 10);

        when(materialRepository.searchMaterials(any(), any(), any(), any(), any()))
                .thenReturn(List.of());

        List<MaterialInfo> result = materialService.findBySearchCriteria(
                "Steel", "materialname", "ASC", "trade", "A", pageable
        );

        assertNotNull(result);
    }

    @Test
    void findBySearchCriteria_shouldReturnEmptyOnException() {
        Pageable pageable = PageRequest.of(0, 10);

        when(materialRepository.searchMaterials(any(), any(), any(), any(), any()))
                .thenThrow(new RuntimeException());

        List<MaterialInfo> result = materialService.findBySearchCriteria(
                "Steel", null, null, null, null, pageable
        );

        assertTrue(result.isEmpty());
    }

    @Test
    void create_shouldThrowDuplicateException() {
        MaterialDTO dto = buildValidDTO();
        UnitOfMeasure uom = new UnitOfMeasure();
        uom.setCode("KG");
        uom.setName("Kilogram");
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(uom);

        when(materialRepository.save(any()))
                .thenThrow(new DataIntegrityViolationException("error"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(true);

            assertThrows(BadRequestException.class, () -> materialService.create(dto));
        }
    }

    private MaterialDTO buildValidDTO() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Steel");
        dto.setGrade("A");
        dto.setVendorId(1L);
        dto.setAllowDecimal(false);
        dto.setBaseUnit("KG");
        dto.setUnit("KG");
        return dto;
    }

    @Test
    void getmaterialsByNameAndGrade_shouldReturnNull_whenInvalidInput() {
        MaterialDTO dto = new MaterialDTO();

        assertNull(materialService.getmaterialsByNameAndGrade(dto));
    }

    // AB#28503: findMaterialByNameAndGrade's mc.cost is per Purchase Unit (e.g. per Bundle) --
    // AddMaterial.tsx's "Create BOM -> Vendor" dialog displays and saves this response's
    // unit/cost verbatim, so getmaterialsByNameAndGrade must derive the true per-Base-Unit rate
    // here, the same way BomService/TaskService/EstimateAutomationService already do.
    @Test
    void getmaterialsByNameAndGrade_correctsToBaseUnitRate_whenConversionFactorPresent() {
        MaterialDTO dto = buildValidDTO();

        MaterialInfo match = mock(MaterialInfo.class);
        when(match.getCost()).thenReturn(java.math.BigDecimal.valueOf(90.0)); // per Purchase Unit (bundle)
        when(match.getConversionFactor()).thenReturn(java.math.BigDecimal.valueOf(45.0));
        when(match.getBaseUnit()).thenReturn("Each");
        when(match.getName()).thenReturn("Portland Brick");
        // getUnit() deliberately left unstubbed -- the correction overrides it with baseUnit
        // ("Each") directly, so the original Purchase Unit ("BD - Bundle") is never read back.
        when(materialRepository.findMaterialByNameAndGrade(anyString(), anyString(), any(), any()))
                .thenReturn(match);

        MaterialInfo result = materialService.getmaterialsByNameAndGrade(dto);

        // 90 / 45 = 2.00, not the raw 90.0 Purchase-Unit cost.
        assertEquals(0, java.math.BigDecimal.valueOf(2.0).compareTo(result.getCost()));
        assertEquals("Each", result.getUnit());
        // Every other field passes through the original match unchanged.
        assertEquals("Portland Brick", result.getName());
    }

    // AB#30036: when a Selling Price is on file for this material, the dialog must display/save
    // that (per Base Unit) instead of the raw vendor purchase cost -- see SellingPriceCalculator.
    @Test
    void getmaterialsByNameAndGrade_usesFlatSellingPrice_whenOnFile() {
        MaterialDTO dto = buildValidDTO();

        MaterialInfo match = mock(MaterialInfo.class);
        when(match.getCost()).thenReturn(java.math.BigDecimal.valueOf(90.0)); // per Purchase Unit (bundle)
        when(match.getConversionFactor()).thenReturn(java.math.BigDecimal.valueOf(45.0));
        when(match.getBaseUnit()).thenReturn("Each");
        when(match.getSellingPriceType()).thenReturn("flat");
        when(match.getSellingPriceValue()).thenReturn(java.math.BigDecimal.valueOf(135.0));
        when(materialRepository.findMaterialByNameAndGrade(anyString(), anyString(), any(), any()))
                .thenReturn(match);

        MaterialInfo result = materialService.getmaterialsByNameAndGrade(dto);

        // Selling Price 135 / conversionFactor 45 = 3.00, not 90 / 45 = 2.00 (the raw cost).
        assertEquals(0, java.math.BigDecimal.valueOf(3.0).compareTo(result.getCost()));
        assertEquals("Each", result.getUnit());
    }

    @Test
    void getmaterialsByNameAndGrade_usesPercentageSellingPrice_whenOnFile() {
        MaterialDTO dto = buildValidDTO();

        MaterialInfo match = mock(MaterialInfo.class);
        when(match.getCost()).thenReturn(java.math.BigDecimal.valueOf(90.0));
        when(match.getConversionFactor()).thenReturn(java.math.BigDecimal.valueOf(45.0));
        when(match.getBaseUnit()).thenReturn("Each");
        when(match.getSellingPriceType()).thenReturn("percentage");
        when(match.getSellingPriceValue()).thenReturn(java.math.BigDecimal.valueOf(20.0));
        when(materialRepository.findMaterialByNameAndGrade(anyString(), anyString(), any(), any()))
                .thenReturn(match);

        MaterialInfo result = materialService.getmaterialsByNameAndGrade(dto);

        // (90 + 20%) / 45 = 108 / 45 = 2.40.
        assertEquals(0, java.math.BigDecimal.valueOf(2.4).compareTo(result.getCost()));
        assertEquals("Each", result.getUnit());
    }

    @Test
    void getmaterialsByNameAndGrade_returnsRawCost_whenNoConversionFactor() {
        MaterialDTO dto = buildValidDTO();

        MaterialInfo match = mock(MaterialInfo.class);
        when(match.getCost()).thenReturn(java.math.BigDecimal.valueOf(6.5));
        when(match.getUnit()).thenReturn("box");
        // getConversionFactor()/getBaseUnit() deliberately left unstubbed (default null) --
        // short-circuits the correction.
        when(materialRepository.findMaterialByNameAndGrade(anyString(), anyString(), any(), any()))
                .thenReturn(match);

        MaterialInfo result = materialService.getmaterialsByNameAndGrade(dto);

        assertEquals(0, java.math.BigDecimal.valueOf(6.5).compareTo(result.getCost()));
        assertEquals("box", result.getUnit());
    }

    @Test
    void getGradesByMaterialName_shouldReturnDistinct() {
        when(materialRepository.findGradesByMaterialName(any(), any()))
                .thenReturn(List.of("A", "A", "B"));

        List<String> result = materialService.getGradesByMaterialName("steel", 1L);

        assertEquals(2, result.size());
    }

    @Test
    void updateMaterialCost_shouldHandleMultipleTrades() {
        MaterialDTO dto = buildValidDTO();
        dto.setId(1L);
        dto.setTags("A;B");
        UnitOfMeasure uom = new UnitOfMeasure();
        uom.setCode("KG");
        uom.setName("Kilogram");
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(uom);
        Material material = new Material();

        when(materialRepository.findById(1L)).thenReturn(Optional.of(material));

        String result = materialService.updateMaterialCost(List.of(dto));

        assertEquals("Updated Successfully", result);
    }

    @Test
    void getMaterials_shouldReturnEmpty_whenTradeEmpty() {
        List<MaterialDTO> result = materialService.getMaterials("");

        assertTrue(result.isEmpty());
    }

    @Test
    void getMaterials_shouldThrowServerException() {
        when(materialRepository.findNameByTradeName(any()))
                .thenThrow(new RuntimeException());

        assertThrows(ServerException.class, () -> materialService.getMaterials("steel"));
    }

    @Test
    void getUnitString_shouldReturnEmpty() {
        String result = ReflectionTestUtils.invokeMethod(materialService, "getUnitString", "");

        assertEquals("", result);
    }

    @Test
    void getUnitString_shouldReturnFormatted() {
        UnitOfMeasure uom = new UnitOfMeasure();
        uom.setCode("KG");
        uom.setName("Kilogram");

        when(unitOfMeasureService.findByUnitCode("KG")).thenReturn(uom);

        String result = ReflectionTestUtils.invokeMethod(materialService, "getUnitString", "KG");

        assertEquals("KG - Kilogram", result);
    }

    @Test
    void getUnitString_shouldReturnInput_whenException() {
        when(unitOfMeasureService.findByUnitCode(any()))
                .thenThrow(new IllegalArgumentException());

        String result = ReflectionTestUtils.invokeMethod(materialService, "getUnitString", "KG");

        assertEquals("KG", result);
    }

    @Test
    void getMaterialTags_shouldReturnList() {
        when(materialRepository.getTags()).thenReturn(List.of("A"));
        assertEquals(1, materialService.getMaterialTags().size());
    }

    @Test
    void getTotalMaterialCount_shouldReturnCount() {
        when(materialRepository.countTotalMaterials(any(), any(), any(), any())).thenReturn(5);
        assertEquals(5, materialService.getTotalMaterialCount("name", "trade", "grade"));
    }

    @Test
    void getTotalMaterialCount_shouldHandleNulls() {
        when(materialRepository.countTotalMaterials(any(), any(), any(), any())).thenReturn(5);
        assertEquals(5, materialService.getTotalMaterialCount("null", "null", "null"));
    }

    @Test
    void findBySearchCriteria_shouldSortCorrectly() {
        Pageable pageable = PageRequest.of(0, 10);
        when(materialRepository.searchMaterials(any(), any(), any(), any(), any())).thenReturn(List.of());

        String[] sorts = {"vendorname", "materialname", "trade", "itemcode", "unitcost", "unit", "manufacturer", "versionstartdate", "grade", "unknown"};
        for(String s : sorts) {
            materialService.findBySearchCriteria("name", s, "ASC", "trade", "grade", pageable);
        }
    }
    
    @Test
    void findBySearchCriteria_shouldHandleNulls() {
        Pageable pageable = PageRequest.of(0, 10);
        when(materialRepository.searchMaterials(any(), any(), any(), any(), any())).thenReturn(List.of());
        materialService.findBySearchCriteria("null", null, null, "null", "null", pageable);
    }

    @Test
    void getMaterialNames_shouldReturnNames() {
        when(materialRepository.findMaterialName()).thenReturn(List.of("A"));
        assertEquals(1, materialService.getMaterialNames().size());
    }

    @Test
    void getCostCode_shouldHandleException() {
        when(codesService.getNames(any())).thenThrow(new RuntimeException("Error"));
        Material material = new Material();
        assertThrows(RuntimeException.class, () -> materialService.getCostCode(material, "code"));
    }

    @Test
    void saveVendorMaterialMap_shouldReturnId() {
        MaterialDTO dto = new MaterialDTO();
        when(vendorMaterialMapService.create(any())).thenReturn(1L);
        assertEquals(1L, materialService.saveVendorMaterialMap(10L, dto));
    }

    @Test
    void saveMaterialCost_shouldCallCreate() {
        MaterialDTO dto = new MaterialDTO();
        materialService.saveMaterialCost(dto, 10L);
        verify(materialCostService, times(1)).create(any(), any());
    }

    @Test
    void update_shouldReturnId() throws com.fasterxml.jackson.core.JsonProcessingException {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        Material mat = new Material();
        mat.setId(10L);
        when(materialRepository.findById(10L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenReturn(mat);
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        assertEquals(10L, materialService.update(10L, dto));
    }

    // AB#26886/26895: a cost change made through this endpoint (Inventory's "Edit material") must
    // route through the same insertMaterialCost -> invalidProject flow Material Management's own
    // grid already uses, so affected Estimates/BOM get flagged instead of silently going stale.
    @Test
    void update_shouldTriggerInsertMaterialCost_whenCostChanges() throws com.fasterxml.jackson.core.JsonProcessingException {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        dto.setCost(new java.math.BigDecimal("25.00"));
        Material mat = new Material();
        mat.setId(10L);
        when(materialRepository.findById(10L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenReturn(mat);
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        MaterialCostDTO existing = new MaterialCostDTO();
        existing.setCost(new java.math.BigDecimal("20.00"));
        existing.setVendorMaterialMapId(55L);
        existing.setUnit("EA");
        when(materialCostService.findMostRecentByMaterialId(10L)).thenReturn(existing);

        materialService.update(10L, dto);

        verify(materialCostService, times(1)).insertMaterialCost(argThat(costDto ->
                costDto.getCost().compareTo(new java.math.BigDecimal("25.00")) == 0
                        && costDto.getVendorMaterialMapId().equals(55L)
        ));
    }

    // AB#: a material with no VendorMaterialMap (never assigned a vendor -- e.g. a stock-only
    // material) has no vendor-scoped cost history for insertMaterialCost to version, so that path
    // must not run -- but the cost/Selling Price edit must still be persisted, not silently
    // dropped, via a direct in-place update of its one MaterialCost row.
    @Test
    void update_updatesInPlace_whenCostChangesAndNoVendorMap() throws com.fasterxml.jackson.core.JsonProcessingException {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        dto.setCost(new java.math.BigDecimal("25.00"));
        dto.setSellingPriceType("flat");
        dto.setSellingPriceValue(new java.math.BigDecimal("27.00"));
        Material mat = new Material();
        mat.setId(10L);
        when(materialRepository.findById(10L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenReturn(mat);
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        MaterialCostDTO existing = new MaterialCostDTO();
        existing.setId(701L);
        existing.setCost(new java.math.BigDecimal("20.00"));
        existing.setVendorMaterialMapId(null);
        when(materialCostService.findMostRecentByMaterialId(10L)).thenReturn(existing);

        materialService.update(10L, dto);

        verify(materialCostService, never()).insertMaterialCost(any());
        verify(materialCostService, times(1)).updateInPlace(701L, dto);
    }

    @Test
    void update_shouldNotTriggerInsertMaterialCost_whenCostUnchanged() throws com.fasterxml.jackson.core.JsonProcessingException {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        dto.setCost(new java.math.BigDecimal("20.00"));
        Material mat = new Material();
        mat.setId(10L);
        when(materialRepository.findById(10L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenReturn(mat);
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        MaterialCostDTO existing = new MaterialCostDTO();
        existing.setCost(new java.math.BigDecimal("20.00"));
        existing.setVendorMaterialMapId(55L);
        when(materialCostService.findMostRecentByMaterialId(10L)).thenReturn(existing);

        materialService.update(10L, dto);

        verify(materialCostService, never()).insertMaterialCost(any());
    }

    @Test
    void update_shouldHandleDuplicateException() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        Material mat = new Material();
        when(materialRepository.findById(10L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenThrow(new DataIntegrityViolationException("err"));
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(true);
            assertThrows(BadRequestException.class, () -> materialService.update(10L, dto));
        }
    }

    @Test
    void update_shouldHandleGenericException() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        Material mat = new Material();
        when(materialRepository.findById(10L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenThrow(new DataIntegrityViolationException("err"));
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(false);
            assertThrows(BadRequestException.class, () -> materialService.update(10L, dto));
        }
    }

    @Test
    void updateMaterialCost_shouldHandleGenericException() {
        MaterialDTO dto = new MaterialDTO();
        dto.setId(10L);
        dto.setTags("tags");
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        Material mat = new Material();
        when(materialRepository.findById(10L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenThrow(new DataIntegrityViolationException("err"));
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(false);
            assertThrows(BadRequestException.class, () -> materialService.updateMaterialCost(List.of(dto)));
        }
    }

    @Test
    void delete_shouldCallRepository() {
        materialService.delete(10L);
        verify(materialRepository, times(1)).deleteById(10L);
    }

    @Test
    void getMaterials_noArgs_shouldReturnEmpty() {
        assertTrue(materialService.getMaterials().isEmpty());
    }
    
    @Test
    void getMaterials_shouldHandleDuplicates() {
        Material m1 = new Material();
        m1.setName("A");
        Material m2 = new Material();
        m2.setName("A"); // duplicate name
        when(materialRepository.findNameByTradeName("trade")).thenReturn(List.of(m1, m2));
        
        List<MaterialDTO> res = materialService.getMaterials("trade");
        assertEquals(1, res.size());
    }
    
    @Test
    void getmaterialsByNameAndGrade_shouldHandleNulls() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName(null);
        dto.setGrade("A");
        assertNull(materialService.getmaterialsByNameAndGrade(dto));
        
        dto.setName("N");
        dto.setGrade(null);
        assertNull(materialService.getmaterialsByNameAndGrade(dto));
    }
    
    @Test
    void getGradesByMaterialName_shouldHandleNulls() {
        assertTrue(materialService.getGradesByMaterialName(null, 1L).isEmpty());
        assertTrue(materialService.getGradesByMaterialName("N", null).isEmpty());
    }

    @Test
    void create_shouldHandleServiceIdExceptionAndFallback() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Name");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setServiceId(null);
        dto.setAllowDecimal(true);
        when(serviceMasterRepository.getIdByName("FieldCraft")).thenThrow(new RuntimeException("err"));
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());
        CodesDTO codes = new CodesDTO();
        when(codesService.getNames(any())).thenReturn(codes);
        
        Material saved = new Material();
        saved.setId(1L);
        when(materialRepository.save(any())).thenReturn(saved);
        
        materialService.create(dto);
        // Ensure exception in getServiceIdByName is caught and handled gracefully
        verify(materialRepository, times(1)).save(any());
    }

    // AB#: materialUid is a material's stable EXTERNAL key -- bom.material_uid and the stock
    // lookups in StockAllocationTransaction.allocateMaterialFromStock reference a material by it
    // rather than by internal pk. mapToEntity (shared by create AND both update paths) used to
    // mint a fresh UUID unconditionally, so ANY edit to an existing material silently re-keyed it
    // and orphaned every BOM line already pointing at the old UUID -- surfacing later as
    // "Material not found: <name>" at Add-BOM/allocate/dispatch time, far from the edit that
    // caused it. An update must preserve whatever materialUid the row already has.
    @Test
    void update_shouldPreserveExistingMaterialUid() throws com.fasterxml.jackson.core.JsonProcessingException {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Aluminum Drip Edge");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);

        UUID existingUid = UUID.fromString("7b76f22b-45a8-4f35-9205-22d209d4eb58");
        Material mat = new Material();
        mat.setId(10304L);
        mat.setMaterialUid(existingUid);

        when(materialRepository.findById(10304L)).thenReturn(Optional.of(mat));
        when(materialRepository.save(any())).thenReturn(mat);
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());

        materialService.update(10304L, dto);

        ArgumentCaptor<Material> captor = ArgumentCaptor.forClass(Material.class);
        verify(materialRepository).save(captor.capture());
        assertEquals(existingUid, captor.getValue().getMaterialUid(),
                "update must not re-key an existing material -- doing so orphans every bom.material_uid "
                        + "reference to it");
    }

    // The flip side of the guard above: a genuinely new material still has to be assigned a UUID,
    // otherwise nothing could reference it (material_uid is also UNIQUE NOT-NULL-in-practice).
    @Test
    void create_shouldGenerateMaterialUid_whenAbsent() {
        MaterialDTO dto = new MaterialDTO();
        dto.setName("Brand New Material");
        dto.setUnit("EA");
        dto.setBaseUnit("EA");
        dto.setAllowDecimal(true);
        when(unitOfMeasureService.findByUnitCode(any())).thenReturn(new UnitOfMeasure());
        when(codesService.getNames(any())).thenReturn(new CodesDTO());

        Material saved = new Material();
        saved.setId(1L);
        when(materialRepository.save(any())).thenReturn(saved);

        materialService.create(dto);

        ArgumentCaptor<Material> captor = ArgumentCaptor.forClass(Material.class);
        verify(materialRepository).save(captor.capture());
        assertNotNull(captor.getValue().getMaterialUid(),
                "a new material must still get a generated materialUid");
    }
}
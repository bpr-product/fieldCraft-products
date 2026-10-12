package com.fieldcraft.tenant.service;

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Purpose      : Unit tests for MaterialCostService
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *      28503     | 31-Aug-2026 |  Surya J     | Tests for invalidProject's conversion-factor
 *                |             |              | correction to per-Base-Unit scale
 *      32920     | 06-Oct-2026 |  Surya J     | AB#32920: the price-change rebuild keeps each row's unit
 */
import com.fasterxml.jackson.core.JsonProcessingException;
import https://fieldcraft.example.invalid;
import https://fieldcraft.example.invalid;
import com.fieldcraft.shared.error.BadRequestException;
import com.fieldcraft.shared.error.NotFoundException;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.shared.utils.ErrorParser;
import com.fieldcraft.tenant.dal.repos.*;
import com.fieldcraft.tenant.data.domain.*;
import com.fieldcraft.tenant.data.model.*;
import com.fieldcraft.tenant.saas.AIChatService;
import com.fieldcraft.tenant.saas.EmpCloudAccountService;
import com.fieldcraft.tenant.saas.AseService;
import com.fieldcraft.tenant.saas.NotificationMessageService;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockedStatic;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Sort;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.*;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@org.mockito.junit.jupiter.MockitoSettings(strictness = org.mockito.quality.Strictness.LENIENT)
class MaterialCostServiceTest {

    @Mock
    private NotificationMessageService notificationMessageService;
    @Mock
    private EmpCloudAccountService empAccountService;
    @Mock
    private AseService aseService;
    @Mock
    private AIChatService aiChatService;
    @Mock
    private TenantService tenantService;
    @Mock
    private AuditLogService auditLogService;
    @Mock
    private MaterialCostRepository materialCostRepository;
    @Mock
    private MaterialRepository materialRepository;
    @Mock
    private VendorMaterialMapRepository vendorMaterialMapRepository;
    @Mock
    private ProjectRepository projectRepository;
    @Mock
    private TaskRepository taskRepository;
    @Mock
    private UserAccountRepository userAccountRepository;
    @Mock
    private ServiceMasterRepository serviceMasterRepository;
    @Mock
    private BomRepository bomRepository;

    @Mock
    private NotificationDispatcher notificationDispatcher;

    @InjectMocks
    private MaterialCostService materialCostService;

    @BeforeEach
    void setup() {
        ReflectionTestUtils.setField(materialCostService, "notificationMessageService", notificationMessageService);
        ReflectionTestUtils.setField(materialCostService, "notificationDispatcher", notificationDispatcher);
        ReflectionTestUtils.setField(materialCostService, "empAccountService", empAccountService);
        ReflectionTestUtils.setField(materialCostService, "aseService", aseService);
        ReflectionTestUtils.setField(materialCostService, "aiChatService", aiChatService);
        ReflectionTestUtils.setField(materialCostService, "tenantService", tenantService);
        ReflectionTestUtils.setField(materialCostService, "auditLogService", auditLogService);
    }

    @Test
    void findAll_shouldReturnList() {
        MaterialCost mc = new MaterialCost();
        mc.setId(1L);
        when(materialCostRepository.findAll(any(Sort.class))).thenReturn(List.of(mc));

        List<MaterialCostDTO> result = materialCostService.findAll();
        assertEquals(1, result.size());
        assertEquals(1L, result.get(0).getId());
    }

    @Test
    void create_shouldReturnId() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setServiceId(10L);

        VendorMaterialMap map = new VendorMaterialMap();
        when(vendorMaterialMapRepository.findById(1L)).thenReturn(Optional.of(map));

        MaterialCost mc = new MaterialCost();
        mc.setId(100L);
        when(materialCostRepository.save(any())).thenReturn(mc);

        Long result = materialCostService.create(dto, 1L);
        assertEquals(100L, result);
    }

    @Test
    void create_shouldHandleDuplicateException() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setServiceId(10L);
        VendorMaterialMap map = new VendorMaterialMap();
        when(vendorMaterialMapRepository.findById(1L)).thenReturn(Optional.of(map));

        when(materialCostRepository.save(any())).thenThrow(new DataIntegrityViolationException("error"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(true);
            assertThrows(BadRequestException.class, () -> materialCostService.create(dto, 1L));
        }
    }

    @Test
    void create_shouldHandleGenericException() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setServiceId(null);
        when(serviceMasterRepository.getIdByName("FieldCraft")).thenReturn(5L);
        VendorMaterialMap map = new VendorMaterialMap();
        when(vendorMaterialMapRepository.findById(1L)).thenReturn(Optional.of(map));

        when(materialCostRepository.save(any())).thenThrow(new DataIntegrityViolationException("error"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(false);
            assertThrows(BadRequestException.class, () -> materialCostService.create(dto, 1L));
        }
    }

    @Test
    void create_shouldHandleServiceIdException() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setServiceId(null);
        when(serviceMasterRepository.getIdByName("FieldCraft")).thenThrow(new RuntimeException("DB error"));
        VendorMaterialMap map = new VendorMaterialMap();
        when(vendorMaterialMapRepository.findById(1L)).thenReturn(Optional.of(map));

        MaterialCost mc = new MaterialCost();
        mc.setId(100L);
        when(materialCostRepository.save(any())).thenReturn(mc);

        Long result = materialCostService.create(dto, 1L);
        assertEquals(100L, result);
    }

    @Test
    void update_shouldUpdateSuccessfully() {
        MaterialDTO dto = new MaterialDTO();
        when(vendorMaterialMapRepository.getId(1L, 2L)).thenReturn(10L);
        MaterialCost mc = new MaterialCost();
        when(materialCostRepository.findMostRecentByVendorMapId(10L)).thenReturn(mc);

        assertDoesNotThrow(() -> materialCostService.update(1L, dto, 2L));
        verify(materialCostRepository, times(1)).save(mc);
    }

    // updateInPlace is the no-vendor-map counterpart to update(materialId, materialDTO, vendorId)
    // above -- resolves the MaterialCost row directly by id (no VendorMaterialMap lookup needed)
    // and updates it in place, for a material that was never assigned a vendor.
    @Test
    void updateInPlace_updatesTheGivenMaterialCostRow() {
        MaterialDTO dto = new MaterialDTO();
        dto.setCost(new java.math.BigDecimal("27.00"));
        MaterialCost mc = new MaterialCost();
        when(materialCostRepository.findById(701L)).thenReturn(java.util.Optional.of(mc));

        materialCostService.updateInPlace(701L, dto);

        verify(materialCostRepository, times(1)).save(mc);
    }

    // AB#31160: mapToUpdateEntity used to set Selling Price directly from the incoming DTO with
    // no fallback -- a cost-only patch (Selling Price left blank/null on the DTO, as happens
    // editing just Purchase Unit Cost) silently wiped out the material's existing Selling Price.
    @Test
    void updateInPlace_preservesExistingSellingPrice_whenDTODoesNotCarryOne() {
        MaterialDTO dto = new MaterialDTO();
        dto.setCost(new java.math.BigDecimal("27.00"));
        // Selling Price left unset on the incoming DTO -- simulates a cost-only edit.

        MaterialCost mc = new MaterialCost();
        mc.setSellingPriceType("flat");
        mc.setSellingPriceValue(new java.math.BigDecimal("150.00"));
        when(materialCostRepository.findById(701L)).thenReturn(java.util.Optional.of(mc));

        materialCostService.updateInPlace(701L, dto);

        assertEquals("flat", mc.getSellingPriceType());
        assertEquals(0, new java.math.BigDecimal("150.00").compareTo(mc.getSellingPriceValue()));
        verify(materialCostRepository, times(1)).save(mc);
    }

    @Test
    void updateInPlace_overwritesSellingPrice_whenDTOCarriesANewOne() {
        MaterialDTO dto = new MaterialDTO();
        dto.setCost(new java.math.BigDecimal("27.00"));
        dto.setSellingPriceType("percentage");
        dto.setSellingPriceValue(new java.math.BigDecimal("20.00"));

        MaterialCost mc = new MaterialCost();
        mc.setSellingPriceType("flat");
        mc.setSellingPriceValue(new java.math.BigDecimal("150.00"));
        when(materialCostRepository.findById(701L)).thenReturn(java.util.Optional.of(mc));

        materialCostService.updateInPlace(701L, dto);

        assertEquals("percentage", mc.getSellingPriceType());
        assertEquals(0, new java.math.BigDecimal("20.00").compareTo(mc.getSellingPriceValue()));
    }

    @Test
    void updateInPlace_throwsNotFound_whenMaterialCostRowMissing() {
        MaterialDTO dto = new MaterialDTO();
        when(materialCostRepository.findById(701L)).thenReturn(java.util.Optional.empty());

        assertThrows(com.fieldcraft.shared.error.NotFoundException.class,
                () -> materialCostService.updateInPlace(701L, dto));
    }

    @Test
    void update_shouldHandleDuplicateException() {
        MaterialDTO dto = new MaterialDTO();
        when(vendorMaterialMapRepository.getId(1L, 2L)).thenReturn(10L);
        MaterialCost mc = new MaterialCost();
        when(materialCostRepository.findMostRecentByVendorMapId(10L)).thenReturn(mc);
        when(materialCostRepository.save(mc)).thenThrow(new DataIntegrityViolationException("error"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(true);
            assertThrows(BadRequestException.class, () -> materialCostService.update(1L, dto, 2L));
        }
    }

    @Test
    void update_shouldHandleGenericException() {
        MaterialDTO dto = new MaterialDTO();
        when(vendorMaterialMapRepository.getId(1L, 2L)).thenReturn(10L);
        MaterialCost mc = new MaterialCost();
        when(materialCostRepository.findMostRecentByVendorMapId(10L)).thenReturn(mc);
        when(materialCostRepository.save(mc)).thenThrow(new DataIntegrityViolationException("error"));

        try (MockedStatic<ErrorParser> mocked = mockStatic(ErrorParser.class)) {
            mocked.when(() -> ErrorParser.isDuplicateError(any())).thenReturn(false);
            assertThrows(BadRequestException.class, () -> materialCostService.update(1L, dto, 2L));
        }
    }

    @Test
    void insertMaterialCost_shouldReturnAlreadyPresent() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setVendorMaterialMapId(1L);
        dto.setVersionStartdate(OffsetDateTime.now());
        dto.setGrade("A");

        when(materialCostRepository.isMaterialCostAvailable(any(), any(), any())).thenReturn(true);
        String result = materialCostService.insertMaterialCost(dto);
        assertEquals("Material Already Present", result);
    }

    @Test
    void insertMaterialCost_shouldInsertAndUpdatePrevious() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setVendorMaterialMapId(1L);
        dto.setGrade("A");
        dto.setServiceId(10L);

        when(materialCostRepository.isMaterialCostAvailable(any(), any(), any())).thenReturn(false);
        
        VendorMaterialMap map = new VendorMaterialMap();
        when(vendorMaterialMapRepository.findById(1L)).thenReturn(Optional.of(map));

        MaterialCost prevMc = new MaterialCost();
        prevMc.setId(5L);
        prevMc.setCost(BigDecimal.ONE);
        prevMc.setVersionStartdate(OffsetDateTime.now());
        // Distinct from the DTO's serviceId (10L) so the assertion proves the values are
        // carried over from the previous version, not coincidentally equal.
        prevMc.setServiceId(42L);
        prevMc.setCreatedBy("prev-admin-oid");
        when(materialCostRepository.findMaterialCostByEffectiveDate(any(), any(), any())).thenReturn(prevMc);

        MaterialCost newMc = new MaterialCost();
        newMc.setId(100L);
        when(materialCostRepository.save(any())).thenReturn(newMc);

        Material material = new Material();
        material.setTags("tag");
        material.setName("Mat");
        when(materialRepository.findMaterialById(any())).thenReturn(material);

        String result = materialCostService.insertMaterialCost(dto);
        assertEquals("Updated Successfully", result);
        verify(materialCostRepository, times(1)).updateMaterialCost(any(), any());

        // Regression assertion for the bug fix: the copied version (first save) must carry
        // serviceId and createdBy from the previous version. Fails if those setters are removed.
        ArgumentCaptor<MaterialCost> captor = ArgumentCaptor.forClass(MaterialCost.class);
        verify(materialCostRepository, atLeast(2)).save(captor.capture());
        MaterialCost copied = captor.getAllValues().get(0);
        assertEquals(42L, copied.getServiceId());
        assertEquals("prev-admin-oid", copied.getCreatedBy());
    }

    @Test
    void insertMaterialCost_shouldThrowException() {
        MaterialCostDTO dto = new MaterialCostDTO();
        when(materialCostRepository.isMaterialCostAvailable(any(), any(), any())).thenThrow(new RuntimeException("Error"));

        assertThrows(ServerException.class, () -> materialCostService.insertMaterialCost(dto));
    }
    
    @Test
    void insertMaterialCost_shouldRethrowServerException() {
        MaterialCostDTO dto = new MaterialCostDTO();
        when(materialCostRepository.isMaterialCostAvailable(any(), any(), any())).thenThrow(new ServerException("Code", "Desc"));

        assertThrows(ServerException.class, () -> materialCostService.insertMaterialCost(dto));
    }

    @Test
    void getMaterialCostChatContent_shouldReturnString() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setCost(BigDecimal.TEN);
        String res = materialCostService.getMaterialCostChatContent("1", 1L, dto);
        assertTrue(res.contains("Material Cost : 10"));
    }

    @Test
    void invalidProject_shouldProcessAffectedProjects() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        dto.setVendorMaterialMapId(100L);

        MaterialCost mc = new MaterialCost();
        mc.setCost(BigDecimal.TEN);

        Material material = new Material();
        material.setTags("tag1;tag2");
        material.setName("MatName");
        when(materialRepository.findMaterialById(10L)).thenReturn(material);

        ProjectToolBomInfo pInfo = mock(ProjectToolBomInfo.class);
        when(pInfo.getId()).thenReturn(1L);
        when(pInfo.getName()).thenReturn("MatName");
        when(pInfo.getQuantity()).thenReturn(2.0);
        when(pInfo.getUnit()).thenReturn("EA");
        when(pInfo.getCode()).thenReturn("Code");
        when(pInfo.getVersion()).thenReturn(1);
        when(pInfo.getActive()).thenReturn(true);
        when(pInfo.getProfitMargin()).thenReturn(10.0);
        when(pInfo.getOverHead()).thenReturn(5.0);
        when(pInfo.getAddInfo()).thenReturn("Info");
        when(pInfo.getTax()).thenReturn("[]");
        when(pInfo.getProjectId()).thenReturn(2L);
        when(pInfo.getTaskOrder()).thenReturn(3L);

        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any())).thenReturn(List.of(pInfo));
        when(projectRepository.findById(2L)).thenReturn(Optional.of(new Project()));
        // EAV rewire: MaterialCostService now uses existsById + getReferenceById (task is used
        // only as a ManyToOne FK, never dereferenced for other fields) instead of the fat-entity
        // findById, so the existence check must be stubbed instead.
        when(taskRepository.existsById(3L)).thenReturn(true);
        when(taskRepository.getReferenceById(3L)).thenReturn(new Task());
        when(bomRepository.saveAll(any())).thenReturn(List.of(new Bom()));
        when(bomRepository.setInvalidByIds(any(), any())).thenReturn(1);
        when(projectRepository.updateProjectsStatusById(any(), any())).thenReturn(1);

        Project proj = new Project();
        proj.setId(2L);
        proj.setCustomerUid(UUID.randomUUID());
        proj.setTeamMembers("[\"member1\"]");
        when(projectRepository.findAffectedProjectsAndBom(any(), any(), any(), any())).thenReturn(List.of(proj));

        UserAccount user = new UserAccount();
        user.setEmail("test@test.com");
        when(userAccountRepository.findByObjectId(anyString())).thenReturn(user);

        materialCostService.invalidProject(dto, mc);

        // FCM-GAP-3: routed through NotificationDispatcher (Web PubSub + mobile push).
        verify(notificationDispatcher, times(1)).send(any(), any(), any(), any(), any());
    }

    // AB#28503: materialCost.getCost() is per Purchase Unit (e.g. per Bundle) -- invalidProject
    // must derive the true per-Base-Unit rate the same way BomService/TaskService/
    // EstimateAutomationService already do, instead of propagating the bundle price straight
    // into every affected project's BOM snapshot.
    @Test
    void invalidProject_recomputesUnitCost_whenMaterialHasRealConversionFactor() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        dto.setVendorMaterialMapId(100L);

        MaterialCost mc = new MaterialCost();
        mc.setCost(BigDecimal.valueOf(90.0)); // per Purchase Unit (bundle)

        UnitOfMeasure baseUnit = new UnitOfMeasure();
        baseUnit.setName("Each");
        Material material = new Material();
        material.setTags("tag1;tag2");
        material.setName("MatName");
        material.setConversionFactor(BigDecimal.valueOf(45.0));
        material.setBaseUnitId(baseUnit);
        when(materialRepository.findMaterialById(10L)).thenReturn(material);

        ProjectToolBomInfo pInfo = mock(ProjectToolBomInfo.class);
        when(pInfo.getId()).thenReturn(1L);
        when(pInfo.getName()).thenReturn("MatName");
        when(pInfo.getQuantity()).thenReturn(3.0);
        when(pInfo.getUnit()).thenReturn("BD - Bundle");
        when(pInfo.getCode()).thenReturn("Code");
        when(pInfo.getVersion()).thenReturn(1);
        when(pInfo.getActive()).thenReturn(true);
        when(pInfo.getProfitMargin()).thenReturn(10.0);
        when(pInfo.getOverHead()).thenReturn(5.0);
        when(pInfo.getAddInfo()).thenReturn("Info");
        when(pInfo.getTax()).thenReturn("[]");
        when(pInfo.getProjectId()).thenReturn(2L);
        when(pInfo.getTaskOrder()).thenReturn(3L);

        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any())).thenReturn(List.of(pInfo));
        when(projectRepository.findById(2L)).thenReturn(Optional.of(new Project()));
        when(taskRepository.existsById(3L)).thenReturn(true);
        when(taskRepository.getReferenceById(3L)).thenReturn(new Task());
        when(bomRepository.setInvalidByIds(any(), any())).thenReturn(1);
        when(projectRepository.updateProjectsStatusById(any(), any())).thenReturn(0);

        ArgumentCaptor<List<Bom>> bomCaptor = ArgumentCaptor.forClass(List.class);
        when(bomRepository.saveAll(bomCaptor.capture())).thenReturn(List.of(new Bom()));

        materialCostService.invalidProject(dto, mc);

        Bom saved = bomCaptor.getValue().get(0);
        // 90 / 45 = 2.00, not the raw 90.0 Purchase-Unit cost.
        assertEquals(0, BigDecimal.valueOf(2.0).compareTo(saved.getUnitCost()));
        assertEquals(0, BigDecimal.valueOf(6.0).compareTo(saved.getTotalCost()));
        assertEquals("BD - Bundle", saved.getUnit());
    }

    // AB#30036: the rate invalidProject persists must be the Selling Price per Base Unit -- the
    // same figure BomService.recomputeVendorUnitCost/TaskService.recomputeVendorUnitCostForBom
    // now persist -- not the raw vendor purchase cost.
    @Test
    void invalidProject_usesFlatSellingPrice_whenOnFile() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        dto.setVendorMaterialMapId(100L);

        MaterialCost mc = new MaterialCost();
        mc.setCost(BigDecimal.valueOf(90.0)); // per Purchase Unit (bundle)
        mc.setSellingPriceType("flat");
        mc.setSellingPriceValue(BigDecimal.valueOf(135.0));

        UnitOfMeasure baseUnit = new UnitOfMeasure();
        baseUnit.setName("Each");
        Material material = new Material();
        material.setTags("tag1;tag2");
        material.setName("MatName");
        material.setConversionFactor(BigDecimal.valueOf(45.0));
        material.setBaseUnitId(baseUnit);
        when(materialRepository.findMaterialById(10L)).thenReturn(material);

        ProjectToolBomInfo pInfo = mock(ProjectToolBomInfo.class);
        when(pInfo.getId()).thenReturn(1L);
        when(pInfo.getName()).thenReturn("MatName");
        when(pInfo.getQuantity()).thenReturn(3.0);
        when(pInfo.getUnit()).thenReturn("BD - Bundle");
        when(pInfo.getCode()).thenReturn("Code");
        when(pInfo.getVersion()).thenReturn(1);
        when(pInfo.getActive()).thenReturn(true);
        when(pInfo.getProfitMargin()).thenReturn(10.0);
        when(pInfo.getOverHead()).thenReturn(5.0);
        when(pInfo.getAddInfo()).thenReturn("Info");
        when(pInfo.getTax()).thenReturn("[]");
        when(pInfo.getProjectId()).thenReturn(2L);
        when(pInfo.getTaskOrder()).thenReturn(3L);

        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any())).thenReturn(List.of(pInfo));
        when(projectRepository.findById(2L)).thenReturn(Optional.of(new Project()));
        when(taskRepository.existsById(3L)).thenReturn(true);
        when(taskRepository.getReferenceById(3L)).thenReturn(new Task());
        when(bomRepository.setInvalidByIds(any(), any())).thenReturn(1);
        when(projectRepository.updateProjectsStatusById(any(), any())).thenReturn(0);

        ArgumentCaptor<List<Bom>> bomCaptor = ArgumentCaptor.forClass(List.class);
        when(bomRepository.saveAll(bomCaptor.capture())).thenReturn(List.of(new Bom()));

        materialCostService.invalidProject(dto, mc);

        Bom saved = bomCaptor.getValue().get(0);
        // A flat Selling Price of 135 is on file -- that must win over cost/conversionFactor
        // (90 / 45 = 2.00): 135 / 45 = 3.00.
        assertEquals(0, BigDecimal.valueOf(3.0).compareTo(saved.getUnitCost()));
        assertEquals(0, BigDecimal.valueOf(9.0).compareTo(saved.getTotalCost()));
        assertEquals("BD - Bundle", saved.getUnit());
    }

    @Test
    void invalidProject_usesPercentageSellingPrice_whenOnFile() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        dto.setVendorMaterialMapId(100L);

        MaterialCost mc = new MaterialCost();
        mc.setCost(BigDecimal.valueOf(90.0)); // per Purchase Unit (bundle)
        mc.setSellingPriceType("percentage");
        mc.setSellingPriceValue(BigDecimal.valueOf(20.0));

        UnitOfMeasure baseUnit = new UnitOfMeasure();
        baseUnit.setName("Each");
        Material material = new Material();
        material.setTags("tag1;tag2");
        material.setName("MatName");
        material.setConversionFactor(BigDecimal.valueOf(45.0));
        material.setBaseUnitId(baseUnit);
        when(materialRepository.findMaterialById(10L)).thenReturn(material);

        ProjectToolBomInfo pInfo = mock(ProjectToolBomInfo.class);
        when(pInfo.getId()).thenReturn(1L);
        when(pInfo.getName()).thenReturn("MatName");
        when(pInfo.getQuantity()).thenReturn(3.0);
        when(pInfo.getUnit()).thenReturn("BD - Bundle");
        when(pInfo.getCode()).thenReturn("Code");
        when(pInfo.getVersion()).thenReturn(1);
        when(pInfo.getActive()).thenReturn(true);
        when(pInfo.getProfitMargin()).thenReturn(10.0);
        when(pInfo.getOverHead()).thenReturn(5.0);
        when(pInfo.getAddInfo()).thenReturn("Info");
        when(pInfo.getTax()).thenReturn("[]");
        when(pInfo.getProjectId()).thenReturn(2L);
        when(pInfo.getTaskOrder()).thenReturn(3L);

        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any())).thenReturn(List.of(pInfo));
        when(projectRepository.findById(2L)).thenReturn(Optional.of(new Project()));
        when(taskRepository.existsById(3L)).thenReturn(true);
        when(taskRepository.getReferenceById(3L)).thenReturn(new Task());
        when(bomRepository.setInvalidByIds(any(), any())).thenReturn(1);
        when(projectRepository.updateProjectsStatusById(any(), any())).thenReturn(0);

        ArgumentCaptor<List<Bom>> bomCaptor = ArgumentCaptor.forClass(List.class);
        when(bomRepository.saveAll(bomCaptor.capture())).thenReturn(List.of(new Bom()));

        materialCostService.invalidProject(dto, mc);

        Bom saved = bomCaptor.getValue().get(0);
        // cost 90 + 20% markup = 108 per Bundle -> 108 / 45 = 2.40/Each.
        assertEquals(0, BigDecimal.valueOf(2.4).compareTo(saved.getUnitCost()));
        assertEquals("BD - Bundle", saved.getUnit());
    }

    @Test
    void invalidProject_nullTax_defaultsToEmptyArrayInsteadOfThrowingNpe() throws Exception {
        // A project/BOM row with no tax breakdown returns getTax() == null -- new JSONArray(null)
        // throws NPE (org.json's JSONTokener wraps the string in a StringReader unconditionally).
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        dto.setVendorMaterialMapId(100L);

        MaterialCost mc = new MaterialCost();
        mc.setCost(BigDecimal.TEN);

        Material material = new Material();
        material.setTags("tag1;tag2");
        material.setName("MatName");
        when(materialRepository.findMaterialById(10L)).thenReturn(material);

        ProjectToolBomInfo pInfo = mock(ProjectToolBomInfo.class);
        when(pInfo.getId()).thenReturn(1L);
        when(pInfo.getName()).thenReturn("MatName");
        when(pInfo.getQuantity()).thenReturn(2.0);
        when(pInfo.getUnit()).thenReturn("EA");
        when(pInfo.getCode()).thenReturn("Code");
        when(pInfo.getVersion()).thenReturn(1);
        when(pInfo.getActive()).thenReturn(true);
        when(pInfo.getProfitMargin()).thenReturn(10.0);
        when(pInfo.getOverHead()).thenReturn(5.0);
        when(pInfo.getAddInfo()).thenReturn("Info");
        when(pInfo.getTax()).thenReturn(null);
        when(pInfo.getProjectId()).thenReturn(2L);
        when(pInfo.getTaskOrder()).thenReturn(3L);

        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any())).thenReturn(List.of(pInfo));
        when(projectRepository.findById(2L)).thenReturn(Optional.of(new Project()));
        when(taskRepository.existsById(3L)).thenReturn(true);
        when(taskRepository.getReferenceById(3L)).thenReturn(new Task());

        ArgumentCaptor<List<Bom>> bomCaptor = ArgumentCaptor.forClass(List.class);
        when(bomRepository.saveAll(bomCaptor.capture())).thenReturn(List.of(new Bom()));
        when(bomRepository.setInvalidByIds(any(), any())).thenReturn(1);
        when(projectRepository.updateProjectsStatusById(any(), any())).thenReturn(1);

        Project proj = new Project();
        proj.setId(2L);
        proj.setCustomerUid(UUID.randomUUID());
        proj.setTeamMembers("[\"member1\"]");
        when(projectRepository.findAffectedProjectsAndBom(any(), any(), any(), any())).thenReturn(List.of(proj));

        UserAccount user = new UserAccount();
        user.setEmail("test@test.com");
        when(userAccountRepository.findByObjectId(anyString())).thenReturn(user);

        materialCostService.invalidProject(dto, mc);

        assertEquals("[]", bomCaptor.getValue().get(0).getTax());
    }

    @Test
    void invalidProject_shouldThrowNotFoundExceptionForProject() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        MaterialCost mc = new MaterialCost();
        mc.setCost(BigDecimal.TEN);

        Material material = new Material();
        material.setTags("tag1");
        when(materialRepository.findMaterialById(10L)).thenReturn(material);

        ProjectToolBomInfo pInfo = mock(ProjectToolBomInfo.class);
        when(pInfo.getId()).thenReturn(1L);
        when(pInfo.getName()).thenReturn("MatName");
        when(pInfo.getQuantity()).thenReturn(2.0);
        when(pInfo.getUnit()).thenReturn("EA");
        when(pInfo.getCode()).thenReturn("Code");
        when(pInfo.getVersion()).thenReturn(1);
        when(pInfo.getActive()).thenReturn(true);
        when(pInfo.getProfitMargin()).thenReturn(10.0);
        when(pInfo.getOverHead()).thenReturn(5.0);
        when(pInfo.getAddInfo()).thenReturn("Info");
        when(pInfo.getTax()).thenReturn("[]");
        when(pInfo.getProjectId()).thenReturn(2L);
        when(pInfo.getTaskOrder()).thenReturn(3L);

        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any())).thenReturn(List.of(pInfo));
        when(projectRepository.findById(2L)).thenReturn(Optional.empty()); // Throws NotFoundException

        assertThrows(ServerException.class, () -> materialCostService.invalidProject(dto, mc));
    }
    
    @Test
    void invalidProject_shouldRethrowServerException() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        when(materialRepository.findMaterialById(10L)).thenThrow(new ServerException("Code", "Desc"));

        assertThrows(ServerException.class, () -> materialCostService.invalidProject(dto, new MaterialCost()));
    }

    @Test
    void getMaterialsBySelectedDateAndMapId_shouldReturnMaterialCost() {
        when(materialCostRepository.findCountBySelectedDateAndMapId(any(), any())).thenReturn(1);
        MaterialInfo mockInfo = mock(MaterialInfo.class);
        when(materialCostRepository.findMaterialCostBySelectedDateAndMapId(any(), any())).thenReturn(mockInfo);

        MaterialInfo result = materialCostService.getMaterialsBySelectedDateAndMapId(OffsetDateTime.now(), 1L);
        assertNotNull(result);
    }

    @Test
    void getMaterialsBySelectedDateAndMapId_shouldReturnNull() {
        when(materialCostRepository.findCountBySelectedDateAndMapId(any(), any())).thenReturn(0);
        assertNull(materialCostService.getMaterialsBySelectedDateAndMapId(OffsetDateTime.now(), 1L));
    }

    @Test
    void getMinimumMaterialCost_shouldCallAseService() throws Exception {
        when(aseService.findMinimumMaterialCost(anyString())).thenReturn("result");
        Object res = materialCostService.getMinimumMaterialCost(new String[]{"mat1"}, "123", "2023-01-01");
        assertEquals("result", res);
    }
    
    @Test
    void prepareRecipientIdsForDirtyBid_shouldReturnArray() {
        Project p = new Project();
        p.setTeamMembers("[\"member1\", \"member2\"]");
        String[] result = materialCostService.prepareRecipientIdsForDirtyBid(p, "updater", "customer");
        assertEquals(4, result.length);
    }

    @Test
    void prepareRecipientIdsForDirtyBid_shouldHandleNullMembers() {
        Project p = new Project();
        p.setTeamMembers(null);
        String[] result = materialCostService.prepareRecipientIdsForDirtyBid(p, "updater", "customer");
        assertEquals(2, result.length);
    }

    @Test
    void create_shouldResolveFieldCraftServiceIdWhenDtoServiceIdNull() {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setServiceId(null);
        when(serviceMasterRepository.getIdByName("FieldCraft")).thenReturn(7L);
        when(vendorMaterialMapRepository.findById(1L)).thenReturn(Optional.of(new VendorMaterialMap()));

        MaterialCost saved = new MaterialCost();
        saved.setId(100L);
        when(materialCostRepository.save(any())).thenReturn(saved);

        Long result = materialCostService.create(dto, 1L);

        assertEquals(100L, result);
        // mapToEntity resolves the in-house "FieldCraft" service and writes it back onto the DTO
        assertEquals(7L, dto.getServiceId());
        verify(serviceMasterRepository, times(1)).getIdByName("FieldCraft");
    }

    @Test
    void findAll_shouldMapServiceIdAndCreatedBy() {
        MaterialCost mc = new MaterialCost();
        mc.setId(1L);
        mc.setServiceId(9L);
        mc.setCreatedBy("8aef1564-cd04-4b85-9633-49ad6f4f3231");
        when(materialCostRepository.findAll(any(Sort.class))).thenReturn(List.of(mc));

        List<MaterialCostDTO> result = materialCostService.findAll();

        assertEquals(1, result.size());
        assertEquals(9L, result.get(0).getServiceId());
        assertEquals("8aef1564-cd04-4b85-9633-49ad6f4f3231", result.get(0).getCreatedBy());
    }

    @Test
    void insertMaterialCost_shouldInsertWhenNoPreviousCost() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        dto.setVendorMaterialMapId(1L);
        dto.setGrade("A");
        dto.setVersionStartdate(OffsetDateTime.now());
        dto.setServiceId(10L);

        when(materialCostRepository.isMaterialCostAvailable(any(), any(), any())).thenReturn(false);
        when(vendorMaterialMapRepository.findById(1L)).thenReturn(Optional.of(new VendorMaterialMap()));
        // No previous cost row → the previous-version copy block is skipped
        when(materialCostRepository.findMaterialCostByEffectiveDate(any(), any(), any())).thenReturn(null);

        MaterialCost saved = new MaterialCost();
        saved.setId(100L);
        when(materialCostRepository.save(any())).thenReturn(saved);

        Material material = new Material();
        material.setTags("tag");
        material.setName("Mat");
        when(materialRepository.findMaterialById(10L)).thenReturn(material);
        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any()))
                .thenReturn(Collections.emptyList());

        String result = materialCostService.insertMaterialCost(dto);

        assertEquals("Updated Successfully", result);
        verify(materialCostRepository, never()).updateMaterialCost(any(), any());
    }

    @Test
    void invalidProject_shouldDoNothingWhenNoAffectedProjects() throws Exception {
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(10L);
        dto.setVendorMaterialMapId(100L);

        Material material = new Material();
        material.setTags("tag1");
        material.setName("MatName");
        when(materialRepository.findMaterialById(10L)).thenReturn(material);
        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any()))
                .thenReturn(Collections.emptyList());

        materialCostService.invalidProject(dto, new MaterialCost());

        verify(bomRepository, never()).saveAll(any());
        verify(notificationDispatcher, never()).send(any(), any(), any(), any(), any());
    }
}

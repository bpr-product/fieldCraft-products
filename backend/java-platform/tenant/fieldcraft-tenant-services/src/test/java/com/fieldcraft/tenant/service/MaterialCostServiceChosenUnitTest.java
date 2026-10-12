/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 06-Oct-2026
 * @author      : Surya J
 * @since       : 1.5.4.2
 * Requirement# : AB#32920
 * Purpose      : Unit tests for MaterialCostService.invalidProject -- a price change rebuilds
 *                each BOM row in its own unit, keeping the row's unit and quantity
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *      32920     | 06-Oct-2026 |  Surya J     | AB#32920: Initial -- price-change rebuild per row unit (AC8)
 */
package com.fieldcraft.tenant.service;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import https://fieldcraft.example.invalid;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.BomRepository;
import com.fieldcraft.tenant.dal.repos.MaterialCostRepository;
import com.fieldcraft.tenant.dal.repos.MaterialRepository;
import com.fieldcraft.tenant.dal.repos.ProjectRepository;
import com.fieldcraft.tenant.dal.repos.TaskRepository;
import com.fieldcraft.tenant.dal.repos.UserAccountRepository;
import com.fieldcraft.tenant.dal.repos.VendorMaterialMapRepository;
import com.fieldcraft.tenant.data.domain.Bom;
import com.fieldcraft.tenant.data.domain.Material;
import com.fieldcraft.tenant.data.domain.MaterialCost;
import com.fieldcraft.tenant.data.domain.Project;
import com.fieldcraft.tenant.data.domain.Task;
import com.fieldcraft.tenant.data.domain.UnitOfMeasure;
import com.fieldcraft.tenant.data.model.MaterialCostDTO;
import com.fieldcraft.tenant.data.model.ProjectToolBomInfo;
import com.fieldcraft.tenant.saas.AIChatService;
import com.fieldcraft.tenant.saas.AseService;
import com.fieldcraft.tenant.saas.EmpCloudAccountService;
import com.fieldcraft.tenant.saas.NotificationMessageService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

// LENIENT: the shared row stubs are wider than any single test reads.
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
@DisplayName("MaterialCostService.invalidProject -- rebuild keeps each row's own unit (AB#32920 AC8)")
class MaterialCostServiceChosenUnitTest {

    private static final Long MATERIAL_ID = 10L;
    private static final UUID MATERIAL_UID = UUID.fromString("00000000-0000-0000-0000-000000032920");

    @Mock private NotificationMessageService notificationMessageService;
    @Mock private EmpCloudAccountService empAccountService;
    @Mock private AseService aseService;
    @Mock private AIChatService aiChatService;
    @Mock private TenantService tenantService;
    @Mock private AuditLogService auditLogService;
    @Mock private MaterialCostRepository materialCostRepository;
    @Mock private MaterialRepository materialRepository;
    @Mock private VendorMaterialMapRepository vendorMaterialMapRepository;
    @Mock private ProjectRepository projectRepository;
    @Mock private TaskRepository taskRepository;
    @Mock private UserAccountRepository userAccountRepository;
    @Mock private ServiceMasterRepository serviceMasterRepository;
    @Mock private BomRepository bomRepository;
    @Mock private NotificationDispatcher notificationDispatcher;

    @InjectMocks
    private MaterialCostService materialCostService;

    private final ArgumentCaptor<List<Bom>> bomCaptor = ArgumentCaptor.forClass(List.class);

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(materialCostService, "notificationDispatcher", notificationDispatcher);
        ReflectionTestUtils.setField(materialCostService, "auditLogService", auditLogService);
        UnitOfMeasure pieces = new UnitOfMeasure();
        pieces.setName("Pieces");
        Material tile = new Material();
        tile.setId(MATERIAL_ID);
        tile.setMaterialUid(MATERIAL_UID);
        tile.setName("DEMO-Tile");
        tile.setTags("DEMO-TAG");
        tile.setBaseUnitId(pieces);
        tile.setUnit("BX - Box");
        tile.setConversionFactor(new BigDecimal("10"));
        when(materialRepository.findMaterialById(MATERIAL_ID)).thenReturn(tile);
        when(projectRepository.findById(2L)).thenReturn(Optional.of(new Project()));
        when(taskRepository.existsById(3L)).thenReturn(true);
        when(taskRepository.getReferenceById(3L)).thenReturn(new Task());
        when(bomRepository.saveAll(bomCaptor.capture())).thenAnswer(inv -> inv.getArgument(0));
        when(bomRepository.setInvalidByIds(any(), any())).thenReturn(1);
        when(projectRepository.updateProjectsStatusById(any(), any())).thenReturn(0);
    }

    private static ProjectToolBomInfo row(final Long bomId, final String unit, final double quantity) {
        ProjectToolBomInfo row = mock(ProjectToolBomInfo.class);
        when(row.getId()).thenReturn(bomId);
        when(row.getName()).thenReturn("DEMO-Tile");
        when(row.getUnit()).thenReturn(unit);
        when(row.getQuantity()).thenReturn(quantity);
        when(row.getMaterialUid()).thenReturn(MATERIAL_UID);
        when(row.getCode()).thenReturn("DEMO-CODE");
        when(row.getVersion()).thenReturn(1);
        when(row.getActive()).thenReturn(true);
        when(row.getTax()).thenReturn("[]");
        when(row.getProjectId()).thenReturn(2L);
        when(row.getTaskOrder()).thenReturn(3L);
        return row;
    }

    /** New cost: $160 per Box with no Selling Price on file, so one Piece is $16. */
    private List<Bom> rebuild(final ProjectToolBomInfo... rows) throws Exception {
        when(projectRepository.findProjectsAndBom(any(), any(), any(), any(), any())).thenReturn(List.of(rows));
        MaterialCostDTO dto = new MaterialCostDTO();
        dto.setId(MATERIAL_ID);
        dto.setVendorMaterialMapId(100L);
        MaterialCost cost = new MaterialCost();
        cost.setCost(new BigDecimal("160"));

        materialCostService.invalidProject(dto, cost);

        return bomCaptor.getValue();
    }

    @Test
    @DisplayName("A 4 Box row reprices at 160 and a 40 Pieces row at 16; both total 640")
    void rebuildPricesEachRowInItsOwnUnit() throws Exception {
        List<Bom> rebuilt = rebuild(row(501L, "Box", 4.0), row(502L, "Pieces", 40.0));

        assertThat(rebuilt.get(0).getUnitCost()).isEqualByComparingTo("160");
        assertThat(rebuilt.get(0).getTotalCost()).isEqualByComparingTo("640");
        assertThat(rebuilt.get(1).getUnitCost()).isEqualByComparingTo("16");
        assertThat(rebuilt.get(1).getTotalCost()).isEqualByComparingTo("640");
    }

    @Test
    @DisplayName("The rebuilt rows keep their own unit and quantity")
    void rebuildKeepsRowUnitAndQuantity() throws Exception {
        List<Bom> rebuilt = rebuild(row(501L, "Box", 4.0), row(502L, "Pieces", 40.0));

        assertThat(rebuilt.get(0).getUnit()).isEqualTo("Box");
        assertThat(rebuilt.get(0).getQuantity()).isEqualTo(4.0);
        assertThat(rebuilt.get(1).getUnit()).isEqualTo("Pieces");
        assertThat(rebuilt.get(1).getQuantity()).isEqualTo(40.0);
    }

    @Test
    @DisplayName("Q6: a row whose unit matches neither name uses factor 1, keeps its unit and logs one warning")
    void rebuildUnmatchedUnitUsesFactorOneAndWarns() throws Exception {
        ListAppender<ILoggingEvent> capture = new ListAppender<>();
        capture.start();
        ch.qos.logback.classic.Logger serviceLogger =
                (ch.qos.logback.classic.Logger) LoggerFactory.getLogger(MaterialCostService.class);
        serviceLogger.setLevel(Level.ALL);
        serviceLogger.addAppender(capture);
        try {
            List<Bom> rebuilt = rebuild(row(503L, "Crate", 3.0));

            assertThat(rebuilt.get(0).getUnit()).isEqualTo("Crate");
            assertThat(rebuilt.get(0).getQuantity()).isEqualTo(3.0);
            assertThat(rebuilt.get(0).getUnitCost()).isEqualByComparingTo("16");
            assertThat(rebuilt.get(0).getTotalCost()).isEqualByComparingTo("48");
            List<String> warnings = capture.list.stream()
                    .filter(event -> event.getLevel() == Level.WARN)
                    .map(ILoggingEvent::getFormattedMessage)
                    .filter(message -> message.contains("503") && message.contains(MATERIAL_UID.toString()))
                    .toList();
            assertThat(warnings).hasSize(1);
        } finally {
            serviceLogger.detachAppender(capture);
            capture.stop();
        }
    }
}

package com.fieldcraft.tenant.dal.repos;

import com.fieldcraft.tenant.dal.repos.config.AbstractTestContainerConfig;
import com.fieldcraft.tenant.dal.repos.config.TenantDbTestConfig;
import com.fieldcraft.tenant.data.domain.Project;
import com.fieldcraft.tenant.data.domain.ProjectExternalMap;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager;
import org.springframework.test.context.junit.jupiter.SpringExtension;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(SpringExtension.class)
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@org.springframework.data.jpa.repository.config.EnableJpaRepositories(
        basePackageClasses = ProjectExternalMapRepository.class,
        includeFilters = @org.springframework.context.annotation.ComponentScan.Filter(
                type = org.springframework.context.annotation.FilterType.ASSIGNABLE_TYPE,
                classes = ProjectExternalMapRepository.class
        )
)
@org.springframework.test.context.ContextConfiguration(classes = TenantDbTestConfig.class)
class ProjectExternalMapRepositoryTest extends AbstractTestContainerConfig {

    @Autowired
    private ProjectExternalMapRepository projectExternalMapRepository;

    @Autowired
    private ProjectRepository projectRepository;

    @Autowired
    private TestEntityManager entityManager;

    private static final OffsetDateTime NOW    = OffsetDateTime.now(ZoneOffset.UTC);
    private static final OffsetDateTime FUTURE = OffsetDateTime.of(3000, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);

    private Project savedProject;

    @BeforeEach
    void setUp() {
        savedProject = createAndSaveProject();
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    private Project createAndSaveProject() {
        Project project = new Project();
        project.setProjectUid(UUID.randomUUID());
        project.setCustomerUid(UUID.randomUUID());
        project.setCustomerAsset("DEMO-ASSET");
        project.setCustomer("DEMO-CUSTOMER");
        project.setOrderNo("DEMO-ORDER-001");
        project.setTrade("DEMO-TRADE");
        project.setStatus("active");
        project.setInvalid(false);
        project.setTaxExemption(false);
        project.setVersion(0);
        project.setCreatedAt(NOW);
        project.setUpdatedAt(NOW);
        return projectRepository.save(project);
    }

    private ProjectExternalMap buildMap(Project project, String syncStatus) {
        ProjectExternalMap map = new ProjectExternalMap();
        map.setProject(project);
        map.setProjectUid(project.getProjectUid());
        map.setTenantId("tn88351775");
        map.setServiceId(6L);
        map.setSyncStatus(syncStatus);
        map.setVersion(1);
        map.setVersionEffectiveDate(NOW);
        map.setVersionExpiryDate(FUTURE);
        map.setEndorsementEffectiveDate(NOW);
        map.setEndorsementExpiryDate(FUTURE);
        map.setInvalid(false);
        return map;
    }

    private List<ProjectExternalMap> findByProjectId(Long projectId) {
        return projectExternalMapRepository.findByProject_Id(projectId);
    }

    // ── updateSyncStatusOnDelete ──────────────────────────────────────────────

    @Test
    @DisplayName("updateSyncStatusOnDelete transitions SYNCED to DELETED")
    void updateSyncStatusOnDelete_changesSyncedToDeleted() {
        projectExternalMapRepository.save(buildMap(savedProject, "SYNCED"));
        entityManager.flush();
        entityManager.clear();

        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());
        entityManager.flush();
        entityManager.clear();

        List<ProjectExternalMap> rows = findByProjectId(savedProject.getId());
        assertEquals(1, rows.size());
        assertEquals("DELETED", rows.get(0).getSyncStatus());
    }

    @Test
    @DisplayName("updateSyncStatusOnDelete transitions PENDING_PUSH to INVALID")
    void updateSyncStatusOnDelete_changesPendingPushToInvalid() {
        projectExternalMapRepository.save(buildMap(savedProject, "PENDING_PUSH"));
        entityManager.flush();
        entityManager.clear();

        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());
        entityManager.flush();
        entityManager.clear();

        List<ProjectExternalMap> rows = findByProjectId(savedProject.getId());
        assertEquals(1, rows.size());
        assertEquals("INVALID", rows.get(0).getSyncStatus());
    }

    @Test
    @DisplayName("updateSyncStatusOnDelete updates both SYNCED and PENDING_PUSH rows together")
    void updateSyncStatusOnDelete_updatesBothSyncedAndPendingPushRows() {
        projectExternalMapRepository.save(buildMap(savedProject, "SYNCED"));
        projectExternalMapRepository.save(buildMap(savedProject, "PENDING_PUSH"));
        entityManager.flush();
        entityManager.clear();

        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());
        entityManager.flush();
        entityManager.clear();

        List<ProjectExternalMap> rows = findByProjectId(savedProject.getId());
        assertEquals(2, rows.size());
        assertTrue(rows.stream().anyMatch(m -> "DELETED".equals(m.getSyncStatus())));
        assertTrue(rows.stream().anyMatch(m -> "INVALID".equals(m.getSyncStatus())));
    }

    @Test
    @DisplayName("updateSyncStatusOnDelete does not touch INVALID rows")
    void updateSyncStatusOnDelete_leavesInvalidRowsUntouched() {
        projectExternalMapRepository.save(buildMap(savedProject, "INVALID"));
        entityManager.flush();
        entityManager.clear();

        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());
        entityManager.flush();
        entityManager.clear();

        List<ProjectExternalMap> rows = findByProjectId(savedProject.getId());
        assertEquals(1, rows.size());
        assertEquals("INVALID", rows.get(0).getSyncStatus());
    }

    @Test
    @DisplayName("updateSyncStatusOnDelete does not touch SYNC_FAILED rows")
    void updateSyncStatusOnDelete_leavesSyncFailedRowsUntouched() {
        projectExternalMapRepository.save(buildMap(savedProject, "SYNC_FAILED"));
        entityManager.flush();
        entityManager.clear();

        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());
        entityManager.flush();
        entityManager.clear();

        List<ProjectExternalMap> rows = findByProjectId(savedProject.getId());
        assertEquals(1, rows.size());
        assertEquals("SYNC_FAILED", rows.get(0).getSyncStatus());
    }

    @Test
    @DisplayName("updateSyncStatusOnDelete does not affect rows of a different project")
    void updateSyncStatusOnDelete_doesNotAffectOtherProjects() {
        Project otherProject = createAndSaveProject();
        projectExternalMapRepository.save(buildMap(otherProject, "SYNCED"));
        entityManager.flush();
        entityManager.clear();

        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());
        entityManager.flush();
        entityManager.clear();

        List<ProjectExternalMap> otherRows = findByProjectId(otherProject.getId());
        assertEquals(1, otherRows.size());
        assertEquals("SYNCED", otherRows.get(0).getSyncStatus());
    }

    @Test
    @DisplayName("updateSyncStatusOnDelete is a no-op when project has no external map rows")
    void updateSyncStatusOnDelete_isNoOpWhenNoRowsExist() {
        entityManager.flush();
        entityManager.clear();

        // must not throw
        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());

        List<ProjectExternalMap> rows = findByProjectId(savedProject.getId());
        assertTrue(rows.isEmpty());
    }

    @Test
    @DisplayName("updateSyncStatusOnDelete handles DELETED status rows without modification")
    void updateSyncStatusOnDelete_leavesAlreadyDeletedRowsUntouched() {
        projectExternalMapRepository.save(buildMap(savedProject, "DELETED"));
        entityManager.flush();
        entityManager.clear();

        projectExternalMapRepository.updateSyncStatusOnDelete(savedProject.getId());
        entityManager.flush();
        entityManager.clear();

        List<ProjectExternalMap> rows = findByProjectId(savedProject.getId());
        assertEquals(1, rows.size());
        assertEquals("DELETED", rows.get(0).getSyncStatus());
    }
}

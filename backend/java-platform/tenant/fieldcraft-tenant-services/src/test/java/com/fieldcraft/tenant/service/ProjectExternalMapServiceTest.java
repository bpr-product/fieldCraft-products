package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.ProjectExternalMapRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.data.domain.Tenant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ProjectExternalMapServiceTest {

    private static final Long   PROJECT_ID         = 42L;
    private static final String TENANT_ID          = "tn88351775";
    private static final long   SERVICE_ID         = 6L;
    private static final String DATA_EXCHANGE      = "Data Exchange";

    @Mock private ProjectExternalMapRepository projectExternalMapRepository;
    @Mock private ServiceMasterRepository      serviceMasterRepository;
    @Mock private AuditLogService              auditLogService;
    @Mock private TenantRepository             tenantRepository;

    private ProjectExternalMapService service;

    @BeforeEach
    void setUp() {
        service = new ProjectExternalMapService(
                projectExternalMapRepository, serviceMasterRepository,
                auditLogService, tenantRepository);
    }

    // ── stub helpers ──────────────────────────────────────────────────────────

    private void stubTenant() {
        Tenant tenant = new Tenant();
        tenant.setTenantid(TENANT_ID);
        when(tenantRepository.findTenant()).thenReturn(tenant);
    }

    private void stubServiceIds(Long... ids) {
        when(serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE))
                .thenReturn(List.of(ids));
    }

    // ── happy path ────────────────────────────────────────────────────────────

    @Test
    void markDeletedForProjectCallsRepositoryAndAuditsWhenQuickBooksConnected() {
        stubTenant();
        stubServiceIds(SERVICE_ID);

        service.markDeletedForProject(PROJECT_ID);

        verify(projectExternalMapRepository).updateSyncStatusOnDelete(PROJECT_ID);
        verify(auditLogService).saveAuditLog(argThat(dto ->
                String.valueOf(PROJECT_ID).equals(dto.getResourceId())
                        && PROJECT_ID.equals(dto.getProjectId())));
    }

    @Test
    void markDeletedForProjectWorksWithMultipleServiceIds() {
        stubTenant();
        stubServiceIds(SERVICE_ID, 7L);

        service.markDeletedForProject(PROJECT_ID);

        verify(projectExternalMapRepository).updateSyncStatusOnDelete(PROJECT_ID);
        verify(auditLogService).saveAuditLog(any());
    }

    // ── skip / guard paths ────────────────────────────────────────────────────

    @Test
    void markDeletedForProjectIsNoOpWhenProjectIdIsNull() {
        service.markDeletedForProject(null);

        verifyNoInteractions(tenantRepository);
        verifyNoInteractions(serviceMasterRepository);
        verifyNoInteractions(projectExternalMapRepository);
        verifyNoInteractions(auditLogService);
    }

    @Test
    void markDeletedForProjectSkipsWhenTenantIsNull() {
        when(tenantRepository.findTenant()).thenReturn(null);

        service.markDeletedForProject(PROJECT_ID);

        verifyNoInteractions(serviceMasterRepository);
        verifyNoInteractions(projectExternalMapRepository);
        verifyNoInteractions(auditLogService);
    }

    @Test
    void markDeletedForProjectSkipsWhenNoDataExchangeServiceConfigured() {
        stubTenant();
        stubServiceIds();

        service.markDeletedForProject(PROJECT_ID);

        verify(projectExternalMapRepository, never()).updateSyncStatusOnDelete(any());
        verifyNoInteractions(auditLogService);
    }

    @Test
    void markDeletedForProjectSkipsWhenServiceIdsListIsNull() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE)).thenReturn(null);

        service.markDeletedForProject(PROJECT_ID);

        verify(projectExternalMapRepository, never()).updateSyncStatusOnDelete(any());
        verifyNoInteractions(auditLogService);
    }

    // ── best-effort / swallow paths ───────────────────────────────────────────

    @Test
    void markDeletedForProjectSwallowsWhenServiceMasterThrows() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE))
                .thenThrow(new RuntimeException("service_master unavailable"));

        service.markDeletedForProject(PROJECT_ID);

        verify(projectExternalMapRepository, never()).updateSyncStatusOnDelete(any());
        verifyNoInteractions(auditLogService);
    }

    @Test
    void markDeletedForProjectSwallowsWhenRepositoryUpdateThrows() {
        stubTenant();
        stubServiceIds(SERVICE_ID);
        doThrow(new RuntimeException("db write failed"))
                .when(projectExternalMapRepository).updateSyncStatusOnDelete(PROJECT_ID);

        service.markDeletedForProject(PROJECT_ID);

        verifyNoInteractions(auditLogService);
    }

    @Test
    void markDeletedForProjectSwallowsWhenTenantRepositoryThrows() {
        when(tenantRepository.findTenant())
                .thenThrow(new RuntimeException("tenant lookup failed"));

        service.markDeletedForProject(PROJECT_ID);

        verifyNoInteractions(projectExternalMapRepository);
        verifyNoInteractions(auditLogService);
    }

    @Test
    void markDeletedForProjectSwallowsWhenAuditLogThrows() {
        stubTenant();
        stubServiceIds(SERVICE_ID);
        doThrow(new RuntimeException("audit db unavailable"))
                .when(auditLogService).saveAuditLog(any());

        service.markDeletedForProject(PROJECT_ID);

        verify(projectExternalMapRepository).updateSyncStatusOnDelete(PROJECT_ID);
    }
}

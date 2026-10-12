package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.shared.constant.AuditLogConstant;
import com.fieldcraft.tenant.dal.repos.ProjectExternalMapRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.data.constant.DomainConstant;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.model.AuditLogDTO;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.json.JSONObject;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Single source of truth for writing {@code project_external_map} rows on project deletion.
 * Mirrors the pattern used by {@code UserAccountExternalMapService} and
 * {@code TimelogExternalMapService}: the QuickBooks / Data Exchange service presence is checked
 * first — if no active service exists for this tenant the call is silently skipped. Any failure
 * is logged and swallowed so it can never block or fail the underlying project delete.
 */
@Service
public class ProjectExternalMapService {

    private static final Logger logger = LogManager.getLogger(ProjectExternalMapService.class);
    private static final String DATA_EXCHANGE_SERVICE_TYPE = "Data Exchange";

    private final ProjectExternalMapRepository projectExternalMapRepository;
    private final ServiceMasterRepository serviceMasterRepository;
    private final AuditLogService auditLogService;
    private final TenantRepository tenantRepository;

    public ProjectExternalMapService(final ProjectExternalMapRepository projectExternalMapRepository,
                                     final ServiceMasterRepository serviceMasterRepository,
                                     final AuditLogService auditLogService,
                                     final TenantRepository tenantRepository) {
        this.projectExternalMapRepository = projectExternalMapRepository;
        this.serviceMasterRepository = serviceMasterRepository;
        this.auditLogService = auditLogService;
        this.tenantRepository = tenantRepository;
    }

    /**
     * Marks any active {@code project_external_map} rows for the given project as deleted/invalid
     * so the downstream QuickBooks sync service knows not to push them:
     * <ul>
     *   <li>{@code SYNCED} → {@code DELETED} (the project existed in QBO; push a void/delete)</li>
     *   <li>{@code PENDING_PUSH} → {@code INVALID} (not yet in QBO; nothing to push)</li>
     * </ul>
     *
     * <p>The call is skipped entirely when:
     * <ul>
     *   <li>No Data Exchange service is configured for this tenant (QuickBooks not connected)</li>
     *   <li>The tenant cannot be resolved</li>
     *   <li>{@code projectId} is null</li>
     * </ul>
     *
     * <p>Best-effort: any exception is logged and swallowed — this must never fail the project delete.
     *
     * @param projectId internal {@code id} (PK) of the project being deleted
     */
    public void markDeletedForProject(final Long projectId) {
        try {
            if (projectId == null) {
                logger.warn("markDeletedForProject called with null projectId, skipping");
                return;
            }

            Tenant tenant = tenantRepository.findTenant();
            if (tenant == null) {
                logger.warn("No tenant resolved for projectId={}, skipping project external map update", projectId);
                return;
            }

            List<Long> externalServiceIds = getExternalServiceIds();
            if (externalServiceIds == null || externalServiceIds.isEmpty()) {
                logger.info("No Data Exchange service configured for tenant={}, skipping project external map update for projectId={}",
                        tenant.getTenantid(), projectId);
                return;
            }

            projectExternalMapRepository.updateSyncStatusOnDelete(projectId);
            logger.info("Project external map sync status updated on delete for projectId={} tenantId={}",
                    projectId, tenant.getTenantid());

            AuditLogDTO auditLogDTO = new AuditLogDTO();
            auditLogDTO.setAction(AuditLogConstant.Action.PROJECT_DELETE.toString());
            auditLogDTO.setStatus(DomainConstant.SUCCESS);
            auditLogDTO.setResourceId(String.valueOf(projectId));
            auditLogDTO.setResourceType(AuditLogConstant.Feature.PROJECT_MANAGEMENT.toString());
            auditLogDTO.setProjectId(projectId);
            auditLogDTO.setData(new JSONObject().put("note", "Project external map marked deleted for QBO sync").put("projectId", projectId).toString());
            auditLogService.saveAuditLog(auditLogDTO);

        } catch (Exception e) {
            logger.error("Failed to update project external map for projectId={}: {}",
                    projectId, e.getMessage(), e);
        }
    }

    private List<Long> getExternalServiceIds() {
        try {
            return serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE_SERVICE_TYPE);
        } catch (Exception e) {
            logger.error("Error retrieving service IDs for service type {}: {}", DATA_EXCHANGE_SERVICE_TYPE, e.getMessage(), e);
            return List.of();
        }
    }
}

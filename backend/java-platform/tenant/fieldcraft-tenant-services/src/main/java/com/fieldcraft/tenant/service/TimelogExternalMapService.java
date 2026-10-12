package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.shared.constant.AuditLogConstant;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.TimelogExternalMapRepository;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.domain.TimelogExternalMap;
import com.fieldcraft.tenant.data.model.AuditLogDTO;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.json.JSONObject;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static com.fieldcraft.tenant.data.constant.DomainConstant.SUCCESS;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_INVALID;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_PENDING_PUSH;
import static com.fieldcraft.tenant.data.status.TaskStatus.COMPLETED;

/**
 * Single source of truth for writing {@code timelog_external_map} rows. Mirrors
 * {@code UserAccountExternalMapService}: a timelog event (clock-out completion, approval, or
 * rejection) produces a fresh {@code PENDING_PUSH} version so the change is re-synced to QuickBooks
 * (as a TimeActivity), carrying the previous external ids forward.
 *
 * <p>Two differences from the user map, both driven by the {@code timelog_external_map} schema:
 * <ul>
 *   <li>The map is keyed on {@code timelog_id} (a timelog has no UUID), not a UUID.</li>
 *   <li>The "open/current" row is the one whose {@code endorsement_expiry_date IS NULL}, enforced
 *       unique by {@code ux_timelog_external_map_active_timelog}. So we must <b>close the prior
 *       open row(s) first, then insert the new open row</b> (the user map's insert-first ordering
 *       would violate that index).</li>
 * </ul>
 *
 * <p>The whole operation is best-effort — any failure is logged and swallowed so it never fails the
 * underlying clock-out / approve / reject.
 */
@Service
public class TimelogExternalMapService {

    private static final Logger logger = LogManager.getLogger(TimelogExternalMapService.class);
    private static final String DATA_EXCHANGE_SERVICE_TYPE = "Data Exchange";

    private final TimelogExternalMapRepository timelogExternalMapRepository;
    private final ServiceMasterRepository serviceMasterRepository;
    private final AuditLogService auditLogService;
    private final TenantRepository tenantRepository;

    @Value("${fieldcraft.externalService.url}")
    String externalServiceUrl;                          // package-private for test injection
    HttpClient httpClient = HttpClient.newHttpClient();  // package-private (non-final) for test injection

    public TimelogExternalMapService(final TimelogExternalMapRepository timelogExternalMapRepository,
                                     final ServiceMasterRepository serviceMasterRepository,
                                     final AuditLogService auditLogService,
                                     final TenantRepository tenantRepository) {
        this.timelogExternalMapRepository = timelogExternalMapRepository;
        this.serviceMasterRepository = serviceMasterRepository;
        this.auditLogService = auditLogService;
        this.tenantRepository = tenantRepository;
    }

    /**
     * Queues the timelog for QBO sync: closes the prior open version and inserts a new
     * {@code PENDING_PUSH} version per Data Exchange service, with {@code version = MAX + 1} and the
     * previous external ids carried forward.
     *
     * @param timelogId the timelog whose state changed (completed / approved / rejected)
     */
    public void saveForTimelog(final Long timelogId) {
        saveForTimelog(timelogId, null);
    }

    /**
     * Variant that records why the push was queued (e.g. "completed", "Approve", "Reject") in the
     * {@code reason} column, so the downstream QBO sync service has the triggering context.
     * Always resolves to {@code PENDING_PUSH} — see {@link #saveForTimelog(Long, String, String)}
     * for a variant that can target a different {@code sync_status}.
     */
    public void saveForTimelog(final Long timelogId, final String reason) {
        saveForTimelog(timelogId, reason, null);
    }

    /**
     * Variant that ALSO lets the caller target a specific {@code sync_status} on the new row
     * (e.g. {@code SYNC_STATUS_AWAITING_APPROVAL}, {@code SYNC_STATUS_REJECTED}), instead of the
     * default {@code PENDING_PUSH}.
     *
     * <p>Deliberately a SEPARATE, explicit parameter rather than inferring the status from
     * {@code reason} — {@code reason} is not a closed, internally-controlled vocabulary: the
     * legacy {@code TimelogService.updateApproveReject} path forwards an external, path-variable
     * {@code status} string straight through as {@code reason} (see its call site), so a
     * reason-string-sniffing resolver would let that legacy caller (and, depending on that
     * endpoint's authorization, potentially any caller of it) silently steer a QBO sync-control
     * column just by choosing that string. Keeping the two call sites that actually need a
     * non-default status (submit -> AWAITING_APPROVAL, reject -> REJECTED, both in
     * {@code TimelogApprovalService}) as the ONLY callers of this 3-arg overload closes that hole:
     * every other caller (including the legacy path) keeps using the 2-arg overload above, which
     * can never produce anything but {@code PENDING_PUSH}, exactly as before this class started
     * distinguishing sync_status at all.
     *
     * @param explicitSyncStatus the target {@code sync_status}, or {@code null} to keep the
     *                           default {@code PENDING_PUSH} (same as the 2-arg overload).
     */
    @Transactional
    public void saveForTimelog(final Long timelogId, final String reason, final String explicitSyncStatus) {
        try {
            if (timelogId == null) {
                return;
            }
            Tenant tenant = tenantRepository.findTenant();
            if (tenant == null) {
                logger.warn("No tenant resolved for timelog {}, skipping external map", timelogId);
                return;
            }
            String tenantId = tenant.getTenantid();

            List<Long> externalServiceIds = getExternalServiceIds();
            if (externalServiceIds == null || externalServiceIds.isEmpty()) {
                logger.warn("No serviceId found for serviceType {} for timelog {}, skipping external map",
                        DATA_EXCHANGE_SERVICE_TYPE, timelogId);
                return;
            }

            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);

            // INVALIDATE-FIRST: the partial unique index allows only one open row per
            // (tenant, service, timelog), so close the currently-open row(s) before inserting the
            // new open version. Closing = set INVALID and stamp the version/endorsement expiry
            // (endorsement_expiry_date becoming non-null is what frees the unique index).
            List<TimelogExternalMap> openRows = timelogExternalMapRepository.findOpenByTimelogId(timelogId);
            if (openRows != null) {
                for (TimelogExternalMap existing : openRows) {
                    // findOpenByTimelogId returns open rows for ALL services, but below we only
                    // re-create rows for the currently-configured externalServiceIds. If a service
                    // was removed from service_master, its open row is closed here but never
                    // replaced — warn so that broken sync chain is not silent (Raja review).
                    if (!externalServiceIds.contains(existing.getServiceId())) {
                        logger.warn("Closing open timelog_external_map row for timelogId={} serviceId={} that is "
                                        + "no longer in the configured Data Exchange services {}; it will NOT be "
                                        + "replaced, leaving that service's sync chain closed.",
                                timelogId, existing.getServiceId(), externalServiceIds);
                    }
                    existing.setSyncStatus(SYNC_STATUS_INVALID);
                    existing.setVersionExpiryDate(now);
                    existing.setEndorsementExpiryDate(now);
                    existing.setUpdatedAt(now);
                    timelogExternalMapRepository.save(existing);
                }
            }

            for (Long externalServiceId : externalServiceIds) {
                String previousTimeActivityId = null;
                String previousAccountId = null;
                String previousDisplayName = null;

                // Carry the QBO ids forward from the most recent row that has them (any status),
                // bounded to a single row by the repository query.
                TimelogExternalMap latestWithExternalId = timelogExternalMapRepository
                        .findFirstByTimelogIdAndServiceIdAndExternalTimeActivityIdIsNotNullOrderByVersionDesc(
                                timelogId, externalServiceId);
                if (latestWithExternalId != null) {
                    previousTimeActivityId = latestWithExternalId.getExternalTimeActivityId();
                    previousAccountId = latestWithExternalId.getExternalAccountId();
                    previousDisplayName = latestWithExternalId.getExternalDisplayName();
                }

                Integer maxVersion = timelogExternalMapRepository
                        .findMaxVersionByTimelogIdAndServiceId(timelogId, externalServiceId);

                TimelogExternalMap map = mapToTimelogExternalMap(timelogId, tenantId, externalServiceId,
                        maxVersion, previousTimeActivityId, previousAccountId, previousDisplayName, reason,
                        explicitSyncStatus, now);

                TimelogExternalMap saved = timelogExternalMapRepository.save(map);

                AuditLogDTO auditLogDTO = new AuditLogDTO();
                auditLogDTO.setAction(AuditLogConstant.Action.TIMELOG_MODIFY.toString());
                auditLogDTO.setStatus(SUCCESS);
                auditLogDTO.setResourceId(String.valueOf(timelogId));
                auditLogDTO.setResourceType(AuditLogConstant.Feature.TIMELOG_MANAGEMENT.toString());
                auditLogDTO.setProjectId(null);
                auditLogDTO.setData(new JSONObject()
                                .put("note", "Timelog external map created for QBO sync")
                                .put("timelogId", timelogId)
                                .put("serviceId", externalServiceId)
                                .put("syncStatus", saved.getSyncStatus())
                                .put("reason", reason)
                                .toString());
                auditLogService.saveAuditLog(auditLogDTO);

                // Completion-only: a GoPAL task completion (reason = "completed") triggers the QBO
                // TimeActivity push. Approve/reject still version the map row above, but do NOT push.
                // After commit, nudge the Python QBO gateway; saved.getId() is the timelog_external_map
                // PK it reads back. Best-effort + afterCommit, so it can never affect the caller flow.
                if (COMPLETED.equalsIgnoreCase(reason)) {
                    enqueueQuickBooksTimeActivityPush(tenantId, externalServiceId, saved.getId());
                }
            }
        } catch (Exception e) {
            logger.error("Failed to write QBO external map for timelogId {}: {}",
                    timelogId, e.getMessage(), e);
        }
    }

    /**
     * After the current transaction commits, fire the async QBO push for this map row. {@code
     * saveForTimelog} is {@code @Transactional}, so a synchronization is active and we defer to
     * {@code afterCommit} (Python only ever reads a committed row). If none is active (e.g. a unit
     * test with a non-proxied instance), push directly.
     */
    private void enqueueQuickBooksTimeActivityPush(final String tenantId, final Long serviceId, final Long timelogExternalId) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    triggerQuickBooksTimeActivityCreate(tenantId, serviceId, timelogExternalId);
                }
            });
        } else {
            triggerQuickBooksTimeActivityCreate(tenantId, serviceId, timelogExternalId);
        }
    }

    /**
     * Fire-and-forget POST to the Python integration gateway so it creates/updates the QuickBooks
     * TimeActivity for this {@code timelog_external_map} row. Mirrors {@code ReceiptService}'s
     * bill-create trigger: identifiers only ({@code tenant_id, service_id, timelog_external_map_id}) —
     * the gateway reads the row and the timelog from the tenant DB and maps it to QBO. Async and
     * log-only, so a QBO/network failure never affects the clock-out / approve / reject flow.
     */
    private void triggerQuickBooksTimeActivityCreate(final String tenantId, final Long serviceId,
                                                     final Long timelogExternalMapId) {
        try {
            if (externalServiceUrl == null || externalServiceUrl.isBlank()) {
                return;
            }
            String url = externalServiceUrl + "/extsvc/api/v1/quickbooks/timelogs/create";
            JSONObject payload = new JSONObject()
                    .put("tenant_id", tenantId)
                    .put("service_id", String.valueOf(serviceId))
                    .put("timelog_external_map_id", timelogExternalMapId);
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofMinutes(1))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(payload.toString()))
                    .build();
            httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                    .thenAccept(response -> {
                        if (response.statusCode() != 200) {
                            logger.error("QuickBooks TimeActivity create failed for timelogExternalMapId {}: HTTP {}",
                                    timelogExternalMapId, response.statusCode());
                        } else {
                            logger.info("QuickBooks TimeActivity create triggered for timelogExternalMapId {}",
                                    timelogExternalMapId);
                        }
                    })
                    .exceptionally(e -> {
                        logger.error("Error triggering QuickBooks TimeActivity create for timelogExternalMapId {}: {}",
                                timelogExternalMapId, e.getMessage());
                        return null;
                    });
        } catch (Exception e) {
            logger.error("Error triggering QuickBooks TimeActivity create for timelogExternalMapId {}: {}",
                    timelogExternalMapId, e.getMessage(), e);
        }
    }

    private List<Long> getExternalServiceIds() {
        try {
            return serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE_SERVICE_TYPE);
        } catch (Exception e) {
            logger.error("Error retrieving service IDs for service type: {}", DATA_EXCHANGE_SERVICE_TYPE, e);
            return List.of();
        }
    }

    private TimelogExternalMap mapToTimelogExternalMap(Long timelogId, String tenantId, Long serviceId,
                                                       Integer maxVersion, String previousTimeActivityId,
                                                       String previousAccountId, String previousDisplayName,
                                                       String reason, String explicitSyncStatus, OffsetDateTime now) {
        TimelogExternalMap map = new TimelogExternalMap();
        map.setTimelogId(timelogId);
        map.setTenantId(tenantId);
        map.setServiceId(serviceId);
        map.setSyncStatus(resolveSyncStatus(explicitSyncStatus));
        map.setVersion((maxVersion == null ? 0 : maxVersion) + 1);
        map.setVersionEffectiveDate(now);
        // Open/current row → expiry dates stay NULL (this is the row the partial unique index tracks).
        map.setVersionExpiryDate(null);
        map.setEndorsementEffectiveDate(now);
        map.setEndorsementExpiryDate(null);
        map.setExternalTimeActivityId(previousTimeActivityId);
        map.setExternalAccountId(previousAccountId);
        map.setExternalDisplayName(previousDisplayName);
        map.setReason(buildReason(reason));
        map.setUpdatedAt(now);
        return map;
    }

    /**
     * Resolves the {@code sync_status} to store on the new open row: the caller's explicit
     * override if one was given (see the 3-arg {@code saveForTimelog} overload), else the
     * pre-existing default {@code PENDING_PUSH}. Deliberately does NOT inspect {@code reason} —
     * see the 3-arg overload's javadoc for why (reason is not a closed, internally-controlled
     * vocabulary; a legacy caller forwards an external string through it).
     */
    private String resolveSyncStatus(final String explicitSyncStatus) {
        return explicitSyncStatus != null ? explicitSyncStatus : SYNC_STATUS_PENDING_PUSH;
    }

    /**
     * Builds the {@code reason} payload stored on the row, as {@code {"message":"..."}} JSON, so the
     * QBO sync side has human-readable context (mirrors the employee external map). Task completion
     * gets the "task completed" message; every other trigger gets the generic "ready to sync"
     * message. A null/blank trigger (the no-reason overload) leaves {@code reason} null.
     */
    private String buildReason(final String reason) {
        if (reason == null || reason.isBlank()) {
            return null;
        }
        final String message = COMPLETED.equalsIgnoreCase(reason.trim())
                ? "Task completed, ready to sync to QuickBooks"
                : "Time log ready to sync to QuickBooks";
        return new JSONObject().put("message", message).toString();
    }
}

package com.fieldcraft.tenant.service.accounts;

import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.accounts.InvoiceExternalMapRepository;
import com.fieldcraft.tenant.data.domain.accounts.InvoiceExternalMap;
import com.fieldcraft.tenant.service.ServiceBase;
import com.fieldcraft.tenant.service.quickbooks.QuickBooksSyncService;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.json.JSONObject;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_AWAITING_APPROVAL;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_INVALID;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_PENDING_PUSH;

/**
 * Single source of truth for writing {@code invoice_external_map} rows. Mirrors
 * {@code PaymentExternalMapService}/{@code TimelogExternalMapService}: an invoice lifecycle event
 * (create, edit, send) produces or updates a row per active "Data Exchange" service, so the
 * change is queued for QuickBooks sync.
 *
 * <p>The current/open version of an invoice mapping is the row whose
 * {@code endorsementExpiryDate} is {@code null} — the partial unique index on
 * {@code invoice_external_map} allows only one such row per (tenant, service, invoice), same
 * convention as {@code timelog_external_map}: close the prior open row(s) first, then insert.
 *
 * <p>Every method here is best-effort: deliberately NOT {@code @Transactional} (each repository
 * write commits on its own, exactly like the sibling {@code PaymentExternalMapService}), and the
 * entire body is wrapped in try/catch so a failure here can never block, fail, or roll back the
 * invoice create/edit/send operation that triggered it.
 *
 * <p>Each saved {@code PENDING_PUSH} row is pushed to QuickBooks through the integration gateway's
 * invoice create endpoint after the surrounding transaction commits (immediately when none is
 * active), forwarding the caller's bearer token and the configured {@code api-secret}.
 */
@Service
public class InvoiceExternalMapService extends ServiceBase {

    private static final Logger logger = LogManager.getLogger(InvoiceExternalMapService.class);
    private static final String DATA_EXCHANGE_SERVICE_TYPE = "Data Exchange";

    private final InvoiceExternalMapRepository invoiceExternalMapRepository;
    private final ServiceMasterRepository serviceMasterRepository;
    private final TenantRepository tenantRepository;
    private final QuickBooksSyncService quickBooksSyncService;

    public InvoiceExternalMapService(final InvoiceExternalMapRepository invoiceExternalMapRepository,
                                     final ServiceMasterRepository serviceMasterRepository,
                                     final TenantRepository tenantRepository,
                                     final QuickBooksSyncService quickBooksSyncService) {
        this.invoiceExternalMapRepository = invoiceExternalMapRepository;
        this.serviceMasterRepository = serviceMasterRepository;
        this.tenantRepository = tenantRepository;
        this.quickBooksSyncService = quickBooksSyncService;
    }

    /**
     * A brand-new invoice was created: insert one {@code AWAITING_APPROVAL} row per active Data
     * Exchange service, {@code version = 1}.
     *
     * @param invoiceId the newly-persisted invoice's id
     */
    public void createForNewInvoice(final Long invoiceId) {
        try {
            if (invoiceId == null) {
                return;
            }
            List<Long> externalServiceIds = getExternalServiceIds();
            if (externalServiceIds.isEmpty()) {
                logger.warn("No serviceId found for serviceType {} for invoiceId {}, skipping invoice external map",
                        DATA_EXCHANGE_SERVICE_TYPE, invoiceId);
                return;
            }
            String tenantId = resolveTenantId();
            if (tenantId == null) {
                logger.warn("No tenant resolved for invoiceId {}, skipping invoice external map", invoiceId);
                return;
            }
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            for (Long externalServiceId : externalServiceIds) {
                InvoiceExternalMap map = new InvoiceExternalMap();
                map.setInvoiceId(invoiceId);
                map.setTenantId(tenantId);
                map.setServiceId(externalServiceId);
                map.setSyncStatus(SYNC_STATUS_AWAITING_APPROVAL);
                map.setVersion(1);
                map.setVersionEffectiveDate(now);
                map.setVersionExpiryDate(null);
                map.setEndorsementEffectiveDate(now);
                map.setEndorsementExpiryDate(null);
                map.setUpdatedAt(now);
                map.setReason(buildReason(SYNC_STATUS_AWAITING_APPROVAL));
                invoiceExternalMapRepository.save(map);
            }
        } catch (Exception e) {
            logger.error("Failed to write invoice external map for new invoiceId {}: {}",
                    invoiceId, e.getMessage(), e);
        }
    }

    /**
     * An invoice was edited: {@code updateInvoice()} always invalidates the old invoice row and
     * persists a new one with a different id, sharing the same {@code invoiceNo}. Closes the open
     * map row(s) on the old invoice id as {@code INVALID}, then inserts a fresh
     * {@code AWAITING_APPROVAL} row on the new invoice id per active Data Exchange service —
     * always resetting to {@code AWAITING_APPROVAL} regardless of what status the old row had
     * reached, since the sync status belongs to that specific invoice version, not the invoice as
     * a whole. Carries {@code externalInvoiceId}/{@code externalAccountId} forward from the old
     * row, since those identify the QuickBooks-side invoice and must not be lost on an edit.
     *
     * @param oldInvoiceId the invoice id being superseded (already invalidated by the caller)
     * @param newInvoiceId the freshly-saved invoice id replacing it
     */
    public void reversionOnEdit(final Long oldInvoiceId, final Long newInvoiceId) {
        try {
            if (oldInvoiceId == null || newInvoiceId == null) {
                return;
            }
            List<Long> externalServiceIds = getExternalServiceIds();
            if (externalServiceIds.isEmpty()) {
                logger.warn("No serviceId found for serviceType {} for invoiceId {}, skipping invoice external map revision",
                        DATA_EXCHANGE_SERVICE_TYPE, newInvoiceId);
                return;
            }
            String tenantId = resolveTenantId();
            if (tenantId == null) {
                logger.warn("No tenant resolved for invoiceId {}, skipping invoice external map revision", newInvoiceId);
                return;
            }
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);

            // Close-first: the partial unique index allows only one open row per
            // (tenant, service, invoice), so close the old invoice's open row(s) before
            // inserting the new invoice's open row.
            List<InvoiceExternalMap> openRows = invoiceExternalMapRepository.findOpenByInvoiceId(oldInvoiceId);
            for (InvoiceExternalMap existing : openRows) {
                existing.setSyncStatus(SYNC_STATUS_INVALID);
                existing.setVersionExpiryDate(now);
                existing.setEndorsementExpiryDate(now);
                existing.setUpdatedAt(now);
                existing.setInvalid(true);
                existing.setReason(buildReason(SYNC_STATUS_INVALID));
                invoiceExternalMapRepository.save(existing);
            }

            for (Long externalServiceId : externalServiceIds) {
                String previousExternalInvoiceId = null;
                String previousExternalAccountId = null;

                InvoiceExternalMap latestWithExternalId = invoiceExternalMapRepository
                        .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(
                                oldInvoiceId, externalServiceId);
                if (latestWithExternalId != null) {
                    previousExternalInvoiceId = latestWithExternalId.getExternalInvoiceId();
                    previousExternalAccountId = latestWithExternalId.getExternalAccountId();
                }

                Integer maxVersion = invoiceExternalMapRepository
                        .findMaxVersionByInvoiceIdAndServiceId(oldInvoiceId, externalServiceId);

                InvoiceExternalMap map = new InvoiceExternalMap();
                map.setInvoiceId(newInvoiceId);
                map.setTenantId(tenantId);
                map.setServiceId(externalServiceId);
                map.setSyncStatus(SYNC_STATUS_AWAITING_APPROVAL);
                map.setVersion((maxVersion == null ? 0 : maxVersion) + 1);
                map.setVersionEffectiveDate(now);
                map.setVersionExpiryDate(null);
                map.setEndorsementEffectiveDate(now);
                map.setEndorsementExpiryDate(null);
                map.setExternalInvoiceId(previousExternalInvoiceId);
                map.setExternalAccountId(previousExternalAccountId);
                map.setUpdatedAt(now);
                map.setReason(buildReason(SYNC_STATUS_AWAITING_APPROVAL));
                invoiceExternalMapRepository.save(map);
            }
        } catch (Exception e) {
            logger.error("Failed to revision invoice external map from oldInvoiceId {} to newInvoiceId {}: {}",
                    oldInvoiceId, newInvoiceId, e.getMessage(), e);
        }
    }

    /**
     * An invoice was saved and sent (its "Approve Invoice Task" completed): closes the open
     * {@code AWAITING_APPROVAL} row(s) and inserts a fresh {@code PENDING_PUSH} row, same
     * {@code invoiceId}, {@code version + 1} — every status transition gets its own row/version,
     * not an in-place overwrite, so the full history (Awaiting Approval → Pending Push) stays
     * queryable. The superseded {@code AWAITING_APPROVAL} row is closed as {@code INVALID}, so
     * the only row with a live sync status is the new {@code PENDING_PUSH} one. A row not
     * currently {@code AWAITING_APPROVAL} is left untouched (e.g. already
     * {@code PENDING_PUSH}/{@code SYNCED} from a prior send, or {@code INVALID}).
     *
     * @param invoiceId the invoice whose send just completed
     */
    public void markPendingPush(final Long invoiceId) {
        try {
            if (invoiceId == null) {
                return;
            }
            final String bearerToken = getBearerToken();
            List<InvoiceExternalMap> openRows = invoiceExternalMapRepository.findOpenByInvoiceId(invoiceId);
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            for (InvoiceExternalMap existing : openRows) {
                if (!SYNC_STATUS_AWAITING_APPROVAL.equalsIgnoreCase(existing.getSyncStatus())) {
                    continue;
                }
                // Close-first: the partial unique index allows only one open row per
                // (tenant, service, invoice), so this AWAITING_APPROVAL row is closed as INVALID
                // before the PENDING_PUSH row is inserted; only the newest open row carries a
                // live sync status, same convention as reversionOnEdit().
                existing.setSyncStatus(SYNC_STATUS_INVALID);
                existing.setVersionExpiryDate(now);
                existing.setEndorsementExpiryDate(now);
                existing.setUpdatedAt(now);
                existing.setInvalid(true);
                existing.setReason(buildReason(SYNC_STATUS_INVALID));
                invoiceExternalMapRepository.save(existing);

                InvoiceExternalMap saved = invoiceExternalMapRepository
                        .save(buildPendingPushRow(existing, invoiceId, now));
                enqueueQuickBooksInvoicePush(saved.getTenantId(), saved.getServiceId(), saved.getId(), bearerToken);
            }
        } catch (Exception e) {
            logger.error("Failed to mark invoice external map PENDING_PUSH for invoiceId {}: {}",
                    invoiceId, e.getMessage(), e);
        }
    }

    /**
     * Builds the next {@code PENDING_PUSH} version for the closed row's invoice and service, at
     * {@code MAX(version) + 1}, carrying the latest known QuickBooks ids forward (falling back to
     * the closed row's own ids when no version ever carried one).
     */
    private InvoiceExternalMap buildPendingPushRow(final InvoiceExternalMap closed, final Long invoiceId,
                                                   final OffsetDateTime now) {
        Long serviceId = closed.getServiceId();
        InvoiceExternalMap latestWithExternalId = invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(invoiceId, serviceId);
        InvoiceExternalMap idSource = latestWithExternalId != null ? latestWithExternalId : closed;
        Integer maxVersion = invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(invoiceId, serviceId);

        InvoiceExternalMap map = new InvoiceExternalMap();
        map.setInvoiceId(invoiceId);
        map.setTenantId(closed.getTenantId());
        map.setServiceId(serviceId);
        map.setSyncStatus(SYNC_STATUS_PENDING_PUSH);
        map.setVersion((maxVersion == null ? 0 : maxVersion) + 1);
        map.setVersionEffectiveDate(now);
        map.setVersionExpiryDate(null);
        map.setEndorsementEffectiveDate(now);
        map.setEndorsementExpiryDate(null);
        map.setExternalInvoiceId(idSource.getExternalInvoiceId());
        map.setExternalAccountId(idSource.getExternalAccountId());
        map.setUpdatedAt(now);
        map.setReason(buildReason(SYNC_STATUS_PENDING_PUSH));
        return map;
    }

    /**
     * Fires the QuickBooks invoice push for a saved row after the current transaction commits, so
     * the gateway only ever reads a committed row; with no active synchronization it fires now.
     */
    private void enqueueQuickBooksInvoicePush(final String tenantId, final Long serviceId,
                                              final Long invoiceExternalMapId, final String bearerToken) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    try {
                        quickBooksSyncService.triggerInvoiceCreate(tenantId, serviceId, invoiceExternalMapId, bearerToken);
                        // CP-EXEMPT: java_catch_all reason="afterCommit() has no caller to rethrow to; this keeps this class's own contextual logging in front of Spring's generic afterCommit() failure log, matching this class's stated best-effort contract" workitem=AB#31008 expires=2026-12-31
                    } catch (Exception e) {
                        // Belt-and-suspenders: triggerInvoiceCreate already never throws for the
                        // cases it knows about, but this keeps the class's own logging (with
                        // context) in front of Spring's generic afterCommit() failure log for
                        // anything else, matching every other code path in this class.
                        logger.error("QuickBooks invoice create after-commit failed for tenantId {}, "
                                + "serviceId {}, invoiceExternalMapId {}: {}",
                                tenantId, serviceId, invoiceExternalMapId, e.getMessage(), e);
                    }
                }
            });
        } else {
            quickBooksSyncService.triggerInvoiceCreate(tenantId, serviceId, invoiceExternalMapId, bearerToken);
        }
    }

    private List<Long> getExternalServiceIds() {
        try {
            List<Long> ids = serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE_SERVICE_TYPE);
            return ids == null ? List.of() : ids;
        } catch (Exception e) {
            logger.error("Error retrieving service IDs for service type: {}", DATA_EXCHANGE_SERVICE_TYPE, e);
            return List.of();
        }
    }

    private String resolveTenantId() {
        var tenant = tenantRepository.findTenant();
        return tenant == null ? null : tenant.getTenantid();
    }

    /**
     * Builds the {@code reason} payload persisted on a row, as {@code {"message":"..."}} JSON so
     * the QBO sync side has a human-readable cause for the row's current status. Mirrors
     * {@code UserAccountExternalMapService}/{@code TimelogExternalMapService}'s convention.
     */
    private String buildReason(final String syncStatus) {
        final String message;
        if (SYNC_STATUS_INVALID.equalsIgnoreCase(syncStatus)) {
            message = "Invoice version is invalid; it has been replaced by a newer version.";
        } else if (SYNC_STATUS_PENDING_PUSH.equalsIgnoreCase(syncStatus)) {
            message = "Invoice is approved and queued for QuickBooks sync.";
        } else {
            message = "Invoice is awaiting approval and not yet ready to sync to QuickBooks.";
        }
        return new JSONObject().put("message", message).toString();
    }
}

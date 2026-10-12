package com.fieldcraft.tenant.service.accounts;

import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.accounts.PaymentExternalMapRepository;
import com.fieldcraft.tenant.data.domain.accounts.PaymentExpense;
import com.fieldcraft.tenant.data.domain.accounts.PaymentExternalMap;
import com.fieldcraft.tenant.data.domain.accounts.PaymentReceives;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.json.JSONObject;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_AWAITING_APPROVAL;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_INVALID;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_PENDING_PUSH;

@Service
public class PaymentExternalMapService {

    private static final Logger logger = LogManager.getLogger(PaymentExternalMapService.class);
    private static final String DATA_EXCHANGE_SERVICE_TYPE = "Data Exchange";
    private static final String REASON_MESSAGE_KEY = "message";

    private final ServiceMasterRepository serviceMasterRepository;
    private final TenantRepository tenantRepository;
    private final PaymentExternalMapRepository paymentExternalMapRepository;

    public PaymentExternalMapService(ServiceMasterRepository serviceMasterRepository,
                                     TenantRepository tenantRepository,
                                     PaymentExternalMapRepository paymentExternalMapRepository) {
        this.serviceMasterRepository = serviceMasterRepository;
        this.tenantRepository = tenantRepository;
        this.paymentExternalMapRepository = paymentExternalMapRepository;
    }

    public void save(PaymentExpense savedExpense, String syncStatus) {
        try {
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            List<Long> externalServiceIds = serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE_SERVICE_TYPE);
            if (externalServiceIds == null || externalServiceIds.isEmpty()) {
                logger.warn("No serviceId found for type {} for expense {}, skipping payment external map",
                        DATA_EXCHANGE_SERVICE_TYPE, savedExpense.getId());
                return;
            }
            String tenantId = tenantRepository.findTenant().getTenantid();
            for (Long externalServiceId : externalServiceIds) {
                PaymentExternalMap map = new PaymentExternalMap();
                map.setPaymentExpense(savedExpense);
                map.setTenantId(tenantId);
                map.setServiceId(externalServiceId.intValue());
                map.setSyncStatus(syncStatus);
                map.setEndorsementEffectiveDate(now);
                Integer maxVersion = paymentExternalMapRepository
                        .findMaxVersionByPaymentExpenseIdAndServiceId(savedExpense.getId(), externalServiceId.intValue());
                map.setVersion((maxVersion == null ? 0 : maxVersion) + 1);
                paymentExternalMapRepository.save(map);
            }
        } catch (Exception e) {
            logger.error("Error saving payment external map for expense {}: {}", savedExpense.getId(), e.getMessage(), e);
        }
    }

    /**
     * A customer payment was received: queue it for QuickBooks by inserting one {@code PENDING_PUSH}
     * row per active Data Exchange service. Unlike the invoice flow there is no approval stage and
     * no versioning -- a {@code payment_receives} row has no edit path (no REST endpoint, no
     * GraphQL mutation, no service update method), so there is never a second version to write.
     *
     * <p>{@code ux_payment_external_map_active_receive} (V1_5_2_0_41) allows only one open row per
     * (tenant, service, payment), so a retry cannot queue the same payment twice.
     *
     * <p>Called only from the three real payment paths. Deliberately NOT called from the GL import
     * ({@code GeneralLedgerImportService}) -- those are ledger rows, not payment events. The
     * QuickBooks import inserts its payments from Python and never reaches this code at all.
     *
     * <p>Best-effort, like every method here: failures are logged and swallowed so a mapping problem
     * can never fail the payment that triggered it.
     *
     * @param payment the freshly-persisted customer payment
     */
    public void createForNewPaymentReceive(final PaymentReceives payment) {
        try {
            if (payment == null || payment.getId() == null) {
                return;
            }
            List<Long> externalServiceIds = serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE_SERVICE_TYPE);
            if (externalServiceIds == null || externalServiceIds.isEmpty()) {
                logger.warn("No serviceId found for type {} for payment {}, skipping payment external map",
                        DATA_EXCHANGE_SERVICE_TYPE, payment.getId());
                return;
            }
            String tenantId = resolveTenantId();
            if (tenantId == null) {
                logger.warn("No tenant resolved for payment {}, skipping payment external map", payment.getId());
                return;
            }
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            for (Long externalServiceId : externalServiceIds) {
                PaymentExternalMap map = new PaymentExternalMap();
                map.setPaymentReceives(payment);
                map.setTenantId(tenantId);
                map.setServiceId(externalServiceId.intValue());
                map.setSyncStatus(SYNC_STATUS_PENDING_PUSH);
                map.setVersion(1);
                map.setVersionEffectiveDate(now);
                map.setVersionExpiryDate(null);
                map.setEndorsementEffectiveDate(now);
                map.setEndorsementExpiryDate(null);
                map.setUpdatedAt(now);
                map.setReason(buildReason());
                paymentExternalMapRepository.save(map);
            }
        } catch (Exception e) {
            logger.error("Error saving payment external map for payment {}: {}",
                    payment == null ? null : payment.getId(), e.getMessage(), e);
        }
    }

    private String resolveTenantId() {
        var tenant = tenantRepository.findTenant();
        return tenant == null ? null : tenant.getTenantid();
    }

    private String buildReason() {
        return new JSONObject()
                .put(REASON_MESSAGE_KEY, "Payment received in FIELDCRAFT and queued for QuickBooks sync.")
                .toString();
    }

    /**
     * A customer payment was verified/reconciled ({@code storeReconciliationPayments}): close the
     * open map row(s) as {@code INVALID} and insert a fresh {@code PENDING_PUSH} row per service,
     * {@code version + 1} -- same close-then-insert convention as
     * {@code InvoiceExternalMapService.markPendingPush}, required because
     * {@code ux_payment_external_map_active_receive} allows only one open row per
     * (tenant, service, payment).
     *
     * <p>Carries {@code externalPaymentId}, {@code externalAccountId}, {@code syncToken} and
     * {@code customerId} forward from the closed row so the exporter amends the existing
     * QuickBooks payment instead of creating a duplicate. Deliberately does NOT carry
     * {@code additionalInfo}: it holds the exporter's {@code sourceFingerprint} and
     * {@code exportClaim} lease, and a stale value would make the new version look already-synced
     * or mid-claim.
     *
     * <p>If the payment has no open row (it was never queued -- e.g. no Data Exchange service was
     * active at payment time), this is a no-op: reconciliation does not backfill a missing row.
     *
     * <p>Best-effort: failures are logged and swallowed so a mapping problem can never fail
     * reconciliation.
     *
     * @param paymentReceivesId the reconciled customer payment
     */
    public void requeueOnReconcile(final Long paymentReceivesId) {
        try {
            if (paymentReceivesId == null) {
                return;
            }
            List<PaymentExternalMap> openRows = paymentExternalMapRepository.findOpenByPaymentReceivesId(paymentReceivesId);
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            for (PaymentExternalMap existing : openRows) {
                Integer serviceId = existing.getServiceId();

                existing.setSyncStatus(SYNC_STATUS_INVALID);
                existing.setVersionExpiryDate(now);
                existing.setEndorsementExpiryDate(now);
                existing.setUpdatedAt(now);
                existing.setInvalid(true);
                existing.setReason(buildInvalidatedReason());
                paymentExternalMapRepository.save(existing);

                List<PaymentExternalMap> withExternalId = paymentExternalMapRepository
                        .findByPaymentReceivesIdAndServiceIdAndExternalPaymentIdIsNotNullOrderByVersionDesc(
                                paymentReceivesId, serviceId);
                PaymentExternalMap latestWithExternalId = withExternalId.isEmpty() ? null : withExternalId.get(0);

                String previousExternalPaymentId = latestWithExternalId != null
                        ? latestWithExternalId.getExternalPaymentId() : existing.getExternalPaymentId();
                String previousExternalAccountId = latestWithExternalId != null
                        ? latestWithExternalId.getExternalAccountId() : existing.getExternalAccountId();
                String previousSyncToken = latestWithExternalId != null
                        ? latestWithExternalId.getSyncToken() : existing.getSyncToken();

                Integer maxVersion = paymentExternalMapRepository
                        .findMaxVersionByPaymentReceivesIdAndServiceId(paymentReceivesId, serviceId);

                PaymentExternalMap map = new PaymentExternalMap();
                map.setPaymentReceives(existing.getPaymentReceives());
                map.setTenantId(existing.getTenantId());
                map.setServiceId(serviceId);
                map.setCustomerId(existing.getCustomerId());
                map.setSyncStatus(SYNC_STATUS_PENDING_PUSH);
                map.setVersion((maxVersion == null ? 0 : maxVersion) + 1);
                map.setVersionEffectiveDate(now);
                map.setVersionExpiryDate(null);
                map.setEndorsementEffectiveDate(now);
                map.setEndorsementExpiryDate(null);
                map.setExternalPaymentId(previousExternalPaymentId);
                map.setExternalAccountId(previousExternalAccountId);
                map.setSyncToken(previousSyncToken);
                map.setUpdatedAt(now);
                map.setReason(buildRequeuedReason());
                paymentExternalMapRepository.save(map);
            }
        } catch (Exception e) {
            logger.error("Failed to requeue payment external map on reconcile for paymentReceivesId {}: {}",
                    paymentReceivesId, e.getMessage(), e);
        }
    }

    private String buildInvalidatedReason() {
        return new JSONObject()
                .put(REASON_MESSAGE_KEY, "Payment version is invalid; it has been replaced by a newer version.")
                .toString();
    }

    private String buildRequeuedReason() {
        return new JSONObject()
                .put(REASON_MESSAGE_KEY, "Payment reconciled and re-queued for QuickBooks sync.")
                .toString();
    }

    // Advances an expense's AWAITING_APPROVAL map row(s) to PENDING_PUSH once the bill they were
    // posted against is reviewed/approved (see ReceiptService#reviewBill and
    // #storeStockReceiptPaymentExpense) -- a row already PENDING_PUSH/SYNCED/etc. is left untouched,
    // so calling this on an already-approved bill (or one with no Mark-as-Paid expense) is a no-op.
    public void advanceAwaitingApprovalToPendingPush(final Long paymentExpenseId) {
        try {
            List<PaymentExternalMap> maps = paymentExternalMapRepository.findByPaymentExpenseId(paymentExpenseId);
            for (PaymentExternalMap map : maps) {
                if (SYNC_STATUS_AWAITING_APPROVAL.equalsIgnoreCase(map.getSyncStatus())) {
                    map.setSyncStatus(SYNC_STATUS_PENDING_PUSH);
                    paymentExternalMapRepository.save(map);
                }
            }
        } catch (Exception e) {
            logger.error("Error advancing payment external map for paymentExpenseId {}: {}", paymentExpenseId, e.getMessage(), e);
        }
    }
}

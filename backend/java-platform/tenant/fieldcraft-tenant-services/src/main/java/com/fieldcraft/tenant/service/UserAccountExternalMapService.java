
package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.shared.constant.AuditLogConstant;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.UserAccountExternalMapRepository;
import com.fieldcraft.tenant.dal.repos.UserRoleMapRepository;
import com.fieldcraft.tenant.dal.repos.UserStatusRepository;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.domain.UserAccount;
import com.fieldcraft.tenant.data.domain.UserAccountExternalMap;
import com.fieldcraft.tenant.data.domain.UserStatus;
import com.fieldcraft.tenant.data.model.AuditLogDTO;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.json.JSONObject;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import static com.fieldcraft.tenant.data.constant.DomainConstant.*;

/**
 * Single source of truth for writing {@code user_account_external_map} rows. Every caller that
 * mutates an employee/user (user create/update, personal-info edit, resource/cost edit, status
 * changes) reuses this implementation instead of duplicating the guard-and-version logic.
 *
 * <p>The eligibility policy lives here, not in callers: the user is null-guarded, customer-only
 * accounts are skipped (external map is employee-only), and the tenant is resolved internally.
 * The whole operation is best-effort — any failure is logged and swallowed so it can never fail
 * the underlying employee save.
 */
@Service
public class UserAccountExternalMapService {

    private static final Logger logger = LogManager.getLogger(UserAccountExternalMapService.class);
    private static final String DATA_EXCHANGE_SERVICE_TYPE = "Data Exchange";
    private static final String CUSTOMER_ROLE = "customer";
    private final UserAccountExternalMapRepository userAccountExternalMapRepository;
    private final ServiceMasterRepository serviceMasterRepository;
    private final AuditLogService auditLogService;
    private final UserRoleMapRepository userRoleMapRepository;
    private final TenantRepository tenantRepository;
    private final UserStatusRepository userStatusRepository;

    public UserAccountExternalMapService(final UserAccountExternalMapRepository userAccountExternalMapRepository,
                                         final ServiceMasterRepository serviceMasterRepository,
                                         final AuditLogService auditLogService,
                                         final UserRoleMapRepository userRoleMapRepository,
                                         final TenantRepository tenantRepository,
                                         final UserStatusRepository userStatusRepository) {
        this.userAccountExternalMapRepository = userAccountExternalMapRepository;
        this.serviceMasterRepository = serviceMasterRepository;
        this.auditLogService = auditLogService;
        this.userRoleMapRepository = userRoleMapRepository;
        this.tenantRepository = tenantRepository;
        this.userStatusRepository = userStatusRepository;
    }

    /**
     * Invalidates any existing {@code PENDING_PUSH}/{@code SYNCED} maps for the user (closing
     * their version/endorsement windows) and creates a fresh {@code PENDING_PUSH} map per
     * Data Exchange service, with {@code version = MAX + 1} and the previous external ids
     * carried forward, so the update is re-synced to QBO.
     *
     * @param savedUser the persisted user/employee whose details changed
     */
    public void saveForUser(final UserAccount savedUser) {
        saveForUser(savedUser, SYNC_STATUS_PENDING_PUSH);
    }

    /**
     * Variant that lets the caller pick the new row's sync status:
     *  - "PENDING_PUSH" — create/update/deactivate/cancel-terminate/reactivate (push the change to QBO)
     *  - "INVALID"      — cancel-hiring (close out the map; nothing to push)
     * In all cases the prior PENDING_PUSH/SYNCED rows are invalidated first. {@code SYNC_FAILED}
     * and existing {@code INVALID} rows are left untouched.
     *
     * <p>Callers pass only the user and (optionally) the status — null-guarding, the customer-only
     * skip, tenant resolution and failure isolation are all handled here.
     */
    public void saveForUser(final UserAccount savedUser, final String newStatus) {
        saveForUser(savedUser, newStatus, null);
    }

    /**
     * Variant that lets the caller supply an already-resolved {@code external_entity_type}.
     * Cancel-hiring needs this: it must capture the 1099/W2 type from the active
     * {@link UserStatus} <em>before</em> that row is invalidated, otherwise
     * {@link #resolveExternalEntityType} can no longer see it and every closing map row would
     * be written as {@code EMPLOYEE}. When {@code externalEntityType} is null the type is
     * resolved internally as usual (preserving the two-arg / create-update behaviour).
     */
    public void saveForUser(final UserAccount savedUser, final String newStatus,
                            final String externalEntityType) {
        try {
            if (savedUser == null) {
                return;
            }
            // Guard a null/blank Azure OID (e.g. a partial create that failed before AD returned the
            // objectId) so UUID.fromString never throws into the generic catch as a noisy error.
            if (savedUser.getObjectId() == null || savedUser.getObjectId().isBlank()) {
                logger.warn("Skipping external map for user id={}: objectId is null/blank", savedUser.getId());
                return;
            }
            // External map is employee-only; customer accounts are tracked via customer_external_map.
            if (isCustomerOnly(savedUser)) {
                return;
            }
            Tenant tenant = tenantRepository.findTenant();
            if (tenant == null) {
                logger.warn("No tenant resolved for user {}, skipping external map", savedUser.getObjectId());
                return;
            }
            String tenantId = tenant.getTenantid();

            List<Long> externalServiceIds = getExternalServiceIds();
            if (externalServiceIds == null || externalServiceIds.isEmpty()) {
                logger.warn("No serviceId found for serviceType {} for user {}, skipping external map",
                        DATA_EXCHANGE_SERVICE_TYPE, savedUser.getObjectId());
                return;
            }

            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            OffsetDateTime future = OffsetDateTime.of(3000, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);
            UUID userUuid = UUID.fromString(savedUser.getObjectId());
            String resolvedEntityType = externalEntityType != null
                    ? externalEntityType : resolveExternalEntityType(savedUser);

            // Capture the rows to close out BEFORE writing the new ones. Insert-first ordering
            // makes a partial failure fail-safe: if the insert fails the old row stays active
            // (the employee is still visible to QBO); if a later invalidation fails we only get a
            // harmless duplicate that self-corrects on the next save — never a closed-out row with
            // no replacement. Only PENDING_PUSH / SYNCED are loaded, so history is never scanned.
            List<UserAccountExternalMap> activeMaps =
                    userAccountExternalMapRepository.findActiveByUserAccountUuid(userUuid);

            for (Long externalServiceId : externalServiceIds) {
                String previousExternalEmployeeId = null;
                String previousExternalAccountId = null;

                // Carry forward the QBO ids from the most recent row that actually has them
                // (any status), bounded to a single row by the repository query.
                UserAccountExternalMap latestWithExternalId = userAccountExternalMapRepository
                        .findFirstByUserAccountUuidAndServiceIdAndExternalEmployeeIdIsNotNullOrderByVersionDesc(
                                userUuid, externalServiceId);
                if (latestWithExternalId != null) {
                    previousExternalEmployeeId = latestWithExternalId.getExternalEmployeeId();
                    previousExternalAccountId = latestWithExternalId.getExternalAccountId();
                }

                Integer maxVersion = userAccountExternalMapRepository
                        .findMaxVersionByUserAccountUuidAndServiceId(userUuid, externalServiceId);

                UserAccountExternalMap map = mapToUserAccountExternalMap(savedUser, userUuid, tenantId,
                        externalServiceId, maxVersion,
                        previousExternalEmployeeId, previousExternalAccountId, now, future, newStatus,
                        resolvedEntityType);

                userAccountExternalMapRepository.save(map);

                AuditLogDTO auditLogDTO = new AuditLogDTO();
                auditLogDTO.setAction(AuditLogConstant.Action.USER_MODIFY.toString());
                auditLogDTO.setStatus(SUCCESS);
                auditLogDTO.setResourceId(String.valueOf(savedUser.getId()));
                auditLogDTO.setResourceType(AuditLogConstant.Feature.USER_MANAGEMENT.toString());
                auditLogDTO.setProjectId(null);
                auditLogDTO.setData(new JSONObject()
                                .put("note", "UserAccount external map created for QBO sync")
                                .put("userUuid", userUuid.toString())
                                .put("serviceId", externalServiceId)
                                .put("syncStatus", newStatus)
                                .toString());
                auditLogService.saveAuditLog(auditLogDTO);
            }

            // Now close out the previously-active rows (captured before the inserts above).
            if (activeMaps != null) {
                for (UserAccountExternalMap existing : activeMaps) {
                    existing.setSyncStatus(SYNC_STATUS_INVALID);
                    existing.setVersionExpiryDate(now);
                    existing.setEndorsementExpiryDate(now);
                    userAccountExternalMapRepository.save(existing);
                }
            }
        } catch (Exception e) {
            logger.error("Failed to write QBO external map for userId {}: {}",
                    savedUser != null ? savedUser.getId() : null, e.getMessage(), e);
        }
    }

    /**
     * Derives {@code external_entity_type} from the user's employment type recorded on
     * {@link UserStatus}: 1099 workers are synced to the external system as vendors,
     * everyone else (W2/regular employees) as employees.
     *
     * <p>Public so callers that invalidate the {@link UserStatus} row (cancel-hiring) can
     * resolve the type while the active row is still visible and pass it to
     * {@link #saveForUser(UserAccount, String, String)}. A null user resolves to the default
     * {@code EMPLOYEE} type.
     */
    public String resolveExternalEntityType(final UserAccount savedUser) {
        if (savedUser == null) {
            return EXTERNAL_ENTITY_TYPE_EMPLOYEE;
        }
        UserStatus userStatus = userStatusRepository.findByUserId(savedUser.getId());
        boolean is1099 = userStatus != null && TEN_NINTYNINE.equalsIgnoreCase(userStatus.getType());
        if (is1099) {
            return EXTERNAL_ENTITY_TYPE_VENDOR;
        } else {
            return EXTERNAL_ENTITY_TYPE_EMPLOYEE;
        }
    }

    /** Employee-only guard: true when every role on the account is the customer role. */
    private boolean isCustomerOnly(final UserAccount savedUser) {
        List<String> roles = userRoleMapRepository.findRoleByUserId(savedUser.getId());
        return roles != null && !roles.isEmpty()
                && roles.stream().allMatch(CUSTOMER_ROLE::equalsIgnoreCase);
    }

    private List<Long> getExternalServiceIds() {
        try {
            return serviceMasterRepository.getIdsByServiceType(DATA_EXCHANGE_SERVICE_TYPE);
        } catch (Exception e) {
            logger.error("Error retrieving service IDs for service type: {}", DATA_EXCHANGE_SERVICE_TYPE, e);
            return List.of();
        }
    }

    private UserAccountExternalMap mapToUserAccountExternalMap(UserAccount savedUser, UUID userUuid, String tenantId,
                                                    Long externalServiceId, Integer maxVersion,
                                                    String previousExternalEmployeeId, String previousExternalAccountId,
                                                    OffsetDateTime now, OffsetDateTime future, String newStatus,
                                                    String externalEntityType) {
        UserAccountExternalMap map = new UserAccountExternalMap();
        map.setUserAccount(savedUser);
        map.setUserAccountUuid(userUuid);
        map.setTenantId(tenantId);
        map.setSyncStatus(newStatus);
        map.setExternalEntityType(externalEntityType);
        map.setServiceId(externalServiceId);
        map.setVersion((maxVersion == null ? 0 : maxVersion) + 1);
        map.setVersionEffectiveDate(now);
        map.setVersionExpiryDate(future);
        map.setEndorsementEffectiveDate(now);
        map.setEndorsementExpiryDate(future);
        map.setExternalEmployeeId(previousExternalEmployeeId);
        map.setExternalAccountId(previousExternalAccountId);
        map.setUpdatedAt(now);
        map.setReason(buildReason(newStatus, maxVersion));
        // externalDisplayName / externalVendorId are intentionally left null here: they are
        // reserved for write-back by the QBO sync consumer, which populates them from the
        // external system's response once the entity is created/matched. They are not derived
        // on the outbound (tenant → QBO) path.
        return map;
    }

    /**
     * Builds the {@code reason} payload persisted on the new map row, as
     * {@code {"message":"..."}} JSON so the QBO sync side can surface a human-readable cause.
     * The message reflects the event that produced the row:
     * <ul>
     *   <li>{@code INVALID} — the employee's hiring was cancelled and the map entry is closed out;</li>
     *   <li>first version ({@code maxVersion == null}) — a newly invited employee who is still
     *       inactive and therefore not yet syncable; sync is deferred until they register;</li>
     *   <li>any later version — an active employee whose change is queued for QuickBooks.</li>
     * </ul>
     */
    private String buildReason(final String newStatus, final Integer maxVersion) {
        final String message;
        if (SYNC_STATUS_INVALID.equalsIgnoreCase(newStatus)) {
            message = "Employee hiring cancelled, so the QuickBooks sync entry has been closed.";
        } else if (maxVersion == null) {
            message = "Ready to sync once the employee becomes active by completing registration "
                    + "or updating their details.";
        } else {
            message = "Employee is active and queued for QuickBooks sync.";
        }
        return new JSONObject().put("message", message).toString();
    }
}

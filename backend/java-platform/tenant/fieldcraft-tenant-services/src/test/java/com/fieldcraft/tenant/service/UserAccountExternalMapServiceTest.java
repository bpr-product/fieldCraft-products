package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.UserAccountExternalMapRepository;
import com.fieldcraft.tenant.dal.repos.UserRoleMapRepository;
import com.fieldcraft.tenant.dal.repos.UserStatusRepository;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.domain.UserAccount;
import com.fieldcraft.tenant.data.domain.UserAccountExternalMap;
import com.fieldcraft.tenant.data.domain.UserStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.argThat;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class UserAccountExternalMapServiceTest {

    private static final long SERVICE_ID = 6L;
    private static final String EXT_EMP_ID = "400000091";
    private static final String EXT_ACC_ID = "9341457167808992";
    private static final String TENANT_ID = "tn24047750";
    private static final OffsetDateTime FAR_FUTURE =
            OffsetDateTime.of(3000, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);

    @Mock
    private UserAccountExternalMapRepository userAccountExternalMapRepository;
    @Mock
    private ServiceMasterRepository serviceMasterRepository;
    @Mock
    private AuditLogService auditLogService;
    @Mock
    private UserRoleMapRepository userRoleMapRepository;
    @Mock
    private TenantRepository tenantRepository;
    @Mock
    private UserStatusRepository userStatusRepository;

    private UserAccountExternalMapService service;

    @BeforeEach
    void setUp() {
        service = new UserAccountExternalMapService(
                userAccountExternalMapRepository, serviceMasterRepository, auditLogService,
                userRoleMapRepository, tenantRepository, userStatusRepository);
        // default: no user_status row → treated as a regular (non-1099) employee
        lenient().when(userStatusRepository.findByUserId(any())).thenReturn(null);
    }

    private UserAccount userWithUuid(UUID uuid) {
        UserAccount user = new UserAccount();
        // Give every user a non-zero id so resolveExternalEntityType's userStatusRepository
        // lookup keys off a stable, distinct value; tests that assert a specific entity type
        // override it (and stub findByUserId) explicitly.
        user.setId(1);
        user.setObjectId(uuid.toString());
        return user;
    }

    private UserAccountExternalMap mapRow(UUID userUuid, String syncStatus, int version,
                                          String externalEmployeeId, String externalAccountId) {
        return mapRow(userUuid, syncStatus, version, externalEmployeeId, externalAccountId, SERVICE_ID);
    }

    private UserAccountExternalMap mapRow(UUID userUuid, String syncStatus, int version,
                                          String externalEmployeeId, String externalAccountId, long serviceId) {
        UserAccountExternalMap map = new UserAccountExternalMap();
        map.setUserAccountUuid(userUuid);
        map.setServiceId(serviceId);
        map.setSyncStatus(syncStatus);
        map.setVersion(version);
        map.setExternalEmployeeId(externalEmployeeId);
        map.setExternalAccountId(externalAccountId);
        return map;
    }

    // ── stub helpers (each test stubs only what its code path exercises, so strict stubs stay clean) ──

    /** Account resolves as an employee (non-customer) so saveForUser proceeds past the skip. */
    private void stubNonCustomer() {
        when(userRoleMapRepository.findRoleByUserId(any())).thenReturn(List.of("employee"));
    }

    /** A resolvable tenant; getTenantid is consumed when saveForUser proceeds to write a row. */
    private void stubTenant() {
        Tenant tenant = mock(Tenant.class);
        when(tenant.getTenantid()).thenReturn(TENANT_ID);
        when(tenantRepository.findTenant()).thenReturn(tenant);
    }

    private void echoSaves() {
        when(userAccountExternalMapRepository.save(any(UserAccountExternalMap.class)))
                .thenAnswer(inv -> inv.getArgument(0));
    }

    /** Active (PENDING_PUSH/SYNCED) rows the invalidation phase will close out. */
    private void stubActive(UUID uuid, UserAccountExternalMap... rows) {
        when(userAccountExternalMapRepository.findActiveByUserAccountUuid(uuid))
                .thenReturn(List.of(rows));
    }

    /** The single latest row carrying external ids, used for carry-forward on a given service. */
    private void stubCarryForward(UUID uuid, long serviceId, UserAccountExternalMap row) {
        when(userAccountExternalMapRepository
                .findFirstByUserAccountUuidAndServiceIdAndExternalEmployeeIdIsNotNullOrderByVersionDesc(uuid, serviceId))
                .thenReturn(row);
    }

    @Test
    void saveForUser_invalidatesSyncedRowAndCreatesNewVersionedPendingPush() {
        UUID userUuid = UUID.randomUUID();
        UserAccountExternalMap synced = mapRow(userUuid, "SYNCED", 1, EXT_EMP_ID, EXT_ACC_ID);

        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid, synced);
        stubCarryForward(userUuid, SERVICE_ID, synced);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(1);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // the previously SYNCED row is invalidated and its version/endorsement windows are closed
        verify(userAccountExternalMapRepository).save(argThat(m ->
                m == synced
                        && "INVALID".equals(m.getSyncStatus())
                        && m.getVersionExpiryDate() != null
                        && m.getEndorsementExpiryDate() != null));

        // a fresh PENDING_PUSH row is created: version bumped, IDs carried forward, windows opened
        verify(userAccountExternalMapRepository).save(argThat(m ->
                m != synced
                        && "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(2).equals(m.getVersion())
                        && EXT_EMP_ID.equals(m.getExternalEmployeeId())
                        && EXT_ACC_ID.equals(m.getExternalAccountId())
                        && m.getVersionEffectiveDate() != null
                        && m.getEndorsementEffectiveDate() != null
                        && m.getUpdatedAt() != null
                        && TENANT_ID.equals(m.getTenantId())
                        && FAR_FUTURE.equals(m.getVersionExpiryDate())
                        && FAR_FUTURE.equals(m.getEndorsementExpiryDate())
                        // existing/active employee re-queued → "active and queued" reason
                        && "{\"message\":\"Employee is active and queued for QuickBooks sync.\"}"
                                .equals(m.getReason())));

        verify(userAccountExternalMapRepository, times(2)).save(any(UserAccountExternalMap.class));
    }

    @Test
    void saveForUser_onlyInvalidatesRowsReturnedByActiveQuery() {
        // The active-only query already excludes INVALID / SYNC_FAILED rows, so the service
        // sees nothing to invalidate and simply writes the next version.
        UUID userUuid = UUID.randomUUID();

        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid); // empty — no PENDING_PUSH/SYNCED rows
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(2);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // only the new PENDING_PUSH row is persisted, version = max + 1 = 3
        verify(userAccountExternalMapRepository, times(1)).save(any(UserAccountExternalMap.class));
        verify(userAccountExternalMapRepository).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(3).equals(m.getVersion())));
    }

    @Test
    void saveForUser_invalidatesBothPendingPushAndSyncedRows() {
        UUID userUuid = UUID.randomUUID();
        UserAccountExternalMap pending = mapRow(userUuid, "PENDING_PUSH", 2, null, null);
        UserAccountExternalMap synced = mapRow(userUuid, "SYNCED", 1, EXT_EMP_ID, EXT_ACC_ID);

        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid, pending, synced);
        stubCarryForward(userUuid, SERVICE_ID, synced);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(2);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // both prior rows invalidated, plus one new row → 3 saves total
        verify(userAccountExternalMapRepository, times(3)).save(any(UserAccountExternalMap.class));
        assertEquals("INVALID", pending.getSyncStatus());
        assertEquals("INVALID", synced.getSyncStatus());

        // external ids are carried forward from the previously SYNCED row
        verify(userAccountExternalMapRepository).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(3).equals(m.getVersion())
                        && EXT_EMP_ID.equals(m.getExternalEmployeeId())
                        && EXT_ACC_ID.equals(m.getExternalAccountId())));
    }

    @Test
    void saveForUser_skipsCustomerOnlyAccount() {
        UUID userUuid = UUID.randomUUID();
        when(userRoleMapRepository.findRoleByUserId(any())).thenReturn(List.of("customer"));

        service.saveForUser(userWithUuid(userUuid));

        // customer-only accounts use customer_external_map, never the employee map
        verify(tenantRepository, never()).findTenant();
        verify(userAccountExternalMapRepository, never()).findActiveByUserAccountUuid(any());
        verify(userAccountExternalMapRepository, never()).save(any(UserAccountExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForUser_skipsWhenNoTenantResolved() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        when(tenantRepository.findTenant()).thenReturn(null);

        service.saveForUser(userWithUuid(userUuid));

        verify(userAccountExternalMapRepository, never()).findActiveByUserAccountUuid(any());
        verify(userAccountExternalMapRepository, never()).save(any(UserAccountExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForUser_isNoOpForNullUser() {
        service.saveForUser(null);

        verify(userAccountExternalMapRepository, never()).save(any(UserAccountExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForUser_skipsWhenObjectIdIsBlank() {
        UserAccount user = new UserAccount();
        user.setObjectId("   ");

        service.saveForUser(user);

        // a partial-create user with a null/blank Azure OID is skipped cleanly, before any lookup
        verify(userRoleMapRepository, never()).findRoleByUserId(any());
        verify(userAccountExternalMapRepository, never()).save(any(UserAccountExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForUser_isNonBlocking_whenNoExternalServiceConfigured() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of());

        service.saveForUser(userWithUuid(userUuid));

        // no service id → nothing read, nothing written, employee flow not impacted
        verify(userAccountExternalMapRepository, never()).findActiveByUserAccountUuid(any());
        verify(userAccountExternalMapRepository, never()).save(any(UserAccountExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForUser_isNonBlocking_whenServiceIdsNull() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(null);

        service.saveForUser(userWithUuid(userUuid));

        verify(userAccountExternalMapRepository, never()).save(any(UserAccountExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForUser_swallowsServiceMasterException_andDoesNotSave() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString()))
                .thenThrow(new RuntimeException("service_master lookup failed"));

        // must not propagate — external map is best-effort and never fails the caller
        service.saveForUser(userWithUuid(userUuid));

        verify(userAccountExternalMapRepository, never()).save(any(UserAccountExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForUser_createsFirstVersionForNewUser_withNoExistingMaps() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid); // brand new user, nothing active
        // no carry-forward row stubbed → repository returns null
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // brand new user → version starts at 1, PENDING_PUSH, no external ids to carry forward
        verify(userAccountExternalMapRepository, times(1)).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(1).equals(m.getVersion())
                        && m.getExternalEmployeeId() == null
                        && m.getExternalAccountId() == null
                        && SERVICE_ID == m.getServiceId()
                        && TENANT_ID.equals(m.getTenantId())
                        && userUuid.equals(m.getUserAccountUuid())
                        && FAR_FUTURE.equals(m.getVersionExpiryDate())
                        && FAR_FUTURE.equals(m.getEndorsementExpiryDate())
                        // first version → "invited / inactive, sync deferred" reason, in {"message":"..."} JSON
                        && ("{\"message\":\"Ready to sync once the employee becomes active by completing "
                                + "registration or updating their details.\"}").equals(m.getReason())));
    }

    @Test
    void saveForUser_handlesNullActiveMapsList() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        when(userAccountExternalMapRepository.findActiveByUserAccountUuid(userUuid)).thenReturn(null);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // a null active list must not NPE; still creates the first PENDING_PUSH row
        verify(userAccountExternalMapRepository, times(1)).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(1).equals(m.getVersion())));
    }

    @Test
    void saveForUser_withInvalidStatus_invalidatesPriorAndWritesInvalidRow() {
        UUID userUuid = UUID.randomUUID();
        UserAccountExternalMap synced = mapRow(userUuid, "SYNCED", 1, EXT_EMP_ID, EXT_ACC_ID);
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid, synced);
        stubCarryForward(userUuid, SERVICE_ID, synced);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(1);
        echoSaves();

        // cancel-hiring path
        service.saveForUser(userWithUuid(userUuid), "INVALID");

        // prior SYNCED row closed out
        assertEquals("INVALID", synced.getSyncStatus());
        // new row is written with INVALID status, version bumped, ids still carried forward
        verify(userAccountExternalMapRepository).save(argThat(m ->
                m != synced
                        && "INVALID".equals(m.getSyncStatus())
                        && Integer.valueOf(2).equals(m.getVersion())
                        && EXT_EMP_ID.equals(m.getExternalEmployeeId())
                        && EXT_ACC_ID.equals(m.getExternalAccountId())
                        // cancel-hiring → "sync entry closed" reason
                        && "{\"message\":\"Employee hiring cancelled, so the QuickBooks sync entry has been closed.\"}"
                                .equals(m.getReason())));
        verify(userAccountExternalMapRepository, times(2)).save(any(UserAccountExternalMap.class));
    }

    @Test
    void saveForUser_createsOneRowPerExternalService_andAuditsEach() {
        long serviceId2 = 7L;
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString()))
                .thenReturn(List.of(SERVICE_ID, serviceId2));
        stubActive(userUuid); // new user
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(eq(userUuid), anyLong()))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // one fresh row per configured Data Exchange service, each audited
        verify(userAccountExternalMapRepository).save(argThat(m -> SERVICE_ID == m.getServiceId()));
        verify(userAccountExternalMapRepository).save(argThat(m -> serviceId2 == m.getServiceId()));
        verify(userAccountExternalMapRepository, times(2)).save(any(UserAccountExternalMap.class));
        verify(auditLogService, times(2))
                .saveAuditLog(any());
    }

    @Test
    void saveForUser_doesNotCarryForwardIdsFromADifferentService() {
        long serviceId2 = 7L;
        UUID userUuid = UUID.randomUUID();
        // A SYNCED row exists for SERVICE_ID, but the only configured service now is serviceId2.
        UserAccountExternalMap syncedOnOtherService =
                mapRow(userUuid, "SYNCED", 3, EXT_EMP_ID, EXT_ACC_ID, SERVICE_ID);
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(serviceId2));
        stubActive(userUuid, syncedOnOtherService); // invalidation is not service-scoped
        // carry-forward lookup is per-service: serviceId2 has no row with external ids → null
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, serviceId2))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // the SYNCED row (any service) is still closed out
        assertEquals("INVALID", syncedOnOtherService.getSyncStatus());
        // but the new serviceId2 row must NOT inherit serviceId's external ids
        verify(userAccountExternalMapRepository).save(argThat(m ->
                serviceId2 == m.getServiceId()
                        && "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(1).equals(m.getVersion())
                        && m.getExternalEmployeeId() == null
                        && m.getExternalAccountId() == null));
        verify(userAccountExternalMapRepository, times(2)).save(any(UserAccountExternalMap.class));
    }

    @Test
    void saveForUser_carriesForwardIdsFromInvalidRow_whenNoActiveRowsRemain() {
        // After cancel-hiring there are no active rows, but the QBO ids still live on an
        // INVALID row. The carry-forward query (any status) must still surface them.
        UUID userUuid = UUID.randomUUID();
        UserAccountExternalMap invalidWithIds = mapRow(userUuid, "INVALID", 2, EXT_EMP_ID, EXT_ACC_ID);
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid); // nothing active (everything already INVALID)
        stubCarryForward(userUuid, SERVICE_ID, invalidWithIds);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(2);
        echoSaves();

        // reactivate path
        service.saveForUser(userWithUuid(userUuid), "PENDING_PUSH");

        // only the new row is written (no active rows to invalidate), ids carried from the INVALID row
        verify(userAccountExternalMapRepository, times(1)).save(any(UserAccountExternalMap.class));
        verify(userAccountExternalMapRepository).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(3).equals(m.getVersion())
                        && EXT_EMP_ID.equals(m.getExternalEmployeeId())
                        && EXT_ACC_ID.equals(m.getExternalAccountId())));
    }

    @Test
    void saveForUser_writesSuccessAuditLogForCreatedRow() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(userWithUuid(userUuid));

        // an audit trail entry is recorded for the QBO sync row
        verify(auditLogService, times(1))
                .saveAuditLog(any());
    }

    @Test
    void saveForUser_setsVendorEntityTypeFor1099Worker() {
        UUID userUuid = UUID.randomUUID();
        UserAccount user = userWithUuid(userUuid);
        user.setId(42);
        UserStatus status = new UserStatus();
        status.setType("1099");
        when(userStatusRepository.findByUserId(42)).thenReturn(status);
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(user);

        verify(userAccountExternalMapRepository).save(argThat(m -> "VENDOR".equals(m.getExternalEntityType())));
    }

    @Test
    void saveForUser_setsEmployeeEntityTypeForW2Worker() {
        UUID userUuid = UUID.randomUUID();
        UserAccount user = userWithUuid(userUuid);
        user.setId(43);
        UserStatus status = new UserStatus();
        status.setType("W2");
        when(userStatusRepository.findByUserId(43)).thenReturn(status);
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(user);

        verify(userAccountExternalMapRepository).save(argThat(m -> "EMPLOYEE".equals(m.getExternalEntityType())));
    }

    @Test
    void saveForUser_usesCallerSuppliedEntityType_withoutResolvingFromUserStatus() {
        // Cancel-hiring supplies the type it resolved BEFORE invalidating UserStatus; the service
        // must honour it verbatim and must NOT re-resolve (findByUserId would now return null and
        // wrongly fall back to EMPLOYEE for a 1099 worker).
        UUID userUuid = UUID.randomUUID();
        UserAccount user = userWithUuid(userUuid);
        user.setId(77);
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForUser(user, "INVALID", "VENDOR");

        verify(userAccountExternalMapRepository).save(argThat(m ->
                "VENDOR".equals(m.getExternalEntityType())
                        && "INVALID".equals(m.getSyncStatus())));
        // the pre-resolved type is used as-is; no UserStatus lookup happens on this path
        verify(userStatusRepository, never()).findByUserId(any());
    }

    @Test
    void resolveExternalEntityType_returnsEmployeeForNullUser() {
        assertEquals("EMPLOYEE", service.resolveExternalEntityType(null));
    }

    @Test
    void saveForUser_defaultOverloadUsesPendingPushStatus() {
        UUID userUuid = UUID.randomUUID();
        stubNonCustomer();
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubActive(userUuid);
        when(userAccountExternalMapRepository.findMaxVersionByUserAccountUuidAndServiceId(userUuid, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        // single-arg overload must default the new row to PENDING_PUSH
        service.saveForUser(userWithUuid(userUuid));

        verify(userAccountExternalMapRepository).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())));
    }
}

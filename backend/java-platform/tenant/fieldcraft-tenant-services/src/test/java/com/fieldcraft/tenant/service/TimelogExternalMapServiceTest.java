package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.TimelogExternalMapRepository;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.domain.TimelogExternalMap;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.concurrent.CompletableFuture;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.argThat;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TimelogExternalMapServiceTest {

    private static final long SERVICE_ID = 6L;
    private static final long TIMELOG_ID = 100L;
    private static final String TENANT_ID = "tn59395620";
    private static final String EXT_TIME_ID = "TA-400";
    private static final String EXT_ACC_ID = "ACC-900";
    private static final String DISPLAY = "John D - Roofing";

    @Mock
    private TimelogExternalMapRepository timelogExternalMapRepository;
    @Mock
    private ServiceMasterRepository serviceMasterRepository;
    @Mock
    private AuditLogService auditLogService;
    @Mock
    private TenantRepository tenantRepository;

    private TimelogExternalMapService service;

    @BeforeEach
    void setUp() {
        service = new TimelogExternalMapService(
                timelogExternalMapRepository, serviceMasterRepository, auditLogService, tenantRepository);
    }

    // ── stub helpers (each test stubs only what its code path exercises, for strict stubs) ──

    private void stubTenant() {
        Tenant tenant = mock(Tenant.class);
        when(tenant.getTenantid()).thenReturn(TENANT_ID);
        when(tenantRepository.findTenant()).thenReturn(tenant);
    }

    private void echoSaves() {
        when(timelogExternalMapRepository.save(any(TimelogExternalMap.class)))
                .thenAnswer(inv -> inv.getArgument(0));
    }

    private void stubOpen(TimelogExternalMap... rows) {
        when(timelogExternalMapRepository.findOpenByTimelogId(TIMELOG_ID)).thenReturn(List.of(rows));
    }

    private void stubCarryForward(long serviceId, TimelogExternalMap row) {
        when(timelogExternalMapRepository
                .findFirstByTimelogIdAndServiceIdAndExternalTimeActivityIdIsNotNullOrderByVersionDesc(TIMELOG_ID, serviceId))
                .thenReturn(row);
    }

    private TimelogExternalMap row(String syncStatus, int version, String extTimeId, String extAccId) {
        TimelogExternalMap map = new TimelogExternalMap();
        map.setTimelogId(TIMELOG_ID);
        map.setServiceId(SERVICE_ID);
        map.setSyncStatus(syncStatus);
        map.setVersion(version);
        map.setExternalTimeActivityId(extTimeId);
        map.setExternalAccountId(extAccId);
        map.setExternalDisplayName(DISPLAY);
        return map;
    }

    @Test
    void saveForTimelog_createsFirstVersion_forNewTimelog() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen(); // nothing open yet
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "completed");

        // first version → PENDING_PUSH, version 1, open (expiry NULL), no external ids carried, reason kept
        verify(timelogExternalMapRepository, times(1)).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(1).equals(m.getVersion())
                        && TIMELOG_ID == m.getTimelogId()
                        && SERVICE_ID == m.getServiceId()
                        && TENANT_ID.equals(m.getTenantId())
                        && m.getExternalTimeActivityId() == null
                        && m.getVersionEffectiveDate() != null
                        && m.getEndorsementEffectiveDate() != null
                        && m.getVersionExpiryDate() == null
                        && m.getEndorsementExpiryDate() == null
                        && "{\"message\":\"Task completed, ready to sync to QuickBooks\"}"
                                .equals(m.getReason())));
        verify(auditLogService, times(1))
                .saveAuditLog(any());
    }

    @Test
    void saveForTimelog_closesOpenRowThenCreatesNewVersion_carryingExternalIds() {
        TimelogExternalMap open = row("SYNCED", 1, EXT_TIME_ID, EXT_ACC_ID);
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen(open);
        stubCarryForward(SERVICE_ID, open);
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(1);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "Approve");

        // prior open row is closed: INVALID + endorsement window stamped (frees the partial unique index)
        assertEquals("INVALID", open.getSyncStatus());
        assertNotNull(open.getEndorsementExpiryDate());
        assertNotNull(open.getVersionExpiryDate());

        // new open version: PENDING_PUSH, version 2, ids carried forward, endorsement expiry still NULL
        verify(timelogExternalMapRepository).save(argThat(m ->
                m != open
                        && "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(2).equals(m.getVersion())
                        && EXT_TIME_ID.equals(m.getExternalTimeActivityId())
                        && EXT_ACC_ID.equals(m.getExternalAccountId())
                        && DISPLAY.equals(m.getExternalDisplayName())
                        && m.getEndorsementExpiryDate() == null
                        && "{\"message\":\"Time log ready to sync to QuickBooks\"}"
                                .equals(m.getReason())));
        verify(timelogExternalMapRepository, times(2)).save(any(TimelogExternalMap.class));
    }

    @Test
    void saveForTimelog_carriesForwardFromClosedRow_whenNoOpenRowsRemain() {
        // e.g. re-approved after the entry was already closed: no open rows, ids live on a closed
        // row. Uses "Approve" (not "Reject") deliberately -- this test is about the carry-forward
        // mechanism, not about which reason resolves to which sync_status (that's covered by the
        // dedicated saveForTimelog_reason*_resolvesTo* tests below), and "Reject" now resolves to
        // a different sync_status/message than what this test asserts.
        TimelogExternalMap closed = row("INVALID", 2, EXT_TIME_ID, EXT_ACC_ID);
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen(); // nothing open
        stubCarryForward(SERVICE_ID, closed);
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(2);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "Approve");

        verify(timelogExternalMapRepository, times(1)).save(any(TimelogExternalMap.class));
        verify(timelogExternalMapRepository).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus())
                        && Integer.valueOf(3).equals(m.getVersion())
                        && EXT_TIME_ID.equals(m.getExternalTimeActivityId())
                        && "{\"message\":\"Time log ready to sync to QuickBooks\"}"
                                .equals(m.getReason())));
    }

    @Test
    void saveForTimelog_createsOneRowPerService_andAuditsEach() {
        long serviceId2 = 7L;
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID, serviceId2));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(eq(TIMELOG_ID), anyLong()))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(timelogExternalMapRepository).save(argThat(m -> SERVICE_ID == m.getServiceId()));
        verify(timelogExternalMapRepository).save(argThat(m -> serviceId2 == m.getServiceId()));
        verify(timelogExternalMapRepository, times(2)).save(any(TimelogExternalMap.class));
        verify(auditLogService, times(2))
                .saveAuditLog(any());
    }

    @Test
    void saveForTimelog_defaultOverload_usesPendingPushAndNullReason() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID);

        verify(timelogExternalMapRepository).save(argThat(m ->
                "PENDING_PUSH".equals(m.getSyncStatus()) && m.getReason() == null));
    }

    // ── sync_status resolution via the explicit 3-arg overload (Timelog 2.0 approval-state ->
    //    QBO sync mapping). Deliberately NOT inferred from `reason` -- see resolveSyncStatus's
    //    javadoc: `reason` is not a closed vocabulary (a legacy caller forwards an external,
    //    path-variable string through it), so only an explicit override can produce a
    //    non-default status. ──

    @Test
    void saveForTimelog_explicitStatusRejected_resolvesToRejectedSyncStatus() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "Reject", "REJECTED");

        verify(timelogExternalMapRepository).save(argThat(m -> "REJECTED".equals(m.getSyncStatus())));
    }

    @Test
    void saveForTimelog_explicitStatusAwaitingApproval_resolvesToAwaitingApprovalSyncStatus() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "Submit", "AWAITING_APPROVAL");

        verify(timelogExternalMapRepository).save(argThat(m -> "AWAITING_APPROVAL".equals(m.getSyncStatus())));
    }

    @Test
    void saveForTimelog_noExplicitStatus_stillResolvesToPendingPush() {
        // Regression: the pre-existing approve/override/on-behalf/auto-approval call sites all
        // use the 2-arg overload (no explicit status) and must keep getting PENDING_PUSH,
        // unchanged by this slice.
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "Approve");

        verify(timelogExternalMapRepository).save(argThat(m -> "PENDING_PUSH".equals(m.getSyncStatus())));
    }

    @Test
    void saveForTimelog_reasonCompleted_stillResolvesToPendingPush() {
        // Regression: the GoPAL task-completion call site passes "completed" and must keep
        // getting PENDING_PUSH, unchanged by this slice.
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(timelogExternalMapRepository).save(argThat(m -> "PENDING_PUSH".equals(m.getSyncStatus())));
    }

    // Security/blast-radius regression (Phase-5 review finding): the legacy
    // TimelogService.updateApproveReject path forwards an external, caller-controlled `status`
    // string straight through as `reason` via the 2-arg overload -- it must NEVER be able to
    // produce REJECTED/AWAITING_APPROVAL just by choosing that string, since it never supplies
    // an explicit override.
    @Test
    void saveForTimelog_twoArgOverload_neverProducesNonDefaultStatus_regardlessOfReasonText() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        // The legacy caller can pass ANY string here (it's an external path-variable value) --
        // even the literal target status names themselves -- and must still only ever get
        // PENDING_PUSH via this 2-arg overload.
        service.saveForTimelog(TIMELOG_ID, "Reject");

        verify(timelogExternalMapRepository).save(argThat(m -> "PENDING_PUSH".equals(m.getSyncStatus())));
    }

    @Test
    void saveForTimelog_unrecognizedReason_defaultsToPendingPush() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "some-unrelated-trigger");

        verify(timelogExternalMapRepository).save(argThat(m -> "PENDING_PUSH".equals(m.getSyncStatus())));
    }

    @Test
    void saveForTimelog_skipsWhenNoTenantResolved() {
        when(tenantRepository.findTenant()).thenReturn(null);

        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(timelogExternalMapRepository, never()).findOpenByTimelogId(any());
        verify(timelogExternalMapRepository, never()).save(any(TimelogExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForTimelog_isNonBlocking_whenNoExternalServiceConfigured() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of());

        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(timelogExternalMapRepository, never()).findOpenByTimelogId(any());
        verify(timelogExternalMapRepository, never()).save(any(TimelogExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForTimelog_swallowsServiceMasterException_andDoesNotSave() {
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString()))
                .thenThrow(new RuntimeException("service_master lookup failed"));

        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(timelogExternalMapRepository, never()).save(any(TimelogExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForTimelog_isNoOpForNullTimelogId() {
        service.saveForTimelog(null, "completed");

        verify(timelogExternalMapRepository, never()).save(any(TimelogExternalMap.class));
        verifyNoInteractions(auditLogService);
    }

    @Test
    void saveForTimelog_swallowsPersistenceException_andDoesNotPropagate() {
        // a failure mid-write (after the guards) must be caught by the outer try/catch — never thrown
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen(); // nothing open
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        when(timelogExternalMapRepository.save(any(TimelogExternalMap.class)))
                .thenThrow(new RuntimeException("db write failed"));

        // must not throw
        service.saveForTimelog(TIMELOG_ID, "completed");

        verifyNoInteractions(auditLogService);
    }

    // ── QBO TimeActivity push (completion-only, after-commit) ──

    @Test
    void saveForTimelog_pushesToQuickBooks_onCompletion() {
        // url + mocked http client so the (no-tx → direct) push is observable
        service.externalServiceUrl = "http://localhost:5001";
        HttpClient mockHttpClient = mock(HttpClient.class);
        service.httpClient = mockHttpClient;
        @SuppressWarnings("unchecked")
        HttpResponse<String> response = mock(HttpResponse.class);
        when(response.statusCode()).thenReturn(200);
        doReturn(CompletableFuture.completedFuture(response)).when(mockHttpClient).sendAsync(any(), any());

        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "completed");

        ArgumentCaptor<HttpRequest> captor = ArgumentCaptor.forClass(HttpRequest.class);
        verify(mockHttpClient).sendAsync(captor.capture(), any());
        assertTrue(captor.getValue().uri().toString().endsWith("/extsvc/api/v1/quickbooks/timelogs/create"));
    }

    @Test
    void saveForTimelog_doesNotPushToQuickBooks_onApprove() {
        // url IS set, so the ONLY thing preventing a push is the completion-only gate
        service.externalServiceUrl = "http://localhost:5001";
        HttpClient mockHttpClient = mock(HttpClient.class);
        service.httpClient = mockHttpClient;

        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "Approve");

        verify(mockHttpClient, never()).sendAsync(any(), any());
    }

    @Test
    void saveForTimelog_skipsPush_whenExternalServiceUrlBlank() {
        service.externalServiceUrl = "";   // blank → trigger returns before any HTTP
        HttpClient mockHttpClient = mock(HttpClient.class);
        service.httpClient = mockHttpClient;

        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(mockHttpClient, never()).sendAsync(any(), any());
    }

    @Test
    void saveForTimelog_continues_whenQuickBooksReturnsNon200() {
        service.externalServiceUrl = "http://localhost:5001";
        HttpClient mockHttpClient = mock(HttpClient.class);
        service.httpClient = mockHttpClient;
        @SuppressWarnings("unchecked")
        HttpResponse<String> response = mock(HttpResponse.class);
        when(response.statusCode()).thenReturn(500);
        doReturn(CompletableFuture.completedFuture(response)).when(mockHttpClient).sendAsync(any(), any());

        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        // a non-200 from QBO is logged, never thrown
        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(mockHttpClient).sendAsync(any(), any());
    }

    @Test
    void saveForTimelog_swallowsAsyncFailure_fromQuickBooksCall() {
        service.externalServiceUrl = "http://localhost:5001";
        HttpClient mockHttpClient = mock(HttpClient.class);
        service.httpClient = mockHttpClient;
        CompletableFuture<HttpResponse<String>> failed = new CompletableFuture<>();
        failed.completeExceptionally(new RuntimeException("connection refused"));
        doReturn(failed).when(mockHttpClient).sendAsync(any(), any());

        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        // an async (network) failure must not propagate to the caller
        service.saveForTimelog(TIMELOG_ID, "completed");

        verify(mockHttpClient).sendAsync(any(), any());
    }

    @Test
    void saveForTimelog_defersPushUntilAfterCommit_whenTransactionActive() {
        service.externalServiceUrl = "http://localhost:5001";
        HttpClient mockHttpClient = mock(HttpClient.class);
        service.httpClient = mockHttpClient;
        @SuppressWarnings("unchecked")
        HttpResponse<String> response = mock(HttpResponse.class);
        when(response.statusCode()).thenReturn(200);
        doReturn(CompletableFuture.completedFuture(response)).when(mockHttpClient).sendAsync(any(), any());

        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        TransactionSynchronizationManager.initSynchronization();
        try {
            service.saveForTimelog(TIMELOG_ID, "completed");
            // with an active transaction the push is deferred, not fired inline
            verify(mockHttpClient, never()).sendAsync(any(), any());

            // simulate the commit → the registered afterCommit fires the push
            TransactionSynchronizationManager.getSynchronizations()
                    .forEach(TransactionSynchronization::afterCommit);
            verify(mockHttpClient).sendAsync(any(), any());
        } finally {
            TransactionSynchronizationManager.clearSynchronization();
        }
    }

    @Test
    void saveForTimelog_usesGenericReasonMessage_forUnrecognizedTrigger() {
        // Any non-null trigger that isn't completed/approve/reject falls back to the generic message.
        stubTenant();
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(List.of(SERVICE_ID));
        stubOpen();
        when(timelogExternalMapRepository.findMaxVersionByTimelogIdAndServiceId(TIMELOG_ID, SERVICE_ID))
                .thenReturn(null);
        echoSaves();

        service.saveForTimelog(TIMELOG_ID, "Something else");

        verify(timelogExternalMapRepository).save(argThat(m ->
                "{\"message\":\"Time log ready to sync to QuickBooks\"}".equals(m.getReason())));
    }
}

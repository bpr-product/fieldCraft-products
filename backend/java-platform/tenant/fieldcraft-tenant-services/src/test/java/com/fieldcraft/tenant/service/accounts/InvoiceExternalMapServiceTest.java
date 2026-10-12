package com.fieldcraft.tenant.service.accounts;

import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.accounts.InvoiceExternalMapRepository;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.domain.accounts.InvoiceExternalMap;
import com.fieldcraft.tenant.service.quickbooks.QuickBooksSyncService;
import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpServer;
import org.json.JSONObject;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_AWAITING_APPROVAL;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_INVALID;
import static com.fieldcraft.tenant.data.constant.DomainConstant.SYNC_STATUS_PENDING_PUSH;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class InvoiceExternalMapServiceTest {

    private static final long SERVICE_ID = 6L;
    private static final long SERVICE_ID_2 = 7L;
    private static final long INVOICE_ID = 100L;
    private static final long NEW_INVOICE_ID = 101L;
    private static final String TENANT_ID = "tn59395620";
    private static final String EXT_INVOICE_ID = "QB-123";
    private static final String EXT_ACC_ID = "ACC-900";

    @Mock
    private InvoiceExternalMapRepository invoiceExternalMapRepository;
    @Mock
    private ServiceMasterRepository serviceMasterRepository;
    @Mock
    private TenantRepository tenantRepository;

    private InvoiceExternalMapService service;
    private QuickBooksSyncService quickBooksSyncService;

    @BeforeEach
    void setUp() {
        quickBooksSyncService = new QuickBooksSyncService();
        service = new InvoiceExternalMapService(invoiceExternalMapRepository, serviceMasterRepository, tenantRepository,
                quickBooksSyncService);
    }

    // ── stub helpers ──

    private void stubTenant() {
        Tenant tenant = mock(Tenant.class);
        when(tenant.getTenantid()).thenReturn(TENANT_ID);
        when(tenantRepository.findTenant()).thenReturn(tenant);
    }

    private void stubServices(Long... serviceIds) {
        when(serviceMasterRepository.getIdsByServiceType("Data Exchange")).thenReturn(List.of(serviceIds));
    }

    private void echoSaves() {
        when(invoiceExternalMapRepository.save(any(InvoiceExternalMap.class)))
                .thenAnswer(inv -> inv.getArgument(0));
    }

    private InvoiceExternalMap row(String syncStatus, int version, String extInvoiceId, String extAccId) {
        InvoiceExternalMap map = new InvoiceExternalMap();
        map.setSyncStatus(syncStatus);
        map.setVersion(version);
        map.setExternalInvoiceId(extInvoiceId);
        map.setExternalAccountId(extAccId);
        return map;
    }

    // ── createForNewInvoice ──

    @Test
    void createForNewInvoice_singleActiveService_insertsAwaitingApprovalRow() {
        stubTenant();
        stubServices(SERVICE_ID);
        echoSaves();

        service.createForNewInvoice(INVOICE_ID);

        ArgumentCaptor<InvoiceExternalMap> captor = ArgumentCaptor.forClass(InvoiceExternalMap.class);
        verify(invoiceExternalMapRepository).save(captor.capture());
        InvoiceExternalMap saved = captor.getValue();
        assertEquals(INVOICE_ID, saved.getInvoiceId());
        assertEquals(TENANT_ID, saved.getTenantId());
        assertEquals(SERVICE_ID, saved.getServiceId());
        assertEquals(SYNC_STATUS_AWAITING_APPROVAL, saved.getSyncStatus());
        assertEquals(1, saved.getVersion());
        assertNull(saved.getEndorsementExpiryDate());
        assertNotNull(saved.getReason());
        assertTrue(saved.getReason().contains("awaiting approval"));
    }

    @Test
    void createForNewInvoice_multipleActiveServices_insertsOneRowPerService() {
        stubTenant();
        stubServices(SERVICE_ID, SERVICE_ID_2);
        echoSaves();

        service.createForNewInvoice(INVOICE_ID);

        verify(invoiceExternalMapRepository, times(2)).save(any(InvoiceExternalMap.class));
    }

    @Test
    void createForNewInvoice_nullInvoiceId_noOp() {
        service.createForNewInvoice(null);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void createForNewInvoice_noActiveService_noOp() {
        stubServices();
        service.createForNewInvoice(INVOICE_ID);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void createForNewInvoice_serviceMasterReturnsNullList_noOp() {
        when(serviceMasterRepository.getIdsByServiceType("Data Exchange")).thenReturn(null);
        service.createForNewInvoice(INVOICE_ID);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void createForNewInvoice_noTenant_noOp() {
        stubServices(SERVICE_ID);
        when(tenantRepository.findTenant()).thenReturn(null);
        service.createForNewInvoice(INVOICE_ID);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void createForNewInvoice_serviceMasterLookupThrows_noOpDoesNotThrow() {
        when(serviceMasterRepository.getIdsByServiceType("Data Exchange")).thenThrow(new RuntimeException("db down"));
        service.createForNewInvoice(INVOICE_ID);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void createForNewInvoice_repositorySaveThrows_doesNotPropagate() {
        stubTenant();
        stubServices(SERVICE_ID);
        when(invoiceExternalMapRepository.save(any(InvoiceExternalMap.class))).thenThrow(new RuntimeException("boom"));

        service.createForNewInvoice(INVOICE_ID);
        // no assertion needed beyond "did not throw" -- reaching here proves the catch worked
    }

    // ── reversionOnEdit ──

    @Test
    void reversionOnEdit_closesOldRowAndOpensNewRow_carriesForwardExternalIds() {
        stubTenant();
        stubServices(SERVICE_ID);
        echoSaves();
        InvoiceExternalMap openOld = row(SYNC_STATUS_PENDING_PUSH, 1, EXT_INVOICE_ID, EXT_ACC_ID);
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of(openOld));
        when(invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(INVOICE_ID, SERVICE_ID))
                .thenReturn(row(SYNC_STATUS_PENDING_PUSH, 1, EXT_INVOICE_ID, EXT_ACC_ID));
        when(invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(INVOICE_ID, SERVICE_ID)).thenReturn(1);

        service.reversionOnEdit(INVOICE_ID, NEW_INVOICE_ID);

        assertEquals(SYNC_STATUS_INVALID, openOld.getSyncStatus());
        // Soft-delete convention: a retired row must be marked invalid=true, not just
        // syncStatus=INVALID, so `WHERE invalid = false` filters (and Debezium CDC) see it
        // as retired.
        assertEquals(Boolean.TRUE, openOld.getInvalid());
        assertNotNull(openOld.getReason());
        assertTrue(openOld.getReason().contains("replaced by a newer version"));

        ArgumentCaptor<InvoiceExternalMap> captor = ArgumentCaptor.forClass(InvoiceExternalMap.class);
        verify(invoiceExternalMapRepository, times(2)).save(captor.capture());
        InvoiceExternalMap newRow = captor.getAllValues().get(1);
        assertEquals(NEW_INVOICE_ID, newRow.getInvoiceId());
        assertEquals(Boolean.FALSE, newRow.getInvalid());
        assertEquals(SYNC_STATUS_AWAITING_APPROVAL, newRow.getSyncStatus());
        assertNotNull(newRow.getReason());
        assertTrue(newRow.getReason().contains("awaiting approval"));
        assertEquals(2, newRow.getVersion());
        assertEquals(EXT_INVOICE_ID, newRow.getExternalInvoiceId());
        assertEquals(EXT_ACC_ID, newRow.getExternalAccountId());
        assertNull(newRow.getEndorsementExpiryDate());
    }

    @Test
    void reversionOnEdit_noExternalIdOnOldRow_newRowCarriesNulls() {
        stubTenant();
        stubServices(SERVICE_ID);
        echoSaves();
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of());
        when(invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(INVOICE_ID, SERVICE_ID))
                .thenReturn(null);
        when(invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(INVOICE_ID, SERVICE_ID)).thenReturn(0);

        service.reversionOnEdit(INVOICE_ID, NEW_INVOICE_ID);

        ArgumentCaptor<InvoiceExternalMap> captor = ArgumentCaptor.forClass(InvoiceExternalMap.class);
        verify(invoiceExternalMapRepository).save(captor.capture());
        InvoiceExternalMap newRow = captor.getValue();
        assertNull(newRow.getExternalInvoiceId());
        assertNull(newRow.getExternalAccountId());
        assertEquals(1, newRow.getVersion());
    }

    @Test
    void reversionOnEdit_nullMaxVersion_startsAtVersionOne() {
        stubTenant();
        stubServices(SERVICE_ID);
        echoSaves();
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of());
        when(invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(INVOICE_ID, SERVICE_ID))
                .thenReturn(null);
        when(invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(INVOICE_ID, SERVICE_ID)).thenReturn(null);

        service.reversionOnEdit(INVOICE_ID, NEW_INVOICE_ID);

        ArgumentCaptor<InvoiceExternalMap> captor = ArgumentCaptor.forClass(InvoiceExternalMap.class);
        verify(invoiceExternalMapRepository).save(captor.capture());
        assertEquals(1, captor.getValue().getVersion());
    }

    @Test
    void reversionOnEdit_multipleActiveServices_insertsOneRowPerService() {
        stubTenant();
        stubServices(SERVICE_ID, SERVICE_ID_2);
        echoSaves();
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of());
        when(invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(anyLong(), anyLong()))
                .thenReturn(null);
        when(invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(anyLong(), anyLong())).thenReturn(0);

        service.reversionOnEdit(INVOICE_ID, NEW_INVOICE_ID);

        verify(invoiceExternalMapRepository, times(2)).save(any(InvoiceExternalMap.class));
    }

    @Test
    void reversionOnEdit_nullIds_noOp() {
        service.reversionOnEdit(null, NEW_INVOICE_ID);
        service.reversionOnEdit(INVOICE_ID, null);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void reversionOnEdit_noActiveService_noOp() {
        stubServices();
        service.reversionOnEdit(INVOICE_ID, NEW_INVOICE_ID);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void reversionOnEdit_noTenant_noOp() {
        stubServices(SERVICE_ID);
        when(tenantRepository.findTenant()).thenReturn(null);
        service.reversionOnEdit(INVOICE_ID, NEW_INVOICE_ID);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void reversionOnEdit_repositoryThrows_doesNotPropagate() {
        stubTenant();
        stubServices(SERVICE_ID);
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenThrow(new RuntimeException("boom"));

        service.reversionOnEdit(INVOICE_ID, NEW_INVOICE_ID);
        // no assertion needed beyond "did not throw" -- reaching here proves the catch worked
    }

    // ── markPendingPush ──

    @Test
    void markPendingPush_awaitingApprovalRow_closesOldRowAndInsertsNewPendingPushVersion() {
        InvoiceExternalMap open = row(SYNC_STATUS_AWAITING_APPROVAL, 1, EXT_INVOICE_ID, EXT_ACC_ID);
        open.setServiceId(SERVICE_ID);
        open.setTenantId(TENANT_ID);
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of(open));
        when(invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(INVOICE_ID, SERVICE_ID))
                .thenReturn(row(SYNC_STATUS_AWAITING_APPROVAL, 1, EXT_INVOICE_ID, EXT_ACC_ID));
        when(invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(INVOICE_ID, SERVICE_ID)).thenReturn(1);
        echoSaves();

        service.markPendingPush(INVOICE_ID);

        // Old row is retired AND stamped INVALID -- a superseded version must never keep
        // advertising a live sync status like AWAITING_APPROVAL.
        assertEquals(SYNC_STATUS_INVALID, open.getSyncStatus());
        assertEquals(Boolean.TRUE, open.getInvalid());
        assertNotNull(open.getEndorsementExpiryDate());
        assertTrue(open.getReason().contains("replaced by a newer version"));

        ArgumentCaptor<InvoiceExternalMap> captor = ArgumentCaptor.forClass(InvoiceExternalMap.class);
        verify(invoiceExternalMapRepository, times(2)).save(captor.capture());
        InvoiceExternalMap newRow = captor.getAllValues().get(1);
        assertEquals(INVOICE_ID, newRow.getInvoiceId());
        assertEquals(SYNC_STATUS_PENDING_PUSH, newRow.getSyncStatus());
        assertEquals(2, newRow.getVersion());
        assertEquals(EXT_INVOICE_ID, newRow.getExternalInvoiceId());
        assertEquals(EXT_ACC_ID, newRow.getExternalAccountId());
        assertNull(newRow.getEndorsementExpiryDate());
        assertNotNull(newRow.getReason());
        assertTrue(newRow.getReason().contains("queued for QuickBooks"));
    }

    @Test
    void markPendingPush_noHistoricalExternalId_fallsBackToClosingRowsOwnIds() {
        // No row anywhere has ever carried a QuickBooks external id -- the fallback path uses
        // the row being closed's own (null) ids rather than defaulting to something else.
        InvoiceExternalMap open = row(SYNC_STATUS_AWAITING_APPROVAL, 1, null, null);
        open.setServiceId(SERVICE_ID);
        open.setTenantId(TENANT_ID);
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of(open));
        when(invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(INVOICE_ID, SERVICE_ID))
                .thenReturn(null);
        // Also exercise the null-maxVersion branch (falls back to version 1) in the same pass.
        when(invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(INVOICE_ID, SERVICE_ID)).thenReturn(null);
        echoSaves();

        service.markPendingPush(INVOICE_ID);

        ArgumentCaptor<InvoiceExternalMap> captor = ArgumentCaptor.forClass(InvoiceExternalMap.class);
        verify(invoiceExternalMapRepository, times(2)).save(captor.capture());
        InvoiceExternalMap newRow = captor.getAllValues().get(1);
        assertNull(newRow.getExternalInvoiceId());
        assertNull(newRow.getExternalAccountId());
        assertEquals(1, newRow.getVersion());
    }

    @Test
    void markPendingPush_rowNotAwaitingApproval_leftUntouched() {
        InvoiceExternalMap open = row(SYNC_STATUS_PENDING_PUSH, 1, EXT_INVOICE_ID, EXT_ACC_ID);
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of(open));

        service.markPendingPush(INVOICE_ID);

        assertEquals(SYNC_STATUS_PENDING_PUSH, open.getSyncStatus());
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void markPendingPush_noOpenRows_noOp() {
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of());
        service.markPendingPush(INVOICE_ID);
        verify(invoiceExternalMapRepository, never()).save(any());
    }

    @Test
    void markPendingPush_nullInvoiceId_noOp() {
        service.markPendingPush(null);
        verify(invoiceExternalMapRepository, never()).findOpenByInvoiceId(anyLong());
    }

    @Test
    void markPendingPush_repositoryThrows_doesNotPropagate() {
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenThrow(new RuntimeException("boom"));
        service.markPendingPush(INVOICE_ID);
        // no assertion needed beyond "did not throw" -- reaching here proves the catch worked
    }

    // ── markPendingPush: QuickBooks invoice create trigger (AB#31008 AC5–AC8) ──

    private static final String INVOICE_CREATE_PATH = "/extsvc/api/v1/quickbooks/invoices/create";
    private static final long SAVED_MAP_ID = 9001L;
    private static final String API_SECRET = "unit-test-api-secret";
    private static final String BEARER_TOKEN = "unit-test-bearer-token";

    /** Real local HTTP server on an ephemeral port that records every request it receives. */
    private static final class StubGateway implements AutoCloseable {
        final HttpServer server;
        final AtomicInteger hits = new AtomicInteger();
        final CountDownLatch received = new CountDownLatch(1);
        final AtomicReference<String> method = new AtomicReference<>();
        final AtomicReference<String> path = new AtomicReference<>();
        final AtomicReference<Headers> headers = new AtomicReference<>();
        final AtomicReference<String> body = new AtomicReference<>();

        StubGateway(int status) throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                method.set(exchange.getRequestMethod());
                path.set(exchange.getRequestURI().getPath());
                headers.set(exchange.getRequestHeaders());
                body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                hits.incrementAndGet();
                byte[] reply = "{}".getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(status, reply.length);
                try (var os = exchange.getResponseBody()) {
                    os.write(reply);
                }
                received.countDown();
            });
            server.start();
        }

        String url() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    private void pointGatewayAt(String url, String apiSecret) {
        ReflectionTestUtils.setField(quickBooksSyncService, "externalServiceUrl", url);
        ReflectionTestUtils.setField(quickBooksSyncService, "externalServiceApiSecret", apiSecret);
    }

    private void authenticateWithJwt() {
        Jwt jwt = Jwt.withTokenValue(BEARER_TOKEN).header("alg", "none").claim("sub", "unit-test-user").build();
        SecurityContextHolder.getContext().setAuthentication(new JwtAuthenticationToken(jwt));
    }

    /** One open AWAITING_APPROVAL row; the save answer assigns {@link #SAVED_MAP_ID} to the new PENDING_PUSH row. */
    private void stubAwaitingApprovalRowWithSavedId() {
        InvoiceExternalMap open = row(SYNC_STATUS_AWAITING_APPROVAL, 1, EXT_INVOICE_ID, EXT_ACC_ID);
        open.setServiceId(SERVICE_ID);
        open.setTenantId(TENANT_ID);
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID)).thenReturn(List.of(open));
        when(invoiceExternalMapRepository
                .findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(INVOICE_ID, SERVICE_ID))
                .thenReturn(null);
        when(invoiceExternalMapRepository.findMaxVersionByInvoiceIdAndServiceId(INVOICE_ID, SERVICE_ID)).thenReturn(1);
        when(invoiceExternalMapRepository.save(any(InvoiceExternalMap.class))).thenAnswer(inv -> {
            InvoiceExternalMap saved = inv.getArgument(0);
            if (SYNC_STATUS_PENDING_PUSH.equals(saved.getSyncStatus())) {
                saved.setId(SAVED_MAP_ID);
            }
            return saved;
        });
    }

    private InvoiceExternalMap savedPendingPushRow() {
        ArgumentCaptor<InvoiceExternalMap> captor = ArgumentCaptor.forClass(InvoiceExternalMap.class);
        verify(invoiceExternalMapRepository, times(2)).save(captor.capture());
        return captor.getAllValues().get(1);
    }

    @Test
    void markPendingPush_awaitingApprovalRow_postsOnceToInvoiceCreateWithSavedRowIdAndAuthHeaders() throws Exception {
        stubAwaitingApprovalRowWithSavedId();
        authenticateWithJwt();
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            service.markPendingPush(INVOICE_ID);

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS), "invoice create trigger must reach the gateway");
            TimeUnit.MILLISECONDS.sleep(300);
            assertEquals(1, gateway.hits.get(), "exactly one POST per saved PENDING_PUSH row");
            assertEquals("POST", gateway.method.get());
            assertEquals(INVOICE_CREATE_PATH, gateway.path.get());
            JSONObject body = new JSONObject(gateway.body.get());
            assertEquals(TENANT_ID, body.getString("tenant_id"));
            assertTrue(body.get("service_id") instanceof String, "service_id must be sent as a string");
            assertEquals(String.valueOf(SERVICE_ID), body.getString("service_id"));
            assertEquals(SAVED_MAP_ID, body.getLong("invoice_external_map_id"));
            Headers headers = gateway.headers.get();
            assertTrue(headers.getFirst("Content-Type").startsWith("application/json"));
            assertEquals(API_SECRET, headers.getFirst("api-secret"));
            assertEquals("Bearer " + BEARER_TOKEN, headers.getFirst("Authorization"));
            InvoiceExternalMap pendingRow = savedPendingPushRow();
            assertEquals(SYNC_STATUS_PENDING_PUSH, pendingRow.getSyncStatus());
            assertEquals(SAVED_MAP_ID, pendingRow.getId());
        } finally {
            SecurityContextHolder.clearContext();
        }
    }

    @Test
    void markPendingPush_unconfiguredSecretAndNoJwt_sendsEmptyApiSecretAndNoAuthorization() throws Exception {
        stubAwaitingApprovalRowWithSavedId();
        SecurityContextHolder.clearContext();
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), null);

            service.markPendingPush(INVOICE_ID);

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS),
                    "the request must still be sent with no secret and no bearer token");
            Headers headers = gateway.headers.get();
            assertEquals("", headers.getFirst("api-secret"), "unconfigured secret sends an empty api-secret header");
            assertNull(headers.getFirst("Authorization"), "no JWT means no Authorization header, never 'Bearer null'");
            assertEquals(SAVED_MAP_ID, new JSONObject(gateway.body.get()).getLong("invoice_external_map_id"));
        } finally {
            SecurityContextHolder.clearContext();
        }
    }

    @Test
    void markPendingPush_noRowMovedToPendingPush_sendsNoRequest() throws Exception {
        InvoiceExternalMap alreadyPending = row(SYNC_STATUS_PENDING_PUSH, 1, EXT_INVOICE_ID, EXT_ACC_ID);
        when(invoiceExternalMapRepository.findOpenByInvoiceId(INVOICE_ID))
                .thenReturn(List.of(alreadyPending), List.of());
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            service.markPendingPush(INVOICE_ID);
            service.markPendingPush(INVOICE_ID);
            service.markPendingPush(null);

            assertFalse(gateway.received.await(500, TimeUnit.MILLISECONDS), "no PENDING_PUSH row, no trigger");
            assertEquals(0, gateway.hits.get());
            verify(invoiceExternalMapRepository, never()).save(any());
        }
    }

    @Test
    void markPendingPush_gatewayUnreachable_returnsNormallyAndKeepsPendingPushRow() throws Exception {
        stubAwaitingApprovalRowWithSavedId();
        int closedPort;
        try (ServerSocket socket = new ServerSocket(0)) {
            closedPort = socket.getLocalPort();
        }
        pointGatewayAt("http://127.0.0.1:" + closedPort, API_SECRET);

        service.markPendingPush(INVOICE_ID);

        assertEquals(SYNC_STATUS_PENDING_PUSH, savedPendingPushRow().getSyncStatus());
    }

    @Test
    void markPendingPush_gatewayReturns500_returnsNormallyAndKeepsPendingPushRow() throws Exception {
        stubAwaitingApprovalRowWithSavedId();
        try (StubGateway gateway = new StubGateway(500)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            service.markPendingPush(INVOICE_ID);

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS), "the trigger must reach the failing gateway");
            assertEquals(SYNC_STATUS_PENDING_PUSH, savedPendingPushRow().getSyncStatus());
        }
    }

    @Test
    void markPendingPush_activeTransaction_postsOnlyAfterCommit() throws Exception {
        stubAwaitingApprovalRowWithSavedId();
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);
            TransactionSynchronizationManager.initSynchronization();
            try {
                service.markPendingPush(INVOICE_ID);

                assertFalse(gateway.received.await(500, TimeUnit.MILLISECONDS), "no POST before the commit");
                List<TransactionSynchronization> syncs = TransactionSynchronizationManager.getSynchronizations();
                assertFalse(syncs.isEmpty(), "the trigger must be registered as an afterCommit synchronization");
                for (TransactionSynchronization sync : syncs) {
                    sync.afterCommit();
                }
            } finally {
                TransactionSynchronizationManager.clearSynchronization();
            }
            assertTrue(gateway.received.await(5, TimeUnit.SECONDS), "afterCommit must send the trigger");
            assertEquals(INVOICE_CREATE_PATH, gateway.path.get());
            assertEquals(SAVED_MAP_ID, new JSONObject(gateway.body.get()).getLong("invoice_external_map_id"));
        }
    }
}

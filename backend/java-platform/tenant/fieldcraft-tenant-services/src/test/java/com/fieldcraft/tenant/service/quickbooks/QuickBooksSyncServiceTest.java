/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 24-Sep-2026
 * @author      : Shabnam Banu H
 * @since       : 1.5.0.0
 * Requirement# : AB#31008 — shared QuickBooksSyncService + Stock Debit Bill 422 (AC10, AC12)
 * Purpose      : Proves QuickBooksSyncService sends the bill and invoice create triggers with the
 *                request shape the two callers sent before (path, JSON body, headers), treats any
 *                2xx as success, never throws, and never sends a request whose external-map id is
 *                null. Uses a real local HTTP server, not a mocked HttpClient.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE       |   AUTHOR          |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31008       | 24-Sep-2026  | Shabnam Banu H    | Initial creation
 */
package com.fieldcraft.tenant.service.quickbooks;

import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpServer;
import org.json.JSONObject;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class QuickBooksSyncServiceTest {

    private static final String TENANT_ID = "tn59395620";
    private static final long SERVICE_ID = 6L;
    private static final long RECEIPT_EXTERNAL_MAP_ID = 8001L;
    private static final long INVOICE_EXTERNAL_MAP_ID = 9001L;
    private static final String BILL_CREATE_PATH = "/extsvc/api/v1/quickbooks/bills/create";
    private static final String INVOICE_CREATE_PATH = "/extsvc/api/v1/quickbooks/invoices/create";
    private static final String API_SECRET = "unit-test-api-secret";
    private static final String BEARER_TOKEN = "unit-test-bearer-token";

    private QuickBooksSyncService service;

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

    @BeforeEach
    void setUp() {
        service = new QuickBooksSyncService();
        ReflectionTestUtils.setField(service, "httpClient", HttpClient.newHttpClient());
    }

    private void pointGatewayAt(String url, String apiSecret) {
        ReflectionTestUtils.setField(service, "externalServiceUrl", url);
        ReflectionTestUtils.setField(service, "externalServiceApiSecret", apiSecret);
    }

    private static String closedPortUrl() throws IOException {
        try (ServerSocket socket = new ServerSocket(0)) {
            return "http://127.0.0.1:" + socket.getLocalPort();
        }
    }

    private static void assertSharedShape(StubGateway gateway, String expectedPath) {
        assertEquals("POST", gateway.method.get());
        assertEquals(expectedPath, gateway.path.get());
        Headers headers = gateway.headers.get();
        assertTrue(headers.getFirst("Content-Type").startsWith("application/json"));
        assertEquals(API_SECRET, headers.getFirst("api-secret"));
        assertEquals("Bearer " + BEARER_TOKEN, headers.getFirst("Authorization"));
    }

    // ── triggerBillCreate (AC10, AC12) ──

    @Test
    void triggerBillCreate_sendsCorrectPayloadAndHeaders() throws Exception {
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            service.triggerBillCreate(TENANT_ID, SERVICE_ID, RECEIPT_EXTERNAL_MAP_ID, BEARER_TOKEN);

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS), "bill create trigger must reach the gateway");
            TimeUnit.MILLISECONDS.sleep(300);
            assertEquals(1, gateway.hits.get(), "exactly one POST per call");
            assertSharedShape(gateway, BILL_CREATE_PATH);
            JSONObject body = new JSONObject(gateway.body.get());
            assertEquals(TENANT_ID, body.getString("tenant_id"));
            assertTrue(body.get("service_id") instanceof String, "service_id must be sent as a string");
            assertEquals(String.valueOf(SERVICE_ID), body.getString("service_id"));
            assertEquals(RECEIPT_EXTERNAL_MAP_ID, body.getLong("receipt_external_map_id"));
        }
    }

    @Test
    void triggerBillCreate_success2xxNot200_treatedAsSuccess() throws Exception {
        // Case under test: a 201 is a 2xx but not exactly 200. Success is widened from the callers'
        // former `== 200` check to the whole 200-299 range; the call must complete without throwing.
        try (StubGateway gateway = new StubGateway(201)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            assertDoesNotThrow(() ->
                    service.triggerBillCreate(TENANT_ID, SERVICE_ID, RECEIPT_EXTERNAL_MAP_ID, BEARER_TOKEN));

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS), "the 201 gateway must receive the trigger");
            assertEquals(1, gateway.hits.get());
            assertEquals(BILL_CREATE_PATH, gateway.path.get());
        }
    }

    @Test
    void triggerBillCreate_nullReceiptExternalMapId_sendsNoRequest() throws Exception {
        // AC12 regression: JSONObject.put(key, null) drops the key, so the gateway answered 422
        // "field required". A null id must be logged and never sent.
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            assertDoesNotThrow(() -> service.triggerBillCreate(TENANT_ID, SERVICE_ID, null, BEARER_TOKEN));

            assertFalse(gateway.received.await(1, TimeUnit.SECONDS), "a null receipt_external_map_id sends nothing");
            assertEquals(0, gateway.hits.get());
        }
    }

    @Test
    void triggerBillCreate_nullTenantId_sendsNoRequest() throws Exception {
        // A null tenantId would be silently dropped from the JSON by org.json's put(key, null)
        // rather than sent as an explicit null -- the request must not go out at all.
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            assertDoesNotThrow(() -> service.triggerBillCreate(null, SERVICE_ID, RECEIPT_EXTERNAL_MAP_ID, BEARER_TOKEN));

            assertFalse(gateway.received.await(1, TimeUnit.SECONDS), "a null tenantId sends nothing");
            assertEquals(0, gateway.hits.get());
        }
    }

    @Test
    void triggerBillCreate_nullServiceId_sendsNoRequest() throws Exception {
        // A null serviceId would otherwise become the literal string "null" via
        // String.valueOf(serviceId) -- the request must not go out at all.
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            assertDoesNotThrow(() -> service.triggerBillCreate(TENANT_ID, null, RECEIPT_EXTERNAL_MAP_ID, BEARER_TOKEN));

            assertFalse(gateway.received.await(1, TimeUnit.SECONDS), "a null serviceId sends nothing");
            assertEquals(0, gateway.hits.get());
        }
    }

    @Test
    void triggerBillCreate_unconfiguredSecretAndNoToken_sendsEmptySecretNoAuthHeader() throws Exception {
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), null);

            service.triggerBillCreate(TENANT_ID, SERVICE_ID, RECEIPT_EXTERNAL_MAP_ID, null);

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS),
                    "the request must still be sent with no secret and no bearer token");
            Headers headers = gateway.headers.get();
            assertEquals("", headers.getFirst("api-secret"), "unconfigured secret sends an empty api-secret header");
            assertNull(headers.getFirst("Authorization"), "no token means no Authorization header, never 'Bearer null'");
            assertEquals(RECEIPT_EXTERNAL_MAP_ID, new JSONObject(gateway.body.get()).getLong("receipt_external_map_id"));
        }
    }

    @Test
    void triggerBillCreate_gatewayUnreachable_returnsNormally() throws Exception {
        pointGatewayAt(closedPortUrl(), API_SECRET);

        assertDoesNotThrow(() ->
                service.triggerBillCreate(TENANT_ID, SERVICE_ID, RECEIPT_EXTERNAL_MAP_ID, BEARER_TOKEN));
        TimeUnit.MILLISECONDS.sleep(300);
    }

    // ── triggerInvoiceCreate (AC10, AC12) ──

    @Test
    void triggerInvoiceCreate_sendsCorrectPayloadAndHeaders() throws Exception {
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            service.triggerInvoiceCreate(TENANT_ID, SERVICE_ID, INVOICE_EXTERNAL_MAP_ID, BEARER_TOKEN);

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS), "invoice create trigger must reach the gateway");
            TimeUnit.MILLISECONDS.sleep(300);
            assertEquals(1, gateway.hits.get(), "exactly one POST per call");
            assertSharedShape(gateway, INVOICE_CREATE_PATH);
            JSONObject body = new JSONObject(gateway.body.get());
            assertEquals(TENANT_ID, body.getString("tenant_id"));
            assertTrue(body.get("service_id") instanceof String, "service_id must be sent as a string");
            assertEquals(String.valueOf(SERVICE_ID), body.getString("service_id"));
            assertEquals(INVOICE_EXTERNAL_MAP_ID, body.getLong("invoice_external_map_id"));
        }
    }

    @Test
    void triggerInvoiceCreate_nullInvoiceExternalMapId_sendsNoRequest() throws Exception {
        try (StubGateway gateway = new StubGateway(200)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            assertDoesNotThrow(() -> service.triggerInvoiceCreate(TENANT_ID, SERVICE_ID, null, BEARER_TOKEN));

            assertFalse(gateway.received.await(1, TimeUnit.SECONDS), "a null invoice_external_map_id sends nothing");
            assertEquals(0, gateway.hits.get());
        }
    }

    @Test
    void triggerInvoiceCreate_gatewayReturns500_returnsNormally() throws Exception {
        try (StubGateway gateway = new StubGateway(500)) {
            pointGatewayAt(gateway.url(), API_SECRET);

            assertDoesNotThrow(() ->
                    service.triggerInvoiceCreate(TENANT_ID, SERVICE_ID, INVOICE_EXTERNAL_MAP_ID, BEARER_TOKEN));

            assertTrue(gateway.received.await(5, TimeUnit.SECONDS), "the trigger must reach the failing gateway");
            assertEquals(INVOICE_CREATE_PATH, gateway.path.get());
        }
    }
}

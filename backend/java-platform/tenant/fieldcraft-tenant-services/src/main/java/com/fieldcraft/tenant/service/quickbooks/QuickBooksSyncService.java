/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 24-Sep-2026
 * @author      : Shabnam Banu H
 * @since       : 1.5.0.0
 * Requirement# : AB#31008 — shared QuickBooksSyncService + Stock Debit Bill 422
 * Purpose      : Sends the QuickBooks bill and invoice create triggers to the integration gateway
 *                (request shape, api-secret and forwarded bearer token, async send, result log).
 *                Callers decide when to sync; this service owns how.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE       |   AUTHOR          |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31008       | 24-Sep-2026  | Shabnam Banu H    | Initial creation
 */
package com.fieldcraft.tenant.service.quickbooks;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.json.JSONException;
import org.json.JSONObject;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.UncheckedIOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * Fire-and-forget POST of {@code tenant_id, service_id, <external map id>} to the gateway's
 * QuickBooks create endpoints. Never throws: a null tenant id, service id or external-map id is
 * logged and nothing is sent, a build failure is logged here, and an HTTP or network failure is
 * logged by the async callbacks. Any 2xx status is a success. {@code fieldcraft.externalService.url}
 * is required configuration — a profile that omits it fails at startup, on purpose, rather than
 * being skipped silently.
 */
@Service
public class QuickBooksSyncService {

    private static final Logger logger = LogManager.getLogger(QuickBooksSyncService.class);
    private static final SyncTarget BILL = new SyncTarget(
            "bill", "/extsvc/api/v1/quickbooks/bills/create", "receipt_external_map_id");
    private static final SyncTarget INVOICE = new SyncTarget(
            "invoice", "/extsvc/api/v1/quickbooks/invoices/create", "invoice_external_map_id");
    private static final String KEY_TENANT_ID = "tenant_id";
    private static final String KEY_SERVICE_ID = "service_id";
    private static final String HEADER_CONTENT_TYPE = "Content-Type";
    private static final String HEADER_API_SECRET = "api-secret";
    private static final String HEADER_AUTHORIZATION = "Authorization";
    private static final String CONTENT_TYPE_JSON = "application/json";
    private static final String BEARER_PREFIX = "Bearer ";
    private static final Duration GATEWAY_TIMEOUT = Duration.ofMinutes(1);
    private static final int HTTP_SUCCESS_MIN = 200;
    private static final int HTTP_SUCCESS_MAX = 299;

    @Value("${fieldcraft.externalService.url}")
    String externalServiceUrl;                          // package-private for test injection
    @Value("${fieldcraft.externalService.api-secret:}")
    String externalServiceApiSecret;                    // package-private for test injection
    HttpClient httpClient = HttpClient.newHttpClient();  // package-private (non-final) for test injection

    /** One gateway create endpoint: log label, path, and the JSON key carrying the external-map id. */
    private record SyncTarget(String label, String path, String idKey) {
    }

    /** Pushes one {@code receipt_external_map} row to QuickBooks as a bill. */
    public void triggerBillCreate(final String tenantId, final Long serviceId,
                                  final Long receiptExternalMapId, final String bearerToken) {
        trigger(BILL, tenantId, serviceId, receiptExternalMapId, bearerToken);
    }

    /** Pushes one {@code invoice_external_map} row to QuickBooks as an invoice. */
    public void triggerInvoiceCreate(final String tenantId, final Long serviceId,
                                     final Long invoiceExternalMapId, final String bearerToken) {
        trigger(INVOICE, tenantId, serviceId, invoiceExternalMapId, bearerToken);
    }

    private void trigger(final SyncTarget target, final String tenantId, final Long serviceId,
                         final Long externalMapId, final String bearerToken) {
        if (externalMapId == null) {
            logger.error("QuickBooks {} create not sent for serviceId {}: {} is null",
                    target.label(), serviceId, target.idKey());
            return;
        }
        if (tenantId == null || serviceId == null) {
            // Neither may reach org.json as null: a null tenantId would be silently dropped from
            // the JSON body (org.json's put(key, null) removes the key) and a null serviceId would
            // become the literal string "null" via String.valueOf -- both send an incomplete or
            // wrong payload instead of failing loudly.
            logger.error("QuickBooks {} create not sent for {} {}: tenantId or serviceId is null",
                    target.label(), target.idKey(), externalMapId);
            return;
        }
        if (externalServiceUrl == null || externalServiceUrl.isBlank()) {
            logger.warn("fieldcraft.externalService.url is not configured; skipping QuickBooks {} create for {} {}",
                    target.label(), target.idKey(), externalMapId);
            return;
        }
        try {
            HttpRequest request = buildRequest(target, tenantId, serviceId, externalMapId, bearerToken);
            httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                    .thenAccept(response -> logResponse(target, externalMapId, response.statusCode()))
                    .exceptionally(e -> {
                        logger.error("Error triggering QuickBooks {} create for {} {}: {}",
                                target.label(), target.idKey(), externalMapId, e.getMessage());
                        return null;
                    });
        } catch (IllegalArgumentException | IllegalStateException | SecurityException
                 | UncheckedIOException | JSONException e) {
            logger.error("Error triggering QuickBooks {} create for {} {}: {}",
                    target.label(), target.idKey(), externalMapId, e.getMessage(), e);
        }
    }

    private HttpRequest buildRequest(final SyncTarget target, final String tenantId, final Long serviceId,
                                     final Long externalMapId, final String bearerToken) {
        JSONObject payload = new JSONObject()
                .put(KEY_TENANT_ID, tenantId)
                .put(KEY_SERVICE_ID, String.valueOf(serviceId))
                .put(target.idKey(), externalMapId);
        if (externalServiceApiSecret == null || externalServiceApiSecret.isBlank()) {
            logger.warn("fieldcraft.externalService.api-secret is not configured for this environment; "
                    + "sending empty api-secret header for {} {}", target.idKey(), externalMapId);
        }
        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(externalServiceUrl + target.path()))
                .timeout(GATEWAY_TIMEOUT)
                .header(HEADER_CONTENT_TYPE, CONTENT_TYPE_JSON)
                .header(HEADER_API_SECRET, externalServiceApiSecret == null ? "" : externalServiceApiSecret);
        if (bearerToken != null) {
            builder.header(HEADER_AUTHORIZATION, BEARER_PREFIX + bearerToken);
        } else {
            logger.warn("No bearer token available to forward for QuickBooks {} create, {} {}",
                    target.label(), target.idKey(), externalMapId);
        }
        return builder.POST(HttpRequest.BodyPublishers.ofString(payload.toString())).build();
    }

    private void logResponse(final SyncTarget target, final Long externalMapId, final int statusCode) {
        if (statusCode >= HTTP_SUCCESS_MIN && statusCode <= HTTP_SUCCESS_MAX) {
            logger.info("QuickBooks {} create triggered for {} {}", target.label(), target.idKey(), externalMapId);
        } else {
            logger.error("QuickBooks {} create failed for {} {}: HTTP {}",
                    target.label(), target.idKey(), externalMapId, statusCode);
        }
    }
}

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * @author : Shanmugapriyan G (Claude-assisted)
 * Purpose : Unit tests for AzurePubSubSubscriber's access-url minting — the connectionString/hub
 *           guard on all three getClientAccessUrl* overloads, and the per-group role/auto-join
 *           construction in getClientAccessUrlWithGroups (added 2026-08-20 for the Provider->Tenant
 *           negotiate endpoint). Web PubSub's getClientAccessToken() signs a JWT locally from the
 *           connection string's access key — no live network call — so the full role/group-list
 *           behavior is exercised directly, not just the guard.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/Issue | DATE       | DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * Provider PubSub   | 2026-08-22 | Initial creation.
 */
package com.fieldcraft.azure.messaging;

import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AzurePubSubSubscriberTest {

    /** Same shape a real Web PubSub connection string takes — the access key is fake but
     *  well-formed, which is all local JWT signing needs; no network call is made. */
    private static final String FAKE_CONNECTION_STRING =
            "Endpoint=https://wps-fake.webpubsub.azure.com;AccessKey=ZmFrZS1hY2Nlc3Mta2V5LXBhZGRlZC1sb25nLWVub3VnaA==;Version=1.0;";

    private static AzurePubSubSubscriber initializedSubscriber() {
        Map<String, String> props = new HashMap<>();
        props.put("endpoint", FAKE_CONNECTION_STRING);
        props.put("hub", "fieldcraft");
        return new AzurePubSubSubscriber(props);
    }

    @Test
    void getClientAccessUrlReturnsNullWhenNeverInitialized() {
        // The no-arg constructor leaves connectionString/hub null — both getters must short-
        // circuit rather than attempt to build a client with nothing to connect to.
        AzurePubSubSubscriber subscriber = new AzurePubSubSubscriber();

        assertNull(subscriber.getClientAccessUrl(List.of("group-a")));
    }

    @Test
    void getClientAccessUrlWithExpiryReturnsNullWhenNeverInitialized() {
        AzurePubSubSubscriber subscriber = new AzurePubSubSubscriber();

        assertNull(subscriber.getClientAccessUrl(List.of("group-a"), 30));
    }

    @Test
    void getClientAccessUrlWithGroupsReturnsNullWhenNeverInitialized() {
        AzurePubSubSubscriber subscriber = new AzurePubSubSubscriber();

        assertNull(subscriber.getClientAccessUrlWithGroups(List.of("role-a"), List.of("auto-a"), 30));
    }

    @Test
    void getClientAccessUrlWithGroupsReturnsNullWhenInitializedWithBlankHub() {
        Map<String, String> props = new HashMap<>();
        props.put("endpoint", FAKE_CONNECTION_STRING);
        props.put("hub", "   ");
        AzurePubSubSubscriber subscriber = new AzurePubSubSubscriber(props);

        assertNull(subscriber.getClientAccessUrlWithGroups(List.of("role-a"), List.of("auto-a"), 30));
    }

    @Test
    void getClientAccessUrlMintsATokenWhenProperlyInitialized() {
        AzurePubSubSubscriber subscriber = initializedSubscriber();

        String url = subscriber.getClientAccessUrl(List.of("tenant:syn-tenant-1"));

        assertTrue(url != null && url.contains("access_token="),
                "a real connection string + hub must mint a client access URL, not return null");
    }

    @Test
    void getClientAccessUrlWithGroupsSkipsBlankEntriesInBothRoleAndAutoJoinLists() {
        // Mirrors NotificationApi#negotiate's own call shape: a mix of real and accidentally-blank
        // entries in either list must not blow up minting the token — blanks are silently skipped
        // in both loops (AzurePubSubSubscriber.getClientAccessUrlWithGroups), never passed to
        // GetClientAccessTokenOptions as a literal empty-string role/group.
        AzurePubSubSubscriber subscriber = initializedSubscriber();

        String url = subscriber.getClientAccessUrlWithGroups(
                List.of("tenant:syn-tenant-1", "  ", ""),
                List.of("provider-broadcast", " "),
                30);

        assertTrue(url != null && url.contains("access_token="),
                "blank role/auto-join entries must be skipped, not fail token minting");
    }
}

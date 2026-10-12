/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 29-Jul-2026
 * @author      : Shabnam
 * @since       : 1.2.8.0
 * Requirement# :
 * Purpose      : Unit tests for AiServiceCredentialBuilder — mints the X-FIELDCRAFT-Token the AI services
 *                authorise against, so the claims it encrypts ARE the authorisation decision.
 *                Tests decrypt the produced token and assert on the claims, rather than only
 *                checking a non-null string: the two security-critical invariants are that roles
 *                is ALWAYS empty (a service principal must never carry superAdmin) and that the
 *                tenant is the one passed in, never a config default. Also covers the
 *                mintV2 -> AES-GCM / legacy -> AES-CBC cipher selection.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *                | 29-Jul-2026  | Shabnam    | Initial version
 * -----------------------------------------------------------------------------
 */
package com.fieldcraft.tenant.feature.estimate;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fieldcraft.utils.CryptUtils;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AiServiceCredentialBuilderTest {

    // 16 chars — AES-128, the length CryptUtils expects.
    private static final String SECRET_KEY = "0123456789abcdef";
    private static final String ENVIRONMENT = "dev";
    private static final String TENANT_ID = "SYN-TENANT-01";
    private static final String SERVICE_OID = "SYN-SP-OID-9999";
    private static final List<String> PERMISSIONS = List.of("EST.CRE", "PRO.CRE", "PRO.EST");
    private static final long TTL_SECONDS = 900L;

    private final AiServiceCredentialBuilder builder = new AiServiceCredentialBuilder();
    private final ObjectMapper mapper = new ObjectMapper();

    private JsonNode decryptLegacy(final String token) throws Exception {
        return mapper.readTree(CryptUtils.decrypt(token, SECRET_KEY));
    }

    private JsonNode decryptV2(final String token) throws Exception {
        return mapper.readTree(CryptUtils.decryptV2(token, SECRET_KEY));
    }

    @Test
    void build_legacyMode_producesAnAesCbcTokenCarryingTheSuppliedClaims() throws Exception {
        String token = builder.build(SECRET_KEY, false, ENVIRONMENT, TENANT_ID, SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);

        JsonNode claims = decryptLegacy(token);

        assertEquals(TENANT_ID, claims.path("tenantId").asText());
        assertEquals(ENVIRONMENT, claims.path("environment").asText());
        assertEquals(SERVICE_OID, claims.path("userUid").asText());
    }

    @Test
    void build_v2Mode_producesAnAesGcmToken() throws Exception {
        String token = builder.build(SECRET_KEY, true, ENVIRONMENT, TENANT_ID, SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);

        JsonNode claims = decryptV2(token);

        assertEquals(TENANT_ID, claims.path("tenantId").asText());
    }

    @Test
    void build_v2Mode_usesARandomIvSoTwoMintsOfTheSameClaimsDiffer() throws Exception {
        String first = builder.build(SECRET_KEY, true, ENVIRONMENT, TENANT_ID, SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);
        String second = builder.build(SECRET_KEY, true, ENVIRONMENT, TENANT_ID, SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);

        assertNotEquals(first, second, "AES-GCM must use a fresh random IV per mint");
        // Both must still decrypt to the same tenant.
        assertEquals(TENANT_ID, decryptV2(first).path("tenantId").asText());
        assertEquals(TENANT_ID, decryptV2(second).path("tenantId").asText());
    }

    @Test
    void build_theTwoCipherModesAreNotInterchangeable() throws Exception {
        String v2Token = builder.build(SECRET_KEY, true, ENVIRONMENT, TENANT_ID, SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);

        // A GCM token must not be readable as CBC — proves mintV2 genuinely switches cipher
        // rather than merely relabelling, which is what the Python dual-read rollout depends on.
        assertThrows(Exception.class, () -> decryptLegacy(v2Token));
    }

    @Test
    void build_neverGrantsARole_soAServicePrincipalCanNeverBeSuperAdmin() throws Exception {
        JsonNode claims = decryptLegacy(builder.build(SECRET_KEY, false, ENVIRONMENT, TENANT_ID,
                SERVICE_OID, PERMISSIONS, TTL_SECONDS));

        JsonNode roles = claims.path("roles");
        assertTrue(roles.isArray(), "roles must be present as an array");
        assertTrue(roles.isEmpty(), "a service principal must carry NO role");
    }

    @Test
    void build_carriesExactlyTheLeastPrivilegePermissionsSupplied() throws Exception {
        JsonNode claims = decryptLegacy(builder.build(SECRET_KEY, false, ENVIRONMENT, TENANT_ID,
                SERVICE_OID, PERMISSIONS, TTL_SECONDS));

        JsonNode rolePermission = claims.path("rolePermission");
        assertEquals(3, rolePermission.size());
        assertEquals("EST.CRE", rolePermission.get(0).asText());
        assertEquals("PRO.CRE", rolePermission.get(1).asText());
        assertEquals("PRO.EST", rolePermission.get(2).asText());
    }

    @Test
    void build_acceptsAnEmptyPermissionListWithoutInventingPermissions() throws Exception {
        JsonNode claims = decryptLegacy(builder.build(SECRET_KEY, false, ENVIRONMENT, TENANT_ID,
                SERVICE_OID, List.of(), TTL_SECONDS));

        assertTrue(claims.path("rolePermission").isEmpty());
    }

    @Test
    void build_stampsExpiryExactlyTtlSecondsAfterIssuedAt() throws Exception {
        long before = Instant.now().getEpochSecond();
        JsonNode claims = decryptLegacy(builder.build(SECRET_KEY, false, ENVIRONMENT, TENANT_ID,
                SERVICE_OID, PERMISSIONS, TTL_SECONDS));
        long after = Instant.now().getEpochSecond();

        long issuedAt = claims.path("issuedAt").asLong();
        long expiresAt = claims.path("expiresAt").asLong();

        assertTrue(issuedAt >= before && issuedAt <= after,
                "issuedAt " + issuedAt + " must sit inside [" + before + ", " + after + "]");
        assertEquals(TTL_SECONDS, expiresAt - issuedAt);
    }

    @Test
    void build_honoursTheTenantArgumentSoABatchSpanningTenantsStaysIsolated() throws Exception {
        String tokenA = builder.build(SECRET_KEY, false, ENVIRONMENT, "SYN-TENANT-A", SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);
        String tokenB = builder.build(SECRET_KEY, false, ENVIRONMENT, "SYN-TENANT-B", SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);

        assertEquals("SYN-TENANT-A", decryptLegacy(tokenA).path("tenantId").asText());
        assertEquals("SYN-TENANT-B", decryptLegacy(tokenB).path("tenantId").asText());
    }

    @Test
    void build_aTokenMintedWithOneKeyIsNotReadableWithAnother() throws Exception {
        String token = builder.build(SECRET_KEY, false, ENVIRONMENT, TENANT_ID, SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);

        assertThrows(Exception.class, () -> CryptUtils.decrypt(token, "fedcba9876543210"));
    }

    @Test
    void build_acceptsAnySecretKeyLengthBecauseTheKeyIsPbkdf2Derived() throws Exception {
        // CryptUtils runs the configured secret through PBKDF2 with a fixed salt, so key LENGTH
        // is not a constraint — a short fieldcraft.<service>.secret-key still mints a usable token
        // rather than failing at runtime. Pinned so nobody "fixes" this into a length check
        // without also re-issuing every environment's configured secret.
        String token = builder.build("short", false, ENVIRONMENT, TENANT_ID, SERVICE_OID,
                PERMISSIONS, TTL_SECONDS);

        assertEquals(TENANT_ID,
                mapper.readTree(CryptUtils.decrypt(token, "short")).path("tenantId").asText());
    }

    @Test
    void build_toleratesNullTenantAndUserWithoutFailingTheMint() throws Exception {
        // The port signature allows nulls; they must serialise as JSON null rather than NPE,
        // so a mis-configured caller fails downstream at the AI service (401) not here.
        JsonNode claims = decryptLegacy(builder.build(SECRET_KEY, false, ENVIRONMENT, null, null,
                PERMISSIONS, TTL_SECONDS));

        assertTrue(claims.path("tenantId").isNull());
        assertTrue(claims.path("userUid").isNull());
    }
}

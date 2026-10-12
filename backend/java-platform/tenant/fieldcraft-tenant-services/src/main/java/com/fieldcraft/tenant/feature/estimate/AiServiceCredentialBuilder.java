/*
 * Copyright (C) FIELDCRAFT inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 *
 * Purpose : Build the X-FIELDCRAFT-Token for the AI services from CONFIG (tenant +
 *           a fixed least-privilege permission list) — no DB lookup, no user
 *           record, never superAdmin. This is the in-process, service-account
 *           counterpart to RecommendationCredentialService (which mints from
 *           the DB by oid). ADDITIVE — does not modify the existing service.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE     |   AUTHOR      |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * RMC-SP         | 16-07-2026 | Aravindan M   | Initial version.
 * -----------------------------------------------------------------------------
 */
package com.fieldcraft.tenant.feature.estimate;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fieldcraft.tenant.data.model.AiServiceCredentialInfo;
import com.fieldcraft.utils.CryptUtils;
import java.time.Instant;
import java.util.Collections;
import java.util.List;
import org.springframework.stereotype.Component;

/**
 * Encrypts an {@link AiServiceCredentialInfo} claims blob exactly like
 * {@code RecommendationCredentialService}, but with the tenant + permissions
 * supplied from configuration instead of resolved from the DB by {@code oid}.
 * The AI service decrypts it with its matching {@code API_SECRET} + salt.
 */
@Component
public class AiServiceCredentialBuilder {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /**
     * @param secretKey   the per-service AES key (fieldcraft.&lt;service&gt;.secret-key)
     * @param mintV2      true = AES-GCM (encryptV2), false = AES-CBC (encrypt)
     * @param environment fieldcraft.environment (e.g. "dev")
     * @param tenantId    the FIELDCRAFT tenant this credential acts on
     * @param userUid     synthetic service-principal id (for audit; not a DB user)
     * @param permissions least-privilege permission list (e.g. EST.CRE/PRO.CRE/PRO.EST)
     * @param ttlSeconds  token lifetime
     * @return the base64 X-FIELDCRAFT-Token
     */
    public String build(
            String secretKey,
            boolean mintV2,
            String environment,
            String tenantId,
            String userUid,
            List<String> permissions,
            long ttlSeconds)
            throws Exception {

        AiServiceCredentialInfo info = new AiServiceCredentialInfo();
        info.setTenantId(tenantId);
        info.setEnvironment(environment);
        info.setUserUid(userUid);
        info.setRoles(Collections.emptyList()); // service principal carries NO role — never superAdmin
        info.setRolePermission(permissions);
        long now = Instant.now().getEpochSecond();
        info.setIssuedAt(now);
        info.setExpiresAt(now + ttlSeconds);

        String json = MAPPER.writeValueAsString(info);
        return mintV2 ? CryptUtils.encryptV2(json, secretKey) : CryptUtils.encrypt(json, secretKey);
    }
}

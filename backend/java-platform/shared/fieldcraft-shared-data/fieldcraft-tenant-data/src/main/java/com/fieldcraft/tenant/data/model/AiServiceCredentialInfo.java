package com.fieldcraft.tenant.data.model;

import java.util.Collections;
import java.util.List;

/**
 * Encrypted-token payload for the AI-service credential endpoints
 * (Recommendation, Ball Park Estimation, vLLM).
 *
 * Consolidates the previously duplicated BallParkCredentialInfo /
 * RecommendationCredentialInfo / VllmCredentialInfo DTOs (which were byte-for-byte
 * identical) into a single reusable model. The List getters return an
 * unmodifiable view so callers cannot mutate the DTO's internal state.
 */
public class AiServiceCredentialInfo {

    private String tenantId;
    private String environment;
    private String userUid;
    private List<String> roles;
    private List<String> rolePermission;
    private long issuedAt;
    private long expiresAt;

    public String getTenantId() {
        return tenantId;
    }

    public void setTenantId(String tenantId) {
        this.tenantId = tenantId;
    }

    public String getEnvironment() {
        return environment;
    }

    public void setEnvironment(String environment) {
        this.environment = environment;
    }

    public String getUserUid() {
        return userUid;
    }

    public void setUserUid(String userUid) {
        this.userUid = userUid;
    }

    public List<String> getRoles() {
        return roles == null ? null : Collections.unmodifiableList(roles);
    }

    public void setRoles(List<String> roles) {
        this.roles = roles;
    }

    public List<String> getRolePermission() {
        return rolePermission == null ? null : Collections.unmodifiableList(rolePermission);
    }

    public void setRolePermission(List<String> rolePermission) {
        this.rolePermission = rolePermission;
    }

    public long getIssuedAt() {
        return issuedAt;
    }

    public void setIssuedAt(long issuedAt) {
        this.issuedAt = issuedAt;
    }

    public long getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(long expiresAt) {
        this.expiresAt = expiresAt;
    }
}

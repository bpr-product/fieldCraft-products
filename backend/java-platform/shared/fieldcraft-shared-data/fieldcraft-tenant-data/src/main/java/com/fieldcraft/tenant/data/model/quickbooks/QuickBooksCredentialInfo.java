package com.fieldcraft.tenant.data.model.quickbooks;


import java.util.List;

public class QuickBooksCredentialInfo {
    private String tenantId;
    private String userUid;
    private List<String> roles;
    private List<String> rolePermission;

    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }

    public String getUserUid() { return userUid; }
    public void setUserUid(String userUid) { this.userUid = userUid; }

    public List<String> getRoles() { return roles; }
    public void setRoles(List<String> roles) { this.roles = roles; }

    public List<String> getRolePermission() { return rolePermission; }
    public void setRolePermission(List<String> rolePermission) { this.rolePermission = rolePermission; }
}

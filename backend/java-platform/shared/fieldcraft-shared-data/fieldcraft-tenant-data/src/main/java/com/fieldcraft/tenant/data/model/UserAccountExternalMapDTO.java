package com.fieldcraft.tenant.data.model;

import java.time.OffsetDateTime;
import java.util.UUID;

public class UserAccountExternalMapDTO {

    private Long id;
    private Long userAccountId;
    private UUID userAccountUuid;
    private String tenantId;
    private Long serviceId;
    private String externalEmployeeId;
    private String externalAccountId;
    private String syncStatus;
    private Integer version;
    private OffsetDateTime versionEffectiveDate;
    private OffsetDateTime versionExpiryDate;
    private OffsetDateTime endorsementEffectiveDate;
    private OffsetDateTime endorsementExpiryDate;
    private OffsetDateTime updatedAt;
    private String syncToken;
    private String reason;
    private String externalDisplayName;
    private String externalVendorId;
    private String externalEntityType;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Long getUserAccountId() { return userAccountId; }
    public void setUserAccountId(Long userAccountId) { this.userAccountId = userAccountId; }

    public UUID getUserAccountUuid() { return userAccountUuid; }
    public void setUserAccountUuid(UUID userAccountUuid) { this.userAccountUuid = userAccountUuid; }

    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }

    public Long getServiceId() { return serviceId; }
    public void setServiceId(Long serviceId) { this.serviceId = serviceId; }

    public String getExternalEmployeeId() { return externalEmployeeId; }
    public void setExternalEmployeeId(String externalEmployeeId) { this.externalEmployeeId = externalEmployeeId; }

    public String getExternalAccountId() { return externalAccountId; }
    public void setExternalAccountId(String externalAccountId) { this.externalAccountId = externalAccountId; }

    public String getSyncStatus() { return syncStatus; }
    public void setSyncStatus(String syncStatus) { this.syncStatus = syncStatus; }

    public Integer getVersion() { return version; }
    public void setVersion(Integer version) { this.version = version; }

    public OffsetDateTime getVersionEffectiveDate() { return versionEffectiveDate; }
    public void setVersionEffectiveDate(OffsetDateTime versionEffectiveDate) { this.versionEffectiveDate = versionEffectiveDate; }

    public OffsetDateTime getVersionExpiryDate() { return versionExpiryDate; }
    public void setVersionExpiryDate(OffsetDateTime versionExpiryDate) { this.versionExpiryDate = versionExpiryDate; }

    public OffsetDateTime getEndorsementEffectiveDate() { return endorsementEffectiveDate; }
    public void setEndorsementEffectiveDate(OffsetDateTime endorsementEffectiveDate) { this.endorsementEffectiveDate = endorsementEffectiveDate; }

    public OffsetDateTime getEndorsementExpiryDate() { return endorsementExpiryDate; }
    public void setEndorsementExpiryDate(OffsetDateTime endorsementExpiryDate) { this.endorsementExpiryDate = endorsementExpiryDate; }

    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }

    public String getSyncToken() { return syncToken; }
    public void setSyncToken(String syncToken) { this.syncToken = syncToken; }

    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }

    public String getExternalDisplayName() { return externalDisplayName; }
    public void setExternalDisplayName(String externalDisplayName) { this.externalDisplayName = externalDisplayName; }

    public String getExternalVendorId() { return externalVendorId; }
    public void setExternalVendorId(String externalVendorId) { this.externalVendorId = externalVendorId; }

    public String getExternalEntityType() { return externalEntityType; }
    public void setExternalEntityType(String externalEntityType) { this.externalEntityType = externalEntityType; }
}

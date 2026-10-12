package com.fieldcraft.tenant.data.domain;

import jakarta.persistence.*;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "user_account_external_map")
public class UserAccountExternalMap {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_account_id")
    private UserAccount userAccount;

    @Column(name = "user_account_uuid")
    private UUID userAccountUuid;

    @Column(name = "tenant_id")
    private String tenantId;

    @Column(name = "service_id")
    private Long serviceId;

    @Column(name = "external_employee_id", length = 128)
    private String externalEmployeeId;

    @Column(name = "external_account_id", length = 128)
    private String externalAccountId;

    @Column(name = "sync_status", length = 32)
    private String syncStatus;

    @Column(name = "version", nullable = false)
    private Integer version;

    @Column(name = "version_effective_date")
    private OffsetDateTime versionEffectiveDate;

    @Column(name = "version_expiry_date")
    private OffsetDateTime versionExpiryDate;

    @Column(name = "endorsement_effective_date", nullable = false)
    private OffsetDateTime endorsementEffectiveDate;

    @Column(name = "endorsement_expiry_date")
    private OffsetDateTime endorsementExpiryDate;

    @Column(name = "updated_at")
    private OffsetDateTime updatedAt;

    @Column(name = "sync_token", length = 255)
    private String syncToken;

    @Column(name = "reason", columnDefinition = "text")
    private String reason;

    @Column(name = "external_display_name", length = 255)
    private String externalDisplayName;

    @Column(name = "external_vendor_id", length = 128)
    private String externalVendorId;

    @Column(name = "external_entity_type", length = 32)
    private String externalEntityType;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public UserAccount getUserAccount() { return userAccount; }
    public void setUserAccount(UserAccount userAccount) { this.userAccount = userAccount; }

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

    public String getExternalEntityType() { return externalEntityType; }
    public void setExternalEntityType(String externalEntityType) { this.externalEntityType = externalEntityType; }

    public String getExternalDisplayName() { return externalDisplayName; }
    public void setExternalDisplayName(String externalDisplayName) { this.externalDisplayName = externalDisplayName; }

    public String getExternalVendorId() { return externalVendorId; }
    public void setExternalVendorId(String externalVendorId) { this.externalVendorId = externalVendorId; }
}

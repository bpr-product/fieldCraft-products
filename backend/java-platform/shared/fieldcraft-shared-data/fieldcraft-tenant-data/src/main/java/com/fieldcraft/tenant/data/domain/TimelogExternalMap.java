package com.fieldcraft.tenant.data.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.OffsetDateTime;

/**
 * Maps a {@code timelog} row to its external (QuickBooks TimeActivity) representation for the
 * Data Exchange sync. Mirrors the customer/vendor/project/user external_map versioning tables.
 *
 * <p>Lifecycle is driven by {@code syncStatus} (PENDING_PUSH / SYNCED / INVALID / SYNC_FAILED).
 * The current/open version of a timelog is the row whose {@code endorsementExpiryDate} is
 * {@code null} — there is a partial unique index enforcing only one such row per
 * (tenant, service, timelog), so superseding a version closes the prior open row first.
 */
@Entity
@Table(name = "timelog_external_map")
public class TimelogExternalMap {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "timelog_id", nullable = false)
    private Long timelogId;

    @Column(name = "tenant_id", nullable = false)
    private String tenantId;

    @Column(name = "service_id", nullable = false)
    private Long serviceId;

    @Column(name = "external_time_activity_id", length = 128)
    private String externalTimeActivityId;

    @Column(name = "external_account_id", length = 128)
    private String externalAccountId;

    @Column(name = "external_display_name", length = 255)
    private String externalDisplayName;

    @Column(name = "sync_status", length = 32, nullable = false)
    private String syncStatus;

    @Column(name = "version", nullable = false)
    private Integer version;

    @Column(name = "version_effective_date")
    private OffsetDateTime versionEffectiveDate;

    @Column(name = "version_expiry_date")
    private OffsetDateTime versionExpiryDate;

    @Column(name = "endorsement_effective_date")
    private OffsetDateTime endorsementEffectiveDate;

    @Column(name = "endorsement_expiry_date")
    private OffsetDateTime endorsementExpiryDate;

    @Column(name = "updated_at")
    private OffsetDateTime updatedAt;

    @Column(name = "sync_token", length = 128)
    private String syncToken;

    @Column(name = "reason", columnDefinition = "text")
    private String reason;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Long getTimelogId() { return timelogId; }
    public void setTimelogId(Long timelogId) { this.timelogId = timelogId; }

    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }

    public Long getServiceId() { return serviceId; }
    public void setServiceId(Long serviceId) { this.serviceId = serviceId; }

    public String getExternalTimeActivityId() { return externalTimeActivityId; }
    public void setExternalTimeActivityId(String externalTimeActivityId) { this.externalTimeActivityId = externalTimeActivityId; }

    public String getExternalAccountId() { return externalAccountId; }
    public void setExternalAccountId(String externalAccountId) { this.externalAccountId = externalAccountId; }

    public String getExternalDisplayName() { return externalDisplayName; }
    public void setExternalDisplayName(String externalDisplayName) { this.externalDisplayName = externalDisplayName; }

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
}

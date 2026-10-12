package com.fieldcraft.tenant.data.domain.accounts;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.OffsetDateTime;

/**
 * Maps an {@code invoice} row to its external (QuickBooks) representation for the Data Exchange
 * sync. Mirrors the customer/vendor/project/user/timelog external_map versioning tables.
 *
 * <p>Lifecycle is driven by {@code syncStatus} (AWAITING_APPROVAL / PENDING_PUSH / INVALID /
 * SYNCED / SYNC_FAILED). The current/open version of an invoice mapping is the row whose
 * {@code endorsementExpiryDate} is {@code null} — there is a partial unique index enforcing only
 * one such row per (tenant, service, invoice), so superseding a version closes the prior open row
 * first, same convention as {@code timelog_external_map}.
 */
@Entity
@Table(name = "invoice_external_map")
public class InvoiceExternalMap {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "invoice_id", nullable = false)
    private Long invoiceId;

    @Column(name = "tenant_id", nullable = false)
    private String tenantId;

    @Column(name = "service_id", nullable = false)
    private Long serviceId;

    @Column(name = "external_invoice_id", length = 128)
    private String externalInvoiceId;

    @Column(name = "external_account_id", length = 128)
    private String externalAccountId;

    @Column(name = "sync_status", length = 32, nullable = false)
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

    @Column(name = "reason", columnDefinition = "text")
    private String reason;

    @Column(name = "sync_token", length = 255)
    private String syncToken;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;

    @Column(name = "additional_info", columnDefinition = "text")
    private String additionalInfo;

    @Column(name = "invalid", nullable = false)
    private Boolean invalid = false;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Long getInvoiceId() { return invoiceId; }
    public void setInvoiceId(Long invoiceId) { this.invoiceId = invoiceId; }

    public String getTenantId() { return tenantId; }
    public void setTenantId(String tenantId) { this.tenantId = tenantId; }

    public Long getServiceId() { return serviceId; }
    public void setServiceId(Long serviceId) { this.serviceId = serviceId; }

    public String getExternalInvoiceId() { return externalInvoiceId; }
    public void setExternalInvoiceId(String externalInvoiceId) { this.externalInvoiceId = externalInvoiceId; }

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

    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }

    public String getSyncToken() { return syncToken; }
    public void setSyncToken(String syncToken) { this.syncToken = syncToken; }

    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }

    public String getAdditionalInfo() { return additionalInfo; }
    public void setAdditionalInfo(String additionalInfo) { this.additionalInfo = additionalInfo; }

    public Boolean getInvalid() { return invalid; }
    public void setInvalid(Boolean invalid) { this.invalid = invalid; }
}

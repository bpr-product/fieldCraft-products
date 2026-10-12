package com.fieldcraft.tenant.data.domain.accounts;

import jakarta.persistence.*;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "receipt_external_map")
public class ReceiptExternalMap {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "receipt_id", nullable = false)
    private Receipt receipt;

    // nullable = false is JPA metadata only -- ddl-auto is "none" (Flyway-managed schema), so this
    // does not alter the live column, which stays nullable at the DB level (see
    // V1_2_5_1__alter_tables.sql / V1_2_6_1__alter_tables.sql, which explicitly dropped the NOT NULL
    // constraint). Mandatory receipt_uuid is enforced at the application level only (this entity's
    // @PrePersist/@PreUpdate guard below + the fail-fast check in saveReceiptExternalMap) --
    // deliberately no migration, so no backfill is needed for existing legacy rows.
    @Column(name = "receipt_uuid", nullable = false)
    private UUID receiptUuid;

    @Column(name = "tenant_id", nullable = false, length = 255)
    private String tenantId;

    @Column(name = "service_id", nullable = false)
    private Long serviceId;

    @Column(name = "external_receipt_id", length = 128)
    private String externalReceiptId;

    @Column(name = "external_account_id", length = 128)
    private String externalAccountId;

    @Column(name = "sync_status", length = 32)
    private String syncStatus;

    @Version
    @Column(nullable = false)
    private Integer version;

    @Column(name = "version_effective_date")
    private OffsetDateTime versionEffectiveDate;

    @Column(name = "version_expiry_date")
    private OffsetDateTime versionExpiryDate;

    @Column(name = "endorsement_effective_date")
    private OffsetDateTime endorsementEffectiveDate;

    @Column(name = "endorsement_expiry_date")
    private OffsetDateTime endorsementExpiryDate;

    @Column(name = "reason", length = 255)
    private String reason;

    @Column(name = "sync_token", length = 255)
    private String syncToken;

    // Every receipt_external_map row must reference a valid receipt via a non-null receipt_uuid --
    // fires regardless of which service creates/updates this entity, not just today's single
    // caller (ReceiptService.saveReceiptExternalMap).
    @PrePersist
    @PreUpdate
    private void validateReceiptUuid() {
        if (receiptUuid == null) {
            throw new IllegalStateException(
                    "ReceiptExternalMap.receiptUuid is mandatory and cannot be null (receiptId=" + id + ")");
        }
    }

    public Long getId() {
        return id;
    }

    public Receipt getReceipt() {
        return receipt;
    }

    public void setReceipt(Receipt receipt) {
        this.receipt = receipt;
    }

    public UUID getReceiptUuid() {
        return receiptUuid;
    }

    public void setReceiptUuid(UUID receiptUuid) {
        this.receiptUuid = receiptUuid;
    }

    public String getTenantId() {
        return tenantId;
    }

    public void setTenantId(String tenantId) {
        this.tenantId = tenantId;
    }

    public Long getServiceId() {
        return serviceId;
    }

    public void setServiceId(Long serviceId) {
        this.serviceId = serviceId;
    }

    public String getExternalReceiptId() {
        return externalReceiptId;
    }

    public void setExternalReceiptId(String externalReceiptId) {
        this.externalReceiptId = externalReceiptId;
    }

    public String getExternalAccountId() {
        return externalAccountId;
    }

    public void setExternalAccountId(String externalAccountId) {
        this.externalAccountId = externalAccountId;
    }

    public String getSyncStatus() {
        return syncStatus;
    }

    public void setSyncStatus(String syncStatus) {
        this.syncStatus = syncStatus;
    }

    public Integer getVersion() {
        return version;
    }

    public void setVersion(Integer version) {
        this.version = version;
    }

    public OffsetDateTime getVersionEffectiveDate() {
        return versionEffectiveDate;
    }

    public void setVersionEffectiveDate(OffsetDateTime versionEffectiveDate) {
        this.versionEffectiveDate = versionEffectiveDate;
    }

    public OffsetDateTime getVersionExpiryDate() {
        return versionExpiryDate;
    }

    public void setVersionExpiryDate(OffsetDateTime versionExpiryDate) {
        this.versionExpiryDate = versionExpiryDate;
    }

    public OffsetDateTime getEndorsementEffectiveDate() {
        return endorsementEffectiveDate;
    }

    public void setEndorsementEffectiveDate(OffsetDateTime endorsementEffectiveDate) {
        this.endorsementEffectiveDate = endorsementEffectiveDate;
    }

    public OffsetDateTime getEndorsementExpiryDate() {
        return endorsementExpiryDate;
    }

    public void setEndorsementExpiryDate(OffsetDateTime endorsementExpiryDate) {
        this.endorsementExpiryDate = endorsementExpiryDate;
    }

    public String getReason() {
        return reason;
    }

    public void setReason(String reason) {
        this.reason = reason;
    }

    public String getSyncToken() {
        return syncToken;
    }

    public void setSyncToken(String syncToken) {
        this.syncToken = syncToken;
    }
}

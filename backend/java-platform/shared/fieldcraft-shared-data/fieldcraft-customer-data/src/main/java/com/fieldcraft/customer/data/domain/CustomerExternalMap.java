package com.fieldcraft.customer.data.domain;

import jakarta.persistence.*;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "customer_external_map")
public class CustomerExternalMap {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "customer_id", nullable = false)
    private Customer customer;

    @Column(unique = false)
    private UUID customerUuid;

    @Column(name = "tenant_id", nullable = false, length = 255)
    private String tenantId;

    @Column(name = "service_id", nullable = false)
    private Long serviceId;

    @Column(name = "external_customer_id", length = 128)
    private String externalCustomerId;

    @Column(name = "external_account_id", length = 128)
    private String externalAccountId;

    @Column(name = "sync_status", length = 32)
    private String syncStatus;

    @Version
    @Column(nullable = false)
    private Integer version;

    @Column(name = "version_effective_date", nullable = false)
    private OffsetDateTime versionEffectiveDate;

    @Column(name = "version_expiry_date", nullable = false)
    private OffsetDateTime versionExpiryDate;

    @Column(name = "endorsement_effective_date", nullable = false)
    private OffsetDateTime endorsementEffectiveDate;

    @Column(name = "endorsement_expiry_date", nullable = false)
    private OffsetDateTime endorsementExpiryDate;

    @Column(name="reason", length = 255)
    private String reason;

    @Column(name="sync_token", length = 255)
    private String syncToken;

    public Long getId() {
        return id;
    }

    public Customer getCustomer() {
        return customer;
    }

    public void setCustomer(Customer customer) {
        this.customer = customer;
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

    public String getExternalCustomerId() {
        return externalCustomerId;
    }

    public void setExternalCustomerId(String externalCustomerId) {
        this.externalCustomerId = externalCustomerId;
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

    public void setId(Long id) {
        this.id = id;
    }

    public UUID getCustomerUuid() {
        return customerUuid;
    }

    public void setCustomerUuid(UUID customerUuid) {
        this.customerUuid = customerUuid;
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
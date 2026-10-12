package com.fieldcraft.tenant.data.domain.accounts;

import jakarta.persistence.*;
import java.time.OffsetDateTime;

@Entity
@Table(name = "payment_external_map")
public class PaymentExternalMap {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "customer_id")
    private Long customerId;

    @Column(name = "tenant_id", nullable = false)
    private String tenantId;

    @Column(name = "external_payment_id", length = 128)
    private String externalPaymentId;

    @Column(name = "external_invoice_id", length = 128)
    private String externalInvoiceId;

    @Column(name = "external_account_id", length = 128)
    private String externalAccountId;

    @Column(name = "sync_status", length = 32)
    private String syncStatus;

    // NOT @Version: this column is a business version counter assigned as max+1 by
    // PaymentExternalMapService, not a JPA optimistic lock. With @Version present Hibernate
    // overwrote the assigned value on insert. InvoiceExternalMap models the same column plainly.
    @Column(name = "version", nullable = false)
    private Integer version;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "payment_receives_id")
    private PaymentReceives paymentReceives;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "payment_expense_id")
    private PaymentExpense paymentExpense;

    @Column(name = "service_id")
    private Integer serviceId;

    @Column(name = "endorsement_effective_date", nullable = false)
    private OffsetDateTime endorsementEffectiveDate;

    @Column(name = "endorsement_expiry_date")
    private OffsetDateTime endorsementExpiryDate;

    @Column(name = "version_effective_date")
    private OffsetDateTime versionEffectiveDate;

    @Column(name = "version_expiry_date")
    private OffsetDateTime versionExpiryDate;

    @Column(name = "reason", length = 255)
    private String reason;

    @Column(name = "sync_token")
    private String syncToken;

    @Column(name = "updated_at")
    private OffsetDateTime updatedAt;

    @Column(name = "additional_info", length = 255)
    private String additionalInfo;

    @Column(name = "invalid", nullable = false)
    private Boolean invalid = false;

    /* -------------------- Getters & Setters -------------------- */

    public Long getId() {
        return id;
    }

    public Long getCustomerId() {
        return customerId;
    }

    public void setCustomerId(Long customerId) {
        this.customerId = customerId;
    }

    public String getTenantId() {
        return tenantId;
    }

    public void setTenantId(String tenantId) {
        this.tenantId = tenantId;
    }

    public String getExternalPaymentId() {
        return externalPaymentId;
    }

    public void setExternalPaymentId(String externalPaymentId) {
        this.externalPaymentId = externalPaymentId;
    }

    public String getExternalInvoiceId() {
        return externalInvoiceId;
    }

    public void setExternalInvoiceId(String externalInvoiceId) {
        this.externalInvoiceId = externalInvoiceId;
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

    public PaymentReceives getPaymentReceives() {
        return paymentReceives;
    }

    public void setPaymentReceives(PaymentReceives paymentReceives) {
        this.paymentReceives = paymentReceives;
    }

    public PaymentExpense getPaymentExpense() {
        return paymentExpense;
    }

    public void setPaymentExpense(PaymentExpense paymentExpense) {
        this.paymentExpense = paymentExpense;
    }

    public Integer getServiceId() {
        return serviceId;
    }

    public void setServiceId(Integer serviceId) {
        this.serviceId = serviceId;
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

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }

    public String getAdditionalInfo() {
        return additionalInfo;
    }

    public void setAdditionalInfo(String additionalInfo) {
        this.additionalInfo = additionalInfo;
    }

    public Boolean getInvalid() {
        return invalid;
    }

    public void setInvalid(Boolean invalid) {
        this.invalid = invalid;
    }
}

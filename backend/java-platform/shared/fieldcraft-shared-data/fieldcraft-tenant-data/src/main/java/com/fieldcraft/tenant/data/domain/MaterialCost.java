/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 30-Nov-2023
 * @author      : Ananth Kumar L
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Domain model for MaterialCost
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |     DATE     |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *   1901         |  14-05-2024  |  Shiyam Kumar | Version Column Added
 *
 *
 */

package com.fieldcraft.tenant.data.domain;

import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

import org.springframework.data.annotation.CreatedBy;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

@Entity
@Table(name = "material_cost")
@EntityListeners(AuditingEntityListener.class)
public class MaterialCost {

    @Id
    @Column(nullable = false, updatable = false)
    @SequenceGenerator(
            name = "primary_sequence",
            sequenceName = "primary_sequence",
            allocationSize = 1,
            initialValue = 1000
    )
    @GeneratedValue(
            strategy = GenerationType.IDENTITY

    )
    private Long id;

    @Column(nullable = false, precision = 15, scale = 3)
    private BigDecimal cost;

    @Column(nullable = false, length = 64)
    private String unit;

    @Column(nullable = false, length = 64)
    private String costType;

    //NEED Discussion
    @Column(nullable = false)
    private String grade;

    @Column(nullable = false)
    private OffsetDateTime effectiveStartdate;
    @Column(nullable = false)
    private OffsetDateTime effectiveEnddate;

    @Column(nullable = false)
    private OffsetDateTime versionStartdate;

    @Column(nullable = false)
    private OffsetDateTime versionEnddate;

    @Column(nullable = false)
    private Integer version;

    @Column(nullable = false)
    private String itemCode;
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "vendor_material_map_id")
    private VendorMaterialMap vendorMaterialMap;

    @Column(name = "service_id")
    private Long serviceId;

    // Selling Price, definable either as a flat dollar value or as a percentage markup of `cost`
    // (Purchase Unit Cost). Both nullable -- a material isn't required to have a Selling Price at
    // all. sellingPriceType distinguishes how sellingPriceValue was entered: "flat" means it IS the
    // dollar selling price directly; "percentage" means it's a markup percent, and the resolved
    // dollar amount must be computed as cost + (cost * sellingPriceValue / 100) wherever a real
    // price is needed (percentage mode requires a known cost -- the frontend disables it when
    // Purchase Unit Cost is unknown, see AddMaterial_v2's knowsCost toggle).
    @Column(name = "selling_price_type", length = 16)
    private String sellingPriceType;

    @Column(name = "selling_price_value", precision = 15, scale = 3)
    private BigDecimal sellingPriceValue;

    // A second, independently-entered cost figure alongside `cost` (Purchase Unit Cost) -- e.g.
    // cost per Base/smallest unit inside the Purchase Unit. Nullable; no calculation link to
    // `cost` or Material.conversionFactor -- the value is taken as entered by the user.
    @Column(name = "cost_per_unit", precision = 15, scale = 3)
    private BigDecimal costPerUnit;

    @CreatedBy
    @Column(name = "created_by", nullable = false, updatable = false)
    private String createdBy;

    public Long getId() {
        return id;
    }

    public void setId(final Long id) {
        this.id = id;
    }

    public BigDecimal getCost() {
        return cost;
    }

    public void setCost(final BigDecimal cost) {
        this.cost = cost;
    }

    public String getUnit() {
        return unit;
    }

    public void setUnit(final String unit) {
        this.unit = unit;
    }

    public String getCostType() { return costType; }

    public void setCostType(final String costType) { this.costType = costType;  }


    public OffsetDateTime getEffectiveStartdate() {
        return effectiveStartdate;
    }

    public void setEffectiveStartdate(OffsetDateTime effectiveStartdate) {
        this.effectiveStartdate = effectiveStartdate;
    }

    public OffsetDateTime getEffectiveEnddate() {
        return effectiveEnddate;
    }

    public void setEffectiveEnddate(OffsetDateTime effectiveEnddate) {
        this.effectiveEnddate = effectiveEnddate;
    }

    public OffsetDateTime getVersionStartdate() {
        return versionStartdate;
    }

    public void setVersionStartdate(OffsetDateTime versionStartdate) {
        this.versionStartdate = versionStartdate;
    }

    public OffsetDateTime getVersionEnddate() {
        return versionEnddate;
    }

    public void setVersionEnddate(OffsetDateTime versionEnddate) {
        this.versionEnddate = versionEnddate;
    }

    public VendorMaterialMap getVendorMaterialMap() {
        return vendorMaterialMap;
    }

    public void setVendorMaterialMap(VendorMaterialMap vendorMaterialMap) {
        this.vendorMaterialMap = vendorMaterialMap;
    }

    public String getGrade() {
        return grade;
    }

    public void setGrade(String grade) {
        this.grade = grade;
    }

    public Integer getVersion() {
        return version;
    }

    public void setVersion(Integer version) {
        this.version = version;
    }

    public String getItemCode() {
        return itemCode;
    }

    public void setItemCode(String itemCode) {
        this.itemCode = itemCode;
    }

    public Long getServiceId() {
        return serviceId;
    }

    public void setServiceId(Long serviceId) {
        this.serviceId = serviceId;
    }

    public String getCreatedBy() {
        return createdBy;
    }

    public void setCreatedBy(String createdBy) {
        this.createdBy = createdBy;
    }

    public String getSellingPriceType() {
        return sellingPriceType;
    }

    public void setSellingPriceType(String sellingPriceType) {
        this.sellingPriceType = sellingPriceType;
    }

    public BigDecimal getCostPerUnit() {
        return costPerUnit;
    }

    public void setCostPerUnit(BigDecimal costPerUnit) {
        this.costPerUnit = costPerUnit;
    }

    public BigDecimal getSellingPriceValue() {
        return sellingPriceValue;
    }

    public void setSellingPriceValue(BigDecimal sellingPriceValue) {
        this.sellingPriceValue = sellingPriceValue;
    }
}

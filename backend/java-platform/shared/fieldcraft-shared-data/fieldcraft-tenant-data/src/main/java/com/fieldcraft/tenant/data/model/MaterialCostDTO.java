/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 31-Oct-2023
 * @author      : Mala.k/Pavithra.P
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Domain model for TradeTemplate
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |     DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *   1901         | 14-05-2024  |  Shiyam Kumar | Version Column Added
 *
 *
 */


package com.fieldcraft.tenant.data.model;

import com.fasterxml.jackson.annotation.JsonFormat;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

public class MaterialCostDTO {

    private Long id;

    @NotNull
    @Digits(integer = 15, fraction = 3)
    @JsonFormat(shape = JsonFormat.Shape.STRING)
    @Schema(type = "string", example = "65.008")
    private BigDecimal cost;

    @NotNull
    @Size(max = 64)
    private String unit;

    @NotNull
    @Size(max = 64)
    private String costType;


    private OffsetDateTime effectiveStartdate;

    private OffsetDateTime effectiveEnddate;


    private OffsetDateTime versionStartdate;


    private OffsetDateTime versionEnddate;

    @Size(max = 16)
    private Long material;
    private Long vendorMaterialMapId;
    private String grade;
    private Integer version;
    private String itemCode;

    private Long serviceId;
    private String createdBy;

    // "flat" (sellingPriceValue IS the dollar selling price) or "percentage" (sellingPriceValue is
    // a markup percent of cost). Both nullable -- Selling Price is optional. See MaterialCost's
    // own field comment for the resolution rule.
    private String sellingPriceType;
    private BigDecimal sellingPriceValue;

    // A second, independently-entered cost figure alongside `cost` -- no calculation link.
    private BigDecimal costPerUnit;

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

    public void setEffectiveStartdate(final OffsetDateTime effectiveStartdate) {
        this.effectiveStartdate = effectiveStartdate;
    }

    public OffsetDateTime getEffectiveEnddate() {
        return effectiveEnddate;
    }

    public void setEffectiveEnddate(final OffsetDateTime effectiveEnddate) {
        this.effectiveEnddate = effectiveEnddate;
    }

    public OffsetDateTime getVersionStartdate() {
        return versionStartdate;
    }

    public void setVersionStartdate(final OffsetDateTime versionStartdate) {
        this.versionStartdate = versionStartdate;
    }

    public OffsetDateTime getVersionEnddate() {
        return versionEnddate;
    }

    public void setVersionEnddate(final OffsetDateTime versionEnddate) {
        this.versionEnddate = versionEnddate;
    }

    public Long getMaterial() {
        return material;
    }

    public void setMaterial(final Long material) {
        this.material = material;
    }

    public String getGrade() {
        return grade;
    }

    public void setGrade(String grade) {
        this.grade = grade;
    }

    public Long getVendorMaterialMapId() {
        return vendorMaterialMapId;
    }

    public void setVendorMaterialMapId(Long vendorMaterialMapId) {
        this.vendorMaterialMapId = vendorMaterialMapId;
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

    public BigDecimal getSellingPriceValue() {
        return sellingPriceValue;
    }

    public void setSellingPriceValue(BigDecimal sellingPriceValue) {
        this.sellingPriceValue = sellingPriceValue;
    }

    public BigDecimal getCostPerUnit() {
        return costPerUnit;
    }

    public void setCostPerUnit(BigDecimal costPerUnit) {
        this.costPerUnit = costPerUnit;
    }
}

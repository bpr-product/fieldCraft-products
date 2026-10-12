/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 18-Apr-2025
 * @author      : Kanimozhi.S
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Domain model for Asset Service RequestDTO
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |     DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *     4216       |18-04-2025   |  Kanimozhi.S  |Create a domain class for Asset Service RequestDTO
 *
 */
package com.fieldcraft.customer.data.model;



import java.time.OffsetDateTime;
import java.util.UUID;

public class AssetServiceRequestDTO {

    private Long id;

    private Long asset;

    private UUID requestUid;

    private String requestType;

    private String status;

    private String requestBy;

    private OffsetDateTime createdAt;

    private OffsetDateTime updatedAt;

    private Boolean invalid;

    private Integer version = 0;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Long getAsset() {
        return asset;
    }

    public void setAsset(Long asset) {
        this.asset = asset;
    }

    public UUID getRequestUid() {
        return requestUid;
    }

    public void setRequestUid(UUID requestUid) {
        this.requestUid = requestUid;
    }

    public String getRequestType() {
        return requestType;
    }

    public void setRequestType(String requestType) {
        this.requestType = requestType;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getRequestBy() {
        return requestBy;
    }

    public void setRequestBy(String requestBy) {
        this.requestBy = requestBy;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }

    public Boolean getInvalid() {
        return invalid;
    }

    public void setInvalid(Boolean invalid) {
        this.invalid = invalid;
    }

    public Integer getVersion() {
        return version;
    }

    public void setVersion(Integer version) {
        this.version = version;
    }
}

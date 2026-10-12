/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 18-Apr-2025
 * @author      : Kanimozhi.S
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Domain model for Service Subscribers DTO
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |     DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *     4216       |18-04-2025   |  Kanimozhi.S  |Create a domain class for Service Subscribers DTO
 *
 */
package com.fieldcraft.external.data.model;
import java.time.OffsetDateTime;
import java.util.UUID;

public class ServiceSubscriberDTO {

    private Long id;

    private String subscriberId;
    private Long serviceMaster;

    private String createdBy;

    private OffsetDateTime createdAt;

    private OffsetDateTime updatedAt;

    private Boolean invalid;

    private Integer version = 0;

    private String subscriberType;

    private String status;
    private  String additionalInfo;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getSubscriberId() {
        return subscriberId;
    }

    public void setSubscriberId(String subscriberId) {
        this.subscriberId = subscriberId;
    }

    public Long getServiceMaster() {
        return serviceMaster;
    }

    public void setServiceMaster(Long serviceMaster) {
        this.serviceMaster = serviceMaster;
    }

    public String getCreatedBy() {
        return createdBy;
    }

    public void setCreatedBy(String createdBy) {
        this.createdBy = createdBy;
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

    public String getSubscriberType() {
        return subscriberType;
    }

    public void setSubscriberType(String subscriberType) {
        this.subscriberType = subscriberType;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getAdditionalInfo() {
        return additionalInfo;
    }

    public void setAdditionalInfo(String additionalInfo) {
        this.additionalInfo = additionalInfo;
    }
}

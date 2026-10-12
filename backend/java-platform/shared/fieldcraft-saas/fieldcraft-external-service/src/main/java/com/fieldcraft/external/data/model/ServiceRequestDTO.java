/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 18-Apr-2025
 * @author      : Kanimozhi.S
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Domain model for Service  Request  DTO
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |     DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *     4216       |18-04-2025   |  Kanimozhi.S  |Create a domain class for Service Request DTO
 *
 */

package com.fieldcraft.external.data.model;

import org.antlr.v4.runtime.misc.NotNull;

import java.time.OffsetDateTime;
import java.util.Map;

public class ServiceRequestDTO {
    private Long id;

    private String response;

   @NotNull
    private Long serviceSubscriber;

    private String type;

    private String status;

    private String statusHistory;

    private String requestedBy;

    private OffsetDateTime createdAt;

    private OffsetDateTime updatedAt;

    private Boolean invalid;

    private Integer version = 0;

    private String requestedByType;

//    private String request;

    private Map<String, Object> request;
    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getResponse() {
        return response;
    }

    public void setResponse(String response) {
        this.response = response;
    }


    public Long getServiceSubscriber() {
        return serviceSubscriber;
    }

    public void setServiceSubscriber(Long serviceSubscriber) {
        this.serviceSubscriber = serviceSubscriber;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getStatusHistory() {
        return statusHistory;
    }

    public void setStatusHistory(String statusHistory) {
        this.statusHistory = statusHistory;
    }

    public String getRequestedBy() {
        return requestedBy;
    }

    public void setRequestedBy(String requestedBy) {
        this.requestedBy = requestedBy;
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

    public String getRequestedByType() {
        return requestedByType;
    }

    public void setRequestedByType(String requestedByType) {
        this.requestedByType = requestedByType;
    }

    public Map<String, Object> getRequest() {
        return request;
    }

    public void setRequest(Map<String, Object> request) {
        this.request = request;
    }
}

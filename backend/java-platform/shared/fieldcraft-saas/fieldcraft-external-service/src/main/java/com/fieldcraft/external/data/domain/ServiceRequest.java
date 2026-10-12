/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 18-Apr-2025
 * @author      : Kanimozhi.S
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Domain model for Service Request
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |     DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *     4216       |18-04-2025   |  Kanimozhi.S  |Create a domain class for Service Request
 *
 */
package com.fieldcraft.external.data.domain;


import jakarta.persistence.*;
import org.hibernate.annotations.UuidGenerator;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "service_request")
public class ServiceRequest {
    @Id
    @Column(nullable = false, updatable = false)
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(columnDefinition = "text")
    private String response;

    // Hibernate 6.4+ changed GenerationType.UUID behavior — @UuidGenerator is the stable, explicit replacement.
    @UuidGenerator
    private UUID requestUid;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "subscriber_id", nullable = false)
    private ServiceSubscriber serviceSubscriber;


    @Column
    private String type;

    @Column(nullable = false)
    private String status;

    @Column(columnDefinition = "text")
    private String statusHistory;

    @Column(nullable = false)
    private String requestedBy;

    @Column
    private OffsetDateTime createdAt;

    @Column
    private OffsetDateTime updatedAt;

    @Column
    private Boolean invalid;

    @Column
    private Integer schemaVersion = 0;

    @Column
    private Integer version = 0;

    @Column(nullable = false)
    private String requestedByType;

    @Column
    private String request;


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

    public UUID getRequestUid() {
        return requestUid;
    }

    public void setRequestUid(UUID requestUid) {
        this.requestUid = requestUid;
    }

    public ServiceSubscriber getServiceSubscriber() {
        return serviceSubscriber;
    }

    public void setServiceSubscriber(ServiceSubscriber serviceSubscriber) {
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

    public Integer getSchemaVersion() {
        return schemaVersion;
    }

    public void setSchemaVersion(Integer schemaVersion) {
        this.schemaVersion = schemaVersion;
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

    public String getRequest() {
        return request;
    }

    public void setRequest(String request) {
        this.request = request;
    }
}
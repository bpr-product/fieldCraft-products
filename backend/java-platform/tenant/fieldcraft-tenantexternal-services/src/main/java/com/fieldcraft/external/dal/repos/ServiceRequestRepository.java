
/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 15-MAY-2025
 * @author      : Kanimozhi
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Rest Api for Service Request Repository
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *
 *
 *
 */

package com.fieldcraft.external.dal.repos;

import com.fieldcraft.external.data.domain.ServiceRequest;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface ServiceRequestRepository extends JpaRepository<ServiceRequest, Long>, JpaSpecificationExecutor<ServiceRequest> {


    @Query("SELECT sr FROM ServiceRequest sr WHERE sr.requestUid IN :requestUids")
    List<ServiceRequest> getRequest(@Param("requestUids") List<UUID> requestUids);

    @Query("SELECT sr FROM ServiceRequest sr WHERE sr.request LIKE %:assetId% AND sr.serviceSubscriber.id = :subscriberId")
    List<ServiceRequest> findByAssetIdAndSubscriberId(@Param("assetId") String assetId, @Param("subscriberId") Long subscriberId);

    @Query("SELECT sr FROM ServiceRequest sr WHERE sr.serviceSubscriber = :serviceSubscriber")
    Optional<ServiceRequest> findByServiceSubscriberId(@Param("serviceSubscriber") Long serviceSubscriber);
}
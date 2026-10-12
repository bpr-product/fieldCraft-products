
/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 15-MAY-2025
 * @author      : Kanimozhi
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Rest Api for Service Subscriber Repository
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

import com.fieldcraft.external.data.domain.ServiceMaster;
import com.fieldcraft.external.data.domain.ServiceSubscriber;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface ServiceSubscriberRepository extends JpaRepository<ServiceSubscriber, Long>, JpaSpecificationExecutor<ServiceSubscriber> {

    @Query("SELECT ss FROM ServiceSubscriber ss WHERE ss.serviceMaster = :serviceMaster AND ss.subscriberId = :subscriberId")
    List<ServiceSubscriber> findByServiceMasterAndSubscriberId(@Param("serviceMaster") ServiceMaster serviceMaster,
                                                               @Param("subscriberId") String subscriberId);


    @Query("SELECT ss FROM ServiceSubscriber ss WHERE ss.subscriberId = :subscriberId AND ss.invalid = false")
    List<ServiceSubscriber> findSubscriberBySubscriberId(@Param("subscriberId") String subscriberId);

    @Query("SELECT ss FROM ServiceSubscriber ss WHERE ss.subscriberId = :subscriberId")
    List<ServiceSubscriber> findBySubscriberId(@Param("subscriberId") String subscriberId);

    @Query("SELECT s.additionalInfo FROM ServiceSubscriber s " +
            "WHERE s.serviceMaster.name = :serviceName AND s.subscriberId = :tenantId")
    Optional<String> findProjectHubCredentials(@Param("serviceName") String serviceName,
                                               @Param("tenantId") String tenantId);


    @Query("SELECT ss FROM ServiceSubscriber ss WHERE ss.subscriberId = :subscriberId AND ss.serviceMaster.id =:serviceId AND invalid = false AND ss.status =:status")
    List<ServiceSubscriber>  findProjectHubCredentialsforTenant(@Param("subscriberId") String subscriberId,@Param("serviceId") Long serviceId,@Param("status") String status);




}

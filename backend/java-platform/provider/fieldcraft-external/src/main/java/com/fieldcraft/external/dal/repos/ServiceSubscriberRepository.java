package com.fieldcraft.external.dal.repos;

import com.fieldcraft.external.data.domain.ServiceMaster;
import com.fieldcraft.external.data.domain.ServiceSubscriber;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;


@Repository
public interface ServiceSubscriberRepository extends JpaRepository<ServiceSubscriber, Long>, JpaSpecificationExecutor<ServiceSubscriber> {

    @Query("SELECT ss FROM ServiceSubscriber ss WHERE ss.serviceMaster = :serviceMaster AND ss.subscriberId = :subscriberId")
    List<ServiceSubscriber> findByServiceMasterAndSubscriberId(@Param("serviceMaster") ServiceMaster serviceMaster,
                                                               @Param("subscriberId") String subscriberId);

    @Query("SELECT ss FROM ServiceSubscriber ss WHERE ss.subscriberId = :subscriberId AND ss.invalid = false")
    List<ServiceSubscriber> findSubscriberBySubscriberId(@Param("subscriberId") String subscriberId);

}
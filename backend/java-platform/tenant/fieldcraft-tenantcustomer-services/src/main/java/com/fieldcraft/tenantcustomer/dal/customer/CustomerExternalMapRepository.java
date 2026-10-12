package com.fieldcraft.tenantcustomer.dal.customer;

import com.fieldcraft.customer.data.domain.CustomerExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface CustomerExternalMapRepository extends JpaRepository<CustomerExternalMap, Long> {
    @Query("""
        SELECT cem
        FROM CustomerExternalMap cem
        WHERE cem.customer.customerUuid = :customerUuid
        """)
    List<CustomerExternalMap> findByCustomerUuid(@Param("customerUuid") UUID customerUuid);
}
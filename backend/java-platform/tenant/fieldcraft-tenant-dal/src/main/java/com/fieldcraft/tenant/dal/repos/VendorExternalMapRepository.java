package com.fieldcraft.tenant.dal.repos;

import com.fieldcraft.tenant.data.domain.VendorExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface VendorExternalMapRepository extends JpaRepository<VendorExternalMap, Long> {

    @Query("""
        SELECT vem
        FROM VendorExternalMap vem
        WHERE vem.vendor.vendorUid = :vendorUuid
        """)
    List<VendorExternalMap> findByVendorUuid(@Param("vendorUuid") UUID vendorUuid);

    @Query("""
    select coalesce(max(v.version), 0)
    from VendorExternalMap v
    where v.vendorUuid = :vendorUuid
      and v.serviceId = :serviceId
""")
    Integer findMaxVersionByVendorUuidAndServiceId(UUID vendorUuid, Long serviceId);
}
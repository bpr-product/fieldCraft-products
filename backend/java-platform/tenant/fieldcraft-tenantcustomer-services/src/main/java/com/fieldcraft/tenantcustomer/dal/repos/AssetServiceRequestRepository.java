package com.fieldcraft.tenantcustomer.dal.repos;

import com.fieldcraft.customer.data.domain.AssetServiceRequest;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface AssetServiceRequestRepository extends JpaRepository<AssetServiceRequest, Long> {

    @Query("SELECT ar FROM AssetServiceRequest ar WHERE ar.asset.id IN :assetIds")
    List<AssetServiceRequest> findByAssetIds(@Param("assetIds") List<Long> assetIds);

    @Query("SELECT ar FROM AssetServiceRequest ar WHERE ar.requestUid= :requestUid")
    Optional<AssetServiceRequest> findByRequestUid(@Param("requestUid") UUID requestUid);

}
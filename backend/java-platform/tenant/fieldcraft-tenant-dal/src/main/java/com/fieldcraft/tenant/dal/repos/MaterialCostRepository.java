/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 30-Nov-2023
 * @author      : Ananth Kumar L
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Repository for MaterialCost
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


package com.fieldcraft.tenant.dal.repos;

import com.fieldcraft.tenant.data.domain.Material;
import com.fieldcraft.tenant.data.domain.MaterialCost;
import com.fieldcraft.tenant.data.model.MaterialDTO;
import com.fieldcraft.tenant.data.model.MaterialInfo;
import com.fieldcraft.tenant.data.model.ToolInfo;
import jakarta.persistence.LockModeType;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;

@Repository
public interface MaterialCostRepository extends JpaRepository<MaterialCost, Long>, JpaSpecificationExecutor<MaterialCost> {

    @Query("SELECT mc FROM MaterialCost mc WHERE mc.vendorMaterialMap.id = :vendorMaterialMapId")
    public MaterialCost findMaterialCostByVendorMapId(@Param("vendorMaterialMapId") Long vendorMaterialMapId);

    @Query("SELECT mc FROM MaterialCost mc WHERE mc.vendorMaterialMap.id = :vendorMaterialMapId " +
            "AND mc.versionStartdate <= :currentDate AND mc.versionEnddate >= :currentDate " +
            "AND mc.effectiveStartdate <= :currentDate AND mc.effectiveEnddate >= :currentDate")
    public List<MaterialCost> findByVendorMapId(@Param("vendorMaterialMapId") Long vendorMaterialMapId,
                                                @Param("currentDate") OffsetDateTime currentDate);

    @Query("SELECT mc from MaterialCost mc "+
            "WHERE mc.versionStartdate <= :versionEffectiveDate " +
            "AND mc.effectiveStartdate <= :versionEffectiveDate " +
            "AND mc.versionEnddate >= :versionEffectiveDate " +
            "AND mc.effectiveEnddate >= :versionEffectiveDate " +
            "AND mc.vendorMaterialMap.Id = :vendorMaterialMapId "+
            "AND mc.grade = :grade "+
            "ORDER BY mc.versionEnddate DESC " +
            "LIMIT 1")
    public MaterialCost findMaterialCostByEffectiveDate(@Param("versionEffectiveDate")OffsetDateTime versionEffectiveDate,
                                                        @Param("vendorMaterialMapId") Long vendorMaterialMapId, @Param("grade") String grade);

    // PR 8110 review: insertMaterialCost's read-check-write sequence (isMaterialCostAvailable,
    // findMaterialCostByEffectiveDate, then two saves) had no lock, so two concurrent bill
    // approvals reconciling the same vendorMaterialMapId+grade could both read "no current row",
    // both close the same previous row, and both insert an overlapping "current" row. Same
    // PESSIMISTIC_WRITE-for-the-duration-of-the-transaction pattern as
    // TenantConfigurationRepository.findByKeyForUpdate/StockPoNumberService: locks whatever
    // "current" row already exists for this vendorMaterialMapId+grade so a second concurrent
    // caller blocks until the first's transaction commits (and then correctly re-reads a
    // now-closed previous row instead of racing past it).
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT mc from MaterialCost mc "+
            "WHERE mc.versionStartdate <= :versionEffectiveDate " +
            "AND mc.effectiveStartdate <= :versionEffectiveDate " +
            "AND mc.versionEnddate >= :versionEffectiveDate " +
            "AND mc.effectiveEnddate >= :versionEffectiveDate " +
            "AND mc.vendorMaterialMap.Id = :vendorMaterialMapId "+
            "AND mc.grade = :grade "+
            "ORDER BY mc.versionEnddate DESC " +
            "LIMIT 1")
    public MaterialCost findMaterialCostByEffectiveDateForUpdate(@Param("versionEffectiveDate")OffsetDateTime versionEffectiveDate,
                                                        @Param("vendorMaterialMapId") Long vendorMaterialMapId, @Param("grade") String grade);

    @Transactional
    @Modifying
    @Query("UPDATE MaterialCost mc "+
            "SET mc.effectiveEnddate = :newEffectiveDate "+
            "WHERE mc.id = :id")
    void updateMaterialCost(@Param("id") Long id, @Param("newEffectiveDate")OffsetDateTime newEffectiveDate);

    @Query("SELECT " +
            "mc.cost as cost, mc.versionStartdate as versionStartDate, " +
            "mc.id as id, mc.itemCode as itemCode " +
            "FROM MaterialCost mc " +
            "WHERE mc.vendorMaterialMap.id = :vendorMaterialMapId AND mc.versionStartdate <= :selectedDate AND mc.versionEnddate >= :selectedDate " +
            "AND mc.effectiveStartdate <= :selectedDate AND mc.effectiveEnddate >= :selectedDate ORDER BY mc.versionEnddate DESC LIMIT 1"
    )
    MaterialInfo findMaterialCostBySelectedDateAndMapId(@Param("selectedDate") OffsetDateTime selectedDate,
                                                        @Param("vendorMaterialMapId") Long vendorMaterialMapId);
    @Query("SELECT COUNT(*) " +
            "FROM MaterialCost mc " +
            "WHERE mc.vendorMaterialMap.id = :vendorMaterialMapId AND mc.versionStartdate <= :selectedDate AND mc.versionEnddate >= :selectedDate " +
            "AND mc.effectiveStartdate <= :selectedDate AND mc.effectiveEnddate >= :selectedDate"
    )
    Integer findCountBySelectedDateAndMapId(@Param("selectedDate") OffsetDateTime selectedDate,
                                            @Param("vendorMaterialMapId") Long vendorMaterialMapId);


    @Query("SELECT mc.itemCode FROM MaterialCost mc WHERE mc.vendorMaterialMap.id = :vendorMaterialMapId ORDER BY mc.id DESC LIMIT 1")
    public String findItemCode(@Param("vendorMaterialMapId") Long vendorMaterialMapId);

    @Query("SELECT CASE WHEN COUNT(mc) > 0 THEN true ELSE false END FROM MaterialCost mc WHERE mc.vendorMaterialMap.id = :vendorMaterialMapId AND mc.grade = :grade AND mc.versionStartdate = :versionStartDate")
    public boolean isMaterialCostAvailable(@Param("vendorMaterialMapId") Long vendorMaterialMapId, @Param("versionStartDate") OffsetDateTime versionStartDate,
                                           @Param("grade") String grade);


    @Query("""
    SELECT mc.vendorMaterialMap.material
    FROM MaterialCost mc
    WHERE mc.itemCode = :itemCode
      AND mc.versionStartdate = (
          SELECT MAX(m.versionStartdate)
          FROM MaterialCost m
          WHERE m.itemCode = :itemCode
      )
""")
    Material findMaterialByItemCode(@Param("itemCode") String itemCode);


    @Query("""
    SELECT mc.vendorMaterialMap.id
    FROM MaterialCost mc
    WHERE mc.itemCode = :itemCode
      AND mc.versionStartdate = (
          SELECT MAX(m.versionStartdate)
          FROM MaterialCost m
          WHERE m.itemCode = :itemCode
      )
""")
    Long findVendorMaterialMapIdByItemCode(@Param("itemCode") String itemCode);

    // Most recent MaterialCost row for a given Material, independent of which vendor it's mapped
    // through -- for the Add Material edit-form prefill (GET /v2/materials/{id}), which knows only
    // the material's own id at that point, not a specific vendorMaterialMapId. A material can in
    // principle have cost rows through more than one VendorMaterialMap; "most recently created"
    // (mc.id DESC, same tie-break convention as findItemCode above) is the best single answer for
    // a single-value prefill, not a claim that only one cost record could ever exist.
    @Query("SELECT mc FROM MaterialCost mc WHERE mc.vendorMaterialMap.material.id = :materialId " +
            "ORDER BY mc.id DESC LIMIT 1")
    MaterialCost findMostRecentByMaterialId(@Param("materialId") Long materialId);

    // PR 8110 review: findMaterialCostByVendorMapId (above) has no date filter and no LIMIT --
    // Spring Data's single-result contract throws IncorrectResultSizeDataAccessException the
    // moment a vendorMaterialMapId has 2+ rows, which insertMaterialCost's own versioning produces
    // on every price change. Same LIMIT-1/most-recent-by-id convention as findMostRecentByMaterialId
    // just above (this call site has no "as of" date to filter by -- it wants whatever is current).
    @Query("SELECT mc FROM MaterialCost mc WHERE mc.vendorMaterialMap.id = :vendorMaterialMapId " +
            "ORDER BY mc.id DESC LIMIT 1")
    MaterialCost findMostRecentByVendorMapId(@Param("vendorMaterialMapId") Long vendorMaterialMapId);

}



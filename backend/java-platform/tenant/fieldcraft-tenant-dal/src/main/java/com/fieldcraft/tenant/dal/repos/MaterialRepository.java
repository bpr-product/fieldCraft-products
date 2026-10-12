/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 06-Nov-2023
 * @author      : Mala.K/Pavithra.P
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Data access object for Material
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |     DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *    1839        | 08-May-2023 | shiyam Kumar  | Bom Table Entry Issue Fixed
 *    2399        | 06-Aug-2024 | shiyam kumar  | change searchMaterials query for pagination
 *    28503       | 31-Aug-2026 | Surya J       | AB#28503: added baseUnit/conversionFactor to
 *                |             |               | findMaterialByNameAndGrade so the Create BOM ->
 *                |             |               | Vendor dialog has data to correct Purchase-Unit
 *                |             |               | cost to per-Base-Unit
 *
 */
package com.fieldcraft.tenant.dal.repos;

import com.fieldcraft.tenant.data.domain.Material;
import com.fieldcraft.tenant.data.model.MaterialDTO;
import com.fieldcraft.tenant.data.model.MaterialInfo;
import com.fieldcraft.tenant.data.model.ToolInfo;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

@Repository
public interface MaterialRepository extends JpaRepository<Material, Long>, JpaSpecificationExecutor<Material> {

    @Query("SELECT m.name FROM Material m WHERE m.id = :id")
    String findItem(@Param("id") Long id);

    @Query(value = "SELECT DISTINCT material.name FROM Material material")
    List<String> findMaterialName();

    @Query("SELECT COUNT(*) " +
            "FROM Material material " +
            "INNER JOIN VendorMaterialMap vm ON vm.material.id = material.id " +
            "INNER JOIN MaterialCost mc ON mc.vendorMaterialMap.id = vm.id " +
            "WHERE mc.versionStartdate <= :currentDate AND mc.versionEnddate >= :currentDate AND mc.effectiveStartdate <= :currentDate AND mc.effectiveEnddate >= :currentDate " +
            "AND (:name IS NULL OR material.name LIKE %:name%) " +
            "AND (:trade IS NULL OR material.tags LIKE %:trade%) " +
            "AND (:grade IS NULL OR material.grade = :grade) ")
    Integer countTotalMaterials(@Param("name") String name, @Param("trade") String trade, @Param("grade") String grade, @Param("currentDate") OffsetDateTime currentDate);

    @Query("SELECT mc.cost as cost, material.id as id, mc.versionStartdate as versionStartdate, material.name as name,material.allowDecimal as allowDecimal, material.baseUnitId.name as baseUnit , material.conversionFactor as conversionFactor ,material.manufacturer as manufacturer, mc.itemCode as itemCode, " +
            "material.tags as tags, material.unit as unit,material.materialUid as materialUid ,material.grade as grade, vm.id as vendorMaterialMapId, mc.id as materialCostId, vm.vendor.companyName as companyName, " +
            "mc.sellingPriceType as sellingPriceType, mc.sellingPriceValue as sellingPriceValue " +
            "FROM Material material " +
            "INNER JOIN VendorMaterialMap vm ON vm.material.id = material.id " +
            "INNER JOIN MaterialCost mc ON mc.vendorMaterialMap.id = vm.id " +
            "WHERE mc.versionStartdate <= :currentDate AND mc.versionEnddate >= :currentDate " +
            "AND mc.effectiveStartdate <= :currentDate AND mc.effectiveEnddate >= :currentDate " +
            "AND (" +
            "(:name IS NULL OR REPLACE(LOWER(material.name), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(vm.vendor.companyName), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.tags), ' ', '') LIKE %:name%  OR " +
            " STR(mc.versionStartdate) LIKE :name OR " +  // if STR doesn't work, we can fix for your DB
            " REPLACE(LOWER(material.manufacturer), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(mc.itemCode), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.grade), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.tags), ' ', '') LIKE %:name% ) " +
            ") " +
            "AND (:trade IS NULL OR material.tags LIKE %:trade%) " +
            "AND (:grade IS NULL OR material.grade = %:grade%)")
    List<MaterialInfo> searchMaterials(
            @Param("name") String name,
            @Param("trade") String trade,
            @Param("grade") String grade,
            @Param("currentDate") OffsetDateTime currentDate,
            Pageable pageable);

    // Same predicate as searchMaterials, plus an EXISTS against StockItem so only materials with
    // a valid (invalid = false) Stock record are returned, and an optional vendorId filter so
    // Inventory Bill Review can scope candidates to materials mapped to the bill's selected
    // vendor -- both conditions required together (a material must have stock AND be mapped to
    // that vendor), not either alone.
    @Query("SELECT mc.cost as cost, material.id as id, mc.versionStartdate as versionStartdate, material.name as name,material.allowDecimal as allowDecimal, material.baseUnitId.name as baseUnit , material.conversionFactor as conversionFactor ,material.manufacturer as manufacturer, mc.itemCode as itemCode, " +
            "material.tags as tags, material.unit as unit,material.materialUid as materialUid ,material.grade as grade, vm.id as vendorMaterialMapId, mc.id as materialCostId, vm.vendor.companyName as companyName, " +
            "mc.sellingPriceType as sellingPriceType, mc.sellingPriceValue as sellingPriceValue " +
            "FROM Material material " +
            "INNER JOIN VendorMaterialMap vm ON vm.material.id = material.id " +
            "INNER JOIN MaterialCost mc ON mc.vendorMaterialMap.id = vm.id " +
            "WHERE mc.versionStartdate <= :currentDate AND mc.versionEnddate >= :currentDate " +
            "AND mc.effectiveStartdate <= :currentDate AND mc.effectiveEnddate >= :currentDate " +
            "AND EXISTS (SELECT 1 FROM StockItem s WHERE s.invalid = false AND s.material.id = material.id) " +
            "AND (:vendorId IS NULL OR vm.vendor.id = :vendorId) " +
            "AND (" +
            "(:name IS NULL OR REPLACE(LOWER(material.name), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(vm.vendor.companyName), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.tags), ' ', '') LIKE %:name%  OR " +
            " STR(mc.versionStartdate) LIKE :name OR " +
            " REPLACE(LOWER(material.manufacturer), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(mc.itemCode), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.grade), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.tags), ' ', '') LIKE %:name% ) " +
            ") " +
            "AND (:trade IS NULL OR material.tags LIKE %:trade%) " +
            "AND (:grade IS NULL OR material.grade = :grade)")
    List<MaterialInfo> searchMaterialsInStockOnly(
            @Param("name") String name,
            @Param("trade") String trade,
            @Param("grade") String grade,
            @Param("currentDate") OffsetDateTime currentDate,
            @Param("vendorId") Long vendorId,
            Pageable pageable);

    // Same predicate as searchMaterials, but scoped to a single vendor's mapped materials --
    // no in-stock requirement (unlike searchMaterialsInStockOnly), used by Review Bill's
    // Reconcile field so it can show a vendor's full material catalog regardless of stock state.
    @Query("SELECT mc.cost as cost, material.id as id, mc.versionStartdate as versionStartdate, material.name as name,material.allowDecimal as allowDecimal, material.baseUnitId.name as baseUnit , material.conversionFactor as conversionFactor ,material.manufacturer as manufacturer, mc.itemCode as itemCode, " +
            "material.tags as tags, material.unit as unit,material.materialUid as materialUid ,material.grade as grade, vm.id as vendorMaterialMapId, mc.id as materialCostId, vm.vendor.companyName as companyName, " +
            "mc.sellingPriceType as sellingPriceType, mc.sellingPriceValue as sellingPriceValue " +
            "FROM Material material " +
            "INNER JOIN VendorMaterialMap vm ON vm.material.id = material.id " +
            "INNER JOIN MaterialCost mc ON mc.vendorMaterialMap.id = vm.id " +
            "WHERE mc.versionStartdate <= :currentDate AND mc.versionEnddate >= :currentDate " +
            "AND mc.effectiveStartdate <= :currentDate AND mc.effectiveEnddate >= :currentDate " +
            "AND vm.vendor.id = :vendorId " +
            "AND (" +
            "(:name IS NULL OR REPLACE(LOWER(material.name), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(vm.vendor.companyName), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.tags), ' ', '') LIKE %:name%  OR " +
            " STR(mc.versionStartdate) LIKE :name OR " +
            " REPLACE(LOWER(material.manufacturer), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(mc.itemCode), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.grade), ' ', '') LIKE %:name%  OR " +
            " REPLACE(LOWER(material.tags), ' ', '') LIKE %:name% ) " +
            ") " +
            "AND (:trade IS NULL OR material.tags LIKE %:trade%) " +
            "AND (:grade IS NULL OR material.grade = :grade)")
    List<MaterialInfo> searchMaterialsByVendor(
            @Param("name") String name,
            @Param("trade") String trade,
            @Param("grade") String grade,
            @Param("currentDate") OffsetDateTime currentDate,
            @Param("vendorId") Long vendorId,
            Pageable pageable);

    @Query("SELECT DISTINCT m.tags FROM Material m")
    List<String> getTags();

    @Query("SELECT m FROM Material m WHERE TRIM(REPLACE(lower(m.name), '\n', ' '))  = :name AND m.grade = :grade")
    Material findMaterialNameAndGrade(@Param("name") String name, @Param("grade") String grade);

    @Query("SELECT m FROM Material m WHERE m.id = :id")
    Material findMaterialById(@Param("id") Long id);


    @Query("SELECT m FROM Material m WHERE m.materialUid = :uuid")
    Material findMaterialByMaterialUid(@Param("uuid") UUID uuid);

    @Query("SELECT CASE WHEN 'MAX' = :level THEN MAX(mc.cost) ELSE CASE WHEN 'MIN' = :level " +
            "THEN MIN(mc.cost) ELSE AVG(mc.cost) END END as cost, material.id as id, material.name as name, material.manufacturer as manufacturer, " +
            "material.tags as tags, material.unit as unit, material.grade as grade " +
            "FROM Material material " +
            "INNER JOIN VendorMaterialMap vm ON vm.material.id = material.id " +
            "INNER JOIN MaterialCost mc ON mc.vendorMaterialMap.id = vm.id " +
            "WHERE TRIM(REPLACE(lower(material.name), '\n', ' ')) IN (:materialNames) AND mc.versionStartdate <= :date AND mc.versionEnddate >= :date " +
            "AND mc.effectiveStartdate <= :date AND mc.effectiveEnddate >= :date " +
            "GROUP BY material.grade, material.name, material.id")
    List<MaterialInfo> findMaterialsByNames(@Param("materialNames") List<String> materialNames,
                                            @Param("date") OffsetDateTime date, @Param("level") String level);


    @Query("SELECT m FROM Material m WHERE lower(m.tags) LIKE %:trade%")
    List<Material> findNameByTradeName(@Param("trade") String trade);


    // AB#28503: baseUnit/conversionFactor are selected so the service layer can derive the
    // true per-Base-Unit rate from mc.cost (per Purchase Unit, e.g. per Bundle) -- same fields
    // the sibling searchMaterials/searchMaterialsInStockOnly/searchMaterialsByVendor queries
    // already select. Before this, AddMaterial.tsx's "Create BOM -> Vendor" dialog (which calls
    // this query via MaterialService.getmaterialsByNameAndGrade) had no conversion data to
    // correct against, so it displayed mc.cost/m.unit -- the raw Purchase Unit cost -- verbatim.
    // AB#30036: mc.sellingPriceType/mc.sellingPriceValue are selected so
    // MaterialService.applyBaseUnitCostCorrection can resolve the Selling Price per Base Unit
    // (SellingPriceCalculator) -- the figure this dialog is actually supposed to show/save,
    // same as a Stock-sourced BOM row -- instead of the raw vendor purchase cost.
    @Query("SELECT m.id as id, " +
            "m.name as name,material.materialUid as materialUid, m.grade as grade, m.unit as unit, m.manufacturer as manufacturer, " +
            "mc.cost as cost, m.baseUnitId.name as baseUnit, m.conversionFactor as conversionFactor, " +
            "m.tags as tags, mc.itemCode as itemCode, mc.versionStartdate as versionStartDate, m.id as materialId, " +
            "vm.id as vendorMaterialMapId, mc.sellingPriceType as sellingPriceType, mc.sellingPriceValue as sellingPriceValue " +
            "FROM Material m " +
            "INNER JOIN VendorMaterialMap vm ON vm.material.id = m.id " +
            "INNER JOIN MaterialCost mc on mc.vendorMaterialMap.id = vm.id " +
            "AND mc.versionStartdate <= :date " +
            "AND mc.versionEnddate >= :date " +
            "AND mc.effectiveStartdate <= :date AND mc.effectiveEnddate >= :date " +
            "WHERE TRIM(REPLACE(lower(m.name), '\n', ' ')) = :materialName AND m.grade = :grade AND vm.vendor.id = :vendorId")
    MaterialInfo findMaterialByNameAndGrade(@Param("materialName") String materialName,
                                                  @Param("grade") String grade,  @Param("date") OffsetDateTime date,  @Param("vendorId") Long vendorId);

    @Query("SELECT m.grade FROM Material m " +
            "INNER JOIN VendorMaterialMap vm ON vm.material.id = m.id " +
            "WHERE m.name = :materialName AND vm.vendor.id = :vendorId")
    List<String> findGradesByMaterialName(@Param("materialName") String materialName,
                                      @Param("vendorId") Long vendorId);

    @Query("SELECT vendor_material_map.material.id, vendor_material_map.vendor.id, " +
            "vendor.zipCode, material_cost.effectiveStartdate, material_cost.effectiveEnddate, " +
            "vendor.leadTime, material_cost.cost " +
            "FROM VendorMaterialMap vendor_material_map " +
            "JOIN MaterialCost material_cost ON vendor_material_map.id = material_cost.vendorMaterialMap.id " +
            "JOIN Vendor vendor ON vendor_material_map.vendor.id = vendor.id " +
            "WHERE vendor_material_map.material.id = :materialId ")
    List<Object[]> fetchMaterialsByMaterialId(@Param("materialId") Long materialId);

}

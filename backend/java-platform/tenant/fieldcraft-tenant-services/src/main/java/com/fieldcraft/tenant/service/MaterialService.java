package com.fieldcraft.tenant.service;

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 18-Nov-2023
 * @author      : Mala.K/Pavithra.M
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Service for Material
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |       DATE     |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *    726         |  29-Nov-2023   |  Mala.K      | Search Implementation
 *    2399        |  06-Aug-2024   | shiyam kumar | made changes in searchMaterials for pagination
 *    28503       |  31-Aug-2026   | Surya J      | AB#28503: getmaterialsByNameAndGrade now
 *                |                |              | corrects Purchase-Unit-scale cost to per-Base-
 *                |                |              | Unit (applyBaseUnitCostCorrection), fixing the
 *                |                |              | Create BOM -> Vendor dialog (AddMaterial.tsx)
 *    30036       |  05-Sep-2026   | Surya J      | AB#30036: applyBaseUnitCostCorrection now
 *                |                |              | resolves the Selling Price per Base Unit
 *                |                |              | (SellingPriceCalculator) instead of the raw
 *                |                |              | vendor purchase cost, matching what the
 *                |                |              | Create BOM -> Stock flow already displays
 */

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fieldcraft.shared.error.*;
import com.fieldcraft.shared.utils.ErrorParser;
import com.fieldcraft.tenant.dal.material.MaterialSearchPersistencePort;
import com.fieldcraft.tenant.dal.repos.MaterialRepository;
import com.fieldcraft.tenant.data.domain.Material;
import com.fieldcraft.tenant.data.domain.UnitOfMeasure;
import com.fieldcraft.tenant.feature.material.MaterialConversionValidator;
import com.fieldcraft.tenant.feature.material.SellingPriceCalculator;
import com.fieldcraft.tenant.data.model.*;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import org.slf4j.*;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.OffsetDateTime;
import java.util.*;

import static com.fieldcraft.shared.error.ErrorMessages.*;

@Service
public class MaterialService implements MaterialSearchPersistencePort {

    private final MaterialRepository materialRepository;
    private final VendorMaterialMapService vendorMaterialMapService;
    private final MaterialCostService materialCostService;
    private final CodesService codesService;
    private final UnitOfMeasureService unitOfMeasureService;
    private final ServiceMasterRepository serviceMasterRepository;
    private static final Logger logger = LoggerFactory.getLogger(MaterialService.class);

    public MaterialService(final MaterialRepository materialRepository, VendorMaterialMapService vendorMaterialMapService, MaterialCostService materialCostService, CodesService codesService,
                           final UnitOfMeasureService unitOfMeasureService, final ServiceMasterRepository serviceMasterRepository) {
        this.materialRepository = materialRepository;
        this.vendorMaterialMapService = vendorMaterialMapService;
        this.materialCostService = materialCostService;
        this.codesService = codesService;
        this.unitOfMeasureService = unitOfMeasureService;
        this.serviceMasterRepository = serviceMasterRepository;
    }

    public List<MaterialDTO> findAll() {
        final List<Material> materials = materialRepository.findAll(Sort.by("id"));
        return materials.stream()
                .map(material -> mapToDTO(material, new MaterialDTO()))
                .toList();
    }

    public MaterialDTO get(final Long id) {
        final MaterialDTO materialDTO = materialRepository.findById(id)
                .map(material -> mapToDTO(material, new MaterialDTO()))
                .orElseThrow(NotFoundException::new);
        // Material itself has no cost/Selling Price columns -- both live on MaterialCost, joined
        // via VendorMaterialMap. Without this, the Add Material edit form's Purchase Unit Cost and
        // Selling Price fields always came back empty regardless of what was actually saved.
        final MaterialCostDTO materialCostDTO = materialCostService.findMostRecentByMaterialId(id);
        if (materialCostDTO != null) {
            materialDTO.setCost(materialCostDTO.getCost());
            materialDTO.setSellingPriceType(materialCostDTO.getSellingPriceType());
            materialDTO.setSellingPriceValue(materialCostDTO.getSellingPriceValue());
            materialDTO.setCostPerUnit(materialCostDTO.getCostPerUnit());
        }
        return materialDTO;
    }

    public List<String> getMaterialTags() {
        return materialRepository.getTags();
    }

    public Integer getTotalMaterialCount(String name, String trade, String grade) {
        return materialRepository.countTotalMaterials(Objects.equals(name, "null") ? null : name, Objects.equals(trade, "null") ? null : trade, Objects.equals(grade, "null") ? null : grade, OffsetDateTime.now());
    }

    /**
     * Vendor-wide search by name+trade+grade only, no pre-known vendorId or paging/sort
     * preference — the automation's own narrow use of findBySearchCriteria (top page, default
     * sort), used where the caller has no specific vendor to query (unlike
     * getmaterialsByNameAndGrade, which requires one).
     */
    @Override
    public List<MaterialInfo> searchMaterial(final String name, final String trade, final String grade) {
        return findBySearchCriteria(name, null, null, trade, grade, PageRequest.of(0, 20));
    }

    public List<MaterialInfo> findBySearchCriteria(String name, String sortField, String sortOrder, String trade, String grade, Pageable page) {
        return findBySearchCriteria(name, sortField, sortOrder, trade, grade, page, false, null);
    }

    public List<MaterialInfo> findBySearchCriteria(String name, String sortField, String sortOrder, String trade, String grade, Pageable page, boolean inStockOnly) {
        return findBySearchCriteria(name, sortField, sortOrder, trade, grade, page, inStockOnly, null);
    }

    public List<MaterialInfo> findBySearchCriteria(String name, String sortField, String sortOrder, String trade, String grade, Pageable page, boolean inStockOnly, Long vendorId) {
        try {
            Pageable pageable;
            String normalizedName = (name == null || "null".equalsIgnoreCase(name)) ? null : name.toLowerCase().replace(" ", "");
            String normalizedTrade = (trade == null || "null".equalsIgnoreCase(trade)) ? null : trade.trim();
            String normalizedGrade = (grade == null || "null".equalsIgnoreCase(grade)) ? null : grade.trim();
            String sortColumn = switch (sortField == null ? "" : sortField.toLowerCase().trim()) {
                case "vendorname" -> "vm.vendor.companyName";
                case "materialname" -> "material.name";
                case "trade" -> "material.tags";
                case "itemcode" -> "mc.itemCode";
                case "unitcost" -> "mc.cost";
                case "unit" -> "material.unit";
                case "manufacturer" -> "material.manufacturer";
                case "versionstartdate" -> "mc.versionStartdate";
                case "grade" -> "material.grade";
                default -> "material.updatedAt";
            };
            // Only add sort if sortOrder is valid
            Sort.Direction direction = Sort.Direction.fromOptionalString(sortOrder).orElse(Sort.Direction.DESC);
            Sort sort = Sort.by(direction, sortColumn);
            pageable = PageRequest.of(page.getPageNumber(), page.getPageSize(), sort);
            return inStockOnly
                    ? materialRepository.searchMaterialsInStockOnly(
                            normalizedName,
                            normalizedTrade,
                            normalizedGrade,
                            OffsetDateTime.now(),
                            vendorId,
                            pageable
                    )
                    : materialRepository.searchMaterials(
                            normalizedName,
                            normalizedTrade,
                            normalizedGrade,
                            OffsetDateTime.now(),
                            pageable
                    );
        } catch (Exception e) {
            logger.error(e.getMessage(), e);
            return Collections.emptyList();
        }
    }

    /**
     * All of a single vendor's mapped materials, no in-stock requirement -- used by Review
     * Bill's Reconcile field so it can offer the vendor's full catalog regardless of stock
     * state (unlike findBySearchCriteria's inStockOnly path, which also requires a Stock record).
     */
    public List<MaterialInfo> findByVendorId(Long vendorId, String name, Pageable page) {
        try {
            String normalizedName = (name == null || "null".equalsIgnoreCase(name)) ? null : name.toLowerCase().replace(" ", "");
            return materialRepository.searchMaterialsByVendor(
                    normalizedName,
                    null,
                    null,
                    OffsetDateTime.now(),
                    vendorId,
                    page
            );
        } catch (Exception e) {
            logger.error(e.getMessage(), e);
            return Collections.emptyList();
        }
    }

    public List<String> getMaterialNames() {
        return materialRepository.findMaterialName();
    }

    public Long create(final MaterialDTO materialDTO) {
        final Material material = new Material();
        getCostCode(material, codesService.constant.getMaterials());
        mapToEntity(materialDTO, material);
        try {
            return materialRepository.save(material).getId();
        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                throw new BadRequestException(BadRequestException.DUPLICATE_ENTRY);
            } else {
                throw new BadRequestException();
            }
        }
    }

    public Material getCostCode(final Material material, final String code) {
        CodesDTO codesDTO = codesService.getNames(code);
        try {
            ObjectMapper mapper = new ObjectMapper();
            String jsonResult = mapper.writeValueAsString(codesDTO);
            material.setCodes(jsonResult);
        } catch (Exception ex) {
            logger.error(ex.getMessage(), ex);
        }
        return material;
    }



    public String createMaterial(final List<MaterialDTO> materialDTO) {
        try {
            materialDTO.forEach(
                    materialDTO1 -> {
                        Material material = materialRepository.findMaterialNameAndGrade(materialDTO1.getName().toLowerCase().trim(), materialDTO1.getGrade());
                        if (material == null) {
                            Long insertedId = create(materialDTO1);
                            if (insertedId != null) {
                                Long vendorMaterialMapId = saveVendorMaterialMapOrThrow(insertedId, materialDTO1);
                                if (vendorMaterialMapId != null) {
                                    saveMaterialCost(materialDTO1, vendorMaterialMapId);
                                }
                            }
                        } else {
                            Long vendorMaterialMapId = saveVendorMaterialMapOrThrow(material.getId(), materialDTO1);
                            if (vendorMaterialMapId != null) {
                                saveMaterialCost(materialDTO1, vendorMaterialMapId);
                            }
                        }
                    }
            );
            return "Inserted";
        } catch (ServerException e) {
            // Thrown directly by saveVendorMaterialMapOrThrow below (or any other ServerException
            // surfaced from deeper in the chain) -- already carries the correct, specific
            // errorCode/message; must not be re-wrapped or relabeled by the generic handling below.
            throw e;
        } catch (Exception e) {
            logger.error("Error occurred while creating material(s): {}", e.getMessage(), e);
            // AB#: this used to also mislabel a vendor_material_map uk_vendor_material violation
            // (vendor already linked to this exact material) as MATERIAL_DUPLICATE_ERROR ("A
            // material with the same name and grade already exists") -- both exceptions matched
            // ErrorParser.isDuplicateError's generic text check, but material has no unique
            // constraint on (name, grade) at all (only material_uid, a fresh random UUID per
            // create(), so that collision isn't realistically what a caller hit). That distinct
            // case is now caught and reported correctly in saveVendorMaterialMapOrThrow, before
            // reaching here -- so this branch is reached only for a genuine create() failure.
            if (ErrorParser.isDuplicateError(e)) {
                throw new ServerException(ErrorMessages.MATERIAL_DUPLICATE_ERROR, MATERIAL_DUPLICATE_ERROR_DESC, e);
            }
            throw new ServerException(ErrorMessages.MATERIAL_ADD_ERROR, MATERIAL_ADD_ERROR_DESC, e);
        }
    }

    // Wraps saveVendorMaterialMap so its own duplicate (uk_vendor_material: this vendor is
    // already linked to this exact material) is reported with its own accurate errorCode/message,
    // instead of falling through to createMaterial's catch block and being relabeled as a
    // material name+grade duplicate -- a different, unrelated conflict.
    private Long saveVendorMaterialMapOrThrow(final Long materialId, final MaterialDTO materialDTO1) {
        try {
            return saveVendorMaterialMap(materialId, materialDTO1);
        } catch (Exception e) {
            if (ErrorParser.isDuplicateError(e)) {
                throw new ServerException(ErrorMessages.VENDOR_MATERIAL_MAP_DUPLICATE_ERROR,
                        VENDOR_MATERIAL_MAP_DUPLICATE_ERROR_DESC, e);
            }
            throw e;
        }
    }

    public Long saveVendorMaterialMap(final Long materialId, final MaterialDTO materialDTO) {
        VendorMaterialMapDTO vendorMaterialMapDTO = new VendorMaterialMapDTO();
        vendorMaterialMapDTO.setMaterialId(materialId);
        vendorMaterialMapDTO.setVendorId(materialDTO.getVendorId());
        vendorMaterialMapDTO.setLeadTime(materialDTO.getLeadTime());
        vendorMaterialMapDTO.setInStock(materialDTO.getInStock());
        return vendorMaterialMapService.create(vendorMaterialMapDTO);
    }

    public void saveMaterialCost(final MaterialDTO materialDTO, final Long vendorMaterialMapId) {
        MaterialCostDTO materialCostDTO = new MaterialCostDTO();
        materialCostDTO.setCost(materialDTO.getCost());
        materialCostDTO.setUnit(materialDTO.getUnit());
        materialCostDTO.setItemCode(materialDTO.getItemCode());
        materialCostDTO.setGrade(materialDTO.getGrade());
        materialCostDTO.setVersionStartdate(materialDTO.getVersionStartdate());
        materialCostDTO.setCostType("USD");
        materialCostDTO.setServiceId(materialDTO.getServiceId());
        materialCostDTO.setSellingPriceType(materialDTO.getSellingPriceType());
        materialCostDTO.setSellingPriceValue(materialDTO.getSellingPriceValue());
        materialCostDTO.setCostPerUnit(materialDTO.getCostPerUnit());
        materialCostService.create(materialCostDTO, vendorMaterialMapId);
    }

    public Long update(final Long id, final MaterialDTO materialDTO) throws JsonProcessingException {
        final Material material = materialRepository.findById(id)
                .orElseThrow(NotFoundException::new);
        mapToEntity(materialDTO, material);
        final Long savedId;
        try {
            savedId = materialRepository.save(material).getId();
        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                throw new BadRequestException(BadRequestException.DUPLICATE_ENTRY);
            } else {
                throw new BadRequestException();
            }
        }
        // AB#26886/26895: this endpoint is Inventory's "Edit material" save path (AddMaterial_v2
        // edit mode) -- unlike Material Management's own grid (which already goes through
        // MaterialCostApi.updateMaterialCost -> insertMaterialCost -> invalidProject), a cost
        // change made here previously updated nothing but the Material entity itself, silently
        // bypassing Estimate/BOM invalidation. Route a real cost change through the same
        // insertMaterialCost flow so affected projects get flagged the same way regardless of
        // which screen the price was changed from.
        if (materialDTO.getCost() != null) {
            final MaterialCostDTO existing = materialCostService.findMostRecentByMaterialId(id);
            final boolean costChanged = existing == null || existing.getCost() == null
                    || existing.getCost().compareTo(materialDTO.getCost()) != 0;
            if (costChanged && existing != null && existing.getVendorMaterialMapId() != null) {
                final MaterialCostDTO materialCostDTO = new MaterialCostDTO();
                materialCostDTO.setCost(materialDTO.getCost());
                materialCostDTO.setUnit(existing.getUnit());
                materialCostDTO.setCostType(existing.getCostType());
                materialCostDTO.setGrade(existing.getGrade());
                materialCostDTO.setItemCode(existing.getItemCode());
                materialCostDTO.setVersionStartdate(OffsetDateTime.now());
                materialCostDTO.setVendorMaterialMapId(existing.getVendorMaterialMapId());
                materialCostDTO.setSellingPriceType(materialDTO.getSellingPriceType() != null ? materialDTO.getSellingPriceType() : existing.getSellingPriceType());
                materialCostDTO.setSellingPriceValue(materialDTO.getSellingPriceValue() != null ? materialDTO.getSellingPriceValue() : existing.getSellingPriceValue());
                materialCostDTO.setCostPerUnit(materialDTO.getCostPerUnit() != null ? materialDTO.getCostPerUnit() : existing.getCostPerUnit());
                materialCostService.insertMaterialCost(materialCostDTO);
            } else if (existing != null && existing.getVendorMaterialMapId() == null) {
                // A material with no VendorMaterialMap (e.g. a stock-only material never assigned
                // a vendor) has no vendor-scoped cost history to version -- insertMaterialCost's
                // whole versioning scheme requires a vendorMaterialMapId, so the branch above can
                // never apply here. Previously this meant a cost/Selling Price edit on such a
                // material was silently dropped: accepted by the UI, written nowhere. Update its
                // one MaterialCost row directly instead of trying to version it.
                materialCostService.updateInPlace(existing.getId(), materialDTO);
            }
        }
        return savedId;
    }

    public String updateMaterialCost(final List<MaterialDTO> materialDTO) {
        try {
            List<String> trades = new ArrayList<>();
            List<String> grades = new ArrayList<>();
            materialDTO.forEach(materialData -> {
                final Material material = materialRepository.findById(materialData.getId())
                        .orElseThrow(NotFoundException::new);
                if (!trades.contains((materialData.getTags()))) {
                    if (materialData.getTags().contains(";")) {
                        String[] array = materialData.getTags().split(";");
                        trades.addAll(Arrays.asList(array));
                    } else {
                        trades.add(materialData.getTags());
                    }
                }
                if (!grades.contains(materialData.getGrade())) {
                    grades.add(materialData.getGrade());
                }
                mapToEntity(materialData, material);
                materialRepository.save(material);
            });
            return "Updated Successfully";
        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                throw new BadRequestException(BadRequestException.DUPLICATE_ENTRY);
            } else {
                throw new BadRequestException();
            }
        }
    }

    public void delete(final Long id) {
        materialRepository.deleteById(id);
    }

    public List<MaterialDTO> getMaterials() {
        return new ArrayList<>();
    }

    public List<MaterialDTO> getMaterials(final String trade) {
        try {
            List<MaterialDTO> materialResult = new ArrayList<>();
            if (!trade.isEmpty()) {
                List<Material> materials = materialRepository.findNameByTradeName(trade.trim().toLowerCase());
                for (Material material : materials) {
                    var res = (materialResult.stream().filter(o -> o.getName().equals(material.getName()))).findFirst().isPresent();
                    if (!res) {
                        materialResult.add(mapToDTO(material, new MaterialDTO()));
                    }
                }
                return materialResult;
            } else {
                return Collections.emptyList();
            }
        } catch (Exception e) {
            logger.error("An unexpected error occurred: {}", e.getMessage(),e);
            if (e instanceof ServerException serverException){
                throw serverException;
            } else {
                throw new ServerException(ErrorMessages.MATERIAL_FETCH_ERROR, MATERIAL_FETCH_ERROR_DESC,e);
            }
        }
    }

    public MaterialInfo getmaterialsByNameAndGrade(final MaterialDTO materialDTO) {
        try {
            if (materialDTO.getName() != null && materialDTO.getGrade() != null) {
                MaterialInfo match = materialRepository.findMaterialByNameAndGrade(materialDTO.getName().trim().toLowerCase(), materialDTO.getGrade(), OffsetDateTime.now(), materialDTO.getVendorId());
                return applyBaseUnitCostCorrection(match);
            } else {
                return null;
            }
        } catch (Exception e) {
            logger.error(e.getMessage(),e);
            if (e instanceof ServerException serverException){
                throw serverException;
            } else {
                throw new ServerException(ErrorMessages.MATERIAL_FETCH_ERROR, MATERIAL_FETCH_ERROR_DESC,e);
            }
        }
    }

    // AB#28503: match.getCost() is per Purchase Unit (e.g. per Bundle) -- derive the true
    // per-Base-Unit rate the same way BomService/TaskService/EstimateAutomationService already
    // do, instead of handing the "Create BOM -> Vendor" dialog (AddMaterial.tsx, which displays
    // and saves this response's unit/cost verbatim) the raw bundle price. Mirrors
    // EstimateAutomationService.hydrateMaterial's vendor-catalog branch exactly.
    //
    // AB#30036: that "true" rate is the Selling Price per Base Unit -- the same figure a
    // Stock-sourced BOM row shows (StockItemTransaction.resolveBaseUnitSellingPrice) -- not the
    // raw vendor purchase cost this previously returned. Falls back to the base-unit-corrected
    // purchase cost (AB#28503's original behavior) only when this material has no Selling Price
    // on file (sellingPriceType/sellingPriceValue absent, e.g. legacy data).
    private MaterialInfo applyBaseUnitCostCorrection(final MaterialInfo match) {
        if (match == null || match.getCost() == null) {
            return match;
        }
        BigDecimal conversionFactor = match.getConversionFactor();
        boolean hasRealConversion = conversionFactor != null
                && conversionFactor.compareTo(BigDecimal.ONE) > 0 && match.getBaseUnit() != null;
        double effectiveConversionFactor = hasRealConversion ? conversionFactor.doubleValue() : 1.0;
        BigDecimal sellingPricePerBaseUnit = SellingPriceCalculator.resolvePerBaseUnit(
                match.getCost(), match.getSellingPriceType(), match.getSellingPriceValue(), effectiveConversionFactor);
        if (sellingPricePerBaseUnit != null) {
            return new BaseUnitCorrectedMaterialInfo(match, sellingPricePerBaseUnit,
                    hasRealConversion ? match.getBaseUnit() : match.getUnit());
        }
        if (!hasRealConversion) {
            return match;
        }
        BigDecimal correctedCost = match.getCost().divide(conversionFactor, 8, RoundingMode.HALF_UP);
        return new BaseUnitCorrectedMaterialInfo(match, correctedCost, match.getBaseUnit());
    }

    // Decorator over the MaterialInfo projection returned by findMaterialByNameAndGrade --
    // overrides only cost/unit with the corrected per-Base-Unit values, delegating everything
    // else unchanged (the projection interface can't be re-instantiated with new field values).
    private static final class BaseUnitCorrectedMaterialInfo implements MaterialInfo {
        private final MaterialInfo delegate;
        private final BigDecimal cost;
        private final String unit;

        BaseUnitCorrectedMaterialInfo(final MaterialInfo delegate, final BigDecimal cost, final String unit) {
            this.delegate = delegate;
            this.cost = cost;
            this.unit = unit;
        }

        @Override public Long getId() { return delegate.getId(); }
        @Override public String getCode() { return delegate.getCode(); }
        @Override public String getName() { return delegate.getName(); }
        @Override public String getManufacturer() { return delegate.getManufacturer(); }
        @Override public String getGrade() { return delegate.getGrade(); }
        @Override public String getUnit() { return unit; }
        @Override public BigDecimal getCost() { return cost; }
        @Override public String getTags() { return delegate.getTags(); }
        @Override public String getItemCode() { return delegate.getItemCode(); }
        @Override public OffsetDateTime getVersionStartDate() { return delegate.getVersionStartDate(); }
        @Override public Long getVendorMaterialMapId() { return delegate.getVendorMaterialMapId(); }
        @Override public Long getMaterialCostId() { return delegate.getMaterialCostId(); }
        @Override public Long getVendorToolMapId() { return delegate.getVendorToolMapId(); }
        @Override public Long getVendorId() { return delegate.getVendorId(); }
        @Override public String getCompanyName() { return delegate.getCompanyName(); }
        @Override public OffsetDateTime getEffectiveStartDate() { return delegate.getEffectiveStartDate(); }
        @Override public OffsetDateTime getVersionStartdate() { return delegate.getVersionStartdate(); }
        @Override public UUID getMaterialUid() { return delegate.getMaterialUid(); }
        @Override public BigDecimal getConversionFactor() { return delegate.getConversionFactor(); }
        @Override public String getBaseUnit() { return delegate.getBaseUnit(); }
        @Override public Boolean getAllowDecimal() { return delegate.getAllowDecimal(); }
        @Override public String getSellingPriceType() { return delegate.getSellingPriceType(); }
        @Override public BigDecimal getSellingPriceValue() { return delegate.getSellingPriceValue(); }
    }

    public List<String> getGradesByMaterialName(final String name, final Long vendorId) {
        try {
            if (name != null && vendorId != null) {
                List<String> grades = materialRepository.findGradesByMaterialName(name, vendorId);
                return grades.stream().distinct().toList();
            } else {
                return Collections.emptyList();
            }
        } catch (Exception e) {
            logger.error("An unexpected error occurred: {}",  e.getMessage(),e);
            if (e instanceof ServerException serverException){
                throw serverException;
            } else {
                throw new ServerException(ErrorMessages.MATERIAL_FETCH_ERROR, MATERIAL_FETCH_ERROR_DESC,e);
            }
        }
    }
    private String getUnitString(String unitInput) {
        if (unitInput == null || unitInput.isEmpty()) {
            return ""; // return empty string if input is null/empty
        }
        try {
            UnitOfMeasure unit = unitOfMeasureService.findByUnitCode(unitInput);
            return unit.getCode() + " - " + unit.getName();
        } catch (IllegalArgumentException e) {
            // If already in "CODE - Name" format, just return it
            if (unitInput.contains("-")) {
                return unitInput;
            }
            // fallback: return input as-is if unit not found
            return unitInput;
        }
    }

    private MaterialDTO mapToDTO(final Material material, final MaterialDTO materialDTO) {
        materialDTO.setId(material.getId());
        materialDTO.setName(material.getName());
        materialDTO.setManufacturer(material.getManufacturer());
        materialDTO.setGrade(material.getGrade());
        materialDTO.setUnit(material.getUnit());
        materialDTO.setTags(material.getTags());
        materialDTO.setEffectiveStartdate(material.getEffectiveStartdate());
        materialDTO.setExpiryDate(material.getExpiryDate());
        materialDTO.setStatus(material.getStatus());
        materialDTO.setVersion(material.getVersion());
        materialDTO.setCreatedAt(material.getCreatedAt());
        materialDTO.setUpdatedAt(material.getUpdatedAt());
        materialDTO.setServiceId(material.getServiceId());
        materialDTO.setCreatedBy(material.getCreatedBy());
        // Conversion config (so view/edit screens can read it back). Direct materials expose only
        // baseUnit + conversionFactor; gross materials also expose the gross unit + sub-factors.
        if (material.getBaseUnitId() != null) {
            materialDTO.setBaseUnit(material.getBaseUnitId().getCode());
        }
        materialDTO.setConversionFactor(material.getConversionFactor());
        materialDTO.setAllowDecimal(material.isAllowDecimal());
        if (material.getGrossUnit() != null) {
            materialDTO.setGrossUnit(material.getGrossUnit().getCode());
        }
        materialDTO.setPurchaseToGrossFactor(material.getPurchaseToGrossFactor());
        materialDTO.setGrossToBaseFactor(material.getGrossToBaseFactor());
        return materialDTO;
    }

    private Long getServiceIdByName(final String serviceName) {
        try {
            return serviceMasterRepository.getIdByName(serviceName);
        } catch (Exception e) {
            logger.error("Error retrieving service id for service name: {}", serviceName, e);
            return null;
        }
    }

    private void mapToEntity(final MaterialDTO materialDTO, final Material material) {
        MaterialConversionValidator.validate(materialDTO);
        // materialUid is the material's stable EXTERNAL key (docs/coding-standards.md: "*_uid UUID
        // = external/API key") -- other rows reference a material by this UUID rather than by its
        // internal pk, notably bom.material_uid and stock_item lookups in
        // StockAllocationTransaction.allocateMaterialFromStock. This mapper is shared by create()
        // AND by both update paths (update(), and the bulk trade-import update), so minting a new
        // UUID unconditionally here silently re-keyed an EXISTING material on every edit -- any
        // cost/selling-price/name change orphaned every BOM line already pointing at the old UUID,
        // which then failed at Add-BOM/allocate/dispatch time with "Material not found: <name>"
        // (AB#: recurring orphaned bom.material_uid values; repeatedly mistaken for a UI
        // material-picker bug because the BOM row looked correct and only the UUID was stale).
        // Generate only when absent, i.e. for a genuinely new material.
        if (material.getMaterialUid() == null) {
            material.setMaterialUid(UUID.randomUUID());
        }
        material.setAlias_name(materialDTO.getName());
        material.setName(materialDTO.getName());
        material.setManufacturer(materialDTO.getManufacturer());
        material.setGrade(materialDTO.getGrade());
        material.setUnit(getUnitString(materialDTO.getUnit()));
        UnitOfMeasure uom = unitOfMeasureService.findByUnitCode(materialDTO.getBaseUnit());
        material.setBaseUnitId(uom);
        // Gross (Purchase -> Gross -> Base): persist the gross unit + sub-factors and compute the
        // total conversionFactor server-side (= p2g * g2b). Direct materials keep the single factor.
        if (materialDTO.getPurchaseToGrossFactor() != null && materialDTO.getGrossToBaseFactor() != null) {
            material.setGrossUnit(unitOfMeasureService.findByUnitCode(materialDTO.getGrossUnit()));
            material.setPurchaseToGrossFactor(materialDTO.getPurchaseToGrossFactor());
            material.setGrossToBaseFactor(materialDTO.getGrossToBaseFactor());
            material.setConversionFactor(materialDTO.getPurchaseToGrossFactor()
                    .multiply(materialDTO.getGrossToBaseFactor()));
        } else {
            material.setConversionFactor(materialDTO.getConversionFactor());
        }
        material.setAllowDecimal(materialDTO.getAllowDecimal());
        material.setTags(materialDTO.getTags());
        material.setEffectiveStartdate(materialDTO.getEffectiveStartdate());
        material.setExpiryDate(materialDTO.getExpiryDate());
        material.setStatus(materialDTO.getStatus());
        material.setVersion(materialDTO.getVersion());
        material.setCreatedAt(materialDTO.getCreatedAt());
        material.setUpdatedAt(materialDTO.getUpdatedAt());
        try {
            material.setServiceId(materialDTO.getServiceId() != null
                    ? materialDTO.getServiceId()
                    : getServiceIdByName("FieldCraft"));
            // Sync the DTO so downstream (like MaterialCost) uses the resolved serviceId if it was null
            materialDTO.setServiceId(material.getServiceId());
        } catch (Exception e) {
            logger.error("Failed to resolve serviceId for material {}; continuing without it.", materialDTO.getName(), e);
        }
    }
}

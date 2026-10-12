/*
 *Copyright(C)FIELDCRAFT Inc-All Rights Reserved
 *This file is part of FieldCraft.-Proprietary and confidential
 *Unauthorized copying of this file,via any medium is strictly prohibited
 *
 *Created on:30-Nov-2023
 *@author      :Ananth Kumar L
 *@since       :0.0.1
 *Requirement# :
 *Purpose:Service layer for MaterialCost
 *-----------------------------------------------------------------------------
 *Revision History
 *-----------------------------------------------------------------------------
 *Requirement/
 * Issue/WorkItem  |    DATE    |    AUTHOR    |    DESCRIPTION OF CHANGE
 *-----------------------------------------------------------------------------
 *   2858          | 04-11-2024 | shiyam kumar | getMinimumMaterialCost function created for minimum cost model integration
 *   28503         | 31-08-2026 | Surya J      | AB#28503: invalidProject now divides materialCost.getCost()
 *                 |            |              | by the conversion factor before writing it into every
 *                 |            |              | affected project's BOM snapshot, instead of propagating the
 *                 |            |              | raw Purchase-Unit-scale bundle price to every row
 *   30036         | 10-09-2026 | Surya J      | AB#30036: invalidProject now persists the Selling Price per
 *                 |            |              | Base Unit (SellingPriceCalculator) into every affected
 *                 |            |              | project's BOM snapshot, matching BomService/TaskService/
 *                 |            |              | MaterialService, instead of the raw base-unit-corrected
 *                 |            |              | purchase cost -- falls back to that purchase-cost figure
 *                 |            |              | only when this material has no Selling Price on file
 *   32920         | 06-10-2026 | Surya J      | AB#32920: the rebuild prices each BOM row in its own unit
 *                 |            |              | and keeps the row's unit and quantity
 *
 */
package com.fieldcraft.tenant.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import https://fieldcraft.example.invalid;
import https://fieldcraft.example.invalid;
import https://fieldcraft.example.invalid;
import com.fieldcraft.shared.error.BadRequestException;
import com.fieldcraft.shared.error.ErrorMessages;
import com.fieldcraft.shared.error.NotFoundException;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.shared.utils.ErrorParser;
import com.fieldcraft.tenant.dal.repos.*;
import com.fieldcraft.tenant.data.domain.*;
import com.fieldcraft.tenant.data.model.*;
import com.fieldcraft.tenant.saas.AIChatService;
import com.fieldcraft.tenant.saas.EmpCloudAccountService;
import com.fieldcraft.tenant.saas.AseService;
import com.fieldcraft.tenant.saas.NotificationMessageService;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.json.JSONArray;
import org.json.JSONObject;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.feature.material.BomUnitFactor;
import com.fieldcraft.tenant.feature.material.ConversionResolver;
import com.fieldcraft.tenant.feature.material.SellingPriceCalculator;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.OffsetDateTime;
import java.util.*;

import static https://fieldcraft.example.invalid*;
import static com.fieldcraft.shared.error.ErrorMessages.*;
import static com.fieldcraft.tenant.data.status.ProjectStatus.CONTRACT_APPROVAL;
import static com.fieldcraft.tenant.data.status.ProjectStatus.PROJECT_INACTIVE;

@Service
public class MaterialCostService {

    private final NotificationMessageService notificationMessageService;
    /** FCM-GAP-3 — bell + phone through one door; see NotificationDispatcher. */
    private final NotificationDispatcher notificationDispatcher;
    private final EmpCloudAccountService empAccountService;
    private final AseService aseService;
    private final AIChatService aiChatService;
    private final TenantService tenantService;
    private final AuditLogService auditLogService;

    private final MaterialCostRepository materialCostRepository;
    private final MaterialRepository materialRepository;
    private final VendorMaterialMapRepository vendorMaterialMapRepository;
    private final ProjectRepository projectRepository;
    private final TaskRepository taskRepository;
    private final UserAccountRepository userAccountRepository;
    private final ServiceMasterRepository serviceMasterRepository;

    private final BomRepository bomRepository;
    private static final Logger logger = LogManager.getLogger(MaterialCostService.class);

    public MaterialCostService(NotificationMessageService notificationMessageService, EmpCloudAccountService empAccountService, AseService aseService, AIChatService aiChatService, TenantService tenantService, AuditLogService auditLogService, final MaterialCostRepository materialCostRepository, final MaterialRepository materialRepository,
                               final VendorMaterialMapRepository vendorMaterialMapRepository,
                               final ProjectRepository projectRepository, final TaskRepository taskRepository,
                               final BomRepository bomRepository, final UserAccountRepository userAccountRepository,
                               final ServiceMasterRepository serviceMasterRepository,
                               final NotificationDispatcher notificationDispatcher) {
        this.notificationMessageService = notificationMessageService;
        this.notificationDispatcher = notificationDispatcher;
        this.empAccountService = empAccountService;
        this.aseService = aseService;
        this.aiChatService = aiChatService;
        this.tenantService = tenantService;
        this.auditLogService = auditLogService;
        this.materialCostRepository = materialCostRepository;
        this.materialRepository = materialRepository;
        this.vendorMaterialMapRepository = vendorMaterialMapRepository;
        this.projectRepository = projectRepository;
        this.taskRepository = taskRepository;
        this.bomRepository = bomRepository;
        this.userAccountRepository = userAccountRepository;
        this.serviceMasterRepository = serviceMasterRepository;
    }

    public List<MaterialCostDTO> findAll() {
        final List<MaterialCost> materialCostList = materialCostRepository.findAll(Sort.by("id"));
        return materialCostList.stream()
                .map(materialCost -> mapToDTO(materialCost, new MaterialCostDTO()))
                .toList();
    }

    // For the Add Material edit-form prefill (MaterialService.get()) -- the material's own record
    // has no cost/Selling Price fields at all (they live on MaterialCost, joined via
    // VendorMaterialMap), so a caller that only knows the Material id needs this to resolve them.
    // Null when the material has never had a MaterialCost row created for it.
    public MaterialCostDTO findMostRecentByMaterialId(final Long materialId) {
        final MaterialCost materialCost = materialCostRepository.findMostRecentByMaterialId(materialId);
        return materialCost == null ? null : mapToDTO(materialCost, new MaterialCostDTO());
    }

    // AB#: a material with no VendorMaterialMap (never assigned a vendor -- e.g. Skylight, a
    // stock-only material) still has a MaterialCost row (created via saveMaterialCost at Add
    // Material time), but every other cost-update path here (insertMaterialCost, update(materialId,
    // materialDTO, vendorId)) requires a vendorMaterialMapId to resolve which row to touch. Without
    // this, editing such a material's cost/Selling Price in Add Material's edit form silently wrote
    // nothing: MaterialService.update()'s insertMaterialCost call was gated on
    // existing.getVendorMaterialMapId() != null, so the edit was accepted by the UI but never
    // persisted. Updates the given MaterialCost row in place (no new version row, matching this
    // no-vendor material having no vendor-scoped cost history to version in the first place).
    public void updateInPlace(final Long materialCostId, final MaterialDTO materialDTO) {
        final MaterialCost materialCost = materialCostRepository.findById(materialCostId)
                .orElseThrow(NotFoundException::new);
        mapToUpdateEntity(materialDTO, materialCost);
        materialCostRepository.save(materialCost);
    }

    public Long create(final MaterialCostDTO materialCostDTO, final Long vendorMaterialMapId) {
        final MaterialCost materialCost = new MaterialCost();
        mapToEntity(materialCostDTO, materialCost, vendorMaterialMapId);
        try {
            Long id = materialCostRepository.save(materialCost).getId();
            logger.info("materialCost created successfully");
            return id;
        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                logger.error("Duplicate entry detected while materialCost creation vendorMaterialMapId : {} .error :{}", vendorMaterialMapId, e.getMessage(), e);
                throw new BadRequestException(BadRequestException.DUPLICATE_ENTRY);
            } else {
                logger.error("Data integrity violation occurred  while materialCost creation : {} {}", vendorMaterialMapId, e.getMessage(), e);
                throw new BadRequestException();
            }
        }
    }

    public void update(final Long materialId, final MaterialDTO materialDTO, final Long vendorId) {
        Long vendorMaterialMapId = vendorMaterialMapRepository.getId(materialId, vendorId);
        // PR 8110 review: findMaterialCostByVendorMapId has no date filter/LIMIT and throws
        // IncorrectResultSizeDataAccessException once this vendorMaterialMapId has 2+ price
        // versions -- routine after any prior price change. findMostRecentByVendorMapId is bounded
        // (LIMIT 1, most-recent-by-id) and resolves the same "current" row this method edits in place.
        final MaterialCost materialCost = materialCostRepository.findMostRecentByVendorMapId(vendorMaterialMapId);
        mapToUpdateEntity(materialDTO, materialCost);
        try {
            materialCostRepository.save(materialCost);
            logger.info("materialCost Updated successfully with materialId :{}", materialId);
        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                logger.error("Duplicate entry detected while materialCost Update with materialId :{} :{}", materialId, e.getMessage(), e);
                throw new BadRequestException(BadRequestException.DUPLICATE_ENTRY);
            } else {
                logger.error("Data integrity violation occurred while materialCost Update: {} :{}", materialId, e.getMessage(), e);
                throw new BadRequestException();
            }
        }
    }

    //Special insert function for endorsement method...

    // PR 8110 review: this read-check-write sequence (isMaterialCostAvailable, then
    // findMaterialCostByEffectiveDate, then two saves) had no lock, so two concurrent callers
    // reconciling the same vendorMaterialMapId+grade (e.g. two overlapping bill approvals via
    // ReceiptService.updateReconciledVendorMaterialCost) could both read "no current row present",
    // both close the SAME previous row, and both insert an overlapping "current" row -- leaving
    // pricing lookups for that material non-deterministic. @Transactional so the pessimistic lock
    // taken below is held for this method's entire read-check-write sequence, not just the single
    // locking query.
    @Transactional
    public String insertMaterialCost(final MaterialCostDTO materialCostDTO) throws JsonProcessingException {
        try {
            OffsetDateTime versionEffectiveDate = materialCostDTO.getVersionStartdate();
            String grade = materialCostDTO.getGrade();
            Long vendorMaterialMapId = materialCostDTO.getVendorMaterialMapId();
            // Lock FIRST, before the availability check: this is what actually closes the race --
            // a second concurrent caller blocks here until the first caller's transaction commits
            // (having closed out the previous row), instead of both callers reading the same
            // pre-close snapshot and racing to write.
            materialCostRepository.findMaterialCostByEffectiveDateForUpdate(versionEffectiveDate, vendorMaterialMapId, grade);

            boolean isPresent = materialCostRepository.isMaterialCostAvailable(vendorMaterialMapId, versionEffectiveDate, grade);
            if (!isPresent) {
                final MaterialCost materialCost = new MaterialCost();
                mapToEntity(materialCostDTO, materialCost, vendorMaterialMapId);

//            OffsetDateTime newEffectiveDate = materialCost.getEffectiveStartdate();
                OffsetDateTime newEffectiveDate = OffsetDateTime.now();
                MaterialCost previousMaterialCost = materialCostRepository.findMaterialCostByEffectiveDate(versionEffectiveDate, vendorMaterialMapId, grade);
                MaterialCost newMaterialCost = new MaterialCost();
                if (previousMaterialCost != null) {
                    materialCostRepository.updateMaterialCost(previousMaterialCost.getId(), newEffectiveDate);
                    newMaterialCost.setCost(previousMaterialCost.getCost());
                    newMaterialCost.setCostType(previousMaterialCost.getCostType());
                    newMaterialCost.setItemCode(previousMaterialCost.getItemCode());
                    newMaterialCost.setVersionStartdate(previousMaterialCost.getVersionStartdate());
                    newMaterialCost.setVersionEnddate(versionEffectiveDate);
                    newMaterialCost.setEffectiveStartdate(newEffectiveDate);
                    newMaterialCost.setEffectiveEnddate(OffsetDateTime.now().plusYears(1000));
                    newMaterialCost.setGrade(previousMaterialCost.getGrade());
                    newMaterialCost.setUnit(previousMaterialCost.getUnit());
                    newMaterialCost.setVersion(previousMaterialCost.getVersion());
                    newMaterialCost.setVendorMaterialMap(previousMaterialCost.getVendorMaterialMap());
                    newMaterialCost.setServiceId(previousMaterialCost.getServiceId());
                    newMaterialCost.setCreatedBy(previousMaterialCost.getCreatedBy());
                    // AB#31160 (secondary defect, same investigation): this archived copy of the
                    // OLD price never copied Selling Price / cost-per-unit forward, so the cost
                    // history's superseded rows were persisted with those fields null -- a silent
                    // data-quality gap, distinct from (but found alongside) the current-row bug
                    // above. Doesn't corrupt what reads back as "current" (that's the freshly
                    // inserted `materialCost` below, which gets the higher id).
                    newMaterialCost.setSellingPriceType(previousMaterialCost.getSellingPriceType());
                    newMaterialCost.setSellingPriceValue(previousMaterialCost.getSellingPriceValue());
                    newMaterialCost.setCostPerUnit(previousMaterialCost.getCostPerUnit());
                    materialCostRepository.save(newMaterialCost);
                }
                Long insertedId = materialCostRepository.save(materialCost).getId();
                if (insertedId != null) {
                    invalidProject(materialCostDTO, materialCost);
                }
                return "Updated Successfully";
            } else {
                return "Material Already Present";
            }

        } catch (Exception e) {
            logger.error("An unexpected error occurred in insertMaterialCost functions:", e);
            if (e instanceof ServerException) {
                throw e;
            } else {
                throw new ServerException(ErrorMessages.UPDATE_MATERIAL_ERROR, UPDATE_MATERIAL_ERROR_DESC, e);
            }
        }
    }

    public String getMaterialCostChatContent(String tenantId, final Long insertedId, final MaterialCostDTO materialCostDTO) {
        StringBuilder sb = new StringBuilder();

        sb.append("\n\n");
        sb.append("Material Cost : ");
        sb.append(materialCostDTO.getCost());
        sb.append("\n");

        sb.append("Effective Start Date : ");
        sb.append(materialCostDTO.getEffectiveStartdate());
        sb.append("\n");

        sb.append("Effective End Date : ");
        sb.append(materialCostDTO.getEffectiveEnddate());
        sb.append("\n");

        return sb.toString();

    }

    public void invalidProject(final MaterialCostDTO materialCostDTO, final MaterialCost materialCost) throws JsonProcessingException {
        try {
            final Material material = materialRepository.findMaterialById(materialCostDTO.getId());
            String[] projectStatus = new String[]{CONTRACT_APPROVAL,PROJECT_INACTIVE};
            List<ProjectToolBomInfo> projectData = projectRepository.findProjectsAndBom(projectStatus, Arrays.asList(material.getTags().split(";")), materialCostDTO.getVersionStartdate(), material.getName(),
                    materialCostDTO.getVendorMaterialMapId());
            // AB#28503: materialCost.getCost() is per Purchase Unit (e.g. per Bundle) -- derive the
            // true per-Base-Unit rate the same way BomService/TaskService/EstimateAutomationService
            // already do, instead of propagating the bundle price straight into every affected
            // project's BOM snapshot. Computed once -- material/materialCost don't vary per project.
            //
            // AB#30036: the rate persisted here must be the Selling Price per Base Unit -- the
            // same figure BomService.recomputeVendorUnitCost/TaskService.recomputeVendorUnitCostForBom
            // now persist -- not the raw vendor purchase cost. Falls back to the base-unit-corrected
            // purchase cost (this method's pre-30036 behavior) only when this material has no
            // Selling Price on file.
            final double conversionFactor = ConversionResolver.resolveTotalFactor(material);
            final boolean hasRealConversion = conversionFactor > 1 && material.getBaseUnitId() != null;
            final BigDecimal sellingPricePerBaseUnit = SellingPriceCalculator.resolvePerBaseUnit(
                    materialCost.getCost(), materialCost.getSellingPriceType(), materialCost.getSellingPriceValue(),
                    hasRealConversion ? conversionFactor : 1.0);
            final BigDecimal correctedUnitCost = sellingPricePerBaseUnit != null
                    ? sellingPricePerBaseUnit
                    : (hasRealConversion
                            ? materialCost.getCost().divide(BigDecimal.valueOf(conversionFactor), 8, RoundingMode.HALF_UP)
                            : materialCost.getCost());
            if (projectData.size() != 0) {
                List<Long> projectIds = new ArrayList<>();
                List<Long> bomIds = new ArrayList<>();
                List<Bom> bom = new ArrayList<>();
                for (ProjectToolBomInfo projectDatum : projectData) {
                    projectIds.add(projectDatum.getProjectId());
                    bomIds.add(projectDatum.getId());
                    Bom bomData = new Bom();
                    bomData.setMaterialName(projectDatum.getName());
                    priceRowInItsOwnUnit(bomData, projectDatum, material, correctedUnitCost);
                    bomData.setVendorMaterialMap(materialCost.getVendorMaterialMap());
                    bomData.setQuantity(projectDatum.getQuantity());
                    bomData.setVersion(projectDatum.getVersion());
                    bomData.setCreatedAt(OffsetDateTime.now());
                    bomData.setUpdatedAt(OffsetDateTime.now());
                    bomData.setActive(projectDatum.getActive());
                    bomData.setCostCode(projectDatum.getCode());
                    bomData.setProfitMargin(projectDatum.getProfitMargin());
                    bomData.setAdditionalInfo(projectDatum.getAddInfo());
                    bomData.setOverHead(projectDatum.getOverHead());
                    bomData.setMaterialUid(projectDatum.getMaterialUid());
                    // projectDatum.getTax() is null when the project's tool/BOM row was never
                    // given a tax breakdown -- new JSONArray(null) throws NPE (org.json's
                    // JSONTokener wraps the string in a StringReader unconditionally). Default to
                    // an empty array, same convention as ProjectService.bomCostBreakDown.setTax.
                    bomData.setTax(String.valueOf(new JSONArray(projectDatum.getTax() != null ? projectDatum.getTax() : "[]")));
                    bomData.setSourceType(projectDatum.getSourceType());
                    final Project project = projectDatum.getProjectId() == null ? null : projectRepository.findById(projectDatum.getProjectId())
                            .orElseThrow(() -> new NotFoundException("project not found"));
                    bomData.setProject(project);
                    // EAV rewire: taskRepository.findById loads the fat Task entity (SELECT of
                    // every mapped column, most dropped from the live `task` table by the V45
                    // slim-schema migration) and throws at runtime. `task` is used ONLY as a
                    // ManyToOne FK association (bomData.setTask(task) below, never dereferenced
                    // for any other field before bomRepository.saveAll persists it), so a lazy
                    // getReferenceById proxy is a safe drop-in; existsById is a column-safe
                    // `SELECT count(id) ...` existence check preserving the original
                    // NotFoundException contract.
                    Task task = null;
                    if (projectDatum.getTaskOrder() != null) {
                        if (!taskRepository.existsById(projectDatum.getTaskOrder())) {
                            throw new NotFoundException("Task not found");
                        }
                        task = taskRepository.getReferenceById(projectDatum.getTaskOrder());
                    }
                    bomData.setTask(task);
                    bomData.setInvalid(false);
                    bom.add(bomData);
                }
                int bomCount = bomRepository.saveAll(bom).size();
                Integer updateBom = null;
                if (bomCount != 0) {
                    updateBom = bomRepository.setInvalidByIds(bomIds, OffsetDateTime.now());
                }
                if (updateBom != null) {
//                Integer updatedRows =  projectRepository.updateProjectsById(projectIds);
                    Integer updatedRows = projectRepository.updateProjectsStatusById(projectIds, PROJECT_INACTIVE);
                    if (updatedRows > 0) {
                        logger.info("{} projects updated successfully!", updatedRows);
                        List<Project> projects = projectRepository.findAffectedProjectsAndBom(new String[]{PROJECT_INACTIVE}, Arrays.asList(material.getTags().split(";")), material.getName(), materialCostDTO.getVendorMaterialMapId());

                        String updaterUserId = auditLogService.getUserObjectId();

                        for (Project project : projects) {

                            if (project == null || project.getCustomerUid() == null) {
                                continue;
                            }
                            // 1. Send Dirty Bid mail to customer
                            String customerUid = String.valueOf(project.getCustomerUid());
                            UserAccount userAccount = userAccountRepository.findByObjectId(customerUid);

                            if (userAccount != null) {
                                empAccountService.dirtyBidMail(
                                        userAccount.getEmail(),
                                        userAccount.getFirstname() + " " + userAccount.getLastname()
                                );
                            }

                            // 2. Prepare recipients for THIS project
                            String[] recipientIds = prepareRecipientIdsForDirtyBid(
                                    project,
                                    updaterUserId,
                                    customerUid
                            );

                            if (recipientIds == null || recipientIds.length == 0) {
                                continue;
                            }

                            // 3. Send Dirty Bid notification for THIS project
                            try {
                                sendDirtyBidNotification(
                                        recipientIds,
                                        project,
                                        updaterUserId,
                                        material
                                );
                            } catch (Exception e) {
                                logger.error("Dirty Bid notification failed for project {}", project.getId(), e);
                            }
                        }


                    } else {
                        logger.error("No projects were updated.");
                    }

                }
            }
        }
        catch (Exception e) {
            logger.error("An unexpected error occurred in invalidProject:", e);
            if (e instanceof ServerException) {
                throw e;
            } else {
                throw new ServerException(ErrorMessages.INVALID_PROJECT_ERROR, INVALID_PROJECT_ERROR_DESC, e);
            }
        }
    }

    // AB#32920 (Q6): the row keeps its unit and quantity and is priced per that unit; a unit
    // matching neither the base nor the purchase unit is priced at factor 1 and logged once.
    private void priceRowInItsOwnUnit(final Bom bomData, final ProjectToolBomInfo row, final Material material,
                                      final BigDecimal perBaseUnitCost) {
        if (!BomUnitFactor.matchesUnit(material, row.getUnit())) {
            logger.warn("BOM {} of material {}: unit matches neither the base nor the purchase unit; priced at factor 1",
                    row.getId(), row.getMaterialUid());
        }
        bomData.setUnit(row.getUnit());
        bomData.setUnitCost(BomUnitFactor.unitPrice(perBaseUnitCost, material, row.getUnit()));
        bomData.setTotalCost(BomUnitFactor.lineTotal(row.getQuantity(), bomData.getUnitCost()));
    }

    public MaterialInfo getMaterialsBySelectedDateAndMapId(OffsetDateTime selectedDate, final Long vendorMaterialMapId) {
        Integer count = materialCostRepository.findCountBySelectedDateAndMapId(selectedDate, vendorMaterialMapId);
        if (count != 0) {
            return materialCostRepository.findMaterialCostBySelectedDateAndMapId(selectedDate, vendorMaterialMapId);
        } else {
            return null;
        }
    }

    public Object getMinimumMaterialCost(final String[] materials, final String pinCode, final String constructionDate) throws Exception {
        JSONObject input = new JSONObject();
        input.put("bill_of_materials", materials);
        input.put("construction_location", pinCode);
        input.put("check_date", constructionDate);

        var bidJobResult = aseService.findMinimumMaterialCost(String.valueOf(input));
        return bidJobResult;
    }

    private MaterialCostDTO mapToDTO(final MaterialCost materialCost, final MaterialCostDTO materialCostDTO) {
        if (materialCost == null) return materialCostDTO;
        materialCostDTO.setId(materialCost.getId());
        materialCostDTO.setUnit(materialCost.getUnit());
        materialCostDTO.setCost(materialCost.getCost());
        materialCostDTO.setCostType(materialCost.getCostType());
        materialCostDTO.setEffectiveStartdate(materialCost.getEffectiveStartdate());
        materialCostDTO.setEffectiveEnddate(materialCost.getEffectiveEnddate());
        materialCostDTO.setVersionStartdate(materialCost.getVersionStartdate());
        materialCostDTO.setVersionEnddate(materialCost.getVersionEnddate());
        materialCostDTO.setItemCode(materialCost.getItemCode());
        materialCostDTO.setGrade(materialCost.getGrade());
        materialCostDTO.setSellingPriceType(materialCost.getSellingPriceType());
        materialCostDTO.setSellingPriceValue(materialCost.getSellingPriceValue());
        materialCostDTO.setCostPerUnit(materialCost.getCostPerUnit());
        if (materialCost.getVendorMaterialMap() != null) {
            materialCostDTO.setVendorMaterialMapId(materialCost.getVendorMaterialMap().getId());
        }
        if (materialCost.getServiceId() != null) {
            materialCostDTO.setServiceId(materialCost.getServiceId());
        }
        if (materialCost.getCreatedBy() != null) {
            materialCostDTO.setCreatedBy(materialCost.getCreatedBy());
        }
//        materialCostDTO.setMaterial(materialCost.getMaterial() == null ? null : materialCost.getMaterial().getId());
        return materialCostDTO;
    }

    private MaterialCost mapToUpdateEntity(final MaterialDTO materialDTO, final MaterialCost materialCost) {
        materialCost.setCost(materialDTO.getCost());
        materialCost.setUnit(materialDTO.getUnit());
        materialCost.setGrade(materialDTO.getGrade());
        materialCost.setItemCode(materialDTO.getItemCode());
//        materialCost.setCostType(materialDTO.getCostType());
        materialCost.setEffectiveStartdate(OffsetDateTime.now());
        materialCost.setEffectiveEnddate(OffsetDateTime.now());
        materialCost.setVersionStartdate(OffsetDateTime.now());
        materialCost.setVersionEnddate(OffsetDateTime.now());
        // AB#31160: this set the incoming DTO's value directly, with no fallback to what was
        // already there -- unlike MaterialService.update()'s vendor-mapped branch a few lines
        // above its own call into this class, which was written specifically to prevent exactly
        // this. A partial update that only carries a changed Purchase Unit Cost (e.g. via
        // MaterialService.update()'s no-vendor branch, which routes here) silently nulled out
        // Selling Price. Read the entity's CURRENT value before this call overwrites it -- Java
        // evaluates the getter before the assignment runs, so this reads the pre-update value.
        materialCost.setSellingPriceType(materialDTO.getSellingPriceType() != null ? materialDTO.getSellingPriceType() : materialCost.getSellingPriceType());
        materialCost.setSellingPriceValue(materialDTO.getSellingPriceValue() != null ? materialDTO.getSellingPriceValue() : materialCost.getSellingPriceValue());
        materialCost.setCostPerUnit(materialDTO.getCostPerUnit() != null ? materialDTO.getCostPerUnit() : materialCost.getCostPerUnit());
        return materialCost;
    }

    private Long getServiceIdByName(final String serviceName) {
        try {
            return serviceMasterRepository.getIdByName(serviceName);
        } catch (Exception e) {
            logger.error("Error retrieving service id for service name: {}", serviceName, e);
            return null;
        }
    }

    private MaterialCost mapToEntity(final MaterialCostDTO materialCostDTO, final MaterialCost materialCost, final Long vendorMaterialMapId) {
        materialCost.setCost(materialCostDTO.getCost());
        materialCost.setUnit(materialCostDTO.getUnit());
        materialCost.setGrade(materialCostDTO.getGrade());
        materialCost.setItemCode(materialCostDTO.getItemCode());
        materialCost.setEffectiveStartdate(OffsetDateTime.now());
        // Use the DTO's costType when provided; default to USD when it is null.
        materialCost.setCostType(materialCostDTO.getCostType() != null
                ? materialCostDTO.getCostType()
                : "USD");
        materialCost.setVersion(0);
        materialCost.setEffectiveEnddate(OffsetDateTime.now().plusYears(1000));
        materialCost.setVersionStartdate(materialCostDTO.getVersionStartdate());
        materialCost.setVersionEnddate(OffsetDateTime.now().plusYears(1000));
        materialCost.setSellingPriceType(materialCostDTO.getSellingPriceType());
        materialCost.setSellingPriceValue(materialCostDTO.getSellingPriceValue());
        materialCost.setCostPerUnit(materialCostDTO.getCostPerUnit());

        try {
            materialCost.setServiceId(materialCostDTO.getServiceId() != null
                    ? materialCostDTO.getServiceId()
                    : getServiceIdByName("FieldCraft"));
            materialCostDTO.setServiceId(materialCost.getServiceId());
        } catch (Exception e) {
            logger.error("Failed to resolve serviceId; continuing without it.", e);
        }

        final VendorMaterialMap vendorMaterialMap = vendorMaterialMapRepository.findById(vendorMaterialMapId)
                .orElseThrow(() -> new NotFoundException("vendor not found"));
//        materialCost.setMaterial(material);
        materialCost.setVendorMaterialMap(vendorMaterialMap);
        return materialCost;
    }

    private MaterialCostDTO mapToMaterialCostDTO(final MaterialCost materialCost, final MaterialCostDTO materialCostDTO) {
        if (materialCost == null) return materialCostDTO;
        materialCostDTO.setId(materialCost.getId());
        materialCostDTO.setUnit(materialCost.getUnit());
        materialCostDTO.setCost(materialCost.getCost());
        materialCostDTO.setCostType(materialCost.getCostType());
        materialCostDTO.setEffectiveStartdate(materialCost.getEffectiveStartdate());
        materialCostDTO.setEffectiveEnddate(materialCost.getEffectiveEnddate());
        materialCostDTO.setVersionStartdate(materialCost.getVersionStartdate());
        materialCostDTO.setVersionEnddate(materialCost.getVersionEnddate());
        materialCostDTO.setItemCode(materialCost.getItemCode());
        if (materialCost.getServiceId() != null) {
            materialCostDTO.setServiceId(materialCost.getServiceId());
        }
        if (materialCost.getCreatedBy() != null) {
            materialCostDTO.setCreatedBy(materialCost.getCreatedBy());
        }
//        materialCostDTO.setMaterial(materialCost.getMaterial() == null ? null : materialCost.getMaterial().getId());
        return materialCostDTO;
    }

    public String[] prepareRecipientIdsForDirtyBid(Project project, String updaterUserId,String customerUid) {

        Set<String> recipients = new HashSet<>();

        String teamMembersJson = project.getTeamMembers();

        if (teamMembersJson != null && !teamMembersJson.isBlank()) {
            JSONArray jsonArray = new JSONArray(teamMembersJson);
            for (int i = 0; i < jsonArray.length(); i++) {
                recipients.add(jsonArray.getString(i));
            }
        }
        recipients.add(updaterUserId);
        recipients.add(customerUid);

        return recipients.toArray(new String[0]);
    }

    private void sendDirtyBidNotification(
            String[] userIds,
            Project project,
            String senderId,
            Material material
    ) throws JsonProcessingException {

        String orderNo = Optional.ofNullable(project.getOrderNo()).orElse("N/A");
        String projectName = Optional.ofNullable(project.getDescription()).orElse("N/A");
        String materialName = Optional.ofNullable(material.getName()).orElse("N/A");

        Map<String, Object> payload = new HashMap<>();
        payload.put("message", DIRTY_BID_MATERIAL_PRICE_UPDATE_MESSAGE);
        payload.put("projectId", project.getId());
        payload.put("projectName", projectName);
        payload.put("orderNo", orderNo);
        payload.put("materialName", materialName);


        ObjectMapper mapper = new ObjectMapper();
        String messageContent = mapper.writeValueAsString(payload);

        NotificationMessage notificationMessage = new NotificationMessage(
                DIRTY_BID_NOTIFICATION_TITLE,
                senderId,
                userIds,
                MessageDeliveryType.Instant
        );

        notificationMessage.setMessageId(DIRTY_BID_PRICE_UPDATED);
        notificationMessage.setMessageContent(messageContent);

        // Push copy is the fixed DIRTY_BID_* sentence, which names no amount and no
        // customer -- only that the price moved and the estimate needs a look.
        notificationDispatcher.send(notificationMessage, FcmEventType.DIRTY_BID_UPDATED,
                DIRTY_BID_NOTIFICATION_TITLE, DIRTY_BID_MATERIAL_PRICE_UPDATE_MESSAGE,
                Map.of(FcmDataKeys.ORDER_NO, orderNo, FcmDataKeys.PROJECT_DB_ID,
                        String.valueOf(project.getId())));
    }

}

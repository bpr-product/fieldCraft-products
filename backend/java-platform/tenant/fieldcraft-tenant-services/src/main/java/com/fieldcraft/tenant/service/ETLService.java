/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 25-Mar-2025
 * @author      : Abirami.R
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : ETL - Data Extraction for Answers & Materials
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE     |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *    4119        | 25-03-2025 | Abirami      | Data Extraction for Answers & Materials
 *    4359        | 23-04-2025 | Abirami      | Enhancing the ETL process to support transformation of additional project statuses
 */
package com.fieldcraft.tenant.service;

import com.fieldcraft.tenant.dal.repos.AnswersRepository;
import com.fieldcraft.tenant.dal.repos.BomRepository;
import com.fieldcraft.tenant.dal.repos.MaterialRepository;
import com.fieldcraft.tenant.data.model.ProjectBomVendorInfo;
import com.fieldcraft.tenant.data.status.ProjectStatus;
import com.fieldcraft.tenant.service.recommendation.RecommendationIngestService;
import com.fieldcraft.tenant.utils.ETLDataTransformationUtil;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.ReplaceOptions;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.bson.Document;

import java.util.*;
import java.util.stream.Collectors;

public class ETLService {
    private final AnswersRepository answersRepository;
    private final MaterialRepository materialRepository;
    private final BomRepository bomRepository;

    private final String etlDbConnection;
    private final RecommendationIngestService recommendationIngestService;

    private static final Logger logger = LogManager.getLogger(ETLService.class);

    public ETLService(
            AnswersRepository answersRepository,
            MaterialRepository materialRepository,
            BomRepository bomRepository,
            String etlDbConnection) {
        this(answersRepository, materialRepository, bomRepository, etlDbConnection, null);
    }

    public ETLService(
            AnswersRepository answersRepository,
            MaterialRepository materialRepository,
            BomRepository bomRepository,
            String etlDbConnection,
            RecommendationIngestService recommendationIngestService) {
        this.answersRepository = answersRepository;
        this.materialRepository = materialRepository;
        this.bomRepository = bomRepository;
        this.etlDbConnection = etlDbConnection;
        this.recommendationIngestService = recommendationIngestService;
    }

    public boolean executeETL(String projectId, String materialId, String tenantId) {
        boolean isAnswerProcessed = false;
        boolean isMaterialProcessed = false;


        try (MongoClient mongoClient = MongoClients.create(etlDbConnection)) {
            MongoDatabase database = mongoClient.getDatabase("FieldCraftDataWarehouse");

            if (projectId != null && !projectId.isEmpty()) {
                isAnswerProcessed = processProjectAnswers(projectId, tenantId, database);
            }

            if (materialId != null && !materialId.isEmpty()) {
                isMaterialProcessed = processMaterialsForMaterialId(materialId, tenantId, database);
            }



            return isAnswerProcessed || isMaterialProcessed;

        }catch (NoClassDefFoundError e) {
            logger.error("Exception Handled in function" + e.getMessage(), e);
            return true;
        }catch (Exception e) {
            logger.error("Error in executeETL: " + e.getMessage(), e);
            return false;
        }
    }

    private boolean processProjectAnswers(String projectId, String tenantId, MongoDatabase db) {
        try {
            Long pid = Long.parseLong(projectId);

            String[] excludedProjectStatuses = {"bidReview", "awaitingEstimation"};
            List<Object[]> rawData =
                    answersRepository.fetchAnswersByProjectId(pid, "A.1", excludedProjectStatuses);

            if (rawData.isEmpty()) return false;

            // 🔹 Fetch BOM data
            List<ProjectBomVendorInfo> bomData =
                    bomRepository.findBomVendorDetailsByProjectId(pid);

            List<Map<String, Object>> answers =
                    mapAnswerDataRows(rawData, tenantId);

            List<Map<String, Object>> bomMaterials =
                    mapBomDataRows(bomData, tenantId, pid);

            // 🔹 Merge BOM into answers document
            answers.forEach(a -> a.put("BomMaterials", bomMaterials));

            List<Map<String, Object>> transformed =
                    ETLDataTransformationUtil.transformData(answers);

            boolean updated = loadTransformedData(
                    "answers",
                    "ProjectId",
                    pid,
                    transformed,
                    db,
                    List.of("startDate", "endDate", "answersData")
            );
            if (updated && recommendationIngestService != null) {
                Object statusRaw = rawData.get(0)[3];
                boolean isClosed = ProjectStatus.PROJECT_CLOSED.equals(
                        statusRaw != null ? statusRaw.toString() : null);
                if (isClosed) {
                    Object orderNoRaw = rawData.get(0)[11];
                    String orderNo = orderNoRaw != null ? orderNoRaw.toString().trim() : null;
                    recommendationIngestService.ingestCompletedBid(orderNo);
                }
            }
            return updated;

        } catch (Exception e) {
            logger.error("Error in processProjectAnswers", e);
            return false;
        }
    }

    private boolean processMaterialsForMaterialId(String materialId, String tenantId, MongoDatabase db) {
        try {
            Long id = Long.parseLong(materialId);
            List<Object[]> rawData = materialRepository.fetchMaterialsByMaterialId(id);

            if (rawData.isEmpty()) return false;

            List<Map<String, Object>> transformed = ETLDataTransformationUtil.transformData(
                    mapMaterialDataRows(rawData, tenantId)
            );

            return loadTransformedData("materials", "materialId", id, transformed, db, List.of("effective_startdate", "effective_enddate"));

        } catch (Exception e) {
            logger.error("Error in processMaterial: " + e.getMessage(), e);
            return false;
        }
    }

    private boolean loadTransformedData(
            String collection,
            String key,
            Long id,
            List<Map<String, Object>> newDocs,
            MongoDatabase db,
            List<String> ignoreFields
    ) {
        boolean updated = false;

        for (Map<String, Object> doc : newDocs) {
            try {
                Document mongoDoc = new Document();
                doc.forEach(mongoDoc::append);

                Object keyValue = doc.get(key);
                Document filter = new Document(key, keyValue);

                ReplaceOptions options = new ReplaceOptions().upsert(true);
                var result = db.getCollection(collection).replaceOne(filter, mongoDoc, options);

                if (result.getModifiedCount() > 0 || result.getUpsertedId() != null) {
                    updated = true;
                    logger.info("Document upserted for {} = {}", key, keyValue);
                }

            } catch (Exception e) {
                logger.error("Failed to upsert document for {} = {}: {}", key, doc.get(key), e.getMessage(), e);
            }
        }

        return updated;
    }



    private List<Map<String, Object>> mapAnswerDataRows(List<Object[]> data, String tenantId) {
        return data.stream().map(row -> {
            Map<String, Object> map = new java.util.HashMap<>();
            map.put("ProjectId", row[0]);
            map.put("AnswersData", row[1]);
            map.put("Description", row[2]);
            map.put("Status", row[3]);
            map.put("CustomerAsset", row[4]);
            map.put("Trade", row[5]);
            map.put("PaymentConfiguration", row[6]);
            map.put("TotalCost", row[7]);
            map.put("EndDate", row[8]);
            map.put("EstimateInvalid", row[9]);
            map.put("ScheduleInvalid", row[10]);
            map.put("OrderNo", row[11]);
            map.put("TenantId", tenantId);
            return map;
        }).collect(Collectors.toList());
    }

    private List<Map<String, Object>> mapMaterialDataRows(List<Object[]> data, String tenantId) {
        return data.stream().map(row -> {
            Map<String, Object> map = new java.util.HashMap<>();
            map.put("MaterialId", row[0]);
            map.put("VendorId", row[1]);
            map.put("ZipCode", row[2]);
            map.put("EffectiveStartDate", row[3]);
            map.put("EffectiveEndDate", row[4]);
            map.put("LeadTime", row[5]);
            map.put("Cost", row[6]);
            map.put("TenantId", tenantId);
            map.put("Availability", "Yes");
            return map;
        }).collect(Collectors.toList());
    }

    private List<Map<String, Object>> mapBomDataRows(
            List<ProjectBomVendorInfo> data,
            String tenantId,
            Long projectId
    ) {
        return data.stream().map(b -> {
            Map<String, Object> map = new HashMap<>();
            map.put("ProjectId", projectId);
            map.put("BomId", b.getBomId());
            map.put("MaterialName", b.getMaterialName());
            map.put("CostCode", b.getCostCode());
            map.put("Quantity", b.getQuantity());
            map.put("Unit", b.getUnit());
            map.put("UnitCost", b.getUnitCost());
            map.put("TotalCost", b.getTotalCost());
            map.put("OverHead", b.getOverHead());
            map.put("ProfitMargin", b.getProfitMargin());
            map.put("Tax", b.getTax());

            map.put("VendorMaterialMapId", b.getVendorMaterialMapId());
            map.put("VendorId", b.getVendorId());
            map.put("VendorName", b.getCompanyName());

            map.put("MaterialId", b.getMaterialId());
            map.put("Grade", b.getGrade());

            map.put("TenantId", tenantId);
            return map;
        }).collect(Collectors.toList());
    }

}



//    private boolean loadTransformedData(
//            String collection,
//            String key,
//            Long id,
//            List<Map<String, Object>> newDocs,
//            MongoDatabase db,
//            List<String> ignoreFields
//    ) {
//        List<Document> existingDocs = db.getCollection(collection)
//                .find(new Document(key, id))
//                .into(new ArrayList<>());
//
//        List<Map<String, Object>> updated = newDocs.stream()
//                .filter(doc -> checkIfRecordModified(doc, existingDocs, ignoreFields))
//                .collect(Collectors.toList());
//
//        if (updated.isEmpty()) return false;
//
//        for (Map<String, Object> doc : updated) {
//            Document mongoDoc = new Document();
//            doc.forEach(mongoDoc::append);
//            db.getCollection(collection).insertOne(mongoDoc);
//        }
//
//        return true;
//    }


//    private boolean checkIfRecordModified(Map<String, Object> newDoc, List<Document> existingDocs, List<String> ignoreFields) {
//        for (Document doc : existingDocs) {
//            Map<String, Object> existingMap = new TreeMap<>(doc);
//            existingMap.remove("_id");
//            ignoreFields.forEach(existingMap::remove);
//
//            Map<String, Object> newMap = new TreeMap<>(newDoc);
//            ignoreFields.forEach(newMap::remove);
//
//            if (Objects.equals(existingMap, newMap)) {
//                return false;
//            }
//        }
//        return true;
//    }
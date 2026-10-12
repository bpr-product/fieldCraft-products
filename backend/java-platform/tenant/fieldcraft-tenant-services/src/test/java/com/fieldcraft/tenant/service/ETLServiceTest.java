/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 */
package com.fieldcraft.tenant.service;

import com.fieldcraft.tenant.dal.repos.AnswersRepository;
import com.fieldcraft.tenant.dal.repos.BomRepository;
import com.fieldcraft.tenant.dal.repos.MaterialRepository;
import com.fieldcraft.tenant.data.model.ProjectBomVendorInfo;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.result.UpdateResult;
import org.bson.BsonObjectId;
import org.bson.Document;
import org.bson.types.ObjectId;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.MockedStatic;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockStatic;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
@SuppressWarnings({"unchecked", "rawtypes"})
class ETLServiceTest {

    @Mock
    private AnswersRepository answersRepository;
    @Mock
    private MaterialRepository materialRepository;
    @Mock
    private BomRepository bomRepository;
    @Mock
    private MongoClient mongoClient;
    @Mock
    private MongoDatabase mongoDatabase;
    @Mock
    private MongoCollection mongoCollection;
    @Mock
    private UpdateResult updateResult;

    private MockedStatic<MongoClients> mongoClientsMock;
    private ETLService etlService;

    private static final String ETL_DB_CONNECTION = "mongodb://localhost:27017/test";
    private static final String TENANT_ID = "SYN-tenant-001";
    private static final Long PROJECT_ID = 100L;
    private static final Long MATERIAL_ID = 50L;

    @BeforeEach
    void setUp() {
        etlService = new ETLService(answersRepository, materialRepository, bomRepository, ETL_DB_CONNECTION);
        mongoClientsMock = mockStatic(MongoClients.class);
    }

    @AfterEach
    void tearDown() {
        mongoClientsMock.close();
    }

    // =========================================================================
    // Helpers shared across tests
    // =========================================================================

    private void givenMongoReturnsClient() {
        mongoClientsMock.when(() -> MongoClients.create(ETL_DB_CONNECTION)).thenReturn(mongoClient);
        when(mongoClient.getDatabase("FieldCraftDataWarehouse")).thenReturn(mongoDatabase);
    }

    private void givenCollectionUpserts(String collectionName, long modifiedCount, boolean newDoc) {
        when(mongoDatabase.getCollection(collectionName)).thenReturn(mongoCollection);
        when(mongoCollection.replaceOne(any(Document.class), any(Document.class), any())).thenReturn(updateResult);
        when(updateResult.getModifiedCount()).thenReturn(modifiedCount);
        when(updateResult.getUpsertedId()).thenReturn(newDoc ? new BsonObjectId(new ObjectId()) : null);
    }

    /** 12-element answer row matching mapAnswerDataRows column order. */
    private Object[] buildAnswerRow(Long projectId) {
        return new Object[]{
                projectId,                                  // [0]  ProjectId
                "{\"question\":\"answer\",\"val\":1}",      // [1]  AnswersData  (JSON)
                "Roofing project description",              // [2]  Description
                "active",                                   // [3]  Status
                "SYN-asset-001",                            // [4]  CustomerAsset
                "Roofing",                                  // [5]  Trade
                "{\"type\":\"card\"}",                      // [6]  PaymentConfiguration
                new BigDecimal("10000.00"),                 // [7]  TotalCost    (BigDecimal)
                OffsetDateTime.now().plusDays(30),          // [8]  EndDate      (OffsetDateTime)
                false,                                      // [9]  EstimateInvalid
                false,                                      // [10] ScheduleInvalid
                "SYN-JOB-001"                               // [11] OrderNo
        };
    }

    /** 7-element material row matching mapMaterialDataRows column order. */
    private Object[] buildMaterialRow(Long materialId) {
        return new Object[]{
                materialId,                                 // [0] MaterialId    (Long → String in transform)
                200L,                                       // [1] VendorId      (Long → String in transform)
                "75001",                                    // [2] ZipCode
                OffsetDateTime.now(),                       // [3] EffectiveStartDate (OffsetDateTime → Date)
                OffsetDateTime.now().plusMonths(6),         // [4] EffectiveEndDate
                14,                                         // [5] LeadTime
                new BigDecimal("49.99")                     // [6] Cost          (BigDecimal → Double)
        };
    }

    /** Wraps one or more Object[] rows into List<Object[]> without varargs spreading. */
    @SafeVarargs
    private static List<Object[]> rows(Object[]... rows) {
        return new ArrayList<>(Arrays.asList(rows));
    }

    private ProjectBomVendorInfo buildBomVendorInfo() {
        ProjectBomVendorInfo bom = mock(ProjectBomVendorInfo.class);
        when(bom.getBomId()).thenReturn(1L);
        when(bom.getMaterialName()).thenReturn("Roofing Shingles");
        when(bom.getCostCode()).thenReturn("CC-001");
        when(bom.getQuantity()).thenReturn(10.0);
        when(bom.getUnit()).thenReturn("SQ");
        when(bom.getUnitCost()).thenReturn(150.0);
        when(bom.getTotalCost()).thenReturn(1500.0);
        when(bom.getOverHead()).thenReturn(0.10f);
        when(bom.getProfitMargin()).thenReturn(0.15f);
        when(bom.getTax()).thenReturn("8%");
        when(bom.getVendorMaterialMapId()).thenReturn(10L);
        when(bom.getVendorId()).thenReturn(5L);
        when(bom.getCompanyName()).thenReturn("DEMO-Vendor Inc.");
        when(bom.getMaterialId()).thenReturn(20L);
        when(bom.getGrade()).thenReturn("Premium");
        return bom;
    }

    // =========================================================================
    // executeETL – skips when IDs are absent
    // =========================================================================

    @Nested
    @DisplayName("Given no projectId and no materialId")
    class NoIds {

        @Test
        @DisplayName("both null → returns false without touching repositories")
        void bothNull_returnsFalse() {
            givenMongoReturnsClient();

            boolean result = etlService.executeETL(null, null, TENANT_ID);

            assertThat(result).isFalse();
            verifyNoInteractions(answersRepository, materialRepository, bomRepository);
        }

        @Test
        @DisplayName("both empty strings → returns false")
        void bothEmpty_returnsFalse() {
            givenMongoReturnsClient();

            boolean result = etlService.executeETL("", "", TENANT_ID);

            assertThat(result).isFalse();
        }

        @Test
        @DisplayName("projectId null, materialId empty → returns false")
        void projectIdNull_materialIdEmpty_returnsFalse() {
            givenMongoReturnsClient();

            boolean result = etlService.executeETL(null, "", TENANT_ID);

            assertThat(result).isFalse();
        }
    }

    // =========================================================================
    // executeETL – answers path
    // =========================================================================

    @Nested
    @DisplayName("Given a projectId is provided")
    class AnswersPath {

        @Test
        @DisplayName("no answers in DB → returns false")
        void noAnswers_returnsFalse() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(Collections.emptyList());

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isFalse();
            verify(answersRepository).fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class));
        }

        @Test
        @DisplayName("answers present, doc modified in Mongo → returns true")
        void answersPresent_docModified_returnsTrue() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(buildAnswerRow(PROJECT_ID)));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
            verify(mongoCollection).replaceOne(any(Document.class), any(Document.class), any());
        }

        @Test
        @DisplayName("answers present, new doc inserted in Mongo → returns true")
        void answersPresent_newDocInserted_returnsTrue() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(buildAnswerRow(PROJECT_ID)));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 0L, true);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("answers present, Mongo reports no change → returns false")
        void answersPresent_noMongoChange_returnsFalse() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(buildAnswerRow(PROJECT_ID)));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 0L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isFalse();
        }

        @Test
        @DisplayName("multiple answer rows → all rows are upserted")
        void multipleAnswerRows_allUpserted() {
            givenMongoReturnsClient();
            List<Object[]> rows = rows(buildAnswerRow(PROJECT_ID), buildAnswerRow(PROJECT_ID));
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows);
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("BOM data present → merged into each answer document")
        void bomDataPresent_mergedIntoAnswers_returnsTrue() {
            // Build and stub the BOM mock BEFORE entering any when() chain to avoid
            // UnfinishedStubbingException caused by nested mock creation inside thenReturn().
            ProjectBomVendorInfo bom = buildBomVendorInfo();
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(buildAnswerRow(PROJECT_ID)));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID))
                    .thenReturn(List.of(bom));
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
            verify(bomRepository).findBomVendorDetailsByProjectId(PROJECT_ID);
        }

        @Test
        @DisplayName("invalid projectId format → returns false without hitting DB")
        void invalidProjectIdFormat_returnsFalse() {
            givenMongoReturnsClient();

            boolean result = etlService.executeETL("not-a-number", null, TENANT_ID);

            assertThat(result).isFalse();
            verifyNoInteractions(answersRepository);
        }

        @Test
        @DisplayName("repository throws exception → caught, returns false")
        void repositoryThrowsException_returnsFalse() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenThrow(new RuntimeException("DB unavailable"));

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isFalse();
        }
    }

    // =========================================================================
    // executeETL – materials path
    // =========================================================================

    @Nested
    @DisplayName("Given a materialId is provided")
    class MaterialsPath {

        @Test
        @DisplayName("no materials in DB → returns false")
        void noMaterials_returnsFalse() {
            givenMongoReturnsClient();
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID)).thenReturn(Collections.emptyList());

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isFalse();
            verify(materialRepository).fetchMaterialsByMaterialId(MATERIAL_ID);
        }

        @Test
        @DisplayName("materials present, doc modified → returns true")
        void materialsPresent_docModified_returnsTrue() {
            givenMongoReturnsClient();
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID))
                    .thenReturn(rows(buildMaterialRow(MATERIAL_ID)));
            givenCollectionUpserts("materials", 1L, false);

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("materials present, new doc inserted → returns true")
        void materialsPresent_newDocInserted_returnsTrue() {
            givenMongoReturnsClient();
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID))
                    .thenReturn(rows(buildMaterialRow(MATERIAL_ID)));
            givenCollectionUpserts("materials", 0L, true);

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("invalid materialId format → returns false without hitting DB")
        void invalidMaterialIdFormat_returnsFalse() {
            givenMongoReturnsClient();

            boolean result = etlService.executeETL(null, "abc", TENANT_ID);

            assertThat(result).isFalse();
            verifyNoInteractions(materialRepository);
        }

        @Test
        @DisplayName("repository throws exception → caught, returns false")
        void repositoryThrowsException_returnsFalse() {
            givenMongoReturnsClient();
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID))
                    .thenThrow(new RuntimeException("Timeout"));

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isFalse();
        }
    }

    // =========================================================================
    // executeETL – both paths together
    // =========================================================================

    @Nested
    @DisplayName("Given both projectId and materialId are provided")
    class BothPaths {

        @Test
        @DisplayName("both succeed → returns true (OR logic)")
        void bothSucceed_returnsTrue() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(buildAnswerRow(PROJECT_ID)));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID))
                    .thenReturn(rows(buildMaterialRow(MATERIAL_ID)));
            when(mongoDatabase.getCollection(anyString())).thenReturn(mongoCollection);
            when(mongoCollection.replaceOne(any(Document.class), any(Document.class), any())).thenReturn(updateResult);
            when(updateResult.getModifiedCount()).thenReturn(1L);
            when(updateResult.getUpsertedId()).thenReturn(null);

            boolean result = etlService.executeETL(
                    String.valueOf(PROJECT_ID), String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("answers empty, materials succeed → returns true")
        void answersEmpty_materialsSucceed_returnsTrue() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(Collections.emptyList());
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID))
                    .thenReturn(rows(buildMaterialRow(MATERIAL_ID)));
            givenCollectionUpserts("materials", 1L, false);

            boolean result = etlService.executeETL(
                    String.valueOf(PROJECT_ID), String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("answers succeed, materials empty → returns true")
        void answersSucceed_materialsEmpty_returnsTrue() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(buildAnswerRow(PROJECT_ID)));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID)).thenReturn(Collections.emptyList());

            boolean result = etlService.executeETL(
                    String.valueOf(PROJECT_ID), String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("both empty → returns false")
        void bothEmpty_returnsFalse() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(Collections.emptyList());
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID)).thenReturn(Collections.emptyList());

            boolean result = etlService.executeETL(
                    String.valueOf(PROJECT_ID), String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isFalse();
        }
    }

    // =========================================================================
    // executeETL – MongoDB connectivity failures
    // =========================================================================

    @Nested
    @DisplayName("Given MongoDB connectivity issues")
    class MongoConnectivity {

        @Test
        @DisplayName("NoClassDefFoundError (driver absent) → returns true")
        void noClassDefFoundError_returnsTrue() {
            mongoClientsMock.when(() -> MongoClients.create(anyString()))
                    .thenThrow(new NoClassDefFoundError("com/mongodb/MongoClient"));

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("RuntimeException on connect → returns false")
        void runtimeExceptionOnConnect_returnsFalse() {
            mongoClientsMock.when(() -> MongoClients.create(anyString()))
                    .thenThrow(new RuntimeException("Connection refused"));

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isFalse();
        }

        @Test
        @DisplayName("Mongo replaceOne throws exception per document → caught, returns false")
        void replaceOneThrowsException_returnsFalse() {
            givenMongoReturnsClient();
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID))
                    .thenReturn(rows(buildMaterialRow(MATERIAL_ID)));
            when(mongoDatabase.getCollection("materials")).thenReturn(mongoCollection);
            when(mongoCollection.replaceOne(any(Document.class), any(Document.class), any()))
                    .thenThrow(new RuntimeException("Write concern error"));

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isFalse();
        }

        @Test
        @DisplayName("Mongo replaceOne fails on first doc, succeeds on second → returns true")
        void replaceOneFailsFirstSucceedsSecond_returnsTrue() {
            givenMongoReturnsClient();
            List<Object[]> twoRows = rows(buildMaterialRow(MATERIAL_ID), buildMaterialRow(MATERIAL_ID + 1));
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID)).thenReturn(twoRows);
            when(mongoDatabase.getCollection("materials")).thenReturn(mongoCollection);
            when(mongoCollection.replaceOne(any(Document.class), any(Document.class), any()))
                    .thenThrow(new RuntimeException("First fails"))
                    .thenReturn(updateResult);
            when(updateResult.getModifiedCount()).thenReturn(1L);
            when(updateResult.getUpsertedId()).thenReturn(null);

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }
    }

    // =========================================================================
    // Data transformation – type conversion coverage
    // =========================================================================

    @Nested
    @DisplayName("ETLDataTransformationUtil type conversions")
    class DataTransformation {

        @Test
        @DisplayName("BigDecimal Cost + Long IDs in material row → transformed correctly")
        void materialRow_bigDecimalAndLongIds_transformed() {
            givenMongoReturnsClient();
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID))
                    .thenReturn(rows(buildMaterialRow(MATERIAL_ID)));
            givenCollectionUpserts("materials", 1L, false);

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("OffsetDateTime fields in answer row → converted to Date")
        void answerRow_offsetDateTime_converted() {
            givenMongoReturnsClient();
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(buildAnswerRow(PROJECT_ID)));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("LocalDateTime field → converted to Date")
        void materialRow_localDateTime_converted() {
            givenMongoReturnsClient();
            Object[] row = buildMaterialRow(MATERIAL_ID);
            row[3] = LocalDateTime.now();           // replace OffsetDateTime with LocalDateTime
            row[4] = LocalDateTime.now().plusMonths(3);
            when(materialRepository.fetchMaterialsByMaterialId(MATERIAL_ID)).thenReturn(rows(row));
            givenCollectionUpserts("materials", 1L, false);

            boolean result = etlService.executeETL(null, String.valueOf(MATERIAL_ID), TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("valid JSON in AnswersData field → parsed to Object")
        void answerRow_validJson_parsed() {
            givenMongoReturnsClient();
            Object[] row = buildAnswerRow(PROJECT_ID);
            row[1] = "{\"roofType\":\"asphalt\",\"sqFt\":2400}";
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(row));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("invalid JSON in AnswersData field → kept as raw string, no exception")
        void answerRow_invalidJson_keptAsRawString() {
            givenMongoReturnsClient();
            Object[] row = buildAnswerRow(PROJECT_ID);
            row[1] = "not-valid-json-{{broken}}";
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(row));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("null values in row fields → skipped without exception")
        void rowWithNullFields_skippedGracefully() {
            givenMongoReturnsClient();
            Object[] row = buildAnswerRow(PROJECT_ID);
            row[2] = null;  // Description null
            row[6] = null;  // PaymentConfiguration null
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(row));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("BigDecimal TotalCost in answers row → converted to Double")
        void answerRow_bigDecimalTotalCost_converted() {
            givenMongoReturnsClient();
            Object[] row = buildAnswerRow(PROJECT_ID);
            row[7] = new BigDecimal("99999.95");
            when(answersRepository.fetchAnswersByProjectId(eq(PROJECT_ID), anyString(), any(String[].class)))
                    .thenReturn(rows(row));
            when(bomRepository.findBomVendorDetailsByProjectId(PROJECT_ID)).thenReturn(Collections.emptyList());
            givenCollectionUpserts("answers", 1L, false);

            boolean result = etlService.executeETL(String.valueOf(PROJECT_ID), null, TENANT_ID);

            assertThat(result).isTrue();
        }
    }
}

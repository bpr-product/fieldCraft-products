/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 15-MAY-2025
 * @author      : Kanimozhi
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Rest Api for Request Service
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
package com.fieldcraft.tenant.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fieldcraft.customer.data.domain.Asset;
import com.fieldcraft.customer.data.domain.AssetDocument;
import com.fieldcraft.customer.data.domain.AssetServiceRequest;
import com.fieldcraft.customer.data.model.AssetDocumentDTO;
import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.external.dal.repos.ServiceRequestRepository;
import com.fieldcraft.external.dal.repos.ServiceSubscriberRepository;
import com.fieldcraft.external.data.domain.ServiceMaster;
import com.fieldcraft.external.data.domain.ServiceRequest;
import com.fieldcraft.external.data.domain.ServiceSubscriber;
import com.fieldcraft.external.data.model.ServiceConfigurationDTO;
import com.fieldcraft.external.data.model.ServiceRequestDTO;
import com.fieldcraft.shared.error.ErrorMessages;
import com.fieldcraft.shared.error.NotFoundException;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.shared.utils.ErrorParser;
import com.fieldcraft.tenant.config.ApplicationContextProvider;
import com.fieldcraft.tenant.saas.CustomerDocumentStorageService;
import com.fieldcraft.tenant.saas.ExternalServices;
import com.fieldcraft.tenantcustomer.dal.repos.AssetDocumentRepository;
import com.fieldcraft.tenantcustomer.dal.repos.AssetRepository;
import com.fieldcraft.tenantcustomer.dal.repos.AssetServiceRequestRepository;
import jakarta.servlet.Filter;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.hibernate.Hibernate;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.ConnectException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

import static com.fieldcraft.shared.error.ErrorMessages.*;
import static com.fieldcraft.tenant.data.constant.DomainConstant.EAGLEVIEW;

@Service
public class RequestService extends ServiceBase{
    private final ServiceRequestRepository serviceRequestRepository;
    private final ServiceSubscriberRepository serviceSubscriberRepository;
    private final ServiceMasterRepository serviceMasterRepository;
    private final AssetDocumentRepository assetDocumentRepository;
    private final ExternalServices externalServices;
    private final AssetRepository assetRepository;
    private final MasterConfigurationService masterConfigurationService;
    private final AssetServiceRequestRepository assetServiceRequestRepository;
    private final CustomerDocumentStorageService customerDocumentStorageService;

    public RequestService(ServiceRequestRepository serviceRequestRepository,
                          ServiceSubscriberRepository serviceSubscriberRepository,
                          ServiceMasterRepository serviceMasterRepository, AssetDocumentRepository assetDocumentRepository,
                          ExternalServices externalServices, AssetRepository assetRepository,
                          MasterConfigurationService masterConfigurationService,
                          AssetServiceRequestRepository assetServiceRequestRepository,
                          CustomerDocumentStorageService customerDocumentStorageService,
                          ApplicationContextProvider appCtxProvider) {
        super(appCtxProvider);
        this.serviceRequestRepository = serviceRequestRepository;
        this.serviceSubscriberRepository = serviceSubscriberRepository;
        this.serviceMasterRepository = serviceMasterRepository;
        this.assetDocumentRepository = assetDocumentRepository;
        this.externalServices = externalServices;
        this.assetRepository = assetRepository;
        this.masterConfigurationService = masterConfigurationService;
        this.assetServiceRequestRepository = assetServiceRequestRepository;

        this.customerDocumentStorageService = customerDocumentStorageService;
    }

    private static final Logger logger = LogManager.getLogger(Filter.class);

    public Boolean placeOrderRequest(ServiceRequestDTO dto) throws Exception {
        super.hasPermissionEx(EAGLEVIEW);

        try {
            // Validate Order
            validateRequestDTO(dto);

            // Validate Subscriber Status
            Long serviceSubscriberId = dto.getServiceSubscriber();
            ServiceSubscriber serviceSubscriber = fetchServiceSubscriber(serviceSubscriberId);
            validateServiceStatus(serviceSubscriber);

            // Validate Service Status
            ServiceMaster serviceMaster = serviceSubscriber.getServiceMaster();
            validateServiceMasterStatus(serviceMaster);

            // Parse assetId from incoming DTO's request JSON
            Map<String, Object> dtoJson = dto.getRequest();
            Long assetId = parseAssetId(dtoJson);

            // Try to find an existing order for this asset
            List<ServiceRequest> existingOrders = serviceRequestRepository.findByAssetIdAndSubscriberId(String.valueOf(assetId), serviceSubscriberId);

            ServiceRequest serviceRequest;
            boolean isModified;

            // Update existing order
            if (!existingOrders.isEmpty()) {
                ServiceRequest existingOrder = existingOrders.get(0);
                if (existingOrder.getResponse() == null) {
                    // Existing order found and not yet placed, allow to proceed
                    serviceRequest = existingOrder;

                    JsonNode jsonNode = new ObjectMapper().readTree(serviceRequest.getRequest());
                    isModified = jsonNode.has("isModified") && jsonNode.get("isModified").asBoolean();
                } else {
                    // Existing order found and already placed (response is not null)
                    throw new ServerException("Order already placed for this asset and subscriber.");
                }
            } else {
                // Place new order
                serviceRequest = createAndSaveServiceRequest(dto);

                JsonNode jsonNode = new ObjectMapper().readTree(serviceRequest.getRequest());
                if (!jsonNode.has("assetId") || !jsonNode.has("isModified")) {
                    throw new ServerException("Invalid request JSON: 'assetId' or 'isModified' not found.");
                }
                isModified = jsonNode.get("isModified").asBoolean();
                Asset asset = assetRepository.findById(assetId).orElseThrow(() -> new IllegalArgumentException("Asset not found with ID: " + assetId));
                AssetServiceRequest assetServiceRequest = new AssetServiceRequest();
                mapToAssetRequestEntity(assetServiceRequest, serviceRequest);
                assetServiceRequest.setAsset(asset);
                assetServiceRequestRepository.save(assetServiceRequest);
            }

            JSONObject reqData = prepareOrderRequestData(serviceRequest, serviceSubscriber, isModified, assetId);
            int maxRetries = 5;
            for (int attempt = 1; attempt <= maxRetries; attempt++) {
                try {
                    externalServices.orderRequest(reqData, getBearerToken());
                    break; // Success, exit retry loop
                } catch (ConnectException ce) {
                    if (attempt == maxRetries) {
                        logger.error(ce);
                        throw new ServerException("Connection failed after " + maxRetries + " attempts.", ce);
                    }
                    // Optional: Wait 1 second before retrying
                    try {
                        Thread.sleep(1000);
                    } catch (InterruptedException ie) {
                        logger.error(ie);
                        Thread.currentThread().interrupt(); // Good practice
                        throw new ServerException("Retry interrupted.", ie);
                    }
                }
            }
            return true;

        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                logger.error(e);
                throw new ServerException("Duplicate entry: Order already exists.");
            } else {
                logger.error(e);
                throw new ServerException("Data integrity violation: " + e.getMessage());
            }
        } catch (Exception e) {
            logger.error("Unexpected error: " + (e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName()),e);
            if (e instanceof ServerException) {
                throw e; // Re-throw if it's a ServerException
            } else {
                throw new ServerException(ErrorMessages.PLACE_ORDER_REQUEST_ERROR,PLACE_ORDER_REQUEST_ERROR_DESC, e);
            }
        }
    }

    private void validateServiceMasterStatus(ServiceMaster serviceMaster) {
        if (serviceMaster == null || !"Active".equalsIgnoreCase(serviceMaster.getStatus())) {
            throw new ServerException("Service is not available now.");
        }
    }

    private void validateServiceStatus(ServiceSubscriber serviceSubscriber) {
        if (!"Active".equalsIgnoreCase(serviceSubscriber.getStatus())) {
            throw new ServerException("Service is not available now.");
        }
    }

    private Long parseAssetId(Map<String, Object> dtoJson) {
        if (dtoJson == null || !dtoJson.containsKey("assetId")) {
            throw new ServerException("assetId is required in request.");
        }
        try {
            return Long.valueOf(dtoJson.get("assetId").toString());
        } catch (NumberFormatException e) {
            throw new ServerException("Invalid assetId format.");
        }
    }

    private ServiceRequest createAndSaveServiceRequest(ServiceRequestDTO dto) {
        ServiceRequest serviceRequest = new ServiceRequest();
        mapToOrderRequestEntity(dto, serviceRequest);
        return serviceRequestRepository.save(serviceRequest); // try-catch
    }

    private void validateRequestDTO(ServiceRequestDTO dto) {
        if (dto == null) throw new ServerException("Request body is missing.");
        if (dto.getServiceSubscriber() == null) throw new ServerException("ServiceSubscriber ID is required.");
    }

    private ServiceSubscriber fetchServiceSubscriber(Long id) {
        return serviceSubscriberRepository.findById(id).orElseThrow(() -> new IllegalArgumentException("ServiceSubscriber not found with ID: " + id));
    }

    public void mapToOrderRequestEntity(final ServiceRequestDTO serviceRequestDTO, final ServiceRequest serviceRequest) {
        try {
            ObjectMapper objectMapper = new ObjectMapper();

            serviceRequest.setRequestUid(UUID.randomUUID());
            serviceRequest.setRequestedBy(serviceRequestDTO.getRequestedBy());
            serviceRequest.setRequestedByType(serviceRequestDTO.getRequestedByType());
            serviceRequest.setCreatedAt(OffsetDateTime.now());
            serviceRequest.setUpdatedAt(OffsetDateTime.now());
            serviceRequest.setStatus(serviceRequestDTO.getStatus());
            serviceRequest.setInvalid(serviceRequestDTO.getInvalid());

            Long serviceSubscriberId = serviceRequestDTO.getServiceSubscriber();
            ServiceSubscriber serviceSubscriber = serviceSubscriberRepository.findById(serviceSubscriberId)
                    .orElseThrow(() -> new IllegalArgumentException("ServiceSubscriber not found with ID: " + serviceSubscriberId));
            serviceRequest.setServiceSubscriber(serviceSubscriber);

            Integer version = serviceRequestDTO.getVersion();
            serviceRequest.setVersion((version != null) ? version + 1 : 1);

            String jsonData = objectMapper.writeValueAsString(serviceRequestDTO.getRequest());
            serviceRequest.setRequest(jsonData);

        } catch (JsonProcessingException e) {
            throw new ServerException("Error serializing 'request' field to JSON: " + e.getMessage());
        } catch (IllegalArgumentException e) {
            throw new ServerException("Invalid input: " + e.getMessage());
        } catch (Exception e) {
            throw new ServerException("Unexpected error during mapping: " + e.getMessage());
        }
    }

    public void mapToAssetRequestEntity(final AssetServiceRequest assetServiceRequest, final ServiceRequest serviceRequest) {
        assetServiceRequest.setRequestUid(serviceRequest.getRequestUid());
        assetServiceRequest.setRequestBy(serviceRequest.getRequestedBy());
        assetServiceRequest.setStatus(serviceRequest.getStatus());
        assetServiceRequest.setCreatedAt(serviceRequest.getCreatedAt());
        assetServiceRequest.setUpdatedAt(serviceRequest.getUpdatedAt());
        assetServiceRequest.setInvalid(serviceRequest.getInvalid());
        assetServiceRequest.setVersion(serviceRequest.getVersion());
        assetServiceRequest.setRequestType(serviceRequest.getRequestedByType());
        assetServiceRequest.setStatus(serviceRequest.getStatus());

        try {
            ObjectMapper objectMapper = new ObjectMapper();
            JsonNode jsonNode = objectMapper.readTree(serviceRequest.getRequest());
            if (jsonNode.has("assetId")) {
                Long assetId = jsonNode.get("assetId").asLong();
                Asset asset = assetRepository.findById(assetId)
                        .orElseThrow(() -> new IllegalArgumentException("Asset not found with ID: " + assetId));
                assetServiceRequest.setAsset(asset);

            } else {
                throw new ServerException("Invalid request JSON: assetId not found.");
            }
        } catch (Exception e) {
            throw new ServerException("Failed to parse assetId from service request JSON: " + e.getMessage());
        }
    }

    private JSONObject prepareOrderRequestData(ServiceRequest serviceRequest,
                                               ServiceSubscriber serviceSubscriber,
                                               Boolean isModified,
                                               Long assetId) {
        JSONObject orderReqData = new JSONObject();

        ServiceMaster serviceMaster = serviceSubscriber.getServiceMaster();
        List<ServiceConfigurationDTO> serviceConfiguration = masterConfigurationService
                .getConfigurationByServiceId(serviceMaster.getId());
        ServiceConfigurationDTO eagleViewConfig = serviceConfiguration.stream()
                .filter(config -> "EagleView.parameter".equalsIgnoreCase(config.getKey()))
                .findFirst()
                .orElseThrow(() -> new ServerException("EagleView.parameter configuration not found."));

        Map<String, Object> dataMap = eagleViewConfig.getData();
        if (dataMap == null || !dataMap.containsKey("data")) {
            throw new ServerException("Invalid configuration data for EagleView.parameter.");
        }

        List<Map<String, Object>> products = (List<Map<String, Object>>) dataMap.get("data");
        if (products.isEmpty()) {
            throw new ServerException("No products found in configuration data.");
        }

        Map<String, Object> product = products.get(0);
        int productID = (int) product.get("productID");
        List<Map<String, Object>> deliveryProducts = (List<Map<String, Object>>) product.get("deliveryProducts");
        if (deliveryProducts.isEmpty()) {
            throw new ServerException("No delivery products found for the selected product.");
        }
        int deliveryTypeID = (int) deliveryProducts.get(0).get("productID");
        List<Integer> measurementInstructionTypes = (List<Integer>) product.get("measurementInstructionTypes");
        if (measurementInstructionTypes.isEmpty()) {
            throw new ServerException("No measurement instruction types found for the selected product.");
        }
        int measurementStructureType = measurementInstructionTypes.get(0);

        orderReqData.put("request_uid", String.valueOf(serviceRequest.getRequestUid()));
        orderReqData.put("tenant_id", String.valueOf(serviceSubscriber.getSubscriberId()));
        orderReqData.put("measurement_type", String.valueOf(productID));
        orderReqData.put("delivery_type", String.valueOf(deliveryTypeID));
        orderReqData.put("measurement_structure_type", String.valueOf(measurementStructureType));
        orderReqData.put("is_modified", String.valueOf(isModified));
        orderReqData.put("asset_id", String.valueOf(assetId));
        orderReqData.put("service_id", Integer.valueOf(Math.toIntExact(serviceMaster.getId())));
        orderReqData.put("user_id", String.valueOf(serviceRequest.getRequestedBy()));
        orderReqData.put("subscriber_id", Integer.valueOf(Math.toIntExact(serviceSubscriber.getId())));

        return orderReqData;
    }

    public List<AssetDocument> getReport(Long assetId) {
        return assetDocumentRepository.getByAssetId(assetId);
    }

    public List<ServiceRequestDTO> trackRequestStatus(final List<Long> assetIds) throws Exception {

        List<ServiceRequestDTO> serviceRequestDTOS = new ArrayList<>();

        try {
            if (assetIds == null || assetIds.isEmpty()) {
                throw new IllegalArgumentException("assetIds is required");
            }

            // 1) Get mappings for all assets
            List<AssetServiceRequest> mappings = assetServiceRequestRepository.findByAssetIds(assetIds);
            if (mappings == null || mappings.isEmpty()) {
                return Collections.emptyList();
            }

            // Group by requestUid
            Map<UUID, List<AssetServiceRequest>> mappingsByRequestUid = mappings.stream()
                    .filter(m -> m.getRequestUid() != null)
                    .collect(Collectors.groupingBy(AssetServiceRequest::getRequestUid));

            if (mappingsByRequestUid.isEmpty()) {
                return Collections.emptyList();
            }

            // 2) Get orders for all requestUids
            List<UUID> requestUids = new ArrayList<>(mappingsByRequestUid.keySet());
            List<ServiceRequest> orders = serviceRequestRepository.getRequest(requestUids);

            if (orders == null || orders.isEmpty()) {
                return Collections.emptyList();
            }

            // Quick lookup: requestUid -> ServiceRequest
            Map<UUID, ServiceRequest> orderByUid = orders.stream()
                    .filter(o -> o.getRequestUid() != null)
                    .collect(Collectors.toMap(ServiceRequest::getRequestUid, o -> o, (a, b) -> a));

            // 3) Prepare external report request array (KEEP your existing logic)
            JSONArray orderReportIdsArray = new JSONArray();
            for (ServiceRequest order : orders) {
                boolean shouldCallExternalService = false;
                String stateChangeHistory = order.getStatusHistory();
                String requestJson = order.getRequest();
                ObjectMapper objectMapper = new ObjectMapper();
                Long assetId = null;

                // Parse assetId from request JSON if present
                if (requestJson != null) {
                    try {
                        JsonNode jsonNode = objectMapper.readTree(requestJson);
                        assetId = jsonNode.has("assetId") ? jsonNode.get("assetId").asLong() : null;
                    } catch (Exception e) {
                        // If parsing fails, treat as no assetId
                        logger.error(e);
                        assetId = null;
                    }
                }

                // Determine if external service call is needed
                if (stateChangeHistory == null || stateChangeHistory.isEmpty()) {
                    shouldCallExternalService = true;
                } else {
                    try {
                        JSONArray historyArray = new JSONArray(stateChangeHistory);
                        JSONObject firstEntry = historyArray.getJSONObject(0);
                        String statusId = firstEntry.optString("status_id", "0");

                        if (!"5".equals(statusId)) {
                            shouldCallExternalService = true;
                        } else {
                            List<AssetDocument> assetDocuments = assetDocumentRepository.findByAssetIdAndRequestId(
                                    String.valueOf(order.getRequestUid()), assetId);
                            if (assetDocuments == null || assetDocuments.isEmpty()) {
                                shouldCallExternalService = true;
                            }
                        }
                    } catch (JSONException e) {
                        logger.error(e);
                        shouldCallExternalService = true;
                    }
                }

                // Prepare reportIds for external call if needed
                if (shouldCallExternalService && order.getResponse() != null) {
                    try {
                        JSONObject responseJson = new JSONObject(order.getResponse());
                        JSONArray reportIds = responseJson.optJSONArray("ReportIds");
                        if (reportIds != null && reportIds.length() > 0) {
                            String reportId = String.valueOf(reportIds.get(0));
                            Optional<AssetServiceRequest> assetServiceRequestOpt = assetServiceRequestRepository.findByRequestUid(order.getRequestUid());
                            if (assetServiceRequestOpt.isPresent()) {
                                AssetServiceRequest assetRequest = assetServiceRequestOpt.get();
                                Long assetIdFromAsset = assetRequest.getAsset().getId();

                                JSONObject orderMap = new JSONObject();
                                orderMap.put("request_uid", order.getRequestUid().toString());
                                orderMap.put("report_id", reportId);
                                orderMap.put("asset_id", String.valueOf(assetIdFromAsset));

                                orderReportIdsArray.put(orderMap);
                            }
                        }
                    } catch (Exception ex) {
                        // Log and skip this order if response JSON is invalid
                        logger.error(ex);
                        System.err.println("Failed to parse order response: " + ex.getMessage());
                    }
                }
            }

            // Only make external call if there's any report to process
            if (!orderReportIdsArray.isEmpty()) {
                // Only call once for the first valid order
                for (ServiceRequest order : orders) {
                    try {
                        Hibernate.initialize(order);
                        Optional<AssetServiceRequest> assetServiceRequest = assetServiceRequestRepository.findByRequestUid(order.getRequestUid());
                        if (assetServiceRequest.isPresent()) {
                            AssetServiceRequest assetRequest = assetServiceRequest.get();
                            String userId = assetRequest.getRequestBy();

                            ServiceSubscriber serviceSubscriber = order.getServiceSubscriber();
                            Hibernate.initialize(serviceSubscriber);
                            String tenantId = serviceSubscriber.getSubscriberId();

                            JSONObject reqData = new JSONObject();
                            reqData.put("report_refs", orderReportIdsArray);
                            reqData.put("tenant_id", String.valueOf(tenantId));
                            reqData.put("user_id", String.valueOf(userId));

                            // Call external report service with retry, but don't throw if it fails
                            int maxRetries = 5;
                            for (int attempt = 1; attempt <= maxRetries; attempt++) {
                                try {
                                    externalServices.getReport(reqData, getBearerToken());
                                    break; // Success, so exit retry loop
                                } catch (ConnectException ce) {
                                    if (attempt == maxRetries) {
                                        logger.error(ce);
                                        throw new RuntimeException("Connection failed after " + maxRetries + " attempts.", ce);
                                    }
                                    try {
                                        Thread.sleep(1000); // Wait 1 second before next retry (optional)
                                    } catch (InterruptedException ie) {
                                        logger.error(ie);
                                        Thread.currentThread().interrupt();
                                        throw new RuntimeException("Thread interrupted while retrying getReport.", ie);
                                    }
                                }
                            }
                            break; // Only call once
                        }
                    } catch (Exception ex) {
                        logger.error(ex);
                        System.err.println("Error preparing external service call: " + ex.getMessage());
                    }
                }
                // No need to reload orders here; proceed with the original list
            }

            // Map and return DTOs
            for (ServiceRequest order : orders) {
                try {
                    serviceRequestDTOS.add(mapToServiceDTO(order));
                } catch (Exception ex) {
                    logger.error(ex);
                    System.err.println("Failed to map ServiceRequest to DTO: " + ex.getMessage());
                }
            }
        } catch (Exception ex) {
            logger.error("Error in trackRequestStatus: " + ex.getMessage(), ex);
            if (ex instanceof NotFoundException) {
                throw ex; // Re-throw if it's a NotFoundException
            }
            throw new ServerException(ErrorMessages.TRACK_REQUEST_STATUS_ERROR,TRACK_REQUEST_STATUS_ERROR_DESC, ex);
        }
        return serviceRequestDTOS;
    }

    private ServiceRequestDTO mapToServiceDTO(final ServiceRequest serviceRequest) {
        ServiceRequestDTO serviceRequestDTO = new ServiceRequestDTO();
        return mapToServiceRequestDTO(serviceRequest, serviceRequestDTO);
    }

    public List<ServiceRequestDTO> getServiceRequest(final List<Long> assetId) throws Exception {
        try {
            List<AssetServiceRequest> mappings = assetServiceRequestRepository.findByAssetIds(assetId);
            List<UUID> requestUids = mappings.stream().map(AssetServiceRequest::getRequestUid).toList();
            List<ServiceRequest> orders = serviceRequestRepository.getRequest(requestUids);

            if (orders.isEmpty()) {
                throw new RuntimeException("No orders found for this customer.");
            }
            List<ServiceRequestDTO> serviceRequestDTOS = new ArrayList<>();
            for (ServiceRequest order : orders) {
                serviceRequestDTOS.add(mapToServiceRequestDTO(order));
            }

            return serviceRequestDTOS;
        } catch (Exception e) {
            logger.error("Error fetching service requests: " + e.getMessage(), e);
            if (e instanceof NotFoundException) {
                throw e; // Re-throw if it's a NotFoundException
            }
            throw new ServerException(ErrorMessages.SERVICE_REQUEST_FETCH_ERROR,SERVICE_REQUEST_FETCH_ERROR_DESC, e);
        }
    }

    public List<AssetDocument> getAllAssetDocument() {
        List<AssetDocument> ExternalService;
        Boolean invalid = false;
        ExternalService = assetDocumentRepository.findAll();
        return ExternalService;
    }


    public String getAssetDocument(Long assetId) throws Exception {
        super.hasPermissionEx(EAGLEVIEW);
        List<AssetDocument> docs = assetDocumentRepository.getByAssetId(assetId);
        if (docs.isEmpty()) {
            throw new NotFoundException("No documents found for asset ID: " + assetId);
        }

        AssetDocument doc = docs.get(0);
        String path = doc.getPath();

        try {
            String sasUrl = customerDocumentStorageService.downloadReport(path);
            if (sasUrl != null && !sasUrl.isEmpty()) {
                URL url = new URL(sasUrl);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");

                try (BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()))) {
                    StringBuilder jsonData = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        jsonData.append(line);
                    }
                    return jsonData.toString();
                }
            } else {
                throw new RuntimeException("Failed to generate SAS URL for asset path: " + path);
            }
        } catch (Exception ex) {
            logger.error("Error fetching asset document: " + ex.getMessage(), ex);
            if (ex instanceof NotFoundException) {
                throw ex; // Re-throw if it's a NotFoundException
            } else {
                throw new ServerException(ErrorMessages.ASSET_DOC_FETCH_ERROR,ASSET_DOC_FETCH_ERROR_DESC,ex);
            }
        }
    }


    private AssetDocumentDTO mapToDTO(final AssetDocument asset, final AssetDocumentDTO assetDocumentDTO) {
        assetDocumentDTO.setDocumentType(asset.getDocumentType());
        assetDocumentDTO.setDescription(asset.getDescription());
        assetDocumentDTO.setId(asset.getId());
        assetDocumentDTO.setMd5(asset.getMd5());
        assetDocumentDTO.setPath(asset.getPath());
        assetDocumentDTO.setType(asset.getType());
        assetDocumentDTO.setCreatedAt(asset.getCreatedAt());
        assetDocumentDTO.setMimeType(asset.getMimeType());
        assetDocumentDTO.setSize(asset.getSize());
        assetDocumentDTO.setInvalid(asset.getInvalid());
        assetDocumentDTO.setDocumentUid(asset.getDocumentUid());
        assetDocumentDTO.setAdditionalInfo(asset.getAdditionalInfo());
        assetDocumentDTO.setAsset(asset.getAsset() == null ? null : asset.getAsset().getId());
        return assetDocumentDTO;
    }


    private ServiceRequestDTO mapToServiceRequestDTO(final ServiceRequest serviceRequest) {
        ServiceRequestDTO serviceRequestDTO = new ServiceRequestDTO();
        return mapToServiceRequestDTO(serviceRequest, serviceRequestDTO);
    }

    private ServiceRequestDTO mapToServiceRequestDTO(final ServiceRequest serviceRequest, final ServiceRequestDTO serviceRequestDTO) {
        ObjectMapper objectMapper = new ObjectMapper();

        serviceRequestDTO.setId(serviceRequest.getId());
        serviceRequestDTO.setResponse(serviceRequest.getResponse());
        serviceRequestDTO.setServiceSubscriber(serviceRequest.getServiceSubscriber().getId());
        serviceRequestDTO.setType(serviceRequest.getType());
        serviceRequestDTO.setCreatedAt(serviceRequest.getCreatedAt());
        serviceRequestDTO.setStatus(serviceRequest.getStatus());
        serviceRequestDTO.setRequestedBy(serviceRequest.getRequestedBy());
        serviceRequestDTO.setInvalid(serviceRequest.getInvalid());
        serviceRequestDTO.setVersion(serviceRequest.getVersion());
        serviceRequestDTO.setStatusHistory(serviceRequest.getStatusHistory());
        serviceRequestDTO.setUpdatedAt(serviceRequest.getUpdatedAt());
        serviceRequestDTO.setResponse(serviceRequest.getResponse());
        serviceRequestDTO.setRequestedByType(serviceRequest.getRequestedByType());

        try {
            if (serviceRequest.getRequest() != null) {
                Map<String, Object> requestMap = objectMapper.readValue(serviceRequest.getRequest(), Map.class);
                serviceRequestDTO.setRequest(requestMap);
            }
        } catch (IOException e) {
            logger.error(e);
            serviceRequestDTO.setRequest(null);
        }

        return serviceRequestDTO;
    }

}

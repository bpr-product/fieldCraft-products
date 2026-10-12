package com.fieldcraft.tenantprovider.service;

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 09-Jan-2026
 * @author      : Aravind J
 * @since       : 1.0.1.0
 * Requirement# :
 * Purpose      : Service class for Support Request
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

import com.fieldcraft.provider.data.domain.SupportRequest;
import com.fieldcraft.provider.data.model.SupportRequestDTO;
import com.fieldcraft.provider.data.model.SupportRequestResponseDTO;
import com.fieldcraft.shared.error.NotFoundException;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.tenantprovider.dal.repos.SupportRequestRepository;
import org.springframework.transaction.annotation.Transactional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.UUID;

import static com.fieldcraft.shared.error.ErrorMessages.*;

@Service
public class SupportRequestService {

    private final SupportRequestRepository supportRequestRepository;
    private final Logger logger = LoggerFactory.getLogger(SupportRequestService.class);


    public SupportRequestService(SupportRequestRepository supportRequestRepository) {
        this.supportRequestRepository = supportRequestRepository;
    }

    @Transactional
    public void createSupportRequest(SupportRequestDTO supportRequestDTO) {

        try {
            SupportRequest supportRequest = new SupportRequest();

            validateDuplicateRequest(supportRequestDTO.getReferenceNo(), supportRequestDTO.getTenantId(), supportRequestDTO.getCategory(), supportRequestDTO.getCategoryType(), supportRequestDTO.getStatus());

            validateRequiredFields(supportRequestDTO);
            mapToEntity(supportRequest, supportRequestDTO);

            logger.info("Creating support request for referenceNo={}", supportRequestDTO.getReferenceNo());

            supportRequestRepository.save(supportRequest);

        } catch (Exception ex) {
            logger.error("Failed to create support request for referenceNo={}", supportRequestDTO.getReferenceNo(), ex);
            throw new ServerException(SUBSCRIPTION_TRIAL_EXTENSION_PROCESSING_FAILED,SUBSCRIPTION_TRIAL_EXTENSION_PROCESSING_FAILED_DESC);
        }
    }

    public SupportRequestResponseDTO getSupportRequestStatus(String referenceNo, String tenantId, String category, String categoryType, String status) {

        boolean supportRequestResponse = supportRequestRepository.supportRequestExists(referenceNo, tenantId, category, categoryType, status);
        if (!supportRequestResponse) {
            throw new NotFoundException(SUBSCRIPTION_TRIAL_EXTENSION_SUPPORT_REQUEST_NOT_FOUND, SUBSCRIPTION_TRIAL_EXTENSION_SUPPORT_REQUEST_NOT_FOUND_DESC);
        }

        SupportRequestResponseDTO supportRequestResponseDTO = new SupportRequestResponseDTO();
        supportRequestResponseDTO.setExists(true);
        return supportRequestResponseDTO;
    }


    private void mapToEntity(SupportRequest supportRequest, SupportRequestDTO supportRequestDTO) {
        supportRequest.setRequestUuid(generateRequestNumber());

        supportRequest.setRequesterType(supportRequestDTO.getRequesterType());
        supportRequest.setRequesterEmail(supportRequestDTO.getRequesterEmail());

        supportRequest.setTenantId(supportRequestDTO.getTenantId());

        supportRequest.setCategory(supportRequestDTO.getCategory());
        supportRequest.setCategoryType(supportRequestDTO.getCategoryType());

        supportRequest.setDescription(supportRequestDTO.getDescription());
        supportRequest.setRequesterUserId(supportRequestDTO.getRequesterUserId());

        supportRequest.setReferenceNo(supportRequestDTO.getReferenceNo());

        supportRequest.setPriority(supportRequestDTO.getPriority());
        supportRequest.setAdditionalData(supportRequestDTO.getAdditionalData());

        supportRequest.setAssigneeType(supportRequestDTO.getAssigneeType());
        supportRequest.setAssigneeEmail(supportRequestDTO.getAssigneeEmail());

        supportRequest.setStatus(supportRequestDTO.getStatus());
        supportRequest.setCreatedAt(OffsetDateTime.now());
        supportRequest.setCreatedBy(supportRequestDTO.getCreatedBy());
        supportRequest.setVersion(0);
        supportRequest.setInvalid(false);

    }

    private void validateDuplicateRequest(String referenceNo, String tenantId, String category, String categoryType, String status) {
        boolean supportRequestExists = supportRequestRepository.supportRequestExists(referenceNo, tenantId, category, categoryType, status);
        if (supportRequestExists) {
            throw new ServerException(SUBSCRIPTION_TRIAL_EXTENSION_SUPPORT_REQUEST_DUPLICATE, SUBSCRIPTION_TRIAL_EXTENSION_SUPPORT_REQUEST_DUPLICATE_DESC);
        }
    }

    private void validateRequiredFields(SupportRequestDTO supportRequestDTO) {
        if (supportRequestDTO.getRequesterEmail() == null || supportRequestDTO.getRequesterEmail().isEmpty()
                || supportRequestDTO.getRequesterType() == null || supportRequestDTO.getRequesterType().isEmpty()
                || supportRequestDTO.getRequesterUserId() == null || supportRequestDTO.getRequesterUserId().isEmpty()
                || supportRequestDTO.getAssigneeEmail() == null || supportRequestDTO.getAssigneeEmail().isEmpty()
                || supportRequestDTO.getAssigneeType() == null || supportRequestDTO.getAssigneeType().isEmpty()
                || supportRequestDTO.getTenantId() == null || supportRequestDTO.getTenantId().isEmpty()
                || supportRequestDTO.getCategory() == null || supportRequestDTO.getCategory().isEmpty()
                || supportRequestDTO.getCategoryType() == null || supportRequestDTO.getCategoryType().isEmpty()
                || supportRequestDTO.getReferenceNo() == null || supportRequestDTO.getReferenceNo().isEmpty()
                || supportRequestDTO.getCreatedBy() == null || supportRequestDTO.getCreatedBy().isEmpty()
                || supportRequestDTO.getStatus() == null || supportRequestDTO.getStatus().isEmpty()) {
            throw new ServerException(SUBSCRIPTION_TRIAL_EXTENSION_SUPPORT_REQUEST_MANDATORY_FIELDS_MISSING, SUBSCRIPTION_TRIAL_EXTENSION_SUPPORT_REQUEST_MANDATORY_FIELDS_MISSING_DESC);
        }
    }

    private UUID generateRequestNumber() {
        return UUID.randomUUID();
    }

}

package com.fieldcraft.tenant.service;


import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.external.dal.repos.ServiceSubscriberRepository;
import com.fieldcraft.external.data.domain.ServiceMaster;
import com.fieldcraft.external.data.domain.ServiceSubscriber;
import com.fieldcraft.external.data.model.ServiceSubscriberDTO;
import com.fieldcraft.shared.error.BadRequestException;
import com.fieldcraft.shared.error.ErrorMessages;
import com.fieldcraft.shared.error.NotFoundException;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.shared.utils.ErrorParser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.stream.Collectors;

import static com.fieldcraft.shared.error.ErrorMessages.*;

@Service
public class SubscriberService {

    private final ServiceSubscriberRepository serviceSubscriberRepository;
    private final ServiceMasterRepository serviceMasterRepository;
    private static final Logger logger = LoggerFactory.getLogger(SubscriberService.class);


    public SubscriberService(ServiceSubscriberRepository serviceSubscriberRepository, ServiceMasterRepository serviceMasterRepository) {
        this.serviceSubscriberRepository = serviceSubscriberRepository;
        this.serviceMasterRepository = serviceMasterRepository;
    }



//    public Boolean createOrUpdateSubscriberDetails(final ServiceSubscriberDTO serviceSubscriberDTO) {
//        final ServiceMaster serviceMaster = serviceMasterRepository.findById(serviceSubscriberDTO.getServiceMaster())
//                .orElseThrow(() -> new NotFoundException("Service not found. Provide proper service ID."));
//
//        // 1. Find existing ServiceSubscriber matching serviceMaster and subscriberId
//        List<ServiceSubscriber> existingSubscribers = serviceSubscriberRepository
//                .findByServiceMasterAndSubscriberId(serviceMaster, serviceSubscriberDTO.getSubscriberId());
//
//        if (!existingSubscribers.isEmpty()) {
//            for (ServiceSubscriber existing : existingSubscribers) {
//                existing.setInvalid(true); // Mark old as invalid
//                existing.setUpdatedAt(OffsetDateTime.now());
//                existing.setCreatedBy(serviceSubscriberDTO.getCreatedBy());
//                existing.setSubscriberId(serviceSubscriberDTO.getSubscriberId());
//                serviceSubscriberRepository.save(existing); // Save updated old record
//            }
//        }
//
//
//
//        // 2. Create a new ServiceSubscriber
//        ServiceSubscriber newSubscriber = new ServiceSubscriber();
//        newSubscriber.setServiceMaster(serviceMaster);
//        newSubscriber.setSubscriberId(serviceSubscriberDTO.getSubscriberId());
//        newSubscriber.setSubscriberType(serviceSubscriberDTO.getSubscriberType());
//        newSubscriber.setCreatedBy(serviceSubscriberDTO.getCreatedBy());
//        newSubscriber.setCreatedAt(OffsetDateTime.now());
//        newSubscriber.setUpdatedAt(OffsetDateTime.now());
//        newSubscriber.setStatus(serviceSubscriberDTO.getStatus());
//        newSubscriber.setInvalid(false); // new record should be valid
//        Integer version = serviceSubscriberDTO.getVersion();
//        newSubscriber.setVersion((version != null) ? version + 1 : 1);
//
//        try {
//            serviceSubscriberRepository.save(newSubscriber);
//            return true;
//        } catch (DataIntegrityViolationException e) {
//            if (ErrorParser.isDuplicateError(e)) {
//                throw new BadRequestException("Duplicate entry detected. Please verify the input.");
//            } else {
//                throw new BadRequestException("Unable to create or update the subscriber. Please try again later.");
//            }
//        }
//    }

    public Boolean createOrUpdateSubscriberDetails(final ServiceSubscriberDTO serviceSubscriberDTO) {

        try {
            final ServiceMaster serviceMaster = serviceMasterRepository.findById(serviceSubscriberDTO.getServiceMaster())
                    .orElseThrow(() -> new NotFoundException("Service not found. Provide proper service ID."));

            // 1. Find existing ServiceSubscriber matching serviceMaster and subscriberId
            List<ServiceSubscriber> existingSubscribers = serviceSubscriberRepository.findByServiceMasterAndSubscriberId(serviceMaster, serviceSubscriberDTO.getSubscriberId());

//        if (!existingSubscribers.isEmpty()) {
//            for (ServiceSubscriber existing : existingSubscribers) {
//                existing.setInvalid(true); // Mark old as invalid
//                existing.setUpdatedAt(OffsetDateTime.now());
//                existing.setCreatedBy(serviceSubscriberDTO.getCreatedBy());
//                existing.setSubscriberId(serviceSubscriberDTO.getSubscriberId());
//                serviceSubscriberRepository.save(existing); // Save updated old record
//            }
//        }
            // 2. Create a new ServiceSubscriber
            ServiceSubscriber newSubscriber = new ServiceSubscriber();
            if(!existingSubscribers.isEmpty()) {
                newSubscriber.setId(existingSubscribers.getFirst().getId());
            }else{
                logger.info("There is service subscriber to this subscriber ID: " + serviceSubscriberDTO.getSubscriberId());
            }
            newSubscriber.setServiceMaster(serviceMaster);
            newSubscriber.setSubscriberId(serviceSubscriberDTO.getSubscriberId());
            newSubscriber.setSubscriberType(serviceSubscriberDTO.getSubscriberType());
            newSubscriber.setCreatedBy(serviceSubscriberDTO.getCreatedBy());
            newSubscriber.setCreatedAt(OffsetDateTime.now());
            newSubscriber.setUpdatedAt(OffsetDateTime.now());
            newSubscriber.setStatus(serviceSubscriberDTO.getStatus());
            newSubscriber.setInvalid(false); // new record should be valid
            Integer version = serviceSubscriberDTO.getVersion();
            newSubscriber.setVersion((version != null) ? version + 1 : 1);
            serviceSubscriberRepository.save(newSubscriber);
            return true;
        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                throw new BadRequestException("Duplicate entry detected. Please verify the input.");
            } else {
                throw new BadRequestException("Unable to create or update the subscriber. Please try again later.");
            }
        } catch (Exception e) {
            logger.error("An error occurred while creating or updating subscriber: {}", e.getMessage(), e);
            if (e instanceof ServerException) {
                throw e;
            } else {
                throw new ServerException(ErrorMessages.CREATEORUPDATESUBSCRIBER_ERROR, CREATEORUPDATESUBSCRIBER_ERROR_DESC, e);
            }
        }
    }

    public List<ServiceSubscriberDTO> getSubscriberById(String subscriberId) {
        List<ServiceSubscriber> serviceSubscribers = serviceSubscriberRepository.findSubscriberBySubscriberId(subscriberId);
        return serviceSubscribers.stream()
                .map(subscriber -> mapToServiceSubscriberDTO(subscriber, new ServiceSubscriberDTO()))
                .collect(Collectors.toList());
    }

    private ServiceSubscriberDTO mapToServiceSubscriberDTO(final ServiceSubscriber serviceSubscriber, final ServiceSubscriberDTO serviceSubscriberDTO) {
        serviceSubscriberDTO.setId(serviceSubscriber.getId());
        serviceSubscriberDTO.setServiceMaster(serviceSubscriber.getServiceMaster().getId());
        serviceSubscriberDTO.setSubscriberId(serviceSubscriber.getSubscriberId());
        serviceSubscriberDTO.setSubscriberType(serviceSubscriber.getSubscriberType());
        serviceSubscriberDTO.setStatus(serviceSubscriber.getStatus());
        serviceSubscriberDTO.setInvalid(serviceSubscriber.getInvalid());
        serviceSubscriberDTO.setVersion(serviceSubscriber.getVersion());
        serviceSubscriberDTO.setCreatedBy(serviceSubscriber.getCreatedBy());
        serviceSubscriberDTO.setCreatedAt(serviceSubscriber.getCreatedAt());
        serviceSubscriberDTO.setUpdatedAt(serviceSubscriber.getUpdatedAt());
        return serviceSubscriberDTO;
    }

}

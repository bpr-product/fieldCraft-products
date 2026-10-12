package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.shared.constant.AuditLogConstant;
import com.fieldcraft.shared.error.BadRequestException;
import com.fieldcraft.shared.error.ErrorMessages;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.shared.utils.ErrorParser;
import com.fieldcraft.tenant.dal.repos.accounts.ChartOfAccountRepository;
import com.fieldcraft.tenant.data.domain.accounts.ChartOfAccount;
import com.fieldcraft.tenant.data.model.AuditLogDTO;
import com.fieldcraft.tenant.data.model.CreateChartOfAccountRequest;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;

import java.util.List;

import static com.fieldcraft.tenant.data.constant.DomainConstant.ERROR;

@Service
public class ChartofAccountService {

    private final ChartOfAccountRepository chartOfAccountRepository;
    private final AuditLogService auditLogService;

    private static final Logger logger = LogManager.getLogger(ChartofAccountService.class);

    public ChartofAccountService(ChartOfAccountRepository chartOfAccountRepository,
                                 AuditLogService auditLogService) {
        this.chartOfAccountRepository = chartOfAccountRepository;
        this.auditLogService = auditLogService;
    }

    public Long create(final CreateChartOfAccountRequest request) {
        try {
            ChartOfAccount chartOfAccount = new ChartOfAccount();

            chartOfAccount.setName(request.getName());
            chartOfAccount.setAccountType(request.getAccountType());
            chartOfAccount.setAccountNo(request.getAccountNo());

            ChartOfAccount savedChartOfAccount = chartOfAccountRepository.save(chartOfAccount);
            Long id = savedChartOfAccount.getId();

            AuditLogDTO auditLogDTO = new AuditLogDTO();
            auditLogDTO.setAction(AuditLogConstant.Action.CHART_OF_ACCOUNT_CREATE.toString());
            auditLogDTO.setStatus(ERROR);
            auditLogDTO.setResourceId(savedChartOfAccount.getName());
            auditLogDTO.setResourceType(AuditLogConstant.Feature.CHART_OF_ACCOUNT.toString());
            auditLogService.saveAuditLog(auditLogDTO);

            logger.info("ChartOfAccount created successfully with ID: {}", id);
            return id;

        } catch (DataIntegrityViolationException e) {
            if (ErrorParser.isDuplicateError(e)) {
                logger.error(
                        "Duplicate entry detected for ChartOfAccount: {}. Error: {}",
                        request.getName(),
                        e.getMessage(),
                        e
                );
                throw new BadRequestException(BadRequestException.DUPLICATE_ENTRY);
            }

            logger.error(
                    "Data integrity violation occurred while creating ChartOfAccount: {}",
                    e.getMessage(),
                    e
            );
            throw new BadRequestException();

        } catch (Exception e) {
            logger.error(
                    "Unexpected error while creating ChartOfAccount: {}",
                    e.getMessage(),
                    e
            );

            if (e instanceof ServerException) {
                throw (ServerException) e;
            }

            throw new ServerException(
                    ErrorMessages.PO_RECEIPT_CREATE_ERROR,
                    e.getMessage(),
                    e
            );
        }
    }

    public List<ChartOfAccount> findAll() {
        return chartOfAccountRepository.findAll(Sort.by("id"));
    }

    public List<String> getAllAccountTypes() {
        return chartOfAccountRepository.getAllAccountTypes();
    }

    public List<ChartOfAccount> findAllActive() {
        return chartOfAccountRepository.findAllActive();
    }
}
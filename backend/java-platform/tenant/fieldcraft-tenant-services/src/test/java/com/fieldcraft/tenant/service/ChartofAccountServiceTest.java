package com.fieldcraft.tenant.service;

import https://fieldcraft.example.invalid;
import com.fieldcraft.shared.constant.AuditLogConstant;
import com.fieldcraft.shared.error.BadRequestException;
import com.fieldcraft.shared.error.ServerException;
import com.fieldcraft.shared.utils.ErrorParser;
import com.fieldcraft.tenant.dal.repos.accounts.ChartOfAccountRepository;
import com.fieldcraft.tenant.data.domain.accounts.ChartOfAccount;
import com.fieldcraft.tenant.data.model.CreateChartOfAccountRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.MockedStatic;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Sort;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ChartofAccountServiceTest {

    @Mock
    private ChartOfAccountRepository chartOfAccountRepository;
    @Mock
    private AuditLogService auditLogService;

    private ChartofAccountService service;

    @BeforeEach
    void setUp() {
        service = new ChartofAccountService(chartOfAccountRepository, auditLogService);
    }

    private CreateChartOfAccountRequest buildRequest() {
        CreateChartOfAccountRequest r = new CreateChartOfAccountRequest();
        r.setName("Accounts Payable");
        r.setAccountType("Liability");
        r.setAccountNo("2000");
        return r;
    }

    @Test
    void create_success_returnsId() {
        ChartOfAccount saved = new ChartOfAccount();
        saved.setId(42L);
        saved.setName("Accounts Payable");
        when(chartOfAccountRepository.save(any(ChartOfAccount.class))).thenReturn(saved);

        Long id = service.create(buildRequest());

        assertEquals(42L, id);
        verify(chartOfAccountRepository).save(any(ChartOfAccount.class));
        verify(auditLogService).saveAuditLog(argThat(dto ->
                AuditLogConstant.Action.CHART_OF_ACCOUNT_CREATE.toString().equals(dto.getAction())
                        && AuditLogConstant.Feature.CHART_OF_ACCOUNT.toString().equals(dto.getResourceType())));
    }

    @Test
    void create_duplicateEntry_throwsBadRequestWithDuplicateMessage() {
        DataIntegrityViolationException dive = new DataIntegrityViolationException("Duplicate key");
        when(chartOfAccountRepository.save(any(ChartOfAccount.class))).thenThrow(dive);

        try (MockedStatic<ErrorParser> parser = mockStatic(ErrorParser.class)) {
            parser.when(() -> ErrorParser.isDuplicateError(dive)).thenReturn(true);

            BadRequestException ex = assertThrows(BadRequestException.class,
                    () -> service.create(buildRequest()));
            assertEquals(BadRequestException.DUPLICATE_ENTRY, ex.getMessage());
        }
    }

    @Test
    void create_nonDuplicateDataIntegrityViolation_throwsGenericBadRequest() {
        DataIntegrityViolationException dive = new DataIntegrityViolationException("constraint violation");
        when(chartOfAccountRepository.save(any(ChartOfAccount.class))).thenThrow(dive);

        try (MockedStatic<ErrorParser> parser = mockStatic(ErrorParser.class)) {
            parser.when(() -> ErrorParser.isDuplicateError(dive)).thenReturn(false);

            assertThrows(BadRequestException.class, () -> service.create(buildRequest()));
        }
    }

    @Test
    void create_serverException_rethrowsAsIs() {
        ServerException se = new ServerException("server error");
        when(chartOfAccountRepository.save(any(ChartOfAccount.class))).thenThrow(se);

        ServerException thrown = assertThrows(ServerException.class, () -> service.create(buildRequest()));
        assertSame(se, thrown);
    }

    @Test
    void create_genericException_wrapsInServerException() {
        when(chartOfAccountRepository.save(any(ChartOfAccount.class)))
                .thenThrow(new RuntimeException("db down"));

        assertThrows(ServerException.class, () -> service.create(buildRequest()));
    }

    @Test
    void findAll_returnsSortedList() {
        ChartOfAccount coa1 = new ChartOfAccount();
        coa1.setId(1L);
        ChartOfAccount coa2 = new ChartOfAccount();
        coa2.setId(2L);
        when(chartOfAccountRepository.findAll(any(Sort.class))).thenReturn(List.of(coa1, coa2));

        List<ChartOfAccount> result = service.findAll();

        assertEquals(2, result.size());
        assertEquals(1L, result.get(0).getId());
        verify(chartOfAccountRepository).findAll(Sort.by("id"));
    }

    @Test
    void getAllAccountTypes_returnsList() {
        when(chartOfAccountRepository.getAllAccountTypes())
                .thenReturn(List.of("Asset", "Liability", "Expense"));

        List<String> result = service.getAllAccountTypes();

        assertEquals(3, result.size());
        assertTrue(result.contains("Asset"));
        assertTrue(result.contains("Liability"));
    }

    @Test
    void findAllActive_returnsOnlyActiveAccounts() {
        ChartOfAccount active = new ChartOfAccount();
        active.setId(5L);
        when(chartOfAccountRepository.findAllActive()).thenReturn(List.of(active));

        List<ChartOfAccount> result = service.findAllActive();

        assertEquals(1, result.size());
        assertEquals(5L, result.get(0).getId());
        verify(chartOfAccountRepository).findAllActive();
    }
}
/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * Unit tests for PaymentExternalMapService.
 */
package com.fieldcraft.tenant.service.accounts;

import com.fieldcraft.external.dal.repos.ServiceMasterRepository;
import com.fieldcraft.tenant.dal.repos.TenantRepository;
import com.fieldcraft.tenant.dal.repos.accounts.PaymentExternalMapRepository;
import com.fieldcraft.tenant.data.domain.Tenant;
import com.fieldcraft.tenant.data.domain.accounts.PaymentExpense;
import com.fieldcraft.tenant.data.domain.accounts.PaymentExternalMap;
import com.fieldcraft.tenant.data.domain.accounts.PaymentReceives;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.util.Collections;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@DisplayName("PaymentExternalMapService Unit Tests")
class PaymentExternalMapServiceTest {

    @Mock private ServiceMasterRepository serviceMasterRepository;
    @Mock private TenantRepository tenantRepository;
    @Mock private PaymentExternalMapRepository paymentExternalMapRepository;

    private PaymentExternalMapService service;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        service = new PaymentExternalMapService(serviceMasterRepository, tenantRepository, paymentExternalMapRepository);
    }

    @Test
    @DisplayName("save returns early and skips persistence when no external service IDs found")
    void save_noExternalServiceIds_skipsPersist() {
        PaymentExpense expense = new PaymentExpense();
        expense.setId(1L);
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(Collections.emptyList());

        service.save(expense, "PENDING");

        verify(paymentExternalMapRepository, never()).save(any());
        verify(tenantRepository, never()).findTenant();
    }

    @Test
    @DisplayName("save returns early when external service IDs are null")
    void save_nullExternalServiceIds_skipsPersist() {
        PaymentExpense expense = new PaymentExpense();
        expense.setId(2L);
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(null);

        service.save(expense, "PENDING");

        verify(paymentExternalMapRepository, never()).save(any());
    }

    @Test
    @DisplayName("save persists one map per external service ID with version starting at 1")
    void save_persistsMapPerServiceId_firstVersion() {
        PaymentExpense expense = new PaymentExpense();
        expense.setId(10L);

        Tenant tenant = new Tenant();
        tenant.setTenantid("tn-1");

        when(serviceMasterRepository.getIdsByServiceType("Data Exchange"))
                .thenReturn(List.of(100L, 200L));
        when(tenantRepository.findTenant()).thenReturn(tenant);
        when(paymentExternalMapRepository
                .findMaxVersionByPaymentExpenseIdAndServiceId(eq(10L), anyInt()))
                .thenReturn(null);

        service.save(expense, "SYNCED");

        ArgumentCaptor<PaymentExternalMap> captor = ArgumentCaptor.forClass(PaymentExternalMap.class);
        verify(paymentExternalMapRepository, times(2)).save(captor.capture());

        List<PaymentExternalMap> saved = captor.getAllValues();
        assertEquals(2, saved.size());
        for (PaymentExternalMap m : saved) {
            assertEquals("tn-1", m.getTenantId());
            assertEquals("SYNCED", m.getSyncStatus());
            assertEquals(1, m.getVersion());
            assertNotNull(m.getEndorsementEffectiveDate());
            assertSame(expense, m.getPaymentExpense());
        }
    }

    @Test
    @DisplayName("save increments existing maxVersion by 1")
    void save_incrementsExistingVersion() {
        PaymentExpense expense = new PaymentExpense();
        expense.setId(20L);

        Tenant tenant = new Tenant();
        tenant.setTenantid("tn-2");

        when(serviceMasterRepository.getIdsByServiceType("Data Exchange"))
                .thenReturn(List.of(300L));
        when(tenantRepository.findTenant()).thenReturn(tenant);
        when(paymentExternalMapRepository
                .findMaxVersionByPaymentExpenseIdAndServiceId(20L, 300))
                .thenReturn(7);

        service.save(expense, "FAILED");

        ArgumentCaptor<PaymentExternalMap> captor = ArgumentCaptor.forClass(PaymentExternalMap.class);
        verify(paymentExternalMapRepository).save(captor.capture());
        assertEquals(8, captor.getValue().getVersion());
        assertEquals("FAILED", captor.getValue().getSyncStatus());
        assertEquals(300, captor.getValue().getServiceId());
    }

    @Test
    @DisplayName("save swallows exceptions and logs error")
    void save_swallowsException() {
        PaymentExpense expense = new PaymentExpense();
        expense.setId(99L);
        when(serviceMasterRepository.getIdsByServiceType(anyString()))
                .thenThrow(new RuntimeException("DB down"));

        // Should not propagate
        assertDoesNotThrow(() -> service.save(expense, "PENDING"));
        verify(paymentExternalMapRepository, never()).save(any());
    }

    @Test
    @DisplayName("advanceAwaitingApprovalToPendingPush moves only AWAITING_APPROVAL rows to PENDING_PUSH")
    void advance_movesOnlyAwaitingApprovalRows() {
        PaymentExternalMap awaiting = new PaymentExternalMap();
        awaiting.setSyncStatus("AWAITING_APPROVAL");
        PaymentExternalMap alreadySynced = new PaymentExternalMap();
        alreadySynced.setSyncStatus("SYNCED");

        when(paymentExternalMapRepository.findByPaymentExpenseId(50L))
                .thenReturn(List.of(awaiting, alreadySynced));

        service.advanceAwaitingApprovalToPendingPush(50L);

        assertEquals("PENDING_PUSH", awaiting.getSyncStatus());
        assertEquals("SYNCED", alreadySynced.getSyncStatus(), "an already-synced row must be left untouched");
        verify(paymentExternalMapRepository, times(1)).save(awaiting);
        verify(paymentExternalMapRepository, never()).save(alreadySynced);
    }

    @Test
    @DisplayName("advanceAwaitingApprovalToPendingPush is a no-op when no map rows exist for the expense")
    void advance_noRows_noop() {
        when(paymentExternalMapRepository.findByPaymentExpenseId(60L)).thenReturn(List.of());

        assertDoesNotThrow(() -> service.advanceAwaitingApprovalToPendingPush(60L));
        verify(paymentExternalMapRepository, never()).save(any());
    }

    @Test
    @DisplayName("advanceAwaitingApprovalToPendingPush swallows exceptions and logs error")
    void advance_swallowsException() {
        when(paymentExternalMapRepository.findByPaymentExpenseId(70L))
                .thenThrow(new RuntimeException("DB down"));

        assertDoesNotThrow(() -> service.advanceAwaitingApprovalToPendingPush(70L));
    }

    // ---- createForNewPaymentReceive ----

    @Test
    @DisplayName("createForNewPaymentReceive persists one PENDING_PUSH map per external service ID at version 1")
    void createForNewPaymentReceive_persistsMapPerServiceId_pendingPush() {
        PaymentReceives payment = new PaymentReceives();
        payment.setId(1027L);

        Tenant tenant = new Tenant();
        tenant.setTenantid("tn-1");

        when(serviceMasterRepository.getIdsByServiceType("Data Exchange")).thenReturn(List.of(100L, 200L));
        when(tenantRepository.findTenant()).thenReturn(tenant);

        service.createForNewPaymentReceive(payment);

        ArgumentCaptor<PaymentExternalMap> captor = ArgumentCaptor.forClass(PaymentExternalMap.class);
        verify(paymentExternalMapRepository, times(2)).save(captor.capture());
        for (PaymentExternalMap saved : captor.getAllValues()) {
            assertEquals("tn-1", saved.getTenantId());
            assertEquals("PENDING_PUSH", saved.getSyncStatus());
            assertEquals(1, saved.getVersion());
            assertNotNull(saved.getEndorsementEffectiveDate());
            assertNull(saved.getEndorsementExpiryDate());
            assertNotNull(saved.getReason());
        }
    }

    @Test
    @DisplayName("createForNewPaymentReceive skips persistence when payment is null")
    void createForNewPaymentReceive_nullPayment_skipsPersist() {
        service.createForNewPaymentReceive(null);

        verify(paymentExternalMapRepository, never()).save(any());
        verify(serviceMasterRepository, never()).getIdsByServiceType(anyString());
    }

    @Test
    @DisplayName("createForNewPaymentReceive skips persistence when payment ID is null")
    void createForNewPaymentReceive_nullPaymentId_skipsPersist() {
        PaymentReceives payment = new PaymentReceives();

        service.createForNewPaymentReceive(payment);

        verify(paymentExternalMapRepository, never()).save(any());
        verify(serviceMasterRepository, never()).getIdsByServiceType(anyString());
    }

    @Test
    @DisplayName("createForNewPaymentReceive skips persistence when no external service IDs found")
    void createForNewPaymentReceive_noExternalServiceIds_skipsPersist() {
        PaymentReceives payment = new PaymentReceives();
        payment.setId(1L);
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(Collections.emptyList());

        service.createForNewPaymentReceive(payment);

        verify(paymentExternalMapRepository, never()).save(any());
        verify(tenantRepository, never()).findTenant();
    }

    @Test
    @DisplayName("createForNewPaymentReceive skips persistence when external service IDs are null")
    void createForNewPaymentReceive_nullExternalServiceIds_skipsPersist() {
        PaymentReceives payment = new PaymentReceives();
        payment.setId(2L);
        when(serviceMasterRepository.getIdsByServiceType(anyString())).thenReturn(null);

        service.createForNewPaymentReceive(payment);

        verify(paymentExternalMapRepository, never()).save(any());
    }

    @Test
    @DisplayName("createForNewPaymentReceive swallows exceptions and logs error")
    void createForNewPaymentReceive_swallowsException() {
        PaymentReceives payment = new PaymentReceives();
        payment.setId(3L);
        when(serviceMasterRepository.getIdsByServiceType(anyString()))
                .thenThrow(new RuntimeException("DB down"));

        assertDoesNotThrow(() -> service.createForNewPaymentReceive(payment));
    }
}

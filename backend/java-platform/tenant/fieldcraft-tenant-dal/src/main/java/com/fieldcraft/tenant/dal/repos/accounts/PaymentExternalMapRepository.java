package com.fieldcraft.tenant.dal.repos.accounts;

import com.fieldcraft.tenant.data.domain.accounts.PaymentExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface PaymentExternalMapRepository extends JpaRepository<PaymentExternalMap, Long> {

    @Query("""
        SELECT pem
        FROM PaymentExternalMap pem
        WHERE pem.paymentExpense.id = :paymentExpenseId
        """)
    List<PaymentExternalMap> findByPaymentExpenseId(@Param("paymentExpenseId") Long paymentExpenseId);

    @Query("""
        SELECT COALESCE(MAX(pem.version), 0)
        FROM PaymentExternalMap pem
        WHERE pem.paymentExpense.id = :paymentExpenseId
          AND pem.serviceId = :serviceId
        """)
    Integer findMaxVersionByPaymentExpenseIdAndServiceId(@Param("paymentExpenseId") Long paymentExpenseId,
                                                         @Param("serviceId") Integer serviceId);

    @Query("""
        SELECT pem
        FROM PaymentExternalMap pem
        WHERE pem.paymentReceives.id = :paymentReceivesId
        """)
    List<PaymentExternalMap> findByPaymentReceivesId(@Param("paymentReceivesId") Long paymentReceivesId);

    /**
     * The open (current) mapping row(s) for a customer payment -- the same
     * {@code endorsementExpiryDate IS NULL} convention {@code InvoiceExternalMapRepository} uses.
     * {@code ux_payment_external_map_active_receive} (V1_5_2_0_41) allows only one such row per
     * (tenant, service, payment).
     */
    @Query("""
        SELECT pem
        FROM PaymentExternalMap pem
        WHERE pem.paymentReceives.id = :paymentReceivesId
          AND pem.endorsementExpiryDate IS NULL
        """)
    List<PaymentExternalMap> findOpenByPaymentReceivesId(@Param("paymentReceivesId") Long paymentReceivesId);

    @Query("""
        SELECT COALESCE(MAX(pem.version), 0)
        FROM PaymentExternalMap pem
        WHERE pem.paymentReceives.id = :paymentReceivesId
          AND pem.serviceId = :serviceId
        """)
    Integer findMaxVersionByPaymentReceivesIdAndServiceId(@Param("paymentReceivesId") Long paymentReceivesId,
                                                           @Param("serviceId") Integer serviceId);

    @Query("""
        SELECT pem
        FROM PaymentExternalMap pem
        WHERE pem.paymentReceives.id = :paymentReceivesId
          AND pem.serviceId = :serviceId
          AND pem.externalPaymentId IS NOT NULL
        ORDER BY pem.version DESC
        """)
    List<PaymentExternalMap> findByPaymentReceivesIdAndServiceIdAndExternalPaymentIdIsNotNullOrderByVersionDesc(
            @Param("paymentReceivesId") Long paymentReceivesId, @Param("serviceId") Integer serviceId);

}

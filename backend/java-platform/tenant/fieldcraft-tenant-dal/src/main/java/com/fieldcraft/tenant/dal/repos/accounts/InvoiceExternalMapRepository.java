package com.fieldcraft.tenant.dal.repos.accounts;

import com.fieldcraft.tenant.data.domain.accounts.InvoiceExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface InvoiceExternalMapRepository extends JpaRepository<InvoiceExternalMap, Long> {

    /**
     * Returns the currently-open row(s) for the invoice — the ones whose endorsement window is
     * still open ({@code endorsement_expiry_date IS NULL}). These must be closed before a new
     * version is inserted (edit) or updated in place (send), because the partial unique index
     * on {@code invoice_external_map} allows only one open row per (tenant, service, invoice).
     */
    @Query("SELECT iem FROM InvoiceExternalMap iem WHERE iem.invoiceId = :invoiceId AND iem.endorsementExpiryDate IS NULL")
    List<InvoiceExternalMap> findOpenByInvoiceId(@Param("invoiceId") Long invoiceId);

    /**
     * Most recent row (any status) for the invoice + service that actually carries a QuickBooks
     * invoice id, used to carry the external ids forward onto the new version after an edit.
     * Bounded to a single row by {@code findFirst ... OrderByVersionDesc}.
     */
    InvoiceExternalMap findFirstByInvoiceIdAndServiceIdAndExternalInvoiceIdIsNotNullOrderByVersionDesc(
            Long invoiceId, Long serviceId);

    @Query("SELECT COALESCE(MAX(iem.version), 0) FROM InvoiceExternalMap iem WHERE iem.invoiceId = :invoiceId AND iem.serviceId = :serviceId")
    Integer findMaxVersionByInvoiceIdAndServiceId(@Param("invoiceId") Long invoiceId, @Param("serviceId") Long serviceId);
}

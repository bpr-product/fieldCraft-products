package com.fieldcraft.tenant.dal.repos.accounts;

import com.fieldcraft.tenant.data.domain.accounts.ReceiptExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface ReceiptExternalMapRepository extends JpaRepository<ReceiptExternalMap, Long> {

    @Query("""
        SELECT rem
        FROM ReceiptExternalMap rem
        WHERE rem.receipt.id = :receiptId
        """)
    List<ReceiptExternalMap> findByReceiptId(@Param("receiptId") Long receiptId);

    @Query("""
        SELECT COALESCE(MAX(rem.version), 0)
        FROM ReceiptExternalMap rem
        WHERE rem.receipt.id = :receiptId
          AND rem.serviceId = :serviceId
        """)
    Integer findMaxVersionByReceiptIdAndServiceId(@Param("receiptId") Long receiptId,
                                                  @Param("serviceId") Long serviceId);

    @Query("""
        SELECT rem
        FROM ReceiptExternalMap rem
        WHERE rem.receiptUuid = :receiptUuid
        """)
    List<ReceiptExternalMap> findByReceiptUuid(@Param("receiptUuid") UUID receiptUuid);
}
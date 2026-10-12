package com.fieldcraft.tenant.dal.repos;

import com.fieldcraft.tenant.data.domain.UserAccountExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.UUID;

public interface UserAccountExternalMapRepository extends JpaRepository<UserAccountExternalMap, Long> {

    /**
     * Returns only the rows that still need to be closed out on the next change
     * (PENDING_PUSH / SYNCED). INVALID and SYNC_FAILED rows are excluded at the query
     * level so the invalidation phase never scans the user's full, unbounded history.
     */
    @Query("SELECT m FROM UserAccountExternalMap m WHERE m.userAccountUuid = :uuid "
            + "AND m.syncStatus IN ('PENDING_PUSH', 'SYNCED')")
    List<UserAccountExternalMap> findActiveByUserAccountUuid(@Param("uuid") UUID uuid);

    /**
     * Most recent row (any status) for the user + service that actually carries an external
     * employee id, used to carry QBO ids forward onto the new version. {@code findFirst ...
     * OrderByVersionDesc} bounds the read to a single row instead of loading history.
     */
    UserAccountExternalMap findFirstByUserAccountUuidAndServiceIdAndExternalEmployeeIdIsNotNullOrderByVersionDesc(
            UUID userAccountUuid, Long serviceId);

    @Query("SELECT MAX(m.version) FROM UserAccountExternalMap m WHERE m.userAccountUuid = :uuid AND m.serviceId = :serviceId")
    Integer findMaxVersionByUserAccountUuidAndServiceId(@Param("uuid") UUID uuid, @Param("serviceId") Long serviceId);
}

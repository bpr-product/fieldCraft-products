package com.fieldcraft.tenant.dal.repos;

import com.fieldcraft.tenant.data.domain.TimelogExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface TimelogExternalMapRepository extends JpaRepository<TimelogExternalMap, Long> {

    /**
     * Returns the currently-open row(s) for the timelog — the ones whose endorsement window is
     * still open ({@code endorsement_expiry_date IS NULL}). These must be closed before a new
     * version is inserted, because the partial unique index
     * {@code ux_timelog_external_map_active_timelog} allows only one open row per
     * (tenant, service, timelog). Bounded by timelog, so the user's full history is never scanned.
     */
    @Query("SELECT m FROM TimelogExternalMap m WHERE m.timelogId = :timelogId "
            + "AND m.endorsementExpiryDate IS NULL")
    List<TimelogExternalMap> findOpenByTimelogId(@Param("timelogId") Long timelogId);

    /**
     * Most recent row (any status) for the timelog + service that actually carries a QBO
     * TimeActivity id, used to carry the external ids forward onto the new version. Bounded to a
     * single row by {@code findFirst ... OrderByVersionDesc}.
     */
    TimelogExternalMap findFirstByTimelogIdAndServiceIdAndExternalTimeActivityIdIsNotNullOrderByVersionDesc(
            Long timelogId, Long serviceId);

    @Query("SELECT MAX(m.version) FROM TimelogExternalMap m WHERE m.timelogId = :timelogId AND m.serviceId = :serviceId")
    Integer findMaxVersionByTimelogIdAndServiceId(@Param("timelogId") Long timelogId, @Param("serviceId") Long serviceId);
}

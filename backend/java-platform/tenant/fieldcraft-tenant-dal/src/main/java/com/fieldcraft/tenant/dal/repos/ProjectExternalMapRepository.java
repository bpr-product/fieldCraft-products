package com.fieldcraft.tenant.dal.repos;

import com.fieldcraft.tenant.data.domain.ProjectExternalMap;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Repository
public interface ProjectExternalMapRepository extends JpaRepository<ProjectExternalMap, Long> {

    List<ProjectExternalMap> findByProject_Id(Long projectId);

    @Transactional
    @Modifying
    @Query("""
            UPDATE ProjectExternalMap pem
            SET pem.syncStatus = CASE pem.syncStatus
                WHEN 'SYNCED' THEN 'DELETED'
                WHEN 'PENDING_PUSH' THEN 'INVALID'
                END
            WHERE pem.project.id = :projectId
              AND pem.syncStatus IN ('SYNCED', 'PENDING_PUSH')
            """)
    void updateSyncStatusOnDelete(@Param("projectId") Long projectId);
}

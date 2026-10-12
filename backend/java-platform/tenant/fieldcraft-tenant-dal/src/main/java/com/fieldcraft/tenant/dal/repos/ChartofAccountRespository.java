package com.fieldcraft.tenant.dal.repos;


import com.fieldcraft.tenant.data.domain.accounts.ChartOfAccount;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface ChartofAccountRespository extends JpaRepository<ChartOfAccount, Long>, JpaSpecificationExecutor<ChartOfAccount> {

    @Query("SELECT DISTINCT coa FROM ChartOfAccount coa WHERE coa.id = :id")
    ChartOfAccount findCOAById(@Param("id") Long id);

    @Query("SELECT DISTINCT coa FROM ChartOfAccount coa WHERE coa.accountType = :type")
    ChartOfAccount findCOAByType(@Param("type") String type);

    @Query(value = "SELECT coa.accountType FROM ChartOfAccount coa")
    List<String> getAllAccountTypes();

}

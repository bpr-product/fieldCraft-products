package com.fieldcraft.tenant.dal.repos.accounts;


/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 11-Jan-2024
 * @author      : Asish
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Data access object for ContractSigningRepository
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *
 *
 *
 */


import com.fieldcraft.tenant.data.domain.accounts.ChartOfAccount;
import com.fieldcraft.tenant.data.model.JobLedgerInfo;
import com.fieldcraft.tenant.data.model.LabourExpense;
import com.fieldcraft.tenant.data.model.accounts.PaymentExpenseInfo;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface ChartOfAccountRepository extends JpaRepository<ChartOfAccount, Long>, JpaSpecificationExecutor<ChartOfAccount> {

    @Query("SELECT ch.id FROM ChartOfAccount ch WHERE ch.code = :code")
    Long findChartOfAccountByCode(@Param("code") String code);

    // get total amount based on code and project id
    //
    // actualCost is SIGNED by entry_type: a CREDIT row (currently only a stock return's
    // StockCreditBill posting) subtracts from the account's actual cost instead of adding to it.
    // payment_expense.amount is stored unsigned for every row -- the direction lives in
    // entry_type -- so without this CASE a return would INCREASE the project's reported cost by
    // the amount it was supposed to give back. Callers sum these values with a plain add() loop
    // (JobLedgerService.getTotalActualAmount), so the sign has to be applied here, at the source.
    @Query("SELECT pr.project.id as id, " +
            "CASE WHEN pr.entryType = 'CREDIT' THEN -pr.amount ELSE pr.amount END as actualCost, " +
            "pe.workerCost as estimateCost, pr.codes as codes, ca.name as name " +
            "FROM ChartOfAccount ca " +
            "INNER JOIN PaymentExpense pr on pr.chartOfAccounts.id = ca.id " +
            "INNER JOIN ProjectEstimate pe on pe.project.id = pr.project.id " +
            "WHERE ca.code = :code AND pr.project.id = :projectId And pe.invalid = false")
    List<JobLedgerInfo> findPaymentExpenses(@Param("code") String code, @Param("projectId") Long projectId);

    // AB#26592: JobLedger's COGS Labor detail rows (JobLedgerService.getLabourExpense) went
    // missing because the original JPQL query projected t.taskName -- a column
    // V1_5_0_0_54__drop_task_legacy_columns.sql already dropped from every tenant's `task`
    // table -- so the query itself threw ("column ... does not exist"), and
    // JobLedgerService.getLabourExpense re-throws that as a ServerException (not swallowed),
    // making the whole COGS Labor section fail to load. Rewritten (then) as a native query
    // joining task/task_attribute for task-level detail.
    //
    // AB#27349: that per-payment_expense-row query made the drill-down list one row per
    // individual clock-in/clock-out entry instead of one consolidated row per resource (e.g.
    // "Jason - 12.5 Hours - $625.00"). Task/task_attribute joins are dropped -- task-level detail
    // is out of scope for this consolidated view (confirmed design decision) -- and the query
    // GROUPs BY resource, summing time/amount across every matching payment_expense row, same
    // GROUP BY / SUM native-query style as TimelogRepository's SUMMARY_BY_EMPLOYEE_SQL.
    //
    // Live report (post-AB#27349): a resource with BOTH Salary and Overtime hours in the same
    // period needs its Type column to actually mean something -- a single row can't carry two
    // types. GROUPs BY (resource, reference_type) as well, so a resource with both types gets two
    // rows ("Jason / Salary / ..." and "Jason / Overtime / ..."), each independently summed; a
    // resource with only one type still gets exactly one row, same as before. LabourExpense stays
    // a plain Spring Data interface projection -- its property names (name/time/amount/type) map
    // onto this query's aliases.
    //
    // Live report: the Direct Job Costs summary (findPaymentExpenses -- every payment_expense row
    // for the code/project, no other joins) counted a payment_expense row this query silently
    // dropped, because the INNER JOIN chain (timelog -> resource_schedule -> resource) drops any
    // row whose reference_id doesn't resolve to a live timelog. Changed to LEFT JOIN so every
    // matching payment_expense row survives.
    //
    // Phase-5 review finding: grouping by bare r.id (nullable after the LEFT JOIN) would collapse
    // EVERY unresolvable payment_expense row of the same reference_type into a single summed row --
    // two separate dangling transactions ($500.36 and $300.00, both "Salary") would merge into one
    // "$800.36 / Salary" line, silently hiding that there were two distinct unresolvable
    // transactions (the same class of information loss this fix exists to eliminate for the
    // resolvable case). Grouping by COALESCE(r.id::text, 'pe-' || pr.id) instead keeps every
    // unresolvable row in ITS OWN group (keyed by its own payment_expense id, always unique), while
    // resolvable rows still consolidate by resource exactly as before (COALESCE returns r.id::text
    // whenever r.id is non-null, so the original grouping key is unchanged for every row this query
    // already handled correctly).
    @Query(value = "SELECT r.name AS name, SUM(tl.time_spent) AS time, SUM(pr.amount) AS amount, " +
                   "pr.reference_type AS type " +
                   "FROM chart_of_account ca " +
                   "INNER JOIN payment_expense pr ON pr.chart_of_account_id = ca.id " +
                   "LEFT JOIN timelog tl ON tl.id = pr.reference_id " +
                   "LEFT JOIN resource_schedule rs ON rs.id = tl.resource_schedule_id " +
                   "LEFT JOIN resource r ON r.id = rs.resource_id " +
                   "WHERE ca.code = :code AND pr.project_id = :projectId " +
                   "GROUP BY COALESCE(r.id::text, 'pe-' || pr.id), r.name, pr.reference_type " +
                   "ORDER BY r.name ASC NULLS LAST, pr.reference_type ASC",
           nativeQuery = true)
    List<LabourExpense> findLabourExpense(@Param("code") String code, @Param("projectId") Long projectId);

    @Query("SELECT pe.referenceId " +
            "FROM ChartOfAccount ca " +
            "INNER JOIN PaymentExpense pe on pe.chartOfAccounts.id = ca.id " +
            "WHERE pe.project.id = :projectId AND ca.code = :code")
    List<Long> findIdByReferenceTypeAndProjectId(@Param("code") String code, @Param("projectId") Long projectId);

    @Query("SELECT ch.name FROM ChartOfAccount ch WHERE ch.code = :code")
    String findChartOfAccountNameByCode(@Param("code") String code);

    @Query("SELECT ch.id FROM ChartOfAccount ch WHERE LOWER(ch.name) = LOWER(:name) AND ch.active = true")
    Long findIdByName(@Param("name") String name);

    // Name-only lookup returned as a List -- a single-result query (findIdByName above) throws
    // IncorrectResultSizeDataAccessException the moment a name matches more than one active row
    // (e.g. duplicate QBO-synced accounts); callers that can't rule that out must use this and pick
    // deterministically among matches instead of crashing.
    @Query("SELECT ch.id FROM ChartOfAccount ch WHERE LOWER(ch.name) = LOWER(:name) AND ch.active = true")
    List<Long> findIdsByName(@Param("name") String name);

    @Query("SELECT ch FROM ChartOfAccount ch WHERE ch.active = true ORDER BY ch.name ASC")
    List<ChartOfAccount> findAllActive();

    // LEFT JOIN Receipt so a payment_expense row whose referenceId doesn't resolve to a live
    // Receipt still surfaces (join-drop bug, same shape as findLabourExpense above) -- r.id/
    // r.name/r.ReceiptAdditionalInfo come back null for that row instead of dropping it entirely.
    // PaymentExpenseInfo's getters are already boxed/nullable (Long/String), so no interface change
    // is needed to carry the null case. r.vendor_id -> vendorUid: Bill Review per-line COA
    // routing's drill-down vendor display (JobLedgerService.getExpenseDetails) -- the interface
    // already declared getVendorUid() but this query never actually selected it, so it always
    // came back null.
    @Query("SELECT pe.amount as total, r.ReceiptAdditionalInfo as receiptAdditionalInfo, " +
            "r.id as receiptId, r.name as receiptType, pe.billable as billable, " +
            "r.vendor_id as vendorUid " +
            "FROM ChartOfAccount ca " +
            "INNER JOIN PaymentExpense pe on pe.chartOfAccounts.id = ca.id " +
            "LEFT JOIN Receipt r on r.id = pe.referenceId " +
            "WHERE pe.project.id = :projectId AND ca.code = :code")
    List<PaymentExpenseInfo> findExpenseDetailsByProjectIdAndCode(@Param("code") String code, @Param("projectId") Long projectId);


    @Query(value = "SELECT coa.accountType FROM ChartOfAccount coa")
    List<String> getAllAccountTypes();

    @Query("SELECT ch FROM ChartOfAccount ch WHERE ch.code = :code AND ch.active = true")
    Optional<ChartOfAccount> findEntityByCode(@Param("code") String code);

    // accountType is not unique -- e.g. QBO-synced "Inventory Asset" and "Fuel" can both carry
    // accountType = "Other Current Asset" -- so callers must handle more than one match.
    @Query("SELECT ch FROM ChartOfAccount ch WHERE ch.accountType = :accountType AND ch.active = true")
    List<ChartOfAccount> findByAccountType(@Param("accountType") String accountType);

    // Resolves a COA by BOTH name and accountType together -- used to pin Inventory Vendor Bills
    // (stock POs, project-less) to the "Inventory Asset" / "Other Current Asset" account
    // specifically. Returns a List, not a single Long/entity -- even name+accountType together can
    // still match more than one active row (e.g. duplicate QBO-synced accounts), and a
    // single-result query throws IncorrectResultSizeDataAccessException ("Query did not return a
    // unique result") the moment that happens; callers must pick deterministically among matches.
    @Query("SELECT ch.id FROM ChartOfAccount ch WHERE LOWER(ch.name) = LOWER(:name) " +
            "AND LOWER(ch.accountType) = LOWER(:accountType) AND ch.active = true")
    List<Long> findIdByNameAndAccountType(@Param("name") String name, @Param("accountType") String accountType);

}

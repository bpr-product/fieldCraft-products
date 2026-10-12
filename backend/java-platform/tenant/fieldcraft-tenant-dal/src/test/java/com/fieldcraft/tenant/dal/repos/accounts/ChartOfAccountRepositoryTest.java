package com.fieldcraft.tenant.dal.repos.accounts;

import com.fieldcraft.shared.domain.EnumCategory;
import com.fieldcraft.shared.domain.EnumValue;
import com.fieldcraft.tenant.dal.repos.config.AbstractTestContainerConfig;
import com.fieldcraft.tenant.dal.repos.config.TenantDbTestConfig;
import com.fieldcraft.tenant.data.domain.Project;
import com.fieldcraft.tenant.data.domain.Resource;
import com.fieldcraft.tenant.data.domain.ResourceSchedule;
import com.fieldcraft.tenant.data.domain.Task;
import com.fieldcraft.tenant.data.domain.TaskAttribute;
import com.fieldcraft.tenant.data.domain.TaskAttributeIds;
import com.fieldcraft.tenant.data.domain.Timelog;
import com.fieldcraft.tenant.data.domain.UserAccount;
import com.fieldcraft.tenant.data.domain.accounts.ChartOfAccount;
import com.fieldcraft.tenant.data.domain.accounts.PaymentExpense;
import com.fieldcraft.tenant.data.domain.accounts.Receipt;
import com.fieldcraft.tenant.data.model.JobLedgerInfo;
import com.fieldcraft.tenant.data.model.LabourExpense;
import com.fieldcraft.tenant.data.model.accounts.PaymentExpenseInfo;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager;
import org.springframework.test.context.junit.jupiter.SpringExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Live report: findLabourExpense's native query joined "payment_expense pr ON
 * pr.chartofaccount_id = ca.id" -- a typo (the real column is chart_of_account_id, matching
 * PaymentExpense.chartOfAccounts's @JoinColumn(name = "chartOfAccount_id")) that threw
 * "column pr.chartofaccount_id does not exist" on every call, breaking JobLedger's COGS Labor
 * detail rows for every tenant. This is a hand-written native SQL string a mocked-repository
 * unit test can't catch (the column name is never type-checked against the schema) -- covers it
 * against a real Postgres instance (Testcontainers), same pattern as ResourceScheduleRepositoryTest.
 *
 * AB#27349: the query used to return one row per payment_expense/timelog entry, so the Job
 * Ledger COGS Labor drill-down listed every individual clock-in/clock-out record separately
 * instead of one consolidated line per resource (e.g. "Jason - 12.5 Hours - $625.00"). Now
 * grouped by resource, summing time/amount across all matching payment_expense rows.
 */
@ExtendWith(SpringExtension.class)
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@org.springframework.data.jpa.repository.config.EnableJpaRepositories(
        basePackageClasses = ChartOfAccountRepository.class,
        includeFilters = @org.springframework.context.annotation.ComponentScan.Filter(
                type = org.springframework.context.annotation.FilterType.ASSIGNABLE_TYPE,
                classes = ChartOfAccountRepository.class
        )
)
@org.springframework.test.context.ContextConfiguration(classes = TenantDbTestConfig.class)
class ChartOfAccountRepositoryTest extends AbstractTestContainerConfig {

    @Autowired
    private ChartOfAccountRepository chartOfAccountRepository;

    @Autowired
    private TestEntityManager entityManager;

    private static final OffsetDateTime NOW = OffsetDateTime.now(ZoneOffset.UTC);
    private static final OffsetDateTime PAST = NOW.minusYears(1);
    private static final OffsetDateTime FUTURE = OffsetDateTime.of(3000, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);

    private Project project;
    private Resource resource;

    @BeforeEach
    void setUp() {
        project = createAndSaveProject();
        resource = createAndSaveResource();
    }

    // ── fixture builders (same pattern as ResourceScheduleRepositoryTest) ─────────

    private Project createAndSaveProject() {
        Project p = new Project();
        p.setProjectUid(UUID.randomUUID());
        p.setCustomerUid(UUID.randomUUID());
        p.setCustomerAsset("DEMO-ASSET");
        p.setCustomer("DEMO-CUSTOMER");
        p.setOrderNo("DEMO-ORDER-" + UUID.randomUUID());
        p.setTrade("DEMO-TRADE");
        p.setStatus("active");
        p.setInvalid(false);
        p.setTaxExemption(false);
        p.setVersion(0);
        p.setCreatedAt(NOW);
        p.setUpdatedAt(NOW);
        return entityManager.persistAndFlush(p);
    }

    private EnumCategory createAndSaveEnumCategory() {
        EnumCategory c = new EnumCategory();
        c.setName("resource_status_" + UUID.randomUUID());
        c.setDisplayName("Resource Status");
        return entityManager.persistAndFlush(c);
    }

    private EnumValue createAndSaveEnumValue(EnumCategory category) {
        EnumValue v = new EnumValue();
        ReflectionTestUtils.setField(v, "UUID", UUID.randomUUID());
        v.setCode("ACTIVE");
        v.setLabel("Active");
        ReflectionTestUtils.setField(v, "enumCategoryId", category);
        return entityManager.persistAndFlush(v);
    }

    private UserAccount createAndSaveUserAccount() {
        EnumValue status = createAndSaveEnumValue(createAndSaveEnumCategory());
        UserAccount u = new UserAccount();
        u.setObjectId(UUID.randomUUID().toString());
        u.setEmail("demo-" + UUID.randomUUID() + "@example.com");
        u.setMobileNo("0000000000");
        u.setFirstname("Demo");
        u.setActive(true);
        u.setEulaAccepted(true);
        u.setDeleted(false);
        u.setUpdatedAt(NOW);
        u.setCreatedAt(NOW);
        u.setStatus(status);
        u.setSchemaVersion(0);
        u.setVersion(0);
        ReflectionTestUtils.setField(u, "createdBy", "test-harness");
        return entityManager.persistAndFlush(u);
    }

    private Resource createAndSaveResource() {
        Resource r = new Resource();
        r.setResourceUid(UUID.randomUUID());
        r.setName("Demo Laborer");
        r.setStatus("active");
        r.setCanOverallocate(false);
        r.setVersion(0);
        r.setCodes("{}");
        r.setUserAccount(createAndSaveUserAccount());
        return entityManager.persistAndFlush(r);
    }

    private Task createAndSaveTask(Project project) {
        Task t = new Task();
        t.setTaskOrder(1);
        t.setTaskUid(UUID.randomUUID());
        t.setIsMilestone(false);
        // task.status / task_name / task_type are not columns any more (V1_5_0_0_54; two-door audit 4.13):
        // the entity carries them as @Transient, so a fixture setter here seeded nothing. Tests that
        // need a value insert the task_attribute row instead, as this class's EAV helpers do.
        t.setPhase("active_construction");
        t.setCreatedAt(NOW);
        t.setInvalid(false);
        t.setVersion(0);
        t.setProject(project);
        t.setParentProject(project);
        return entityManager.persistAndFlush(t);
    }

    private void saveTaskNameAttribute(Long taskId, String taskName) {
        TaskAttribute a = new TaskAttribute();
        ReflectionTestUtils.setField(a, "taskId", taskId);
        ReflectionTestUtils.setField(a, "attributeId", TaskAttributeIds.TASK_NAME);
        ReflectionTestUtils.setField(a, "attrValueText", taskName);
        ReflectionTestUtils.setField(a, "isValid", true);
        ReflectionTestUtils.setField(a, "versionEffectiveDate", PAST);
        ReflectionTestUtils.setField(a, "versionExpiryDate", FUTURE);
        entityManager.persistAndFlush(a);
    }

    private ResourceSchedule createAndSaveSchedule(Resource resource, Project project, Task task) {
        ResourceSchedule rs = new ResourceSchedule();
        rs.setResource(resource);
        rs.setProject(project);
        rs.setTask(task);
        rs.setStatus("active");
        rs.setStartTime(NOW.minusDays(1));
        rs.setEndTime(NOW.plusDays(1));
        rs.setCreatedAt(NOW);
        rs.setUpdatedAt(NOW);
        rs.setInvalid(false);
        rs.setVersion(0);
        return entityManager.persistAndFlush(rs);
    }

    private ChartOfAccount createAndSaveChartOfAccount(String code) {
        ChartOfAccount ca = new ChartOfAccount();
        ca.setCode(code);
        ca.setName("Labor Expense");
        ca.setAccountNo("DEMO-0001-" + UUID.randomUUID());
        ca.setBuiltin(false);
        ca.setActive(true);
        return entityManager.persistAndFlush(ca);
    }

    private Timelog createAndSaveTimelog(ResourceSchedule schedule, Double timeSpent) {
        Timelog tl = new Timelog();
        tl.setResourceSchedule(schedule);
        tl.setStatus("COMPLETED");
        tl.setStartTime(NOW.minusHours(8));
        tl.setTimeSpent(timeSpent);
        return entityManager.persistAndFlush(tl);
    }

    private PaymentExpense createAndSavePaymentExpense(ChartOfAccount coa, Project project, Timelog timelog,
                                                        BigDecimal amount, String referenceType) {
        PaymentExpense pe = new PaymentExpense();
        pe.setChartOfAccounts(coa);
        pe.setAmount(amount);
        pe.setReferenceId(timelog.getId());
        pe.setReferenceType(referenceType);
        pe.setProject(project);
        pe.setCreatedAt(NOW);
        return entityManager.persistAndFlush(pe);
    }

    // referenceId points at a row that is NOT a timelog id (e.g. a deleted timelog, or a
    // payment_expense row whose reference never resolved) -- the join-drop bug case.
    private PaymentExpense createAndSavePaymentExpenseWithDanglingReference(ChartOfAccount coa, Project project,
                                                                             Long danglingReferenceId,
                                                                             BigDecimal amount, String referenceType) {
        PaymentExpense pe = new PaymentExpense();
        pe.setChartOfAccounts(coa);
        pe.setAmount(amount);
        pe.setReferenceId(danglingReferenceId);
        pe.setReferenceType(referenceType);
        pe.setProject(project);
        pe.setCreatedAt(NOW);
        return entityManager.persistAndFlush(pe);
    }

    private Receipt createAndSaveReceipt(Project project, String name, String receiptAdditionalInfo) {
        Receipt r = new Receipt();
        r.setProject(project);
        r.setName(name);
        r.setStatus("received");
        r.setReceiptAdditionalInfo(receiptAdditionalInfo);
        r.setCreatedAt(NOW);
        r.setInvalid(false);
        r.setVersion(0);
        r.setReceiptUuid(UUID.randomUUID());
        return entityManager.persistAndFlush(r);
    }

    private PaymentExpense createAndSavePaymentExpenseForReceipt(ChartOfAccount coa, Project project, Receipt receipt,
                                                                  BigDecimal amount) {
        PaymentExpense pe = new PaymentExpense();
        pe.setChartOfAccounts(coa);
        pe.setAmount(amount);
        pe.setReferenceId(receipt.getId());
        pe.setReferenceType("Bill");
        pe.setProject(project);
        pe.setCreatedAt(NOW);
        return entityManager.persistAndFlush(pe);
    }

    // Materials/Tools/Subcontractor path -- referenceId points at a row that is NOT a Receipt id
    // (e.g. a deleted/never-created receipt) -- the same join-drop bug shape as Labor's dangling
    // timelog reference, but through findExpenseDetailsByProjectIdAndCode's INNER JOIN Receipt.
    private PaymentExpense createAndSavePaymentExpenseWithDanglingReceiptReference(ChartOfAccount coa, Project project,
                                                                                    Long danglingReceiptId,
                                                                                    BigDecimal amount) {
        PaymentExpense pe = new PaymentExpense();
        pe.setChartOfAccounts(coa);
        pe.setAmount(amount);
        pe.setReferenceId(danglingReceiptId);
        pe.setReferenceType("Bill");
        pe.setProject(project);
        pe.setCreatedAt(NOW);
        return entityManager.persistAndFlush(pe);
    }

    // ── tests ────────────────────────────────────────────────────────────────

    @Test
    @DisplayName("findLabourExpense joins payment_expense on the real chart_of_account_id column, not the typo'd chartofaccount_id")
    void findLabourExpense_returnsRow_joiningOnRealColumnName() {
        Task task = createAndSaveTask(project);
        saveTaskNameAttribute(task.getId(), "Install Shingles");
        ResourceSchedule schedule = createAndSaveSchedule(resource, project, task);
        Timelog timelog = createAndSaveTimelog(schedule, 8.0);
        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-LABOR-" + UUID.randomUUID());
        createAndSavePaymentExpense(coa, project, timelog, new BigDecimal("200.000"), "Salary");
        entityManager.clear();

        List<LabourExpense> result = chartOfAccountRepository.findLabourExpense(coa.getCode(), project.getId());

        assertEquals(1, result.size());
        LabourExpense row = result.get(0);
        assertEquals("Demo Laborer", row.getName());
        assertEquals(0, new BigDecimal("200.000").compareTo(row.getAmount()));
        assertEquals(8.0, row.getTime());
        assertEquals("Salary", row.getType());
    }

    @Test
    @DisplayName("findLabourExpense returns empty (not an error) when no payment_expense row matches the code/project")
    void findLabourExpense_noMatch_returnsEmpty() {
        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-UNUSED-" + UUID.randomUUID());

        List<LabourExpense> result = chartOfAccountRepository.findLabourExpense(coa.getCode(), project.getId());

        org.junit.jupiter.api.Assertions.assertTrue(result.isEmpty());
    }

    // AB#27349: the Job Ledger COGS Labor drill-down must show ONE consolidated row per resource
    // (e.g. "Jason - 12.5 Hours - $625.00"), not one row per individual clock-in/clock-out entry.
    // Two separate approved timelog entries for the SAME resource on DIFFERENT tasks must collapse
    // into a single summed row, not two.
    @Test
    @DisplayName("findLabourExpense consolidates multiple payment_expense rows for the same resource into one summed row")
    void findLabourExpense_multipleEntriesSameResource_returnsOneConsolidatedRow() {
        Task task1 = createAndSaveTask(project);
        saveTaskNameAttribute(task1.getId(), "Install Shingles");
        ResourceSchedule schedule1 = createAndSaveSchedule(resource, project, task1);
        Timelog timelog1 = createAndSaveTimelog(schedule1, 8.0);

        Task task2 = createAndSaveTask(project);
        saveTaskNameAttribute(task2.getId(), "Clean Gutters");
        ResourceSchedule schedule2 = createAndSaveSchedule(resource, project, task2);
        Timelog timelog2 = createAndSaveTimelog(schedule2, 4.5);

        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-LABOR-" + UUID.randomUUID());
        createAndSavePaymentExpense(coa, project, timelog1, new BigDecimal("200.00"), "Salary");
        createAndSavePaymentExpense(coa, project, timelog2, new BigDecimal("112.50"), "Salary");
        entityManager.clear();

        List<LabourExpense> result = chartOfAccountRepository.findLabourExpense(coa.getCode(), project.getId());

        assertEquals(1, result.size());
        LabourExpense row = result.get(0);
        assertEquals("Demo Laborer", row.getName());
        assertEquals(12.5, row.getTime());
        assertEquals(0, new BigDecimal("312.50").compareTo(row.getAmount()));
        assertEquals("Salary", row.getType());
    }

    // Live report (post-AB#27349): a resource with BOTH Salary and Overtime hours in the same
    // period must NOT collapse into one row -- a single row can't carry two Type values. Each
    // type gets its own row, independently summed, same resource.
    @Test
    @DisplayName("findLabourExpense splits Salary and Overtime for the same resource into separate rows")
    void findLabourExpense_sameResourceDifferentTypes_returnsOneRowPerType() {
        Task task1 = createAndSaveTask(project);
        saveTaskNameAttribute(task1.getId(), "Install Shingles");
        ResourceSchedule schedule1 = createAndSaveSchedule(resource, project, task1);
        Timelog timelog1 = createAndSaveTimelog(schedule1, 8.0);

        Task task2 = createAndSaveTask(project);
        saveTaskNameAttribute(task2.getId(), "Weekend Emergency Repair");
        ResourceSchedule schedule2 = createAndSaveSchedule(resource, project, task2);
        Timelog timelog2 = createAndSaveTimelog(schedule2, 3.0);

        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-LABOR-" + UUID.randomUUID());
        createAndSavePaymentExpense(coa, project, timelog1, new BigDecimal("200.00"), "Salary");
        createAndSavePaymentExpense(coa, project, timelog2, new BigDecimal("90.00"), "Overtime");
        entityManager.clear();

        List<LabourExpense> result = chartOfAccountRepository.findLabourExpense(coa.getCode(), project.getId());

        assertEquals(2, result.size());
        LabourExpense salaryRow = result.stream().filter(r -> "Salary".equals(r.getType())).findFirst().orElseThrow();
        LabourExpense overtimeRow = result.stream().filter(r -> "Overtime".equals(r.getType())).findFirst().orElseThrow();
        assertEquals("Demo Laborer", salaryRow.getName());
        assertEquals(8.0, salaryRow.getTime());
        assertEquals(0, new BigDecimal("200.00").compareTo(salaryRow.getAmount()));
        assertEquals("Demo Laborer", overtimeRow.getName());
        assertEquals(3.0, overtimeRow.getTime());
        assertEquals(0, new BigDecimal("90.00").compareTo(overtimeRow.getAmount()));
    }

    // Two DIFFERENT resources booking labor on the same project must stay as two separate rows --
    // consolidation groups by resource, not by project/chart-of-account alone.
    @Test
    @DisplayName("findLabourExpense keeps separate resources as separate rows")
    void findLabourExpense_multipleResources_returnsOneRowPerResource() {
        Resource secondResource = createAndSaveResource();

        Task task = createAndSaveTask(project);
        saveTaskNameAttribute(task.getId(), "Install Shingles");
        ResourceSchedule schedule1 = createAndSaveSchedule(resource, project, task);
        ResourceSchedule schedule2 = createAndSaveSchedule(secondResource, project, task);
        Timelog timelog1 = createAndSaveTimelog(schedule1, 8.0);
        Timelog timelog2 = createAndSaveTimelog(schedule2, 6.0);

        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-LABOR-" + UUID.randomUUID());
        createAndSavePaymentExpense(coa, project, timelog1, new BigDecimal("200.00"), "Salary");
        createAndSavePaymentExpense(coa, project, timelog2, new BigDecimal("150.00"), "Salary");
        entityManager.clear();

        List<LabourExpense> result = chartOfAccountRepository.findLabourExpense(coa.getCode(), project.getId());

        assertEquals(2, result.size());
    }

    // Live report: the Direct Job Costs summary row (findPaymentExpenses -- every payment_expense
    // row for the code/project, no other joins) showed "3 items / $500.36" for COGS Labor while
    // the drill-down (findLabourExpense) showed "0 posted / No transactions posted yet." Root
    // cause: findLabourExpense's INNER JOIN chain (timelog -> resource_schedule -> resource)
    // silently drops any payment_expense row whose referenceId doesn't resolve to a live timelog --
    // with no relation to any actual "posted" status (no such column exists on payment_expense).
    // A payment_expense row the summary counts must never vanish from the detail view.
    @Test
    @DisplayName("findLabourExpense does not silently drop a payment_expense row whose reference_id has no resolvable timelog (join-drop bug)")
    void findLabourExpense_danglingTimelogReference_stillReturnsTheRow() {
        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-LABOR-" + UUID.randomUUID());
        Long danglingTimelogId = 987654321L; // no Timelog row exists with this id
        createAndSavePaymentExpenseWithDanglingReference(coa, project, danglingTimelogId,
                new BigDecimal("500.36"), "Salary");
        entityManager.clear();

        // The summary query counts this row regardless of any join resolution.
        List<JobLedgerInfo> summary = chartOfAccountRepository.findPaymentExpenses(coa.getCode(), project.getId());
        assertEquals(1, summary.size());

        List<LabourExpense> detail = chartOfAccountRepository.findLabourExpense(coa.getCode(), project.getId());

        assertEquals(1, detail.size(), "a payment_expense row the summary counts must still appear in the detail table");
        LabourExpense row = detail.get(0);
        assertEquals(0, new BigDecimal("500.36").compareTo(row.getAmount()));
        assertEquals("Salary", row.getType());
    }

    // Phase-5 review finding: grouping the LEFT JOIN fix's unresolvable rows by bare r.id (always
    // NULL for every dangling reference) would merge ALL of them together, hiding that there were
    // multiple distinct unresolvable transactions -- exactly the kind of count-masking this fix
    // exists to eliminate for the resolvable case. Two separate dangling-reference payment_expense
    // rows (same reference_type, different amounts) must stay as two separate detail rows, not
    // collapse into one summed row.
    @Test
    @DisplayName("findLabourExpense keeps TWO separate dangling-reference payment_expense rows distinct, not merged into one")
    void findLabourExpense_multipleDanglingTimelogReferences_staySeparate() {
        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-LABOR-" + UUID.randomUUID());
        createAndSavePaymentExpenseWithDanglingReference(coa, project, 987654321L,
                new BigDecimal("500.36"), "Salary");
        createAndSavePaymentExpenseWithDanglingReference(coa, project, 987654323L,
                new BigDecimal("300.00"), "Salary");
        entityManager.clear();

        List<JobLedgerInfo> summary = chartOfAccountRepository.findPaymentExpenses(coa.getCode(), project.getId());
        assertEquals(2, summary.size(), "the summary counts both dangling rows separately");

        List<LabourExpense> detail = chartOfAccountRepository.findLabourExpense(coa.getCode(), project.getId());

        assertEquals(2, detail.size(),
                "two distinct dangling-reference transactions must not merge into one summed row");
        BigDecimal totalDetailAmount = detail.stream().map(LabourExpense::getAmount).reduce(BigDecimal.ZERO, BigDecimal::add);
        assertEquals(0, new BigDecimal("800.36").compareTo(totalDetailAmount));
    }

    // Same join-drop shape as Labor's dangling timelog reference, but for the
    // Materials/Tools/Subcontractor path (findExpenseDetailsByProjectIdAndCode's INNER JOIN
    // Receipt). A payment_expense row whose referenceId never resolves to a live Receipt must
    // still surface in the detail table (with a null receiptId/receiptType), not vanish.
    @Test
    @DisplayName("findExpenseDetailsByProjectIdAndCode does not silently drop a payment_expense row whose reference_id has no resolvable Receipt (join-drop bug)")
    void findExpenseDetailsByProjectIdAndCode_danglingReceiptReference_stillReturnsTheRow() {
        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-MATERIALS-" + UUID.randomUUID());
        Long danglingReceiptId = 987654322L; // no Receipt row exists with this id
        createAndSavePaymentExpenseWithDanglingReceiptReference(coa, project, danglingReceiptId,
                new BigDecimal("97.20"));
        entityManager.clear();

        List<JobLedgerInfo> summary = chartOfAccountRepository.findPaymentExpenses(coa.getCode(), project.getId());
        assertEquals(1, summary.size());

        List<PaymentExpenseInfo> detail = chartOfAccountRepository.findExpenseDetailsByProjectIdAndCode(coa.getCode(), project.getId());

        assertEquals(1, detail.size(), "a payment_expense row the summary counts must still appear in the detail table");
        PaymentExpenseInfo row = detail.get(0);
        assertEquals(0, new BigDecimal("97.20").compareTo(row.getTotal()));
        org.junit.jupiter.api.Assertions.assertNull(row.getReceiptId());
    }

    // A payment_expense row whose reference DOES resolve to a real Receipt must keep carrying the
    // receipt's own id/name/additional info exactly as before -- the LEFT JOIN fix must not regress
    // the already-working, resolvable case.
    @Test
    @DisplayName("findExpenseDetailsByProjectIdAndCode still returns receiptId/receiptType/additionalInfo for a resolvable Receipt")
    void findExpenseDetailsByProjectIdAndCode_resolvableReceipt_returnsReceiptFields() {
        ChartOfAccount coa = createAndSaveChartOfAccount("SYN-MATERIALS-" + UUID.randomUUID());
        Receipt receipt = createAndSaveReceipt(project, "Bill", "[{\"billNo\":\"SDB-345837\"}]");
        createAndSavePaymentExpenseForReceipt(coa, project, receipt, new BigDecimal("97.20"));
        entityManager.clear();

        List<PaymentExpenseInfo> detail = chartOfAccountRepository.findExpenseDetailsByProjectIdAndCode(coa.getCode(), project.getId());

        assertEquals(1, detail.size());
        PaymentExpenseInfo row = detail.get(0);
        assertEquals(receipt.getId(), row.getReceiptId());
        assertEquals("Bill", row.getReceiptType());
        assertEquals(0, new BigDecimal("97.20").compareTo(row.getTotal()));
    }
}

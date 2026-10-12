import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

jest.mock("./QuickBooksTable.scss", () => ({}));

// QuickBooksTable is a large workspace: a single render mounts the rail, module
// header, table and every dialog. Tests that navigate modules and drive dialogs
// routinely need more than Jest's 5s default on a loaded CI agent.
jest.setTimeout(20000);

const mockUseOidcAccessToken = jest.fn();

jest.mock("@axa-fr/react-oidc", () => ({
  useOidcAccessToken: () => mockUseOidcAccessToken(),
}));

jest.mock("../../../../hooks/UserSession", () => {
  const React = require("react");
  return {
    UserContext: React.createContext({
      userSessionDetails: { objectId: "object-123" },
    }),
  };
});

jest.mock("../../../../services/TenantService", () => ({
  getTenantDetails: jest.fn(),
}));

jest.mock("../../../../services/FeaturesService", () => ({
  getAllServices: jest.fn(),
  getSubscriberById: jest.fn(),
}));

jest.mock("../../../../services/ApiBase", () => ({
  executePostDataWithHeaders: jest.fn(),
}));

jest.mock("../../../../auth/OidcConfig", () => ({
  ConfigUrl: {
    apiUrl: "https://tenant-api.example.com",
    extservice_base_url: "https://external-service.example.com",
  },
}));

jest.mock("../../../../services/integrations/QuickBooksService", () => ({
  disconnectQuickBooks: jest.fn(),
  downloadQuickBooksBillDocument: jest.fn(),
  downloadQuickBooksInvoiceDocument: jest.fn(),
  getQuickBooksAccountCdcView: jest.fn(), getQuickBooksAutoSyncConfig: jest.fn(),
  getQuickBooksBillCdcView: jest.fn(), saveQuickBooksAutoSyncConfig: jest.fn(),
  getQuickBooksBillDocumentInfo: jest.fn(),
  getQuickBooksBillLineItems: jest.fn(),
  getQuickBooksStatus: jest.fn(),
  getQuickBooksSyncJob: jest.fn(),
  getQuickBooksSyncState: jest.fn(),
  getQuickBooksVendorCdcView: jest.fn(),
  initiateQuickBooksAuth: jest.fn(),
  startQuickBooksBillsSync: jest.fn(),
  startQuickBooksChartOfAccountsSync: jest.fn(),
  startQuickBooksVendorsSync: jest.fn(),
  // bill payments
  getQuickBooksBillPaymentCdcView: jest.fn(),
  getQuickBooksBillPaymentAccounts: jest.fn(),
  assignQuickBooksBillPaymentAccount: jest.fn(),
  startQuickBooksBillPaymentsSync: jest.fn(),
  // customers
  getQuickBooksCustomerCdcView: jest.fn(),
  startQuickBooksCustomersSync: jest.fn(),
  // projects
  getQuickBooksProjectCdcView: jest.fn(),
  startQuickBooksProjectsSync: jest.fn(),
  // employees
  getQuickBooksEmployeeCdcView: jest.fn(),
  startQuickBooksEmployeesSync: jest.fn(),
  getQuickBooksEmployeeSyncConfig: jest.fn(),
  saveQuickBooksEmployeeSyncConfig: jest.fn(),
  DEFAULT_EMPLOYEE_SYNC_CONFIG: { sync_mode: "cdc", days_after_hire: 1, configured: false },
  // service roles (new module)
  getQuickBooksServiceRoleCdcView: jest.fn(),
  startQuickBooksServiceRolesSync: jest.fn(),
  getQuickBooksServiceIncomeAccounts: jest.fn(),
  getQuickBooksServiceCoaAccounts: jest.fn(),
  assignQuickBooksServiceIncomeAccount: jest.fn(),
  // timelogs (time activity)
  getQuickBooksTimelogCdcView: jest.fn(),
  startQuickBooksTimelogsSync: jest.fn(),
  // project sync config
  getQuickBooksProjectSyncConfig: jest.fn(),
  saveQuickBooksProjectSyncConfig: jest.fn(),
  DEFAULT_PROJECT_SYNC_CONFIG: { sync_mode: "cdc", configured: false },
}));

import QuickBooksTable from "./QuickBooksTable";
import { UserContext } from "../../../../hooks/UserSession";
import { getTenantDetails } from "../../../../services/TenantService";
import { getAllServices, getSubscriberById } from "../../../../services/FeaturesService";
import { executePostDataWithHeaders } from "../../../../services/ApiBase";
import {
  disconnectQuickBooks,
  downloadQuickBooksBillDocument,
  downloadQuickBooksInvoiceDocument,
  getQuickBooksAccountCdcView,
  getQuickBooksBillCdcView,
  getQuickBooksBillDocumentInfo,
  getQuickBooksBillLineItems,
  getQuickBooksStatus,
  getQuickBooksSyncJob,
  getQuickBooksSyncState,
  getQuickBooksVendorCdcView,
  initiateQuickBooksAuth,
  startQuickBooksBillsSync,
  startQuickBooksChartOfAccountsSync,
  startQuickBooksVendorsSync,
  getQuickBooksCustomerCdcView,
  startQuickBooksCustomersSync,
  getQuickBooksServiceRoleCdcView,
  getQuickBooksServiceCoaAccounts,
  getQuickBooksServiceIncomeAccounts,
  startQuickBooksServiceRolesSync,
  assignQuickBooksServiceIncomeAccount,
  getQuickBooksProjectSyncConfig,
  saveQuickBooksProjectSyncConfig,
  getQuickBooksProjectCdcView,
  startQuickBooksProjectsSync,
  getQuickBooksEmployeeCdcView,
  startQuickBooksEmployeesSync,
  getQuickBooksEmployeeSyncConfig,
  saveQuickBooksEmployeeSyncConfig,
  getQuickBooksBillPaymentCdcView,
  getQuickBooksBillPaymentAccounts,
  assignQuickBooksBillPaymentAccount,
  startQuickBooksBillPaymentsSync,
  getQuickBooksTimelogCdcView,
  startQuickBooksTimelogsSync,
} from "../../../../services/integrations/QuickBooksService";

const mockedGetTenantDetails = getTenantDetails as jest.Mock;
const mockedGetAllServices = getAllServices as jest.Mock;
const mockedGetSubscriberById = getSubscriberById as jest.Mock;
const mockedExecutePostDataWithHeaders = executePostDataWithHeaders as jest.Mock;
const mockedDisconnectQuickBooks = disconnectQuickBooks as jest.Mock;
const mockedGetQuickBooksAccountCdcView = getQuickBooksAccountCdcView as jest.Mock;
const mockedGetQuickBooksBillCdcView = getQuickBooksBillCdcView as jest.Mock;
const mockedGetQuickBooksStatus = getQuickBooksStatus as jest.Mock;
const mockedGetQuickBooksSyncJob = getQuickBooksSyncJob as jest.Mock;
const mockedGetQuickBooksSyncState = getQuickBooksSyncState as jest.Mock;
const mockedGetQuickBooksVendorCdcView = getQuickBooksVendorCdcView as jest.Mock;
const mockedInitiateQuickBooksAuth = initiateQuickBooksAuth as jest.Mock;
const mockedStartQuickBooksBillsSync = startQuickBooksBillsSync as jest.Mock;
const mockedStartQuickBooksChartOfAccountsSync = startQuickBooksChartOfAccountsSync as jest.Mock;
const mockedStartQuickBooksVendorsSync = startQuickBooksVendorsSync as jest.Mock;
const mockedGetQuickBooksBillLineItems = getQuickBooksBillLineItems as jest.Mock;
const mockedGetQuickBooksBillDocumentInfo = getQuickBooksBillDocumentInfo as jest.Mock;
const mockedDownloadQuickBooksBillDocument = downloadQuickBooksBillDocument as jest.Mock;
const mockedDownloadQuickBooksInvoiceDocument = downloadQuickBooksInvoiceDocument as jest.Mock;
const mockedGetQuickBooksCustomerCdcView = getQuickBooksCustomerCdcView as jest.Mock;
const mockedStartQuickBooksCustomersSync = startQuickBooksCustomersSync as jest.Mock;
const mockedGetQuickBooksServiceRoleCdcView = getQuickBooksServiceRoleCdcView as jest.Mock;
const mockedGetQuickBooksServiceCoaAccounts = getQuickBooksServiceCoaAccounts as jest.Mock;
const mockedGetQuickBooksServiceIncomeAccounts = getQuickBooksServiceIncomeAccounts as jest.Mock;
const mockedStartQuickBooksServiceRolesSync = startQuickBooksServiceRolesSync as jest.Mock;
const mockedAssignQuickBooksServiceIncomeAccount = assignQuickBooksServiceIncomeAccount as jest.Mock;
const mockedGetQuickBooksProjectSyncConfig = getQuickBooksProjectSyncConfig as jest.Mock;
const mockedSaveQuickBooksProjectSyncConfig = saveQuickBooksProjectSyncConfig as jest.Mock;
const mockedGetQuickBooksProjectCdcView = getQuickBooksProjectCdcView as jest.Mock;
const mockedStartQuickBooksProjectsSync = startQuickBooksProjectsSync as jest.Mock;
const mockedGetQuickBooksEmployeeCdcView = getQuickBooksEmployeeCdcView as jest.Mock;
const mockedStartQuickBooksEmployeesSync = startQuickBooksEmployeesSync as jest.Mock;
const mockedGetQuickBooksEmployeeSyncConfig = getQuickBooksEmployeeSyncConfig as jest.Mock;
const mockedSaveQuickBooksEmployeeSyncConfig = saveQuickBooksEmployeeSyncConfig as jest.Mock;
const mockedGetQuickBooksBillPaymentCdcView = getQuickBooksBillPaymentCdcView as jest.Mock;
const mockedGetQuickBooksBillPaymentAccounts = getQuickBooksBillPaymentAccounts as jest.Mock;
const mockedAssignQuickBooksBillPaymentAccount = assignQuickBooksBillPaymentAccount as jest.Mock;
const mockedStartQuickBooksBillPaymentsSync = startQuickBooksBillPaymentsSync as jest.Mock;
const mockedGetQuickBooksTimelogCdcView = getQuickBooksTimelogCdcView as jest.Mock;
const mockedStartQuickBooksTimelogsSync = startQuickBooksTimelogsSync as jest.Mock;

const accessToken = "test-access-token";
const tenantId = "tn24047750";
const serviceId = 6;
const objectId = "object-123";

// ── Service-role fixtures ─────────────────────────────────────────────────────

const emptyCdcPagination = { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 };

const defaultCustomerResponse = {
  data: {
    summary: {
      totalCustomers: 0, mappedCustomers: 0, unmappedCustomers: 0,
      syncedCustomers: 0, pendingPushCustomers: 0, failedCustomers: 0,
      importFailedCustomers: 0, syncFailedCustomers: 0,
    },
    pagination: emptyCdcPagination,
    records: [],
  },
};

const emptySrResponse = {
  data: {
    records: [],
    pagination: emptyCdcPagination,
    summary: { total: 0, synced: 0, pending: 0, failed: 0 },
  },
};

const defaultCoaAccountsResponse = {
  data: {
    records: [
      {
        id: 10,
        name: "Services Income",
        accountType: "Income",
        detailType: "ServiceFeeIncome",
        externalAccountId: "qbo-ext-10",
        syncStatus: "SYNCED",
      },
    ],
  },
};

/** A service-role row with NO COA assigned (additionalInfo is null). */
const srRowNoCoa = {
  id: 101,
  roleId: 101,
  roleName: "Roofing Foreman",
  syncStatus: "PENDING_PUSH",
  externalServiceId: null,
  additionalInfo: null,
};

/**
 * A service-role row whose COA is fully configured.
 * normalizeServiceCoaAccount requires both `id` and `externalAccountId` to be present.
 */
const srRowWithCoa = {
  id: 201,
  roleId: 201,
  roleName: "Estimator",
  syncStatus: "PENDING_PUSH",
  externalServiceId: null,
  additionalInfo: {
    qbo_service_income_account: {
      id: 10,
      name: "Services Income",
      externalAccountId: "qbo-ext-10",
    },
  },
};

function makeSrResponse(rows: object[]) {
  return {
    data: {
      records: rows,
      pagination: { page: 1, pageSize: 10, totalRecords: rows.length, totalPages: rows.length > 0 ? 1 : 0 },
      summary: { total: rows.length, synced: 0, pending: rows.length, failed: 0 },
    },
  };
}

const defaultVendorResponse = {
  data: {
    summary: {
      totalVendors: 2,
      mappedVendors: 1,
      unmappedVendors: 1,
      syncedVendors: 1,
      pendingPushVendors: 1,
      failedVendors: 0,
      importFailedVendors: 0,
    },
    pagination: { page: 1, pageSize: 10, totalRecords: 2, totalPages: 1 },
    rows: [
      {
        vendorId: 101,
        displayName: "Acme Supplies",
        companyName: "Acme Inc",
        email: "ap@acme.test",
        phone: "1234567890",
        externalVendorId: "QBO-V-101",
        syncStatus: "SYNCED",
        mappingId: 201,
        mappingUpdatedAt: "2026-04-08T04:15:00Z",
        linked: true,
      },
      {
        vendorId: 102,
        displayName: "Northwind",
        companyName: "Northwind LLC",
        syncStatus: "PENDING_PUSH",
        mappingId: 202,
        mappingUpdatedAt: "2026-04-08T04:30:00Z",
        linked: false,
      },
    ],
  },
};

const defaultAccountResponse = {
  data: {
    summary: {
      totalAccounts: 1,
      syncedAccounts: 1,
      pendingPushAccounts: 0,
    },
    pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
    rows: [
      {
        accountId: 301,
        name: "Accounts Payable",
        accountType: "Liability",
        detailType: "Accounts Payable",
        accountNo: "2000",
        externalId: "QBO-A-301",
        syncStatus: "SYNCED",
        updatedAt: "2026-04-08T04:00:00Z",
      },
    ],
  },
};

const defaultBillResponse = {
  data: {
    summary: {
      totalBills: 1,
      mappedBills: 1,
      unmappedBills: 0,
      syncedBills: 1,
      pendingPushBills: 0,
      failedBills: 0,
      importFailedBills: 0,
    },
    pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
    rows: [
      {
        receiptId: 401,
        displayName: "Office Rent",
        vendorCompany: "Acme Inc",
        invoiceDate: "2026-04-07",
        amount: 2500,
        externalReceiptId: "QBO-B-401",
        syncStatus: "SYNCED",
        mappingId: 501,
        mappingUpdatedAt: "2026-04-08T03:45:00Z",
        linked: true,
      },
    ],
  },
};

// ── Bill payments fixtures ────────────────────────────────────────────────────

const defaultBillPaymentResponse = {
  data: {
    summary: {
      totalBillPayments: 2,
      mappedBillPayments: 1,
      unmappedBillPayments: 1,
      assignedBillPayments: 1,
      unassignedBillPayments: 1,
      readyToSyncBillPayments: 1,
      syncedBillPayments: 1,
      pendingPushBillPayments: 0,
      failedBillPayments: 0,
      importFailedBillPayments: 0,
    },
    pagination: { page: 1, pageSize: 10, totalRecords: 2, totalPages: 1 },
    rows: [
      {
        paymentExpenseId: 501,
        displayName: "Payment to Acme",
        vendorCompany: "Acme Inc",
        billDisplayName: "BILL-401",
        billName: "Bill",
        amount: 500,
        paymentDate: "2026-04-01",
        externalAccountId: "qbo-acct-1",
        assignedAccountName: "Business Checking",
        assignedAccountType: "Bank",
        assignedEligiblePayType: "Check",
        syncStatus: "SYNCED",
        externalPaymentId: "QBO-P-501",
        mappingUpdatedAt: "2026-04-08T04:00:00Z",
        linked: true,
        source: "FIELDCRAFT",
      },
      {
        paymentExpenseId: 502,
        displayName: "Payment to Northwind",
        vendorCompany: "Northwind LLC",
        billDisplayName: "BILL-402",
        billName: "Bill",
        amount: 250,
        paymentDate: "2026-04-02",
        externalAccountId: null,
        syncStatus: "PENDING_PUSH",
        mappingUpdatedAt: null,
        linked: false,
        source: "FIELDCRAFT",
      },
    ],
  },
};

const emptyBillPaymentResponse = {
  data: {
    summary: {
      totalBillPayments: 0, mappedBillPayments: 0, unmappedBillPayments: 0,
      assignedBillPayments: 0, unassignedBillPayments: 0, readyToSyncBillPayments: 0,
      syncedBillPayments: 0, pendingPushBillPayments: 0, failedBillPayments: 0, importFailedBillPayments: 0,
    },
    pagination: { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 },
    rows: [],
  },
};

const defaultBillPaymentAccountsResponse = {
  data: {
    accounts: [
      { id: 20, name: "Business Checking", account_type: "Bank", detail_type: "Checking", external_id: "qbo-acct-1", eligible_pay_type: "Check" },
    ],
  },
};

const emptyBillPaymentAccountsResponse = { data: { accounts: [] } };

// ── Projects fixtures ──────────────────────────────────────────────────────

const defaultProjectResponse = {
  data: {
    summary: { total: 1, synced: 1, pending: 0, failed: 0 },
    pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
    records: [
      {
        projectId: 601,
        displayName: "Roof Replacement — 12 Elm St",
        orderNo: "ORD-1001",
        mappingId: 701,
        externalProjectId: "QBO-PRJ-601",
        projectSyncTarget: "SUB_CUSTOMER",
        syncStatus: "SYNCED",
        mappingUpdatedAt: "2026-04-08T04:00:00Z",
      },
    ],
  },
};

const emptyProjectResponse = {
  data: {
    summary: { total: 0, synced: 0, pending: 0, failed: 0 },
    pagination: { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 },
    records: [],
  },
};

// ── Employees fixtures ─────────────────────────────────────────────────────

const defaultEmployeeResponse = {
  data: {
    summary: {
      totalEmployees: 1, mappedEmployees: 1, unmappedEmployees: 0,
      syncedEmployees: 1, pendingPushEmployees: 0, failedEmployees: 0, importFailedEmployees: 0,
    },
    pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
    rows: [
      {
        employeeId: 801,
        displayName: "Jamie Rivera",
        email: "jamie.rivera@example.test",
        active: true,
        mappingId: 901,
        externalEmployeeId: "QBO-EMP-801",
        syncStatus: "SYNCED",
        mappingUpdatedAt: "2026-04-08T04:00:00Z",
      },
    ],
  },
};

const emptyEmployeeResponse = {
  data: {
    summary: {
      totalEmployees: 0, mappedEmployees: 0, unmappedEmployees: 0,
      syncedEmployees: 0, pendingPushEmployees: 0, failedEmployees: 0, importFailedEmployees: 0,
    },
    pagination: { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 },
    rows: [],
  },
};

// ── Timelogs fixtures ──────────────────────────────────────────────────────

const defaultTimelogResponse = {
  data: {
    summary: { total: 1, synced: 0, pending: 1, failed: 0, notReady: 0 },
    pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
    records: [
      {
        timelogId: 1001,
        taskName: "Tear-off & haul-away",
        projectId: 601,
        projectName: "Roof Replacement — 12 Elm St",
        employeeId: 801,
        employeeName: "Jamie Rivera",
        roleId: 101,
        roleName: "Roofing Foreman",
        startTime: "2026-04-07T13:00:00Z",
        endTime: "2026-04-07T15:00:00Z",
        durationSeconds: 7200,
        durationMinutes: 120,
        syncStatus: "PENDING_PUSH",
        employeeLinked: true,
        projectLinked: true,
        serviceLinked: true,
      },
    ],
  },
};

const connectedStatusResponse = {
  data: {
    connected: true,
    status: "connected",
    env: "sandbox",
    realmId: "9130357999999999",
    connectedAt: "2026-04-08T03:30:00Z",
    accessExpiresAt: "2026-04-09T03:30:00Z",
    refreshExpiresAt: "2026-05-08T03:30:00Z",
    lastSyncAt: "2026-04-08T04:00:00Z",
    lastVendorSyncAt: "2026-04-08T04:10:00Z",
    lastPaymentSyncAt: "2026-04-08T04:20:00Z",
  },
};

const disconnectedStatusResponse = {
  data: {
    connected: false,
    status: "disconnected",
  },
};

const reconnectRequiredStatusResponse = {
  data: {
    connected: false,
    status: "reauthorization_required",
    reconnectRequired: true,
    authError: {
      code: "refresh_token_expired",
      reason: "Reconnect needed",
      detail: "Your QuickBooks session expired.",
    },
  },
};

let consoleErrorSpy: jest.SpyInstance;

function setupBaseMocks() {
  mockUseOidcAccessToken.mockReturnValue({ accessToken });

  mockedGetTenantDetails.mockResolvedValue({
    status: 200,
    data: { tenantid: tenantId },
  } as any);

  mockedGetSubscriberById.mockResolvedValue({
    data: [{ status: "Active", serviceMaster: serviceId }],
  } as any);

  mockedGetAllServices.mockResolvedValue({
    data: [{ id: serviceId, name: "QuickBooks", status: "Active" }],
  } as any);

  mockedExecutePostDataWithHeaders.mockResolvedValue({
    data: {
      ok: true,
      deleted: 1,
      alreadyMissing: 0,
      failed: 0,
      resetMappings: 1,
    },
  } as any);

  mockedGetQuickBooksStatus.mockResolvedValue(connectedStatusResponse as any);
  mockedGetQuickBooksSyncState.mockResolvedValue({ data: { jobs: {} } } as any);
  mockedGetQuickBooksVendorCdcView.mockResolvedValue(defaultVendorResponse as any);
  mockedGetQuickBooksAccountCdcView.mockResolvedValue(defaultAccountResponse as any);
  mockedGetQuickBooksBillCdcView.mockResolvedValue(defaultBillResponse as any);
  mockedGetQuickBooksSyncJob.mockResolvedValue({ data: { job: { status: "COMPLETED" } } } as any);
  mockedDisconnectQuickBooks.mockResolvedValue({} as any);
  mockedGetQuickBooksBillLineItems.mockResolvedValue({ data: { lineItems: [] } } as any);
  mockedGetQuickBooksBillDocumentInfo.mockResolvedValue({ data: null } as any);
  mockedDownloadQuickBooksBillDocument.mockResolvedValue({ objectUrl: "blob:test-document-url" } as any);
  mockedDownloadQuickBooksInvoiceDocument.mockResolvedValue({ objectUrl: "blob:test-invoice-document-url", mimeType: "application/pdf", fileName: "invoice.pdf" } as any);
  mockedInitiateQuickBooksAuth.mockResolvedValue({
    data: { credentials: { authorizationUrl: "https://qbo.example.com/oauth" } },
  } as any);
  mockedStartQuickBooksVendorsSync.mockResolvedValue({ ok: true, data: { jobId: 901, status: "RUNNING" } } as any);
  mockedStartQuickBooksBillsSync.mockResolvedValue({ ok: true, data: { jobId: 902, status: "RUNNING" } } as any);
  mockedStartQuickBooksChartOfAccountsSync.mockResolvedValue({ ok: true, data: { jobId: 903, status: "RUNNING" } } as any);

  // Default module is "customers" — mock it to return empty so no error state bleeds into tests
  mockedGetQuickBooksCustomerCdcView.mockResolvedValue(defaultCustomerResponse as any);

  // Service-role module defaults (overridden per test as needed)
  mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(emptySrResponse as any);
  mockedGetQuickBooksServiceCoaAccounts.mockResolvedValue(defaultCoaAccountsResponse as any);
  mockedStartQuickBooksServiceRolesSync.mockResolvedValue({ ok: true, data: { jobId: 910, status: "RUNNING" } } as any);
  mockedAssignQuickBooksServiceIncomeAccount.mockResolvedValue({ ok: true } as any);

  // timelogs / project-sync-config — safe defaults so the component never crashes if it
  // calls these during a MODULE_DEPENDENCIES effect even when the module isn't selected.
  mockedGetQuickBooksProjectSyncConfig.mockResolvedValue({ data: { sync_mode: "cdc", configured: false } } as any);
  mockedSaveQuickBooksProjectSyncConfig.mockResolvedValue({ ok: true } as any);
  mockedGetQuickBooksTimelogCdcView.mockResolvedValue({
    data: {
      records: [],
      pagination: emptyCdcPagination,
      summary: { total: 0, synced: 0, pending: 0, failed: 0, notReady: 0 },
    },
  } as any);
  mockedStartQuickBooksTimelogsSync.mockResolvedValue({ ok: true, data: { jobId: 920, status: "RUNNING" } } as any);

  // customer / project / employee / bill-payment sync starters
  mockedStartQuickBooksCustomersSync.mockResolvedValue({ ok: true, data: { jobId: 930, status: "RUNNING" } } as any);
  mockedStartQuickBooksProjectsSync.mockResolvedValue({ ok: true, data: { jobId: 931, status: "RUNNING" } } as any);
  mockedStartQuickBooksEmployeesSync.mockResolvedValue({ ok: true, data: { jobId: 932, status: "RUNNING" } } as any);
  mockedStartQuickBooksBillPaymentsSync.mockResolvedValue({ ok: true, data: { jobId: 933, status: "RUNNING" } } as any);

  // project / employee / bill-payment module CDC views (overridden per test as needed)
  mockedGetQuickBooksProjectCdcView.mockResolvedValue(defaultProjectResponse as any);
  mockedGetQuickBooksEmployeeCdcView.mockResolvedValue(defaultEmployeeResponse as any);
  mockedGetQuickBooksBillPaymentCdcView.mockResolvedValue(defaultBillPaymentResponse as any);
  mockedGetQuickBooksBillPaymentAccounts.mockResolvedValue(defaultBillPaymentAccountsResponse as any);
  mockedAssignQuickBooksBillPaymentAccount.mockResolvedValue({ ok: true } as any);

  // employee sync config
  mockedGetQuickBooksEmployeeSyncConfig.mockResolvedValue({
    ok: true,
    data: { configured: false, days_after_hire: 1 },
  } as any);
  mockedSaveQuickBooksEmployeeSyncConfig.mockResolvedValue({ ok: true } as any);
}

function renderComponent() {
  return render(
    <UserContext.Provider value={{ userSessionDetails: { objectId } } as any}>
      <QuickBooksTable />
    </UserContext.Provider>,
  );
}

async function waitForDashboardReady() {
  await waitFor(() => expect(mockedGetQuickBooksStatus).toHaveBeenCalled());
}

/**
 * Both QuickBooks connections (Accounting + Time) must resolve before the connected
 * workspace renders. The workspace rail's module buttons are the earliest reliable
 * signal, so navigate by clicking the rail button (by its label) and then confirm the
 * module header heading is shown.
 */
async function gotoModule(railLabel: string, headingTitle: string) {
  fireEvent.click(await screen.findByRole("button", { name: railLabel }));
  await screen.findByRole("heading", { name: headingTitle });
}

async function renderConnectedDashboard() {
  renderComponent();
  // The component's initial module is "customers"; navigate to Vendors so downstream
  // tests that depend on vendor data continue to work as expected.
  await gotoModule("Vendors", "Vendors");
}

/** Renders the component and navigates to the Services module. Waits for the sync button to be enabled. */
async function renderConnectedServices() {
  renderComponent();
  await gotoModule("Services", "Services");
  // Wait until the sync button is both present AND enabled (serviceIncomeAccountsLoading must finish)
  await waitFor(() => {
    const btn = screen.getByRole("button", { name: /sync services/i });
    expect(btn).toBeInTheDocument();
    expect(btn).not.toBeDisabled();
  });
}

/** Renders the component and navigates to the Bill Payments module. */
async function renderConnectedBillPayments() {
  renderComponent();
  await gotoModule("Bill Payments", "Bill Payments");
}

/** Renders the component and navigates to the Projects module. */
async function renderConnectedProjects() {
  renderComponent();
  await gotoModule("Projects", "Projects");
}

/** Renders the component and navigates to the Employees module. */
async function renderConnectedEmployees() {
  renderComponent();
  await gotoModule("Employees", "Employees");
}

/** Renders the component and navigates to the Time Activity module. */
async function renderConnectedTimelogs() {
  renderComponent();
  await gotoModule("Time Activity", "Time Activity");
}

/**
 * Selects the "full" sync mode from the split Sync button's caret menu. The mode
 * now lives inside the caret menu ("Everything" = full, "Only what changed" = cdc),
 * so tests open the menu and choose "Everything". Only modules that pass
 * showSyncMode expose the caret (vendors, bills, customers, bill payments, employees).
 * Assumes the target module is already visible.
 */
function selectFullSyncMode() {
  fireEvent.click(screen.getByRole("button", { name: /sync options/i }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /everything/i }));
}

describe("QuickBooksTable", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useRealTimers();
    setupBaseMocks();
    consoleErrorSpy = jest.spyOn(console, "error").mockImplementation((...args: any[]) => {
      const firstArg = String(args[0] ?? "");
      if (firstArg.includes("not wrapped in act")) return;
      if (firstArg.includes("baseline-browser-mapping")) return;
    });
  });

  afterEach(() => {
    jest.useRealTimers();
    consoleErrorSpy.mockRestore();
  });

  it("renders the not connected landing state when QuickBooks is disconnected", async () => {
    mockedGetQuickBooksStatus.mockResolvedValueOnce(disconnectedStatusResponse as any);

    renderComponent();

    expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect quickbooks/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedGetTenantDetails).toHaveBeenCalledWith(accessToken);
      expect(mockedGetSubscriberById).toHaveBeenCalledWith(accessToken, tenantId);
      expect(mockedGetAllServices).toHaveBeenCalledWith(accessToken);
      expect(mockedGetQuickBooksStatus).toHaveBeenCalledWith(accessToken, tenantId, String(serviceId), objectId);
    });
  });

  it("renders reconnect state when status says reauthorization is required", async () => {
    mockedGetQuickBooksStatus.mockResolvedValueOnce(reconnectRequiredStatusResponse as any);

    renderComponent();

    expect(await screen.findByText("Sign in again to keep syncing")).toBeInTheDocument();
    expect(screen.getByText(/nothing has been removed from either system/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in to quickbooks/i })).toBeInTheDocument();
    // The rail reflects the reconnect state rather than "Not connected".
    expect(screen.getByText("Reconnect")).toBeInTheDocument();
  });

  it("renders the connected vendors dashboard by default", async () => {
    await renderConnectedDashboard();

    await waitFor(() => {
      expect(screen.getByText("Acme Supplies")).toBeInTheDocument();
      expect(screen.getByText("QBO-V-101")).toBeInTheDocument();
      // Second vendor (Northwind) has no QuickBooks link yet — its pending state is shown.
      expect(screen.getByText("Northwind")).toBeInTheDocument();
      expect(screen.getByText("Not linked")).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(mockedGetQuickBooksSyncState).toHaveBeenCalledWith(accessToken, tenantId, String(serviceId), objectId);
      expect(mockedGetQuickBooksVendorCdcView).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({
          tenantId,
          serviceId: String(serviceId),
          page: 1,
          pageSize: 10,
          search: "",
          syncStatus: "ALL",
          mappedOnly: true,
        }),
      );
    });
  });

  /*
   * Invoices and Customer Payments were gated off in constants/FeatureFlag.ts while they
   * were built; both are released (commit 83c2872e77), so each must now have a real entry
   * point — a rail button that selects the module and renders its header. Bill Payments is
   * asserted alongside them as the neighbouring settlement module sharing the rail group,
   * proving the group renders all of its members rather than a subset.
   *
   * That the gate can still *remove* a module is covered against stubbed flags in
   * QuickBooksUtils.test.ts, so it stays tested with nothing currently gated.
   */
  it("offers the released Invoices and Customer Payments modules", async () => {
    await renderConnectedDashboard();

    expect(screen.getByRole("button", { name: "Invoices" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Customer Payments" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bill Payments" })).toBeInTheDocument();
  });

  it("selects Invoices from the rail and renders its module header", async () => {
    await renderConnectedDashboard();
    await gotoModule("Invoices", "Invoices");

    expect(screen.getByRole("heading", { name: "Invoices" })).toBeInTheDocument();
  });

  it("selects Customer Payments from the rail and renders its module header", async () => {
    await renderConnectedDashboard();
    await gotoModule("Customer Payments", "Customer Payments");

    expect(screen.getByRole("heading", { name: "Customer Payments" })).toBeInTheDocument();
  });

  /*
   * The master-data lists are mapping-driven, so a record with no external-map row is
   * simply absent. Customers, Vendors and Employees each carry a hint explaining how to
   * bring one in; the transaction modules do not, because their rows follow the records
   * these three own. Asserted here rather than only in the component's own suite so a
   * dropped `titleHint` prop on one module is caught.
   */
  describe("missing-record hint", () => {
    it("offers the hint on Customers, named for that module's record", async () => {
      renderComponent();
      await screen.findByRole("heading", { name: "Customers" });

      expect(
        screen.getByRole("button", { name: "About missing customer records" }),
      ).toBeInTheDocument();
    });

    it("offers the hint on Vendors", async () => {
      await renderConnectedDashboard();

      expect(
        screen.getByRole("button", { name: "About missing vendor records" }),
      ).toBeInTheDocument();
    });

    it("offers the hint on Employees", async () => {
      await renderConnectedEmployees();

      expect(
        screen.getByRole("button", { name: "About missing employee records" }),
      ).toBeInTheDocument();
    });

    it("reveals the guidance when the hint is opened", async () => {
      renderComponent();
      await screen.findByRole("heading", { name: "Customers" });

      fireEvent.click(screen.getByRole("button", { name: "About missing customer records" }));

      expect(screen.getByText(/Don’t see the customer here\?/)).toBeInTheDocument();
      expect(
        screen.getByText("Refresh or update the customer, then run the sync again."),
      ).toBeInTheDocument();
    });

    it("does not offer it on a transaction module", async () => {
      await renderConnectedBillPayments();

      expect(screen.queryByRole("button", { name: /About missing/ })).not.toBeInTheDocument();
    });
  });

  it("switches between modules and shows the correct content", async () => {
    await renderConnectedDashboard();

    fireEvent.click(screen.getByRole("button", { name: "Chart of Accounts" }));
    expect(await screen.findByRole("heading", { name: "Chart of Accounts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sync accounts/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Bills" }));
    expect(await screen.findByRole("heading", { name: "Bills" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sync bills/i })).toBeInTheDocument();
  });

  it("starts the OAuth flow when the user clicks Connect QuickBooks", async () => {
    mockedGetQuickBooksStatus.mockResolvedValueOnce(disconnectedStatusResponse as any);

    const popup = {
      closed: false,
      close: jest.fn(),
      focus: jest.fn(),
      location: { href: "" },
    } as any;

    const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

    renderComponent();

    expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();
    const connectButton = screen.getByRole("button", { name: /connect quickbooks/i });

    await waitFor(() => expect(connectButton).toBeEnabled());

    fireEvent.click(connectButton);

    await waitFor(() => {
      expect(mockedInitiateQuickBooksAuth).toHaveBeenCalledWith(accessToken, tenantId, String(serviceId), objectId);
      expect(popup.location.href).toBe("https://qbo.example.com/oauth");
      expect(popup.focus).toHaveBeenCalled();
    });

    openSpy.mockRestore();
  });

  it("refreshes connection status and clears the connecting state when the OAuth popup is closed", async () => {
    mockedGetQuickBooksStatus
      .mockResolvedValueOnce(disconnectedStatusResponse as any)
      .mockResolvedValueOnce(disconnectedStatusResponse as any);

    const popup = {
      closed: false,
      close: jest.fn(),
      focus: jest.fn(),
      location: { href: "" },
    } as any;
    const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

    renderComponent();
    const connectButton = await screen.findByRole("button", { name: /connect quickbooks/i });
    await waitFor(() => expect(connectButton).toBeEnabled());

    jest.useFakeTimers();
    await act(async () => {
      fireEvent.click(connectButton);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(popup.location.href).toBe("https://qbo.example.com/oauth");
    expect(screen.getByText("Opening QuickBooks…")).toBeInTheDocument();

    popup.closed = true;
    await act(async () => {
      jest.advanceTimersByTime(500);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Opening QuickBooks…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect quickbooks/i })).toBeEnabled();

    openSpy.mockRestore();
  });

  it("closes the OAuth popup and shows an error when sign-in times out", async () => {
    mockedGetQuickBooksStatus.mockResolvedValueOnce(disconnectedStatusResponse as any);

    const popup = {
      closed: false,
      close: jest.fn(),
      focus: jest.fn(),
      location: { href: "" },
    } as any;
    const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

    renderComponent();
    const connectButton = await screen.findByRole("button", { name: /connect quickbooks/i });
    await waitFor(() => expect(connectButton).toBeEnabled());

    jest.useFakeTimers();
    await act(async () => {
      fireEvent.click(connectButton);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("Opening QuickBooks…")).toBeInTheDocument();

    await act(async () => {
      jest.advanceTimersByTime(5 * 60 * 1000);
      await Promise.resolve();
    });

    // The timeout closes the popup, clears the connecting state, and does NOT
    // re-fetch status (unlike the popup-closed path, which refreshes).
    expect(popup.close).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Opening QuickBooks…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect quickbooks/i })).toBeEnabled();
    expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);

    openSpy.mockRestore();
  });

  it("handles the frontend OAuth completion message and refreshes connection status", async () => {
    mockedGetQuickBooksStatus
      .mockResolvedValueOnce(disconnectedStatusResponse as any)
      .mockResolvedValue(connectedStatusResponse as any);

    renderComponent();
    expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: window.location.origin,
          data: {
            type: "FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK",
            provider: "accounting",
            result: "success",
          },
        }),
      );
    });

    await waitFor(() => expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("QuickBooks connected successfully.")).toBeInTheDocument();
  });

  it("ignores OAuth completion messages from a different origin", async () => {
    mockedGetQuickBooksStatus.mockResolvedValueOnce(disconnectedStatusResponse as any);

    renderComponent();
    expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();
    expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "https://evil.example.com",
          data: {
            type: "FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK",
            provider: "accounting",
            result: "success",
          },
        }),
      );
      await Promise.resolve();
    });

    expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Connect your QuickBooks account")).toBeInTheDocument();
    expect(screen.queryByText("QuickBooks connected successfully.")).not.toBeInTheDocument();
  });

  it("ignores OAuth completion messages for non-accounting providers", async () => {
    mockedGetQuickBooksStatus.mockResolvedValueOnce(disconnectedStatusResponse as any);

    renderComponent();
    expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();
    expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: window.location.origin,
          data: {
            type: "FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK",
            provider: "time",
            result: "success",
          },
        }),
      );
      await Promise.resolve();
    });

    expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Connect your QuickBooks account")).toBeInTheDocument();
    expect(screen.queryByText("QuickBooks connected successfully.")).not.toBeInTheDocument();
  });

  it("disconnects QuickBooks and returns to the landing page", async () => {
    await renderConnectedDashboard();

    const disconnectButton = await screen.findByRole("button", { name: "Disconnect QuickBooks" });
    fireEvent.click(disconnectButton);

    await waitFor(() => {
      expect(mockedDisconnectQuickBooks).toHaveBeenCalledWith(accessToken, tenantId, String(serviceId), objectId);
    });

    // Disconnecting Accounting drops the workspace and returns to the connection screen.
    expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();
  });


  /**
   * A sync start is only visible in `<module>AnyRunning` once the start POST *and* the
   * follow-up sync-state refresh have both resolved. Until then the workspace leans on
   * `startingSyncModule` (and its `startingSyncRef` mirror) to look busy and to refuse a
   * second start. These two tests hold the start request pending so that in-flight window
   * stays open and can be asserted.
   */
  describe("sync start in flight", () => {
    /** A start-request mock that stays pending until `resolve()` is called. */
    function pendingSyncStart(mock: jest.Mock) {
      let release: (value: unknown) => void = () => {};
      mock.mockImplementation(
        () => new Promise((resolveRequest) => { release = resolveRequest; }),
      );
      return {
        resolve: () => release({ ok: true, data: { jobId: 901, status: "RUNNING" } }),
      };
    }

    it("starts only one sync when the Sync button is clicked twice in quick succession", async () => {
      const pending = pendingSyncStart(mockedStartQuickBooksVendorsSync);
      await renderConnectedDashboard();

      const syncButton = screen.getByRole("button", { name: /sync vendors/i });
      await waitFor(() => expect(syncButton).not.toBeDisabled());

      // Both clicks are dispatched inside ONE act() scope on purpose. `fireEvent` flushes
      // React between calls, which re-renders the button as disabled and means a second
      // `fireEvent.click` never reaches the handler — that only proves the disabled
      // attribute works. Batching keeps the button enabled for the second click, so the
      // handler really does run twice and `startingSyncRef` is what has to refuse it.
      await act(async () => {
        syncButton.click();
        syncButton.click();
      });

      expect(mockedStartQuickBooksVendorsSync).toHaveBeenCalledTimes(1);

      // Still exactly one after the request settles: releasing the slot must not replay it.
      pending.resolve();
      await waitFor(() => expect(mockedGetQuickBooksSyncState).toHaveBeenCalled());
      expect(mockedStartQuickBooksVendorsSync).toHaveBeenCalledTimes(1);
    });

    it("shows the syncing button and a Starting banner before the start request resolves", async () => {
      const pending = pendingSyncStart(mockedStartQuickBooksVendorsSync);
      await renderConnectedDashboard();

      const syncButton = screen.getByRole("button", { name: /sync vendors/i });
      await waitFor(() => expect(syncButton).not.toBeDisabled());

      fireEvent.click(syncButton);

      // No job id or status exists yet — this is the window the fix covers.
      expect(await screen.findByRole("button", { name: "Syncing…" })).toBeDisabled();
      expect(screen.getByText("Syncing vendors")).toBeInTheDocument();
      expect(screen.getByText("Starting")).toBeInTheDocument();

      pending.resolve();
      await waitFor(() => expect(mockedGetQuickBooksSyncState).toHaveBeenCalled());
    });
  });

  it("starts vendor sync with full mode selected", async () => {
    await renderConnectedDashboard();

    selectFullSyncMode();
    fireEvent.click(screen.getByRole("button", { name: /sync vendors/i }));

    await waitFor(() => {
      expect(mockedStartQuickBooksVendorsSync).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({
          tenant_id: tenantId,
          service_id: String(serviceId),
          object_id: objectId,
          sync_mode: "full",
        }),
      );
    });

    expect(await screen.findByText("Vendor sync started successfully.")).toBeInTheDocument();
  });

  it("locks other modules' sync buttons while a sync is already running", async () => {
    // An active vendor job makes anyRunning=true across the whole workspace.
    mockedGetQuickBooksSyncState.mockResolvedValue({
      data: { jobs: { vendors: { activeJobId: 55, activeStatus: "RUNNING" } } },
    } as any);
    mockedGetQuickBooksSyncJob.mockResolvedValue({ data: { job: { status: "RUNNING" } } } as any);

    renderComponent();
    await gotoModule("Bills", "Bills");
    await waitFor(() => expect(mockedGetQuickBooksSyncState).toHaveBeenCalled());

    // The running vendor sync surfaces its banner on every module and disables other syncs,
    // and the handler guard blocks a start even if the button is triggered programmatically.
    expect(await screen.findByText("Syncing vendors")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /sync bills/i })).toBeDisabled());

    // Hovering the blocked button explains why it's disabled.
    fireEvent.mouseOver(screen.getByRole("button", { name: /sync bills/i }));
    expect(await screen.findByText(/a quickbooks sync is already running/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /sync bills/i }));
    expect(mockedStartQuickBooksBillsSync).not.toHaveBeenCalled();
  });

  it("starts chart of accounts sync from the accounts module", async () => {
    await renderConnectedDashboard();

    fireEvent.click(screen.getByRole("button", { name: "Chart of Accounts" }));
    fireEvent.click(await screen.findByRole("button", { name: /sync accounts/i }));

    await waitFor(() => {
      expect(mockedStartQuickBooksChartOfAccountsSync).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({
          tenant_id: tenantId,
          service_id: String(serviceId),
          object_id: objectId,
        }),
      );
    });

    expect(await screen.findByText("Chart of accounts sync started successfully.")).toBeInTheDocument();
  });

  it("starts bill sync with full mode selected", async () => {
    await renderConnectedDashboard();

    fireEvent.click(screen.getByRole("button", { name: "Bills" }));
    await screen.findByRole("heading", { name: "Bills" });
    selectFullSyncMode();
    fireEvent.click(await screen.findByRole("button", { name: /sync bills/i }));

    await waitFor(() => {
      expect(mockedStartQuickBooksBillsSync).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({
          tenant_id: tenantId,
          service_id: String(serviceId),
          object_id: objectId,
          sync_mode: "full",
        }),
      );
    });

    expect(await screen.findByText("Bill sync started successfully.")).toBeInTheDocument();
  });

  it("filters vendor records when the Pending filter pill is clicked", async () => {
    await renderConnectedDashboard();

    const initialCalls = mockedGetQuickBooksVendorCdcView.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /pending/i }));

    await waitFor(() => {
      expect(mockedGetQuickBooksVendorCdcView.mock.calls.length).toBeGreaterThan(initialCalls);
      expect(mockedGetQuickBooksVendorCdcView).toHaveBeenLastCalledWith(
        accessToken,
        expect.objectContaining({ syncStatus: "PENDING_PUSH" }),
      );
    });
  });

  it("applies vendor search after debounce", async () => {
    // Navigate to vendors with real timers so async init completes normally
    renderComponent();
    await gotoModule("Vendors", "Vendors");

    // Switch to fake timers only for the debounce portion
    jest.useFakeTimers();

    const searchInput = screen.getByPlaceholderText(/search by name, company, or email/i);
    fireEvent.change(searchInput, { target: { value: "Northwind" } });

    await act(async () => {
      jest.advanceTimersByTime(400);
    });

    // Use toHaveBeenCalledWith (not Last) — a follow-up empty-search re-fetch can race during waitFor
    await waitFor(() => {
      expect(mockedGetQuickBooksVendorCdcView).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({ search: "Northwind" }),
      );
    });
  });

  it("shows vendor empty state when the API returns no rows", async () => {
    mockedGetQuickBooksVendorCdcView.mockResolvedValue({
      data: {
        summary: {
          totalVendors: 0, mappedVendors: 0, unmappedVendors: 0,
          syncedVendors: 0, pendingPushVendors: 0, failedVendors: 0, importFailedVendors: 0,
        },
        pagination: { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 },
        rows: [],
      },
    } as any);

    renderComponent();
    await gotoModule("Vendors", "Vendors");

    expect(await screen.findByText("No vendor records match your filters.")).toBeInTheDocument();
  });

  it("shows an error alert when vendor data loading fails", async () => {
    mockedGetQuickBooksVendorCdcView.mockRejectedValue(new Error("Unable to load vendor data."));

    renderComponent();
    await gotoModule("Vendors", "Vendors");

    expect(await screen.findByText("Unable to load vendor data.")).toBeInTheDocument();
  });

  it("filters vendor records by status when the filter pills are clicked", async () => {
    await renderConnectedDashboard();

    // "Needs attention" pill → FAILED
    fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
    await waitFor(() => {
      expect(mockedGetQuickBooksVendorCdcView).toHaveBeenLastCalledWith(
        accessToken,
        expect.objectContaining({ syncStatus: "FAILED" }),
      );
    });

    // "Synced" pill → SYNCED
    fireEvent.click(screen.getByRole("button", { name: /synced/i }));
    await waitFor(() => {
      expect(mockedGetQuickBooksVendorCdcView).toHaveBeenLastCalledWith(
        accessToken,
        expect.objectContaining({ syncStatus: "SYNCED" }),
      );
    });

    // "All" pill → ALL
    fireEvent.click(screen.getByRole("button", { name: /^all/i }));
    await waitFor(() => {
      expect(mockedGetQuickBooksVendorCdcView).toHaveBeenLastCalledWith(
        accessToken,
        expect.objectContaining({ syncStatus: "ALL" }),
      );
    });
  });

  it("polls an active account job and shows completion feedback", async () => {
    jest.useFakeTimers();

    mockedGetQuickBooksSyncState.mockResolvedValueOnce({
      data: {
        jobs: {
          accounts: {
            activeJobId: 888,
            activeStatus: "RUNNING",
          },
        },
      },
    } as any);

    mockedGetQuickBooksSyncJob.mockResolvedValueOnce({
      data: { job: { status: "COMPLETED" } },
    } as any);

    renderComponent();
    await waitForDashboardReady();

    await act(async () => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(mockedGetQuickBooksSyncJob).toHaveBeenCalledWith(accessToken, tenantId, 888);
    });

    expect(await screen.findByText("Chart of accounts sync completed.")).toBeInTheDocument();
    expect(mockedGetQuickBooksAccountCdcView).toHaveBeenCalled();
  });

  it("shows error when account data loading fails", async () => {
    mockedGetQuickBooksAccountCdcView.mockRejectedValue(new Error("Unable to load account data."));

    renderComponent();
    await gotoModule("Chart of Accounts", "Chart of Accounts");

    expect(await screen.findByText("Unable to load account data.")).toBeInTheDocument();
  });

  it("shows error when bill data loading fails", async () => {
    mockedGetQuickBooksBillCdcView.mockRejectedValue(new Error("Unable to load bill data."));

    renderComponent();
    await gotoModule("Bills", "Bills");

    expect(await screen.findByText("Unable to load bill data.")).toBeInTheDocument();
  });

  describe("bills project column", () => {
    /** Renders the Bills module with a single bill row built from the default fixture. */
    async function renderBillsWithRow(overrides: Record<string, any> = {}) {
      const billRow = { ...defaultBillResponse.data.rows[0], ...overrides };
      mockedGetQuickBooksBillCdcView.mockResolvedValue({
        data: { ...defaultBillResponse.data, rows: [billRow] },
      } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      await screen.findByText(billRow.displayName);
      return billRow;
    }

    it("labels a stock bill instead of leaving the project cell blank", async () => {
      await renderBillsWithRow({
        source: "FIELDCRAFT",
        isStockBill: true,
        stockReference: "PO-STOCK-001",
        projectId: null,
        projectName: null,
      });

      expect(await screen.findByText("Stock bill")).toBeInTheDocument();
    });

    it("labels a stock debit bill with its own wording", async () => {
      await renderBillsWithRow({
        source: "FIELDCRAFT",
        isStockBill: true,
        isStockDebitBill: true,
        receiptType: "StockDebitBill",
        projectId: null,
        projectName: null,
      });

      expect(await screen.findByText("Stock debit bill")).toBeInTheDocument();
    });

    it("labels a stock credit bill with its own wording", async () => {
      await renderBillsWithRow({
        source: "FIELDCRAFT",
        isStockBill: true,
        isStockCreditBill: true,
        receiptType: "StockCreditBill",
        projectId: null,
        projectName: null,
      });

      expect(await screen.findByText("Stock credit bill")).toBeInTheDocument();
      expect(screen.queryByText("Stock debit bill")).not.toBeInTheDocument();
    });

    it("recognises a stock credit bill from its receipt type alone", async () => {
      // The flag is new, so a row served before the backend change still has to
      // fall back to the receipt type rather than rendering as a vendor bill.
      await renderBillsWithRow({
        source: "FIELDCRAFT",
        isStockBill: false,
        isStockCreditBill: false,
        receiptType: "StockCreditBill",
        stockReference: null,
        projectId: null,
        projectName: null,
      });

      expect(await screen.findByText("Stock credit bill")).toBeInTheDocument();
    });

    it("still shows the project name for a project-linked bill", async () => {
      await renderBillsWithRow({
        source: "FIELDCRAFT",
        projectId: 77,
        projectName: "Roof Replacement — 12 Elm St",
      });

      expect(await screen.findByText("Roof Replacement — 12 Elm St")).toBeInTheDocument();
      expect(screen.queryByText("Stock bill")).not.toBeInTheDocument();
    });

    it("keeps the warning for a non-stock bill that is missing its project link", async () => {
      // A project-less overhead bill is not inventory — it must still be flagged.
      await renderBillsWithRow({
        source: "FIELDCRAFT",
        syncStatus: "SYNCED_INCOMPLETE",
        isStockBill: false,
        stockReference: null,
        projectId: null,
        projectName: null,
      });

      expect(await screen.findByText("No project linked")).toBeInTheDocument();
      expect(screen.queryByText("Stock bill")).not.toBeInTheDocument();
    });

    it("annotates a paid bill when its bill payment still needs to be synced", async () => {
      await renderBillsWithRow({
        isPaid: true,
        billPaymentCount: 1,
        syncedBillPaymentCount: 0,
        billPaymentSyncRequired: true,
      });

      expect(await screen.findByText("Sync bill payment")).toBeInTheDocument();
    });

    it("does not show the payment-sync annotation when the bill payment is already synced", async () => {
      await renderBillsWithRow({
        isPaid: true,
        billPaymentCount: 1,
        syncedBillPaymentCount: 1,
        billPaymentSyncRequired: false,
      });

      expect(screen.queryByText("Sync bill payment")).not.toBeInTheDocument();
    });
  });

  describe("bill detail dialog", () => {
    async function navigateToBillsAndClickRow(overrides: Record<string, any> = {}) {
      const billRow = { ...defaultBillResponse.data.rows[0], ...overrides };
      mockedGetQuickBooksBillCdcView.mockResolvedValue({
        data: { ...defaultBillResponse.data, rows: [billRow] },
      } as any);

      await renderConnectedDashboard();
      fireEvent.click(screen.getByRole("button", { name: "Bills" }));
      await screen.findByRole("heading", { name: "Bills" });
      // Wait for the row to appear in the table before clicking (CDC fetch completes async)
      fireEvent.click(await screen.findByText(billRow.displayName));
    }

    it("opens the detail dialog and fires both parallel API calls on row click", async () => {
      await navigateToBillsAndClickRow();

      await waitFor(() => {
        expect(mockedGetQuickBooksBillLineItems).toHaveBeenCalledWith(
          accessToken,
          401,
          expect.objectContaining({ tenantId }),
        );
        expect(mockedGetQuickBooksBillDocumentInfo).toHaveBeenCalledWith(
          accessToken,
          401,
          expect.objectContaining({ tenantId }),
        );
      });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("shows error toast when an unexpected error occurs during handleBillRowClick", async () => {
      mockedGetQuickBooksBillLineItems.mockImplementationOnce(() => {
        throw new Error("Unexpected sync error");
      });

      await navigateToBillsAndClickRow();

      expect(
        await screen.findByText("Could not load bill details. Please try again."),
      ).toBeInTheDocument();
    });

    it("calls URL.revokeObjectURL when the dialog is closed with an active object URL", async () => {
      // jsdom does not define URL.revokeObjectURL — define it so spyOn can instrument it
      if (!URL.revokeObjectURL) URL.revokeObjectURL = jest.fn();
      const revokeObjectURLSpy = jest.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

      mockedGetQuickBooksBillDocumentInfo.mockResolvedValueOnce({
        data: { hasDocument: true, mimeType: "application/pdf", size: 10240 },
      } as any);

      await navigateToBillsAndClickRow();

      fireEvent.click(await screen.findByText("View receipt"));
      await waitFor(() => expect(mockedDownloadQuickBooksBillDocument).toHaveBeenCalled());
      // "Hide receipt" appears only after setBillDocumentPreviewOpen(true) AND
      // setBillDocumentObjectUrl(...) complete — ensures the handler closure has the URL
      await screen.findByText("Hide receipt");

      fireEvent.click(screen.getByRole("button", { name: /close/i }));

      expect(revokeObjectURLSpy).toHaveBeenCalledWith("blob:test-document-url");
      revokeObjectURLSpy.mockRestore();
    });

    it("renders a Paid chip when isPaid is true", async () => {
      await navigateToBillsAndClickRow({ isPaid: true });

      expect(await screen.findByText("Paid")).toBeInTheDocument();
    });

    it("renders an Unpaid chip when isPaid is false", async () => {
      await navigateToBillsAndClickRow({ isPaid: false });

      expect(await screen.findByText("Unpaid")).toBeInTheDocument();
    });

    it("renders category-only line items without section headers", async () => {
      mockedGetQuickBooksBillLineItems.mockResolvedValueOnce({
        data: {
          lineItems: [
            { description: "Office supplies", productCode: null, quantity: null, unitPrice: null, amount: 50 },
          ],
        },
      } as any);

      await navigateToBillsAndClickRow();

      expect(await screen.findByText("Office supplies")).toBeInTheDocument();
      expect(screen.queryByText("Category Details")).not.toBeInTheDocument();
      expect(screen.queryByText("Item Details")).not.toBeInTheDocument();
    });

    it("uses explicit category lineKind even when quantity and unit price are present", async () => {
      mockedGetQuickBooksBillLineItems.mockResolvedValueOnce({
        data: {
          lineItems: [
            {
              description: "Imported category x 1",
              productCode: null,
              quantity: 1,
              unitPrice: 20,
              amount: 20,
              lineKind: "category",
            },
            {
              description: "Imported item",
              productCode: "ITEM-1",
              quantity: 1,
              unitPrice: 10,
              amount: 10,
              lineKind: "item",
            },
          ],
        },
      } as any);

      await navigateToBillsAndClickRow();

      expect(await screen.findByText("Category Details")).toBeInTheDocument();
      expect(screen.getByText("Item Details")).toBeInTheDocument();

      const categoryRow = screen.getByText("Imported category x 1").closest(".qb-fin-table-row");
      expect(categoryRow).not.toBeNull();
      expect(categoryRow?.querySelector(".qb-fin-cell-qty")).toHaveTextContent("—");
      expect(categoryRow?.querySelector(".qb-fin-cell-unit")).toHaveTextContent("—");

      const itemRow = screen.getByText("Imported item").closest(".qb-fin-table-row");
      expect(itemRow).not.toBeNull();
      expect(itemRow?.querySelector(".qb-fin-cell-qty")).toHaveTextContent("1");
      expect(itemRow?.querySelector(".qb-fin-cell-unit")).toHaveTextContent("$10.00");
    });

    it("keeps the existing quantity-based fallback when lineKind is missing", async () => {
      mockedGetQuickBooksBillLineItems.mockResolvedValueOnce({
        data: {
          lineItems: [
            { description: "Existing category", productCode: null, quantity: null, unitPrice: null, amount: 100 },
            { description: "Existing item", productCode: "ITEM-2", quantity: 2, unitPrice: 25, amount: 50 },
          ],
        },
      } as any);

      await navigateToBillsAndClickRow();

      expect(await screen.findByText("Category Details")).toBeInTheDocument();
      expect(screen.getByText("Item Details")).toBeInTheDocument();
    });

    it("renders item-detail-only line items without section headers", async () => {
      mockedGetQuickBooksBillLineItems.mockResolvedValueOnce({
        data: {
          lineItems: [
            { description: "Drill bit set", productCode: "TOOL-001", quantity: 2, unitPrice: 25.0, amount: 50 },
          ],
        },
      } as any);

      await navigateToBillsAndClickRow();

      expect(await screen.findByText("Drill bit set")).toBeInTheDocument();
      expect(screen.queryByText("Category Details")).not.toBeInTheDocument();
      expect(screen.queryByText("Item Details")).not.toBeInTheDocument();
    });

    it("renders Category Details and Item Details headers when both item types are present", async () => {
      mockedGetQuickBooksBillLineItems.mockResolvedValueOnce({
        data: {
          lineItems: [
            { description: "Overhead cost", productCode: null, quantity: null, unitPrice: null, amount: 100 },
            { description: "Lumber 2x4", productCode: "LBR-001", quantity: 10, unitPrice: 5.0, amount: 50 },
          ],
        },
      } as any);

      await navigateToBillsAndClickRow();

      expect(await screen.findByText("Category Details")).toBeInTheDocument();
      expect(screen.getByText("Item Details")).toBeInTheDocument();
      expect(screen.getByText("Overhead cost")).toBeInTheDocument();
      expect(screen.getByText("Lumber 2x4")).toBeInTheDocument();
    });
  });

  it("polls an active vendor job and shows completion feedback", async () => {
    jest.useFakeTimers();

    mockedGetQuickBooksSyncState.mockResolvedValueOnce({
      data: {
        jobs: {
          vendors: {
            activeJobId: 777,
            activeStatus: "RUNNING",
          },
        },
      },
    } as any);

    mockedGetQuickBooksSyncJob.mockResolvedValueOnce({
      data: { job: { status: "COMPLETED" } },
    } as any);

    renderComponent();
    await waitForDashboardReady();

    await act(async () => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(mockedGetQuickBooksSyncJob).toHaveBeenCalledWith(accessToken, tenantId, 777);
    });

    expect(await screen.findByText("Vendor sync completed.")).toBeInTheDocument();
    expect(mockedGetQuickBooksVendorCdcView).toHaveBeenCalled();
  });

  // ── Services module ─────────────────────────────────────────────────────────

  describe("fetchServiceRoleCdcView", () => {
    it("calls the API and renders rows on success", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(makeSrResponse([srRowNoCoa]) as any);

      await renderConnectedServices();

      await waitFor(() => expect(screen.getByText("Roofing Foreman")).toBeInTheDocument());
      expect(mockedGetQuickBooksServiceRoleCdcView).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({ tenantId, serviceId: String(serviceId) }),
      );
    });

    it("accepts rows returned under the `rows` key instead of `records`", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue({
        data: {
          rows: [srRowNoCoa],
          pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
          summary: { total: 1, synced: 0, pending: 1, failed: 0 },
        },
      } as any);

      await renderConnectedServices();

      await waitFor(() => expect(screen.getByText("Roofing Foreman")).toBeInTheDocument());
    });

    it("normalises summary fields that use alternative backend names (totalRoles / pendingPush)", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue({
        data: {
          records: [],
          pagination: { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 },
          summary: { totalRoles: 5, syncedRoles: 2, pendingPush: 2, syncFailed: 1 },
        },
      } as any);

      await renderConnectedServices();

      // Alternative backend summary names normalise into the filter-pill counts.
      await waitFor(() => {
        const filterGroup = screen.getByRole("group", { name: /filter by status/i });
        expect(within(filterGroup).getByText("5")).toBeInTheDocument(); // totalRoles → All
        expect(within(filterGroup).getByText("1")).toBeInTheDocument(); // syncFailed → Needs attention
      });
    });

    it("shows an error message when the API throws", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockRejectedValue(
        Object.assign(new Error("network error"), {
          response: { data: { detail: "Service role data unavailable" } },
        }),
      );

      await renderConnectedServices();

      await waitFor(() =>
        expect(screen.getByText("Service role data unavailable")).toBeInTheDocument(),
      );
    });
  });

  describe("handleServiceRoleSync", () => {
    it("calls startQuickBooksServiceRolesSync and shows a success toast", async () => {
      await renderConnectedServices();

      fireEvent.click(screen.getByRole("button", { name: /sync services/i }));

      await waitFor(() =>
        expect(mockedStartQuickBooksServiceRolesSync).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({ tenant_id: tenantId, service_id: String(serviceId) }),
        ),
      );
      await waitFor(() =>
        expect(screen.getByText("Service role export started successfully.")).toBeInTheDocument(),
      );
    });

    it("does not start a services sync while another module's job is running", async () => {
      // An active vendor job makes anyRunning=true; handleServiceRoleSync returns early,
      // so clicking Sync services must not call the start-sync API.
      mockedGetQuickBooksSyncState.mockResolvedValue({
        data: { jobs: { vendors: { activeJobId: 55, activeStatus: "RUNNING" } } },
      } as any);
      // Keep the polling job in RUNNING state so anyRunning persists
      mockedGetQuickBooksSyncJob.mockResolvedValue({ data: { job: { status: "RUNNING" } } } as any);

      renderComponent();
      await gotoModule("Services", "Services");

      // Let the sync-state poll register the running vendor job.
      await waitFor(() => expect(mockedGetQuickBooksSyncState).toHaveBeenCalled());

      fireEvent.click(screen.getByRole("button", { name: /sync services/i }));
      expect(mockedStartQuickBooksServiceRolesSync).not.toHaveBeenCalled();
    });

    it("shows an error toast when the sync API call fails", async () => {
      mockedStartQuickBooksServiceRolesSync.mockRejectedValue(
        Object.assign(new Error("quota exceeded"), {
          response: { data: { detail: "QuickBooks quota exceeded" } },
        }),
      );

      await renderConnectedServices();

      fireEvent.click(screen.getByRole("button", { name: /sync services/i }));

      await waitFor(() =>
        expect(screen.getByText("QuickBooks quota exceeded")).toBeInTheDocument(),
      );
    });

    it("shows a capability error when the backend returns a 403 feature-blocked response", async () => {
      mockedStartQuickBooksServiceRolesSync.mockResolvedValue({
        ok: false,
        status: 403,
        message: "This feature is not available on your current QuickBooks plan.",
      } as any);

      await renderConnectedServices();

      fireEvent.click(screen.getByRole("button", { name: /sync services/i }));

      await waitFor(() =>
        expect(
          screen.getByText(/This feature is not available on your current QuickBooks plan\./i),
        ).toBeInTheDocument(),
      );
    });
  });

  describe("handleBulkAssignServiceRoleCoa", () => {
    /** Navigate to services with one row, select it, open the COA assign dialog. */
    async function openCoaDialog() {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([srRowNoCoa]) as any,
      );

      await renderConnectedServices();
      await waitFor(() => expect(screen.getByText("Roofing Foreman")).toBeInTheDocument());

      // Select the row via its checkbox (row checkboxes sit outside <thead>)
      const checkboxes = screen.getAllByRole("checkbox");
      const rowCheckbox =
        checkboxes.find((cb) => !cb.closest("thead")) ?? checkboxes[checkboxes.length - 1];
      fireEvent.click(rowCheckbox);

      // Toolbar "Assign COA (1)" button appears once a row is selected
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /assign coa/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole("button", { name: /assign coa \(1\)/i }));

      await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    }

    it("calls assignQuickBooksServiceIncomeAccount, shows a success toast, and refreshes the table", async () => {
      await openCoaDialog();

      // Open the COA Autocomplete and pick the option
      const combobox = within(screen.getByRole("dialog")).getByRole("combobox");
      fireEvent.mouseDown(combobox);
      await waitFor(() => expect(screen.getByText("Services Income")).toBeInTheDocument());
      fireEvent.click(screen.getByText("Services Income"));

      // Confirm in the dialog — the confirm button text is "Assign COA" (no count)
      fireEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: /^assign coa$/i }),
      );

      await waitFor(() =>
        expect(mockedAssignQuickBooksServiceIncomeAccount).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({
            tenant_id: tenantId,
            service_id: String(serviceId),
            chart_of_account_id: 10,
            role_ids: [101],
          }),
        ),
      );
      await waitFor(() =>
        expect(screen.getByText(/COA assigned to 1 role/i)).toBeInTheDocument(),
      );
      // Table refresh: getQuickBooksServiceRoleCdcView must have been called at least twice
      // (once on module load, once after assignment)
      expect(mockedGetQuickBooksServiceRoleCdcView.mock.calls.length).toBeGreaterThanOrEqual(2);
    });

    it("shows an error toast when the assignment API returns ok=false", async () => {
      mockedAssignQuickBooksServiceIncomeAccount.mockResolvedValue({
        ok: false,
        message: "Assignment rejected by server",
      } as any);

      await openCoaDialog();

      const combobox = within(screen.getByRole("dialog")).getByRole("combobox");
      fireEvent.mouseDown(combobox);
      await waitFor(() => expect(screen.getByText("Services Income")).toBeInTheDocument());
      fireEvent.click(screen.getByText("Services Income"));

      fireEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: /^assign coa$/i }),
      );

      await waitFor(() =>
        expect(screen.getByText(/Assignment rejected by server/i)).toBeInTheDocument(),
      );
    });
  });

  describe("hasAssignedServiceRoleCoa guard", () => {
    it("flags a role with no COA assigned via a per-row chip", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([srRowNoCoa]) as any,
      );

      await renderConnectedServices();

      await waitFor(() => expect(screen.getByText("Roofing Foreman")).toBeInTheDocument());
      expect(screen.getByText("No COA assigned")).toBeInTheDocument();
    });

    it("shows the assigned COA account for a role that has one configured", async () => {
      // Mix: one with COA, one without — the configured role shows its account name.
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([srRowWithCoa, srRowNoCoa]) as any,
      );

      await renderConnectedServices();

      await waitFor(() => expect(screen.getByText("Estimator")).toBeInTheDocument());
      expect(screen.getByText("Services Income")).toBeInTheDocument();
    });

    it("disables services sync when no chart-of-accounts income accounts are available", async () => {
      mockedGetQuickBooksServiceCoaAccounts.mockResolvedValue({
        data: { records: [] },
      } as any);
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([srRowNoCoa]) as any,
      );

      // No COA accounts → sync stays disabled, so we can't use renderConnectedServices
      // (which waits for the button to become enabled).
      renderComponent();
      await gotoModule("Services", "Services");

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sync services/i })).toBeDisabled(),
      );
    });

    it("treats an additionalInfo COA object missing id/externalAccountId as unassigned", async () => {
      // An empty {} inside qbo_service_income_account must NOT satisfy isServiceCoaConfigured
      const rowWithEmptyCoa = {
        ...srRowNoCoa,
        roleId: 301,
        roleName: "Painter",
        additionalInfo: { qbo_service_income_account: {} },
      };
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([rowWithEmptyCoa]) as any,
      );

      await renderConnectedServices();

      await waitFor(() => expect(screen.getByText("Painter")).toBeInTheDocument());
      expect(screen.getByText("No COA assigned")).toBeInTheDocument();
    });
  });

  // ── Customers module ────────────────────────────────────────────────────────

  describe("customers module", () => {
    it("renders customer records in the default landing module", async () => {
      mockedGetQuickBooksCustomerCdcView.mockResolvedValue(defaultCustomerResponse as any);
      renderComponent();

      expect(await screen.findByRole("heading", { name: "Customers" })).toBeInTheDocument();
    });

    it("starts customer sync with full mode selected", async () => {
      renderComponent();
      await screen.findByRole("heading", { name: "Customers" });

      selectFullSyncMode();
      fireEvent.click(screen.getByRole("button", { name: /sync customers/i }));

      await waitFor(() => {
        expect(mockedStartQuickBooksCustomersSync).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({
            tenant_id: tenantId,
            service_id: String(serviceId),
            object_id: objectId,
            sync_mode: "full",
          }),
        );
      });

      expect(await screen.findByText("Customer sync started successfully.")).toBeInTheDocument();
    });

    it("shows customer empty state when the API returns no rows", async () => {
      mockedGetQuickBooksCustomerCdcView.mockResolvedValue({
        data: {
          summary: {
            totalCustomers: 0, mappedCustomers: 0, unmappedCustomers: 0,
            syncedCustomers: 0, pendingPushCustomers: 0, failedCustomers: 0,
            importFailedCustomers: 0, syncFailedCustomers: 0,
          },
          pagination: { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 },
          rows: [],
        },
      } as any);

      renderComponent();
      await screen.findByRole("heading", { name: "Customers" });

      expect(await screen.findByText("No customer records match your filters.")).toBeInTheDocument();
    });

    it("shows an error alert when customer data loading fails", async () => {
      mockedGetQuickBooksCustomerCdcView.mockRejectedValue(new Error("Unable to load customer data."));

      renderComponent();
      await screen.findByRole("heading", { name: "Customers" });

      expect(await screen.findByText("Unable to load customer data.")).toBeInTheDocument();
    });

    it("passes the NEEDS_ATTENTION sentinel (not FAILED) when the Needs attention pill is clicked", async () => {
      mockedGetQuickBooksCustomerCdcView.mockResolvedValue(defaultCustomerResponse as any);
      renderComponent();
      await screen.findByRole("heading", { name: "Customers" });

      // "Needs attention" aggregates failed + import-failed + sync-failed, so the pill must
      // send the NEEDS_ATTENTION sentinel — the backend expands it to that set. Sending a bare
      // "FAILED" (the old behavior) returned nothing when the flagged rows were import/sync-failed.
      fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
      await waitFor(() => {
        expect(mockedGetQuickBooksCustomerCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "NEEDS_ATTENTION" }),
        );
      });

      // Concrete-status pills still pass their exact status through unchanged.
      fireEvent.click(screen.getByRole("button", { name: /synced/i }));
      await waitFor(() => {
        expect(mockedGetQuickBooksCustomerCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "SYNCED" }),
        );
      });
    });
  });

  // ── Bill Payments module ─────────────────────────────────────────────────────

  describe("bill payments module", () => {
    it("renders bill payment records with their vendor and linked bill", async () => {
      await renderConnectedBillPayments();

      await waitFor(() => {
        expect(screen.getByText("Payment to Acme")).toBeInTheDocument();
        expect(screen.getByText("Acme Inc")).toBeInTheDocument();
        expect(screen.getByText("BILL-401")).toBeInTheDocument();
        expect(screen.getByText("Payment to Northwind")).toBeInTheDocument();
        expect(screen.getByText("BILL-402")).toBeInTheDocument();
        expect(screen.getByText("QBO-P-501")).toBeInTheDocument();
      });
    });

    it("disables bill-payment sync when no eligible payment accounts exist", async () => {
      mockedGetQuickBooksBillPaymentAccounts.mockResolvedValue(emptyBillPaymentAccountsResponse as any);

      await renderConnectedBillPayments();

      // Without an eligible payment account, bill-payment sync stays disabled.
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sync bill payments/i })).toBeDisabled(),
      );
    });

    it("shows the bill payment empty state when there are no records", async () => {
      mockedGetQuickBooksBillPaymentCdcView.mockResolvedValue(emptyBillPaymentResponse as any);

      await renderConnectedBillPayments();

      expect(await screen.findByText("No bill payments match your filters.")).toBeInTheDocument();
    });

    it("shows an error alert when bill payment data loading fails", async () => {
      mockedGetQuickBooksBillPaymentCdcView.mockRejectedValue(new Error("Unable to load bill payment data."));

      await renderConnectedBillPayments();

      expect(await screen.findByText("Unable to load bill payment data.")).toBeInTheDocument();
    });

    it("filters bill payments by status when the filter pills are clicked", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Acme");

      fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
      await waitFor(() => {
        expect(mockedGetQuickBooksBillPaymentCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "FAILED" }),
        );
      });

      fireEvent.click(screen.getByRole("button", { name: /synced/i }));
      await waitFor(() => {
        expect(mockedGetQuickBooksBillPaymentCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "SYNCED" }),
        );
      });

      fireEvent.click(screen.getByRole("button", { name: /^all/i }));
      await waitFor(() => {
        expect(mockedGetQuickBooksBillPaymentCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "ALL" }),
        );
      });
    });

    it("starts bill payment sync with full mode selected", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Acme");

      selectFullSyncMode();
      fireEvent.click(screen.getByRole("button", { name: /sync bill payments/i }));

      await waitFor(() => {
        expect(mockedStartQuickBooksBillPaymentsSync).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({
            tenant_id: tenantId,
            service_id: String(serviceId),
            object_id: objectId,
            sync_mode: "full",
          }),
        );
      });

      expect(await screen.findByText("Bill payment sync started successfully.")).toBeInTheDocument();
    });

    it("selects an unassigned row, assigns a QBO payment account via the dialog, and refreshes the table", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Northwind");

      // Select the unassigned row's checkbox (row checkboxes live outside <thead>)
      const checkboxes = screen.getAllByRole("checkbox");
      const rowCheckbox = checkboxes.find((cb) => !cb.closest("thead"));
      fireEvent.click(rowCheckbox as HTMLElement);

      fireEvent.click(await screen.findByRole("button", { name: /assign account \(1\)/i }));
      const dialog = await screen.findByRole("dialog");

      const selectTrigger = document.getElementById("quick-books-table-select-quickbooks-payment-account");
      fireEvent.mouseDown(selectTrigger as HTMLElement);
      await waitFor(() => expect(screen.getByText("Business Checking")).toBeInTheDocument());
      fireEvent.click(screen.getByText("Business Checking"));

      fireEvent.click(within(dialog).getByRole("button", { name: /^assign account$/i }));

      await waitFor(() => {
        expect(mockedAssignQuickBooksBillPaymentAccount).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({
            tenant_id: tenantId,
            service_id: String(serviceId),
            qbo_external_account_id: "qbo-acct-1",
          }),
        );
      });

      expect(await screen.findByText(/Assigned QuickBooks payment account to 1 bill payment/i)).toBeInTheDocument();
      expect(mockedGetQuickBooksBillPaymentCdcView.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  // ── Projects module ──────────────────────────────────────────────────────────

  describe("projects module", () => {
    it("renders project records", async () => {
      await renderConnectedProjects();

      await waitFor(() => {
        expect(screen.getByText("Roof Replacement — 12 Elm St")).toBeInTheDocument();
        expect(screen.getByText("QBO-PRJ-601")).toBeInTheDocument();
      });
    });

    it("shows the project empty state when there are no records", async () => {
      mockedGetQuickBooksProjectCdcView.mockResolvedValue(emptyProjectResponse as any);

      await renderConnectedProjects();

      expect(await screen.findByText("No projects match your filters.")).toBeInTheDocument();
    });

    it("shows an error alert when project data loading fails", async () => {
      mockedGetQuickBooksProjectCdcView.mockRejectedValue(new Error("Unable to load project data."));

      await renderConnectedProjects();

      expect(await screen.findByText("Unable to load project data.")).toBeInTheDocument();
    });

    it("starts a project export", async () => {
      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");

      fireEvent.click(screen.getByRole("button", { name: /sync projects/i }));

      await waitFor(() => {
        expect(mockedStartQuickBooksProjectsSync).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({ tenant_id: tenantId, service_id: String(serviceId), object_id: objectId }),
        );
      });

      expect(await screen.findByText("Project export started successfully.")).toBeInTheDocument();
    });

    it("shows a plan-upgrade warning when project sync is blocked by the QuickBooks plan", async () => {
      mockedStartQuickBooksProjectsSync.mockResolvedValue({
        ok: false,
        status: 403,
        message: "Projects are not available on your current QuickBooks plan.",
      } as any);

      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");

      fireEvent.click(screen.getByRole("button", { name: /sync projects/i }));

      await waitFor(() =>
        expect(
          screen.getByText(/Projects are not available on your current QuickBooks plan\./i),
        ).toBeInTheDocument(),
      );
      expect(screen.getByText("QuickBooks plan upgrade required")).toBeInTheDocument();
    });

    it("opens the project sync method dialog, selects QuickBooks Project, and saves", async () => {
      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");

      // The project sync-method chooser button is labelled with the current method ("Auto").
      fireEvent.click(screen.getByRole("button", { name: /auto/i }));
      const dialog = await screen.findByRole("dialog");
      const qboProjectOption = await within(dialog).findByText("QuickBooks Project");

      fireEvent.click(qboProjectOption);
      fireEvent.click(within(dialog).getByRole("button", { name: /save settings/i }));

      await waitFor(() => {
        expect(mockedSaveQuickBooksProjectSyncConfig).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({
            tenant_id: tenantId,
            service_id: String(serviceId),
            method: "QBO_PROJECT",
          }),
        );
      });

      expect(await screen.findByText("Project sync settings saved.")).toBeInTheDocument();
    });
  });

  // ── Employees module ─────────────────────────────────────────────────────────

  describe("employees module", () => {
    it("renders employee records", async () => {
      await renderConnectedEmployees();

      await waitFor(() => {
        expect(screen.getByText("Jamie Rivera")).toBeInTheDocument();
        expect(screen.getByText("QBO-EMP-801")).toBeInTheDocument();
      });
    });

    it("shows the employee empty state when there are no records", async () => {
      mockedGetQuickBooksEmployeeCdcView.mockResolvedValue(emptyEmployeeResponse as any);

      await renderConnectedEmployees();

      expect(await screen.findByText("No employees match your filters.")).toBeInTheDocument();
    });

    it("shows an error alert when employee data loading fails", async () => {
      mockedGetQuickBooksEmployeeCdcView.mockRejectedValue(new Error("Unable to load employee data."));

      await renderConnectedEmployees();

      expect(await screen.findByText("Unable to load employee data.")).toBeInTheDocument();
    });

    it("prompts to configure settings and disables sync until employee sync is configured", async () => {
      await renderConnectedEmployees();

      // A clear setup prompt explains why sync is off (instead of a silently disabled button).
      expect(await screen.findByText("Configure employee sync to get started")).toBeInTheDocument();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sync employees/i })).toBeDisabled(),
      );

      // The "Configure settings" shortcut in the prompt opens the settings dialog.
      fireEvent.click(screen.getByRole("button", { name: /configure settings/i }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Employee Sync Settings")).toBeInTheDocument();
    });

    it("opens the employee sync settings dialog, edits values, and saves", async () => {
      await renderConnectedEmployees();
      const settingsButton = await screen.findByRole("button", { name: /employee sync settings/i });

      fireEvent.click(settingsButton);
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Employee Sync Settings")).toBeInTheDocument();
      await within(dialog).findByText("Work start date");

      const daysField = document.getElementById("qb-field-days-after-hire") as HTMLInputElement;
      fireEvent.change(daysField, { target: { value: "5" } });
      fireEvent.blur(daysField);

      // Worker classification is now derived automatically from QuickBooks (W-2 vs
      // 1099 with tracking), so the dialog shows static guidance, not a picker.
      expect(within(dialog).getByText("Worker classification")).toBeInTheDocument();

      fireEvent.click(within(dialog).getByRole("button", { name: /save settings/i }));

      await waitFor(() => {
        expect(mockedSaveQuickBooksEmployeeSyncConfig).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({
            tenant_id: tenantId,
            service_id: String(serviceId),
            days_after_hire: 5,
            object_id: objectId,
          }),
        );
      });

      expect(await screen.findByText("Employee sync settings saved.")).toBeInTheDocument();
      // Saving marks the config configured, which enables employee sync. Query with
      // hidden:true because the just-closed settings dialog can still leave the
      // background aria-hidden during its close transition.
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sync employees/i, hidden: true })).not.toBeDisabled(),
      );
    });

    it("starts employee sync once configured", async () => {
      mockedGetQuickBooksEmployeeSyncConfig.mockResolvedValue({
        ok: true,
        data: { configured: true, days_after_hire: 2 },
      } as any);

      await renderConnectedEmployees();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sync employees/i })).not.toBeDisabled(),
      );

      fireEvent.click(screen.getByRole("button", { name: /sync employees/i }));

      await waitFor(() => {
        expect(mockedStartQuickBooksEmployeesSync).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({ tenant_id: tenantId, service_id: String(serviceId), object_id: objectId }),
        );
      });

      expect(await screen.findByText("Employee sync started successfully.")).toBeInTheDocument();
    });
  });

  // ── Time Activities (timelogs) module ────────────────────────────────────────

  describe("timelogs module", () => {
    it("renders timelog records", async () => {
      mockedGetQuickBooksTimelogCdcView.mockResolvedValue(defaultTimelogResponse as any);

      await renderConnectedTimelogs();

      await waitFor(() => {
        expect(screen.getByText("Tear-off & haul-away")).toBeInTheDocument();
        expect(screen.getByText("Jamie Rivera")).toBeInTheDocument();
      });
    });

    it("shows the timelog empty state when there are no records", async () => {
      await renderConnectedTimelogs();

      expect(await screen.findByText("No time activity records match your filters.")).toBeInTheDocument();
    });

    it("shows an error alert when timelog data loading fails", async () => {
      mockedGetQuickBooksTimelogCdcView.mockRejectedValue(new Error("Unable to load time activity data."));

      await renderConnectedTimelogs();

      expect(await screen.findByText("Unable to load time activity data.")).toBeInTheDocument();
    });

    it("starts a time activity export", async () => {
      mockedGetQuickBooksTimelogCdcView.mockResolvedValue(defaultTimelogResponse as any);

      await renderConnectedTimelogs();
      await screen.findByText("Tear-off & haul-away");

      fireEvent.click(screen.getByRole("button", { name: /sync time activity/i }));

      await waitFor(() => {
        expect(mockedStartQuickBooksTimelogsSync).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({ tenant_id: tenantId, service_id: String(serviceId), object_id: objectId }),
        );
      });

      expect(await screen.findByText("QuickBooks time activity sync started successfully.")).toBeInTheDocument();
    });

    // The mapped TimeActivity delete action is developer-only and hidden unless the
    // frontend maintenance flag is explicitly enabled.
    it("does not expose the developer delete action when the maintenance flag is off", async () => {
      mockedGetQuickBooksTimelogCdcView.mockResolvedValue({
        data: {
          summary: { total: 1, synced: 1, pending: 0, failed: 0, notReady: 0 },
          pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
          records: [
            {
              ...defaultTimelogResponse.data.records[0],
              syncStatus: "SYNCED",
              externalEntityType: "TIME_ACTIVITY",
              externalEntityId: "12345",
            },
          ],
        },
      } as any);

      await renderConnectedTimelogs();
      await screen.findByText("Tear-off & haul-away");

      expect(screen.queryByRole("button", { name: /delete mapped time/i })).toBeNull();
      expect(screen.queryByText("Delete synced QuickBooks TimeActivity records?")).toBeNull();
      expect(mockedExecutePostDataWithHeaders).not.toHaveBeenCalledWith(
        expect.stringContaining("/extsvc/api/v1/quickbooks/timelogs/delete-all"),
        expect.anything(),
        expect.anything(),
      );
    });

    it("passes the NEEDS_ATTENTION sentinel (not FAILED) when the Needs attention pill is clicked", async () => {
      mockedGetQuickBooksTimelogCdcView.mockResolvedValue(defaultTimelogResponse as any);
      await renderConnectedTimelogs();

      // For time activity, "Needs attention" spans failed + sync-failed + not-ready; the pill must
      // send the NEEDS_ATTENTION sentinel so the backend returns the same rows the count reflects
      // (NOT_READY rows in particular were invisible under the old bare-"FAILED" filter).
      fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
      await waitFor(() => {
        expect(mockedGetQuickBooksTimelogCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "NEEDS_ATTENTION" }),
        );
      });

      // Concrete-status pills still pass their exact status through unchanged.
      fireEvent.click(screen.getByRole("button", { name: /synced/i }));
      await waitFor(() => {
        expect(mockedGetQuickBooksTimelogCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "SYNCED" }),
        );
      });
    });
  });

  // ── Bootstrap: tenant + QuickBooks service resolution ────────────────────────
  //
  // These failures happen before the connection screen exists, so the workspace
  // skeleton stays up and the error alert is the only user-visible signal.

  describe("bootstrap failures", () => {
    it("reports an initialization error when the tenant lookup returns no tenant id", async () => {
      mockedGetTenantDetails.mockResolvedValue({ status: 500, data: undefined } as any);

      renderComponent();

      expect(await screen.findByText("Unable to determine tenantId")).toBeInTheDocument();
      expect(mockedGetQuickBooksStatus).not.toHaveBeenCalled();
    });

    it("reports an error when the tenant has no active subscriptions", async () => {
      mockedGetSubscriberById.mockResolvedValue({
        data: [{ status: "Inactive", serviceMaster: serviceId }],
      } as any);

      renderComponent();

      expect(await screen.findByText("No active services enabled for this tenant")).toBeInTheDocument();
    });

    it("reports an error when QuickBooks is missing from the service master", async () => {
      mockedGetAllServices.mockResolvedValue({
        data: [{ id: 99, name: "EagleView", status: "Active" }],
      } as any);

      renderComponent();

      expect(await screen.findByText("QuickBooks not found in service master")).toBeInTheDocument();
    });

    it("reports an error when QuickBooks is not enabled for the tenant", async () => {
      mockedGetSubscriberById.mockResolvedValue({
        data: [{ status: "Active", serviceMaster: 999 }],
      } as any);

      renderComponent();

      expect(await screen.findByText("QuickBooks service not enabled for this tenant")).toBeInTheDocument();
    });

    it("moves to the reconnect screen when the status call fails with a reconnect payload", async () => {
      mockedGetQuickBooksStatus.mockRejectedValue({
        response: {
          data: {
            error: "refresh_token_expired",
            detail: "QuickBooks needs to be reconnected.",
            authError: { code: "refresh_token_expired", reason: "Reconnect needed" },
          },
        },
      });

      renderComponent();

      expect(await screen.findByText("Sign in again to keep syncing")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /sign in to quickbooks/i })).toBeInTheDocument();
    });

    it("falls back to the connect screen when the status call fails without a reconnect payload", async () => {
      mockedGetQuickBooksStatus.mockRejectedValue(new Error("Status service unavailable"));

      renderComponent();

      expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();
      expect(mockedGetQuickBooksSyncState).not.toHaveBeenCalled();
    });
  });

  // ── OAuth pop-up edge cases ──────────────────────────────────────────────────

  describe("OAuth pop-up edge cases", () => {
    /** A stub QuickBooks OAuth pop-up window. */
    function makePopup() {
      return { closed: false, close: jest.fn(), focus: jest.fn(), location: { href: "" } } as any;
    }

    async function renderDisconnectedAndFindConnect() {
      mockedGetQuickBooksStatus.mockResolvedValue(disconnectedStatusResponse as any);
      const view = renderComponent();
      const connectButton = await screen.findByRole("button", { name: /connect quickbooks/i });
      await waitFor(() => expect(connectButton).toBeEnabled());
      return { view, connectButton };
    }

    it("never asks QuickBooks for an authorization URL when the browser blocks the pop-up", async () => {
      const openSpy = jest.spyOn(window, "open").mockReturnValue(null);

      const { connectButton } = await renderDisconnectedAndFindConnect();
      fireEvent.click(connectButton);

      await waitFor(() => expect(connectButton).toBeEnabled());
      expect(mockedInitiateQuickBooksAuth).not.toHaveBeenCalled();

      openSpy.mockRestore();
    });

    it("closes the pop-up when QuickBooks returns no authorization URL", async () => {
      mockedInitiateQuickBooksAuth.mockResolvedValue({ data: {} } as any);
      const popup = makePopup();
      const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

      const { connectButton } = await renderDisconnectedAndFindConnect();
      fireEvent.click(connectButton);

      await waitFor(() => expect(popup.close).toHaveBeenCalledTimes(1));
      expect(popup.focus).not.toHaveBeenCalled();

      openSpy.mockRestore();
    });

    it("closes the pop-up when the authorization request itself fails", async () => {
      mockedInitiateQuickBooksAuth.mockRejectedValue(new Error("QuickBooks auth unavailable"));
      const popup = makePopup();
      const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

      const { connectButton } = await renderDisconnectedAndFindConnect();
      fireEvent.click(connectButton);

      await waitFor(() => expect(popup.close).toHaveBeenCalledTimes(1));
      // The connecting state is released so the user can retry.
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /connect quickbooks/i })).toBeEnabled(),
      );

      openSpy.mockRestore();
    });

    it("leaves an already-closed pop-up alone when the authorization request fails", async () => {
      mockedInitiateQuickBooksAuth.mockRejectedValue(new Error("QuickBooks auth unavailable"));
      const popup = makePopup();
      popup.closed = true;
      const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

      const { connectButton } = await renderDisconnectedAndFindConnect();
      fireEvent.click(connectButton);

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /connect quickbooks/i })).toBeEnabled(),
      );
      expect(popup.close).not.toHaveBeenCalled();

      openSpy.mockRestore();
    });

    it("refreshes status but shows no success toast for a denied OAuth callback", async () => {
      mockedGetQuickBooksStatus.mockResolvedValue(disconnectedStatusResponse as any);

      renderComponent();
      expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();

      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            origin: window.location.origin,
            data: {
              type: "FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK",
              provider: "accounting",
              result: "denied",
            },
          }),
        );
        await Promise.resolve();
      });

      await waitFor(() => expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(2));
      expect(screen.queryByText("QuickBooks connected successfully.")).not.toBeInTheDocument();
    });

    it("ignores an OAuth callback message that carries no result", async () => {
      mockedGetQuickBooksStatus.mockResolvedValue(disconnectedStatusResponse as any);

      renderComponent();
      expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();
      expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);

      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            origin: window.location.origin,
            data: { type: "FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK", provider: "accounting" },
          }),
        );
        await Promise.resolve();
      });

      expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);
    });

    it("ignores a message that is not a QuickBooks OAuth callback", async () => {
      mockedGetQuickBooksStatus.mockResolvedValue(disconnectedStatusResponse as any);

      renderComponent();
      expect(await screen.findByText("Connect your QuickBooks account")).toBeInTheDocument();

      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            origin: window.location.origin,
            data: { type: "SOMETHING_ELSE", provider: "accounting", result: "success" },
          }),
        );
        await Promise.resolve();
      });

      expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(1);
    });

    it("clears the pop-up watcher when the workspace unmounts mid sign-in", async () => {
      const popup = makePopup();
      const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

      const { view, connectButton } = await renderDisconnectedAndFindConnect();

      jest.useFakeTimers();
      await act(async () => {
        fireEvent.click(connectButton);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(popup.focus).toHaveBeenCalled();

      const callsBeforeUnmount = mockedGetQuickBooksStatus.mock.calls.length;
      view.unmount();

      // A watcher that survived unmount would poll the closed pop-up and refetch status.
      popup.closed = true;
      await act(async () => {
        jest.advanceTimersByTime(5000);
        await Promise.resolve();
      });

      expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(callsBeforeUnmount);

      openSpy.mockRestore();
    });

    it("stops watching the pop-up as soon as the frontend callback arrives", async () => {
      const popup = makePopup();
      const openSpy = jest.spyOn(window, "open").mockReturnValue(popup);

      const { connectButton } = await renderDisconnectedAndFindConnect();

      jest.useFakeTimers();
      await act(async () => {
        fireEvent.click(connectButton);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByText("Opening QuickBooks…")).toBeInTheDocument();

      // The popup posts its result before it closes; that message must retire the
      // 500ms watcher so the popup-closed path cannot refetch status a second time.
      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            origin: window.location.origin,
            data: {
              type: "FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK",
              provider: "accounting",
              result: "success",
            },
          }),
        );
        await Promise.resolve();
        await Promise.resolve();
      });

      const callsAfterCallback = mockedGetQuickBooksStatus.mock.calls.length;
      expect(callsAfterCallback).toBe(2);

      popup.closed = true;
      await act(async () => {
        jest.advanceTimersByTime(5000);
        await Promise.resolve();
      });

      expect(mockedGetQuickBooksStatus).toHaveBeenCalledTimes(callsAfterCallback);

      openSpy.mockRestore();
    });

    it("shows an error when disconnecting QuickBooks fails", async () => {
      mockedDisconnectQuickBooks.mockRejectedValue(new Error("Disconnect rejected by QuickBooks."));

      await renderConnectedDashboard();

      fireEvent.click(await screen.findByRole("button", { name: "Disconnect QuickBooks" }));

      expect(await screen.findByText("Disconnect rejected by QuickBooks.")).toBeInTheDocument();
      // The workspace stays put — nothing was actually disconnected.
      expect(screen.getByRole("heading", { name: "Vendors" })).toBeInTheDocument();
    });
  });

  // ── Resuming in-flight sync jobs on load ─────────────────────────────────────
  //
  // getQuickBooksSyncState reports jobs that were already running when the page
  // loaded. Each module re-attaches its own 3s poller, and on a terminal status it
  // stops polling, refreshes the connection, refetches that module, and toasts.

  describe("resuming an in-flight sync job", () => {
    /** Arms the sync-state response with a single active job for `jobKey`. */
    function armActiveJob(jobKey: string, jobId: number) {
      mockedGetQuickBooksSyncState.mockResolvedValueOnce({
        data: { jobs: { [jobKey]: { activeJobId: jobId, activeStatus: "RUNNING" } } },
      } as any);
    }

    const RESUME_CASES: { jobKey: string; jobId: number; toast: string; refetch: jest.Mock }[] = [
      { jobKey: "bills", jobId: 8001, toast: "Bill sync completed.", refetch: mockedGetQuickBooksBillCdcView },
      { jobKey: "bill_payments", jobId: 8002, toast: "Bill payment sync completed.", refetch: mockedGetQuickBooksBillPaymentCdcView },
      { jobKey: "customers", jobId: 8003, toast: "Customer sync completed.", refetch: mockedGetQuickBooksCustomerCdcView },
      { jobKey: "projects", jobId: 8004, toast: "Project sync completed.", refetch: mockedGetQuickBooksProjectCdcView },
      { jobKey: "employees", jobId: 8005, toast: "Employee sync completed.", refetch: mockedGetQuickBooksEmployeeCdcView },
      { jobKey: "services", jobId: 8006, toast: "Service role sync completed.", refetch: mockedGetQuickBooksServiceRoleCdcView },
      { jobKey: "timelogs", jobId: 8007, toast: "Time activity sync completed.", refetch: mockedGetQuickBooksTimelogCdcView },
    ];

    RESUME_CASES.forEach(({ jobKey, jobId, toast, refetch }) => {
      it(`polls the resumed ${jobKey} job to completion and refreshes that module`, async () => {
        jest.useFakeTimers();
        armActiveJob(jobKey, jobId);
        mockedGetQuickBooksSyncJob.mockResolvedValueOnce({
          data: { job: { status: "COMPLETED" } },
        } as any);

        renderComponent();
        await waitForDashboardReady();

        await act(async () => {
          jest.advanceTimersByTime(3000);
        });

        await waitFor(() =>
          expect(mockedGetQuickBooksSyncJob).toHaveBeenCalledWith(accessToken, tenantId, jobId),
        );
        expect(await screen.findByText(toast)).toBeInTheDocument();
        expect(refetch).toHaveBeenCalled();
      });
    });

    it("reports a failed job as an error toast", async () => {
      jest.useFakeTimers();
      armActiveJob("vendors", 8100);
      mockedGetQuickBooksSyncJob.mockResolvedValueOnce({
        data: { job: { status: "FAILED" } },
      } as any);

      renderComponent();
      await waitForDashboardReady();

      await act(async () => {
        jest.advanceTimersByTime(3000);
      });

      expect(
        await screen.findByText("Vendor sync failed. Please try again or check your connection."),
      ).toBeInTheDocument();
    });

    it("warns in the singular when one record still needs attention", async () => {
      jest.useFakeTimers();
      armActiveJob("vendors", 8101);
      mockedGetQuickBooksSyncJob.mockResolvedValueOnce({
        data: { job: { status: "COMPLETED" } },
      } as any);
      mockedGetQuickBooksVendorCdcView.mockResolvedValue({
        data: {
          ...defaultVendorResponse.data,
          summary: { ...defaultVendorResponse.data.summary, failedVendors: 1, importFailedVendors: 0 },
        },
      } as any);

      renderComponent();
      await waitForDashboardReady();

      await act(async () => {
        jest.advanceTimersByTime(3000);
      });

      expect(
        await screen.findByText("Vendor sync completed — 1 record needs attention."),
      ).toBeInTheDocument();
    });

    it("warns in the plural when several records still need attention", async () => {
      jest.useFakeTimers();
      armActiveJob("vendors", 8102);
      mockedGetQuickBooksSyncJob.mockResolvedValueOnce({
        data: { job: { status: "COMPLETED" } },
      } as any);
      mockedGetQuickBooksVendorCdcView.mockResolvedValue({
        data: {
          ...defaultVendorResponse.data,
          summary: { ...defaultVendorResponse.data.summary, failedVendors: 2, importFailedVendors: 1 },
        },
      } as any);

      renderComponent();
      await waitForDashboardReady();

      await act(async () => {
        jest.advanceTimersByTime(3000);
      });

      expect(
        await screen.findByText("Vendor sync completed — 3 records need attention."),
      ).toBeInTheDocument();
    });

    it("keeps polling when the job response carries no job payload yet", async () => {
      jest.useFakeTimers();
      armActiveJob("vendors", 8103);
      mockedGetQuickBooksSyncJob
        .mockResolvedValueOnce({ data: {} } as any)
        .mockResolvedValueOnce({ data: { job: { status: "COMPLETED" } } } as any);

      renderComponent();
      await waitForDashboardReady();

      await act(async () => {
        jest.advanceTimersByTime(3000);
      });
      expect(screen.queryByText("Vendor sync completed.")).not.toBeInTheDocument();

      await act(async () => {
        jest.advanceTimersByTime(3000);
      });
      expect(await screen.findByText("Vendor sync completed.")).toBeInTheDocument();
    });

    it("survives a transient network blip and completes on the next tick", async () => {
      jest.useFakeTimers();
      armActiveJob("vendors", 8104);
      mockedGetQuickBooksSyncJob
        .mockRejectedValueOnce(new Error("Network Error"))
        .mockResolvedValueOnce({ data: { job: { status: "COMPLETED" } } } as any);

      renderComponent();
      await waitForDashboardReady();

      await act(async () => {
        jest.advanceTimersByTime(3000);
      });
      await act(async () => {
        jest.advanceTimersByTime(3000);
      });

      expect(await screen.findByText("Vendor sync completed.")).toBeInTheDocument();
    });

    it("stops polling and surfaces a non-transient poll failure", async () => {
      jest.useFakeTimers();
      armActiveJob("vendors", 8105);
      mockedGetQuickBooksSyncJob.mockRejectedValue(new Error("Job lookup rejected"));

      renderComponent();
      await waitForDashboardReady();

      await act(async () => {
        jest.advanceTimersByTime(3000);
      });

      expect(await screen.findByText("Job lookup rejected")).toBeInTheDocument();
      const callsAfterStop = mockedGetQuickBooksSyncJob.mock.calls.length;

      // Polling was torn down, so further ticks must not hit the API again.
      await act(async () => {
        jest.advanceTimersByTime(9000);
      });
      expect(mockedGetQuickBooksSyncJob).toHaveBeenCalledTimes(callsAfterStop);
    });

    it("shows an error when the sync-state lookup itself fails", async () => {
      mockedGetQuickBooksSyncState.mockRejectedValue(new Error("Sync state unavailable"));

      renderComponent();

      expect(await screen.findByText("Sync state unavailable")).toBeInTheDocument();
    });

    it("skips the sync-state lookup entirely when the session has no object id", async () => {
      render(
        <UserContext.Provider value={{ userSessionDetails: {} } as any}>
          <QuickBooksTable />
        </UserContext.Provider>,
      );

      await waitFor(() => expect(mockedGetQuickBooksStatus).toHaveBeenCalled());
      expect(mockedGetQuickBooksSyncState).not.toHaveBeenCalled();
    });

    // Every module runs its own poller, so each one needs its own proof that a
    // half-written response is tolerated and that a real failure tears it down.
    const ALL_JOB_KEYS: { jobKey: string; jobId: number; toast: string }[] = [
      { jobKey: "vendors", jobId: 8201, toast: "Vendor sync completed." },
      { jobKey: "accounts", jobId: 8202, toast: "Chart of accounts sync completed." },
      { jobKey: "bills", jobId: 8203, toast: "Bill sync completed." },
      { jobKey: "bill_payments", jobId: 8204, toast: "Bill payment sync completed." },
      { jobKey: "customers", jobId: 8205, toast: "Customer sync completed." },
      { jobKey: "projects", jobId: 8206, toast: "Project sync completed." },
      { jobKey: "employees", jobId: 8207, toast: "Employee sync completed." },
      { jobKey: "services", jobId: 8208, toast: "Service role sync completed." },
      { jobKey: "timelogs", jobId: 8209, toast: "Time activity sync completed." },
    ];

    ALL_JOB_KEYS.forEach(({ jobKey, jobId, toast }) => {
      it(`rides out an empty response and a network blip on the ${jobKey} job`, async () => {
        jest.useFakeTimers();
        armActiveJob(jobKey, jobId);
        mockedGetQuickBooksSyncJob
          .mockResolvedValueOnce({ data: {} } as any)
          .mockRejectedValueOnce(new Error("Failed to fetch"))
          .mockResolvedValueOnce({ data: { job: { status: "COMPLETED" } } } as any);

        renderComponent();
        await waitForDashboardReady();

        // Tick 1: empty response. Tick 2: transient network error. Tick 3: completion.
        await act(async () => {
          jest.advanceTimersByTime(3000);
        });
        await act(async () => {
          jest.advanceTimersByTime(3000);
        });
        await act(async () => {
          jest.advanceTimersByTime(3000);
        });

        expect(await screen.findByText(toast)).toBeInTheDocument();
      });

      it(`stops the ${jobKey} poller when the job lookup keeps failing`, async () => {
        jest.useFakeTimers();
        armActiveJob(jobKey, jobId);
        mockedGetQuickBooksSyncJob.mockRejectedValue(new Error("Job lookup rejected"));

        renderComponent();
        await waitForDashboardReady();

        await act(async () => {
          jest.advanceTimersByTime(3000);
        });

        expect(await screen.findByText("Job lookup rejected")).toBeInTheDocument();
        const callsAfterStop = mockedGetQuickBooksSyncJob.mock.calls.length;

        await act(async () => {
          jest.advanceTimersByTime(9000);
        });
        expect(mockedGetQuickBooksSyncJob).toHaveBeenCalledTimes(callsAfterStop);
      });
    });
  });

  // ── Sync start failures ──────────────────────────────────────────────────────
  //
  // Every module refuses to enter the "running" state unless the backend hands back
  // a job id, so a malformed 200 is reported instead of silently looking successful.

  describe("sync start failures", () => {
    /** Each module's rail label, heading, sync button, and sync-start mock. */
    const SYNC_SPECS: {
      rail: string;
      heading: string;
      button: RegExp;
      start: jest.Mock;
      missingJobIdMessage: string;
      notOkMessage?: string;
    }[] = [
      {
        rail: "Vendors", heading: "Vendors", button: /sync vendors/i,
        start: mockedStartQuickBooksVendorsSync, missingJobIdMessage: "Vendor sync jobId missing",
      },
      {
        rail: "Chart of Accounts", heading: "Chart of Accounts", button: /sync accounts/i,
        start: mockedStartQuickBooksChartOfAccountsSync, missingJobIdMessage: "Account sync jobId missing",
      },
      {
        rail: "Bills", heading: "Bills", button: /sync bills/i,
        start: mockedStartQuickBooksBillsSync, missingJobIdMessage: "Bill sync jobId missing",
        notOkMessage: "Bill sync failed. Please try again.",
      },
      {
        rail: "Bill Payments", heading: "Bill Payments", button: /sync bill payments/i,
        start: mockedStartQuickBooksBillPaymentsSync, missingJobIdMessage: "Bill payment sync jobId missing",
        notOkMessage: "Bill payment sync failed. Please try again.",
      },
      {
        rail: "Customers", heading: "Customers", button: /sync customers/i,
        start: mockedStartQuickBooksCustomersSync, missingJobIdMessage: "Customer sync jobId missing",
      },
      {
        rail: "Projects", heading: "Projects", button: /sync projects/i,
        start: mockedStartQuickBooksProjectsSync, missingJobIdMessage: "Project export jobId missing",
        notOkMessage: "Project sync failed. Please try again.",
      },
      {
        rail: "Time Activity", heading: "Time Activity", button: /sync time activity/i,
        start: mockedStartQuickBooksTimelogsSync,
        missingJobIdMessage: "QuickBooks time activity sync jobId missing",
        notOkMessage: "QuickBooks time activity sync failed. Please try again.",
      },
      {
        rail: "Services", heading: "Services", button: /sync services/i,
        start: mockedStartQuickBooksServiceRolesSync, missingJobIdMessage: "Service role export jobId missing",
        notOkMessage: "Service role export failed. Please try again.",
      },
    ];

    SYNC_SPECS.forEach(({ rail, heading, button, start, missingJobIdMessage }) => {
      it(`reports a missing job id when starting the ${rail.toLowerCase()} sync`, async () => {
        start.mockResolvedValue({ ok: true, data: {} } as any);

        renderComponent();
        await gotoModule(rail, heading);
        const syncButton = await screen.findByRole("button", { name: button });
        await waitFor(() => expect(syncButton).not.toBeDisabled());

        fireEvent.click(syncButton);

        expect(await screen.findByText(missingJobIdMessage)).toBeInTheDocument();
      });
    });

    SYNC_SPECS.filter((s) => s.notOkMessage).forEach(({ rail, heading, button, start, notOkMessage }) => {
      it(`reports a rejected ${rail.toLowerCase()} sync request`, async () => {
        start.mockResolvedValue({ ok: false } as any);

        renderComponent();
        await gotoModule(rail, heading);
        const syncButton = await screen.findByRole("button", { name: button });
        await waitFor(() => expect(syncButton).not.toBeDisabled());

        fireEvent.click(syncButton);

        expect(await screen.findByText(notOkMessage as string)).toBeInTheDocument();
      });
    });

    it("reports a missing job id when starting the employee sync", async () => {
      // Employee sync stays locked until the module is configured.
      mockedGetQuickBooksEmployeeSyncConfig.mockResolvedValue({
        ok: true,
        data: { configured: true, days_after_hire: 2 },
      } as any);
      mockedStartQuickBooksEmployeesSync.mockResolvedValue({ ok: true, data: {} } as any);

      await renderConnectedEmployees();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sync employees/i })).not.toBeDisabled(),
      );

      fireEvent.click(screen.getByRole("button", { name: /sync employees/i }));

      expect(await screen.findByText("Employee sync jobId missing")).toBeInTheDocument();
    });

    it("reports a rejected employee sync request", async () => {
      mockedGetQuickBooksEmployeeSyncConfig.mockResolvedValue({
        ok: true,
        data: { configured: true, days_after_hire: 2 },
      } as any);
      mockedStartQuickBooksEmployeesSync.mockResolvedValue({ ok: false } as any);

      await renderConnectedEmployees();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sync employees/i })).not.toBeDisabled(),
      );

      fireEvent.click(screen.getByRole("button", { name: /sync employees/i }));

      expect(await screen.findByText("Employee sync failed. Please try again.")).toBeInTheDocument();
    });

    it("shows a plan-upgrade warning when the bill sync is blocked by the QuickBooks plan", async () => {
      mockedStartQuickBooksBillsSync.mockResolvedValue({
        ok: false,
        status: 403,
        message: "Bills are not available on your current QuickBooks plan.",
      } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByRole("button", { name: /sync bills/i }));

      expect(await screen.findByText("QuickBooks plan upgrade required")).toBeInTheDocument();
      expect(
        screen.getByText(/Bills are not available on your current QuickBooks plan\./i),
      ).toBeInTheDocument();
    });

    it("shows a plan-upgrade warning when the bill payment sync is blocked by the QuickBooks plan", async () => {
      mockedStartQuickBooksBillPaymentsSync.mockResolvedValue({
        ok: false,
        status: 403,
        message: "Bill payments are not available on your current QuickBooks plan.",
      } as any);

      await renderConnectedBillPayments();
      const syncButton = await screen.findByRole("button", { name: /sync bill payments/i });
      await waitFor(() => expect(syncButton).not.toBeDisabled());

      fireEvent.click(syncButton);

      expect(await screen.findByText("QuickBooks plan upgrade required")).toBeInTheDocument();
    });
  });

  // ── Row rendering: status wording, dates, durations, worker type ──────────────

  describe("bill status wording", () => {
    /** One bill row per backend sync status, so every label branch renders at once. */
    const billStatusRows = [
      { receiptId: 4101, displayName: "SYN-Bill Assignment", syncStatus: "ASSIGNMENT_NEEDED", linked: true },
      { receiptId: 4102, displayName: "SYN-Bill Pending", syncStatus: "PENDING_PUSH", linked: true },
      { receiptId: 4103, displayName: "SYN-Bill Failed", syncStatus: "FAILED", linked: true },
      { receiptId: 4104, displayName: "SYN-Bill Conflict", syncStatus: "CONFLICT", linked: true },
      { receiptId: 4105, displayName: "SYN-Bill Import", syncStatus: "IMPORT_FAILED", linked: true },
      { receiptId: 4106, displayName: "SYN-Bill SyncFail", syncStatus: "SYNC_FAILED", linked: true },
      { receiptId: 4107, displayName: "SYN-Bill NotReady", syncStatus: "NOT_READY", linked: true },
      { receiptId: 4108, displayName: "SYN-Bill Incomplete", syncStatus: "SYNCED_INCOMPLETE", linked: true },
      { receiptId: 4109, displayName: "SYN-Bill Approval", syncStatus: "AWAITING_APPROVAL", linked: true },
      { receiptId: 4110, displayName: "SYN-Bill Unlinked", syncStatus: "MYSTERY_STATE", linked: false },
      { receiptId: 4111, displayName: "SYN-Bill Unknown", syncStatus: "MYSTERY_STATE", linked: true },
    ];

    async function renderBillRows(rows: object[]) {
      mockedGetQuickBooksBillCdcView.mockResolvedValue({
        data: { ...defaultBillResponse.data, rows },
      } as any);
      renderComponent();
      await gotoModule("Bills", "Bills");
    }

    it("labels every backend sync status with its own wording", async () => {
      await renderBillRows(billStatusRows);
      await screen.findByText("SYN-Bill Assignment");

      const expected = [
        "Assignment needed", "Pending", "Failed", "Conflict", "Import Failed",
        "Sync Failed", "Not Ready", "Needs Project Link", "Awaiting Approval",
        "Not linked", "Unknown",
      ];
      expected.forEach((label) => {
        expect(screen.getAllByText(label).length).toBeGreaterThan(0);
      });
    });

    it("marks a stock bill awaiting review instead of reusing the generic wording", async () => {
      await renderBillRows([
        {
          ...defaultBillResponse.data.rows[0],
          receiptId: 4120,
          displayName: "SYN-Stock Review",
          source: "FIELDCRAFT",
          isStockBill: true,
          syncStatus: "SYNCED_INCOMPLETE",
          linked: true,
        },
      ]);

      expect(await screen.findByText("Stock bill review")).toBeInTheDocument();
    });

    it("explains that a stock bill's project link was deliberately ignored", async () => {
      await renderBillRows([
        {
          ...defaultBillResponse.data.rows[0],
          receiptId: 4121,
          displayName: "SYN-Stock Blocked",
          source: "FIELDCRAFT",
          isStockBill: true,
          stockReference: "PO-STOCK-9",
          syncStatus: "FAILED",
          linked: true,
          reason: JSON.stringify({
            message: "Stock bill cannot be linked to a project in QuickBooks.",
          }),
        },
      ]);

      expect(await screen.findAllByText("Project link ignored")).not.toHaveLength(0);
    });

    it("keeps a plain-text failure reason readable when it is not JSON", async () => {
      await renderBillRows([
        {
          ...defaultBillResponse.data.rows[0],
          receiptId: 4122,
          displayName: "SYN-Bill Raw Reason",
          syncStatus: "FAILED",
          linked: true,
          reason: "Vendor is missing in QuickBooks",
        },
      ]);
      await screen.findByText("SYN-Bill Raw Reason");

      expect(
        screen.getByLabelText("Details: Vendor is missing in QuickBooks"),
      ).toBeInTheDocument();
    });
  });

  describe("customer row rendering", () => {
    const customerRows = [
      {
        customerId: 1101, displayName: "SYN-Acme Homes", source: "FIELDCRAFT",
        email: "ap@syn-acme.test", customerStatus: "ACTIVE", syncStatus: "SYNCED",
        externalCustomerId: "QBO-C-1101", mappingId: 1201,
        mappingUpdatedAt: "2026-04-08T04:00:00Z", linked: true,
      },
      {
        customerId: 1102, displayName: "SYN-Bell Roofing", source: "QuickBooks",
        customerStatus: "INACTIVE", syncStatus: "PENDING_PUSH",
        externalCustomerId: null, mappingId: null, mappingUpdatedAt: null, linked: false,
      },
      {
        customerId: 1103, displayName: "SYN-Cedar Grove", customerStatus: "INVITED",
        syncStatus: "FAILED", reason: JSON.stringify({ message: "Customer email is required." }),
        mappingUpdatedAt: "not-a-real-date", linked: true,
      },
      {
        customerId: 1104, displayName: "SYN-Dover Estates", customerStatus: null,
        syncStatus: "IMPORT_FAILED", mappingUpdatedAt: null, linked: true,
      },
    ];

    async function renderCustomerRows() {
      mockedGetQuickBooksCustomerCdcView.mockResolvedValue({
        data: {
          summary: {
            totalCustomers: 4, mappedCustomers: 2, unmappedCustomers: 2,
            syncedCustomers: 1, pendingPushCustomers: 1, failedCustomers: 1,
            importFailedCustomers: 1, syncFailedCustomers: 0,
          },
          pagination: { page: 1, pageSize: 10, totalRecords: 4, totalPages: 1 },
          rows: customerRows,
        },
      } as any);
      renderComponent();
      await screen.findByRole("heading", { name: "Customers" });
      await screen.findByText("SYN-Acme Homes");
    }

    it("shows each FIELDCRAFT customer state and falls back to 'Not in FIELDCRAFT'", async () => {
      await renderCustomerRows();

      expect(screen.getByText("Active")).toBeInTheDocument();
      expect(screen.getByText("Inactive")).toBeInTheDocument();
      expect(screen.getByText("Invited")).toBeInTheDocument();
      expect(screen.getByText("Not in FIELDCRAFT")).toBeInTheDocument();
    });

    it("shows the QuickBooks id when linked and 'Not linked' when it is missing", async () => {
      await renderCustomerRows();

      expect(screen.getByText("QBO-C-1101")).toBeInTheDocument();
      expect(screen.getAllByText("Not linked").length).toBeGreaterThan(0);
    });

    it("renders an em dash for a missing or unparseable last-synced timestamp", async () => {
      await renderCustomerRows();

      // Two rows have no timestamp and one has an unparseable one.
      expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    });

    // Demo feedback: this column used to render 08/04/2026 for April 8th, because the
    // formatter was hardcoded to en-GB. The whole workspace is US numeric now.
    it("renders last-synced as a US numeric date with the month first", async () => {
      await renderCustomerRows();

      // 04:00Z falls on Apr 7 or Apr 8 depending on the viewer's zone, so pin the shape
      // and the leading month rather than an exact day — the point is that 04 leads.
      expect(screen.getByText(/^04\/0[78]\/2026 \d{2}:\d{2} (AM|PM)$/)).toBeInTheDocument();
      expect(screen.queryByText(/\bApr(il)?\b/)).not.toBeInTheDocument();
    });

    it("surfaces a JSON failure reason as its message only", async () => {
      await renderCustomerRows();

      expect(screen.getByLabelText("Details: Customer email is required.")).toBeInTheDocument();
    });
  });

  describe("time activity row rendering", () => {
    async function renderTimelogRows(rows: object[]) {
      mockedGetQuickBooksTimelogCdcView.mockResolvedValue({
        data: {
          summary: { total: rows.length, synced: 0, pending: rows.length, failed: 0, notReady: 0 },
          pagination: { page: 1, pageSize: 10, totalRecords: rows.length, totalPages: 1 },
          records: rows,
        },
      } as any);
      await renderConnectedTimelogs();
    }

    it("formats durations with exact seconds", async () => {
      await renderTimelogRows([
        { ...defaultTimelogResponse.data.records[0], timelogId: 2001, taskName: "SYN-Two hours", durationSeconds: 7200, durationMinutes: 120 },
        { ...defaultTimelogResponse.data.records[0], timelogId: 2002, taskName: "SYN-Hour and a half", durationSeconds: 5400, durationMinutes: 90 },
        { ...defaultTimelogResponse.data.records[0], timelogId: 2003, taskName: "SYN-Short task", durationSeconds: 2736, durationMinutes: 45 },
        { ...defaultTimelogResponse.data.records[0], timelogId: 2004, taskName: "SYN-Sub-minute task", durationSeconds: 38, durationMinutes: 0 },
        { ...defaultTimelogResponse.data.records[0], timelogId: 2005, taskName: "SYN-No duration", durationSeconds: null, durationMinutes: null },
      ]);
      await screen.findByText("SYN-Two hours");

      expect(screen.getByText("2h 0m 0s")).toBeInTheDocument();
      expect(screen.getByText("1h 30m 0s")).toBeInTheDocument();
      expect(screen.getByText("45m 36s")).toBeInTheDocument();
      expect(screen.getByText("38s")).toBeInTheDocument();
      expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    });

    it("shows both the task start and end time", async () => {
      await renderTimelogRows([
        {
          ...defaultTimelogResponse.data.records[0],
          timelogId: 2006,
          taskName: "SYN-Time range",
          startTime: "2026-04-07T13:00:00Z",
          endTime: "2026-04-07T13:38:00Z",
        },
      ]);

      await screen.findByText("SYN-Time range");
      // Two labelled lines, not one run: the end time used to be ellipsised away at the
      // sub line's 240px cap, so assert both labels AND both values actually render.
      const win = document.querySelector(".qbw-timewin");
      expect(win).not.toBeNull();
      expect(win).toHaveTextContent(/^Start:.+End:.+/);
      const [startCell, endCell] = [win!.children[1], win!.children[3]];
      expect(startCell.textContent).not.toBe("—");
      expect(endCell.textContent).not.toBe("—");
      expect(startCell.textContent).not.toBe(endCell.textContent);
    });

    it("tags the worker classification returned by QuickBooks", async () => {
      await renderTimelogRows([
        {
          ...defaultTimelogResponse.data.records[0], timelogId: 2011,
          taskName: "SYN-Contractor task", employeeName: "SYN-Casey Vendor",
          workerTypeLabel: "1099",
        },
        {
          ...defaultTimelogResponse.data.records[0], timelogId: 2012,
          taskName: "SYN-Employee task", employeeName: "SYN-Robin Staff",
          workerTypeLabel: "W2",
        },
      ]);
      await screen.findByText("SYN-Contractor task");

      expect(screen.getByText("1099")).toBeInTheDocument();
      expect(screen.getByText("W-2")).toBeInTheDocument();
    });

    it("flags unlinked employee, project, and service prerequisites per row", async () => {
      await renderTimelogRows([
        {
          ...defaultTimelogResponse.data.records[0], timelogId: 2021,
          taskName: "SYN-Unlinked everywhere",
          startTime: "definitely-not-a-date",
          employeeLinked: false, projectLinked: false, serviceLinked: false,
          projectName: null, roleName: "Roofing Foreman",
        },
      ]);
      await screen.findByText("SYN-Unlinked everywhere");

      expect(screen.getByText("Not synced")).toBeInTheDocument();
      expect(screen.getByText("Project")).toBeInTheDocument();
      expect(screen.getByText("Service")).toBeInTheDocument();
      // "Not synced yet" is the QuickBooks-id column's own empty wording.
      expect(screen.getByText("Not synced yet")).toBeInTheDocument();
    });

    it("shows the QuickBooks TimeActivity id once the row is mapped", async () => {
      await renderTimelogRows([
        {
          ...defaultTimelogResponse.data.records[0], timelogId: 2031,
          taskName: "SYN-Mapped task", syncStatus: "SYNCED",
          externalEntityType: "TIME_ACTIVITY", externalEntityId: "QBO-TA-2031",
        },
      ]);

      expect(await screen.findByText("QBO-TA-2031")).toBeInTheDocument();
    });
  });

  describe("employee row rendering", () => {
    it("tags employees with the worker classification QuickBooks reported", async () => {
      mockedGetQuickBooksEmployeeCdcView.mockResolvedValue({
        data: {
          ...defaultEmployeeResponse.data,
          rows: [
            { ...defaultEmployeeResponse.data.rows[0], employeeId: 8101, displayName: "SYN-Casey Contractor", workerType: "VENDOR" },
            { ...defaultEmployeeResponse.data.rows[0], employeeId: 8102, displayName: "SYN-Robin Employee", workerType: "EMPLOYEE" },
          ],
        },
      } as any);

      await renderConnectedEmployees();
      await screen.findByText("SYN-Casey Contractor");

      expect(screen.getByText("1099")).toBeInTheDocument();
      expect(screen.getByText("W-2")).toBeInTheDocument();
    });
  });

  // ── Pagination, shared across every module's table ────────────────────────────

  describe("pagination", () => {
    const manyPages = { page: 1, pageSize: 10, totalRecords: 25, totalPages: 3 };
    const onePage = { page: 2, pageSize: 10, totalRecords: 5, totalPages: 1 };

    const customerSummary = {
      totalCustomers: 25, mappedCustomers: 25, unmappedCustomers: 0,
      syncedCustomers: 25, pendingPushCustomers: 0, failedCustomers: 0,
      importFailedCustomers: 0, syncFailedCustomers: 0,
    };

    /** Per-module rail label, heading, CDC mock, and a paginated response factory. */
    const PAGER_SPECS: {
      rail: string;
      heading: string;
      mock: jest.Mock;
      respond: (pagination: object) => object;
    }[] = [
      {
        rail: "Vendors", heading: "Vendors", mock: mockedGetQuickBooksVendorCdcView,
        respond: (pagination) => ({ data: { ...defaultVendorResponse.data, pagination } }),
      },
      {
        rail: "Chart of Accounts", heading: "Chart of Accounts", mock: mockedGetQuickBooksAccountCdcView,
        respond: (pagination) => ({ data: { ...defaultAccountResponse.data, pagination } }),
      },
      {
        rail: "Bills", heading: "Bills", mock: mockedGetQuickBooksBillCdcView,
        respond: (pagination) => ({ data: { ...defaultBillResponse.data, pagination } }),
      },
      {
        rail: "Bill Payments", heading: "Bill Payments", mock: mockedGetQuickBooksBillPaymentCdcView,
        respond: (pagination) => ({ data: { ...defaultBillPaymentResponse.data, pagination } }),
      },
      {
        rail: "Customers", heading: "Customers", mock: mockedGetQuickBooksCustomerCdcView,
        respond: (pagination) => ({
          data: {
            summary: customerSummary,
            pagination,
            rows: [{ customerId: 1301, displayName: "SYN-Paged Customer", syncStatus: "SYNCED", linked: true }],
          },
        }),
      },
      {
        rail: "Projects", heading: "Projects", mock: mockedGetQuickBooksProjectCdcView,
        respond: (pagination) => ({ data: { ...defaultProjectResponse.data, pagination } }),
      },
      {
        rail: "Employees", heading: "Employees", mock: mockedGetQuickBooksEmployeeCdcView,
        respond: (pagination) => ({ data: { ...defaultEmployeeResponse.data, pagination } }),
      },
      {
        rail: "Time Activity", heading: "Time Activity", mock: mockedGetQuickBooksTimelogCdcView,
        respond: (pagination) => ({ data: { ...defaultTimelogResponse.data, pagination } }),
      },
      {
        rail: "Services", heading: "Services", mock: mockedGetQuickBooksServiceRoleCdcView,
        respond: (pagination) => ({ data: { ...makeSrResponse([srRowNoCoa]).data, pagination } }),
      },
    ];

    PAGER_SPECS.forEach(({ rail, heading, mock, respond }) => {
      it(`refetches the ${rail.toLowerCase()} table from page one when rows-per-page changes`, async () => {
        mock.mockResolvedValue(respond(manyPages) as any);

        renderComponent();
        await gotoModule(rail, heading);
        await waitFor(() => expect(mock).toHaveBeenCalled());

        fireEvent.change(screen.getByLabelText("Rows per page"), { target: { value: "25" } });

        await waitFor(() =>
          expect(mock).toHaveBeenLastCalledWith(
            accessToken,
            expect.objectContaining({ page: 1, pageSize: 25 }),
          ),
        );
      });

      it(`snaps the ${rail.toLowerCase()} table back to page one when the backend reports fewer pages`, async () => {
        mock.mockResolvedValue(respond(manyPages) as any);

        renderComponent();
        await gotoModule(rail, heading);
        // Wait for the first response to land — the pager only unlocks once the
        // table knows there is more than one page.
        await waitFor(() =>
          expect(screen.getByRole("button", { name: "Next page" })).not.toBeDisabled(),
        );

        // The next request comes back describing a much smaller result set, so the
        // page the user just moved to no longer exists.
        mock.mockResolvedValue(respond(onePage) as any);
        mock.mockClear();
        fireEvent.click(screen.getByRole("button", { name: "Next page" }));

        // Page 2 is requested first…
        await waitFor(() =>
          expect(mock).toHaveBeenCalledWith(accessToken, expect.objectContaining({ page: 2 })),
        );
        // …then the shrunken result set forces the table back to page 1.
        await waitFor(() =>
          expect(mock).toHaveBeenLastCalledWith(accessToken, expect.objectContaining({ page: 1 })),
        );
      });
    });
  });

  // ── Filter pills that are not already covered per module ──────────────────────

  describe("filter pills", () => {
    it("filters chart of accounts by pending and back to all", async () => {
      renderComponent();
      await gotoModule("Chart of Accounts", "Chart of Accounts");

      fireEvent.click(screen.getByRole("button", { name: /pending/i }));
      await waitFor(() =>
        expect(mockedGetQuickBooksAccountCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "PENDING_PUSH" }),
        ),
      );

      fireEvent.click(screen.getByRole("button", { name: /^all/i }));
      await waitFor(() =>
        expect(mockedGetQuickBooksAccountCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "ALL" }),
        ),
      );
    });

    it("maps the bills needs-attention pill onto the FAILED status", async () => {
      renderComponent();
      await gotoModule("Bills", "Bills");

      fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
      await waitFor(() =>
        expect(mockedGetQuickBooksBillCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "FAILED" }),
        ),
      );

      fireEvent.click(screen.getByRole("button", { name: /synced/i }));
      await waitFor(() =>
        expect(mockedGetQuickBooksBillCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "SYNCED" }),
        ),
      );
    });

    it("maps the projects needs-attention pill onto the FAILED status", async () => {
      await renderConnectedProjects();

      fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
      await waitFor(() =>
        expect(mockedGetQuickBooksProjectCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "FAILED" }),
        ),
      );
    });

    it("maps the employees needs-attention pill onto the FAILED status", async () => {
      await renderConnectedEmployees();

      fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
      await waitFor(() =>
        expect(mockedGetQuickBooksEmployeeCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "FAILED" }),
        ),
      );
    });

    it("maps the services needs-attention pill onto the FAILED status", async () => {
      await renderConnectedServices();

      fireEvent.click(screen.getByRole("button", { name: /needs attention/i }));
      await waitFor(() =>
        expect(mockedGetQuickBooksServiceRoleCdcView).toHaveBeenLastCalledWith(
          accessToken,
          expect.objectContaining({ syncStatus: "FAILED" }),
        ),
      );
    });
  });

  // ── Dismissible alerts ───────────────────────────────────────────────────────

  describe("dismissible alerts", () => {
    /** Closes the MUI alert that contains `text`. */
    function closeAlertContaining(text: string) {
      const alert = screen.getByText(text).closest(".MuiAlert-root") as HTMLElement;
      fireEvent.click(within(alert).getByRole("button", { name: /close/i }));
    }

    it("dismisses a success toast", async () => {
      await renderConnectedDashboard();

      fireEvent.click(screen.getByRole("button", { name: /sync vendors/i }));
      expect(await screen.findByText("Vendor sync started successfully.")).toBeInTheDocument();

      closeAlertContaining("Vendor sync started successfully.");

      await waitFor(() =>
        expect(screen.queryByText("Vendor sync started successfully.")).not.toBeInTheDocument(),
      );
    });

    it("dismisses an error alert", async () => {
      mockedGetQuickBooksVendorCdcView.mockRejectedValue(new Error("Unable to load vendor data."));

      renderComponent();
      await gotoModule("Vendors", "Vendors");
      expect(await screen.findByText("Unable to load vendor data.")).toBeInTheDocument();

      closeAlertContaining("Unable to load vendor data.");

      await waitFor(() =>
        expect(screen.queryByText("Unable to load vendor data.")).not.toBeInTheDocument(),
      );
    });

    it("dismisses the plan-upgrade warning", async () => {
      mockedStartQuickBooksProjectsSync.mockResolvedValue({
        ok: false,
        status: 403,
        message: "Projects are not available on your current QuickBooks plan.",
      } as any);

      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");
      fireEvent.click(screen.getByRole("button", { name: /sync projects/i }));

      expect(await screen.findByText("QuickBooks plan upgrade required")).toBeInTheDocument();

      closeAlertContaining("QuickBooks plan upgrade required");

      await waitFor(() =>
        expect(screen.queryByText("QuickBooks plan upgrade required")).not.toBeInTheDocument(),
      );
    });
  });

  // ── Row selection ────────────────────────────────────────────────────────────

  describe("row selection", () => {
    function rowCheckboxes() {
      return screen.getAllByRole("checkbox").filter((cb) => !cb.closest("thead"));
    }

    function headerCheckbox() {
      return screen.getAllByRole("checkbox").find((cb) => cb.closest("thead")) as HTMLElement;
    }

    it("deselects a bill payment row when its checkbox is clicked twice", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Northwind");

      const [firstRow] = rowCheckboxes();
      fireEvent.click(firstRow);
      expect(await screen.findByRole("button", { name: /assign account \(1\)/i })).toBeInTheDocument();

      fireEvent.click(firstRow);
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /assign account/i })).not.toBeInTheDocument(),
      );
    });

    it("selects and clears every bill payment on the page from the header checkbox", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Northwind");

      fireEvent.click(headerCheckbox());
      expect(await screen.findByRole("button", { name: /assign account \(2\)/i })).toBeInTheDocument();

      fireEvent.click(headerCheckbox());
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /assign account/i })).not.toBeInTheDocument(),
      );
    });

    it("selects and clears every service role on the page from the header checkbox", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(makeSrResponse([srRowNoCoa]) as any);

      await renderConnectedServices();
      await screen.findByText("Roofing Foreman");

      fireEvent.click(headerCheckbox());
      expect(await screen.findByRole("button", { name: /assign coa \(1\)/i })).toBeInTheDocument();

      fireEvent.click(headerCheckbox());
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /assign coa/i })).not.toBeInTheDocument(),
      );
    });

    it("deselects a service role when its checkbox is clicked twice", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(makeSrResponse([srRowNoCoa]) as any);

      await renderConnectedServices();
      await screen.findByText("Roofing Foreman");

      const [firstRow] = rowCheckboxes();
      fireEvent.click(firstRow);
      expect(await screen.findByRole("button", { name: /assign coa \(1\)/i })).toBeInTheDocument();

      fireEvent.click(firstRow);
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /assign coa/i })).not.toBeInTheDocument(),
      );
    });
  });

  // ── Dialogs: dismissal without saving ────────────────────────────────────────

  describe("dialog dismissal", () => {
    it("closes the bill payment assign dialog from Cancel without assigning", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Northwind");

      const rowCheckbox = screen.getAllByRole("checkbox").find((cb) => !cb.closest("thead"));
      fireEvent.click(rowCheckbox as HTMLElement);
      fireEvent.click(await screen.findByRole("button", { name: /assign account \(1\)/i }));

      const dialog = await screen.findByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedAssignQuickBooksBillPaymentAccount).not.toHaveBeenCalled();
    });

    it("closes the bill payment assign dialog when it is dismissed with Escape", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Northwind");

      const rowCheckbox = screen.getAllByRole("checkbox").find((cb) => !cb.closest("thead"));
      fireEvent.click(rowCheckbox as HTMLElement);
      fireEvent.click(await screen.findByRole("button", { name: /assign account \(1\)/i }));

      fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape", code: "Escape" });

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedAssignQuickBooksBillPaymentAccount).not.toHaveBeenCalled();
    });

    it("closes the employee sync settings dialog from Cancel without saving", async () => {
      await renderConnectedEmployees();
      fireEvent.click(await screen.findByRole("button", { name: /employee sync settings/i }));

      const dialog = await screen.findByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedSaveQuickBooksEmployeeSyncConfig).not.toHaveBeenCalled();
    });

    it("closes the employee sync settings dialog when it is dismissed with Escape", async () => {
      await renderConnectedEmployees();
      fireEvent.click(await screen.findByRole("button", { name: /employee sync settings/i }));

      fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape", code: "Escape" });

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedSaveQuickBooksEmployeeSyncConfig).not.toHaveBeenCalled();
    });

    it("closes the project sync method dialog from Cancel without saving", async () => {
      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");
      fireEvent.click(screen.getByRole("button", { name: /auto/i }));

      const dialog = await screen.findByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedSaveQuickBooksProjectSyncConfig).not.toHaveBeenCalled();
    });

    it("closes the project sync method dialog when it is dismissed with Escape", async () => {
      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");
      fireEvent.click(screen.getByRole("button", { name: /auto/i }));

      fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape", code: "Escape" });

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedSaveQuickBooksProjectSyncConfig).not.toHaveBeenCalled();
    });

    it("lets the keyboard pick a project sync method", async () => {
      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");
      fireEvent.click(screen.getByRole("button", { name: /auto/i }));

      const dialog = await screen.findByRole("dialog");
      const qboProjectOption = (await within(dialog).findByText("QuickBooks Project"))
        .closest('[role="radio"]') as HTMLElement;

      fireEvent.keyDown(qboProjectOption, { key: "Enter", code: "Enter" });
      await waitFor(() => expect(qboProjectOption).toHaveAttribute("aria-checked", "true"));

      fireEvent.click(within(dialog).getByRole("button", { name: /save settings/i }));

      await waitFor(() =>
        expect(mockedSaveQuickBooksProjectSyncConfig).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({ method: "QBO_PROJECT" }),
        ),
      );
    });

    /** Clicks Save once the dialog has finished loading its current settings. */
    async function clickSaveSettings(dialog: HTMLElement) {
      const save = within(dialog).getByRole("button", { name: /save settings/i });
      await waitFor(() => expect(save).not.toBeDisabled());
      fireEvent.click(save);
    }

    it("reports a rejected project sync settings save", async () => {
      mockedSaveQuickBooksProjectSyncConfig.mockResolvedValue({ ok: false } as any);

      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");
      fireEvent.click(screen.getByRole("button", { name: /auto/i }));

      await clickSaveSettings(await screen.findByRole("dialog"));

      expect(await screen.findByText("Failed to save project sync settings.")).toBeInTheDocument();
    });

    it("marks projects unavailable when saving the sync method hits the plan gate", async () => {
      mockedSaveQuickBooksProjectSyncConfig.mockResolvedValue({
        ok: false,
        status: 403,
        message: "Projects require a higher QuickBooks plan.",
      } as any);

      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");
      fireEvent.click(screen.getByRole("button", { name: /auto/i }));

      await clickSaveSettings(await screen.findByRole("dialog"));

      expect(await screen.findByText("QuickBooks plan upgrade required")).toBeInTheDocument();
    });

    it("reports a rejected employee sync settings save", async () => {
      mockedSaveQuickBooksEmployeeSyncConfig.mockResolvedValue({
        ok: false,
        message: "Employee settings rejected.",
      } as any);

      await renderConnectedEmployees();
      fireEvent.click(await screen.findByRole("button", { name: /employee sync settings/i }));

      await clickSaveSettings(await screen.findByRole("dialog"));

      expect(await screen.findByText("Employee settings rejected.")).toBeInTheDocument();
    });

    it("flushes an uncommitted days-after-hire edit when Save is pressed without blurring", async () => {
      await renderConnectedEmployees();
      fireEvent.click(await screen.findByRole("button", { name: /employee sync settings/i }));

      const dialog = await screen.findByRole("dialog");
      await within(dialog).findByText("Work start date");

      const daysField = document.getElementById("qb-field-days-after-hire") as HTMLInputElement;
      // Deliberately no blur — Save must still pick up the typed value.
      fireEvent.change(daysField, { target: { value: "9" } });
      fireEvent.click(within(dialog).getByRole("button", { name: /save settings/i }));

      await waitFor(() =>
        expect(mockedSaveQuickBooksEmployeeSyncConfig).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({ days_after_hire: 9 }),
        ),
      );
    });

    it("clamps a blank days-after-hire entry to one day", async () => {
      await renderConnectedEmployees();
      fireEvent.click(await screen.findByRole("button", { name: /employee sync settings/i }));

      const dialog = await screen.findByRole("dialog");
      await within(dialog).findByText("Work start date");

      const daysField = document.getElementById("qb-field-days-after-hire") as HTMLInputElement;
      fireEvent.change(daysField, { target: { value: "" } });
      fireEvent.click(within(dialog).getByRole("button", { name: /save settings/i }));

      await waitFor(() =>
        expect(mockedSaveQuickBooksEmployeeSyncConfig).toHaveBeenCalledWith(
          accessToken,
          expect.objectContaining({ days_after_hire: 1 }),
        ),
      );
    });
  });

  // ── Chart-of-accounts picker in the services module ──────────────────────────

  describe("service COA picker", () => {
    const twoCoaAccounts = {
      data: {
        records: [
          {
            id: 12, name: "Zinc Roofing Income", accountType: "Income",
            detailType: "ServiceFeeIncome", externalAccountId: "qbo-ext-12", syncStatus: "SYNCED",
          },
          {
            id: 11, name: "Alpine Labor Income", accountType: "Income",
            detailType: "SalesOfProductIncome", externalAccountId: "qbo-ext-11", syncStatus: "SYNCED",
          },
        ],
      },
    };

    async function openCoaPicker(accountsResponse: object = twoCoaAccounts) {
      mockedGetQuickBooksServiceCoaAccounts.mockResolvedValue(accountsResponse as any);
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(makeSrResponse([srRowNoCoa]) as any);

      await renderConnectedServices();
      await screen.findByText("Roofing Foreman");

      const rowCheckbox = screen.getAllByRole("checkbox").find((cb) => !cb.closest("thead"));
      fireEvent.click(rowCheckbox as HTMLElement);
      fireEvent.click(await screen.findByRole("button", { name: /assign coa \(1\)/i }));

      return screen.findByRole("dialog");
    }

    it("lists the accounts alphabetically within their account type", async () => {
      const dialog = await openCoaPicker();

      fireEvent.mouseDown(within(dialog).getByRole("combobox"));

      // Options carry the detail type and QuickBooks id after the name, so assert on
      // the leading account name — the part the sort actually orders by.
      const options = await screen.findAllByRole("option");
      expect(options).toHaveLength(2);
      expect(options[0].textContent).toMatch(/^Alpine Labor Income/);
      expect(options[1].textContent).toMatch(/^Zinc Roofing Income/);
    });

    it("narrows the account list as the user types", async () => {
      const dialog = await openCoaPicker();

      const combobox = within(dialog).getByRole("combobox");
      fireEvent.mouseDown(combobox);
      await screen.findAllByRole("option");
      // MUI's Autocomplete resets its input value on every render unless the input
      // is focused, so focus before typing or the query is thrown away.
      fireEvent.focus(combobox);

      fireEvent.change(combobox, { target: { value: "alpine" } });

      await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
      expect(screen.getByRole("option")).toHaveTextContent("Alpine Labor Income");
    });

    it("matches an account by its QuickBooks id as well as its name", async () => {
      const dialog = await openCoaPicker();

      const combobox = within(dialog).getByRole("combobox");
      fireEvent.mouseDown(combobox);
      await screen.findAllByRole("option");
      // MUI's Autocomplete resets its input value on every render unless the input
      // is focused, so focus before typing or the query is thrown away.
      fireEvent.focus(combobox);

      fireEvent.change(combobox, { target: { value: "qbo-ext-12" } });

      await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
      expect(screen.getByRole("option")).toHaveTextContent("Zinc Roofing Income");
    });

    it("closes the picker from Cancel without assigning", async () => {
      const dialog = await openCoaPicker();

      fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedAssignQuickBooksServiceIncomeAccount).not.toHaveBeenCalled();
    });

    it("closes the picker when it is dismissed with Escape", async () => {
      const dialog = await openCoaPicker();

      fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockedAssignQuickBooksServiceIncomeAccount).not.toHaveBeenCalled();
    });

    it("falls back to the income-accounts endpoint when the COA endpoint fails", async () => {
      mockedGetQuickBooksServiceCoaAccounts.mockRejectedValue(new Error("coa-accounts unavailable"));
      mockedGetQuickBooksServiceIncomeAccounts.mockResolvedValue({
        data: {
          accounts: [
            {
              id: 14, name: "Fallback Services Income", accountType: "Income",
              detailType: "ServiceFeeIncome", externalAccountId: "qbo-ext-14", syncStatus: "SYNCED",
            },
          ],
        },
      } as any);
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(makeSrResponse([srRowNoCoa]) as any);

      await renderConnectedServices();
      await screen.findByText("Roofing Foreman");

      await waitFor(() => expect(mockedGetQuickBooksServiceIncomeAccounts).toHaveBeenCalled());
      // The fallback list is what enables assignment, so the sync button stays usable.
      expect(screen.getByRole("button", { name: /sync services/i })).not.toBeDisabled();
    });

    it("shows an error when both chart-of-accounts endpoints fail", async () => {
      mockedGetQuickBooksServiceCoaAccounts.mockRejectedValue(new Error("coa-accounts unavailable"));
      mockedGetQuickBooksServiceIncomeAccounts.mockRejectedValue(
        new Error("Unable to load chart of accounts."),
      );
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(makeSrResponse([srRowNoCoa]) as any);

      renderComponent();
      await gotoModule("Services", "Services");

      expect(await screen.findByText("Unable to load chart of accounts.")).toBeInTheDocument();
    });
  });

  // ── Bill attachment preview ──────────────────────────────────────────────────

  describe("bill attachment preview", () => {
    async function openBillWithAttachment() {
      mockedGetQuickBooksBillDocumentInfo.mockResolvedValue({
        data: { hasDocument: true, mimeType: "application/pdf", size: 10240 },
      } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByText("Office Rent"));
      await screen.findByRole("dialog");
    }

    it("collapses the preview again and releases the object URL", async () => {
      if (!URL.revokeObjectURL) URL.revokeObjectURL = jest.fn();
      const revokeSpy = jest.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

      await openBillWithAttachment();

      fireEvent.click(await screen.findByText("View receipt"));
      await screen.findByText("Hide receipt");

      fireEvent.click(screen.getByText("Hide receipt"));

      await screen.findByText("View receipt");
      expect(revokeSpy).toHaveBeenCalledWith("blob:test-document-url");

      revokeSpy.mockRestore();
    });

    it("reports a failed attachment download", async () => {
      mockedDownloadQuickBooksBillDocument.mockRejectedValue(new Error("Blob fetch failed"));

      await openBillWithAttachment();

      fireEvent.click(await screen.findByText("View receipt"));

      expect(
        await screen.findByText("Could not load the attachment. Please try again."),
      ).toBeInTheDocument();
    });
  });

  // ── Bill detail dialog: the project card ─────────────────────────────────────

  describe("bill detail project card", () => {
    async function openBillDialog(overrides: Record<string, any>) {
      const billRow = { ...defaultBillResponse.data.rows[0], ...overrides };
      mockedGetQuickBooksBillCdcView.mockResolvedValue({
        data: { ...defaultBillResponse.data, rows: [billRow] },
      } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByText(billRow.displayName));
      return within(await screen.findByRole("dialog"));
    }

    it("names the linked project", async () => {
      const dialog = await openBillDialog({
        projectName: "SYN-Roof Replacement — 12 Elm St",
      });

      expect(await dialog.findByText("SYN-Roof Replacement — 12 Elm St")).toBeInTheDocument();
    });

    it("explains that a stock bill's project link was ignored", async () => {
      const dialog = await openBillDialog({
        source: "FIELDCRAFT",
        isStockBill: true,
        stockReference: "PO-STOCK-77",
        projectName: null,
        syncStatus: "FAILED",
        reason: JSON.stringify({
          message: "Stock bill cannot be linked to a project in QuickBooks.",
        }),
      });

      expect(await dialog.findByText("Project link ignored")).toBeInTheDocument();
      expect(dialog.getByText("PO-STOCK-77")).toBeInTheDocument();
    });

    it("labels a plain stock bill and shows its stock reference", async () => {
      const dialog = await openBillDialog({
        source: "FIELDCRAFT",
        isStockBill: true,
        stockReference: "PO-STOCK-88",
        projectName: null,
        syncStatus: "SYNCED",
        reason: null,
      });

      expect(await dialog.findByText("Stock bill")).toBeInTheDocument();
      expect(dialog.getByText("PO-STOCK-88")).toBeInTheDocument();
      expect(dialog.queryByText("Project link ignored")).not.toBeInTheDocument();
    });

    it("asks the user to assign a project when the bill was imported without one", async () => {
      const dialog = await openBillDialog({
        source: "FIELDCRAFT",
        isStockBill: false,
        stockReference: null,
        projectName: null,
        syncStatus: "SYNCED_INCOMPLETE",
      });

      expect(await dialog.findByText("No project linked")).toBeInTheDocument();
      expect(
        dialog.getByText(/Assign it to the correct synced project in QuickBooks/i),
      ).toBeInTheDocument();
    });

    it("tells the user to sync projects first when that is what the failure says", async () => {
      const dialog = await openBillDialog({
        source: "FIELDCRAFT",
        isStockBill: false,
        stockReference: null,
        projectName: null,
        syncStatus: "SYNC_FAILED",
        reason: JSON.stringify({
          message: "Project is not mapped to a FIELDCRAFT project yet, so the bill sync stopped.",
        }),
      });

      expect(await dialog.findByText("Sync projects first")).toBeInTheDocument();
    });

    it("shows no project card at all when there is nothing useful to say", async () => {
      const dialog = await openBillDialog({
        source: "FIELDCRAFT",
        isStockBill: false,
        stockReference: null,
        projectName: null,
        syncStatus: "PENDING_PUSH",
        reason: null,
      });

      await dialog.findByText("Office Rent");
      expect(dialog.queryByText("No project linked")).not.toBeInTheDocument();
      expect(dialog.queryByText("Sync projects first")).not.toBeInTheDocument();
      expect(dialog.queryByText("Stock bill")).not.toBeInTheDocument();
    });

    it("does not blame projects for a failure that carries no reason", async () => {
      const dialog = await openBillDialog({
        source: "FIELDCRAFT",
        isStockBill: false,
        stockReference: null,
        projectName: null,
        syncStatus: "FAILED",
        reason: null,
      });

      await dialog.findByText("Office Rent");
      expect(dialog.queryByText("Sync projects first")).not.toBeInTheDocument();
    });

    it("does not blame projects for a failure unrelated to projects", async () => {
      const dialog = await openBillDialog({
        source: "FIELDCRAFT",
        isStockBill: false,
        stockReference: null,
        projectName: null,
        syncStatus: "IMPORT_FAILED",
        reason: JSON.stringify({ message: "Vendor is missing in QuickBooks." }),
      });

      await dialog.findByText("Office Rent");
      expect(dialog.queryByText("Sync projects first")).not.toBeInTheDocument();
    });
  });

  describe("bill line items and attachment rendering", () => {
    async function openBillDialogWith(lineItems: object[], documentInfo: object | null = null) {
      mockedGetQuickBooksBillLineItems.mockResolvedValue({ data: { lineItems } } as any);
      mockedGetQuickBooksBillDocumentInfo.mockResolvedValue({ data: documentInfo } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByText("Office Rent"));
      return within(await screen.findByRole("dialog"));
    }

    it("falls back to a placeholder for a line item with no description or code", async () => {
      const dialog = await openBillDialogWith([
        { amount: 120, quantity: 2, unitPrice: 60 },
        { description: "Lumber 2x4", amount: 300, quantity: 10, unitPrice: 30 },
      ]);

      expect(await dialog.findByText("Unnamed item")).toBeInTheDocument();
      expect(dialog.getByText("Lumber 2x4")).toBeInTheDocument();
    });

    it("names a line item by its product code when there is no description", async () => {
      const dialog = await openBillDialogWith([
        { productCode: "SKU-8891", amount: 45, quantity: 1, unitPrice: 45 },
      ]);

      expect(await dialog.findByText("SKU-8891")).toBeInTheDocument();
    });

    it("names the attachment card after the document when the backend supplies one", async () => {
      const dialog = await openBillDialogWith([], {
        hasDocument: true,
        mimeType: "image/png",
        name: "SYN-receipt.png",
        size: 4096,
      });

      // With a name, the card shows it instead of the generic "View receipt".
      fireEvent.click(await dialog.findByText("SYN-receipt.png"));
      await dialog.findByText("Hide receipt");

      const image = dialog.getByAltText("SYN-receipt.png") as HTMLImageElement;
      expect(image.getAttribute("src")).toBe("blob:test-document-url");
      expect(dialog.getByText("PNG")).toBeInTheDocument();
      expect(dialog.getByText("4.0 KB")).toBeInTheDocument();
    });

    it("previews a PDF attachment as an embedded document", async () => {
      const dialog = await openBillDialogWith([], {
        hasDocument: true,
        mimeType: "application/pdf",
        size: 8192,
      });

      fireEvent.click(await dialog.findByText("View receipt"));
      await dialog.findByText("Hide receipt");

      expect(dialog.getByText("PDF")).toBeInTheDocument();
      expect(dialog.getByText("Document")).toBeInTheDocument();
      expect(document.querySelector('embed[type="application/pdf"]')).not.toBeNull();
    });

    it("previews any other attachment type in a frame", async () => {
      const dialog = await openBillDialogWith([], {
        hasDocument: true,
        mimeType: "application/msword",
        size: 2048,
      });

      fireEvent.click(await dialog.findByText("View receipt"));
      await dialog.findByText("Hide receipt");

      expect(dialog.getByText("MSWORD")).toBeInTheDocument();
      expect(dialog.getByTitle("document")).toBeInTheDocument();
    });

    it("shows the attachment card as a skeleton while the lookup is in flight", async () => {
      let resolveDocInfo: (value: unknown) => void = () => {};
      mockedGetQuickBooksBillDocumentInfo.mockImplementation(
        () => new Promise((resolve) => { resolveDocInfo = resolve; }),
      );
      mockedGetQuickBooksBillLineItems.mockResolvedValue({ data: { lineItems: [] } } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByText("Office Rent"));

      const dialog = within(await screen.findByRole("dialog"));
      expect(await dialog.findByText("Attachment")).toBeInTheDocument();
      expect(dialog.queryByText("View receipt")).not.toBeInTheDocument();

      await act(async () => {
        resolveDocInfo({ data: null });
      });
    });
  });

  describe("bill detail loading edge cases", () => {
    it("ignores a click on a bill row that has no receipt id", async () => {
      mockedGetQuickBooksBillCdcView.mockResolvedValue({
        data: {
          ...defaultBillResponse.data,
          rows: [{ ...defaultBillResponse.data.rows[0], receiptId: null, displayName: "SYN-Orphan Bill" }],
        },
      } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByText("SYN-Orphan Bill"));

      await waitFor(() => expect(mockedGetQuickBooksBillLineItems).not.toHaveBeenCalled());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("still opens the dialog when the line-item lookup rejects", async () => {
      mockedGetQuickBooksBillLineItems.mockRejectedValue(new Error("Line items unavailable"));

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByText("Office Rent"));

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("still opens the dialog when the attachment lookup rejects", async () => {
      mockedGetQuickBooksBillDocumentInfo.mockRejectedValue(new Error("Document info unavailable"));

      renderComponent();
      await gotoModule("Bills", "Bills");
      fireEvent.click(await screen.findByText("Office Rent"));

      const dialog = await screen.findByRole("dialog");
      // No attachment card is offered when the lookup failed.
      expect(within(dialog).queryByText("View receipt")).not.toBeInTheDocument();
    });

    it("releases a previous preview URL when another bill is opened", async () => {
      if (!URL.revokeObjectURL) URL.revokeObjectURL = jest.fn();
      const revokeSpy = jest.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
      mockedGetQuickBooksBillDocumentInfo.mockResolvedValue({
        data: { hasDocument: true, mimeType: "application/pdf", size: 2048 },
      } as any);

      renderComponent();
      await gotoModule("Bills", "Bills");
      const row = await screen.findByText("Office Rent");

      fireEvent.click(row);
      await screen.findByRole("dialog");
      fireEvent.click(await screen.findByText("View receipt"));
      await screen.findByText("Hide receipt");

      revokeSpy.mockClear();
      // Re-opening a bill while a preview URL is still held must release it.
      fireEvent.click(row);

      await waitFor(() => expect(revokeSpy).toHaveBeenCalledWith("blob:test-document-url"));

      revokeSpy.mockRestore();
    });
  });

  // ── Service-role field-name variants ─────────────────────────────────────────

  describe("service role field-name variants", () => {
    it("reads snake_case service-role fields from the backend", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([
          {
            id: 301,
            role_name: "SYN-Snake Case Role",
            sync_status: "SYNCED",
            external_service_id: "QBO-SVC-301",
            additional_info: {
              qbo_service_income_account: { id: 10, name: "Services Income", externalAccountId: "qbo-ext-10" },
            },
          },
        ]) as any,
      );

      await renderConnectedServices();

      expect(await screen.findByText("SYN-Snake Case Role")).toBeInTheDocument();
      expect(screen.getByText("QBO-SVC-301")).toBeInTheDocument();
      expect(screen.getByText("Services Income")).toBeInTheDocument();
    });

    it("treats an unparseable additionalInfo string as no COA assignment", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([
          {
            id: 302,
            roleName: "SYN-Broken Info Role",
            syncStatus: "PENDING_PUSH",
            externalServiceId: null,
            additionalInfo: "{not-valid-json",
          },
        ]) as any,
      );

      await renderConnectedServices();
      await screen.findByText("SYN-Broken Info Role");

      expect(screen.getByText("No COA assigned")).toBeInTheDocument();
      expect(screen.getByText("Not linked")).toBeInTheDocument();
    });

    it("reads a COA stored as a JSON string in additionalInfo", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([
          {
            id: 303,
            roleName: "SYN-String Info Role",
            syncStatus: "PENDING_PUSH",
            additionalInfo: JSON.stringify({
              qbo_service_income_account: {
                id: 10, name: "Services Income", account_type: "Income",
                detail_type: "ServiceFeeIncome", external_account_id: "qbo-ext-10",
              },
            }),
          },
        ]) as any,
      );

      await renderConnectedServices();
      await screen.findByText("SYN-String Info Role");

      expect(screen.getByText("Services Income")).toBeInTheDocument();
      expect(screen.getByText("Income • ServiceFeeIncome")).toBeInTheDocument();
    });

    it("shows a service-role failure reason from JSON and from plain text", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([
          {
            id: 304, roleName: "SYN-JSON Reason Role", syncStatus: "FAILED", additionalInfo: null,
            reason: JSON.stringify({
              message: "Income account is required.",
              action_required: "assign_coa",
            }),
          },
          {
            id: 305, roleName: "SYN-Text Reason Role", syncStatus: "FAILED", additionalInfo: null,
            reason: "QuickBooks rejected the service item",
          },
        ]) as any,
      );

      await renderConnectedServices();
      await screen.findByText("SYN-JSON Reason Role");

      expect(screen.getByLabelText("Details: Income account is required.")).toBeInTheDocument();
      expect(
        screen.getByLabelText("Details: QuickBooks rejected the service item"),
      ).toBeInTheDocument();
    });

    it("ignores selection for a service role with no usable id", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([
          { id: null, roleId: null, roleName: "SYN-Idless Role", syncStatus: "PENDING_PUSH", additionalInfo: null },
        ]) as any,
      );

      await renderConnectedServices();
      await screen.findByText("SYN-Idless Role");

      const rowCheckbox = screen.getAllByRole("checkbox").find((cb) => !cb.closest("thead"));
      fireEvent.click(rowCheckbox as HTMLElement);

      // Nothing selectable means no bulk-assign affordance appears.
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /assign coa/i })).not.toBeInTheDocument(),
      );
    });
  });

  // ── Remaining module configuration paths ─────────────────────────────────────

  describe("module configuration", () => {
    it("adopts the stored project sync method returned by the backend", async () => {
      mockedGetQuickBooksProjectSyncConfig.mockResolvedValue({
        ok: true,
        data: { method: "QBO_PROJECT", projectsAvailable: true, configured: true },
      } as any);

      await renderConnectedProjects();
      await screen.findByText("Roof Replacement — 12 Elm St");

      // The chooser button is labelled with the effective method.
      expect(await screen.findByRole("button", { name: /quickbooks project/i })).toBeInTheDocument();
    });

    it("shows an error when the eligible payment accounts cannot be loaded", async () => {
      mockedGetQuickBooksBillPaymentAccounts.mockRejectedValue(
        new Error("Unable to load eligible QuickBooks payment accounts."),
      );

      await renderConnectedBillPayments();

      expect(
        await screen.findByText("Unable to load eligible QuickBooks payment accounts."),
      ).toBeInTheDocument();
    });

    it("shows an error when assigning a bill payment account fails", async () => {
      mockedAssignQuickBooksBillPaymentAccount.mockRejectedValue(
        new Error("Assignment rejected by QuickBooks."),
      );

      await renderConnectedBillPayments();
      await screen.findByText("Payment to Northwind");

      const rowCheckbox = screen.getAllByRole("checkbox").find((cb) => !cb.closest("thead"));
      fireEvent.click(rowCheckbox as HTMLElement);
      fireEvent.click(await screen.findByRole("button", { name: /assign account \(1\)/i }));

      const dialog = await screen.findByRole("dialog");
      const selectTrigger = document.getElementById("quick-books-table-select-quickbooks-payment-account");
      fireEvent.mouseDown(selectTrigger as HTMLElement);
      fireEvent.click(await screen.findByText("Business Checking"));
      fireEvent.click(within(dialog).getByRole("button", { name: /^assign account$/i }));

      expect(await screen.findByText("Assignment rejected by QuickBooks.")).toBeInTheDocument();
    });

    it("does nothing when Assign account is confirmed without picking an account", async () => {
      await renderConnectedBillPayments();
      await screen.findByText("Payment to Northwind");

      const rowCheckbox = screen.getAllByRole("checkbox").find((cb) => !cb.closest("thead"));
      fireEvent.click(rowCheckbox as HTMLElement);
      fireEvent.click(await screen.findByRole("button", { name: /assign account \(1\)/i }));

      const dialog = await screen.findByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: /^assign account$/i }));

      await waitFor(() =>
        expect(mockedAssignQuickBooksBillPaymentAccount).not.toHaveBeenCalled(),
      );
    });
  });

  // ── Backend field-name variants across module responses ──────────────────────

  describe("alternative backend response shapes", () => {
    it("reads flat pagination fields and a rows key for projects", async () => {
      mockedGetQuickBooksProjectCdcView.mockResolvedValue({
        data: {
          summary: { total: 1, synced: 1, pending: 0, failed: 0 },
          page: 1,
          pageSize: 10,
          totalRecords: 1,
          totalPages: 1,
          rows: [{ ...defaultProjectResponse.data.records[0], displayName: "SYN-Flat Project" }],
        },
      } as any);

      await renderConnectedProjects();

      expect(await screen.findByText("SYN-Flat Project")).toBeInTheDocument();
    });

    it("reads flat pagination fields and a rows key for time activity", async () => {
      mockedGetQuickBooksTimelogCdcView.mockResolvedValue({
        data: {
          summary: { total: 1, synced: 0, pending: 1, failed: 0, notReady: 0 },
          page: 1,
          pageSize: 10,
          totalRecords: 1,
          totalPages: 1,
          rows: [{ ...defaultTimelogResponse.data.records[0], taskName: "SYN-Flat Timelog" }],
        },
      } as any);

      await renderConnectedTimelogs();

      expect(await screen.findByText("SYN-Flat Timelog")).toBeInTheDocument();
    });

    it("normalises the remaining service-role summary aliases", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue({
        data: {
          page: 1,
          pageSize: 10,
          totalRecords: 1,
          totalPages: 1,
          rows: [{ id: 401, roleName: "SYN-Alias Role", syncStatus: "SYNC_FAILED", additionalInfo: null }],
          summary: { totalRoles: 4, syncedRoles: 1, pendingRoles: 2, syncFailed: 1 },
        },
      } as any);

      await renderConnectedServices();
      await screen.findByText("SYN-Alias Role");

      // The pills read straight off the normalised summary.
      expect(screen.getByRole("button", { name: /all\s*4/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /needs attention\s*1/i })).toBeInTheDocument();
    });

    it("falls back to a placeholder when a service role has no name at all", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([{ id: 402, syncStatus: "PENDING_PUSH", additionalInfo: null }]) as any,
      );

      await renderConnectedServices();

      expect(await screen.findByText("Unnamed role")).toBeInTheDocument();
    });

    it("names a service role from the generic name field", async () => {
      mockedGetQuickBooksServiceRoleCdcView.mockResolvedValue(
        makeSrResponse([
          { id: 403, name: "SYN-Generic Name Role", syncStatus: "PENDING_PUSH", additionalInfo: null },
        ]) as any,
      );

      await renderConnectedServices();

      expect(await screen.findByText("SYN-Generic Name Role")).toBeInTheDocument();
    });

    it("labels an employee whose active flag is missing as Unknown", async () => {
      mockedGetQuickBooksEmployeeCdcView.mockResolvedValue({
        data: {
          ...defaultEmployeeResponse.data,
          rows: [
            { ...defaultEmployeeResponse.data.rows[0], employeeId: 8201, displayName: "SYN-Unflagged Employee", active: null },
          ],
        },
      } as any);

      await renderConnectedEmployees();
      await screen.findByText("SYN-Unflagged Employee");

      expect(screen.getByText("Unknown")).toBeInTheDocument();
    });

    it("still shows the linked bill when a bill payment has no vendor company", async () => {
      mockedGetQuickBooksBillPaymentCdcView.mockResolvedValue({
        data: {
          ...defaultBillPaymentResponse.data,
          rows: [
            {
              ...defaultBillPaymentResponse.data.rows[0],
              paymentExpenseId: 5101,
              displayName: "SYN-Payment Without Vendor",
              vendorCompany: null,
              billDisplayName: "SYN-Bill 5101",
            },
          ],
        },
      } as any);

      await renderConnectedBillPayments();
      await screen.findByText("SYN-Payment Without Vendor");

      expect(screen.getByText("SYN-Bill 5101")).toBeInTheDocument();
    });

    it("builds a reconnect state even when the failure carries no authError block", async () => {
      mockedGetQuickBooksStatus.mockRejectedValue({
        response: {
          data: {
            error: "invalid_grant",
            message: "The QuickBooks refresh token is no longer valid.",
          },
        },
      });

      renderComponent();

      expect(await screen.findByText("Sign in again to keep syncing")).toBeInTheDocument();
    });
  });

  // ── Legacy module cards (kept mounted but visually hidden) ────────────────────

  it("still switches modules from the legacy module card grid", async () => {
    await renderConnectedDashboard();

    fireEvent.click(screen.getByTestId("qb-module-card-bills"));

    expect(await screen.findByRole("heading", { name: "Bills" })).toBeInTheDocument();
  });
});

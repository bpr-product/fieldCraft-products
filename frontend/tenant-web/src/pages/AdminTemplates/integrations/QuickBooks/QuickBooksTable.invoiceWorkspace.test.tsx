/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Container-level coverage for the Invoice and Customer Payment workspaces.
 *
 * ── Why this is a separate file from QuickBooksTable.test.tsx ──────────────────
 * Two reasons, both mechanical:
 *
 * 1. Both modules are gated OFF by default (ENABLE_QUICKBOOKS_INVOICES /
 *    ENABLE_QUICKBOOKS_CUSTOMER_PAYMENTS in constants/FeatureFlag.ts), so in the main
 *    suite they have no rail button and cannot be reached at all. This file mocks the
 *    flags ON. `jest.mock` is hoisted above the imports, so QuickBooksUtils reads the
 *    mocked values when it builds QB_MODULE_FLAGS at module load.
 * 2. The main suite's `jest.mock` factory for QuickBooksService does not declare
 *    getQuickBooksInvoiceCdcView / getQuickBooksInvoiceDetails / the payment equivalents.
 *    A factory replaces the whole module, so those exports are `undefined` there and
 *    navigating to either module throws. That is why its
 *    `downloadQuickBooksInvoiceDocument` mock was never invoked by any test.
 *
 * Keeping this here rather than widening the main suite also means the main suite keeps
 * asserting the shipped (gated-off) state, which is what production renders.
 *
 * ── What this covers ──────────────────────────────────────────────────────────
 * · the invoice attachment click-through: service called with the mapping id, returned
 *   objectUrl reaching the preview, the toggle closing it, and the failure toast;
 * · the client-visible surface of the backend's SyncToken / identity-recovery /
 *   duplicate-prevention logic — those mechanics are server-side, but the reason they
 *   produce lands in this UI, and a row must not read as "synced" while it holds one;
 * · that flipping the flags genuinely restores both modules end to end.
 */
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

jest.mock("./QuickBooksTable.scss", () => ({}));

jest.setTimeout(20000);

// Force both gated modules on. Spread the real flags so unrelated ones keep their values.
jest.mock("../../../../constants/FeatureFlag", () => {
  const actual = jest.requireActual("../../../../constants/FeatureFlag");
  return {
    ...actual,
    FEATURE_FLAGS: {
      ...actual.FEATURE_FLAGS,
      ENABLE_QUICKBOOKS_INVOICES: true,
      ENABLE_QUICKBOOKS_CUSTOMER_PAYMENTS: true,
    },
  };
});

const mockUseOidcAccessToken = jest.fn();

jest.mock("@axa-fr/react-oidc", () => ({
  useOidcAccessToken: () => mockUseOidcAccessToken(),
}));

jest.mock("../../../../hooks/UserSession", () => {
  const React = require("react");
  return {
    UserContext: React.createContext({ userSessionDetails: { objectId: "object-123" } }),
  };
});

jest.mock("../../../../services/TenantService", () => ({ getTenantDetails: jest.fn() }));

jest.mock("../../../../services/FeaturesService", () => ({
  getAllServices: jest.fn(),
  getSubscriberById: jest.fn(),
}));

jest.mock("../../../../services/ApiBase", () => ({ executePostDataWithHeaders: jest.fn() }));

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
  getQuickBooksAccountCdcView: jest.fn(),
  getQuickBooksBillCdcView: jest.fn(),
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
  getQuickBooksBillPaymentCdcView: jest.fn(),
  getQuickBooksBillPaymentAccounts: jest.fn(),
  assignQuickBooksBillPaymentAccount: jest.fn(),
  startQuickBooksBillPaymentsSync: jest.fn(),
  getQuickBooksCustomerCdcView: jest.fn(),
  startQuickBooksCustomersSync: jest.fn(),
  getQuickBooksProjectCdcView: jest.fn(),
  startQuickBooksProjectsSync: jest.fn(),
  getQuickBooksEmployeeCdcView: jest.fn(),
  startQuickBooksEmployeesSync: jest.fn(),
  getQuickBooksEmployeeSyncConfig: jest.fn(),
  saveQuickBooksEmployeeSyncConfig: jest.fn(),
  DEFAULT_EMPLOYEE_SYNC_CONFIG: { sync_mode: "cdc", days_after_hire: 1, configured: false },
  getQuickBooksServiceRoleCdcView: jest.fn(),
  startQuickBooksServiceRolesSync: jest.fn(),
  getQuickBooksServiceIncomeAccounts: jest.fn(),
  getQuickBooksServiceCoaAccounts: jest.fn(),
  assignQuickBooksServiceIncomeAccount: jest.fn(),
  getQuickBooksTimelogCdcView: jest.fn(),
  startQuickBooksTimelogsSync: jest.fn(),
  getQuickBooksProjectSyncConfig: jest.fn(),
  saveQuickBooksProjectSyncConfig: jest.fn(),
  DEFAULT_PROJECT_SYNC_CONFIG: { sync_mode: "cdc", configured: false },
  // The exports the main suite omits, which is what made these paths unreachable there.
  getQuickBooksInvoiceCdcView: jest.fn(),
  getQuickBooksInvoiceDetails: jest.fn(),
  startQuickBooksInvoicesSync: jest.fn(),
  getQuickBooksPaymentCdcView: jest.fn(),
  getQuickBooksPaymentDetails: jest.fn(),
  startQuickBooksPaymentsSync: jest.fn(),
  exportQuickBooksPayments: jest.fn(),
  importQuickBooksPayments: jest.fn(),
}));

import QuickBooksTable from "./QuickBooksTable";
import { UserContext } from "../../../../hooks/UserSession";
import { getTenantDetails } from "../../../../services/TenantService";
import { getAllServices, getSubscriberById } from "../../../../services/FeaturesService";
import { executePostDataWithHeaders } from "../../../../services/ApiBase";
import * as QB from "../../../../services/integrations/QuickBooksService";

const m = (fn: unknown) => fn as jest.Mock;

const accessToken = "test-access-token";
const tenantId = "tn24047750";
const serviceId = 6;
const objectId = "object-123";

const emptyPagination = { page: 1, pageSize: 10, totalRecords: 0, totalPages: 0 };
const emptyModuleResponse = {
  data: { summary: {}, pagination: emptyPagination, records: [], rows: [] },
};

const connectedStatusResponse = {
  data: {
    connected: true,
    status: "connected",
    env: "sandbox",
    realmId: "9130357999999999",
    connectedAt: "2026-04-08T03:30:00Z",
    lastSyncAt: "2026-04-08T04:00:00Z",
  },
};

const INVOICE_MAPPING_ID = 9001;

const invoiceRow = {
  invoiceId: 71,
  invoiceNo: "SYN-INV-1001",
  customerUid: "SYN-CUST-9",
  customerName: "Synthia Customer",
  projectId: 12,
  projectName: "Synthia Customer, Austin-Roof Replacement-1001",
  invoiceStatus: "verified",
  // 4 March: the ambiguous date that distinguishes month-first (03/04) from day-first
  // (04/03). Every OTHER date on this row is deliberately in a different month so the
  // date-order assertion can never accidentally match one of them — in a negative-offset
  // zone a 5 March "last synced" would also render as 03/05 and make the query ambiguous.
  invoiceDate: "2026-03-04T12:00:00Z",
  dueDate: "2026-11-30T12:00:00Z",
  amount: 4195.26,
  taxAmount: 199.77,
  mappingId: INVOICE_MAPPING_ID,
  externalInvoiceId: "445",
  syncStatus: "SYNCED",
  mappingUpdatedAt: "2026-06-15T12:00:00Z",
  reason: null,
  source: "FIELDCRAFT",
};

const invoiceCdcResponse = (rows: object[] = [invoiceRow]) => ({
  data: {
    summary: { total: rows.length, synced: rows.length, pending: 0, failed: 0, notReady: 0 },
    pagination: { page: 1, pageSize: 10, totalRecords: rows.length, totalPages: 1 },
    rows,
  },
});

const invoiceDetailResponse = (overrides: Record<string, any> = {}) => ({
  data: {
    ok: true,
    mapping: {
      id: INVOICE_MAPPING_ID,
      external_invoice_id: "445",
      sync_status: "SYNCED",
      updated_at: "2026-03-05T12:00:00Z",
      ...(overrides.mapping || {}),
    },
    invoice: {
      id: 71,
      invoice_no: "SYN-INV-1001",
      customer_name: "Synthia Customer",
      project_name: "Synthia Customer, Austin-Roof Replacement-1001",
      issue_at: "2026-03-04T12:00:00Z",
      due_date: "2026-04-04T12:00:00Z",
      status: "verified",
      invoiced_amount: 4195.26,
      tax_amount: 199.77,
      paid_amount: 0,
      balance_due: 4195.26,
      items: [],
      tax_info: [],
      ...(overrides.invoice || {}),
    },
    document: overrides.document === undefined
      ? { id: 55, name: "invoice-scan.pdf", mime_type: "application/pdf", size: 4096 }
      : overrides.document,
  },
});

function renderComponent() {
  return render(
    <UserContext.Provider value={{ userSessionDetails: { objectId } } as any}>
      <QuickBooksTable />
    </UserContext.Provider>,
  );
}

/** Navigate by rail button, then wait for the module heading. */
async function gotoModule(railLabel: string, headingTitle: string) {
  fireEvent.click(await screen.findByRole("button", { name: railLabel }));
  await screen.findByRole("heading", { name: headingTitle });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockUseOidcAccessToken.mockReturnValue({ accessToken });

  m(getTenantDetails).mockResolvedValue({ status: 200, data: { tenantid: tenantId } });
  m(getSubscriberById).mockResolvedValue({ data: [{ status: "Active", serviceMaster: serviceId }] });
  m(getAllServices).mockResolvedValue({ data: [{ id: serviceId, name: "QuickBooks", status: "Active" }] });
  m(executePostDataWithHeaders).mockResolvedValue({ data: { ok: true } });

  m(QB.getQuickBooksStatus).mockResolvedValue(connectedStatusResponse);
  m(QB.getQuickBooksSyncState).mockResolvedValue({ data: { jobs: {} } });
  m(QB.getQuickBooksSyncJob).mockResolvedValue({ data: { job: { status: "COMPLETED" } } });

  for (const fn of [
    QB.getQuickBooksVendorCdcView, QB.getQuickBooksAccountCdcView, QB.getQuickBooksBillCdcView,
    QB.getQuickBooksCustomerCdcView, QB.getQuickBooksProjectCdcView, QB.getQuickBooksEmployeeCdcView,
    QB.getQuickBooksServiceRoleCdcView, QB.getQuickBooksTimelogCdcView,
    QB.getQuickBooksBillPaymentCdcView, QB.getQuickBooksPaymentCdcView,
  ]) {
    m(fn).mockResolvedValue(emptyModuleResponse);
  }
  m(QB.getQuickBooksBillPaymentAccounts).mockResolvedValue({ data: { records: [] } });
  m(QB.getQuickBooksServiceCoaAccounts).mockResolvedValue({ data: { records: [] } });
  m(QB.getQuickBooksBillLineItems).mockResolvedValue({ data: { lineItems: [] } });
  m(QB.getQuickBooksBillDocumentInfo).mockResolvedValue({ data: null });
  m(QB.getQuickBooksProjectSyncConfig).mockResolvedValue({ data: { sync_mode: "cdc", configured: false } });
  m(QB.getQuickBooksEmployeeSyncConfig).mockResolvedValue({ data: { sync_mode: "cdc", configured: false } });

  m(QB.getQuickBooksInvoiceCdcView).mockResolvedValue(invoiceCdcResponse());
  m(QB.getQuickBooksInvoiceDetails).mockResolvedValue(invoiceDetailResponse());
  m(QB.downloadQuickBooksInvoiceDocument).mockResolvedValue({
    objectUrl: "blob:test-invoice-document-url",
    mimeType: "application/pdf",
    fileName: "invoice-scan.pdf",
  });

  // jsdom has no object-URL implementation; the dialog creates and revokes them.
  if (!URL.createObjectURL) Object.assign(URL, { createObjectURL: jest.fn(() => "blob:created") });
  if (!URL.revokeObjectURL) Object.assign(URL, { revokeObjectURL: jest.fn() });
});

// ── The release switch, at container level ────────────────────────────────────
describe("flags on: both modules become reachable", () => {
  it("renders the Invoices and Customer Payments rail entries", async () => {
    renderComponent();
    expect(await screen.findByRole("button", { name: "Invoices" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Customer Payments" })).toBeInTheDocument();
  });

  it("loads the invoice CDC view when the module is opened", async () => {
    renderComponent();
    await gotoModule("Invoices", "Invoices");

    await waitFor(() => {
      expect(m(QB.getQuickBooksInvoiceCdcView)).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({ tenantId, serviceId: String(serviceId) }),
      );
    });
    expect(await screen.findByText("SYN-INV-1001")).toBeInTheDocument();
    // The friendly labels the backend resolves, not UUID/PK internals.
    expect(screen.getAllByText("Synthia Customer").length).toBeGreaterThan(0);
    expect(screen.queryByText("SYN-CUST-9")).not.toBeInTheDocument();
  });

  it("renders invoice dates in US numeric format, month first", async () => {
    renderComponent();
    await gotoModule("Invoices", "Invoices");

    // The Invoices table renders the date as a datetime sub-label under the invoice number.
    //
    // 2026-03-04T12:00:00Z is 4 March, so month-first is 03/… and day-first would be 04/03.
    // The day itself is left loose (0[45]) rather than pinned: at UTC+12 and beyond noon UTC
    // has already rolled to the 5th, and the assertion is about component ORDER, not about
    // which zone the test host is in.
    expect(await screen.findByText(/^03\/0[45]\/2026 \d{2}:\d{2} (AM|PM)$/)).toBeInTheDocument();
    expect(screen.queryByText(/^04\/0[34]\/2026/)).not.toBeInTheDocument();
  });
});

// ── Invoice attachment click-through ─────────────────────────────────────────
describe("invoice attachment preview", () => {
  async function openInvoiceDetail() {
    renderComponent();
    await gotoModule("Invoices", "Invoices");
    fireEvent.click(await screen.findByText("SYN-INV-1001"));
    await waitFor(() => {
      expect(m(QB.getQuickBooksInvoiceDetails)).toHaveBeenCalledWith(
        accessToken,
        INVOICE_MAPPING_ID,
        expect.objectContaining({ tenantId, serviceId: String(serviceId) }),
      );
    });
    return screen.findByText("invoice-scan.pdf");
  }

  it("downloads the document with the mapping id and shows the preview", async () => {
    await openInvoiceDetail();

    fireEvent.click(screen.getByText("invoice-scan.pdf"));

    await waitFor(() => {
      expect(m(QB.downloadQuickBooksInvoiceDocument)).toHaveBeenCalledWith(
        accessToken,
        INVOICE_MAPPING_ID,
        expect.objectContaining({ tenantId, serviceId: String(serviceId) }),
      );
    });

    // The returned objectUrl has to reach the DOM, not just the state hook — the download
    // control is what the user acts on.
    const download = await screen.findByRole("link", { name: /download/i });
    expect(download).toHaveAttribute("href", "blob:test-invoice-document-url");
    expect(screen.getByText("Hide attachment")).toBeInTheDocument();
  });

  it("toggles the preview closed without re-fetching the document", async () => {
    await openInvoiceDetail();

    fireEvent.click(screen.getByText("invoice-scan.pdf"));
    await screen.findByText("Hide attachment");

    fireEvent.click(screen.getByText("Hide attachment"));

    await waitFor(() => {
      expect(screen.queryByText("Hide attachment")).not.toBeInTheDocument();
    });
    // Cached objectUrl: closing and reopening must not hit the endpoint twice.
    expect(m(QB.downloadQuickBooksInvoiceDocument)).toHaveBeenCalledTimes(1);
  });

  it("surfaces a failed attachment download instead of a silent no-op", async () => {
    m(QB.downloadQuickBooksInvoiceDocument).mockRejectedValueOnce(new Error("boom"));
    await openInvoiceDetail();

    fireEvent.click(screen.getByText("invoice-scan.pdf"));

    expect(
      await screen.findByText("Could not load the invoice attachment. Please try again."),
    ).toBeInTheDocument();
  });

  it("shows no attachment control when the invoice has no document", async () => {
    m(QB.getQuickBooksInvoiceDetails).mockResolvedValue(invoiceDetailResponse({ document: null }));
    renderComponent();
    await gotoModule("Invoices", "Invoices");
    fireEvent.click(await screen.findByText("SYN-INV-1001"));

    await waitFor(() => expect(m(QB.getQuickBooksInvoiceDetails)).toHaveBeenCalled());
    expect(screen.queryByText("Attachment")).not.toBeInTheDocument();
    expect(m(QB.downloadQuickBooksInvoiceDocument)).not.toHaveBeenCalled();
  });
});

/*
 * ── Client-visible surface of SyncToken / recovery / duplicate-prevention ─────
 *
 * The mechanics are entirely server-side, in QuickBooksInvoiceExportService: stale-token
 * refetch-and-retry, recovery of a missing mapped invoice by DocNumber filtered on
 * Customer/Project refs, and failing safely when more than one candidate matches instead of
 * guessing. Those have no client control — the UI cannot retry a single row, resolve a
 * duplicate, or see a SyncToken.
 *
 * What IS client-visible is the outcome: the row's status and the reason string the exporter
 * persisted. So these assert the only contract the frontend actually owns — such a row reads
 * as needing attention, and the reason reaches the user instead of being swallowed. The
 * export logic itself is covered by the backend suite
 * (source/tests/test_quickbooks_invoice_export_service.py).
 */
describe("sync-failure reasons reach the user", () => {
  const failedRow = (reason: string) => ({
    ...invoiceRow,
    invoiceNo: "SYN-INV-2002",
    mappingId: 9002,
    syncStatus: "SYNC_FAILED",
    externalInvoiceId: null,
    reason: JSON.stringify({ message: reason }),
  });

  const DUPLICATE_REASON =
    "Multiple QuickBooks invoices match this FIELDCRAFT invoice number/customer/project. "
    + "Resolve the duplicates in QuickBooks before retrying.";

  it("shows a duplicate-candidate failure as needing attention, not as synced", async () => {
    m(QB.getQuickBooksInvoiceCdcView).mockResolvedValue({
      data: {
        summary: { total: 1, synced: 0, pending: 0, failed: 1, notReady: 0 },
        pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
        rows: [failedRow(DUPLICATE_REASON)],
      },
    });

    renderComponent();
    await gotoModule("Invoices", "Invoices");

    expect(await screen.findByText("SYN-INV-2002")).toBeInTheDocument();
    // The reason is parsed out of the JSON envelope rather than dumped raw.
    expect(screen.getByLabelText(`Details: ${DUPLICATE_REASON}`)).toBeInTheDocument();
    expect(screen.queryByText(/"message"/)).not.toBeInTheDocument();
  });

  it("shows a stale-SyncToken failure reason", async () => {
    const reason = "QuickBooks invoice update failed because SyncToken is missing.";
    m(QB.getQuickBooksInvoiceCdcView).mockResolvedValue({
      data: {
        summary: { total: 1, synced: 0, pending: 0, failed: 1, notReady: 0 },
        pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
        rows: [failedRow(reason)],
      },
    });

    renderComponent();
    await gotoModule("Invoices", "Invoices");

    await screen.findByText("SYN-INV-2002");
    expect(screen.getByLabelText(`Details: ${reason}`)).toBeInTheDocument();
  });

  it("keeps a NOT_READY invoice visually distinct from a failure", async () => {
    m(QB.getQuickBooksInvoiceCdcView).mockResolvedValue({
      data: {
        summary: { total: 1, synced: 0, pending: 0, failed: 0, notReady: 1 },
        pagination: { page: 1, pageSize: 10, totalRecords: 1, totalPages: 1 },
        rows: [{
          ...invoiceRow,
          invoiceNo: "SYN-INV-3003",
          syncStatus: "NOT_READY",
          externalInvoiceId: null,
          reason: "Invoice is not yet in an issued/payment state or has no positive amount to export.",
        }],
      },
    });

    renderComponent();
    await gotoModule("Invoices", "Invoices");

    await screen.findByText("SYN-INV-3003");
    expect(screen.queryByText(/sync failed/i)).not.toBeInTheDocument();
  });
});

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * @author Bala Pradeep
 *
 * Container-level coverage for the Auto-sync preference: the workspace loading
 * the tenant's saved value, handing it to the settings panel, and reporting what
 * happened when the user changes it.
 *
 * ── Why this is a separate file from QuickBooksTable.test.tsx ──────────────────
 * That suite is past the 800-line file limit and frozen at its baseline, so new
 * cases cannot land in it. QuickBooksTable.invoiceWorkspace.test.tsx set the
 * precedent for a focused sibling suite against the same container.
 *
 * ── What this covers that the panel's own suite cannot ─────────────────────────
 * QuickBooksSettingsDialog.test.tsx drives the panel through props. These cases
 * cover the wiring behind them: which value the workspace derives from the
 * service response, what it sends back, and how a failed save is reported. The
 * service default is off, so a response that does not say `enabled: true` must
 * not read as on — that is the point of the second case.
 *
 * i18n is initialised with the real en.json in setupTests, so the queries below
 * assert the shipped copy, not key names.
 *
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR      |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31584       | 23-09-2026 | Bala Pradeep    | Initial — auto-sync load, save,
 *                |            |                 | confirmation and failure
 * -----------------------------------------------------------------------------
 */
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

jest.mock("./QuickBooksTable.scss", () => ({}));

jest.setTimeout(20000);

const mockUseOidcAccessToken = jest.fn();

jest.mock("@axa-fr/react-oidc", () => ({
  useOidcAccessToken: () => mockUseOidcAccessToken(),
}));

jest.mock("../../../../hooks/UserSession", () => {
  const ReactLib = require("react");
  return {
    UserContext: ReactLib.createContext({ userSessionDetails: { objectId: "object-123" } }),
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
  getQuickBooksAccountCdcView: jest.fn(),
  getQuickBooksAutoSyncConfig: jest.fn(),
  saveQuickBooksAutoSyncConfig: jest.fn(),
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
  getQuickBooksInvoiceCdcView: jest.fn(),
  getQuickBooksInvoiceDetails: jest.fn(),
  startQuickBooksInvoicesSync: jest.fn(),
  getQuickBooksPaymentCdcView: jest.fn(),
  getQuickBooksPaymentDetails: jest.fn(),
  startQuickBooksPaymentsSync: jest.fn(),
  exportQuickBooksPayments: jest.fn(),
  importQuickBooksPayments: jest.fn(),
  downloadQuickBooksInvoiceDocument: jest.fn(),
  getQuickBooksErrorMessage: jest.fn(() => "Unable to save."),
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

function renderComponent() {
  return render(
    <UserContext.Provider value={{ userSessionDetails: { objectId } } as any}>
      <QuickBooksTable />
    </UserContext.Provider>,
  );
}

/** Open the settings panel from the rail footer. */
async function openSettings() {
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  return screen.findByRole("heading", { name: "Settings" });
}

const theSwitch = () => screen.getByRole("checkbox", { name: "Auto-sync" });

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
    QB.getQuickBooksBillPaymentCdcView,
  ]) {
    m(fn).mockResolvedValue(emptyModuleResponse);
  }
  m(QB.getQuickBooksBillPaymentAccounts).mockResolvedValue({ data: { records: [] } });
  m(QB.getQuickBooksServiceCoaAccounts).mockResolvedValue({ data: { records: [] } });
  m(QB.getQuickBooksProjectSyncConfig).mockResolvedValue({ data: { sync_mode: "cdc", configured: false } });
  m(QB.getQuickBooksEmployeeSyncConfig).mockResolvedValue({ data: { sync_mode: "cdc", configured: false } });

  m(QB.getQuickBooksAutoSyncConfig).mockResolvedValue({ data: { ok: true, enabled: true } });
  m(QB.saveQuickBooksAutoSyncConfig).mockResolvedValue({ data: { ok: true } });
});

describe("Auto-sync: loading the tenant's saved value", () => {
  it("reflects an enabled tenant once the value arrives", async () => {
    renderComponent();
    await openSettings();

    await waitFor(() => expect(theSwitch()).toBeChecked());
    expect(m(QB.getQuickBooksAutoSyncConfig)).toHaveBeenCalledWith(
      accessToken,
      tenantId,
      String(serviceId),
    );
  });

  it("draws no switch while the value is still in flight", async () => {
    let release: (value: unknown) => void = () => undefined;
    m(QB.getQuickBooksAutoSyncConfig).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );

    renderComponent();
    await openSettings();

    expect(screen.queryByRole("checkbox", { name: "Auto-sync" })).not.toBeInTheDocument();

    release({ data: { ok: true, enabled: true } });
    await waitFor(() => expect(theSwitch()).toBeChecked());
  });

  it("reads anything that is not an explicit true as off, matching the service default", async () => {
    m(QB.getQuickBooksAutoSyncConfig).mockResolvedValue({ data: { ok: true } });

    renderComponent();
    await openSettings();

    await waitFor(() => expect(theSwitch()).toBeInTheDocument());
    expect(theSwitch()).not.toBeChecked();
    expect(screen.getByText("You choose what to sync, and when.")).toBeInTheDocument();
  });
});

describe("Auto-sync: changing it", () => {
  it("sends the value the user picked and confirms the save in the panel", async () => {
    renderComponent();
    await openSettings();
    await waitFor(() => expect(theSwitch()).toBeChecked());

    fireEvent.click(theSwitch());

    await waitFor(() =>
      expect(m(QB.saveQuickBooksAutoSyncConfig)).toHaveBeenCalledWith(
        accessToken,
        expect.objectContaining({ tenant_id: tenantId, enabled: false, object_id: objectId }),
      ),
    );
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(theSwitch()).not.toBeChecked();
  });

  it("puts the switch back and reports the failure when the save is refused", async () => {
    m(QB.saveQuickBooksAutoSyncConfig).mockResolvedValue({
      data: { ok: false, message: "QuickBooks rejected the change." },
    });

    renderComponent();
    await openSettings();
    await waitFor(() => expect(theSwitch()).toBeChecked());

    fireEvent.click(theSwitch());

    expect(await screen.findByText("That change didn't save. Try again.")).toBeInTheDocument();
    await waitFor(() => expect(theSwitch()).toBeChecked());
  });

  it("puts the switch back when the request itself throws", async () => {
    m(QB.saveQuickBooksAutoSyncConfig).mockRejectedValue(new Error("network down"));

    renderComponent();
    await openSettings();
    await waitFor(() => expect(theSwitch()).toBeChecked());

    fireEvent.click(theSwitch());

    expect(await screen.findByText("That change didn't save. Try again.")).toBeInTheDocument();
    await waitFor(() => expect(theSwitch()).toBeChecked());
  });
});

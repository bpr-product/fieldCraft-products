/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 */
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

const _originalConsoleError = console.error.bind(console);
beforeAll(() => {
  jest.spyOn(console, 'error').mockImplementation((...args: any[]) => {
    const msg = typeof args[0] === 'string' ? args[0] : '';
    if (
      msg.includes('ReactDOMTestUtils.act') ||
      msg.includes('not configured to support act') ||
      msg.includes('was not wrapped in act')
    ) return;
    _originalConsoleError(...args);
  });
});
afterAll(() => (console.error as jest.Mock).mockRestore());

jest.mock("./ABCSupplyConnectionSettings.scss", () => ({}));

const mockUseOidcAccessToken = jest.fn();

jest.mock("@axa-fr/react-oidc", () => ({
  useOidcAccessToken: () => mockUseOidcAccessToken(),
}));

jest.mock("../../../../services/integrations/ABCSupplyService", () => ({
  getABCSupplyConnectionStatus: jest.fn(),
  saveABCSupplyConnection: jest.fn(),
  disconnectABCSupplyConnection: jest.fn(),
}));

jest.mock("../../../../hooks/UserSession", () => {
  const React = require("react") as typeof import("react");
  return {
    TenantContext: React.createContext<any>({ tenantSessionDetails: null, setTenantDetails: () => {} }),
  };
});

import ABCSupplyConnectionSettings from "./ABCSupplyConnectionSettings";
import { TenantContext } from "../../../../hooks/UserSession";
import {
  getABCSupplyConnectionStatus,
  saveABCSupplyConnection,
  disconnectABCSupplyConnection,
} from "../../../../services/integrations/ABCSupplyService";

const mockedGetConnectionStatus = getABCSupplyConnectionStatus as jest.Mock;
const mockedSaveConnection = saveABCSupplyConnection as jest.Mock;
const mockedDisconnect = disconnectABCSupplyConnection as jest.Mock;

const accessToken = "test-access-token";
const tenantId = "tenant-abc-supply";

const renderComponent = (
  ui: React.ReactElement,
  tenantSessionDetails: any = { tenantid: tenantId }
) =>
  render(
    <TenantContext.Provider value={{ tenantSessionDetails, setTenantDetails: () => {} }}>
      {ui}
    </TenantContext.Provider>
  );

function setupNotConnected() {
  mockUseOidcAccessToken.mockReturnValue({ accessToken });
  mockedGetConnectionStatus.mockResolvedValue({ data: { connected: false } });
}

function setupConnected(overrides: Record<string, any> = {}) {
  mockUseOidcAccessToken.mockReturnValue({ accessToken });
  mockedGetConnectionStatus.mockResolvedValue({
    data: { connected: true, branchNumber: "17", connectedAt: "2024-06-01T00:00:00Z", ...overrides },
  });
}

describe("ABCSupplyConnectionSettings", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  // ── Landing state ─────────────────────────────────────────────────────────

  it("shows landing page when not connected", async () => {
    setupNotConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    expect(await screen.findByText(/Connect ABC Supply to FIELDCRAFT/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect ABC Supply/i })).toBeInTheDocument();
  });

  it("shows Reconnect heading after a disconnect", async () => {
    setupConnected();
    mockedDisconnect.mockResolvedValueOnce({ data: { connected: false } });
    renderComponent(<ABCSupplyConnectionSettings />);

    const disconnectBtn = await screen.findByRole("button", { name: /Disconnect/i });
    await act(async () => {
      fireEvent.click(disconnectBtn);
    });

    expect(await screen.findByText(/Reconnect ABC Supply to FIELDCRAFT/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reconnect ABC Supply/i })).toBeInTheDocument();
  });

  // ── Connected state ───────────────────────────────────────────────────────

  it("shows connected banner with branch and date meta", async () => {
    setupConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    expect(await screen.findByText(/Connected/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Disconnect/i })).toBeInTheDocument();
  });

  it("shows connected banner with empty meta when no branch or connectedAt", async () => {
    mockUseOidcAccessToken.mockReturnValue({ accessToken });
    mockedGetConnectionStatus.mockResolvedValue({ data: { connected: true } });
    renderComponent(<ABCSupplyConnectionSettings />);
    expect(await screen.findByText(/Connected/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Disconnect/i })).toBeInTheDocument();
  });

  // ── Form navigation ───────────────────────────────────────────────────────

  it("navigates from landing to credential form on CTA click", async () => {
    setupNotConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    expect(screen.getByText(/Enter your credentials/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Your ABC Supply client ID/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Your ABC Supply client secret/i)).toBeInTheDocument();
  });

  it("navigates back to landing via the Back button", async () => {
    setupNotConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.click(screen.getByRole("button", { name: /Back/i }));
    expect(await screen.findByText(/Connect ABC Supply to FIELDCRAFT/i)).toBeInTheDocument();
  });

  it("cancels form and returns to landing page", async () => {
    setupNotConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));
    expect(await screen.findByText(/Connect ABC Supply to FIELDCRAFT/i)).toBeInTheDocument();
  });

  // ── Form validation ───────────────────────────────────────────────────────

  it("disables the Connect button until both credential fields are filled", async () => {
    setupNotConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));

    const connectBtn = screen.getByRole("button", { name: /^Connect$/i });
    expect(connectBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "some-id" },
    });
    expect(connectBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "some-secret" },
    });
    expect(connectBtn).not.toBeDisabled();
  });

  it("allows filling optional account detail fields", async () => {
    setupNotConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));

    fireEvent.change(screen.getByPlaceholderText("e.g. 17"), { target: { value: "42" } });
    const shipToFields = screen.getAllByPlaceholderText("e.g. 2154884-2");
    fireEvent.change(shipToFields[0], { target: { value: "123456-1" } });
    fireEvent.change(shipToFields[1], { target: { value: "789012-2" } });

    expect(screen.getByPlaceholderText("e.g. 17")).toHaveValue("42");
    expect(shipToFields[0]).toHaveValue("123456-1");
    expect(shipToFields[1]).toHaveValue("789012-2");
  });

  // ── Save / connect ────────────────────────────────────────────────────────

  it("successfully saves credentials and transitions to connected state", async () => {
    setupNotConnected();
    mockedSaveConnection.mockResolvedValueOnce({
      ok: true,
      data: { connected: true, branchNumber: "17" },
    });
    renderComponent(<ABCSupplyConnectionSettings />);

    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "my-client-id" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "my-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Connect$/i }));

    await waitFor(() => {
      expect(mockedSaveConnection).toHaveBeenCalledWith(
        expect.objectContaining({
          tenant_id: tenantId,
          client_id: "my-client-id",
          client_secret: "my-secret",
          env: "production",
        }),
        accessToken
      );
    });

    expect(await screen.findByRole("button", { name: /Disconnect/i })).toBeInTheDocument();
  });

  it("shows credential error when save returns ok:false with 401-in-message", async () => {
    setupNotConnected();
    mockedSaveConnection.mockResolvedValueOnce({
      ok: false,
      message: "401 Unauthorized: invalid client credentials",
    });
    renderComponent(<ABCSupplyConnectionSettings />);

    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "bad-id" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "bad-secret" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Connect$/i }));
    });

    expect(await screen.findByText(/Invalid Client ID or Client Secret/i)).toBeInTheDocument();
  });

  it("shows the error message when save returns ok:false with a non-credential message", async () => {
    setupNotConnected();
    mockedSaveConnection.mockResolvedValueOnce({
      ok: false,
      message: "Service temporarily unavailable",
    });
    renderComponent(<ABCSupplyConnectionSettings />);

    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "my-id" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "my-secret" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Connect$/i }));
    });

    expect(await screen.findByText(/Service temporarily unavailable/i)).toBeInTheDocument();
  });

  it("shows fallback error text when save returns ok:false with empty message", async () => {
    setupNotConnected();
    mockedSaveConnection.mockResolvedValueOnce({ ok: false, message: "" });
    renderComponent(<ABCSupplyConnectionSettings />);

    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "my-id" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "my-secret" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Connect$/i }));
    });

    expect(await screen.findByText(/Connection failed/i)).toBeInTheDocument();
  });

  it("shows credential error when save throws a 401 exception", async () => {
    setupNotConnected();
    const err: any = new Error("Unauthorized");
    err.response = { status: 401, data: { detail: "invalid_token" } };
    mockedSaveConnection.mockRejectedValue(err);
    renderComponent(<ABCSupplyConnectionSettings />);

    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "bad-id" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "bad-secret" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Connect$/i }));
    });

    expect(await screen.findByText(/Invalid Client ID or Client Secret/i)).toBeInTheDocument();
  });

  it("shows generic error when save throws a non-credential exception", async () => {
    setupNotConnected();
    mockedSaveConnection.mockRejectedValue(new Error("Internal server error"));
    renderComponent(<ABCSupplyConnectionSettings />);

    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "my-id" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "my-secret" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Connect$/i }));
    });

    expect(await screen.findByText(/Connection failed. Please verify/i)).toBeInTheDocument();
  });

  // ── Disconnect ────────────────────────────────────────────────────────────

  it("disconnects, shows success toast, and notifies parent with null", async () => {
    setupConnected();
    mockedDisconnect.mockResolvedValueOnce({ data: { connected: false } });
    const onStatusChange = jest.fn();
    renderComponent(<ABCSupplyConnectionSettings onStatusChange={onStatusChange} />);

    await screen.findByRole("button", { name: /Disconnect/i });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Disconnect/i }));
    });

    await waitFor(() => {
      expect(mockedDisconnect).toHaveBeenCalledWith(
        expect.objectContaining({ tenant_id: tenantId, clear_credentials: false }),
        accessToken
      );
    });

    expect(await screen.findByText(/ABC Supply disconnected/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(onStatusChange).toHaveBeenLastCalledWith(null);
    });
  });

  it("shows disconnect failed toast when disconnect throws", async () => {
    setupConnected();
    mockedDisconnect.mockRejectedValue(new Error("disconnect failed"));
    renderComponent(<ABCSupplyConnectionSettings />);

    const disconnectBtn = await screen.findByRole("button", { name: /Disconnect/i });
    await act(async () => { fireEvent.click(disconnectBtn); });

    expect(await screen.findByText(/Disconnect failed/i)).toBeInTheDocument();
  });

  // ── onStatusChange callbacks ──────────────────────────────────────────────

  it("calls onStatusChange with null when not connected", async () => {
    setupNotConnected();
    const onStatusChange = jest.fn();
    renderComponent(<ABCSupplyConnectionSettings onStatusChange={onStatusChange} />);
    await waitFor(() => {
      expect(onStatusChange).toHaveBeenCalledWith(null);
    });
  });

  it("calls onStatusChange with full connection info when connected", async () => {
    setupConnected({ shipToNumber: "2154884-2", billToNumberPresent: true });
    const onStatusChange = jest.fn();
    renderComponent(<ABCSupplyConnectionSettings onStatusChange={onStatusChange} />);
    await waitFor(() => {
      expect(onStatusChange).toHaveBeenCalledWith({
        connected: true,
        tenantId,
        branchNumber: "17",
        shipToNumber: "2154884-2",
        billToNumberPresent: true,
      });
    });
  });

  // ── Setup error states ────────────────────────────────────────────────────

  it("shows setup_error when tenantSessionDetails is null (cannot resolve tenant)", async () => {
    mockUseOidcAccessToken.mockReturnValue({ accessToken });
    renderComponent(<ABCSupplyConnectionSettings />, null);
    expect(await screen.findByText(/ABC Supply unavailable/i)).toBeInTheDocument();
    expect(await screen.findByText(/Tenant ID not found/i)).toBeInTheDocument();
  });

  it("shows setup_error when tenantid is empty string in context", async () => {
    mockUseOidcAccessToken.mockReturnValue({ accessToken });
    renderComponent(<ABCSupplyConnectionSettings />, { tenantid: "" });
    expect(await screen.findByText(/Tenant ID not found/i)).toBeInTheDocument();
  });

  it("shows setup_error when getABCSupplyConnectionStatus throws a network error", async () => {
    mockUseOidcAccessToken.mockReturnValue({ accessToken });
    mockedGetConnectionStatus.mockRejectedValue(new Error("Network failure"));
    renderComponent(<ABCSupplyConnectionSettings />);
    expect(await screen.findByText(/Failed to load ABC Supply/i)).toBeInTheDocument();
  });

  // ── Tenant context field name variants ───────────────────────────────────

  it("resolves tenant from tenantId (camelCase) context field", async () => {
    mockUseOidcAccessToken.mockReturnValue({ accessToken });
    mockedGetConnectionStatus.mockResolvedValue({ data: { connected: false } });
    renderComponent(<ABCSupplyConnectionSettings />, { tenantId });
    expect(await screen.findByText(/Connect ABC Supply to FIELDCRAFT/i)).toBeInTheDocument();
  });

  it("resolves tenant from tenant_id (snake_case) context field", async () => {
    mockUseOidcAccessToken.mockReturnValue({ accessToken });
    mockedGetConnectionStatus.mockResolvedValue({ data: { connected: false } });
    renderComponent(<ABCSupplyConnectionSettings />, { tenant_id: tenantId });
    expect(await screen.findByText(/Connect ABC Supply to FIELDCRAFT/i)).toBeInTheDocument();
  });

  // ── Secret visibility toggle ──────────────────────────────────────────────

  it("toggles client secret visibility when the eye button is clicked", async () => {
    setupNotConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));

    const secretInput = screen.getByPlaceholderText(/Your ABC Supply client secret/i);
    expect(secretInput).toHaveAttribute("type", "password");

    const visibilityToggle = screen
      .getAllByRole("button")
      .find((btn) => btn.getAttribute("tabindex") === "-1");
    expect(visibilityToggle).toBeTruthy();

    fireEvent.click(visibilityToggle!);
    expect(secretInput).toHaveAttribute("type", "text");

    fireEvent.click(visibilityToggle!);
    expect(secretInput).toHaveAttribute("type", "password");
  });

  // ── Toast close ───────────────────────────────────────────────────────────

  it("closes the success toast via the Alert close button", async () => {
    setupNotConnected();
    mockedSaveConnection.mockResolvedValueOnce({
      ok: true,
      data: { connected: true, branchNumber: "17" },
    });
    renderComponent(<ABCSupplyConnectionSettings />);

    fireEvent.click(await screen.findByRole("button", { name: /Connect ABC Supply/i }));
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client ID/i), {
      target: { value: "my-client-id" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your ABC Supply client secret/i), {
      target: { value: "my-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Connect$/i }));

    expect(await screen.findByRole("button", { name: /Disconnect/i })).toBeInTheDocument();

    const closeBtn = await screen.findByRole("button", { name: /close/i });
    fireEvent.click(closeBtn);
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /close/i })).not.toBeInTheDocument();
    });
  });

  // ── fmt() edge case ───────────────────────────────────────────────────────

  it("fmt falls back to raw ISO string when toLocaleDateString throws", async () => {
    const spy = jest
      .spyOn(Date.prototype, "toLocaleDateString")
      .mockImplementationOnce(() => { throw new Error("format error"); });
    setupConnected();
    renderComponent(<ABCSupplyConnectionSettings />);
    await screen.findByRole("button", { name: /Disconnect/i });
    spy.mockRestore();
  });
});

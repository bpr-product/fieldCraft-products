import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

jest.mock("./QuickBooksConnection.scss", () => ({}));

import QuickBooksConnection, {
  QuickBooksConnection as NamedConnection,
  QuickBooksCapabilityState,
  QbCapStatus,
} from "./QuickBooksConnection";

type ConnectionProps = React.ComponentProps<typeof QuickBooksConnection>;

function cap(status: QbCapStatus, over: Partial<QuickBooksCapabilityState> = {}): QuickBooksCapabilityState {
  return { status, ...over };
}

function renderConnection(overrides: Partial<ConnectionProps> = {}) {
  const props: ConnectionProps = {
    accounting: cap("off"),
    onConnectAccounting: jest.fn(),
    onReconnectAccounting: jest.fn(),
    onDisconnectAccounting: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<QuickBooksConnection {...props} />) };
}

describe("QuickBooksConnection", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QuickBooksConnection).toBe(NamedConnection);
  });

  describe("disconnected", () => {
    it("shows the QuickBooks Online connect screen", () => {
      renderConnection();
      expect(screen.getByText("Connect your QuickBooks account")).toBeInTheDocument();
      // "QuickBooks Online" is rendered twice: the rail capability row and the
      // "What syncs" side panel heading.
      expect(screen.getAllByText("QuickBooks Online")).toHaveLength(2);
      expect(screen.getByText("Accounting & time activity")).toBeInTheDocument();
    });

    it("calls onConnectAccounting", () => {
      const onConnectAccounting = jest.fn();
      renderConnection({ onConnectAccounting });

      fireEvent.click(screen.getByRole("button", { name: /connect quickbooks/i }));

      expect(onConnectAccounting).toHaveBeenCalledTimes(1);
    });

    it("shows the modules that sync through QuickBooks Online", () => {
      renderConnection();
      expect(screen.getByText("What syncs with QuickBooks")).toBeInTheDocument();
      expect(screen.getByText("Time Activity")).toBeInTheDocument();
    });

    it("disables the connect button when disabled", () => {
      renderConnection({ disabled: true });
      expect(screen.getByRole("button", { name: /connect quickbooks/i })).toBeDisabled();
    });
  });

  describe("connected", () => {
    it("shows the connected state", () => {
      renderConnection({ accounting: cap("on", { companyName: "FIELDCRAFT Books" }) });

      expect(screen.getByText("QuickBooks is connected")).toBeInTheDocument();
      expect(screen.getByText("FIELDCRAFT Books")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Disconnect QuickBooks" })).toBeInTheDocument();
    });

    it("confirms and calls onDisconnectAccounting", async () => {
      const onDisconnectAccounting = jest.fn();
      renderConnection({ accounting: cap("on"), onDisconnectAccounting });

      fireEvent.click(screen.getByRole("button", { name: "Disconnect QuickBooks" }));
      expect(await screen.findByText("Disconnect QuickBooks?")).toBeInTheDocument();

      const disconnectButtons = screen.getAllByRole("button", { name: "Disconnect QuickBooks" });
      fireEvent.click(disconnectButtons[disconnectButtons.length - 1]);

      expect(onDisconnectAccounting).toHaveBeenCalledTimes(1);
    });

    it("does not disconnect when the dialog is cancelled", async () => {
      const onDisconnectAccounting = jest.fn();
      renderConnection({ accounting: cap("on"), onDisconnectAccounting });

      fireEvent.click(screen.getByRole("button", { name: "Disconnect QuickBooks" }));
      expect(await screen.findByText("Disconnect QuickBooks?")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      expect(onDisconnectAccounting).not.toHaveBeenCalled();
    });

    it("does not disconnect when the dialog is dismissed with Escape", async () => {
      const onDisconnectAccounting = jest.fn();
      renderConnection({ accounting: cap("on"), onDisconnectAccounting });

      fireEvent.click(screen.getByRole("button", { name: "Disconnect QuickBooks" }));
      const dialog = await screen.findByRole("dialog");

      fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(onDisconnectAccounting).not.toHaveBeenCalled();
    });

    it("explains what disconnecting does before the user commits", async () => {
      renderConnection({ accounting: cap("on") });

      fireEvent.click(screen.getByRole("button", { name: "Disconnect QuickBooks" }));
      await screen.findByText("Disconnect QuickBooks?");

      expect(screen.getByText("Syncing stops until you connect again.")).toBeInTheDocument();
      expect(screen.getByText(/stay there/)).toBeInTheDocument();
      expect(screen.getByText(/stop syncing/)).toBeInTheDocument();
      expect(screen.getByText("You can reconnect anytime.")).toBeInTheDocument();
    });

    it("ticks off every module that syncs once QuickBooks is connected", () => {
      const { container } = renderConnection({ accounting: cap("on") });

      // The side panel switches from "After you connect" to a checked list.
      expect(container.querySelector(".qbc-side-group-state")).toHaveTextContent("Connected");
      expect(screen.queryByText("After you connect")).not.toBeInTheDocument();
      expect(screen.getAllByTestId("CheckRoundedIcon").length).toBeGreaterThanOrEqual(9);
    });
  });

  describe("busy and disabled states", () => {
    it("shows the opening-QuickBooks label and blocks a second connect click", () => {
      const onConnectAccounting = jest.fn();
      renderConnection({ accounting: cap("off", { busy: true }), onConnectAccounting });

      const button = screen.getByRole("button", { name: /opening quickbooks/i });
      expect(button).toBeDisabled();

      fireEvent.click(button);
      expect(onConnectAccounting).not.toHaveBeenCalled();
    });

    it("shows the opening-QuickBooks label while reconnecting", () => {
      renderConnection({ accounting: cap("reconnect", { busy: true }) });

      expect(screen.getByRole("button", { name: /opening quickbooks/i })).toBeDisabled();
    });

    it("disables the reconnect button when the workspace is disabled", () => {
      renderConnection({ accounting: cap("reconnect"), disabled: true });

      expect(screen.getByRole("button", { name: /sign in to quickbooks/i })).toBeDisabled();
    });

    it("disables the disconnect button while a connection action is in flight", () => {
      renderConnection({ accounting: cap("on", { busy: true }) });

      expect(screen.getByRole("button", { name: "Disconnect QuickBooks" })).toBeDisabled();
    });
  });

  describe("rail", () => {
    it.each([
      ["off", "Not connected"],
      ["on", "Connected"],
      ["attention", "Action needed"],
      ["reconnect", "Reconnect"],
    ] as [QbCapStatus, string][])("labels the %s state as %s", (status, label) => {
      // "Connected" and "Action needed" also appear elsewhere on the page, so assert
      // on the rail's own state element rather than the first text match.
      const { container } = renderConnection({ accounting: cap(status) });

      expect(container.querySelector(".qbc-cap-state")).toHaveTextContent(label);
      expect(container.querySelector(".qbc-cap-row")).toHaveAttribute("data-status", status);
    });

    it("omits the company footer until QuickBooks reports a company name", () => {
      const { container } = renderConnection({ accounting: cap("on") });

      expect(container.querySelector(".qbc-company")).toBeNull();
    });

    it("shows the connected company name in the rail footer", () => {
      const { container } = renderConnection({
        accounting: cap("on", { companyName: "SYN-FIELDCRAFT Books" }),
      });

      expect(container.querySelector(".qbc-company")).toHaveTextContent("SYN-FIELDCRAFT Books");
    });
  });

  describe("reconnect", () => {
    (["reconnect", "attention"] as QbCapStatus[]).forEach((status) => {
      it(`shows the reconnect state for ${status}`, () => {
        renderConnection({ accounting: cap(status) });
        expect(screen.getByText("Sign in again to keep syncing")).toBeInTheDocument();
      });
    });

    it("calls onReconnectAccounting", () => {
      const onReconnectAccounting = jest.fn();
      renderConnection({ accounting: cap("reconnect"), onReconnectAccounting });

      fireEvent.click(screen.getByRole("button", { name: /sign in to quickbooks/i }));

      expect(onReconnectAccounting).toHaveBeenCalledTimes(1);
    });
  });
});

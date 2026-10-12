import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import QuickBooksModuleHeader, {
  QuickBooksModuleHeader as NamedModuleHeader,
  FilterPill,
} from "./QuickBooksModuleHeader";

type HeaderProps = React.ComponentProps<typeof QuickBooksModuleHeader>;
 
function renderHeader(overrides: Partial<HeaderProps> = {}) {
  const props: HeaderProps = {
    title: "Vendors",
    onSync: jest.fn(),
    search: "",
    onSearchChange: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<QuickBooksModuleHeader {...props} />) };
}

describe("QuickBooksModuleHeader", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QuickBooksModuleHeader).toBe(NamedModuleHeader);
  });

  it("renders the title and description", () => {
    renderHeader({ title: "Vendors", description: "People you buy from" });
    expect(screen.getByRole("heading", { name: "Vendors" })).toBeInTheDocument();
    expect(screen.getByText("People you buy from")).toBeInTheDocument();
  });

  it("renders the default sync button label and calls onSync when clicked", () => {
    const onSync = jest.fn();
    renderHeader({ onSync });
    const btn = screen.getByRole("button", { name: "Sync" });
    fireEvent.click(btn);
    expect(onSync).toHaveBeenCalledTimes(1);
  });

  it("renders a custom sync button label", () => {
    renderHeader({ syncButtonLabel: "Sync vendors" });
    expect(screen.getByRole("button", { name: "Sync vendors" })).toBeInTheDocument();
  });

  it("shows 'Syncing…' and disables the button while syncing", () => {
    renderHeader({ syncButtonLabel: "Sync vendors", syncing: true });
    const btn = screen.getByRole("button", { name: "Syncing…" });
    expect(btn).toBeDisabled();
  });

  it("disables the sync button when syncDisabled is set", () => {
    renderHeader({ syncDisabled: true });
    expect(screen.getByRole("button", { name: "Sync" })).toBeDisabled();
  });

  it("shows the disabled reason on hover when the button is disabled", async () => {
    renderHeader({
      syncDisabled: true,
      syncDisabledReason: "A QuickBooks sync is already running.",
    });
    fireEvent.mouseOver(screen.getByRole("button", { name: "Sync" }));
    expect(await screen.findByText("A QuickBooks sync is already running.")).toBeInTheDocument();
  });

  it("calls onSearchChange as the user types", () => {
    const onSearchChange = jest.fn();
    renderHeader({ onSearchChange, searchPlaceholder: "Search vendors" });
    fireEvent.change(screen.getByPlaceholderText("Search vendors"), { target: { value: "Acme" } });
    expect(onSearchChange).toHaveBeenCalledWith("Acme");
  });

  describe("filter pills", () => {
    const filters: FilterPill[] = [
      { id: "all", label: "All", count: 10, tone: "ok" },
      { id: "pending", label: "Pending", count: 2, tone: "wait" },
    ];

    it("renders each filter pill with its label and count", () => {
      renderHeader({ filters, activeFilter: "all" });
      expect(screen.getByText("All")).toBeInTheDocument();
      expect(screen.getByText("Pending")).toBeInTheDocument();
      expect(screen.getByText("10")).toBeInTheDocument();
      expect(screen.getByText("2")).toBeInTheDocument();
    });

    it("marks the active filter with aria-pressed=true", () => {
      renderHeader({ filters, activeFilter: "all" });
      expect(screen.getByText("All").closest("button")).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByText("Pending").closest("button")).toHaveAttribute("aria-pressed", "false");
    });

    it("calls onFilterChange with the pill id when a pill is clicked", () => {
      const onFilterChange = jest.fn();
      renderHeader({ filters, activeFilter: "all", onFilterChange });
      fireEvent.click(screen.getByText("Pending"));
      expect(onFilterChange).toHaveBeenCalledWith("pending");
    });

    it("renders no filter group when filters is empty", () => {
      renderHeader({ filters: [] });
      expect(screen.queryByRole("group", { name: /filter by status/i })).not.toBeInTheDocument();
    });
  });

  describe("sync mode menu", () => {
    it("does not render the sync options caret when showSyncMode is false", () => {
      renderHeader();
      expect(screen.queryByRole("button", { name: "Sync options" })).not.toBeInTheDocument();
    });

    it("opens the menu and calls onSyncModeChange with 'full' when Everything is chosen", () => {
      const onSyncModeChange = jest.fn();
      renderHeader({ showSyncMode: true, syncMode: "cdc", onSyncModeChange });
      fireEvent.click(screen.getByRole("button", { name: "Sync options" }));
      expect(screen.getByRole("menu")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("menuitemradio", { name: /everything/i }));
      expect(onSyncModeChange).toHaveBeenCalledWith("full");
    });

    it("calls onSyncModeChange with 'cdc' when 'Only what changed' is chosen", () => {
      const onSyncModeChange = jest.fn();
      renderHeader({ showSyncMode: true, syncMode: "full", onSyncModeChange });
      fireEvent.click(screen.getByRole("button", { name: "Sync options" }));
      fireEvent.click(screen.getByRole("menuitemradio", { name: /only what changed/i }));
      expect(onSyncModeChange).toHaveBeenCalledWith("cdc");
    });

    it("reflects the current sync mode via aria-checked", () => {
      renderHeader({ showSyncMode: true, syncMode: "full" });
      fireEvent.click(screen.getByRole("button", { name: "Sync options" }));
      expect(screen.getByRole("menuitemradio", { name: /everything/i })).toHaveAttribute("aria-checked", "true");
      expect(screen.getByRole("menuitemradio", { name: /only what changed/i })).toHaveAttribute("aria-checked", "false");
    });
  });

  it("renders extra actions passed via the actions prop", () => {
    renderHeader({ actions: <button type="button">Assign</button> });
    expect(screen.getByRole("button", { name: "Assign" })).toBeInTheDocument();
  });

  describe("title hint slot", () => {
    it("renders the hint beside the title when supplied", () => {
      renderHeader({ title: "Customers", titleHint: <button type="button">Why?</button> });
      expect(screen.getByRole("button", { name: "Why?" })).toBeInTheDocument();
    });

    it("is optional — the header renders its title normally without one", () => {
      renderHeader({ title: "Customers" });
      expect(screen.getByRole("heading", { name: "Customers" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Why?" })).not.toBeInTheDocument();
    });

    it("keeps the hint out of the heading, so its label does not join the heading name", () => {
      renderHeader({
        title: "Customers",
        titleHint: <button type="button" aria-label="Why a customer may be missing" />,
      });

      // Would read "Customers Why a customer may be missing" if the button were
      // nested inside the <h2> rather than rendered as its sibling.
      const heading = screen.getByRole("heading", { name: "Customers" });
      expect(heading).toBeInTheDocument();
      expect(heading).not.toContainElement(
        screen.getByRole("button", { name: "Why a customer may be missing" }),
      );
    });

    it("passes interaction through to the supplied hint", () => {
      const onHintClick = jest.fn();
      renderHeader({
        titleHint: <button type="button" onClick={onHintClick}>Why?</button>,
      });
      fireEvent.click(screen.getByRole("button", { name: "Why?" }));
      expect(onHintClick).toHaveBeenCalledTimes(1);
    });
  });

  describe("context slot", () => {
    it("renders module context when supplied", () => {
      renderHeader({ context: <span>Attaches to 3</span> });
      expect(screen.getByText("Attaches to 3")).toBeInTheDocument();
    });

    it("is optional — the header renders normally without it", () => {
      renderHeader({ searchPlaceholder: "Search vendors…" });
      expect(screen.getByRole("textbox", { name: "Search vendors…" })).toBeInTheDocument();
      expect(screen.queryByText("Attaches to 3")).not.toBeInTheDocument();
    });

    it("shares the toolbar row with the search field and filter pills rather than replacing them", () => {
      const filters: FilterPill[] = [{ id: "ALL", label: "All", count: 4 }];
      renderHeader({
        filters,
        activeFilter: "ALL",
        searchPlaceholder: "Search vendors…",
        context: <span>Attaches to 3</span>,
      });
      expect(screen.getByRole("textbox", { name: "Search vendors…" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /All/ })).toBeInTheDocument();
      expect(screen.getByText("Attaches to 3")).toBeInTheDocument();
    });

    it("passes interaction through to the supplied context", () => {
      const onContextClick = jest.fn();
      renderHeader({
        context: <button type="button" onClick={onContextClick}>Attaches to 3</button>,
      });
      fireEvent.click(screen.getByRole("button", { name: "Attaches to 3" }));
      expect(onContextClick).toHaveBeenCalledTimes(1);
    });
  });
});

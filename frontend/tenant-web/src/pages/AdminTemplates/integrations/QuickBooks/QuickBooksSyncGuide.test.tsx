import React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import {
  computeModuleHealth,
  ALL_MODULE_DEPENDENCIES,
  MODULE_DEPENDENCIES,
  MODULE_LABELS,
  ModuleDependencyHint,
  ModuleDependencyHintProps,
  ModuleSummaryBag,
  SyncPipelineGuide,
  SyncPipelineGuideProps,
} from "./QuickBooksSyncGuide";

// babel-jest hoists these above the imports above, so the stylesheets are stubbed
// before the component under test is evaluated.
jest.mock("./QuickBooksSyncGuide.scss", () => ({}));
jest.mock("./QuickBooksWorkspace.scss", () => ({}));

// Suppress the `ReactDOMTestUtils.act` deprecation emitted by @testing-library/react
// internals on this React+RTL version combination — it's a library issue, not our code.
const _origConsoleError = console.error;
beforeAll(() => {
  console.error = (...args: any[]) => {
    if (typeof args[0] === "string" && args[0].includes("ReactDOMTestUtils.act")) return;
    _origConsoleError.call(console, ...args);
  };
});
afterAll(() => {
  console.error = _origConsoleError;
});

// ── Shared fixtures ────────────────────────────────────────────────────────────

const emptyBag: ModuleSummaryBag = {};

const fullBag: ModuleSummaryBag = {
  customers: {
    totalCustomers: 10,
    syncedCustomers: 8,
    failedCustomers: 1,
    importFailedCustomers: 0,
    syncFailedCustomers: 1,
  },
  vendors: {
    totalVendors: 5,
    syncedVendors: 3,
    failedVendors: 1,
    importFailedVendors: 1,
  },
  "chart-of-accounts": {
    totalAccounts: 20,
    syncedAccounts: 20,
  },
  employees: {
    totalEmployees: 4,
    syncedEmployees: 2,
    failedEmployees: 0,
    importFailedEmployees: 1,
    syncFailedEmployees: 1,
  },
  projects: { total: 3, synced: 2, failed: 1 },
  services: { total: 6, synced: 4, failed: 0 },
  bills: {
    totalBills: 7,
    syncedBills: 5,
    failedBills: 1,
    importFailedBills: 0,
    syncFailedBills: 1,
  },
  "bill-payments": {
    totalBillPayments: 2,
    syncedBillPayments: 1,
    failedBillPayments: 0,
    importFailedBillPayments: 1,
  },
  timelogs: { total: 8, synced: 6, failed: 1, notReady: 1 },
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function renderGuide(props: Partial<SyncPipelineGuideProps> = {}) {
  const defaults: SyncPipelineGuideProps = {
    open: true,
    onClose: jest.fn(),
    selectedModule: "customers",
    onSelectModule: jest.fn(),
  };
  return render(<SyncPipelineGuide {...defaults} {...props} />);
}

/** The module chip inside a phase group (labelled by the module name alone). */
const chip = (label: string) => screen.getByRole("button", { name: label });

/** A chip along the panel foot for a record to set up first. */
const attachChip = (label: string) => screen.getByRole("button", { name: `Go to ${label}` });

/** The footer group naming what to set up first for the selected module. */
const attachGroup = (label: string) => screen.getByRole("group", { name: `What to set up first for ${label}` });
const queryAttachGroup = (label: string) => screen.queryByRole("group", { name: `What to set up first for ${label}` });

function renderHint(props: Partial<ModuleDependencyHintProps> & { moduleId: string }) {
  const defaults: ModuleDependencyHintProps = {
    moduleId: props.moduleId,
    summaries: emptyBag,
    onNavigate: jest.fn(),
    loadedModules: new Set<string>(),
  };
  return render(<ModuleDependencyHint {...defaults} {...props} />);
}

// ── computeModuleHealth ────────────────────────────────────────────────────────

describe("computeModuleHealth", () => {
  it("returns 'no-data' when bag is empty", () => {
    expect(computeModuleHealth("customers", {})).toBe("no-data");
  });

  it("returns 'no-data' when the module key is null in the bag", () => {
    expect(computeModuleHealth("customers", { customers: null })).toBe("no-data");
  });

  it("returns 'has-data' for customers when syncedCustomers > 0", () => {
    expect(computeModuleHealth("customers", { customers: { syncedCustomers: 5 } })).toBe("has-data");
  });

  it("returns 'no-data' for customers when syncedCustomers is 0", () => {
    expect(computeModuleHealth("customers", { customers: { syncedCustomers: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for vendors when syncedVendors > 0", () => {
    expect(computeModuleHealth("vendors", { vendors: { syncedVendors: 2 } })).toBe("has-data");
  });

  it("returns 'no-data' for vendors when syncedVendors is 0", () => {
    expect(computeModuleHealth("vendors", { vendors: { syncedVendors: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for chart-of-accounts when syncedAccounts > 0", () => {
    expect(computeModuleHealth("chart-of-accounts", { "chart-of-accounts": { syncedAccounts: 10 } })).toBe("has-data");
  });

  it("returns 'no-data' for chart-of-accounts when syncedAccounts is 0", () => {
    expect(computeModuleHealth("chart-of-accounts", { "chart-of-accounts": { syncedAccounts: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for employees when syncedEmployees > 0", () => {
    expect(computeModuleHealth("employees", { employees: { syncedEmployees: 3 } })).toBe("has-data");
  });

  it("returns 'no-data' for employees when syncedEmployees is 0", () => {
    expect(computeModuleHealth("employees", { employees: { syncedEmployees: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for projects when synced > 0", () => {
    expect(computeModuleHealth("projects", { projects: { synced: 1 } })).toBe("has-data");
  });

  it("returns 'no-data' for projects when synced is 0", () => {
    expect(computeModuleHealth("projects", { projects: { synced: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for services when synced > 0", () => {
    expect(computeModuleHealth("services", { services: { synced: 4 } })).toBe("has-data");
  });

  it("returns 'no-data' for services when synced is 0", () => {
    expect(computeModuleHealth("services", { services: { synced: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for bills when syncedBills > 0", () => {
    expect(computeModuleHealth("bills", { bills: { syncedBills: 3 } })).toBe("has-data");
  });

  it("returns 'no-data' for bills when syncedBills is 0", () => {
    expect(computeModuleHealth("bills", { bills: { syncedBills: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for bill-payments when syncedBillPayments > 0", () => {
    expect(computeModuleHealth("bill-payments", { "bill-payments": { syncedBillPayments: 1 } })).toBe("has-data");
  });

  it("returns 'no-data' for bill-payments when syncedBillPayments is 0", () => {
    expect(computeModuleHealth("bill-payments", { "bill-payments": { syncedBillPayments: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for timelogs when synced > 0", () => {
    expect(computeModuleHealth("timelogs", { timelogs: { synced: 6 } })).toBe("has-data");
  });

  it("returns 'no-data' for timelogs when synced is 0", () => {
    expect(computeModuleHealth("timelogs", { timelogs: { synced: 0 } })).toBe("no-data");
  });

  it("returns 'has-data' for invoices when synced > 0", () => {
    expect(computeModuleHealth("invoices", { invoices: { synced: 2 } })).toBe("has-data");
  });

  it("returns 'has-data' for payments when synced > 0", () => {
    expect(computeModuleHealth("payments", { payments: { synced: 2 } })).toBe("has-data");
  });

  it("returns 'no-data' for an unknown moduleId", () => {
    expect(computeModuleHealth("unknown-module", {})).toBe("no-data");
  });
});

// ── MODULE_LABELS ──────────────────────────────────────────────────────────────

describe("MODULE_LABELS", () => {
  it("contains all 11 expected module labels", () => {
    expect(MODULE_LABELS).toEqual({
      customers: "Customers",
      vendors: "Vendors",
      "chart-of-accounts": "Chart of Accounts",
      employees: "Employees",
      projects: "Projects",
      services: "Services",
      bills: "Bills",
      invoices: "Invoices",
      payments: "Customer Payments",
      timelogs: "Time Activities",
      "bill-payments": "Bill Payments",
    });
  });
});

// ── Release gating ─────────────────────────────────────────────────────────────

describe("gated modules are absent from the guide", () => {
  it("does not render an Invoices or Customer Payments chip", () => {
    renderGuide();
    expect(screen.queryByRole("button", { name: "Invoices" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Customer Payments" })).not.toBeInTheDocument();
  });

  it("still renders the released modules in the same groups", () => {
    renderGuide();
    expect(chip("Customers")).toBeInTheDocument();
    expect(chip("Bills")).toBeInTheDocument();
    expect(chip("Bill Payments")).toBeInTheDocument();
    // "Settle up" keeps Bill Payments, so it must not have been dropped as empty.
    expect(screen.getByText("Settle up")).toBeInTheDocument();
  });

  it("omits gated modules from the rendered dependency graph", () => {
    expect(MODULE_DEPENDENCIES["invoices"]).toBeUndefined();
    expect(MODULE_DEPENDENCIES["payments"]).toBeUndefined();
  });

  it("keeps the released dependency lists intact", () => {
    // No released module depends on a gated one today, so nothing should have been
    // stripped from these — this guards the filter against over-reaching.
    expect(MODULE_DEPENDENCIES["bills"]).toEqual(["vendors", "chart-of-accounts", "projects"]);
    expect(MODULE_DEPENDENCIES["bill-payments"]).toEqual(["bills"]);
    expect(MODULE_DEPENDENCIES["timelogs"]).toEqual(["employees", "projects", "services"]);
  });

  it("keeps the ungated domain graph complete", () => {
    expect(ALL_MODULE_DEPENDENCIES["invoices"]).toEqual(["customers", "projects"]);
    expect(ALL_MODULE_DEPENDENCIES["payments"]).toEqual(["invoices"]);
  });
});

// ── MODULE_DEPENDENCIES ────────────────────────────────────────────────────────

describe("MODULE_DEPENDENCIES", () => {
  it("foundation modules have no dependencies", () => {
    expect(MODULE_DEPENDENCIES["customers"]).toEqual([]);
    expect(MODULE_DEPENDENCIES["vendors"]).toEqual([]);
    expect(MODULE_DEPENDENCIES["chart-of-accounts"]).toEqual([]);
    expect(MODULE_DEPENDENCIES["employees"]).toEqual([]);
  });

  it("projects depends on customers only", () => {
    expect(MODULE_DEPENDENCIES["projects"]).toEqual(["customers"]);
  });

  it("services depends on chart-of-accounts only", () => {
    expect(MODULE_DEPENDENCIES["services"]).toEqual(["chart-of-accounts"]);
  });

  it("bills depends on vendors, chart-of-accounts, and projects", () => {
    expect(MODULE_DEPENDENCIES["bills"]).toEqual(["vendors", "chart-of-accounts", "projects"]);
  });

  it("invoices depends on customers and projects in the domain graph", () => {
    expect(ALL_MODULE_DEPENDENCIES["invoices"]).toEqual(["customers", "projects"]);
  });

  it("customer payments depend on invoices only in the domain graph", () => {
    expect(ALL_MODULE_DEPENDENCIES["payments"]).toEqual(["invoices"]);
  });

  it("bill-payments depends on bills only", () => {
    expect(MODULE_DEPENDENCIES["bill-payments"]).toEqual(["bills"]);
  });

  it("timelogs depends on employees, projects, and services", () => {
    expect(MODULE_DEPENDENCIES["timelogs"]).toEqual(["employees", "projects", "services"]);
  });
});

// ── SyncPipelineGuide ──────────────────────────────────────────────────────────

describe("SyncPipelineGuide", () => {
  it("renders nothing when closed", () => {
    renderGuide({ open: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders a dialog when open", () => {
    renderGuide();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("labels the guide region for assistive tech", () => {
    renderGuide();
    expect(screen.getByRole("region", { name: "How it works" })).toBeInTheDocument();
  });

  it("renders the 'How it works' heading", () => {
    renderGuide();
    expect(screen.getByRole("heading", { name: "How it works", level: 2 })).toBeInTheDocument();
  });

  it("frames the reference records as a one-time setup, not a repeating order", () => {
    renderGuide();
    expect(
      screen.getByText(/Sync your reference records once/i),
    ).toBeInTheDocument();
  });

  it("renders all 9 released module chips", () => {
    renderGuide();
    ["Customers", "Vendors", "Chart of Accounts", "Employees", "Projects", "Services", "Bills", "Time Activities", "Bill Payments"]
      .forEach((label) => expect(chip(label)).toBeInTheDocument());
  });

  it("renders the two phases", () => {
    renderGuide();
    expect(screen.getByRole("heading", { name: "Set up once", level: 3 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sync as work comes in", level: 3 })).toBeInTheDocument();
  });

  it("explains that setup records are synced once per record", () => {
    renderGuide();
    expect(screen.getByText(/come back only when you add a new one/i)).toBeInTheDocument();
  });

  it("explains that transactions attach to what was already set up", () => {
    renderGuide();
    expect(screen.getByText(/they link to what you set up above/i)).toBeInTheDocument();
  });

  it("renders the 4 group labels", () => {
    renderGuide();
    ["Start here", "Then", "Day to day", "Settle up"]
      .forEach((label) => expect(screen.getByText(label)).toBeInTheDocument());
  });

  it("renders group descriptions", () => {
    renderGuide();
    expect(screen.getByText("These can be synced right away")).toBeInTheDocument();
    expect(screen.getByText("These attach to the records above")).toBeInTheDocument();
    expect(screen.getByText("Created as you work")).toBeInTheDocument();
    expect(screen.getByText("Payments against the records above")).toBeInTheDocument();
  });

  it("numbers the phases sequentially, with no gaps", () => {
    // Numbering comes from the rendered position, so a phase emptied by release
    // gating can never leave a hole in the sequence. Each `getByText` also asserts
    // uniqueness — a repeated number would throw.
    renderGuide();
    ["1", "2"].forEach((n) => expect(screen.getByText(n)).toBeInTheDocument());
    expect(screen.queryByText("3")).not.toBeInTheDocument();
  });

  it("shows the resting hint when nothing is selected", () => {
    renderGuide();
    expect(screen.getByText("Select any module to see what to set up for it first.")).toBeInTheDocument();
  });

  it("marks the workspace's current module with aria-current", () => {
    renderGuide({ selectedModule: "bills" });
    expect(chip("Bills")).toHaveAttribute("aria-current", "true");
    expect(chip("Vendors")).not.toHaveAttribute("aria-current");
  });

  it("gives the current module the --current class", () => {
    renderGuide({ selectedModule: "bills" });
    expect(chip("Bills")).toHaveClass("qbsg-chip--current");
  });

  it("renders every chip unpressed while nothing is focused", () => {
    renderGuide();
    ["Customers", "Bills", "Projects"].forEach((label) => {
      expect(chip(label)).toHaveAttribute("aria-pressed", "false");
    });
  });

  it("applies no chain-mode classes while nothing is focused", () => {
    renderGuide();
    ["qbsg-chip--focal", "qbsg-chip--chain", "qbsg-chip--dimmed"].forEach((cls) => {
      expect(chip("Bills")).not.toHaveClass(cls);
    });
  });

  it("closes when the close button is clicked", () => {
    const onClose = jest.fn();
    renderGuide({ onClose });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape when nothing is selected", () => {
    const onClose = jest.fn();
    renderGuide({ onClose });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape", code: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // ── Selecting a module ──────────────────────────────────────────────────────

  it("shows what to set up first when a module is selected", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    expect(attachGroup("Bills")).toBeInTheDocument();
    expect(screen.getByText("Set up first")).toBeInTheDocument();
  });

  it("lists everything to set up first for bills", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    const names = within(attachGroup("Bills")).getAllByRole("button").map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual(["Go to Vendors", "Go to Chart of Accounts", "Go to Projects"]);
  });

  it("lists everything to set up first for time activities", () => {
    renderGuide();
    fireEvent.click(chip("Time Activities"));
    const names = within(attachGroup("Time Activities")).getAllByRole("button").map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual(["Go to Employees", "Go to Projects", "Go to Services"]);
  });

  it("lists everything to set up first for bill payments", () => {
    renderGuide();
    fireEvent.click(chip("Bill Payments"));
    const names = within(attachGroup("Bill Payments")).getAllByRole("button").map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual(["Go to Bills"]);
  });

  /*
   * The wording here is load-bearing. "sync Bills on its own" meant "separately from the
   * records above", but read as "automatically" — and nothing in this integration syncs on
   * its own; the user presses Sync every time. The copy now names the user as the actor, and
   * these assertions pin it so it cannot drift back.
   */
  it("says the user runs the transaction sync themselves, each time", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    expect(screen.getByText(/Set each of those up once/i)).toHaveTextContent(
      "Set each of those up once. After that Bills is the only one you sync — you run it each time you want new ones in QuickBooks.",
    );
  });

  it("never implies a module syncs by itself", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    expect(screen.queryByText(/on its own/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/automatic/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/attach themselves/i)).not.toBeInTheDocument();
  });

  it("says a foundation module needs nothing set up first", () => {
    renderGuide();
    fireEvent.click(chip("Customers"));
    expect(screen.getByText("No setup needed")).toBeInTheDocument();
    expect(within(attachGroup("Customers")).queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText(/does not need anything set up first/i)).toHaveTextContent(
      "Customers does not need anything set up first — you run a sync each time you want new ones in QuickBooks.",
    );
  });

  it("marks the selected chip as pressed and focal", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    expect(chip("Bills")).toHaveAttribute("aria-pressed", "true");
    expect(chip("Bills")).toHaveClass("qbsg-chip--focal");
  });

  it("highlights the records to set up for the selected module", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    ["Vendors", "Chart of Accounts", "Projects"].forEach((label) => {
      expect(chip(label)).toHaveClass("qbsg-chip--chain");
    });
  });

  it("dims modules unrelated to the selection", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    ["Customers", "Employees", "Services", "Bill Payments"].forEach((label) => {
      expect(chip(label)).toHaveClass("qbsg-chip--dimmed");
    });
  });

  it("does not navigate the workspace when a chip is selected", () => {
    const onSelectModule = jest.fn();
    const onClose = jest.fn();
    renderGuide({ onSelectModule, onClose });
    fireEvent.click(chip("Bills"));
    expect(onSelectModule).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("clears the selection when the same chip is selected again", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    expect(attachGroup("Bills")).toBeInTheDocument();
    fireEvent.click(chip("Bills"));
    expect(queryAttachGroup("Bills")).not.toBeInTheDocument();
    expect(screen.getByText("Select any module to see what to set up for it first.")).toBeInTheDocument();
  });

  it("switches the selection when a different chip is selected", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    fireEvent.click(chip("Projects"));
    expect(queryAttachGroup("Bills")).not.toBeInTheDocument();
    expect(attachGroup("Projects")).toBeInTheDocument();
  });

  it("clears the selection with the Clear button", () => {
    renderGuide();
    fireEvent.click(chip("Bills"));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(queryAttachGroup("Bills")).not.toBeInTheDocument();
  });

  it("clears the selection on Escape instead of closing the panel", () => {
    const onClose = jest.fn();
    renderGuide({ onClose });
    fireEvent.click(chip("Bills"));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape", code: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    expect(queryAttachGroup("Bills")).not.toBeInTheDocument();
  });

  it("closes on a second Escape, once the selection has been cleared", () => {
    const onClose = jest.fn();
    renderGuide({ onClose });
    fireEvent.click(chip("Bills"));
    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });
    fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // ── Navigating out of the panel ─────────────────────────────────────────────

  it("navigates and closes when an attached record is clicked", () => {
    const onSelectModule = jest.fn();
    const onClose = jest.fn();
    renderGuide({ onSelectModule, onClose });
    fireEvent.click(chip("Bills"));
    fireEvent.click(attachChip("Vendors"));
    expect(onSelectModule).toHaveBeenCalledWith("vendors");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("navigates to the selected module from the Go to action", () => {
    const onSelectModule = jest.fn();
    const onClose = jest.fn();
    renderGuide({ onSelectModule, onClose });
    fireEvent.click(chip("Bills"));
    fireEvent.click(screen.getByRole("button", { name: "Go to Bills" }));
    expect(onSelectModule).toHaveBeenCalledWith("bills");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("offers a Go to action even for a module that needs nothing set up", () => {
    const onSelectModule = jest.fn();
    renderGuide({ onSelectModule });
    fireEvent.click(chip("Customers"));
    fireEvent.click(screen.getByRole("button", { name: "Go to Customers" }));
    expect(onSelectModule).toHaveBeenCalledWith("customers");
  });

  it("resets the selection when the panel is closed and reopened", () => {
    const { rerender } = renderGuide();
    fireEvent.click(chip("Bills"));
    expect(attachGroup("Bills")).toBeInTheDocument();

    rerender(
      <SyncPipelineGuide open={false} onClose={jest.fn()} selectedModule="customers" onSelectModule={jest.fn()} />,
    );
    rerender(
      <SyncPipelineGuide open onClose={jest.fn()} selectedModule="customers" onSelectModule={jest.fn()} />,
    );

    expect(queryAttachGroup("Bills")).not.toBeInTheDocument();
    expect(screen.getByText("Select any module to see what to set up for it first.")).toBeInTheDocument();
  });
});

// ── ModuleDependencyHint ───────────────────────────────────────────────────────


describe("ModuleDependencyHint", () => {
  /** The status pill in the module header's toolbar row. */
  const pin = () => screen.getByRole("button", { name: /^(Set up once|Check setup) for /i });
  /** Open the pill's popover and return it. */
  const openPopover = () => {
    fireEvent.click(pin());
    return screen.getByRole("dialog");
  };

  // ── Modules that attach to nothing render nothing ───────────────────────────

  it("renders nothing for customers (no dependencies)", () => {
    const { container } = renderHint({ moduleId: "customers" });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for vendors (no dependencies)", () => {
    const { container } = renderHint({ moduleId: "vendors" });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for chart-of-accounts (no dependencies)", () => {
    const { container } = renderHint({ moduleId: "chart-of-accounts" });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for employees (no dependencies)", () => {
    const { container } = renderHint({ moduleId: "employees" });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for an unknown module id", () => {
    const { container } = renderHint({ moduleId: "not-a-module" });
    expect(container).toBeEmptyDOMElement();
  });

  // ── The pill: costs no row, carries the headline ────────────────────────────

  it("renders a single pill and nothing else at rest", () => {
    renderHint({ moduleId: "bills" });
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("names the one-time setup and counts the records on the pill", () => {
    renderHint({ moduleId: "bills" });
    expect(pin()).toHaveTextContent("Set up once3");
  });

  it("counts a single record correctly", () => {
    renderHint({ moduleId: "projects" });
    expect(pin()).toHaveTextContent("Set up once1");
  });

  it("gives the pill a module-scoped accessible name", () => {
    renderHint({ moduleId: "bills" });
    expect(
      screen.getByRole("button", { name: "Set up once for Bills — 3 records" }),
    ).toBeInTheDocument();
  });

  it("keeps the detail closed until the pill is clicked", () => {
    renderHint({ moduleId: "bills" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(pin()).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the detail popover on click", () => {
    renderHint({ moduleId: "bills" });
    // Grab the pill before opening: MUI's Popover marks the rest of the document
    // aria-hidden while it is open, so a role query would no longer reach it.
    const pill = pin();
    fireEvent.click(pill);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(pill).toHaveAttribute("aria-expanded", "true");
  });

  // ── The popover: everything the row used to show ────────────────────────────

  it("labels the popover for assistive tech", () => {
    renderHint({ moduleId: "bills" });
    openPopover();
    expect(screen.getByRole("dialog", { name: "Set up once for Bills" })).toBeInTheDocument();
  });

  it("titles the popover with the module it belongs to", () => {
    renderHint({ moduleId: "bills" });
    openPopover();
    expect(within(screen.getByRole("dialog")).getByText("Set up once for Bills")).toBeInTheDocument();
  });

  it("lists every attached record for bills", () => {
    renderHint({ moduleId: "bills" });
    const popover = openPopover();
    const names = within(popover)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"))
      .filter((n): n is string => !!n && n.startsWith("Go to "));
    expect(names).toEqual(["Go to Vendors", "Go to Chart of Accounts", "Go to Projects"]);
  });

  it("lists every attached record for timelogs", () => {
    renderHint({ moduleId: "timelogs" });
    const popover = openPopover();
    ["Employees", "Projects", "Services"].forEach((label) => {
      expect(within(popover).getByRole("button", { name: `Go to ${label}` })).toBeInTheDocument();
    });
  });

  it("lists the single attached record for bill-payments", () => {
    renderHint({ moduleId: "bill-payments" });
    const popover = openPopover();
    expect(within(popover).getByRole("button", { name: "Go to Bills" })).toBeInTheDocument();
  });

  it("tells the user to settle the attached modules before syncing, naming this module", () => {
    renderHint({ moduleId: "bills" });
    openPopover();
    expect(screen.getByText(/Before syncing new/i)).toHaveTextContent(
      "Before syncing new Bills, make sure the related modules are up to date. If anything has failed or needs attention, resolve and sync it first.",
    );
    expect(screen.queryByText(/on its own/i)).not.toBeInTheDocument();
  });

  it("navigates and closes the popover when an attached record is clicked", () => {
    const onNavigate = jest.fn();
    renderHint({ moduleId: "projects", onNavigate });
    openPopover();
    fireEvent.click(screen.getByRole("button", { name: "Go to Customers" }));
    expect(onNavigate).toHaveBeenCalledWith("customers");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // ── The "How it works" way out ──────────────────────────────────────────────

  it("hides the 'How it works' link when no handler is supplied", () => {
    renderHint({ moduleId: "projects" });
    openPopover();
    expect(screen.queryByRole("button", { name: "How it works ?" })).not.toBeInTheDocument();
  });

  it("opens the guide and closes the popover from the 'How it works' link", () => {
    const onOpenGuide = jest.fn();
    renderHint({ moduleId: "projects", onOpenGuide });
    openPopover();
    fireEvent.click(screen.getByRole("button", { name: "How it works ?" }));
    expect(onOpenGuide).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // Placement, not just presence: the link closes the panel from inside the footer band,
  // grouped with the note. On its own it was a full-width row of bare accent text that
  // read as a fourth list item, so the band and the chip are the fix, not a move.
  it("groups the 'How it works' chip with the note in the panel's footer", () => {
    renderHint({ moduleId: "projects", onOpenGuide: jest.fn() });
    openPopover();

    const foot = screen.getByRole("button", { name: "How it works ?" }).closest(".qbsg-pop-foot");
    expect(foot).not.toBeNull();
    expect(foot!.querySelector(".qbsg-pop-note")).toHaveTextContent(/Before syncing new/i);

    // The footer is the panel's last block, so the list still ends the panel's body.
    expect(screen.getByRole("dialog").lastElementChild).toHaveClass("qbsg-pop-foot");
  });

  it("labels the chip exactly as the workspace rail does, question mark included", () => {
    renderHint({ moduleId: "projects", onOpenGuide: jest.fn() });
    openPopover();

    const chip = screen.getByRole("button", { name: "How it works ?" });
    // Text is the entire control — no glyph rides along to out-weigh 11.5px type.
    expect(chip.querySelector("svg")).toBeNull();
  });

  it("keeps the note in the footer when there is no guide to link to", () => {
    renderHint({ moduleId: "projects" });
    openPopover();

    const foot = screen.getByRole("dialog").querySelector(".qbsg-pop-foot");
    expect(foot!.querySelector(".qbsg-pop-note")).toBeInTheDocument();
    expect(foot!.querySelector(".qbsg-pop-more")).toBeNull();
  });

  // ── Tones ───────────────────────────────────────────────────────────────────

  it("is neutral while no attached record has been loaded", () => {
    renderHint({ moduleId: "projects" });
    expect(pin()).toHaveClass("qbsg-pin--neutral");
  });

  it("turns ok when every attached record is loaded and clean", () => {
    renderHint({
      moduleId: "projects",
      summaries: { customers: { syncedCustomers: 5, failedCustomers: 0 } },
      loadedModules: new Set(["customers"]),
    });
    expect(pin()).toHaveClass("qbsg-pin--ok");
  });

  it("reports a clean record as 'No issues' in the popover", () => {
    renderHint({
      moduleId: "projects",
      summaries: { customers: { syncedCustomers: 5, failedCustomers: 0 } },
      loadedModules: new Set(["customers"]),
    });
    openPopover();
    expect(screen.getByText("No issues")).toBeInTheDocument();
  });

  it("does not claim a record is clean before its data has loaded", () => {
    renderHint({
      moduleId: "projects",
      summaries: { customers: { syncedCustomers: 5, failedCustomers: 0 } },
      loadedModules: new Set<string>(),
    });
    expect(pin()).toHaveClass("qbsg-pin--neutral");
    expect(pin()).not.toHaveClass("qbsg-pin--ok");
    openPopover();
    expect(screen.queryByText("No issues")).not.toBeInTheDocument();
  });

  it("stays neutral for bills when only some attached records are loaded", () => {
    renderHint({
      moduleId: "bills",
      summaries: fullBag,
      loadedModules: new Set(["chart-of-accounts"]),
    });
    expect(pin()).toHaveClass("qbsg-pin--neutral");
  });

  it("turns ok for bills when all three attached records are loaded and clean", () => {
    renderHint({
      moduleId: "bills",
      summaries: {
        vendors: { syncedVendors: 3, failedVendors: 0, importFailedVendors: 0 },
        "chart-of-accounts": { syncedAccounts: 10 },
        projects: { synced: 2, failed: 0 },
      },
      loadedModules: new Set(["vendors", "chart-of-accounts", "projects"]),
    });
    expect(pin()).toHaveClass("qbsg-pin--ok");
  });

  // ── Escalation to the warning tone ──────────────────────────────────────────

  describe("warning state with fake timers", () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });
    afterEach(() => {
      act(() => { jest.runOnlyPendingTimers(); });
      jest.useRealTimers();
    });

    const advance = () => act(() => { jest.advanceTimersByTime(400); });

    it("does not warn immediately when a loaded record has issues", () => {
      renderHint({
        moduleId: "projects",
        summaries: { customers: { failedCustomers: 2 } },
        loadedModules: new Set(["customers"]),
      });
      expect(pin()).toHaveClass("qbsg-pin--neutral");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("warns after the 400ms delay", () => {
      renderHint({
        moduleId: "projects",
        summaries: { customers: { failedCustomers: 2 } },
        loadedModules: new Set(["customers"]),
      });
      advance();
      expect(pin()).toHaveClass("qbsg-pin--warn");
    });

    it("switches the pill to a setup-check without borrowing the filter row wording", () => {
      renderHint({
        moduleId: "bills",
        summaries: { vendors: { failedVendors: 1 }, projects: { failed: 2 } },
        loadedModules: new Set(["vendors", "chart-of-accounts", "projects"]),
      });
      advance();
      expect(pin()).toHaveTextContent("Check setup2");
    });

    it("never reuses the filter row's 'Needs attention' wording", () => {
      // The module header renders a "Needs attention" filter pill counting THIS
      // module's failing records, a few pixels from this one — which counts failures
      // in the records it was set up on. Two controls, one row, same words, different
      // numbers. Guard the wording so they cannot converge again.
      renderHint({
        moduleId: "bills",
        summaries: { vendors: { failedVendors: 1 }, projects: { failed: 2 } },
        loadedModules: new Set(["vendors", "chart-of-accounts", "projects"]),
      });
      advance();
      expect(pin()).not.toHaveTextContent(/needs attention/i);
      expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
    });

    it("counts a single flagged record on the pill", () => {
      renderHint({
        moduleId: "bills",
        summaries: { projects: { failed: 2 } },
        loadedModules: new Set(["vendors", "chart-of-accounts", "projects"]),
      });
      advance();
      expect(pin()).toHaveTextContent("Check setup1");
    });

    it("announces the problem to assistive tech without opening the popover", () => {
      // The alert row is gone, so this off-screen live region is what carries the
      // announcement — the popover is closed and cannot.
      renderHint({
        moduleId: "projects",
        summaries: { customers: { failedCustomers: 2 } },
        loadedModules: new Set(["customers"]),
      });
      advance();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Fix the flagged setup records first — Projects that point at them can fail to sync.",
      );
    });

    it("flags the offending record inside the popover", () => {
      renderHint({
        moduleId: "projects",
        summaries: { customers: { failedCustomers: 2 } },
        loadedModules: new Set(["customers"]),
      });
      advance();
      openPopover();
      expect(screen.getByText("Has issues")).toHaveClass("qbsg-pop-item-state--warn");
    });

    it("flags only the offending record, reporting the clean ones as ok", () => {
      renderHint({
        moduleId: "bills",
        summaries: {
          vendors: { failedVendors: 0, importFailedVendors: 0 },
          "chart-of-accounts": { syncedAccounts: 4 },
          projects: { failed: 2 },
        },
        loadedModules: new Set(["vendors", "chart-of-accounts", "projects"]),
      });
      advance();
      openPopover();
      expect(screen.getByText("Has issues")).toBeInTheDocument();
      expect(screen.getAllByText("No issues")).toHaveLength(2);
    });

    it("clears the warning when the issue disappears before the timer fires", () => {
      const { rerender } = render(
        <ModuleDependencyHint
          moduleId="projects"
          summaries={{ customers: { failedCustomers: 2 } }}
          onNavigate={jest.fn()}
          loadedModules={new Set(["customers"])}
        />,
      );
      rerender(
        <ModuleDependencyHint
          moduleId="projects"
          summaries={{ customers: { failedCustomers: 0 } }}
          onNavigate={jest.fn()}
          loadedModules={new Set(["customers"])}
        />,
      );
      advance();
      expect(pin()).toHaveClass("qbsg-pin--ok");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("clears the warning when the issue disappears after the timer fired", () => {
      const { rerender } = render(
        <ModuleDependencyHint
          moduleId="projects"
          summaries={{ customers: { failedCustomers: 2 } }}
          onNavigate={jest.fn()}
          loadedModules={new Set(["customers"])}
        />,
      );
      advance();
      expect(screen.getByRole("alert")).toBeInTheDocument();

      rerender(
        <ModuleDependencyHint
          moduleId="projects"
          summaries={{ customers: { failedCustomers: 0 } }}
          onNavigate={jest.fn()}
          loadedModules={new Set(["customers"])}
        />,
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(pin()).toHaveClass("qbsg-pin--ok");
    });

    it("never warns about a record whose data has not loaded", () => {
      renderHint({
        moduleId: "projects",
        summaries: { customers: { failedCustomers: 9 } },
        loadedModules: new Set<string>(),
      });
      advance();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(pin()).toHaveClass("qbsg-pin--neutral");
    });

    it.each([
      ["failedCustomers", { failedCustomers: 1 }],
      ["importFailedCustomers", { importFailedCustomers: 1 }],
      ["syncFailedCustomers", { syncFailedCustomers: 1 }],
    ])("detects customer issues from %s", (_field, customers) => {
      renderHint({
        moduleId: "projects",
        summaries: { customers },
        loadedModules: new Set(["customers"]),
      });
      advance();
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    it.each([
      ["failedVendors", { failedVendors: 1 }],
      ["importFailedVendors", { importFailedVendors: 1 }],
    ])("detects vendor issues from %s", (_field, vendors) => {
      renderHint({
        moduleId: "bills",
        summaries: { vendors },
        loadedModules: new Set(["vendors"]),
      });
      advance();
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    it.each([
      ["failedEmployees", { failedEmployees: 1 }],
      ["importFailedEmployees", { importFailedEmployees: 1 }],
      ["syncFailedEmployees", { syncFailedEmployees: 1 }],
    ])("detects employee issues from %s", (_field, employees) => {
      renderHint({
        moduleId: "timelogs",
        summaries: { employees },
        loadedModules: new Set(["employees"]),
      });
      advance();
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    it.each([
      ["failedBills", { failedBills: 1 }],
      ["importFailedBills", { importFailedBills: 1 }],
      ["syncFailedBills", { syncFailedBills: 1 }],
    ])("detects bill issues from %s via the bill-payments relationship", (_field, bills) => {
      renderHint({
        moduleId: "bill-payments",
        summaries: { bills },
        loadedModules: new Set(["bills"]),
      });
      advance();
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    it("detects project issues from the failed count", () => {
      renderHint({
        moduleId: "bills",
        summaries: { projects: { failed: 3 } },
        loadedModules: new Set(["projects"]),
      });
      advance();
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    it("detects service issues from the failed count", () => {
      renderHint({
        moduleId: "timelogs",
        summaries: { services: { failed: 2 } },
        loadedModules: new Set(["services"]),
      });
      advance();
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    it("never raises an issue for chart-of-accounts", () => {
      renderHint({
        moduleId: "bills",
        // chart-of-accounts has no failure counters, so it can never be the offender.
        summaries: { "chart-of-accounts": { syncedAccounts: 0 } },
        loadedModules: new Set(["chart-of-accounts"]),
      });
      advance();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("does not warn when every failure counter is zero", () => {
      renderHint({
        moduleId: "bill-payments",
        summaries: { bills: { failedBills: 0, importFailedBills: 0, syncFailedBills: 0 } },
        loadedModules: new Set(["bills"]),
      });
      advance();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(pin()).toHaveClass("qbsg-pin--ok");
    });

    it("warns for timelogs when a record is flagged even though all are loaded", () => {
      renderHint({
        moduleId: "timelogs",
        summaries: fullBag,
        loadedModules: new Set(["employees", "projects", "services"]),
      });
      advance();
      expect(pin()).toHaveClass("qbsg-pin--warn");
      expect(pin()).not.toHaveClass("qbsg-pin--ok");
    });
  });
});

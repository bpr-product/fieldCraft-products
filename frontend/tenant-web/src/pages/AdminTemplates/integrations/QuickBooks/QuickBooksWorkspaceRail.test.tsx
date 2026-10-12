import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import QuickBooksWorkspaceRail, {
  QuickBooksWorkspaceRail as NamedRail,
  RailGroup,
} from "./QuickBooksWorkspaceRail";

const groups: RailGroup[] = [
  {
    label: "Accounting",
    modules: [
      { id: "vendors", label: "Vendors", icon: <span />, count: 12 },
      { id: "bills", label: "Bills", icon: <span />, attention: 3 },
    ],
  },
  {
    label: "Time & Payroll",
    modules: [{ id: "employees", label: "Employees", icon: <span /> }],
  },
];

type RailProps = React.ComponentProps<typeof QuickBooksWorkspaceRail>;

function renderRail(overrides: Partial<RailProps> = {}) {
  const props: RailProps = {
    groups,
    selectedId: "vendors",
    onSelect: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<QuickBooksWorkspaceRail {...props} />) };
}

describe("QuickBooksWorkspaceRail", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QuickBooksWorkspaceRail).toBe(NamedRail);
  });

  it("renders each group label", () => {
    renderRail();
    expect(screen.getByText("Accounting")).toBeInTheDocument();
    expect(screen.getByText("Time & Payroll")).toBeInTheDocument();
  });

  it("renders a button for each module", () => {
    renderRail();
    expect(screen.getByRole("button", { name: /Vendors/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bills/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Employees/ })).toBeInTheDocument();
  });

  it("calls onSelect with the module id when a module is clicked", () => {
    const onSelect = jest.fn();
    renderRail({ onSelect });
    fireEvent.click(screen.getByRole("button", { name: /Employees/ }));
    expect(onSelect).toHaveBeenCalledWith("employees");
  });

  it("marks the selected module with aria-current", () => {
    renderRail({ selectedId: "vendors" });
    expect(screen.getByRole("button", { name: /Vendors/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /Employees/ })).not.toHaveAttribute("aria-current");
  });

  it("shows a neutral count when there is nothing to flag", () => {
    renderRail();
    const count = screen.getByText("12");
    expect(count).toHaveClass("qbw-mod-count");
  });

  it("shows an amber attention badge when a module has items needing attention", () => {
    renderRail();
    const badge = screen.getByText("3");
    expect(badge).toHaveClass("qbw-mod-badge");
  });

  it("renders the status label in the header", () => {
    renderRail({ statusLabel: "Both connected" });
    expect(screen.getByText("Both connected")).toBeInTheDocument();
  });

  it("toggles collapse when the header is clicked, updating aria-expanded", () => {
    renderRail();
    const header = screen.getByRole("button", { name: "Collapse QuickBooks menu" });
    expect(header).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    // The accessible label flips to the expand affordance.
    expect(screen.getByRole("button", { name: "Expand QuickBooks menu" })).toBe(header);
  });

  it("hides the header when showHeader is false", () => {
    renderRail({ showHeader: false });
    expect(screen.queryByRole("button", { name: /QuickBooks menu/i })).not.toBeInTheDocument();
  });

  it("renders footer content when provided", () => {
    renderRail({ footer: <div>Footer content</div> });
    expect(screen.getByText("Footer content")).toBeInTheDocument();
  });

  it("renders no footer region when footer is omitted", () => {
    const { container } = renderRail();
    expect(container.querySelector(".qbw-rail-foot")).toBeNull();
  });
});

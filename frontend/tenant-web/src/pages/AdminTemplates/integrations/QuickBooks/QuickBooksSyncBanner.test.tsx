import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import QuickBooksSyncBanner, { QuickBooksSyncBanner as NamedBanner } from "./QuickBooksSyncBanner";

describe("QuickBooksSyncBanner", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QuickBooksSyncBanner).toBe(NamedBanner);
  });

  it("renders the title inside a status region", () => {
    render(<QuickBooksSyncBanner title="Syncing vendors" />);
    const region = screen.getByRole("status");
    expect(region).toBeInTheDocument();
    expect(screen.getByText("Syncing vendors")).toBeInTheDocument();
  });

  it("renders the optional detail line when provided", () => {
    render(<QuickBooksSyncBanner title="Syncing vendors" detail="Processing 12 of 40" />);
    expect(screen.getByText("Processing 12 of 40")).toBeInTheDocument();
  });

  it("does not render a detail element when detail is omitted", () => {
    const { container } = render(<QuickBooksSyncBanner title="Syncing vendors" />);
    expect(container.querySelector(".qbw-syncbar-detail")).toBeNull();
  });

  it("tidies a single-word status by capitalising it", () => {
    render(<QuickBooksSyncBanner title="Syncing" status="RUNNING" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("replaces underscores with spaces and capitalises only the first word", () => {
    render(<QuickBooksSyncBanner title="Syncing" status="PENDING_PUSH" />);
    expect(screen.getByText("Pending push")).toBeInTheDocument();
  });

  it("shows 'In progress' when status is empty, null, or undefined", () => {
    const { rerender } = render(<QuickBooksSyncBanner title="Syncing" status="" />);
    expect(screen.getByText("In progress")).toBeInTheDocument();

    rerender(<QuickBooksSyncBanner title="Syncing" status={null} />);
    expect(screen.getByText("In progress")).toBeInTheDocument();

    rerender(<QuickBooksSyncBanner title="Syncing" />);
    expect(screen.getByText("In progress")).toBeInTheDocument();
  });
});

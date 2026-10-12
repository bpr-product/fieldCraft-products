import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import QbStatusCell, { QbStatusCell as NamedStatusCell, syncTone, syncLabel } from "./QuickBooksStatus";

describe("syncTone", () => {
  it("maps SYNCED to 'ok'", () => {
    expect(syncTone("SYNCED")).toBe("ok");
  });

  it("maps pending-ish states to 'wait'", () => {
    expect(syncTone("PENDING_PUSH")).toBe("wait");
    expect(syncTone("PENDING_PULL")).toBe("wait");
    expect(syncTone("SYNCING")).toBe("wait");
  });

  it("maps failure states to 'bad'", () => {
    expect(syncTone("FAILED")).toBe("bad");
    expect(syncTone("SYNC_FAILED")).toBe("bad");
  });

  it("maps attention states to 'warn'", () => {
    expect(syncTone("ASSIGNMENT_NEEDED")).toBe("warn");
    expect(syncTone("CONFLICT")).toBe("warn");
    expect(syncTone("IMPORT_FAILED")).toBe("warn");
    expect(syncTone("SYNCED_INCOMPLETE")).toBe("warn");
    expect(syncTone("AWAITING_APPROVAL")).toBe("warn");
    expect(syncTone("INVALID")).toBe("warn");
  });

  it("maps NOT_READY to 'warn'", () => {
    expect(syncTone("NOT_READY")).toBe("warn");
  });

  it("falls back to 'wait' for unknown, null, and undefined statuses", () => {
    expect(syncTone("SOMETHING_ELSE")).toBe("wait");
    expect(syncTone(undefined)).toBe("wait");
    expect(syncTone(null)).toBe("wait");
    expect(syncTone("")).toBe("wait");
  });

  it("is case-insensitive", () => {
    expect(syncTone("synced")).toBe("ok");
    expect(syncTone("failed")).toBe("bad");
  });
});

describe("syncLabel", () => {
  it("maps SYNCED to 'Synced'", () => {
    expect(syncLabel("SYNCED")).toBe("Synced");
  });

  it("maps pending states to 'Pending'", () => {
    expect(syncLabel("PENDING_PUSH")).toBe("Pending");
    expect(syncLabel("SYNCING")).toBe("Pending");
  });

  it("maps failure states to 'Failed'", () => {
    expect(syncLabel("FAILED")).toBe("Failed");
    expect(syncLabel("SYNC_FAILED")).toBe("Failed");
  });

  it("maps attention states to 'Needs attention'", () => {
    expect(syncLabel("ASSIGNMENT_NEEDED")).toBe("Needs attention");
    expect(syncLabel("CONFLICT")).toBe("Needs attention");
  });

  it("returns 'Not synced' when unmapped and linked is false", () => {
    expect(syncLabel("MYSTERY", false)).toBe("Not synced");
    expect(syncLabel(undefined, false)).toBe("Not synced");
  });

  it("returns 'Pending' when unmapped and linked is true or omitted", () => {
    expect(syncLabel("MYSTERY", true)).toBe("Pending");
    expect(syncLabel("MYSTERY")).toBe("Pending");
    expect(syncLabel(undefined)).toBe("Pending");
  });
});

describe("QbStatusCell", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QbStatusCell).toBe(NamedStatusCell);
  });

  it("renders the mapped label for a status", () => {
    render(<QbStatusCell status="SYNCED" />);
    expect(screen.getByText("Synced")).toBeInTheDocument();
  });

  it("applies the tone class matching the status", () => {
    render(<QbStatusCell status="SYNCED" />);
    expect(screen.getByText("Synced")).toHaveClass("qbw-spill", "qbw-spill--ok");
  });

  it("applies the 'bad' tone class for failed statuses", () => {
    render(<QbStatusCell status="FAILED" />);
    expect(screen.getByText("Failed")).toHaveClass("qbw-spill--bad");
  });

  it("shows 'Not synced' when there is no status and linked is false", () => {
    render(<QbStatusCell linked={false} />);
    expect(screen.getByText("Not synced")).toBeInTheDocument();
  });

  it("renders an override label when the label prop is provided", () => {
    render(<QbStatusCell status="SYNCED" label="Custom text" />);
    expect(screen.getByText("Custom text")).toBeInTheDocument();
    expect(screen.queryByText("Synced")).not.toBeInTheDocument();
  });

  it("renders the info icon with an accessible label when a reason is given", () => {
    render(<QbStatusCell status="ASSIGNMENT_NEEDED" reason="No project linked" />);
    expect(screen.getByRole("img", { name: "Details: No project linked" })).toBeInTheDocument();
  });

  it("does not render an info icon when there is no reason", () => {
    render(<QbStatusCell status="SYNCED" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});

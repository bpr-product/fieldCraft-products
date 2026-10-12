import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import QuickBooksMissingRecordNote, {
  QuickBooksMissingRecordNote as NamedNote,
} from "./QuickBooksMissingRecordNote";

const trigger = (noun: string) => screen.getByRole("button", { name: `About missing ${noun} records` });

describe("QuickBooksMissingRecordNote", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QuickBooksMissingRecordNote).toBe(NamedNote);
  });

  it("renders only a collapsed trigger until it is opened", () => {
    render(<QuickBooksMissingRecordNote noun="customer" />);
    expect(trigger("customer")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Don’t see the customer here\?/)).toBeNull();
  });

  it("names the trigger after the module's own record type", () => {
    render(<QuickBooksMissingRecordNote noun="vendor" />);
    expect(trigger("vendor")).toBeInTheDocument();
  });

  it("reveals the message on click", () => {
    render(<QuickBooksMissingRecordNote noun="customer" />);
    fireEvent.click(trigger("customer"));

    expect(trigger("customer")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Don’t see the customer here\?/)).toBeInTheDocument();
    expect(
      screen.getByText("Refresh or update the customer, then run the sync again."),
    ).toBeInTheDocument();
  });

  it("points the trigger at the panel it controls", () => {
    render(<QuickBooksMissingRecordNote noun="customer" />);
    fireEvent.click(trigger("customer"));

    const panelId = trigger("customer").getAttribute("aria-controls");
    expect(panelId).toBeTruthy();
    expect(document.getElementById(panelId as string)).toBeInTheDocument();
  });

  it("closes again when the trigger is clicked a second time", () => {
    render(<QuickBooksMissingRecordNote noun="customer" />);
    fireEvent.click(trigger("customer"));
    fireEvent.click(trigger("customer"));

    expect(trigger("customer")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Don’t see the customer here\?/)).toBeNull();
  });

  it("closes on Escape", () => {
    render(<QuickBooksMissingRecordNote noun="employee" />);
    fireEvent.click(trigger("employee"));
    fireEvent.keyDown(document, { key: "Escape", code: "Escape" });

    expect(screen.queryByText(/Don’t see the employee here\?/)).toBeNull();
  });

  it("closes when the pointer goes down outside it", () => {
    render(
      <div>
        <QuickBooksMissingRecordNote noun="customer" />
        <button type="button">Elsewhere</button>
      </div>,
    );
    fireEvent.click(trigger("customer"));
    fireEvent.mouseDown(screen.getByRole("button", { name: "Elsewhere" }));

    expect(screen.queryByText(/Don’t see the customer here\?/)).toBeNull();
  });

  it("stays open when the pointer goes down inside it", () => {
    render(<QuickBooksMissingRecordNote noun="customer" />);
    fireEvent.click(trigger("customer"));
    fireEvent.mouseDown(screen.getByText(/Don’t see the customer here\?/));

    expect(screen.getByText(/Don’t see the customer here\?/)).toBeInTheDocument();
  });

  it("resolves its copy from en.json rather than leaking raw i18n keys", () => {
    const { container } = render(<QuickBooksMissingRecordNote noun="customer" />);
    fireEvent.click(trigger("customer"));

    expect(container.textContent).not.toContain("quickbooks.missingRecordNote");
    expect(trigger("customer").getAttribute("aria-label")).not.toContain("quickbooks.");
  });

  it("uses the supplied noun throughout the message for each module", () => {
    const { rerender, container } = render(<QuickBooksMissingRecordNote noun="customer" />);
    fireEvent.click(trigger("customer"));
    expect(container.textContent).toContain("Refresh or update the customer");

    rerender(<QuickBooksMissingRecordNote noun="vendor" />);
    expect(container.textContent).toContain("Refresh or update the vendor");

    rerender(<QuickBooksMissingRecordNote noun="employee" />);
    expect(container.textContent).toContain("Refresh or update the employee");
  });
});

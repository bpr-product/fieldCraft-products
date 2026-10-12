/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for EdenThinkingRow -- the activity-labelled typing row
*                that replaces the old generic "EDEN is thinking" bubble.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { render, screen } from "@testing-library/react";
import EdenThinkingRow from "./EdenThinkingRow";

describe("EdenThinkingRow", () => {
  test("renders the activity label as visible text and as its accessible name", () => {
    render(<EdenThinkingRow label="Checking invoices…" />);
    expect(screen.getByText("Checking invoices…")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Checking invoices…" })).toBeInTheDocument();
  });

  test("announces politely (aria-live=polite) so it never interrupts", () => {
    render(<EdenThinkingRow label="Thinking…" />);
    expect(screen.getByTestId("eden-thinking-row")).toHaveAttribute("aria-live", "polite");
  });

  test("re-labels correctly when the resolved activity changes across renders", () => {
    const { rerender } = render(<EdenThinkingRow label="Thinking…" />);
    expect(screen.getByText("Thinking…")).toBeInTheDocument();

    rerender(<EdenThinkingRow label="Checking the schedule…" />);
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument();
    expect(screen.getByText("Checking the schedule…")).toBeInTheDocument();
  });
});

/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for resolveEdenActivity/useEdenActivity -- the rule order
*                (a current-page entity beats every keyword rule; more specific
*                domains are checked before generic navigation verbs), the long-name
*                truncation, and the empty-utterance fallback.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import { renderHook } from "@testing-library/react";
import { resolveEdenActivity, useEdenActivity, EDEN_ACTIVITY_DEFAULT } from "./useEdenActivity";

describe("resolveEdenActivity", () => {
  test("returns the default for an empty or whitespace-only utterance", () => {
    expect(resolveEdenActivity("")).toBe(EDEN_ACTIVITY_DEFAULT);
    expect(resolveEdenActivity("   ")).toBe(EDEN_ACTIVITY_DEFAULT);
  });

  test("falls back to the default when nothing matches", () => {
    expect(resolveEdenActivity("hello there")).toBe(EDEN_ACTIVITY_DEFAULT);
  });

  test("matches domain keyword rules (invoices, schedule, ledger, materials, contacts)", () => {
    expect(resolveEdenActivity("Where are my unpaid invoices?")).toBe("Checking invoices…");
    expect(resolveEdenActivity("When does the project start?")).toBe("Checking the schedule…");
    expect(resolveEdenActivity("Why did my bid cost change?")).toBe("Reading the ledger…");
    expect(resolveEdenActivity("I need to add a new material")).toBe("Checking materials…");
    expect(resolveEdenActivity("Who is the supervisor on this job?")).toBe("Looking up contacts…");
  });

  test("a specific domain (invoices) wins over the generic navigation verb rule", () => {
    // Contains both "where" (navigation) and "invoice" (billing) -- billing must win
    // because it is listed before the generic verbs.
    expect(resolveEdenActivity("where is my invoice")).toBe("Checking invoices…");
  });

  test("falls back to the generic navigation rule when no specific domain matches", () => {
    expect(resolveEdenActivity("take me to the dashboard")).toBe("Finding the right page…");
  });

  test("a current-page entity beats every keyword rule", () => {
    expect(
      resolveEdenActivity("where are my invoices", { name: "Buma P" } as any)
    ).toBe("Looking up Buma P…");
  });

  test("truncates a long entity name so the row never overflows", () => {
    const longName = "A".repeat(40);
    const label = resolveEdenActivity("anything", { name: longName } as any);
    expect(label.startsWith("Looking up ")).toBe(true);
    expect(label).toContain("…");
    expect(label.length).toBeLessThan(`Looking up ${longName}…`.length);
  });

  test("an entity with a blank name is ignored, falling through to keyword rules", () => {
    expect(resolveEdenActivity("where are my invoices", { name: "  " } as any)).toBe(
      "Checking invoices…"
    );
  });
});

describe("useEdenActivity", () => {
  test("returns a stable callback across re-renders", () => {
    const { result, rerender } = renderHook(() => useEdenActivity());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });

  test("the returned callback delegates to resolveEdenActivity", () => {
    const { result } = renderHook(() => useEdenActivity());
    expect(result.current("where are my invoices")).toBe("Checking invoices…");
    expect(result.current("where are my invoices", { name: "Buma P" } as any)).toBe(
      "Looking up Buma P…"
    );
  });
});

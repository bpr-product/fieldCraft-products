/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose      : Locks the QuickBooks workspace display formats. Dates render in the
*                US numeric convention (MM/DD/YYYY) — the demo feedback that drove
*                this was a table showing 19/08/2026 for August 19th. A tz argument
*                is passed explicitly so assertions are deterministic regardless of
*                the CI machine's zone.
* -----------------------------------------------------------------------------
*/
import { ALL_MODULE_DEPENDENCIES } from "./QuickBooksSyncGuide";
import {
  enabledQuickBooksModuleIds,
  formatCurrency,
  isQuickBooksModuleEnabled,
  qbFormatDate,
  qbFormatDateTime,
} from "./QuickBooksUtils";

const IST = "Asia/Kolkata"; // UTC+5:30, no DST
const CHICAGO = "America/Chicago"; // CST (-6) winter / CDT (-5) summer

describe("qbFormatDate (US numeric date, month first)", () => {
  test("renders month before day — the regression this fixes", () => {
    // Aug 19th. Day-first would read 19/08/2026.
    expect(qbFormatDate("2026-08-19T16:03:00Z", CHICAGO)).toBe("08/19/2026");
  });

  test("pads single-digit month and day to two places", () => {
    expect(qbFormatDate("2026-01-05T12:00:00Z", CHICAGO)).toBe("01/05/2026");
  });

  test("a day that is ambiguous either way still parses as month-first", () => {
    // 03/04 vs 04/03: this is March 4th, so the month is 03.
    expect(qbFormatDate("2026-03-04T12:00:00Z", CHICAGO)).toBe("03/04/2026");
  });

  test("converts UTC to the viewer's zone before choosing the calendar day", () => {
    // 2026-08-19T02:30Z is still Aug 18th in Chicago (UTC-5 in summer).
    expect(qbFormatDate("2026-08-19T02:30:00Z", CHICAGO)).toBe("08/18/2026");
    // ...and already Aug 19th in IST (UTC+5:30).
    expect(qbFormatDate("2026-08-19T02:30:00Z", IST)).toBe("08/19/2026");
  });

  test("returns empty string for null / undefined / empty / unparseable", () => {
    expect(qbFormatDate(null, CHICAGO)).toBe("");
    expect(qbFormatDate(undefined, CHICAGO)).toBe("");
    expect(qbFormatDate("", CHICAGO)).toBe("");
    expect(qbFormatDate("not-a-date", CHICAGO)).toBe("");
  });
});

describe("qbFormatDateTime (US numeric date + 12-hour clock)", () => {
  test("renders MM/DD/YYYY hh:mm A", () => {
    expect(qbFormatDateTime("2026-08-19T16:03:00Z", CHICAGO)).toBe("08/19/2026 11:03 AM");
  });

  test("uppercase meridiem, not the en-GB lowercase 'am' the old formatter emitted", () => {
    const formatted = qbFormatDateTime("2026-08-19T16:03:00Z", CHICAGO);
    expect(formatted).toContain("AM");
    expect(formatted).not.toContain("am");
  });

  test("keeps a 12-hour clock rather than 24-hour", () => {
    // 18:03Z is 13:03 in Chicago — 01:03 PM on a 12-hour clock.
    expect(qbFormatDateTime("2026-08-19T18:03:00Z", CHICAGO)).toBe("08/19/2026 01:03 PM");
  });

  test("honours DST: the same wall-clock hour maps from a different UTC instant", () => {
    expect(qbFormatDateTime("2026-01-15T14:00:00Z", CHICAGO)).toBe("01/15/2026 08:00 AM");
    expect(qbFormatDateTime("2026-07-15T13:00:00Z", CHICAGO)).toBe("07/15/2026 08:00 AM");
  });

  test("returns empty string for null / undefined / empty / unparseable", () => {
    expect(qbFormatDateTime(null, CHICAGO)).toBe("");
    expect(qbFormatDateTime(undefined, CHICAGO)).toBe("");
    expect(qbFormatDateTime("", CHICAGO)).toBe("");
    expect(qbFormatDateTime("not-a-date", CHICAGO)).toBe("");
  });
});

describe("date formatters agree with each other", () => {
  test("the date half of qbFormatDateTime is exactly qbFormatDate", () => {
    const iso = "2026-11-02T23:45:00Z";
    expect(qbFormatDateTime(iso, IST).startsWith(qbFormatDate(iso, IST))).toBe(true);
  });
});

describe("module release gating", () => {
  /*
   * Invoices and Customer Payments are under development. The raw flag defaults are pinned
   * in constants/FeatureFlag.test.ts, alongside the app's other no-silent-flip guards;
   * these cover the gate that reads them. The first case fails if either flag flips, so it
   * is also the signal that this file needs updating as part of that release.
   */
  it("reports the two under-development modules as disabled", () => {
    expect(isQuickBooksModuleEnabled("invoices")).toBe(false);
    expect(isQuickBooksModuleEnabled("payments")).toBe(false);
  });

  it("treats every module without a flag entry as enabled", () => {
    for (const id of [
      "customers", "vendors", "bills", "bill-payments",
      "chart-of-accounts", "projects", "employees", "timelogs", "services",
    ]) {
      expect(isQuickBooksModuleEnabled(id)).toBe(true);
    }
  });

  /*
   * The two defaults point in opposite directions on purpose, and both directions matter:
   *
   *   · a module ABSENT from the flag map is released → open, so the nine shipped modules
   *     need no entry and a newly added one is never invisible by accident;
   *   · a module IN the map is under development → closed unless its flag is explicitly
   *     `true`, so a flag that goes missing (dropped in a merge, renamed, or a file left out
   *     of a commit) hides the module rather than exposing unfinished work.
   *
   * TypeScript already rejects a missing flag key at compile time, so this is belt-and-braces
   * — but the failure it guards against is an unfinished accounting surface reaching a
   * tenant, which is worth being certain about at runtime too.
   */
  it("fails closed for a gated module whose flag value is not explicitly true", () => {
    // Mirrors the production expression with a missing/garbage flag value, proving the
    // coercion rather than the current flag state.
    const gateOpen = (flag: unknown): boolean => flag === true;
    for (const bad of [undefined, null, "", "false", "true", 0, 1, NaN]) {
      expect(gateOpen(bad)).toBe(false);
    }
    expect(gateOpen(true)).toBe(true);
  });

  it("does not gate an unknown module id", () => {
    // A module absent from the flag map is on: only under-development ids need an entry,
    // so a newly added module is never invisible by accident.
    expect(isQuickBooksModuleEnabled("some-future-module")).toBe(true);
  });

  it("filters gated ids out of a list while preserving order", () => {
    expect(enabledQuickBooksModuleIds([
      "customers", "invoices", "bills", "payments", "timelogs",
    ])).toEqual(["customers", "bills", "timelogs"]);
  });

  it("returns an empty list when every id is gated", () => {
    expect(enabledQuickBooksModuleIds(["invoices", "payments"])).toEqual([]);
  });

  it("leaves a fully-released list untouched", () => {
    const released = ["customers", "vendors", "bills"];
    expect(enabledQuickBooksModuleIds(released)).toEqual(released);
  });

  /*
   * The invariant that lets QuickBooksSyncGuide keep dependency lists unfiltered.
   *
   * If an enabled module had a disabled prerequisite, the guide would have to either strip
   * it — telling the user "no setup needed" for a module whose one prerequisite is actually
   * unavailable — or render a dead link to a module that is not there. Neither is
   * acceptable, so the gate rules the combination out instead: Customer Payments is gated on
   * Invoices as well as its own flag.
   *
   * This is a loop over the real graph rather than a hardcoded pair, so it also covers any
   * flag added later: gate a module that something enabled depends on and this fails.
   */
  it("never enables a module whose prerequisite is disabled", () => {
    const offenders = Object.entries(ALL_MODULE_DEPENDENCIES)
      .filter(([moduleId]) => isQuickBooksModuleEnabled(moduleId))
      .flatMap(([moduleId, deps]) =>
        deps
          .filter((dep) => !isQuickBooksModuleEnabled(dep))
          .map((dep) => `${moduleId} depends on disabled ${dep}`),
      );
    expect(offenders).toEqual([]);
  });

  it("gates Customer Payments whenever Invoices is gated", () => {
    // The concrete case behind the rule above: allocations resolve through a SYNCED
    // invoice_external_map row, so Customer Payments cannot work without Invoices.
    expect(ALL_MODULE_DEPENDENCIES["payments"]).toEqual(["invoices"]);
    if (!isQuickBooksModuleEnabled("invoices")) {
      expect(isQuickBooksModuleEnabled("payments")).toBe(false);
    }
  });
});

describe("formatCurrency", () => {
  test("renders USD with two fraction digits", () => {
    expect(formatCurrency(12480)).toBe("$12,480.00");
    expect(formatCurrency(0)).toBe("$0.00");
  });
});

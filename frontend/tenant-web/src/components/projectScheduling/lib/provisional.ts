// projectScheduling/lib/provisional.ts — the PROVISIONAL task marker (attribute 567).
//
// A provisional task is one the engine KNOWINGLY OWES A RECOMPUTE, and it names who
// pays and when: a deferred budget chunk (the engine's next chunk finishes it), an
// island still settling (the child solve finishes it), or work blocked on an
// undecidable input (whatever unblocks it finishes it). Distance is not doubt and
// reachability is not doubt — neither of those marks a task any more.
//
// It therefore clears BY ITSELF, in seconds to minutes, without anyone pressing
// anything. There is no Reload / Re-plan / Refresh affordance and there must not be
// one: the old button re-planned without the job-start anchor that produced the dates,
// so pressing it destroyed correct dates (ADO #28358). The honest UI is "these dates
// are still confirming", never "click here to fix them".
//
// Single source of truth for: is this task provisional (isProvisional), what the user
// is told (PROVISIONAL_LABEL / PROVISIONAL_TOOLTIP), and — through provisionalSinceDate
// — the ONE place the marker is resolved, so the grid indicator, the canvas bar paint,
// the bar tooltip and the edit shield can never disagree about which tasks are marked.
//
// ─────────────────────────────────────────────────────────────────────────────
// D-NO-GREY (owner ruling, Riyaz, 2026-09-04 17:32 IST), verbatim: *"go retire,
// but no grey at all. If it is parked then just status is parked, no grey at all."*
//
// PROVISIONAL IS NO LONGER A USER-VISIBLE STATE. The engine keeps its internal
// ledger for the two bookkeeping cases (a deferred budget chunk and an island still
// settling, each with an engine finalizer that runs in seconds to minutes), and the
// third case — a successor of a PARKED task — is no longer surfaced either: a parked
// task shows its own status, "parked", with its reason where the receipt shows it,
// and nothing on the board is painted grey or italic to suggest a date is in doubt.
//
// The retirement is STRUCTURAL, not a switch. `provisionalSinceDate` below now
// returns null for every task, so `isProvisional` is false everywhere, the tooltip is
// null everywhere and the edit shield never holds — and the feature flag that used to
// gate all of this (`ENABLE_PROVISIONAL_INDICATOR`) is REMOVED, exactly as its own
// predecessor `ENABLE_PROVISIONAL_REFRESH` was, so no flag flip can bring the grey
// back. The engine half is retired in the same change: nothing writes task attribute
// 567 from distance any more, and no API or UI-delta payload carries a provisional
// flag, so the field this module reads is not sent at all.
//
// The exported names, signatures and constants are DELIBERATELY KEPT. Every consumer
// (ganttAdapter's bar paint, GanttGridV2's label, provisionalShield, the edit gate)
// reaches the marker through this one function, so retiring it here retires all of
// them at once with no diff in those files — which matters while `lib/ganttAdapter.ts`
// sits in a pending dev merge. Their provisional branches are now unreachable.
// See docs/design/reschedule-v2-split-solve-design.md §8 and V249.
// ─────────────────────────────────────────────────────────────────────────────

import { Task } from "../types";

/**
 * The short form — an aria-label, a badge, a one-line status. Present tense and
 * self-resolving on purpose: it reports a state the system is working through, and
 * asks the user for nothing.
 */
export const PROVISIONAL_LABEL = "Dates confirming…";

/**
 * The one user-facing explanation of a greyed bar/row. Says what is happening (still
 * settling), that it ends without help, and — explicitly — that there is nothing to
 * press. The wording is the subject of §15 open ruling 1 in the V2 living spec and is
 * NOT settled; changing it is this one constant.
 */
export const PROVISIONAL_TOOLTIP =
  "Dates confirming… — the schedule is still settling this task. It finishes on its own; nothing to do.";

/** Grey used for a provisional bar, per theme. Deliberately outside the status palette
 *  (ganttAdapter's STATUS_COLOR_*) — provisional is orthogonal to status: a task can be
 *  SCHEDULED *and* provisional, and the grey must read as "not settled yet" rather
 *  than as any particular status. */
export const PROVISIONAL_BAR_COLOR_LIGHT = "#94a3b8";
export const PROVISIONAL_BAR_COLOR_DARK = "#64748b";

/**
 * True when this task carries a VALID provisional marker.
 *
 * Valid = a non-blank string that parses as a real date. A blank string, a null, an
 * absent field (the API has not surfaced attribute 567 yet) or unparseable junk all
 * mean "not provisional" — the board must fail SAFE, showing normal bars, rather than
 * greying the whole schedule out on a malformed payload.
 */
export function isProvisional(task: Pick<Task, "provisionalSince"> | null | undefined): boolean {
  return provisionalSinceDate(task) !== null;
}

/**
 * RETIRED BY D-NO-GREY (2026-09-04) — always null, for every task.
 *
 * THE RETIREMENT IS EXPRESSED HERE, and only here. Every other consumer —
 * isProvisional, the tooltip, the shield's isTaskProvisional, the bar paint, the grid
 * label, the edit gate — reaches the marker through this function, so returning null
 * unconditionally returns the board to its pre-feature painting across all five
 * surfaces with no change in any of them.
 *
 * The `task` parameter is kept so every call site compiles unchanged, and the parsing
 * that used to live here is gone rather than dead-branched: a stamp that survives on a
 * tenant row (attribute 567 written before this ruling) is inert, not merely ignored.
 * A future re-introduction of a legitimately user-visible provisional state would put
 * its own rule here, under its own ruling — it must not resurrect this one.
 */
export function provisionalSinceDate(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  task: Pick<Task, "provisionalSince"> | null | undefined
): Date | null {
  return null;
}

/**
 * Tooltip text for a provisional task: the standard explanation, plus the date the
 * marker was written when it is known. Returns null for a non-provisional task so
 * callers can `?? ` straight onto their normal tooltip.
 */
export function provisionalTooltip(
  task: Pick<Task, "provisionalSince"> | null | undefined
): string | null {
  const since = provisionalSinceDate(task);
  if (!since) return null;
  return `${PROVISIONAL_TOOLTIP} (since ${since.toLocaleDateString()})`;
}

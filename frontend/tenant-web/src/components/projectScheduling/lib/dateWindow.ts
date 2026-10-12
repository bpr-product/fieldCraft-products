// projectScheduling/lib/dateWindow.ts — Sequential date-window filter (LAYER C).
//
// Translates a user-facing date-window preset ("day"/"week"/"month"/"custom"/"all")
// into an absolute [fromMin, toMin) minute range (relative to ganttV2/lib/time's
// TODAY baseline) that the Sequential row builder can compare task startMin/endMin
// against. Pure function, no store dependency, so it's independently unit-testable.

import { nowMin, startOfDay, startOfWeek, startOfMonth, minToDate, TODAY, MIN_PER_DAY } from "../../ganttV2/lib/time";

export type DateWindowPreset = "all" | "day" | "week" | "month" | "custom";

export interface DateWindow {
  preset: DateWindowPreset;
  from: string | null;
  to: string | null;
}

export interface MinRange {
  fromMin: number;
  toMin: number;
}

export function dateWindowToMinRange(dw: DateWindow): MinRange | null {
  switch (dw.preset) {
    case "all":
      return null;
    case "day": {
      const fromMin = startOfDay(nowMin());
      return { fromMin, toMin: fromMin + MIN_PER_DAY };
    }
    case "week": {
      const fromMin = startOfWeek(nowMin());
      return { fromMin, toMin: fromMin + 7 * MIN_PER_DAY };
    }
    case "month": {
      const fromMin = startOfMonth(nowMin());
      const d = minToDate(fromMin);
      d.setMonth(d.getMonth() + 1);
      const toMin = Math.round((d.getTime() - TODAY.getTime()) / 60000);
      return { fromMin, toMin };
    }
    case "custom": {
      if (dw.from == null && dw.to == null) return null;
      // Only one bound set → the other stays open-ended (±Infinity) rather than
      // collapsing to null, so a PM can filter "everything from this date on" (or
      // "everything up to this date") without having to pick both ends.
      const fromMin = dw.from == null
        ? -Infinity
        : Math.round((new Date(`${dw.from}T00:00:00`).getTime() - TODAY.getTime()) / 60000);
      const toMin = dw.to == null
        ? Infinity
        : Math.round((new Date(`${dw.to}T23:59:59.999`).getTime() - TODAY.getTime()) / 60000);
      // Normalize an inverted range (from > to, e.g. a user picks the dates out of
      // order) so the window is never empty — swap rather than silently return nothing.
      return fromMin > toMin ? { fromMin: toMin, toMin: fromMin } : { fromMin, toMin };
    }
    default:
      return null;
  }
}

// ganttV2/lib/time.ts — minute-based time helpers (ported from data.jsx)

export const MIN_PER_HOUR = 60;
export const MIN_PER_DAY = 1440;
export const WORK_DAY_START = 7;   // 7:00 AM
export const WORK_DAY_END = 17;    // 5:00 PM
export const WORK_MIN_PER_DAY = (WORK_DAY_END - WORK_DAY_START) * 60; // 600

// TODAY = today at 7:00 AM local. Stable for the session lifetime.
export const TODAY = (() => {
  const d = new Date();
  d.setHours(WORK_DAY_START, 0, 0, 0);
  return d;
})();

export function minToDate(m: number): Date {
  return new Date(TODAY.getTime() + m * 60_000);
}

export function fmtDateTime(m: number): string {
  const d = minToDate(m);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtTime(m: number): string {
  const d = minToDate(m);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Full ISO-ish datetime (`YYYY-MM-DDTHH:mm:ssZ`) matching the shape the tenant API
 * emits for task start/end times. Inline edits must write this format (not a bare
 * `HH:mm`) so downstream consumers — the grid cell's `slice(11,16)`, InfoTab's
 * `dayjs(...)`, the edit payload builder — all read it back correctly.
 */
export function fmtIsoDateTime(m: number): string {
  // Persist the REAL UTC instant of the (browser-local) minute position. minToDate(m)
  // is a true Date, so .toISOString() is the correct UTC — this round-trips through the
  // UTC-honoring read helpers (isoToMin/toMin) and stores the right instant on edit.
  // (Was: local getters + a literal "Z" — the pre-migration "fake-Z" convention, which
  // wrote local-wall-clock-labelled-UTC and mis-shifted once storage became real UTC.)
  return minToDate(m).toISOString();
}

export function fmtDate(m: number): string {
  const d = minToDate(m);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fmtDuration(mins: number): string {
  const total = Math.round(mins);
  if (total <= 0) return "0m";
  if (total < 60) return `${total}m`;
  const hWhole = Math.floor(total / 60);
  const mRem = total % 60;
  if (mRem === 0) return `${hWhole}h`;
  return `${hWhole}h ${mRem}m`;
}

const MIN_PER_WEEK = 7 * MIN_PER_DAY;

/**
 * Magnitude-only duration text for a signed gap (e.g. baseline vs. current
 * variance), which can legitimately be large in either direction depending on
 * scheduling preferences — not just same-day drift. Below 24h this is
 * identical to fmtDuration; at/above 24h it switches to whole-day + remainder-
 * hour granularity ("-17d 3h"); at/above 7 days it switches again to
 * whole-week + remainder-day granularity ("-8w 6d") so a multi-month drift
 * reads as weeks instead of an ever-growing day count. Caller applies the
 * +/- sign separately.
 */
export function fmtVarianceDuration(absMins: number): string {
  const total = Math.round(absMins);
  if (total < MIN_PER_DAY) return fmtDuration(total);
  if (total < MIN_PER_WEEK) {
    const dWhole = Math.floor(total / MIN_PER_DAY);
    const hRem = Math.floor((total % MIN_PER_DAY) / MIN_PER_HOUR);
    if (hRem === 0) return `${dWhole}d`;
    return `${dWhole}d ${hRem}h`;
  }
  const wWhole = Math.floor(total / MIN_PER_WEEK);
  const dRem = Math.floor((total % MIN_PER_WEEK) / MIN_PER_DAY);
  if (dRem === 0) return `${wWhole}w`;
  return `${wWhole}w ${dRem}d`;
}

/** "HH:MM" for editing — matches TaskDialog's InfoTab duration-field onFocus format. */
export function fmtHHMM(mins: number): string {
  const total = Math.round(mins);
  if (!total || total <= 0) return "";
  const h = Math.floor(total / 60);
  const m = total % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** Parses "HH:MM" or a bare minute-count string into total minutes. Matches
 *  TaskDialog's InfoTab duration-field onBlur parsing exactly. Returns null if empty. */
export function parseHHMMToMinutes(input: string): number | null {
  if (!input) return null;
  let h = 0;
  let m = 0;
  if (input.includes(":")) {
    const [hStr, mStr] = input.split(":");
    h = Math.max(0, parseInt(hStr || "0", 10));
    m = Math.max(0, Math.min(59, parseInt(mStr || "0", 10)));
  } else {
    h = Math.max(0, parseInt(input, 10));
  }
  const totalHours = parseFloat((h + m / 60).toFixed(4));
  return Math.round(totalHours * 60);
}

/** Minute offset for a given day offset and hour (relative to TODAY 7am). */
export function dayHourMin(dayOffset: number, hour: number, minute = 0): number {
  return dayOffset * MIN_PER_DAY + (hour - WORK_DAY_START) * 60 + minute;
}

/** Current wall-clock minute offset from TODAY. */
export function nowMin(): number {
  return Math.round((Date.now() - TODAY.getTime()) / 60_000);
}

// ===== Canvas timeline tick helpers =====

export function startOfHour(m: number): number {
  const d = minToDate(m);
  d.setMinutes(0, 0, 0);
  return Math.round((d.getTime() - TODAY.getTime()) / 60_000);
}

export function startOfDay(m: number): number {
  const d = minToDate(m);
  d.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - TODAY.getTime()) / 60_000);
}

export function startOfWeek(m: number): number {
  const d = minToDate(m);
  const dow = d.getDay();
  d.setDate(d.getDate() - dow);
  d.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - TODAY.getTime()) / 60_000);
}

export function startOfMonth(m: number): number {
  const d = minToDate(m);
  d.setDate(1);
  d.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - TODAY.getTime()) / 60_000);
}

export function formatTimeShort(m: number, scale: string): string {
  const d = minToDate(m);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (scale === "5min" || scale === "15min" || scale === "30min") {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  if (scale === "hour") {
    const h = d.getHours();
    return h === 0 ? "12am" : h < 12 ? `${h}am` : h === 12 ? "12pm" : `${h - 12}pm`;
  }
  if (scale === "day") {
    return `${d.getMonth() + 1}/${d.getDate()}`;
  }
  // week
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function formatDayHeader(m: number, scale: string): string {
  const d = minToDate(m);
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  if (scale === "5min" || scale === "15min" || scale === "30min" || scale === "hour") {
    return `${days[d.getDay()]} ${months[d.getMonth()]} ${d.getDate()}`;
  }
  if (scale === "day") {
    return `${months[d.getMonth()]} ${d.getFullYear()}`;
  }
  return `${months[d.getMonth()]} ${d.getFullYear()}`;
}

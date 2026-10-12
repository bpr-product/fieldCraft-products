/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose      : UTC-first date utility. The UI is the only tz-aware boundary:
*                it converts UTC (from the API) to the user's local zone for
*                display (fromUtc / formatDateTime / formatDateOnly / formatTime),
*                and converts local input back to UTC (toUtcIso) before sending.
*                See utcDateUtil.test.ts for the locked contract.
* -----------------------------------------------------------------------------
*/
import dayjs, { Dayjs } from "dayjs";
import utc from "dayjs/plugin/utc";
import timezone from "dayjs/plugin/timezone";

dayjs.extend(utc);
dayjs.extend(timezone);

// navigator.language carries the full regional locale ("en-GB", "de-DE").
// localStorage['i18nextLng'] is the i18next translation key ("en", "fr") — it does NOT
// distinguish region, so it must never be used for date/time format decisions.
export const getDisplayLocale = (): string => {
  if (typeof navigator !== "undefined" && navigator.language) {
    const parts = navigator.language.split("-");
    return parts.length >= 2 ? `${parts[0]}-${parts[1]}` : parts[0];
  }
  return "en-US";
};

export const userTz = (): string => {
  return dayjs.tz.guess();
};

export const fromUtc = (iso?: string | null, tz: string = userTz()): Dayjs | null => {
  if (!iso) return null;
  const d = dayjs.utc(iso);
  if (!d.isValid()) return null;
  return d.tz(tz);
};

// A trailing Z or numeric offset means the string is already an absolute
// instant — re-interpreting its wall-clock in tz would double-convert.
const HAS_EXPLICIT_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;

export const toUtcIso = (value?: Dayjs | string | Date | null, tz: string = userTz()): string | null => {
  if (!value) return null;
  let d: Dayjs;
  if (typeof value === "string") {
    const raw = value.trim();
    if (!raw) return null;
    try {
      d = HAS_EXPLICIT_OFFSET.test(raw) ? dayjs(raw) : dayjs.tz(raw, tz);
    } catch {
      // dayjs.tz throws (RangeError) on unparseable input instead of returning
      // an invalid instance — the contract here is null, never a throw.
      return null;
    }
  } else {
    d = dayjs(value);
  }
  if (!d.isValid()) return null;
  return d.utc().toISOString();
};

export const formatDateTime = (iso?: string | null, tz: string = userTz()): string => {
  const d = fromUtc(iso, tz);
  if (!d) return "";
  return new Intl.DateTimeFormat(getDisplayLocale(), {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", timeZone: tz,
  }).format(d.toDate());
};

export const formatDateOnly = (iso?: string | null, tz: string = userTz()): string => {
  const d = fromUtc(iso, tz);
  if (!d) return "";
  return new Intl.DateTimeFormat(getDisplayLocale(), {
    year: "numeric", month: "short", day: "2-digit", timeZone: tz,
  }).format(d.toDate());
};

export const formatTime = (iso?: string | null, tz: string = userTz()): string => {
  const d = fromUtc(iso, tz);
  if (!d) return "";
  return new Intl.DateTimeFormat(getDisplayLocale(), {
    hour: "2-digit", minute: "2-digit", timeZone: tz,
  }).format(d.toDate());
};

// navigator.language is static for the session (a change requires a page reload), so the
// Intl construction + formatToParts work below is cached per locale. getLocaleDateFormat() is
// on the hot path of the Gantt grid's date cells (gridDateFormat.ts), which renders every
// visible row on every scroll/refresh in a virtualized, scroll-perf-sensitive grid with a
// recorded incident history (see .claude/prompts/fix-29007-gantt-sequential-scroll-performance.md
// and siblings) — an uncached Intl.DateTimeFormat construction per cell is a measurable,
// avoidable cost there. The cache is transparent: same locale in, same string out, always.
const localeDateFormatCache = new Map<string, string>();

// Returns a dayjs format string matching the user's navigator.language date ordering
// (e.g. "MM/DD/YYYY" for en-US, "DD/MM/YYYY" for en-GB, "DD.MM.YYYY" for de-DE).
// Use this as the `format` prop on MUI DatePicker so the picker input respects the
// browser locale — not the OS regional setting that native <input type="date"> follows.
export const getLocaleDateFormat = (): string => {
  const locale = getDisplayLocale();
  const cached = localeDateFormatCache.get(locale);
  if (cached !== undefined) return cached;
  let result: string;
  try {
    const parts = new Intl.DateTimeFormat(locale, {
      year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date("2023-04-15T12:00:00Z"));
    const sep = parts.find(p => p.type === "literal")?.value ?? "/";
    const order = parts
      .filter(p => p.type === "year" || p.type === "month" || p.type === "day")
      .map(p => p.type === "year" ? "YYYY" : p.type === "month" ? "MM" : "DD");
    result = order.join(sep);
  } catch {
    result = "MM/DD/YYYY";
  }
  localeDateFormatCache.set(locale, result);
  return result;
};

const localeReadableDateFormatCache = new Map<string, string>();


export const getLocaleReadableDateFormat = (): string => {
  const locale = getDisplayLocale();
  const cached = localeReadableDateFormatCache.get(locale);
  if (cached !== undefined) return cached;
  let result: string;
  try {
    const parts = new Intl.DateTimeFormat(locale, {
      year: "numeric", month: "short", day: "2-digit",
    }).formatToParts(new Date("2023-04-15T12:00:00Z"));
    result = parts
      .map(p =>
        p.type === "year" ? "YYYY" :
        p.type === "month" ? "MMM" :
        p.type === "day" ? "DD" :
        p.value)
      .join("");
  } catch {
    result = "MMM DD, YYYY";
  }
  localeReadableDateFormatCache.set(locale, result);
  return result;
};

// Returns true when the user's navigator.language uses a 12-hour AM/PM clock.
// Use alongside getLocaleDateTimeFormat() as the `ampm` prop on MUI DateTimePicker —
// MUI v6 requires ampm to be set explicitly when a custom format is provided, otherwise
// it falls back to its own default (MMMM DD, YYYY hh:mm aa) regardless of the format prop.
export const getLocaleAmPm = (): boolean => {
  const locale = getDisplayLocale();
  try {
    return new Intl.DateTimeFormat(locale, { hour: "numeric" })
      .resolvedOptions().hour12 ?? true;
  } catch {
    return true;
  }
};

// Returns a dayjs format string for date + time matching the user's locale.
// Combines getLocaleDateFormat() with a locale-appropriate time token:
// 12-hour AM/PM (e.g. "hh:mm A") for en-US; 24-hour (e.g. "HH:mm") for en-GB/de-DE.
// Always pair with ampm={getLocaleAmPm()} on MUI DateTimePicker.
export const getLocaleDateTimeFormat = (): string => {
  try {
    return `${getLocaleDateFormat()} ${getLocaleAmPm() ? "hh:mm A" : "HH:mm"}`;
  } catch {
    return "MM/DD/YYYY hh:mm A";
  }
};

import { FEATURE_FLAGS } from "../../../../constants/FeatureFlag";
import { fromUtc } from "../../../../utils/utcDateUtil";

/**
 * Which QuickBooks workspace modules are released.
 *
 * A module absent from this map is always on — only under-development modules need an
 * entry, so this stays a two-line list rather than a registry to maintain alongside
 * `SYNC_MODULES`. Flags live in `constants/FeatureFlag.ts` with the rest of the app's.
 *
 * Every place that enumerates modules filters through here — the module selector, the
 * workspace rail, the landing preview and the sync guide's pipeline and dependency
 * lists — so a disabled module cannot be selected, and nothing offers to navigate to it.
 */
/**
 * A gated module opens only on an explicit `true`.
 *
 * The map below is the list of modules that are NOT released, so anything in it must fail
 * CLOSED: were a flag ever missing — dropped by a bad merge, renamed, or a file left out of
 * a commit — coercing here means the module stays hidden instead of an unfinished accounting
 * surface appearing in a tenant's workspace. TypeScript already rejects an undefined flag
 * (`FEATURE_FLAGS` is an inferred literal type, so a missing key is a compile error rather
 * than `undefined`), but that only holds while every consumer is type-checked TS; this makes
 * the safe direction true at runtime as well and costs nothing.
 *
 * Note this is the opposite default from `isQuickBooksModuleEnabled` below, deliberately:
 * a module ABSENT from the map is released and open; a module IN the map is closed unless its
 * flag explicitly says otherwise.
 */
const gateOpen = (flag: unknown): boolean => flag === true;

const invoicesEnabled = gateOpen(FEATURE_FLAGS.ENABLE_QUICKBOOKS_INVOICES);

const QB_MODULE_FLAGS: Record<string, boolean> = {
  invoices: invoicesEnabled,
  /*
   * Customer Payments requires Invoices, so it is gated on BOTH flags.
   *
   * This is not defensive tidiness — Customer Payments cannot function without it. Every
   * allocation resolves `reference_no + project_id` to a FIELDCRAFT invoice and then to a SYNCED
   * `invoice_external_map` row to obtain the QBO Invoice id; with Invoices unavailable an
   * allocation has nothing to link to. `MODULE_DEPENDENCIES` in QuickBooksSyncGuide records
   * exactly this: `payments: ["invoices"]`, its only prerequisite.
   *
   * Enabling Customer Payments alone is therefore not a valid configuration. Coupling here
   * rather than in the guide means the invalid combination cannot reach the UI at all: the
   * alternative was letting the module render with its one prerequisite filtered out of its
   * dependency list, which would tell the user "no setup needed" for a module whose
   * prerequisite is simply missing.
   */
  payments: gateOpen(FEATURE_FLAGS.ENABLE_QUICKBOOKS_CUSTOMER_PAYMENTS) && invoicesEnabled,
};

export const isQuickBooksModuleEnabled = (moduleId: string): boolean =>
  QB_MODULE_FLAGS[moduleId] ?? true;

/** Filter a list of module ids down to the released ones, preserving order. */
export const enabledQuickBooksModuleIds = (moduleIds: readonly string[]): string[] =>
  moduleIds.filter(isQuickBooksModuleEnabled);

export const formatCurrency = (amount: number): string =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);

/**
 * QuickBooks is an accounting surface reviewed against QuickBooks Online itself, so
 * every date in this workspace renders in the US numeric convention — matching the
 * `en-US` currency format above and the `MM/DD/YYYY` pickers used elsewhere in the app.
 *
 * These wrap `fromUtc` rather than parsing with `new Date(...)`: stored timestamps are
 * UTC and the viewer's browser timezone is the only converting layer (the UTC-first
 * rule in the repo's CLAUDE.md). `tz` is threaded through so tests can assert a fixed
 * zone, mirroring the `utcDateUtil` contract.
 *
 * Deliberately NOT the shared `utcDateUtil.formatDateOnly` / `formatDateTime`: those
 * render `MMM DD, YYYY` app-wide and their format is locked by `utcDateUtil.test.ts`.
 * Both return "" for a missing/unparseable value; callers add their own placeholder.
 */
const QB_DATE_FORMAT = "MM/DD/YYYY";
const QB_DATE_TIME_FORMAT = "MM/DD/YYYY hh:mm A";

export const qbFormatDate = (iso?: string | null, tz?: string): string => {
  const d = tz ? fromUtc(iso ?? null, tz) : fromUtc(iso ?? null);
  return d ? d.format(QB_DATE_FORMAT) : "";
};

export const qbFormatDateTime = (iso?: string | null, tz?: string): string => {
  const d = tz ? fromUtc(iso ?? null, tz) : fromUtc(iso ?? null);
  return d ? d.format(QB_DATE_TIME_FORMAT) : "";
};

export const QB_SYNC_STATUS = {
  SYNCED: "SYNCED",
  PENDING_PUSH: "PENDING_PUSH",
  FAILED: "FAILED",
  IMPORT_FAILED: "IMPORT_FAILED",
  CONFLICT: "CONFLICT",
  ASSIGNMENT_NEEDED: "ASSIGNMENT_NEEDED",
  SYNCED_INCOMPLETE: "SYNCED_INCOMPLETE",
  AWAITING_APPROVAL: "AWAITING_APPROVAL",
} as const;

export const QB_SYNC_LABEL = {
  PENDING_SYNC: "Pending sync",
  SYNC_FAILED: "Sync failed",
  NOT_SYNCED: "Not synced",
} as const;

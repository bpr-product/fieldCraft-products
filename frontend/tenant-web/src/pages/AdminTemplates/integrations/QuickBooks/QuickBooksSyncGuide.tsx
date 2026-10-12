/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * QuickBooks "How it works" guidance, rebuilt in the connected workspace's design
 * language (the `--qbw-*` tokens: system font, hairlines, one accent, four status
 * tones). It replaces the previous strip, which carried its own Tailwind-flavoured
 * palette and sat permanently above every module table.
 *
 * ── The mental model this surface has to teach ──────────────────────────────────
 * The old version read as a running order: sync Vendors, then Projects, then Bills,
 * every time. That is not how the integration behaves and it made routine syncing
 * look far heavier than it is.
 *
 * What actually happens is a one-time setup followed by ongoing work:
 *   · Reference records — customers, vendors, accounts, employees, projects,
 *     services — are synced ONCE. A tenant only comes back to them when a new one
 *     is added.
 *   · Transactions — bills, invoices, time, payments — keep being created against
 *     those records. Day to day the user syncs only the transaction module; it
 *     attaches itself to whatever is already in QuickBooks.
 *
 * So the copy is built on ONE phrase — SET UP ONCE — used on every surface here, and
 * never on an order to repeat. Nothing blocks a sync; this is guidance.
 * ("Attaches to", an earlier attempt at naming this, was dropped: it described the
 * mechanism rather than the thing the user has to do.)
 *
 * ── Components ─────────────────────────────────────────────────────────────────
 *   SyncPipelineGuide     "How does this work?" — reference material, so it is an
 *                         on-demand panel opened from the workspace rail rather
 *                         than a permanent band above the table. Two phases (set up
 *                         once → sync as work comes in); selecting a module lights
 *                         up what to set up for it and explains it at the foot.
 *
 *   ModuleDependencyHint  "Is this module ready to sync?" — a single status pill that
 *                         sits on the module header's toolbar row, so it costs NO row
 *                         of its own. It carries the headline only — "Set up once 3",
 *                         or "Check setup 2" in amber; the detail (which records,
 *                         their state, what to do) opens in a popover on click.
 *
 *                         It borrows the filter pills' box metrics and their 7px status
 *                         dot so the row reads as one aligned set of controls, but not
 *                         their solid fill or their `aria-pressed` toggle — it is not a
 *                         filter and must not be mistaken for one. For the same reason
 *                         it never says "needs attention": that is the wording of a
 *                         filter pill a few pixels away, counting a different thing.
 *
 *                         It was a full-width band, then a caption line, and both were
 *                         a whole row on five of the nine module screens for
 *                         information that is reference material once a tenant knows
 *                         it. A pill on a row that already exists gives the table that
 *                         row back without hiding anything.
 *
 * The phases deliberately carry no readiness state and no aggregate counts — that is
 * this surface's original navigation-first decision and it is preserved. Record-level
 * state belongs to the pill, which is scoped to a single module and already had it.
 *
 * Styling depends on the `--qbw-*` tokens declared by QuickBooksWorkspace.scss, so
 * that sheet is imported here too; webpack de-duplicates it against the import in
 * QuickBooksTable.tsx, which already loads it first.
 */
import React, { useEffect, useMemo, useState } from "react";
import { Dialog, Popover, Tooltip } from "@mui/material";
import AccessTimeRoundedIcon from "@mui/icons-material/AccessTimeRounded";
import AccountBalanceWalletRoundedIcon from "@mui/icons-material/AccountBalanceWalletRounded";
import AccountTreeRoundedIcon from "@mui/icons-material/AccountTreeRounded";
import BadgeRoundedIcon from "@mui/icons-material/BadgeRounded";
import BusinessRoundedIcon from "@mui/icons-material/BusinessRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import ConstructionRoundedIcon from "@mui/icons-material/ConstructionRounded";
import FolderSpecialRoundedIcon from "@mui/icons-material/FolderSpecialRounded";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import PaymentsRoundedIcon from "@mui/icons-material/PaymentsRounded";
import ReceiptLongRoundedIcon from "@mui/icons-material/ReceiptLongRounded";
import { enabledQuickBooksModuleIds, isQuickBooksModuleEnabled } from "./QuickBooksUtils";
import "./QuickBooksWorkspace.scss";
import "./QuickBooksSyncGuide.scss";

// ── Types ─────────────────────────────────────────────────────────
export type ModuleSummaryBag = {
  customers?: {
    totalCustomers?: number;
    syncedCustomers?: number;
    failedCustomers?: number;
    importFailedCustomers?: number;
    syncFailedCustomers?: number;
  } | null;
  vendors?: {
    totalVendors?: number;
    syncedVendors?: number;
    failedVendors?: number;
    importFailedVendors?: number;
  } | null;
  "chart-of-accounts"?: {
    totalAccounts?: number;
    syncedAccounts?: number;
  } | null;
  employees?: {
    totalEmployees?: number;
    syncedEmployees?: number;
    failedEmployees?: number;
    importFailedEmployees?: number;
    syncFailedEmployees?: number;
  } | null;
  projects?: { total?: number; synced?: number; failed?: number } | null;
  services?: { total?: number; synced?: number; failed?: number } | null;
  bills?: {
    totalBills?: number;
    syncedBills?: number;
    failedBills?: number;
    importFailedBills?: number;
    syncFailedBills?: number;
  } | null;
  "bill-payments"?: {
    totalBillPayments?: number;
    syncedBillPayments?: number;
    failedBillPayments?: number;
    importFailedBillPayments?: number;
  } | null;
  timelogs?: {
    total?: number;
    synced?: number;
    failed?: number;
    notReady?: number;
  } | null;
  invoices?: {
    total?: number;
    synced?: number;
    failed?: number;
    notReady?: number;
  } | null;
  payments?: {
    total?: number;
    synced?: number;
    failed?: number;
    notReady?: number;
  } | null;
};

/**
 * How a module reads while another module's attachments are on screen.
 * `normal` is the resting state, when nothing is selected.
 */
type ChainMode = "normal" | "focal" | "chain" | "dimmed";

/** Tone of the module status pill, mapped onto the workspace's status scale. */
type PrereqTone = "neutral" | "ok" | "warn";

// ── Static config ─────────────────────────────────────────────────
/** Glyphs match the workspace rail so a module looks the same everywhere. */
const MODULE_ICONS: Record<string, React.ReactNode> = {
  customers: <PaymentsRoundedIcon />,
  vendors: <BusinessRoundedIcon />,
  "chart-of-accounts": <AccountTreeRoundedIcon />,
  employees: <BadgeRoundedIcon />,
  projects: <FolderSpecialRoundedIcon />,
  services: <ConstructionRoundedIcon />,
  bills: <ReceiptLongRoundedIcon />,
  invoices: <ReceiptLongRoundedIcon />,
  payments: <AccountBalanceWalletRoundedIcon />,
  timelogs: <AccessTimeRoundedIcon />,
  "bill-payments": <AccountBalanceWalletRoundedIcon />,
};

export const MODULE_LABELS: Record<string, string> = {
  customers: "Customers",
  vendors: "Vendors",
  "chart-of-accounts": "Chart of Accounts",
  employees: "Employees",
  projects: "Projects",
  services: "Services",
  bills: "Bills",
  invoices: "Invoices",
  payments: "Customer Payments",
  timelogs: "Time Activities",
  "bill-payments": "Bill Payments",
};

/** Falls back to the raw id so an unmapped module can still be rendered and read. */
const moduleLabel = (moduleId: string): string => MODULE_LABELS[moduleId] ?? moduleId;

/**
 * What each module attaches to in QuickBooks — direct relationships only, for every
 * module the integration supports, including ones currently behind a release flag.
 * This is the domain graph, so it stays complete; use `MODULE_DEPENDENCIES` below for
 * what the UI should actually render.
 */
export const ALL_MODULE_DEPENDENCIES: Record<string, string[]> = {
  customers: [],
  vendors: [],
  "chart-of-accounts": [],
  employees: [],
  projects: ["customers"],
  services: ["chart-of-accounts"],
  bills: ["vendors", "chart-of-accounts", "projects"],
  invoices: ["customers", "projects"],
  payments: ["invoices"],
  "bill-payments": ["bills"],
  timelogs: ["employees", "projects", "services"],
};

/**
 * Release-gated view of the graph: a gated module loses its own entry.
 *
 * Dependency lists are deliberately NOT filtered. A module is only enabled when all of its
 * prerequisites are (see the Customer-Payments-requires-Invoices coupling in
 * `QuickBooksUtils`), so a rendered module's prerequisites are always rendered too and there
 * is nothing to strip. Stripping them instead would be actively harmful: it would leave a
 * module claiming it needs no setup while its one real prerequisite was quietly unavailable.
 *
 * The invariant is enforced by a test — "every prerequisite of an enabled module is itself
 * enabled", in QuickBooksUtils.test.ts — so a future flag that breaks it fails the build
 * instead of shipping a misleading hint.
 */
export const MODULE_DEPENDENCIES: Record<string, string[]> = Object.fromEntries(
  Object.entries(ALL_MODULE_DEPENDENCIES).filter(([moduleId]) =>
    isQuickBooksModuleEnabled(moduleId),
  ),
);

/**
 * The two phases of working with the integration. Phase 1 is done once per record;
 * phase 2 is the routine work. The groups inside a phase carry the finer nuance
 * (Projects attach to Customers, so they come after) without implying that either
 * has to be re-run every time a transaction syncs.
 */
const ALL_SYNC_PHASES = [
  {
    id: "setup",
    label: "Set up once",
    desc: "Your customers, vendors, accounts, people and jobs. Sync each one once — then come back only when you add a new one.",
    groups: [
      {
        id: "foundation",
        label: "Start here",
        desc: "These can be synced right away",
        modules: ["customers", "vendors", "chart-of-accounts", "employees"],
      },
      {
        id: "references",
        label: "Then",
        desc: "These attach to the records above",
        modules: ["projects", "services"],
      },
    ],
  },
  {
    id: "ongoing",
    label: "Sync as work comes in",
    desc: "New records keep arriving here. You sync them each time you want QuickBooks up to date — they link to what you set up above.",
    groups: [
      {
        id: "transactional",
        label: "Day to day",
        desc: "Created as you work",
        modules: ["bills", "invoices", "timelogs"],
      },
      {
        id: "settlements",
        label: "Settle up",
        desc: "Payments against the records above",
        modules: ["bill-payments", "payments"],
      },
    ],
  },
] as const;

/**
 * The phases actually drawn. Gated modules are removed from their group; a group left
 * with none is dropped, and so is a phase left with no groups — so the panel never
 * renders an empty heading or a connector running to one. The displayed number comes
 * from the rendered position, so dropping a phase cannot leave a gap in the sequence.
 */
const SYNC_PHASES = ALL_SYNC_PHASES
  .map((phase) => ({
    ...phase,
    groups: phase.groups
      .map((group) => ({ ...group, modules: enabledQuickBooksModuleIds(group.modules) }))
      .filter((group) => group.modules.length > 0),
  }))
  .filter((phase) => phase.groups.length > 0);

// ── Helpers ────────────────────────────────────────────────────────
function getSyncedCount(moduleId: string, bag: ModuleSummaryBag): number {
  const s = (bag as Record<string, any>)[moduleId];
  if (!s) return 0;
  switch (moduleId) {
    case "customers": return s.syncedCustomers ?? 0;
    case "vendors": return s.syncedVendors ?? 0;
    case "chart-of-accounts": return s.syncedAccounts ?? 0;
    case "employees": return s.syncedEmployees ?? 0;
    case "projects": return s.synced ?? 0;
    case "services": return s.synced ?? 0;
    case "bills": return s.syncedBills ?? 0;
    case "invoices": return s.synced ?? 0;
    case "payments": return s.synced ?? 0;
    case "bill-payments": return s.syncedBillPayments ?? 0;
    case "timelogs": return s.synced ?? 0;
    default: return 0;
  }
}

/**
 * Count of failed / needs-attention records for a module. Decides whether an attached
 * record reads as clean or as needing attention on the inline strip.
 */
function getNeedsAttentionCount(moduleId: string, bag: ModuleSummaryBag): number {
  const s = (bag as Record<string, any>)[moduleId];
  if (!s) return 0;
  switch (moduleId) {
    case "customers":
      return (s.failedCustomers ?? 0) + (s.importFailedCustomers ?? 0) + (s.syncFailedCustomers ?? 0);
    case "vendors":
      return (s.failedVendors ?? 0) + (s.importFailedVendors ?? 0);
    case "chart-of-accounts":
      return 0;
    case "employees":
      return (s.failedEmployees ?? 0) + (s.importFailedEmployees ?? 0) + (s.syncFailedEmployees ?? 0);
    case "projects":
      return s.failed ?? 0;
    case "services":
      return s.failed ?? 0;
    case "bills":
      return (s.failedBills ?? 0) + (s.importFailedBills ?? 0) + (s.syncFailedBills ?? 0);
    case "invoices":
      return s.failed ?? 0;
    case "payments":
      return s.failed ?? 0;
    case "bill-payments":
      return (s.failedBillPayments ?? 0) + (s.importFailedBillPayments ?? 0);
    case "timelogs":
      return (s.failed ?? 0) + (s.notReady ?? 0);
    default:
      return 0;
  }
}

// Kept for external consumers.
export function computeModuleHealth(moduleId: string, bag: ModuleSummaryBag): string {
  return getSyncedCount(moduleId, bag) > 0 ? "has-data" : "no-data";
}

function getChainMode(moduleId: string, focusedModule: string | null): ChainMode {
  if (!focusedModule) return "normal";
  if (moduleId === focusedModule) return "focal";
  if ((MODULE_DEPENDENCIES[focusedModule] ?? []).includes(moduleId)) return "chain";
  return "dimmed";
}

// ── ModuleChip ────────────────────────────────────────────────────
/**
 * One module inside a group. A native `<button>`, so Enter/Space activation, the focus
 * ring and button semantics come from the platform instead of hand-rolled key handlers.
 * `aria-pressed` reports whether this module's attachments are the ones on show;
 * `aria-current` marks the module the workspace is displaying behind the panel.
 */
const ModuleChip: React.FC<{
  moduleId: string;
  isCurrent: boolean;
  mode: ChainMode;
  focusedLabel: string | null;
  onSelect: (moduleId: string) => void;
}> = ({ moduleId, isCurrent, mode, focusedLabel, onSelect }) => {
  const label = moduleLabel(moduleId);
  const className = [
    "qbsg-chip",
    isCurrent ? "qbsg-chip--current" : "",
    mode === "focal" ? "qbsg-chip--focal" : "",
    mode === "chain" ? "qbsg-chip--chain" : "",
    mode === "dimmed" ? "qbsg-chip--dimmed" : "",
  ].filter(Boolean).join(" ");

  let hint: string;
  if (mode === "focal") hint = `${label} — showing what to set up for it. Select again to clear.`;
  else if (mode === "chain" && focusedLabel) hint = `${label} — set this up for ${focusedLabel}`;
  else hint = `${label} — see what to set up for it`;

  return (
    <Tooltip title={hint} arrow placement="top">
      <button
        type="button"
        className={className}
        onClick={() => onSelect(moduleId)}
        aria-label={label}
        aria-pressed={mode === "focal"}
        aria-current={isCurrent ? "true" : undefined}
      >
        <span className="qbsg-chip-ico" aria-hidden="true">{MODULE_ICONS[moduleId]}</span>
        <span className="qbsg-chip-name">{label}</span>
        {isCurrent ? <span className="qbsg-chip-here" aria-hidden="true" /> : null}
      </button>
    </Tooltip>
  );
};

// ── SyncPipelineGuide ──────────────────────────────────────────────
export type SyncPipelineGuideProps = {
  /** Whether the panel is on screen. The parent owns this so the rail can open it. */
  open: boolean;
  /** Called when the panel should close — backdrop, Escape, close button, navigation. */
  onClose: () => void;
  /** Module the workspace is currently showing; marked as "you are here". */
  selectedModule: string;
  /** Navigate the workspace to a module. The panel closes after calling this. */
  onSelectModule: (id: string) => void;
};

export const SyncPipelineGuide: React.FC<SyncPipelineGuideProps> = ({
  open,
  onClose,
  selectedModule,
  onSelectModule,
}) => {
  const [focusedModule, setFocusedModule] = useState<string | null>(null);

  // Every opening starts from a clean slate, whichever way the panel was closed.
  useEffect(() => {
    if (!open) setFocusedModule(null);
  }, [open]);

  const focusedLabel = focusedModule ? moduleLabel(focusedModule) : null;
  const focusedDeps = useMemo(
    () => (focusedModule ? MODULE_DEPENDENCIES[focusedModule] ?? [] : []),
    [focusedModule],
  );

  const handleChipSelect = (moduleId: string) => {
    setFocusedModule((current) => (current === moduleId ? null : moduleId));
  };

  const handleNavigate = (moduleId: string) => {
    onSelectModule(moduleId);
    onClose();
  };

  // Escape unwinds one step at a time: it clears the highlighted module first, and only
  // closes the panel once there is nothing highlighted left to clear.
  const handleDialogClose = (_event: object, reason: string) => {
    if (reason === "escapeKeyDown" && focusedModule) {
      setFocusedModule(null);
      return;
    }
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleDialogClose}
      maxWidth="md"
      fullWidth
      aria-labelledby="qbsg-title"
      PaperProps={{ className: "qbsg-paper" }}
    >
      <div className="qbsg-root" role="region" aria-label="How it works">
        <header className="qbsg-head">
          <span className="qbsg-head-mark" aria-hidden="true"><span>qb</span></span>
          <span className="qbsg-head-txt">
            <h2 className="qbsg-head-title" id="qbsg-title">How it works</h2>
            <p className="qbsg-head-sub">
              Sync your reference records once. After that the only thing you sync day to day
              is the transactions — they link to what is already in QuickBooks.
            </p>
          </span>
          <button type="button" className="qbsg-close" onClick={onClose} aria-label="Close">
            <CloseRoundedIcon />
          </button>
        </header>

        <ol className="qbsg-phases">
          {SYNC_PHASES.map((phase, index) => (
            <li className="qbsg-phase" key={phase.id}>
              <span className="qbsg-phase-spine" aria-hidden="true">
                <span className="qbsg-phase-num">{index + 1}</span>
                {index < SYNC_PHASES.length - 1 ? <span className="qbsg-phase-line" /> : null}
              </span>
              <div className="qbsg-phase-main">
                <div className="qbsg-phase-head">
                  <h3 className="qbsg-phase-label">{phase.label}</h3>
                  <p className="qbsg-phase-desc">{phase.desc}</p>
                </div>
                {phase.groups.map((group) => (
                  <div className="qbsg-group" key={group.id}>
                    <div className="qbsg-group-head">
                      <span className="qbsg-group-label">{group.label}</span>
                      <span className="qbsg-group-desc">{group.desc}</span>
                    </div>
                    <div className="qbsg-group-mods">
                      {group.modules.map((moduleId) => (
                        <ModuleChip
                          key={moduleId}
                          moduleId={moduleId}
                          isCurrent={moduleId === selectedModule}
                          mode={getChainMode(moduleId, focusedModule)}
                          focusedLabel={focusedLabel}
                          onSelect={handleChipSelect}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </li>
          ))}
        </ol>

        <footer className="qbsg-foot">
          {focusedModule ? (
            <>
              <div className="qbsg-foot-row">
                <div
                  className="qbsg-attach"
                  role="group"
                  aria-label={`What to set up first for ${focusedLabel}`}
                >
                  <span className="qbsg-attach-label">
                    {focusedDeps.length > 0 ? "Set up first" : "No setup needed"}
                  </span>
                  {focusedDeps.length > 0 ? (
                    <div className="qbsg-attach-chips">
                      {focusedDeps.map((depId) => (
                        <button
                          key={depId}
                          type="button"
                          className="qbsg-attach-chip"
                          onClick={() => handleNavigate(depId)}
                          aria-label={`Go to ${moduleLabel(depId)}`}
                        >
                          <span className="qbsg-attach-chip-ico" aria-hidden="true">{MODULE_ICONS[depId]}</span>
                          {moduleLabel(depId)}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
                <div className="qbsg-foot-actions">
                  <button
                    type="button"
                    className="qbsg-foot-go"
                    onClick={() => handleNavigate(focusedModule)}
                  >
                    Go to {focusedLabel}
                  </button>
                  <button type="button" className="qbsg-foot-clear" onClick={() => setFocusedModule(null)}>
                    Clear
                  </button>
                </div>
              </div>
              <p className="qbsg-foot-note">
                {focusedDeps.length > 0 ? (
                  <>
                    Set each of those up once. After that <strong>{focusedLabel}</strong> is the
                    only one you sync — you run it each time you want new ones in QuickBooks.
                  </>
                ) : (
                  <>
                    <strong>{focusedLabel}</strong> does not need anything set up first — you run
                    a sync each time you want new ones in QuickBooks.
                  </>
                )}
              </p>
            </>
          ) : (
            <p className="qbsg-foot-hint">
              <InfoOutlinedIcon aria-hidden="true" />
              Select any module to see what to set up for it first.
            </p>
          )}
        </footer>
      </div>
    </Dialog>
  );
};

// ── ModuleDependencyHint ──────────────────────────────────────────
export type ModuleDependencyHintProps = {
  moduleId: string;
  summaries: ModuleSummaryBag;
  onNavigate: (depId: string) => void;
  loadedModules?: Set<string>;
  /** Renders the "How it works" link when supplied; omit it to hide the link. */
  onOpenGuide?: () => void;
};

/**
 * How long a confirmed problem waits before it is announced. Summaries for the attached
 * modules stream in one at a time, so a pill that reacted instantly would flash amber
 * during a normal load and then settle. Delaying the escalation keeps the pill quiet
 * unless the problem is real and still there.
 */
const WARNING_DELAY_MS = 400;

export const ModuleDependencyHint: React.FC<ModuleDependencyHintProps> = ({
  moduleId,
  summaries,
  onNavigate,
  loadedModules,
  onOpenGuide,
}) => {
  const deps = MODULE_DEPENDENCIES[moduleId] ?? [];
  const label = moduleLabel(moduleId);

  const isLoaded = (depId: string) => !!loadedModules?.has(depId);

  // Only gate on modules whose data has been explicitly fetched.
  const hasConfirmedIssues = deps.some((d) => isLoaded(d) && getNeedsAttentionCount(d, summaries) > 0);
  const allLoadedOk = deps.length > 0 && deps.every((d) => isLoaded(d)) && !hasConfirmedIssues;

  const [showWarning, setShowWarning] = useState(false);
  useEffect(() => {
    if (!hasConfirmedIssues) { setShowWarning(false); return; }
    const timer = setTimeout(() => setShowWarning(true), WARNING_DELAY_MS);
    return () => clearTimeout(timer);
  }, [hasConfirmedIssues]);

  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const popoverOpen = Boolean(anchorEl);

  // Switching modules re-keys this component in the workspace, but close the panel on
  // any module change anyway so it can never outlive the pill that opened it.
  useEffect(() => { setAnchorEl(null); }, [moduleId]);

  if (deps.length === 0) return null;

  let tone: PrereqTone = "neutral";
  if (showWarning) tone = "warn";
  else if (allLoadedOk) tone = "ok";

  const flaggedCount = showWarning
    ? deps.filter((d) => isLoaded(d) && getNeedsAttentionCount(d, summaries) > 0).length
    : 0;

  // "Set up once" is the framing the whole surface uses: these records go into
  // QuickBooks one time, and everything this module syncs afterwards points at them.
  // The count rides in a badge, matching the filter pills this sits beside.
  //
  // The warn label stays inside that same "setup" vocabulary and deliberately avoids
  // "needs attention": every module's own filter row carries a "Needs attention" pill
  // a few pixels away, counting THIS module's failing records. Two controls on one row
  // with the same word, counting different things, is a trap — "Check setup" says
  // plainly that the problem is upstream, in the records this module was set up on.
  const pillText = showWarning ? "Check setup" : "Set up once";
  const pillCount = showWarning ? flaggedCount : deps.length;
  const pillHint = showWarning
    ? `Check setup for ${label} — ${flaggedCount} record${flaggedCount === 1 ? "" : "s"} with issues`
    : `Set up once for ${label} — ${deps.length} record${deps.length === 1 ? "" : "s"}`;

  const closePopover = () => setAnchorEl(null);
  const navigateTo = (depId: string) => {
    closePopover();
    onNavigate(depId);
  };

  return (
    <>
      <button
        type="button"
        className={`qbsg-pin qbsg-pin--${tone}`}
        onClick={(event) => setAnchorEl(event.currentTarget)}
        aria-haspopup="dialog"
        aria-expanded={popoverOpen}
        aria-label={pillHint}
      >
        {/* A status dot, not a glyph — the same 7px mark the filter pills next to it
            use, so the two controls sit at one visual weight. Tone is the only thing
            it carries, which is all it was ever doing. */}
        <span className={`qbsg-pin-dot qbsg-pin-dot--${tone}`} aria-hidden="true" />
        {pillText}
        <span className="qbsg-pin-n">{pillCount}</span>
      </button>

      {/* The panel is what costs space, so it only exists while it is open. A problem
          still has to reach a screen reader with the panel shut, which is what this
          off-screen live region is for — it replaces the alert row the strip used to
          occupy, at zero visual cost. */}
      {showWarning ? (
        <span className="qbsg-sr-only" role="alert">
          Fix the flagged setup records first — {label} that point at them can fail to sync.
        </span>
      ) : null}

      <Popover
        open={popoverOpen}
        anchorEl={anchorEl}
        onClose={closePopover}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        PaperProps={{ className: "qbsg-pop-paper" }}
      >
        <div className="qbsg-pop" role="dialog" aria-label={`Set up once for ${label}`}>
          <p className="qbsg-pop-title">Set up once for {label}</p>

          <ul className="qbsg-pop-list">
            {deps.map((depId) => {
              const loaded = isLoaded(depId);
              const hasIssues = loaded && getNeedsAttentionCount(depId, summaries) > 0;
              const isOk = loaded && !hasIssues;
              const flagged = hasIssues && showWarning;
              const depLabel = moduleLabel(depId);
              return (
                <li key={depId}>
                  <button
                    type="button"
                    className="qbsg-pop-item"
                    onClick={() => navigateTo(depId)}
                    aria-label={`Go to ${depLabel}`}
                  >
                    <span className="qbsg-pop-item-ico" aria-hidden="true">{MODULE_ICONS[depId]}</span>
                    <span className="qbsg-pop-item-name">{depLabel}</span>
                    {/* "Has issues" / "No issues" — a matched pair, and neither collides
                        with the module's own "Needs attention" filter on the row behind. */}
                    {flagged ? (
                      <span className="qbsg-pop-item-state qbsg-pop-item-state--warn">Has issues</span>
                    ) : isOk ? (
                      <span className="qbsg-pop-item-state qbsg-pop-item-state--ok">No issues</span>
                    ) : null}
                    <ChevronRightRoundedIcon className="qbsg-pop-item-go" aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>

          {/* The closing advice and the way into the full guide belong together, so they
              share one banded footer rather than stacking as two loose blocks under the
              list. The link was a full-width block of bare accent text here, which read
              as a fourth list row that happened to be blue — and a full-bleed row is a
              lot of panel for reference material. As a bordered chip on a tinted band it
              is unmistakably a control, sits where a panel's actions are expected — the
              band's trailing edge, under the note — and takes only the width of its own
              label.

              Label only, no glyphs: it is the same "How it works ?" the workspace rail
              uses, so the two entry points into the guide read as one thing. A leading
              help icon sat heavier than the 11.5px text it was labelling, and a trailing
              chevron next to the question mark is a second mark doing the first one's
              job — the border already says "press me". */}
          <div className="qbsg-pop-foot">
            <p className="qbsg-pop-note">
              Before syncing new <strong>{label}</strong>, make sure the related modules are up to date.
              If anything has failed or needs attention, resolve and sync it first.
            </p>

            {onOpenGuide ? (
              <button
                type="button"
                className="qbsg-pop-more"
                onClick={() => { closePopover(); onOpenGuide(); }}
              >
                How it works ?
              </button>
            ) : null}
          </div>
        </div>
      </Popover>
    </>
  );
};

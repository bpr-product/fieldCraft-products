/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * @author Bala Pradeep
 *
 * The connected QuickBooks workspace's settings panel — one home for every
 * tenant-level preference about how the integration behaves.
 *
 * ── Why it takes its content as data ────────────────────────────────────────
 * Sections and rows are props, not markup written here, so adding a preference
 * is an entry in an array rather than a change to this layout. `kind` is a
 * union: `toggle` for an on/off preference, `custom` when the caller draws its
 * own control.
 *
 * ── Why a section is one bordered list, not a stack of cards ────────────────
 * Preferences read as groups. A single rounded container with hairline
 * dividers ties a group together and keeps each control visually bound to the
 * label on its own row; separate cards per row make a short list look like
 * unrelated dialogs stacked on top of each other.
 *
 * ── Why an unloaded toggle draws a placeholder ──────────────────────────────
 * A switch has no third position, so an unloaded preference cannot be drawn as
 * a switch without asserting a value the tenant may not hold. While `loading`
 * is set the row shows an inert placeholder in the control's place.
 *
 * Copy arrives already translated. This component owns only its own chrome, so
 * a caller can label a row from wherever that row's wording belongs without
 * this file growing a key for every preference the workspace will ever have.
 *
 * Presented in the same shell as "How it works" (the other on-demand panel this
 * workspace opens from the rail footer) so the two read as one pair rather than
 * two unrelated overlays. Styling depends on the `--qbw-*` tokens declared by
 * QuickBooksWorkspace.scss, so that sheet is imported here too; webpack
 * de-duplicates it against the import in QuickBooksTable.tsx.
 *
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR      |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Initial — generic settings panel
 *                |            |                 | housing the Auto sync toggle
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Auto sync as a two-option
 *                |            |                 | choice; save confirmation
 * AB#31584       | 23-09-2026 | Bala Pradeep    | Preferences layout — grouped
 *                |            |                 | rows, switch, load placeholder
 * -----------------------------------------------------------------------------
 */
import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog, Switch } from "@mui/material";
import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import "./QuickBooksWorkspace.scss";
import "./QuickBooksSettings.scss";

/** How long the save confirmation stays on screen. */
const SAVED_VISIBLE_MS = 2500;

/** A preference the user turns on or off. */
export type QuickBooksToggleSetting = {
  kind: "toggle";
  id: string;
  /** Already translated. */
  label: string;
  /** Already translated — what the preference does, in the user's terms. */
  description: string;
  checked: boolean;
  /** A save is in flight; the control is held until it lands. */
  busy?: boolean;
  /** The saved value has not arrived; a placeholder stands in for the switch. */
  loading?: boolean;
  /** Shown under the description while the preference is off. */
  offHint?: string;
  onChange: (next: boolean) => void;
};

/**
 * A preference this panel does not know how to draw — the caller supplies the
 * control. The row's frame, label and description still come from here, so a
 * custom preference sits flush with the rows beside it.
 */
export type QuickBooksCustomSetting = {
  kind: "custom";
  id: string;
  label: string;
  description: string;
  render: () => React.ReactNode;
};

export type QuickBooksSetting = QuickBooksToggleSetting | QuickBooksCustomSetting;

export type QuickBooksSettingsSection = {
  id: string;
  /** Already translated; rendered as the group heading. */
  label: string;
  settings: QuickBooksSetting[];
};

/** What the panel's footer reports about the last change. */
export type QuickBooksSettingsStatus = "idle" | "saving" | "saved" | "error";

const STATUS_KEY: Record<Exclude<QuickBooksSettingsStatus, "idle">, string> = {
  saving: "quickbooks.settings.saving",
  saved: "quickbooks.settings.saved",
  error: "quickbooks.settings.saveFailed",
};

export interface QuickBooksSettingsDialogProps {
  open: boolean;
  onClose: () => void;
  sections: QuickBooksSettingsSection[];
  status?: QuickBooksSettingsStatus;
}

/** The control a toggle row carries: a switch, or a placeholder until it loads. */
const ToggleControl: React.FC<{ setting: QuickBooksToggleSetting }> = ({ setting }) => {
  const { t } = useTranslation();

  if (setting.loading) {
    return (
      <span
        className="qbst-row-loading"
        role="status"
        aria-label={t("quickbooks.settings.loading")}
      />
    );
  }

  return (
    <Switch
      disableRipple
      checked={setting.checked}
      disabled={setting.busy}
      onChange={(_, checked) => setting.onChange(checked)}
      inputProps={{ "aria-labelledby": `qbst-label-${setting.id}` }}
    />
  );
};

/** One row: label and description on the left, the control on the right. */
const SettingRow: React.FC<{ setting: QuickBooksSetting }> = ({ setting }) => {
  const isToggle = setting.kind === "toggle";
  const showHint = isToggle && !setting.checked && !setting.loading && Boolean(setting.offHint);

  return (
    <div className="qbst-row">
      <div className="qbst-row-copy">
        <span className="qbst-row-label" id={`qbst-label-${setting.id}`}>
          {setting.label}
        </span>
        <span className="qbst-row-desc">{setting.description}</span>
        {showHint ? <span className="qbst-row-hint">{setting.offHint}</span> : null}
      </div>
      <div className="qbst-row-control">
        {isToggle ? <ToggleControl setting={setting} /> : setting.render()}
      </div>
    </div>
  );
};

/** The line under the body reporting what happened to the last change. */
const SettingsFooter: React.FC<{ status: QuickBooksSettingsStatus }> = ({ status }) => {
  const { t } = useTranslation();
  if (status === "idle") return null;

  return (
    <footer className={`qbst-foot qbst-foot--${status}`} role="status">
      {status === "saved" ? <CheckCircleRoundedIcon /> : null}
      <span>{t(STATUS_KEY[status])}</span>
    </footer>
  );
};

/**
 * The settings panel. Renders whatever sections it is given, in order, and says
 * so plainly when it is given none.
 */
export const QuickBooksSettingsDialog: React.FC<QuickBooksSettingsDialogProps> = ({
  open,
  onClose,
  sections,
  status = "idle",
}) => {
  const { t } = useTranslation();
  const filled = sections.filter((section) => section.settings.length > 0);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="qbst-title"
      PaperProps={{ className: "qbst-paper" }}
    >
      <div className="qbst-root" role="region" aria-label={t("quickbooks.settings.title")}>
        <header className="qbst-head">
          <span className="qbst-head-mark" aria-hidden="true">
            <span>qb</span>
          </span>
          <span className="qbst-head-txt">
            <h2 className="qbst-head-title" id="qbst-title">
              {t("quickbooks.settings.title")}
            </h2>
            <p className="qbst-head-sub">{t("quickbooks.settings.subtitle")}</p>
          </span>
          <button
            type="button"
            className="qbst-close"
            onClick={onClose}
            aria-label={t("quickbooks.settings.close")}
          >
            <CloseRoundedIcon />
          </button>
        </header>

        <div className="qbst-body">
          {filled.length ? (
            filled.map((section) => (
              <section className="qbst-section" key={section.id}>
                <h3 className="qbst-section-label">{section.label}</h3>
                <div className="qbst-section-rows">
                  {section.settings.map((setting) => (
                    <SettingRow key={setting.id} setting={setting} />
                  ))}
                </div>
              </section>
            ))
          ) : (
            <p className="qbst-empty">{t("quickbooks.settings.empty")}</p>
          )}
        </div>

        <SettingsFooter status={status} />
      </div>
    </Dialog>
  );
};

export interface QuickBooksSettingsLauncherProps {
  /** null until the tenant's saved answer arrives. */
  autoSyncEnabled: boolean | null;
  autoSyncStatus: QuickBooksSettingsStatus;
  onAutoSyncChange: (next: boolean) => void;
}

/** The preferences this workspace offers, as data the panel can draw. */
const buildSections = (
  t: (key: string) => string,
  autoSyncEnabled: boolean | null,
  saving: boolean,
  onAutoSyncChange: (next: boolean) => void,
): QuickBooksSettingsSection[] => [
  {
    id: "sync",
    label: t("quickbooks.settings.sectionSync"),
    settings: [
      {
        kind: "toggle",
        id: "auto-sync",
        label: t("quickbooks.settings.autoSyncLabel"),
        description: t("quickbooks.settings.autoSyncDescription"),
        offHint: t("quickbooks.settings.autoSyncOffHint"),
        checked: autoSyncEnabled === true,
        loading: autoSyncEnabled === null,
        busy: saving,
        onChange: onAutoSyncChange,
      },
    ],
  },
];

/**
 * The rail-footer entry point and the preferences it opens, as one unit.
 *
 * The workspace mounts this and nothing else: the open state, the section list
 * and the wording all live here, so adding the next tenant-level preference is
 * a change to this file rather than to the table that hosts it.
 *
 * The workspace reports the outcome of a save; how long the confirmation stays
 * on screen is this panel's business, so the timer lives here.
 */
export const QuickBooksSettingsLauncher: React.FC<QuickBooksSettingsLauncherProps> = ({
  autoSyncEnabled,
  autoSyncStatus,
  onAutoSyncChange,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [savedVisible, setSavedVisible] = useState(false);

  useEffect(() => {
    if (autoSyncStatus !== "saved") {
      setSavedVisible(false);
      return undefined;
    }
    setSavedVisible(true);
    const timer = window.setTimeout(() => setSavedVisible(false), SAVED_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [autoSyncStatus]);

  const status: QuickBooksSettingsStatus =
    autoSyncStatus === "saved" && !savedVisible ? "idle" : autoSyncStatus;

  return (
    <>
      <button
        type="button"
        className="qbw-rail-link qbw-rail-link--icon"
        onClick={() => setOpen(true)}
      >
        <SettingsOutlinedIcon />
        {t("quickbooks.settings.railLink")}
      </button>
      <QuickBooksSettingsDialog
        open={open}
        onClose={() => setOpen(false)}
        status={status}
        sections={buildSections(t, autoSyncEnabled, autoSyncStatus === "saving", onAutoSyncChange)}
      />
    </>
  );
};

export default QuickBooksSettingsDialog;

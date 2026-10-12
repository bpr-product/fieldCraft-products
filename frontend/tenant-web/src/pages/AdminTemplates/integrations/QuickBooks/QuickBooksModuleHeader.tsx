/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Sticky per-module header for the connected workspace: title + description, a
 * grouped split Sync button (the "how much to sync" mode lives inside its menu,
 * so it reads as sync configuration — not a filter), and a toolbar with search
 * plus status filter pills that double as the summary.
 */
import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip } from "@mui/material";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import AutorenewRoundedIcon from "@mui/icons-material/AutorenewRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import type { QbTone } from "./QuickBooksStatus";

export type SyncMode = "cdc" | "full";

export interface FilterPill {
  id: string;
  label: string;
  count?: number | null;
  /** Colored dot matching the status tone. */
  tone?: QbTone;
}

export interface QuickBooksModuleHeaderProps {
  title: string;
  description?: string;
  /** Small icon chip shown beside the title. */
  titleIcon?: React.ReactNode;
  /**
   * Inline affordance rendered immediately after the title text — e.g. an info
   * icon that opens a short explanation about the module's list. A slot rather
   * than a set of data props, so the header does not have to know what the hint
   * says or how it opens.
   */
  titleHint?: React.ReactNode;

  // Sync
  onSync: () => void;
  syncing?: boolean;
  syncButtonLabel?: string;
  syncDisabled?: boolean;
  /** Message shown on hover when the sync button is disabled (e.g. another sync is running). */
  syncDisabledReason?: string;
  /** Show the "how much to sync" menu (not every module supports modes). */
  showSyncMode?: boolean;
  syncMode?: SyncMode;
  onSyncModeChange?: (mode: SyncMode) => void;

  // Search
  search: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder?: string;

  // Status filter pills
  filters?: FilterPill[];
  activeFilter?: string;
  onFilterChange?: (id: string) => void;

  /** Extra actions rendered before the sync controls (e.g. Assign, Settings). */
  actions?: React.ReactNode;

  /**
   * Module-scoped context rendered at the trailing end of the toolbar row, in the
   * space the search field and filter pills leave empty — e.g. the records this
   * module attaches to in QuickBooks. Kept a slot rather than a set of data props so
   * the header does not have to know what the context is about.
   */
  context?: React.ReactNode;
}

export const QuickBooksModuleHeader: React.FC<QuickBooksModuleHeaderProps> = ({
  title,
  description,
  titleIcon,
  titleHint,
  onSync,
  syncing = false,
  syncButtonLabel,
  syncDisabled = false,
  syncDisabledReason,
  showSyncMode = false,
  syncMode = "cdc",
  onSyncModeChange,
  search,
  onSearchChange,
  searchPlaceholder,
  filters,
  activeFilter,
  onFilterChange,
  actions,
  context,
}) => {
  const { t } = useTranslation();
  const syncLabel = syncButtonLabel ?? t("quickbooks.moduleHeader.sync");
  const searchPlaceholderText = searchPlaceholder ?? t("quickbooks.moduleHeader.searchPlaceholder");
  const [menuOpen, setMenuOpen] = useState(false);
  const syncRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDocMouseDown = (e: MouseEvent) => {
      if (syncRef.current && !syncRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const chooseMode = (mode: SyncMode) => {
    onSyncModeChange?.(mode);
    setMenuOpen(false);
  };

  return (
    <div className="qbw-sticky">
      <div className="qbw-whead">
        <div className="qbw-whead-l">
          {titleIcon ? <div className="qbw-title-ico" aria-hidden="true">{titleIcon}</div> : null}
          <div>
            {/* The hint sits beside the heading, not inside it — nesting its button
                would fold the button's label into the heading's accessible name. */}
            <div className="qbw-wtitle-row">
              <h2 className="qbw-wtitle">{title}</h2>
              {titleHint}
            </div>
            {description ? <p className="qbw-wdesc">{description}</p> : null}
          </div>
        </div>

        <div className="qbw-sync-cluster">
          {actions}
          <div className="qbw-sync-wrap" ref={syncRef}>
            <Tooltip
              title={syncDisabled && !syncing && syncDisabledReason ? syncDisabledReason : ""}
              arrow
              placement="bottom"
            >
              <div className={`qbw-splitbtn${syncing ? " qbw-splitbtn--busy" : ""}`}>
                <button
                  type="button"
                  className="qbw-split-main"
                  onClick={onSync}
                  disabled={syncDisabled || syncing}
                  aria-busy={syncing || undefined}
                >
                  {syncing ? <span className="qbw-spin" aria-hidden="true" /> : <AutorenewRoundedIcon />}
                  {syncing ? t("quickbooks.moduleHeader.syncing") : syncLabel}
                </button>
                {showSyncMode && (
                  <button
                    type="button"
                    className="qbw-split-caret"
                    onClick={() => setMenuOpen((o) => !o)}
                    disabled={syncDisabled || syncing}
                    aria-label={t("quickbooks.moduleHeader.syncOptions")}
                    aria-haspopup="true"
                    aria-expanded={menuOpen}
                  >
                    <ExpandMoreRoundedIcon />
                  </button>
                )}
              </div>
            </Tooltip>

            {showSyncMode && menuOpen && (
              <div className="qbw-sync-menu" role="menu">
                <div className="qbw-sync-menu-label">{t("quickbooks.moduleHeader.howMuchToSync")}</div>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={syncMode === "cdc"}
                  className={`qbw-sync-opt${syncMode === "cdc" ? " qbw-sync-opt--on" : ""}`}
                  onClick={() => chooseMode("cdc")}
                >
                  <CheckRoundedIcon className="qbw-sync-opt-check" />
                  <span>
                    <span className="qbw-sync-opt-t">{t("quickbooks.moduleHeader.cdcTitle")}</span>
                    <span className="qbw-sync-opt-d">{t("quickbooks.moduleHeader.cdcDescription")}</span>
                  </span>
                </button>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={syncMode === "full"}
                  className={`qbw-sync-opt${syncMode === "full" ? " qbw-sync-opt--on" : ""}`}
                  onClick={() => chooseMode("full")}
                >
                  <CheckRoundedIcon className="qbw-sync-opt-check" />
                  <span>
                    <span className="qbw-sync-opt-t">{t("quickbooks.moduleHeader.fullTitle")}</span>
                    <span className="qbw-sync-opt-d">{t("quickbooks.moduleHeader.fullDescription")}</span>
                  </span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="qbw-toolbar">
        <label className="qbw-search">
          <SearchRoundedIcon />
          <input
            type="text"
            value={search}
            placeholder={searchPlaceholderText}
            onChange={(e) => onSearchChange(e.target.value)}
            aria-label={searchPlaceholderText}
          />
        </label>

        {filters && filters.length > 0 && (
          <div className="qbw-fgroup">
            <span className="qbw-fgroup-label">{t("quickbooks.moduleHeader.show")}</span>
            <div className="qbw-fpills" role="group" aria-label={t("quickbooks.moduleHeader.filterByStatus")}>
              {filters.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className="qbw-fpill"
                  aria-pressed={activeFilter === f.id}
                  onClick={() => onFilterChange?.(f.id)}
                >
                  {f.tone ? <span className={`qbw-fpill-dot qbw-fpill-dot--${f.tone}`} /> : null}
                  {f.label}
                  {f.count != null ? <span className="qbw-fpill-n">{f.count}</span> : null}
                </button>
              ))}
            </div>
          </div>
        )}

        {context ? <div className="qbw-toolbar-ctx">{context}</div> : null}
      </div>
    </div>
  );
};

export default QuickBooksModuleHeader;

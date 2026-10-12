/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * The single quiet module selector for the connected QuickBooks workspace.
 * Replaces the previous three navigators (card strip + pipeline pills +
 * prerequisites hint). Modules are grouped by connection; a module with items
 * needing attention shows a small amber badge so problems surface at a glance.
 *
 * Clicking the QuickBooks mark collapses the rail to icons only, giving the
 * module table more room; clicking it again expands it back.
 */
import React, { useState } from "react";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";

export interface RailModule {
  id: string;
  label: string;
  icon: React.ReactNode;
  /** Neutral total count shown at the right when there's nothing to flag. */
  count?: number | null;
  /** When > 0, shows an amber badge instead of the count. */
  attention?: number | null;
}

export interface RailGroup {
  label: string;
  modules: RailModule[];
}

export interface QuickBooksWorkspaceRailProps {
  groups: RailGroup[];
  selectedId: string;
  onSelect: (id: string) => void;
  /** e.g. "Both connected". */
  statusLabel?: string;
  /** Hide the QuickBooks header (e.g. when a banner above already shows it). */
  showHeader?: boolean;
  /** Rendered pinned to the bottom of the rail (e.g. Disconnect actions). */
  footer?: React.ReactNode;
}

export const QuickBooksWorkspaceRail: React.FC<QuickBooksWorkspaceRailProps> = ({
  groups,
  selectedId,
  onSelect,
  statusLabel = "Both connected",
  showHeader = true,
  footer,
}) => {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside className={`qbw-rail${collapsed ? " qbw-rail--collapsed" : ""}`}>
      {showHeader && (
        <button
          type="button"
          className="qbw-rail-head"
          onClick={() => setCollapsed((c) => !c)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand QuickBooks menu" : "Collapse QuickBooks menu"}
          title={collapsed ? "Expand menu" : "Collapse menu"}
        >
          <div className="qbw-mark" aria-hidden="true"><span>qb</span></div>
          <div className="qbw-rail-head-txt">
            <div className="qbw-rail-head-t">QuickBooks</div>
            <div className="qbw-rail-head-s"><span className="qbw-live" />{statusLabel}</div>
          </div>
          <ChevronLeftRoundedIcon className="qbw-rail-chev" />
        </button>
      )}

      {groups.map((group) => (
        <React.Fragment key={group.label}>
          <div className="qbw-rail-group">{group.label}</div>
          {group.modules.map((mod) => {
            const selected = mod.id === selectedId;
            return (
              <button
                key={mod.id}
                type="button"
                className={`qbw-mod${selected ? " qbw-mod--sel" : ""}`}
                aria-current={selected || undefined}
                onClick={() => onSelect(mod.id)}
                title={mod.label}
              >
                <span className="qbw-mod-ico" aria-hidden="true">{mod.icon}</span>
                <span className="qbw-mod-name">{mod.label}</span>
                {mod.attention ? (
                  <span className="qbw-mod-badge" title={`${mod.attention} need attention`}>{mod.attention}</span>
                ) : mod.count != null ? (
                  <span className="qbw-mod-count">{mod.count}</span>
                ) : null}
                {mod.attention ? <span className="qbw-mod-dot" aria-hidden="true" title={`${mod.attention} need attention`} /> : null}
              </button>
            );
          })}
        </React.Fragment>
      ))}
      {footer ? <div className="qbw-rail-foot">{footer}</div> : null}
    </aside>
  );
};

export default QuickBooksWorkspaceRail;

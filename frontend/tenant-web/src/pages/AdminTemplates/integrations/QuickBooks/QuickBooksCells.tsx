/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Reusable table cells for the QuickBooks workspace. The identity cell turns a
 * name/source/email into a designed row — a leading mark, the name, a small
 * "From FIELDCRAFT / From QuickBooks" pill, and a quiet email line — instead of a raw
 * stack of text.
 *
 * The leading mark uses SHAPE to say what a row is, which is the same language the
 * rest of the workspace already speaks (round `.qbw-live` dots for state, rounded
 * squares for `.qbw-mod-ico` / `.qbw-title-ico` module glyphs):
 *
 *   circle + initials  → a person or company you correspond with
 *                        (customers, vendors, employees)
 *   rounded square + glyph → a record (a bill, a project, an account, a service)
 *
 * Initials only mean something for a name a human chose for a person or business.
 * On a record like "Bill #4021" or a project code a monogram reads as a profile
 * photo that failed to load, so those callers pass an `icon` instead.
 */
import React from "react";
import MailOutlineRoundedIcon from "@mui/icons-material/MailOutlineRounded";

/** Soft, calm avatar tints (background + foreground), chosen per-name. */
const AVATAR_TINTS: { bg: string; fg: string }[] = [
  { bg: "rgba(37,99,235,0.14)", fg: "#2f6fe0" },
  { bg: "rgba(15,157,104,0.15)", fg: "#0f9d68" },
  { bg: "rgba(124,92,196,0.16)", fg: "#8158d6" },
  { bg: "rgba(194,118,30,0.16)", fg: "#c2761e" },
  { bg: "rgba(208,90,120,0.15)", fg: "#d05a78" },
  { bg: "rgba(91,122,153,0.16)", fg: "#5b7a99" },
];

function monogramInitials(name: string): string {
  const parts = name.replace(/^(DEMO-|SYN-)/i, "").trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "";
  const second = parts[1]?.[0] ?? "";
  return (first + second).toUpperCase() || "?";
}

function tintForName(name: string): { bg: string; fg: string } {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_TINTS[hash % AVATAR_TINTS.length];
}

/** Circular initials mark for a person or company. Tinted per name so a row the user
 *  scans for repeatedly stays recognisable at a glance. */
const QbMonogram: React.FC<{ name: string }> = ({ name }) => {
  const safeName = name || "?";
  const tint = tintForName(safeName);
  return (
    <span className="qbw-avatar" style={{ background: tint.bg, color: tint.fg }} aria-hidden="true">
      {monogramInitials(safeName)}
    </span>
  );
};

export interface QbIdentityCellProps {
  name: string;
  /**
   * Renders a rounded-square record tile holding this glyph instead of the circular
   * monogram avatar. Pass it for records (bills, projects, accounts, services, time
   * activity); omit it for people and companies, which keep the monogram.
   */
  icon?: React.ReactNode;
  /** "FIELDCRAFT" | "QuickBooks" (anything else / null hides the pill). */
  source?: string | null;
  /** Rendered as a quiet line with a mail glyph. */
  email?: string | null;
  /** Alternative secondary line (e.g. invoice number) when there's no email. */
  sub?: React.ReactNode;
  /** Small pill rendered after the name (e.g. W-2/1099, Stock). */
  badge?: React.ReactNode;
  /** Let a long name use up to two lines instead of truncating on one
   *  (e.g. project names). Off by default so other modules stay single-line. */
  allowWrap?: boolean;
}

export const QbIdentityCell: React.FC<QbIdentityCellProps> = ({ name, icon, source, email, sub, badge, allowWrap }) => {
  const isQbo = source === "QuickBooks";
  return (
    <div className="qbw-idc">
      {icon ? (
        <span className="qbw-rectile" aria-hidden="true">{icon}</span>
      ) : (
        <QbMonogram name={name} />
      )}
      <div className="qbw-idc-txt">
        {/* A wrapping name and its pill/badge share one inline run so the pill hugs the last
            word; single-line names stay a flex row. See `.qbw-idc-line1--wrap`. */}
        <div className={`qbw-idc-line1${allowWrap ? " qbw-idc-line1--wrap" : ""}`}>
          <span className={`qbw-idc-name${allowWrap ? " qbw-idc-name--wrap" : ""}`} title={name}>{name}</span>
          {source ? (
            <span className={`qbw-src-pill ${isQbo ? "qbw-src-pill--qbo" : "qbw-src-pill--fieldcraft"}`}>
              {isQbo ? "From QuickBooks" : "From FIELDCRAFT"}
            </span>
          ) : null}
          {badge}
        </div>
        {email ? (
          <div className="qbw-idc-email"><MailOutlineRoundedIcon /><span className="qbw-idc-linetxt" title={email}>{email}</span></div>
        ) : sub ? (
          <div className="qbw-idc-sub"><span className="qbw-idc-linetxt" title={typeof sub === "string" ? sub : undefined}>{sub}</span></div>
        ) : null}
      </div>
    </div>
  );
};

export default QbIdentityCell;

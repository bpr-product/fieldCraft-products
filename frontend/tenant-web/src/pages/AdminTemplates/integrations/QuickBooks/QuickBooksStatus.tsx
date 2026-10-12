/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Calm, restrained sync-status system for the QuickBooks workspace.
 * Four tones only — ok (synced, blue), wait (pending), warn (needs attention),
 * bad (failed) — replacing the previous ten-colour rainbow.
 */
import React from "react";
import { Tooltip } from "@mui/material";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";

export type QbTone = "ok" | "wait" | "warn" | "bad";

const TONE_BY_STATUS: Record<string, QbTone> = {
  SYNCED: "ok",
  PENDING_PUSH: "wait",
  PENDING_PULL: "wait",
  SYNCING: "wait",
  ASSIGNMENT_NEEDED: "warn",
  IMPORT_FAILED: "warn",
  NOT_READY: "warn",
  SYNCED_INCOMPLETE: "warn",
  AWAITING_APPROVAL: "warn",
  CONFLICT: "warn",
  INVALID: "warn",
  FAILED: "bad",
  SYNC_FAILED: "bad",
};

const LABEL_BY_STATUS: Record<string, string> = {
  SYNCED: "Synced",
  PENDING_PUSH: "Pending",
  PENDING_PULL: "Pending",
  SYNCING: "Pending",
  ASSIGNMENT_NEEDED: "Needs attention",
  IMPORT_FAILED: "Needs attention",
  NOT_READY: "Not ready",
  SYNCED_INCOMPLETE: "Needs attention",
  AWAITING_APPROVAL: "Awaiting approval",
  CONFLICT: "Needs attention",
  INVALID: "Needs attention",
  FAILED: "Failed",
  SYNC_FAILED: "Failed",
};

/** Map a backend sync status to one of the four calm tones. Unmapped states
 *  (including not-yet-linked rows) fall back to the neutral "wait" tone;
 *  {@link syncLabel} is what differentiates the wording ("Not synced" vs "Pending"). */
export function syncTone(status?: string | null): QbTone {
  const key = String(status || "").toUpperCase();
  return TONE_BY_STATUS[key] ?? "wait";
}

/** Map a backend sync status to a short, user-first label. */
export function syncLabel(status?: string | null, linked?: boolean): string {
  const key = String(status || "").toUpperCase();
  if (LABEL_BY_STATUS[key]) return LABEL_BY_STATUS[key];
  return linked === false ? "Not synced" : "Pending";
}

export interface QbStatusCellProps {
  status?: string | null;
  /** When false and no status maps, renders "Not synced". */
  linked?: boolean;
  /** Optional specific reason shown under the label (e.g. "No project linked"). */
  reason?: string | null;
  /** Override the computed label. */
  label?: string;
}

/** A status dot + label, with an optional reason line underneath. */
export const QbStatusCell: React.FC<QbStatusCellProps> = ({ status, linked, reason, label }) => {
  const tone = syncTone(status);
  const text = label || syncLabel(status, linked);
  return (
    <span className={`qbw-spill qbw-spill--${tone}`}>
      <span className="qbw-spill-dot" />
      {text}
      {reason ? (
        <Tooltip title={reason} arrow placement="top">
          <span className="qbw-spill-info" tabIndex={0} role="img" aria-label={`Details: ${reason}`}>
            <InfoOutlinedIcon />
          </span>
        </Tooltip>
      ) : null}
    </span>
  );
};

export default QbStatusCell;

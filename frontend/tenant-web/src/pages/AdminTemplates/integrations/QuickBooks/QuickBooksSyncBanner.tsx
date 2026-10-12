/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Calm "sync in progress" banner for the connected workspace. Replaces the old
 * per-module colored boxes (each a different loud hue, MUI spinner + chip) with
 * one restrained, on-brand strip: a spinner, a plain-language title + detail, a
 * small state pill, and a quiet indeterminate progress line. Same look for every
 * module, so a running sync reads consistently rather than as nine themes.
 */
import React from "react";

export interface QuickBooksSyncBannerProps {
  /** Plain-language heading, e.g. "Syncing vendors". */
  title: string;
  /** One quiet line under the title. */
  detail?: React.ReactNode;
  /** Raw job status (e.g. "RUNNING"); shown tidied in the state pill. */
  status?: string | null;
}

function tidyStatus(status?: string | null): string {
  if (!status) return "In progress";
  const s = status.replace(/_/g, " ").trim().toLowerCase();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "In progress";
}

export const QuickBooksSyncBanner: React.FC<QuickBooksSyncBannerProps> = ({ title, detail, status }) => (
  <div className="qbw-syncbar" role="status" aria-live="polite">
    <span className="qbw-syncbar-spin" aria-hidden="true" />
    <div className="qbw-syncbar-txt">
      <span className="qbw-syncbar-title">{title}</span>
      {detail ? <span className="qbw-syncbar-detail">{detail}</span> : null}
    </div>
    <span className="qbw-syncbar-state">{tidyStatus(status)}</span>
    <span className="qbw-syncbar-track" aria-hidden="true"><span className="qbw-syncbar-fill" /></span>
  </div>
);

export default QuickBooksSyncBanner;

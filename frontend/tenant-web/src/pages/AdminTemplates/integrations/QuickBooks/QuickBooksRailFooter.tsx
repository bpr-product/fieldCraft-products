/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * @author Bala Pradeep
 *
 * The three links pinned to the foot of the connected workspace's rail:
 * how the integration works, its settings, and disconnecting it.
 *
 * They are one component because they read as one set. Each carries a leading
 * glyph — a question mark, a gear, a broken link — so the group is scannable
 * without reading it, and so no single link looks like an ornament the others
 * lack. Disconnect keeps the danger tone it already had; the icon does not
 * soften it.
 *
 * The order is deliberate: reference, then configuration, then the one
 * irreversible action, kept last and visually apart from the two that are safe
 * to click out of curiosity.
 *
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR      |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Initial — rail footer links as
 *                |            |                 | one iconed set
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Carry the auto sync save
 *                |            |                 | status through to the panel
 * -----------------------------------------------------------------------------
 */
import React from "react";
import { useTranslation } from "react-i18next";
import HelpOutlineRoundedIcon from "@mui/icons-material/HelpOutlineRounded";
import LinkOffRoundedIcon from "@mui/icons-material/LinkOffRounded";
import {
  QuickBooksSettingsLauncher,
  QuickBooksSettingsStatus,
} from "./QuickBooksSettingsDialog";
import "./QuickBooksWorkspace.scss";

/* The workspace reaches the panel through this component, so it takes the save
   status from here rather than importing the panel it does not render. */
export type { QuickBooksSettingsStatus } from "./QuickBooksSettingsDialog";

export interface QuickBooksRailFooterProps {
  /** null until the tenant's saved answer arrives. */
  autoSyncEnabled: boolean | null;
  autoSyncStatus: QuickBooksSettingsStatus;
  onAutoSyncChange: (next: boolean) => void;
  onOpenGuide: () => void;
  onDisconnect: () => void;
  disconnecting: boolean;
}

export const QuickBooksRailFooter: React.FC<QuickBooksRailFooterProps> = ({
  autoSyncEnabled,
  autoSyncStatus,
  onAutoSyncChange,
  onOpenGuide,
  onDisconnect,
  disconnecting,
}) => {
  const { t } = useTranslation();

  return (
    <>
      <button
        type="button"
        className="qbw-rail-link qbw-rail-link--icon"
        onClick={onOpenGuide}
      >
        <HelpOutlineRoundedIcon />
        {t("quickbooks.rail.howItWorks")}
      </button>

      <QuickBooksSettingsLauncher
        autoSyncEnabled={autoSyncEnabled}
        autoSyncStatus={autoSyncStatus}
        onAutoSyncChange={onAutoSyncChange}
      />

      <button
        type="button"
        className="qbw-rail-link qbw-rail-link--icon qbw-rail-link--danger"
        onClick={onDisconnect}
        disabled={disconnecting}
      >
        <LinkOffRoundedIcon />
        {disconnecting ? t("quickbooks.rail.disconnecting") : t("quickbooks.rail.disconnect")}
      </button>
    </>
  );
};

export default QuickBooksRailFooter;

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 05-Sep-2026
 * @author      : Vijayakumar S
 * @since       : 1.5.3.3
 * Requirement# : Asset-triggered auto-estimate — step 5 (the UI button)
 * Purpose      : Asset-list "Auto Estimate" trigger. Presentation only — all behaviour (the
 *                trigger, the HARD/SOFT-block pre-checks, status polling, the failure budget)
 *                lives in `useAutoEstimate` so the customer-portal Assets tab's Tailwind shell
 *                (`AutoEstimateTile`) can reuse it unchanged; CLAUDE.md forbids mixing MUI v5 and
 *                Tailwind inside one screen, so each surface keeps its own thin shell over the one
 *                shared hook. See AutoEstimateButton.test.tsx (locked) for the full contract.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |   AUTHOR         |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AutoEst S5     | 05-Sep-2026 | Vijayakumar S    | Initial creation — trigger, HARD/SOFT-block
 *                |             |                  | dialogs, status polling
 * AutoEst Portal | 07-Sep-2026 | FIELDCRAFT Web          | Reduced to a thin MUI shell over the extracted
 *                |             |                  | `useAutoEstimate` hook — no behaviour change
 * AutoEst Notify | 07-Sep-2026 | FIELDCRAFT Web          | Removed the stage caption (no more polling);
 *                |             |                  | replaced with the "requested" acknowledgement
 * -----------------------------------------------------------------------------
 */
import React from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { isAssetAddressUnvalidated, useAutoEstimate } from "../../../hooks/useAutoEstimate";
import OnboardingIncompleteWarning from "./OnboardingIncompleteWarning";

interface AutoEstimateButtonProps {
  assetUid: string;
  /** This asset's `status`, when the caller has it. "Parked" withholds the action entirely,
   *  because no estimate is possible until the address is corrected. Omit it and the server
   *  decides — see `isAssetAddressUnvalidated`. */
  assetStatus?: string | null;
}

/**
 * The two pre-check dialogs are deliberately different shapes, matching the server's own HARD vs
 * SOFT distinction (EstimateAutomationJobService.enqueue):
 *  - 422 ROOF_AREA_REQUIRED — a HARD block. `confirm` cannot bypass it server-side, so this
 *    dialog offers no "create anyway" control of any kind — only a way to close it.
 *  - 409 ESTIMATE_EXISTS — a SOFT block. Offers "Create another" (re-posts with confirm: true)
 *    and "Open existing" (hands the existing project straight to the caller, no request).
 */
const AutoEstimateButton: React.FC<AutoEstimateButtonProps> = ({ assetUid, assetStatus }) => {
  const { t } = useTranslation();
  const {
    running,
    requested,
    alreadyRunning,
    failed,
    lastError,
    roofAreaBlocked,
    existingProjectUid,
    start,
    createAnother,
    retry,
    dismissRoofArea,
    dismissExisting,
    addressUnvalidated,
    customerInvited,
    dismissCustomerInvited,
  } = useAutoEstimate(assetUid);

  // Proactively from the status we were handed, or reactively from the server's 400 when the
  // asset was parked between render and click. Either way there is nothing to offer.
  const addressBlocked = isAssetAddressUnvalidated(assetStatus) || addressUnvalidated;

  return (
    <Stack spacing={0.5} alignItems="flex-start">
      {/* Withheld, not disabled: the address has to be fixed elsewhere, so there is no state
          in which pressing this would help. The reason renders in its place — a control that
          simply vanishes is indistinguishable from one that failed to render. */}
      {addressBlocked ? (
        <Typography variant="caption" color="text.secondary">
          {t("asset.autoEstimate.addressNotValidated")}
        </Typography>
      ) : (
      <Button
        variant="outlined"
        size="small"
        onClick={start}
        disabled={running || requested || alreadyRunning}
      >
        {running ? t("asset.autoEstimate.triggerRunning") : t("asset.autoEstimate.trigger")}
      </Button>
      )}

      {requested && (
        <Typography variant="caption" color="text.secondary">
          {t("asset.autoEstimate.requested")}
        </Typography>
      )}
      {alreadyRunning && (
        <Typography variant="caption" color="text.secondary">
          {t("asset.autoEstimate.alreadyRunning")}
        </Typography>
      )}

      {failed && (
        <Stack spacing={0.5} alignItems="flex-start">
          <Typography variant="caption" color="error">
            {lastError}
          </Typography>
          <Button size="small" onClick={retry} disabled={running}>
            {t("common.retry")}
          </Button>
        </Stack>
      )}

      {/* HARD block — no "create anyway" control anywhere in this dialog. */}
      <Dialog
        open={roofAreaBlocked}
        onClose={dismissRoofArea}
        aria-labelledby="auto-estimate-roof-area-title"
        aria-describedby="auto-estimate-roof-area-description"
      >
        <DialogTitle id="auto-estimate-roof-area-title">
          {t("asset.autoEstimate.roofAreaRequiredTitle")}
        </DialogTitle>
        <DialogContent>
          <DialogContentText id="auto-estimate-roof-area-description">
            {t("asset.autoEstimate.roofAreaRequiredBody")}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={dismissRoofArea}>{t("dialog.close")}</Button>
        </DialogActions>
      </Dialog>

      {/* HARD block — the customer has not finished onboarding. Bespoke overlay, not this file's
          plain Dialog: per Onboarding Incomplete Warning (3a) Spec, the backdrop click must be a
          no-op here (unlike the roof-area dialog above), and the animated icon/status-pill layout
          does not fit Dialog's default chrome. */}
      <OnboardingIncompleteWarning open={customerInvited} onClose={dismissCustomerInvited} />

      {/* SOFT block — Cancel / Open existing / Create another. */}
      <Dialog
        open={!!existingProjectUid}
        onClose={dismissExisting}
        aria-labelledby="auto-estimate-exists-title"
        aria-describedby="auto-estimate-exists-description"
      >
        <DialogTitle id="auto-estimate-exists-title">
          {t("asset.autoEstimate.estimateExistsTitle")}
        </DialogTitle>
        <DialogContent>
          <DialogContentText id="auto-estimate-exists-description">
            {t("asset.autoEstimate.estimateExistsBody")}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          {/* No "Open existing" action: /estimateview needs a full projectData object in
              router state rather than a uid, so the control had nothing to navigate to.
              A locked gate asserts its absence. */}
          <Button onClick={dismissExisting}>{t("common.cancel")}</Button>
          <Button onClick={createAnother} variant="contained" disabled={running}>
            {t("asset.autoEstimate.createAnother")}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

export default AutoEstimateButton;

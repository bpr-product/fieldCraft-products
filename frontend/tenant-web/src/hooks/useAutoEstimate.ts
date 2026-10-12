/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 07-Sep-2026
 * @author      : FIELDCRAFT Web
 * @since       : 1.5.3.3
 * Requirement# : Asset-triggered auto-estimate — customer-portal Assets tab parity
 * Purpose      : ALL asset-triggered Auto Estimate behaviour (trigger, HARD/SOFT-block pre-checks)
 *                extracted out of `AutoEstimateButton` (MUI, asset list) so it can be reused
 *                unchanged by `AutoEstimateTile` (Tailwind, customer-portal Assets tab) — CLAUDE.md
 *                forbids mixing MUI v5 and Tailwind inside one screen, so the two surfaces need
 *                their own presentational shells, but the logic itself must exist exactly once.
 *                See AutoEstimateButton.test.tsx (locked) for the full behavioural contract this
 *                hook must preserve byte-for-byte.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |   AUTHOR         |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AutoEst Portal | 07-Sep-2026 | FIELDCRAFT Web          | New file — extracted from AutoEstimateButton
 * AutoEst Notify | 07-Sep-2026 | FIELDCRAFT Web          | Removed status polling (Abi, 2026-09-07): the
 *                |             |                  | worker now sends a bell notification on the
 *                |             |                  | run's terminal state, so the UI is fire-and-
 *                |             |                  | forget — queue the job, acknowledge, done. A
 *                |             |                  | second click after reload is safe without any
 *                |             |                  | client-side "is it running" tracking: the DB's
 *                |             |                  | uq_eaj_asset_in_flight partial index plus the
 *                |             |                  | ALREADY_RUNNING outcome reject a duplicate
 *                |             |                  | server-side.
 * -----------------------------------------------------------------------------
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useOidcAccessToken } from "@axa-fr/react-oidc";
import { requestAutoEstimate } from "../services/AssetService";

export interface UseAutoEstimateResult {
  /** True only while the POST itself is in flight. */
  running: boolean;
  /** True once the POST has succeeded (202 ENQUEUED or 200 ALREADY_RUNNING) — a job exists and
   *  the UI's job is done. Stays true for the rest of this mount; there is nothing client-side
   *  that would ever turn it back off (a reload is what resets it, on purpose). */
  requested: boolean;
  /** True when the server answered 200 ALREADY_RUNNING — a job for this asset was already
   *  in flight, so this press started nothing new. Distinct from `requested` on purpose:
   *  telling someone "requested" when nothing was requested is a small lie that makes
   *  them doubt the button. */
  alreadyRunning: boolean;
  failed: boolean;
  lastError: string;
  /** 422 ROOF_AREA_REQUIRED — HARD block. No forward action exists for it (see file doc). */
  roofAreaBlocked: boolean;
  /** True when the server refused because the asset is parked (its address could not be
   *  validated). A HARD, non-retryable block — see `isAssetAddressUnvalidated`. */
  addressUnvalidated: boolean;
  /** 412 CUSTOMER_INVITED — HARD block. The customer has not finished onboarding yet
   *  (customer.status = "Invited"), so there is no confirmed information to price an estimate
   *  against. No forward action exists for it, same as `roofAreaBlocked`. */
  customerInvited: boolean;
  /** 409 ESTIMATE_EXISTS — SOFT block. Non-null while the "estimate exists" choice is pending. */
  existingProjectUid: string | null;
  /** First press. Never pre-confirms — that is the entire point of the SOFT block. */
  start: () => void;
  /** SOFT block -> "Create Another": re-posts WITH confirm. */
  createAnother: () => void;
  /** Re-posts after a failed POST. */
  retry: () => void;
  /** Closes the HARD-block dialog/modal. */
  dismissRoofArea: () => void;
  /** Closes the customer-invited HARD-block dialog/modal. */
  dismissCustomerInvited: () => void;
  /** Closes the SOFT-block dialog/modal (Cancel). */
  dismissExisting: () => void;
}

/**
 * The two pre-check outcomes are deliberately different shapes, matching the server's own HARD vs
 * SOFT distinction (EstimateAutomationJobService.enqueue):
 *  - 422 ROOF_AREA_REQUIRED — a HARD block. `confirm` cannot bypass it server-side, so no shell
 *    built on this hook may offer a "create anyway" control of any kind — only a way to dismiss.
 *  - 409 ESTIMATE_EXISTS — a SOFT block. Offers "Create another" (re-posts with confirm: true)
 *    and a way to dismiss. "Open existing" was removed 2026-09-08 — it navigated nowhere,
 *    because /estimateview needs a full projectData object in router state, not a uid.
 */
/**
 * The one value of `asset.status` that means "this address was CONFIRMED invalid" — the server's
 * own `AssetService.PARKED_STATUS`. RoofLens sets it via LeadService.createAssetFromLead, and
 * AssetService.update clears it on a passing re-validation.
 */
export const PARKED_ASSET_STATUS = "Parked";

/**
 * Should the Auto Estimate action be withheld for this asset?
 *
 * A parked asset cannot produce an estimate at all: EstimateAutomationService creates a NEW
 * project carrying the assetUid, and ProjectService.save refuses exactly that
 * (INVALID_PROPERTY_ADDRESS) — the same gate that already blocks manual Create Bid. Offering the
 * button would queue a job whose only possible outcome is a failure notification minutes later.
 *
 * Compared exactly, matching the server's `PARKED_STATUS.equals(...)`. Any other value — Active,
 * absent (assets predating the flag), a different casing — is NOT parked, because the server
 * would not treat it as parked either, and withholding the button for something the server
 * accepts would be a feature outage rather than a guard.
 */
export const isAssetAddressUnvalidated = (status: string | null | undefined): boolean =>
  status === PARKED_ASSET_STATUS;

export const useAutoEstimate = (assetUid: string): UseAutoEstimateResult => {
  const { t } = useTranslation();
  const { accessToken } = useOidcAccessToken();

  const [running, setRunning] = useState(false);
  const [requested, setRequested] = useState(false);
  const [alreadyRunning, setAlreadyRunning] = useState(false);
  const [failed, setFailed] = useState(false);
  const [lastError, setLastError] = useState<string>("");
  const [roofAreaBlocked, setRoofAreaBlocked] = useState(false);
  const [addressUnvalidated, setAddressUnvalidated] = useState(false);
  const [customerInvited, setCustomerInvited] = useState(false);
  const [existingProjectUid, setExistingProjectUid] = useState<string | null>(null);

  // React.StrictMode double-invokes effects (L-WEB-037): a `useRef(true)` with the flag assigned
  // only in the cleanup gets permanently stuck `false` after the first mount/unmount/remount
  // cycle. Setting it `true` inside the effect body itself — not just at declaration — is what
  // keeps it correct across that double-invoke.
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const startRun = async (confirm: boolean) => {
    setRunning(true);
    setFailed(false);
    setLastError("");
    setExistingProjectUid(null);
    setAddressUnvalidated(false);
    setCustomerInvited(false);
    try {
      const result = await requestAutoEstimate(assetUid, confirm, accessToken);
      if (!mountedRef.current) {
        // The row scrolled out of the (virtualised) list, or the tile unmounted, while the
        // request was in flight — nothing should still be talking to the caller on its behalf.
        return;
      }
      // 202 ENQUEUED and 200 ALREADY_RUNNING both mean a job exists, but they do NOT mean the
      // same thing to the person who just pressed the button: one started a run, the other
      // found one already under way. Either way the UI acknowledges and stops — the worker's
      // bell notification is the completion signal from here on.
      setRunning(false);
      if (result?.status === 200) {
        setAlreadyRunning(true);
      } else {
        setRequested(true);
      }
    } catch (error: any) {
      if (!mountedRef.current) {
        return;
      }
      const status = error?.response?.status;
      if (status === 422) {
        setRunning(false);
        setRoofAreaBlocked(true);
        return;
      }
      // 400 — the asset is parked. Deliberately NOT the generic failure below: retrying can
      // never succeed, so offering Retry would be a lie. Bodyless by necessity (this
      // endpoint's response type is Map<String, UUID>), so the status alone identifies it.
      if (status === 400) {
        setRunning(false);
        setAddressUnvalidated(true);
        return;
      }
      if (status === 409) {
        setRunning(false);
        setExistingProjectUid(error?.response?.data?.existingProjectUid || null);
        return;
      }
      // 412 — the customer has not finished onboarding (status = "Invited"). Deliberately NOT
      // the generic failure below: retrying cannot succeed until the customer's profile is
      // completed elsewhere, so Retry would be a lie, same reasoning as the 400 case above.
      // Bodyless by necessity (this endpoint's response type is Map<String, UUID>), so the
      // status alone identifies it.
      if (status === 412) {
        setRunning(false);
        setCustomerInvited(true);
        return;
      }
      // Any other failure (network error, 5xx) used to reset the trigger with no explanation —
      // indistinguishable from a click that never landed. Surface it via failed/lastError so
      // Retry is available.
      setRunning(false);
      setFailed(true);
      setLastError(t("asset.autoEstimate.requestFailed"));
    }
  };

  const start = () => {
    startRun(false); // Never pre-confirm — that is the entire point of the SOFT block.
  };

  const dismissRoofArea = () => setRoofAreaBlocked(false);
  const dismissCustomerInvited = () => setCustomerInvited(false);
  const dismissExisting = () => setExistingProjectUid(null);
  const createAnother = () => startRun(true);
  const retry = () => startRun(false);

  return {
    running,
    requested,
    alreadyRunning,
    failed,
    lastError,
    roofAreaBlocked,
    addressUnvalidated,
    customerInvited,
    existingProjectUid,
    start,
    createAnother,
    retry,
    dismissRoofArea,
    dismissCustomerInvited,
    dismissExisting,
  };
};

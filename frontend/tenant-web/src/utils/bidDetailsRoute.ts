/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-09-2026
* @author      : Mohamed Riyaz A
* @since       : 2.0.0
* Requirement# : AB#30581
* Purpose      : One answer to "am I on the Bid Details page?", and one place that strips a
*                stale status off an autosave payload built from the page-open snapshot.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* AB#30581       | 10-09-2026  | Mohamed Riyaz A | New file — BID_DETAILS_ROUTES /
*                                                 isBidDetailsRoute / stripSnapshotStatus.
* -----------------------------------------------------------------------------
*/

/**
 * The Bid Details page is one component (`ProjectInformation`) mounted at TWO routes — see
 * `Routes.tsx`, which registers both `/salesestimate/projectinformation` and
 * `/soldproduction/projectinformation` against it. Every "am I on Bid Details?" guard in the
 * app was written as `window.location.pathname === '/soldproduction/projectinformation'`, so
 * on the Sales/Estimate route the page answered "no" to a question about itself: the
 * Contract-Approval read-only short-circuits never fired there, while the Q&A card's
 * next-question autosave ran anyway.
 */
export const BID_DETAILS_ROUTES = [
  '/soldproduction/projectinformation',
  '/salesestimate/projectinformation',
] as const;

/**
 * True when `pathname` is one of the Bid Details routes. Defaults to the browser's current
 * path, and answers false rather than throwing when there is no `window` (SSR / a test that
 * has not set up a location).
 */
export const isBidDetailsRoute = (
  pathname: string | undefined = typeof window !== 'undefined' ? window.location.pathname : undefined,
): boolean => (BID_DETAILS_ROUTES as readonly string[]).includes(pathname ?? '');

/**
 * Drops `status` from an autosave payload.
 *
 * The Bid Details autosaves are built by spreading `sessionStorage.currentBidData` — the whole
 * project as it was when the page opened — and replacing only `answerData`. Its `status` is
 * therefore a snapshot value, stale the moment anything else moves the board in the same
 * session. Measured on staging tn88351775: a Gantt Preview Submit advanced a bid
 * bidReview -> awaitingEstimation, and the next-question autosave 17 seconds later posted the
 * page-open "bidReview" back and dragged the board with it.
 *
 * An autosave saves ANSWERS. It has no opinion about the lifecycle, so it must not carry a
 * status at all. Use this on every payload spread from the snapshot; a deliberate status move
 * (the Create Bid wizard's Submit) builds its own payload and is unaffected.
 */
export const stripSnapshotStatus = <T extends Record<string, any>>(payload: T): Omit<T, 'status'> => {
  const { status, ...withoutStatus } = payload ?? ({} as T);
  return withoutStatus as Omit<T, 'status'>;
};

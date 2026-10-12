/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Derives the activity label shown in the thinking row ("Looking up
*                Buma P…", "Checking invoices…") from the utterance the user just
*                sent plus the page context EDEN is already being given.
*
*                Why client-side: /api/v1/agent/navigate returns only the finished
*                NavigationResponse -- there is no progress channel and no `activity`
*                field, so the honest options were a generic "EDEN is thinking" (what
*                ships today) or a local guess. A local guess is correct often enough
*                to be reassuring and, being derived from the user's own words, is
*                never a claim about what the agent found -- only about what it was
*                asked. If the backend later sends a real `activity` string, prefer
*                it and keep this as the fallback (see README, Backend section).
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import { useCallback } from "react";
import { CurrentEntity } from "../../services/AIChatService";

export const EDEN_ACTIVITY_DEFAULT = "Thinking…";

/**
 * Ordered rules -- first match wins, so the more specific domains are listed before
 * the generic navigation verbs ("where is my invoice" must read as invoices, not as
 * a page lookup).
 */
const ACTIVITY_RULES: ReadonlyArray<{ test: RegExp; label: string }> = [
  { test: /\b(invoice|invoices|bill|billed|payment|payments|paid|unpaid|receivable)\b/, label: "Checking invoices…" },
  { test: /\b(schedule|scheduled|start|starts|begin|finish|complete|date|dates|timeline|duration|delay)\b/, label: "Checking the schedule…" },
  { test: /\b(cost|costs|price|pricing|bid|estimate|margin|budget|change order|ledger)\b/, label: "Reading the ledger…" },
  { test: /\b(material|materials|inventory|tool|tools|resource|resources|purchase order)\b/, label: "Checking materials…" },
  { test: /\b(who|contact|support|supervisor|crew|assigned)\b/, label: "Looking up contacts…" },
  { test: /\b(where|how do i|how can i|open|go to|take me|navigate|show me|find)\b/, label: "Finding the right page…" },
];

/**
 * @param utterance     Exactly what the user sent.
 * @param currentEntity The entity already being POSTed in SessionContext -- when the
 *                      user is standing on a project page, naming it back is both
 *                      the most specific label available and provably true.
 */
export function resolveEdenActivity(
  utterance: string,
  currentEntity?: CurrentEntity
): string {
  const text = (utterance ?? "").toLowerCase().trim();
  if (!text) return EDEN_ACTIVITY_DEFAULT;

  // A named entity in context beats every keyword rule: it is the one label that
  // reflects real state rather than an inference from wording.
  const entityName = currentEntity?.name?.trim();
  if (entityName) {
    // Truncated so a long project name cannot push the row wider than the bubble.
    const shown = entityName.length > 28 ? `${entityName.slice(0, 27)}…` : entityName;
    return `Looking up ${shown}…`;
  }

  for (const rule of ACTIVITY_RULES) {
    if (rule.test.test(text)) return rule.label;
  }
  return EDEN_ACTIVITY_DEFAULT;
}

/**
 * Hook form, so callers do not re-create the resolver on every render of a panel
 * that re-renders on every streamed word.
 */
export function useEdenActivity(): (utterance: string, currentEntity?: CurrentEntity) => string {
  return useCallback(
    (utterance: string, currentEntity?: CurrentEntity) =>
      resolveEdenActivity(utterance, currentEntity),
    []
  );
}

export default useEdenActivity;

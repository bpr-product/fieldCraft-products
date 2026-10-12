/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose      : EDEN Navigation Agent client — replaces the old general-
*                purpose chat API (/chat, /chat_history, /chat_reset).
*                NOTE: EDEN's conversation state is no longer fully
*                stateless — a pending "which project?" goal and a
*                last-discussed-project memory now live server-side
*                (Redis, short TTLs) to support multi-turn follow-ups. See
*                resetNavigationSession, which clears that state explicitly.
*/
import { executeGetDataWithHeaders, executePostDataWithHeaders } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

const EDEN_BASE_URL = ConfigUrl.ai_chat_URL;

export interface CurrentEntity {
  type: string;
  // At least one of name/id is set. `id` is the exact projectId (preferred
  // -- the backend resolves it exactly, immune to duplicate names); `name`
  // is a fallback for pages that carry the project as an object with a name
  // but no id. See deriveCurrentEntity in useSessionContext.ts.
  name?: string;
  id?: string;
}

export interface SessionContext {
  utterance: string;
  currentRoute: string;
  currentEntity?: CurrentEntity;
  timeOnPage?: number;
  routeHistory?: string[];
  userId: string;
  tenantId: string;
}

export interface ProjectRef {
  id: string;
  name: string;
  status: string;
}

export interface DisambiguationOption {
  label: string;
  route: string;
  icon?: string;
  description?: string;
  project?: ProjectRef;
  routeState?: Record<string, any>;
}

export type NavigationResponse =
  | {
      type: "navigate";
      route: string;
      label: string;
      icon?: string;
      description?: string;
      module?: string;
      message: string;
      insight?: string;
      confidence: number;
      project?: ProjectRef;
      // Router state to pass to navigate(route, {state}) alongside route —
      // e.g. {selectedTab: "5"} for Admin-hosted modules (Materials/Tools/
      // Resources/Inventory) so the shared Admin shell (tab bar, Notes
      // panel) renders instead of the bare standalone page.
      routeState?: Record<string, any>;
    }
  | {
      type: "disambiguation";
      question: string;
      // Real bug, fixed: this was declared as always a real array, but the
      // backend can send a bare yes/no-style disambiguation with no
      // clickable options at all (e.g. EDEN's project-context-switch
      // confirmation, "you were asking about X, now on Y — continue with
      // X, or check Y?") -- options is null there. The type contract lying
      // about this is exactly why nothing downstream (OptionCardList)
      // null-checked it and crashed instead.
      options: DisambiguationOption[] | null;
    }
  | {
      type: "fallback";
      message: string;
      insight?: string;
      suggestion?: { label: string; route: string; routeState?: Record<string, any> };
      options?: DisambiguationOption[];
    }
  | {
      type: "guidance";
      message: string;
      insight?: string;
      highlight?: string;
      highlightTargets?: string[];
      // Front-desk follow-up: a successful Insight/Reporting answer offers a
      // relevant page here ("Job Ledger", "Invoices", "Projects") so the
      // conversation continues instead of dead-ending on the bare answer.
      // Previously the backend sent this on guidance answers but the render
      // dropped it -- see NavigationMessage's guidance case.
      suggestion?: { label: string; route: string; routeState?: Record<string, any> };
    };

export const EDEN_UNAVAILABLE_MESSAGE =
  "Oops, looks like I need a quick reboot break..Try again later!";

export async function sendNavigationRequest(
  accessToken: any,
  context: SessionContext
): Promise<NavigationResponse> {
  const url = `${EDEN_BASE_URL}/api/v1/agent/navigate`;
  const res = await executePostDataWithHeaders<NavigationResponse>(
    url,
    context,
    accessToken
  );

  if (!res.ok || !res.data?.type) {
    return { type: "fallback", message: EDEN_UNAVAILABLE_MESSAGE };
  }
  return res.data;
}

export interface HistoryTurn {
  utterance: string;
  type: string;
  route?: string | null;
  label?: string | null;
  response: NavigationResponse | Record<string, never>;
  created_at?: string | null;
}

// Durable readback of past turns (see source/api/navigation_api.py's
// GET /history) — used to repopulate the chat window on reopen/reload.
// Best-effort: any failure returns an empty list rather than throwing, so a
// history-fetch problem never blocks the chat from opening with a fresh
// greeting instead.
export async function fetchNavigationHistory(
  accessToken: any,
  userId: string,
  tenantId: string,
  limit = 20
): Promise<HistoryTurn[]> {
  if (!userId || !tenantId) return [];
  const url = `${EDEN_BASE_URL}/api/v1/agent/history?userId=${encodeURIComponent(
    userId
  )}&tenantId=${encodeURIComponent(tenantId)}&limit=${limit}`;
  const res = await executeGetDataWithHeaders<{ turns: HistoryTurn[] }>(
    url,
    accessToken
  );
  if (!res.ok || !res.data?.turns) return [];
  return res.data.turns;
}

// Clears server-side conversation state (pending "which project?" goal,
// last-discussed-project memory) for this tenant/user — see
// source/api/navigation_api.py's POST /reset. Best-effort: the caller's
// local reset already happened by the time this runs, so a failure here
// just means that state expires on its own TTL (5-10 min) instead of
// immediately — never worth blocking or erroring the reset button over.
export async function resetNavigationSession(
  accessToken: any,
  userId: string,
  tenantId: string
): Promise<void> {
  if (!userId || !tenantId) return;
  try {
    await executePostDataWithHeaders(
      `${EDEN_BASE_URL}/api/v1/agent/reset`,
      { userId, tenantId },
      accessToken
    );
  } catch (e) {
    console.error("EDEN chat: server-side reset failed (will expire on its own TTL)", e);
  }
}

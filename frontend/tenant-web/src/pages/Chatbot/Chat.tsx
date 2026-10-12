/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 01-Dec-2024
* @author      : Ananth Kumar L
* @since       : 0.0.1
* Requirement# :
* Purpose      : For Chat UI (EDEN Navigation Agent)
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Nav POC   | 2026-07-06| Claude    | Replaced old chat API with EDEN
*                                          /api/v1/agent/navigate integration.
*                                          Chat history/reset are client-side
*                                          only — EDEN is stateless.
* EDEN Aurora UI | 2026-09-10| FIELDCRAFT Web    | Aurora Glass presentation behind
*                                          FEATURE_FLAGS.ENABLE_EDEN_AURORA_UI. Logic,
*                                          API calls, cache keys and reset semantics
*                                          unchanged; legacy render kept as
*                                          renderLegacy() for one release.
*/
import React, { useState, useEffect, useRef, useContext } from "react";
import Card from "@mui/material/Card";
import CardHeader from "@mui/material/CardHeader";
import CardContent from "@mui/material/CardContent";
import CardActions from "@mui/material/CardActions";
import "./Chat.scss";
import { Box, Fab, Stack, TextField, IconButton } from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import { useNavigate } from "react-router-dom";
import { useOidcAccessToken } from "@axa-fr/react-oidc";
import { MessagesInfo, ChatOptions } from "../../viewmodel/Chat";
import { defaultMessage } from "../../data/ChatQuestions";
import {
  UserContext,
  tenantSession,
  getTenantSessionDetails,
} from "../../hooks/UserSession";
import chatlogo from "../../assets/chat.png";
import RestartAltIcon from '@mui/icons-material/RestartAlt'
import CloseIcon from '@mui/icons-material/Close'
import {
  sendNavigationRequest,
  fetchNavigationHistory,
  resetNavigationSession,
  HistoryTurn,
  NavigationResponse,
  ProjectRef,
  SessionContext,
} from "../../services/AIChatService";
import { useSessionContext } from "../../hooks/useSessionContext";
import NavigationMessage from "./NavigationMessage";
import PageSpotlight, { SpotlightTarget } from "./PageSpotlight";
// ---- Aurora additions ----
import { FEATURE_FLAGS } from "../../constants/FeatureFlag";
import EdenAuroraShell, { EdenQuickPrompt } from "./EdenAuroraShell";
import EdenThinkingRow from "./EdenThinkingRow";
import EdenBoardingPass from "./EdenBoardingPass";
import EdenArrivalToast from "./EdenArrivalToast";
import EdenMessageBubble from "./EdenMessageBubble";
import { useEdenActivity, EDEN_ACTIVITY_DEFAULT } from "./useEdenActivity";
import { EDEN_TIMING, EdenOrbState } from "./edenAurora.tokens";

// Whether this response renders a NavigationChip and/or one or more
// OptionCards below the message text (disambiguation always does when it
// carries options; fallback does only when it carries its own options
// list; navigate always renders its chip; guidance renders its suggestion
// chip whenever one is present) — used to decide whether the message
// bubble needs to be forced to fill its max-width instead of the .message
// class's default shrink-to-fit sizing. Chips are non-wrapping
// (white-space: nowrap), so a fit-content bubble can size itself around
// the chip's own unbreakable width and clip the wrapped text above it
// instead of growing to the intended max-width — this must cover every
// response type that can render a chip/card, not just the ones the width
// fix was originally written for.
// See the width calculation where this is used, in the FIELDCRAFT bubble render.
function navigationHasOptionCards(nav: NavigationResponse | undefined): boolean {
  if (!nav) return false;
  // A disambiguation turn can be a bare yes/no-style question with no
  // clickable options at all (e.g. EDEN's "you were asking about X, now
  // on Y — continue with X, or check Y?" project-context-switch prompt) --
  // options is null there, not an empty array, so this must be as
  // null-safe as the fallback branch right below it.
  if (nav.type === "disambiguation") return (nav.options?.length ?? 0) > 0;
  if (nav.type === "fallback") return (nav.options?.length ?? 0) > 0;
  if (nav.type === "navigate") return true;
  if (nav.type === "guidance") return !!(nav as any).suggestion;
  return false;
}

interface Props {
  userResponse: string;
  botResponse: {
    purpose: string;
    message: string;
    options?: string[];
    sender: string;
  };
  sendUserResponse: string;
  options: ChatOptions[];
  optionClick: (opt: ChatOptions) => void;
  // Fired after EDEN navigates to a page — lets the parent collapse the chat
  // into a small non-intrusive "here you are" overlay so it doesn't block the
  // page the user just landed on.
  onNavigated?: (info: { label: string; insight?: string }) => void;
  // Fired when the user dismisses the chat via its own header close button.
  // Optional so existing callers (e.g. FloatChatbot.tsx) that have no close
  // affordance of their own keep rendering with no close button at all.
  onClose?: () => void;
}

// Exported (Aurora addition) so EdenMessageBubble can type its `message` prop
// against the real shape instead of a re-declared duplicate.
export type ChatMessage = MessagesInfo & { navigation?: NavigationResponse };

// Local, per-browser-tab cache so closing/reopening the widget or reloading
// the page shows the same transcript instantly, with no network round trip.
// sessionStorage (not localStorage) deliberately — "recent session" scope,
// cleared when the tab actually closes, matching how EDEN's own backend
// history TTLs its live-follow-up window. The backend's GET /history is the
// durable, cross-device source of truth; this is purely a fast local mirror
// of it, reconciled on every mount.
const HISTORY_CACHE_PREFIX = "eden_chat_cache";
const RESET_MARKER_PREFIX = "eden_chat_reset_at";

const cacheKey = (tenantId: string, userId: string) =>
  `${HISTORY_CACHE_PREFIX}:${tenantId}:${userId}`;

const resetMarkerKey = (tenantId: string, userId: string) =>
  `${RESET_MARKER_PREFIX}:${tenantId}:${userId}`;

const loadCachedMessages = (tenantId: string, userId: string): ChatMessage[] => {
  try {
    const raw = sessionStorage.getItem(cacheKey(tenantId, userId));
    const parsed = raw ? (JSON.parse(raw) as ChatMessage[]) : [];
    // Drop malformed/empty entries (e.g. cached by an earlier buggy build) —
    // an all-empty cache must not shadow the greeting.
    return Array.isArray(parsed)
      ? parsed.filter((m) => m && (m.message?.trim() || m.navigation))
      : [];
  } catch {
    return [];
  }
};

const saveCachedMessages = (
  tenantId: string,
  userId: string,
  messages: ChatMessage[]
) => {
  try {
    sessionStorage.setItem(cacheKey(tenantId, userId), JSON.stringify(messages));
  } catch {
    // sessionStorage full/unavailable (private browsing, quota) — non-fatal,
    // the chat still works, it just won't survive a reload.
  }
};

const getResetMarker = (tenantId: string, userId: string): string | null => {
  try {
    return sessionStorage.getItem(resetMarkerKey(tenantId, userId));
  } catch {
    return null;
  }
};

const setResetMarker = (tenantId: string, userId: string) => {
  try {
    sessionStorage.setItem(resetMarkerKey(tenantId, userId), new Date().toISOString());
  } catch {
    // non-fatal — see loadCachedMessages
  }
};

const formatHistoryTimestamp = (iso?: string | null): string => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const hours = d.getHours();
  const minutes = d.getMinutes();
  const period = hours >= 12 ? "PM" : "AM";
  const formattedHours = hours % 12 === 0 ? 12 : hours % 12;
  const formattedMinutes = minutes < 10 ? `0${minutes}` : minutes;
  return `${formattedHours}:${formattedMinutes} ${period}`;
};

// Backend history stores one record per turn (the user's utterance +
// EDEN's full response) — expand each into the same {You, FIELDCRAFT} message pair
// shape the live chat already renders, so historical and live bubbles use
// identical rendering (NavigationMessage) with no special-casing.
const historyTurnsToMessages = (turns: HistoryTurn[]): ChatMessage[] => {
  const messages: ChatMessage[] = [];
  for (const turn of turns) {
    const ts = formatHistoryTimestamp(turn.created_at);
    messages.push({ message: turn.utterance, sender: "You", timestamp: ts });
    const navigation = turn.response as NavigationResponse;
    const text =
      navigation && "message" in navigation
        ? (navigation as any).message
        : navigation && "question" in navigation
        ? (navigation as any).question
        : "";
    messages.push({ message: text, sender: "FIELDCRAFT", timestamp: ts, navigation });
  }
  return messages;
};

// Literally the user typing that sentence -- routed through the existing
// setNextStep, so there is no new request shape and no new API surface.
const QUICK_PROMPTS: EdenQuickPrompt[] = [
  { text: "Where are my unpaid invoices?" },
  { text: "I need to add a new material", label: "Add a new material" },
  { text: "Why did my bid cost change?" },
];

const ChatCard: React.FC<Props> = (props) => {
  const { userSessionDetails } = useContext(UserContext);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const { accessTokenPayload } = useOidcAccessToken();
  const [userResponse, setUserResponse] = useState<string>("");
  const [isSending, setIsSending] = useState(false);
  const { accessToken } = useOidcAccessToken();
  const endRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const [isResetting, setIsResetting] = useState(false);
  const [spotlight, setSpotlight] = useState<SpotlightTarget | null>(null);
  const navigate = useNavigate();
  const getSessionContext = useSessionContext();
  const identityRef = useRef<{ userId: string; tenantId: string } | null>(null);

  // ---- Aurora presentation state (no behavioural effect) ----------------
  // Marks how many messages existed the moment the greeting/hydration/reset
  // last settled -- an index at or past this is a message THIS mount pushed
  // live (setNextStep), not one loaded from sessionStorage cache or backend
  // history. Only that live message should ever animate (see `animate` at
  // the EdenMessageBubble render below).
  //
  // Real bug, found 2026-09-10: `animate` was computed purely as
  // `index === messages.length - 1`, which is also true for the LAST message
  // of a freshly hydrated transcript. Reopening the chat hydrates the cached
  // conversation wholesale (swapInIfUntouched) -- if that cache's last turn
  // happened to be a `navigate` response, its now-"last" bubble looked live,
  // so EdenBoardingPass mounted, ran its progress timers, and called
  // navigate() again on its own, silently re-redirecting the user to
  // wherever Eden had sent them last time, every time they reopened the
  // panel. Gating on this baseline instead of pure position fixes it at the
  // source, and also stops hydrated history from re-streaming word-by-word.
  const liveMessageBaselineRef = useRef(0);
  const [activity, setActivity] = useState<string>(EDEN_ACTIVITY_DEFAULT);
  const [orbState, setOrbState] = useState<EdenOrbState>("idle");
  const [panelFolded, setPanelFolded] = useState(false);
  const [arrival, setArrival] = useState<{ label: string } | null>(null);
  const resolveActivity = useEdenActivity();
  const sessionContext = getSessionContext();

  // Every Aurora timer, so a remount or a new turn can clear the lot. An
  // orphaned fold timer would leave the panel invisible with no way back
  // short of a reload.
  const auroraTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const clearAuroraTimers = () => {
    auroraTimersRef.current.forEach(clearTimeout);
    auroraTimersRef.current = [];
  };
  useEffect(() => clearAuroraTimers, []);

  // -------- Helpers (immutable message pushes) ----------
  const getTimeStamp = (): string => {
    const now = new Date();
    const hours = now.getHours();
    const minutes = now.getMinutes();
    const period = hours >= 12 ? 'PM' : 'AM';
    const formattedHours = hours % 12 === 0 ? 12 : hours % 12;
    const formattedMinutes = minutes < 10 ? `0${minutes}` : minutes;
    return `${formattedHours}:${formattedMinutes} ${period}`;
  };

  const pushUserMessage = (msg: string) => {
    setMessages((prev) => [
      ...prev,
      { message: msg, sender: "You", timestamp: getTimeStamp() },
    ]);
  };

  const pushBotMessage = (msg: string, navigation?: NavigationResponse) => {
    setMessages((prev) => [
      ...prev,
      { message: msg, sender: "FIELDCRAFT", timestamp: getTimeStamp(), navigation },
    ]);
  };

  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const resolveUserUid = (): string => {
    const u = (accessTokenPayload as any)?.oid;
    return Array.isArray(u) ? String(u[0] ?? "") : String(u ?? "");
  };

  const resolveTenantId = async (): Promise<string> => {
    const tenant = tenantSession ?? (await getTenantSessionDetails(accessToken));
    return String(tenant?.tenantid ?? "");
  };

  // -------- Initial greeting (synchronous, never blocked on network) ----------
  // This is the original pre-history behavior, kept verbatim as the safety
  // net: the greeting appears the moment userSessionDetails is available,
  // regardless of what the async hydration below does or whether it fails.
  useEffect(() => {
    if (messages.length > 0 || !userSessionDetails) return;
    liveMessageBaselineRef.current = 1;
    setMessages([defaultMessage(userSessionDetails.firstname)]);
  }, [userSessionDetails]);

  // -------- Hydrate transcript (cache + backend history, purely additive) ----------
  // Runs alongside the greeting effect above, never instead of it. When it
  // finds prior turns (sessionStorage cache, then the backend's durable
  // GET /agent/history), it swaps them in — but only via a functional update
  // that checks the transcript is still untouched (nothing but the greeting),
  // so it can never clobber a conversation the user has already started, and
  // a hydration failure can never suppress the greeting. StrictMode-safe by
  // construction: running twice is harmless (idempotent reads), so there is
  // deliberately no "ran once" ref guarding the effect itself.
  useEffect(() => {
    if (!userSessionDetails) return;

    let cancelled = false;

    // Replace the transcript only if it's still just the greeting (or empty).
    const swapInIfUntouched = (hydrated: ChatMessage[]) => {
      setMessages((prev) => {
        if (prev.length > 1) return prev;
        // Only actually mutated when the swap applies -- see
        // liveMessageBaselineRef's own doc comment above its declaration.
        liveMessageBaselineRef.current = hydrated.length;
        return hydrated;
      });
    };

    const hydrate = async () => {
      let userId = "";
      let tenantId = "";
      try {
        userId = resolveUserUid();
        tenantId = await resolveTenantId();
      } catch (e) {
        console.error("EDEN chat: identity resolution failed", e);
        return; // greeting effect above already covers the UI
      }
      if (cancelled || !userId || !tenantId) return;
      identityRef.current = { userId, tenantId };

      const greeting = defaultMessage(userSessionDetails.firstname);

      const cached = loadCachedMessages(tenantId, userId);
      if (cached.length > 0) {
        swapInIfUntouched(cached);
      }

      try {
        const resetAt = getResetMarker(tenantId, userId);
        const turns = await fetchNavigationHistory(accessToken, userId, tenantId, 20);
        if (cancelled) return;
        const filtered = resetAt
          ? turns.filter((t) => !t.created_at || t.created_at > resetAt)
          : turns;
        if (filtered.length > 0) {
          // Greeting stays on top so the welcome message is always visible.
          const fromBackend = [greeting, ...historyTurnsToMessages(filtered)];
          swapInIfUntouched(fromBackend);
          saveCachedMessages(tenantId, userId, fromBackend);
        }
      } catch (e) {
        // Backend unreachable — greeting/cache already shown, nothing to do.
        console.error("EDEN chat: history hydration failed", e);
      }
    };

    hydrate();
    return () => {
      cancelled = true;
    };
  }, [userSessionDetails]);

  // -------- Persist transcript locally on every change ----------
  useEffect(() => {
    const identity = identityRef.current;
    if (!identity || messages.length === 0) return;
    saveCachedMessages(identity.tenantId, identity.userId, messages);
  }, [messages]);

  const handleNavigate = (
    route: string,
    project?: ProjectRef,
    routeState?: Record<string, any>
  ) => {
    if (project || routeState) {
      navigate(route, { state: { ...(project ? { project } : {}), ...routeState } });
    } else {
      navigate(route);
    }
  };

  const botTextFor = (response: NavigationResponse): string => {
    switch (response.type) {
      case "disambiguation":
        return response.question;
      case "navigate":
      case "fallback":
      case "guidance":
      default:
        return response.message;
    }
  };

  const setNextStep = async (userResp: string) => {
    if (!userResp?.trim() || isSending || isResetting) return;

    pushUserMessage(userResp);
    setUserResponse("");
    setIsSending(true);

    // (a) name the wait -- see useEdenActivity for why this is client-derived.
    setActivity(resolveActivity(userResp, sessionContext.currentEntity));
    setOrbState("thinking");

    try {
      const userUid = resolveUserUid();
      if (!userUid) throw new Error("User ID (oid) not available");
      const tenantId = await resolveTenantId();
      if (!tenantId) throw new Error("Tenant ID not available");

      const context: SessionContext = {
        utterance: userResp,
        ...getSessionContext(),
        userId: userUid,
        tenantId,
      };

      const response = await sendNavigationRequest(accessToken, context);
      pushBotMessage(botTextFor(response), response);

      // (b) the avatar reports success. Cosmetic only.
      setOrbState("found");
      auroraTimersRef.current.push(
        setTimeout(() => setOrbState("idle"), 1400)
      );

      // Spotlight a live element the backend pointed at. Two shapes:
      //  - "where is X" feature lookups set `highlight` (a caption label) AND
      //    `highlightTargets` (DOM text to find).
      //  - reporting data answers (start date, invoice amount, invoice no,
      //    address) set ONLY `highlightTargets` — the value to locate — with
      //    the explanation in `insight`, no separate caption label.
      // Real bug, fixed: this used to trigger on `highlight` alone, so every
      // data answer (which sends targets but no label) silently never
      // spotlighted anything — the whole reason start-date / invoice-amount
      // highlighting appeared dead. Trigger on EITHER field now, and fall
      // back to the value itself as the caption when there's no label.
      const highlightLabel = (response as any).highlight as string | undefined;
      const highlightTargets = (response as any).highlightTargets as
        | string[]
        | undefined;
      if (highlightLabel || (highlightTargets && highlightTargets.length > 0)) {
        setSpotlight({
          label: highlightLabel ?? highlightTargets![0],
          targets: highlightTargets ?? [highlightLabel as string],
          hint: (response as any).insight,
          // Only true for a genuine action request the backend chose to
          // navigate straight to (see NavigationResponse.autoClick's own
          // docstring, fieldcraft-ai-services) -- never for a "where is X"
          // lookup or a plain data-answer highlight.
          autoClick: (response as any).autoClick === true,
          // Second-stage target inside whatever autoClick just opened
          // (e.g. a specific column checkbox inside the Material Fields
          // dropdown) -- see NavigationResponse.postClickHighlightTargets.
          postClickTargets: (response as any).postClickHighlightTargets as
            | string[]
            | undefined,
        });
      } else {
        // New answer with nothing to point at — clear any spotlight still
        // showing from a previous turn so a stale ring doesn't linger.
        setSpotlight(null);
      }

      if (response.type === "navigate") {
        // The agent said "Taking you to X" — actually go there. Short delay so
        // the user reads the confirmation before the page changes. Then hand
        // off to the parent, which collapses the chat into an overlay toast so
        // it doesn't cover the page they just navigated to.
        //
        // (c) Under Aurora, this bare timer is REPLACED by EdenBoardingPass's
        // onArrive (handleBoardingArrive below) -- same handleNavigate call,
        // same props.onNavigated payload, just triggered by the boarding
        // pass's progress finishing rather than an opaque setTimeout, so the
        // wait is explained instead of silent. Under the legacy flag this
        // original timer still runs exactly as before.
        if (!FEATURE_FLAGS.ENABLE_EDEN_AURORA_UI) {
          setTimeout(() => {
            handleNavigate(response.route, response.project, response.routeState);
            props.onNavigated?.({
              label: response.label,
              insight: response.insight,
            });
          }, 800);
        }
      }
    } catch (e: any) {
      console.error("Chat error:", e);
      pushBotMessage(
        "Sorry — I couldn’t reach the assistant. Please try again."
      );
      setOrbState("idle");
    } finally {
      setIsSending(false);
    }
  };

  // Called by EdenBoardingPass when its progress bar completes. This is the
  // ONLY navigation path in the Aurora branch, and it performs exactly what
  // the legacy 800ms timer performed.
  const handleBoardingArrive = (
    route: string,
    label: string,
    insight?: string,
    project?: ProjectRef,
    routeState?: Record<string, any>
  ) => {
    handleNavigate(route, project, routeState);
    props.onNavigated?.({ label, insight });

    // Fold, toast, then RETURN. The panel must never be left invisible -- a
    // chat that vanishes after every redirect is unreachable without
    // reopening it.
    setPanelFolded(true);
    auroraTimersRef.current.push(
      setTimeout(() => setArrival({ label }), EDEN_TIMING.TOAST_LINGER_MS)
    );
    auroraTimersRef.current.push(
      setTimeout(() => setPanelFolded(false), EDEN_TIMING.TOAST_LINGER_MS * 2)
    );
  };

  // -------- Auto scroll ----------
  const scrollToBottom = (smooth = true) => {
    const el = scrollAreaRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  };

  useEffect(() => {
    if (isResetting) return;
    scrollToBottom(true);
  }, [messages.length, isSending]);

  // Stick to the bottom only when the user is already near it -- otherwise a
  // streaming Aurora answer would yank the view away from history they
  // scrolled up to read. Aurora-only: the legacy panel has no streaming, so
  // the effect above already covers it completely.
  const STICK_THRESHOLD_PX = 96;
  const isNearBottom = () => {
    const el = scrollAreaRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
  };

  useEffect(() => {
    if (!FEATURE_FLAGS.ENABLE_EDEN_AURORA_UI) return;
    const el = scrollAreaRef.current;
    if (!el || typeof MutationObserver === "undefined") return;
    // Streaming grows the LAST bubble's text on every word tick without
    // changing messages.length and without changing el's own box size (it is
    // flex:1 inside a fixed-height panel) -- so neither the [messages.length,
    // isSending] effect above nor a ResizeObserver on `el` itself would ever
    // fire mid-stream. A MutationObserver on the subtree catches every text
    // node change the streaming caret produces.
    const mo = new MutationObserver(() => {
      if (isNearBottom()) scrollToBottom(false);
    });
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    return () => mo.disconnect();
  }, []);

  // Clears the local cache and stamps a reset marker (identityRef.current)
  // so the next hydration filters out anything the backend's durable
  // history still has from before this point, instead of silently
  // resurrecting "cleared" history on the next reload. ALSO clears EDEN's
  // real server-side conversation state (pending "which project?" goal,
  // last-discussed-project memory) via resetNavigationSession -- these are
  // newer additions with their own short TTLs (5-10 min), so without this
  // call a reset followed immediately by a new question could still
  // silently inherit stale state from before the reset.
  const handleReset = async () => {
    setIsResetting(true);
    try {
      const identity = identityRef.current;
      if (identity) {
        setResetMarker(identity.tenantId, identity.userId);
        saveCachedMessages(identity.tenantId, identity.userId, []);
        // Fire-and-forget: resetNavigationSession already swallows its own
        // errors, and the visible reset shouldn't wait on (or fail because
        // of) this network call.
        resetNavigationSession(accessToken, identity.userId, identity.tenantId);
      }
      liveMessageBaselineRef.current = 1;
      setMessages([defaultMessage(userSessionDetails?.firstname)]);
      // tiny delay so the animation is visible but snappy
      await new Promise(r => setTimeout(r, 450));
    } finally {
      setIsResetting(false);
      scrollToBottom(false);
    }
  };

  // -------- Legacy render (unchanged JSX, moved verbatim into a function) ----------
  const renderLegacy = () => (
   <>
    <Card
      className="chatcard-container"
      sx={{
        position: "fixed",
        bottom: 15,
        right: 95,
        width: 450,
        height: 680,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        zIndex: 1050,
        borderRadius: 3
      }}
    >
      <CardHeader
        className="chat-header"
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 3,
          background: "primary.main",
          color: "white",
          py: 1,
        }}
        style={{ display: "flex", alignItems: "center" }}
        avatar={
          <Box component="img" src={chatlogo} sx={{ width: "30px", height: "30px" }} />
        }
        title="EDEN"
        titleTypographyProps={{ variant: "h3", color: "white" }}
        action={
          <>
            <IconButton
              color="inherit"
              disabled={isResetting}
      sx={{
        backgroundColor: "white",
        "&:hover": { backgroundColor: "#f0f0f0" },
        mr: 1.5,
        mt: 2.0,
        maxWidth: 450,
      }}
      onClick={handleReset}
      aria-label="Reset chat"
      title={isResetting ? "Resetting…" : "Reset chat"}
    >
              <RestartAltIcon sx={{ color: "#333" }} />
            </IconButton>
            {props.onClose && (
              <IconButton
                color="inherit"
                sx={{
                  backgroundColor: "white",
                  "&:hover": { backgroundColor: "#f0f0f0" },
                  mr: 1.5,
                  mt: 2.0,
                  maxWidth: 450,
                }}
                onClick={() => props.onClose?.()}
                aria-label="Close chat"
                title="Close chat"
              >
                <CloseIcon sx={{ color: "#333" }} />
              </IconButton>
            )}
          </>
        }
      />

      <CardContent
        className="chat-msg-container"
        sx={{
          position: "relative",
          p: 0,
          display: "flex",
          flexDirection: "column",
          height: "100%",
          minHeight: 0,
        }}
      >
        <Box
          ref={scrollAreaRef}
          sx={{
            flex: 1,
            overflowY: isResetting ? "hidden" : "auto",
            p: 2,
            position: "relative",
            minHeight: 0,
          }}
        >
          {messages.map((chat, index) => (
            <React.Fragment key={index}>
              {chat.sender === "FIELDCRAFT" ? (
                <div>
                  <span style={{ fontSize: "14px", color: "grey", marginRight: "auto", marginLeft: "6px" }}>
                    {chat.sender}
                  </span>
                  <div
                    className={`message ${chat.sender}`}
                    style={{
                      padding: "12px 16px",
                      whiteSpace: "normal",
                      wordBreak: "break-word",
                      backgroundColor: "primary.main",
                      color: "white",
                      borderRadius: "12px",
                      maxWidth: "80%",
                      // The .message class (Chat.scss) sets width: fit-content
                      // so a short text bubble stays compact — but that
                      // shrink-to-fit sizing conflicts with option cards
                      // inside asking to be 100% of this same bubble: the
                      // bubble sizes to the SHORT question text ("A few
                      // pages match — which did you mean?"), while the
                      // cards' own natural width can render wider than
                      // that, visibly poking out past the bubble's edge.
                      // Force the bubble to actually fill its max-width
                      // whenever it holds option cards, so cards can never
                      // be wider than the container they're "100%" of.
                      width: navigationHasOptionCards(chat.navigation) ? "80%" : undefined,
                      // Belt-and-suspenders on top of the width fix above:
                      // explicit border-box (matches .message's own rule,
                      // but inline for certainty regardless of cascade
                      // order) and a hard horizontal clip, so nothing
                      // inside — cards, long labels, anything — can ever
                      // visually render past the bubble's own edge, even if
                      // some nested flex/text sizing pushes wider than its
                      // 100% intent.
                      boxSizing: "border-box",
                      overflowX: "hidden",
                      marginLeft: "6px",
                      marginTop: "8px",
                    }}
                  >
                    {chat.navigation ? (
                      <NavigationMessage
                        response={chat.navigation}
                        onNavigate={handleNavigate}
                        onContinueChat={setNextStep}
                      />
                    ) : (
                      chat.message
                    )}
                  </div>
                  <span className="timestamp" style={{ fontSize: "11px", color: "grey", marginRight: "auto", marginLeft: "12px" }}>
                    {chat.timestamp}
                  </span>
                </div>
              ) : (
                <div>
                  <Stack direction="column" justifyContent="flex-end" alignItems="flex-end" spacing={2}>
                    <div
                      className={`message ${chat.sender}`}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        padding: "12px 16px",
                        whiteSpace: "normal",
                        wordBreak: "break-word",
                        backgroundColor: "#E0E0E0",
                        color: "black",
                        borderRadius: "12px",
                        maxWidth: "80%",
                        marginRight: "6px",
                        marginTop: "8px",
                      }}
                    >
                      {chat.message}
                    </div>
                    <span className="timestamp" style={{ fontSize: "11px", color: "grey", marginTop: "0px", marginRight: "12px" }}>
                      {chat.timestamp}&#10004;&#10004;
                    </span>
                  </Stack>
                </div>
              )}
            </React.Fragment>
          ))}

          {isSending && (
            <div className="typing-row" aria-live="polite" aria-label="FIELDCRAFT is typing">
              <span className="typing-label">FIELDCRAFT</span>
              <div className="message FIELDCRAFT typing">
                <span className="thinking-bubble">
                  <span>EDEN is thinking</span>
                  <span className="typing-dots" aria-hidden="true">
                    <span className="dot" /><span className="dot" /><span className="dot" />
                  </span>
                </span>
              </div>
            </div>
          )}

          <div ref={endRef} />
        </Box>

        {isResetting && (
          <div
            className="overlay-portal"
            role="status"
            aria-live="polite"
            aria-label="Clearing the chat history"
          >
            <div className="overlay-center">
              <div className="reset-anim" aria-hidden="true">
                <div className="reset-ring" />
                <div className="reset-arrow">↻</div>
              </div>

              <p className="caption lg">
                Clearing the chat history
                <span className="ellipsis"><i /><i /><i /></span>
              </p>
            </div>
          </div>
        )}
      </CardContent>

      <CardActions
        sx={{
          flexShrink: 0,
          backgroundColor: "#fff",
          borderTop: "1px solid rgba(0,0,0,.08)",
          py: 1,
          px: 1,
        }}
      >
        <Box sx={{ display: "flex", gap: 1, width: "100%", alignItems: "center" }}>
          <TextField id="chat-textfield-type-a-message"
            inputRef={inputRef}
            fullWidth
            size="small"
            placeholder="Where do you want to go?"
            value={userResponse}
            onChange={(e) => setUserResponse(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!isSending && !isResetting && userResponse.trim()) {
                  setNextStep(userResponse);
                }
              }
            }}
          />
          <Fab
            color="primary"
            aria-label="send"
            disabled={isSending || isResetting || !userResponse.trim()}
            onClick={() => setNextStep(userResponse)}
            sx={{ width: 44, height: 44, minHeight: 44, position: "static" }}
          >
            <SendIcon />
          </Fab>
        </Box>
      </CardActions>
    </Card>

    <PageSpotlight target={spotlight} onDismiss={() => setSpotlight(null)} />
   </>
  );

  // -------- Render ----------
  if (!FEATURE_FLAGS.ENABLE_EDEN_AURORA_UI) return renderLegacy();

  const showQuickPrompts = messages.length <= 1 && !isSending && !isResetting;

  return (
    <>
      <Box
        className="chatPopup-aurora"
        sx={{ display: "flex", flexDirection: "column", gap: "14px", alignItems: "flex-end" }}
      >
        <EdenAuroraShell
          orbState={orbState}
          contextEntity={sessionContext.currentEntity?.name}
          scrollAreaRef={scrollAreaRef}
          quickPrompts={showQuickPrompts ? QUICK_PROMPTS : undefined}
          onQuickPrompt={setNextStep}
          inputRef={inputRef}
          value={userResponse}
          onChange={setUserResponse}
          onSend={() => setNextStep(userResponse)}
          sendDisabled={isSending || isResetting || !userResponse.trim()}
          onReset={handleReset}
          onClose={props.onClose}
          isResetting={isResetting}
          folded={panelFolded}
        >
          {messages.map((chat, index) => (
            <EdenMessageBubble
              key={index}
              id={String(index)}
              message={chat}
              // Staging/streaming (and, critically, the auto-navigating
              // boarding pass -- see EdenMessageBubble) apply to the LIVE
              // turn only. `index === messages.length - 1` alone is NOT
              // enough: it's also true for the last message of a freshly
              // hydrated transcript, which is what silently re-navigated the
              // user every time they reopened the chat (found 2026-09-10).
              // liveMessageBaselineRef is the fix -- see its own doc comment.
              animate={
                index === messages.length - 1 &&
                index >= liveMessageBaselineRef.current &&
                !isResetting
              }
              onNavigate={handleNavigate}
              onContinueChat={setNextStep}
              renderBoardingPass={(nav, live) => (
                <EdenBoardingPass
                  label={nav.label}
                  carriesContext={Boolean(nav.project || nav.routeState)}
                  live={live}
                  onArrive={() =>
                    handleBoardingArrive(nav.route, nav.label, nav.insight, nav.project, nav.routeState)
                  }
                />
              )}
            />
          ))}

          {isSending && <EdenThinkingRow label={activity} />}

          <div ref={endRef} />
        </EdenAuroraShell>

        {arrival && (
          <EdenArrivalToast
            label={arrival.label}
            onUndo={() => navigate(-1)}
            onDismiss={() => setArrival(null)}
          />
        )}
      </Box>

      <PageSpotlight target={spotlight} onDismiss={() => setSpotlight(null)} />
    </>
  );
};

export default ChatCard;

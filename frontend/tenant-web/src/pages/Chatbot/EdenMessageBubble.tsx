/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Renders one ChatMessage as Aurora bubble(s). Not part of the design
*                handoff's src_reference/ -- deliberately left for this branch (see
*                Chat.aurora.patch.md §8), because it has to match this repo's real
*                MessagesInfo/ChatMessage/NavigationResponse shapes exactly.
*
*                A "You" message is one static bubble -- the user just typed it, there
*                is nothing to stream. A "FIELDCRAFT" message is split by useStagedReply into
*                up to 3 bubbles; the LAST one carries chat.navigation's interactive
*                content (option cards / chip / boarding pass), never an earlier one,
*                so a card can never appear to answer a question that has not
*                finished being asked.
*
*                NavigationMessage is reused as-is for the interactive content (it
*                stays the single source of truth for chips/cards -- README/
*                IMPLEMENTATION are explicit that NavigationChip/OptionCard/
*                OptionCardList keep every prop and testid), but its own copy of
*                response.message/.question is suppressed via the new hideMessage
*                prop: this component already renders that exact text itself, word by
*                word, via useStagedReply -- rendering it twice would show every
*                answer once instantly (inside NavigationMessage) and once streamed.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { Box, Typography } from "@mui/material";
import { ChatMessage } from "./Chat";
import { NavigationResponse } from "../../services/AIChatService";
import NavigationMessage, {
  NavigateHandler,
  ContinueChatHandler,
} from "./NavigationMessage";
import useStagedReply from "./useStagedReply";
import {
  EDEN_ALPHA,
  EDEN_BUBBLE_RADIUS,
  EDEN_COLORS,
  EDEN_EASING,
  EDEN_GRADIENTS,
  EDEN_SHADOWS,
} from "./edenAurora.tokens";

type NavigateResponse = Extract<NavigationResponse, { type: "navigate" }>;

// Mirrors Chat.tsx's navigationHasOptionCards -- duplicated rather than imported
// because Chat.tsx does not export it (it is a private render-time helper there,
// and importing a non-exported symbol across files is not possible). Keep this in
// sync with that function if the NavigationResponse union changes shape.
function navigationHasOptionCards(nav: NavigationResponse | undefined): boolean {
  if (!nav) return false;
  if (nav.type === "disambiguation") return (nav.options?.length ?? 0) > 0;
  if (nav.type === "fallback") return (nav.options?.length ?? 0) > 0;
  if (nav.type === "navigate") return true;
  if (nav.type === "guidance") return !!(nav as any).suggestion;
  return false;
}

interface EdenMessageBubbleProps {
  message: ChatMessage;
  /** Stable identity for useStagedReply -- Chat.tsx passes String(index). */
  id: string;
  /**
   * False for hydrated history and for every already-rendered turn: only the
   * live, just-arrived turn should stream. Replaying twenty old answers word
   * by word on open would be absurd (README §5, "Staged, streaming reply").
   */
  animate: boolean;
  onNavigate: NavigateHandler;
  onContinueChat: ContinueChatHandler;
  /**
   * Used in place of NavigationChip for `type === "navigate"`, both live and
   * historical -- `live` (mirrors this component's own `animate`) tells the
   * caller which EdenBoardingPass variant to render: the auto-progressing
   * live card, or the static, non-navigating "Arrived" record. See
   * EdenBoardingPass's own `live` prop doc for why this distinction exists.
   */
  renderBoardingPass: (nav: NavigateResponse, live: boolean) => React.ReactNode;
}

const EdenMessageBubble: React.FC<EdenMessageBubbleProps> = ({
  message,
  id,
  animate,
  onNavigate,
  onContinueChat,
  renderBoardingPass,
}) => {
  const { segments, finishNow } = useStagedReply(message.message, id, animate);

  // README §5 / IMPLEMENTATION §5: streaming is cosmetic -- a click reaching
  // onNavigate/onContinueChat mid-stream must complete the render immediately
  // and then act, never wait for the caret to catch up.
  const handleNavigate: NavigateHandler = (...args) => {
    finishNow();
    onNavigate(...args);
  };
  const handleContinueChat: ContinueChatHandler = (text) => {
    finishNow();
    onContinueChat(text);
  };

  if (message.sender === "You") {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
        <Box
          data-testid="eden-user-bubble"
          sx={{
            maxWidth: "80%",
            padding: "11px 15px",
            borderRadius: EDEN_BUBBLE_RADIUS.user,
            background: EDEN_GRADIENTS.userBubble,
            color: "#fff",
            boxShadow: EDEN_SHADOWS.userBubble,
            boxSizing: "border-box",
            overflowX: "hidden",
            animation: `edenBubbleIn 500ms ${EDEN_EASING.spring} both`,
          }}
        >
          <Typography
            component="span"
            sx={{ font: "400 13.5px/1.55 Poppins, system-ui, sans-serif", textWrap: "pretty" }}
          >
            {message.message}
          </Typography>
        </Box>
        {message.timestamp && (
          <Typography
            component="span"
            sx={{
              font: "400 10px/1 Poppins, system-ui, sans-serif",
              letterSpacing: ".02em",
              color: EDEN_COLORS.fg3,
              marginTop: "5px",
              marginBottom: "12px",
              marginRight: "2px",
            }}
          >
            {message.timestamp} · sent
          </Typography>
        )}
      </Box>
    );
  }

  // FIELDCRAFT (bot) message. Nothing to render yet if the text hasn't split into a
  // segment (e.g. an empty message with only `navigation` set) -- fall back to
  // a single synthetic segment so cards/chips still render somewhere.
  const hasCards = navigationHasOptionCards(message.navigation);
  const rows = segments.length > 0 ? segments : [
    { id: `${id}-0`, text: "", full: "", streaming: false, isLast: true },
  ];

  return (
    <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
      {rows.map((segment) => (
        <Box
          key={segment.id}
          data-testid="eden-bot-bubble"
          aria-busy={segment.streaming}
          sx={{
            width: segment.isLast && hasCards ? "100%" : undefined,
            maxWidth: "86%",
            padding: "12px 15px",
            borderRadius: EDEN_BUBBLE_RADIUS.eden,
            backgroundColor: EDEN_COLORS.surface1,
            border: `1px solid ${EDEN_ALPHA.glassBorder}`,
            boxShadow: EDEN_SHADOWS.botBubble,
            boxSizing: "border-box",
            overflowX: "hidden",
            marginTop: "8px",
            animation: `edenBubbleIn 500ms ${EDEN_EASING.spring} both`,
          }}
        >
          {segment.text && (
            <Typography
              component="span"
              sx={{
                font: "400 13.5px/1.55 Poppins, system-ui, sans-serif",
                color: EDEN_COLORS.fg1,
                textWrap: "pretty",
              }}
            >
              {segment.text}
              {segment.streaming && (
                <Box
                  component="span"
                  className="eden-caret"
                  aria-hidden="true"
                  sx={{
                    display: "inline-block",
                    width: "2px",
                    height: "14px",
                    marginLeft: "2px",
                    verticalAlign: "text-bottom",
                    backgroundColor: EDEN_COLORS.accent,
                    animation: "edenCaret 1s steps(1) infinite",
                  }}
                />
              )}
            </Typography>
          )}

          {/* Interactive content rides on the LAST segment only -- see file
              header. Rendered as soon as this bubble mounts, independent of
              whether ITS OWN text has finished streaming (the boarding pass
              and option cards must never wait on the cosmetic caret).

              Real bug, found 2026-09-10: EdenBoardingPass used to auto-fire
              its progress timers and call navigate() on MOUNT regardless of
              whether it was the live turn or a hydrated history replay --
              reopening the chat re-rendered the boarding pass for whatever
              "navigate" turn was last in the cache and silently re-navigated
              the user right back to that page. `animate` is exactly "this is
              the live, just-arrived turn" (see the prop's own doc, and how
              Chat.tsx sets it), so it is threaded through as `live` here --
              EdenBoardingPass itself now refuses to call onArrive at all
              when live=false, so a historical card can never trigger
              navigate(), not on mount and not on a later click. */}
          {segment.isLast && message.navigation && (
            message.navigation.type === "navigate" ? (
              renderBoardingPass(message.navigation, animate)
            ) : (
              <NavigationMessage
                response={message.navigation}
                onNavigate={handleNavigate}
                onContinueChat={handleContinueChat}
                hideMessage
              />
            )
          )}
        </Box>
      ))}

      {message.timestamp && (
        <Typography
          component="span"
          sx={{
            font: "400 10px/1 Poppins, system-ui, sans-serif",
            letterSpacing: ".02em",
            color: EDEN_COLORS.fg3,
            marginTop: "5px",
            marginBottom: "12px",
            marginLeft: "2px",
          }}
        >
          {message.timestamp}
        </Typography>
      )}
    </Box>
  );
};

export default EdenMessageBubble;

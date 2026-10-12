/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : The Aurora Glass panel chrome -- header, page-context line, scroll
*                area, quick prompts, composer and reset overlay -- as a purely
*                presentational shell.
*
*                It owns NO conversation state. Chat.tsx keeps every piece of that
*                (messages, hydration, sessionStorage cache, reset semantics,
*                sendNavigationRequest, navigate) and passes rendered children in.
*                That split is the whole point: the redesign must not be able to
*                change behaviour, and a shell that cannot see the transcript cannot
*                change what happens to it.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { Box, IconButton, TextField, Tooltip, Typography } from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import CloseIcon from "@mui/icons-material/Close";
import EdenAvatarOrb from "./EdenAvatarOrb";
import {
  EDEN_ALPHA,
  EDEN_COLORS,
  EDEN_EASING,
  EDEN_GRADIENTS,
  EDEN_ORB_STATUS,
  EDEN_PANEL,
  EDEN_RADIUS,
  EDEN_SHADOWS,
  EDEN_TIMING,
  EdenOrbState,
} from "./edenAurora.tokens";

export interface EdenQuickPrompt {
  /** Sent verbatim through the existing setNextStep(), exactly as if typed. */
  text: string;
  /** Shorter label for the chip, when the full utterance is too long. */
  label?: string;
}

interface EdenAuroraShellProps {
  orbState: EdenOrbState;
  /**
   * Entity name in context, e.g. "Buma P". The context strip shows only this
   * -- no raw route/path text anywhere in the panel (product decision,
   * 2026-09-10). Hide the strip by passing undefined.
   */
  contextEntity?: string;

  /** The transcript -- rendered bubbles, thinking row, boarding pass. */
  children: React.ReactNode;
  /** Ref for the scroll container. Chat.tsx's existing scrollAreaRef. */
  scrollAreaRef: React.RefObject<HTMLDivElement>;

  /** Shown only while the transcript is just the greeting and nothing is in flight. */
  quickPrompts?: EdenQuickPrompt[];
  onQuickPrompt?: (text: string) => void;

  inputRef: React.RefObject<HTMLInputElement>;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  sendDisabled: boolean;

  onReset: () => void;
  /** Rendered only when provided -- matches today's behaviour for FloatChatbot.tsx. */
  onClose?: () => void;
  isResetting: boolean;

  /** True while the panel is folded away after a redirect. */
  folded?: boolean;
}

const EdenAuroraShell: React.FC<EdenAuroraShellProps> = ({
  orbState,
  contextEntity,
  children,
  scrollAreaRef,
  quickPrompts,
  onQuickPrompt,
  inputRef,
  value,
  onChange,
  onSend,
  sendDisabled,
  onReset,
  onClose,
  isResetting,
  folded = false,
}) => {
  // Product decision, 2026-09-10: no raw route/path text anywhere in the Eden
  // panel -- see the boarding pass and arrival toast, which dropped their mono
  // route lines for the same reason. The header's own status line already
  // says "Reading this page", so this strip is only worth showing when it has
  // something the header doesn't: the entity (project) name.
  const showContext = Boolean(contextEntity);

  return (
    <Box
      className="eden-aurora"
      data-testid="eden-aurora-panel"
      sx={{
        position: "relative",
        width: EDEN_PANEL.width,
        height: EDEN_PANEL.height,
        borderRadius: `${EDEN_RADIUS.panel}px`,
        backgroundColor: EDEN_ALPHA.glassFill,
        // -webkit- prefix kept explicitly: Safari still needs it, and
        // .chat-msg-container's `isolation: isolate` is what stops the blur
        // sampling the whole page behind the panel.
        backdropFilter: "blur(20px) saturate(1.4)",
        WebkitBackdropFilter: "blur(20px) saturate(1.4)",
        border: `1px solid ${EDEN_ALPHA.glassBorder}`,
        boxShadow: EDEN_SHADOWS.panel,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        transform: folded ? "translateY(18px) scale(.9)" : "none",
        opacity: folded ? 0 : 1,
        // Folded is a transient state -- the panel returns TOAST_LINGER_MS after the
        // toast appears. pointerEvents guards the gap so an invisible panel cannot
        // eat clicks meant for the page underneath.
        pointerEvents: folded ? "none" : "auto",
        transition: `transform ${EDEN_TIMING.PANEL_FOLD_MS}ms ${EDEN_EASING.spring}, opacity 350ms ease`,
      }}
    >
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <Box
        sx={{
          flex: "none",
          display: "flex",
          alignItems: "center",
          gap: "12px",
          padding: "15px 14px 12px 16px",
          borderBottom: `1px solid ${EDEN_ALPHA.hairline}`,
        }}
      >
        <EdenAvatarOrb state={orbState} size={40} />

        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            component="div"
            sx={{
              font: "600 17px/1.15 Poppins, system-ui, sans-serif",
              color: EDEN_COLORS.fg1,
              letterSpacing: "-.015em",
            }}
          >
            Eden
          </Typography>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              marginTop: "2px",
              font: "400 11.5px/1.3 Poppins, system-ui, sans-serif",
              color: EDEN_COLORS.accentText,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            <Box
              aria-hidden="true"
              sx={{
                width: 5,
                height: 5,
                flex: "none",
                borderRadius: "50%",
                backgroundColor: EDEN_COLORS.accent,
                animation: "edenPulseDot 1.9s ease-in-out infinite",
              }}
            />
            {EDEN_ORB_STATUS[orbState]}
          </Box>
        </Box>

        <Tooltip title={isResetting ? "Resetting…" : "Reset chat"} placement="bottom" arrow>
          <IconButton
            id="eden-aurora-reset"
            aria-label="Reset chat"
            disabled={isResetting}
            onClick={onReset}
            sx={{
              width: 32,
              height: 32,
              flex: "none",
              borderRadius: `${EDEN_RADIUS.iconBtn}px`,
              backgroundColor: EDEN_ALPHA.iconBtnBg,
              color: EDEN_COLORS.fg2,
              transition: "background-color 180ms ease, transform 180ms ease, color 180ms ease",
              "&:hover": {
                backgroundColor: EDEN_ALPHA.iconBtnHover,
                color: EDEN_COLORS.accentText,
                transform: "rotate(-45deg)",
              },
            }}
          >
            <RestartAltIcon sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>

        {onClose && (
          <Tooltip title="Close chat" placement="bottom" arrow>
            <IconButton
              id="eden-aurora-close"
              aria-label="Close chat"
              onClick={onClose}
              sx={{
                width: 32,
                height: 32,
                flex: "none",
                marginLeft: "6px",
                borderRadius: `${EDEN_RADIUS.iconBtn}px`,
                backgroundColor: EDEN_ALPHA.iconBtnBg,
                color: EDEN_COLORS.fg2,
                transition: "background-color 180ms ease",
                "&:hover": { backgroundColor: "rgba(17,45,78,.11)" },
              }}
            >
              <CloseIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        )}
      </Box>

      {/* ── Page-context line ──────────────────────────────────────────────
          Surfaces the currentRoute / currentEntity already being POSTed in
          SessionContext. It is not new information -- it just stops the context
          being invisible, which is why "why is it answering about that project?"
          was impossible to self-diagnose before. */}
      {showContext && (
        <Box
          data-testid="eden-context-line"
          sx={{
            flex: "none",
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "9px 16px",
            background: EDEN_ALPHA.contextStrip,
            borderBottom: `1px solid ${EDEN_ALPHA.hairline}`,
            color: EDEN_COLORS.fg2,
          }}
        >
          <Box aria-hidden="true" sx={{ position: "relative", width: 12, height: 12, flex: "none" }}>
            <Box
              sx={{
                position: "absolute",
                inset: 0,
                borderRadius: "50%",
                border: `1px solid ${EDEN_COLORS.accent}`,
                opacity: 0.5,
                animation: "edenRingThink 2.4s ease-out infinite",
              }}
            />
            <Box
              sx={{
                position: "absolute",
                inset: "4px",
                borderRadius: "50%",
                backgroundColor: EDEN_COLORS.accent,
              }}
            />
          </Box>
          {/*
            All the text lives inside ONE flex item rather than each piece
            being its own flex sibling. Previously "Reading this page —", the
            mono route, and the optional entity were three separate flex
            children of an `alignItems:center` row -- since the mono route
            uses a different font-size (10.5px vs 11px), each was centered
            against its OWN line-box height independently, so the dot icon
            and the text never shared a baseline and looked vertically
            uneven. Grouping them here means they flow inline and share one
            baseline internally, and the whole block is centered against the
            icon as a single unit.
          */}
          <Box component="span" sx={{ font: "400 11px/1.3 Poppins, system-ui, sans-serif" }}>
            Reading this page —&nbsp;
            <Box component="span" sx={{ fontWeight: 500, color: EDEN_COLORS.accentText }}>
              {contextEntity}
            </Box>
          </Box>
        </Box>
      )}

      {/* ── Transcript ─────────────────────────────────────────────────────── */}
      <Box
        ref={scrollAreaRef}
        className="chat-msg-container"
        sx={{
          flex: 1,
          minHeight: 0,
          // Matches today's rule: scrolling is suppressed while the reset overlay
          // is up so the wipe animation cannot be scrolled away from.
          overflowY: isResetting ? "hidden" : "auto",
          padding: "16px 16px 10px",
          display: "flex",
          flexDirection: "column",
          gap: "2px",
        }}
      >
        {children}
      </Box>

      {/* ── Quick prompts ──────────────────────────────────────────────────── */}
      {quickPrompts && quickPrompts.length > 0 && (
        <Box
          sx={{
            flex: "none",
            display: "flex",
            flexWrap: "wrap",
            gap: "7px",
            padding: "0 16px 10px",
          }}
        >
          {quickPrompts.map((prompt) => (
            <Box
              key={prompt.text}
              component="button"
              type="button"
              data-testid="eden-quick-prompt"
              onClick={() => onQuickPrompt?.(prompt.text)}
              sx={{
                padding: "7px 12px",
                borderRadius: "99px",
                border: `1px solid ${EDEN_ALPHA.chipBorder}`,
                backgroundColor: EDEN_ALPHA.chipBg,
                color: EDEN_COLORS.accentText,
                font: "500 11px/1.2 Poppins, system-ui, sans-serif",
                cursor: "pointer",
                transition: `background-color 180ms ease, transform 200ms ${EDEN_EASING.spring}`,
                "&:hover": {
                  backgroundColor: EDEN_ALPHA.chipHover,
                  transform: "translateY(-1px)",
                },
                "&:focus-visible": {
                  outline: `2px solid ${EDEN_COLORS.accent}`,
                  outlineOffset: 2,
                },
              }}
            >
              {prompt.label ?? prompt.text}
            </Box>
          ))}
        </Box>
      )}

      {/* ── Composer ───────────────────────────────────────────────────────── */}
      <Box
        sx={{
          flex: "none",
          display: "flex",
          alignItems: "center",
          gap: "9px",
          padding: "11px 12px",
          borderTop: `1px solid ${EDEN_ALPHA.glassBorder}`,
          backgroundColor: "rgba(255,255,255,.62)",
        }}
      >
        <TextField
          /* id preserved verbatim from the legacy layout -- existing tests and any
             automation select on it. */
          id="chat-textfield-type-a-message"
          inputRef={inputRef}
          fullWidth
          size="small"
          placeholder="Where do you want to go?"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!sendDisabled) onSend();
            }
          }}
          sx={{
            "& .MuiOutlinedInput-root": {
              borderRadius: `${EDEN_RADIUS.composer}px`,
              backgroundColor: EDEN_COLORS.surface1,
              boxShadow: EDEN_SHADOWS.composerInset,
              font: "400 13px/1.3 Poppins, system-ui, sans-serif",
              "& fieldset": { borderColor: "rgba(17,45,78,.11)" },
              "&:hover fieldset": { borderColor: EDEN_ALPHA.chipBorder },
              "&.Mui-focused fieldset": { borderColor: EDEN_COLORS.accent, borderWidth: "1px" },
            },
          }}
        />
        <IconButton
          id="chatbot-button-button"
          aria-label="send"
          disabled={sendDisabled}
          onClick={onSend}
          sx={{
            width: 44,
            height: 44,
            flex: "none",
            borderRadius: `${EDEN_RADIUS.composer}px`,
            background: EDEN_GRADIENTS.accentTile,
            color: "#fff",
            boxShadow: EDEN_SHADOWS.fabChat,
            transition: `transform 220ms ${EDEN_EASING.spring}, box-shadow 220ms ease`,
            "&:hover": {
              background: EDEN_GRADIENTS.accentTile,
              transform: "translateY(-2px) scale(1.04)",
              boxShadow: "0 14px 28px rgba(14,158,168,.42)",
            },
            "&.Mui-disabled": { background: "rgba(17,45,78,.12)", color: "rgba(17,45,78,.32)" },
          }}
        >
          <SendIcon sx={{ fontSize: 19 }} />
        </IconButton>
      </Box>

      {/* ── Reset overlay ──────────────────────────────────────────────────
          Same trigger and semantics as today's .overlay-portal: the marker is
          stamped, the cache emptied and resetNavigationSession fired by
          Chat.tsx's handleReset -- this only draws the wait. */}
      {isResetting && (
        <Box
          className="eden-reset-overlay"
          role="status"
          aria-live="polite"
          aria-label="Clearing the conversation"
          sx={{
            position: "absolute",
            inset: 0,
            zIndex: 5,
            backgroundColor: EDEN_ALPHA.resetVeil,
            backdropFilter: "blur(3px)",
            WebkitBackdropFilter: "blur(3px)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: "16px",
          }}
        >
          <Box sx={{ position: "relative", width: 96, height: 96 }} aria-hidden="true">
            <Box
              sx={{
                position: "absolute",
                inset: 0,
                borderRadius: "50%",
                border: `1.5px dashed ${EDEN_COLORS.accent}80`,
                animation: "edenSpin 2.4s linear infinite",
              }}
            />
            <Box
              sx={{
                position: "absolute",
                inset: "14px",
                borderRadius: "50%",
                background: EDEN_GRADIENTS.orb,
                display: "grid",
                placeItems: "center",
                color: "#fff",
                animation: "edenSpin 1.1s linear infinite",
              }}
            >
              <RestartAltIcon sx={{ fontSize: 26 }} />
            </Box>
          </Box>

          <Typography
            component="div"
            sx={{ font: "600 13px/1.4 Poppins, system-ui, sans-serif", color: EDEN_COLORS.accentText }}
          >
            Clearing the conversation…
          </Typography>

          <Box sx={{ display: "grid", gap: "6px", width: 190 }} aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <Box
                key={i}
                sx={{
                  height: 5,
                  borderRadius: "99px",
                  backgroundColor: `${EDEN_COLORS.accent}99`,
                  transformOrigin: "left",
                  animation: `edenWipe 1.1s ease-in-out ${i * 0.12}s infinite`,
                }}
              />
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
};

export default EdenAuroraShell;

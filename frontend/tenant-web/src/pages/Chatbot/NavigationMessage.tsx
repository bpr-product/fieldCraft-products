/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose      : Renders EDEN NavigationResponse messages inside the chat —
*                navigation chips, disambiguation options, fallback suggestions
*                and plain guidance text.
*/
import React, { useState } from "react";
import {
  Box,
  ButtonBase,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import { NavigationResponse, ProjectRef } from "../../services/AIChatService";
import { FEATURE_FLAGS } from "../../constants/FeatureFlag";

// Read once at module scope, not per-render: NavigationChip/OptionCard are the
// SAME components rendered by both the legacy and Aurora Chat.tsx branches, so
// their colours are gated on the flag directly (rather than unconditionally
// restyled) to keep the legacy panel pixel-identical while it is off.
const AURORA_UI = FEATURE_FLAGS.ENABLE_EDEN_AURORA_UI;

export type NavigateHandler = (
  route: string,
  project?: ProjectRef,
  routeState?: Record<string, any>
) => void;

// Sends `text` into the chat as if the user had typed it -- used when a
// disambiguation option represents an ANSWER to a pending question ("which
// project?"), not a page to open. See OptionCard's handleClick for how this
// is told apart from a real "which page?" option (option.project is only
// ever set on the former).
export type ContinueChatHandler = (text: string) => void;

interface NavigationChipProps {
  label: string;
  route: string;
  icon?: string;
  project?: ProjectRef;
  routeState?: Record<string, any>;
  onNavigate: NavigateHandler;
}

const OPENING_DELAY_MS = 400;

export const NavigationChip: React.FC<NavigationChipProps> = ({
  label,
  route,
  project,
  routeState,
  onNavigate,
}) => {
  const [opening, setOpening] = useState(false);

  const handleClick = () => {
    if (opening) return;
    setOpening(true);
    setTimeout(() => onNavigate(route, project, routeState), OPENING_DELAY_MS);
  };

  return (
    <Chip
      data-testid="navigation-chip"
      label={opening ? "Opening…" : label}
      color="primary"
      variant="outlined"
      clickable
      disabled={opening}
      onClick={handleClick}
      icon={
        opening ? (
          <CircularProgress size={14} sx={AURORA_UI ? { color: "#0e9ea8 !important" } : undefined} />
        ) : (
          <ArrowForwardIcon fontSize="small" />
        )
      }
      sx={{
        mt: 1,
        mr: 1,
        maxWidth: "100%",
        ...(AURORA_UI && {
          borderRadius: "99px",
          borderColor: "rgba(14,158,168,.35)",
          backgroundColor: "rgba(14,158,168,.07)",
          color: "#0e7c86",
          fontFamily: "Poppins, system-ui, sans-serif",
          fontWeight: 500,
          "& .MuiChip-icon": { color: "#0e9ea8" },
          "&:hover": { backgroundColor: "rgba(14,158,168,.17)" },
        }),
      }}
    />
  );
};

export interface NavigationOptionData {
  label: string;
  route: string;
  icon?: string;
  description?: string;
  project?: ProjectRef;
  routeState?: Record<string, any>;
}

interface OptionCardProps {
  option: NavigationOptionData;
  onNavigate: NavigateHandler;
  onContinueChat?: ContinueChatHandler;
}

const OPTION_CARD_OPENING_DELAY_MS = 250;

// Icon + title + subtitle + chevron, one per row — the pattern used whenever
// EDEN offers more than one page and needs the user to pick (route
// disambiguation, a data question's "here's where you'll find it", or
// multiple projects matching the same name).
export const OptionCard: React.FC<OptionCardProps> = ({ option, onNavigate, onContinueChat }) => {
  const [opening, setOpening] = useState(false);

  const handleClick = () => {
    if (opening) return;
    setOpening(true);
    setTimeout(() => {
      // option.project is only ever set on Insight/Reporting's "which
      // project?" disambiguation (see projects_disambiguation in the
      // backend) -- clicking one of THOSE means "the answer is this
      // project," so continue the chat with its name instead of navigating
      // away to a blank page and leaving the original question unanswered.
      // Route-disambiguation options ("which page?") never set option.project,
      // so they keep navigating exactly as before.
      if (option.project && onContinueChat) {
        // Send the full label ("Buma P (Order #950558)"), not just the bare
        // project name -- project_health has real name duplication (two
        // different projects can share the exact same name), so the bare
        // name alone is ambiguous and re-triggers the same disambiguation
        // forever. The order number is an exact, unambiguous identifier
        // already checked first by the backend's entity resolver.
        onContinueChat(option.label);
      } else {
        onNavigate(option.route, option.project, option.routeState);
      }
    }, OPTION_CARD_OPENING_DELAY_MS);
  };

  return (
    <ButtonBase
      data-testid="navigation-option-card"
      onClick={handleClick}
      disabled={opening}
      focusRipple
      sx={
        AURORA_UI
          ? {
              width: "100%",
              maxWidth: "100%",
              boxSizing: "border-box",
              textAlign: "left",
              borderRadius: "14px",
              border: "1px solid rgba(17,45,78,.10)",
              background: "linear-gradient(180deg,#FBFDFE,#F4F8FA)",
              px: 1.5,
              py: 1.375,
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              transition: "border-color 180ms ease, box-shadow 220ms ease, transform 220ms cubic-bezier(.34,1.56,.64,1)",
              "&:hover": {
                borderColor: "#0e9ea8",
                boxShadow: "0 10px 22px rgba(14,158,168,.20)",
                transform: "translateY(-2px)",
                "& .option-card-chevron": { opacity: 1, transform: "translateX(2px)" },
              },
              "&.Mui-focusVisible": {
                outline: "2px solid #0e9ea8",
                outlineOffset: 2,
              },
            }
          : {
              width: "100%",
              maxWidth: "100%",
              boxSizing: "border-box",
              textAlign: "left",
              borderRadius: 2,
              border: "1px solid",
              borderColor: "divider",
              borderLeft: "3px solid",
              borderLeftColor: "primary.main",
              bgcolor: "background.paper",
              px: 2,
              py: 1.5,
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              boxShadow: "0 1px 2px rgba(0,0,0,0.06)",
              transition: "border-color 150ms ease, background-color 150ms ease, box-shadow 150ms ease",
              "&:hover": {
                borderColor: "primary.main",
                bgcolor: "action.hover",
                boxShadow: 2,
                "& .option-card-chevron": {
                  opacity: 1,
                  transform: "translateX(2px)",
                },
              },
              "&.Mui-focusVisible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: 2,
              },
            }
      }
    >
      <Box
        sx={
          AURORA_UI
            ? {
                width: 34,
                height: 34,
                borderRadius: "12px",
                background: "linear-gradient(135deg,#0e9ea8,#0b3552)",
                color: "#fff",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 17,
                lineHeight: 1,
                flexShrink: 0,
              }
            : {
                width: 36,
                height: 36,
                borderRadius: "50%",
                bgcolor: "primary.main",
                color: "primary.contrastText",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 18,
                lineHeight: 1,
                flexShrink: 0,
              }
        }
        aria-hidden="true"
      >
        {option.icon || "📄"}
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          variant="body2"
          sx={
            AURORA_UI
              ? { fontWeight: 600, fontSize: "12.5px", lineHeight: 1.35, color: "#0A1330", fontFamily: "Poppins, system-ui, sans-serif" }
              : { fontWeight: 700, lineHeight: 1.3, color: "text.primary" }
          }
        >
          {option.label}
        </Typography>
        {option.description && (
          <Typography
            variant="caption"
            sx={
              AURORA_UI
                ? {
                    color: "#5B7391",
                    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                    fontSize: "10px",
                    fontWeight: 500,
                    display: "block",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }
                : {
                    color: "text.secondary",
                    display: "block",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }
            }
          >
            {option.description}
          </Typography>
        )}
      </Box>
      {opening ? (
        <CircularProgress size={16} sx={{ flexShrink: 0, ...(AURORA_UI && { color: "#0e9ea8 !important" }) }} />
      ) : (
        <ChevronRightIcon
          fontSize="small"
          className="option-card-chevron"
          sx={{
            color: AURORA_UI ? "#0e9ea8" : "primary.main",
            opacity: 0.6,
            flexShrink: 0,
            transition: "color 150ms ease, opacity 150ms ease, transform 150ms ease",
          }}
        />
      )}
    </ButtonBase>
  );
};

interface OptionCardListProps {
  // A disambiguation turn can be a bare yes/no-style question with no
  // clickable options at all (e.g. EDEN's project-context-switch prompt,
  // "you were asking about X, now on Y — continue with X, or check Y?")
  // -- options is null there, not an empty array, so every caller of this
  // component must be able to pass that through untouched.
  options: NavigationOptionData[] | null | undefined;
  onNavigate: NavigateHandler;
  onContinueChat?: ContinueChatHandler;
}

// Per explicit user direction: the list stays VERTICAL regardless of count
// (an earlier horizontal-scroll layout was tried and explicitly rejected in
// favor of this). Above this many options, a plain vertical stack would
// push the chat panel itself taller and taller -- instead the list is
// capped to roughly this many cards' worth of height and scrolls
// vertically WITHIN that contained area, so the chat layout around it
// never grows unbounded.
//
// Raised 4 -> 5 (2026-09-03) alongside the backend change that lists every
// project as cards instead of redirecting to the Projects page above 30 --
// see fieldcraft-ai-services/src/eden's insight_agent.py projects_disambiguation.
const VISIBLE_CARDS_AT_ONCE = 5;
// One OptionCard's rendered height (icon circle 36px + vertical padding
// py=1.5 -> 12px top/bottom = 24px, so ~60px) plus the Stack's own
// spacing={1} gap (8px) between cards.
const CARD_HEIGHT_PX = 60;
const CARD_GAP_PX = 8;
const SCROLL_MAX_HEIGHT_PX =
  VISIBLE_CARDS_AT_ONCE * CARD_HEIGHT_PX + (VISIBLE_CARDS_AT_ONCE - 1) * CARD_GAP_PX;

export const OptionCardList: React.FC<OptionCardListProps> = ({ options, onNavigate, onContinueChat }) => {
  if (!options || options.length === 0) return null;

  const needsScroll = options.length > VISIBLE_CARDS_AT_ONCE;

  return (
    // width/maxWidth/boxSizing set explicitly at every level here (outer
    // Box, Stack, and each OptionCard's own sx) rather than trusting the
    // .message bubble ancestor in Chat.tsx to constrain this correctly —
    // defense in depth, so cards can never render wider than their
    // immediate parent regardless of what any ancestor container computes.
    <Box
      sx={{
        mt: 1,
        width: "100%",
        maxWidth: "100%",
        boxSizing: "border-box",
        ...(needsScroll && {
          maxHeight: SCROLL_MAX_HEIGHT_PX,
          overflowY: "auto",
          overflowX: "hidden",
          pr: 0.5,
          // A visible (not just functional) scrollbar -- otherwise nothing
          // on screen hints that more options exist below the fold.
          "&::-webkit-scrollbar": { width: 6 },
          "&::-webkit-scrollbar-thumb": { backgroundColor: "action.disabled", borderRadius: 3 },
          scrollbarWidth: "thin",
        }),
      }}
    >
      <Stack spacing={1} sx={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
        {options.map((option) => (
          <OptionCard
            key={`${option.route}-${option.label}`}
            option={option}
            onNavigate={onNavigate}
            onContinueChat={onContinueChat}
          />
        ))}
      </Stack>
    </Box>
  );
};

interface DisambiguationChipsProps {
  question: string;
  options: NavigationOptionData[] | null | undefined;
  onNavigate: NavigateHandler;
  onContinueChat?: ContinueChatHandler;
  /** Aurora only (default false): the question text is already rendered by the
   * caller's own staged bubble, so suppress this component's copy of it and
   * render only the interactive part. See NavigationMessageProps.hideMessage. */
  hideMessage?: boolean;
}

export const DisambiguationChips: React.FC<DisambiguationChipsProps> = ({
  question,
  options,
  onNavigate,
  onContinueChat,
  hideMessage = false,
}) => (
  <Box sx={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
    {!hideMessage && (
      <Typography variant="body2" component="span">
        {question}
      </Typography>
    )}
    <OptionCardList options={options} onNavigate={onNavigate} onContinueChat={onContinueChat} />
  </Box>
);

interface FallbackMessageProps {
  message: string;
  suggestion?: { label: string; route: string; routeState?: Record<string, any> };
  options?: NavigationOptionData[];
  onNavigate: NavigateHandler;
  /** Aurora only (default false) — see NavigationMessageProps.hideMessage. */
  hideMessage?: boolean;
}

export const FallbackMessage: React.FC<FallbackMessageProps> = ({
  message,
  suggestion,
  options,
  onNavigate,
  hideMessage = false,
}) => (
  <Box sx={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
    {!hideMessage && (
      <Typography variant="body2" component="span">
        {message}
      </Typography>
    )}
    {/* Cards — e.g. a data question EDEN can't answer offers the pages where
        the answer lives. Falls back to the single "did you mean" chip. */}
    {options && options.length > 0 ? (
      <OptionCardList options={options} onNavigate={onNavigate} />
    ) : (
      suggestion && (
        <Box sx={{ mt: 1 }}>
          <NavigationChip
            label={suggestion.label}
            route={suggestion.route}
            routeState={suggestion.routeState}
            onNavigate={onNavigate}
          />
        </Box>
      )
    )}
  </Box>
);

interface NavigationMessageProps {
  response: NavigationResponse;
  onNavigate: NavigateHandler;
  onContinueChat?: ContinueChatHandler;
  /**
   * Aurora only. Default false, so every existing (legacy) call site renders
   * byte-identically. EdenMessageBubble already renders response.message /
   * .question itself via useStagedReply (word-by-word streamed) -- passing
   * true here suppresses this component's OWN copy of that same text so it
   * is not shown twice, while keeping this file the single source of truth
   * for the interactive part (chips, option cards, boarding-pass slot).
   */
  hideMessage?: boolean;
}

const NavigationMessage: React.FC<NavigationMessageProps> = ({
  response,
  onNavigate,
  onContinueChat,
  hideMessage = false,
}) => {
  switch (response.type) {
    case "navigate":
      return (
        <Box sx={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
          {!hideMessage && (
            <Typography variant="body2" component="span">
              {response.message}
            </Typography>
          )}
          <Box>
            <NavigationChip
              label={response.label}
              route={response.route}
              icon={response.icon}
              project={response.project}
              routeState={response.routeState}
              onNavigate={onNavigate}
            />
          </Box>
        </Box>
      );
    case "disambiguation":
      return (
        <DisambiguationChips
          question={response.question}
          options={response.options}
          onNavigate={onNavigate}
          onContinueChat={onContinueChat}
          hideMessage={hideMessage}
        />
      );
    case "fallback":
      return (
        <FallbackMessage
          message={response.message}
          suggestion={response.suggestion}
          options={(response as any).options}
          onNavigate={onNavigate}
          hideMessage={hideMessage}
        />
      );
    case "guidance": {
      // Front-desk follow-up: a successful Insight/Reporting answer carries
      // a `suggestion` (Job Ledger / Invoices / Projects) so the user can
      // continue to the relevant page. This used to be dropped here --
      // guidance rendered message-only -- which is why answers felt like
      // dead ends. The message itself already includes the natural-language
      // offer ("Would you like to see the X page?"); this renders the
      // clickable chip that acts on it.
      const suggestion = (response as any).suggestion;
      return (
        <Box sx={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
          {!hideMessage && (
            <Typography variant="body2" component="span">
              {(response as any).message}
            </Typography>
          )}
          {suggestion && (
            <Box sx={{ mt: 1 }}>
              <NavigationChip
                label={suggestion.label}
                route={suggestion.route}
                routeState={suggestion.routeState}
                onNavigate={onNavigate}
              />
            </Box>
          )}
        </Box>
      );
    }
    default:
      return hideMessage ? null : (
        <Typography variant="body2" component="span">
          {(response as any).message}
        </Typography>
      );
  }
};

export default NavigationMessage;

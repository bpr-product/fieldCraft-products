/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Single source of truth for the EDEN "Aurora Glass" chat surface --
*                colours, elevation, radii, easing and conversation pacing.
*
*                Why a module and not theme.ts: the navy ink IS the app palette and is
*                read from the MUI theme (palette.primary.main === colors.primary[500]
*                === #112D4E), but the teal accent is new and scoped to this one
*                surface. Keeping it here means there is exactly one place to change
*                if/when it graduates to a brand colour in hooks/theme.ts +
*                colors_and_type.css -- rather than a hex literal scattered across
*                eight components, which is how the old purple gradient ended up
*                hard-coded in Chat.scss in six different places.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/

/** Solid colours. Navy values mirror hooks/theme.ts `colors.primary`; teal is new. */
export const EDEN_COLORS = {
  /** palette.primary.main -- user bubble, FAB base, on-light spotlight ring. */
  ink: "#112D4E",
  /** Gradient partner for `ink` (primary 600-700 range). */
  inkDeep: "#0B3552",
  /** --fieldcraft-primary-hover. */
  inkHover: "#0B1F36",

  /**
   * New accent. 3.0:1 on white -- FILLS, BORDERS and >=15px semibold text only.
   * For accent-coloured body copy use `accentText`, which clears 4.5:1.
   */
  accent: "#0E9EA8",
  accentText: "#0E7C86",
  accentBright: "#5FE0E8",
  accentGlow: "#8FE3E9",

  fg1: "#0A1330",
  fg2: "#33506E",
  fg3: "#8397AC",
  mono: "#5B7391",

  border: "#E2E8F0",
  surface1: "#FFFFFF",
  surface2: "#F7F8FC",

  warning: "#D97706",
  warningOnDark: "#FFC46B",
} as const;

/**
 * Pre-blended alpha values.
 *
 * Written as literals on purpose. colors_and_type.css documents that an opacity
 * modifier applied to a `var(--fieldcraft-*)` hex token silently emits no CSS at all --
 * the class is dropped and the element falls back to `currentColor`, which is how
 * borders in the Leads tree once rendered far darker than intended. Same hazard
 * applies to any `rgba()` built from a token at runtime, so these are fixed here.
 */
export const EDEN_ALPHA = {
  glassFill: "rgba(255,255,255,.74)",
  glassBorder: "rgba(17,45,78,.10)",
  hairline: "rgba(17,45,78,.07)",
  iconBtnBg: "rgba(17,45,78,.05)",
  iconBtnHover: "rgba(14,158,168,.14)",
  chipBg: "rgba(14,158,168,.07)",
  chipBorder: "rgba(14,158,168,.35)",
  chipHover: "rgba(14,158,168,.17)",
  cardBorder: "rgba(17,45,78,.10)",
  contextStrip:
    "linear-gradient(90deg, rgba(14,158,168,.09), rgba(14,158,168,.02))",
  toastFill: "rgba(10,19,48,.93)",
  resetVeil: "rgba(255,255,255,.82)",
} as const;

export const EDEN_GRADIENTS = {
  userBubble: `linear-gradient(135deg, #16375C, ${EDEN_COLORS.ink})`,
  orb: `radial-gradient(circle at 32% 26%, ${EDEN_COLORS.accentBright}, ${EDEN_COLORS.accent} 58%, ${EDEN_COLORS.inkDeep})`,
  accentTile: `linear-gradient(135deg, ${EDEN_COLORS.accent}, ${EDEN_COLORS.inkDeep})`,
  optionCard: "linear-gradient(180deg, #FBFDFE, #F4F8FA)",
  boardingPass: `linear-gradient(135deg, ${EDEN_COLORS.inkDeep}, ${EDEN_COLORS.ink} 55%, ${EDEN_COLORS.accentText})`,
  boardingSheen:
    "linear-gradient(105deg, transparent 32%, rgba(255,255,255,.22) 50%, transparent 68%)",
  progressFill: `linear-gradient(90deg, ${EDEN_COLORS.accentGlow}, #fff)`,
} as const;

export const EDEN_SHADOWS = {
  panel: "0 28px 64px rgba(5,10,43,.16), 0 2px 6px rgba(5,10,43,.05)",
  botBubble: "0 10px 26px rgba(5,10,43,.08)",
  userBubble: "0 8px 20px rgba(17,45,78,.22)",
  cardHover: "0 10px 22px rgba(14,158,168,.20)",
  boardingPass: "0 12px 28px rgba(11,53,82,.34)",
  toast: "0 18px 42px rgba(5,10,43,.34)",
  fabChat: "0 10px 26px rgba(14,158,168,.34)",
  composerInset: "inset 0 1px 2px rgba(5,10,43,.04)",
} as const;

/** Radii in px. Bubble radii are asymmetric -- see EDEN_BUBBLE_RADIUS. */
export const EDEN_RADIUS = {
  panel: 26,
  bubble: 18,
  bubbleTail: 6,
  composer: 16,
  toast: 18,
  card: 14,
  boardingPass: 16,
  tile: 12,
  iconBtn: 11,
  pill: 99,
} as const;

export const EDEN_BUBBLE_RADIUS = {
  /** Tail on the top-left, mirroring the legacy `.message` rule. */
  eden: "6px 18px 18px 18px",
  /** Tail on the top-right, mirroring the legacy `.message.You` rule. */
  user: "18px 6px 18px 18px",
} as const;

export const EDEN_EASING = {
  /** Entrances, hover lifts, panel return. Same curve as tailwind.theme.js `spring`. */
  spring: "cubic-bezier(.34, 1.56, .64, 1)",
  /** List/row reveals. Same curve as --fieldcraft-ease-out. */
  glide: "cubic-bezier(.16, 1, .3, 1)",
} as const;

export const EDEN_PANEL = {
  width: 416,
  height: 648,
} as const;

/**
 * Conversation pacing.
 *
 * All of it is cosmetic: the full NavigationResponse is already in hand before any
 * of these timers start. Nothing functional may be gated on them -- a click during
 * streaming must complete the render and act immediately (see useStagedReply's
 * finishNow), and navigate() fires off the boarding-pass ticks regardless of
 * whether the text stream has caught up.
 */
export const EDEN_TIMING = {
  /** Word-by-word stream rate. */
  STREAM_MS_PER_WORD: 46,
  /** Pause between staged segments of one answer; thinking row shows during it. */
  STAGE_GAP_MS: 520,
  /** The thinking row is never flashed for less than this -- sub-300ms flicker reads as a glitch. */
  THINK_MIN_MS: 700,
  /** One boarding-pass progress tick. Four ticks: 18 / 46 / 74 / 100 %. */
  NAV_STEP_MS: 380,
  /** Dwell on "Arrived" before navigate() fires. */
  NAV_SETTLE_MS: 400,
  /** Panel fold-away. */
  PANEL_FOLD_MS: 520,
  /** Toast appears this long after the fold, and the panel returns this long after the toast. */
  TOAST_LINGER_MS: 700,
  /** Toast auto-dismiss. */
  TOAST_DISMISS_MS: 6000,
} as const;

/** Boarding-pass progress steps, in order. Percentages are also rendered as the counter. */
export const EDEN_NAV_STEPS: readonly number[] = [18, 46, 74, 100];

export type EdenOrbState = "idle" | "reading" | "thinking" | "found";

/**
 * CSS animation shorthand per orb state. The keyframes live in Chat.scss under
 * `.eden-aurora` so they are shared with the reset overlay and context ping.
 */
export const EDEN_ORB_ANIMATION: Record<EdenOrbState, string> = {
  idle: "edenRingIdle 3.4s ease-in-out infinite",
  reading: "edenRingIdle 1.6s ease-in-out infinite",
  thinking: "edenRingThink 1.1s ease-out infinite",
  found: "edenRingIdle .6s ease-in-out 2",
};

/** Header status copy per orb state. Kept here so it cannot drift from the animation. */
export const EDEN_ORB_STATUS: Record<EdenOrbState, string> = {
  idle: "Reading this page",
  reading: "Reading this page",
  thinking: "Thinking…",
  found: "Found it",
};

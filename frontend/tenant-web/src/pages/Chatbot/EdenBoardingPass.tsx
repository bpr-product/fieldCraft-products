/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : The redirect card. Renders inside the final bot bubble of a
*                `navigate` turn and narrates the hand-off -- destination, route,
*                progress and the phase EDEN is in -- then calls onArrive, which is
*                what actually triggers navigate() in Chat.tsx.
*
*                Replaces the old pattern (a chip that flips to "Opening…" and an
*                opaque 800ms setTimeout). The wait is the same kind of wait; it is
*                just no longer unexplained.
*
*                Not cancellable, per product direction: the affordance for "that
*                wasn't what I meant" is the Undo on EdenArrivalToast, after landing.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React, { useEffect, useRef, useState } from "react";
import { Box, Typography } from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { useReducedMotion } from "framer-motion";
import {
  EDEN_GRADIENTS,
  EDEN_NAV_STEPS,
  EDEN_RADIUS,
  EDEN_SHADOWS,
  EDEN_TIMING,
} from "./edenAurora.tokens";

interface EdenBoardingPassProps {
  /** NavigationResponse.label -- the human destination, e.g. "Invoices · Buma P". */
  label: string;
  /**
   * True when the turn carries a project or routeState, which is what the second
   * phase caption is about. Purely cosmetic copy selection.
   */
  carriesContext?: boolean;
  /**
   * Fired once, NAV_SETTLE_MS after the bar reaches 100%. Chat.tsx does the real
   * navigate() here. Must be idempotent-safe; this component guarantees one call
   * per mount but a parent re-render must not double-navigate.
   */
  onArrive: () => void;
  /**
   * False for a hydrated/historical turn: skips the auto-progressing timers
   * entirely and renders permanently at "Arrived 100%" -- the card is inert and
   * display-only in this state, never calling onArrive again, not on mount and
   * not on click. Added 2026-09-10 -- this component used to always auto-fire
   * on mount regardless of whether it was the live turn or history being
   * replayed after reopening the chat, which silently re-navigated the user
   * every time (see EdenMessageBubble's own note on `animate`). Default true
   * so existing call sites (the live turn) are unaffected.
   */
  live?: boolean;
}

const phaseFor = (index: number, label: string, carriesContext: boolean): string => {
  if (index === 0) return "Resolving route";
  if (index === 1) return carriesContext ? "Passing project context" : `Loading ${label}`;
  if (index === 2) return `Loading ${label}`;
  return "Arrived";
};

const EdenBoardingPass: React.FC<EdenBoardingPassProps> = ({
  label,
  carriesContext = false,
  onArrive,
  live = true,
}) => {
  const prefersReducedMotion = useReducedMotion();
  const skip = prefersReducedMotion === true || !live;

  const [stepIndex, setStepIndex] = useState<number>(skip ? EDEN_NAV_STEPS.length - 1 : -1);

  // onArrive lands in a timer; keeping it in a ref means a parent re-render that
  // hands us a new closure cannot restart the whole progress sequence.
  const onArriveRef = useRef(onArrive);
  useEffect(() => {
    onArriveRef.current = onArrive;
  }, [onArrive]);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];

    // `live=false` (a hydrated/historical turn) renders the finished state
    // and stops right there -- no timer, no onArrive call, ever. This is not
    // just "skip the animation": it is the fix for a real bug where reopening
    // the chat re-fired this exact effect for whatever navigate turn was last
    // in the cache and silently re-navigated the user. A false `live` must
    // never end in a call to onArrive, on mount or on any later click --
    // once a turn has navigated, this card is a record of that, not a
    // repeatable trigger.
    if (!live) return;

    if (skip) {
      // Reduced motion, but the navigation still has to happen -- and promptly.
      timers.push(setTimeout(() => onArriveRef.current(), EDEN_TIMING.NAV_SETTLE_MS));
      return () => timers.forEach(clearTimeout);
    }

    EDEN_NAV_STEPS.forEach((_, index) => {
      timers.push(
        setTimeout(() => setStepIndex(index), (index + 1) * EDEN_TIMING.NAV_STEP_MS)
      );
    });
    timers.push(
      setTimeout(
        () => onArriveRef.current(),
        EDEN_NAV_STEPS.length * EDEN_TIMING.NAV_STEP_MS + EDEN_TIMING.NAV_SETTLE_MS
      )
    );

    // Unmounting mid-flight (panel closed, chat reset, route changed under us) must
    // leave nothing pending -- an orphaned timer here would navigate the app after
    // the user had already gone somewhere else themselves.
    return () => timers.forEach(clearTimeout);
  }, [skip]);

  const pct = stepIndex >= 0 ? EDEN_NAV_STEPS[stepIndex] : 0;
  const phase = stepIndex >= 0 ? phaseFor(stepIndex, label, carriesContext) : "Resolving route";

  return (
    <Box
      data-testid="eden-boarding-pass"
      role="status"
      aria-live="polite"
      aria-label={live ? `Opening ${label}. ${phase}.` : `Arrived at ${label}.`}
      sx={{
        position: "relative",
        marginTop: "12px",
        padding: "13px 14px",
        borderRadius: `${EDEN_RADIUS.boardingPass}px`,
        background: EDEN_GRADIENTS.boardingPass,
        boxShadow: EDEN_SHADOWS.boardingPass,
        overflow: "hidden",
      }}
    >
      {/* Sheen. Decorative, and only for the live in-progress card -- a settled
          historical "Arrived" record shouldn't shimmer as if still working. */}
      {live && (
        <Box
          aria-hidden="true"
          sx={{
            position: "absolute",
            inset: 0,
            background: EDEN_GRADIENTS.boardingSheen,
            animation: "edenSheen 2.4s ease-in-out infinite",
            pointerEvents: "none",
          }}
        />
      )}

      <Box
        sx={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "10px",
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography
            component="div"
            sx={{
              font: "500 9.5px/1 Poppins, system-ui, sans-serif",
              letterSpacing: ".14em",
              textTransform: "uppercase",
              color: "rgba(255,255,255,.6)",
            }}
          >
            Now boarding
          </Typography>
          <Typography
            component="div"
            sx={{
              font: "600 15px/1.25 Poppins, system-ui, sans-serif",
              color: "#fff",
              marginTop: "4px",
            }}
          >
            {label}
          </Typography>
        </Box>
        <Box
          aria-hidden="true"
          sx={{
            width: 44,
            height: 44,
            flex: "none",
            borderRadius: "14px",
            backgroundColor: "rgba(255,255,255,.14)",
            display: "grid",
            placeItems: "center",
            color: "#fff",
          }}
        >
          <ArrowForwardIcon sx={{ fontSize: 20 }} />
        </Box>
      </Box>

      <Box
        aria-hidden="true"
        sx={{
          position: "relative",
          height: 3,
          borderRadius: "99px",
          backgroundColor: "rgba(255,255,255,.2)",
          marginTop: "12px",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            position: "absolute",
            inset: "0 auto 0 0",
            width: `${pct}%`,
            background: EDEN_GRADIENTS.progressFill,
            borderRadius: "99px",
            transition: "width 350ms linear",
          }}
        />
      </Box>

      <Box
        sx={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: "8px",
        }}
      >
        <Typography
          component="span"
          sx={{ font: "400 10.5px/1.3 Poppins, system-ui, sans-serif", color: "rgba(255,255,255,.72)" }}
        >
          {phase}
        </Typography>
        <Typography
          component="span"
          sx={{
            font: "500 10px/1.3 'JetBrains Mono', ui-monospace, monospace",
            color: "rgba(255,255,255,.72)",
          }}
        >
          {pct}%
        </Typography>
      </Box>
    </Box>
  );
};

export default EdenBoardingPass;

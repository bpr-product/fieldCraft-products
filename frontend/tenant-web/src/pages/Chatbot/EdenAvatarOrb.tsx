/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : EDEN's avatar. A gradient orb whose ring animation and inner mark
*                report what the agent is doing (idle / reading the page / thinking /
*                found it), replacing the static chat.png in the old CardHeader.
*
*                Deliberately CSS-only -- no icon font, no SVG. The states differ by
*                animation, not by drawing, so there is nothing to redraw and nothing
*                to keep in sync with an asset.
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
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import {
  EDEN_COLORS,
  EDEN_EASING,
  EDEN_GRADIENTS,
  EDEN_ORB_ANIMATION,
  EdenOrbState,
} from "./edenAurora.tokens";

interface EdenAvatarOrbProps {
  state: EdenOrbState;
  /** Diameter in px. 40 in the panel header, 68 in the reset overlay. */
  size?: number;
}

const EdenAvatarOrb: React.FC<EdenAvatarOrbProps> = ({ state, size = 40 }) => (
  <Box
    data-testid="eden-avatar-orb"
    sx={{ position: "relative", width: size, height: size, flex: "none" }}
    aria-hidden="true"
  >
    {/*
      The expanding ring is the whole state signal, so it sits OUTSIDE the orb
      (inset -5px) where it can grow to 1.35x without being clipped by the disc's
      own overflow. Keyframes edenRingIdle / edenRingThink live in Chat.scss under
      .eden-aurora.
    */}
    <Box
      sx={{
        position: "absolute",
        inset: "-5px",
        borderRadius: "50%",
        border: `1.5px solid ${EDEN_COLORS.accent}66`,
        animation: EDEN_ORB_ANIMATION[state],
      }}
    />
    <Box
      sx={{
        position: "relative",
        width: size,
        height: size,
        borderRadius: "50%",
        background: EDEN_GRADIENTS.orb,
        boxShadow: `0 6px 18px rgba(14,158,168,.34), inset 0 -3px 8px rgba(0,0,0,.18)`,
        display: "grid",
        placeItems: "center",
        // Thinking dims the core so the ring reads as the active element; found
        // restores it at full strength behind the tick.
        opacity: state === "thinking" ? 0.82 : 1,
        transition: `opacity 220ms ${EDEN_EASING.spring}`,
      }}
    >
      {state === "found" ? (
        <CheckRoundedIcon
          sx={{
            fontSize: Math.round(size * 0.42),
            color: "rgba(255,255,255,.92)",
            animation: `edenPop 220ms ${EDEN_EASING.spring} both`,
          }}
        />
      ) : (
        // "E" monogram (reverted 2026-09-10, direct request) so the header
        // orb reads as EDEN's mark rather than an empty circle in every
        // state but "found". Same white-on-gradient treatment as the
        // checkmark it swaps out for.
        <Typography
          component="span"
          sx={{
            fontFamily: "Poppins, system-ui, sans-serif",
            fontWeight: 700,
            fontSize: Math.round(size * 0.44),
            lineHeight: 1,
            color: "rgba(255,255,255,.92)",
            userSelect: "none",
          }}
        >
          E
        </Typography>
      )}
    </Box>
  </Box>
);

export default EdenAvatarOrb;

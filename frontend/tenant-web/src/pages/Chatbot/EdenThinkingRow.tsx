/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : The typing row. Replaces the old "EDEN is thinking" bubble with an
*                activity-labelled one ("Checking invoices…") so the wait says what
*                is being waited on. Label comes from useEdenActivity.
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
import {
  EDEN_ALPHA,
  EDEN_BUBBLE_RADIUS,
  EDEN_COLORS,
  EDEN_EASING,
  EDEN_SHADOWS,
} from "./edenAurora.tokens";

interface EdenThinkingRowProps {
  /** e.g. "Looking up Buma P…". Never empty -- resolveEdenActivity always returns a string. */
  label: string;
}

const DOT_DELAYS = ["0s", ".14s", ".28s"];

const EdenThinkingRow: React.FC<EdenThinkingRowProps> = ({ label }) => (
  <Box
    data-testid="eden-thinking-row"
    role="status"
    aria-live="polite"
    aria-label={label}
    sx={{
      alignSelf: "flex-start",
      display: "flex",
      alignItems: "center",
      gap: "9px",
      padding: "11px 15px",
      borderRadius: EDEN_BUBBLE_RADIUS.eden,
      backgroundColor: EDEN_COLORS.surface1,
      border: `1px solid ${EDEN_ALPHA.glassBorder}`,
      boxShadow: EDEN_SHADOWS.botBubble,
      animation: `edenBubbleIn 400ms ${EDEN_EASING.spring} both`,
    }}
  >
    <Box sx={{ display: "inline-flex", gap: "4px" }} aria-hidden="true">
      {DOT_DELAYS.map((delay) => (
        <Box
          key={delay}
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            backgroundColor: EDEN_COLORS.accent,
            animation: `edenDot 1.15s ease-in-out ${delay} infinite`,
          }}
        />
      ))}
    </Box>
    <Typography
      component="span"
      sx={{ font: "400 12.5px/1.4 Poppins, system-ui, sans-serif", color: EDEN_COLORS.fg2 }}
    >
      {label}
    </Typography>
  </Box>
);

export default EdenThinkingRow;

/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : "Here you are -- <page>" toast with Undo, shown in the panel's place
*                immediately after a redirect so the chat does not sit on top of the
*                page the user was just taken to.
*
*                This is the same intent as today's props.onNavigated collapse, given
*                a visible landing and a way back. Note the panel RETURNS shortly
*                after this appears (TOAST_LINGER_MS) -- the chat is never left
*                invisible, so this toast is a confirmation, not a replacement UI.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React, { useEffect, useRef } from "react";
import { Box, Button, Typography } from "@mui/material";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import {
  EDEN_ALPHA,
  EDEN_COLORS,
  EDEN_EASING,
  EDEN_GRADIENTS,
  EDEN_PANEL,
  EDEN_RADIUS,
  EDEN_SHADOWS,
  EDEN_TIMING,
} from "./edenAurora.tokens";

interface EdenArrivalToastProps {
  /** NavigationResponse.label of the page just opened. */
  label: string;
  /** Go back. Chat.tsx passes a handler that calls navigate(-1) and dismisses. */
  onUndo: () => void;
  /** Called on auto-dismiss and after Undo, so the parent can clear its `arrival` state. */
  onDismiss: () => void;
}

const EdenArrivalToast: React.FC<EdenArrivalToastProps> = ({
  label,
  onUndo,
  onDismiss,
}) => {
  // Same reasoning as EdenBoardingPass: the dismiss timer must not restart when the
  // parent re-renders and hands us a fresh closure, or a toast next to a streaming
  // panel would never dismiss at all.
  const onDismissRef = useRef(onDismiss);
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    const timer = setTimeout(() => onDismissRef.current(), EDEN_TIMING.TOAST_DISMISS_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <Box
      data-testid="eden-arrival-toast"
      role="status"
      aria-live="polite"
      sx={{
        width: EDEN_PANEL.width,
        maxWidth: "100%",
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "13px 14px",
        borderRadius: `${EDEN_RADIUS.toast}px`,
        backgroundColor: EDEN_ALPHA.toastFill,
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        boxShadow: EDEN_SHADOWS.toast,
        animation: `edenToastIn 500ms ${EDEN_EASING.spring} both`,
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          width: 32,
          height: 32,
          flex: "none",
          borderRadius: "50%",
          background: EDEN_GRADIENTS.accentTile,
          display: "grid",
          placeItems: "center",
          color: "#fff",
        }}
      >
        <CheckRoundedIcon sx={{ fontSize: 17 }} />
      </Box>

      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          component="div"
          sx={{
            font: "500 12.5px/1.3 Poppins, system-ui, sans-serif",
            color: "#fff",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {`Here you are — ${label}`}
        </Typography>
      </Box>

      <Button
        id="eden-arrival-toast-undo"
        onClick={() => {
          onUndo();
          onDismissRef.current();
        }}
        sx={{
          flex: "none",
          padding: "7px 12px",
          minWidth: 0,
          borderRadius: "99px",
          border: "1px solid rgba(143,227,233,.45)",
          color: EDEN_COLORS.accentGlow,
          font: "500 11px/1.2 Poppins, system-ui, sans-serif",
          textTransform: "none",
          transition: "background-color 180ms ease",
          "&:hover": { backgroundColor: "rgba(143,227,233,.14)" },
        }}
      >
        Undo
      </Button>
    </Box>
  );
};

export default EdenArrivalToast;

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 *
 * Frontend-only landing route for QuickBooks OAuth popup completion.
 * Intuit redirects to the backend first. The backend processes and stores the
 * connection, then redirects here with only provider/result status values.
 */
import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Box, CircularProgress, Typography } from "@mui/material";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import {
  isQuickBooksOAuthProvider,
  isQuickBooksOAuthResult,
  QUICKBOOKS_OAUTH_CALLBACK_TYPE,
} from "./QuickBooksOAuthPopup";

type PageStatus = "processing" | "success" | "error" | "invalid";

const QuickBooksOAuthReturn: React.FC = () => {
  const { t } = useTranslation();
  const [status, setStatus] = useState<PageStatus>("processing");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const provider = params.get("provider");
    const result = params.get("result");

    if (!isQuickBooksOAuthProvider(provider) || !isQuickBooksOAuthResult(result)) {
      setStatus("invalid");
      return;
    }

    setStatus(result === "success" ? "success" : "error");

    if (window.opener && !window.opener.closed) {
      window.opener.postMessage(
        {
          type: QUICKBOOKS_OAUTH_CALLBACK_TYPE,
          provider,
          result,
        },
        window.location.origin,
      );
    }

    // The opener message gives immediate feedback. Closing remains the fallback
    // when browser isolation prevents window.opener from being available.
    window.setTimeout(() => window.close(), 300);
  }, []);

  return (
    <Box
      display="flex"
      flexDirection="column"
      alignItems="center"
      justifyContent="center"
      minHeight="100vh"
      gap={2}
      px={3}
      textAlign="center"
    >
      {status === "processing" && (
        <>
          <CircularProgress size={36} />
          <Typography color="text.secondary">{t("quickbooks.oauthReturn.processing")}</Typography>
        </>
      )}

      {status === "success" && (
        <>
          <CheckCircleOutlineIcon color="success" sx={{ fontSize: 48 }} />
          <Typography color="text.secondary">{t("quickbooks.oauthReturn.success")}</Typography>
        </>
      )}

      {status === "error" && (
        <>
          <ErrorOutlineIcon color="error" sx={{ fontSize: 48 }} />
          <Typography color="text.secondary">
            {t("quickbooks.oauthReturn.error")}
          </Typography>
        </>
      )}

      {status === "invalid" && (
        <Typography color="text.secondary">
          {t("quickbooks.oauthReturn.invalid")}
        </Typography>
      )}
    </Box>
  );
};

export default QuickBooksOAuthReturn;

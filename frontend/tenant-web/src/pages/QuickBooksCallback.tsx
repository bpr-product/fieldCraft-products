import React, { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Typography,
} from "@mui/material";
import { useSearchParams, useNavigate } from "react-router-dom";
import axios from "axios";
import { ConfigUrl } from "../auth/OidcConfig";

const OAUTH_POPUP_WINDOW_NAME = "qbo_oauth";
const OAUTH_STORAGE_EVENT_KEY = "qbo_oauth_event";
const STATE_MARKER_INFLIGHT = "inflight";
const STATE_MARKER_DONE = "done";

const QuickBooksCallback: React.FC = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();

  const quickBooksReturnRoute = "/admin";
  const quickBooksReturnState = { selectedTab: "9" };

  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const code = params.get("code");
  const state = params.get("state");
  const realmId = params.get("realmId");
  const error = params.get("error");
  const errorDescription = params.get("error_description");

  const exchangeUrl = useMemo(() => {
    const base = (ConfigUrl.extservice_base_url || "").replace(/\/$/, "");
    const qp = new URLSearchParams();

    if (code) qp.set("code", code);
    if (state) qp.set("state", state);
    if (realmId) qp.set("realmId", realmId);
    if (error) qp.set("error", error);
    if (errorDescription) qp.set("error_description", errorDescription);

    return `${base}/extsvc/api/v1/qbo/callback?${qp.toString()}`;
  }, [code, state, realmId, error, errorDescription]);

  useEffect(() => {
    const run = async () => {
      try {
        const isPopupWindow = window.name === OAUTH_POPUP_WINDOW_NAME;

        if (error) {
          const msg = `${error}${errorDescription ? `: ${errorDescription}` : ""}`;
          setErrorMessage(msg);
          return;
        }

        if (!code || !state || !realmId) {
          setErrorMessage("Missing code/state/realmId in callback URL.");
          return;
        }

        const key = `qbo_callback_state_${state}`;
        const marker = sessionStorage.getItem(key);

        if (marker === STATE_MARKER_DONE) {
          if (isPopupWindow) {
            try {
              window.close();
            } catch {
              console.error("Failed to close popup window");
            }
            return;
          }
          navigate(quickBooksReturnRoute, { replace: true, state: quickBooksReturnState });
          return;
        }

        if (marker === STATE_MARKER_INFLIGHT) {
          return;
        }

        sessionStorage.setItem(key, STATE_MARKER_INFLIGHT);

        const res = await axios.get(exchangeUrl, {
          headers: { Accept: "application/json" },
          timeout: 30000,
        });

        sessionStorage.setItem(key, STATE_MARKER_DONE);

        try {
          localStorage.setItem(
            OAUTH_STORAGE_EVENT_KEY,
            JSON.stringify({ ts: Date.now(), payload: res.data })
          );
        } catch {
          console.error("Failed to write OAuth storage event");
        }

        try {
          if (window.opener && !window.opener.closed) {
            window.opener.postMessage(
              { type: "QBO_CONNECTED", payload: res.data },
              window.location.origin
            );
          }
        } catch {
          console.error("Failed to postMessage to opener window");
        }

        if (isPopupWindow) {
          try {
            window.close();
          } catch {
            console.error("Failed to close popup window");
          }
          return;
        }

        navigate(quickBooksReturnRoute, {
          replace: true,
          state: quickBooksReturnState,
        });
      } catch (e: any) {
        try {
          if (state) {
            sessionStorage.removeItem(`qbo_callback_state_${state}`);
          }
        } catch {
          console.error("Failed to clean up session storage on error");
        }

        const msg =
          e?.response?.data
            ? typeof e.response.data === "string"
              ? e.response.data
              : JSON.stringify(e.response.data)
            : e?.message || "Network Error";
        setErrorMessage(msg);
      } finally {
        setLoading(false);
      }
    };

    run();
  }, []);

  return (
    <Box display="flex" justifyContent="center" mt={6} px={2}>
      <Card sx={{ maxWidth: 720, width: "100%" }}>
        <CardContent>
          <Typography variant="h6" fontWeight="bold" mb={1}>
            QuickBooks Authentication
          </Typography>

          {loading && (
            <Box display="flex" alignItems="center" gap={2}>
              <CircularProgress size={22} />
              <Typography>Loading...</Typography>
            </Box>
          )}

          {!loading && errorMessage && (
            <>
              <Typography color="error" sx={{ whiteSpace: "pre-wrap" }}>
                {errorMessage}
              </Typography>

              {/* <Box mt={2}>
                <Buttonk
                  // variant="contained"
                  onClick={() =>
                    navigate(quickBooksReturnRoute, {
                      replace: true,
                      state: quickBooksReturnState,
                    })
                  }
                  sx={{ textTransform: "none" }}
                 id="quick-books-callback-button-back-to-quickbooks">
                  Back to QuickBooks
                </Button>
              </Box> */}
            </>
          )}

          {!loading && !errorMessage && (
            <Typography>Connecting to your QuickBooks account . . .</Typography>
          )}
        </CardContent>
      </Card>
    </Box>
  );
};

export default QuickBooksCallback;

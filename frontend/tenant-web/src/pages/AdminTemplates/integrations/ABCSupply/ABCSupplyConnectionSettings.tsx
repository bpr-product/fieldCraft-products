/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Purpose : ABC Supply tenant connection settings.
 */
import React, { useCallback, useContext, useEffect, useState } from "react";
import abcSupplyLogo from "../../../../assets/imgs/Abc supply.jpg";
import { useOidcAccessToken } from "@axa-fr/react-oidc";
import {
  Alert,
  Button,
  CircularProgress,
  Grid,
  IconButton,
  InputAdornment,
  Skeleton,
  Snackbar,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import LocalOfferRoundedIcon from "@mui/icons-material/LocalOfferRounded";
import PlaylistAddCheckRoundedIcon from "@mui/icons-material/PlaylistAddCheckRounded";
import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import ErrorOutlineRoundedIcon from "@mui/icons-material/ErrorOutlineRounded";
import LinkOffRoundedIcon from "@mui/icons-material/LinkOffRounded";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import ArrowBackRoundedIcon from "@mui/icons-material/ArrowBackRounded";
import Visibility from "@mui/icons-material/Visibility";
import VisibilityOff from "@mui/icons-material/VisibilityOff";
import {
  getABCSupplyConnectionStatus,
  saveABCSupplyConnection,
  disconnectABCSupplyConnection,
  type ABCSupplyConnectionStatus,
} from "../../../../services/integrations/ABCSupplyService";
import { TenantContext } from "../../../../hooks/UserSession";
import "./ABCSupplyConnectionSettings.scss";

export interface ABCConnectionInfo {
  connected: boolean;
  tenantId: string;
  serviceId?: string | null;
  branchNumber?: string | null;
  shipToNumber?: string | null;
  billToNumberPresent?: boolean;
}

interface Props {
  onStatusChange?: (info: ABCConnectionInfo | null) => void;
}

type UIState = "loading" | "setup_error" | "landing" | "form" | "connected";

const ABCLogoMark: React.FC<{ size?: number }> = ({ size = 48 }) => (
  <div className="abc-logo-mark" style={{ width: size, height: size }}>
    <img src={abcSupplyLogo} alt="ABC Supply" className="abc-logo-img" />
  </div>
);

const DEFAULT_SCOPES = "product.read pricing.read account.read location.read";

const fmt = (iso?: string | null): string => {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("en-US", {
      month: "long", day: "numeric", year: "numeric",
    });
  } catch {
    return iso;
  }
};

const STEPS = [
  {
    n: "1",
    title: "Get your API credentials",
    body: "Log in to your ABC Supply account portal and generate a Client ID and Client Secret for API access.",
  },
  {
    n: "2",
    title: "Enter and test",
    body: "Paste your credentials here. FIELDCRAFT validates them against ABC Supply's API before saving.",
  },
  {
    n: "3",
    title: "Search and add materials",
    body: "Browse the catalog, see your account pricing, and add materials to any project — without leaving FIELDCRAFT.",
  },
];

const FEATURES = [
  {
    icon: <SearchRoundedIcon />,
    label: "Full catalog search",
    body: "Search ABC Supply's entire product catalog by keyword, item number, or category.",
  },
  {
    icon: <LocalOfferRoundedIcon />,
    label: "Live account pricing",
    body: "See your negotiated account pricing in real time — not list price — for accurate estimates.",
  },
  {
    icon: <PlaylistAddCheckRoundedIcon />,
    label: "Add to material list",
    body: "Select items and add them directly to your FIELDCRAFT material list, ready for any project BOM.",
  },
];

const ABCSupplyConnectionSettings: React.FC<Props> = ({ onStatusChange }) => {
  const { accessToken } = useOidcAccessToken();
  const { tenantSessionDetails } = useContext(TenantContext);
  const resolvedTenantId = React.useMemo(() => {
    const raw = tenantSessionDetails?.tenantid ?? tenantSessionDetails?.tenantId ?? tenantSessionDetails?.tenant_id;
    return raw ? String(raw).trim() : null;
  }, [tenantSessionDetails]);

  const [uiState, setUiState] = useState<UIState>("loading");
  const [setupError, setSetupError] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ABCSupplyConnectionStatus | null>(null);
  const [wasDisconnected, setWasDisconnected] = useState(false);

  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [branchNumber, setBranchNumber] = useState("");
  const [shipToNumber, setShipToNumber] = useState("");
  const [billToNumber, setBillToNumber] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isDisconnecting, setIsDisconnecting] = useState(false);

  const [toast, setToast] = useState<{ message: string; severity: "success" | "error" | "info" } | null>(null);

  const notifyParent = useCallback(
    (status: ABCSupplyConnectionStatus | null, tid: string) => {
      if (!onStatusChange) return;
      if (status?.connected) {
        onStatusChange({
          connected: true,
          tenantId: tid,
          serviceId: status.serviceId,
          branchNumber: status.branchNumber,
          shipToNumber: status.shipToNumber,
          billToNumberPresent: status.billToNumberPresent,
        });
      } else {
        onStatusChange(null);
      }
    },
    [onStatusChange]
  );

  useEffect(() => {
    let active = true;
    const init = async () => {
      const tid = resolvedTenantId;
      if (!tid) {
        setSetupError("Tenant ID not found. Please contact your administrator.");
        setUiState("setup_error");
        return;
      }
      try {
        setTenantId(tid);
        const statusRes = await getABCSupplyConnectionStatus({ tenant_id: tid }, accessToken);
        if (!active) return;
        const status: ABCSupplyConnectionStatus = statusRes.data ?? {};
        setConnectionStatus(status);
        notifyParent(status, tid);
        setUiState(status.connected ? "connected" : "landing");
      } catch {
        if (!active) return;
        setSetupError("Failed to load ABC Supply connection. Check your network and try again.");
        setUiState("setup_error");
      }
    };
    init();
    return () => { active = false; };
  }, [accessToken, notifyParent, resolvedTenantId]);

  const handleSave = async () => {
    if (!tenantId) return;
    if (!clientId.trim()) { setFormError("Client ID is required."); return; }
    if (!clientSecret.trim()) { setFormError("Client Secret is required."); return; }
    setFormError(null);
    setIsSaving(true);
    try {
      const res = await saveABCSupplyConnection(
        {
          tenant_id: tenantId,
          env: "production",
          client_id: clientId.trim(),
          client_secret: clientSecret.trim(),
          branch_number: branchNumber.trim() || null,
          ship_to_number: shipToNumber.trim() || null,
          bill_to_number: billToNumber.trim() || null,
          scopes: DEFAULT_SCOPES,
        },
        accessToken
      );
      if (!res.ok) {
        const msg = res.message ?? "";
        const lc = msg.toLowerCase();
        // Prefer the explicit status code the backend returns (401 = bad credentials,
        // distinct from 502 = ABC Supply temporarily unavailable) over sniffing the message
        // text, which is a fragile signal that can silently break if the wording changes.
        const isCredError =
          res.status === 401 ||
          lc.includes("token") || lc.includes("401") || lc.includes("invalid") ||
          lc.includes("unauthorized") || lc.includes("client") || lc.includes("authentication");
        setFormError(
          isCredError
            ? "Invalid Client ID or Client Secret. Please use valid credentials."
            : msg || "Connection failed. Please check your credentials."
        );
        return;
      }
      const status: ABCSupplyConnectionStatus = res.data ?? {};
      setConnectionStatus(status);
      setClientId("");
      setClientSecret("");
      setWasDisconnected(false);
      setUiState("connected");
      notifyParent(status, tenantId);
      setToast({ message: "ABC Supply connected successfully.", severity: "success" });
    } catch (error: any) {
      const status = error?.response?.status ?? error?.status;
      const detail: string = error?.response?.data?.detail ?? error?.message ?? "";
      const lc = detail.toLowerCase();
      const isCredError =
        status === 401 || status === 502 ||
        lc.includes("token") || lc.includes("401") ||
        lc.includes("invalid") || lc.includes("unauthorized");
      setFormError(
        isCredError
          ? "Invalid Client ID or Client Secret. Please use valid credentials."
          : "Connection failed. Please verify your credentials and try again."
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleDisconnect = async () => {
    if (!tenantId) return;
    setIsDisconnecting(true);
    try {
      const res = await disconnectABCSupplyConnection(
        { tenant_id: tenantId, clear_credentials: false },
        accessToken
      );
      const status: ABCSupplyConnectionStatus = res.data ?? { connected: false };
      setConnectionStatus(status);
      notifyParent(status, tenantId);
      setWasDisconnected(true);
      setUiState("landing");
      setToast({ message: "ABC Supply disconnected.", severity: "info" });
    } catch {
      setToast({ message: "Disconnect failed. Please try again.", severity: "error" });
    } finally {
      setIsDisconnecting(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  const renderLoading = () => (
    <div className="abc-cs-load-panel">
      {/* Banner skeleton */}
      <div className="abc-cs-load-banner">
        <div className="abc-cs-load-banner-inner">
          <Skeleton variant="rounded" width={42} height={42}
            sx={{ bgcolor: "rgba(255,255,255,0.10)", flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <Skeleton width={100} height={16} sx={{ bgcolor: "rgba(255,255,255,0.12)", mb: 0.75 }} />
            <Skeleton width={210} height={13} sx={{ bgcolor: "rgba(255,255,255,0.07)" }} />
          </div>
          <Skeleton variant="rounded" width={96} height={30}
            sx={{ bgcolor: "rgba(255,255,255,0.07)", flexShrink: 0 }} />
        </div>
      </div>

      {/* Row skeletons */}
      <div className="abc-cs-load-rows">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton
            key={i}
            variant="rectangular"
            height={46}
            sx={{ display: "block", bgcolor: i % 2 === 0 ? "#f4f0ea" : "#f9f6f1" }}
          />
        ))}
      </div>
    </div>
  );

  const renderSetupError = () => (
    <div className="abc-cs-setup-error">
      <ErrorOutlineRoundedIcon className="abc-cs-setup-error-icon" />
      <Typography className="abc-cs-setup-error-title">ABC Supply unavailable</Typography>
      <Typography className="abc-cs-setup-error-sub">{setupError}</Typography>
    </div>
  );

  const renderLanding = () => (
    <div className="abc-cs-landing">
      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <div className="abc-cs-hero">
        <ABCLogoMark size={60} />
        <div className="abc-cs-hero-body">
          <Typography className="abc-cs-hero-headline">
            {wasDisconnected ? "Reconnect ABC Supply to FIELDCRAFT" : "Connect ABC Supply to FIELDCRAFT"}
          </Typography>
          <Typography className="abc-cs-hero-sub">
            Browse your account's full catalog, see live negotiated pricing, and add materials directly to any project — without leaving FIELDCRAFT.
          </Typography>
          <Button
            variant="contained"
            className="abc-cs-hero-cta"
            endIcon={<ArrowForwardRoundedIcon />}
            onClick={() => { setFormError(null); setUiState("form"); }}
          >
            {wasDisconnected ? "Reconnect ABC Supply" : "Connect ABC Supply"}
          </Button>
        </div>
      </div>

      {/* ── How it works ─────────────────────────────────────────────────── */}
      <div className="abc-cs-section">
        <Typography className="abc-cs-section-label">How to connect</Typography>
        <Grid container spacing={2} sx={{ mt: 0 }}>
          {STEPS.map(({ n, title, body }) => (
            <Grid item xs={12} md={4} key={n}>
              <div className="abc-cs-step">
                <div className="abc-cs-step-num">{n}</div>
                <Typography className="abc-cs-step-title">{title}</Typography>
                <Typography className="abc-cs-step-body">{body}</Typography>
              </div>
            </Grid>
          ))}
        </Grid>
      </div>

      {/* ── Features ─────────────────────────────────────────────────────── */}
      <div className="abc-cs-section">
        <Typography className="abc-cs-section-label">What you can do</Typography>
        <Grid container spacing={2} sx={{ mt: 0 }}>
          {FEATURES.map(({ icon, label, body }) => (
            <Grid item xs={12} md={4} key={label}>
              <div className="abc-cs-feature">
                <div className="abc-cs-feature-icon">{icon}</div>
                <Typography className="abc-cs-feature-label">{label}</Typography>
                <Typography className="abc-cs-feature-body">{body}</Typography>
              </div>
            </Grid>
          ))}
        </Grid>
      </div>
    </div>
  );

  const renderForm = () => (
    <div className="abc-cs-form-wrap abc-cs-anim-fade-up">
      <button className="abc-cs-back" onClick={() => { setFormError(null); setUiState("landing"); }}>
        <ArrowBackRoundedIcon fontSize="small" />
        <span>Back</span>
      </button>

      <div className="abc-cs-form-layout">
        {/* ── Left brand panel ──────────────────────────────────────────── */}
        <div className="abc-cs-form-brand">
          <ABCLogoMark size={52} />
          <Typography className="abc-cs-form-brand-title">ABC Supply</Typography>
          <Typography className="abc-cs-form-brand-body">
            Connect your account to browse the full catalog with live negotiated pricing and add materials directly to any project.
          </Typography>
          <ul className="abc-cs-form-brand-trust">
            <li>
              <CheckCircleRoundedIcon />
              <span>Use your Client ID and Client Secret to connect securely
</span>
            </li>
            <li>
              <CheckCircleRoundedIcon />
              <span>Credentials stored securely in FIELDCRAFT Cloud</span>
            </li>
            <li>
              <CheckCircleRoundedIcon />
              <span>Disconnect anytime from this page</span>
            </li>
          </ul>
        </div>

        {/* ── Right form panel ──────────────────────────────────────────── */}
        <div className="abc-cs-form-card">
          <Typography className="abc-cs-form-title">Enter your credentials</Typography>
          <Typography className="abc-cs-form-sub">
            Get your Client ID and Client Secret from your ABC Supply account portal.
          </Typography>

          <Stack gap={2.5} sx={{ mt: 3 }}>
            <div className="abc-cs-field-row abc-cs-anim-field" style={{ "--field-i": 0 } as React.CSSProperties}>
              <Typography className="abc-cs-field-label">Client ID</Typography>
              <TextField
                size="small"
                fullWidth
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                className="abc-cs-field"
                autoComplete="off"
                inputProps={{ autoComplete: "new-password" }}
                placeholder="Your ABC Supply client ID"
              />
            </div>

            <div className="abc-cs-field-row abc-cs-anim-field" style={{ "--field-i": 1 } as React.CSSProperties}>
              <Typography className="abc-cs-field-label">Client Secret</Typography>
              <TextField
                size="small"
                fullWidth
                type={showSecret ? "text" : "password"}
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="abc-cs-field"
                autoComplete="off"
                inputProps={{ autoComplete: "new-password" }}
                placeholder="Your ABC Supply client secret"
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton size="small" onClick={() => setShowSecret((v) => !v)} tabIndex={-1}>
                        {showSecret ? <VisibilityOff fontSize="small" /> : <Visibility fontSize="small" />}
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
              />
            </div>

            <div className="abc-cs-anim-field" style={{ "--field-i": 2 } as React.CSSProperties}>
              <Typography className="abc-cs-section-label">
                Account details <span className="abc-cs-field-optional">(optional)</span>
              </Typography>
              <Stack direction="row" gap={1.5} flexWrap="wrap">
                <div className="abc-cs-field-account">
                  <Typography className="abc-cs-field-label">Branch #</Typography>
                  <TextField
                    size="small"
                    fullWidth
                    value={branchNumber}
                    onChange={(e) => setBranchNumber(e.target.value)}
                    className="abc-cs-field"
                    placeholder="e.g. 17"
                  />
                </div>
                <div className="abc-cs-field-account">
                  <Typography className="abc-cs-field-label">Ship-To #</Typography>
                  <TextField
                    size="small"
                    fullWidth
                    value={shipToNumber}
                    onChange={(e) => setShipToNumber(e.target.value)}
                    className="abc-cs-field"
                    placeholder="e.g. 2154884-2"
                  />
                </div>
                <div className="abc-cs-field-account">
                  <Typography className="abc-cs-field-label">Bill-To #</Typography>
                  <TextField
                    size="small"
                    fullWidth
                    value={billToNumber}
                    onChange={(e) => setBillToNumber(e.target.value)}
                    className="abc-cs-field"
                    placeholder="e.g. 2154884-2"
                  />
                </div>
              </Stack>
              <Typography className="abc-cs-field-hint">
                Used for catalog filtering and live pricing. Can be added later.
              </Typography>
            </div>
          </Stack>

          {formError && (
            <Alert severity="error" sx={{ mt: 2, borderRadius: "8px", fontSize: "0.82rem" }}>
              {formError}
            </Alert>
          )}

          <Stack direction="row" gap={1.5} justifyContent="flex-end" sx={{ mt: 3 }}>
            <Button
              variant="text"
              className="abc-cs-btn-cancel"
              onClick={() => { setFormError(null); setUiState("landing"); }}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              className="abc-cs-btn-save"
              onClick={handleSave}
              disabled={isSaving || !clientId.trim() || !clientSecret.trim()}
              endIcon={isSaving ? undefined : <ArrowForwardRoundedIcon />}
              startIcon={isSaving ? <CircularProgress size={15} color="inherit" /> : undefined}
            >
              {isSaving ? "connection…" : "Connect"}
            </Button>
          </Stack>
        </div>
      </div>
    </div>
  );

  const renderConnected = () => {
    const s = connectionStatus;
    const since = fmt(s?.connectedAt);
    const meta = [
      since ? `Since ${since}` : null,
      s?.branchNumber ? `Branch ${s.branchNumber}` : null,
    ].filter(Boolean).join(" · ");
    return (
      <div className="abc-cs-banner">
        <div className="abc-cs-banner-left">
          <ABCLogoMark size={42} />
          <div>
            <Typography className="abc-cs-banner-title">ABC Supply</Typography>
            <Stack direction="row" alignItems="center" gap={1}>
              <div className="abc-cs-pulse" />
              <Typography className="abc-cs-banner-meta">
                Connected{meta ? ` · ${meta}` : ""}
              </Typography>
            </Stack>
          </div>
        </div>
        <Tooltip title="Disconnect ABC Supply">
          <span>
            <Button
              variant="outlined"
              size="small"
              className="abc-cs-btn-disconnect"
              startIcon={
                isDisconnecting
                  ? <CircularProgress size={13} color="inherit" />
                  : <LinkOffRoundedIcon fontSize="small" />
              }
              onClick={handleDisconnect}
              disabled={isDisconnecting}
            >
              {isDisconnecting ? "Disconnecting…" : "Disconnect"}
            </Button>
          </span>
        </Tooltip>
      </div>
    );
  };

  return (
    <>
      <div className="abc-cs-root">
        {uiState === "loading" && renderLoading()}
        {uiState === "setup_error" && renderSetupError()}
        {uiState === "landing" && renderLanding()}
        {uiState === "form" && renderForm()}
        {uiState === "connected" && renderConnected()}
      </div>

      <Snackbar
        open={!!toast}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        {toast ? (
          <Alert severity={toast.severity} onClose={() => setToast(null)} sx={{ borderRadius: 2 }}>
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </>
  );
};

export default ABCSupplyConnectionSettings;

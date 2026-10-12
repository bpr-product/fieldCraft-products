/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 */
import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Box, Button, CircularProgress, Dialog, Typography } from "@mui/material";
import AccountBalanceRoundedIcon from "@mui/icons-material/AccountBalanceRounded";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import LinkRoundedIcon from "@mui/icons-material/LinkRounded";
import LinkOffRoundedIcon from "@mui/icons-material/LinkOffRounded";
import AutorenewRoundedIcon from "@mui/icons-material/AutorenewRounded";
import LockRoundedIcon from "@mui/icons-material/LockRounded";
import PauseRoundedIcon from "@mui/icons-material/PauseRounded";
import PeopleRoundedIcon from "@mui/icons-material/PeopleRounded";
import StorefrontRoundedIcon from "@mui/icons-material/StorefrontRounded";
import AccountTreeRoundedIcon from "@mui/icons-material/AccountTreeRounded";
import ReceiptLongRoundedIcon from "@mui/icons-material/ReceiptLongRounded";
import AccountBalanceWalletRoundedIcon from "@mui/icons-material/AccountBalanceWalletRounded";
import FolderSpecialRoundedIcon from "@mui/icons-material/FolderSpecialRounded";
import ConstructionRoundedIcon from "@mui/icons-material/ConstructionRounded";
import BadgeRoundedIcon from "@mui/icons-material/BadgeRounded";
import AccessTimeRoundedIcon from "@mui/icons-material/AccessTimeRounded";
import "./QuickBooksConnection.scss";

export type QbCapStatus = "off" | "on" | "attention" | "reconnect";

export interface QuickBooksCapabilityState {
  status: QbCapStatus;
  companyName?: string | null;
  lastCheckedLabel?: string | null;
  busy?: boolean;
}

export interface QuickBooksConnectionProps {
  accounting: QuickBooksCapabilityState;
  onConnectAccounting: () => void;
  onReconnectAccounting: () => void;
  onDisconnectAccounting: () => void;
  disabled?: boolean;
}

const getStateText = (t: (key: string) => string): Record<QbCapStatus, string> => ({
  off: t("quickbooks.connection.stateNotConnected"),
  on: t("quickbooks.connection.stateConnected"),
  attention: t("quickbooks.connection.stateActionNeeded"),
  reconnect: t("quickbooks.connection.stateReconnect"),
});

const getSyncItems = (t: (key: string) => string) => [
  { id: "Customers", name: t("tenantConfig.commsRedirect.audienceCustomer"), icon: <PeopleRoundedIcon /> },
  { id: "Vendors", name: t("quickbooks.connection.syncVendors"), icon: <StorefrontRoundedIcon /> },
  { id: "Chart of Accounts", name: t("quickbooks.connection.syncChartOfAccounts"), icon: <AccountTreeRoundedIcon /> },
  { id: "Bills", name: t("quickbooks.connection.syncBills"), icon: <ReceiptLongRoundedIcon /> },
  { id: "Bill Payments", name: t("quickbooks.connection.syncBillPayments"), icon: <AccountBalanceWalletRoundedIcon /> },
  { id: "Projects", name: t("quickbooks.connection.syncProjects"), icon: <FolderSpecialRoundedIcon /> },
  { id: "Services", name: t("quickbooks.connection.syncServices"), icon: <ConstructionRoundedIcon /> },
  { id: "Employees", name: t("tenantConfig.commsRedirect.audienceEmployee"), icon: <BadgeRoundedIcon /> },
  { id: "Time Activity", name: t("quickbooks.connection.syncTimeActivity"), icon: <AccessTimeRoundedIcon /> },
];

export const QuickBooksConnection: React.FC<QuickBooksConnectionProps> = ({
  accounting,
  onConnectAccounting,
  onReconnectAccounting,
  onDisconnectAccounting,
  disabled = false,
}) => {
  const { t } = useTranslation();
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const reconnect = accounting.status === "reconnect" || accounting.status === "attention";
  const connected = accounting.status === "on";
  const stateText = getStateText(t);
  const syncItems = getSyncItems(t);

  return (
    <Box className="qbc-root">
      <Box className="qbc-card" role="region" aria-label={t("quickbooks.connection.regionLabel")}>
        <Box className="qbc-body">
          <Box component="aside" className="qbc-rail">
            <Box className="qbc-rail-head">
              <Box className="qbc-mark" aria-hidden="true"><span>qb</span></Box>
              <Box className="qbc-rail-head-txt">
                <Typography className="qbc-rail-head-t">{t("quickbooks.connection.quickBooks")}</Typography>
                <Typography className="qbc-rail-head-s">{t("quickbooks.connection.syncsWithFieldCraft")}</Typography>
              </Box>
            </Box>
            <Typography className="qbc-rail-label">{t("quickbooks.connection.connection")}</Typography>
            <Box className="qbc-cap-row" data-status={accounting.status} aria-current>
              <Box className="qbc-cap-icon" aria-hidden="true"><AccountBalanceRoundedIcon /></Box>
              <Box className="qbc-cap-text">
                <Typography className="qbc-cap-name">{t("quickbooks.connection.quickBooksOnline")}</Typography>
                <Typography className="qbc-cap-sub">{t("quickbooks.connection.accountingAndTimeActivity")}</Typography>
              </Box>
              <Box className="qbc-cap-status">
                <Box className="qbc-cap-dot" />
                <Typography className="qbc-cap-state">{stateText[accounting.status]}</Typography>
              </Box>
            </Box>
            {accounting.companyName && (
              <Box className="qbc-rail-foot"><span className="qbc-company">{accounting.companyName}</span></Box>
            )}
          </Box>

          <Box component="section" className="qbc-detail">
            <Box className="qbc-detail-top">
              <Box className="qbc-main">
                {!connected && !reconnect && (
                  <Box className="qbc-panel">
                    <Box>
                      <span className="qbc-eyebrow accent">{t("quickbooks.connection.quickBooks")}</span>
                      <Typography className="qbc-title">{t("quickbooks.connection.connectTitle")}</Typography>
                    </Box>
                    <Typography className="qbc-lede">
                      {t("quickbooks.connection.connectLede")}
                    </Typography>
                    <Box className="qbc-actions">
                      <Button
                        className="qbc-btn qbc-btn-primary"
                        onClick={onConnectAccounting}
                        disabled={disabled || accounting.busy}
                        startIcon={accounting.busy ? <CircularProgress size={15} color="inherit" /> : <LinkRoundedIcon />}
                      >
                        {accounting.busy ? t("quickbooks.connection.openingQuickBooks") : t("quickbooks.connection.connectQuickBooks")}
                      </Button>
                      <Typography className="qbc-reassure"><LockRoundedIcon /> {t("quickbooks.connection.secureSignInIntuitDot")}</Typography>
                    </Box>
                  </Box>
                )}

                {reconnect && (
                  <Box className="qbc-panel">
                    <Box>
                      <span className="qbc-eyebrow attn">{t("quickbooks.connection.stateActionNeeded")}</span>
                      <Typography className="qbc-title">{t("quickbooks.connection.reconnectTitle")}</Typography>
                    </Box>
                    <Typography className="qbc-lede">
                      {t("quickbooks.connection.reconnectLede")}
                    </Typography>
                    <Box className="qbc-actions">
                      <Button
                        className="qbc-btn qbc-btn-primary"
                        onClick={onReconnectAccounting}
                        disabled={disabled || accounting.busy}
                        startIcon={accounting.busy ? <CircularProgress size={15} color="inherit" /> : <AutorenewRoundedIcon />}
                      >
                        {accounting.busy ? t("quickbooks.connection.openingQuickBooks") : t("quickbooks.connection.signInToQuickBooks")}
                      </Button>
                    </Box>
                  </Box>
                )}

                {connected && (
                  <Box className="qbc-panel">
                    <Box>
                      <span className="qbc-eyebrow accent">{t("quickbooks.connection.stateConnected")}</span>
                      <Typography className="qbc-title">{t("quickbooks.connection.connectedTitle")}</Typography>
                    </Box>
                    <Typography className="qbc-lede">{t("quickbooks.connection.connectedLede")}</Typography>
                    <Box className="qbc-actions">
                      <Button
                        className="qbc-btn qbc-btn-quiet-danger"
                        onClick={() => setConfirmDisconnect(true)}
                        disabled={disabled || accounting.busy}
                        startIcon={<LinkOffRoundedIcon />}
                      >
                        {t("quickbooks.connection.disconnectQuickBooks")}
                      </Button>
                    </Box>
                  </Box>
                )}
              </Box>

              <Box className="qbc-side" aria-label={t("quickbooks.connection.whatSyncsWithQuickBooks")}>
                <Typography className="qbc-side-label">{t("quickbooks.connection.whatSyncsWithQuickBooks")}</Typography>
                <Box className="qbc-side-group" data-ready={connected}>
                  <Box className="qbc-side-group-head">
                    <Typography className="qbc-side-group-name">{t("quickbooks.connection.quickBooksOnline")}</Typography>
                    <span className="qbc-side-group-state">{connected ? t("quickbooks.connection.stateConnected") : t("quickbooks.connection.afterYouConnect")}</span>
                  </Box>
                  <Box className="qbc-side-items">
                    {syncItems.map((item) => (
                      <Box className="qbc-side-item" key={item.id}>
                        <span className="qbc-side-item-icon">{item.icon}</span>
                        <span className="qbc-side-item-name">{item.name}</span>
                        <span className="qbc-side-item-mark">{connected ? <CheckRoundedIcon /> : null}</span>
                      </Box>
                    ))}
                  </Box>
                </Box>
              </Box>
            </Box>
            <Box className="qbc-detail-foot">
              <span className="qbc-foot-item"><LockRoundedIcon /> {t("quickbooks.connection.secureSignInIntuit")}</span>
              <span className="qbc-foot-dot" aria-hidden="true" />
              <span className="qbc-foot-item">{t("quickbooks.connection.disconnectAnytime")}</span>
              <span className="qbc-foot-dot" aria-hidden="true" />
              <span className="qbc-foot-item">{t("quickbooks.connection.worksWithQuickBooksOnline")}</span>
            </Box>
          </Box>
        </Box>
      </Box>

      <Dialog
        open={confirmDisconnect}
        onClose={() => setConfirmDisconnect(false)}
        PaperProps={{ className: "qbc-sheet-paper" }}
      >
        <Box className="qbc-sheet">
          <Box className="qbc-sheet-icon" aria-hidden="true"><LinkOffRoundedIcon /></Box>
          <Typography className="qbc-sheet-title">Disconnect QuickBooks?</Typography>
          <Typography className="qbc-sheet-body">Syncing stops until you connect again.</Typography>
          <Box className="qbc-impact">
            <Box className="qbc-impact-line keep"><CheckRoundedIcon /><span>Records already in QuickBooks <b>stay there</b>.</span></Box>
            <Box className="qbc-impact-line pause"><PauseRoundedIcon /><span>New records <b>stop syncing</b> until you reconnect.</span></Box>
            <Box className="qbc-impact-line keep"><AutorenewRoundedIcon /><span>You can reconnect anytime.</span></Box>
          </Box>
          <Box className="qbc-sheet-actions">
            <Button className="qbc-btn qbc-btn-ghost" onClick={() => setConfirmDisconnect(false)}>Cancel</Button>
            <Button
              className="qbc-btn qbc-btn-danger"
              onClick={() => { setConfirmDisconnect(false); onDisconnectAccounting(); }}
            >
              Disconnect QuickBooks
            </Button>
          </Box>
        </Box>
      </Dialog>
    </Box>
  );
};

export default QuickBooksConnection;

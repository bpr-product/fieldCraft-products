/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * QuickBooks Nexus — Multi-module Sync Dashboard
 */
import React, { useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Skeleton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import QuickBooksRailFooter, { QuickBooksSettingsStatus } from "./QuickBooksRailFooter";
import PaymentsRoundedIcon from "@mui/icons-material/PaymentsRounded";
import LinkIcon from "@mui/icons-material/Link";
import AutorenewRoundedIcon from "@mui/icons-material/AutorenewRounded";
import CheckCircleOutlineRoundedIcon from "@mui/icons-material/CheckCircleOutlineRounded";
import WarningAmberRoundedIcon from "@mui/icons-material/WarningAmberRounded";
import ErrorOutlineRoundedIcon from "@mui/icons-material/ErrorOutlineRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import BusinessRoundedIcon from "@mui/icons-material/BusinessRounded";
import ReceiptLongRoundedIcon from "@mui/icons-material/ReceiptLongRounded";
import AccountBalanceWalletRoundedIcon from "@mui/icons-material/AccountBalanceWalletRounded";
import AccountTreeRoundedIcon from "@mui/icons-material/AccountTreeRounded";
import AttachFileRoundedIcon from "@mui/icons-material/AttachFileRounded";
import ConstructionRoundedIcon from "@mui/icons-material/ConstructionRounded";
import EngineeringRoundedIcon from "@mui/icons-material/EngineeringRounded";
import FolderSpecialRoundedIcon from "@mui/icons-material/FolderSpecialRounded";
import AccessTimeRoundedIcon from "@mui/icons-material/AccessTimeRounded";
// import DeleteOutlineRoundedIcon from "@mui/icons-material/DeleteOutlineRounded"; // Dev-only TimeActivity cleanup disabled for PR
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import Inventory2OutlinedIcon from "@mui/icons-material/Inventory2Outlined";
// Chart-of-accounts record tiles — one glyph per QuickBooks account family.
import AccountBalanceRoundedIcon from "@mui/icons-material/AccountBalanceRounded";
import CreditCardRoundedIcon from "@mui/icons-material/CreditCardRounded";
import TrendingUpRoundedIcon from "@mui/icons-material/TrendingUpRounded";
// QB_SYNC_LABEL/QB_SYNC_STATUS are no longer needed here — the shared
// `QuickBooksStatusBody` owns the status-to-wording mapping for all three dialogs.
import {
  enabledQuickBooksModuleIds,
  formatCurrency,
  isQuickBooksModuleEnabled,
  qbFormatDate,
  qbFormatDateTime,
} from "./QuickBooksUtils";
import QuickBooksConnection, { QbCapStatus } from "./QuickBooksConnection";
import QuickBooksWorkspaceRail from "./QuickBooksWorkspaceRail";
import QuickBooksModuleHeader, { FilterPill } from "./QuickBooksModuleHeader";
import QuickBooksDataTable, { DataColumn } from "./QuickBooksDataTable";
import QuickBooksSyncBanner from "./QuickBooksSyncBanner";
import QuickBooksMissingRecordNote from "./QuickBooksMissingRecordNote";
import { QbStatusCell } from "./QuickBooksStatus";
import { QbIdentityCell } from "./QuickBooksCells";
import "./QuickBooksWorkspace.scss";
import BadgeRoundedIcon from "@mui/icons-material/BadgeRounded";
import {
  disconnectQuickBooks,
  getQuickBooksAutoSyncConfig,
  saveQuickBooksAutoSyncConfig,
  getQuickBooksAccountCdcView,
  getQuickBooksVendorCdcView,
  getQuickBooksBillCdcView,
  assignQuickBooksBillPaymentAccount,
  getQuickBooksBillPaymentAccounts,
  getQuickBooksBillPaymentCdcView,
  getQuickBooksCustomerCdcView,
  getQuickBooksStatus,
  getQuickBooksSyncJob,
  getQuickBooksSyncState,
  initiateQuickBooksAuth,
  startQuickBooksChartOfAccountsSync,
  startQuickBooksVendorsSync,
  startQuickBooksBillsSync,
  startQuickBooksBillPaymentsSync,
  startQuickBooksCustomersSync,
  getQuickBooksProjectCdcView,
  startQuickBooksProjectsSync,
  getQuickBooksInvoiceCdcView,
  getQuickBooksInvoiceDetails,
  downloadQuickBooksInvoiceDocument,
  startQuickBooksInvoicesSync,
  getQuickBooksPaymentCdcView,
  getQuickBooksPaymentDetails,
  startQuickBooksPaymentsSync,
  getQuickBooksProjectSyncConfig,
  saveQuickBooksProjectSyncConfig,
  ProjectSyncConfig,
  ProjectSyncMethod,
  DEFAULT_PROJECT_SYNC_CONFIG,
  getQuickBooksBillLineItems,
  getQuickBooksBillDocumentInfo,
  downloadQuickBooksBillDocument,
  getQuickBooksEmployeeCdcView,
  startQuickBooksEmployeesSync,
  getQuickBooksEmployeeSyncConfig,
  saveQuickBooksEmployeeSyncConfig,
  EmployeeSyncConfig,
  DEFAULT_EMPLOYEE_SYNC_CONFIG,
  getQuickBooksServiceRoleCdcView,
  startQuickBooksServiceRolesSync,
  getQuickBooksTimelogCdcView,
  startQuickBooksTimelogsSync,
  // deleteMappedQuickBooksTimeActivities, // Dev-only TimeActivity cleanup disabled for PR
  getQuickBooksServiceIncomeAccounts,
  getQuickBooksServiceCoaAccounts,
  assignQuickBooksServiceIncomeAccount,
  ServiceRoleCdcRow,
  ServiceRoleIncomeAccount,
  QuickBooksAuthError,
  BillPaymentEligibleAccount,
  BillPaymentCdcRow,
  BillLineItem,
  BillDocumentInfo,
  TimelogCdcRow,
  InvoiceCdcRow,
  InvoiceDetailResponse,
  CustomerPaymentCdcRow,
  CustomerPaymentDetailResponse,
} from "../../../../services/integrations/QuickBooksService";
import { getTenantDetails } from "../../../../services/TenantService";
import { getAllServices, getSubscriberById } from "../../../../services/FeaturesService";
import { useOidcAccessToken } from "@axa-fr/react-oidc";
import { UserContext } from "../../../../hooks/UserSession";
import "./QuickBooksTable.scss";
import { SyncPipelineGuide, ModuleDependencyHint, ModuleSummaryBag, MODULE_DEPENDENCIES } from "./QuickBooksSyncGuide";
import {
  QUICKBOOKS_OAUTH_CALLBACK_TYPE,
  QuickBooksOAuthCallbackMessage,
  quickBooksOAuthErrorMessage,
} from "./QuickBooksOAuthPopup";
import {
  BlockHeading,
  CustomerPaymentDetailDialog,
  DialogCloseAction,
  EmptyState,
  InvoiceDetailDialog,
  NoteStrip,
  QuickBooksStatusBody,
  SummaryCard,
  SummaryCards,
} from "./QuickBooksFinancialDetailDialogs";

// ── Types ─────────────────────────────────────────────────────
type QuickBooksConnectionStatus = {
  connected: boolean;
  status?: string;
  env?: string;
  realmId?: string;
  refreshExpiresAt?: string;
  accessExpiresAt?: string;
  connectedAt?: string;
  reconnectRequired?: boolean;
  authError?: QuickBooksAuthError | null;
  lastSyncAt?: string | null;
  lastVendorSyncAt?: string | null;
  lastPaymentSyncAt?: string | null;
  lastAccountSyncAt?: string | null;
  lastBillSyncAt?: string | null;
  lastBillPaymentSyncAt?: string | null;
  lastCustomerSyncAt?: string | null;
  lastProjectSyncAt?: string | null;
  lastEmployeeSyncAt?: string | null;
  lastServiceSyncAt?: string | null;
  lastTimelogSyncAt?: string | null;
  lastInvoiceSyncAt?: string | null;
};

type VendorRow = {
  vendorId: number | null;
  vendorUuid?: string | null;
  displayName: string;
  companyName?: string | null;
  email?: string | null;
  phone?: string | null;
  website?: string | null;
  vendorStatus?: string | null;
  mappingId?: number | null;
  externalVendorId?: string | null;
  externalAccountId?: string | null;
  syncStatus?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
  source?: "QuickBooks" | "FIELDCRAFT" | null;
};

type VendorSummary = {
  totalVendors: number;
  mappedVendors: number;
  unmappedVendors: number;
  syncedVendors: number;
  pendingPushVendors: number;
  failedVendors: number;
  importFailedVendors: number;
};

type VendorCdcPayload = {
  summary: VendorSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: VendorRow[];
};

type AccountRow = {
  accountId: number;
  name: string;
  accountType?: string | null;
  detailType?: string | null;
  accountNo?: string | null;
  description?: string | null;
  active?: boolean;
  externalId?: string | null;
  syncToken?: string | null;
  serviceId?: string | null;
  externalAccountId?: string | null;
  updatedAt?: string | null;
  syncStatus?: string | null;
  reason?: string | null;
};

type AccountSummary = {
  totalAccounts: number;
  syncedAccounts: number;
  pendingPushAccounts: number;
};

type AccountCdcPayload = {
  summary: AccountSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: AccountRow[];
};

type BillRow = {
  receiptId: number | null;
  receiptUuid?: string | null;
  displayName: string;
  billName?: string | null;
  receiptType?: string | null;
  isStockDebitBill?: boolean | null;
  isStockCreditBill?: boolean | null;
  invoiceNumber?: string | null;
  invoiceDate?: string | null;
  vendorId?: number | null;
  vendorCompany?: string | null;
  category?: string | null;
  chartOfAccountId?: number | null;
  projectId?: number | null;
  projectName?: string | null;
  projectSyncStatus?: string | null;
  externalProjectId?: string | null;
  isStockBill?: boolean | null;
  stockReference?: string | null;
  status?: string | null;
  isPaid?: boolean | null;
  billPaymentCount?: number;
  syncedBillPaymentCount?: number;
  billPaymentSyncRequired?: boolean;
  amount?: number | null;
  mappingId?: number | null;
  externalReceiptId?: string | null;
  externalAccountId?: string | null;
  syncStatus?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
  source?: "QuickBooks" | "FIELDCRAFT" | null;
};

type BillSummary = {
  totalBills: number;
  mappedBills: number;
  unmappedBills: number;
  syncedBills: number;
  pendingPushBills: number;
  failedBills: number;
  importFailedBills: number;
  syncFailedBills: number;
};

type BillCdcPayload = {
  summary: BillSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: BillRow[];
};


type BillPaymentSummary = {
  totalBillPayments: number;
  mappedBillPayments: number;
  unmappedBillPayments: number;
  assignedBillPayments: number;
  unassignedBillPayments: number;
  readyToSyncBillPayments: number;
  syncedBillPayments: number;
  pendingPushBillPayments: number;
  failedBillPayments: number;
  importFailedBillPayments: number;
};

type BillPaymentCdcPayload = {
  summary: BillPaymentSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: BillPaymentCdcRow[];
};

type CustomerRow = {
  customerId: number | null;
  customerUuid?: string | null;
  displayName: string;
  companyName?: string | null;
  email?: string | null;
  phone?: string | null;
  contactName?: string | null;
  customerStatus?: string | null;
  mappingId?: number | null;
  externalCustomerId?: string | null;
  externalAccountId?: string | null;
  syncStatus?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
  source?: "QuickBooks" | "FIELDCRAFT" | null;
};

type CustomerSummary = {
  totalCustomers: number;
  mappedCustomers: number;
  unmappedCustomers: number;
  syncedCustomers: number;
  pendingPushCustomers: number;
  failedCustomers: number;
  importFailedCustomers: number;
  syncFailedCustomers: number;
};

type CustomerCdcPayload = {
  summary: CustomerSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: CustomerRow[];
};

type ProjectRow = {
  projectId: number | null;
  projectUid?: string | null;
  customerUid?: string | null;
  displayName: string;
  description?: string | null;
  orderNo?: string | null;
  projectStatus?: string | null;
  mappingId?: number | null;
  externalProjectId?: string | null;
  externalAccountId?: string | null;
  projectSyncTarget?: "QBO_PROJECT" | "SUB_CUSTOMER" | null;
  syncStatus?: string | null;
  syncToken?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  reason?: string | null;
  source?: "FIELDCRAFT" | null;
};

type ProjectSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  byStatus?: Record<string, number>;
};

type ProjectCdcPayload = {
  summary: ProjectSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: ProjectRow[];
};

type EmployeeRow = {
  employeeId: number | null;
  employeeUuid?: string | null;
  displayName: string;
  firstName?: string | null;
  middleName?: string | null;
  lastName?: string | null;
  email?: string | null;
  phone?: string | null;
  roles?: string | null;
  employeeStatus?: string | null;
  active?: boolean | null;
  mappingId?: number | null;
  externalEmployeeId?: string | null;
  qboEmployeeId?: string | null;
  externalVendorId?: string | null;
  qboVendorId?: string | null;
  externalWorkerId?: string | null;
  quickBooksId?: string | null;
  qboWorkerId?: string | null;
  externalEntityType?: string | null;
  quickBooksEntityType?: string | null;
  qboWorkerEntityType?: string | null;
  employeeType?: string | null;
  workerType?: string | null;
  workerTypeLabel?: string | null;
  workerClassification?: string | null;
  externalAccountId?: string | null;
  syncStatus?: string | null;
  syncToken?: string | null;
  mappingVersion?: number | null;
  endorsementEffectiveDate?: string | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
  source?: "QuickBooks" | "FIELDCRAFT" | null;
};

type EmployeeSummary = {
  totalEmployees: number;
  mappedEmployees: number;
  unmappedEmployees: number;
  syncedEmployees: number;
  pendingPushEmployees: number;
  failedEmployees: number;
  importFailedEmployees: number;
  syncFailedEmployees?: number;
};

type EmployeeCdcPayload = {
  summary: EmployeeSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: EmployeeRow[];
};

type ServiceRoleSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
};

type ServiceRoleCdcPayload = {
  summary: ServiceRoleSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: ServiceRoleCdcRow[];
};

type InvoiceSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  notReady: number;
};

type InvoiceCdcPayload = {
  summary: InvoiceSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: InvoiceCdcRow[];
};

type CustomerPaymentSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  notReady: number;
};

type CustomerPaymentCdcPayload = {
  summary: CustomerPaymentSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: CustomerPaymentCdcRow[];
};

type TimelogSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  notReady: number;
};

type TimelogCdcPayload = {
  summary: TimelogSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: TimelogCdcRow[];
};


type SyncModuleDef = {
  id: string;
  label: string;
  shortLabel: string;
  description: string;
  color: string;
  bgColor: string;
  icon: React.ReactNode;
};

// ── Module Definitions ────────────────────────────────────────
/**
 * Every module the workspace can render, before release gating. Consumers use
 * `SYNC_MODULES` below, which is this list filtered to the released modules — see
 * `isQuickBooksModuleEnabled` in QuickBooksUtils.
 */
const ALL_SYNC_MODULES: SyncModuleDef[] = [
  {
    id: "customers",
    label: "Customers",
    shortLabel: "Customers",
    description: "Keep customer records aligned between FIELDCRAFT and QuickBooks for smoother account and job tracking.",
    color: "#10B981",
    bgColor: "#ECFDF5",
    icon: <PaymentsRoundedIcon />,
  },
  {
    id: "vendors",
    label: "Vendors",
    shortLabel: "Vendors",
    description: "Keep vendor information aligned with QuickBooks to support bills, payments, and AP workflows.",
    color: "#8B5CF6",
    bgColor: "#F5F3FF",
    icon: <BusinessRoundedIcon />,
  },
  {
    id: "bills",
    label: "Bills",
    shortLabel: "Bills",
    description: "Send FIELDCRAFT bills to QuickBooks and track their sync progress from one place.",
    color: "#F59E0B",
    bgColor: "#FFFBEB",
    icon: <ReceiptLongRoundedIcon />,
  },
  {
    id: "invoices",
    label: "Invoices",
    shortLabel: "Invoices",
    description: "Send issued FIELDCRAFT project invoices to QuickBooks and track their export status.",
    color: "#D97706",
    bgColor: "#FFF7ED",
    icon: <ReceiptLongRoundedIcon />,
  },
  {
    id: "payments",
    label: "Customer Payments",
    shortLabel: "Payments",
    description: "Send FIELDCRAFT customer receipts and invoice allocations to QuickBooks Payments.",
    color: "#059669",
    bgColor: "#ECFDF5",
    icon: <AccountBalanceWalletRoundedIcon />,
  },
  {
    id: "bill-payments",
    label: "Bill Payments",
    shortLabel: "Bill Payments",
    description: "Sync bill payment details with QuickBooks to keep payment activity connected to the right bills.",
    color: "#16A34A",
    bgColor: "#F0FDF4",
    icon: <AccountBalanceWalletRoundedIcon />,
  },
  {
    id: "chart-of-accounts",
    label: "Chart of Accounts",
    shortLabel: "Accounts",
    description: "Bring QuickBooks account details into FIELDCRAFT so financial records use the right account structure.",
    color: "#0EA5E9",
    bgColor: "#F0F9FF",
    icon: <AccountTreeRoundedIcon />,
  },
  {
    id: "projects",
    label: "Projects",
    shortLabel: "Projects",
    description: "Send FIELDCRAFT projects to QuickBooks so job-related activity stays connected to the right customer context.",
    color: "#6366F1",
    bgColor: "#EEF2FF",
    icon: <FolderSpecialRoundedIcon />,
  },
  {
    id: "employees",
    label: "Employees",
    shortLabel: "Employees",
    description: "Keep employee records aligned with QuickBooks for time tracking and workforce-related workflows.",
    color: "#0D9488",
    bgColor: "#F0FDFA",
    icon: <BadgeRoundedIcon />,
  },

  {
    id: "timelogs",
    label: "Time Activity",
    shortLabel: "Time",
    description: "Send completed FIELDCRAFT timelogs to QuickBooks Online as daily Time Activity for the mapped worker and project.",
    color: "#2563EB",
    bgColor: "#EFF6FF",
    icon: <AccessTimeRoundedIcon />,
  },
  {
    id: "services",
    label: "Services",
    shortLabel: "Services",
    description: "Send FIELDCRAFT service roles to QuickBooks so time and accounting records can reference the mapped service item.",
    color: "#EA580C",
    bgColor: "#FFF7ED",
    icon: <ConstructionRoundedIcon />,
  },
];

/**
 * The modules actually rendered. Filtering here covers every consumer at once — the
 * module selector, the landing preview tiles and `activeModuleDef` — so a module behind
 * an off flag has no entry point. `selectedModule` is plain component state seeded with
 * "customers" and only ever set from these rendered controls, so a gated module cannot
 * become the active one.
 */
const SYNC_MODULES: SyncModuleDef[] = ALL_SYNC_MODULES.filter((mod) =>
  isQuickBooksModuleEnabled(mod.id),
);

// ── Constants ─────────────────────────────────────────────────
const OAUTH_POPUP_TIMEOUT_MS = 5 * 60 * 1000;
const ACTIVE_JOB_STATUSES = new Set(["RUNNING", "IN_PROGRESS", "STARTED", "PENDING"]);
/**
 * Stand-in job status shown in the sync banner between the click and the real job
 * arriving. It is display-only — never sent to or compared against backend statuses.
 */
const SYNC_STARTING_STATUS = "Starting";
// const QBO_DESTRUCTIVE_MAINTENANCE_ENABLED =
//   String(process.env.REACT_APP_QBO_DESTRUCTIVE_MAINTENANCE_ENABLED || "")
//     .trim()
//     .toLowerCase() === "true"; // Dev-only TimeActivity cleanup disabled for PR
const DONE_JOB_STATUSES = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "DONE", "FINISHED", "FAILED"]);
const RECONNECT_REQUIRED_STATUSES = new Set(["reauthorization_required", "reconnect_required"]);

const emptyVendorSummary: VendorSummary = {
  totalVendors: 0, mappedVendors: 0, unmappedVendors: 0,
  syncedVendors: 0, pendingPushVendors: 0, failedVendors: 0, importFailedVendors: 0,
};

const emptyAccountSummary: AccountSummary = {
  totalAccounts: 0, syncedAccounts: 0, pendingPushAccounts: 0,
};

const emptyBillSummary: BillSummary = {
  totalBills: 0, mappedBills: 0, unmappedBills: 0,
  syncedBills: 0, pendingPushBills: 0, failedBills: 0, importFailedBills: 0, syncFailedBills: 0,
};

const emptyBillPaymentSummary: BillPaymentSummary = {
  totalBillPayments: 0, mappedBillPayments: 0, unmappedBillPayments: 0,
  assignedBillPayments: 0, unassignedBillPayments: 0, readyToSyncBillPayments: 0,
  syncedBillPayments: 0, pendingPushBillPayments: 0, failedBillPayments: 0, importFailedBillPayments: 0,
};

const emptyCustomerSummary: CustomerSummary = {
  totalCustomers: 0, mappedCustomers: 0, unmappedCustomers: 0,
  syncedCustomers: 0, pendingPushCustomers: 0, failedCustomers: 0, importFailedCustomers: 0, syncFailedCustomers: 0,
};

const emptyProjectSummary: ProjectSummary = {
  total: 0, synced: 0, pending: 0, failed: 0, byStatus: {},
};

const emptyEmployeeSummary: EmployeeSummary = {
  totalEmployees: 0, mappedEmployees: 0, unmappedEmployees: 0,
  syncedEmployees: 0, pendingPushEmployees: 0, failedEmployees: 0, importFailedEmployees: 0, syncFailedEmployees: 0,
};

const emptyServiceRoleSummary: ServiceRoleSummary = {
  total: 0, synced: 0, pending: 0, failed: 0,
};

const emptyInvoiceSummary: InvoiceSummary = {
  total: 0, synced: 0, pending: 0, failed: 0, notReady: 0,
};

const emptyCustomerPaymentSummary: CustomerPaymentSummary = {
  total: 0, synced: 0, pending: 0, failed: 0, notReady: 0,
};

const emptyTimelogSummary: TimelogSummary = {
  total: 0, synced: 0, pending: 0, failed: 0, notReady: 0,
};


// ── Helpers ────────────────────────��──────────────────────────
const moduleThemeClass = (id?: string | null) => `qb-theme-${String(id || "default").toLowerCase()}`;
/**
 * Date-only rendering for the date columns (Invoice Date, Payment Date) and the bill
 * dialog — UTC-safe, and the same US numeric format as the financial dialogs.
 */
const formatDateOnlyUtc = (value?: string | null) => qbFormatDate(value) || "—";


const getQuickBooksApiPayload = (error: any) => error?.response?.data || {};
const getQuickBooksErrorMessage = (error: any) => {
  const payload = getQuickBooksApiPayload(error);
  return payload?.detail || payload?.message || error?.message || "QuickBooks request failed.";
};
const isTransientPollError = (error: any): boolean => {
  const msg: string = error?.message ?? "";
  return msg.includes("Failed to fetch") || msg.includes("Network Error");
};

// Distinct error class so catch blocks can tell a plan-gate apart from a real failure.
class QBOPlanError extends Error {}

// Throws a QBOPlanError when the backend returns a feature_not_available 403.
// executePostDataWithHeaders catches axios 403s and puts the backend message in res.message;
// res.data is undefined in that path, so we check res.status/res.ok, not res.data.error.
const assertNotFeatureBlocked = (res: any): void => {
  const payload = res?.data && typeof res.data === "object" ? res.data : res || {};
  const status = payload.status ?? res?.status;
  const isFeatureBlocked = payload.error === "feature_not_available" || status === 403;
  if (!payload.ok && isFeatureBlocked) {
    throw new QBOPlanError(payload.message ?? "This feature is not available on your current QuickBooks plan.");
  }
};
const isQuickBooksReconnectResponse = (payload: any) => {
  const errorCode = String(payload?.error || payload?.authError?.code || "").toLowerCase();
  const status = String(payload?.status || "").toLowerCase();
  return Boolean(
    payload?.reconnectRequired || payload?.authError ||
    RECONNECT_REQUIRED_STATUSES.has(status) ||
    ["invalid_grant", "refresh_token_expired", "access_token_expired"].includes(errorCode)
  );
};
/**
 * Was a hand-rolled `Intl.DateTimeFormat("en-GB", …)` that rendered `19/08/2026, 11:03 am`
 * — day/month order, and a raw `new Date(...)` parse that bypassed the UTC boundary. Both
 * are wrong here: this is a US accounting workspace, and the "Last Synced" column sat next
 * to a detail dialog formatting the same instant differently. Now shares one formatter with
 * the dialogs. Keeps the "—" fallback so every call site is unchanged apart from the format.
 */
const formatDateTime = (value?: string | null) => qbFormatDateTime(value) || "—";
const formatDuration = (seconds?: number | null, minutes?: number | null) => {
  const rawSeconds = seconds ?? (minutes === null || minutes === undefined ? null : Number(minutes) * 60);
  if (rawSeconds === null || Number.isNaN(Number(rawSeconds))) return "—";

  const totalSeconds = Math.max(0, Math.floor(Number(rawSeconds)));
  const hours = Math.floor(totalSeconds / 3600);
  const remainingSeconds = totalSeconds % 3600;
  const wholeMinutes = Math.floor(remainingSeconds / 60);
  const wholeSeconds = remainingSeconds % 60;

  if (hours) return `${hours}h ${wholeMinutes}m ${wholeSeconds}s`;
  if (wholeMinutes) return `${wholeMinutes}m ${wholeSeconds}s`;
  return `${wholeSeconds}s`;
};

/**
 * Start over End, as a two-column grid so the two timestamps line up under each other
 * instead of sitting at the ragged edge of labels of different widths.
 *
 * One line was cheaper and did not fit: this renders into the identity cell's sub line,
 * which is capped at 240px with `text-overflow: ellipsis`, so a single
 * "Start: … • End: …" run got cut mid-value ("• End: 08/20/2026 0") — losing exactly the
 * half a reader checking a shift came for. Stacked, each line clears the cap on its own.
 */
const renderTimelogTimeRange = (startTime?: string | null, endTime?: string | null) => (
  <span className="qbw-timewin">
    <span className="qbw-timewin-lbl">Start:</span>
    <span>{formatDateTime(startTime)}</span>
    <span className="qbw-timewin-lbl">End:</span>
    <span>{formatDateTime(endTime)}</span>
  </span>
);

const parseReasonMessage = (reason?: string | null): string => {
  if (!reason) return "";
  try {
    const parsed = JSON.parse(reason);
    return parsed?.message ?? reason;
  } catch {
    return reason;
  }
};

const isProjectSyncError = (row: { reason?: string | null; syncStatus?: string | null }): boolean => {
  if (!row.reason) return false;
  const msg = parseReasonMessage(row.reason).toLowerCase();
  return msg.includes("project") && (msg.includes("sync") || msg.includes("not mapped") || msg.includes("mapped to a fieldcraft"));
};

const statusLabel = (value?: string | null, linked?: boolean) => {
  const s = String(value || "").toUpperCase();
  if (s === "SYNCED") return "Synced";
  if (s === "ASSIGNMENT_NEEDED") return "Assignment needed";
  if (s === "PENDING_PUSH") return "Pending";
  if (s === "FAILED") return "Failed";
  if (s === "CONFLICT") return "Conflict";
  if (s === "IMPORT_FAILED") return "Import Failed";
  if (s === "SYNC_FAILED") return "Sync Failed";
  if (s === "NOT_READY") return "Not Ready";
  if (s === "SYNCED_INCOMPLETE") return "Needs Project Link";
  if (s === "AWAITING_APPROVAL") return "Awaiting Approval";
  if (!linked) return "Not linked";
  return "Unknown";
};

const normalizeBillType = (value?: string | null): string => String(value || "").trim().toLowerCase();

const isFieldCraftStockDebitBill = (row?: BillRow | null): boolean => {
  if (!row || row.receiptId == null || row.source !== "FIELDCRAFT") return false;
  const type = normalizeBillType(row.receiptType || row.billName);
  return row.isStockDebitBill === true || type === "stockdebitbill" || type === "stock debit bill";
};

// A stock return: material dispatched to a project coming back into inventory. Exports to
// QuickBooks as the reverse journal entry of the StockDebitBill, so it is inventory too.
const isFieldCraftStockCreditBill = (row?: BillRow | null): boolean => {
  if (!row || row.receiptId == null || row.source !== "FIELDCRAFT") return false;
  const type = normalizeBillType(row.receiptType || row.billName);
  return row.isStockCreditBill === true || type === "stockcreditbill" || type === "stock credit bill";
};

// Mirrors the backend rule (ReceiptRepository.is_stock_receipt): a bill is inventory only
// when it carries an explicit stock receipt type or a stock reference. A missing project
// alone is NOT enough — ordinary overhead bills can also be unassigned.
const isFieldCraftStockBill = (row?: BillRow | null): boolean => {
  if (!row || row.receiptId == null || row.source !== "FIELDCRAFT") return false;
  return row.isStockBill === true || Boolean(row.stockReference)
    || isFieldCraftStockDebitBill(row) || isFieldCraftStockCreditBill(row);
};

const stockBillLabel = (row?: BillRow | null): string => {
  if (isFieldCraftStockDebitBill(row)) return "Stock debit bill";
  if (isFieldCraftStockCreditBill(row)) return "Stock credit bill";
  return "Stock bill";
};

/** Short form of {@link stockBillLabel} for the row badge. */
const stockBillBadgeLabel = (row?: BillRow | null): string => {
  if (isFieldCraftStockDebitBill(row)) return "Stock debit";
  if (isFieldCraftStockCreditBill(row)) return "Stock credit";
  return "Stock";
};

const isStockBillProjectLinkBlocked = (row?: BillRow | null): boolean => {
  if (!isFieldCraftStockBill(row)) return false;
  const message = parseReasonMessage(row?.reason).toLowerCase();
  return (message.includes("stock bill") || message.includes("stock debit bill")) && (
    message.includes("cannot be linked to a project") ||
    message.includes("project/customer link") ||
    message.includes("quickbooks link")
  );
};

const billStatusLabel = (row: BillRow): string => {
  if (isStockBillProjectLinkBlocked(row)) return "Project link ignored";
  if (isFieldCraftStockBill(row) && String(row.syncStatus || "").toUpperCase() === "SYNCED_INCOMPLETE") {
    return "Stock bill review";
  }
  return statusLabel(row.syncStatus, row.linked);
};

const fieldcraftStatusLabel = (status?: string | null): string | null => {
  const s = String(status || "").toUpperCase();
  if (s === "ACTIVE") return "Active";
  if (s === "INACTIVE") return "Inactive";
  if (s === "INVITED") return "Invited";
  return null;
};

/**
 * Glyph for a chart-of-accounts row's record tile. Accounts are the one table whose rows
 * are genuinely different kinds of thing, so the tile earns its place by showing the
 * account family rather than repeating one icon down the column. Matched on the QuickBooks
 * `AccountType` family, which is free text from the provider — hence substring matching
 * and an explicit fallback rather than an exhaustive map.
 */
const accountTypeIcon = (accountType?: string | null): React.ReactNode => {
  const t = String(accountType || "").toLowerCase();
  if (t.includes("bank")) return <AccountBalanceRoundedIcon />;
  if (t.includes("credit card")) return <CreditCardRoundedIcon />;
  if (t.includes("income") || t.includes("revenue")) return <TrendingUpRoundedIcon />;
  if (t.includes("expense") || t.includes("cost of goods")) return <ReceiptLongRoundedIcon />;
  if (t.includes("asset")) return <Inventory2OutlinedIcon />;
  return <AccountTreeRoundedIcon />;
};

// Resolves role display name across backend field-name variants
const resolveServiceRoleName = (row: ServiceRoleCdcRow): string =>
  row.roleName || row.role_name || row.name || "Unnamed role";

// Resolves externalServiceId across backend field-name variants
const resolveServiceRoleExternalId = (row: ServiceRoleCdcRow): string | null | undefined =>
  row.externalServiceId || row.external_service_id;

// Resolves externalDisplayName across backend field-name variants
const resolveServiceRoleExternalDisplayName = (row: ServiceRoleCdcRow): string | null | undefined =>
  row.externalDisplayName || row.external_display_name;

// Resolves syncStatus across backend field-name variants
const resolveServiceRoleSyncStatus = (row: ServiceRoleCdcRow): string | null | undefined =>
  row.syncStatus || row.sync_status;

// Prefers lastSyncedAt, falls back to mappingUpdatedAt or updatedAt
const resolveServiceRoleLastSynced = (row: ServiceRoleCdcRow): string | null | undefined =>
  row.lastSyncedAt || row.mappingUpdatedAt || row.updatedAt;

// Safely parses additionalInfo (object or JSON string) and returns the role-level COA override
const resolveServiceRoleIncomeAccount = (row: ServiceRoleCdcRow): Record<string, any> | null => {
  try {
    const raw = row.additionalInfo ?? (row as any).additional_info ?? null;
    const info = typeof raw === "string" ? JSON.parse(raw) : raw;
    return info?.qbo_service_income_account ?? null;
  } catch {
    return null;
  }
};

// Normalises a raw COA object from the backend — handles camelCase and snake_case field variants.
// Empty objects like {} are not valid configuration and must not unlock service sync.
const normalizeServiceCoaAccount = (raw: any): ServiceRoleIncomeAccount | null => {
  if (!raw || typeof raw !== "object") return null;
  const id = Number(raw.id ?? raw.chart_of_account_id ?? raw.chartOfAccountId ?? 0);
  const externalAccountId = String(raw.externalAccountId ?? raw.external_account_id ?? raw.externalId ?? raw.external_id ?? "").trim();
  if (!id || !externalAccountId) return null;
  return {
    id,
    name: String(raw.name ?? raw.accountName ?? raw.account_name ?? "").trim(),
    accountType: raw.accountType || raw.account_type || null,
    detailType: raw.detailType || raw.detail_type || null,
    externalAccountId,
    syncStatus: raw.syncStatus || raw.sync_status || null,
  };
};

// Returns the QuickBooks external account ID from a normalised COA account
const resolveServiceCoaExternalId = (account: ServiceRoleIncomeAccount | null): string | null => {
  const externalId = account?.externalAccountId ? String(account.externalAccountId).trim() : "";
  return externalId || null;
};

const isServiceCoaConfigured = (account: ServiceRoleIncomeAccount | null): boolean =>
  resolveServiceCoaExternalId(account) !== null;

// Parses failure reason JSON — extracts message + optional action_required
const parseServiceRoleReason = (reason?: string | null): { message: string; actionRequired: string | null } => {
  if (!reason) return { message: "", actionRequired: null };
  try {
    const parsed = JSON.parse(reason);
    return {
      message: parsed?.message ?? reason,
      actionRequired: parsed?.action_required ?? null,
    };
  } catch {
    return { message: reason, actionRequired: null };
  }
};

const renderRows = (items: BillLineItem[]) => items.map((item: BillLineItem, idx: number) => {
  const isLast = idx === items.length - 1;
  const itemKey = `${item.description ?? ""}-${item.productCode ?? ""}-${idx}`;
  const showItemPricing = item.lineKind !== "category";
  return (
    <Box key={itemKey} className={`qb-fin-table-row${isLast ? " is-last" : ""}`}>
      <Box minWidth={0}>
        <Typography className="qb-fin-row-title">
          {item.description || item.productCode || "Unnamed item"}
        </Typography>
        {item.productCode && item.description && (
          <Typography className="qb-fin-row-sub">{item.productCode}</Typography>
        )}
      </Box>
      {/* A category line is a roll-up, so its quantity/unit cost are meaningless even
          when the payload carries them — hence `showItemPricing` rather than a null check. */}
      <Typography className="qb-fin-row-sub qb-fin-cell-qty">
        {showItemPricing && item.quantity != null
          ? Number(item.quantity).toLocaleString(undefined, { maximumFractionDigits: 4 })
          : "—"}
      </Typography>
      <Typography className="qb-fin-row-sub qb-fin-cell-unit">
        {showItemPricing && item.unitPrice != null ? formatCurrency(Number(item.unitPrice)) : "—"}
      </Typography>
      {/* A credit reads green, a cost reads in the bill's amber — the sign is the
          fastest thing to scan for in a cost list. */}
      <Typography className={`qb-fin-row-amount ${item.amount < 0 ? "qb-fin-amount-credit" : "qb-fin-amount-cost"}`}>
        {formatCurrency(item.amount)}
      </Typography>
    </Box>
  );
});

// ── QuickBooks Logo — accurate circle mark ──────────────��─────
const QBLogoMark: React.FC<{ size?: number }> = ({ size = 54 }) => (
  <svg
    width={size} height={size} viewBox="0 0 100 100" fill="none"
    xmlns="http://www.w3.org/2000/svg" className="qb-sx-001"
  >
    {/* Green circle */}
    <circle cx="50" cy="50" r="50" fill="#2CA01C" />
    {/* q — round bowl (left) + descender going down-right */}
    <circle cx="34" cy="44" r="13.5" stroke="white" strokeWidth="5" fill="none" />
    <line x1="47.5" y1="47" x2="47.5" y2="65" stroke="white" strokeWidth="5" strokeLinecap="round" />
    {/* b — ascending stem (left) + round bowl (right) */}
    <line x1="52.5" y1="35" x2="52.5" y2="65" stroke="white" strokeWidth="5" strokeLinecap="round" />
    <circle cx="66" cy="56" r="13.5" stroke="white" strokeWidth="5" fill="none" />
  </svg>
);

// ── Module Selector Card ──────────────────────────────────────
// ── Workspace rail: one quiet module selector, grouped by connection.
const RAIL_GROUPS: { label: string; ids: string[] }[] = [
  { label: "Accounting", ids: ["customers", "vendors", "chart-of-accounts", "bills", "invoices", "payments", "bill-payments"] },
  { label: "Workforce", ids: ["projects", "services", "employees", "timelogs"] },
];
const RAIL_LABEL_OVERRIDES: Record<string, string> = {
  "chart-of-accounts": "Chart of Accounts",
  "bill-payments": "Bill Payments",
  payments: "Customer Payments",
  timelogs: "Time Activity",
};
function buildRailGroups() {
  // The rail keeps its own id list (it groups and relabels them differently), so it needs
  // the same release filter. A group left with no released modules is dropped rather than
  // rendered as an empty heading.
  return RAIL_GROUPS.map((group) => ({
    label: group.label,
    modules: enabledQuickBooksModuleIds(group.ids).map((id) => {
      const def = SYNC_MODULES.find((m) => m.id === id);
      return { id, label: RAIL_LABEL_OVERRIDES[id] || def?.label || id, icon: def?.icon ?? null };
    }),
  })).filter((group) => group.modules.length > 0);
}

const ModuleCard: React.FC<{
  module: SyncModuleDef;
  active: boolean;
  onClick: () => void;
}> = ({ module, active, onClick }) => (
  <Card
    data-testid={`qb-module-card-${module.id}`}
    className={`qb-module-card ${moduleThemeClass(module.id)} ${active ? "active" : ""} live`}
    onClick={onClick}
  >
    <Box className="qb-module-icon-wrap">
      {module.icon}
    </Box>
    <Typography className="qb-module-name">{module.shortLabel}</Typography>
    <Box className="qb-module-live-badge">
      <Box className="qb-module-live-dot" />
      <span>Live</span>
    </Box>
  </Card>
);

// ── Not Connected Landing ─────────────────────────────────────
const NotConnectedLanding: React.FC = () => (
  <Box className="qb-landing-wrap">

    {/* Hero CTA card */}
    <Box className="qb-landing-hero-card">
      <QBLogoMark size={80} />
      <Typography className="qb-landing-headline">Connect QuickBooks AP Operations to FIELDCRAFT</Typography>
      <Typography className="qb-landing-body">
        Manage vendors, chart of accounts, bills, and bill payments from one focused QuickBooks workspace built for accounts payable teams.
      </Typography>
    </Box>

    {/* How it works */}
    <Box className="qb-landing-how">
      <Typography className="qb-landing-section-label">How it works</Typography>
      <Grid container spacing={2} className="qb-sx-009">
        {[
          { n: "1", text: "Authorize QuickBooks Online once to unlock your AP sync workspace." },
          { n: "2", text: "Review vendor, account, and bill readiness with clear mapping visibility." },
          { n: "3", text: "Run syncs in the background while FIELDCRAFT keeps AP data aligned with QuickBooks." },
        ].map(({ n, text }) => (
          <Grid item xs={12} md={4} key={n}>
            <Box className="qb-landing-step">
              <Box className="qb-landing-step-num">{n}</Box>
              <Typography className="qb-landing-step-text">{text}</Typography>
            </Box>
          </Grid>
        ))}
      </Grid>
    </Box>

    {/* Module preview */}
    <Box className="qb-landing-modules">
      <Typography className="qb-landing-section-label">What gets synced</Typography>
      <Grid container spacing={1.5} className="qb-sx-010">
        {SYNC_MODULES.map((mod) => (
          <Grid item xs={6} sm={3} key={mod.id}>
            <Box className="qb-landing-mod-card">
              <Box className={`qb-landing-mod-icon ${moduleThemeClass(mod.id)}`}>
                {mod.icon}
              </Box>
              <Typography className="qb-landing-mod-name">{mod.label}</Typography>
              <Typography className="qb-landing-mod-desc">{mod.description}</Typography>
              <Chip size="small" icon={<CheckCircleOutlineRoundedIcon className="qb-sx-012" />}
                label="Ready now" color="success" className="qb-sx-011" />
            </Box>
          </Grid>
        ))}
      </Grid>
    </Box>
  </Box>
);

// ── Reconnect Required Card ─��─────────────────────────────────
const ReconnectCard: React.FC<{
  onReconnect: () => void;
  loading: boolean;
  message: string;
}> = ({ onReconnect, loading, message }) => (
  <Box className="qb-reconnect-wrap">
    <Card className="qb-reconnect-card" variant="outlined">
      <CardContent className="qb-sx-015">
        <Box className="qb-reconnect-logo-wrap">
          <QBLogoMark size={64} />
          <Box className="qb-reconnect-warn-dot">
            <WarningAmberRoundedIcon className="qb-sx-016" />
          </Box>
        </Box>
        <Typography className="qb-reconnect-title">QuickBooks Connection Expired</Typography>
        <Typography className="qb-reconnect-body">{message}</Typography>
        <Button
          id="qb-btn-reconnect"
          className="qb-btn-connect"
          variant="contained"
          onClick={onReconnect}
          disabled={loading}
          startIcon={loading ? <CircularProgress size={17} color="inherit" /> : <LinkIcon />}
        >
          {loading ? "Reconnecting…" : "Reconnect QuickBooks"}
        </Button>
        <Typography variant="body2" color="text.secondary" className="qb-sx-017">
          Your data is safe. Reconnecting simply re-authorizes FIELDCRAFT with QuickBooks and restores all sync modules.
        </Typography>
      </CardContent>
    </Card>
  </Box>
);

const toCount = (value: any): number => {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
};

const getModuleNeedsAttentionCount = (moduleId: string, summary: any): number => {
  const s = summary || {};
  switch (moduleId) {
    case "vendors":
      return toCount(s.failedVendors) + toCount(s.importFailedVendors);
    case "accounts":
      return 0;
    case "bills":
      return toCount(s.failedBills) + toCount(s.importFailedBills) + toCount(s.syncFailedBills);
    case "billPayments":
      return toCount(s.failedBillPayments) + toCount(s.importFailedBillPayments);
    case "customers":
      return toCount(s.failedCustomers) + toCount(s.importFailedCustomers) + toCount(s.syncFailedCustomers);
    case "projects":
      return toCount(s.failed);
    case "invoices":
      return toCount(s.failed);
    case "payments":
      return toCount(s.failed);
    case "employees":
      return toCount(s.failedEmployees) + toCount(s.importFailedEmployees) + toCount(s.syncFailedEmployees);
    case "services":
      return toCount(s.failed);
    case "timelogs":
      return toCount(s.failed) + toCount(s.notReady);
    default:
      return 0;
  }
};

const resolveWorkerTypeLabelFromRow = (row: Record<string, any>): "W2" | "1099" | null => {
  const raw = String(
    row.workerTypeLabel ??
    row.workerClassification ??
    row.workerType ??
    row.employeeType ??
    row.resourceType ??
    row.qboWorkerEntityType ??
    row.workerEntityType ??
    row.quickBooksEntityType ??
    row.externalEntityType ??
    ""
  ).trim().toUpperCase();

  if (!raw) return null;
  if (raw.includes("1099") || raw === "VENDOR" || raw.includes("CONTRACTOR")) return "1099";
  if (raw.includes("W2") || raw === "EMPLOYEE") return "W2";
  return null;
};

const resolveTimelogWorkerTypeLabel = (row: TimelogCdcRow): "W2" | "1099" | null => resolveWorkerTypeLabelFromRow(row as any);
const resolveEmployeeWorkerTypeLabel = (row: EmployeeRow): "W2" | "1099" | null => resolveWorkerTypeLabelFromRow(row as any);

function buildSyncToast(
  label: string,
  status: string,
  moduleId: string,
  summary: any
): { type: "success" | "warning" | "error"; message: string } {
  if (status === "FAILED") {
    return { type: "error", message: `${label} sync failed. Please try again or check your connection.` };
  }

  const needsAttentionCount = getModuleNeedsAttentionCount(moduleId, summary);
  if (needsAttentionCount > 0) {
    const noun = needsAttentionCount === 1 ? "record" : "records";
    const verb = needsAttentionCount === 1 ? "needs" : "need";
    return { type: "warning", message: `${label} sync completed — ${needsAttentionCount} ${noun} ${verb} attention.` };
  }

  return { type: "success", message: `${label} sync completed.` };
}

/**
 * Calm placeholder shown while QuickBooks status resolves on first paint. It mirrors
 * the connected workspace (rail + module header + table) using the real `.qbw-*`
 * layout, so the UI reads as "materializing in place" instead of flashing a spinner
 * and a progress bar. Once status + data are ready, the real workspace replaces it.
 */
const WorkspaceSkeleton: React.FC = () => {
  const railItems = [0, 1, 2, 3, 4, 5];
  const rows = [0, 1, 2, 3, 4, 5, 6, 7];
  return (
    <Box className="qbw-root qbw-embedded qbw-skeleton" role="status" aria-busy="true" aria-label="Loading QuickBooks">
      <aside className="qbw-rail">
        <div className="qbw-skel-railhead">
          <Skeleton variant="rounded" width={30} height={30} />
          <div className="qbw-skel-lines">
            <Skeleton variant="text" width={82} height={14} />
            <Skeleton variant="text" width={54} height={11} />
          </div>
        </div>
        {railItems.map((i) => (
          <div className="qbw-skel-mod" key={i}>
            <Skeleton variant="rounded" width={18} height={18} />
            <Skeleton variant="text" width={`${58 + ((i * 13) % 30)}%`} height={13} />
          </div>
        ))}
      </aside>

      <div className="qbw-work">
        <div className="qbw-sticky">
          <div className="qbw-skel-whead">
            <div className="qbw-skel-whead-title">
              <Skeleton variant="rounded" width={30} height={30} />
              <div className="qbw-skel-lines">
                <Skeleton variant="text" width={148} height={20} />
                <Skeleton variant="text" width={224} height={12} />
              </div>
            </div>
            <Skeleton variant="rounded" width={150} height={34} />
          </div>
          <div className="qbw-skel-toolbar">
            <Skeleton variant="rounded" width={264} height={34} />
            <div className="qbw-skel-pills">
              {[64, 74, 82, 118].map((w, i) => <Skeleton key={i} variant="rounded" width={w} height={28} />)}
            </div>
          </div>
        </div>
        <div className="qbw-table-region">
          <div className="qbw-skel-table">
            {rows.map((i) => (
              <div className="qbw-skel-row" key={i} style={{ animationDelay: `${(0.03 * i).toFixed(2)}s` }}>
                <div className="qbw-skel-idc">
                  <Skeleton variant="circular" width={30} height={30} />
                  <div className="qbw-skel-lines">
                    <Skeleton variant="text" width={`${54 + ((i * 17) % 34)}%`} height={14} />
                    <Skeleton variant="text" width={`${32 + ((i * 11) % 20)}%`} height={11} />
                  </div>
                </div>
                <Skeleton variant="text" className="qbw-skel-col" width={124} height={14} />
                <Skeleton variant="rounded" className="qbw-skel-col" width={92} height={22} />
                <Skeleton variant="text" className="qbw-skel-col" width={84} height={14} />
                <Skeleton variant="text" className="qbw-skel-col" width={96} height={13} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </Box>
  );
};

// ── Main Component ────────────────────────────────────────────
const QuickBooksTable: React.FC = () => {
  const { accessToken } = useOidcAccessToken();
  const { userSessionDetails } = useContext(UserContext);
  const objectId = userSessionDetails?.objectId;

  const [tenantId, setTenantId] = useState<string | null>(null);
  const [serviceId, setServiceId] = useState<string | null>(null);
  const [connection, setConnection] = useState<QuickBooksConnectionStatus>({ connected: false, reconnectRequired: false, authError: null });
  const [autoSyncEnabled, setAutoSyncEnabled] = useState<boolean | null>(null); // null until loaded; the service default is off
  const [autoSyncStatus, setAutoSyncStatus] = useState<QuickBooksSettingsStatus>("idle");
  const [connectionAction, setConnectionAction] = useState<"connecting" | "disconnecting" | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [syncStateLoading, setSyncStateLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "warning" | "error"; message: string } | null>(null);
  const [selectedModule, setSelectedModule] = useState("customers");
  const [loadedModules, setLoadedModules] = useState<Set<string>>(new Set());
  // The "How it works" guide is reference material, so it opens on demand from the
  // rail (or from the module's attachment strip) instead of occupying the workspace.
  const [syncGuideOpen, setSyncGuideOpen] = useState(false);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  // Id of the module whose sync request is in flight (start POST + sync-state refresh).
  // A job id/status only arrives once that round trip finishes, so this is what makes the
  // Sync button and banner respond on the very click instead of a beat later.
  const [startingSyncModule, setStartingSyncModule] = useState<string | null>(null);

  const [vendorJobId, setVendorJobId] = useState<number | null>(null);
  const [vendorJobStatus, setVendorJobStatus] = useState<string | null>(null);
  const [accountJobId, setAccountJobId] = useState<number | null>(null);
  const [accountJobStatus, setAccountJobStatus] = useState<string | null>(null);

  // Vendor table state
  const [vendorTableLoading, setVendorTableLoading] = useState(false);
  const [vendorCdcData, setVendorCdcData] = useState<VendorCdcPayload>({
    summary: emptyVendorSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [vendorPage, setVendorPage] = useState(0);
  const [vendorPageSize, setVendorPageSize] = useState(10);
  const [vendorSearchInput, setVendorSearchInput] = useState("");
  const [vendorSearchTerm, setVendorSearchTerm] = useState("");
  const [vendorSyncFilter, setVendorSyncFilter] = useState("ALL");
  const [vendorSyncMode, setVendorSyncMode] = useState<"cdc" | "full">("cdc");

  // Account table state
  const [accountTableLoading, setAccountTableLoading] = useState(false);
  const [accountCdcData, setAccountCdcData] = useState<AccountCdcPayload>({
    summary: emptyAccountSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [accountPage, setAccountPage] = useState(0);
  const [accountPageSize, setAccountPageSize] = useState(10);
  const [accountSearchInput, setAccountSearchInput] = useState("");
  const [accountSearchTerm, setAccountSearchTerm] = useState("");
  const [accountSyncFilter, setAccountSyncFilter] = useState("ALL");

  // Bill table state
  const [billTableLoading, setBillTableLoading] = useState(false);
  const [billCdcData, setBillCdcData] = useState<BillCdcPayload>({
    summary: emptyBillSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [billPage, setBillPage] = useState(0);
  const [billPageSize, setBillPageSize] = useState(10);
  const [billSearchInput, setBillSearchInput] = useState("");
  const [billSearchTerm, setBillSearchTerm] = useState("");
  const [billSyncFilter, setBillSyncFilter] = useState("ALL");
  const [billSortMode, setBillSortMode] = useState("UPDATED_DESC");
  const [billSyncMode, setBillSyncMode] = useState<"cdc" | "full">("cdc");
  const [billJobId, setBillJobId] = useState<number | null>(null);
  const [billJobStatus, setBillJobStatus] = useState<string | null>(null);

  const [billPaymentTableLoading, setBillPaymentTableLoading] = useState(false);
  const [billPaymentCdcData, setBillPaymentCdcData] = useState<BillPaymentCdcPayload>({
    summary: emptyBillPaymentSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [billPaymentPage, setBillPaymentPage] = useState(0);
  const [billPaymentPageSize, setBillPaymentPageSize] = useState(10);
  const [billPaymentSearchInput, setBillPaymentSearchInput] = useState("");
  const [billPaymentSearchTerm, setBillPaymentSearchTerm] = useState("");
  const [billPaymentSyncFilter, setBillPaymentSyncFilter] = useState("ALL");
  const [billPaymentSyncMode, setBillPaymentSyncMode] = useState<"cdc" | "full">("cdc");
  const [billPaymentJobId, setBillPaymentJobId] = useState<number | null>(null);
  const [billPaymentJobStatus, setBillPaymentJobStatus] = useState<string | null>(null);
  const [billPaymentEligibleAccounts, setBillPaymentEligibleAccounts] = useState<BillPaymentEligibleAccount[]>([]);
  const [selectedBillPaymentIds, setSelectedBillPaymentIds] = useState<number[]>([]);
  const [assignDialogOpen, setAssignDialogOpen] = useState(false);
  const [selectedBillPaymentAccountId, setSelectedBillPaymentAccountId] = useState("");
  const [assignBillPaymentLoading, setAssignBillPaymentLoading] = useState(false);

  // Customer table state
  const [customerTableLoading, setCustomerTableLoading] = useState(false);
  const [customerCdcData, setCustomerCdcData] = useState<CustomerCdcPayload>({
    summary: emptyCustomerSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [customerPage, setCustomerPage] = useState(0);
  const [customerPageSize, setCustomerPageSize] = useState(10);
  const [customerSearchInput, setCustomerSearchInput] = useState("");
  const [customerSearchTerm, setCustomerSearchTerm] = useState("");
  const [customerSyncFilter, setCustomerSyncFilter] = useState("ALL");
  const [customerSyncMode, setCustomerSyncMode] = useState<"cdc" | "full">("cdc");
  const [customerJobId, setCustomerJobId] = useState<number | null>(null);
  const [customerJobStatus, setCustomerJobStatus] = useState<string | null>(null);

  // Project table state
  const [projectTableLoading, setProjectTableLoading] = useState(false);
  const [projectCdcData, setProjectCdcData] = useState<ProjectCdcPayload>({
    summary: emptyProjectSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [projectPage, setProjectPage] = useState(0);
  const [projectPageSize, setProjectPageSize] = useState(10);
  const [projectSearchInput, setProjectSearchInput] = useState("");
  const [projectSearchTerm, setProjectSearchTerm] = useState("");
  const [projectSyncFilter, setProjectSyncFilter] = useState("ALL");
  const [projectJobId, setProjectJobId] = useState<number | null>(null);
  const [projectJobStatus, setProjectJobStatus] = useState<string | null>(null);
  const [projectSyncConfigOpen, setProjectSyncConfigOpen] = useState(false);
  const [projectSyncConfigLoading, setProjectSyncConfigLoading] = useState(false);
  const [projectSyncConfigSaving, setProjectSyncConfigSaving] = useState(false);
  const [projectSyncConfig, setProjectSyncConfig] = useState<ProjectSyncConfig>(DEFAULT_PROJECT_SYNC_CONFIG);

  // Invoice table state
  const [invoiceTableLoading, setInvoiceTableLoading] = useState(false);
  const [invoiceCdcData, setInvoiceCdcData] = useState<InvoiceCdcPayload>({
    summary: emptyInvoiceSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [invoicePage, setInvoicePage] = useState(0);
  const [invoicePageSize, setInvoicePageSize] = useState(10);
  const [invoiceSearchInput, setInvoiceSearchInput] = useState("");
  const [invoiceSearchTerm, setInvoiceSearchTerm] = useState("");
  const [invoiceSyncFilter, setInvoiceSyncFilter] = useState("ALL");
  const [invoiceJobId, setInvoiceJobId] = useState<number | null>(null);
  const [invoiceJobStatus, setInvoiceJobStatus] = useState<string | null>(null);
  const [invoiceDetailOpen, setInvoiceDetailOpen] = useState(false);
  const [invoiceDetailLoading, setInvoiceDetailLoading] = useState(false);
  const [invoiceDetail, setInvoiceDetail] = useState<InvoiceDetailResponse | null>(null);
  const [invoiceDocumentObjectUrl, setInvoiceDocumentObjectUrl] = useState<string | null>(null);
  const [invoiceDocumentPreviewOpen, setInvoiceDocumentPreviewOpen] = useState(false);
  const [invoiceDocumentFetchLoading, setInvoiceDocumentFetchLoading] = useState(false);

  // Customer Payment table state
  const [paymentTableLoading, setPaymentTableLoading] = useState(false);
  const [paymentCdcData, setPaymentCdcData] = useState<CustomerPaymentCdcPayload>({
    summary: emptyCustomerPaymentSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [paymentPage, setPaymentPage] = useState(0);
  const [paymentPageSize, setPaymentPageSize] = useState(10);
  const [paymentSearchInput, setPaymentSearchInput] = useState("");
  const [paymentSearchTerm, setPaymentSearchTerm] = useState("");
  const [paymentSyncFilter, setPaymentSyncFilter] = useState("ALL");
  const [paymentJobId, setPaymentJobId] = useState<number | null>(null);
  const [paymentJobStatus, setPaymentJobStatus] = useState<string | null>(null);
  const [paymentDetailOpen, setPaymentDetailOpen] = useState(false);
  const [paymentDetailLoading, setPaymentDetailLoading] = useState(false);
  const [paymentDetail, setPaymentDetail] = useState<CustomerPaymentDetailResponse | null>(null);

  // Employee table state
  const [employeeTableLoading, setEmployeeTableLoading] = useState(false);
  const [employeeCdcData, setEmployeeCdcData] = useState<EmployeeCdcPayload>({
    summary: emptyEmployeeSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [employeePage, setEmployeePage] = useState(0);
  const [employeePageSize, setEmployeePageSize] = useState(10);
  const [employeeSearchInput, setEmployeeSearchInput] = useState("");
  const [employeeSearchTerm, setEmployeeSearchTerm] = useState("");
  const [employeeSyncFilter, setEmployeeSyncFilter] = useState("ALL");
  const [employeeSyncMode, setEmployeeSyncMode] = useState<"cdc" | "full">("cdc");
  const [employeeJobId, setEmployeeJobId] = useState<number | null>(null);
  const [employeeJobStatus, setEmployeeJobStatus] = useState<string | null>(null);
  const [employeeSyncConfigOpen, setEmployeeSyncConfigOpen] = useState(false);
  const [employeeSyncConfigLoading, setEmployeeSyncConfigLoading] = useState(false);
  const [employeeSyncConfigSaving, setEmployeeSyncConfigSaving] = useState(false);
  const [employeeSyncConfig, setEmployeeSyncConfig] = useState<EmployeeSyncConfig>(DEFAULT_EMPLOYEE_SYNC_CONFIG);
  // Tracks the raw string in the days-after-hire input so the user can clear and retype freely.
  // Clamped to [1, 365] on blur and on save — not on every keystroke.
  const [daysInputValue, setDaysInputValue] = useState<string>(String(DEFAULT_EMPLOYEE_SYNC_CONFIG.days_after_hire));
  // null = not yet loaded; false = loaded but never configured; true = configured
  const [employeeSyncConfigured, setEmployeeSyncConfigured] = useState<boolean | null>(null);

  // Service role table state
  const [serviceRoleTableLoading, setServiceRoleTableLoading] = useState(false);
  const [serviceRoleCdcData, setServiceRoleCdcData] = useState<ServiceRoleCdcPayload>({
    summary: emptyServiceRoleSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [serviceRolePage, setServiceRolePage] = useState(0);
  const [serviceRolePageSize, setServiceRolePageSize] = useState(10);
  const [serviceRoleSearchInput, setServiceRoleSearchInput] = useState("");
  const [serviceRoleSearchTerm, setServiceRoleSearchTerm] = useState("");
  const [serviceRoleSyncFilter, setServiceRoleSyncFilter] = useState("ALL");
  const [serviceRoleJobId, setServiceRoleJobId] = useState<number | null>(null);
  const [serviceRoleJobStatus, setServiceRoleJobStatus] = useState<string | null>(null);
  const [serviceIncomeAccounts, setServiceIncomeAccounts] = useState<ServiceRoleIncomeAccount[]>([]);
  const [serviceIncomeAccountsLoading, setServiceIncomeAccountsLoading] = useState(false);
  const [selectedServiceRoleIds, setSelectedServiceRoleIds] = useState<number[]>([]);
  const [serviceCoaAssignDialogOpen, setServiceCoaAssignDialogOpen] = useState(false);
  const [selectedServiceCoaOption, setSelectedServiceCoaOption] = useState<ServiceRoleIncomeAccount | null>(null);
  const [assigningServiceRoleCoa, setAssigningServiceRoleCoa] = useState(false);


  // Timelog / QuickBooks Online TimeActivity table state
  const [timelogTableLoading, setTimelogTableLoading] = useState(false);
  const [timelogCdcData, setTimelogCdcData] = useState<TimelogCdcPayload>({
    summary: emptyTimelogSummary,
    pagination: { page: 1, pageSize: 25, totalRecords: 0, totalPages: 0 },
    rows: [],
  });
  const [timelogPage, setTimelogPage] = useState(0);
  const [timelogPageSize, setTimelogPageSize] = useState(10);
  const [timelogSearchInput, setTimelogSearchInput] = useState("");
  const [timelogSearchTerm, setTimelogSearchTerm] = useState("");
  const [timelogSyncFilter, setTimelogSyncFilter] = useState("ALL");
  const [timelogJobId, setTimelogJobId] = useState<number | null>(null);
  const [timelogJobStatus, setTimelogJobStatus] = useState<string | null>(null);
  // const [timelogDeleteDialogOpen, setTimelogDeleteDialogOpen] = useState(false); // Dev-only cleanup disabled for PR
  // const [timelogDeleteLoading, setTimelogDeleteLoading] = useState(false);

  // Bill detail drawer state
  const [billDetailOpen, setBillDetailOpen] = useState(false);
  const [selectedBill, setSelectedBill] = useState<BillRow | null>(null);
  const [billLineItems, setBillLineItems] = useState<BillLineItem[]>([]);
  const [billLineItemsLoading, setBillLineItemsLoading] = useState(false);
  const [billDocumentInfo, setBillDocumentInfo] = useState<BillDocumentInfo | null>(null);
  const [billDocumentInfoLoading, setBillDocumentInfoLoading] = useState(false);
  const [billDocumentPreviewOpen, setBillDocumentPreviewOpen] = useState(false);
  const [billDocumentObjectUrl, setBillDocumentObjectUrl] = useState<string | null>(null);
  const [billDocumentFetchLoading, setBillDocumentFetchLoading] = useState(false);

  const pollersRef = useRef<Record<string, number | null>>({ vendors: null, accounts: null, bills: null, billPayments: null, customers: null, projects: null, employees: null, services: null, timelogs: null, invoices: null, payments: null });
  // OAuth popup watchers — tracked so they can be cleared on unmount. Unlike the
  // sync-job pollers above, these otherwise self-clear only when the popup closes.
  const oauthPollersRef = useRef<{ accounting: number | null }>({ accounting: null });
  // Mirrors `startingSyncModule` synchronously. State updates are not visible to a second
  // handler call in the same tick, so the guard reads the ref — otherwise a double-click
  // could fire two start requests before the button re-renders as disabled.
  const startingSyncRef = useRef<string | null>(null);

  // Latest-ref pattern: always points to the current render's version of each fetch
  // function so interval callbacks never hold stale closures over pagination/filter state.
  type FetchFn = (tid: string, sid: string) => Promise<any>;
  const fetchVendorRef = useRef<FetchFn>(async () => {});
  const fetchAccountRef = useRef<FetchFn>(async () => {});
  const fetchBillRef = useRef<FetchFn>(async () => {});
  const fetchBillPaymentRef = useRef<FetchFn>(async () => {});
  const fetchCustomerRef = useRef<FetchFn>(async () => {});
  const fetchProjectRef = useRef<FetchFn>(async () => {});
  const fetchInvoiceRef = useRef<FetchFn>(async () => {});
  const fetchPaymentRef = useRef<FetchFn>(async () => {});
  const fetchEmployeeRef = useRef<FetchFn>(async () => {});
  const fetchServiceRoleRef = useRef<FetchFn>(async () => {});
  const fetchTimelogRef = useRef<FetchFn>(async () => {});
  const fetchConnectionRef = useRef<FetchFn>(async () => {});

  const isConnecting = connectionAction === "connecting";
  const isDisconnecting = connectionAction === "disconnecting";
  const isReconnectRequired = connection.reconnectRequired === true;
  const isConnected = connection.connected === true;
  const vendorAnyRunning = Boolean(vendorJobId && ACTIVE_JOB_STATUSES.has(String(vendorJobStatus || "").toUpperCase()));
  const accountAnyRunning = Boolean(accountJobId && ACTIVE_JOB_STATUSES.has(String(accountJobStatus || "").toUpperCase()));
  const billAnyRunning = Boolean(billJobId && ACTIVE_JOB_STATUSES.has(String(billJobStatus || "").toUpperCase()));
  const billPaymentAnyRunning = Boolean(billPaymentJobId && ACTIVE_JOB_STATUSES.has(String(billPaymentJobStatus || "").toUpperCase()));
  const customerAnyRunning = Boolean(customerJobId && ACTIVE_JOB_STATUSES.has(String(customerJobStatus || "").toUpperCase()));
  const projectAnyRunning = Boolean(projectJobId && ACTIVE_JOB_STATUSES.has(String(projectJobStatus || "").toUpperCase()));
  const invoiceAnyRunning = Boolean(invoiceJobId && ACTIVE_JOB_STATUSES.has(String(invoiceJobStatus || "").toUpperCase()));
  const paymentAnyRunning = Boolean(paymentJobId && ACTIVE_JOB_STATUSES.has(String(paymentJobStatus || "").toUpperCase()));
  const employeeAnyRunning = Boolean(employeeJobId && ACTIVE_JOB_STATUSES.has(String(employeeJobStatus || "").toUpperCase()));
  const serviceRoleAnyRunning = Boolean(serviceRoleJobId && ACTIVE_JOB_STATUSES.has(String(serviceRoleJobStatus || "").toUpperCase()));
  const timelogAnyRunning = Boolean(timelogJobId && ACTIVE_JOB_STATUSES.has(String(timelogJobStatus || "").toUpperCase()));
  const anyRunning = vendorAnyRunning || accountAnyRunning || billAnyRunning || billPaymentAnyRunning || customerAnyRunning || projectAnyRunning || employeeAnyRunning || serviceRoleAnyRunning || timelogAnyRunning || invoiceAnyRunning || paymentAnyRunning;
  // A start request is in flight — treated exactly like a running job for button/banner
  // purposes so the workspace never looks idle between the click and the job appearing.
  const syncStartPending = startingSyncModule !== null;
  // Shown on a module's Sync button (as a hover tooltip) when it is blocked because another
  // sync is already running. Only one QuickBooks sync may run at a time.
  const syncBusyReason = anyRunning || syncStartPending
    ? "A QuickBooks sync is already running. You can start this once it finishes."
    : undefined;
  /** True while `moduleId` has a start request in flight or a job actively running. */
  const isModuleSyncing = (moduleId: string, jobRunning: boolean) =>
    jobRunning || startingSyncModule === moduleId;
  /**
   * Claims the single sync slot for `moduleId` and paints the busy state immediately.
   * Returns false when a start is already in flight, so the caller must bail out.
   */
  const beginSyncRequest = (moduleId: string): boolean => {
    if (startingSyncRef.current !== null) return false;
    startingSyncRef.current = moduleId;
    setStartingSyncModule(moduleId);
    return true;
  };
  /** Releases the sync slot; the job's own status drives the busy state from here on. */
  const endSyncRequest = () => {
    startingSyncRef.current = null;
    setStartingSyncModule(null);
  };
  const activeModuleDef = SYNC_MODULES.find((m) => m.id === selectedModule) ?? SYNC_MODULES[0];
  // Disable all actions while fetching status or sync state
  const isFetchingStatus = statusLoading || syncStateLoading;

  useEffect(() => {
    const timer = window.setTimeout(() => { setVendorSearchTerm(vendorSearchInput.trim()); setVendorPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [vendorSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setAccountSearchTerm(accountSearchInput.trim()); setAccountPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [accountSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setBillSearchTerm(billSearchInput.trim()); setBillPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [billSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setBillPaymentSearchTerm(billPaymentSearchInput.trim()); setBillPaymentPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [billPaymentSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setCustomerSearchTerm(customerSearchInput.trim()); setCustomerPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [customerSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setProjectSearchTerm(projectSearchInput.trim()); setProjectPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [projectSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setInvoiceSearchTerm(invoiceSearchInput.trim()); setInvoicePage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [invoiceSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setPaymentSearchTerm(paymentSearchInput.trim()); setPaymentPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [paymentSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setEmployeeSearchTerm(employeeSearchInput.trim()); setEmployeePage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [employeeSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setServiceRoleSearchTerm(serviceRoleSearchInput.trim()); setServiceRolePage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [serviceRoleSearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setTimelogSearchTerm(timelogSearchInput.trim()); setTimelogPage(0); }, 350);
    return () => window.clearTimeout(timer);
  }, [timelogSearchInput]);

  const loadTenantId = async () => {
    const response = await getTenantDetails(accessToken);
    if (response?.data !== undefined && (response.status === 200 || response.status === 201))
      return response?.data?.tenantid as string;
    return null;
  };

  const fetchQuickBooksServiceId = async (tid: string) => {
    const subscriberRes = await getSubscriberById(accessToken, tid);
    const subscribers = subscriberRes?.data || [];
    const activeSubscribers = subscribers.filter((sub: any) => sub?.status === "Active");
    if (!activeSubscribers.length) throw new Error("No active services enabled for this tenant");
    const serviceRes = await getAllServices(accessToken);
    const services = serviceRes?.data || [];
    const quickBooksService = services.find((svc: any) => svc?.status === "Active" && svc.name === "QuickBooks");
    if (!quickBooksService) throw new Error("QuickBooks not found in service master");
    const qbSubscriber = activeSubscribers.find((sub: any) => sub.serviceMaster === quickBooksService.id);
    if (!qbSubscriber) throw new Error("QuickBooks service not enabled for this tenant");
    setServiceId(String(quickBooksService.id));
  };

  const applyReconnectState = (message: string, payload?: any) => {
    setConnection((prev) => ({
      ...prev,
      connected: false,
      status: "reauthorization_required",
      reconnectRequired: true,
      authError: payload?.authError ?? { code: payload?.error, reason: message, detail: payload?.detail || payload?.message || message },
    }));
    setErrorMessage(message);
  };

  const stopVendorPolling = () => {
    const pollerId = pollersRef.current.vendors;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.vendors = null;
  };

  const stopAccountPolling = () => {
    const pollerId = pollersRef.current.accounts;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.accounts = null;
  };

  const stopBillPolling = () => {
    const pollerId = pollersRef.current.bills;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.bills = null;
  };

  const stopBillPaymentPolling = () => {
    const pollerId = pollersRef.current.billPayments;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.billPayments = null;
  };

  const stopCustomerPolling = () => {
    const pollerId = pollersRef.current.customers;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.customers = null;
  };

  const stopProjectPolling = () => {
    const pollerId = pollersRef.current.projects;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.projects = null;
  };

  const stopInvoicePolling = () => {
    const pollerId = pollersRef.current.invoices;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.invoices = null;
  };

  const stopPaymentPolling = () => {
    const pollerId = pollersRef.current.payments;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.payments = null;
  };

  const stopEmployeePolling = () => {
    const pollerId = pollersRef.current.employees;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.employees = null;
  };

  const stopServiceRolePolling = () => {
    const pollerId = pollersRef.current.services;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.services = null;
  };

  const stopTimelogPolling = () => {
    const pollerId = pollersRef.current.timelogs;
    if (pollerId) window.clearInterval(pollerId);
    pollersRef.current.timelogs = null;
  };

  const selectedBillPaymentIdsSet = useMemo(() => new Set(selectedBillPaymentIds), [selectedBillPaymentIds]);
  const selectableBillPaymentIds = useMemo(() => (billPaymentCdcData.rows || [])
    .map((row) => row.paymentExpenseId)
    .filter((value): value is number => typeof value === "number" && value > 0), [billPaymentCdcData.rows]);
  const allBillPaymentsOnPageSelected = selectableBillPaymentIds.length > 0 && selectableBillPaymentIds.every((id) => selectedBillPaymentIdsSet.has(id));
  const someBillPaymentsOnPageSelected = selectableBillPaymentIds.some((id) => selectedBillPaymentIdsSet.has(id));

  const selectedServiceRoleIdsSet = useMemo(() => new Set(selectedServiceRoleIds), [selectedServiceRoleIds]);
  const selectableServiceRoleIds = useMemo(() => (serviceRoleCdcData.rows || [])
    .map((row) => Number(row.roleId ?? row.id ?? 0))
    .filter((id: number) => id > 0), [serviceRoleCdcData.rows]);
  const allServiceRolesOnPageSelected = selectableServiceRoleIds.length > 0 && selectableServiceRoleIds.every((id) => selectedServiceRoleIdsSet.has(id));
  const someServiceRolesOnPageSelected = selectableServiceRoleIds.some((id) => selectedServiceRoleIdsSet.has(id));

  const getQuickBooksDetailRequestContext = () => {
    if (!tenantId || !serviceId) return null;
    return { tenantId, serviceId };
  };

  const handleBillRowClick = async (row: BillRow) => {
    const receiptId = row?.receiptId;
    if (receiptId == null) return;

    setSelectedBill(row);
    setBillDetailOpen(true);
    setBillLineItemsLoading(true);
    setBillDocumentInfoLoading(true);
    setBillDocumentPreviewOpen(false);
    setBillDocumentObjectUrl((prev: string | null) => { if (prev) URL.revokeObjectURL(prev); return null; });
    setBillDocumentFetchLoading(false);

    const requestContext = getQuickBooksDetailRequestContext();
    if (!requestContext) {
      setBillLineItemsLoading(false);
      setBillDocumentInfoLoading(false);
      return;
    }

    try {
      const [lineItemsResult, documentInfoResult] = await Promise.allSettled([
        getQuickBooksBillLineItems(accessToken, receiptId, requestContext),
        getQuickBooksBillDocumentInfo(accessToken, receiptId, requestContext),
      ]);

      if (lineItemsResult.status === "fulfilled") {
        const lineItems = lineItemsResult.value?.data?.lineItems;
        setBillLineItems(Array.isArray(lineItems) ? lineItems : []);
      } else {
        setBillLineItems([]);
      }

      if (documentInfoResult.status === "fulfilled") {
        setBillDocumentInfo(documentInfoResult.value?.data ?? null);
      } else {
        setBillDocumentInfo(null);
      }
    } catch {
      setBillLineItems([]);
      setBillDocumentInfo(null);
      setToast({ type: "error", message: "Could not load bill details. Please try again." });
    } finally {
      setBillLineItemsLoading(false);
      setBillDocumentInfoLoading(false);
    }
  };

  const handleBillAttachmentClick = async () => {
    const receiptId = selectedBill?.receiptId;
    if (receiptId == null) return;

    if (billDocumentPreviewOpen) {
      setBillDocumentPreviewOpen(false);
      if (billDocumentObjectUrl) { URL.revokeObjectURL(billDocumentObjectUrl); setBillDocumentObjectUrl(null); }
      return;
    }

    const requestContext = getQuickBooksDetailRequestContext();
    if (!requestContext) {
      setToast({ type: "error", message: "Could not load the attachment. Please refresh and try again." });
      return;
    }

    setBillDocumentFetchLoading(true);
    try {
      const { objectUrl } = await downloadQuickBooksBillDocument(accessToken, receiptId, requestContext);
      setBillDocumentObjectUrl(objectUrl);
      setBillDocumentPreviewOpen(true);
    } catch {
      setToast({ type: "error", message: "Could not load the attachment. Please try again." });
    } finally {
      setBillDocumentFetchLoading(false);
    }
  };

  const handleBillDialogClose = () => {
    setBillDetailOpen(false);
    setBillDocumentPreviewOpen(false);
    if (billDocumentObjectUrl) { URL.revokeObjectURL(billDocumentObjectUrl); setBillDocumentObjectUrl(null); }
  };

  const handleInvoiceRowClick = async (row: InvoiceCdcRow) => {
    if (!row.mappingId) return;
    const requestContext = getQuickBooksDetailRequestContext();
    if (!requestContext) return;
    setInvoiceDocumentPreviewOpen(false);
    if (invoiceDocumentObjectUrl) { URL.revokeObjectURL(invoiceDocumentObjectUrl); setInvoiceDocumentObjectUrl(null); }
    setInvoiceDetailOpen(true);
    setInvoiceDetailLoading(true);
    setInvoiceDetail(null);
    try {
      const response = await getQuickBooksInvoiceDetails(accessToken, row.mappingId, requestContext);
      setInvoiceDetail(response?.data || null);
    } catch {
      setInvoiceDetail(null);
      setToast({ type: "error", message: "Could not load invoice details. Please try again." });
    } finally {
      setInvoiceDetailLoading(false);
    }
  };

  const handleInvoiceAttachmentClick = async () => {
    if (invoiceDocumentPreviewOpen) {
      setInvoiceDocumentPreviewOpen(false);
      return;
    }
    const mappingId = invoiceDetail?.mapping?.id;
    if (!mappingId || !invoiceDetail?.document) return;
    const requestContext = getQuickBooksDetailRequestContext();
    if (!requestContext) return;

    if (invoiceDocumentObjectUrl) {
      setInvoiceDocumentPreviewOpen(true);
      return;
    }

    setInvoiceDocumentFetchLoading(true);
    try {
      const { objectUrl } = await downloadQuickBooksInvoiceDocument(accessToken, mappingId, requestContext);
      setInvoiceDocumentObjectUrl(objectUrl);
      setInvoiceDocumentPreviewOpen(true);
    } catch {
      setToast({ type: "error", message: "Could not load the invoice attachment. Please try again." });
    } finally {
      setInvoiceDocumentFetchLoading(false);
    }
  };

  const handleInvoiceDialogClose = () => {
    setInvoiceDetailOpen(false);
    setInvoiceDocumentPreviewOpen(false);
    if (invoiceDocumentObjectUrl) { URL.revokeObjectURL(invoiceDocumentObjectUrl); setInvoiceDocumentObjectUrl(null); }
  };

  const handlePaymentRowClick = async (row: CustomerPaymentCdcRow) => {
    if (!row.mappingId) return;
    const requestContext = getQuickBooksDetailRequestContext();
    if (!requestContext) return;
    setPaymentDetailOpen(true);
    setPaymentDetailLoading(true);
    setPaymentDetail(null);
    try {
      const response = await getQuickBooksPaymentDetails(accessToken, row.mappingId, requestContext);
      setPaymentDetail(response?.data || null);
    } catch {
      setPaymentDetail(null);
      setToast({ type: "error", message: "Could not load customer payment details. Please try again." });
    } finally {
      setPaymentDetailLoading(false);
    }
  };

  const toggleBillPaymentSelection = (paymentExpenseId: number) => {
    setSelectedBillPaymentIds((prev) => prev.includes(paymentExpenseId) ? prev.filter((id) => id !== paymentExpenseId) : [...prev, paymentExpenseId]);
  };

  const toggleSelectAllBillPaymentsOnPage = () => {
    setSelectedBillPaymentIds((prev) => {
      const current = new Set(prev);
      if (selectableBillPaymentIds.every((id) => current.has(id))) {
        selectableBillPaymentIds.forEach((id) => current.delete(id));
      } else {
        selectableBillPaymentIds.forEach((id) => current.add(id));
      }
      return Array.from(current);
    });
  };

  const fetchVendorCdcView = async (tid: string, sid: string) => {
    setVendorTableLoading(true);
    try {
      const res = await getQuickBooksVendorCdcView(accessToken, {
        tenantId: tid, serviceId: sid,
        page: vendorPage + 1, pageSize: vendorPageSize,
        search: vendorSearchTerm, syncStatus: vendorSyncFilter,
        mappedOnly: true,
      });
      const data = res?.data || {};
      const pagination = data.pagination || { page: vendorPage + 1, pageSize: vendorPageSize, totalRecords: 0, totalPages: 0 };
      if (pagination.totalPages > 0 && vendorPage >= pagination.totalPages) {
        setVendorPage(0);
        return data.summary || emptyVendorSummary;
      }
      const summary = data.summary || emptyVendorSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("vendors"); return n; });
      setVendorCdcData({
        summary,
        pagination,
        rows: data.rows || [],
      });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load vendor data.");
    } finally {
      setVendorTableLoading(false);
    }
  };
  fetchVendorRef.current = fetchVendorCdcView;

  const fetchAccountCdcView = async (tid: string, sid: string) => {
    setAccountTableLoading(true);
    try {
      const res = await getQuickBooksAccountCdcView(accessToken, {
        tenantId: tid, serviceId: sid,
        page: accountPage + 1, pageSize: accountPageSize,
        search: accountSearchTerm, syncStatus: accountSyncFilter,
      });
      const data = res?.data || {};
      const pagination = data.pagination || { page: accountPage + 1, pageSize: accountPageSize, totalRecords: 0, totalPages: 0 };
      if (pagination.totalPages > 0 && accountPage >= pagination.totalPages) {
        setAccountPage(0);
        return data.summary || emptyAccountSummary;
      }
      const summary = data.summary || emptyAccountSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("chart-of-accounts"); return n; });
      setAccountCdcData({
        summary,
        pagination,
        rows: data.rows || [],
      });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load account data.");
    } finally {
      setAccountTableLoading(false);
    }
  };
  fetchAccountRef.current = fetchAccountCdcView;

  const handleAccountSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("chart-of-accounts")) return;
    try {
      setLoading(true);
      const res = await startQuickBooksChartOfAccountsSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId });
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Account sync jobId missing");
      setAccountJobId(jobId);
      setAccountJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Chart of accounts sync started successfully." });
    } catch (e: any) { setErrorMessage(getQuickBooksErrorMessage(e)); }
    finally { endSyncRequest(); setLoading(false); }
  };

  const handleVendorSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("vendors")) return;
    try {
      setLoading(true);
      const res = await startQuickBooksVendorsSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId, sync_mode: vendorSyncMode });
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Vendor sync jobId missing");
      setVendorJobId(jobId);
      setVendorJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Vendor sync started successfully." });
    } catch (e: any) { setErrorMessage(getQuickBooksErrorMessage(e)); }
    finally { endSyncRequest(); setLoading(false); }
  };

  const fetchBillCdcView = async (tid: string, sid: string) => {
    setBillTableLoading(true);
    try {
      const billSortBy = billSortMode.startsWith("PROJECT") ? "PROJECT" : "UPDATED_AT";
      const billSortDirection = billSortMode.endsWith("_ASC") ? "ASC" : "DESC";
      const res = await getQuickBooksBillCdcView(accessToken, {
        tenantId: tid, serviceId: sid,
        page: billPage + 1, pageSize: billPageSize,
        search: billSearchTerm, syncStatus: billSyncFilter,
        mappedOnly: true,
        sortBy: billSortBy,
        sortDirection: billSortDirection,
      });
      const data = res?.data || {};
      const pagination = data.pagination || { page: billPage + 1, pageSize: billPageSize, totalRecords: 0, totalPages: 0 };
      if (pagination.totalPages > 0 && billPage >= pagination.totalPages) {
        setBillPage(0);
        return data.summary || emptyBillSummary;
      }
      const summary = data.summary || emptyBillSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("bills"); return n; });
      setBillCdcData({
        summary,
        pagination,
        rows: data.rows || [],
      });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load bill data.");
    } finally {
      setBillTableLoading(false);
    }
  };
  fetchBillRef.current = fetchBillCdcView;

  const fetchBillPaymentCdcView = async (tid: string, sid: string) => {
    setBillPaymentTableLoading(true);
    try {
      const res = await getQuickBooksBillPaymentCdcView(accessToken, {
        tenantId: tid,
        serviceId: sid,
        page: billPaymentPage + 1,
        pageSize: billPaymentPageSize,
        search: billPaymentSearchTerm,
        syncStatus: billPaymentSyncFilter,
        mappedOnly: false,
      });
      const data = res?.data || {};
      const pagination = data.pagination || { page: billPaymentPage + 1, pageSize: billPaymentPageSize, totalRecords: 0, totalPages: 0 };
      if (pagination.totalPages > 0 && billPaymentPage >= pagination.totalPages) {
        setBillPaymentPage(0);
        return data.summary || emptyBillPaymentSummary;
      }
      const summary = data.summary || emptyBillPaymentSummary;
      setBillPaymentCdcData({ summary, pagination, rows: data.rows || [] });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load bill payment data.");
    } finally {
      setBillPaymentTableLoading(false);
    }
  };
  fetchBillPaymentRef.current = fetchBillPaymentCdcView;

  const fetchCustomerCdcView = async (tid: string, sid: string) => {
    setCustomerTableLoading(true);
    try {
      const res = await getQuickBooksCustomerCdcView(accessToken, {
        tenantId: tid, serviceId: sid,
        page: customerPage + 1, pageSize: customerPageSize,
        search: customerSearchTerm, syncStatus: customerSyncFilter,
        mappedOnly: true,
      });
      const data = res?.data || {};
      const pagination = data.pagination || { page: customerPage + 1, pageSize: customerPageSize, totalRecords: 0, totalPages: 0 };
      if (pagination.totalPages > 0 && customerPage >= pagination.totalPages) {
        setCustomerPage(0);
        return data.summary || emptyCustomerSummary;
      }
      const summary = data.summary || emptyCustomerSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("customers"); return n; });
      setCustomerCdcData({
        summary,
        pagination,
        rows: data.rows || [],
      });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load customer data.");
    } finally {
      setCustomerTableLoading(false);
    }
  };
  fetchCustomerRef.current = fetchCustomerCdcView;

  const handleCustomerSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("customers")) return;
    try {
      setLoading(true);
      const res = await startQuickBooksCustomersSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId, sync_mode: customerSyncMode });
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Customer sync jobId missing");
      setCustomerJobId(jobId);
      setCustomerJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Customer sync started successfully." });
    } catch (e: any) { setErrorMessage(getQuickBooksErrorMessage(e)); }
    finally { endSyncRequest(); setLoading(false); }
  };

  const fetchProjectCdcView = async (tid: string, sid: string) => {
    setProjectTableLoading(true);
    try {
      const res = await getQuickBooksProjectCdcView(accessToken, {
        tenantId: tid, serviceId: sid,
        page: projectPage + 1, pageSize: projectPageSize,
        search: projectSearchTerm, syncStatus: projectSyncFilter,
      });
      const data = res?.data || {};
      // Backend returns flat pagination fields alongside records[] — normalize to standard shape.
      const pagination = data.pagination || {
        page: data.page || projectPage + 1,
        pageSize: data.pageSize || projectPageSize,
        totalRecords: data.totalRecords || 0,
        totalPages: data.totalPages || 0,
      };
      const rows: ProjectRow[] = data.records || data.rows || [];
      if (pagination.totalPages > 0 && projectPage >= pagination.totalPages) {
        setProjectPage(0);
        return data.summary || emptyProjectSummary;
      }
      const summary = data.summary || emptyProjectSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("projects"); return n; });
      setProjectCdcData({
        summary,
        pagination,
        rows,
      });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load project data.");
    } finally {
      setProjectTableLoading(false);
    }
  };
  fetchProjectRef.current = fetchProjectCdcView;

  const fetchInvoiceCdcView = async (tid: string, sid: string) => {
    setInvoiceTableLoading(true);
    try {
      const res = await getQuickBooksInvoiceCdcView(accessToken, {
        tenantId: tid,
        serviceId: sid,
        page: invoicePage + 1,
        pageSize: invoicePageSize,
        search: invoiceSearchTerm,
        syncStatus: invoiceSyncFilter,
      });
      const data = res?.data || {};
      const pagination = data.pagination || {
        page: data.page || invoicePage + 1,
        pageSize: data.pageSize || invoicePageSize,
        totalRecords: data.totalRecords || 0,
        totalPages: data.totalPages || 0,
      };
      if (pagination.totalPages > 0 && invoicePage >= pagination.totalPages) {
        setInvoicePage(0);
        return data.summary || emptyInvoiceSummary;
      }
      const summary = data.summary || emptyInvoiceSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("invoices"); return n; });
      setInvoiceCdcData({ summary, pagination, rows: data.records || data.rows || [] });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load invoice data.");
    } finally {
      setInvoiceTableLoading(false);
    }
  };
  fetchInvoiceRef.current = fetchInvoiceCdcView;

  const handleInvoiceSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("invoices")) return;
    try {
      setLoading(true);
      const res = await startQuickBooksInvoicesSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId });
      if (!res.ok) throw new Error(res.message ?? "QuickBooks invoice sync failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("QuickBooks invoice sync jobId missing");
      setInvoiceJobId(jobId);
      setInvoiceJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "QuickBooks invoice sync started successfully." });
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e));
    } finally {
      endSyncRequest();
      setLoading(false);
    }
  };

  const fetchPaymentCdcView = async (tid: string, sid: string) => {
    setPaymentTableLoading(true);
    try {
      const res = await getQuickBooksPaymentCdcView(accessToken, {
        tenantId: tid,
        serviceId: sid,
        page: paymentPage + 1,
        pageSize: paymentPageSize,
        search: paymentSearchTerm,
        syncStatus: paymentSyncFilter,
      });
      const data = res?.data || {};
      const pagination = data.pagination || {
        page: data.page || paymentPage + 1,
        pageSize: data.pageSize || paymentPageSize,
        totalRecords: data.totalRecords || 0,
        totalPages: data.totalPages || 0,
      };
      if (pagination.totalPages > 0 && paymentPage >= pagination.totalPages) {
        setPaymentPage(0);
        return data.summary || emptyCustomerPaymentSummary;
      }
      const summary = data.summary || emptyCustomerPaymentSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("payments"); return n; });
      setPaymentCdcData({ summary, pagination, rows: data.records || data.rows || [] });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load customer payment data.");
    } finally {
      setPaymentTableLoading(false);
    }
  };
  fetchPaymentRef.current = fetchPaymentCdcView;

  const handlePaymentSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("payments")) return;
    try {
      setLoading(true);
      const res = await startQuickBooksPaymentsSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId });
      if (!res.ok) throw new Error(res.message ?? "QuickBooks customer payment sync failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("QuickBooks customer payment sync jobId missing");
      setPaymentJobId(jobId);
      setPaymentJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "QuickBooks customer payment sync started successfully." });
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e));
    } finally {
      endSyncRequest();
      setLoading(false);
    }
  };

  const fetchEmployeeCdcView = async (tid: string, sid: string) => {
    setEmployeeTableLoading(true);
    try {
      const res = await getQuickBooksEmployeeCdcView(accessToken, {
        tenantId: tid, serviceId: sid,
        page: employeePage + 1, pageSize: employeePageSize,
        search: employeeSearchTerm, syncStatus: employeeSyncFilter,
        mappedOnly: false,
      });
      const data = res?.data || {};
      const pagination = data.pagination || { page: employeePage + 1, pageSize: employeePageSize, totalRecords: 0, totalPages: 0 };
      if (pagination.totalPages > 0 && employeePage >= pagination.totalPages) {
        setEmployeePage(0);
        return data.summary || emptyEmployeeSummary;
      }
      const summary = data.summary || emptyEmployeeSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("employees"); return n; });
      setEmployeeCdcData({
        summary,
        pagination,
        rows: data.rows || [],
      });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load employee data.");
    } finally {
      setEmployeeTableLoading(false);
    }
  };
  fetchEmployeeRef.current = fetchEmployeeCdcView;

  const fetchServiceRoleCdcView = async (tid: string, sid: string) => {
    setServiceRoleTableLoading(true);
    try {
      const res = await getQuickBooksServiceRoleCdcView(accessToken, {
        tenantId: tid, serviceId: sid,
        page: serviceRolePage + 1, pageSize: serviceRolePageSize,
        search: serviceRoleSearchTerm, syncStatus: serviceRoleSyncFilter,
      });
      const data = res?.data || {};
      // Backend may return flat pagination fields or a nested pagination object (same as project module)
      const pagination = data.pagination || {
        page: data.page || serviceRolePage + 1,
        pageSize: data.pageSize || serviceRolePageSize,
        totalRecords: data.totalRecords || 0,
        totalPages: data.totalPages || 0,
      };
      if (pagination.totalPages > 0 && serviceRolePage >= pagination.totalPages) {
        setServiceRolePage(0);
        const raw = data.summary || data;
        return {
          total:   raw?.total   ?? raw?.totalRoles   ?? 0,
          synced:  raw?.synced  ?? raw?.syncedRoles  ?? 0,
          pending: raw?.pending ?? raw?.pendingRoles ?? raw?.pendingPush ?? 0,
          failed:  raw?.failed  ?? raw?.failedRoles  ?? raw?.syncFailed  ?? 0,
        };
      }
      // Backend may return rows as `records` or `rows`
      const rows: ServiceRoleCdcRow[] = data.records || data.rows || [];
      // Normalize summary — backend may use different field names
      const raw = data.summary || data;
      const summary: ServiceRoleSummary = {
        total:   raw?.total   ?? raw?.totalRoles   ?? 0,
        synced:  raw?.synced  ?? raw?.syncedRoles  ?? 0,
        pending: raw?.pending ?? raw?.pendingRoles ?? raw?.pendingPush ?? 0,
        failed:  raw?.failed  ?? raw?.failedRoles  ?? raw?.syncFailed  ?? 0,
      };
      setLoadedModules(prev => { const n = new Set(prev); n.add("services"); return n; });
      setServiceRoleCdcData({ summary, pagination, rows });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load service role data.");
    } finally {
      setServiceRoleTableLoading(false);
    }
  };
  fetchServiceRoleRef.current = fetchServiceRoleCdcView;



  const fetchTimelogCdcView = async (tid: string, sid: string) => {
    setTimelogTableLoading(true);
    try {
      const res = await getQuickBooksTimelogCdcView(accessToken, {
        tenantId: tid,
        serviceId: sid,
        page: timelogPage + 1,
        pageSize: timelogPageSize,
        search: timelogSearchTerm,
        syncStatus: timelogSyncFilter,
      });
      const data = res?.data || {};
      const pagination = data.pagination || {
        page: data.page || timelogPage + 1,
        pageSize: data.pageSize || timelogPageSize,
        totalRecords: data.totalRecords || 0,
        totalPages: data.totalPages || 0,
      };
      if (pagination.totalPages > 0 && timelogPage >= pagination.totalPages) {
        setTimelogPage(0);
        return data.summary || emptyTimelogSummary;
      }
      const summary = data.summary || emptyTimelogSummary;
      setLoadedModules(prev => { const n = new Set(prev); n.add("timelogs"); return n; });
      setTimelogCdcData({
        summary,
        pagination,
        rows: data.records || data.rows || [],
      });
      return summary;
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load QuickBooks time activity data.");
    } finally {
      setTimelogTableLoading(false);
    }
  };
  fetchTimelogRef.current = fetchTimelogCdcView;

  const handleTimelogSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("timelogs")) return;
    try {
      setLoading(true);
      const res = await startQuickBooksTimelogsSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId });
      if (!res.ok) throw new Error(res.message ?? "QuickBooks time activity sync failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("QuickBooks time activity sync jobId missing");
      setTimelogJobId(jobId);
      setTimelogJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "QuickBooks time activity sync started successfully." });
    } catch (e: any) { setErrorMessage(getQuickBooksErrorMessage(e)); }
    finally { endSyncRequest(); setLoading(false); }
  };

//   const handleDeleteMappedTimeActivities = async () => {
//     if (!tenantId || !serviceId || timelogDeleteLoading) return;
//     try {
//       setTimelogDeleteLoading(true);
//       const res = await deleteMappedQuickBooksTimeActivities(accessToken, {
//         tenant_id: tenantId,
//         service_id: String(serviceId),
//         confirm_delete: true,
//         dry_run: false,
//         object_id: objectId || null,
//       });
//       const payload = res?.data || res || {};
//       if (payload?.ok === false && Number(payload?.failed || 0) === 0) {
//         throw new Error(payload?.message || "Unable to delete mapped QuickBooks TimeActivity records.");
//       }
//
//       setTimelogDeleteDialogOpen(false);
//       setTimelogPage(0);
//       await fetchTimelogCdcView(tenantId, String(serviceId));
//       await fetchSyncState(tenantId, String(serviceId));
//
//       const deleted = Number(payload?.deleted || 0);
//       const alreadyMissing = Number(payload?.alreadyMissing || 0);
//       const failed = Number(payload?.failed || 0);
//       const resetMappings = Number(payload?.resetMappings || 0);
//       const message = failed > 0
//         ? `Deleted ${deleted} mapped TimeActivity records, reset ${resetMappings} mappings, and ${failed} failed.`
//         : `Deleted ${deleted} mapped TimeActivity records${alreadyMissing ? `; ${alreadyMissing} were already missing` : ""}. ${resetMappings} mappings are ready to sync again.`;
//       setToast({ type: failed > 0 ? "warning" : "success", message });
//     } catch (e: any) {
//       setErrorMessage(getQuickBooksErrorMessage(e));
//     } finally {
//       setTimelogDeleteLoading(false);
//     }
//   };
//
  const fetchServiceIncomeAccounts = async (tid: string, sid: string) => {
    setServiceIncomeAccountsLoading(true);
    try {
      // Try preferred /coa-accounts first; fall back to /income-accounts on failure
      let res: any;
      try {
        res = await getQuickBooksServiceCoaAccounts(accessToken, { tenantId: tid, serviceId: sid });
      } catch {
        res = await getQuickBooksServiceIncomeAccounts(accessToken, { tenantId: tid, serviceId: sid });
      }
      const data = res?.data || {};
      const raw: any[] = data.records || data.rows || data.accounts || [];
      const accounts = raw.map(normalizeServiceCoaAccount).filter((a): a is ServiceRoleIncomeAccount => a !== null);
      setServiceIncomeAccounts(accounts);
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load chart of accounts.");
    } finally {
      setServiceIncomeAccountsLoading(false);
    }
  };

  const handleBulkAssignServiceRoleCoa = async () => {
    if (!tenantId || !serviceId || !selectedServiceCoaOption || selectedServiceRoleIds.length === 0) return;
    setAssigningServiceRoleCoa(true);
    try {
      const res = await assignQuickBooksServiceIncomeAccount(accessToken, {
        tenant_id: tenantId,
        service_id: serviceId,
        chart_of_account_id: selectedServiceCoaOption.id,
        role_ids: selectedServiceRoleIds,
        object_id: objectId,
      });
      if (!res?.ok) throw new Error(res?.message ?? "Failed to assign COA.");
      setToast({ type: "success", message: `COA assigned to ${selectedServiceRoleIds.length} role${selectedServiceRoleIds.length === 1 ? "" : "s"}. They are queued for the next service sync.` });
      setServiceCoaAssignDialogOpen(false);
      setSelectedServiceCoaOption(null);
      setSelectedServiceRoleIds([]);
      await fetchServiceRoleCdcView(tenantId, serviceId);
    } catch (e: any) {
      setToast({ type: "error", message: getQuickBooksErrorMessage(e) || "Could not assign COA. Please try again." });
    } finally {
      setAssigningServiceRoleCoa(false);
    }
  };

  const handleServiceRoleSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("services")) return;
    try {
      setLoading(true);
      setCapabilityError(null);
      const res = await startQuickBooksServiceRolesSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId });
      assertNotFeatureBlocked(res);
      if (!res.ok) throw new Error(res.message ?? "Service role export failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Service role export jobId missing");
      setServiceRoleJobId(jobId);
      setServiceRoleJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Service role export started successfully." });
    } catch (e: any) {
      if (e instanceof QBOPlanError) { setCapabilityError(e.message); }
      else { setErrorMessage(getQuickBooksErrorMessage(e)); }
    }
    finally { endSyncRequest(); setLoading(false); }
  };

  const handleEmployeeSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("employees")) return;
    try {
      setLoading(true);
      const res = await startQuickBooksEmployeesSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId, sync_mode: employeeSyncMode });
      if (!res.ok) throw new Error(res.message ?? "Employee sync failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Employee sync jobId missing");
      setEmployeeJobId(jobId);
      setEmployeeJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Employee sync started successfully." });
    } catch (e: any) { setErrorMessage(getQuickBooksErrorMessage(e)); }
    finally { endSyncRequest(); setLoading(false); }
  };

  const loadEmployeeSyncConfig = async () => {
    if (!tenantId || !serviceId) return;
    setEmployeeSyncConfigLoading(true);
    try {
      const res = await getQuickBooksEmployeeSyncConfig(accessToken, { tenantId, serviceId });
      if (res?.ok && res?.data) {
        const isConfigured = res.data.configured === true;
        const days = res.data.days_after_hire ?? 1;
        setEmployeeSyncConfigured(isConfigured);
        setEmployeeSyncConfig({
          days_after_hire: days,
          configured: isConfigured,
        });
        setDaysInputValue(String(days));
      }
    } catch { /* silent — dialog will show defaults */ }
    finally { setEmployeeSyncConfigLoading(false); }
  };

  const handleOpenEmployeeSyncConfig = () => {
    // Reset raw input to match current stored value before opening
    setDaysInputValue(String(employeeSyncConfig.days_after_hire));
    setEmployeeSyncConfigOpen(true);
    loadEmployeeSyncConfig();
  };

  const handleSaveEmployeeSyncConfig = async () => {
    if (!tenantId || !serviceId) return;
    // Flush any uncommitted raw input (user may Save without blurring the field)
    const rawParsed = parseInt(daysInputValue, 10);
    const flushedDays = Number.isNaN(rawParsed) || rawParsed < 1 ? 1 : Math.min(365, rawParsed);
    if (flushedDays !== employeeSyncConfig.days_after_hire) {
      setDaysInputValue(String(flushedDays));
      setEmployeeSyncConfig((prev: EmployeeSyncConfig) => ({ ...prev, days_after_hire: flushedDays }));
    }
    const days = flushedDays;
    if (!Number.isInteger(days) || days < 1 || days > 365) {
      setErrorMessage("Days after hire must be a whole number between 1 and 365.");
      return;
    }
    setEmployeeSyncConfigSaving(true);
    try {
      const res = await saveQuickBooksEmployeeSyncConfig(accessToken, {
        tenant_id: tenantId,
        service_id: serviceId,
        days_after_hire: days,
        object_id: objectId,
      });
      if (!res?.ok) throw new Error(res?.message ?? "Failed to save employee sync settings.");
      setEmployeeSyncConfigured(true);
      setEmployeeSyncConfig((prev: EmployeeSyncConfig) => ({ ...prev, configured: true }));
      setEmployeeSyncConfigOpen(false);
      setToast({ type: "success", message: "Employee sync settings saved." });
    } catch (e: any) { setErrorMessage(getQuickBooksErrorMessage(e)); }
    finally { setEmployeeSyncConfigSaving(false); }
  };

  const normalizeProjectSyncConfig = (payload: any, fallbackMethod: ProjectSyncMethod = "AUTO"): ProjectSyncConfig => {
    const data = payload?.data && typeof payload.data === "object" ? payload.data : payload || {};
    const method = (data.method || fallbackMethod || "AUTO") as ProjectSyncMethod;
    const projectsAvailable = Boolean(
      data.projectsAvailable ??
      data.projects_available ??
      data.capabilities?.projects_enabled ??
      DEFAULT_PROJECT_SYNC_CONFIG.projectsAvailable
    );
    const effectiveTarget = (
      data.effectiveProjectSyncTarget ??
      data.effective_project_sync_target ??
      (projectsAvailable ? "QBO_PROJECT" : "SUB_CUSTOMER")
    ) as ProjectSyncConfig["effectiveProjectSyncTarget"];

    return {
      ...DEFAULT_PROJECT_SYNC_CONFIG,
      ...data,
      method,
      projectsAvailable,
      effectiveProjectSyncTarget: effectiveTarget,
      message: data.message ?? null,
      capabilityError: data.capabilityError ?? data.capability_error ?? null,
    };
  };

  const loadProjectSyncConfig = async () => {
    if (!tenantId || !serviceId) return;
    setProjectSyncConfigLoading(true);
    try {
      const res = await getQuickBooksProjectSyncConfig(accessToken, tenantId, serviceId);
      if (res?.ok || res?.data?.ok) {
        setProjectSyncConfig(normalizeProjectSyncConfig(res));
      }
    } catch { /* silent — the dialog keeps safe defaults */ }
    finally { setProjectSyncConfigLoading(false); }
  };

  const handleOpenProjectSyncConfig = () => {
    setProjectSyncConfigOpen(true);
    loadProjectSyncConfig();
  };

  const handleSaveProjectSyncConfig = async () => {
    if (!tenantId || !serviceId) return;
    setCapabilityError(null);
    setProjectSyncConfigSaving(true);
    try {
      const res = await saveQuickBooksProjectSyncConfig(accessToken, {
        tenant_id: tenantId,
        service_id: serviceId,
        method: projectSyncConfig.method,
        object_id: objectId,
      });
      assertNotFeatureBlocked(res);
      const responseOk = Boolean(res?.ok ?? res?.data?.ok);
      if (!responseOk) throw new Error(res?.message ?? res?.data?.message ?? "Failed to save project sync settings.");
      setProjectSyncConfig(normalizeProjectSyncConfig(res, projectSyncConfig.method));
      setProjectSyncConfigOpen(false);
      setToast({ type: "success", message: "Project sync settings saved." });
    } catch (e: any) {
      if (e instanceof QBOPlanError) {
        setProjectSyncConfig((prev: ProjectSyncConfig) => ({
          ...prev,
          projectsAvailable: false,
          message: e.message,
        }));
        setCapabilityError(e.message);
      } else { setErrorMessage(getQuickBooksErrorMessage(e)); }
    }
    finally { setProjectSyncConfigSaving(false); }
  };

  const handleProjectSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("projects")) return;
    try {
      setLoading(true);
      setCapabilityError(null);
      const res = await startQuickBooksProjectsSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId });
      assertNotFeatureBlocked(res);
      if (!res.ok) throw new Error(res.message ?? "Project sync failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Project export jobId missing");
      setProjectJobId(jobId);
      setProjectJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Project export started successfully." });
    } catch (e: any) {
      if (e instanceof QBOPlanError) { setCapabilityError(e.message); }
      else { setErrorMessage(getQuickBooksErrorMessage(e)); }
    }
    finally { endSyncRequest(); setLoading(false); }
  };

  const fetchBillPaymentAccounts = async (tid: string, sid: string) => {
    try {
      const res = await getQuickBooksBillPaymentAccounts(accessToken, { tenantId: tid, serviceId: sid });
      setBillPaymentEligibleAccounts(res?.data?.accounts || []);
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load eligible QuickBooks payment accounts.");
    }
  };

  const handleBillSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("bills")) return;
    try {
      setLoading(true);
      setCapabilityError(null);
      const res = await startQuickBooksBillsSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId, sync_mode: billSyncMode });
      assertNotFeatureBlocked(res);
      if (!res.ok) throw new Error(res.message ?? "Bill sync failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Bill sync jobId missing");
      setBillJobId(jobId);
      setBillJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Bill sync started successfully." });
    } catch (e: any) {
      if (e instanceof QBOPlanError) { setCapabilityError(e.message); }
      else { setErrorMessage(getQuickBooksErrorMessage(e)); }
    }
    finally { endSyncRequest(); setLoading(false); }
  };

  const handleBillPaymentSync = async () => {
    if (!tenantId || !serviceId) return;
    if (anyRunning) return;
    if (!beginSyncRequest("bill-payments")) return;
    try {
      setLoading(true);
      setCapabilityError(null);
      const res = await startQuickBooksBillPaymentsSync(accessToken, { tenant_id: tenantId, service_id: serviceId, object_id: objectId, sync_mode: billPaymentSyncMode });
      assertNotFeatureBlocked(res);
      if (!res.ok) throw new Error(res.message ?? "Bill payment sync failed. Please try again.");
      const jobId = Number(res?.data?.jobId);
      if (!jobId) throw new Error("Bill payment sync jobId missing");
      setBillPaymentJobId(jobId);
      setBillPaymentJobStatus(res?.data?.status ?? "RUNNING");
      await fetchSyncState(tenantId, serviceId);
      setToast({ type: "success", message: "Bill payment sync started successfully." });
    } catch (e: any) {
      if (e instanceof QBOPlanError) { setCapabilityError(e.message); }
      else { setErrorMessage(getQuickBooksErrorMessage(e)); }
    }
    finally { endSyncRequest(); setLoading(false); }
  };

  const handleAssignBillPaymentAccount = async () => {
    if (!tenantId || !serviceId) return;
    if (!selectedBillPaymentAccountId || selectedBillPaymentIds.length === 0) return;
    try {
      setAssignBillPaymentLoading(true);
      await assignQuickBooksBillPaymentAccount(accessToken, {
        tenant_id: tenantId,
        service_id: serviceId,
        payment_expense_ids: selectedBillPaymentIds,
        qbo_external_account_id: selectedBillPaymentAccountId,
        object_id: objectId,
      });
      setToast({ type: "success", message: `Assigned QuickBooks payment account to ${selectedBillPaymentIds.length} bill payment${selectedBillPaymentIds.length === 1 ? "" : "s"}.` });
      setAssignDialogOpen(false);
      setSelectedBillPaymentAccountId("");
      setSelectedBillPaymentIds([]);
      await fetchBillPaymentCdcView(tenantId, serviceId);
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to assign QuickBooks payment account.");
    } finally {
      setAssignBillPaymentLoading(false);
    }
  };

  const fetchSyncState = async (tid: string, sid: string) => {
    if (!objectId) return;
    setSyncStateLoading(true);
    try {
      const res = await getQuickBooksSyncState(accessToken, tid, sid, objectId);

      // ── Vendor job polling ──
      const vendorJob = res?.data?.jobs?.vendors || {};
      if (vendorJob?.activeJobId) {
        setVendorJobId(Number(vendorJob.activeJobId));
        setVendorJobStatus(vendorJob?.activeStatus ?? "RUNNING");
        stopVendorPolling();
        pollersRef.current.vendors = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(vendorJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setVendorJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopVendorPolling();
              setVendorJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchVendorRef.current(tid, sid);
              setToast(buildSyncToast("Vendor", status, "vendors", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopVendorPolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setVendorJobId(null);
        setVendorJobStatus(null);
      }

      // ── Account job polling ──
      const accountJob = res?.data?.jobs?.accounts || {};
      if (accountJob?.activeJobId) {
        setAccountJobId(Number(accountJob.activeJobId));
        setAccountJobStatus(accountJob?.activeStatus ?? "RUNNING");
        stopAccountPolling();
        pollersRef.current.accounts = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(accountJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setAccountJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopAccountPolling();
              setAccountJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchAccountRef.current(tid, sid);
              setToast(buildSyncToast("Chart of accounts", status, "accounts", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopAccountPolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setAccountJobId(null);
        setAccountJobStatus(null);
      }

      // ── Bill job polling ──
      const billJob = res?.data?.jobs?.bills || {};
      if (billJob?.activeJobId) {
        setBillJobId(Number(billJob.activeJobId));
        setBillJobStatus(billJob?.activeStatus ?? "RUNNING");
        stopBillPolling();
        pollersRef.current.bills = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(billJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setBillJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopBillPolling();
              setBillJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchBillRef.current(tid, sid);
              setToast(buildSyncToast("Bill", status, "bills", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopBillPolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setBillJobId(null);
        setBillJobStatus(null);
      }

      const billPaymentJob = res?.data?.jobs?.bill_payments || {};
      if (billPaymentJob?.activeJobId) {
        setBillPaymentJobId(Number(billPaymentJob.activeJobId));
        setBillPaymentJobStatus(billPaymentJob?.activeStatus ?? "RUNNING");
        stopBillPaymentPolling();
        pollersRef.current.billPayments = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(billPaymentJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setBillPaymentJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopBillPaymentPolling();
              setBillPaymentJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchBillPaymentRef.current(tid, sid);
              setToast(buildSyncToast("Bill payment", status, "billPayments", summary));
            }
          } catch (e: any) {
            if (isTransientPollError(e)) return;
            stopBillPaymentPolling();
            setErrorMessage(getQuickBooksErrorMessage(e));
          }
        }, 3000);
      } else {
        setBillPaymentJobId(null);
        setBillPaymentJobStatus(null);
      }

      // ── Customer job polling ──
      const customerJob = res?.data?.jobs?.customers || {};
      if (customerJob?.activeJobId) {
        setCustomerJobId(Number(customerJob.activeJobId));
        setCustomerJobStatus(customerJob?.activeStatus ?? "RUNNING");
        stopCustomerPolling();
        pollersRef.current.customers = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(customerJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setCustomerJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopCustomerPolling();
              setCustomerJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchCustomerRef.current(tid, sid);
              setToast(buildSyncToast("Customer", status, "customers", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopCustomerPolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setCustomerJobId(null);
        setCustomerJobStatus(null);
      }

      // ── Project job polling ──
      const projectJob = res?.data?.jobs?.projects || {};
      if (projectJob?.activeJobId) {
        setProjectJobId(Number(projectJob.activeJobId));
        setProjectJobStatus(projectJob?.activeStatus ?? "RUNNING");
        stopProjectPolling();
        pollersRef.current.projects = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(projectJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setProjectJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopProjectPolling();
              setProjectJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchProjectRef.current(tid, sid);
              setToast(buildSyncToast("Project", status, "projects", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopProjectPolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setProjectJobId(null);
        setProjectJobStatus(null);
      }

      // ── Invoice job polling ──
      const invoiceJob = res?.data?.jobs?.invoices || {};
      if (invoiceJob?.activeJobId) {
        setInvoiceJobId(Number(invoiceJob.activeJobId));
        setInvoiceJobStatus(invoiceJob?.activeStatus ?? "RUNNING");
        stopInvoicePolling();
        pollersRef.current.invoices = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(invoiceJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setInvoiceJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopInvoicePolling();
              setInvoiceJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchInvoiceRef.current(tid, sid);
              setToast(buildSyncToast("Invoice", status, "invoices", summary));
            }
          } catch (e: any) {
            if (isTransientPollError(e)) return;
            stopInvoicePolling();
            setErrorMessage(getQuickBooksErrorMessage(e));
          }
        }, 3000);
      } else {
        setInvoiceJobId(null);
        setInvoiceJobStatus(null);
      }

      // ── Customer Payment job polling ──
      const paymentJob = res?.data?.jobs?.payments || {};
      if (paymentJob?.activeJobId) {
        setPaymentJobId(Number(paymentJob.activeJobId));
        setPaymentJobStatus(paymentJob?.activeStatus ?? "RUNNING");
        stopPaymentPolling();
        pollersRef.current.payments = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(paymentJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setPaymentJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopPaymentPolling();
              setPaymentJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchPaymentRef.current(tid, sid);
              setToast(buildSyncToast("Customer payment", status, "payments", summary));
            }
          } catch (e: any) {
            if (isTransientPollError(e)) return;
            stopPaymentPolling();
            setErrorMessage(getQuickBooksErrorMessage(e));
          }
        }, 3000);
      } else {
        setPaymentJobId(null);
        setPaymentJobStatus(null);
      }

      // ── Employee job polling ──
      const employeeJob = res?.data?.jobs?.employees || {};
      if (employeeJob?.activeJobId) {
        setEmployeeJobId(Number(employeeJob.activeJobId));
        setEmployeeJobStatus(employeeJob?.activeStatus ?? "RUNNING");
        stopEmployeePolling();
        pollersRef.current.employees = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(employeeJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setEmployeeJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopEmployeePolling();
              setEmployeeJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchEmployeeRef.current(tid, sid);
              setToast(buildSyncToast("Employee", status, "employees", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopEmployeePolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setEmployeeJobId(null);
        setEmployeeJobStatus(null);
      }

      // ── Service role job polling ──
      const serviceRoleJob = res?.data?.jobs?.services || {};
      if (serviceRoleJob?.activeJobId) {
        setServiceRoleJobId(Number(serviceRoleJob.activeJobId));
        setServiceRoleJobStatus(serviceRoleJob?.activeStatus ?? "RUNNING");
        stopServiceRolePolling();
        pollersRef.current.services = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(serviceRoleJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setServiceRoleJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopServiceRolePolling();
              setServiceRoleJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchServiceRoleRef.current(tid, sid);
              setToast(buildSyncToast("Service role", status, "services", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopServiceRolePolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setServiceRoleJobId(null);
        setServiceRoleJobStatus(null);
      }

      // ── Timelog job polling ──
      const timelogJob = res?.data?.jobs?.timelogs || {};
      if (timelogJob?.activeJobId) {
        setTimelogJobId(Number(timelogJob.activeJobId));
        setTimelogJobStatus(timelogJob?.activeStatus ?? "RUNNING");
        stopTimelogPolling();
        pollersRef.current.timelogs = window.setInterval(async () => {
          try {
            const jobRes = await getQuickBooksSyncJob(accessToken, tid, Number(timelogJob.activeJobId));
            const job = jobRes?.data?.job;
            if (!job) return;
            setTimelogJobStatus(job?.status ?? null);
            const status = String(job?.status || "").toUpperCase();
            if (DONE_JOB_STATUSES.has(status)) {
              stopTimelogPolling();
              setTimelogJobId(null);
              await fetchConnectionRef.current(tid, sid);
              const summary = await fetchTimelogRef.current(tid, sid);
              setToast(buildSyncToast("Time activity", status, "timelogs", summary));
            }
          } catch (e: any) { if (isTransientPollError(e)) return; stopTimelogPolling(); setErrorMessage(getQuickBooksErrorMessage(e)); }
        }, 3000);
      } else {
        setTimelogJobId(null);
        setTimelogJobStatus(null);
      }

    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e));
    } finally {
      setSyncStateLoading(false);
    }
  };

  const loadAutoSyncConfig = async (tid: string, sid: string) => {
    try {
      const res = await getQuickBooksAutoSyncConfig(accessToken, tid, sid);
      const data = res?.data || {};
      setAutoSyncEnabled(data.enabled === true);
    } catch (e: any) {
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to load QuickBooks auto sync setting.");
    }
  };

  const fetchConnectionStatus = async (tid: string, sid: string) => {
    setStatusLoading(true);
    setErrorMessage(null);
    try {
      const res = await getQuickBooksStatus(accessToken, tid, sid, objectId);
      const data = res?.data || {};
      const reconnectRequired = data.reconnectRequired === true || String(data.status || "").toLowerCase() === "reauthorization_required";
      const connected = !reconnectRequired && (data.connected === true || data.status === "connected");
      setConnection({ connected, status: reconnectRequired ? "reauthorization_required" : data.status, env: data.env, realmId: data.realmId, refreshExpiresAt: data.refreshExpiresAt, accessExpiresAt: data.accessExpiresAt, connectedAt: data.connectedAt, reconnectRequired, authError: data.authError || null, lastSyncAt: data.lastSyncAt, lastVendorSyncAt: data.lastVendorSyncAt, lastPaymentSyncAt: data.lastPaymentSyncAt, lastAccountSyncAt: data.lastAccountSyncAt, lastBillSyncAt: data.lastBillSyncAt, lastBillPaymentSyncAt: data.lastBillPaymentSyncAt, lastCustomerSyncAt: data.lastCustomerSyncAt, lastProjectSyncAt: data.lastProjectSyncAt, lastEmployeeSyncAt: data.lastEmployeeSyncAt, lastServiceSyncAt: data.lastServiceSyncAt, lastTimelogSyncAt: data.lastTimelogSyncAt, lastInvoiceSyncAt: data.lastInvoiceSyncAt });
      if (reconnectRequired) { setErrorMessage(data?.authError?.reason || "Your QuickBooks connection expired. Please reconnect."); return; }
      if (connected) await fetchSyncState(tid, sid);
    } catch (e: any) {
      const payload = getQuickBooksApiPayload(e);
      const message = getQuickBooksErrorMessage(e);
      if (isQuickBooksReconnectResponse(payload)) applyReconnectState(message, payload);
      else { setConnection({ connected: false, reconnectRequired: false, authError: null }); setErrorMessage(message || "Unable to check QuickBooks status."); }
    } finally {
      setStatusLoading(false);
      setHasLoadedOnce(true);
    }
  };
  fetchConnectionRef.current = fetchConnectionStatus;

  // Revoke the bill-document preview blob URL when it is replaced or on unmount,
  // so a preview left open while navigating away does not leak memory.
  useEffect(() => {
    return () => { if (billDocumentObjectUrl) URL.revokeObjectURL(billDocumentObjectUrl); };
  }, [billDocumentObjectUrl]);

  useEffect(() => {
    return () => { if (invoiceDocumentObjectUrl) URL.revokeObjectURL(invoiceDocumentObjectUrl); };
  }, [invoiceDocumentObjectUrl]);

  useEffect(() => {
    const init = async () => {
      try {
        const tid = await loadTenantId();
        if (!tid) throw new Error("Unable to determine tenantId");
        setTenantId(tid);
        await fetchQuickBooksServiceId(tid);
      } catch (e: any) { setErrorMessage(e?.message || "Initialization failed"); }
    };
    init();
    return () => {
      stopVendorPolling(); stopAccountPolling(); stopBillPolling(); stopBillPaymentPolling(); stopCustomerPolling(); stopProjectPolling(); stopInvoicePolling(); stopEmployeePolling(); stopServiceRolePolling(); stopTimelogPolling();
      if (oauthPollersRef.current.accounting) window.clearInterval(oauthPollersRef.current.accounting);
    };
  }, []);

  useEffect(() => { if (tenantId && serviceId) fetchConnectionStatus(tenantId, serviceId); }, [tenantId, serviceId]);

  useEffect(() => {
    if (tenantId && serviceId && isConnected) loadAutoSyncConfig(tenantId, serviceId);
  }, [tenantId, serviceId, isConnected]);

  useEffect(() => {
    const handleOAuthResult = async (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;

      const message = event.data as Partial<QuickBooksOAuthCallbackMessage> | null;
      if (message?.type !== QUICKBOOKS_OAUTH_CALLBACK_TYPE || !message.provider || !message.result) return;
      if (message.provider !== "accounting") return;
      if (!tenantId || !serviceId) return;

      const error = quickBooksOAuthErrorMessage(message.provider, message.result);
      if (oauthPollersRef.current.accounting) {
        window.clearInterval(oauthPollersRef.current.accounting);
        oauthPollersRef.current.accounting = null;
      }
      setLoading(false);
      setConnectionAction(null);
      await fetchConnectionRef.current(tenantId, serviceId);

      if (error) {
        setErrorMessage(error);
      } else {
        setErrorMessage(null);
        setToast({
          type: "success",
          message: "QuickBooks connected successfully.",
        });
      }
    };

    window.addEventListener("message", handleOAuthResult);
    return () => window.removeEventListener("message", handleOAuthResult);
  }, [tenantId, serviceId]);

  useEffect(() => {
    if (selectedModule === "vendors" && tenantId && serviceId && isConnected) fetchVendorCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, vendorPage, vendorPageSize, vendorSearchTerm, vendorSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "chart-of-accounts" && tenantId && serviceId && isConnected) fetchAccountCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, accountPage, accountPageSize, accountSearchTerm, accountSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "bills" && tenantId && serviceId && isConnected) fetchBillCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, billPage, billPageSize, billSearchTerm, billSyncFilter, billSortMode, isConnected]);

  useEffect(() => {
    if (selectedModule === "bill-payments" && tenantId && serviceId && isConnected) fetchBillPaymentCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, billPaymentPage, billPaymentPageSize, billPaymentSearchTerm, billPaymentSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "bill-payments" && tenantId && serviceId && isConnected) fetchBillPaymentAccounts(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, isConnected]);

  useEffect(() => {
    if (selectedModule === "customers" && tenantId && serviceId && isConnected) fetchCustomerCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, customerPage, customerPageSize, customerSearchTerm, customerSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "projects" && tenantId && serviceId && isConnected) fetchProjectCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, projectPage, projectPageSize, projectSearchTerm, projectSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "projects" && tenantId && serviceId && isConnected) loadProjectSyncConfig();
  }, [selectedModule, tenantId, serviceId, isConnected]);

  useEffect(() => {
    if (selectedModule === "employees" && tenantId && serviceId && isConnected) fetchEmployeeCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, employeePage, employeePageSize, employeeSearchTerm, employeeSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "employees" && tenantId && serviceId && isConnected) loadEmployeeSyncConfig();
  }, [selectedModule, tenantId, serviceId, isConnected]);

  useEffect(() => {
    if (selectedModule === "services" && tenantId && serviceId && isConnected) fetchServiceRoleCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, serviceRolePage, serviceRolePageSize, serviceRoleSearchTerm, serviceRoleSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "services" && tenantId && serviceId && isConnected) fetchServiceIncomeAccounts(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, isConnected]);

  useEffect(() => {
    if (selectedModule === "invoices" && tenantId && serviceId && isConnected) fetchInvoiceCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, invoicePage, invoicePageSize, invoiceSearchTerm, invoiceSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "payments" && tenantId && serviceId && isConnected) fetchPaymentCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, paymentPage, paymentPageSize, paymentSearchTerm, paymentSyncFilter, isConnected]);

  useEffect(() => {
    if (selectedModule === "timelogs" && tenantId && serviceId && isConnected) fetchTimelogCdcView(tenantId, serviceId);
  }, [selectedModule, tenantId, serviceId, timelogPage, timelogPageSize, timelogSearchTerm, timelogSyncFilter, isConnected]);

  // Proactively fetch summaries for prerequisite modules so ModuleDependencyHint
  // shows correct status without requiring the user to visit each dep first.
  useEffect(() => {
    if (!tenantId || !serviceId || !isConnected) return;
    const deps = MODULE_DEPENDENCIES[selectedModule] ?? [];
    deps.forEach((dep) => {
      if (dep === "employees")          fetchEmployeeRef.current(tenantId, serviceId);
      else if (dep === "projects")      fetchProjectRef.current(tenantId, serviceId);
      else if (dep === "services")      fetchServiceRoleRef.current(tenantId, serviceId);
      else if (dep === "customers")     fetchCustomerRef.current(tenantId, serviceId);
      else if (dep === "chart-of-accounts") fetchAccountRef.current(tenantId, serviceId);
      else if (dep === "vendors")       fetchVendorRef.current(tenantId, serviceId);
      else if (dep === "bills")         fetchBillRef.current(tenantId, serviceId);
      else if (dep === "invoices")      fetchInvoiceRef.current(tenantId, serviceId);
    });
  }, [selectedModule, tenantId, serviceId, isConnected]);

  useEffect(() => {
    setSelectedBillPaymentIds([]);
  }, [billPaymentPage, billPaymentSearchTerm, billPaymentSyncFilter, billPaymentPageSize]);

  useEffect(() => {
    setSelectedServiceRoleIds([]);
  }, [serviceRolePage, serviceRoleSearchTerm, serviceRoleSyncFilter, serviceRolePageSize]);

  const openOAuthPopup = (popup: Window, authorizationUrl: string) => {
    popup.location.href = authorizationUrl;
    popup.focus();
    if (oauthPollersRef.current.accounting) window.clearInterval(oauthPollersRef.current.accounting);
    const openedAt = Date.now();
    const poll = window.setInterval(async () => {
      if (!tenantId || !serviceId) return;
      if (popup.closed) {
        window.clearInterval(poll);
        oauthPollersRef.current.accounting = null;
        setLoading(false);
        setConnectionAction(null);
        await fetchConnectionStatus(tenantId, serviceId);
        return;
      }
      if (Date.now() - openedAt >= OAUTH_POPUP_TIMEOUT_MS) {
        window.clearInterval(poll);
        oauthPollersRef.current.accounting = null;
        popup.close();
        setLoading(false);
        setConnectionAction(null);
        setErrorMessage("QuickBooks sign-in timed out. Please try again.");
      }
    }, 500);
    oauthPollersRef.current.accounting = poll;
  };

  const handleConnect = async () => {
    if (!tenantId || !serviceId) return;
    setToast(null); setErrorMessage(null);
    const popup = window.open("about:blank", "qbo_oauth", `width=700,height=800,left=${window.screenX + 80},top=${window.screenY + 40},resizable=yes,scrollbars=yes`);
    if (!popup) {
      setErrorMessage("Your browser blocked the QuickBooks sign-in window. Allow pop-ups and try again.");
      return;
    }
    try {
      setLoading(true); setConnectionAction("connecting");
      const res = await initiateQuickBooksAuth(accessToken, tenantId, serviceId, objectId);
      const authorizationUrl = res?.data?.credentials?.authorizationUrl || res?.data?.authUrl;
      if (!authorizationUrl) throw new Error("QuickBooks service is temporarily unavailable.");
      openOAuthPopup(popup, authorizationUrl);
    } catch (e: any) {
      if (!popup.closed) popup.close();
      setLoading(false); setConnectionAction(null); setErrorMessage(getQuickBooksErrorMessage(e));
    }
  };

  const handleDisconnect = async () => {
    if (!tenantId || !serviceId) return;
    stopVendorPolling();
    stopAccountPolling();
    stopBillPolling();
    stopBillPaymentPolling();
    stopCustomerPolling();
    stopProjectPolling();
    stopInvoicePolling();
    stopPaymentPolling();
    stopEmployeePolling();
    stopServiceRolePolling();
    stopTimelogPolling();
    try {
      setLoading(true); setConnectionAction("disconnecting");
      await disconnectQuickBooks(accessToken, tenantId, serviceId, objectId);
      setConnection({ connected: false, reconnectRequired: false, authError: null });
      setLoadedModules(new Set());
      setVendorCdcData({ summary: emptyVendorSummary, pagination: { page: 1, pageSize: vendorPageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setAccountCdcData({ summary: emptyAccountSummary, pagination: { page: 1, pageSize: accountPageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setBillCdcData({ summary: emptyBillSummary, pagination: { page: 1, pageSize: billPageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setBillPaymentCdcData({ summary: emptyBillPaymentSummary, pagination: { page: 1, pageSize: billPaymentPageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setCustomerCdcData({ summary: emptyCustomerSummary, pagination: { page: 1, pageSize: customerPageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setProjectCdcData({ summary: emptyProjectSummary, pagination: { page: 1, pageSize: projectPageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setInvoiceCdcData({ summary: emptyInvoiceSummary, pagination: { page: 1, pageSize: invoicePageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setPaymentCdcData({ summary: emptyCustomerPaymentSummary, pagination: { page: 1, pageSize: paymentPageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setEmployeeCdcData({ summary: emptyEmployeeSummary, pagination: { page: 1, pageSize: employeePageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setServiceRoleCdcData({ summary: emptyServiceRoleSummary, pagination: { page: 1, pageSize: serviceRolePageSize, totalRecords: 0, totalPages: 0 }, rows: [] });
      setServiceIncomeAccounts([]);
      setSelectedServiceRoleIds([]);
      setBillPaymentEligibleAccounts([]);
      setSelectedBillPaymentIds([]);
      setSelectedBillPaymentAccountId("");
      setToast({ type: "success", message: "QuickBooks disconnected successfully." });
    } catch (e: any) { setErrorMessage(getQuickBooksErrorMessage(e)); }
    finally { setLoading(false); setConnectionAction(null); }
  };

  // The settings panel reports the outcome itself, so this raises no toast.
  const handleAutoSyncToggle = async (enabled: boolean) => {
    if (!tenantId || !serviceId || autoSyncStatus === "saving") return;

    const previous = autoSyncEnabled;
    setAutoSyncEnabled(enabled);
    setAutoSyncStatus("saving");
    setErrorMessage(null);
    try {
      const res = await saveQuickBooksAutoSyncConfig(accessToken, {
        tenant_id: tenantId,
        service_id: serviceId,
        enabled,
        object_id: objectId,
      });
      if (res?.data?.ok === false) throw new Error(res?.data?.message || "Unable to save auto sync setting.");
      setAutoSyncStatus("saved");
    } catch (e: any) {
      setAutoSyncEnabled(previous);
      setAutoSyncStatus("error");
      setErrorMessage(getQuickBooksErrorMessage(e) || "Unable to save QuickBooks auto sync setting.");
    }
  };

  const reconnectMessage = connection.authError?.detail || connection.authError?.reason || "QuickBooks needs to be reconnected before sync can continue.";

  const moduleSummaryBag: ModuleSummaryBag = useMemo(() => ({
    customers:           customerCdcData.summary,
    vendors:             vendorCdcData.summary,
    "chart-of-accounts": accountCdcData.summary,
    employees:           employeeCdcData.summary,
    projects:            projectCdcData.summary,
    invoices:            invoiceCdcData.summary,
    payments:            paymentCdcData.summary,
    services:            serviceRoleCdcData.summary,
    bills:               billCdcData.summary,
    "bill-payments":     billPaymentCdcData.summary,
    timelogs:            timelogCdcData.summary,
  }), [customerCdcData.summary, vendorCdcData.summary, accountCdcData.summary, employeeCdcData.summary, projectCdcData.summary, serviceRoleCdcData.summary, billCdcData.summary, billPaymentCdcData.summary, timelogCdcData.summary, invoiceCdcData.summary, paymentCdcData.summary]);

  // ── Render ──────────────────────────────────────────────────

  /**
   * "What this module attaches to", rendered into the trailing space of whichever
   * module header is on screen (its `context` slot) rather than as a row of its own.
   *
   * Built once here instead of per pane: the panes are mutually exclusive on
   * `selectedModule`, so the single header that is mounted is always this module's.
   * `ModuleDependencyHint` renders nothing for a module that attaches to nothing.
   */
  const moduleContextSlot = (
    <ModuleDependencyHint
      moduleId={selectedModule}
      summaries={moduleSummaryBag}
      onNavigate={setSelectedModule}
      loadedModules={loadedModules}
      onOpenGuide={() => setSyncGuideOpen(true)}
    />
  );

  const accountingStatus: QbCapStatus = isReconnectRequired ? "reconnect" : isConnected ? "on" : "off";

  if (hasLoadedOnce && !isConnected) {
    return (
      <Box className="qb-shell qb-shell--connect">
        <QuickBooksConnection
          accounting={{ status: accountingStatus, busy: isConnecting || isDisconnecting || isFetchingStatus }}
          onConnectAccounting={handleConnect}
          onReconnectAccounting={handleConnect}
          onDisconnectAccounting={handleDisconnect}
          disabled={!tenantId || !serviceId}
        />
      </Box>
    );
  }

  return (
    <Box className="qb-shell">


      {/* ═══ MAIN CONTENT ══════════════════════════════════════ */}
      <Box
        className={`qb-main-content ${moduleThemeClass(activeModuleDef.id)}`}
      >

        {/* Toast + alerts (no top progress bar — loading is shown as a skeleton). */}
        {toast && <Alert className="qb-sx-046 qbw-toast" severity={toast.type} onClose={() => setToast(null)}>{toast.message}</Alert>}
        {errorMessage && !isReconnectRequired && (
          <Alert className="qb-sx-047 qbw-toast" severity="error" icon={<ErrorOutlineRoundedIcon />} onClose={() => setErrorMessage(null)}>
            {errorMessage}
          </Alert>
        )}
        {capabilityError && (
          <Alert severity="warning" icon={<WarningAmberRoundedIcon />} onClose={() => setCapabilityError(null)} className="qb-sx-047">
            <strong>QuickBooks plan upgrade required</strong> — {capabilityError}
          </Alert>
        )}

        {/* ── INITIAL LOADING — skeleton of the workspace, not a spinner. Gated on
            !isConnected/!isReconnectRequired so it NEVER stacks above the real
            workspace once the connection state is known (connected flips true a beat
            before hasLoadedOnce, so !hasLoadedOnce alone would double-render). ── */}
        {!hasLoadedOnce && !isConnected && !isReconnectRequired && (
          <WorkspaceSkeleton />
        )}

        {/* ── NOT CONNECTED — show landing ── */}
        {hasLoadedOnce && !isConnected && !isReconnectRequired && (
          <NotConnectedLanding />
        )}

        {/* ── RECONNECT REQUIRED ── */}
        {hasLoadedOnce && isReconnectRequired && (
          <ReconnectCard
            onReconnect={handleConnect}
            loading={loading || isFetchingStatus}
            message={reconnectMessage}
          />
        )}

        {/* ── CONNECTED — full dashboard ── */}
        {isConnected && (
          <>
          <Box className="qbw-root qbw-embedded">
            <QuickBooksWorkspaceRail
              groups={buildRailGroups()}
              selectedId={selectedModule}
              onSelect={setSelectedModule}
              statusLabel="Connected"
              footer={
                <QuickBooksRailFooter
                  autoSyncEnabled={autoSyncEnabled}
                  autoSyncStatus={autoSyncStatus}
                  onAutoSyncChange={handleAutoSyncToggle}
                  onOpenGuide={() => setSyncGuideOpen(true)}
                  onDisconnect={handleDisconnect}
                  disconnecting={isDisconnecting}
                />
              }
            />
            <Box className="qbw-work">
            {/* Sync-in-progress banners — calm, unified across modules */}
            {isModuleSyncing("vendors", vendorAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing vendors"
                status={vendorAnyRunning ? vendorJobStatus : SYNC_STARTING_STATUS}
                detail="Running in the background — the table updates automatically when it's done."
              />
            )}
            {isModuleSyncing("chart-of-accounts", accountAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing chart of accounts"
                status={accountAnyRunning ? accountJobStatus : SYNC_STARTING_STATUS}
                detail="Running in the background — the table updates automatically when it's done."
              />
            )}
            {isModuleSyncing("bills", billAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing bills"
                status={billAnyRunning ? billJobStatus : SYNC_STARTING_STATUS}
                detail="Running in the background — the table updates automatically when it's done."
              />
            )}
            {isModuleSyncing("customers", customerAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing customers"
                status={customerAnyRunning ? customerJobStatus : SYNC_STARTING_STATUS}
                detail="Running in the background — the table updates automatically when it's done."
              />
            )}
            {isModuleSyncing("projects", projectAnyRunning) && (
              <QuickBooksSyncBanner
                title="Exporting projects"
                status={projectAnyRunning ? projectJobStatus : SYNC_STARTING_STATUS}
                detail={`Exporting your projects to QuickBooks as ${
                  projectSyncConfig.effectiveProjectSyncTarget === "QBO_PROJECT" ? "QuickBooks Projects" : "sub-customers"
                } — the table updates when it's done.`}
              />
            )}
            {isModuleSyncing("bill-payments", billPaymentAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing bill payments"
                status={billPaymentAnyRunning ? billPaymentJobStatus : SYNC_STARTING_STATUS}
                detail="Running in the background — assigned rows refresh automatically when it's done."
              />
            )}
            {isModuleSyncing("employees", employeeAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing employees"
                status={employeeAnyRunning ? employeeJobStatus : SYNC_STARTING_STATUS}
                detail="Running in the background — the table updates automatically when it's done."
              />
            )}
            {isModuleSyncing("services", serviceRoleAnyRunning) && (
              <QuickBooksSyncBanner
                title="Exporting services"
                status={serviceRoleAnyRunning ? serviceRoleJobStatus : SYNC_STARTING_STATUS}
                detail="Exporting your roles to QuickBooks as service items — the table updates when it's done."
              />
            )}
            {isModuleSyncing("invoices", invoiceAnyRunning) && (
              <QuickBooksSyncBanner
                title="Exporting invoices"
                status={invoiceAnyRunning ? invoiceJobStatus : SYNC_STARTING_STATUS}
                detail="Sending issued FIELDCRAFT project invoices to QuickBooks — the table updates when it's done."
              />
            )}
            {isModuleSyncing("payments", paymentAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing customer payments"
                status={paymentAnyRunning ? paymentJobStatus : SYNC_STARTING_STATUS}
                detail="Sending FIELDCRAFT customer receipts and reconciled invoice allocations to QuickBooks — the table updates when it's done."
              />
            )}
            {isModuleSyncing("timelogs", timelogAnyRunning) && (
              <QuickBooksSyncBanner
                title="Syncing time activity"
                status={timelogAnyRunning ? timelogJobStatus : SYNC_STARTING_STATUS}
                detail="Sending completed time to QuickBooks — the table updates when it's done."
              />
            )}

            {/* Legacy navigators — superseded by the rail. Kept mounted but
                hidden during the staged migration to avoid orphaning shared
                state/imports; removed once the workspace refactor is verified. */}
            <Box sx={{ display: "none" }} aria-hidden="true">
            {/* ═══ MODULE SELECTOR ══════════════════════════════ */}
            <Box className="qb-modules-section">
              <Typography className="qb-modules-label">Sync Modules</Typography>
              <Box className="qb-modules-row">
                {SYNC_MODULES.map((mod) => (
                  <ModuleCard key={mod.id} module={mod} active={selectedModule === mod.id} onClick={() => setSelectedModule(mod.id)} />
                ))}
              </Box>
            </Box>

            {/* ─── Module Context Bridge ─────────────────────── */}
            <Box
              key={activeModuleDef.id}
              className="qb-module-context-bridge"
            >
              <Box className="qb-mcb-icon">
                {activeModuleDef.icon}
              </Box>
              <Box className="qb-sx-068">
                <Typography className="qb-mcb-name">
                  {activeModuleDef.label}
                </Typography>
                <Typography className="qb-mcb-desc">Filter · Search · Review sync records</Typography>
              </Box>
            </Box>
            </Box>

            {/* ═══ VENDORS (redesigned) ═══════════════════════ */}
            {selectedModule === "vendors" && (() => {
              const vs = vendorCdcData.summary;
              const vRows = vendorCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("vendors", vs);
              const vendorFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: vs.totalVendors },
                { id: "SYNCED", label: "Synced", count: vs.syncedVendors, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: vs.pendingPushVendors, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeVendorFilter =
                vendorSyncFilter === "SYNCED" ? "SYNCED"
                  : vendorSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : (vendorSyncFilter === "FAILED" || vendorSyncFilter === "IMPORT_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleVendorFilter = (id: string) => {
                setVendorPage(0);
                setVendorSyncFilter(id === "NEEDS_ATTENTION" ? "FAILED" : id);
              };
              const vendorColumns: DataColumn<VendorRow>[] = [
                {
                  key: "vendor",
                  header: "Vendor",
                  render: (row) => (
                    <QbIdentityCell name={row.displayName} source={row.source} email={row.email} />
                  ),
                },
                {
                  key: "company",
                  header: "Company",
                  render: (row) => row.companyName || "—",
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell
                      status={row.syncStatus}
                      linked={row.linked}
                      reason={row.reason ? parseReasonMessage(row.reason) : undefined}
                    />
                  ),
                },
                {
                  key: "qbid",
                  header: "QuickBooks ID",
                  render: (row) =>
                    row.externalVendorId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalVendorId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not linked</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Vendors"
                    titleIcon={activeModuleDef.icon}
                    titleHint={<QuickBooksMissingRecordNote noun="vendor" />}
                    context={moduleContextSlot}
                    description="Vendors and 1099 contractors, kept in sync with QuickBooks."
                    onSync={handleVendorSync}
                    syncing={isModuleSyncing("vendors", vendorAnyRunning)}
                    syncButtonLabel="Sync vendors"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    showSyncMode
                    syncMode={vendorSyncMode}
                    onSyncModeChange={setVendorSyncMode}
                    search={vendorSearchInput}
                    onSearchChange={setVendorSearchInput}
                    searchPlaceholder="Search by name, company, or email…"
                    filters={vendorFilters}
                    activeFilter={activeVendorFilter}
                    onFilterChange={handleVendorFilter}
                  />
                  <QuickBooksDataTable
                    columns={vendorColumns}
                    rows={vRows}
                    rowKey={(row) => `${row.vendorId}-${row.mappingId || "none"}`}
                    loading={vendorTableLoading}
                    emptyMessage="No vendor records match your filters."
                    page={vendorPage}
                    pageSize={vendorPageSize}
                    total={vendorCdcData.pagination.totalRecords}
                    onPageChange={setVendorPage}
                    onPageSizeChange={(n) => { setVendorPageSize(n); setVendorPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ CHART OF ACCOUNTS (redesigned) ═════════════════ */}
            {selectedModule === "chart-of-accounts" && (() => {
              const as = accountCdcData.summary;
              const aRows = accountCdcData.rows || [];
              const accountFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: as.totalAccounts },
                { id: "SYNCED", label: "Synced", count: as.syncedAccounts, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: as.pendingPushAccounts, tone: "wait" },
              ];
              const activeAccountFilter =
                accountSyncFilter === "SYNCED" ? "SYNCED"
                  : accountSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : "ALL";
              const handleAccountFilter = (id: string) => {
                setAccountPage(0);
                setAccountSyncFilter(id);
              };
              const accountColumns: DataColumn<AccountRow>[] = [
                {
                  key: "account",
                  header: "Account",
                  render: (row) => (
                    <QbIdentityCell
                      name={row.name}
                      icon={accountTypeIcon(row.accountType)}
                      sub={row.accountNo || undefined}
                    />
                  ),
                },
                {
                  key: "type",
                  header: "Type",
                  render: (row) => (
                    <span className="qbw-cell-lines">
                      <span>{row.accountType || "—"}</span>
                      {row.detailType && row.detailType !== row.accountType ? (
                        <span className="qbw-cell-meta">{row.detailType}</span>
                      ) : null}
                    </span>
                  ),
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QuickBooks ID",
                  render: (row) =>
                    row.externalId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not synced</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Updated",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.updatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Chart of Accounts"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="Your QuickBooks accounts, imported and kept in sync."
                    onSync={handleAccountSync}
                    syncing={isModuleSyncing("chart-of-accounts", accountAnyRunning)}
                    syncButtonLabel="Sync accounts"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    search={accountSearchInput}
                    onSearchChange={setAccountSearchInput}
                    searchPlaceholder="Search accounts…"
                    filters={accountFilters}
                    activeFilter={activeAccountFilter}
                    onFilterChange={handleAccountFilter}
                  />
                  <QuickBooksDataTable
                    columns={accountColumns}
                    rows={aRows}
                    rowKey={(row) => `${row.accountId}-${row.externalId || "none"}`}
                    loading={accountTableLoading}
                    emptyMessage="No accounts match your filters."
                    page={accountPage}
                    pageSize={accountPageSize}
                    total={accountCdcData.pagination.totalRecords}
                    onPageChange={setAccountPage}
                    onPageSizeChange={(n) => { setAccountPageSize(n); setAccountPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ BILLS (redesigned) ═══════════════════════════ */}
            {selectedModule === "bills" && (() => {
              const bs = billCdcData.summary;
              const bRows = billCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("bills", bs);
              const billFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: bs.totalBills },
                { id: "SYNCED", label: "Synced", count: bs.syncedBills, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: bs.pendingPushBills, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeBillFilter =
                billSyncFilter === "SYNCED" ? "SYNCED"
                  : billSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : (billSyncFilter === "FAILED" || billSyncFilter === "IMPORT_FAILED" || billSyncFilter === "SYNC_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleBillFilter = (id: string) => {
                setBillPage(0);
                setBillSyncFilter(id === "NEEDS_ATTENTION" ? "FAILED" : id);
              };
              const billColumns: DataColumn<BillRow>[] = [
                {
                  key: "bill",
                  header: "Bill",
                  render: (row) => (
                    <QbIdentityCell
                      name={row.displayName}
                      // Stock bills move inventory rather than pay a vendor invoice, so the
                      // tile distinguishes them at a glance.
                      icon={isFieldCraftStockBill(row) ? <Inventory2OutlinedIcon /> : <ReceiptLongRoundedIcon />}
                      source={row.source}
                      sub={row.vendorCompany || undefined}
                      badge={isFieldCraftStockBill(row) ? (
                        <span className="qbw-stock">{stockBillBadgeLabel(row)}</span>
                      ) : row.billPaymentSyncRequired ? (
                        <Tooltip title="This bill is paid, but its bill payment has not been synced to QuickBooks yet. Sync Bill Payments to complete the payment sync." arrow>
                          <span className="qbw-mini-chip qbw-mini-chip--warn qbw-bill-payment-sync-needed">
                            <WarningAmberRoundedIcon />
                            Sync bill payment
                          </span>
                        </Tooltip>
                      ) : undefined}
                    />
                  ),
                },
                {
                  key: "date",
                  header: "Invoice Date",
                  render: (row) => <span className="qbw-last">{formatDateOnlyUtc(row.invoiceDate)}</span>,
                },
                {
                  key: "amount",
                  header: "Amount",
                  align: "right",
                  render: (row) =>
                    row.amount != null ? (
                      <span className="qbw-amount">{`$${Number(row.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}</span>
                    ) : "—",
                },
                {
                  key: "project",
                  header: "Project",
                  render: (row) =>
                    row.projectName ? (
                      <span className="qbw-proj">{row.projectName}</span>
                    ) : isFieldCraftStockBill(row) ? (
                      // Stock bills are inventory purchases — they never carry a project,
                      // so name the reason instead of showing an empty dash.
                      <span className="qbw-proj--stock">
                        <Inventory2OutlinedIcon />
                        {stockBillLabel(row)}
                      </span>
                    ) : row.syncStatus === "SYNCED_INCOMPLETE" ? (
                      <span className="qbw-proj--none"><WarningAmberRoundedIcon />No project linked</span>
                    ) : "—",
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} label={billStatusLabel(row)} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QuickBooks ID",
                  render: (row) =>
                    row.externalReceiptId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalReceiptId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not linked</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Bills"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="Vendor bills, kept in sync with QuickBooks. Tap a row for line items and the attached document."
                    onSync={handleBillSync}
                    syncing={isModuleSyncing("bills", billAnyRunning)}
                    syncButtonLabel="Sync bills"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    showSyncMode
                    syncMode={billSyncMode}
                    onSyncModeChange={setBillSyncMode}
                    search={billSearchInput}
                    onSearchChange={setBillSearchInput}
                    searchPlaceholder="Search bills, vendors, or projects…"
                    filters={billFilters}
                    activeFilter={activeBillFilter}
                    onFilterChange={handleBillFilter}
                  />
                  <QuickBooksDataTable
                    columns={billColumns}
                    rows={bRows}
                    rowKey={(row) => `${row.receiptId ?? row.externalReceiptId ?? "row"}-${row.mappingId ?? "none"}`}
                    onRowClick={handleBillRowClick}
                    loading={billTableLoading}
                    emptyMessage="No bills match your filters."
                    page={billPage}
                    pageSize={billPageSize}
                    total={billCdcData.pagination.totalRecords}
                    onPageChange={setBillPage}
                    onPageSizeChange={(n) => { setBillPageSize(n); setBillPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ BILL PAYMENTS (redesigned) ═══════════════════ */}
            {selectedModule === "bill-payments" && (() => {
              const ps = billPaymentCdcData.summary;
              const pRows = billPaymentCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("billPayments", ps);
              const bpFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: ps.totalBillPayments },
                { id: "ASSIGNMENT_NEEDED", label: "Assignment needed", count: ps.unassignedBillPayments, tone: "warn" },
                { id: "SYNCED", label: "Synced", count: ps.syncedBillPayments, tone: "ok" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeBpFilter =
                billPaymentSyncFilter === "ASSIGNMENT_NEEDED" ? "ASSIGNMENT_NEEDED"
                  : billPaymentSyncFilter === "SYNCED" ? "SYNCED"
                  : (billPaymentSyncFilter === "FAILED" || billPaymentSyncFilter === "IMPORT_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleBpFilter = (id: string) => {
                setBillPaymentPage(0);
                setBillPaymentSyncFilter(id === "NEEDS_ATTENTION" ? "FAILED" : id);
              };
              const bpColumns: DataColumn<BillPaymentCdcRow>[] = [
                {
                  key: "payment",
                  header: "Payment",
                  render: (row) => (
                    <QbIdentityCell
                      name={row.displayName}
                      icon={<AccountBalanceWalletRoundedIcon />}
                      source={row.source}
                      sub={row.vendorCompany || undefined}
                    />
                  ),
                },
                {
                  key: "bill",
                  header: "Bill",
                  render: (row) => {
                    const billLabel = row.billDisplayName || row.billInvoiceNumber || row.billName;
                    return billLabel ? (
                      <span>{billLabel}</span>
                    ) : (
                      <span className="qbw-cell-meta">Bill not linked</span>
                    );
                  },
                },
                {
                  key: "date",
                  header: "Date",
                  render: (row) => <span className="qbw-last">{formatDateOnlyUtc(row.paymentDate)}</span>,
                },
                {
                  key: "amount",
                  header: "Amount",
                  align: "right",
                  render: (row) =>
                    row.amount != null ? (
                      <span className="qbw-amount">{`$${Number(row.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}</span>
                    ) : "—",
                },
                {
                  key: "account",
                  header: "Payment Account",
                  render: (row) => {
                    const assigned = row.assignedAccountName || row.externalAccountId;
                    if (!assigned) return <span className="qbw-mini-chip qbw-mini-chip--warn">Account required</span>;
                    const isCC = row.assignedEligiblePayType === "CreditCard";
                    const meta = [row.assignedAccountType, row.assignedAccountDetailType].filter(Boolean).join(" • ");
                    return (
                      <span className="qbw-cell-lines">
                        <span>{isCC ? "💳" : "🏦"} {assigned}</span>
                        {meta ? <span className="qbw-cell-meta">{meta}</span> : null}
                      </span>
                    );
                  },
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} linked={row.linked} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QuickBooks ID",
                  render: (row) =>
                    row.externalPaymentId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalPaymentId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not linked</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Bill Payments"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="Payments against QuickBooks bills. Assign a payment account, then sync."
                    onSync={handleBillPaymentSync}
                    syncing={isModuleSyncing("bill-payments", billPaymentAnyRunning)}
                    syncButtonLabel="Sync bill payments"
                    syncDisabled={anyRunning || syncStartPending || !billPaymentEligibleAccounts.length}
                    syncDisabledReason={syncBusyReason ?? (!billPaymentEligibleAccounts.length ? "Sync your Chart of Accounts first — there are no eligible QuickBooks payment accounts yet." : undefined)}
                    showSyncMode
                    syncMode={billPaymentSyncMode}
                    onSyncModeChange={setBillPaymentSyncMode}
                    search={billPaymentSearchInput}
                    onSearchChange={setBillPaymentSearchInput}
                    searchPlaceholder="Search payments, vendors, or bills…"
                    filters={bpFilters}
                    activeFilter={activeBpFilter}
                    onFilterChange={handleBpFilter}
                    actions={selectedBillPaymentIds.length > 0 ? (
                      <button type="button" className="qbw-btn qbw-btn-primary" onClick={() => setAssignDialogOpen(true)}>
                        Assign account ({selectedBillPaymentIds.length})
                      </button>
                    ) : undefined}
                  />
                  <QuickBooksDataTable
                    columns={bpColumns}
                    rows={pRows}
                    rowKey={(row) => `${row.paymentExpenseId ?? "row"}-${row.mappingId ?? "none"}`}
                    selection={{
                      isSelected: (row) => row.paymentExpenseId != null && selectedBillPaymentIdsSet.has(row.paymentExpenseId),
                      onToggle: (row) => { if (row.paymentExpenseId != null) toggleBillPaymentSelection(row.paymentExpenseId); },
                      allSelected: allBillPaymentsOnPageSelected,
                      someSelected: someBillPaymentsOnPageSelected,
                      onToggleAll: toggleSelectAllBillPaymentsOnPage,
                    }}
                    loading={billPaymentTableLoading}
                    emptyMessage="No bill payments match your filters."
                    page={billPaymentPage}
                    pageSize={billPaymentPageSize}
                    total={billPaymentCdcData.pagination.totalRecords}
                    onPageChange={setBillPaymentPage}
                    onPageSizeChange={(n) => { setBillPaymentPageSize(n); setBillPaymentPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ CUSTOMERS (redesigned) ═══════════════════════ */}
            {selectedModule === "customers" && (() => {
              const cs = customerCdcData.summary;
              const cRows = customerCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("customers", cs);
              const customerFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: cs.totalCustomers },
                { id: "SYNCED", label: "Synced", count: cs.syncedCustomers, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: cs.pendingPushCustomers, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeCustomerFilter =
                customerSyncFilter === "SYNCED" ? "SYNCED"
                  : customerSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : (customerSyncFilter === "NEEDS_ATTENTION" || customerSyncFilter === "FAILED" || customerSyncFilter === "IMPORT_FAILED" || customerSyncFilter === "SYNC_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleCustomerFilter = (id: string) => {
                setCustomerPage(0);
                // "Needs attention" spans failed + import-failed + sync-failed; the backend
                // expands the NEEDS_ATTENTION sentinel to that set, so pass the pill id through.
                setCustomerSyncFilter(id);
              };
              const customerColumns: DataColumn<CustomerRow>[] = [
                {
                  key: "customer",
                  header: "Customer",
                  render: (row) => (
                    <QbIdentityCell name={row.displayName} source={row.source} email={row.email} />
                  ),
                },
                {
                  key: "status",
                  header: "Status",
                  render: (row) => {
                    const raw = fieldcraftStatusLabel(row.customerStatus);
                    const label = raw || "Not in FIELDCRAFT";
                    const inactive = !raw || /inactive|not\b/i.test(label);
                    return (
                      <span className={`qbw-fieldcraft-pill${inactive ? " qbw-fieldcraft-pill--inactive" : ""}`}>
                        <span className="qbw-fieldcraft-pill-dot" />
                        {label}
                      </span>
                    );
                  },
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell
                      status={row.syncStatus}
                      linked={row.linked}
                      reason={row.reason ? parseReasonMessage(row.reason) : undefined}
                    />
                  ),
                },
                {
                  key: "qbid",
                  header: "QuickBooks ID",
                  render: (row) =>
                    row.externalCustomerId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalCustomerId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not linked</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Customers"
                    titleIcon={activeModuleDef.icon}
                    titleHint={<QuickBooksMissingRecordNote noun="customer" />}
                    context={moduleContextSlot}
                    description="All customers, kept in sync with QuickBooks."
                    onSync={handleCustomerSync}
                    syncing={isModuleSyncing("customers", customerAnyRunning)}
                    syncButtonLabel="Sync customers"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    showSyncMode
                    syncMode={customerSyncMode}
                    onSyncModeChange={setCustomerSyncMode}
                    search={customerSearchInput}
                    onSearchChange={setCustomerSearchInput}
                    searchPlaceholder="Search by name, company, or email…"
                    filters={customerFilters}
                    activeFilter={activeCustomerFilter}
                    onFilterChange={handleCustomerFilter}
                  />
                  <QuickBooksDataTable
                    columns={customerColumns}
                    rows={cRows}
                    rowKey={(row) => `${row.customerId}-${row.mappingId || "none"}`}
                    loading={customerTableLoading}
                    emptyMessage="No customer records match your filters."
                    page={customerPage}
                    pageSize={customerPageSize}
                    total={customerCdcData.pagination.totalRecords}
                    onPageChange={setCustomerPage}
                    onPageSizeChange={(n) => { setCustomerPageSize(n); setCustomerPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ PROJECTS (redesigned) ══════════════════════════ */}
            {selectedModule === "projects" && (() => {
              const ps = projectCdcData.summary;
              const pRows = projectCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("projects", ps);
              const projectFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: ps.total },
                { id: "SYNCED", label: "Synced", count: ps.synced, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: ps.pending, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeProjectFilter =
                projectSyncFilter === "SYNCED" ? "SYNCED"
                  : projectSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : (projectSyncFilter === "FAILED" || projectSyncFilter === "SYNC_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleProjectFilter = (id: string) => {
                setProjectPage(0);
                setProjectSyncFilter(id === "NEEDS_ATTENTION" ? "FAILED" : id);
              };
              const projectColumns: DataColumn<ProjectRow>[] = [
                {
                  key: "project",
                  header: "Project",
                  render: (row) => (
                    <QbIdentityCell
                      name={row.displayName}
                      icon={<FolderSpecialRoundedIcon />}
                      source={row.source || "FIELDCRAFT"}
                      allowWrap
                    />
                  ),
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QuickBooks Target",
                  render: (row) =>
                    row.externalProjectId ? (
                      <span className="qbw-cell-lines">
                        <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalProjectId}</span>
                        <span className="qbw-cell-meta">{row.projectSyncTarget === "QBO_PROJECT" ? "QuickBooks Project" : "Sub-customer"}</span>
                      </span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not linked</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Projects"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="FIELDCRAFT projects, synced to QuickBooks as projects or sub-customers."
                    onSync={handleProjectSync}
                    syncing={isModuleSyncing("projects", projectAnyRunning)}
                    syncButtonLabel="Sync projects"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    search={projectSearchInput}
                    onSearchChange={setProjectSearchInput}
                    searchPlaceholder="Search projects…"
                    filters={projectFilters}
                    activeFilter={activeProjectFilter}
                    onFilterChange={handleProjectFilter}
                    actions={
                      <button
                        type="button"
                        className="qbw-btn qbw-btn-ghost"
                        onClick={handleOpenProjectSyncConfig}
                        disabled={loading || isFetchingStatus}
                      >
                        <AccountTreeRoundedIcon />
                        {projectSyncConfig.method === "QBO_PROJECT"
                          ? "QuickBooks Project"
                          : projectSyncConfig.method === "SUB_CUSTOMER"
                          ? "Sub-customer"
                          : "Auto"}
                      </button>
                    }
                  />
                  <QuickBooksDataTable
                    columns={projectColumns}
                    rows={pRows}
                    rowKey={(row) => `${row.projectId ?? row.projectUid ?? "row"}-${row.mappingId || "none"}`}
                    loading={projectTableLoading}
                    emptyMessage="No projects match your filters."
                    page={projectPage}
                    pageSize={projectPageSize}
                    total={projectCdcData.pagination.totalRecords}
                    onPageChange={setProjectPage}
                    onPageSizeChange={(n) => { setProjectPageSize(n); setProjectPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ EMPLOYEES (redesigned) ═════════════════════════ */}
            {selectedModule === "employees" && (() => {
              const es = employeeCdcData.summary;
              const eRows = employeeCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("employees", es);
              const employeeFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: es.totalEmployees },
                { id: "SYNCED", label: "Synced", count: es.syncedEmployees, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: es.pendingPushEmployees, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeEmployeeFilter =
                employeeSyncFilter === "SYNCED" ? "SYNCED"
                  : employeeSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : (employeeSyncFilter === "FAILED" || employeeSyncFilter === "IMPORT_FAILED" || employeeSyncFilter === "SYNC_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleEmployeeFilter = (id: string) => {
                setEmployeePage(0);
                setEmployeeSyncFilter(id === "NEEDS_ATTENTION" ? "FAILED" : id);
              };
              const employeeColumns: DataColumn<EmployeeRow>[] = [
                {
                  key: "employee",
                  header: "Employee",
                  render: (row) => {
                    const wt = resolveEmployeeWorkerTypeLabel(row);
                    return (
                      <QbIdentityCell
                        name={row.displayName}
                        source={row.source}
                        email={row.email}
                        badge={wt ? (
                          <span className={`qbw-mini-chip qbw-mini-chip--${wt === "1099" ? "1099" : "w2"}`}>
                            {wt === "1099" ? "1099" : "W-2"}
                          </span>
                        ) : undefined}
                      />
                    );
                  },
                },
                {
                  key: "status",
                  header: "FIELDCRAFT Status",
                  render: (row) => {
                    const active = row.active === true;
                    const label = row.active == null ? "Unknown" : active ? "Active" : "Inactive";
                    return (
                      <span className={`qbw-fieldcraft-pill${active ? "" : " qbw-fieldcraft-pill--inactive"}`}>
                        <span className="qbw-fieldcraft-pill-dot" />
                        {label}
                      </span>
                    );
                  },
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} linked={row.linked} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QuickBooks ID",
                  render: (row) =>
                    row.externalEmployeeId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalEmployeeId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not linked</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Employees"
                    titleIcon={activeModuleDef.icon}
                    titleHint={<QuickBooksMissingRecordNote noun="employee" />}
                    context={moduleContextSlot}
                    description="W-2 employees and 1099 contractors, kept in sync with QuickBooks."
                    onSync={handleEmployeeSync}
                    syncing={isModuleSyncing("employees", employeeAnyRunning)}
                    syncButtonLabel="Sync employees"
                    syncDisabled={anyRunning || syncStartPending || employeeSyncConfigured === false}
                    syncDisabledReason={syncBusyReason ?? (employeeSyncConfigured === false ? "Configure employee sync settings before the first sync." : undefined)}
                    showSyncMode
                    syncMode={employeeSyncMode}
                    onSyncModeChange={setEmployeeSyncMode}
                    search={employeeSearchInput}
                    onSearchChange={setEmployeeSearchInput}
                    searchPlaceholder="Search by name or email…"
                    filters={employeeFilters}
                    activeFilter={activeEmployeeFilter}
                    onFilterChange={handleEmployeeFilter}
                    actions={
                      <Tooltip title="Employee sync settings" arrow>
                        <IconButton
                          size="small"
                          onClick={handleOpenEmployeeSyncConfig}
                          disabled={loading || isFetchingStatus}
                          aria-label="Employee sync settings"
                        >
                          <EngineeringRoundedIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    }
                  />
                  {employeeSyncConfigured === false && (
                    <div className="qbw-setup-note" role="status">
                      <span className="qbw-setup-note-ico" aria-hidden="true"><InfoOutlinedIcon /></span>
                      <div className="qbw-setup-note-txt">
                        <span className="qbw-setup-note-title">Configure employee sync to get started</span>
                        <span className="qbw-setup-note-detail">
                          Set the work start date first — the Sync button turns on once your settings are saved.
                        </span>
                      </div>
                      <button
                        type="button"
                        className="qbw-btn qbw-btn-ghost"
                        onClick={handleOpenEmployeeSyncConfig}
                        disabled={loading || isFetchingStatus}
                      >
                        Configure settings
                      </button>
                    </div>
                  )}
                  <QuickBooksDataTable
                    columns={employeeColumns}
                    rows={eRows}
                    rowKey={(row) => `${row.employeeId}-${row.mappingId || "none"}`}
                    loading={employeeTableLoading}
                    emptyMessage="No employees match your filters."
                    page={employeePage}
                    pageSize={employeePageSize}
                    total={employeeCdcData.pagination.totalRecords}
                    onPageChange={setEmployeePage}
                    onPageSizeChange={(n) => { setEmployeePageSize(n); setEmployeePage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ INVOICES ══════════════════════════════════════ */}
            {selectedModule === "invoices" && (() => {
              const summary = invoiceCdcData.summary;
              const rows = invoiceCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("invoices", summary);
              const filters: FilterPill[] = [
                { id: "ALL", label: "All", count: summary.total },
                { id: "SYNCED", label: "Synced", count: summary.synced, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: summary.pending, tone: "wait" },
                { id: "NOT_READY", label: "Not ready", count: summary.notReady, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeFilter =
                invoiceSyncFilter === "SYNCED" ? "SYNCED"
                  : invoiceSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : invoiceSyncFilter === "NOT_READY" ? "NOT_READY"
                  : (invoiceSyncFilter === "NEEDS_ATTENTION" || invoiceSyncFilter === "FAILED" || invoiceSyncFilter === "SYNC_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleFilter = (id: string) => {
                setInvoicePage(0);
                setInvoiceSyncFilter(id);
              };
              const columns: DataColumn<InvoiceCdcRow>[] = [
                {
                  key: "invoice",
                  header: "Invoice",
                  render: (row) => (
                    <QbIdentityCell
                      name={row.invoiceNo || `Invoice #${row.invoiceId}`}
                      icon={<ReceiptLongRoundedIcon />}
                      sub={formatDateTime(row.invoiceDate) || "—"}
                    />
                  ),
                },
                {
                  key: "customer",
                  header: "Customer",
                  render: (row) => <span>{row.customerName || "Customer not available"}</span>,
                },
                {
                  key: "project",
                  header: "Project",
                  render: (row) => (
                    <span className="qbw-cell-lines">
                      <span>{row.projectName || "Project not available"}</span>
                      {row.invoiceStatus ? <span className="qbw-cell-meta">{row.invoiceStatus}</span> : null}
                    </span>
                  ),
                },
                {
                  key: "amount",
                  header: "Invoice Total",
                  align: "right",
                  render: (row) => (
                    <span className="qbw-cell-lines qbw-cell-lines--right">
                      <span>{formatCurrency(Number(row.amount || 0))}</span>
                    </span>
                  ),
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QB Invoice ID",
                  render: (row) => row.externalInvoiceId ? (
                    <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalInvoiceId}</span>
                  ) : (
                    <span className="qbw-qbid qbw-qbid--none">Not synced yet</span>
                  ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Invoices"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="Issued FIELDCRAFT project invoices exported to QuickBooks using the existing customer and project mappings."
                    onSync={handleInvoiceSync}
                    syncing={isModuleSyncing("invoices", invoiceAnyRunning)}
                    syncButtonLabel="Sync invoices"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    search={invoiceSearchInput}
                    onSearchChange={setInvoiceSearchInput}
                    searchPlaceholder="Search invoice, customer name, or project name…"
                    filters={filters}
                    activeFilter={activeFilter}
                    onFilterChange={handleFilter}
                  />
                  <QuickBooksDataTable
                    columns={columns}
                    rows={rows}
                    rowKey={(row) => `${row.invoiceId}-${row.mappingId ?? "none"}`}
                    onRowClick={handleInvoiceRowClick}
                    loading={invoiceTableLoading}
                    emptyMessage="No invoices match your filters."
                    page={invoicePage}
                    pageSize={invoicePageSize}
                    total={invoiceCdcData.pagination.totalRecords}
                    onPageChange={setInvoicePage}
                    onPageSizeChange={(n) => { setInvoicePageSize(n); setInvoicePage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ CUSTOMER PAYMENTS ══════════════════════════════ */}
            {selectedModule === "payments" && (() => {
              const summary = paymentCdcData.summary;
              const rows = paymentCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("payments", summary);
              const filters: FilterPill[] = [
                { id: "ALL", label: "All", count: summary.total },
                { id: "SYNCED", label: "Synced", count: summary.synced, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: summary.pending, tone: "wait" },
                { id: "NOT_READY", label: "Not ready", count: summary.notReady, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeFilter =
                paymentSyncFilter === "SYNCED" ? "SYNCED"
                  : paymentSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : paymentSyncFilter === "NOT_READY" ? "NOT_READY"
                  : (paymentSyncFilter === "NEEDS_ATTENTION" || paymentSyncFilter === "FAILED" || paymentSyncFilter === "SYNC_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleFilter = (id: string) => {
                setPaymentPage(0);
                setPaymentSyncFilter(id);
              };
              const columns: DataColumn<CustomerPaymentCdcRow>[] = [
                {
                  key: "payment",
                  header: "Payment",
                  render: (row) => (
                    <QbIdentityCell
                      name={row.paymentReferenceNo || `Payment #${row.paymentId}`}
                      icon={<AccountBalanceWalletRoundedIcon />}
                      sub={formatDateTime(row.receivedTime) || "—"}
                    />
                  ),
                },
                {
                  key: "customer",
                  header: "Customer",
                  render: (row) => (
                    <span className="qbw-cell-lines">
                      <span>{row.customerName || "Customer not available"}</span>
                      {row.paymentStatus ? <span className="qbw-cell-meta">{row.paymentStatus}</span> : null}
                    </span>
                  ),
                },
                {
                  key: "amount",
                  header: "Received",
                  align: "right",
                  render: (row) => <span>{formatCurrency(Number(row.amount || 0))}</span>,
                },
                {
                  key: "applied",
                  header: "Applied",
                  align: "right",
                  render: (row) => (
                    <span className="qbw-cell-lines qbw-cell-lines--right">
                      <span>{formatCurrency(Number(row.appliedAmount || 0))}</span>
                      <span className="qbw-cell-meta">{Number(row.allocationCount || 0)} allocation{Number(row.allocationCount || 0) === 1 ? "" : "s"}</span>
                    </span>
                  ),
                },
                {
                  key: "unapplied",
                  header: "Unapplied",
                  align: "right",
                  render: (row) => <span>{formatCurrency(Number(row.unappliedAmount || 0))}</span>,
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QB Payment ID",
                  render: (row) => row.externalPaymentId ? (
                    <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalPaymentId}</span>
                  ) : (
                    <span className="qbw-qbid qbw-qbid--none">Not synced yet</span>
                  ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Customer Payments"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="Send FIELDCRAFT customer receipts to QuickBooks and apply reconciled amounts to their synced invoices."
                    onSync={handlePaymentSync}
                    syncing={isModuleSyncing("payments", paymentAnyRunning)}
                    syncButtonLabel="Sync payments"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    search={paymentSearchInput}
                    onSearchChange={setPaymentSearchInput}
                    searchPlaceholder="Search payment, customer name, or status…"
                    filters={filters}
                    activeFilter={activeFilter}
                    onFilterChange={handleFilter}
                  />
                  <QuickBooksDataTable
                    columns={columns}
                    rows={rows}
                    rowKey={(row) => `${row.paymentId}-${row.mappingId ?? "none"}`}
                    onRowClick={handlePaymentRowClick}
                    loading={paymentTableLoading}
                    emptyMessage="No customer payments match your filters."
                    page={paymentPage}
                    pageSize={paymentPageSize}
                    total={paymentCdcData.pagination.totalRecords}
                    onPageChange={setPaymentPage}
                    onPageSizeChange={(n) => { setPaymentPageSize(n); setPaymentPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ TIME ACTIVITY ═════════════════════════════════ */}
            {selectedModule === "timelogs" && (() => {
              const ts = timelogCdcData.summary;
              const tRows = timelogCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("timelogs", ts);
              const timelogFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: ts.total },
                { id: "SYNCED", label: "Synced", count: ts.synced, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: ts.pending, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeTimelogFilter =
                timelogSyncFilter === "SYNCED" ? "SYNCED"
                  : timelogSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : (timelogSyncFilter === "NEEDS_ATTENTION" || timelogSyncFilter === "FAILED" || timelogSyncFilter === "SYNC_FAILED" || timelogSyncFilter === "NOT_READY") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleTimelogFilter = (id: string) => {
                setTimelogPage(0);
                // "Needs attention" spans failed + sync-failed + not-ready; the backend expands
                // the NEEDS_ATTENTION sentinel to that set, so pass the pill id through.
                setTimelogSyncFilter(id);
              };
              const timelogColumns: DataColumn<TimelogCdcRow>[] = [
                {
                  key: "timelog",
                  header: "Timelog",
                  render: (row) => (
                    <QbIdentityCell
                      name={row.taskName || `Timelog #${row.timelogId}`}
                      icon={<AccessTimeRoundedIcon />}
                      sub={renderTimelogTimeRange(row.startTime, row.endTime)}
                      // Task names run long ("Tear-off & haul-away — north elevation") and
                      // used to wrap freely in this column before it became an identity
                      // cell; allowWrap keeps them readable instead of truncating at 200px.
                      allowWrap
                    />
                  ),
                },
                {
                  key: "employee",
                  header: "Employee",
                  render: (row) => {
                    const wt = resolveTimelogWorkerTypeLabel(row);
                    return (
                      <span className="qbw-cell-lines">
                        <span className="qbw-cell-chips">
                          <span>{row.employeeName || "—"}</span>
                          {wt ? <span className={`qbw-mini-chip qbw-mini-chip--${wt === "1099" ? "1099" : "w2"}`}>{wt === "1099" ? "1099" : "W-2"}</span> : null}
                        </span>
                        {!row.employeeLinked ? <span className="qbw-mini-chip qbw-mini-chip--warn">Not synced</span> : null}
                      </span>
                    );
                  },
                },
                {
                  key: "projectservice",
                  header: "Project / Service",
                  render: (row) => (
                    <span className="qbw-cell-lines">
                      <span className="qbw-cell-chips">
                        <span>{row.projectName || "—"}</span>
                        {!row.projectLinked ? <span className="qbw-mini-chip qbw-mini-chip--warn">Project</span> : null}
                      </span>
                      {row.roleName ? (
                        <span className="qbw-cell-chips">
                          <span className="qbw-cell-meta">{row.roleName}</span>
                          {!row.serviceLinked ? <span className="qbw-mini-chip qbw-mini-chip--warn">Service</span> : null}
                        </span>
                      ) : null}
                    </span>
                  ),
                },
                {
                  key: "duration",
                  header: "Duration",
                  align: "right",
                  render: (row) => <span className="qbw-duration">{formatDuration(row.durationSeconds, row.durationMinutes)}</span>,
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={row.syncStatus} reason={row.reason ? parseReasonMessage(row.reason) : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QB TimeActivity ID",
                  render: (row) =>
                    row.externalEntityType === "TIME_ACTIVITY" && row.externalEntityId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{row.externalEntityId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not synced yet</span>
                    ),
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(row.mappingUpdatedAt) || "—"}</span>,
                },
              ];
              return (
                <>
                  {/* Development/testing-only TimeActivity delete action disabled for PR. */}
                  <QuickBooksModuleHeader
                    title="Time Activity"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="FIELDCRAFT time logs grouped by worker, day, and project, then exported to QuickBooks Online as Time Activity."
                    onSync={handleTimelogSync}
                    syncing={isModuleSyncing("timelogs", timelogAnyRunning)}
                    syncButtonLabel="Sync Time Activity"
                    syncDisabled={anyRunning || syncStartPending}
                    syncDisabledReason={syncBusyReason}
                    search={timelogSearchInput}
                    onSearchChange={setTimelogSearchInput}
                    searchPlaceholder="Search time activity…"
                    filters={timelogFilters}
                    activeFilter={activeTimelogFilter}
                    onFilterChange={handleTimelogFilter}
                  />
                  <QuickBooksDataTable
                    columns={timelogColumns}
                    rows={tRows}
                    rowKey={(row) => `${row.timelogId}-${row.mappingId ?? "none"}`}
                    loading={timelogTableLoading}
                    emptyMessage="No time activity records match your filters."
                    page={timelogPage}
                    pageSize={timelogPageSize}
                    total={timelogCdcData.pagination.totalRecords}
                    onPageChange={setTimelogPage}
                    onPageSizeChange={(n) => { setTimelogPageSize(n); setTimelogPage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* ═══ SERVICES (redesigned) ══════════════════════════ */}
            {selectedModule === "services" && (() => {
              const ss = serviceRoleCdcData.summary;
              const srRows = serviceRoleCdcData.rows || [];
              const needsAttention = getModuleNeedsAttentionCount("services", ss);
              const serviceFilters: FilterPill[] = [
                { id: "ALL", label: "All", count: ss.total },
                { id: "SYNCED", label: "Synced", count: ss.synced, tone: "ok" },
                { id: "PENDING_PUSH", label: "Pending", count: ss.pending, tone: "wait" },
                { id: "NEEDS_ATTENTION", label: "Needs attention", count: needsAttention, tone: "warn" },
              ];
              const activeServiceFilter =
                serviceRoleSyncFilter === "SYNCED" ? "SYNCED"
                  : serviceRoleSyncFilter === "PENDING_PUSH" ? "PENDING_PUSH"
                  : (serviceRoleSyncFilter === "FAILED" || serviceRoleSyncFilter === "SYNC_FAILED") ? "NEEDS_ATTENTION"
                  : "ALL";
              const handleServiceFilter = (id: string) => {
                setServiceRolePage(0);
                setServiceRoleSyncFilter(id === "NEEDS_ATTENTION" ? "FAILED" : id);
              };
              const roleIdOf = (row: ServiceRoleCdcRow): number | null => (row.roleId ?? row.id ?? null);
              const toggleServiceRole = (row: ServiceRoleCdcRow) => {
                const id = roleIdOf(row);
                if (id == null) return;
                setSelectedServiceRoleIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
              };
              const serviceColumns: DataColumn<ServiceRoleCdcRow>[] = [
                {
                  key: "service",
                  header: "Service",
                  render: (row) => (
                    <QbIdentityCell
                      name={resolveServiceRoleName(row)}
                      icon={<ConstructionRoundedIcon />}
                      source="FIELDCRAFT"
                      sub={row.description || undefined}
                    />
                  ),
                },
                {
                  key: "coa",
                  header: "Chart of Account",
                  render: (row) => {
                    const coa = normalizeServiceCoaAccount(resolveServiceRoleIncomeAccount(row));
                    return coa && isServiceCoaConfigured(coa) ? (
                      <span className="qbw-cell-lines">
                        <span>{coa.name}</span>
                        <span className="qbw-cell-meta">{[coa.accountType, coa.detailType].filter(Boolean).join(" • ")}</span>
                      </span>
                    ) : (
                      <span className="qbw-mini-chip qbw-mini-chip--warn">No COA assigned</span>
                    );
                  },
                },
                {
                  key: "sync",
                  header: "Sync Status",
                  render: (row) => (
                    <QbStatusCell status={resolveServiceRoleSyncStatus(row)} reason={row.reason ? parseServiceRoleReason(row.reason).message : undefined} />
                  ),
                },
                {
                  key: "qbid",
                  header: "QB Service ID",
                  render: (row) => {
                    const extId = resolveServiceRoleExternalId(row);
                    return extId ? (
                      <span className="qbw-qbid"><CheckCircleOutlineRoundedIcon />{extId}</span>
                    ) : (
                      <span className="qbw-qbid qbw-qbid--none">Not linked</span>
                    );
                  },
                },
                {
                  key: "last",
                  header: "Last Synced",
                  render: (row) => <span className="qbw-last">{formatDateTime(resolveServiceRoleLastSynced(row)) || "—"}</span>,
                },
              ];
              return (
                <>
                  <QuickBooksModuleHeader
                    title="Services"
                    titleIcon={activeModuleDef.icon}
                    context={moduleContextSlot}
                    description="FIELDCRAFT service roles, synced to QuickBooks as service items."
                    onSync={handleServiceRoleSync}
                    syncing={isModuleSyncing("services", serviceRoleAnyRunning)}
                    syncButtonLabel="Sync services"
                    syncDisabled={anyRunning || syncStartPending || serviceIncomeAccountsLoading || serviceIncomeAccounts.length === 0}
                    syncDisabledReason={syncBusyReason ?? ((serviceIncomeAccountsLoading || serviceIncomeAccounts.length === 0) ? "Sync your Chart of Accounts first, then assign income accounts to roles." : undefined)}
                    search={serviceRoleSearchInput}
                    onSearchChange={setServiceRoleSearchInput}
                    searchPlaceholder="Search services…"
                    filters={serviceFilters}
                    activeFilter={activeServiceFilter}
                    onFilterChange={handleServiceFilter}
                    actions={selectedServiceRoleIds.length > 0 ? (
                      <button
                        type="button"
                        className="qbw-btn qbw-btn-primary"
                        onClick={() => { setSelectedServiceCoaOption(null); setServiceCoaAssignDialogOpen(true); }}
                      >
                        Assign COA ({selectedServiceRoleIds.length})
                      </button>
                    ) : undefined}
                  />
                  <QuickBooksDataTable
                    columns={serviceColumns}
                    rows={srRows}
                    rowKey={(row) => `${roleIdOf(row) ?? "row"}-${row.mappingId ?? "none"}`}
                    selection={{
                      isSelected: (row) => { const id = roleIdOf(row); return id != null && selectedServiceRoleIdsSet.has(id); },
                      onToggle: toggleServiceRole,
                      allSelected: allServiceRolesOnPageSelected,
                      someSelected: someServiceRolesOnPageSelected,
                      onToggleAll: () => setSelectedServiceRoleIds(allServiceRolesOnPageSelected ? [] : selectableServiceRoleIds),
                    }}
                    loading={serviceRoleTableLoading}
                    emptyMessage="No services match your filters."
                    page={serviceRolePage}
                    pageSize={serviceRolePageSize}
                    total={serviceRoleCdcData.pagination.totalRecords}
                    onPageChange={setServiceRolePage}
                    onPageSizeChange={(n) => { setServiceRolePageSize(n); setServiceRolePage(0); }}
                    pageSizeOptions={[10, 25, 50, 100]}
                  />
                </>
              );
            })()}

            {/* -- Module dialogs (moved out of the retired legacy blocks so they render) -- */}
                <Dialog
                  open={billDetailOpen}
                  onClose={handleBillDialogClose}
                  maxWidth="md"
                  fullWidth
                  PaperProps={{ className: "qb-dialog-paper" }}
                >
                  {/* Amber gradient accent bar */}
                  <Box className="qb-dialog-accent-bills" />

                  <DialogTitle className="qb-fin-title">
                    <Box className="qb-fin-title-row">
                      <Box className="qb-fin-title-icon qb-fin-title-icon--bill">
                        <ReceiptLongRoundedIcon />
                      </Box>
                      <Box minWidth={0} flex={1}>
                        <Typography className="qb-fin-title-text" noWrap>
                          {selectedBill?.displayName || "Bill Details"}
                        </Typography>
                        <Box className="qb-fin-title-meta">
                          {selectedBill?.vendorCompany && (
                            <Chip label={selectedBill.vendorCompany} size="small" className="qb-fin-meta-chip" />
                          )}
                          {selectedBill?.invoiceDate && (
                            <Typography className="qb-fin-title-date">
                              {formatDateOnlyUtc(selectedBill.invoiceDate)}
                            </Typography>
                          )}
                          {/* The vendor's own document number — user-facing reference, not
                              an internal id, so it stays. */}
                          {selectedBill?.invoiceNumber && (
                            <Typography className="qb-fin-title-ref qb-fin-mono">
                              #{selectedBill.invoiceNumber}
                            </Typography>
                          )}
                          {selectedBill?.isPaid != null && (
                            <Chip
                              label={selectedBill.isPaid ? "Paid" : "Unpaid"}
                              size="small"
                              className={`qb-paid-chip ${selectedBill.isPaid ? "is-paid" : "is-unpaid"}`}
                            />
                          )}
                          {selectedBill?.billPaymentSyncRequired && (
                            <Tooltip title="This bill is paid, but its bill payment still needs to be synced to QuickBooks." arrow>
                              <Chip
                                icon={<WarningAmberRoundedIcon />}
                                label="Sync bill payment"
                                size="small"
                                className="qb-bill-payment-sync-chip"
                              />
                            </Tooltip>
                          )}
                        </Box>
                      </Box>
                    </Box>
                  </DialogTitle>

                  <DialogContent className="qb-fin-content">
                    {/* Summary cards — shared with the Invoice and Customer Payment
                        dialogs so all three financial views read as one system. */}
                    <SummaryCards>
                      {/* No sub-line: payment state is on the title's Paid/Unpaid chip and the
                          vendor is on the title's chip, so anything here would repeat one of
                          them. The figure stands alone. */}
                      <SummaryCard label="Total Amount" variant="hero" tone="bill">
                        <Typography className="qb-fin-card-amount">
                          {selectedBill?.amount != null ? formatCurrency(Number(selectedBill.amount)) : "—"}
                        </Typography>
                      </SummaryCard>

                      <SummaryCard label="QuickBooks Status">
                        <QuickBooksStatusBody
                          externalId={selectedBill?.externalReceiptId}
                          syncStatus={selectedBill?.syncStatus}
                        />
                      </SummaryCard>

                      {/* Project card — linked, stock, needs link, sync required */}
                      {(() => {
                        const isDialogIncomplete = selectedBill?.syncStatus === "SYNCED_INCOMPLETE";
                        const isDialogStockBill = isFieldCraftStockBill(selectedBill);
                        const isDialogStockProjectLinkBlocked = isStockBillProjectLinkBlocked(selectedBill);
                        const isDialogProjectError = selectedBill != null
                          && (selectedBill.syncStatus === "IMPORT_FAILED" || selectedBill.syncStatus === "SYNC_FAILED" || selectedBill.syncStatus === "FAILED")
                          && isProjectSyncError(selectedBill);

                        if (selectedBill?.projectName) {
                          return (
                            <SummaryCard label="Project">
                              <Typography className="qb-fin-card-value" noWrap>
                                {selectedBill.projectName}
                              </Typography>
                            </SummaryCard>
                          );
                        }
                        if (isDialogStockProjectLinkBlocked) {
                          return (
                            <SummaryCard label="Project">
                              <Typography className="qb-fin-card-value" noWrap>
                                {stockBillLabel(selectedBill)}
                              </Typography>
                              <Typography className="qb-fin-card-sub">
                                Project link ignored
                              </Typography>
                              {selectedBill?.stockReference && (
                                <Typography className="qb-fin-card-sub" noWrap>
                                  {selectedBill.stockReference}
                                </Typography>
                              )}
                            </SummaryCard>
                          );
                        }
                        if (isDialogStockBill) {
                          return (
                            <SummaryCard label="Project">
                              <Typography className="qb-fin-card-value" noWrap>
                                {stockBillLabel(selectedBill)}
                              </Typography>
                              {selectedBill?.stockReference && (
                                <Typography fontSize={11} className="qb-fin-card-sub" noWrap>
                                  {selectedBill.stockReference}
                                </Typography>
                              )}
                            </SummaryCard>
                          );
                        }
                        if (isDialogIncomplete) {
                          return (
                            <SummaryCard label="Project" variant="due">
                              <Typography className="qb-fin-card-value qb-fin-card-amount-sm">
                                No project linked
                              </Typography>
                              <Typography className="qb-fin-card-sub">
                                Imported without a project. Assign it to the correct synced project in
                                QuickBooks, then sync again.
                              </Typography>
                            </SummaryCard>
                          );
                        }
                        if (isDialogProjectError) {
                          return (
                            <SummaryCard label="Project">
                              <Typography className="qb-fin-card-value" noWrap>
                                Sync projects first
                              </Typography>
                              <Typography className="qb-fin-card-sub">
                                Sync FIELDCRAFT projects first, then re-sync this bill to link it.
                              </Typography>
                            </SummaryCard>
                          );
                        }
                        return null;
                      })()}
                      {(billDocumentInfoLoading || billDocumentInfo?.hasDocument) && (
                        <Box
                          onClick={billDocumentInfo?.hasDocument ? handleBillAttachmentClick : undefined}
                          className={`qb-fin-card qb-fin-card--plain qb-fin-card--action${billDocumentPreviewOpen ? " is-open" : ""}${billDocumentInfo?.hasDocument ? " is-clickable" : ""}`}
                        >
                          <Typography className="qb-fin-card-label">Attachment</Typography>
                          {billDocumentInfoLoading ? (
                            <Skeleton variant="text" width={100} height={18} />
                          ) : (
                            <Box className="qb-fin-card-attachment">
                              {billDocumentFetchLoading
                                ? <CircularProgress size={14} className="qb-fin-attach-spinner" />
                                : <AttachFileRoundedIcon className="qb-fin-attach-icon" />}
                              <Typography className="qb-fin-card-value" noWrap>
                                {billDocumentPreviewOpen ? "Hide receipt" : (billDocumentInfo?.name || "View receipt")}
                              </Typography>
                            </Box>
                          )}
                        </Box>
                      )}
                    </SummaryCards>

                    {/* Document preview section */}
                    {billDocumentPreviewOpen && billDocumentObjectUrl && (
                      <Box className="qb-sx-231">
                        <Box className="qb-sx-232">
                          <Box className="qb-sx-233">
                            <AttachFileRoundedIcon className="qb-sx-234" />
                            <Typography fontSize={13} fontWeight={700} color="#1D4ED8" noWrap>
                              {billDocumentInfo?.name || "Document"}
                            </Typography>
                            {billDocumentInfo?.mimeType && (
                              <Chip
                                label={billDocumentInfo.mimeType.includes("pdf") ? "PDF"
                                  : (billDocumentInfo.mimeType.split("/")[1] || "FILE").toUpperCase()}
                                size="small" className="qb-sx-235"
                              />
                            )}
                            {billDocumentInfo?.size != null && (
                              <Typography fontSize={11.5} color="text.secondary" className="qb-sx-236">
                                {(billDocumentInfo.size / 1024).toFixed(1)} KB
                              </Typography>
                            )}
                          </Box>
                          <Box className="qb-sx-237">
                            <Button id="qb-btn-bill-doc-download" size="small" variant="outlined"
                              component="a" href={billDocumentObjectUrl} download={billDocumentInfo?.name || "document"} className="qb-sx-238">
                              Download
                            </Button>
                            <Button id="qb-btn-bill-doc-close" size="small" variant="outlined"
                              onClick={handleBillAttachmentClick} className="qb-sx-239">
                              ✕
                            </Button>
                          </Box>
                        </Box>
                        {billDocumentInfo?.mimeType?.startsWith("image/") ? (
                          <Box className="qb-sx-240">
                            <Box component="img" src={billDocumentObjectUrl} alt={billDocumentInfo.name || "attachment"} className="qb-sx-241" />
                          </Box>
                        ) : billDocumentInfo?.mimeType === "application/pdf" ? (
                          <Box component="embed" src={billDocumentObjectUrl!} type="application/pdf" className="qb-sx-242" />
                        ) : (
                          <Box component="iframe" src={billDocumentObjectUrl} title={billDocumentInfo?.name || "document"} className="qb-sx-242" />
                        )}
                      </Box>
                    )}

                    {/* Cost breakdown header */}
                    <BlockHeading
                      title="Cost Breakdown"
                      count={!billLineItemsLoading && billLineItems.length > 0
                        ? `${billLineItems.length} line item${billLineItems.length !== 1 ? "s" : ""}`
                        : null}
                    />

                    {/* Line items body */}
                    {billLineItemsLoading ? (
                      <Box>
                        {[1, 2, 3].map((i) => (
                          <Box key={i} className="qb-sx-247">
                            <Box className="qb-sx-248">
                              <Skeleton variant="text" width={200} height={18} />
                              <Skeleton variant="text" width={80} height={18} />
                            </Box>
                            <Skeleton variant="text" width={100} height={13} />
                          </Box>
                        ))}
                      </Box>
                    ) : billLineItems.length === 0 ? (
                      <EmptyState
                        icon={<ReceiptLongRoundedIcon className="qb-fin-empty-icon" />}
                        title="No line items on record"
                        detail="This bill was exported to QuickBooks as a single total. Add itemized receipts in FIELDCRAFT to see a detailed cost breakdown here."
                      />
                    ) : (() => {
                      const isCategoryLine = (item: BillLineItem) =>
                        item.lineKind === "category"
                          ? true
                          : item.lineKind === "item"
                            ? false
                            : item.quantity == null && item.unitPrice == null;

                      const categoryItems = billLineItems.filter(isCategoryLine);
                      const itemDetailItems = billLineItems.filter((item: BillLineItem) => !isCategoryLine(item));
                      const hasBothSections = categoryItems.length > 0 && itemDetailItems.length > 0;
                      const lineTotal = billLineItems.reduce((sum: number, it: BillLineItem) => sum + it.amount, 0);

                      return (
                        <Box className="qb-fin-table">
                          {/* Column headers. Unlike an invoice charge line, a bill line
                              comes from a real receipt, so quantity and unit cost are
                              genuine business data and stay visible. */}
                          <Box className="qb-fin-table-head">
                            {["Item", "Qty", "Unit Cost", "Total"].map((heading, index) => (
                              <Typography key={heading}
                                className={index > 0 ? "qb-line-header is-right" : "qb-line-header"}>
                                {heading}
                              </Typography>
                            ))}
                          </Box>

                          {/* Category Details */}
                          {hasBothSections && (
                            <Box className="qb-fin-table-group">
                              <Typography className="qb-fin-table-group-label">Category Details</Typography>
                              <Chip label={categoryItems.length} size="small" className="qb-fin-count-chip" />
                            </Box>
                          )}
                          {renderRows(categoryItems)}

                          {/* Item Details */}
                          {hasBothSections && (
                            <Box className="qb-fin-table-group">
                              <Typography className="qb-fin-table-group-label">Item Details</Typography>
                              <Chip label={itemDetailItems.length} size="small" className="qb-fin-count-chip" />
                            </Box>
                          )}
                          {renderRows(itemDetailItems)}

                          {/* Total row */}
                          <Box className="qb-fin-table-total qb-fin-table-total--bill">
                            <Typography className="qb-fin-total-label">
                              {billLineItems?.length ?? 0} item{(billLineItems?.length ?? 0) !== 1 ? "s" : ""}
                              {selectedBill?.externalReceiptId ? " · synced to QuickBooks" : ""}
                            </Typography>
                            <Typography className="qb-fin-total-value">{formatCurrency(Number(lineTotal))}</Typography>
                          </Box>
                        </Box>
                      );
                    })()}

                    {/* COA footer */}
                    {!billLineItemsLoading && billLineItems.length > 0
                      && selectedBill?.category && selectedBill.category !== "QuickBooks Import" && (
                      <NoteStrip icon={<AccountTreeRoundedIcon className="qb-fin-note-icon" />}>
                        All items posted under:{" "}
                        <Box component="span" fontWeight={700} color="text.primary">
                          {selectedBill.category}
                        </Box>
                      </NoteStrip>
                    )}
                  </DialogContent>

                  <DialogCloseAction onClose={handleBillDialogClose} id="qb-btn-bill-dialog-close" />
                </Dialog>
                <InvoiceDetailDialog
                  open={invoiceDetailOpen}
                  loading={invoiceDetailLoading}
                  detail={invoiceDetail}
                  onClose={handleInvoiceDialogClose}
                  attachmentPreviewOpen={invoiceDocumentPreviewOpen}
                  attachmentLoading={invoiceDocumentFetchLoading}
                  attachmentObjectUrl={invoiceDocumentObjectUrl}
                  onAttachmentClick={invoiceDetail?.document ? handleInvoiceAttachmentClick : undefined}
                />
                <CustomerPaymentDetailDialog
                  open={paymentDetailOpen}
                  loading={paymentDetailLoading}
                  detail={paymentDetail}
                  onClose={() => setPaymentDetailOpen(false)}
                />
                  <Dialog
                    open={assignDialogOpen}
                    onClose={() => { if (!assignBillPaymentLoading) { setAssignDialogOpen(false); setSelectedBillPaymentAccountId(""); } }}
                    maxWidth="sm" fullWidth
                    PaperProps={{ className: "qb-dialog-paper" }}
                  >
                    {/* Gradient accent bar */}
                    <Box className="qb-dialog-accent-bill-payments" />
                    <DialogTitle className="qb-sx-340">
                      <Box className="qb-sx-341">
                        <Box className="qb-sx-342">
                          <AccountBalanceWalletRoundedIcon className="qb-sx-343" />
                        </Box>
                        <Box>
                          <Typography fontWeight={800} fontSize={16} lineHeight={1.2}>Assign QBO Payment Account</Typography>
                          <Typography fontSize={12.5} color="text.secondary" className="qb-sx-344">
                            {selectedBillPaymentIds.length} payment{selectedBillPaymentIds.length !== 1 ? "s" : ""} selected
                          </Typography>
                        </Box>
                      </Box>
                    </DialogTitle>
                    <DialogContent className="qb-sx-345">
                      <Box className="qb-sx-346">
                        <Typography fontSize={13} color="text.secondary" lineHeight={1.65}>
                          Choose the QuickBooks <Box component="span" className="qb-sx-347">🏦 Bank</Box> or <Box component="span" className="qb-sx-348">💳 Credit Card</Box> account to use as the payment source. This updates the sync mapping only — your FIELDCRAFT chart of account is not changed.
                        </Typography>
                      </Box>
                      <FormControl fullWidth size="small">
                        <InputLabel className="qb-sx-349">QuickBooks Payment Account</InputLabel>
                        <Select id="quick-books-table-select-quickbooks-payment-account"
                          value={selectedBillPaymentAccountId}
                          label="QuickBooks Payment Account"
                          onChange={(e: { target: { value: string } }) => setSelectedBillPaymentAccountId(e.target.value)}
                          disabled={assignBillPaymentLoading} className="qb-sx-350"
                          MenuProps={{ PaperProps: { className: "qb-select-menu-paper" } }}
                        >
                          {billPaymentEligibleAccounts.length === 0 ? (
                            <MenuItem disabled value="">
                              <Typography fontSize={13} color="text.disabled" fontStyle="italic">No eligible accounts — sync Chart of Accounts first.</Typography>
                            </MenuItem>
                          ) : (
                            billPaymentEligibleAccounts.map((account: BillPaymentEligibleAccount) => (
                              <MenuItem key={`${account.external_id}-${account.id}`} value={account.external_id || ""} className="qb-sx-351">
                                <Box className="qb-sx-352">
                                  <Box className={`qb-payment-account-option-icon ${account.eligible_pay_type === "CreditCard" ? "is-credit-card" : "is-bank"}`}>
                                    {account.eligible_pay_type === "CreditCard" ? "💳" : "🏦"}
                                  </Box>
                                  <Box>
                                    <Typography fontWeight={700} fontSize={13.5} lineHeight={1.3}>{account.name}</Typography>
                                    <Typography fontSize={11.5} color="text.secondary">
                                      {[account.account_type, account.detail_type].filter(Boolean).join(" · ")}
                                      {account.eligible_pay_type ? ` · ${account.eligible_pay_type}` : ""}
                                    </Typography>
                                  </Box>
                                </Box>
                              </MenuItem>
                            ))
                          )}
                        </Select>
                      </FormControl>
                    </DialogContent>
                    <DialogActions className="qb-sx-353">
                      <Button
                        id="qb-btn-assign-dialog-cancel"
                        onClick={() => { setAssignDialogOpen(false); setSelectedBillPaymentAccountId(""); }}
                        disabled={assignBillPaymentLoading}
                        variant="outlined" className="qb-sx-354"
                      >Cancel</Button>
                      <Button
                        id="qb-btn-assign-dialog-confirm"
                        variant="contained"
                        onClick={handleAssignBillPaymentAccount}
                        disabled={assignBillPaymentLoading || !selectedBillPaymentAccountId || selectedBillPaymentIds.length === 0}
                        startIcon={assignBillPaymentLoading ? <CircularProgress size={14} color="inherit" /> : null} className="qb-sx-355"
                      >
                        {assignBillPaymentLoading ? "Assigning…" : "Assign Account"}
                      </Button>
                    </DialogActions>
                  </Dialog>
                  <Dialog open={projectSyncConfigOpen} onClose={() => setProjectSyncConfigOpen(false)} fullWidth maxWidth="sm">
                    <Box sx={{ height: 3, background: "linear-gradient(90deg, #6366F1, #8B5CF6, #3B82F6)", borderRadius: "4px 4px 0 0" }} />
                    <DialogTitle sx={{ pb: 0.5 }}>
                      <Box display="flex" alignItems="center" gap={1.5}>
                        <Box sx={{ width: 34, height: 34, borderRadius: "9px", background: "rgba(99,102,241,0.1)", color: "#6366F1", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                          <AccountTreeRoundedIcon sx={{ fontSize: 18 }} />
                        </Box>
                        <Box>
                          <Typography fontWeight={800} fontSize={15.5} color="text.primary" lineHeight={1.2}>Project sync method</Typography>
                          <Typography fontSize={11.5} color="text.secondary" mt="1px">Choose how new projects appear in QuickBooks</Typography>
                        </Box>
                      </Box>
                    </DialogTitle>
                    <DialogContent sx={{ pt: "16px !important" }}>
                      {projectSyncConfigLoading ? (
                        <Box py={3} display="flex" alignItems="center" justifyContent="center" gap={1.5}>
                          <CircularProgress size={20} sx={{ color: "#6366F1" }} />
                          <Typography fontSize={13} color="text.secondary">Loading project sync settings…</Typography>
                        </Box>
                      ) : (
                        <Stack spacing={1.5}>
                          {/* Option cards */}
                          <Box className="qb-sync-method-cards">
                            {(
                              [
                                {
                                  value: "AUTO" as ProjectSyncMethod,
                                  icon: <AutorenewRoundedIcon />,
                                  label: "Auto",
                                  badge: "Recommended",
                                  desc: "FIELDCRAFT checks whether your QuickBooks company has Projects enabled. New projects sync as QuickBooks Projects if they are — otherwise they sync as sub-customers automatically.",
                                },
                                {
                                  value: "QBO_PROJECT" as ProjectSyncMethod,
                                  icon: <AccountTreeRoundedIcon />,
                                  label: "QuickBooks Project",
                                  badge: null,
                                  desc: "New projects always sync as QuickBooks Projects. If your QuickBooks subscription doesn't include Projects, sync will pause with a clear message so you can choose a different option.",
                                  unavailable:
                                    projectSyncConfig.method === "QBO_PROJECT" &&
                                    projectSyncConfig.projectsAvailable === false &&
                                    Boolean(projectSyncConfig.message),
                                  unavailableMsg: projectSyncConfig.message,
                                },
                                {
                                  value: "SUB_CUSTOMER" as ProjectSyncMethod,
                                  icon: <BusinessRoundedIcon />,
                                  label: "Sub-customer",
                                  badge: null,
                                  desc: "New projects always sync as sub-customers under their linked QuickBooks customer. Works on any QuickBooks plan — no Projects subscription needed.",
                                },
                              ] as Array<{
                                value: ProjectSyncMethod;
                                icon: React.ReactNode;
                                label: string;
                                badge: string | null;
                                desc: string;
                                unavailable?: boolean;
                                unavailableMsg?: string | null;
                              }>
                            ).map((opt) => (
                              <Box
                                key={opt.value}
                                className={`qb-sync-method-card${projectSyncConfig.method === opt.value ? " is-selected" : ""}`}
                                onClick={() => {
                                  setCapabilityError(null);
                                  setProjectSyncConfig((prev: ProjectSyncConfig) => ({
                                    ...prev,
                                    method: opt.value,
                                    message: null,
                                  }));
                                }}
                                onKeyDown={(e: React.KeyboardEvent) => {
                                  if (e.key === "Enter" || e.key === " ") {
                                    setCapabilityError(null);
                                    setProjectSyncConfig((prev: ProjectSyncConfig) => ({
                                      ...prev,
                                      method: opt.value,
                                      message: null,
                                    }));
                                  }
                                }}
                                role="radio"
                                aria-checked={projectSyncConfig.method === opt.value}
                                tabIndex={0}
                              >
                                <Box className="qb-sync-method-card-icon">{opt.icon}</Box>
                                <Box className="qb-sync-method-card-body">
                                  <Box className="qb-sync-method-card-title-row">
                                    <Typography className="qb-sync-method-card-title">{opt.label}</Typography>
                                    {opt.badge && (
                                      <Chip label={opt.badge} size="small" className="qb-sync-method-card-badge" />
                                    )}
                                  </Box>
                                  <Typography className="qb-sync-method-card-desc">{opt.desc}</Typography>
                                  {opt.unavailable && opt.unavailableMsg && (
                                    <Typography className="qb-sync-method-card-warn">{opt.unavailableMsg}</Typography>
                                  )}
                                </Box>
                                <Box className="qb-sync-method-card-check" aria-hidden="true">
                                  <CheckCircleOutlineRoundedIcon />
                                </Box>
                              </Box>
                            ))}
                          </Box>

                          {/* What happens to new projects */}
                          <Box className="qb-sync-method-effective">
                            <Box className="qb-sync-method-effective-icon" aria-hidden="true">
                              <InfoOutlinedIcon sx={{ fontSize: 16 }} />
                            </Box>
                            <Box className="qb-sync-method-effective-body">
                              <Box className="qb-sync-method-effective-main">
                                <Typography className="qb-sync-method-effective-label">New projects will sync as</Typography>
                                <Chip
                                  className="qb-sync-method-effective-chip"
                                  size="small"
                                  label={
                                    projectSyncConfig.effectiveProjectSyncTarget === "QBO_PROJECT"
                                      ? "QuickBooks Project"
                                      : "Sub-customer"
                                  }
                                />
                              </Box>
                              <Typography className="qb-sync-method-effective-note">
                                Already-synced projects remain as they are.
                              </Typography>
                            </Box>
                          </Box>
                        </Stack>
                      )}
                    </DialogContent>
                    <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
                      <Button onClick={() => setProjectSyncConfigOpen(false)} disabled={projectSyncConfigSaving} variant="outlined" sx={{ borderRadius: "9px", fontWeight: 700, textTransform: "none", fontSize: 13 }}>Cancel</Button>
                      <Button
                        onClick={handleSaveProjectSyncConfig}
                        disabled={projectSyncConfigSaving || projectSyncConfigLoading}
                        variant="contained"
                        sx={{ borderRadius: "9px", fontWeight: 700, textTransform: "none", fontSize: 13, background: "linear-gradient(135deg, #6366F1, #4F46E5)", boxShadow: "0 4px 14px rgba(99,102,241,0.38)", "&:hover": { background: "linear-gradient(135deg, #4F46E5, #4338CA)" } }}
                        startIcon={projectSyncConfigSaving ? <CircularProgress size={14} color="inherit" /> : null}
                      >
                        {projectSyncConfigSaving ? "Saving…" : "Save settings"}
                      </Button>
                    </DialogActions>
                  </Dialog>
                  <Dialog
                    open={employeeSyncConfigOpen}
                    onClose={() => setEmployeeSyncConfigOpen(false)}
                    maxWidth="xs" fullWidth
                    PaperProps={{ className: "qb-dialog-paper" }}
                  >
                    <Box className="qb-dialog-accent-employees" />
                    <DialogTitle className="qb-sx-376">
                      <Box className="qb-sx-377">
                        <Box className="qb-sx-378">
                          <EngineeringRoundedIcon className="qb-sx-379" />
                        </Box>
                        <Box>
                          <Typography fontWeight={800} fontSize={16} lineHeight={1.2}>Employee Sync Settings</Typography>
                          <Typography fontSize={12.5} color="text.secondary" className="qb-sx-380">
                            Applied to every import run for this connection
                          </Typography>
                        </Box>
                      </Box>
                    </DialogTitle>
                    <DialogContent className="qb-sx-381">
                      {employeeSyncConfigLoading ? (
                        <Box className="qb-sx-382">
                          <CircularProgress size={24} />
                        </Box>
                      ) : (
                        <>
                          {/* ── Work start date ── */}
                          <Box className="qb-sx-383">
                            <Typography fontSize={13} fontWeight={700} mb={0.4}>Work start date</Typography>
                            <Typography fontSize={12} color="text.secondary" lineHeight={1.55} mb={1.25}>
                              When an employee is imported from QuickBooks, their work start date in FIELDCRAFT is calculated as:
                            </Typography>

                            {/* Live formula preview */}
                            <Box className="qb-sx-398">
                              <Box className="qb-sx-399">
                                <Chip label="QBO hire date" size="small" className="qb-sx-emp-formula-base" />
                                <Typography fontSize={13} color="text.secondary" fontWeight={600}>+</Typography>
                                <Chip
                                  label={`${Number.isNaN(parseInt(daysInputValue, 10)) ? "?" : parseInt(daysInputValue, 10)} day${parseInt(daysInputValue, 10) === 1 ? "" : "s"}`}
                                  size="small" className="qb-sx-emp-formula-days"
                                />
                                <Typography fontSize={13} color="text.secondary" fontWeight={600}>=</Typography>
                                <Chip label="Work start" size="small" className="qb-sx-emp-formula-result" />
                              </Box>
                            </Box>

                            {/* Amber warning callout */}
                            <Box className="qb-sx-400">
                              <WarningAmberRoundedIcon className="qb-sx-400-icon" />
                              <Typography fontSize={12} lineHeight={1.5}>
                                Employees without a hire date in QuickBooks will be skipped and marked{" "}
                                <Box component="span" className="qb-import-failed-label">Import Failed</Box>.
                                Add a hire date in QuickBooks, then sync again.
                              </Typography>
                            </Box>

                            <TextField
                              id="qb-field-days-after-hire"
                              label="Days after hire"
                              type="number"
                              size="small"
                              fullWidth
                              value={daysInputValue}
                              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDaysInputValue(e.target.value)}
                              onBlur={() => {
                                const parsed = parseInt(daysInputValue, 10);
                                const clamped = Number.isNaN(parsed) || parsed < 1 ? 1 : Math.min(365, parsed);
                                setDaysInputValue(String(clamped));
                                setEmployeeSyncConfig((prev: EmployeeSyncConfig) => ({ ...prev, days_after_hire: clamped }));
                              }}
                              inputProps={{ min: 1, max: 365, step: 1 }}
                              helperText="Enter a value between 1 and 365"
                              disabled={employeeSyncConfigSaving}
                            />
                          </Box>

                          {/* ── Worker classification (derived from QuickBooks) ── */}
                          <Box className="qb-sx-384">
                            <Typography fontSize={13} fontWeight={700} mb={0.4}>Worker classification</Typography>
                            <Typography fontSize={12} color="text.secondary" lineHeight={1.55}>
                              Classification is automatic — QuickBooks <Box component="span" fontWeight={700} color="text.primary">Employees</Box> import as W-2, and QuickBooks <Box component="span" fontWeight={700} color="text.primary">Vendors with 1099 tracking</Box> import as 1099 contractors.
                            </Typography>
                          </Box>
                        </>
                      )}
                    </DialogContent>
                    <DialogActions className="qb-sx-353">
                      <Button
                        id="qb-btn-employee-sync-config-cancel"
                        onClick={() => setEmployeeSyncConfigOpen(false)}
                        disabled={employeeSyncConfigSaving}
                        variant="outlined" className="qb-sx-354"
                      >Cancel</Button>
                      <Button
                        id="qb-btn-employee-sync-config-save"
                        variant="contained"
                        onClick={handleSaveEmployeeSyncConfig}
                        disabled={employeeSyncConfigSaving || employeeSyncConfigLoading}
                        startIcon={employeeSyncConfigSaving ? <CircularProgress size={14} color="inherit" /> : null}
                        className="qb-sx-355"
                      >
                        {employeeSyncConfigSaving ? "Saving…" : "Save settings"}
                      </Button>
                    </DialogActions>
                  </Dialog>
                  {/* Development/testing-only TimeActivity delete dialog disabled for PR.
                  {QBO_DESTRUCTIVE_MAINTENANCE_ENABLED && (
                    <Dialog
                      open={timelogDeleteDialogOpen}
                      onClose={() => { if (!timelogDeleteLoading) setTimelogDeleteDialogOpen(false); }}
                      maxWidth="sm"
                      fullWidth
                    >
                      <DialogTitle>Delete synced QuickBooks TimeActivity records?</DialogTitle>
                      <DialogContent>
                        <Alert severity="warning" sx={{ mb: 2 }}>
                          Development/testing only. This deletes only QuickBooks TimeActivity records currently mapped by FIELDCRAFT.
                        </Alert>
                        <Typography fontSize={13.5} color="text.secondary" lineHeight={1.6}>
                          Deleted mappings are moved back to Pending so the same FIELDCRAFT time can be synced again.
                        </Typography>
                      </DialogContent>
                      <DialogActions>
                        <Button
                          onClick={() => setTimelogDeleteDialogOpen(false)}
                          disabled={timelogDeleteLoading}
                          variant="outlined"
                        >
                          Cancel
                        </Button>
                        <Button
                          id="qb-btn-confirm-delete-all-time-entries"
                          onClick={handleDeleteMappedTimeActivities}
                          disabled={timelogDeleteLoading}
                          color="error"
                          variant="contained"
                          startIcon={timelogDeleteLoading ? <CircularProgress size={14} color="inherit" /> : <DeleteOutlineRoundedIcon />}
                        >
                          {timelogDeleteLoading ? "Deleting…" : "Delete mapped time"}
                        </Button>
                      </DialogActions>
                    </Dialog>
                  )}
                  */}
                  <Dialog
                    open={serviceCoaAssignDialogOpen}
                    onClose={() => { if (!assigningServiceRoleCoa) { setServiceCoaAssignDialogOpen(false); setSelectedServiceCoaOption(null); } }}
                    maxWidth="xs" fullWidth
                    PaperProps={{ className: "qb-dialog-paper" }}
                  >
                    <Box className="qb-dialog-accent-services" />
                    <DialogTitle className="qb-sx-376">
                      <Box className="qb-sx-377">
                        <Box className="qb-sx-378">
                          <AccountTreeRoundedIcon className="qb-sx-379" />
                        </Box>
                        <Box>
                          <Typography fontWeight={800} fontSize={16} lineHeight={1.2}>Assign Chart of Account</Typography>
                          <Typography fontSize={12.5} color="text.secondary" className="qb-sx-380">
                            {selectedServiceRoleIds.length} role{selectedServiceRoleIds.length !== 1 ? "s" : ""} selected
                          </Typography>
                        </Box>
                      </Box>
                    </DialogTitle>
                    <DialogContent className="qb-sx-381">
                      <Box className="qb-sx-383">
                        <Typography fontSize={13} color="text.secondary" lineHeight={1.65} mb={1.5}>
                          Choose a Chart of Account to use as the{" "}
                          <Box component="span" fontWeight={600} color="text.primary">IncomeAccountRef</Box>{" "}
                          when creating QuickBooks service items for the selected role{selectedServiceRoleIds.length !== 1 ? "s" : ""}.
                        </Typography>
                        {serviceIncomeAccounts.length === 0 ? (
                          <Box className="qb-sx-400">
                            <WarningAmberRoundedIcon className="qb-sx-400-icon" />
                            <Typography fontSize={12} lineHeight={1.5}>
                              No synced chart of accounts available. Sync{" "}
                              <Box component="span" fontWeight={700} color="text.primary">Chart of Accounts</Box>{" "}
                              first, then return here to assign.
                            </Typography>
                          </Box>
                        ) : (
                          <Autocomplete<ServiceRoleIncomeAccount>
                            id="qb-service-coa-assign-autocomplete"
                            options={[...serviceIncomeAccounts].sort((a, b) => {
                              const ta = a.accountType ?? "Other";
                              const tb = b.accountType ?? "Other";
                              return ta.localeCompare(tb) || a.name.localeCompare(b.name);
                            })}
                            groupBy={(option: ServiceRoleIncomeAccount) => option.accountType ?? "Other"}
                            getOptionLabel={(option: ServiceRoleIncomeAccount) => option.name}
                            value={selectedServiceCoaOption}
                            onChange={(_: any, newValue: ServiceRoleIncomeAccount | null) => setSelectedServiceCoaOption(newValue)}
                            isOptionEqualToValue={(option: ServiceRoleIncomeAccount, value: ServiceRoleIncomeAccount) => option.id === value.id}
                            disabled={assigningServiceRoleCoa}
                            fullWidth
                            size="small"
                            filterOptions={(options: ServiceRoleIncomeAccount[], { inputValue }: { inputValue: string }) => {
                              const q = inputValue.toLowerCase();
                              if (!q) return options;
                              return options.filter((o: ServiceRoleIncomeAccount) =>
                                o.name.toLowerCase().includes(q) ||
                                (o.accountType ?? "").toLowerCase().includes(q) ||
                                (o.detailType ?? "").toLowerCase().includes(q) ||
                                (o.externalAccountId ?? "").toLowerCase().includes(q)
                              );
                            }}
                            renderInput={(params: any) => (
                              <TextField
                                {...params}
                                label="Search Chart of Account"
                                placeholder="Type to filter by name, type, or QB ID…"
                                InputProps={{
                                  ...params.InputProps,
                                  startAdornment: (
                                    <>
                                      <SearchRoundedIcon className="qb-sx-410" />
                                      {params.InputProps.startAdornment}
                                    </>
                                  ),
                                }}
                              />
                            )}
                            renderOption={(props: any, option: ServiceRoleIncomeAccount, { selected }: { selected: boolean }) => (
                              <Box
                                component="li"
                                {...props}
                                key={option.id}
                                sx={{
                                  py: "8px !important",
                                  px: "12px !important",
                                  alignItems: "flex-start !important",
                                  bgcolor: selected ? "#F0FDFA !important" : undefined,
                                  "&:hover": { bgcolor: "#F8FAFC !important" },
                                  borderBottom: "1px solid",
                                  borderColor: "divider",
                                  "&:last-of-type": { borderBottom: "none" },
                                }}
                              >
                                <Box className="qb-sx-411">
                                  <Box sx={{
                                    width: 7, height: 7, borderRadius: "50%", mt: "5px", flexShrink: 0,
                                    bgcolor: selected ? "#0F766E" : "#CBD5E1",
                                    boxShadow: selected ? "0 0 0 2px #CCFBF1" : "none",
                                    transition: "all 0.15s",
                                  }} />
                                  <Box className="qb-sx-412">
                                    <Typography fontWeight={selected ? 700 : 500} fontSize={13} lineHeight={1.3} color={selected ? "#0F766E" : "text.primary"} noWrap>
                                      {option.name}
                                    </Typography>
                                    <Box className="qb-sx-413">
                                      {option.detailType && option.detailType !== option.accountType && (
                                        <Chip label={option.detailType} size="small" className="qb-sx-414" />
                                      )}
                                      {option.externalAccountId && (
                                        <Typography fontSize={10.5} color="text.disabled" className="qb-sx-415">QB: {option.externalAccountId}</Typography>
                                      )}
                                    </Box>
                                  </Box>
                                  {selected && <CheckCircleOutlineRoundedIcon className="qb-sx-416" />}
                                </Box>
                              </Box>
                            )}
                            noOptionsText={
                              <Box className="qb-sx-417">
                                <Typography fontSize={12.5} color="text.secondary">No accounts match your search</Typography>
                              </Box>
                            }
                            ListboxProps={{ style: { maxHeight: 260, padding: "0" } }}
                            componentsProps={{
                              paper: {
                                elevation: 6,
                                sx: {
                                  mt: 0.5, borderRadius: 2, border: "1px solid", borderColor: "divider", overflow: "hidden",
                                  "& .MuiAutocomplete-listbox": { p: 0 },
                                  "& .MuiAutocomplete-groupLabel": {
                                    fontSize: 10, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase",
                                    color: "#475569", lineHeight: "26px", px: 1.5, bgcolor: "#F1F5F9",
                                    borderBottom: "1px solid", borderBottomColor: "divider", position: "sticky", top: 0, zIndex: 1,
                                  },
                                  "& .MuiAutocomplete-groupUl": { p: 0 },
                                },
                              },
                            }}
                          />
                        )}
                      </Box>
                    </DialogContent>
                    <DialogActions className="qb-sx-353">
                      <Button
                        id="qb-btn-service-coa-assign-cancel"
                        onClick={() => { setServiceCoaAssignDialogOpen(false); setSelectedServiceCoaOption(null); }}
                        disabled={assigningServiceRoleCoa}
                        variant="outlined" className="qb-sx-354"
                      >Cancel</Button>
                      <Button
                        id="qb-btn-service-coa-assign-confirm"
                        variant="contained"
                        onClick={handleBulkAssignServiceRoleCoa}
                        disabled={assigningServiceRoleCoa || !selectedServiceCoaOption || selectedServiceRoleIds.length === 0}
                        startIcon={assigningServiceRoleCoa ? <CircularProgress size={14} color="inherit" /> : null}
                        className="qb-sx-355"
                      >
                        {assigningServiceRoleCoa ? "Assigning…" : "Assign COA"}
                      </Button>
                    </DialogActions>
                  </Dialog>

            </Box>{/* /qbw-work */}
          </Box>{/* /qbw-root */}

          {/* "How it works" — a panel opened from the rail or a module's attachment
              strip. It is portalled by MUI, so it sits outside the workspace card. */}
          <SyncPipelineGuide
            open={syncGuideOpen}
            onClose={() => setSyncGuideOpen(false)}
            selectedModule={selectedModule}
            onSelectModule={setSelectedModule}
          />
          </>
        )}

      </Box>
    </Box>
  );
};

export default QuickBooksTable;

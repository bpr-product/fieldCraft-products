/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*/
import {
  executeGetData,
  executeGetDataWithHeaders,
  executePostDataWithHeaders,
} from "../ApiBase";
import { ConfigUrl } from "../../auth/OidcConfig";
import { getUserSessionDetails, userSession } from "../../hooks/UserSession";

let auditUserPromise: Promise<any> | null = null;
let credentialPromise: Promise<any> | null = null;

async function buildAuditUser(accessToken: any) {
  if (userSession) {
    return normalizeAuditUser(userSession);
  }
  if (!auditUserPromise) {
    auditUserPromise = getUserSessionDetails(accessToken)
      .then((session) => normalizeAuditUser(userSession ?? session ?? {}))
      .finally(() => {
        auditUserPromise = null;
      });
  }
  return await auditUserPromise;
}

function normalizeAuditUser(session: any) {
  const objectId = session?.objectId;
  const email = session?.email;
  const roles: string[] = Array.isArray(session?.role) ? session.role : [];
  const user_role = roles.join(",");
  const user_agent = navigator.userAgent;
  return { objectId, email, user_role, user_agent };
}

type WithAudit = {
  object_id?: string;
  email?: string;
  user_role?: string;
  user_agent?: string;
  source?: string;
};

async function withAudit<T extends WithAudit>(
  accessToken: any,
  req: T,
  override?: Partial<WithAudit>
): Promise<T> {
  const auditUser = await buildAuditUser(accessToken);
  return {
    ...req,
    object_id: req.object_id ?? auditUser.objectId,
    email: auditUser.email,
    user_role: auditUser.user_role,
    user_agent: auditUser.user_agent,
    source: req.source ?? "Web",
    ...override,
  };
}

export function resetQuickBooksAuditUserCache() {
  auditUserPromise = null;
}

// The external-services gateway requires two factors on every protected QuickBooks route:
// the user's bearer token AND the shared api-secret. The secret is never shipped in the
// bundle — it is fetched per session from the tenant API (/v1/quickbooks/credential) and
// attached as the "api-secret" header by qbGet/qbPost.
export const getQuickBooksCredential = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v1/quickbooks/credential`;
  return await executeGetData(url, accessToken);
};

const getQuickBooksSecretHeader = async (accessToken: any) => {
  if (!credentialPromise) {
    credentialPromise = getQuickBooksCredential(accessToken).finally(() => {
      credentialPromise = null;
    });
  }
  const cred: any = await credentialPromise;
  const secretKey =
    cred?.data?.secret_key ??
    cred?.data?.secretKey ??
    cred?.data?.secret ??
    cred?.secret_key ??
    cred?.secretKey ??
    "";
  if (!secretKey) {
    credentialPromise = null; // don't cache a bad result — next call retries fresh
    throw new Error("QuickBooks secret key not found");
  }
  return { "api-secret": secretKey };
};

const qbGet = async (url: string, accessToken: any) => {
  const secretHeader = await getQuickBooksSecretHeader(accessToken);
  return await executeGetDataWithHeaders(url, accessToken, secretHeader);
};

const qbPost = async (url: string, payload: any, accessToken: any) => {
  const secretHeader = await getQuickBooksSecretHeader(accessToken);
  return await executePostDataWithHeaders(url, payload, accessToken, secretHeader);
};

export type QuickBooksAuthError = {
  code?: string;
  reason?: string;
  detail?: string;
  at?: string;
};

export type QuickBooksStatusResponse = {
  connected?: boolean;
  status?: string;
  env?: string;
  realmId?: string;
  refreshExpiresAt?: string;
  accessExpiresAt?: string;
  connectedAt?: string;
  lastSyncAt?: string | null;
  lastPaymentSyncAt?: string | null;
  lastVendorSyncAt?: string | null;
  lastAccountSyncAt?: string | null;
  lastBillSyncAt?: string | null;
  lastBillPaymentSyncAt?: string | null;
  lastCustomerSyncAt?: string | null;
  lastProjectSyncAt?: string | null;
  lastServiceSyncAt?: string | null;
  lastTimelogSyncAt?: string | null;
  lastInvoiceSyncAt?: string | null;
  reconnectRequired?: boolean;
  authError?: QuickBooksAuthError | null;
};

export const initiateQuickBooksAuth = async (
  accessToken: any,
  tenantId: string,
  serviceId: string,
  objectId?: string,
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooksAuth`;
  const payload = await withAudit(accessToken, {
    tenant_id: tenantId,
    service_id: serviceId,
    object_id: objectId,
  });
  return await qbPost(url, payload, accessToken);
};

export const getQuickBooksStatus = async (
  accessToken: any,
  tenantId: string,
  serviceId: string,
  objectId?: string
) => {
  const url =
    `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/status` +
    `?tenantId=${encodeURIComponent(tenantId)}` +
    `&serviceId=${encodeURIComponent(serviceId)}` +
    (objectId ? `&object_id=${encodeURIComponent(objectId)}` : "");
  return await qbGet(url, accessToken);
};

export type QuickBooksAutoSyncConfig = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  enabled: boolean;
};

export const getQuickBooksAutoSyncConfig = async (
  accessToken: any,
  tenantId: string,
  serviceId: string,
) => {
  const url =
    `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/auto-sync-config` +
    `?tenantId=${encodeURIComponent(tenantId)}` +
    `&serviceId=${encodeURIComponent(serviceId)}`;
  return await qbGet(url, accessToken);
};

export const saveQuickBooksAutoSyncConfig = async (
  accessToken: any,
  req: {
    tenant_id: string;
    service_id: string;
    enabled: boolean;
    object_id?: string;
  },
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/auto-sync-config`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export const disconnectQuickBooks = async (
  accessToken: any,
  tenantId: string,
  serviceId: string,
  objectId?: string
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/disconnect`;
  const payload = await withAudit(accessToken, {
    tenant_id: tenantId,
    service_id: serviceId,
    object_id: objectId,
  });
  return await qbPost(url, payload, accessToken);
};

export type CustomersActionRequest = {
  tenant_id: string;
  service_id: string;
  object_id?: string;
  email?: string;
  user_role?: string;
  user_agent?: string;
  source?: string;
  sync_mode?: string;
};

export type QuickBooksSyncModuleState = {
  activeJobId?: number | null;
  activeStatus?: string | null;
  lastJobId?: number | null;
  lastStatus?: string | null;
  lastCompletedAt?: string | null;
  lastResult?: any | null;
};

export type QuickBooksScheduledJob = {
  id: number;
  jobType?: string;
  status?: string;
  createdBy?: string;
  createdTime?: string | null;
  startTime?: string | null;
  completedTime?: string | null;
  additionalData?: any;
  result?: any;
};

export type QuickBooksStartJobResponse = {
  ok?: boolean;
  jobId?: number;
  status?: string;
  reused?: boolean;
};

export const getQuickBooksSyncState = async (
  accessToken: any,
  tenantId: string,
  serviceId: string,
  objectId: string
) => {
  const url =
    `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/sync/state` +
    `?tenantId=${encodeURIComponent(tenantId)}` +
    `&serviceId=${encodeURIComponent(serviceId)}` +
    `&objectId=${encodeURIComponent(objectId)}`;
  return await qbGet(url, accessToken);
};

export const getQuickBooksSyncJob = async (
  accessToken: any,
  tenantId: string,
  jobId: number
) => {
  const url =
    `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/sync/job` +
    `?tenantId=${encodeURIComponent(tenantId)}` +
    `&jobId=${encodeURIComponent(String(jobId))}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksVendorsSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/vendors/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export const exportQuickBooksPayments = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/payments/export`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export const importQuickBooksPayments = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/payments/import`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export const startQuickBooksPaymentsSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/payments/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export type CustomerPaymentCdcRow = {
  paymentId: number;
  paymentReferenceNo?: string | null;
  customerUid?: string | null;
  customerName?: string | null;
  projectId?: number | null;
  projectName?: string | null;
  paymentStatus?: string | null;
  receivedTime?: string | null;
  amount?: number | null;
  appliedAmount?: number | null;
  unappliedAmount?: number | null;
  allocationCount?: number | null;
  mappingId?: number | null;
  externalPaymentId?: string | null;
  externalAccountId?: string | null;
  syncStatus?: string | null;
  mappingUpdatedAt?: string | null;
  reason?: string | null;
  source?: "FIELDCRAFT" | null;
};

export type CustomerPaymentCdcSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  notReady: number;
};

export type CustomerPaymentCdcResponse = {
  ok?: boolean;
  summary: CustomerPaymentCdcSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: CustomerPaymentCdcRow[];
};

export const getQuickBooksPaymentCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });
  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/payments/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export type CustomerPaymentAllocationDetail = {
  id: number;
  payment_receives_id: number;
  project_id?: number | null;
  project_name?: string | null;
  payment_amount?: number | null;
  interest_paid?: number | null;
  late_fee_paid?: number | null;
  reference_no?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  fieldcraft_invoice_id?: number | null;
  invoice_status?: string | null;
  external_invoice_id?: string | null;
  invoice_sync_status?: string | null;
};

export type CustomerPaymentDetailResponse = {
  ok?: boolean;
  mapping: {
    id: number;
    external_payment_id?: string | null;
    external_account_id?: string | null;
    sync_status?: string | null;
    sync_token?: string | null;
    version?: number | null;
    reason?: string | null;
    updated_at?: string | null;
    additional_info?: Record<string, any> | null;
  };
  payment: {
    id: number;
    chart_of_account_id?: number | null;
    project_id?: number | null;
    project_name?: string | null;
    customer_uid?: string | null;
    customer_name?: string | null;
    payment_reference_no?: string | null;
    fieldcraft_transaction_id?: string | null;
    payment_receipt_id?: string | null;
    payment_transaction_id?: string | null;
    amount?: number | null;
    suspense_amount?: number | null;
    currency_code?: string | null;
    exchange_rate?: string | null;
    pay_method?: string | null;
    description?: string | null;
    status?: string | null;
    received_time?: string | null;
    updated_at?: string | null;
  };
  allocations: CustomerPaymentAllocationDetail[];
  totals: {
    amount: number;
    applied_amount: number;
    unapplied_amount: number;
    allocation_count: number;
  };
};

export const getQuickBooksPaymentDetails = async (
  accessToken: any,
  mappingId: number,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({ tenantId: params.tenantId, serviceId: params.serviceId });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/payments/${mappingId}/details?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksChartOfAccountsSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/accounts/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export type VendorCdcRow = {
  vendorId: number;
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
  syncToken?: string | null;
  mappingVersion?: number | null;
  endorsementEffectiveDate?: string | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
};

export type VendorCdcSummary = {
  totalVendors: number;
  mappedVendors: number;
  unmappedVendors: number;
  syncedVendors: number;
  pendingPushVendors: number;
  failedVendors: number;
  importFailedVendors: number;
};

export type VendorCdcResponse = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  summary: VendorCdcSummary;
  pagination: {
    page: number;
    pageSize: number;
    totalRecords: number;
    totalPages: number;
  };
  rows: VendorCdcRow[];
};

export const getQuickBooksVendorCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
    mappedOnly?: boolean;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });

  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  if (params.mappedOnly) query.set("mappedOnly", "true");

  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/vendors/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export type AccountCdcRow = {
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
};

export type AccountCdcSummary = {
  totalAccounts: number;
  syncedAccounts: number;
  pendingPushAccounts: number;
};

export type AccountCdcResponse = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  summary: AccountCdcSummary;
  pagination: {
    page: number;
    pageSize: number;
    totalRecords: number;
    totalPages: number;
  };
  rows: AccountCdcRow[];
};

export const getQuickBooksAccountCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });

  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);

  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/accounts/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export type BillCdcRow = {
  receiptId: number | null;
  receiptUuid?: string | null;
  displayName: string;
  billName?: string | null;
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
  status?: string | null;
  amount?: number | null;
  mappingId?: number | null;
  externalReceiptId?: string | null;
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

export type BillCdcSummary = {
  totalBills: number;
  mappedBills: number;
  unmappedBills: number;
  syncedBills: number;
  pendingPushBills: number;
  failedBills: number;
  importFailedBills: number;
};

export type BillCdcResponse = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  summary: BillCdcSummary;
  pagination: {
    page: number;
    pageSize: number;
    totalRecords: number;
    totalPages: number;
  };
  rows: BillCdcRow[];
};

export const getQuickBooksBillCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
    mappedOnly?: boolean;
    projectSearch?: string;
    sortBy?: string;
    sortDirection?: "ASC" | "DESC";
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });

  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  if (params.mappedOnly) query.set("mappedOnly", "true");
  if (params.projectSearch) query.set("projectSearch", params.projectSearch);
  if (params.sortBy) query.set("sortBy", params.sortBy);
  if (params.sortDirection) query.set("sortDirection", params.sortDirection);

  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bills/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksBillsSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bills/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};


export type BillPaymentCdcRow = {
  paymentExpenseId: number | null;
  displayName: string;
  receiptId?: number | null;
  billDisplayName?: string | null;
  vendorId?: number | null;
  vendorCompany?: string | null;
  amount?: number | null;
  status?: string | null;
  paymentDate?: string | null;
  chartOfAccountId?: number | null;
  projectId?: number | null;
  projectName?: string | null;
  billName?: string | null;
  billInvoiceNumber?: string | null;
  mappingId?: number | null;
  externalPaymentId?: string | null;
  externalInvoiceId?: string | null;
  externalAccountId?: string | null;
  assignedAccountName?: string | null;
  assignedAccountType?: string | null;
  assignedAccountDetailType?: string | null;
  assignedEligiblePayType?: "Check" | "CreditCard" | null;
  assigned?: boolean;
  syncStatus?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
  source?: "QuickBooks" | "FIELDCRAFT" | null;
};

export type BillPaymentCdcSummary = {
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

export type BillPaymentCdcResponse = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  summary: BillPaymentCdcSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: BillPaymentCdcRow[];
};

export type BillPaymentEligibleAccount = {
  id: number;
  name?: string | null;
  account_type?: string | null;
  detail_type?: string | null;
  external_id?: string | null;
  external_acc_id?: string | null;
  eligible_pay_type?: "Check" | "CreditCard" | null;
  active?: boolean;
};

export const getQuickBooksBillPaymentAccounts = async (
  accessToken: any,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
  });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bill-payments/payment-accounts?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const assignQuickBooksBillPaymentAccount = async (
  accessToken: any,
  req: {
    tenant_id: string;
    service_id: string;
    payment_expense_ids: number[];
    qbo_external_account_id: string;
    object_id?: string;
    email?: string;
    user_role?: string;
    user_agent?: string;
    source?: string;
  }
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bill-payments/assign-account`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export const getQuickBooksBillPaymentCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
    mappedOnly?: boolean;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });
  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  if (params.mappedOnly) query.set("mappedOnly", "true");
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bill-payments/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksBillPaymentsSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bill-payments/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export type BillLineItem = {
  description?: string | null;
  productCode?: string | null;
  quantity?: number | null;
  unitPrice?: number | null;
  amount: number;
  lineKind?: "category" | "item" | null;
};

export const getQuickBooksBillLineItems = async (
  accessToken: any,
  receiptId: number,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
  });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bills/${receiptId}/line-items?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export type BillDocumentInfo = {
  hasDocument: boolean;
  documentId?: number | null;
  name?: string | null;
  mimeType?: string | null;
  size?: number | null;
};

export const getQuickBooksBillDocumentInfo = async (
  accessToken: any,
  receiptId: number,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
  });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bills/${receiptId}/document-info?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export type CustomerCdcRow = {
  customerId: number;
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
  syncToken?: string | null;
  mappingVersion?: number | null;
  endorsementEffectiveDate?: string | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
  source?: "QuickBooks" | "FIELDCRAFT" | null;
};

export type CustomerCdcSummary = {
  totalCustomers: number;
  mappedCustomers: number;
  unmappedCustomers: number;
  syncedCustomers: number;
  pendingPushCustomers: number;
  failedCustomers: number;
  importFailedCustomers: number;
};

export type CustomerCdcResponse = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  summary: CustomerCdcSummary;
  pagination: {
    page: number;
    pageSize: number;
    totalRecords: number;
    totalPages: number;
  };
  rows: CustomerCdcRow[];
};

export const getQuickBooksCustomerCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
    mappedOnly?: boolean;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });

  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  if (params.mappedOnly) query.set("mappedOnly", "true");

  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/customers/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksCustomersSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/customers/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export const downloadQuickBooksBillDocument = async (
  accessToken: any,
  receiptId: number,
  params: { tenantId: string; serviceId: string }
): Promise<{ objectUrl: string; mimeType: string; fileName: string }> => {
  const secretHeader = await getQuickBooksSecretHeader(accessToken);
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
  });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/bills/${receiptId}/document?${query.toString()}`;
  const token: string =
    typeof accessToken === "string"
      ? accessToken
      : (accessToken?.accessToken ?? accessToken?.access_token ?? "");
  const res = await fetch(url, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...secretHeader,
    },
  });
  if (!res.ok) throw new Error(`Document fetch failed: ${res.status}`);
  const blob = await res.blob();
  const mimeType =
    res.headers.get("Content-Type") || blob.type || "application/octet-stream";
  const disposition = res.headers.get("Content-Disposition") || "";
  const fileNameMatch = disposition.match(/filename="([^"]+)"/);
  const fileName = fileNameMatch?.[1] || "document";
  return { objectUrl: URL.createObjectURL(blob), mimeType, fileName };
};

// ── Projects ──────────────────────────────────────────────────

export type ProjectCdcRow = {
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

export type ProjectCdcSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  byStatus?: Record<string, number>;
};

export type ProjectCdcResponse = {
  ok?: boolean;
  summary: ProjectCdcSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: ProjectCdcRow[];
};

export const getQuickBooksProjectCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });
  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/projects/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksProjectsSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/projects/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export type ProjectSyncMethod = "AUTO" | "QBO_PROJECT" | "SUB_CUSTOMER";
export type ProjectSyncTarget = "QBO_PROJECT" | "SUB_CUSTOMER";

export type ProjectSyncConfig = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  configured?: boolean;
  method: ProjectSyncMethod;
  projectsAvailable?: boolean;
  effectiveProjectSyncTarget?: ProjectSyncTarget | null;
  message?: string | null;
  capabilityError?: string | null;
};

export const DEFAULT_PROJECT_SYNC_CONFIG: ProjectSyncConfig = {
  method: "AUTO",
  configured: false,
  projectsAvailable: false,
  effectiveProjectSyncTarget: "SUB_CUSTOMER",
};

export const getQuickBooksProjectSyncConfig = async (
  accessToken: any,
  tenantId: string,
  serviceId: string,
) => {
  const url =
    `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/projects/sync-config` +
    `?tenantId=${encodeURIComponent(tenantId)}` +
    `&serviceId=${encodeURIComponent(serviceId)}`;
  return await qbGet(url, accessToken);
};

export const saveQuickBooksProjectSyncConfig = async (
  accessToken: any,
  req: {
    tenant_id: string;
    service_id: string;
    method: ProjectSyncMethod;
    object_id?: string;
  }
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/projects/sync-config`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

// ── Employees ─────────────────────────────────────────────────

export type EmployeeCdcRow = {
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
  externalAccountId?: string | null;
  syncStatus?: string | null;
  syncToken?: string | null;
  mappingVersion?: number | null;
  endorsementEffectiveDate?: string | null;
  mappingUpdatedAt?: string | null;
  linked?: boolean;
  reason?: string | null;
  source?: "FIELDCRAFT" | null;
};

export type EmployeeCdcSummary = {
  totalEmployees: number;
  mappedEmployees: number;
  unmappedEmployees: number;
  syncedEmployees: number;
  pendingPushEmployees: number;
  failedEmployees: number;
  importFailedEmployees: number;
};

export type EmployeeCdcResponse = {
  ok?: boolean;
  tenantId?: string;
  serviceId?: string;
  summary: EmployeeCdcSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: EmployeeCdcRow[];
};

export const getQuickBooksEmployeeCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
    mappedOnly?: boolean;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });
  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  if (params.mappedOnly) query.set("mappedOnly", "true");
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/employees/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksEmployeesSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/employees/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

// ── Employee sync configuration ────────────────────────────────────────────

/** Shape of the per-tenant employee sync settings stored in additional_info. */
export type EmployeeSyncConfig = {
  /** Days (≥ 1) added to the QuickBooks hire date to produce the employee's work start date. */
  days_after_hire: number;
  /**
   * True when the tenant has explicitly saved settings at least once.
   * False (or absent) when the backend has no saved config — syncing is blocked until configured.
   */
  configured?: boolean;
};

/**
 * Default UI state — used before the tenant saves their first config.
 * Worker type (W2/1099) is no longer configured; it is derived from QuickBooks
 * on import (QBO Employee → W2, 1099-tracked QBO Vendor → 1099).
 */
export const DEFAULT_EMPLOYEE_SYNC_CONFIG: EmployeeSyncConfig = {
  days_after_hire: 1,
  configured: false,
};

export const getQuickBooksEmployeeSyncConfig = async (
  accessToken: any,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
  });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/employees/sync-config?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const saveQuickBooksEmployeeSyncConfig = async (
  accessToken: any,
  req: {
    tenant_id: string;
    service_id: string;
    days_after_hire: number;
    object_id?: string;
  }
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/employees/sync-config`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

// ── Service Roles ─────────────────────────────────────────────

export type ServiceRoleCdcRow = {
  id?: number | null;
  roleId: number | null;
  roleName: string;
  role_name?: string | null;
  name?: string | null;
  description?: string | null;
  active?: boolean | null;
  mappingId?: number | null;
  externalServiceId?: string | null;
  external_service_id?: string | null;
  externalAccountId?: string | null;
  externalDisplayName?: string | null;
  external_display_name?: string | null;
  syncStatus?: string | null;
  sync_status?: string | null;
  syncToken?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  updatedAt?: string | null;
  lastSyncedAt?: string | null;
  reason?: string | null;
  /** Backend may return as object or JSON string */
  additionalInfo?: Record<string, any> | string | null;
  additional_info?: Record<string, any> | string | null;
  source?: "FIELDCRAFT" | null;
};

export type ServiceRoleIncomeAccount = {
  id: number;
  name: string;
  accountType?: string | null;
  account_type?: string | null;
  detailType?: string | null;
  detail_type?: string | null;
  externalAccountId?: string | null;
  external_account_id?: string | null;
  externalId?: string | null;
  external_id?: string | null;
  syncStatus?: string | null;
  sync_status?: string | null;
};

export type ServiceRoleCdcSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
};

export type ServiceRoleCdcResponse = {
  ok?: boolean;
  summary: ServiceRoleCdcSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: ServiceRoleCdcRow[];
};

export const getQuickBooksServiceRoleCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });
  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/services/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksServiceRolesSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/services/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

export const getQuickBooksServiceIncomeAccounts = async (
  accessToken: any,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
  });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/services/income-accounts?${query.toString()}`;
  return await qbGet(url, accessToken);
};

/** Preferred endpoint — returns all synced COA records, not only income accounts */
export const getQuickBooksServiceCoaAccounts = async (
  accessToken: any,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
  });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/services/coa-accounts?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const assignQuickBooksServiceIncomeAccount = async (
  accessToken: any,
  req: {
    tenant_id: string;
    service_id: string;
    /** Required. COA is assigned per service role; send one or more role ids. */
    role_ids: number[];
    chart_of_account_id: number;
    object_id?: string;
  }
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/services/assign-income-account`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};


// ── Invoices ─────────────────────────────────────────────────

export type InvoiceCdcRow = {
  invoiceId: number;
  invoiceNo: string;
  customerUid?: string | null;
  customerName?: string | null;
  projectId?: number | null;
  projectName?: string | null;
  invoiceStatus?: string | null;
  invoiceDate?: string | null;
  dueDate?: string | null;
  amount?: number | null;
  amountBeforeTax?: number | null;
  baseAmount?: number | null;
  taxAmount?: number | null;
  mappingId?: number | null;
  externalInvoiceId?: string | null;
  externalAccountId?: string | null;
  syncStatus?: string | null;
  syncToken?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  reason?: string | null;
  source?: "FIELDCRAFT" | null;
};

export type InvoiceCdcSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  notReady: number;
};

export type InvoiceCdcResponse = {
  ok?: boolean;
  summary: InvoiceCdcSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: InvoiceCdcRow[];
};

export const getQuickBooksInvoiceCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });
  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/invoices/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export type InvoiceDetailResponse = {
  ok?: boolean;
  mapping: {
    id: number;
    external_invoice_id?: string | null;
    external_account_id?: string | null;
    sync_status?: string | null;
    sync_token?: string | null;
    version?: number | null;
    reason?: string | null;
    updated_at?: string | null;
    additional_info?: Record<string, any> | null;
  };
  invoice: {
    id: number;
    invoice_no: string;
    customer_uid?: string | null;
    customer_name?: string | null;
    project_id?: number | null;
    project_name?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
    issue_at?: string | null;
    due_date?: string | null;
    status?: string | null;
    invoice_message?: string | null;
    term?: string | null;
    balance?: number | null;
    paid_amount?: number | null;
    balance_due?: number | null;
    tax_amount?: number | null;
    invoiced_amount?: number | null;
    amount_before_tax?: number | null;
    total_amount?: number | null;
    items?: Record<string, any>[];
    tax_info?: Record<string, any>[];
    additional_info?: Record<string, any> | null;
    version?: number | null;
    type?: string | null;
    document_id?: number | null;
  };
  document?: {
    id: number;
    name?: string | null;
    mime_type?: string | null;
    path?: string | null;
    size?: number | null;
    document_type?: string | null;
    project_id?: number | null;
    status?: string | null;
    invalid?: boolean | null;
    updated_at?: string | null;
  } | null;
};

export const getQuickBooksInvoiceDetails = async (
  accessToken: any,
  mappingId: number,
  params: { tenantId: string; serviceId: string }
) => {
  const query = new URLSearchParams({ tenantId: params.tenantId, serviceId: params.serviceId });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/invoices/${mappingId}/details?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const downloadQuickBooksInvoiceDocument = async (
  accessToken: any,
  mappingId: number,
  params: { tenantId: string; serviceId: string }
): Promise<{ objectUrl: string; mimeType: string; fileName: string }> => {
  const secretHeader = await getQuickBooksSecretHeader(accessToken);
  const query = new URLSearchParams({ tenantId: params.tenantId, serviceId: params.serviceId });
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/invoices/${mappingId}/document?${query.toString()}`;
  const token: string =
    typeof accessToken === "string"
      ? accessToken
      : (accessToken?.accessToken ?? accessToken?.access_token ?? "");
  const res = await fetch(url, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...secretHeader,
    },
  });
  if (!res.ok) throw new Error(`Invoice document fetch failed: ${res.status}`);
  const blob = await res.blob();
  const mimeType = res.headers.get("Content-Type") || blob.type || "application/octet-stream";
  const disposition = res.headers.get("Content-Disposition") || "";
  const fileNameMatch = disposition.match(/filename="([^"]+)"/);
  const fileName = fileNameMatch?.[1] || "invoice-document";
  return { objectUrl: URL.createObjectURL(blob), mimeType, fileName };
};

export const startQuickBooksInvoicesSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/invoices/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};


// ── Timelogs / Time Activities ───────────────────────────────

export type TimelogCdcRow = {
  timelogId: number;
  resourceScheduleId?: number | null;
  taskId?: number | null;
  taskName?: string | null;
  projectId?: number | null;
  projectName?: string | null;
  employeeId?: number | null;
  employeeName?: string | null;
  roleId?: number | null;
  roleName?: string | null;
  startTime?: string | null;
  endTime?: string | null;
  durationSeconds?: number | null;
  durationMinutes?: number | null;
  timelogStatus?: string | null;
  mappingId?: number | null;
  externalEntityId?: string | null;
  externalEntityType?: string | null;
  externalAccountId?: string | null;
  syncStatus?: string | null;
  syncToken?: string | null;
  mappingVersion?: number | null;
  mappingUpdatedAt?: string | null;
  reason?: string | null;
  readyToSync?: boolean;
  employeeLinked?: boolean;
  projectLinked?: boolean;
  serviceLinked?: boolean;
  workerEntityType?: string | null;
  workerExternalEntityType?: string | null;
  workerType?: string | null;
  workerTypeLabel?: string | null;
  employeeType?: string | null;
  qboEmployeeId?: string | null;
  qboProjectId?: string | null;
  qboServiceId?: string | null;
  source?: "FIELDCRAFT" | null;
};

export type TimelogCdcSummary = {
  total: number;
  synced: number;
  pending: number;
  failed: number;
  notReady: number;
};

export type TimelogCdcResponse = {
  ok?: boolean;
  summary: TimelogCdcSummary;
  pagination: { page: number; pageSize: number; totalRecords: number; totalPages: number };
  rows: TimelogCdcRow[];
};

export const getQuickBooksTimelogCdcView = async (
  accessToken: any,
  params: {
    tenantId: string;
    serviceId: string;
    page?: number;
    pageSize?: number;
    search?: string;
    syncStatus?: string;
  }
) => {
  const query = new URLSearchParams({
    tenantId: params.tenantId,
    serviceId: params.serviceId,
    page: String(params.page ?? 1),
    pageSize: String(params.pageSize ?? 25),
  });
  if (params.search) query.set("search", params.search);
  if (params.syncStatus && params.syncStatus !== "ALL") query.set("syncStatus", params.syncStatus);
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/timelogs/cdc-view?${query.toString()}`;
  return await qbGet(url, accessToken);
};

export const startQuickBooksTimelogsSync = async (
  accessToken: any,
  req: CustomersActionRequest
) => {
  const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/timelogs/sync`;
  const payload = await withAudit(accessToken, req);
  return await qbPost(url, payload, accessToken);
};

// export const deleteMappedQuickBooksTimeActivities = async (
//   accessToken: any,
//   req: {
//     tenant_id: string;
//     service_id: string;
//     confirm_delete: boolean;
//     dry_run?: boolean;
//     object_id?: string | null;
//   }
// ) => {
//   const url = `${ConfigUrl.extservice_base_url}/extsvc/api/v1/quickbooks/timelogs/delete-all`;
//   const normalizedRequest = {
//     ...req,
//     object_id: req.object_id ?? undefined,
//     dry_run: req.dry_run ?? false,
//   };
//   const payload = await withAudit(accessToken, normalizedRequest);
//   return await qbPost(url, payload, accessToken);
// };

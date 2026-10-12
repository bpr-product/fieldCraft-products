/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 6-Nov-2023
 * @author      : Asish
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Base class for REST Controller used for preparing the results and validating the parameters etc
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR   |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *     2265       | 17-06-2024 | Shiyam Kumar | Create a Service For project Reschedule and StateChangeHistory
 *
 *  2235           |  14/06/24   |   Vanitha    | sold this year bar chart implementation
 *  2235           |  21/06/24   |   Vanitha    | Project Total Esitmate for Month  and completed project count display implementation
 *  2235           |  25/06/24   |   Vanitha    | Closing Ration display service
 *
 */

import {
  executeGetData,
  executePostData,
  executePutData,
  executePostImage,
} from "./ApiBase";
// Api.ts helpers send NO Authorization header — used for the PUBLIC customer
// estimate-preview endpoints (mirrors InvoiceServices' getInvoiceDetailsByInvoiceId).
import { executeGet, executePost } from "./Api";
import { UUID } from "crypto";
import { ConfigUrl } from "../auth/OidcConfig";
import type { GetActiveProjectsParams } from "../components/projectScheduling/types";

export const getProjectBycustomerUId = async (
  customerUId: UUID,
  page: any,
  size: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/customer/${customerUId}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};
export const getProjectCountBycustomerUId = async (
  customerUId: UUID,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/customer/${customerUId}/count`;
  return await executeGetData(url, accessToken);
};

export const getProjectById = async (accessToken: any, projectId: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const getProjectByUid = async (accessToken: any, projectUid: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getProjectByUid/${projectUid}`;
  return await executeGetData(url, accessToken);
};

export const getProjectEstimateByYear = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getProjectEstimateByYear`;
  return await executeGetData(url, accessToken);
};

export const getMostLeadTrade = async (
  accessToken: string,
  startDate: any,
  endDate: any,
  status: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeStatistics/tradeSalesStatistics/from/${startDate}/to/${endDate}/status/${status}`;
  return await executeGetData(url, accessToken);
};

export const getEmployeeTerget = async (
  resourceUid: any,
  status: any,
  range: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeStatistics/employeeTarget/resourceUid/${resourceUid}/status/${status}/range/${range}`;
  return await executeGetData(url, accessToken);
};

export const getClosingRatio = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/closingRatio`;
  return await executeGetData(url, accessToken);
};

export const getCompletedProjectCount = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeStatistics/count`;
  return await executeGetData(url, accessToken);
};

export const getProjectEstimateByMonth = async (
  accessToken: any,
  status: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeStatistics/projectEstimateByMonth/status/${status}`;
  return await executeGetData(url, accessToken);
};

export const getAllProjectByStatus = async (
  status: any,
  page: any,
  size: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/status/${status}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};

export const getProjectCountByStatus = async (
  status: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getMyProjectInfo/status/${status}/count`;
  return await executeGetData(url, accessToken);
};

export const getdisabledDatesByStartDateAndResourceType = async (
  startDate: any,
  resourceType: string,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/resourceSchedules/blockedDates/startDate/${startDate}/type/${resourceType}`;
  return await executeGetData(url, accessToken);
};

export const getBookedLeaves = async (ResourceId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/resourceSchedules/getBookedLeaves/resourceId/${ResourceId}`;
  return await executeGetData(url, accessToken);
};

export const addResourceSchedule = async (
  scheduleData: any,
  date: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/resourceSchedules/addResourceSchedule/date/${date}`;
  return await executePostData(url, scheduleData, accessToken);
};

export const bookSalesPerson = async (
  bidData: any,
  accessToken: any,
  islatest: boolean = true,
) => {
  const version = islatest ? "v3" : "v2";
  const url = `${ConfigUrl.apiUrl}/${version}/bids/createSchedule`;
  return await executePostData(url, bidData, accessToken);
};

export const projectReschedule = async (
  projectId: any,
  accessToken: any,
  islatest: boolean = true,
) => {
  const version = islatest ? "v3" : "v2";
  const url = `${ConfigUrl.apiUrl}/${version}/projects/reschedule/projectId/${projectId}`;
  return await executePostData(url, null, accessToken);
};

export const reschedule = async (
  projectId: any,
  scheduleType: any,
  accessToken: any,
  islatest: boolean = true,
) => {
  const version = islatest ? "v3" : "v2";
  const url = `${ConfigUrl.apiUrl}/${version}/projects/reschedule/projectId/${projectId}/scheduleType/${scheduleType}`;
  return await executePostData(url, null, accessToken);
};

export const projectReschedulewithDate = async (
  projectId: any,
  startDate: any,
  scheduleType: any,
  accessToken: any,
  // null → Java parses the literal "null" path segment to no-version and
  // resolves the current task version from the DB itself.
  taskVersion: number | null,
  islatest: boolean = true,
) => {
  const version = islatest ? "v3" : "v2";
  const url = `${ConfigUrl.apiUrl}/${version}/projects/reschedule/projectId/${projectId}/startDate/${startDate}/scheduleType/${scheduleType}/version/${taskVersion}`;
  return await executePostData(url, null, accessToken);
};

export const getProjectInformation = async (
  projectId: any,
  name: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getProjectInformation/projectId/${projectId}/name/${name}`;
  return await executeGetData(url, accessToken);
};

export const getSearch = async (
  searchParam: any,
  sortField: any,
  sortOrder: any,
  accessToken: any,
  page: any,
  size: any,
) => {
  const Value = searchParam?.trim() || "null"; // or 'null' or 'none' if your backend expects that
  const url = `${ConfigUrl.apiUrl}/v2/projects/search/searchParam/${Value}/sortField/${sortField}/sortOrder/${sortOrder}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};

export const getSearchBySales = async (
  customerUid: any,
  status: any,
  customer: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/customer/${customerUid}/Bysales/${status}/search/customer/${customer}`;
  return await executeGetData(url, accessToken);
};
// export const getSearchByTrade = async (trade: any,accessToken: any) => {
//   const url = `${ConfigUrl.apiUrl}/v2/projects/search/customer/${trade}`;
//   return await executeGetData(url, accessToken);
// };
export const getSortByAdmin = async (
  customerUid: any,
  status: any,
  role: any,
  sortField: any,
  sortOrder: any,
  customer: any,
  page: any,
  size: any,
  accessToken: any,
) => {
  const customerValue = customer?.trim() || "null"; // or 'null' or 'none' if your backend expects that

  const url = `${ConfigUrl.apiUrl}/v2/projects/customer/${customerUid}/byStatus/${status}/roles/${role}/search/customer/${customerValue}/sortField/${sortField}/sortOrder/${sortOrder}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};

export const getSortByCustomerAndStatus = async (
  customerUid: any,
  customer: any,
  sortField: any,
  sortOrder: any,
  page: any,
  size: any,
  accessToken: any,
) => {
  const customerValue = customer?.trim() || "all"; // or 'null' or 'none' if your backend expects that

  const url = `${ConfigUrl.apiUrl}/v2/projects/customer/${customerUid}/search/status/customer/${customerValue}/sortField/${sortField}/sortOrder/${sortOrder}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};

export const CheckInvoiceAvailability = async (
  projectId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/checkInvoiceAvailability/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const taskReschedule = async (
  projectId: any,
  taskOrder: any,
  taskUid: any,
  startTime: any,
  endTime: any,
  lastUpdatedAt: any,
  accessToken: any,
  version: any,
  islatest: boolean = true,
 
) => {
  let url: any;
  const version1 = islatest ? "v3" : "v2";
  if (endTime != null) {
    url = `${ConfigUrl.apiUrl}/${version1}/projects/reschedule/projectId/${projectId}/taskOrder/${taskOrder}/taskUid/${taskUid}/startTime/${startTime}/endTime/${endTime}/${lastUpdatedAt}/version/${version}`;
  } else {
url = `${ConfigUrl.apiUrl}/${version1}/projects/reschedule/projectId/${projectId}/taskOrder/${taskOrder}/taskUid/${taskUid}/startTime/${startTime}/lastUpdatedAt/${lastUpdatedAt}/version/${version}`;
  }
  return await executePostData(url, null, accessToken);
};
/**
 * Timelog 2.0 Epic 6 (TL-6.3, reworked 27-Jul-2026): a phase-bar drag has no dedicated
 * reschedule endpoint of its own anymore (PhaseRescheduleApi was removed) -- it reschedules the
 * phase's header task directly through the SAME live, ASE-backed path (taskReschedule above) a
 * single-task Gantt/GoPAL drag already uses. Since the calendar only knows "phase name, project
 * id" (not a taskUid/taskOrder/version), this resolves the phase-header task's identifiers first
 * (GET /v2/tasks/getPhaseHeaderTask/...) so the caller can then call taskReschedule with them.
 * The backend still validates the fixed project-lifecycle phase order (Sales -> Pre-Construction
 * -> Active Construction -> Post-Construction) before ASE is called, and still shifts every
 * other task in the phase after ASE moves the header -- see PhaseRescheduleService.
 */
export const getPhaseHeaderTask = async (
  projectId: any,
  phase: string,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/tasks/getPhaseHeaderTask/projectId/${projectId}/phase/${encodeURIComponent(phase)}`;
  return await executeGetData(url, accessToken);
};

/**
 * Timelog 2.0 (Epic 6 follow-up): every phase-header schedule window (scheduled/active/
 * completed) across a set of projects -- the Timelog Calendar's Phase view previously only ever
 * rendered already-logged TimeEntry rows, so a fully-scheduled phase with zero clocked time was
 * invisible on the calendar even though it showed up on the project's Gantt chart (found via
 * live testing). Used to synthesize a phase bar even when there are no entries to group yet.
 */
export const getPhaseSchedules = async (
  projectIds: Array<string | number>,
  accessToken: any,
) => {
  if (!projectIds.length) {
    return { ok: true, status: 200, data: [] };
  }
  const url = `${ConfigUrl.apiUrl}/v2/tasks/getPhaseSchedules?projectIds=${projectIds.join(',')}`;
  return await executeGetData(url, accessToken);
};

// REMOVED 2026-09-04 (D-RS-DEAD-DOORS): leaveBook — POST /v2/resourceSchedules/addResourceSchedule/
// resourceId/{}/startDate/{}/endDate/{}. Its only consumer was BookLeave.tsx, which moved to the
// GraphQL BOOK_LEAVE mutation on 2026-01-08 in f62b68d14c ("Book leave reason field integration in
// ui") and left this wrapper behind. Booking leave now has one door: the BOOK_LEAVE mutation ->
// BookLeaveController (@PreAuthorize LEAVE_CREATE_AUTHORITIES) -> BookLeaveOrchestrator, which is
// the path that carries the leave status machine and the replacement re-plan dispatch. The REST
// twin is retired; see the tombstone in ResourceScheduleApi.java.
export const getEmployeeLeaves = async (type: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/resourceSchedules/employeeLeaves/type/${type}`;
  return await executeGetData(url, accessToken);
};

export const updateProjectByProjectId = async (
  projectId: any,
  name: any,
  reason: any,
  status: any,
  lastUpdatedAt: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/updateProjectStatus/projectId/${projectId}/name/${name}/reason/${reason}/status/${status}/${lastUpdatedAt}`;
  return await executePutData(url, null, accessToken);
};

export const getRecentStateChangeHistory = async (
  projectId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getStatusChangeHistory/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const getSalesPersonName = async (projectId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getSalesPersonName/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const getReferenceProjects = async (
  projectId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getReferenceProjects/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const getReferenceProjectsCount = async (
  trade: any,
  status: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getReferenceProjectsCount/trade/${trade}/status/${status}`;
  return await executeGetData(url, accessToken);
};

export const updateTemplatesData = async (
  projectId: any,
  payload: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/answers/updateAnswerTemplateData/projectId/${projectId}`;
  return await executePutData(url, payload, accessToken);
};

export const getProjectTotalCost = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeStatistics/projectTotalCost`;
  return await executeGetData(url, accessToken);
};

export const getSalesPersonPerformance = async (
  accessToken: any,
  from: any,
  to: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeStatistics/salesPersonPerformance/from/${from}/to/${to}`;
  return await executeGetData(url, accessToken);
};

export const getVerificationProjects = async (
  searchParams: any,
  page: any,
  size: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/MyVerificationProjects/search/${searchParams}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};
export const sortVerificationProjects = async (
  searchParams: any,
  page: any,
  size: any,
  sortField: any,
  sortOrder: any,
  accessToken: any,
) => {
  const customerValue = searchParams?.trim() || "null"; // or 'null' or 'none' if your backend expects that
  const url = `${ConfigUrl.apiUrl}/v2/projects/MyVerificationProjects/search/${customerValue}/sortField/${sortField}/sortOrder/${sortOrder}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};
export const getVerificationProjectsCount = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getVerificationProjectCount`;
  return await executeGetData(url, accessToken);
};

export const getProjectTax = async (projectId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getTax/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const updateProjectTax = async (
  projectId: any,
  payload: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/updateTax/projectId/${projectId}`;
  return await executePutData(url, payload, accessToken);
};

export const updateProjectMarginAndOverHead = async (
  projectId: any,
  payload: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/updateMarginAndOverHead/projectId/${projectId}`;
  return await executePutData(url, payload, accessToken);
};

export const getAnswerData = async (data: any, accessToken: string) => {
  const url = `${ConfigUrl.apiUrl}/v2/answers/getAnswerTemplateData?data=${encodeURIComponent(data)}`;
  return await executeGetData(url, accessToken);
};

export const getAssigneesProjectIdByStatus = async (
  assigneeUid: any,
  status: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getMyProjectId/assignee/${assigneeUid}/status/${status}`;
  return await executeGetData(url, accessToken);
};

export const completeProject = async (projectId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/completeProject/projectId/${projectId}`;
  return await executePostData(url, null, accessToken);
};

export const getDuration = async (
  starttime: any,
  endtime: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/duration/starttime/${starttime}/endtime/${endtime}`;
  return await executeGetData(url, accessToken);
};

export const getOrderNoByRole = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getProjectOrderNo`;
  return await executeGetData(url, accessToken);
};

export const searchProjects = async (searchParams: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/searchProjects/searchParams/${searchParams}`;
  return await executeGetData(url, accessToken);
};
export const getProjectEstimateBreakDown = async (
  projectId: any,
  detailedEstimate: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getEstimatePreview/projectId/${projectId}/detailedEstimate/${detailedEstimate}`;
  return await executeGetData(url, accessToken);
};

/**
 * AB#29907 — every estimate revision of a project, for the Estimate Preview version dropdown.
 *
 * Includes invalidated revisions, which is the point: the whole history is what the dropdown lists.
 * `estimateId` is the identity to send back on every version-scoped call; `label` is positional and
 * assigned by the server (first revision = 1), and the stored `version` column is deliberately not
 * returned at all — it can legally repeat once an older revision has been activated.
 */
export const getEstimateVersions = async (projectId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getEstimateVersions/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

/**
 * AB#29907 — the Estimate Preview payload for ONE revision.
 *
 * A separate function rather than an extra argument on `getProjectEstimateBreakDown`, because that
 * one is also consumed by ContractTemplate.tsx, ChangeOrderContractTemplate.tsx and
 * SubcontractContract.tsx — contract generation and subcontract POs. Leaving its URL and its
 * signature alone is what keeps this feature away from those.
 */
export const getProjectEstimateBreakDownByVersion = async (
  projectId: any,
  detailedEstimate: any,
  estimateId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getEstimatePreview/projectId/${projectId}/detailedEstimate/${detailedEstimate}/estimateId/${estimateId}`;
  return await executeGetData(url, accessToken);
};

/**
 * AB#29907 — makes an older revision the valid one (requirement 8).
 *
 * `expectedValidEstimateId` is the revision the UI believed was valid when it rendered the dropdown,
 * and it is required. The server turns it into a conditional update whose row count is the answer,
 * so if another tab activated or saved first this returns 409 and nothing is written — rather than
 * the silent half-apply concurrent estimate saves produce today. Callers must surface that 409 as
 * "reload and try again", not swallow it.
 */
export const activateEstimateVersion = async (
  projectId: any,
  estimateId: any,
  expectedValidEstimateId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/activateEstimateVersion/projectId/${projectId}/estimateId/${estimateId}?expectedValidEstimateId=${expectedValidEstimateId}`;
  return await executePostData(url, {}, accessToken);
};

/**
 * AB#29907 — save the Estimate Preview from a chosen revision.
 *
 * With `estimateId` naming the currently valid revision this is byte-identical to `editEstimate`
 * (Case A). With an older revision the server creates a PARKED revision — invalid from birth, valid
 * revision untouched — and returns its id so the UI can redirect to it.
 */
export const editEstimateVersion = async (
  projectId: any,
  estimateId: any,
  payload: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/editEstimatePreview/projectId/${projectId}/estimateId/${estimateId}`;
  return await executePostData(url, payload, accessToken);
};

export const updateProjectStatus = async (
  projectId: any,
  status: any,
  payload: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/updateStatus/projectId/${projectId}/status/${status}`;
  return await executePostImage(url, payload, accessToken);
};

export const editEstimate = async (
  projectId: any,
  payload: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/editEstimatePreview/projectId/${projectId}`;
  return await executePostData(url, payload, accessToken);
};

// ── Customer Estimate Preview link (mirrors the invoice payment link) ─────────

/**
 * Authenticated: generate/regenerate the public customer estimate-preview link.
 * Returns the full URL (with the encrypted token in its query string) in res.data.
 * payload: { usage: 'single' | 'multi', expiry: <days> }
 */
export const generateEstimateLink = async (
  projectId: any,
  estimateNo: any,
  payload: { usage: "single" | "multi"; expiry: number },
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/estimate-preview/generate/projectId/${projectId}/estimateNo/${estimateNo}`;
  return await executePostData(url, payload, accessToken);
};

/**
 * Authenticated: list the existing preview links for a project (one single-use + one
 * multi-use, whichever exist) so the "Share & Manage Links" modal loads real DB state.
 * Returns res.data = {
 *   links: Array<{ usage, url, status: 'Valid'|'Expired'|'Used', expiry }>,
 *   smsTemplateMasterEnabled: boolean,  // mirrors fieldcraft.sms.template-master-send.enabled (SMS split PR-5)
 * }.
 */
export const getEstimateLinks = async (projectId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/estimate-preview/links/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

/**
 * PUBLIC (no auth): fetch the redacted customer estimate view for a link token.
 * `token` is the raw query string from the preview URL (location.split('?')[1]),
 * same convention as getInvoiceDetailsByInvoiceId.
 */
export const getEstimatePreviewByToken = async (token: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/estimate-preview/data/${token}`;
  return await executeGet(url);
};

/**
 * PUBLIC (no auth): record the customer's decision on the estimate link.
 * decision: 'approved' | 'reval'. Returns the resulting link status.
 */
export const submitEstimateDecision = async (
  token: any,
  decision: "approved" | "reval",
  comments: Array<{ group: string; id: number; comment: string }> = [],
) => {
  const url = `${ConfigUrl.apiUrl}/v2/estimate-preview/action/${token}`;
  return await executePost(url, { decision, comments });
};

export const checkProjectEstimateAvaialbility = async (
  projectId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/checkProjectEstimateAvailability/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

// Sends the project's estimate to the customer via SMS using an application-managed
// SMS template (requirement 24114). Backend renders/sends via the existing reused
// provider-api /api/v1/sms/send endpoint — this call never talks to Twilio directly.
//
// templateId carries EITHER a legacy sms_template numeric id OR a message_template_master
// templateMasterUid (SMS split PR-5) — pass whichever EstimateSmsTemplateOption.id the picker
// selected; tenant-api's ProjectApi.TemplateRef disambiguates by string format (a UUID always
// contains hyphens, a legacy id never does), so this call site never needs to know which catalog
// it came from.
export const sendEstimateSms = async (
  projectId: any,
  templateId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/sendEstimateSms/projectId/${projectId}/templateId/${templateId}`;
  return await executePostData(url, null, accessToken);
};

// Returns the estimate SMS exactly as it will be sent for this project + template, WITHOUT
// sending it (Phase 2, requirement 24114) — backend renders via the same SmsTemplateRenderer
// and the same placeholder value map sendEstimateSms uses; no rendering here on the frontend.
// templateId — see sendEstimateSms's comment above; the same dual-catalog identifier applies here.
export const previewEstimateSms = async (
  projectId: any,
  templateId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/previewEstimateSms/projectId/${projectId}/templateId/${templateId}`;
  return await executeGetData(url, accessToken);
};

// Sends the project's estimate to the customer via EMAIL — the "Send by Email" counterpart of
// sendEstimateSms above. Unlike SMS there is no legacy catalog: templateId is always a
// message_template_master templateMasterUid (channel = Email), and the backend sends via the
// same TenantEmailService/EmailRecipientRouter path SendEmailActionHandler already uses for
// GoPAL task actions — this call never talks to a mail transport directly.
export const sendEstimateEmail = async (
  projectId: any,
  templateId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/sendEstimateEmail/projectId/${projectId}/templateId/${templateId}`;
  return await executePostData(url, null, accessToken);
};

// Returns the estimate email (subject + rendered body) exactly as it will be sent for this
// project + template, WITHOUT sending it — the email counterpart of previewEstimateSms above.
export const previewEstimateEmail = async (
  projectId: any,
  templateId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/previewEstimateEmail/projectId/${projectId}/templateId/${templateId}`;
  return await executeGetData(url, accessToken);
};

// AB#26894 — renders a GoPAL task action's configured template (channel: 'Send SMS' | 'Send eMail')
// exactly as it will look for this project's real customer, WITHOUT sending anything — same
// pure-render contract as previewEstimateSms above, one level more generic (any channel, any
// task-action template, not just the estimate-SMS template). Returns { subject, renderedBody }.
export const previewTaskAction = async (
  projectId: any,
  templateId: any,
  channel: string,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/previewTaskAction/projectId/${projectId}/templateId/${templateId}?channel=${encodeURIComponent(channel)}`;
  return await executeGetData(url, accessToken);
};

// AB#21877 — estimate availability + freshness. Returns { hasEstimate, estimateStale }.
// estimateStale is true only in estimationApproval when the task list changed after
// the estimate was generated, so the Bid task view hides "View Estimate" and forces
// a fresh "Create Estimate".
export const getEstimateStatus = async (projectId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/estimateStatus/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};
export const getProjectEstimateList = async (
  projectId: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getEstimateList/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const getProjects = async (page: any, size: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getProjects?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};

export const getProjectCount = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getProjectCount`;
  return await executeGetData(url, accessToken);
};

export const getAffectedProjectDetails = async (
  projectId: any,
  jobStartDate: any,
  jobEndDate: any,
  resourceUids,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getAffectedProjects/projectId/${projectId}/jobStartDate/${jobStartDate}/jobEndDate/${jobEndDate}/resourceUids/${resourceUids}`;
  return await executeGetData(url, accessToken);
};

export const getProjectEstimateDifference = async (
  projectId: any,
  detailedEstimate: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getEstimateDifference/subProjectId/${projectId}/detailedEstimate/${detailedEstimate}`;
  return await executeGetData(url, accessToken);
};

export const hasSubProjects = async (projectId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/hasSubProjects/projectId/${projectId}`;
  return await executeGetData(url, accessToken);
};

export const rejectProjectEstimate = async (
  projectId: any,
  status: any,
  reason: any,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/rejectProject/projectId/${projectId}/status/${status}/reason/${reason}`;
  return await executePutData(url, null, accessToken);
};

// Project Notes (#22645) — id-based helpers. The project LIST rows carry the
// internal numeric project id (not the external UUID), so notes are read/added
// against the /projects/id/{projectId}/notes endpoints. Synchronous tenant-API
// calls (NOT the async solve queue): each add appends to the project's
// append-only notes JSON array and returns the full updated array. No reschedule.
export const addProjectNoteById = async (
  projectId: number | string,
  text: string,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/id/${projectId}/notes`;
  return await executePostData(url, { text }, accessToken);
};

export const getProjectNotesById = async (
  projectId: number | string,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/id/${projectId}/notes`;
  return await executeGetData(url, accessToken);
};

export const getActiveProjects = async (
  params: GetActiveProjectsParams,
  accessToken: any,
) => {
  const query = new URLSearchParams();
  if (params.cursorId != null) query.set("cursorId", String(params.cursorId));
  if (params.backward) query.set("backward", "true");
  if (params.size != null) query.set("size", String(params.size));
  const qs = query.toString();
  const url = `${ConfigUrl.apiUrl}/v4/projects/active${qs ? `?${qs}` : ""}`;
  return await executeGetData(url, accessToken);
};

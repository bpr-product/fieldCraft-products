/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 3-Nov-2023
* @author      : Shiyamkumar.G
* @since       : 0.0.1
* Requirement# :
* Purpose      : Bid Service
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |    DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*       1385     | 01-04-2024 | Shiyam Kumar  | Calculate Every Month Average For PriceTrend
* 2235           |  10/06/24   |   Vanitha    | Most Lead and Bid Total Esitmate for month display implementation
* 2235           |  10/06/24   |   Vanitha    | Most Lead and Bid Total Esitmate for year  display implementation
* 2235           |  21/06/24   |   Vanitha    | Bid Total Esitmate for Month  display implementation
* 2235           |  25/06/24   |   Vanitha    | Bid Total count display implementation
* 21877          |  09/07/26   | Mohamed Riyaz A | AseSolvePayload: add project_id (SAVE_TASKS identifier) + estimate_document_id (APPROVE_ESTIMATE PDF link); document etag = project.etag (DB-trigger UUID), not project.version.
* 24363          | 10-07-2026 | Mohamed Riyaz A | Submit APPROVE_ESTIMATE asynchronously and handle the 202 response.
* 58661          | 18-07-2026 | Codex           | unwrapAseResponse now checks res.ok before unwrapping — a failed HTTP call (4xx/5xx/network) was being silently reinterpreted as an AseJobResponse instead of surfacing as an error.


*
*/

import { executeGetData, executePostData, executePutData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";


export const getUsers = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/customers/getCustomers`;
  return await executeGetData(url, accessToken);
}
export const saveCustomer = async (customer: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/customers/addcustomer`;
  return await executePostData(url, customer, accessToken);
};

export const getTrades = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/trades/getalltrades`;
  return await executeGetData(url, accessToken)
}

export const getMaterials = async (accessToken: any, trade: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/trades/material/${trade}`;
  return await executeGetData(url, accessToken)
}

export const getAssets = async (accessToken: any, customerId: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/assets/getAssetByCustomerUid/customerUid/${customerId}`;
  return await executeGetData(url, accessToken)
}

export const getAssetsByAssetUid = async (accessToken: any, assetUid: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/assets/getAssetByAssetUid/assetUid/${assetUid}`;
  return await executeGetData(url, accessToken)
}

export const getBidTotalEstimate = async (accessToken: any,startDate:any,endDate:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/getBidEstimate/from/${startDate}/to/${endDate}`;
  return await executeGetData(url, accessToken)
}

export const getEstimateByYear = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/getBidEstimateByYear`;
  return await executeGetData(url, accessToken)
}

export const getBidCount = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/count`;
  return await executeGetData(url, accessToken)
}

export const getEstimateByMonth = async (accessToken: any,status:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/getBidEstimateByMonth/status/${status}`;
  return await executeGetData(url, accessToken)
}

export const revalidate = async (projectId: any, accessToken: any, jobStartDate: any = null) => {
  const url = jobStartDate != null
    ? `${ConfigUrl.apiUrl}/v3/bids/revalidateProject/projectId/${projectId}/jobStartDate/${jobStartDate}`
    : `${ConfigUrl.apiUrl}/v3/bids/revalidateProject/projectId/${projectId}`;
  return await executePostData(url, null, accessToken)
}

export const addToCart = async (bidId:any, bidNo:any, status:any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/bids/addToCart/bidId/${bidId}/bidNo/${bidNo}/status/${status}`;
    return await executePutData(url, null, accessToken)
}


export const deleteBidById = async (projectId: any, status:any, accessToken: any) => {
  // v4: ASE 2.0 slim-schema delete. Routes to BidApiV4 -> ProjectServiceV4, which
  // soft-deletes tasks via the task_attribute EAV store (task.invalid/status were
  // dropped by the V45 migration, so the v2 endpoint 500s on this schema).
  const url = `${ConfigUrl.apiUrl}/v4/bids/deleteProject/projectId/${projectId}/status/${status}`;
  return await executePutData(url, null, accessToken);
}
export const getPriceChartData = async (item: any, accessToken: any) => {
  const searchCriteriaList:any = [];
  Object.keys(item).map(key => {
    return searchCriteriaList.push({
      "key": key,
      "value": item[key],
      "operation": "eq",
      "logicalOp": "and"
    });
  });
  const url = `${ConfigUrl.apiUrl}/v2/analyticalDatas/search`;
  const payload ={"searchCriteriaList": searchCriteriaList}
  return await executePostData(url, payload, accessToken);
}

export const createBid = async (bidData: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/createBid`;
  return await executePostData(url, bidData, accessToken)
}

export const autoSaveBid = async (bidData: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/autoSaveBid`;
  return await executePostData(url, bidData, accessToken);
}

/**
 * Record the chosen Good-Better-Best tier on the project row (project.additional_info).
 *
 * This is the ONLY durable record of the user's choice. The grade otherwise lives in
 * sessionStorage and a Mongo audit log; Postgres holds nothing, and the grade can only be
 * inferred by joining bom.material_uid -> material.grade — which breaks the moment a BOM
 * line is edited.
 *
 * It is also what ASE reads back: at BID, once the sales tasks (and with them the
 * "Create BOM Task") exist, the engine resolves this entry into stock-sourced BOM rows.
 * Without it the engine finds nothing and logs gbb_stock_bom.no_selection.
 *
 * The server MERGES a single {"type":"gbbSelection"} entry into the additional_info JSON
 * array — it is not a raw setter, so the tradeQuestionTemplateVersion / changeOrderRejectReason
 * entries that share that column are preserved.
 *
 * @param payload { projectId, grade: 'a'|'b'|'c', tier?, materials? }
 */
export const persistGbbSelection = async (payload: {
  projectId: number | string;
  grade: string;
  tier?: string;
  materials?: any[];
}, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v3/bids/gbbSelection`;
  return await executePostData(url, payload, accessToken);
}

/**
 * Re-resolve and re-stamp gbbSelection SERVER-SIDE from the project's current BPE snapshot —
 * the rep-facing counterpart to what Create Bid's wizard/lead automation already does before
 * its own BID solve (EstimateAutomationService.recomputeGbbSelectionForProject /
 * ServiceCombinationCatalog#resolve — the same combination-resolution policy, not a second,
 * independently-drifting one). Root-caused 2026-09-10: Bid Details previously reconstructed
 * "which combination fits the current answers" in the browser from whatever BPE catalogue
 * snapshot happened to already be loaded, which was frequently stale (fetched once on page
 * load, never refreshed after a Q&A edit). Calling this instead means every Update reads a
 * FRESH snapshot fetched at call time — no client-side staleness possible.
 *
 * Advisory only, like the persist calls above: `{stamped:false}` is a normal, non-error
 * outcome (no usable BPE snapshot yet, or nothing sellable in it), not a failure to surface
 * to the rep.
 */
export const recomputeGbbSelection = async (
  projectId: number | string,
  tenantId: string,
  accessToken: any,
): Promise<{ ok: boolean; stamped: boolean | null; message: string | null }> => {
  if (!projectId || !tenantId) {
    return { ok: false, stamped: null, message: 'Missing projectId or tenantId.' };
  }
  try {
    const url = `${ConfigUrl.apiUrl}/v3/bids/gbbSelection/projectId/${projectId}/recompute?tenantId=${encodeURIComponent(tenantId)}`;
    const result: any = await executePostData(url, {}, accessToken);
    if (!result?.ok) {
      return { ok: false, stamped: null, message: result?.message || 'Could not recompute the GBB selection.' };
    }
    const body = result?.data ?? result;
    return { ok: true, stamped: Boolean(body?.stamped), message: null };
  } catch (err: any) {
    return { ok: false, stamped: null, message: err?.message || 'Could not recompute the GBB selection.' };
  }
};

/**
 * Read back the previously-recorded GBB tier for a project (see `persistGbbSelection`
 * above for why `project.additional_info` — not the BPE Mongo snapshot — is the system
 * of record). Used to redisplay a prior grade choice on page load (e.g. Bid Details).
 *
 * Returns `null` when the project has no gbbSelection entry yet (a common, expected
 * state — not an error) or when the request fails; never throws.
 */
export const getGbbSelection = async (
  projectId: number | string,
  accessToken: any,
): Promise<{ type: string; grade: string; tier?: string; materials?: any[] } | null> => {
  const url = `${ConfigUrl.apiUrl}/v3/bids/gbbSelection/projectId/${projectId}`;
  const res = await executeGetData(url, accessToken);
  const body = res?.ok === false ? null : (res?.data ?? res);
  if (!body || typeof body !== 'object' || !('grade' in body)) {
    return null;
  }
  return body;
};

export const getCartCount = async (accessToken: any, status:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/getMyBidCount/status/${status}`;
  return await executeGetData(url, accessToken)
}

export const getCartList = async (accesstoken: any, status:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/getBidsByCustomerUidAndStatus/status/${status}`;
  return await executeGetData(url, accesstoken);
};

export const getTradeDetails = async ( tradeName,accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/trades/tradeName/${tradeName}`;
  const response= await executeGetData(url,accessToken);
  return response;
}

export const getBidStatus = async (dateRange:any, status:any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/bidCountStatistics/dateRange/${dateRange}/status/${status}`;
  return await executeGetData(url, accessToken)
}

export const createEstimate = async (projectId:any, jobStartDate:any, accessToken: any, islatest:boolean = true) => {
  let url:any;
  const version = islatest ? "v3" : "v2";
  if(jobStartDate != null){
    url = `${ConfigUrl.apiUrl}/${version}/bids/createEstimate/projectId/${projectId}/jobStartDate/${jobStartDate}`;
  }else{
    url = `${ConfigUrl.apiUrl}/${version}/bids/createEstimate/projectId/${projectId}`;
  }
  return await executePostData(url, null, accessToken)
}

export const getValidTradeDetails = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/trades/getValidTradeDetails`;
  return await executeGetData(url, accessToken)
}

export const getValidTradeData = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/trades/getValidTradeData`;
  return await executeGetData(url, accessToken)
}

export const createChangeOrderEstimates = async (data:any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/createChangeOrderEstimate`;
  return await executePostData(url, data, accessToken)
}

export const createMultiEstimate = async (projectId: any, jobStartDate: string | null, accessToken: any) => {
  let url: string;
  if (jobStartDate) {
    url = `${ConfigUrl.apiUrl}/v3/bids/createMultiEstimate/projectId/${projectId}?jobStartDate=${encodeURIComponent(jobStartDate)}`;
  } else {
    url = `${ConfigUrl.apiUrl}/v3/bids/createMultiEstimate/projectId/${projectId}`;
  }
  return await executePostData(url, null, accessToken);
}

export const saveEstimate = async (estimateData: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v3/bids/saveMultiEstimate`;
  return await executePostData(url, JSON.stringify(estimateData), accessToken);
}

export const resolveGradeVariants = async (projectId: any, materials: { name: string; material_uid?: string; quantity: number; unit: string }[], accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v3/bids/resolveGradeVariants`;
  return await executePostData(url, { projectId, materials }, accessToken);
}

export const insertBomFromGrade = async (projectId: any, selectedGrade: string, materials: any[], accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v3/bids/insertBomFromGrade`;
  return await executePostData(url, { projectId, selectedGrade, materials }, accessToken);
}

/** Stock-based BOM insertion — resolves materials by name+grade from stock_item,
 *  inserts BOM with source_type=27 (Stock) and creates stock_allocation rows. */
export const insertBomFromStock = async (
  projectId: number | string,
  selectedGrade: string,
  materials: Array<{ name: string; grade: string; quantity: number; unit: string; unit_cost: number }>,
  accessToken: any,
) => {
  const url = `${ConfigUrl.apiUrl}/v3/bids/insertBomFromStock`;
  return await executePostData(url, { projectId, selectedGrade, materials }, accessToken);
}

// ============================================================================
// ASE 2.0 — Next-Gen Scheduling Engine
// ============================================================================

export type AseSchedulingType =
  | 'BID' | 'INITIAL_PROJECT_SCHEDULE' | 'ESTIMATION' | 'APPROVE_ESTIMATE'
  | 'RESCHEDULE' | 'RESUME' | 'CREATE_TASK' | 'UPDATE_TASK' | 'DELETE_TASK'
  | 'AUTO_RESCHEDULE' | 'SAVE_TASKS';

export interface AseJobResponse {
  job_id: string;
  status: string;
  result?: any;
  diagnosis?: any;
  error?: string;
  result_snapshot?: {
    status?: string;
    makespan_hours?: number | null;
    tasks_scheduled?: number;
    solver_time_seconds?: number | null;
    assigned_to_name?: string | null;
    assigned_to_uid?: string | null;
  } | null;
  error_message?: string | null;
}

export interface AseSolvePayload {
  project_uid: string;
  /** Numeric project PK — REQUIRED for SAVE_TASKS (its identifier); unused otherwise. */
  project_id?: number;
  scheduling_type: AseSchedulingType;
  /** Sales/schedule origin — the sales phase is anchored here (the sales discussion date). */
  start_date?: string;
  /** Crew-on-site date — the hard floor for activeConstruction tasks. */
  job_start_date?: string;
  /**
   * B4 durable Project Start lock. true = after this solve persists, the entry
   * (first sales) task is stamped with the durable start lock at its solved
   * position, so every future solve honors it without the UI resending it;
   * false = clear the lock; omitted = no change.
   */
  project_start_locked?: boolean;
  /** B4 durable Job Start lock — same contract, applied to the first activeConstruction task. */
  job_start_locked?: boolean;
  reference_project_uid?: string;
  /**
   * project.etag (the DB-trigger UUID regenerated on every project UPDATE), fetched fresh
   * at click — NOT project.version. REQUIRED for the sync paths (SAVE_TASKS / ESTIMATION /
   * MUTATE_TASK direct save) and the async repair types. Stale/missing → 412 ETAG_MISMATCH.
   */
  etag?: string;
  task_uid?: string;
  user_oid?: string;
  /**
   * Stored estimate PDF document id — optional on APPROVE_ESTIMATE; the ASE worker links
   * it to project_estimate.document_id on the successful status flip (see PT-062).
   */
  estimate_document_id?: number;
  /**
   * SAVE_TASKS only: caller-supplied non-sales task set (raw trade_template shape),
   * answer-reconciled (deletions/updates applied). Always sent when non-empty,
   * regardless of whether a reference project is selected — the backend uses it
   * to replace the default trade_template when there is no reference project, or
   * as an overlay (delete/update by metaKey) on the cloned reference-project
   * tasks when one is selected. The engine falls back to the default/reference
   * template on any error building from it.
   */
  template_override?: any[];
}

/**
 * Unwraps ApiBase's `{ ok, status, data, message, errorCode }` envelope into the
 * ASE job payload. `executePostData`/`executeGetData` never throw — a failed HTTP
 * call (4xx/5xx/network) resolves with `ok: false` instead. Checking `res.ok` here
 * (rather than falling through to `res.data`/`res` on failure) prevents a failure
 * envelope from being silently reinterpreted as a job object — one whose error body
 * happens to contain any `data`/`status`-shaped key could otherwise read back as a
 * false "success". Throws an Error carrying `.response = { status, data }` (mirroring
 * the shape axios/ApiBase error handling elsewhere in this codebase expects) so
 * existing callers' status/errorCode-based error handling keeps working.
 */
const unwrapAseResponse = (res: any): AseJobResponse => {
  if (res && res.ok === false) {
    const err: any = new Error(
      res.message || res.data?.message || res.data?.error || "ASE request failed"
    );
    err.response = { status: res.status, data: res.data };
    err.status = res.status;
    err.errorCode = res.errorCode;
    throw err;
  }
  const apiResponseBody = res?.data ?? res;
  const inner = apiResponseBody && typeof apiResponseBody === 'object' && 'data' in apiResponseBody
    ? apiResponseBody.data
    : apiResponseBody;
  return (inner ?? apiResponseBody ?? res) as AseJobResponse;
};

export const submitBidToASE = async (payload: AseSolvePayload, accessToken: any): Promise<AseJobResponse> => {
  const url = `${ConfigUrl.next_gen_ase_url}/v1/solve`;
  // Date anchors (start_date / job_start_date) are sent as the caller supplied them —
  // a NAIVE job-site-local wall-clock ("2026-07-31T10:00:00", no Z). The frontend does
  // NOT convert to UTC here: the job's timezone is derived server-side from the project
  // zipcode, so ASE (context_builder) interprets these digits in the project zone.
  // Converting in the creator's browser zone is wrong for a cross-timezone job.
  const res = await executePostData(url, payload, accessToken);
  return unwrapAseResponse(res);
};

export const submitScheduleToASE = submitBidToASE;

export const getASEJob = async (jobId: string, accessToken: any): Promise<AseJobResponse> => {
  const url = `${ConfigUrl.next_gen_ase_url}/v1/jobs/${jobId}`;
  const res = await executeGetData(url, accessToken);
  return unwrapAseResponse(res);
};
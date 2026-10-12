/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : May-2026
 * @author      : vaisnave.im
 * @since       : 2.0.0
 * Requirement# : BID-2.0 / BPE-15712
 * Purpose      : Ballpark Estimate API service — calls POST /api/bpe/estimate
 */

import { PyexecutePostData } from './ApiBase';
import { ConfigUrl } from '../auth/OidcConfig';
import { fieldcraftAuthHeaders, invalidateFieldCraftCredential } from './fieldcraftTokenClient';

// ── Request / Response types ──────────────────────────────────────────────────

export interface QAResponse {
  question_id: string;
  answer_value?: string | number | string[] | null;
}

export interface BPERequest {
  project_id:   string;
  sq_ft:        number;
  stories:      number;
  tenant_id:    string;
  customer_uid?: string;   // document identity — who owns this bid
  asset_uid?:    string;   // document identity — which property/asset
  trade_name?:   string;   // document identity — trade OID or name
  services?:       string[]; // active FIELDCRAFT services (e.g. ["RoofMaxx", "TuneUp"])
  qa_responses:    QAResponse[];
  job_start_date?: string;   // YYYY-MM-DD; enables temporal material pricing
}

export interface SubBreakdownItem {
  name:       string;
  amount:     number;
  percentage: number;  // share of parent category (0–1)
  quantity:   number;  // computed from sq_ft
  unit:       string;  // "tote" | "gal" | "pack" | "box" | "unit" | "hrs"
  notes:      string;
}

export interface BreakdownItem {
  amount:     number;
  percentage: number;
  items:      SubBreakdownItem[];  // populated for materials & labour; [] for others
}

export interface CostBreakdown {
  materials:       BreakdownItem;
  tools_equipment: BreakdownItem;
  labour:          BreakdownItem;
  overhead:        BreakdownItem;
  margin:          BreakdownItem;
}

/** One material line item within a service */
export interface ServiceMaterialItem {
  name:          string;
  material_uid?: string;   // UUID from BPE catalog — shared across all grades of the same material group
  qty:           number;
  unit:          string;
  unit_cost:     number;
  subtotal:      number;
  notes:         string;
}

/** Per-service cost allocation from log-linear proportional split */
export interface ServiceBreakdownEntry {
  display_name: string;
  cost:         number;
  percentage:   number;
  subtotal:     number;   // cost minus margin
  margin:       number;
  margin_pct:   number;
  overhead:     number;
  overhead_pct: number;
  materials:    ServiceMaterialItem[];
  /**
   * Root-caused 2026-09-08: a `model_version: "v0"` response puts each service's REAL
   * materials/labour/tools dollar amounts directly on `services[]` (as `{amount}` /
   * `{total}` objects — see normalizeV2Response), while its TOP-LEVEL
   * `materials`/`labour`/`tool_cost` are genuine, correct zeros (this shape's real
   * breakdown lives per-service, not in an aggregate to proportionally split). The
   * "aiserver01" shape is the opposite: a real top-level aggregate, no per-service
   * amounts. buildServiceRows (BallParkEstimate.tsx) must prefer these when present —
   * multiplying a genuinely-zero top-level aggregate by a percentage is still zero,
   * which is what was reported live (TuneUp/Cleaning/Enhancement all showing $0
   * despite the raw response carrying real, non-zero per-service costs). Optional:
   * absent for the aiserver01 shape, where the proportional-split fallback still
   * applies.
   */
  labour_amount?:    number;
  tools_amount?:     number;
  materials_amount?: number;
}

export interface SimilarProject {
  project_uid:   string;
  area:          number;
  num_facets:    number;
  state:         string;
  cost:          number;
  cost_per_sqft: number;
}

export interface BPEResponse {
  total_estimate:     number;
  confidence_range:   { low: number; high: number };
  service_breakdown:  Record<string, ServiceBreakdownEntry>;
  combined_materials: ServiceMaterialItem[];
  cost_breakdown:     CostBreakdown;
  method_used:        string;    // 'xgboost_phase1' | 'rule_based' | 'cost_estimator_v4'
  model_version:      string;
  fallback:           boolean;
  fallback_level:     number | null;
  tenant_id:          string;
  similar_projects:   SimilarProject[];
}

// ASE service wraps all responses in ApiResponse<T>
interface AseApiResponse<T> {
  success: boolean;
  message: string;
  data:    T | null;
}

// ── API call ──────────────────────────────────────────────────────────────────

// BPE base URL — read from ConfigUrl.bpe_url (same pattern as recommendation_url).
// Defaults to the LAN BPE server in every OidcConfig variant; overridable via
// the REACT_APP_BPE_BASE_URL env var at build time. BPE has CORSMiddleware
// allow_origins=["*"] so direct cross-origin calls from the browser are fine.
const ASE_ORIGIN = ConfigUrl.bpe_url || '';
const BPE_ENDPOINT     = `${ASE_ORIGIN}/api/bpe/estimate`;
const BPE_LOG_ENDPOINT = `${ASE_ORIGIN}/api/bpe/log`;

const _DEV = process.env.NODE_ENV === 'development';

/**
 * Call the Ballpark Estimate API.
 * Returns null (never throws) if the service is unavailable — bid creation must
 * continue without an estimate.
 */
export const getBallParkEstimate = async (
  request: BPERequest,
): Promise<BPEResponse | null> => {
  const t0 = performance.now();

  try {
    // Credential endpoint requires BallParkCredentialApi deployed on the Java
    // backend. Fall back to unauthenticated when it is absent (404) — the BPE
    // service accepts unauthenticated requests when AUTH_ENABLED=false.
    let headers: Record<string, string> | undefined;
    try {
      headers = await fieldcraftAuthHeaders('ballpark');
    } catch (credErr: any) {
      if (_DEV) console.warn('[BPE][Service] credential fetch skipped (Java endpoint unavailable):', credErr?.message);
    }

    const result = await PyexecutePostData<AseApiResponse<BPEResponse>>(
      BPE_ENDPOINT,
      request,
      headers,
    );

    if (_DEV) {
      const ms = Math.round(performance.now() - t0);
      console.log('[BPE][Service] HTTP %s (%dms)', result.status, ms);
    }

    if (result.status === 401) {
      invalidateFieldCraftCredential('ballpark');
    }

    if (result.ok && result.data?.success && result.data?.data) {
      return result.data.data;
    }

    console.warn('[BPE][Service] Non-success → HTTP %s', result.status);
    return null;
  } catch (err) {
    console.error('[BPE][Service] Request failed:', err);
    return null;
  }
};

// ── Section-navigation snapshot ───────────────────────────────────────────────

export interface BPELogRequest {
  id?:           string;   // integer project ID from autosave
  project_id:    string;   // legacy field (may be UUID); backend prefers `id` when provided
  order_number?: string;   // 6-digit order_no from project table (fetched via GET /api/v2/projects/{id})
                           // — used as MongoDB document key; backend uses this directly when provided
  tenant_id:     string;
  customer_uid?: string;   // document identity
  asset_uid?:    string;   // document identity
  trade_name?:   string;   // document identity
  // 'bid_details_update': logged from the Bid Details page's "Update" button
  // (BallParkEstimate.tsx's logTrigger prop) — there's no wizard section index
  // to attach on that page, hence section_index being optional below.
  trigger:       'section_change' | 'bid_details_update';
  section_index?: number;
  section_name?: string;
  sq_ft:         number;
  stories:       number;
  project_type:  'residential' | 'commercial' | 'industrial';
  qa_responses:  QAResponse[];
  estimate:      BPEResponse;   // full model output — materials, quantities, labour, etc.
}

/**
 * Log a BPE snapshot to MongoDB when the user navigates between sections.
 * Fire-and-forget — never throws.
 */
export const logBPESnapshot = async (payload: BPELogRequest): Promise<void> => {
  try {
    const headers = await fieldcraftAuthHeaders('ballpark').catch(() => ({} as Record<string, string>));
    const result = await PyexecutePostData<AseApiResponse<null>>(BPE_LOG_ENDPOINT, payload, headers);
    console.log('[BPE-LOG-DIAG]', 'logBPESnapshot response', {
      trigger: payload.trigger,
      section_index: payload.section_index,
      project_id: payload.project_id,
      order_number: payload.order_number,
      status: result.status,
      ok: result.ok,
      message: result.message,
      data: result.data,
    });
    if (result.status === 401) {
      invalidateFieldCraftCredential('ballpark');
    }
  } catch (err) {
    console.log('[BPE-LOG-DIAG]', 'logBPESnapshot THREW', err);
    // non-fatal — navigation must not be blocked by log failures
  }
};

/**
 * Format a dollar amount for display (e.g. 357000 → "$357,000").
 */
export const formatCurrency = (amount: number): string =>
  `$${amount.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

/**
 * Format a confidence range for display.
 */
export const formatRange = (low: number, high: number): string =>
  `${formatCurrency(low)} – ${formatCurrency(high)}`;

// ── GBB Integration helpers ───────────────────────────────────────────────────

/**
 * Minimal bid-payload shape used by buildBPERequestFromPayload.
 * Compatible with both CreateBidWizard bidData and createSalesSchedule payload.
 */
export interface BPEBidPayload {
  bid?:           number | string;
  id?:            number | string;
  bidId?:         number | string;
  orderNO?:       string;
  jobNO?:         string;
  selectedTrade?: string;
  trade?:         string;
  customerUid?:   string;
  assetUid?:      string;
  assetAnswers?:  Record<string, string>;
  answerData?:    Array<{ tradeOid: string; answersData: string | any }>;
}

/** Decode I.66 coded values from P.41 answerData → BPE service names. */
function extractServicesFromAnswerData(answerData: BPEBidPayload['answerData']): string[] {
  const SERVICE_MAP: Record<string, string> = {
    '1': 'TuneUp', '2': 'Cleaning', '3': 'Enhancement',
  };
  const services: string[] = [];
  for (const entry of answerData ?? []) {
    if (entry.tradeOid !== 'P.41') continue;
    try {
      const parsed = typeof entry.answersData === 'string'
        ? JSON.parse(entry.answersData)
        : entry.answersData;
      const allItems: any[] =
        parsed?.EventData?.[0]?.FormData?.[0]?.ItemGroupData
          ?.flatMap((ig: any) => ig.ItemData ?? []) ?? [];
      const i66 = allItems.find((item: any) => item.ItemOID === 'I.66');
      (Array.isArray(i66?.CodeList) ? i66.CodeList : []).forEach((code: any) => {
        const svc = SERVICE_MAP[String(code)];
        if (svc && !services.includes(svc)) services.push(svc);
      });
      if (i66?.Value) {
        String(i66.Value).split(',').map((v) => v.trim()).forEach((v) => {
          const svc = SERVICE_MAP[v];
          if (svc && !services.includes(svc)) services.push(svc);
        });
      }
    } catch { /* skip malformed entry */ }
  }
  return services;
}

/** Flatten all ItemData across all answerData entries into qa_responses. */
function extractQAResponses(answerData: BPEBidPayload['answerData']): QAResponse[] {
  const responses: QAResponse[] = [];
  for (const entry of answerData ?? []) {
    try {
      const parsed = typeof entry.answersData === 'string'
        ? JSON.parse(entry.answersData)
        : entry.answersData;
      const allItems: any[] =
        parsed?.EventData?.[0]?.FormData?.[0]?.ItemGroupData
          ?.flatMap((ig: any) => ig.ItemData ?? []) ?? [];
      for (const item of allItems) {
        if (item.ItemOID && item.Value !== undefined) {
          responses.push({
            question_id:  item.ItemOID,
            answer_value: Array.isArray(item.CodeList) && item.CodeList.length > 0
              ? item.CodeList
              : item.Value,
          });
        }
      }
    } catch { /* skip malformed entry */ }
  }
  return responses;
}

/**
 * Build a BPERequest from the bid data available at createSchedule success time.
 * projectId: the integer project id returned by the createSchedule API.
 * tenantId:  from OIDC context (userSessionDetails.tenantId or similar).
 */
export function buildBPERequestFromPayload(
  payload:   BPEBidPayload,
  tenantId:  string,
  projectId: string | number,
): BPERequest {
  const assetAnswers = payload.assetAnswers ?? {};
  return {
    project_id:   String(projectId),
    sq_ft:        Number(assetAnswers['I.2'] ?? 0),
    stories:      Number(assetAnswers['I.8'] ?? 1),
    tenant_id:    tenantId,
    customer_uid: payload.customerUid,
    asset_uid:    payload.assetUid,
    trade_name:   payload.selectedTrade ?? payload.trade,
    services:     extractServicesFromAnswerData(payload.answerData),
    qa_responses: extractQAResponses(payload.answerData),
  };
}

const BPE_LATEST_ENDPOINT = `${ASE_ORIGIN}/api/bpe/latest`;

/**
 * Retrieve the latest valid BPE snapshot from MongoDB (fallback path).
 * Used when orderNO was absent from the createSalesSchedule payload, and by
 * bidDetailsMode to seed the display from the last committed estimate.
 * Returns null (never throws) — bid navigation must not be blocked.
 *
 * NOTE: GET /api/bpe/latest returns the v2-shaped doc (see
 * bpe_frontend_router.py get_latest() -> shape_v2()) — the same fields
 * /v2/infer persists (estimate_mid/materials/labour/overhead/...) — NOT the
 * v1 BPEResponse shape. The return type was previously mistyped as
 * BPEResponse, which silently produced an object with no `total_estimate`
 * and let callers assign it to the wrong display state. Fixed 2026-09-07.
 */
export async function getLatestBPESnapshot(
  tenantId:    string,
  orderNumber: string,
): Promise<BPEv2Response | null> {
  try {
    const url = `${BPE_LATEST_ENDPOINT}?tenant_id=${encodeURIComponent(tenantId)}&order_number=${encodeURIComponent(orderNumber)}`;
    const headers = await fieldcraftAuthHeaders('ballpark').catch(() => ({} as Record<string, string>));
    const res = await fetch(url, { headers });
    if (res.status === 401) {
      invalidateFieldCraftCredential('ballpark');
    }
    if (!res.ok) return null;
    const json = await res.json() as AseApiResponse<BPEv2Response>;
    return json?.success && json?.data ? normalizeV2Response(json.data) : null;
  } catch {
    return null;
  }
}

const BPE_LOG_VERSIONS_ENDPOINT = `${ASE_ORIGIN}/api/bpe/log/versions`;

/** One entry of GET /api/bpe/log/versions — a previously committed /log checkpoint. */
export interface BPESnapshotVersionEntry {
  version:        number;
  answers_hash:   string;
  is_valid:       boolean;
  saved_at:       string;
  section_name:   string;
  section_index:  number;
  sq_ft:          number;
  bpe_estimate:   Omit<BPEv2Response, 'key_missing' | 'component_available' | 'range_label'> & { total?: number };
}

/**
 * Retrieve the committed BPE snapshot history for a bid, newest first.
 *
 * Used by bidDetailsMode instead of getLatestBPESnapshot()/`/api/bpe/latest`:
 * that endpoint was found (2026-09-07) to occasionally return data matching
 * neither the document's current committed estimate nor any of its own listed
 * versions for the same (tenant_id, order_number) — a backend-side document
 * resolution issue in fieldcraft-ai-services, out of scope for this repo to fix.
 * `/log/versions` reads the same store's `versions[]` archive, which is more
 * reliable but — by upsert_estimate's own design — always one /log commit
 * BEHIND the current top-level estimate (the outgoing estimate is archived
 * into versions[] on the NEXT commit, not the one that produced it). Prefer
 * this over `/api/bpe/latest` anyway: a real, legitimate prior committed
 * value beats one matching no record at all. Returns null (never throws) —
 * bid navigation must not be blocked.
 */
export async function getBPESnapshotVersions(
  tenantId:    string,
  orderNumber: string,
): Promise<BPESnapshotVersionEntry[] | null> {
  try {
    // The endpoint's query param is named `project_id`, but the backend resolves
    // it against EITHER the document's project_id OR order_number field (see
    // resolve_bid_filter in bpe_dao.py) — passing order_number here matches the
    // same convention getLatestBPESnapshot already relies on for `/latest`.
    const url = `${BPE_LOG_VERSIONS_ENDPOINT}?tenant_id=${encodeURIComponent(tenantId)}&project_id=${encodeURIComponent(orderNumber)}`;
    const headers = await fieldcraftAuthHeaders('ballpark').catch(() => ({} as Record<string, string>));
    const res = await fetch(url, { headers });
    if (res.status === 401) {
      invalidateFieldCraftCredential('ballpark');
    }
    if (!res.ok) return null;
    const json = await res.json() as AseApiResponse<{ versions: BPESnapshotVersionEntry[]; total: number }>;
    return json?.success && json?.data
      ? json.data.versions.map(v => ({ ...v, bpe_estimate: normalizeV2Response(v.bpe_estimate as any) as any }))
      : null;
  } catch {
    return null;
  }
}

// ── BPE v2 — Dynamic Q&A-driven estimate ─────────────────────────────────────

export interface BPEv2Request {
  tenant_id:      string;
  order_number:   string;
  answered:       Record<string, string | number | string[] | null>;
  project_id?:    number;
  job_start_date?: string;   // YYYY-MM-DD; enables temporal price adjustment
}

export interface BPEv2MaterialEstimate {
  material_uid: string;
  name:         string;
  qty:          number;
  unit:         string;
  unit_cost:    number;
  subtotal:     number;
}

/**
 * One service group in the BPE v2 response. aiserver01 nests the per-material
 * breakdown here (under `materials`) and leaves the top-level
 * `material_breakdown` empty — see {@link flattenV2Materials}.
 */
export interface BPEv2Service {
  name:         string;
  display_name: string;
  is_selected:  boolean;
  cost:         number;
  subtotal:     number;
  percentage:   number;
  // `notes` is optional in the raw payload; the mappers default it to ''.
  materials:    Array<Omit<ServiceMaterialItem, 'notes'> & { notes?: string }>;
  /** Post-normalizeV2Response: this service's own real materials/labour/tools dollar
   *  amount — present only for the "v0" shape (see ServiceBreakdownEntry's own doc).
   *  Absent for "aiserver01" shape responses. */
  labour_amount?:    number;
  tools_amount?:     number;
  materials_amount?: number;
  /** This service's own material/labour/tool cost, already weighted by this
   *  service's own qa_weightage (overhead/margin are the shared project-level
   *  figure, NOT per-service — see direct_cost below). Used to resum
   *  materials/labour/tool for whichever combo is currently on screen when it
   *  differs from the combo v2Estimate was fetched for — see BallParkEstimate.tsx's
   *  activeCombination. */
  material_cost?: number;
  labour_cost?:   number;
  tool_cost?:     number;
  /** This service's own price excluding overhead/margin (material_cost+labour_cost+tool_cost). */
  direct_cost?:   number;
}

/** One pre-computed "job kind" combination — bpe_frontend_router.py's
 *  combinations[], mirroring the 5 single-select "What kind of job is going
 *  to be done?" options. Lets the frontend resolve a combo-option click's
 *  real, correct total INSTANTLY from the already-fetched snapshot instead of
 *  re-deriving it or waiting on a new /v2/infer call — see
 *  BallParkEstimate.tsx's activeCombination. */
export interface BPEv2Combination {
  key:                    string;
  services:               string[];
  display_name:           string;
  material_count:         number;
  direct_cost:            number;
  overhead:               number;
  margin:                 number;
  combination_cost:       number;
  is_current_selection:   boolean;
}

export interface BPEv2Response {
  estimate_low:         number;
  estimate_mid:         number;
  estimate_high:        number;
  materials:            number;
  labour:               number;
  tool_cost:            number;
  overhead:             number;
  margin:               number;
  tax:                  number;
  material_breakdown:   BPEv2MaterialEstimate[];
  confidence:           number;
  confidence_label:     string;
  key_missing:          string[];
  answered_count:       number;
  component_available:  boolean;
  model_version:        string;
  range_label:          string;
  /** Present on the aiserver01 response shape; carries materials per service. */
  services?:            BPEv2Service[];
  /** materials+labour+tool_cost+overhead+margin, reconciled with the visible
   *  per-service/overhead/margin line items for the CURRENTLY selected combo.
   *  Use this (not estimate_mid) for the headline total — estimate_mid does
   *  NOT sum from those same fields once the combo's weight differs from the
   *  overhead/margin multiplier (see BallParkEstimate.tsx's rawDisplayEstimate).
   *  Absent on older server responses that predate this field. */
  combination_cost?:    number;
  /** All 5 pre-computed "job kind" combinations (see BPEv2Combination) — lets
   *  the frontend resolve a combo-option click's real total instantly, without
   *  a new /v2/infer call. Absent on older server responses that predate this
   *  field. */
  combinations?:         BPEv2Combination[];
}

/** A line item carries real data only when it has a positive quantity or
 *  subtotal; the model emits zero-filled placeholder rows for unselected
 *  options (e.g. shingle colours) that must not be displayed. */
const isMeaningfulMaterial = (m: { qty?: number; subtotal?: number }): boolean =>
  (m.qty ?? 0) > 0 || (m.subtotal ?? 0) > 0;

const toServiceMaterial = (m: Partial<ServiceMaterialItem>): ServiceMaterialItem => ({
  name:         m.name ?? '',
  material_uid: m.material_uid ?? '',
  qty:          m.qty ?? 0,
  unit:         m.unit ?? '',
  unit_cost:    m.unit_cost ?? 0,
  subtotal:     m.subtotal ?? 0,
  notes:        m.notes ?? '',
});

/**
 * Root-caused 2026-09-08: a `model_version: "v0"` BPE v2 response was found to shape
 * `overhead`/`margin` as `{ amount, percentage }` objects rather than plain numbers, and
 * `services[].materials` as `{ total, percentage, items }` rather than an array — a
 * genuinely DIFFERENT wire shape than the "aiserver01" shape the rest of this file (and
 * BallParkEstimate.tsx) is built around. `?? 0`/`Array.isArray` guards elsewhere only
 * catch null/undefined/wrong-array-ness — they correctly stop a crash, but an object
 * multiplied by a scale factor still produces NaN (confirmed live: Overhead/Margin
 * rendered as "NaN%"/"$NaN", TuneUp/Cleaning/Enhancement rendered "$0" despite the raw
 * response carrying real, non-zero `cost`/`materials.items`/`labour.amount` values).
 * Normalize both known shapes into the flat-number/array shape every consumer already
 * expects, ONCE, at every point a v2 response is parsed — rather than teaching every
 * downstream consumer (BallParkEstimate.tsx, buildV2ServiceBreakdown, flattenV2Materials)
 * to handle a second wire shape individually.
 */
function unwrapAmount(v: unknown): number {
  if (typeof v === 'number') return v;
  if (v && typeof v === 'object' && typeof (v as any).amount === 'number') return (v as any).amount;
  return 0;
}

export function normalizeV2Response(raw: any): BPEv2Response {
  if (!raw || typeof raw !== 'object') return raw;
  const services = Array.isArray(raw.services)
    ? raw.services.map((s: any) => ({
        ...s,
        materials: Array.isArray(s?.materials)
          ? s.materials
          : (Array.isArray(s?.materials?.items) ? s.materials.items : []),
        // See ServiceBreakdownEntry's own doc: the "v0" shape's real per-service
        // labour/tools/materials dollar amounts live in these nested objects — carry
        // them through as plain numbers so buildServiceRows can prefer them over a
        // genuinely-zero top-level aggregate.
        labour_amount:    s?.labour != null ? unwrapAmount(s.labour) : undefined,
        tools_amount:     s?.tools  != null ? unwrapAmount(s.tools)  : undefined,
        materials_amount: (s?.materials && !Array.isArray(s.materials) && typeof s.materials.total === 'number')
          ? s.materials.total
          : undefined,
      }))
    : raw.services;
  return {
    ...raw,
    materials: unwrapAmount(raw.materials),
    labour:    unwrapAmount(raw.labour),
    tool_cost: unwrapAmount(raw.tool_cost),
    overhead:  unwrapAmount(raw.overhead),
    tax:       unwrapAmount(raw.tax),
    margin:    raw.margin != null ? unwrapAmount(raw.margin) : raw.margin,
    services,
  };
}

/**
 * Resolve the flat material breakdown from a BPE v2 response, tolerant of the
 * two server response shapes:
 *   - older / local: a populated top-level `material_breakdown`
 *   - aiserver01:    materials nested under `services[].materials`, with an
 *                    empty top-level `material_breakdown`
 * Zero-quantity / zero-subtotal placeholder rows are dropped.
 */
export function flattenV2Materials(resp: BPEv2Response): ServiceMaterialItem[] {
  // See buildV2ServiceBreakdown's comment: a DB-seeded snapshot can carry a
  // wrong-shaped (non-array) value for a field typed as an array — guard both
  // top-level `material_breakdown` and `services` explicitly, not just via
  // `?? []` (which only catches null/undefined).
  const flat = Array.isArray(resp.material_breakdown) ? resp.material_breakdown : [];
  if (flat.length > 0) {
    return flat.map(toServiceMaterial).filter(isMeaningfulMaterial);
  }
  const services = Array.isArray(resp.services) ? resp.services : [];
  return services
    .flatMap(s => Array.isArray(s.materials) ? s.materials : [])
    .map(toServiceMaterial)
    .filter(isMeaningfulMaterial);
}

/**
 * Build a per-service breakdown map (keyed by service name) from a v2
 * response's `services[]`, carrying each service's materials through. Returns
 * {} when the response has no services (older flat shape) so callers fall back
 * to the v1 service_breakdown.
 */
export function buildV2ServiceBreakdown(
  resp: BPEv2Response,
): Record<string, ServiceBreakdownEntry> {
  const out: Record<string, ServiceBreakdownEntry> = {};
  // Build ALL services regardless of `is_selected` — the component filters down
  // to the user's I.66 answer choices via filteredSvcBd / selectedServiceNames
  // downstream, so pre-filtering here would be redundant.
  // `resp.services` and each `s.materials` are typed as arrays, but a DB-seeded
  // snapshot (a different serialization path than the live model response) can
  // carry a wrong-shaped value at runtime — confirmed live via a crash
  // ("...materials.map is not a function") once such a snapshot got cached and
  // re-rendered. `?? []` only guards null/undefined, not "truthy but not an
  // array," so guard both explicitly.
  const services = Array.isArray(resp.services) ? resp.services : [];
  services.forEach(s => {
    out[s.name] = {
      display_name: s.display_name ?? s.name,
      cost:         s.cost ?? 0,
      percentage:   s.percentage ?? 0,
      subtotal:     s.subtotal ?? 0,
      margin:       0,
      margin_pct:   0,
      overhead:     0,
      overhead_pct: 0,
      materials:    (Array.isArray(s.materials) ? s.materials : []).map(toServiceMaterial).filter(isMeaningfulMaterial),
      labour_amount:    s.labour_amount,
      tools_amount:     s.tools_amount,
      materials_amount: s.materials_amount,
    };
  });
  return out;
}

export type BPEv2Result =
  | { ok: true;  data: BPEv2Response }
  | { ok: false; reason: 'service_unavailable' | 'no_data' };

const BPE_V2_INFER_ENDPOINT              = `${ASE_ORIGIN}/api/bpe/v2/infer`;
const BPE_V2_MATERIAL_GRADES_ENDPOINT    = `${ASE_ORIGIN}/api/bpe/v2/material-grades`;
const BPE_V2_GRADE_SELECTION_ENDPOINT    = `${ASE_ORIGIN}/api/bpe/v2/grade-selection`;
const BPE_V2_SNAPSHOT_ENDPOINT           = `${ASE_ORIGIN}/api/bpe/v2/snapshot`;

// ── BPE v2 — Material grade catalogue (GBB) ───────────────────────────────────

/** One grade variant from the BPE material catalogue */
export interface BPEGradeVariant {
  material_uid: string;
  name:         string;
  unit_cost:    number;
  unit:         string;
  notes?:       string;
  services?:    string[];
}

/** A requested material item with all three grade variants resolved */
export interface BPEResolvedMaterial {
  name:         string;
  material_uid?: string;
  quantity:     number;
  unit:         string;
  matched:      boolean;
  grades:       Partial<Record<'a' | 'b' | 'c', BPEGradeVariant>>;
}

export interface BPEMaterialGradesRequest {
  materials:       Array<{ name: string; material_uid?: string; quantity: number; unit: string }>;
  /** YYYY-MM-DD. When set, each variant's unit_cost is scaled by ENR-CCI
   *  projection so GBB cards reflect the price at the projected job start. */
  job_start_date?: string;
}

/**
 * Fetch all three grade variants (a/b/c) for each material from the BPE
 * material catalogue — replaces the Java resolveGradeVariants DB call.
 * Returns [] (never throws) on any failure.
 */
export const getBPEMaterialGrades = async (
  request: BPEMaterialGradesRequest,
): Promise<BPEResolvedMaterial[]> => {
  try {
    const headers = await fieldcraftAuthHeaders('ballpark').catch(() => ({} as Record<string, string>));
    const result = await PyexecutePostData<AseApiResponse<{ resolved: BPEResolvedMaterial[] }>>(
      BPE_V2_MATERIAL_GRADES_ENDPOINT,
      request,
      headers,
    );
    if (result.status === 401) {
      invalidateFieldCraftCredential('ballpark');
    }
    if (result.ok && result.data?.success && result.data?.data?.resolved) {
      return result.data.data.resolved;
    }
    return [];
  } catch {
    return [];
  }
};

// ── GBB grade selection log ───────────────────────────────────────────────────

export interface BPEGradeSelectionMaterial {
  name:          string;
  material_uid?: string;
  quantity:      number;
  unit:          string;
  unit_cost:     number;
  subtotal:      number;
}

export interface BPEGradeSelectionRequest {
  tenant_id:    string;
  order_number: string;
  project_id?:  string;
  grade:        'a' | 'b' | 'c';
  materials:    BPEGradeSelectionMaterial[];
}

/**
 * Persist the confirmed GBB grade choice to the BPE MongoDB document.
 * Fire-and-forget — never throws.
 */
export const logBPEGradeSelection = async (
  request: BPEGradeSelectionRequest,
): Promise<void> => {
  try {
    const headers = await fieldcraftAuthHeaders('ballpark').catch(() => ({} as Record<string, string>));
    const result = await PyexecutePostData<AseApiResponse<null>>(BPE_V2_GRADE_SELECTION_ENDPOINT, request, headers);
    if (result.status === 401) {
      invalidateFieldCraftCredential('ballpark');
    }
  } catch {
    // non-fatal — grade selection must not be blocked by log failures
  }
};

/**
 * Call the BPE v2 dynamic inference endpoint.
 * Designed to be called on every Q&A change (debounced by the component).
 * Returns null (never throws) — estimate must not block bid creation.
 */
export const getBPEv2Estimate = async (
  request: BPEv2Request,
): Promise<BPEv2Result> => {
  try {
    const headers = await fieldcraftAuthHeaders('ballpark').catch(() => ({} as Record<string, string>));
    const result = await PyexecutePostData<AseApiResponse<BPEv2Response>>(
      BPE_V2_INFER_ENDPOINT,
      request,
      headers,
    );
    if (result.status === 401) {
      invalidateFieldCraftCredential('ballpark');
    }
    if (result.ok && result.data?.success && result.data?.data) {
      return { ok: true, data: normalizeV2Response(result.data.data) };
    }
    // HTTP-level failure (server down, 5xx) is distinguishable from empty data.
    if (!result.ok) return { ok: false, reason: 'service_unavailable' };
    return { ok: false, reason: 'no_data' };
  } catch {
    // Network error — service unreachable (ECONNREFUSED, timeout, CORS, etc.)
    return { ok: false, reason: 'service_unavailable' };
  }
};

/**
 * Fetch the raw BPE MongoDB document for a bid via GET /v2/snapshot.
 *
 * Returns the document in MongoBpeDocument shape (schema_version 2.0.0) so the
 * frontend's adaptMongoDocumentToSnapshot() can convert it to BpeSnapshot for
 * the GBB tier algorithm and card builder.
 *
 * `idParam` selects which identity the lookup is keyed on — defaults to
 * 'project_id' for existing callers (GBB). Pass 'order_number' when the
 * caller only trusts the stable order number: the tenant's live project id
 * can DRIFT after submit (e.g. 1837 → 1838 — same class of bug documented on
 * getBPEv2Estimate below), which re-keys a project_id lookup onto a document
 * that was never written under that id. order_number never drifts.
 *
 * Returns null for 404 (document not yet written) and transient network errors —
 * the caller should retry.
 * Throws on auth errors (401) and server errors (5xx) so the caller can fail
 * fast rather than retrying indefinitely.
 * Re-throws AbortError/TimeoutError when the caller's signal or the per-attempt
 * timeout fires.
 */
export const getBpeSnapshot = async (
  tenantId: string,
  id: string | number,
  signal?: AbortSignal,
  idParam: 'project_id' | 'order_number' = 'project_id',
): Promise<Record<string, unknown> | null> => {
  if (!tenantId || !id) return null;
  try {
    const url = `${BPE_V2_SNAPSHOT_ENDPOINT}?tenant_id=${encodeURIComponent(tenantId)}&${idParam}=${encodeURIComponent(String(id))}`;
    const headers = await fieldcraftAuthHeaders('ballpark').catch(() => ({} as Record<string, string>));

    // 10 s per-attempt cap: prevents a slow server from hanging the GBB retry loop indefinitely.
    const timeoutSignal = AbortSignal.timeout(10_000);
    // AbortSignal.any is not available in Safari < 17.4 — guard with a runtime check and
    // degrade gracefully: the per-attempt timeout still works; caller-abort is best-effort.
    const combinedSignal =
      signal && typeof AbortSignal.any === 'function'
        ? AbortSignal.any([signal, timeoutSignal])
        : timeoutSignal;

    const result = await fetch(url, { headers, signal: combinedSignal });

    if (result.status === 401) {
      invalidateFieldCraftCredential('ballpark');
      const err = new Error('Unauthorized');
      (err as any).status = 401;
      throw err;
    }
    if (result.status === 404) return null;  // doc not written yet — caller should retry
    if (!result.ok) {
      const err = new Error(`Snapshot service error (${result.status})`);
      (err as any).status = result.status;
      throw err;
    }
    const json = await result.json();
    return json?.success && json?.data ? (json.data as Record<string, unknown>) : null;
  } catch (err: any) {
    // Re-throw auth/server errors and explicit aborts so the caller can fail fast.
    // AbortError = caller-initiated abort; TimeoutError = per-attempt AbortSignal.timeout fire.
    if (err?.status || err?.name === 'AbortError' || err?.name === 'TimeoutError') throw err;
    // Network error or CORS — transient; caller may retry.
    return null;
  }
};

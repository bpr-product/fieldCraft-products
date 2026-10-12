/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-21
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Flatten a finalized RoofLens measurement export into the flat
 *                MetaKey -> value map the Asset questionnaire understands, so a
 *                saved measurement can best-effort populate matching asset
 *                questions. Pure (no I/O). Decision-support only — not a survey.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-21 | FIELDCRAFT Web | Initial version
 */

import { RoofLensExportData } from "./exportRoofLens";
import { FacetEdgeMetrics, RoofMetrics } from "./types";

/** Summed edge-length totals (feet) across a set of facets/buildings. */
interface EdgeTotals {
  ridge: number;
  hip: number;
  valley: number;
  rake: number;
  eave: number;
}

const emptyEdges = (): EdgeTotals => ({ ridge: 0, hip: 0, valley: 0, rake: 0, eave: 0 });

/** Edge totals from a building's Auto-Detect roof report (already whole-building). */
function edgesFromReport(rr: RoofMetrics): EdgeTotals {
  return {
    ridge: rr.ridge_ft ?? 0,
    hip: rr.hip_ft ?? 0,
    valley: rr.valley_ft ?? 0,
    rake: rr.rake_ft ?? 0,
    eave: rr.eave_ft ?? 0,
  };
}

/** Edge totals summed from per-facet edge breakdowns (fallback when no report). */
function edgesFromFacets(edges: (FacetEdgeMetrics | undefined)[]): EdgeTotals {
  return edges.reduce<EdgeTotals>((acc, e) => {
    if (!e) return acc;
    acc.ridge += e.ridge_ft ?? 0;
    acc.hip += e.hip_ft ?? 0;
    acc.valley += e.valley_ft ?? 0;
    acc.rake += e.rake_ft ?? 0;
    acc.eave += e.eave_ft ?? 0;
    return acc;
  }, emptyEdges());
}

/**
 * Alias MetaKeys per metric. These MIRROR the alias arrays in CreateAsset_v3's
 * `handleMetricsComputed` / `handleAreaComputed` (kept in sync deliberately —
 * DO NOT refactor CreateAsset_v3 to share this). The asset side tries several
 * MetaKeys per metric because production templates use varied MetaKey names; the
 * bid pre-population loop does an EXACT `roofLensMeta[itemDef.MetaKey]` lookup, so
 * this util must emit an entry for EVERY alias (all aliases share the same value)
 * or a template MetaKey we didn't emit is silently missed. The pre-existing short
 * keys are kept first for back-compat with existing consumers/tests.
 */
const META_KEY_ALIASES = {
  // `TotalArea` / `TotalRoofFacets` / `Pitch` are the REAL template-fixture
  // MetaKeys (top-level, non-repeating). The pre-existing short/legacy aliases are
  // kept alongside them — harmless, and back-compat for existing consumers/tests.
  area: ["area", "TotalArea"],
  facetCount: ["facetCount", "TotalRoofFacets"],
  ridge: ["ridge", "ridgeLength"],
  hip: ["hip", "hipLength"],
  ridgeHip: ["ridgeHip", "ridgeHipLength", "ridgesHips"],
  valley: ["valley", "valleyLength"],
  rake: ["rake", "rakeLength"],
  eave: ["eave", "eaveLength"],
  predominantPitch: ["predominantPitch", "pitch", "roofPitch", "Pitch"],
  attic: ["attic", "estimatedAttic", "atticSqft"],
  stories: ["stories", "numberOfStories", "noOfStories"],
  height: ["height", "buildingHeight"],
  complexity: ["complexity", "structureComplexity"],
} as const;

/**
 * Flatten a finalized `RoofLensExportData` into a flat MetaKey -> value map for
 * best-effort auto-fill of the asset questionnaire. Each metric is emitted under
 * EVERY alias MetaKey (see `META_KEY_ALIASES`) so a template using any known
 * MetaKey name resolves; the aliases mirror CreateAsset_v3's handleMetricsComputed.
 *
 * Aggregation is across ALL buildings: area/facet counts come from the export
 * summary; edge totals sum each building's Auto-Detect roof report (falling back
 * to summing that building's per-facet edge metrics when the report is absent);
 * pitch / attic / stories / height / complexity come from the largest building's
 * roof report. Keys whose source value is null/undefined are omitted, so a
 * template without those questions is unaffected. Pure — no I/O.
 */
export function roofLensExportToMetaKeyValues(
  data: RoofLensExportData
): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  if (!data) return out;

  // Emit `value` under every alias MetaKey for a metric; omit when value is null.
  const emit = (
    aliases: readonly string[],
    value: string | number | null | undefined
  ): void => {
    // Defensive null-guard: every call site already checks its source value is non-null
    // before calling emit, so this early-return is not reachable from the current callers.
    /* istanbul ignore next */
    if (value == null) return;
    aliases.forEach((k) => {
      out[k] = value;
    });
  };

  // Area + facet count come straight off the whole-property summary.
  if (data.summary?.total_roof_area_sqft != null) {
    emit(META_KEY_ALIASES.area, Math.round(data.summary.total_roof_area_sqft));
  }
  if (data.summary?.facet_count != null) {
    emit(META_KEY_ALIASES.facetCount, data.summary.facet_count);
  }

  // Edge totals: sum each building's roof report; fall back to summing the
  // building's per-facet edge metrics when that building has no report.
  const buildings = data.buildings ?? [];
  const edgeTotal = emptyEdges();
  buildings.forEach((b) => {
    const be = b.roof_report
      ? edgesFromReport(b.roof_report)
      : edgesFromFacets((b.facets ?? []).map((f) => f.edge_metrics));
    edgeTotal.ridge += be.ridge;
    edgeTotal.hip += be.hip;
    edgeTotal.valley += be.valley;
    edgeTotal.rake += be.rake;
    edgeTotal.eave += be.eave;
  });
  // If there were no buildings at all, fall back to the flattened facet list.
  if (buildings.length === 0 && (data.facets?.length ?? 0) > 0) {
    const be = edgesFromFacets(data.facets.map((f) => f.edge_metrics));
    edgeTotal.ridge = be.ridge;
    edgeTotal.hip = be.hip;
    edgeTotal.valley = be.valley;
    edgeTotal.rake = be.rake;
    edgeTotal.eave = be.eave;
  }
  emit(META_KEY_ALIASES.ridge, Math.round(edgeTotal.ridge));
  emit(META_KEY_ALIASES.hip, Math.round(edgeTotal.hip));
  emit(META_KEY_ALIASES.ridgeHip, Math.round(edgeTotal.ridge + edgeTotal.hip));
  emit(META_KEY_ALIASES.valley, Math.round(edgeTotal.valley));
  emit(META_KEY_ALIASES.rake, Math.round(edgeTotal.rake));
  emit(META_KEY_ALIASES.eave, Math.round(edgeTotal.eave));

  // Pitch / attic / stories / height / complexity: take the predominant building
  // (largest roof area) that carries an Auto-Detect report; else the first one.
  const withReport = buildings.filter((b) => b.roof_report);
  const predominant =
    withReport
      .slice()
      .sort(
        (a, b) =>
          (b.summary?.total_roof_area_sqft ?? 0) - (a.summary?.total_roof_area_sqft ?? 0)
      )[0] ?? buildings[0];
  const rr: RoofMetrics | null = predominant?.roof_report ?? null;
  if (rr) {
    if (rr.predominant_pitch != null) emit(META_KEY_ALIASES.predominantPitch, rr.predominant_pitch);
    if (rr.estimated_attic_sqft != null) emit(META_KEY_ALIASES.attic, Math.round(rr.estimated_attic_sqft));
    if (rr.stories != null) emit(META_KEY_ALIASES.stories, rr.stories);
    if (rr.building_height_ft != null) emit(META_KEY_ALIASES.height, Math.round(rr.building_height_ft));
    if (rr.structure_complexity != null) emit(META_KEY_ALIASES.complexity, rr.structure_complexity);
  }

  return out;
}

/**
 * The REAL per-facet MetaKey vocabulary emitted per `data.facets[]` entry. These
 * are the measurement MetaKeys a repeating (per-facet) ItemGroup tags its member
 * questions with, across ANY trade — matching is by MetaKey, never by OID.
 *   `area` / `TotalArea` / `roof_area_sqft` : this facet's roof area (sq ft)
 *   `Pitch`              : this facet's pitch, raw "X/12"
 *   `LengthRidge/Eave/Valley/Rake/Hip` : this facet's edge lengths (ft)
 */
const PER_FACET_METAKEY_OUTPUTS = [
  'area', 'TotalArea', 'roof_area_sqft', 'Pitch',
  'LengthRidge', 'LengthEave', 'LengthValley', 'LengthRake', 'LengthHip',
] as const;

/** Round to a whole number; null when the input isn't a finite number. */
function toIntOrNull(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n) : null;
}

/**
 * Flatten a finalized `RoofLensExportData` into one MetaKey -> value map PER FACET
 * (`data.facets[]`), keyed by the real per-facet measurement vocabulary (see
 * `PER_FACET_METAKEY_OUTPUTS`). Feeds the generic per-facet repeating-group
 * autofill: each entry maps to one repeating-group instance, matched to the
 * template's questions by MetaKey. Numbers are rounded to integers; the raw
 * "X/12" pitch string is preserved (coded-value resolution happens downstream).
 * Null/undefined source values are omitted so a template lacking a question is
 * simply not filled. Pure — no I/O.
 */
export function roofLensExportToPerFacetMetaKeyValues(
  data: RoofLensExportData
): Array<Record<string, string | number>> {
  const out: Array<Record<string, string | number>> = [];
  if (!data?.facets) return out;

  for (const facet of data.facets) {
    const row: Record<string, string | number> = {};

    const area = toIntOrNull(facet.roof_area_sqft);
    if (area !== null) {
      row.area = area;
      row.TotalArea = area;
      row.roof_area_sqft = area; // template's per-facet "square footage" question MetaKey
    }

    if (facet.pitch != null && String(facet.pitch).trim() !== '') {
      row.Pitch = facet.pitch;
    }

    const e = facet.edge_metrics;
    if (e) {
      const ridge = toIntOrNull(e.ridge_ft);
      if (ridge !== null) row.LengthRidge = ridge;
      const eave = toIntOrNull(e.eave_ft);
      if (eave !== null) row.LengthEave = eave;
      const valley = toIntOrNull(e.valley_ft);
      if (valley !== null) row.LengthValley = valley;
      const rake = toIntOrNull(e.rake_ft);
      if (rake !== null) row.LengthRake = rake;
      const hip = toIntOrNull(e.hip_ft);
      if (hip !== null) row.LengthHip = hip;
    }

    out.push(row);
  }

  return out;
}

/** Exported for reuse by the generic per-facet autofill (MetaKey matching). */
export const PER_FACET_METAKEYS: ReadonlySet<string> = new Set(PER_FACET_METAKEY_OUTPUTS);

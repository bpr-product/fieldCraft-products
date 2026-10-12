/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Shared types for the RoofLens roof-measurement feature embedded
 *                in the Asset page (ported from the standalone RoofLens frontend).
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import type { LatLng } from "../../../interface/AddressInterfaces";
export type { LatLng };

export type RoofLensTool = "view" | "draw" | "edit" | "pin";

/** One building/structure on a property (a house, a detached garage, …). */
export interface Building {
  id: string;
  name: string;
}

/** Per-facet edge-length breakdown (feet) from the last Auto-Detect run. */
export interface FacetEdgeMetrics {
  ridge_ft: number;
  hip_ft: number;
  valley_ft: number;
  rake_ft: number;
  eave_ft: number;
  parapet_ft: number;
}

/** A single roof facet held in the store (data only — no Google Maps objects). */
export interface RoofFacet {
  id: string;
  /** Which building this facet belongs to (see store `buildings`). */
  buildingId: string;
  /** Boundary vertices in lat/lng. */
  path: LatLng[];
  /** Pitch rise per 12 (0-24). Default 6. */
  pitchRise: number;
  /** True when produced by SAM2/auto segmentation, false for manual draw. */
  isAuto: boolean;
  /** True 3D roof area (sq ft) from the ML service; null once the user edits the boundary. */
  serviceAreaSqft: number | null;
  /** True once the user has moved a vertex (invalidates serviceAreaSqft). */
  edited: boolean;
  /** Edge breakdown attached at Auto-Detect time so it survives deleting/adjusting
   *  OTHER facets (kept as a last-known value while the boundary is edited, since
   *  edge classification can only be recomputed by a fresh Auto-Detect run). */
  edgeMetrics?: FacetEdgeMetrics;
}

/** Per-facet computed measurement for the live table. */
export interface FacetMeasurement {
  id: string;
  label: string;
  roofAreaSqft: number;
  footprintSqft: number;
  perimeterFt: number;
  pitch: string;
  source: "auto" | "auto-adjusted" | "manual";
}

export interface RoofTotals {
  totalRoofAreaSqft: number;
  footprintSqft: number;
  perimeterFt: number;
  facetCount: number;
  facets: FacetMeasurement[];
}

/** Shape of a facet in the ML /ml/facets response. */
export interface MlFacet {
  boundary: [number, number][]; // [lat, lng]
  pitch_ratio?: string;
  area_sqft?: number;
}

/**
 * EagleView-style metrics for the whole structure, computed by the backend from
 * the same SAM2 + 3D-tile geometry (see structure_from_facets.py). All values
 * are estimates: edge-length totals are the most reliable; per-type counts,
 * attic, stories and height are rough and carry confidence notes. Present only
 * when the backend could assemble a structure from the auto-detect run.
 */
/** One facet's own area, pitch, and edge-length breakdown (from Auto-Detect). */
export interface RoofFacetMetric {
  facet_number: number;
  facet_id?: string;
  area_sqft?: number;
  pitch?: string;
  direction?: string;
  ridge_ft?: number;
  hip_ft?: number;
  valley_ft?: number;
  rake_ft?: number;
  eave_ft?: number;
  parapet_ft?: number;
}

export interface RoofMetrics {
  facet_count?: number;
  per_facet?: RoofFacetMetric[];
  predominant_pitch?: string;
  area_per_pitch?: Record<string, number>;
  ridge_ft?: number;
  hip_ft?: number;
  ridge_hip_ft?: number;
  valley_ft?: number;
  rake_ft?: number;
  eave_ft?: number;
  parapet_ft?: number;
  ridge_count?: number;
  hip_count?: number;
  valley_count?: number;
  rake_count?: number;
  eave_count?: number;
  parapet_count?: number;
  footprint_sqft?: number;
  estimated_attic_sqft?: number;
  estimated_attic_confidence_note?: string;
  building_height_ft?: number;
  eave_height_ft?: number;
  stories?: number;
  number_of_stories?: string;
  number_of_stories_confidence_note?: string;
  structure_complexity?: string;
  structure_complexity_confidence_note?: string;
  metrics_confidence_note?: string;
}

export interface MlFacetsResponse {
  facets: MlFacet[];
  facet_count?: number;
  score?: number;
  total_area_sqft?: number;
  status?: string;
  message?: string;
  config?: Record<string, unknown>;
  metrics?: RoofMetrics;
  metrics_error?: string;
  /** AB#28854 phase 2 — the RoofLens VM now runs Address Validation + Solar checks itself
   *  before any mesh/SAM2 work, and carries the verdict on every /ml/facets response (a
   *  short-circuited `status: "invalid_address"` response, or a normal successful one) so the
   *  picker can read it here instead of a separate round-trip to /ml/validate-address. Plain
   *  strings ("VALID"/"INVALID"/"UNVERIFIED", "ROOF_FOUND"/"NO_ROOF"/"UNVERIFIED"), mirroring
   *  the backend's own String-status-constant convention. */
  address_status?: string;
  roof_status?: string;
}

export interface GeocodeResult {
  lat: number;
  lng: number;
  address: string;
}

/** Moved to the shared interface module so services need not import from components; re-exported for existing importers. */
export type { PropertyValidationResult } from "../../../interface/AddressInterfaces";

/** Payload for POST /ml/blueprint (drives the exported PDF's diagram images). */
export interface BlueprintRequest {
  lat: number;
  lng: number;
  address: string;
  facets: { boundary: [number, number][]; area_sqft: number; pitch: string }[];
}

export interface BlueprintResponse {
  aerial?: string;
  image?: string;
  facets_only?: string;
}

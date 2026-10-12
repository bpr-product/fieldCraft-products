/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : In-house spherical geometry for roof measurement (area, perimeter,
 *                pitch). Computed here rather than via google.maps.geometry so the
 *                shared Maps loader (libraries:["places"]) is left untouched.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import { LatLng } from "../types";

const EARTH_RADIUS_M = 6378137; // WGS84 semi-major axis
const SQM_TO_SQFT = 10.7639104;
const M_TO_FT = 3.280839895;

const toRad = (deg: number): number => (deg * Math.PI) / 180;

/**
 * Spherical polygon area in square meters (same spherical-excess method
 * google.maps.geometry.spherical.computeArea uses). Ring is auto-closed.
 */
export function sphericalAreaSqm(path: LatLng[]): number {
  const n = path.length;
  if (n < 3) return 0;
  let total = 0;
  for (let i = 0; i < n; i++) {
    const p1 = path[i];
    const p2 = path[(i + 1) % n];
    total += toRad(p2.lng - p1.lng) * (2 + Math.sin(toRad(p1.lat)) + Math.sin(toRad(p2.lat)));
  }
  total = (total * EARTH_RADIUS_M * EARTH_RADIUS_M) / 2;
  return Math.abs(total);
}

/** Horizontal footprint area of a polygon in square feet. */
export function footprintSqft(path: LatLng[]): number {
  return sphericalAreaSqm(path) * SQM_TO_SQFT;
}

/** Great-circle distance between two points, in meters. */
function haversineM(a: LatLng, b: LatLng): number {
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Closed-loop perimeter of a polygon in feet. */
export function perimeterFt(path: LatLng[]): number {
  const n = path.length;
  if (n < 2) return 0;
  let meters = 0;
  for (let i = 0; i < n; i++) {
    meters += haversineM(path[i], path[(i + 1) % n]);
  }
  return meters * M_TO_FT;
}

/** Roof-area multiplier for a pitch rise (per 12): 1/cos(atan(rise/12)). */
export function pitchFactor(rise: number): number {
  return 1 / Math.cos(Math.atan(rise / 12));
}

/** Parse a "6/12" pitch ratio into its rise (default 6). */
export function parsePitchRise(ratio?: string): number {
  if (!ratio) return 6;
  const m = /(-?\d+(?:\.\d+)?)\s*\/\s*12/.exec(ratio);
  return m ? Number(m[1]) : 6;
}

/** Parse a "lat,lng" string into a LatLng, or null when invalid. */
export function parseLatLngStr(value?: string): LatLng | null {
  if (!value || !value.includes(",")) return null;
  const [a, b] = value.split(",");
  const lat = Number(a);
  const lng = Number(b);
  if (Number.isNaN(lat) || Number.isNaN(lng)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return { lat, lng };
}

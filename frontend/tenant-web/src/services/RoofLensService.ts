/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Client for the RoofLens gateway (roof-measurement backend + AI/ML
 *                service). Calls the single RoofLens nginx gateway configured at
 *                ConfigUrl.rooflens_url.
 *
 *                AUTH (deliberate bearer-only): every request carries ONLY the Azure AD
 *                B2C bearer token (Authorization: Bearer), which the RoofLens backend
 *                validates against AD B2C. This is intentionally NOT the recommendation/BPE
 *                pattern — those call fieldcraftAuthHeaders(kind) to mint a scoped, revocable
 *                X-FIELDCRAFT-Token (with bearer-only only as a dev fallback). RoofLens has no
 *                X-FIELDCRAFT-Token layer yet: there is no "rooflens" FieldCraftCredentialKind and the
 *                backend does not validate one, so the raw platform B2C token is the sole
 *                credential sent to this gateway. Adding a tenant-scoped X-FIELDCRAFT-Token is a
 *                tracked follow-up (see the RoofLens auth/persistence notes).
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import axios from "axios";
import { ConfigUrl } from "../auth/OidcConfig";
import { bearerOnlyHeaders } from "./fieldcraftTokenClient";
import {
  BlueprintRequest,
  BlueprintResponse,
  GeocodeResult,
  MlFacetsResponse,
} from "../components/asset/rooflens/types";

const base = (): string => ConfigUrl.rooflens_url || "";

// SAM2 + 3D-mesh segmentation can take ~60-90s (cold mesh fetch); allow 5 min.
const client = axios.create({ timeout: 300000 });

// Attach the Azure AD B2C bearer token to every RoofLens call so the backend can
// validate it (Authorization: Bearer <accessToken>). Wrapped in try/catch so calls
// made before login don't throw — an unauthenticated request simply 401s server-side.
client.interceptors.request.use((config) => {
  try {
    Object.assign(config.headers, bearerOnlyHeaders());
  } catch (e) {
    // No B2C token yet (e.g. pre-login) — leave the request unauthenticated; it 401s
    // server-side. Log it so an auth-token failure is distinguishable from a generic
    // backend error in telemetry (mirrors RecommendationService's bearer-only fallback).
    console.warn("RoofLensService: no B2C bearer token available; sending unauthenticated request", e);
  }
  return config;
});

/** Run SAM2 + 3D-mesh facet segmentation for a lat/lng. */
export const fetchFacets = async (lat: number, lng: number): Promise<MlFacetsResponse> => {
  const url = `${base()}/ml/facets?lat=${lat}&lng=${lng}`;
  const { data } = await client.get<MlFacetsResponse>(url);
  return data;
};

/** Server-side geocode fallback (the search bar normally geocodes client-side). */
export const geocodeAddress = async (address: string): Promise<GeocodeResult> => {
  const url = `${base()}/api/geocode?address=${encodeURIComponent(address)}`;
  // Geocode is a fast lookup — cap it well below the shared client's 5-min SAM2
  // timeout so a hang here fails quickly instead of holding for 5 minutes.
  const { data } = await client.get<GeocodeResult>(url, { timeout: 15000 });
  return data;
};

/** Render the aerial/diagram images used in the exported PDF. */
export const fetchBlueprint = async (payload: BlueprintRequest): Promise<BlueprintResponse> => {
  const url = `${base()}/ml/blueprint`;
  const { data } = await client.post<BlueprintResponse>(url, payload);
  return data;
};

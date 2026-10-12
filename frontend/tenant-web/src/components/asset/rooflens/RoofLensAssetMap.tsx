/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : RoofLens roof-measurement panel embedded in the Asset page's
 *                "Asset Image" card. Orchestrates search + map + toolbar +
 *                measurements + export. Ported from the standalone RoofLens
 *                frontend (app.js/measure.js) into React/MUI. Experimental /
 *                internal — Google 3D-tiles measurement is ToS-gated (feature-flagged).
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 * AB#32415 | 30-Sep-2026 | Steeve | A pick is claimed so auto-detect does not re-run for it;
 *          |             |        | a stale saved-address validation cannot overwrite a pick
 *          |             |        | or recentre the map; Move Pin uses the latest pick handler
 */

import * as React from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { useFlag } from "@openfeature/react-sdk";
import ScienceOutlinedIcon from "@mui/icons-material/ScienceOutlined";
import ViewInArIcon from "@mui/icons-material/ViewInAr";
import GridOnIcon from "@mui/icons-material/GridOn";
import MapOutlinedIcon from "@mui/icons-material/MapOutlined";
import { useGoogleMaps } from "../../../context/GoogleMapsContext";
import { fetchBlueprint, fetchFacets, geocodeAddress } from "../../../services/RoofLensService";
import { validatePropertyAddress } from "../../../services/AssetService";
import RoofLensMap from "./RoofLensMap";
import RoofLens3DView from "./RoofLens3DView";
import RoofLensToolbar from "./RoofLensToolbar";
import RoofLensBuildingBar from "./RoofLensBuildingBar";
import RoofLensSearchBar from "./RoofLensSearchBar";
import RoofLensMeasurements from "./RoofLensMeasurements";
import RoofLensLoadingOverlay from "./RoofLensLoadingOverlay";
import { computeTotals, nextFacetId, useRoofLensStore } from "./store/rooflensStore";
import { buildExportData, exportJson, exportPdf, RoofLensExportData } from "./exportRoofLens";
import { BlueprintResponse, LatLng, PropertyValidationResult, RoofFacet, RoofMetrics } from "./types";
import { parseLatLngStr, parsePitchRise } from "./utils/geometry";
import { ParsedAddress } from "../AssetLocationPicker";
import { isGoogleMapsGeocoderReady } from "../../../utils/mapUtils";

/** Map Google Places/Geocoder address_components to the CRM's ParsedAddress shape.
 *  Deliberately a local copy, not a cross-import from RoofLensSearchBar/AssetLocationPicker
 *  (which already each keep their own for the same reason): those files are unit-tested
 *  with jest.mock() elsewhere in this component tree, and mocking a module's default export
 *  wipes its named exports too, so importing a named helper from a sibling UI component that
 *  other tests mock out is fragile. Kept in sync with the other two copies by convention. */
function parseAddressComponents(components: any[] = []): ParsedAddress {
  const fullAddress: string[] = [];
  const result: ParsedAddress = { address: "", city: "", state: "", country: "", zipCode: "" };
  components.forEach((c: any) => {
    const types: string[] = c.types || [];
    if (
      types.includes("street_number") || types.includes("route") ||
      types.includes("premise") || types.includes("subpremise") ||
      types.includes("neighborhood") || types.includes("sublocality") ||
      types.includes("sublocality_level_1") || types.includes("sublocality_level_2")
    ) {
      fullAddress.push(c.long_name);
    }
    if (!result.city && (types.includes("locality") || types.includes("postal_town") || types.includes("administrative_area_level_2"))) {
      result.city = c.long_name;
    }
    if (!result.state && types.includes("administrative_area_level_1")) result.state = c.long_name;
    if (!result.country && types.includes("country")) result.country = c.long_name;
    if (!result.zipCode && types.includes("postal_code")) result.zipCode = c.long_name;
  });
  result.address = fullAddress.join(" ").trim();
  return result;
}

interface Props {
  /** Asset location as "lat,lng" (from assetData.location), may be empty. */
  location?: string;
  /** Stable identity of the asset being edited (assetUid/id, or "new" for create).
   *  When it changes, the RoofLens store is reset so one asset's measurements can
   *  never leak into another within the same SPA session. */
  assetKey?: string;
  /** Best-known property address (used to seed search + exports). */
  address?: string;
  /** Property country — included in the exported measurement JSON. */
  country?: string;
  /** Called on address search so the Property Details fields auto-fill (same
   *  contract as the old AssetLocationPicker's onChange). */
  onLocationPicked?: (locStr: string, parsed?: ParsedAddress) => void;
  /** Called whenever the measured total roof area changes (SAM2 result or manual
   *  facet edits) so the "Area in square feet" field can live-update. `opts.isAutoDetect`
   *  is true ONLY when this change came from a fresh Auto-Detect run replacing the
   *  facet list — never for a manual per-facet edit (add/adjust/delete/pitch/roof-area
   *  override) — so the caller can guard against Auto-Detect overwriting an
   *  already-answered field while still always propagating a human's own facet edit. */
  onAreaChange?: (totalSqft: number, opts?: { isAutoDetect?: boolean }) => void;
  /** Called after an Auto-Detect run yields EagleView-style metrics (edges,
   *  attic, stories, height…) so matching asset fields can best-effort auto-fill. */
  onMetricsComputed?: (metrics: RoofMetrics) => void;
  /** Called whenever the finalized measurement changes so the parent can persist
   *  it (asset.measurement_data). Passes null once there are no facets. */
  onMeasurementChange?: (data: RoofLensExportData | null) => void;
  /** Previously-saved measurement JSON (asset.measurementData). Restored into the
   *  editor on load when there are no live facets yet. */
  savedMeasurement?: string;
  /** AB#28854 — bearer token for the address/property validation call (a Java tenant-api
   *  endpoint, unlike every other RoofLensService call in this file, which hits the separate
   *  RoofLens gateway). Validation is skipped (fails open, same as an UNVERIFIED result) when
   *  this is absent, so omitting it is safe but disables the check. */
  accessToken?: string;
  /** AB#28854 — reports the outcome of the picker-level address/property validation so the
   *  parent (CreateAsset_v3) can track it too. `reason` is a human-readable message only when
   *  `ok` is false or the check couldn't complete. */
  onAddressValidated?: (ok: boolean, reason?: string) => void;
}

const DEFAULT_CENTER: LatLng = { lat: 39.8283, lng: -98.5795 }; // continental-US centroid

// Reset the RoofLens store only once per page session (see the mount effect
// below). Survives the panel's transient remounts so measurements/overlays are
// never wiped by a view toggle; a full page navigation reloads the module and
// clears it, giving a fresh editor for a genuinely new session.
// The asset the RoofLens store was last reset for. Module scope so it SURVIVES the
// panel's transient unmount/remount (e.g. the 3D View toggle) — but still detects a
// genuine asset switch within the same SPA session and re-resets.
let _roofLensAssetKey: string | undefined;

// The asset RoofLens has already auto-detected for. Module scope (like the reset key)
// so opening an asset for edit auto-measures ONCE — never again on the 3D-toggle
// remount, and never re-firing after the user manually clears/edits the facets.
let _roofLensAutoDetectedKey: string | undefined;

const RoofLensAssetMap: React.FC<Props> = ({ location, address, country, assetKey, onLocationPicked, onAreaChange, onMetricsComputed, onMeasurementChange, savedMeasurement, accessToken, onAddressValidated }) => {
  const { t } = useTranslation();
  const { isLoaded } = useGoogleMaps();
  // Human-correction-priority fix: re-running Auto-Detect on a building that already
  // has manually-drawn or manually-adjusted facets (!isAuto || edited) would otherwise
  // silently delete and replace them with a fresh detection — only Ctrl+Z recovered it,
  // with no warning. Gates runSam2's facet replacement behind this confirmation whenever
  // that's the case; `target` is remembered so confirming resumes the exact same call.
  const [confirmReplace, setConfirmReplace] = React.useState<{ open: boolean; target?: LatLng; buildingId?: string }>({ open: false });
  // Human-correction-priority regression fix: distinguishes "this facets change came
  // from a fresh Auto-Detect run" (which must not silently overwrite an already-answered
  // Area field) from any OTHER facets change — manual add/adjust/delete/pitch/roof-area
  // edits, which are themselves human corrections and must always propagate live, exactly
  // as they did before the Auto-Detect guard existed. Set true right before the facets
  // commit in runSam2's success path; consumed and reset by the onAreaChange effect below
  // the very next time it runs.
  const justAutoDetectedRef = React.useRef(false);
  const parsed = parseLatLngStr(location);
  // AB#28854 — off by default (FeatureFlag.ts); flag OFF must take the exact pre-existing code
  // path, unchanged, everywhere this is checked below.
  const { value: addressValidationEnabled } = useFlag("rooflens-address-validation", false);

  const [center, setCenter] = React.useState<LatLng>(parsed ?? DEFAULT_CENTER);
  const [hasLocation, setHasLocation] = React.useState<boolean>(!!parsed);
  const [addr, setAddr] = React.useState<string>(address ?? "");
  // Separate from the store's `status` (rendered only inside the hasLocation branch, and always
  // severity="info") — a rejected pick never sets hasLocation, so this has to live outside that
  // branch and be visually distinct (severity="error") from an informational SAM2 status line.
  const [validationError, setValidationError] = React.useState<string | null>(null);
  // Also separate from the store's `status`: an UNVERIFIED accept-with-warning must survive
  // runSam2, which unconditionally clears `status` (setStatus(null)) synchronously at its own
  // start — the exact same call that would otherwise wipe this warning before a single frame
  // renders it, since handleSearchPick calls runSam2 right after a successful validation.
  const [addressWarning, setAddressWarning] = React.useState<string | null>(null);
  const [validating, setValidating] = React.useState(false);
  // Best-known country for exports: form value → searched value → reverse-geocoded pin.
  const [resolvedCountry, setResolvedCountry] = React.useState<string>(country ?? "");
  React.useEffect(() => { if (country) setResolvedCountry(country); }, [country]);
  // Current 2D-map viewport center — lets the Auto-Detect button measure whatever
  // building the user has panned to (e.g. a detached garage = Building 2), not
  // only the originally-searched address.
  const [viewportCenter, setViewportCenter] = React.useState<LatLng | null>(null);
  // 2D "Measure & Edit" is the default (auto-detect / draw / adjust facets);
  // switch to "3d" for the photorealistic view.
  const [view, setView] = React.useState<"3d" | "2d">("2d");

  const facets = useRoofLensStore((s) => s.facets);
  const buildings = useRoofLensStore((s) => s.buildings);
  const metricsByBuilding = useRoofLensStore((s) => s.metricsByBuilding);
  const hydrateFromExport = useRoofLensStore((s) => s.hydrateFromExport);
  const setFacets = useRoofLensStore((s) => s.setFacets);
  const setTool = useRoofLensStore((s) => s.setTool);
  const setMetrics = useRoofLensStore((s) => s.setMetrics);
  const setLoading = useRoofLensStore((s) => s.setLoading);
  const setStatus = useRoofLensStore((s) => s.setStatus);
  const loading = useRoofLensStore((s) => s.loading);
  const status = useRoofLensStore((s) => s.status);
  const reset = useRoofLensStore((s) => s.reset);

  // Current asset identity in a ref so an in-flight detection can notice a mid-request
  // asset switch and discard its (now stale) result instead of writing it into the new asset.
  const assetKeyRef = React.useRef(assetKey);
  assetKeyRef.current = assetKey;

  // Detection supersession: every runSam2 call takes the next id and becomes "the latest
  // request". A detection that returns after a newer one started (e.g. the user changed
  // the address mid-detection) sees its id is stale and discards its result — so an old
  // address's roof can never commit over a newer one. `pendingTargetRef` holds a target
  // requested while a detection was already in flight, so it runs as soon as the slot frees.
  const runIdRef = React.useRef(0);
  const pendingTargetRef = React.useRef<LatLng | undefined>(undefined);

  // The `location|address` the auto-detect-on-load effect last actually processed (claimed as
  // already-measured, or committed to detecting) — lets that effect tell "facets exist because
  // this asset already has saved measurements" apart from "facets exist but the address changed
  // SINCE they were committed" (e.g. a manual search, then applying a different billing address).
  // Per-instance, not module-level — must be reset on a genuine asset switch below, since this
  // component does not remount between assets.
  const lastAutoDetectAddrRef = React.useRef<string | null>(null);

  // The `lat,lng` string of the most recent successful pick (search-bar pick, Move Pin click or
  // drag). handleSearchPick already fully handles a pick (validate, recenter, set the search text,
  // clear facets, run detection) and feeds the same string back up via onLocationPicked, so the
  // parent hands it back as the `location` prop. The auto-detect effect below uses this to tell
  // that echo apart from a location applied programmatically — see the claim at its top.
  const lastPickedLocationRef = React.useRef<string | null>(null);

  // Reset the store when the ASSET changes — not on every mount. This app's asset editor
  // is one SPA route, so opening a different asset does NOT remount this component; keying
  // the reset on asset identity stops one asset's facets/measurements from leaking into the
  // next. It still survives the panel's transient 3D-toggle remount for the SAME asset
  // (identity unchanged → no reset), preserving in-progress work.
  React.useEffect(() => {
    if (_roofLensAssetKey !== assetKey) {
      _roofLensAssetKey = assetKey;
      lastAutoDetectAddrRef.current = null;
      lastPickedLocationRef.current = null;
      reset();
      // PR 8396 review (Raja) — this component doesn't remount between assets (see comment
      // above), so its own local location state must be reset here too, or a previous asset's
      // hasLocation/center/addr leaks into the next one. This matters more than it used to now
      // that the 3D view sits under the same `!hasLocation` gate as the 2D map: without this, a
      // user landing on an address-less asset with the 3D tab selected would immediately see the
      // PREVIOUS asset's location rendered as if it were this one's.
      setHasLocation(false);
      setCenter(DEFAULT_CENTER);
      setAddr(address ?? "");
      setValidationError(null);
      setAddressWarning(null);
    }
  }, [assetKey, reset, address]);

  // Live-report the total roof area whenever facets change (SAM2 result or manual
  // add/adjust/delete/pitch) so the "Area in square feet" field stays in sync.
  // Only report once there's an actual measurement, so mounting/reset (empty
  // facets) never wipes an existing area value.
  React.useEffect(() => {
    if (!onAreaChange || facets.length === 0) return;
    const isAutoDetect = justAutoDetectedRef.current;
    justAutoDetectedRef.current = false; // consume — only the run that set it is tagged
    onAreaChange(computeTotals(facets).totalRoofAreaSqft, { isAutoDetect });
  }, [facets, onAreaChange]);

  // Live-report the finalized measurement export whenever the underlying store
  // slices (facets / buildings / metrics) or the address/country/center change, so
  // the parent can persist it on save. Read the store via getState() so the export
  // always reflects the freshest slices. Passes null once there are no facets, so
  // clearing the measurement clears the stored value too. Mirrors onAreaChange.
  React.useEffect(() => {
    if (!onMeasurementChange) return;
    const s = useRoofLensStore.getState();
    if (s.facets.length === 0) {
      onMeasurementChange(null);
      return;
    }
    onMeasurementChange(
      buildExportData(addr, resolvedCountry || "", center.lat, center.lng, s.buildings, s.facets, s.metricsByBuilding)
    );
  }, [facets, buildings, metricsByBuilding, addr, resolvedCountry, center, onMeasurementChange]);

  // Keep the map centered on the asset's pin if it arrives/changes after mount.
  React.useEffect(() => {
    const p = parseLatLngStr(location);
    if (p) {
      setCenter(p);
      setHasLocation(true);
    }
  }, [location]);

  // `target` lets an address search kick off detection immediately with the
  // just-picked coordinates, without waiting for the `center` state to settle.
  // Guard the shape: the toolbar wires this to a button onClick, which would
  // otherwise pass a MouseEvent as `target` (→ NaN lat/lng → 422).
  const runSam2 = async (target?: LatLng, bypassReplaceConfirm?: boolean) => {
    // Ask before wiping manual work. Skipped when bypassReplaceConfirm is set (the
    // confirm dialog's own "continue" resumes here), and naturally a no-op for a
    // fresh address search, which already clears facets to [] before calling this
    // (handleSearchPick) — nothing manual exists yet to lose.
    if (!bypassReplaceConfirm) {
      const activeBuildingId = useRoofLensStore.getState().activeBuildingId;
      const hasManualWork = useRoofLensStore.getState().facets
        .some((f) => f.buildingId === activeBuildingId && (!f.isAuto || f.edited));
      if (hasManualWork) {
        // Pin the building this confirmation is FOR — adversarial-review finding:
        // without this, switching the active building while the dialog is still
        // open (e.g. via the building bar) would let "Continue" silently replace
        // a DIFFERENT building's facets than the one the user actually confirmed
        // for, since resuming below would simply re-read whatever building is
        // active at click time. Re-checked on confirm.
        setConfirmReplace({ open: true, target, buildingId: activeBuildingId });
        return;
      }
    }
    const validTarget =
      !!target && typeof target.lat === "number" && typeof target.lng === "number";
    // From the button (no target) → measure the current map viewport (pan to any
    // building); from a search → the picked coords.
    const c = validTarget ? (target as LatLng) : viewportCenter ?? center;
    // Defensive precondition. The only caller that can pass an invalid target is the
    // toolbar's Auto-Detect button (it forwards a MouseEvent), and that button is only
    // mounted once hasLocation is already true — so this branch is not reachable from the
    // UI. Kept as a guard against direct/edge callers; excluded from coverage accordingly.
    /* istanbul ignore next */
    if (!validTarget && !hasLocation) {
      setStatus("Set the property location first — search an address above.");
      return;
    }
    // This call is now the newest request; any in-flight detection is thereby superseded
    // and will discard its result when it returns.
    const myRunId = ++runIdRef.current;
    // The loading slot is global (one detection/export at a time). If it's busy, don't
    // stomp it — remember this target and run it the moment the current op finishes.
    // (Fixes: picking a new address mid-detection used to be dropped silently, and the
    // old address's result then committed over the new pin.)
    if (useRoofLensStore.getState().loading.active) {
      pendingTargetRef.current = c;
      return;
    }
    const opKey = assetKeyRef.current; // capture asset identity for the stale-result guard
    const opBuildingId = useRoofLensStore.getState().activeBuildingId; // and the target building
    setStatus(null);
    setLoading(true, "Analyzing roof…");
    try {
      const res = await fetchFacets(c.lat, c.lng);
      // Discard if a newer request superseded this one, or the asset/active building
      // changed during the fetch — otherwise this detection's result would commit onto
      // the wrong roof (setFacets/setMetrics write to activeBuildingId at commit time).
      if (runIdRef.current !== myRunId) return;
      if (assetKeyRef.current !== opKey ||
          useRoofLensStore.getState().activeBuildingId !== opBuildingId) return;
      if (res.status === "no_building" || !res.facets || res.facets.length === 0) {
        setFacets([]);
        setMetrics(null);
        setStatus(res.message || "No rooftop found here — check the address or draw facets manually with Add Area.");
        return;
      }
      const perFacet = res.metrics?.per_facet;
      const mapped: RoofFacet[] = res.facets.map((f, i) => {
        const pf = perFacet?.[i];
        return {
          id: nextFacetId(),
          buildingId: "", // set by the store to the active building
          path: (f.boundary || []).map(([lat, lng]) => ({ lat, lng })),
          pitchRise: parsePitchRise(f.pitch_ratio),
          isAuto: true,
          serviceAreaSqft: typeof f.area_sqft === "number" ? f.area_sqft : null,
          edited: false,
          // Attach this facet's own edge breakdown so it stays with the facet
          // through deletes/edits of other rows.
          edgeMetrics: pf
            ? {
                ridge_ft: pf.ridge_ft ?? 0,
                hip_ft: pf.hip_ft ?? 0,
                valley_ft: pf.valley_ft ?? 0,
                rake_ft: pf.rake_ft ?? 0,
                eave_ft: pf.eave_ft ?? 0,
                parapet_ft: pf.parapet_ft ?? 0,
              }
            : undefined,
        };
      });
      // Tag the facets change this triggers as Auto-Detect-sourced BEFORE committing it,
      // so the onAreaChange effect below can tell it apart from a manual facet edit.
      justAutoDetectedRef.current = true;
      setFacets(mapped);
      // EagleView-style metrics (edges/attic/stories/height): store for the panel
      // and best-effort auto-fill of matching asset fields. Present only when the
      // backend could assemble a structure; older backends simply omit it.
      const metrics = res.metrics ?? null;
      setMetrics(metrics);
      if (metrics) onMetricsComputed?.(metrics);
      setTool("view");
    } catch (e: any) {
      // Only surface the error if this run is still the latest — a superseded run's
      // failure is irrelevant (the newer detection will report its own outcome).
      if (runIdRef.current === myRunId) {
        setStatus(e?.message ? `Measurement failed: ${e.message}` : "Measurement failed — the RoofLens service may be unavailable.");
      }
    } finally {
      setLoading(false);
      // If a new target was requested while this detection was in flight (e.g. the user
      // changed the address mid-detection), run it now — its facets replace the ones just
      // discarded, so the map always ends on the roof for the address the user last chose.
      const next = pendingTargetRef.current;
      if (next) {
        pendingTargetRef.current = undefined;
        // A queued continuation of a request already underway, not a fresh user
        // click — the confirm gate (if applicable) already ran when that request
        // was first made, so don't ask again here without a new user gesture.
        runSam2(next, true);
      }
    }
  };

  // AB#28854 — supersede guard for handleSearchPick's own async validation round-trip (the same
  // pattern runSam2 already uses via runIdRef, applied one layer earlier): without this, picking
  // address A then quickly picking address B could have A's slower validation commit AFTER B's —
  // silently overwriting B's already-committed center/address/form fields with A's, while B's
  // facets (guarded separately by runIdRef) stay on screen. A fieldcraft-reviewer finding.
  const pickRunIdRef = React.useRef(0);

  // AB#28854 — confirms `pickedAddress` is real/deliverable AND has an actual rooftop before the
  // caller commits it to map/form state. Returns true when the pick should proceed (accepted, or
  // the flag is off, or accessToken is unavailable, or this call was superseded by a newer pick),
  // false when it must be rejected. Fail-open per the confirmed decision: `accepted` covers both a
  // genuine pass and an UNVERIFIED (couldn't check) outcome, distinguished only by `message`.
  const checkPropertyValid = async (pickedAddress: string, myPickRunId: number): Promise<boolean> => {
    if (!addressValidationEnabled || !accessToken) return true;
    setValidating(true);
    setValidationError(null);
    setAddressWarning(null);
    try {
      const res = await validatePropertyAddress(pickedAddress, undefined, undefined, undefined, accessToken);
      if (pickRunIdRef.current !== myPickRunId) return false; // superseded — a newer pick owns the UI now
      const result: PropertyValidationResult | undefined = res?.ok ? res.data : undefined;
      if (!result) {
        // The validation CALL itself failed (network/5xx/auth) — same fail-open treatment as a
        // server-reported UNVERIFIED result: accept, but warn rather than pretending it passed.
        setAddressWarning("Could not verify this address right now — it was accepted, but please double-check it.");
        onAddressValidated?.(true, "Could not verify this address right now.");
        return true;
      }
      if (!result.accepted) {
        setValidationError(result.message || "This address was rejected — no rooftop found here.");
        onAddressValidated?.(false, result.message ?? undefined);
        return false;
      }
      if (result.message) {
        // Accepted, but one of the two checks was UNVERIFIED — non-blocking warning.
        setAddressWarning(result.message);
      }
      onAddressValidated?.(true, result.message ?? undefined);
      return true;
    } catch (e) {
      if (pickRunIdRef.current !== myPickRunId) return false;
      console.warn("RoofLens: address validation call failed; accepting (fail-open)", e);
      setAddressWarning("Could not verify this address right now — it was accepted, but please double-check it.");
      onAddressValidated?.(true, "Could not verify this address right now.");
      return true;
    } finally {
      if (pickRunIdRef.current === myPickRunId) setValidating(false);
    }
  };

  const handleSearchPick = async (lat: number, lng: number, pickedAddress: string, parsedAddr?: ParsedAddress) => {
    const myPickRunId = ++pickRunIdRef.current; // this pick is now the newest; a stale one bows out
    const isValid = await checkPropertyValid(pickedAddress, myPickRunId);
    if (!isValid) return; // rejected, or superseded by a newer pick — map/form state untouched

    const pickedLocStr = `${lat},${lng}`;
    lastPickedLocationRef.current = pickedLocStr; // before the parent echoes it back as `location`
    setCenter({ lat, lng });
    setHasLocation(true);
    setAddr(pickedAddress);
    if (parsedAddr?.country) setResolvedCountry(parsedAddr.country);
    // Feed the Property Details fields (same behaviour as the old location picker).
    onLocationPicked?.(pickedLocStr, parsedAddr);
    // A new address means the previous roof's facets/metrics no longer apply — clear them
    // immediately so a stale overlay never lingers (especially if this detection has to
    // queue behind one that's still in flight).
    setFacets([]);
    setMetrics(null);
    // Auto-measure the located roof — no need to click Auto-Detect. Show the 2D
    // measuring view and run detection immediately with the just-picked coords.
    setView("2d");
    runSam2({ lat, lng });
  };

  // Manual pin placement — dragging the locator pin or clicking the map (RoofLensMap,
  // view mode only). Reverse-geocodes the dropped point and feeds it through the exact
  // same handleSearchPick pipeline a real address-search pick uses, so it gets the same
  // address-fill/facet-clear/re-detect behaviour and the same confirm-before-replace
  // protection for manually-edited facets — no separate path to keep in sync.
  //
  // Supersede guard: reverse-geocoding is async, so a fast second drag/click could have
  // its result arrive BEFORE a slower first one's — without this, the stale first pick
  // would land last and silently override the newer one. Same "only the newest attempt
  // wins" pattern as pickRunIdRef/searchGenerationRef elsewhere in this tree.
  const manualPinGenerationRef = React.useRef(0);
  // This callback is memoised with [] (RoofLensMap's click/drag handlers must stay stable), so it
  // would freeze the FIRST render's handleSearchPick — and with it the first render's
  // onLocationPicked, accessToken and validation flag. Always call through a ref to the latest.
  const handleSearchPickRef = React.useRef(handleSearchPick);
  handleSearchPickRef.current = handleSearchPick;
  const handleManualPinMove = React.useCallback((c: LatLng) => {
    const myGen = ++manualPinGenerationRef.current;
    if (!isGoogleMapsGeocoderReady()) {
      handleSearchPickRef.current(c.lat, c.lng, `${c.lat.toFixed(6)}, ${c.lng.toFixed(6)}`);
      return;
    }
    new google.maps.Geocoder().geocode({ location: { lat: c.lat, lng: c.lng } }, (results, status) => {
      if (manualPinGenerationRef.current !== myGen) return; // superseded by a newer drag/click
      if (status === "OK" && results?.[0]) {
        handleSearchPickRef.current(c.lat, c.lng, results[0].formatted_address || `${c.lat.toFixed(6)}, ${c.lng.toFixed(6)}`,
          parseAddressComponents(results[0].address_components || []));
      } else {
        // Fail-open, same as checkPropertyValid elsewhere here: the pin still moves even
        // when reverse geocoding can't resolve an address for it.
        handleSearchPickRef.current(c.lat, c.lng, `${c.lat.toFixed(6)}, ${c.lng.toFixed(6)}`);
      }
    });
  }, []);

  // Restore a previously-saved measurement on load: if the asset has stored JSON and
  // the store has no facets yet, rebuild the editor from it instead of re-running
  // Auto-Detect. Claiming the auto-detect key here stops the auto-detect effect below
  // from firing and overwriting the restored measurement. Runs before that effect
  // (declared first). Malformed JSON is ignored (warn, no crash).
  React.useEffect(() => {
    if (!savedMeasurement) return;
    if (useRoofLensStore.getState().facets.length > 0) return; // live work present — don't clobber
    try {
      const data = JSON.parse(savedMeasurement) as RoofLensExportData;
      if (!data || !Array.isArray(data.buildings) || data.buildings.length === 0) return;
      hydrateFromExport(data);
      if (typeof data.lat === "number" && typeof data.lng === "number" &&
          Number.isFinite(data.lat) && Number.isFinite(data.lng)) {
        setCenter({ lat: data.lat, lng: data.lng });
        setHasLocation(true);
      }
      // Claim the auto-detect slot so the effect below is a no-op for this asset and
      // can't overwrite the restored facets/metrics.
      _roofLensAutoDetectedKey = assetKey;
    } catch (e) {
      console.warn("RoofLens: could not restore saved measurement", e);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedMeasurement, assetKey]);

  // Auto-detect on edit-load: when an asset opens (Edit Asset) with a saved pin and no
  // measurements yet, kick off detection automatically — same as if the user had searched
  // the address, so they don't have to re-click Auto-Detect. Fires ONCE per asset:
  // guarded by the module key (survives the 3D-toggle remount) and skipped once facets
  // exist, so a manual clear/edit is never overwritten.
  React.useEffect(() => {
    const currentAddrKey = `${location ?? ""}|${address ?? ""}`;
    // A `location` the user just picked is already fully handled by handleSearchPick (validate, recentre,
    // search text, detection), so claim it and stop. The parent echoes the pick back as `location`, but its
    // `address` prop only follows when the pick carried an address (otherwise it stays at the saved one),
    // so the key below can read "new location | old address", look like a programmatic change and — once
    // detection ends (facets.length 0→N) — wipe the fresh facets, re-detect and rewrite the search box.
    // A programmatic change (the billing checkbox clears `location`) normally differs from the last pick.
    if (location && location === lastPickedLocationRef.current) {
      _roofLensAutoDetectedKey = assetKey;
      lastAutoDetectAddrRef.current = currentAddrKey;
      return;
    }
    if (_roofLensAutoDetectedKey === assetKey && lastAutoDetectAddrRef.current === currentAddrKey) {
      return;                                                // already auto-detected THIS exact address for this asset
    }
    if (facets.length > 0) {
      if (lastAutoDetectAddrRef.current === null) {
        // First time this effect has run for this asset and facets already exist — a real
        // edit-load with a previously-saved measurement (hydrateFromExport ran before this
        // effect). Claim without re-detecting, matching the original "fires once per asset" intent.
        _roofLensAutoDetectedKey = assetKey;
        lastAutoDetectAddrRef.current = currentAddrKey;
        return;
      }
      if (lastAutoDetectAddrRef.current === currentAddrKey) return; // facets exist, address unchanged — nothing to do
      // Real bug fix: `location`/`address` genuinely changed SINCE these facets were
      // committed (e.g. the user manually searched an address, THEN checked "Use customer
      // billing address as asset address" — a real, live-reported scenario). The old blanket
      // "facets.length > 0 => already handled, bail" check couldn't tell that apart from "an
      // asset that already has saved measurements from a prior edit session," and silently did
      // nothing — no search-bar update, no new pin, no re-detection — for the new address.
      // The old facets describe the OLD address; clear them so the fresh Auto-Detect below
      // runs for the new one, exactly as a manual search already does via handleSearchPick.
      setFacets([]);
      setMetrics(null);
    }
    if (useRoofLensStore.getState().loading.active) return;  // detection already running

    const pin = parseLatLngStr(location);                    // saved lat/lng, if the asset has one
    const savedAddr = (address ?? "").trim();                // else fall back to the stored address
    if (!pin && !savedAddr) return;                          // nothing to auto-detect from
    // Address-only asset (no saved pin — e.g. created via the lead/customer quick form): we
    // must geocode the address, which needs the Google Maps API. Wait until it's loaded; this
    // effect re-runs when `isLoaded` flips true. Don't claim the once-key until we succeed, so
    // the retry can still fire.
    if (!pin && !isLoaded) return;

    let cancelled = false;
    (async () => {
      let target = pin;
      if (!target && savedAddr) {
        // Prefer the browser's Google geocoder (no RoofLens-backend dependency); fall back to
        // the RoofLens /api/geocode service only if the Maps API isn't usable.
        if (isLoaded && typeof google !== "undefined" && google.maps?.Geocoder) {
          target = await new Promise<LatLng | null>((resolve) => {
            new google.maps.Geocoder().geocode({ address: savedAddr }, (results, status) => {
              if (status === "OK" && results?.[0]?.geometry?.location) {
                const loc = results[0].geometry.location;
                resolve({ lat: loc.lat(), lng: loc.lng() });
              } else resolve(null);
            });
          });
        }
        if (!target) {
          try {
            const g = await geocodeAddress(savedAddr);
            if (g && Number.isFinite(g.lat) && Number.isFinite(g.lng)) target = { lat: g.lat, lng: g.lng };
          } catch {
            // Both geocoders failed — leave the map on the address input; user can search manually.
          }
        }
      }
      if (!target || cancelled) return;
      if (assetKeyRef.current !== assetKey) return;          // asset switched while geocoding — discard

      // AB#28854 — validate an already-saved address the same way a fresh search-pick is
      // validated, so a bad address that slipped in before this feature (or via the legacy
      // handleSubmit save path) surfaces the same warning on edit-load. Claim the once-key on
      // EITHER outcome here (unlike the success-only claim above) — a rejected saved address
      // must not re-run this expensive geocode+validate pair on every re-render this effect's
      // own deps (facets.length, isLoaded) can trigger.
      //
      // Unlike a fresh search-pick, a REJECTED result here does not hide the map: this address
      // was already persisted (possibly before this feature existed, or via the legacy
      // handleSubmit path) — the record already exists and the user did not just do anything
      // wrong, so suppressing their already-saved location would be a display regression on
      // existing data, not a validation gate on new input (fieldcraft-reviewer finding). It still skips
      // the paid SAM2 auto-detect call on a flagged-bad address, and still shows the warning
      // banner, but the pin/map itself is shown.
      let skipAutoDetect = false;
      if (savedAddr) {
        const myPickRunId = ++pickRunIdRef.current;
        const isValid = await checkPropertyValid(savedAddr, myPickRunId);
        if (cancelled || assetKeyRef.current !== assetKey) return;
        _roofLensAutoDetectedKey = assetKey;
        lastAutoDetectAddrRef.current = currentAddrKey;
        // A user pick took over while we validated (a pick bumps pickRunIdRef; a genuine rejection does NOT):
        // it owns the search text and detection, so never write the saved address over it. Show the saved pin
        // only if no pick has committed (a committed pick already recentred the map).
        if (pickRunIdRef.current !== myPickRunId) {
          if (lastPickedLocationRef.current === null) { setCenter(target); setHasLocation(true); }
          return;
        }
        skipAutoDetect = !isValid;
      } else {
        _roofLensAutoDetectedKey = assetKey;                 // no address to validate against (pin-only asset)
        lastAutoDetectAddrRef.current = currentAddrKey;
      }
      setCenter(target);
      setHasLocation(true);
      // Reflect the resolved address in the search box (same as a fresh manual pick) — otherwise
      // an address applied programmatically (e.g. "Use customer billing address as asset address")
      // moves the pin/runs detection but leaves the search box showing stale/blank text.
      if (savedAddr) setAddr(savedAddr);
      setView("2d");
      if (!skipAutoDetect) runSam2(target);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location, address, assetKey, facets.length, isLoaded]);

  // Country for exports: use the known value; else reverse-geocode the pin (lat/lng)
  // so exports carry a country even when the form field was never filled.
  const resolveCountryForExport = async (): Promise<string> => {
    if (resolvedCountry) return resolvedCountry;
    if (!isLoaded || typeof google === "undefined" || !google.maps?.Geocoder) return "";
    try {
      const found = await new Promise<string>((resolve) => {
        new google.maps.Geocoder().geocode(
          { location: { lat: center.lat, lng: center.lng } },
          (results, status) => {
            if (status === "OK" && results && results.length) {
              const comp = results[0].address_components?.find((c) => c.types.includes("country"));
              resolve(comp?.long_name ?? "");
            } else {
              resolve("");
            }
          }
        );
      });
      if (found) setResolvedCountry(found);
      return found;
    } catch {
      return "";
    }
  };

  const handleExportJson = async () => {
    if (useRoofLensStore.getState().loading.active) return; // one operation at a time
    const s = useRoofLensStore.getState();
    const ctry = await resolveCountryForExport();
    exportJson(buildExportData(addr, ctry, center.lat, center.lng, s.buildings, s.facets, s.metricsByBuilding));
  };

  const handleExportPdf = async () => {
    if (useRoofLensStore.getState().loading.active) return; // one operation at a time
    const s = useRoofLensStore.getState();
    const current = s.facets;
    const totals = computeTotals(current);
    const ctry = await resolveCountryForExport();
    setLoading(true, "Building report…");
    let blueprint: BlueprintResponse | null = null;
    try {
      blueprint = await fetchBlueprint({
        lat: center.lat,
        lng: center.lng,
        address: addr,
        facets: current.map((f) => ({
          boundary: f.path.map((p) => [p.lat, p.lng] as [number, number]),
          area_sqft: totals.facets.find((m) => m.id === f.id)?.roofAreaSqft ?? 0,
          pitch: `${f.pitchRise}/12`,
        })),
      });
    } catch (e) {
      // Blueprint is best-effort, but surface a non-blocking notice so a real
      // network/auth/5xx failure isn't mistaken for "no diagrams" — the export
      // still proceeds without the diagram images.
      console.warn("RoofLens: blueprint diagram fetch failed; exporting without diagrams", e);
      setStatus("Diagram images unavailable — exporting the report without them.");
    } finally {
      setLoading(false);
    }
    // Re-snapshot immediately before rendering — fetchBlueprint can await up to 5 min,
    // during which the user may edit facets; reading the store again ensures those edits
    // land in the PDF instead of being silently dropped by a pre-await snapshot.
    const sNow = useRoofLensStore.getState();
    const data = buildExportData(addr, ctry, center.lat, center.lng, sNow.buildings, sNow.facets, sNow.metricsByBuilding);
    exportPdf(data, blueprint);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="body2" color="text.secondary">
          Measure this roof from imagery — auto-detect or draw facets manually.
        </Typography>
        <Chip
          icon={<ScienceOutlinedIcon />}
          label="Experimental · internal"
          color="warning"
          size="small"
          variant="outlined"
        />
      </Stack>

      {!isLoaded ? (
        <Box
          sx={{
            height: 380,
            borderRadius: "8px",
            border: "1px dashed",
            borderColor: "grey.300",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography variant="caption" color="text.secondary">
            Loading map…
          </Typography>
        </Box>
      ) : (
        <>
          <Box sx={{ mb: 1 }}>
            <RoofLensSearchBar initialText={addr} onPick={handleSearchPick} />
          </Box>

          {/* AB#28854 — rendered here, OUTSIDE the hasLocation branch below: a rejected pick
              never sets hasLocation, so the store's own `status` Alert (which lives inside that
              branch) would never show a rejection. */}
          {validationError && (
            <Alert severity="error" sx={{ mb: 1 }} onClose={() => setValidationError(null)}>
              {validationError}
            </Alert>
          )}
          {/* AB#28854 — a dedicated state, not the store's `status`: runSam2 (called right after a
              successful validation) unconditionally clears `status` at its own start, which would
              wipe this warning before it ever rendered. */}
          {addressWarning && (
            <Alert severity="warning" sx={{ mb: 1 }} onClose={() => setAddressWarning(null)}>
              {addressWarning}
            </Alert>
          )}
          {validating && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
              Verifying this address…
            </Typography>
          )}

          <ToggleButtonGroup
            size="small"
            exclusive
            value={view}
            onChange={(_e, v) => { if (v) setView(v); }}
            sx={{ mb: 1 }}
          >
            <ToggleButton value="3d" sx={{ textTransform: "none", gap: 0.5 }}>
              <ViewInArIcon fontSize="small" /> 3D View
            </ToggleButton>
            <ToggleButton value="2d" sx={{ textTransform: "none", gap: 0.5 }}>
              <GridOnIcon fontSize="small" /> Measure &amp; Edit
            </ToggleButton>
          </ToggleButtonGroup>

          {!hasLocation ? (
            // Neither view is useful without a located property — don't show the 2D map on
            // empty ground, and don't show the 3D view's globe default either (both would
            // leave the user staring at something with no way to tell what to do next).
            // Same placeholder regardless of which toggle is selected.
            <Box
              sx={{
                height: 380,
                borderRadius: "8px",
                border: "1px dashed",
                borderColor: "grey.300",
                bgcolor: "grey.50",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 1,
                textAlign: "center",
                px: 3,
              }}
            >
              <MapOutlinedIcon sx={{ fontSize: 40, color: "text.disabled" }} />
              <Typography variant="body2" color="text.secondary">
                Search a property address above to load the roof map and start measuring.
              </Typography>
            </Box>
          ) : view === "3d" ? (
            <Box sx={{ border: "1px solid #e2e8f0", borderRadius: "8px", overflow: "hidden" }}>
              <RoofLens3DView center={center} hasLocation={hasLocation} />
            </Box>
          ) : (
            <>
              <RoofLensBuildingBar />
              <Box sx={{ border: "1px solid #e2e8f0", borderRadius: "8px", overflow: "hidden" }}>
                <RoofLensToolbar onSam2={runSam2} busy={loading.active} />
                <Box sx={{ position: "relative" }}>
                  <RoofLensMap center={center} hasLocation={hasLocation} onViewportCenter={setViewportCenter} onManualPinMove={handleManualPinMove} />
                  <RoofLensLoadingOverlay active={loading.active} label={loading.label} />
                </Box>
              </Box>

              {status && (
                <Alert severity="info" sx={{ mt: 1 }}>
                  {status}
                </Alert>
              )}

              <RoofLensMeasurements onExportJson={handleExportJson} onExportPdf={handleExportPdf} />
            </>
          )}

          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
            Decision-support estimate — not a quote or survey. Roof measurements are experimental and for internal
            planning only.
          </Typography>
        </>
      )}

      <Dialog open={confirmReplace.open} onClose={() => setConfirmReplace({ open: false })} maxWidth="sm" fullWidth>
        <DialogTitle>{t("asset.rooflens.confirmReplace.title")}</DialogTitle>
        <DialogContent>
          <Typography variant="body2">{t("asset.rooflens.confirmReplace.body")}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmReplace({ open: false })}>{t("common.cancel")}</Button>
          <Button
            variant="contained"
            color="primary"
            onClick={() => {
              const { target, buildingId } = confirmReplace;
              setConfirmReplace({ open: false });
              // If the active building changed while the dialog was open, the
              // confirmation the user just gave no longer applies to whatever is
              // active now — do nothing rather than silently replacing a building
              // nobody confirmed for. The user can click Auto-Detect again, which
              // re-runs the check (and, if needed, re-prompts) for the NOW-active
              // building.
              if (useRoofLensStore.getState().activeBuildingId !== buildingId) return;
              runSam2(target, true);
            }}
          >
            {t("asset.rooflens.confirmReplace.continue")}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default RoofLensAssetMap;

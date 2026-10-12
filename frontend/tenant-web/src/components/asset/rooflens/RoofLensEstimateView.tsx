/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-23
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : READ-ONLY RoofLens roof view for the Estimate modal. Renders the asset's
 *                saved measurement (asset.measurement_data JSON) as facet polygons over a
 *                Google satellite map, framed on the house, with a legend checkbox to hide
 *                the overlay and see the bare satellite image. Fully self-contained — parses
 *                its own measurement, holds facets in LOCAL state, and does NOT touch the
 *                RoofLens zustand store (which is a module singleton owned by the asset
 *                editor), so it can never cross-contaminate an open editor or another view.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Estimate | 2026-07-23 | FIELDCRAFT Web | Initial version
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { GoogleMap, Polygon } from "@react-google-maps/api";
import { Check } from "lucide-react";
import { useGoogleMaps } from "../../../context/GoogleMapsContext";
import type { RoofLensExportData } from "./exportRoofLens";
import type { LatLng } from "./types";

interface Props {
  /** The asset's stored measurement JSON (asset.measurement_data). */
  measurementJson?: string | null;
  /** Map height in px. Defaults to 236 to match the modal's previous pane. */
  height?: number;
}

const PALETTE = ["#4f7fff", "#00b8a3", "#ffb020", "#a259ff", "#ff6b6b", "#20c997", "#f06595", "#5c7cfa"];
const PROPERTY_ZOOM = 20;

/** One facet reduced to just what the read-only overlay needs. */
interface FacetShape {
  id: string;
  path: LatLng[];
}

/** Parse + validate the stored measurement into drawable facet paths (local, no store). */
function parseMeasurement(json?: string | null): { data: RoofLensExportData; facets: FacetShape[] } | null {
  if (!json || typeof json !== "string") return null;
  try {
    const data = JSON.parse(json) as RoofLensExportData;
    if (!data || !Array.isArray(data.facets)) return null;
    const facets: FacetShape[] = data.facets
      .map((f) => ({
        id: f.facet_id,
        path: (Array.isArray(f.boundary_latlng) ? f.boundary_latlng : [])
          .map(([lat, lng]) => ({ lat, lng }))
          .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lng)),
      }))
      .filter((f) => f.path.length >= 3);
    if (facets.length === 0) return null;
    return { data, facets };
  } catch {
    return null;
  }
}

const RoofLensEstimateView: React.FC<Props> = ({ measurementJson, height = 236 }) => {
  const { t } = useTranslation();
  const { isLoaded } = useGoogleMaps();
  const parsed = React.useMemo(() => parseMeasurement(measurementJson), [measurementJson]);

  const [mapObj, setMapObj] = React.useState<google.maps.Map | null>(null);
  // Only paint polygons after tiles have actually rendered (StrictMode-safe), same gate the
  // editor map uses — otherwise polygons attached during the mount churn can stay invisible.
  const [mapReady, setMapReady] = React.useState(false);
  const [showFacets, setShowFacets] = React.useState(true);

  // Frame the map on the roof: fit to all facet vertices; fall back to the stored center.
  const fitToFacets = React.useCallback((m: google.maps.Map) => {
    if (!parsed) return;
    const bounds = new google.maps.LatLngBounds();
    let any = false;
    for (const f of parsed.facets) for (const p of f.path) { bounds.extend(p); any = true; }
    if (any) {
      m.fitBounds(bounds, 40);
    } else if (parsed.data.lat != null && parsed.data.lng != null) {
      m.setCenter({ lat: parsed.data.lat, lng: parsed.data.lng });
      m.setZoom(PROPERTY_ZOOM);
    }
  }, [parsed]);

  React.useEffect(() => {
    if (mapObj) fitToFacets(mapObj);
  }, [mapObj, fitToFacets]);

  // Nothing to show if there's no valid stored measurement — let the modal render without a map.
  if (!parsed) return null;

  const { data, facets } = parsed;
  const totalArea = data.summary?.total_roof_area_sqft;
  const facetCount = data.summary?.facet_count ?? facets.length;
  const center: LatLng =
    data.lat != null && data.lng != null ? { lat: data.lat, lng: data.lng } : facets[0].path[0];

  return (
    <div className="px-[22px] pb-1">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-fieldcraft-fg-3">{t("asset.rooflens.roofMeasurementHeading")}</div>
        <div className="flex items-center gap-[5px] text-[11px] text-fieldcraft-fg-3">
          <span className="h-1.5 w-1.5 rounded-full bg-fieldcraft-success" />{t("asset.rooflens.measured")}
        </div>
      </div>

      <div className="relative overflow-hidden rounded-xl border border-fieldcraft-border shadow-fieldcraft-low" style={{ height }}>
        {!isLoaded ? (
          <div className="flex h-full w-full items-center justify-center bg-fieldcraft-surface-2 text-[12.5px] text-fieldcraft-fg-3">
            {t("asset.rooflens.loadingMap")}
          </div>
        ) : (
          <GoogleMap
            mapContainerStyle={{ width: "100%", height: "100%" }}
            center={center}
            zoom={PROPERTY_ZOOM}
            onLoad={(m) => { setMapObj(m); fitToFacets(m); }}
            onTilesLoaded={() => setMapReady(true)}
            onUnmount={() => { setMapObj(null); setMapReady(false); }}
            options={{
              mapTypeId: "satellite",
              tilt: 0,
              rotateControl: false,
              streetViewControl: false,
              mapTypeControl: false,
              fullscreenControl: true,
              zoomControl: true,
              gestureHandling: "cooperative",
            }}
          >
            {/* Facet overlay — toggled off via the legend to reveal the bare satellite image. */}
            {mapObj && mapReady && showFacets && facets.map((f, i) => (
              // Composite key: facet_id is only "Facet N" PER building, so the flattened
              // cross-building facets array can repeat ids (house + garage) — pair with index.
              <Polygon
                key={`${f.id}-${i}`}
                path={f.path}
                options={{
                  fillColor: PALETTE[i % PALETTE.length],
                  fillOpacity: 0.35,
                  strokeColor: "#ffffff",
                  strokeWeight: 2,
                  clickable: false,
                  editable: false,
                  zIndex: 1,
                }}
              />
            ))}
          </GoogleMap>
        )}

        {/* Layer legend — only "Roof facets" (RoofLens stores building/facet geometry only; no
            tree-overhang layer exists, so we don't fabricate one). */}
        <div className="absolute left-3 top-3 min-w-[150px] rounded-[10px] bg-[rgba(255,255,255,.96)] px-[13px] py-[11px] shadow-fieldcraft-med">
          <div className="mb-[9px] font-display text-[12.5px] font-extrabold text-fieldcraft-fg-1">{t("asset.rooflens.mapLayers")}</div>
          <button
            id="rl-est-btn-facets" type="button" onClick={() => setShowFacets((v) => !v)}
            className="flex w-full items-center gap-2 border-none bg-transparent text-[12.5px] font-semibold text-fieldcraft-fg-1"
          >
            <span
              className="flex h-4 w-4 flex-none items-center justify-center rounded border-[1.5px]"
              style={{ background: showFacets ? "var(--fieldcraft-primary)" : "#fff", borderColor: showFacets ? "var(--fieldcraft-primary)" : "#CBD5E1" }}
            >
              {showFacets && <Check size={10} strokeWidth={2} className="text-white" />}
            </span>
            <span>{t("asset.rooflens.roofFacets")}</span>
            <span className="ml-auto h-[11px] w-[11px] rounded-full" style={{ background: PALETTE[0] }} />
          </button>
        </div>

        {/* Measurement callout — real values from the saved export (area + facet count). */}
        {showFacets && (
          <div className="absolute bottom-3 left-3 min-w-[176px] rounded-[11px] bg-white px-[13px] py-2.5 shadow-fieldcraft-high">
            <div className="mb-1 font-display text-[13px] font-bold text-fieldcraft-fg-1">
              {facetCount ? t("asset.rooflens.roofFacetCount", { count: facetCount }) : t("asset.rooflens.roof")}
            </div>
            {typeof totalArea === "number" && (
              <div className="font-display text-[20px] font-extrabold tracking-[-0.01em] text-fieldcraft-fg-1">
                {totalArea.toLocaleString("en-US")} ft²
              </div>
            )}
          </div>
        )}

        {/* Attribution — FIELDCRAFT RoofLens over Google satellite imagery (not Nearmap). */}
        <div className="absolute bottom-2.5 right-2.5 flex items-center gap-1.5 rounded-[7px] bg-[rgba(10,19,48,.62)] px-[9px] py-1 backdrop-blur-[6px]">
          <span className="text-[10px] font-bold tracking-[0.04em] text-white/90">{t("asset.rooflens.brand")}</span>
          {data.address && <span className="max-w-[220px] truncate text-[10px] text-white/60">{data.address}</span>}
        </div>
      </div>

      {/* Decision-support disclaimer (mirrors the RoofLens export note). */}
      <div className="mt-1.5 text-[10.5px] leading-snug text-fieldcraft-fg-3">
        {t("asset.rooflens.disclaimer")}
      </div>
    </div>
  );
};

export default RoofLensEstimateView;

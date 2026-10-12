/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : 2D satellite map for the RoofLens editor. Renders facets as
 *                editable Google Maps polygons driven by the zustand store, and
 *                supports manual click-to-draw of new facets. Store is the source
 *                of truth; polygon edits sync back to it.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { Box, Button, Stack } from "@mui/material";
import PlaceIcon from "@mui/icons-material/Place";
import LocationOffIcon from "@mui/icons-material/LocationOff";
import { GoogleMap, MarkerF, Polygon, Polyline } from "@react-google-maps/api";
import { LatLng } from "./types";
import { nextFacetId, useRoofLensStore } from "./store/rooflensStore";

interface Props {
  center: LatLng;
  /** False until an address is searched — controls whether the map opens on the
   *  full continental-US view (default) or zoomed into a located property. */
  hasLocation?: boolean;
  /** Reports the map's current viewport center as the user pans, so Auto-Detect
   *  can measure whatever building is centered (e.g. pan to a detached garage). */
  onViewportCenter?: (c: LatLng) => void;
  /** Manually re-picking the property location: dragging the locator pin, or
   *  clicking the map while not drawing/editing a facet. Reports the dropped
   *  lat/lng for the caller to reverse-geocode and treat like a fresh search
   *  pick (same downstream address-fill/re-detect/confirm-dialog pipeline). */
  onManualPinMove?: (c: LatLng) => void;
}

const CONTAINER_STYLE = { width: "100%", height: "380px" };
const PALETTE = ["#4f7fff", "#00b8a3", "#ffb020", "#a259ff", "#ff6b6b", "#20c997", "#f06595", "#5c7cfa"];
// Zoom levels: whole continental US when no property is located; rooftop-close
// once an address is searched.
const US_ZOOM = 4;
const PROPERTY_ZOOM = 20;

const RoofLensMap: React.FC<Props> = ({ center, hasLocation, onViewportCenter, onManualPinMove }) => {
  const { t } = useTranslation();
  const facets = useRoofLensStore((s) => s.facets);
  const activeBuildingId = useRoofLensStore((s) => s.activeBuildingId);
  const tool = useRoofLensStore((s) => s.tool);
  const selectedId = useRoofLensStore((s) => s.selectedId);
  const selectFacet = useRoofLensStore((s) => s.selectFacet);
  const updateFacetPath = useRoofLensStore((s) => s.updateFacetPath);
  const addFacet = useRoofLensStore((s) => s.addFacet);
  const setTool = useRoofLensStore((s) => s.setTool);

  const [mapObj, setMapObj] = React.useState<google.maps.Map | null>(null);
  // Only render facet overlays once the map has actually PAINTED (tiles loaded), not
  // merely when the map object exists. In React 18 StrictMode (dev) the GoogleMap
  // double-mounts, so polygons attached at onLoad churn (mount→unmount→remount) and can
  // end up attached-but-unpainted. Gating on tilesloaded defers them to a settled map.
  const [mapReady, setMapReady] = React.useState(false);
  const [draft, setDraft] = React.useState<LatLng[]>([]);
  // Red locator pin on the searched property (mirrors the 3D view). Toggleable so
  // it never covers the roof while the user is measuring.
  const [showMarker, setShowMarker] = React.useState(true);
  const polyRefs = React.useRef<Record<string, google.maps.Polygon>>({});
  const initialCenter = React.useRef(center).current;
  const viewInited = React.useRef(false);

  // Frame the existing overlays (all buildings) so they're always visible — e.g.
  // after remounting from a 3D-View toggle. Returns false if there's nothing to fit.
  const fitToFacets = (m: google.maps.Map): boolean => {
    const bounds = new google.maps.LatLngBounds();
    let any = false;
    for (const f of useRoofLensStore.getState().facets) {
      for (const p of f.path) {
        bounds.extend(p);
        any = true;
      }
    }
    if (any) {
      m.fitBounds(bounds, 60);
      return true;
    }
    return false;
  };

  const handleMapLoad = (m: google.maps.Map) => {
    setMapObj(m);
    // If facets already exist (e.g. remount after a view toggle), frame them so
    // the overlay never disappears; otherwise open on the located property / US.
    if (!fitToFacets(m) && hasLocation) {
      m.setCenter(center);
      m.setZoom(PROPERTY_ZOOM);
    }
  };

  // Recenter on address search without fighting the initial fit or the user's
  // manual panning. Skip the first run (handleMapLoad already set the view).
  React.useEffect(() => {
    if (!mapObj) return;
    if (!viewInited.current) {
      viewInited.current = true;
      return;
    }
    mapObj.panTo(center);
    if (hasLocation) mapObj.setZoom(PROPERTY_ZOOM);
  }, [center.lat, center.lng, mapObj, hasLocation]);

  // When a facet SET arrives after the map is ready — e.g. restored from a saved
  // measurement on edit-load, or a fresh Auto-Detect — frame it once. This also forces a
  // redraw so google.maps.Polygons added right as the map initializes actually paint.
  // Fit ONCE per non-empty set (reset when facets clear) and never while the user is
  // drawing/editing, so manual "Add Area" / delete don't yank the viewport. A new-address
  // search clears facets to 0 first, so the next detection re-frames the new roof.
  const hasFittedRef = React.useRef(false);
  React.useEffect(() => {
    if (!mapObj) return;
    if (facets.length === 0) { hasFittedRef.current = false; return; }
    if (hasFittedRef.current) return;                 // already framed this set
    if (tool === "draw" || tool === "edit") return;   // don't fight manual editing
    if (fitToFacets(mapObj)) hasFittedRef.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapObj, facets.length, tool]);

  // Leaving draw mode discards any in-progress shape.
  React.useEffect(() => {
    if (tool !== "draw") setDraft([]);
  }, [tool]);

  const readPath = (poly: google.maps.Polygon): LatLng[] =>
    poly
      .getPath()
      .getArray()
      .map((p) => ({ lat: p.lat(), lng: p.lng() }));

  const syncPath = (id: string) => {
    const poly = polyRefs.current[id];
    if (poly) updateFacetPath(id, readPath(poly));
  };

  const handleMapClick = (e: google.maps.MapMouseEvent) => {
    if (!e.latLng) return;
    if (tool === "draw") {
      setDraft((d) => [...d, { lat: e.latLng!.lat(), lng: e.latLng!.lng() }]);
      return;
    }
    // Click-to-place the property pin: only while the "Move pin" tool is explicitly
    // armed (a dedicated toggle in RoofLensToolbar, like Add Area/Adjust) — NOT plain
    // "view" mode. A plain view-mode click is just normal map interaction (panning);
    // gating this on "view" previously meant every ordinary click silently relocated
    // the pin, which fought normal map use — a real reported bug, not a design choice.
    if (tool === "pin" && onManualPinMove) {
      onManualPinMove({ lat: e.latLng.lat(), lng: e.latLng.lng() });
    }
  };

  const handleMarkerDragEnd = (e: google.maps.MapMouseEvent) => {
    if (!e.latLng || !onManualPinMove) return;
    onManualPinMove({ lat: e.latLng.lat(), lng: e.latLng.lng() });
  };

  const finishDraft = () => {
    if (draft.length < 3) return;
    addFacet({ id: nextFacetId(), buildingId: "", path: draft, pitchRise: 6, isAuto: false, serviceAreaSqft: null, edited: false });
    setDraft([]);
    setTool("edit");
  };

  const cancelDraft = () => {
    setDraft([]);
    setTool("view");
  };

  return (
    <Box sx={{ position: "relative" }}>
      <GoogleMap
        mapContainerStyle={CONTAINER_STYLE}
        center={initialCenter}
        zoom={hasLocation ? PROPERTY_ZOOM : US_ZOOM}
        onLoad={handleMapLoad}
        onTilesLoaded={() => setMapReady(true)}
        onUnmount={() => { setMapObj(null); setMapReady(false); }}
        onClick={handleMapClick}
        onDragEnd={() => {
          const c = mapObj?.getCenter();
          if (c && onViewportCenter) onViewportCenter({ lat: c.lat(), lng: c.lng() });
        }}
        options={{
          mapTypeId: "satellite",
          tilt: 0,
          rotateControl: false,
          streetViewControl: false,
          mapTypeControl: false,
          fullscreenControl: true,
          disableDoubleClickZoom: true,
          gestureHandling: "greedy",
        }}
      >
        {/* Render overlays only once the map instance exists. On a remount (e.g.
            returning from 3D View) the facets already exist in the store, so
            rendering the Polygons before the GoogleMap's map is ready in context
            leaves them detached/invisible. Gating on mapObj defers them to after
            onLoad, so they always attach and stay visible. */}
        {mapObj && mapReady && facets.map((f, i) => {
          // Facets of OTHER buildings are shown dimmed for context but are not
          // editable/clickable — only the active building's facets are live.
          const isActive = f.buildingId === activeBuildingId;
          return (
            <Polygon
              key={f.id}
              path={f.path}
              editable={isActive && tool === "edit"}
              onLoad={(poly) => {
                polyRefs.current[f.id] = poly;
              }}
              onUnmount={() => {
                delete polyRefs.current[f.id];
              }}
              onClick={isActive ? () => selectFacet(f.id) : undefined}
              // Only sync the path back to the store when the polygon is actually
              // editable (tool === "edit", matching the `editable` prop below) — a
              // human-entered area override lives on serviceAreaSqft, and
              // updateFacetPath unconditionally clears it. Firing on every mouseup
              // (as this used to, gated only on isActive) meant simply clicking a
              // facet outside Adjust mode could silently wipe a just-typed
              // correction before the user ever got to Save.
              onMouseUp={isActive && tool === "edit" ? () => syncPath(f.id) : undefined}
              options={{
                fillColor: f.id === selectedId ? "#ff5252" : PALETTE[i % PALETTE.length],
                fillOpacity: isActive ? 0.35 : 0.12,
                strokeColor: f.id === selectedId ? "#d32f2f" : isActive ? "#ffffff" : "#9e9e9e",
                strokeWeight: isActive ? 2 : 1,
                clickable: isActive,
                zIndex: f.id === selectedId ? 3 : isActive ? 1 : 0,
              }}
            />
          );
        })}

        {mapObj && hasLocation && showMarker && (
          <MarkerF
            position={center}
            title={tool === "pin" ? t("asset.rooflens.map.locatedPropertyDraggable") : t("asset.rooflens.map.locatedProperty")}
            draggable={tool === "pin" && !!onManualPinMove}
            onDragEnd={handleMarkerDragEnd}
          />
        )}

        {draft.length > 0 && (
          <Polyline
            path={draft}
            options={{ strokeColor: "#ffb020", strokeWeight: 2, clickable: false }}
          />
        )}
        {draft.map((p, i) => (
          <MarkerF
            key={`draft-${i}`}
            position={p}
            onClick={i === 0 ? finishDraft : undefined}
            cursor={i === 0 ? "pointer" : "crosshair"}
            title={i === 0 ? t("asset.rooflens.map.closeAreaHint") : undefined}
            icon={{
              path: google.maps.SymbolPath.CIRCLE,
              // First (start) vertex is a larger green circle — click it to close
              // the loop and add the facet; the rest are small orange dots.
              scale: i === 0 ? 7 : 5,
              fillColor: i === 0 ? "#00c853" : "#ffb020",
              fillOpacity: 1,
              strokeColor: "#ffffff",
              strokeWeight: 2,
            }}
          />
        ))}
      </GoogleMap>

      {hasLocation && (
        <Button
          size="small"
          variant="contained"
          onClick={() => setShowMarker((v) => !v)}
          startIcon={showMarker ? <LocationOffIcon /> : <PlaceIcon />}
          sx={{
            position: "absolute",
            top: 52,
            right: 8,
            zIndex: 4,
            textTransform: "none",
            bgcolor: "#fff",
            color: "text.primary",
            boxShadow: 2,
            "&:hover": { bgcolor: "#f5f5f5" },
          }}
        >
          {showMarker ? t("asset.rooflens.map.hidePin") : t("asset.rooflens.map.showPin")}
        </Button>
      )}

      {tool === "draw" && (
        <Stack
          direction="row"
          spacing={1}
          sx={{ position: "absolute", top: 8, left: 8, zIndex: 4 }}
        >
          <Button
            size="small"
            variant="contained"
            disabled={draft.length < 3}
            onClick={finishDraft}
            sx={{ textTransform: "none" }}
          >
            {t("asset.rooflens.map.finishShape", { count: draft.length })}
          </Button>
          <Button size="small" variant="outlined" color="inherit" onClick={cancelDraft} sx={{ textTransform: "none", bgcolor: "#fff" }}>
            {t("common.cancel")}
          </Button>
        </Stack>
      )}
    </Box>
  );
};

export default RoofLensMap;

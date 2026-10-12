/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Photorealistic 3D view for the RoofLens panel (parity with the
 *                standalone RoofLens 3D View). Loads maps3d on demand via
 *                importLibrary so the shared Maps loader is untouched. Shows a
 *                zoomed-out globe by default (no pin); once a property is located
 *                it flies to the house and drops a red pin. Uses ground elevation
 *                (Elevation API) when available, else a terrain-clearing fallback.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version + elevation framing + red marker
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Global default; fly-to + pin only after locating
 * AB#32816              | 2026-10-05 | Steeve  | Moved from asset/rooflens/ so the shared AddressMapPicker has no upward import (re-exported at the old path)
 */

import * as React from "react";
import { Box, Typography } from "@mui/material";
import type { LatLng } from "../../interface/AddressInterfaces";

interface Props {
  center: LatLng;
  /** True once a property has been located; drives the fly-in + pin. */
  hasLocation: boolean;
}

const HEIGHT = 380;

// Located-house framing (Elevation API on): tight oblique.
const GROUNDED_TILT = 47;
const GROUNDED_RANGE = 135;
// Fallback (no elevation): near-top-down so `range` mostly becomes altitude and
// a small range still clears the terrain, keeping the house filling the frame.
const FALLBACK_TILT = 20;
const FALLBACK_RANGE = 330;
// Default (nothing located yet): a zoomed-out globe view.
const GLOBE_CENTER = { lat: 20, lng: 0, altitude: 0 };
const GLOBE_RANGE = 19_000_000;

async function groundAltitude(lat: number, lng: number): Promise<number | null> {
  const g: any = (window as any).google;
  try {
    const { ElevationService } = await g.maps.importLibrary("elevation");
    const svc = new ElevationService();
    return await new Promise<number | null>((resolve) => {
      svc.getElevationForLocations(
        { locations: [{ lat, lng }] },
        (results: any[], status: string) => {
          if (status === "OK" && results && results[0] && typeof results[0].elevation === "number") {
            resolve(results[0].elevation);
          } else {
            resolve(null);
          }
        }
      );
    });
  } catch {
    return null;
  }
}

function houseCamera(lat: number, lng: number, altitude: number | null) {
  const grounded = altitude !== null;
  return {
    center: { lat, lng, altitude: grounded ? (altitude as number) : 0 },
    tilt: grounded ? GROUNDED_TILT : FALLBACK_TILT,
    range: grounded ? GROUNDED_RANGE : FALLBACK_RANGE,
    heading: 0,
  };
}

const RoofLens3DView: React.FC<Props> = ({ center, hasLocation }) => {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const mapRef = React.useRef<any>(null);
  const markerRef = React.useRef<any>(null);
  const maps3dRef = React.useRef<{ Marker3DElement?: any; AltitudeMode?: any } | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // Build the 3D map once — globe by default, or the house if already located.
  React.useEffect(() => {
    let cancelled = false;
    const g: any = (window as any).google;
    if (!g?.maps?.importLibrary) {
      setError("3D view unavailable — the Maps API is not ready.");
      return;
    }
    (async () => {
      try {
        const maps3d: any = await g.maps.importLibrary("maps3d");
        const { Map3DElement, Marker3DElement, AltitudeMode, MapMode } = maps3d;
        if (cancelled || !containerRef.current) return;
        if (!Map3DElement) {
          setError("3D view isn't enabled for this Google Maps key. Use Measure & Edit for the 2D view.");
          return;
        }
        maps3dRef.current = { Marker3DElement, AltitudeMode };

        let camera: any;
        if (hasLocation) {
          const altitude = await groundAltitude(center.lat, center.lng);
          if (cancelled) return;
          camera = houseCamera(center.lat, center.lng, altitude);
        } else {
          camera = { center: GLOBE_CENTER, range: GLOBE_RANGE, tilt: 0, heading: 0 };
        }

        const map = new Map3DElement({
          center: camera.center,
          range: camera.range,
          tilt: camera.tilt,
          mode: MapMode ? MapMode.HYBRID : undefined,
        });
        map.style.width = "100%";
        map.style.height = `${HEIGHT}px`;
        map.style.display = "block";
        containerRef.current.innerHTML = "";
        containerRef.current.appendChild(map);
        mapRef.current = map;

        // Drop the pin only when a property is already located at build time.
        if (hasLocation && Marker3DElement) {
          const marker = new Marker3DElement({
            position: { lat: center.lat, lng: center.lng, altitude: 14 },
            altitudeMode: AltitudeMode ? AltitudeMode.RELATIVE_TO_GROUND : undefined,
            label: "Located property",
          });
          map.append(marker);
          markerRef.current = marker;
        }
      } catch (e) {
        if (!cancelled) {
          setError("3D view isn't available for this Google Maps configuration. Use Measure & Edit for the 2D view.");
        }
      }
    })();
    return () => {
      cancelled = true;
      mapRef.current = null;
      markerRef.current = null;
      maps3dRef.current = null;
    };
    // Build once; locating/recentring is handled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Once a property is located (or the located center changes), fly to the house
  // and add/move the red pin. Before locating, stay on the globe (no pin).
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      const map = mapRef.current;
      if (!map || !hasLocation) return;
      const altitude = await groundAltitude(center.lat, center.lng);
      if (cancelled || !mapRef.current) return;

      const m3d = maps3dRef.current;
      if (!markerRef.current && m3d?.Marker3DElement) {
        const marker = new m3d.Marker3DElement({
          position: { lat: center.lat, lng: center.lng, altitude: 14 },
          altitudeMode: m3d.AltitudeMode ? m3d.AltitudeMode.RELATIVE_TO_GROUND : undefined,
          label: "Located property",
        });
        map.append(marker);
        markerRef.current = marker;
      } else if (markerRef.current) {
        markerRef.current.position = { lat: center.lat, lng: center.lng, altitude: 14 };
      }

      const endCamera = houseCamera(center.lat, center.lng, altitude);
      try {
        if (typeof map.flyCameraTo === "function") {
          map.flyCameraTo({ endCamera, durationMillis: 1500 });
        } else {
          map.center = endCamera.center;
          map.range = endCamera.range;
          map.tilt = endCamera.tilt;
        }
      } catch {
        /* ignore transient camera errors */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [center.lat, center.lng, hasLocation]);

  if (error) {
    return (
      <Box
        sx={{
          height: HEIGHT,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: "#f5f6fa",
          textAlign: "center",
          p: 2,
        }}
      >
        <Typography variant="body2" color="text.secondary">
          {error}
        </Typography>
      </Box>
    );
  }

  return <div ref={containerRef} style={{ width: "100%", height: HEIGHT }} />;
};

export default RoofLens3DView;

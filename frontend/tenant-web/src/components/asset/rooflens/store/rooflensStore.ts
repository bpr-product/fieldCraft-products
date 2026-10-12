/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Zustand store for the RoofLens measure editor (facets, active
 *                tool, selection, loading). Data only — Google Maps Polygon
 *                objects live in the map component, driven from this store.
 *                Supports multiple BUILDINGS per property (e.g. a house plus a
 *                detached garage — EagleView reports these as separate structures).
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 */

import { create } from "zustand";
import { Building, FacetEdgeMetrics, FacetMeasurement, LatLng, RoofFacet, RoofLensTool, RoofMetrics, RoofTotals } from "../types";
import { footprintSqft, parsePitchRise, perimeterFt, pitchFactor } from "../utils/geometry";
// Type-only import (erased at compile) — avoids a runtime cycle with exportRoofLens,
// which imports computeTotals from this module.
import type { RoofLensExportData } from "../exportRoofLens";

let _seq = 1;
export const nextFacetId = (): string => `F${_seq++}`;

let _bseq = 1;
export const nextBuildingId = (): string => `B${_bseq++}`;

const firstBuilding = (): Building => ({ id: "B0", name: "Building 1" });

/** Full-state snapshot of the editable facet content — deliberately NOT a patch/inverse-diff.
 *  The state here is small and flat, so snapshotting the whole thing before each mutation is
 *  simpler and far less error-prone than hand-writing an inverse for every action, unlike
 *  Gantt 2.0's command/inverse-patch undo (projectScheduling/store/workflowStore.ts), which is
 *  built for much larger, deeply-nested state. */
interface HistoryEntry {
  facets: RoofFacet[];
  buildings: Building[];
  activeBuildingId: string;
  metricsByBuilding: Record<string, RoofMetrics | null>;
}

/** Cap so a long editing session can't grow the history unboundedly — mirrors Gantt 2.0's
 *  own MAX_UNDO pattern (projectScheduling/store/workflowStore.ts). */
const MAX_UNDO = 50;

/** Snapshots the current editable content onto undoStack and clears redoStack (a new edit
 *  invalidates any previously-undone redo history — standard undo/redo semantics). Called by
 *  every undoable action below, immediately before it applies its own change. */
function snapshotForUndo(
  s: Pick<RoofLensState, "facets" | "buildings" | "activeBuildingId" | "metricsByBuilding" | "undoStack">
): Pick<RoofLensState, "undoStack" | "redoStack" | "canUndo" | "canRedo"> {
  const entry: HistoryEntry = {
    facets: s.facets,
    buildings: s.buildings,
    activeBuildingId: s.activeBuildingId,
    metricsByBuilding: s.metricsByBuilding,
  };
  return {
    undoStack: [...s.undoStack, entry].slice(-MAX_UNDO),
    redoStack: [],
    canUndo: true,
    canRedo: false,
  };
}

interface RoofLensState {
  tool: RoofLensTool;
  facets: RoofFacet[];
  selectedId: string | null;
  loading: { active: boolean; label: string };
  status: string | null;
  /** Buildings on this property; always at least one. */
  buildings: Building[];
  /** The building that Auto-Detect / Add Area / edits currently target. */
  activeBuildingId: string;
  /**
   * EagleView-style metrics from the last Auto-Detect run, per building. Edge
   * breakdown / attic / stories reflect the auto-detected geometry; manual facet
   * edits update the area/facet totals live but NOT this classified breakdown.
   */
  metricsByBuilding: Record<string, RoofMetrics | null>;
  /** Undo/redo history — see HistoryEntry/snapshotForUndo above. */
  undoStack: HistoryEntry[];
  redoStack: HistoryEntry[];
  canUndo: boolean;
  canRedo: boolean;

  setTool: (tool: RoofLensTool) => void;
  /** Store the active building's Auto-Detect metrics. */
  setMetrics: (metrics: RoofMetrics | null) => void;
  /** Replace the ACTIVE building's facets (used by Auto-Detect); others untouched. */
  setFacets: (facets: RoofFacet[]) => void;
  addFacet: (facet: RoofFacet) => void;
  updateFacetPath: (id: string, path: LatLng[]) => void;
  updateFacetPitch: (id: string, rise: number) => void;
  /** Directly override a facet's roof area (sq ft) with a user-entered value. */
  updateFacetArea: (id: string, sqft: number) => void;
  /** Edit one of a facet's edge lengths (ridge/hip/valley/rake/eave/parapet), ft. */
  updateFacetEdge: (id: string, key: keyof FacetEdgeMetrics, ft: number) => void;
  deleteFacet: (id: string) => void;
  selectFacet: (id: string | null) => void;
  /** Clear only the ACTIVE building's facets + metrics. */
  clearAll: () => void;
  addBuilding: () => void;
  setActiveBuilding: (id: string) => void;
  removeBuilding: (id: string) => void;
  setLoading: (active: boolean, label?: string) => void;
  setStatus: (status: string | null) => void;
  /** Rebuild the whole editor state from a saved measurement export (inverse of
   *  buildExportData). No-op on malformed/empty data. */
  hydrateFromExport: (data: RoofLensExportData) => void;
  reset: () => void;
  /** Revert to the last undo-stack snapshot; no-op when undoStack is empty. */
  undo: () => void;
  /** Reapply the most recently undone snapshot; no-op when redoStack is empty. */
  redo: () => void;
}

const initial = () => ({
  tool: "view" as RoofLensTool,
  facets: [] as RoofFacet[],
  selectedId: null as string | null,
  loading: { active: false, label: "" },
  status: null as string | null,
  buildings: [firstBuilding()],
  activeBuildingId: "B0",
  metricsByBuilding: {} as Record<string, RoofMetrics | null>,
  undoStack: [] as HistoryEntry[],
  redoStack: [] as HistoryEntry[],
  canUndo: false,
  canRedo: false,
});

export const useRoofLensStore = create<RoofLensState>((set) => ({
  ...initial(),

  setTool: (tool) => set({ tool }),
  setMetrics: (metrics) =>
    set((s) => ({ metricsByBuilding: { ...s.metricsByBuilding, [s.activeBuildingId]: metrics } })),
  setFacets: (facets) =>
    set((s) => ({
      ...snapshotForUndo(s),
      // keep other buildings' facets; replace this building's with the new set
      facets: [
        ...s.facets.filter((f) => f.buildingId !== s.activeBuildingId),
        ...facets.map((f) => ({ ...f, buildingId: s.activeBuildingId })),
      ],
      selectedId: null,
    })),
  addFacet: (facet) =>
    set((s) => ({
      ...snapshotForUndo(s),
      facets: [...s.facets, { ...facet, buildingId: s.activeBuildingId }],
    })),
  updateFacetPath: (id, path) =>
    set((s) => ({
      ...snapshotForUndo(s),
      facets: s.facets.map((f) =>
        f.id === id ? { ...f, path, edited: true, serviceAreaSqft: null } : f
      ),
    })),
  updateFacetPitch: (id, rise) =>
    set((s) => ({
      ...snapshotForUndo(s),
      facets: s.facets.map((f) => (f.id === id ? { ...f, pitchRise: rise } : f)),
    })),
  updateFacetArea: (id, sqft) =>
    // Store the user's value as the service area and clear `edited` so
    // computeTotals uses it directly (instead of footprint x pitch factor).
    set((s) => ({
      ...snapshotForUndo(s),
      facets: s.facets.map((f) =>
        f.id === id ? { ...f, serviceAreaSqft: sqft, edited: false } : f
      ),
    })),
  updateFacetEdge: (id, key, ft) =>
    set((s) => ({
      ...snapshotForUndo(s),
      facets: s.facets.map((f) => {
        if (f.id !== id) return f;
        const base: FacetEdgeMetrics =
          f.edgeMetrics ?? { ridge_ft: 0, hip_ft: 0, valley_ft: 0, rake_ft: 0, eave_ft: 0, parapet_ft: 0 };
        return { ...f, edgeMetrics: { ...base, [key]: ft } };
      }),
    })),
  deleteFacet: (id) =>
    set((s) => ({
      ...snapshotForUndo(s),
      facets: s.facets.filter((f) => f.id !== id),
      selectedId: s.selectedId === id ? null : s.selectedId,
    })),
  selectFacet: (selectedId) => set({ selectedId }),
  clearAll: () =>
    set((s) => ({
      ...snapshotForUndo(s),
      facets: s.facets.filter((f) => f.buildingId !== s.activeBuildingId),
      selectedId: null,
      metricsByBuilding: { ...s.metricsByBuilding, [s.activeBuildingId]: null },
    })),
  addBuilding: () =>
    set((s) => {
      const id = nextBuildingId();
      return {
        ...snapshotForUndo(s),
        buildings: [...s.buildings, { id, name: `Building ${s.buildings.length + 1}` }],
        activeBuildingId: id,
        selectedId: null,
        tool: "view",
      };
    }),
  setActiveBuilding: (id) => set({ activeBuildingId: id, selectedId: null, tool: "view" }),
  removeBuilding: (id) =>
    set((s) => {
      if (s.buildings.length <= 1) return s; // never remove the last building
      const buildings = s.buildings.filter((b) => b.id !== id);
      const nextActive = s.activeBuildingId === id ? buildings[0].id : s.activeBuildingId;
      const { [id]: _removed, ...metricsByBuilding } = s.metricsByBuilding;
      return {
        ...snapshotForUndo(s),
        buildings,
        facets: s.facets.filter((f) => f.buildingId !== id),
        activeBuildingId: nextActive,
        metricsByBuilding,
        selectedId: null,
      };
    }),
  setLoading: (active, label = "") => set({ loading: { active, label } }),
  setStatus: (status) => set({ status }),
  hydrateFromExport: (data) => {
    // Guard: nothing to restore from malformed/empty payloads.
    const buildingsIn = data?.buildings ?? [];
    if (buildingsIn.length === 0) return;

    const buildings: Building[] = buildingsIn.map((b) => ({
      id: b.building_id,
      name: b.building_name,
    }));

    // Prefer the flattened facet list; fall back to each building's own facets.
    const facetsIn =
      data.facets && data.facets.length > 0
        ? data.facets
        : buildingsIn.flatMap((b) => b.facets ?? []);
    // Mirrors the RoofFacet construction in RoofLensAssetMap.runSam2 (auto) and
    // RoofLensMap.addFacet (manual). Preserve each facet's original provenance
    // (isAuto), but restore the saved roof_area_sqft identically for every facet,
    // manual or not — a human-entered correction always outranks an automated
    // recompute, so a value the user explicitly typed into a manual facet's area
    // cell must survive a reload exactly as reliably as an auto-detected value
    // already does. This used to null out manual facets' area unconditionally —
    // "no frozen area" was meant for facets that were never given an explicit
    // area, but it silently discarded ones that were. computeTotals
    // still recomputes from path+pitch whenever serviceAreaSqft is null or the
    // facet has since been edited (a later drag clears both via updateFacetPath),
    // so an un-overridden manual facet's area is unaffected by this change — its
    // saved value is already exactly footprint x pitchFactor, the same formula
    // computeTotals would derive fresh from the restored path/pitch.
    const facets: RoofFacet[] = facetsIn.map((f) => {
      const isManual = f.source === "manual";
      return {
        id: nextFacetId(),
        buildingId: f.building_id,
        path: (f.boundary_latlng ?? []).map(([lat, lng]) => ({ lat, lng })),
        pitchRise: parsePitchRise(f.pitch),
        isAuto: !isManual,
        serviceAreaSqft: typeof f.roof_area_sqft === "number" ? f.roof_area_sqft : null,
        edited: false,
        edgeMetrics: f.edge_metrics,
      };
    });

    const metricsByBuilding: Record<string, RoofMetrics | null> = {};
    buildingsIn.forEach((b) => {
      metricsByBuilding[b.building_id] = b.roof_report ?? null;
    });

    set({
      buildings,
      facets,
      metricsByBuilding,
      activeBuildingId: buildings[0].id,
      selectedId: null,
      tool: "view",
      // Loading a saved measurement starts a fresh editing session — undo history from
      // whatever was open before (a different asset, or pre-load scratch edits) must not
      // leak into it.
      undoStack: [],
      redoStack: [],
      canUndo: false,
      canRedo: false,
    });
  },
  reset: () => {
    _bseq = 1;
    set(initial());
  },
  undo: () =>
    set((s) => {
      if (s.undoStack.length === 0) return s;
      const prev = s.undoStack[s.undoStack.length - 1];
      const currentEntry: HistoryEntry = {
        facets: s.facets,
        buildings: s.buildings,
        activeBuildingId: s.activeBuildingId,
        metricsByBuilding: s.metricsByBuilding,
      };
      return {
        ...prev,
        undoStack: s.undoStack.slice(0, -1),
        redoStack: [...s.redoStack, currentEntry].slice(-MAX_UNDO),
        canUndo: s.undoStack.length > 1,
        canRedo: true,
        selectedId: null,
      };
    }),
  redo: () =>
    set((s) => {
      if (s.redoStack.length === 0) return s;
      const next = s.redoStack[s.redoStack.length - 1];
      const currentEntry: HistoryEntry = {
        facets: s.facets,
        buildings: s.buildings,
        activeBuildingId: s.activeBuildingId,
        metricsByBuilding: s.metricsByBuilding,
      };
      return {
        ...next,
        redoStack: s.redoStack.slice(0, -1),
        undoStack: [...s.undoStack, currentEntry].slice(-MAX_UNDO),
        canRedo: s.redoStack.length > 1,
        canUndo: true,
        selectedId: null,
      };
    }),
}));

/** Pure selector: derive the live measurement totals from the facet list. */
export function computeTotals(facets: RoofFacet[]): RoofTotals {
  const rows: FacetMeasurement[] = facets.map((f, i) => {
    const footprint = footprintSqft(f.path);
    const roofArea =
      f.serviceAreaSqft != null && !f.edited
        ? f.serviceAreaSqft
        : footprint * pitchFactor(f.pitchRise);
    const source: FacetMeasurement["source"] = !f.isAuto
      ? "manual"
      : f.edited
      ? "auto-adjusted"
      : "auto";
    return {
      id: f.id,
      label: `Facet ${i + 1}`,
      roofAreaSqft: roofArea,
      footprintSqft: footprint,
      perimeterFt: perimeterFt(f.path),
      pitch: `${f.pitchRise}/12`,
      source,
    };
  });

  return {
    totalRoofAreaSqft: rows.reduce((sum, r) => sum + r.roofAreaSqft, 0),
    footprintSqft: rows.reduce((sum, r) => sum + r.footprintSqft, 0),
    perimeterFt: rows.reduce((sum, r) => sum + r.perimeterFt, 0),
    facetCount: rows.length,
    facets: rows,
  };
}

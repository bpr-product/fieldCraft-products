/**
 * Unit tests for rooflensStore.ts — the Zustand store backing the RoofLens measure
 * editor (multi-building facets, active tool, selection, per-building metrics,
 * hydrate-from-export) and the pure computeTotals() selector.
 *
 * The store is a module singleton, so every test starts from reset(). ID helpers
 * (nextFacetId/nextBuildingId) are module-level counters; nextFacetId is NOT reset
 * by reset(), so tests never assert absolute facet-id values — they create facets
 * with explicit ids.
 */

import {
  useRoofLensStore,
  computeTotals,
  nextFacetId,
  nextBuildingId,
} from './rooflensStore';
import { RoofFacet } from '../types';

const store = useRoofLensStore;
const get = () => store.getState();

/** A square-ish facet near the equator (small, positive area/perimeter). */
const SQUARE = [
  { lat: 0, lng: 0 },
  { lat: 0, lng: 0.001 },
  { lat: 0.001, lng: 0.001 },
  { lat: 0.001, lng: 0 },
];

const makeFacet = (over: Partial<RoofFacet> = {}): RoofFacet => ({
  id: 'F-test',
  buildingId: 'B0',
  path: SQUARE,
  pitchRise: 6,
  isAuto: true,
  serviceAreaSqft: null,
  edited: false,
  ...over,
});

beforeEach(() => {
  get().reset();
});

describe('initial state', () => {
  test('resets to a single default building in view mode', () => {
    const s = get();
    expect(s.tool).toBe('view');
    expect(s.facets).toEqual([]);
    expect(s.selectedId).toBeNull();
    expect(s.loading).toEqual({ active: false, label: '' });
    expect(s.status).toBeNull();
    expect(s.buildings).toEqual([{ id: 'B0', name: 'Building 1' }]);
    expect(s.activeBuildingId).toBe('B0');
    expect(s.metricsByBuilding).toEqual({});
  });
});

describe('id generators', () => {
  test('nextFacetId returns F-prefixed, strictly increasing ids', () => {
    const a = nextFacetId();
    const b = nextFacetId();
    expect(a).toMatch(/^F\d+$/);
    expect(b).toMatch(/^F\d+$/);
    expect(Number(b.slice(1))).toBe(Number(a.slice(1)) + 1);
  });

  test('nextBuildingId returns B-prefixed ids', () => {
    expect(nextBuildingId()).toMatch(/^B\d+$/);
  });
});

describe('tool / selection / status / loading', () => {
  test('setTool updates the active tool', () => {
    get().setTool('draw');
    expect(get().tool).toBe('draw');
  });

  test('selectFacet stores the id', () => {
    get().selectFacet('F1');
    expect(get().selectedId).toBe('F1');
    get().selectFacet(null);
    expect(get().selectedId).toBeNull();
  });

  test('setStatus stores the status string', () => {
    get().setStatus('Detecting…');
    expect(get().status).toBe('Detecting…');
  });

  test('setLoading defaults the label to an empty string', () => {
    get().setLoading(true, 'Working');
    expect(get().loading).toEqual({ active: true, label: 'Working' });
    get().setLoading(false);
    expect(get().loading).toEqual({ active: false, label: '' });
  });
});

describe('facet mutations', () => {
  test('addFacet stamps the facet with the active building id', () => {
    get().addFacet(makeFacet({ id: 'F1', buildingId: 'ignored' }));
    expect(get().facets).toHaveLength(1);
    expect(get().facets[0].buildingId).toBe('B0');
  });

  test('updateFacetPath marks the facet edited and clears its frozen area', () => {
    get().addFacet(makeFacet({ id: 'F1', serviceAreaSqft: 1000, edited: false }));
    const newPath = [{ lat: 1, lng: 1 }, { lat: 1, lng: 2 }, { lat: 2, lng: 2 }];
    get().updateFacetPath('F1', newPath);
    const f = get().facets[0];
    expect(f.path).toEqual(newPath);
    expect(f.edited).toBe(true);
    expect(f.serviceAreaSqft).toBeNull();
  });

  test('updateFacetPitch changes only the pitch rise', () => {
    get().addFacet(makeFacet({ id: 'F1', pitchRise: 6 }));
    get().updateFacetPitch('F1', 9);
    expect(get().facets[0].pitchRise).toBe(9);
  });

  test('updateFacetArea sets the service area and clears edited so it is used directly', () => {
    get().addFacet(makeFacet({ id: 'F1', edited: true, serviceAreaSqft: null }));
    get().updateFacetArea('F1', 1750);
    const f = get().facets[0];
    expect(f.serviceAreaSqft).toBe(1750);
    expect(f.edited).toBe(false);
  });

  test('updateFacetEdge seeds a zeroed breakdown when none exists, then merges', () => {
    get().addFacet(makeFacet({ id: 'F1' }));
    get().updateFacetEdge('F1', 'ridge_ft', 42);
    expect(get().facets[0].edgeMetrics).toEqual({
      ridge_ft: 42, hip_ft: 0, valley_ft: 0, rake_ft: 0, eave_ft: 0, parapet_ft: 0,
    });
    get().updateFacetEdge('F1', 'eave_ft', 15);
    expect(get().facets[0].edgeMetrics).toEqual({
      ridge_ft: 42, hip_ft: 0, valley_ft: 0, rake_ft: 0, eave_ft: 15, parapet_ft: 0,
    });
  });

  test('deleteFacet removes the facet and clears the selection only when it was selected', () => {
    get().addFacet(makeFacet({ id: 'F1' }));
    get().addFacet(makeFacet({ id: 'F2' }));
    get().selectFacet('F2');
    get().deleteFacet('F1');
    expect(get().facets.map(f => f.id)).toEqual(['F2']);
    expect(get().selectedId).toBe('F2'); // unaffected — F1 was not selected

    get().deleteFacet('F2');
    expect(get().facets).toHaveLength(0);
    expect(get().selectedId).toBeNull(); // cleared — F2 was selected
  });

  test('setFacets replaces only the active building, keeping other buildings intact', () => {
    get().addFacet(makeFacet({ id: 'F1' })); // B0
    get().addBuilding(); // now active B1
    const b1 = get().activeBuildingId;
    get().addFacet(makeFacet({ id: 'F2' })); // B1
    get().selectFacet('F2');

    get().setFacets([makeFacet({ id: 'FX', buildingId: 'whatever' })]);

    const byId = Object.fromEntries(get().facets.map(f => [f.id, f]));
    expect(get().facets.map(f => f.id).sort()).toEqual(['F1', 'FX']); // F2 replaced
    expect(byId.F1.buildingId).toBe('B0'); // other building untouched
    expect(byId.FX.buildingId).toBe(b1); // new facets stamped to active building
    expect(get().selectedId).toBeNull();
  });
});

describe('metrics', () => {
  test('setMetrics stores under the active building', () => {
    get().setMetrics({ facet_count: 3 });
    expect(get().metricsByBuilding.B0).toEqual({ facet_count: 3 });
  });

  test('clearAll wipes the active building facets + metrics but keeps other buildings', () => {
    get().addFacet(makeFacet({ id: 'F1' })); // B0
    get().setMetrics({ facet_count: 1 });
    get().addBuilding(); // B1 active
    const b1 = get().activeBuildingId;
    get().addFacet(makeFacet({ id: 'F2' }));
    get().setMetrics({ facet_count: 9 });
    get().selectFacet('F2');

    get().clearAll(); // clears B1 only

    expect(get().facets.map(f => f.id)).toEqual(['F1']);
    expect(get().metricsByBuilding.B0).toEqual({ facet_count: 1 });
    expect(get().metricsByBuilding[b1]).toBeNull();
    expect(get().selectedId).toBeNull();
  });
});

describe('buildings', () => {
  test('addBuilding appends a building, activates it, and resets tool/selection', () => {
    get().setTool('draw');
    get().selectFacet('F1');
    get().addBuilding();
    const s = get();
    expect(s.buildings).toHaveLength(2);
    expect(s.buildings[1]).toEqual({ id: 'B1', name: 'Building 2' });
    expect(s.activeBuildingId).toBe('B1');
    expect(s.tool).toBe('view');
    expect(s.selectedId).toBeNull();
  });

  test('setActiveBuilding switches the active building and resets tool/selection', () => {
    get().addBuilding(); // active B1
    get().setTool('edit');
    get().selectFacet('F9');
    get().setActiveBuilding('B0');
    expect(get().activeBuildingId).toBe('B0');
    expect(get().tool).toBe('view');
    expect(get().selectedId).toBeNull();
  });

  test('removeBuilding is a no-op when only one building remains', () => {
    const before = get().buildings;
    get().removeBuilding('B0');
    expect(get().buildings).toEqual(before);
  });

  test('removeBuilding drops the building, its facets and metrics, and reassigns active', () => {
    get().addFacet(makeFacet({ id: 'F1' })); // B0
    get().addBuilding(); // B1 active
    const b1 = get().activeBuildingId;
    get().addFacet(makeFacet({ id: 'F2' })); // B1
    get().setMetrics({ facet_count: 5 }); // B1 metrics

    get().removeBuilding(b1); // removing the active one

    expect(get().buildings.map(b => b.id)).toEqual(['B0']);
    expect(get().facets.map(f => f.id)).toEqual(['F1']);
    expect(get().metricsByBuilding[b1]).toBeUndefined();
    expect(get().activeBuildingId).toBe('B0'); // reassigned to first remaining
  });

  test('removeBuilding of a non-active building leaves the active one unchanged', () => {
    get().addBuilding(); // B1 active
    const b1 = get().activeBuildingId;
    get().setActiveBuilding('B0'); // B0 active again, B1 non-active
    get().removeBuilding(b1);
    expect(get().buildings.map(b => b.id)).toEqual(['B0']);
    expect(get().activeBuildingId).toBe('B0');
  });
});

describe('undo / redo', () => {
  test('canUndo/canRedo are false with an empty history', () => {
    expect(get().canUndo).toBe(false);
    expect(get().canRedo).toBe(false);
    get().undo(); // no-op — must not throw or change state
    get().redo();
    expect(get().facets).toEqual([]);
  });

  test('deleteFacet -> undo restores the facet; redo removes it again', () => {
    const facet = makeFacet({ id: 'F1' });
    get().addFacet(facet);
    get().deleteFacet('F1');
    expect(get().facets).toEqual([]);
    expect(get().canUndo).toBe(true);

    get().undo();
    expect(get().facets.map(f => f.id)).toEqual(['F1']);
    expect(get().canRedo).toBe(true);

    get().redo();
    expect(get().facets).toEqual([]);
    expect(get().canRedo).toBe(false);
  });

  test('updateFacetPath -> undo restores the pre-drag path', () => {
    const facet = makeFacet({ id: 'F1', path: SQUARE });
    get().addFacet(facet);
    const NEW_PATH = [{ lat: 5, lng: 5 }, { lat: 5, lng: 6 }, { lat: 6, lng: 6 }];
    get().updateFacetPath('F1', NEW_PATH);
    expect(get().facets[0].path).toEqual(NEW_PATH);

    get().undo();
    expect(get().facets[0].path).toEqual(SQUARE);
    expect(get().facets[0].edited).toBe(false);
  });

  test('clearAll -> undo restores every cleared facet and the metrics', () => {
    get().addFacet(makeFacet({ id: 'F1' }));
    get().addFacet(makeFacet({ id: 'F2' }));
    get().setMetrics({ facet_count: 2 } as any);
    get().clearAll();
    expect(get().facets).toEqual([]);
    expect(get().metricsByBuilding.B0).toBeNull();

    get().undo();
    expect(get().facets.map(f => f.id).sort()).toEqual(['F1', 'F2']);
    expect(get().metricsByBuilding.B0).toEqual({ facet_count: 2 });
  });

  test('addBuilding -> undo removes it and restores the previous active building', () => {
    get().addBuilding();
    expect(get().buildings.length).toBe(2);
    const secondId = get().activeBuildingId;
    expect(secondId).not.toBe('B0');

    get().undo();
    expect(get().buildings).toEqual([{ id: 'B0', name: 'Building 1' }]);
    expect(get().activeBuildingId).toBe('B0');
  });

  test('a new edit after an undo clears the redo stack', () => {
    get().addFacet(makeFacet({ id: 'F1' }));
    get().deleteFacet('F1');
    get().undo(); // F1 restored, redo available
    expect(get().canRedo).toBe(true);

    get().addFacet(makeFacet({ id: 'F2' }));
    expect(get().canRedo).toBe(false); // the new edit invalidated the old redo entry
    get().redo(); // no-op
    expect(get().facets.map(f => f.id).sort()).toEqual(['F1', 'F2']);
  });

  test('history is capped so a long editing session cannot grow it unboundedly', () => {
    for (let i = 0; i < 60; i++) {
      get().addFacet(makeFacet({ id: `F${i}` }));
    }
    expect(get().undoStack.length).toBeLessThanOrEqual(50);
  });

  test('setTool / selectFacet / setActiveBuilding do not push undo history (view-state only)', () => {
    get().addFacet(makeFacet({ id: 'F1' }));
    const undoDepth = get().undoStack.length;
    get().setTool('draw');
    get().selectFacet('F1');
    get().setActiveBuilding('B0');
    expect(get().undoStack.length).toBe(undoDepth);
  });

  test('reset clears both stacks', () => {
    get().addFacet(makeFacet({ id: 'F1' }));
    get().deleteFacet('F1');
    expect(get().undoStack.length).toBeGreaterThan(0);

    get().reset();
    expect(get().undoStack).toEqual([]);
    expect(get().redoStack).toEqual([]);
    expect(get().canUndo).toBe(false);
    expect(get().canRedo).toBe(false);
  });

  test('hydrateFromExport clears both stacks so a previous session cannot leak in', () => {
    get().addFacet(makeFacet({ id: 'F1' }));
    get().deleteFacet('F1');
    get().undo();
    expect(get().canRedo).toBe(true);

    get().hydrateFromExport({ buildings: [] } as any);
    // Malformed payload is a no-op for content, but the guard runs before the stack
    // reset — use a valid payload to actually exercise the clearing branch.
    get().hydrateFromExport({
      buildings: [{ building_id: 'BX', building_name: 'House', roof_report: null, facets: [] }],
      facets: [],
    } as any);
    expect(get().undoStack).toEqual([]);
    expect(get().redoStack).toEqual([]);
    expect(get().canUndo).toBe(false);
    expect(get().canRedo).toBe(false);
  });
});

describe('hydrateFromExport', () => {
  test('is a no-op for empty / malformed payloads', () => {
    const before = get();
    get().hydrateFromExport({ buildings: [] } as any);
    expect(get().buildings).toEqual(before.buildings);
    get().hydrateFromExport(undefined as any);
    expect(get().buildings).toEqual(before.buildings);
  });

  test('rebuilds buildings, facets and per-building metrics, preserving provenance', () => {
    const exportData = {
      buildings: [
        {
          building_id: 'BX',
          building_name: 'House',
          roof_report: { facet_count: 2 },
          facets: [],
        },
        {
          building_id: 'BY',
          building_name: 'Garage',
          roof_report: null,
          facets: [],
        },
      ],
      facets: [
        {
          building_id: 'BX',
          source: 'auto',
          boundary_latlng: [[40, -80], [40, -79], [41, -79]],
          pitch: '8/12',
          roof_area_sqft: 1234,
          edge_metrics: { ridge_ft: 10, hip_ft: 0, valley_ft: 0, rake_ft: 0, eave_ft: 0, parapet_ft: 0 },
        },
        {
          building_id: 'BY',
          source: 'manual',
          boundary_latlng: [[10, 10], [10, 11], [11, 11]],
          pitch: '4/12',
          roof_area_sqft: 999, // a human-typed override — must survive hydration
        },
      ],
    };

    get().hydrateFromExport(exportData as any);
    const s = get();

    expect(s.buildings).toEqual([
      { id: 'BX', name: 'House' },
      { id: 'BY', name: 'Garage' },
    ]);
    expect(s.activeBuildingId).toBe('BX');
    expect(s.metricsByBuilding).toEqual({ BX: { facet_count: 2 }, BY: null });

    const auto = s.facets.find(f => f.buildingId === 'BX')!;
    const manual = s.facets.find(f => f.buildingId === 'BY')!;

    expect(auto.isAuto).toBe(true);
    expect(auto.serviceAreaSqft).toBe(1234); // frozen area kept for auto
    expect(auto.pitchRise).toBe(8);
    expect(auto.path[0]).toEqual({ lat: 40, lng: -80 });
    expect(auto.edgeMetrics?.ridge_ft).toBe(10);

    expect(manual.isAuto).toBe(false);
    // A human-entered correction always outranks an automated recompute — restoring
    // it on reload is what this whole fix is for. Not toBeNull(): that was the bug.
    expect(manual.serviceAreaSqft).toBe(999);
    expect(manual.pitchRise).toBe(4);
  });

  test('restores a human-typed area override on a manual facet even though it disagrees with geometry', () => {
    // Regression test for the reported bug: clear auto-detected facets, draw new
    // ones manually, type a corrected area, save, navigate away and back. The
    // saved roof_area_sqft (500) deliberately differs from what footprint x
    // pitchFactor would derive from this path/pitch, so this only passes if the
    // saved value — not a geometry recompute — is what survives hydration.
    const exportData = {
      buildings: [{ building_id: 'BX', building_name: 'House', roof_report: null, facets: [] }],
      facets: [
        {
          building_id: 'BX',
          source: 'manual',
          boundary_latlng: [[0, 0], [0, 0.0001], [0.0001, 0.0001], [0.0001, 0]],
          pitch: '6/12',
          roof_area_sqft: 500, // the human's typed correction
        },
      ],
    };

    get().hydrateFromExport(exportData as any);
    const manual = get().facets[0];

    expect(manual.serviceAreaSqft).toBe(500);
    expect(manual.edited).toBe(false);
    // computeTotals must use the restored override, not recompute from geometry.
    const totals = computeTotals(get().facets);
    expect(totals.totalRoofAreaSqft).toBe(500);
  });

  test('a manual facet never given an explicit area is unaffected by the fix (control case)', () => {
    // A manual facet the user never overrode saves roof_area_sqft as exactly
    // footprint x pitchFactor (computeTotals' own formula). Restoring that value
    // on hydration must therefore be indistinguishable from the old "recompute
    // live" behavior for this case — proving the fix changes nothing here.
    const path = [{ lat: 0, lng: 0 }, { lat: 0, lng: 0.001 }, { lat: 0.001, lng: 0.001 }, { lat: 0.001, lng: 0 }];
    const pitchRise = 6;
    const geometryArea = computeTotals([
      { id: 'tmp', buildingId: 'BX', path, pitchRise, isAuto: false, serviceAreaSqft: null, edited: false },
    ]).totalRoofAreaSqft;

    const exportData = {
      buildings: [{ building_id: 'BX', building_name: 'House', roof_report: null, facets: [] }],
      facets: [
        {
          building_id: 'BX',
          source: 'manual',
          boundary_latlng: path.map((p) => [p.lat, p.lng]),
          pitch: `${pitchRise}/12`,
          roof_area_sqft: Math.round(geometryArea),
        },
      ],
    };

    get().hydrateFromExport(exportData as any);
    const totals = computeTotals(get().facets);
    expect(totals.totalRoofAreaSqft).toBeCloseTo(geometryArea, 0);
  });

  test('dragging a restored facet in a later session still un-freezes its area (no stale-area regression)', () => {
    const exportData = {
      buildings: [{ building_id: 'BX', building_name: 'House', roof_report: null, facets: [] }],
      facets: [
        {
          building_id: 'BX',
          source: 'manual',
          boundary_latlng: [[0, 0], [0, 0.0001], [0.0001, 0.0001], [0.0001, 0]],
          pitch: '6/12',
          roof_area_sqft: 500,
        },
      ],
    };
    get().hydrateFromExport(exportData as any);
    const restored = get().facets[0];
    expect(restored.serviceAreaSqft).toBe(500);

    get().updateFacetPath(restored.id, [
      { lat: 0, lng: 0 }, { lat: 0, lng: 0.0002 }, { lat: 0.0002, lng: 0.0002 }, { lat: 0.0002, lng: 0 },
    ]);

    const dragged = get().facets.find((f) => f.id === restored.id)!;
    expect(dragged.serviceAreaSqft).toBeNull();
    expect(dragged.edited).toBe(true);
  });

  test('falls back to each building\'s own facets when the flattened list is empty', () => {
    const exportData = {
      buildings: [
        {
          building_id: 'BX',
          building_name: 'House',
          roof_report: null,
          facets: [
            {
              building_id: 'BX',
              source: 'auto',
              boundary_latlng: [[0, 0], [0, 1], [1, 1]],
              pitch: '6/12',
              roof_area_sqft: 500,
            },
          ],
        },
      ],
      facets: [],
    };
    get().hydrateFromExport(exportData as any);
    expect(get().facets).toHaveLength(1);
    expect(get().facets[0].buildingId).toBe('BX');
  });
});

describe('reset', () => {
  test('restores the initial state and restarts the building sequence', () => {
    get().addBuilding();
    get().addFacet(makeFacet({ id: 'F1' }));
    get().setStatus('busy');

    get().reset();

    expect(get().buildings).toEqual([{ id: 'B0', name: 'Building 1' }]);
    expect(get().facets).toEqual([]);
    expect(get().status).toBeNull();

    // _bseq restarted, so the next added building is B1 again.
    get().addBuilding();
    expect(get().buildings[1].id).toBe('B1');
  });
});

describe('computeTotals', () => {
  test('returns all-zero totals for no facets', () => {
    expect(computeTotals([])).toEqual({
      totalRoofAreaSqft: 0,
      footprintSqft: 0,
      perimeterFt: 0,
      facetCount: 0,
      facets: [],
    });
  });

  test('uses the frozen service area verbatim for an unedited auto facet', () => {
    const totals = computeTotals([
      makeFacet({ id: 'F1', isAuto: true, edited: false, serviceAreaSqft: 1500 }),
    ]);
    expect(totals.facetCount).toBe(1);
    expect(totals.facets[0].roofAreaSqft).toBe(1500);
    expect(totals.facets[0].source).toBe('auto');
    expect(totals.facets[0].label).toBe('Facet 1');
    expect(totals.facets[0].pitch).toBe('6/12');
    expect(totals.totalRoofAreaSqft).toBe(1500);
  });

  test('classifies a manual facet and keeps its service area', () => {
    const totals = computeTotals([
      makeFacet({ id: 'F1', isAuto: false, edited: false, serviceAreaSqft: 800 }),
    ]);
    expect(totals.facets[0].source).toBe('manual');
    expect(totals.facets[0].roofAreaSqft).toBe(800);
  });

  test('recomputes area from footprint × pitch factor once edited (auto-adjusted)', () => {
    // pitchRise 0 → pitchFactor 1 → roof area equals footprint, so we can assert equality
    // without depending on exact spherical-area magnitudes.
    const totals = computeTotals([
      makeFacet({ id: 'F1', isAuto: true, edited: true, pitchRise: 0, serviceAreaSqft: 9999 }),
    ]);
    expect(totals.facets[0].source).toBe('auto-adjusted');
    expect(totals.facets[0].footprintSqft).toBeGreaterThan(0);
    expect(totals.facets[0].roofAreaSqft).toBeCloseTo(totals.facets[0].footprintSqft, 6);
    expect(totals.facets[0].perimeterFt).toBeGreaterThan(0);
  });

  test('sums roof area, footprint and perimeter across facets and labels them in order', () => {
    const totals = computeTotals([
      makeFacet({ id: 'A', isAuto: true, edited: false, serviceAreaSqft: 100 }),
      makeFacet({ id: 'B', isAuto: false, edited: false, serviceAreaSqft: 250 }),
    ]);
    expect(totals.facetCount).toBe(2);
    expect(totals.facets.map(f => f.label)).toEqual(['Facet 1', 'Facet 2']);
    expect(totals.totalRoofAreaSqft).toBe(350);
    expect(totals.footprintSqft).toBeCloseTo(
      totals.facets[0].footprintSqft + totals.facets[1].footprintSqft,
      6,
    );
    expect(totals.perimeterFt).toBeCloseTo(
      totals.facets[0].perimeterFt + totals.facets[1].perimeterFt,
      6,
    );
  });
});

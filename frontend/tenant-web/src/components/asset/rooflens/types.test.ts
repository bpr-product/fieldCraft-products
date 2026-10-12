/**
 * Unit tests for the RoofLens shared types.
 *
 * types.ts is declaration-only (no runtime code), so these are compile-time
 * conformance checks: they construct values for each interface/union and assert
 * the constrained fields. If a field name, casing, or union member drifts, this
 * suite fails to compile — which is the guard we want on the shape the store,
 * geometry helpers, and export path all share.
 */

import {
  LatLng,
  RoofLensTool,
  Building,
  FacetEdgeMetrics,
  RoofFacet,
  FacetMeasurement,
  RoofTotals,
  MlFacet,
  RoofFacetMetric,
  RoofMetrics,
  MlFacetsResponse,
  GeocodeResult,
  BlueprintRequest,
  BlueprintResponse,
} from './types';

describe('RoofLens type conformance', () => {
  test('RoofLensTool accepts exactly view/draw/edit', () => {
    const tools: RoofLensTool[] = ['view', 'draw', 'edit'];
    expect(tools).toEqual(['view', 'draw', 'edit']);
  });

  test('LatLng holds numeric lat/lng', () => {
    const p: LatLng = { lat: 40.1, lng: -80.2 };
    expect(p.lat).toBeCloseTo(40.1);
    expect(p.lng).toBeCloseTo(-80.2);
  });

  test('Building is an id + name pair', () => {
    const b: Building = { id: 'B0', name: 'Building 1' };
    expect(b).toEqual({ id: 'B0', name: 'Building 1' });
  });

  test('FacetEdgeMetrics carries all six edge lengths in feet', () => {
    const e: FacetEdgeMetrics = {
      ridge_ft: 1, hip_ft: 2, valley_ft: 3, rake_ft: 4, eave_ft: 5, parapet_ft: 6,
    };
    expect(Object.keys(e).sort()).toEqual(
      ['eave_ft', 'hip_ft', 'parapet_ft', 'rake_ft', 'ridge_ft', 'valley_ft'],
    );
  });

  test('RoofFacet: serviceAreaSqft may be null and edgeMetrics is optional', () => {
    const manual: RoofFacet = {
      id: 'F1',
      buildingId: 'B0',
      path: [{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }, { lat: 1, lng: 1 }],
      pitchRise: 6,
      isAuto: false,
      serviceAreaSqft: null,
      edited: false,
    };
    const auto: RoofFacet = {
      ...manual,
      id: 'F2',
      isAuto: true,
      serviceAreaSqft: 1200,
      edgeMetrics: { ridge_ft: 10, hip_ft: 0, valley_ft: 0, rake_ft: 0, eave_ft: 20, parapet_ft: 0 },
    };
    expect(manual.serviceAreaSqft).toBeNull();
    expect(auto.edgeMetrics?.ridge_ft).toBe(10);
  });

  test('FacetMeasurement.source is one of auto/auto-adjusted/manual', () => {
    const sources: FacetMeasurement['source'][] = ['auto', 'auto-adjusted', 'manual'];
    const m: FacetMeasurement = {
      id: 'F1',
      label: 'Facet 1',
      roofAreaSqft: 1000,
      footprintSqft: 900,
      perimeterFt: 120,
      pitch: '6/12',
      source: 'auto',
    };
    expect(sources).toContain(m.source);
  });

  test('RoofTotals aggregates facet rows', () => {
    const totals: RoofTotals = {
      totalRoofAreaSqft: 2000,
      footprintSqft: 1800,
      perimeterFt: 240,
      facetCount: 2,
      facets: [],
    };
    expect(totals.facetCount).toBe(2);
    expect(Array.isArray(totals.facets)).toBe(true);
  });

  test('MlFacet boundary is an array of [lat, lng] tuples', () => {
    const f: MlFacet = { boundary: [[40, -80], [40, -79]], pitch_ratio: '6/12', area_sqft: 500 };
    expect(f.boundary[0]).toEqual([40, -80]);
  });

  test('RoofMetrics and RoofFacetMetric allow the optional EagleView-style breakdown', () => {
    const perFacet: RoofFacetMetric = { facet_number: 1, area_sqft: 500, pitch: '6/12', ridge_ft: 12 };
    const metrics: RoofMetrics = {
      facet_count: 1,
      per_facet: [perFacet],
      predominant_pitch: '6/12',
      area_per_pitch: { '6/12': 500 },
      ridge_ft: 12,
      footprint_sqft: 480,
      stories: 1,
      number_of_stories: '1',
    };
    expect(metrics.per_facet).toHaveLength(1);
    expect(metrics.area_per_pitch?.['6/12']).toBe(500);
  });

  test('MlFacetsResponse wraps facets and optional metrics', () => {
    const resp: MlFacetsResponse = {
      facets: [{ boundary: [[0, 0]] }],
      facet_count: 1,
      score: 0.9,
      total_area_sqft: 500,
      status: 'ok',
      metrics: { facet_count: 1 },
    };
    expect(resp.facets).toHaveLength(1);
    expect(resp.status).toBe('ok');
  });

  test('GeocodeResult / BlueprintRequest / BlueprintResponse shapes', () => {
    const geo: GeocodeResult = { lat: 40, lng: -80, address: '123 Main St' };
    const req: BlueprintRequest = {
      lat: 40,
      lng: -80,
      address: '123 Main St',
      facets: [{ boundary: [[40, -80]], area_sqft: 500, pitch: '6/12' }],
    };
    const resp: BlueprintResponse = { aerial: 'data:aerial', image: 'data:img', facets_only: 'data:fo' };
    expect(geo.address).toBe('123 Main St');
    expect(req.facets[0].pitch).toBe('6/12');
    expect(resp.aerial).toBe('data:aerial');
  });
});

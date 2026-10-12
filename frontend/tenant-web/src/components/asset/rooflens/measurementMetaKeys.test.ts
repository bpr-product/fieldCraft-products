import {
  roofLensExportToMetaKeyValues,
  roofLensExportToPerFacetMetaKeyValues,
  PER_FACET_METAKEYS,
} from "./measurementMetaKeys";
import { RoofLensExportData } from "./exportRoofLens";
import { RoofMetrics } from "./types";

const edge = (n: number) => ({
  ridge_ft: n,
  hip_ft: n,
  valley_ft: n,
  rake_ft: n,
  eave_ft: n,
  parapet_ft: 0,
});

const report = (area: number, over: Partial<RoofMetrics> = {}): RoofMetrics => ({
  ridge_ft: 10,
  hip_ft: 5,
  valley_ft: 4,
  rake_ft: 3,
  eave_ft: 20,
  predominant_pitch: "6/12",
  estimated_attic_sqft: 800.4,
  stories: 2,
  building_height_ft: 24.6,
  structure_complexity: "Simple",
  ...over,
  // area lives on the building summary, but keep a value here for reference
  facet_count: 1,
});

const exportData = (over: Partial<RoofLensExportData> = {}): RoofLensExportData =>
  ({
    product: "FIELDCRAFT RoofLens",
    generated: "2026-07-21",
    experimental: true,
    note: "Decision-support only",
    address: "557 Jupiter Dr",
    country: "United States",
    lat: 43.09,
    lng: -89.28,
    summary: {
      total_roof_area_sqft: 1515.6,
      facet_count: 3,
      total_edge_length_ft: 300,
      footprint_sqft: 1400,
      building_count: 1,
    },
    buildings: [
      {
        building_id: "B0",
        building_name: "Building 1",
        summary: { total_roof_area_sqft: 1515.6, facet_count: 3, total_edge_length_ft: 300, footprint_sqft: 1400 },
        roof_report: report(1515.6),
        facets: [],
      },
    ],
    facets: [
      {
        facet_id: "Facet 1",
        building_id: "B0",
        building_name: "Building 1",
        roof_area_sqft: 500.6,
        footprint_sqft: 480,
        pitch: "6/12",
        perimeter_ft: 90,
        source: "auto",
        edge_metrics: edge(11.4),
        boundary_latlng: [[43.09, -89.28]],
      },
    ],
    ...over,
  } as RoofLensExportData);

describe("roofLensExportToMetaKeyValues", () => {
  it("returns an empty object for null/undefined data", () => {
    expect(roofLensExportToMetaKeyValues(undefined as any)).toEqual({});
  });

  it("emits area & facet count under every alias, rounding area", () => {
    const out = roofLensExportToMetaKeyValues(exportData());
    // 1515.6 -> 1516 under all area aliases
    expect(out.area).toBe(1516);
    expect(out.TotalArea).toBe(1516);
    // facet count passes through under both aliases
    expect(out.facetCount).toBe(3);
    expect(out.TotalRoofFacets).toBe(3);
  });

  it("sums edge totals from the building roof report and derives ridge+hip", () => {
    const out = roofLensExportToMetaKeyValues(exportData());
    expect(out.ridge).toBe(10);
    expect(out.hip).toBe(5);
    expect(out.ridgeHip).toBe(15);
    expect(out.valley).toBe(4);
    expect(out.rake).toBe(3);
    expect(out.eave).toBe(20);
  });

  it("emits pitch/attic/stories/height/complexity from the predominant building", () => {
    const out = roofLensExportToMetaKeyValues(exportData());
    expect(out.predominantPitch).toBe("6/12");
    expect(out.pitch).toBe("6/12");
    expect(out.Pitch).toBe("6/12");
    expect(out.attic).toBe(800); // 800.4 rounded
    expect(out.stories).toBe(2);
    expect(out.height).toBe(25); // 24.6 rounded
    expect(out.complexity).toBe("Simple");
  });

  it("picks the largest-area building among those carrying a roof report", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "Small",
          summary: { total_roof_area_sqft: 200, facet_count: 1, total_edge_length_ft: 10, footprint_sqft: 190 },
          roof_report: report(200, { predominant_pitch: "3/12" }),
          facets: [],
        },
        {
          building_id: "B1",
          building_name: "Large",
          summary: { total_roof_area_sqft: 2000, facet_count: 1, total_edge_length_ft: 40, footprint_sqft: 1900 },
          roof_report: report(2000, { predominant_pitch: "9/12" }),
          facets: [],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.predominantPitch).toBe("9/12"); // from the larger building
    // ridge/hip totals sum across BOTH buildings (10+10 ridge, 5+5 hip)
    expect(out.ridge).toBe(20);
    expect(out.hip).toBe(10);
    expect(out.ridgeHip).toBe(30);
  });

  it("falls back to summing per-facet edge metrics when a building has no report", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          summary: { total_roof_area_sqft: 500, facet_count: 1, total_edge_length_ft: 20, footprint_sqft: 480 },
          roof_report: null,
          facets: [
            {
              facet_id: "Facet 1",
              building_id: "B0",
              building_name: "Building 1",
              roof_area_sqft: 500,
              footprint_sqft: 480,
              pitch: "6/12",
              perimeter_ft: 20,
              source: "auto",
              edge_metrics: edge(7),
              boundary_latlng: [],
            },
          ],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.ridge).toBe(7);
    expect(out.eave).toBe(7);
    // no roof report anywhere → pitch/attic/stories omitted
    expect(out.predominantPitch).toBeUndefined();
    expect(out.stories).toBeUndefined();
  });

  it("falls back to the flattened facet list when there are no buildings", () => {
    const data = exportData({
      buildings: [],
      facets: [
        {
          facet_id: "Facet 1",
          building_id: "B0",
          building_name: "Building 1",
          roof_area_sqft: 500,
          footprint_sqft: 480,
          pitch: "6/12",
          perimeter_ft: 20,
          source: "auto",
          edge_metrics: edge(9),
          boundary_latlng: [],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.ridge).toBe(9);
    expect(out.valley).toBe(9);
  });

  it("omits area/facet count when the summary lacks them", () => {
    const data = exportData({
      summary: { building_count: 1 } as any,
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.area).toBeUndefined();
    expect(out.facetCount).toBeUndefined();
  });
});

describe("roofLensExportToPerFacetMetaKeyValues", () => {
  it("returns an empty array when there are no facets", () => {
    expect(roofLensExportToPerFacetMetaKeyValues(undefined as any)).toEqual([]);
    expect(roofLensExportToPerFacetMetaKeyValues(exportData({ facets: undefined as any }))).toEqual([]);
  });

  it("maps each facet to its per-facet MetaKey row (rounded area + edge lengths, raw pitch)", () => {
    const rows = roofLensExportToPerFacetMetaKeyValues(exportData());
    expect(rows).toHaveLength(1);
    const r = rows[0];
    expect(r.area).toBe(501); // 500.6 rounded
    expect(r.TotalArea).toBe(501);
    expect(r.roof_area_sqft).toBe(501);
    expect(r.Pitch).toBe("6/12"); // raw string preserved
    expect(r.LengthRidge).toBe(11); // 11.4 rounded
    expect(r.LengthEave).toBe(11);
    expect(r.LengthValley).toBe(11);
    expect(r.LengthRake).toBe(11);
    expect(r.LengthHip).toBe(11);
  });

  it("omits area when it is not a finite number and omits blank pitch", () => {
    const data = exportData({
      facets: [
        {
          facet_id: "Facet 1",
          building_id: "B0",
          building_name: "Building 1",
          roof_area_sqft: NaN as any,
          footprint_sqft: 0,
          pitch: "   ",
          perimeter_ft: 0,
          source: "manual",
          edge_metrics: undefined as any,
          boundary_latlng: [],
        },
      ],
    });
    const rows = roofLensExportToPerFacetMetaKeyValues(data);
    expect(rows[0].area).toBeUndefined();
    expect(rows[0].Pitch).toBeUndefined();
    expect(rows[0].LengthRidge).toBeUndefined();
  });
});

describe("roofLensExportToMetaKeyValues — branch coverage", () => {
  it("treats missing edge fields on a roof report as zero", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          summary: { total_roof_area_sqft: 100, facet_count: 1, total_edge_length_ft: 0, footprint_sqft: 90 },
          // report with NO edge fields set → each `?? 0` right-hand side is taken
          roof_report: { predominant_pitch: "4/12" } as RoofMetrics,
          facets: [],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.ridge).toBe(0);
    expect(out.hip).toBe(0);
    expect(out.ridgeHip).toBe(0);
    expect(out.valley).toBe(0);
    expect(out.rake).toBe(0);
    expect(out.eave).toBe(0);
  });

  it("skips per-metric emits when the predominant report omits those fields", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          summary: { total_roof_area_sqft: 100, facet_count: 1, total_edge_length_ft: 0, footprint_sqft: 90 },
          // report present but pitch/attic/stories/height/complexity all absent
          roof_report: { ridge_ft: 1 } as RoofMetrics,
          facets: [],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.predominantPitch).toBeUndefined();
    expect(out.attic).toBeUndefined();
    expect(out.stories).toBeUndefined();
    expect(out.height).toBeUndefined();
    expect(out.complexity).toBeUndefined();
  });

  it("defaults buildings and facets to empty lists when both fields are absent", () => {
    const data = exportData({ buildings: undefined as any, facets: undefined as any });
    const out = roofLensExportToMetaKeyValues(data);
    // No buildings and no facets → all edge totals default to 0.
    expect(out.ridge).toBe(0);
  });

  it("skips facets with no edge_metrics when falling back to per-facet edge sums", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          summary: { total_roof_area_sqft: 100, facet_count: 2, total_edge_length_ft: 5, footprint_sqft: 90 },
          roof_report: null,
          facets: [
            {
              facet_id: "Facet 1",
              building_id: "B0",
              building_name: "Building 1",
              roof_area_sqft: 50,
              footprint_sqft: 45,
              pitch: "6/12",
              perimeter_ft: 5,
              source: "auto",
              edge_metrics: undefined as any, // no breakdown → skipped by the reducer
              boundary_latlng: [],
            },
            {
              facet_id: "Facet 2",
              building_id: "B0",
              building_name: "Building 1",
              roof_area_sqft: 50,
              footprint_sqft: 45,
              pitch: "6/12",
              perimeter_ft: 5,
              source: "auto",
              edge_metrics: { ridge_ft: 6 } as any, // partial → other edges via `?? 0`
              boundary_latlng: [],
            },
          ],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.ridge).toBe(6);
    expect(out.hip).toBe(0);
  });

  it("treats each individually-absent per-facet edge field as zero", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          summary: { total_roof_area_sqft: 100, facet_count: 1, total_edge_length_ft: 4, footprint_sqft: 90 },
          roof_report: null,
          facets: [
            {
              facet_id: "Facet 1",
              building_id: "B0",
              building_name: "Building 1",
              roof_area_sqft: 100,
              footprint_sqft: 90,
              pitch: "6/12",
              perimeter_ft: 4,
              source: "auto",
              // only valley set → ridge/hip/rake/eave take the `?? 0` right-hand side
              edge_metrics: { valley_ft: 4 } as any,
              boundary_latlng: [],
            },
          ],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.ridge).toBe(0);
    expect(out.hip).toBe(0);
    expect(out.rake).toBe(0);
    expect(out.eave).toBe(0);
    expect(out.valley).toBe(4);
  });

  it("handles a report-less building whose facets field is absent", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          summary: { total_roof_area_sqft: 0, facet_count: 0, total_edge_length_ft: 0, footprint_sqft: 0 },
          roof_report: null,
          facets: undefined as any, // → `?? []`
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.ridge).toBe(0);
  });

  it("sorts report-bearing buildings even when a summary area is absent", () => {
    const data = exportData({
      buildings: [
        {
          building_id: "B0",
          building_name: "No summary",
          summary: undefined as any, // → `?? 0` in the sort comparator
          roof_report: report(0, { predominant_pitch: "5/12" }),
          facets: [],
        },
      ],
    });
    const out = roofLensExportToMetaKeyValues(data);
    expect(out.predominantPitch).toBe("5/12");
  });
});

describe("roofLensExportToPerFacetMetaKeyValues — branch coverage", () => {
  it("omits individual edge lengths that are not finite numbers", () => {
    const data = exportData({
      facets: [
        {
          facet_id: "Facet 1",
          building_id: "B0",
          building_name: "Building 1",
          roof_area_sqft: 500,
          footprint_sqft: 480,
          pitch: "6/12",
          perimeter_ft: 90,
          source: "auto",
          // ridge finite; the rest absent → each `!== null` guard drops them
          edge_metrics: { ridge_ft: 12 } as any,
          boundary_latlng: [],
        },
      ],
    });
    const rows = roofLensExportToPerFacetMetaKeyValues(data);
    expect(rows[0].LengthRidge).toBe(12);
    expect(rows[0].LengthHip).toBeUndefined();
    expect(rows[0].LengthEave).toBeUndefined();
    expect(rows[0].LengthValley).toBeUndefined();
    expect(rows[0].LengthRake).toBeUndefined();
  });

  it("omits Pitch when the facet pitch is null (not merely blank)", () => {
    const rows = roofLensExportToPerFacetMetaKeyValues(
      exportData({
        facets: [
          {
            facet_id: "Facet 1",
            building_id: "B0",
            building_name: "Building 1",
            roof_area_sqft: 100,
            footprint_sqft: 90,
            pitch: null as any, // first operand of the pitch guard is false
            perimeter_ft: 10,
            source: "auto",
            edge_metrics: undefined as any,
            boundary_latlng: [],
          },
        ],
      })
    );
    expect(rows[0].Pitch).toBeUndefined();
    expect(rows[0].area).toBe(100);
  });
});

describe("PER_FACET_METAKEYS", () => {
  it("exposes the per-facet vocabulary as a Set", () => {
    expect(PER_FACET_METAKEYS.has("area")).toBe(true);
    expect(PER_FACET_METAKEYS.has("LengthRidge")).toBe(true);
    expect(PER_FACET_METAKEYS.has("Pitch")).toBe(true);
    expect(PER_FACET_METAKEYS.has("nope")).toBe(false);
  });
});

import { buildExportData } from "./exportRoofLens";
import { Building, RoofFacet, RoofMetrics } from "./types";

const path = [
  { lat: 43.0906, lng: -89.2818 },
  { lat: 43.0907, lng: -89.2819 },
  { lat: 43.0908, lng: -89.2818 },
];

const facet: RoofFacet = {
  id: "f1",
  buildingId: "B0",
  path,
  pitchRise: 7,
  isAuto: true,
  serviceAreaSqft: 445,
  edited: false,
  edgeMetrics: { ridge_ft: 1, hip_ft: 2, valley_ft: 3, rake_ft: 4, eave_ft: 5, parapet_ft: 6 },
};

const buildings: Building[] = [{ id: "B0", name: "Building 1" }];
const metrics = { predominant_pitch: "7/12" } as unknown as RoofMetrics;

describe("buildExportData", () => {
  const data = buildExportData(
    "557 Jupiter Dr",
    "United States",
    43.0906,
    -89.2818,
    buildings,
    [facet],
    { B0: metrics },
  );

  it("wraps with the product/disclaimer envelope and passes address/country/lat/lng", () => {
    expect(data.product).toBe("FIELDCRAFT RoofLens");
    expect(data.experimental).toBe(true);
    expect(data.note).toMatch(/Decision-support/i);
    expect(data.address).toBe("557 Jupiter Dr");
    expect(data.country).toBe("United States");
    expect(data.lat).toBe(43.0906);
    expect(data.lng).toBe(-89.2818);
  });

  it("groups facets into one building with the right id/name/count and roof_report", () => {
    expect(data.summary.building_count).toBe(1);
    expect(data.buildings).toHaveLength(1);
    expect(data.buildings[0].building_id).toBe("B0");
    expect(data.buildings[0].building_name).toBe("Building 1");
    expect(data.buildings[0].roof_report).toBe(metrics);
  });

  it("maps each facet: id, source, pitch, edge_metrics, boundary_latlng, area", () => {
    const f = data.facets[0];
    expect(f.facet_id).toBe("Facet 1");
    expect(f.building_id).toBe("B0");
    expect(f.source).toBe("auto");
    expect(f.pitch).toBe("7/12");
    expect(f.edge_metrics).toEqual(facet.edgeMetrics);
    expect(f.boundary_latlng).toEqual([
      [43.0906, -89.2818],
      [43.0907, -89.2819],
      [43.0908, -89.2818],
    ]);
    expect(f.roof_area_sqft).toBe(445); // serviceAreaSqft (unedited)
  });

  it("defaults country to empty string when not provided", () => {
    const d = buildExportData("addr", "", 1, 2, buildings, [facet], { B0: null });
    expect(d.country).toBe("");
    expect(d.buildings[0].roof_report).toBeNull();
  });
});

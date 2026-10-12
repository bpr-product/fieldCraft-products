import { computeTotals } from "./rooflensStore";
import { footprintSqft, pitchFactor } from "../utils/geometry";
import { RoofFacet } from "../types";

const path = [
  { lat: 0, lng: 0 },
  { lat: 0, lng: 0.0009 },
  { lat: 0.0009, lng: 0.0009 },
  { lat: 0.0009, lng: 0 },
];

const facet = (over: Partial<RoofFacet>): RoofFacet => ({
  id: "f1",
  buildingId: "B0",
  path,
  pitchRise: 6,
  isAuto: true,
  serviceAreaSqft: null,
  edited: false,
  ...over,
});

describe("computeTotals", () => {
  it("uses serviceAreaSqft for an unedited auto facet", () => {
    const t = computeTotals([facet({ serviceAreaSqft: 500, edited: false })]);
    expect(t.facets[0].roofAreaSqft).toBe(500);
    expect(t.facets[0].source).toBe("auto");
    expect(t.facets[0].label).toBe("Facet 1");
    expect(t.facetCount).toBe(1);
  });

  it("falls back to footprint × pitchFactor when there is no service area", () => {
    const t = computeTotals([facet({ serviceAreaSqft: null, pitchRise: 12 })]);
    expect(t.facets[0].roofAreaSqft).toBeCloseTo(footprintSqft(path) * pitchFactor(12), 3);
  });

  it("classifies source: manual / auto-adjusted / auto", () => {
    expect(computeTotals([facet({ isAuto: false })]).facets[0].source).toBe("manual");
    expect(computeTotals([facet({ isAuto: true, edited: true })]).facets[0].source).toBe("auto-adjusted");
    expect(computeTotals([facet({ isAuto: true, edited: false })]).facets[0].source).toBe("auto");
  });

  it("sums totals across facets", () => {
    const t = computeTotals([
      facet({ id: "a", serviceAreaSqft: 100 }),
      facet({ id: "b", serviceAreaSqft: 200 }),
    ]);
    expect(t.totalRoofAreaSqft).toBe(300);
    expect(t.facetCount).toBe(2);
  });

  it("returns zeros for an empty facet list", () => {
    expect(computeTotals([])).toMatchObject({
      totalRoofAreaSqft: 0,
      footprintSqft: 0,
      perimeterFt: 0,
      facetCount: 0,
    });
  });
});

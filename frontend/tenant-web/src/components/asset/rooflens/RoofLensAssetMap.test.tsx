import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

// ── Google Maps context + RoofLens backend service ─────────────────────────────
// (jest.mock factories may only reference variables prefixed with `mock`.)
let mockIsLoaded = true;
jest.mock("../../../context/GoogleMapsContext", () => ({
  useGoogleMaps: () => ({ isLoaded: mockIsLoaded, mapApiKey: "k" }),
}));

const mockFetchFacets = jest.fn();
const mockFetchBlueprint = jest.fn();
const mockGeocodeAddress = jest.fn();
jest.mock("../../../services/RoofLensService", () => ({
  fetchFacets: (...a: any[]) => mockFetchFacets(...a),
  fetchBlueprint: (...a: any[]) => mockFetchBlueprint(...a),
  geocodeAddress: (...a: any[]) => mockGeocodeAddress(...a),
}));

// AB#28854 — off by default, matching the real shipped default (FeatureFlag.ts). Tests that
// need it on set mockAddressValidationFlag = true in their own body/beforeEach.
let mockAddressValidationFlag = false;
jest.mock("@openfeature/react-sdk", () => ({
  useFlag: (key: string) => ({ value: key === "rooflens-address-validation" ? mockAddressValidationFlag : false }),
}));

const mockValidatePropertyAddress = jest.fn();
jest.mock("../../../services/AssetService", () => ({
  validatePropertyAddress: (...a: any[]) => mockValidatePropertyAddress(...a),
}));

// Keep buildExportData real (its output is asserted) but stub the two side-effecting
// exporters — the real ones touch URL.createObjectURL / open a print window (no-ops in jsdom).
const mockExportJson = jest.fn();
const mockExportPdf = jest.fn();
jest.mock("./exportRoofLens", () => {
  const actual = jest.requireActual("./exportRoofLens");
  return {
    __esModule: true,
    ...actual,
    exportJson: (...a: any[]) => mockExportJson(...a),
    exportPdf: (...a: any[]) => mockExportPdf(...a),
  };
});

// Stub the map-heavy children so this suite tests the container's orchestration.
jest.mock("./RoofLensMap", () => ({
  __esModule: true,
  default: ({ onViewportCenter }: any) => (
    <button data-testid="viewport" onClick={() => onViewportCenter?.({ lat: 1, lng: 2 })}>
      map
    </button>
  ),
}));
jest.mock("./RoofLens3DView", () => ({
  __esModule: true,
  default: () => <div data-testid="rl-3d">3d</div>,
}));
jest.mock("./RoofLensSearchBar", () => ({
  __esModule: true,
  default: ({ onPick }: any) => (
    <button
      data-testid="search-pick"
      onClick={() => onPick(43.09, -89.28, "557 Jupiter Dr", { country: "United States" })}
    >
      search
    </button>
  ),
}));
// Two distinct fake coordinates so a supersede-guard test can tell which manual pin
// move a given reverse-geocode call/result belongs to.
jest.mock("./RoofLensMap", () => ({
  __esModule: true,
  default: ({ onManualPinMove }: any) => (
    <>
      <button data-testid="manual-pin-move" onClick={() => onManualPinMove({ lat: 43.5, lng: -89.5 })}>
        move-pin
      </button>
      <button data-testid="manual-pin-move-2" onClick={() => onManualPinMove({ lat: 44.5, lng: -90.5 })}>
        move-pin-2
      </button>
    </>
  ),
}));

import RoofLensAssetMap from "./RoofLensAssetMap";
import { useRoofLensStore } from "./store/rooflensStore";

const facetsResponse = {
  status: "ok",
  facets: [{ boundary: [[43.09, -89.28], [43.091, -89.281], [43.092, -89.28]], pitch_ratio: "6/12", area_sqft: 480 }],
  metrics: {
    predominant_pitch: "6/12",
    per_facet: [{ ridge_ft: 10, hip_ft: 5, valley_ft: 4, rake_ft: 3, eave_ft: 20, parapet_ft: 0 }],
  },
};

let keySeq = 0;
const freshKey = () => `asset-${keySeq++}`;

// A Google geocoder that answers both forward (address→coords) and reverse (coords→country).
function installGeocoder() {
  class Geocoder {
    geocode(req: any, cb: (r: any[] | null, s: string) => void) {
      if (req.location) {
        cb([{ address_components: [{ long_name: "United States", types: ["country"] }] }], "OK");
      } else {
        cb([{ geometry: { location: { lat: () => 43.09, lng: () => -89.28 } } }], "OK");
      }
    }
  }
  (global as any).google = { maps: { Geocoder } };
}

beforeEach(() => {
  mockIsLoaded = true;
  mockFetchFacets.mockReset();
  mockFetchBlueprint.mockReset();
  mockGeocodeAddress.mockReset();
  mockExportJson.mockReset();
  mockExportPdf.mockReset();
  mockAddressValidationFlag = false;
  mockValidatePropertyAddress.mockReset();
  useRoofLensStore.getState().reset();
});

afterEach(() => {
  delete (global as any).google;
});

describe("RoofLensAssetMap", () => {
  it("shows a loading placeholder until the Maps API is ready", () => {
    mockIsLoaded = false;
    render(<RoofLensAssetMap assetKey={freshKey()} />);
    expect(screen.getByText(/Loading map/i)).toBeInTheDocument();
    expect(screen.getByText(/Experimental · internal/)).toBeInTheDocument();
  });

  it("prompts for an address before showing the 2D map when nothing is located", () => {
    render(<RoofLensAssetMap assetKey={freshKey()} />);
    expect(screen.getByText(/Search a property address above/i)).toBeInTheDocument();
  });

  it("keeps showing the address-prompt placeholder in 3D view too when nothing is located — never the globe default", () => {
    render(<RoofLensAssetMap assetKey={freshKey()} />);
    fireEvent.click(screen.getByRole("button", { name: /3D View/i }));
    expect(screen.getByText(/Search a property address above/i)).toBeInTheDocument();
    expect(screen.queryByTestId("rl-3d")).not.toBeInTheDocument();
  });

  it("shows the 3D view once a property is located, and ignores a de-select click", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} />);
    fireEvent.click(screen.getByTestId("search-pick")); // locates a property
    await waitFor(() => expect(useRoofLensStore.getState().facets.length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: /3D View/i }));
    expect(screen.getByTestId("rl-3d")).toBeInTheDocument();
    // Clicking the already-active toggle yields a null value → the view is left unchanged.
    fireEvent.click(screen.getByRole("button", { name: /3D View/i }));
    expect(screen.getByTestId("rl-3d")).toBeInTheDocument();
  });

  it("does not reset the store when re-rendered with the same asset key", () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const key = freshKey();
    const { rerender } = render(<RoofLensAssetMap assetKey={key} />);
    useRoofLensStore.setState({ facets: [] });
    const reset = jest.spyOn(useRoofLensStore.getState(), "reset");
    rerender(<RoofLensAssetMap assetKey={key} address="unchanged" />);
    expect(reset).not.toHaveBeenCalled(); // same key → no reset
    reset.mockRestore();
  });

  // PR 8396 review (Raja) — the app's asset editor is one SPA route, so switching assets
  // re-renders this component with a new assetKey rather than remounting it. Before this fix,
  // hasLocation/center/addr stayed stuck from the previous asset, so a user landing on an
  // address-less asset could see the PREVIOUS asset's location rendered under the new one.
  it("resets hasLocation/center/addr on asset switch, even without unmounting", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const { rerender } = render(
      <RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" />
    );
    // Asset A is located — the placeholder must be gone, the map showing.
    await waitFor(() =>
      expect(screen.queryByText(/Search a property address above/i)).not.toBeInTheDocument()
    );

    // Switch to asset B: new assetKey, no saved pin, no saved address.
    rerender(<RoofLensAssetMap assetKey={freshKey()} />);

    // hasLocation must reset to false — asset A's stale center must never render for asset B.
    expect(await screen.findByText(/Search a property address above/i)).toBeInTheDocument();
    expect(screen.queryByTestId("rl-3d")).not.toBeInTheDocument();
    expect(screen.queryByTestId("viewport")).not.toBeInTheDocument();
  });

  it("restores a saved measurement that carries no lat/lng without recentering", async () => {
    const saved = JSON.stringify({
      product: "FIELDCRAFT RoofLens",
      // no lat / lng on purpose
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          roof_report: null,
          facets: [
            {
              building_id: "B0",
              source: "manual",
              pitch: "6/12",
              boundary_latlng: [[43.09, -89.28], [43.091, -89.281], [43.092, -89.28]],
            },
          ],
        },
      ],
      facets: [],
    });
    render(<RoofLensAssetMap assetKey={freshKey()} savedMeasurement={saved} />);
    await waitFor(() => expect(useRoofLensStore.getState().facets.length).toBeGreaterThan(0));
    expect(mockFetchFacets).not.toHaveBeenCalled();
  });

  it("runs Auto-Detect on address pick, commits facets, and reports area + metrics", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onAreaChange = jest.fn();
    const onMetricsComputed = jest.fn();
    const onLocationPicked = jest.fn();
    render(
      <RoofLensAssetMap
        assetKey={freshKey()}
        onAreaChange={onAreaChange}
        onMetricsComputed={onMetricsComputed}
        onLocationPicked={onLocationPicked}
      />
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });

    expect(onLocationPicked).toHaveBeenCalledWith("43.09,-89.28", { country: "United States" });
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    expect(mockFetchFacets).toHaveBeenCalledWith(43.09, -89.28);
    expect(onMetricsComputed).toHaveBeenCalled();
    // Human-correction-priority regression fix: a genuine Auto-Detect run must tag its
    // onAreaChange call as such, so the caller (CreateAsset_v3) knows it's safe to guard
    // against overwriting an already-answered Area field.
    expect(onAreaChange).toHaveBeenCalledWith(expect.any(Number), { isAutoDetect: true });
    // The measurements panel is now populated for the located building.
    expect(await screen.findByText(/total roof area/)).toBeInTheDocument();
  });

  it("reports a manual facet-area edit's recompute WITHOUT the isAutoDetect flag, so it always propagates (adversarial live-bug regression)", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onAreaChange = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} onAreaChange={onAreaChange} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    expect(onAreaChange).toHaveBeenLastCalledWith(expect.any(Number), { isAutoDetect: true });
    onAreaChange.mockClear();

    // Simulates the Live Measurements panel's per-facet "Roof area" edit — a direct human
    // correction, not an Auto-Detect run.
    const facetId = useRoofLensStore.getState().facets[0].id;
    await act(async () => {
      useRoofLensStore.getState().updateFacetArea(facetId, 1234);
    });

    expect(onAreaChange).toHaveBeenCalledWith(expect.any(Number), { isAutoDetect: false });
  });

  it("commits facets without edge breakdowns when the service returns no metrics", async () => {
    const onMetricsComputed = jest.fn();
    mockFetchFacets.mockResolvedValue({
      status: "ok",
      // facet with no per-facet metrics, and no top-level metrics object at all
      facets: [{ boundary: [[43.09, -89.28], [43.091, -89.281], [43.092, -89.28]], area_sqft: 300 }],
    });
    render(<RoofLensAssetMap assetKey={freshKey()} onMetricsComputed={onMetricsComputed} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    // No metrics returned → no edge breakdown attached and no metrics callback fired.
    expect(useRoofLensStore.getState().facets[0].edgeMetrics).toBeUndefined();
    expect(onMetricsComputed).not.toHaveBeenCalled();
  });

  it("surfaces a status message when no rooftop is detected", async () => {
    mockFetchFacets.mockResolvedValue({ status: "no_building", facets: [], message: "No rooftop found here." });
    render(<RoofLensAssetMap assetKey={freshKey()} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });
    expect(await screen.findByText(/No rooftop found here/)).toBeInTheDocument();
    expect(useRoofLensStore.getState().facets).toHaveLength(0);
  });

  it("reports a measurement failure when the service throws", async () => {
    mockFetchFacets.mockRejectedValue(new Error("503 down"));
    render(<RoofLensAssetMap assetKey={freshKey()} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });
    expect(await screen.findByText(/Measurement failed: 503 down/)).toBeInTheDocument();
  });

  it("auto-detects on load from a saved pin without a manual search", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" />);
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledWith(43.09, -89.28));
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
  });

  it("restores a previously-saved measurement instead of re-detecting", async () => {
    const saved = JSON.stringify({
      product: "FIELDCRAFT RoofLens",
      lat: 43.09,
      lng: -89.28,
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          roof_report: null,
          facets: [
            {
              building_id: "B0",
              source: "manual",
              pitch: "6/12",
              boundary_latlng: [[43.09, -89.28], [43.091, -89.281], [43.092, -89.28]],
            },
          ],
        },
      ],
      facets: [],
    });
    render(<RoofLensAssetMap assetKey={freshKey()} savedMeasurement={saved} />);
    await waitFor(() => expect(useRoofLensStore.getState().facets.length).toBeGreaterThan(0));
    expect(mockFetchFacets).not.toHaveBeenCalled();
  });

  it("ignores malformed saved measurement JSON without crashing", () => {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
    render(<RoofLensAssetMap assetKey={freshKey()} savedMeasurement="{not json" />);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("reports the finalized measurement, and null once there are no facets", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onMeasurementChange = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} onMeasurementChange={onMeasurementChange} />);
    // Empty on mount → null.
    expect(onMeasurementChange).toHaveBeenLastCalledWith(null);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    const last = onMeasurementChange.mock.calls[onMeasurementChange.mock.calls.length - 1][0];
    expect(last).toMatchObject({ product: "FIELDCRAFT RoofLens" });
  });

  it("exports JSON, reverse-geocoding the country when it is unknown", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installGeocoder();
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Export JSON/i }));
    });
    expect(mockExportJson).toHaveBeenCalledTimes(1);
    expect(mockExportJson.mock.calls[0][0].country).toBe("United States");
  });

  it("exports JSON with an empty country when reverse-geocoding finds none", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    class Geocoder {
      geocode(req: any, cb: (r: any[] | null, s: string) => void) {
        if (req.location) cb([], "ZERO_RESULTS"); // reverse → no result
        else cb([{ geometry: { location: { lat: () => 43.09, lng: () => -89.28 } } }], "OK");
      }
    }
    (global as any).google = { maps: { Geocoder } };
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Export JSON/i }));
    });
    expect(mockExportJson.mock.calls[0][0].country).toBe("");
  });

  it("exports JSON with an empty country when the reverse geocoder throws", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    class Geocoder {
      geocode() {
        throw new Error("geocoder unavailable");
      }
    }
    (global as any).google = { maps: { Geocoder } };
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Export JSON/i }));
    });
    expect(mockExportJson.mock.calls[0][0].country).toBe("");
  });

  it("exports a PDF, fetching blueprint diagrams first", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    mockFetchBlueprint.mockResolvedValue({ aerial: "img" });
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" country="United States" />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Export PDF/i }));
    });
    expect(mockFetchBlueprint).toHaveBeenCalledTimes(1);
    expect(mockExportPdf).toHaveBeenCalledTimes(1);
    expect(mockExportPdf.mock.calls[0][1]).toEqual({ aerial: "img" });
  });

  it("still exports the PDF without diagrams when the blueprint fetch fails", async () => {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
    mockFetchFacets.mockResolvedValue(facetsResponse);
    mockFetchBlueprint.mockRejectedValue(new Error("502"));
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" country="United States" />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Export PDF/i }));
    });
    expect(mockExportPdf).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Diagram images unavailable/)).toBeInTheDocument();
    warn.mockRestore();
  });

  // Real live bug, reported with a precise repro and diagnosed via console logging: manually
  // searching an address (committing real facets), then applying a DIFFERENT address
  // programmatically (e.g. checking "Use customer billing address as asset address", which sets
  // a new `address` prop and clears `location` — CreateAsset_v3.tsx's own documented contract)
  // silently did nothing — no new pin, no re-detection — because the effect's old
  // `facets.length > 0` guard couldn't tell "an asset that already has saved measurements from a
  // prior edit session" apart from "facets exist but the address changed since they were
  // committed." Fixed by tracking the last address the effect actually processed.
  it("re-detects when the address changes AFTER a manual search already committed facets (real live bug)", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installGeocoder();
    const key = freshKey();
    const { rerender } = render(<RoofLensAssetMap assetKey={key} />);

    await act(async () => { fireEvent.click(screen.getByTestId("search-pick")); });
    await waitFor(() => expect(useRoofLensStore.getState().facets.length).toBeGreaterThan(0));
    expect(mockFetchFacets).toHaveBeenCalledTimes(1);

    // A different address applied programmatically afterward, `location` cleared — exactly
    // CreateAsset_v3.tsx's handleUseBillingAddressChange's contract.
    rerender(<RoofLensAssetMap assetKey={key} location={undefined} address="Vepery Street, Chennai, Tamil Nadu, 600003" />);

    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(2));
  });

  // Adversarial-review finding: the original version of this test re-rendered with only
  // `country` changed, which isn't one of the effect's own dependencies
  // (`[location, address, assetKey, facets.length, isLoaded]`) — so the effect never actually
  // re-ran at all, and the test passed for a trivial reason rather than proving the
  // `lastAutoDetectAddrRef` guard itself. Fixed: force a REAL re-run by toggling `isLoaded`
  // (a genuine dependency) while keeping `address`/`location` identical throughout.
  it("does NOT re-detect when the effect genuinely re-runs but the address/location are unchanged (no regression to the once-per-asset intent)", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installGeocoder();
    const key = freshKey();
    const address = "Vepery Street, Chennai, Tamil Nadu, 600003";
    const { rerender } = render(<RoofLensAssetMap assetKey={key} address={address} />);

    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(1));

    // Force a genuine re-run of the effect (isLoaded flipping is a real dependency) with the
    // SAME address — must still no-op, not re-detect a second time for the identical address.
    mockIsLoaded = false;
    rerender(<RoofLensAssetMap assetKey={key} address={address} />);
    mockIsLoaded = true;
    rerender(<RoofLensAssetMap assetKey={key} address={address} />);
    await act(async () => {});

    expect(mockFetchFacets).toHaveBeenCalledTimes(1);
  });

  it("geocodes a saved address (no pin) via the browser geocoder and auto-detects", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installGeocoder();
    render(<RoofLensAssetMap assetKey={freshKey()} address="820 Congress Ave" />);
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledWith(43.09, -89.28));
  });

  it("falls back to the RoofLens geocode service when the browser geocoder misses", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    mockGeocodeAddress.mockResolvedValue({ lat: 10, lng: 20 });
    class Geocoder {
      geocode(_r: any, cb: (r: any[] | null, s: string) => void) {
        cb(null, "ZERO_RESULTS");
      }
    }
    (global as any).google = { maps: { Geocoder } };
    render(<RoofLensAssetMap assetKey={freshKey()} address="nowhere in particular" />);
    await waitFor(() => expect(mockGeocodeAddress).toHaveBeenCalledWith("nowhere in particular"));
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledWith(10, 20));
  });

  it("stays put when both geocoders fail for an address-only asset", async () => {
    class Geocoder {
      geocode(_r: any, cb: (r: any[] | null, s: string) => void) {
        cb(null, "ZERO_RESULTS");
      }
    }
    (global as any).google = { maps: { Geocoder } };
    mockGeocodeAddress.mockRejectedValue(new Error("no match"));
    render(<RoofLensAssetMap assetKey={freshKey()} address="void" />);
    await act(async () => {});
    expect(mockFetchFacets).not.toHaveBeenCalled();
  });

  it("queues a detection requested mid-flight and supersedes the first", async () => {
    let resolveFirst: (v: any) => void = () => {};
    mockFetchFacets
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; }))
      .mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick")); // run 1 — in flight
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick")); // run 2 — queued behind run 1
    });
    await act(async () => {
      resolveFirst(facetsResponse); // run 1 returns stale → discarded → run 2 fires
    });

    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    expect(mockFetchFacets.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("discards a detection whose active building changed while it was in flight", async () => {
    let resolveFirst: (v: any) => void = () => {};
    mockFetchFacets.mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; }));
    render(<RoofLensAssetMap assetKey={freshKey()} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });
    // User switches the target building mid-detection.
    act(() => useRoofLensStore.setState({ activeBuildingId: "B-OTHER" }));
    await act(async () => {
      resolveFirst(facetsResponse);
    });
    expect(useRoofLensStore.getState().facets).toHaveLength(0); // stale result dropped
  });
});

// AB#28854 — the flag-ON path was entirely untested by the initial pass (fieldcraft-reviewer finding):
// mockAddressValidationFlag/mockValidatePropertyAddress existed but were never exercised. These
// prove the actual accept/reject/warning behavior, not just that the flag-off default is safe.
describe("RoofLensAssetMap — address validation (AB#28854, flag ON)", () => {
  it("accepted (VALID/ROOF_FOUND): proceeds exactly as flag-off would — map pans, form fed, detection runs", async () => {
    mockAddressValidationFlag = true;
    mockValidatePropertyAddress.mockResolvedValue({
      ok: true,
      data: { accepted: true, addressStatus: "VALID", roofStatus: "ROOF_FOUND", message: null },
    });
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} accessToken="token" onLocationPicked={onLocationPicked} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });

    expect(mockValidatePropertyAddress).toHaveBeenCalledWith("557 Jupiter Dr", undefined, undefined, undefined, "token");
    expect(onLocationPicked).toHaveBeenCalledWith("43.09,-89.28", { country: "United States" });
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    expect(screen.queryByText(/rejected/i)).not.toBeInTheDocument();
  });

  it("rejected (accepted:false): shows an error banner, never touches map/form state, never runs detection", async () => {
    mockAddressValidationFlag = true;
    mockValidatePropertyAddress.mockResolvedValue({
      ok: true,
      data: { accepted: false, addressStatus: "VALID", roofStatus: "NO_ROOF", message: "No rooftop was found at this address." },
    });
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} accessToken="token" onLocationPicked={onLocationPicked} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });

    expect(await screen.findByText("No rooftop was found at this address.")).toBeInTheDocument();
    expect(onLocationPicked).not.toHaveBeenCalled();
    expect(mockFetchFacets).not.toHaveBeenCalled();
    // Rejected → hasLocation never set → still on the "search an address" empty state.
    expect(screen.getByText(/Search a property address above/i)).toBeInTheDocument();
  });

  it("UNVERIFIED (couldn't check): accepts AND shows the warning — it must not be silently dropped", async () => {
    mockAddressValidationFlag = true;
    mockValidatePropertyAddress.mockResolvedValue({
      ok: true,
      data: { accepted: true, addressStatus: "UNVERIFIED", roofStatus: "UNVERIFIED", message: "Could not verify this address right now — it was accepted, but please double-check it." },
    });
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} accessToken="token" onLocationPicked={onLocationPicked} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });

    // Proceeds (fail-open)...
    expect(onLocationPicked).toHaveBeenCalled();
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    // ...AND the warning is actually visible, not clobbered by runSam2's own setStatus(null).
    expect(await screen.findByText(/Could not verify this address right now/)).toBeInTheDocument();
  });

  it("the validation CALL itself failing (network/5xx): accepts with a warning, same as a server-reported UNVERIFIED", async () => {
    mockAddressValidationFlag = true;
    mockValidatePropertyAddress.mockResolvedValue({ ok: false, status: 500 });
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} accessToken="token" onLocationPicked={onLocationPicked} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });

    expect(onLocationPicked).toHaveBeenCalled();
    expect(await screen.findByText(/Could not verify this address right now/)).toBeInTheDocument();
  });

  it("flag ON but no accessToken: skips the call entirely and behaves like flag-off (fail-open by omission)", async () => {
    mockAddressValidationFlag = true;
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} onLocationPicked={onLocationPicked} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });

    expect(mockValidatePropertyAddress).not.toHaveBeenCalled();
    expect(onLocationPicked).toHaveBeenCalled();
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
  });

  it("a rejected pick reports false via onAddressValidated with the rejection reason", async () => {
    mockAddressValidationFlag = true;
    mockValidatePropertyAddress.mockResolvedValue({
      ok: true,
      data: { accepted: false, addressStatus: "INVALID", roofStatus: null, message: "This doesn't look like a valid address." },
    });
    const onAddressValidated = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} accessToken="token" onAddressValidated={onAddressValidated} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick"));
    });

    expect(onAddressValidated).toHaveBeenCalledWith(false, "This doesn't look like a valid address.");
  });

  it("a slower rejected pick that resolves AFTER a faster accepted re-pick does not clobber the newer pick's state (supersede guard)", async () => {
    mockAddressValidationFlag = true;
    let resolveFirst: (v: any) => void = () => {};
    mockValidatePropertyAddress
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; })) // pick 1 — slow, will reject
      .mockResolvedValueOnce({ ok: true, data: { accepted: true, addressStatus: "VALID", roofStatus: "ROOF_FOUND", message: null } }); // pick 2 — fast, accepts
    mockFetchFacets.mockResolvedValue(facetsResponse);
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} accessToken="token" onLocationPicked={onLocationPicked} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick")); // pick 1 — in flight, not yet resolved
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("search-pick")); // pick 2 — completes and commits first
    });
    expect(onLocationPicked).toHaveBeenCalledTimes(1);

    // Pick 1 now resolves as a REJECTION, after pick 2 already committed as accepted.
    await act(async () => {
      resolveFirst({ ok: true, data: { accepted: false, addressStatus: "INVALID", roofStatus: null, message: "stale rejection" } });
    });

    // The stale rejection must not retroactively show an error banner or undo pick 2's commit.
    expect(screen.queryByText("stale rejection")).not.toBeInTheDocument();
    expect(onLocationPicked).toHaveBeenCalledTimes(1);
  });
});

// Human-correction-priority fix: re-running Auto-Detect on a building that already has
// manually-drawn or manually-adjusted facets must confirm before silently replacing them.
describe("RoofLensAssetMap — confirm before Auto-Detect replaces manual/edited facets", () => {
  // source: "manual" hydrates to isAuto:false (rooflensStore.hydrateFromExport), and
  // hydrateFromExport sets activeBuildingId to the first (only) building here, "B0" —
  // matching the manual facet's building_id, so the guard's building-scoped check applies.
  const savedWithManualFacet = JSON.stringify({
    product: "FIELDCRAFT RoofLens",
    lat: 43.09,
    lng: -89.28,
    buildings: [
      {
        building_id: "B0",
        building_name: "Building 1",
        roof_report: null,
        facets: [
          {
            building_id: "B0",
            source: "manual",
            pitch: "6/12",
            boundary_latlng: [[43.09, -89.28], [43.091, -89.281], [43.092, -89.28]],
          },
        ],
      },
    ],
    facets: [],
  });

  const clickAutoDetect = () => fireEvent.click(screen.getByRole("button", { name: /Auto-Detect Roof/i }));

  it("shows a confirmation dialog instead of detecting when the active building has a MANUAL facet", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} savedMeasurement={savedWithManualFacet} />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    expect(useRoofLensStore.getState().facets[0].isAuto).toBe(false);

    await act(async () => {
      clickAutoDetect();
    });

    expect(screen.getByText(/Replace manually-edited facets/i)).toBeInTheDocument();
    expect(mockFetchFacets).not.toHaveBeenCalled();
  });

  it("also shows the dialog when the active building has an EDITED auto-detected facet (not just a manual one)", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));

    // Simulate the user having adjusted (dragged) this auto-detected facet.
    act(() => {
      const state = useRoofLensStore.getState();
      useRoofLensStore.setState({
        facets: state.facets.map((f) => ({ ...f, edited: true })),
      });
    });
    mockFetchFacets.mockClear();

    await act(async () => {
      clickAutoDetect();
    });

    expect(screen.getByText(/Replace manually-edited facets/i)).toBeInTheDocument();
    expect(mockFetchFacets).not.toHaveBeenCalled();
  });

  it("proceeds with detection and replaces the facets once the dialog is confirmed", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} savedMeasurement={savedWithManualFacet} />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));

    await act(async () => {
      clickAutoDetect();
    });
    expect(screen.getByText(/Replace manually-edited facets/i)).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Replace facets/i }));
    });

    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(1));
    // The manual facet is gone, replaced by the freshly-detected (auto) one.
    expect(useRoofLensStore.getState().facets).toHaveLength(1);
    expect(useRoofLensStore.getState().facets[0].isAuto).toBe(true);
    // MUI's Dialog unmounts its content after its exit transition — assert with
    // waitFor rather than a synchronous check right after the click.
    await waitFor(() => expect(screen.queryByText(/Replace manually-edited facets/i)).not.toBeInTheDocument());
  });

  it("cancels without touching the existing manual facet or calling the detection service", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} savedMeasurement={savedWithManualFacet} />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    const originalFacetId = useRoofLensStore.getState().facets[0].id;

    await act(async () => {
      clickAutoDetect();
    });
    expect(screen.getByText(/Replace manually-edited facets/i)).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    });

    // MUI's Dialog unmounts its content after its exit transition — assert with
    // waitFor rather than a synchronous check right after the click.
    await waitFor(() => expect(screen.queryByText(/Replace manually-edited facets/i)).not.toBeInTheDocument());
    expect(mockFetchFacets).not.toHaveBeenCalled();
    expect(useRoofLensStore.getState().facets).toHaveLength(1);
    expect(useRoofLensStore.getState().facets[0].id).toBe(originalFacetId);
    expect(useRoofLensStore.getState().facets[0].isAuto).toBe(false);
  });

  it("does NOT replace a DIFFERENT building's facets if the active building changed while the dialog was open (adversarial-review finding)", async () => {
    const savedWithTwoManualBuildings = JSON.stringify({
      product: "FIELDCRAFT RoofLens",
      lat: 43.09,
      lng: -89.28,
      buildings: [
        {
          building_id: "B0",
          building_name: "Building 1",
          roof_report: null,
          facets: [
            {
              building_id: "B0",
              source: "manual",
              pitch: "6/12",
              boundary_latlng: [[43.09, -89.28], [43.091, -89.281], [43.092, -89.28]],
            },
          ],
        },
        {
          building_id: "B1",
          building_name: "Building 2",
          roof_report: null,
          facets: [
            {
              building_id: "B1",
              source: "manual",
              pitch: "4/12",
              boundary_latlng: [[43.19, -89.38], [43.191, -89.381], [43.192, -89.38]],
            },
          ],
        },
      ],
      facets: [],
    });

    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} savedMeasurement={savedWithTwoManualBuildings} />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(2));
    expect(useRoofLensStore.getState().activeBuildingId).toBe("B0");
    const b1FacetIdBefore = useRoofLensStore.getState().facets.find((f) => f.buildingId === "B1")!.id;

    // Dialog opens for B0 (the active building at click time).
    await act(async () => {
      clickAutoDetect();
    });
    expect(screen.getByText(/Replace manually-edited facets/i)).toBeInTheDocument();

    // User switches the active building to B1 WHILE the dialog is still open —
    // the confirmation on screen was never given for B1's facets.
    act(() => {
      useRoofLensStore.setState({ activeBuildingId: "B1" });
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Replace facets/i }));
    });

    // The stale confirmation must be discarded, not silently applied to B1.
    expect(mockFetchFacets).not.toHaveBeenCalled();
    const facetsAfter = useRoofLensStore.getState().facets;
    expect(facetsAfter).toHaveLength(2);
    const b1FacetAfter = facetsAfter.find((f) => f.buildingId === "B1")!;
    expect(b1FacetAfter.id).toBe(b1FacetIdBefore);
    expect(b1FacetAfter.isAuto).toBe(false);
    await waitFor(() => expect(screen.queryByText(/Replace manually-edited facets/i)).not.toBeInTheDocument());
  });

  it("does NOT prompt when every existing facet in the active building is untouched auto-detected work (no new friction on the common case)", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    render(<RoofLensAssetMap assetKey={freshKey()} location="43.09,-89.28" />);
    await waitFor(() => expect(useRoofLensStore.getState().facets).toHaveLength(1));
    expect(useRoofLensStore.getState().facets[0].isAuto).toBe(true);
    expect(useRoofLensStore.getState().facets[0].edited).toBe(false);
    mockFetchFacets.mockClear();

    await act(async () => {
      clickAutoDetect();
    });

    expect(screen.queryByText(/Replace manually-edited facets/i)).not.toBeInTheDocument();
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(1));
  });
});

describe("RoofLensAssetMap — manual pin placement (drag/click on the map)", () => {
  // A manually dropped/dragged pin must behave exactly like a real address-search pick:
  // reverse-geocoded, fed through the same handleSearchPick pipeline (address-fill,
  // facet-clear, Auto-Detect), not a separate lighter-weight path.

  it("reverse-geocodes the dropped point and runs Auto-Detect through the same pipeline as a search pick", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installGeocoder();
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} onLocationPicked={onLocationPicked} />);
    fireEvent.click(screen.getByTestId("search-pick")); // locate a property first — the map only mounts once located
    await waitFor(() => expect(useRoofLensStore.getState().facets.length).toBeGreaterThan(0));
    onLocationPicked.mockClear();
    mockFetchFacets.mockClear();

    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-pin-move"));
    });

    expect(onLocationPicked).toHaveBeenCalledWith("43.5,-89.5",
      { address: "", city: "", state: "", country: "United States", zipCode: "" });
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledWith(43.5, -89.5));
  });

  it("still moves the pin (fail-open) when reverse geocoding finds no address for the dropped point", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installGeocoder();
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} onLocationPicked={onLocationPicked} />);
    fireEvent.click(screen.getByTestId("search-pick"));
    await waitFor(() => expect(useRoofLensStore.getState().facets.length).toBeGreaterThan(0));
    onLocationPicked.mockClear();

    // Swap in a geocoder that finds nothing for the reverse lookup specifically.
    class NoResultGeocoder {
      geocode(req: any, cb: (r: any[] | null, s: string) => void) {
        if (req.location) cb([], "ZERO_RESULTS");
        else cb([{ geometry: { location: { lat: () => 43.09, lng: () => -89.28 } } }], "OK");
      }
    }
    (global as any).google = { maps: { Geocoder: NoResultGeocoder } };

    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-pin-move"));
    });

    expect(onLocationPicked).toHaveBeenCalledWith("43.5,-89.5", undefined);
  });

  it("a stale in-flight manual-pin reverse-geocode does not overwrite a newer manual pin's result (supersede guard)", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installGeocoder();
    const onLocationPicked = jest.fn();
    render(<RoofLensAssetMap assetKey={freshKey()} onLocationPicked={onLocationPicked} />);
    fireEvent.click(screen.getByTestId("search-pick"));
    await waitFor(() => expect(useRoofLensStore.getState().facets.length).toBeGreaterThan(0));
    onLocationPicked.mockClear();

    const pending: Array<{ cb: (r: any[] | null, s: string) => void; lat: number }> = [];
    class QueueingGeocoder {
      geocode(req: any, cb: (r: any[] | null, s: string) => void) {
        if (req.location) {
          pending.push({ cb, lat: req.location.lat });
        } else {
          cb([{ geometry: { location: { lat: () => 43.09, lng: () => -89.28 } } }], "OK");
        }
      }
    }
    (global as any).google = { maps: { Geocoder: QueueingGeocoder } };

    // Pin move #1 (lat 43.5) fires first but will resolve LAST — stale by the time it lands.
    fireEvent.click(screen.getByTestId("manual-pin-move"));
    // Pin move #2 (lat 44.5) fires second but will resolve FIRST — the newer, correct result.
    fireEvent.click(screen.getByTestId("manual-pin-move-2"));
    expect(pending).toHaveLength(2);

    await act(async () => {
      pending[1].cb([{ address_components: [{ long_name: "Canada", types: ["country"] }] }], "OK");
    });
    await act(async () => {
      pending[0].cb([{ address_components: [{ long_name: "United States", types: ["country"] }] }], "OK");
    });

    // Only the newer pick (#2) should have committed — the stale #1 result must be dropped,
    // not land afterward and silently override it.
    expect(onLocationPicked).toHaveBeenCalledTimes(1);
    expect(onLocationPicked).toHaveBeenCalledWith("44.5,-90.5",
      { address: "", city: "", state: "", country: "Canada", zipCode: "" });
  });
});

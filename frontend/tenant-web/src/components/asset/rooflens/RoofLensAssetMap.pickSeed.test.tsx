/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 30-Sep-2026
* @author      : Steeve
* @since       :
* Requirement# :
* Purpose      : RoofLensAssetMap picks (search bar / Move Pin) inside an asset form seeded with a saved address.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* AB#32415       | 30-Sep-2026 |     Steeve      | Tests for the pick / address-revert fix
*/
import { useState } from "react";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import RoofLensAssetMap from "./RoofLensAssetMap";
import { useRoofLensStore } from "./store/rooflensStore";

// A pick (search-bar pick, Move Pin click/drag) inside an asset form SEEDED with a saved address.
// The parent echoes the picked `location` back in (handleLocationPicked → assetData.location) but its
// `address` prop only follows when the pick carried an address; SeededParent below models the case where it
// does not (a pick without address parts, or any parent that has not updated it). Create Bid's add-new-property
// has no seed, so its `address` is ''. Live report: after analysis finished the search bar flipped back to the
// OLD address while the form fields kept the new one, and Auto-Detect ran a second time, wiping the fresh facets.

let mockIsLoaded = true;
jest.mock("../../../context/GoogleMapsContext", () => ({
  useGoogleMaps: () => ({ isLoaded: mockIsLoaded, mapApiKey: "k" }),
}));

const mockFetchFacets = jest.fn();
jest.mock("../../../services/RoofLensService", () => ({
  fetchFacets: (...a: any[]) => mockFetchFacets(...a),
  fetchBlueprint: jest.fn(),
  geocodeAddress: jest.fn(),
}));

let mockAddressValidationFlag = false;
jest.mock("@openfeature/react-sdk", () => ({
  useFlag: (key: string) => ({ value: key === "rooflens-address-validation" ? mockAddressValidationFlag : false }),
}));

const mockValidatePropertyAddress = jest.fn();
jest.mock("../../../services/AssetService", () => ({
  validatePropertyAddress: (...a: any[]) => mockValidatePropertyAddress(...a),
}));

jest.mock("./RoofLensMap", () => ({
  __esModule: true,
  default: ({ onManualPinMove, center }: any) => (
    <>
      <span data-testid="map-center">{center ? `${center.lat},${center.lng}` : ""}</span>
      <button data-testid="manual-pin-move" onClick={() => onManualPinMove({ lat: 43.5, lng: -89.5 })}>move-pin</button>
      <button data-testid="manual-pin-move-2" onClick={() => onManualPinMove({ lat: 44.5, lng: -90.5 })}>move-pin-2</button>
    </>
  ),
}));
jest.mock("./RoofLens3DView", () => ({ __esModule: true, default: () => <div data-testid="rl-3d">3d</div> }));
// Faithful to the real bar: it copies a NON-EMPTY `initialText` into its own state (RoofLensSearchBar.tsx:91-93).
jest.mock("./RoofLensSearchBar", () => {
  const React = require("react");
  return {
    __esModule: true,
    default: function SearchBarMock({ onPick, initialText }: any) {
      const [text, setText] = React.useState(initialText || "");
      React.useEffect(() => { if (initialText) setText(initialText); }, [initialText]);
      return (
        <>
          <span data-testid="search-text">{text}</span>
          <button data-testid="search-pick" onClick={() => onPick(43.09, -89.28, "DEMO 300 Pick Rd", { country: "United States" })}>
            search
          </button>
        </>
      );
    },
  };
});

const SEED_LOCATION = "43.09,-89.28"; // the pin the search-pick mock also reports
const SEEDED_ADDRESS = "DEMO 100 Seed St, Demoville, WI 00000";
const BILLING_ADDRESS = "DEMO 200 Billing Ave, Demoville, WI 00000";
const pickedLabel = (lat: number, lng: number) => `addr@${lat.toFixed(1)},${lng.toFixed(1)}`;
const okValidation = { ok: true, data: { accepted: true, addressStatus: "VALID", roofStatus: "ROOF_FOUND", message: null } };

const facetsResponse = {
  status: "ok",
  facets: [{ boundary: [[43.09, -89.28], [43.091, -89.281], [43.092, -89.28]], pitch_ratio: "6/12", area_sqft: 480 }],
  metrics: { predominant_pitch: "6/12", per_facet: [{ ridge_ft: 10, hip_ft: 5, valley_ft: 4, rake_ft: 3, eave_ft: 20, parapet_ft: 0 }] },
};

let keySeq = 0;
const freshKey = () => `pickseed-${keySeq++}`;
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 40)); });
const hasFacets = () => useRoofLensStore.getState().facets.length > 0;

// Reverse-geocodes to a label derived from the coordinates so each pin has a distinct address.
function installCoordinateGeocoder() {
  class Geocoder {
    geocode(req: any, cb: (r: any[] | null, s: string) => void) {
      if (req.location) {
        cb([{
          formatted_address: pickedLabel(req.location.lat, req.location.lng),
          address_components: [{ long_name: "United States", short_name: "US", types: ["country"] }],
        }], "OK");
      } else {
        cb([{ geometry: { location: { lat: () => 43.09, lng: () => -89.28 } } }], "OK");
      }
    }
  }
  (global as any).google = { maps: { Geocoder } };
}

// `location` is fed back from the pick like CreateAsset_v3; `address` stays the seeded value.
function SeededParent({ assetKey }: { assetKey: string }) {
  const [location, setLocation] = useState<string | undefined>(SEED_LOCATION);
  const [address, setAddress] = useState(SEEDED_ADDRESS);
  return (
    <>
      <RoofLensAssetMap assetKey={assetKey} location={location} address={address} onLocationPicked={(loc) => setLocation(loc)} />
      {/* CreateAsset_v3.handleUseBillingAddressChange(true): new address prop AND location cleared. */}
      <button data-testid="apply-billing" onClick={() => { setLocation(undefined); setAddress(BILLING_ADDRESS); }}>apply</button>
      {/* …(false): address cleared, location left alone. */}
      <button data-testid="uncheck-billing" onClick={() => setAddress("")}>uncheck</button>
    </>
  );
}

beforeEach(() => {
  mockIsLoaded = true;
  mockFetchFacets.mockReset();
  mockAddressValidationFlag = false;
  mockValidatePropertyAddress.mockReset();
  useRoofLensStore.getState().reset();
});
afterEach(() => { delete (global as any).google; });

async function renderSeededAndWaitForInitialDetect() {
  mockFetchFacets.mockResolvedValue(facetsResponse);
  installCoordinateGeocoder();
  render(<SeededParent assetKey={freshKey()} />);
  await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(hasFacets()).toBe(true));
  await settle();
  expect(screen.getByTestId("search-text")).toHaveTextContent(SEEDED_ADDRESS);
}

describe("RoofLensAssetMap — a pick in a flow seeded with a saved address", () => {
  it("keeps the picked address in the search bar and does not re-run detection when analysis finishes", async () => {
    await renderSeededAndWaitForInitialDetect();

    // Hold the pick's detection open so the effect runs while `loading.active` is true (the live order).
    let resolvePick!: (v: any) => void;
    mockFetchFacets.mockImplementationOnce(() => new Promise((r) => { resolvePick = r; }));
    fireEvent.click(screen.getByTestId("manual-pin-move"));
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("search-text")).toHaveTextContent(pickedLabel(43.5, -89.5)));

    await act(async () => { resolvePick(facetsResponse); });
    await waitFor(() => expect(hasFacets()).toBe(true));
    await settle();

    expect(screen.getByTestId("search-text")).toHaveTextContent(pickedLabel(43.5, -89.5));
    expect(screen.getByTestId("search-text")).not.toHaveTextContent(SEEDED_ADDRESS);
    // initial load + the pick — NOT a third, redundant detection (which also wiped the fresh facets).
    expect(mockFetchFacets).toHaveBeenCalledTimes(2);
    expect(hasFacets()).toBe(true);
  });

  it("holds for a SECOND consecutive pick too (the claim follows the newest pick)", async () => {
    await renderSeededAndWaitForInitialDetect();

    fireEvent.click(screen.getByTestId("manual-pin-move"));
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(hasFacets()).toBe(true));
    await settle();

    fireEvent.click(screen.getByTestId("manual-pin-move-2"));
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(hasFacets()).toBe(true));
    await settle();

    expect(screen.getByTestId("search-text")).toHaveTextContent(pickedLabel(44.5, -90.5));
    expect(mockFetchFacets).toHaveBeenCalledTimes(3);
  });

  it("still re-detects and shows the billing address when it is applied AFTER a pick (billing-checkbox fix kept)", async () => {
    await renderSeededAndWaitForInitialDetect();

    fireEvent.click(screen.getByTestId("manual-pin-move"));
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(hasFacets()).toBe(true));
    await settle();
    expect(mockFetchFacets).toHaveBeenCalledTimes(2); // pins the count BEFORE the click (a redundant 3rd call would be the bug)

    fireEvent.click(screen.getByTestId("apply-billing"));
    await waitFor(() => expect(screen.getByTestId("search-text")).toHaveTextContent(BILLING_ADDRESS));
    await settle();
    expect(mockFetchFacets).toHaveBeenCalledTimes(3);
  });

  it("unchecking billing after a pick does nothing — the pin was already handled, so facets and text stay", async () => {
    await renderSeededAndWaitForInitialDetect();

    fireEvent.click(screen.getByTestId("manual-pin-move"));
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(hasFacets()).toBe(true));
    await settle();

    fireEvent.click(screen.getByTestId("uncheck-billing"));
    await settle();

    expect(hasFacets()).toBe(true);
    expect(mockFetchFacets).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("search-text")).toHaveTextContent(pickedLabel(43.5, -89.5));
  });

  it("Move Pin uses the LATEST onLocationPicked, not the one from the first render", async () => {
    mockFetchFacets.mockResolvedValue(facetsResponse);
    installCoordinateGeocoder();
    const key = freshKey();
    const first = jest.fn();
    const latest = jest.fn();
    const { rerender } = render(<RoofLensAssetMap assetKey={key} location={SEED_LOCATION} address={SEEDED_ADDRESS} onLocationPicked={first} />);
    await waitFor(() => expect(hasFacets()).toBe(true));
    rerender(<RoofLensAssetMap assetKey={key} location={SEED_LOCATION} address={SEEDED_ADDRESS} onLocationPicked={latest} />);

    fireEvent.click(screen.getByTestId("manual-pin-move"));
    await waitFor(() => expect(latest).toHaveBeenCalledTimes(1));
    expect(first).not.toHaveBeenCalled();
  });
});

describe("RoofLensAssetMap — saved-address validation racing a pick (flag ON)", () => {
  // With validation ON the on-load effect awaits a real network validation of the saved address. A pick at
  // the SAME coordinates as the seeded pin changes no dependency, so the effect neither re-runs nor cancels;
  // the stale validation later resolves as "superseded" (the pick bumped the run id) and must not write the
  // OLD saved address back into the search box. The pick's own detection must still be in flight at that
  // moment (otherwise facets.length changing cancels the stale run), which is the realistic case: detection
  // takes many seconds, validation about one.
  it("a same-location pick while the saved address is still validating is not overwritten by the stale saved address", async () => {
    mockAddressValidationFlag = true;
    installCoordinateGeocoder();
    let resolveSaved!: (v: any) => void;
    let resolvePickDetect!: (v: any) => void;
    mockValidatePropertyAddress
      .mockImplementationOnce(() => new Promise((r) => { resolveSaved = r; })) // mount effect's validation, held open
      .mockResolvedValueOnce(okValidation);                                     // the pick's validation
    mockFetchFacets.mockImplementation(() => new Promise((r) => { resolvePickDetect = r; })); // pick's detection, held open
    // Static props: the parent never echoes the pick, so no dependency of the on-load effect changes.
    render(<RoofLensAssetMap assetKey={freshKey()} location={SEED_LOCATION} address={SEEDED_ADDRESS}
                             accessToken="token" onLocationPicked={jest.fn()} />);
    await waitFor(() => expect(mockValidatePropertyAddress).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId("search-pick")); // reports exactly the seeded coordinates
    await waitFor(() => expect(screen.getByTestId("search-text")).toHaveTextContent("DEMO 300 Pick Rd"));
    await waitFor(() => expect(mockFetchFacets).toHaveBeenCalledTimes(1));

    await act(async () => { resolveSaved(okValidation); }); // stale validation resolves mid-detection
    await settle();
    expect(screen.getByTestId("search-text")).toHaveTextContent("DEMO 300 Pick Rd");
    expect(screen.getByTestId("search-text")).not.toHaveTextContent(SEEDED_ADDRESS);

    await act(async () => { resolvePickDetect(facetsResponse); });
    await waitFor(() => expect(hasFacets()).toBe(true));
    await settle();
    expect(screen.getByTestId("search-text")).toHaveTextContent("DEMO 300 Pick Rd");
    expect(mockFetchFacets).toHaveBeenCalledTimes(1); // only the pick's detection
  });

  it("a Move Pin pick at new coordinates while the saved address is still validating keeps the map centred on the pick", async () => {
    mockAddressValidationFlag = true;
    installCoordinateGeocoder();
    let resolveSaved!: (v: any) => void;
    mockValidatePropertyAddress
      .mockImplementationOnce(() => new Promise((r) => { resolveSaved = r; }))
      .mockResolvedValue(okValidation);
    mockFetchFacets.mockImplementation(() => new Promise(() => {})); // detection stays in flight
    render(<RoofLensAssetMap assetKey={freshKey()} location={SEED_LOCATION} address={SEEDED_ADDRESS}
                             accessToken="token" onLocationPicked={jest.fn()} />);
    await waitFor(() => expect(mockValidatePropertyAddress).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId("manual-pin-move")); // 43.5,-89.5
    await waitFor(() => expect(screen.getByTestId("map-center")).toHaveTextContent("43.5,-89.5"));

    await act(async () => { resolveSaved(okValidation); }); // stale validation of the seeded address resolves
    await settle();
    expect(screen.getByTestId("map-center")).toHaveTextContent("43.5,-89.5");
  });

  it("a genuinely REJECTED saved address on load still shows the banner and skips detection (deliberate behaviour kept)", async () => {
    mockAddressValidationFlag = true;
    mockValidatePropertyAddress.mockResolvedValue({
      ok: true,
      data: { accepted: false, addressStatus: "VALID", roofStatus: "NO_ROOF", message: "No rooftop was found at this address." },
    });
    render(<RoofLensAssetMap assetKey={freshKey()} location={SEED_LOCATION} address={SEEDED_ADDRESS} accessToken="token" />);
    expect(await screen.findByText("No rooftop was found at this address.")).toBeInTheDocument();
    expect(mockFetchFacets).not.toHaveBeenCalled();
    expect(screen.getByTestId("search-text")).toHaveTextContent(SEEDED_ADDRESS);
  });
});

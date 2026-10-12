import { render, screen, fireEvent, act } from "@testing-library/react";
import "@testing-library/jest-dom";

// Capture the props the component hands to <StandaloneSearchBox> so a test can
// drive the Google Places lifecycle (onLoad → set ref, onPlacesChanged → fire).
const box: { load?: (ref: any) => void; changed?: () => void } = {};
jest.mock("@react-google-maps/api", () => ({
  StandaloneSearchBox: ({ children, onLoad, onPlacesChanged }: any) => {
    box.load = onLoad;
    box.changed = onPlacesChanged;
    return <div data-testid="search-box">{children}</div>;
  },
}));

import RoofLensSearchBar from "./RoofLensSearchBar";

const makePlace = (over: any = {}) => ({
  geometry: { location: { lat: () => 43.09, lng: () => -89.28 } },
  formatted_address: "557 Jupiter Dr, Madison, WI 53718, USA",
  address_components: [
    { long_name: "557", types: ["street_number"] },
    { long_name: "Jupiter Dr", types: ["route"] },
    { long_name: "Madison", types: ["locality"] },
    { long_name: "Wisconsin", types: ["administrative_area_level_1"] },
    { long_name: "United States", types: ["country"] },
    { long_name: "53718", types: ["postal_code"] },
  ],
  ...over,
});

const loadWith = (places: any[]) =>
  act(() => {
    box.load?.({ getPlaces: () => places });
  });

beforeEach(() => {
  box.load = undefined;
  box.changed = undefined;
});

describe("RoofLensSearchBar", () => {
  it("renders the search field seeded with initialText", () => {
    render(<RoofLensSearchBar initialText="820 Congress Ave" onPick={jest.fn()} />);
    expect(screen.getByPlaceholderText(/Search the property address/i)).toHaveValue("820 Congress Ave");
  });

  it("updates its text as the user types", () => {
    render(<RoofLensSearchBar onPick={jest.fn()} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.change(input, { target: { value: "12 Main St" } });
    expect(input).toHaveValue("12 Main St");
  });

  it("emits lat/lng, formatted address and a parsed address on a place selection", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    loadWith([makePlace()]);
    act(() => box.changed?.());

    expect(onPick).toHaveBeenCalledTimes(1);
    const [lat, lng, address, parsed] = onPick.mock.calls[0];
    expect(lat).toBe(43.09);
    expect(lng).toBe(-89.28);
    expect(address).toMatch(/557 Jupiter Dr/);
    expect(parsed).toEqual({
      address: "557 Jupiter Dr",
      city: "Madison",
      state: "Wisconsin",
      country: "United States",
      zipCode: "53718",
    });
    // The field reflects the chosen address.
    expect(screen.getByDisplayValue(/557 Jupiter Dr, Madison/)).toBeInTheDocument();
  });

  it("ignores a places-changed event with no geometry, and shows the no-match hint", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    loadWith([{ formatted_address: "no geometry" }]);
    act(() => box.changed?.());
    expect(onPick).not.toHaveBeenCalled();
    expect(screen.getByText(/No matching address found/i)).toBeInTheDocument();
  });

  it("does nothing when there are no places at all", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    loadWith([]);
    act(() => box.changed?.());
    expect(onPick).not.toHaveBeenCalled();
  });

  it("falls back to the typed text and empty components when the place is sparse", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.change(input, { target: { value: "typed fallback" } });
    // Geometry only — no formatted_address (=> `|| text`) and no address_components (=> `|| []`).
    loadWith([{ geometry: { location: { lat: () => 1, lng: () => 2 } } }]);
    act(() => box.changed?.());
    const [lat, lng, address, parsed] = onPick.mock.calls[0];
    expect(lat).toBe(1);
    expect(lng).toBe(2);
    expect(address).toBe("typed fallback");
    expect(parsed).toEqual({ address: "", city: "", state: "", country: "", zipCode: "" });
  });

  it("parses the alternate address-component types (premise, sublocality, postal_town)", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    loadWith([
      makePlace({
        formatted_address: "Unit 4, Some Estate, Reading RG1",
        address_components: [
          { long_name: "Unit 4", types: ["subpremise"] },
          { long_name: "Rose House", types: ["premise"] },
          { long_name: "Old Town", types: ["neighborhood"] },
          { long_name: "Central", types: ["sublocality_level_1"] },
          { long_name: "Reading", types: ["postal_town"] },
          { long_name: "Berkshire", types: ["administrative_area_level_2"] },
        ],
      }),
    ]);
    act(() => box.changed?.());
    const parsed = onPick.mock.calls[0][3];
    expect(parsed.address).toBe("Unit 4 Rose House Old Town Central");
    // locality absent → falls back to postal_town for city.
    expect(parsed.city).toBe("Reading");
  });
});

// AB#28854 — live testing found that pressing Enter on text Google can't resolve to a place did
// NOTHING at all: no error, no feedback, the map stayed on its empty state with zero indication
// why (StandaloneSearchBox only fires onPlacesChanged for text that resolves to something usable
// — there is no "nothing matched" event to react to on its own). These tests cover the fix: a
// visible hint when Enter is pressed and no pick lands shortly after.
describe("RoofLensSearchBar — Enter with no resolvable place (AB#28854)", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("shows a hint when Enter is pressed and onPlacesChanged never fires at all", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.change(input, { target: { value: "asdkjhaskjdh 999999 Nowhereville Fakestate" } });

    fireEvent.keyDown(input, { key: "Enter" });
    // No box.changed?.() call at all — simulates StandaloneSearchBox never firing.
    act(() => { jest.advanceTimersByTime(300); });

    expect(screen.getByText(/No matching address found/i)).toBeInTheDocument();
    expect(onPick).not.toHaveBeenCalled();
  });

  it("does NOT show the hint when a real pick lands before the timer elapses", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);

    fireEvent.keyDown(input, { key: "Enter" });
    loadWith([makePlace()]);
    act(() => box.changed?.()); // a genuine pick, landing before the hint timer fires
    act(() => { jest.advanceTimersByTime(300); });

    expect(screen.queryByText(/No matching address found/i)).not.toBeInTheDocument();
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it("clears a shown hint once the user edits the text again", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);

    fireEvent.keyDown(input, { key: "Enter" });
    act(() => { jest.advanceTimersByTime(300); });
    expect(screen.getByText(/No matching address found/i)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "trying again" } });
    expect(screen.queryByText(/No matching address found/i)).not.toBeInTheDocument();
  });

  it("a non-Enter key does not start the hint timer", () => {
    render(<RoofLensSearchBar onPick={jest.fn()} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.keyDown(input, { key: "a" });
    act(() => { jest.advanceTimersByTime(300); });
    expect(screen.queryByText(/No matching address found/i)).not.toBeInTheDocument();
  });
});

// Real live bug: editing an already-resolved address in place (e.g. "557" -> "533" Jupiter
// Drive) and pressing Enter WITHOUT selecting the new dropdown suggestion left the address
// unresolved — StandaloneSearchBox never fires onPlacesChanged for a bare Enter press, even
// when the typed text is a real, geocodable address. Fixed with a Geocoder fallback.
describe("RoofLensSearchBar — Enter falls back to a plain geocode when Places never resolves", () => {
  const mockGeocode = jest.fn();

  beforeEach(() => {
    jest.useFakeTimers();
    mockGeocode.mockReset();
    (global as any).google = {
      maps: {
        Geocoder: jest.fn().mockImplementation(() => ({ geocode: mockGeocode })),
      },
    };
  });

  afterEach(() => {
    jest.useRealTimers();
    delete (global as any).google;
  });

  it("resolves via a plain geocode and reports it exactly like a real pick, when Enter is pressed on a real address with no dropdown selection", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.change(input, { target: { value: "533 Jupiter Dr, Madison, WI" } });
    fireEvent.keyDown(input, { key: "Enter" });
    // No box.changed?.() call — simulates StandaloneSearchBox never firing for a bare Enter.

    act(() => { jest.advanceTimersByTime(300); });
    expect(mockGeocode).toHaveBeenCalledWith(
      { address: "533 Jupiter Dr, Madison, WI" },
      expect.any(Function)
    );

    // Fire the geocode callback with a real, resolvable result.
    const geocodeCallback = mockGeocode.mock.calls[0][1];
    act(() => {
      geocodeCallback(
        [{
          geometry: { location: { lat: () => 43.1, lng: () => -89.3 } },
          formatted_address: "533 Jupiter Dr, Madison, WI 53718, USA",
          address_components: [
            { long_name: "533", types: ["street_number"] },
            { long_name: "Jupiter Dr", types: ["route"] },
            { long_name: "Madison", types: ["locality"] },
            { long_name: "Wisconsin", types: ["administrative_area_level_1"] },
            { long_name: "United States", types: ["country"] },
            { long_name: "53718", types: ["postal_code"] },
          ],
        }],
        "OK"
      );
    });

    expect(onPick).toHaveBeenCalledTimes(1);
    const [lat, lng, address, parsed] = onPick.mock.calls[0];
    expect(lat).toBe(43.1);
    expect(lng).toBe(-89.3);
    expect(address).toMatch(/533 Jupiter Dr/);
    expect(parsed.city).toBe("Madison");
    expect(screen.queryByText(/No matching address found/i)).not.toBeInTheDocument();
  });

  it("still shows the no-match hint when the geocode fallback ALSO fails to resolve anything", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.change(input, { target: { value: "complete gibberish xyz123" } });
    fireEvent.keyDown(input, { key: "Enter" });

    act(() => { jest.advanceTimersByTime(300); });
    const geocodeCallback = mockGeocode.mock.calls[0][1];
    act(() => { geocodeCallback([], "ZERO_RESULTS"); });

    expect(onPick).not.toHaveBeenCalled();
    expect(screen.getByText(/No matching address found/i)).toBeInTheDocument();
  });

  it("does not even attempt a geocode call when the input is blank", () => {
    render(<RoofLensSearchBar onPick={jest.fn()} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.keyDown(input, { key: "Enter" });
    act(() => { jest.advanceTimersByTime(300); });

    expect(mockGeocode).not.toHaveBeenCalled();
    expect(screen.getByText(/No matching address found/i)).toBeInTheDocument();
  });

  it("does not fire the geocode fallback when a real Places pick already landed before the timer elapsed", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);
    fireEvent.keyDown(input, { key: "Enter" });
    loadWith([makePlace()]);
    act(() => box.changed?.()); // a genuine pick, landing before the fallback timer fires
    act(() => { jest.advanceTimersByTime(300); });

    expect(mockGeocode).not.toHaveBeenCalled();
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  // Adversarial-review finding: a stale in-flight geocode for OLD text must never overwrite
  // what the user has typed since — even if it resolves successfully.
  // Real live bug, root-caused via console logging: Google's own StandaloneSearchBox dropdown
  // result can carry a genuine formatted_address while OMITTING address_components entirely
  // (confirmed live for a real second search — the first pick for the same street had full
  // components, the second didn't). Previously this silently produced an empty ParsedAddress, so
  // the questionnaire's Street Address/City/etc. fields kept showing the PREVIOUS pick's stale
  // values even though the map/pin/search box had all moved to the new address.
  it("re-resolves via a geocode fallback when a dropdown pick's own place is missing address_components", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    loadWith([makePlace({ address_components: undefined })]); // matches the real logged shape exactly
    act(() => box.changed?.());

    expect(mockGeocode).toHaveBeenCalledWith(
      { address: "557 Jupiter Dr, Madison, WI 53718, USA" },
      expect.any(Function)
    );
    const geocodeCallback = mockGeocode.mock.calls[0][1];
    act(() => {
      geocodeCallback(
        [{
          geometry: { location: { lat: () => 43.09, lng: () => -89.28 } },
          formatted_address: "557 Jupiter Dr, Madison, WI 53718, USA",
          address_components: [
            { long_name: "557", types: ["street_number"] },
            { long_name: "Jupiter Dr", types: ["route"] },
            { long_name: "Madison", types: ["locality"] },
            { long_name: "Wisconsin", types: ["administrative_area_level_1"] },
            { long_name: "United States", types: ["country"] },
            { long_name: "53718", types: ["postal_code"] },
          ],
        }],
        "OK"
      );
    });

    expect(onPick).toHaveBeenCalledTimes(1);
    const [, , , parsed] = onPick.mock.calls[0];
    expect(parsed.address).toBe("557 Jupiter Dr"); // real components recovered, not left empty
    expect(parsed.city).toBe("Madison");
  });

  // Adversarial-review finding: a stale in-flight geocode fallback from an EARLIER dropdown pick
  // (missing address_components) must never overwrite a NEWER pick that already resolved.
  it("a stale in-flight dropdown-pick geocode fallback does NOT overwrite a newer pick that already resolved", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);

    // Pick A: missing address_components, kicks off a geocode fallback that stays unresolved.
    loadWith([makePlace({ address_components: undefined, formatted_address: "Address A" })]);
    act(() => box.changed?.());
    expect(mockGeocode).toHaveBeenCalledTimes(1);
    const staleCallback = mockGeocode.mock.calls[0][1];

    // Before A's geocode resolves, the user makes a NEWER pick, B, with full components.
    loadWith([makePlace({ formatted_address: "557 Jupiter Dr, Madison, WI 53718" })]);
    act(() => box.changed?.());
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][2]).toBe("557 Jupiter Dr, Madison, WI 53718");

    // A's stale geocode finally resolves — must be a no-op now.
    act(() => {
      staleCallback(
        [{
          geometry: { location: { lat: () => 0, lng: () => 0 } },
          formatted_address: "Address A",
          address_components: [{ long_name: "Stale", types: ["route"] }],
        }],
        "OK"
      );
    });

    expect(onPick).toHaveBeenCalledTimes(1); // still just the one, real call for B
  });

  it("emits empty components (no crash) when a dropdown pick's missing address_components ALSO fails to re-resolve via geocode", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    loadWith([makePlace({ address_components: undefined })]);
    act(() => box.changed?.());

    const geocodeCallback = mockGeocode.mock.calls[0][1];
    act(() => { geocodeCallback([], "ZERO_RESULTS"); });

    expect(onPick).toHaveBeenCalledTimes(1);
    const [lat, lng, , parsed] = onPick.mock.calls[0];
    expect(lat).toBe(43.09); // the original place's own lat/lng is still used — never blocked
    expect(parsed).toEqual({ address: "", city: "", state: "", country: "", zipCode: "" });
  });

  it("a stale in-flight geocode for text the user has since changed does NOT fire onPick or overwrite the newer text", () => {
    const onPick = jest.fn();
    render(<RoofLensSearchBar onPick={onPick} />);
    const input = screen.getByPlaceholderText(/Search the property address/i);

    fireEvent.change(input, { target: { value: "557 Jupiter Dr" } });
    fireEvent.keyDown(input, { key: "Enter" });
    act(() => { jest.advanceTimersByTime(300); }); // fires the geocode call for "557 Jupiter Dr"
    expect(mockGeocode).toHaveBeenCalledTimes(1);
    const staleCallback = mockGeocode.mock.calls[0][1];

    // The user keeps typing something else BEFORE that geocode call resolves.
    fireEvent.change(input, { target: { value: "totally different address" } });

    // The stale callback finally resolves — successfully — for the OLD "557 Jupiter Dr" text.
    act(() => {
      staleCallback(
        [{
          geometry: { location: { lat: () => 43.09, lng: () => -89.28 } },
          formatted_address: "557 Jupiter Dr, Madison, WI 53718, USA",
          address_components: [],
        }],
        "OK"
      );
    });

    expect(onPick).not.toHaveBeenCalled();
    expect(input).toHaveValue("totally different address"); // never clobbered by the stale result
  });
});

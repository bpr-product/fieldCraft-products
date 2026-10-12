import { render, screen, act, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";

// ── Mock the Google Maps map object + globals fitToFacets relies on ──────────────
const mockMap = {
  fitBounds: jest.fn(),
  setCenter: jest.fn(),
  setZoom: jest.fn(),
  panTo: jest.fn(),
  getCenter: jest.fn(() => ({ lat: () => 43, lng: () => -89 })),
};

// Captures the latest onLoad / onTilesLoaded the component passed to <GoogleMap>, so a test
// can drive the real Google Maps lifecycle: onLoad (map object ready) then onTilesLoaded
// (map actually painted). The component gates facet overlays on BOTH.
const handlers: { load?: (m: any) => void; tiles?: () => void } = {};

jest.mock("@react-google-maps/api", () => ({
  // The fixed lat/lng a mocked click/drag "reports" — the exact value doesn't matter to
  // these tests, only that the component's handler receives SOME lat()/lng() point and
  // does the right thing with it. Inlined (not a shared outer const) since a jest.mock()
  // factory can't reference out-of-scope variables unless they're "mock"-prefixed.
  GoogleMap: ({ children, onLoad, onTilesLoaded, onClick }: any) => {
    handlers.load = onLoad;
    handlers.tiles = onTilesLoaded;
    return (
      <div
        data-testid="gmap"
        onClick={() => onClick?.({ latLng: { lat: () => 44.5, lng: () => -90.5 } })}
      >
        {children}
      </div>
    );
  },
  // Calls onLoad with a fake google.maps.Polygon (readPath()/syncPath() need
  // getPath().getArray()) and wires onMouseUp to a real DOM event, so tests can
  // fire a genuine mouseUp and assert on what the component actually does with
  // it — not just that a prop was passed.
  Polygon: ({ onLoad, onMouseUp }: any) => {
    onLoad?.({
      getPath: () => ({
        getArray: () => [
          { lat: () => 43.0, lng: () => -89.0 },
          { lat: () => 43.001, lng: () => -89.001 },
          { lat: () => 43.002, lng: () => -89.0 },
        ],
      }),
    });
    return <div data-testid="polygon" onMouseUp={onMouseUp} />;
  },
  // Forwards draggable/onDragEnd/title so tests can assert on the locator pin's
  // interactivity, and fire a real drag-end event.
  MarkerF: ({ draggable, onDragEnd, title }: any) => (
    <div
      data-testid="marker"
      data-draggable={draggable ? "true" : "false"}
      title={title}
      onDragEnd={() => onDragEnd?.({ latLng: { lat: () => 45.5, lng: () => -91.5 } })}
    />
  ),
  Polyline: () => <div data-testid="polyline" />,
}));

import RoofLensMap from "./RoofLensMap";
import { useRoofLensStore } from "./store/rooflensStore";
import type { RoofFacet } from "./types";

const facet = (id: string): RoofFacet => ({
  id,
  buildingId: "B0",
  path: [
    { lat: 43.0, lng: -89.0 },
    { lat: 43.001, lng: -89.001 },
    { lat: 43.002, lng: -89.0 },
  ],
  pitchRise: 6,
  isAuto: true,
  serviceAreaSqft: 100,
  edited: false,
});

const seed = (facets: RoofFacet[], tool: "view" | "draw" | "edit" | "pin" = "view") =>
  useRoofLensStore.setState({ facets, activeBuildingId: "B0", tool, selectedId: null });

const fireLoad = () => act(() => { handlers.load?.(mockMap); });
const fireTiles = () => act(() => { handlers.tiles?.(); });

beforeAll(() => {
  (global as any).google = {
    maps: {
      LatLngBounds: class { extend() {} },
      SymbolPath: { CIRCLE: 0 },
    },
  };
});

beforeEach(() => {
  mockMap.fitBounds.mockClear();
  handlers.load = undefined;
  handlers.tiles = undefined;
  seed([]);
});

const renderMap = (onManualPinMove?: (c: any) => void) =>
  render(<RoofLensMap center={{ lat: 43, lng: -89 }} hasLocation onManualPinMove={onManualPinMove} />);

describe("RoofLensMap — overlay repaint gating", () => {
  it("renders facet polygons only AFTER onTilesLoaded, not merely on onLoad", () => {
    seed([facet("f1")]);
    renderMap();

    // Map object not yet loaded → no polygons.
    expect(screen.queryAllByTestId("polygon")).toHaveLength(0);

    fireLoad(); // map object exists, but tiles haven't painted yet
    expect(screen.queryAllByTestId("polygon")).toHaveLength(0);

    fireTiles(); // map painted → overlays mount
    expect(screen.getAllByTestId("polygon")).toHaveLength(1);
  });

  it("fits to facets once per non-empty set, and not again when a facet is added in view mode", () => {
    seed([facet("f1")]);
    renderMap();
    fireLoad();
    fireTiles();
    mockMap.fitBounds.mockClear(); // ignore any load/first-fit calls

    // Adding a facet in view mode must NOT re-frame (already fitted this set).
    act(() => useRoofLensStore.setState((s) => ({ facets: [...s.facets, facet("f2")] })));
    expect(mockMap.fitBounds).not.toHaveBeenCalled();
  });

  it("re-fits after facets clear and a new set arrives", () => {
    seed([facet("f1")]);
    renderMap();
    fireLoad();
    fireTiles();
    mockMap.fitBounds.mockClear();

    act(() => useRoofLensStore.setState({ facets: [] }));      // clears → resets the one-shot guard
    act(() => useRoofLensStore.setState({ facets: [facet("f3")] })); // new set → frames again
    expect(mockMap.fitBounds).toHaveBeenCalledTimes(1);
  });

  it("does NOT re-fit while the user is drawing/editing (guard against yanking the viewport)", () => {
    renderMap();
    fireLoad();
    fireTiles();
    mockMap.fitBounds.mockClear();

    // New facet set arrives while in draw mode → skipped.
    act(() => useRoofLensStore.setState({ facets: [facet("f1")], tool: "draw" }));
    expect(mockMap.fitBounds).not.toHaveBeenCalled();

    // Same unfitted set, switch to edit → still skipped.
    act(() => useRoofLensStore.setState({ tool: "edit" }));
    expect(mockMap.fitBounds).not.toHaveBeenCalled();

    // Back to view with the set still unfitted → now it frames.
    act(() => useRoofLensStore.setState({ tool: "view" }));
    expect(mockMap.fitBounds).toHaveBeenCalledTimes(1);
  });
});

describe("RoofLensMap — onMouseUp only syncs the path in Adjust mode", () => {
  // A human-entered area override (serviceAreaSqft) must never be silently
  // discarded by anything short of the user actually dragging a vertex. Before
  // this fix, onMouseUp fired on any click release on the active building's
  // facet — not just in Adjust mode — which called updateFacetPath and wiped
  // serviceAreaSqft/set edited:true even though nothing was actually dragged.

  it("does NOT clear a facet's area on mouseUp outside Adjust mode (view)", () => {
    seed([{ ...facet("f1"), serviceAreaSqft: 500, edited: false }], "view");
    renderMap();
    fireLoad();
    fireTiles();

    fireEvent.mouseUp(screen.getByTestId("polygon"));

    const f = useRoofLensStore.getState().facets.find((x) => x.id === "f1")!;
    expect(f.serviceAreaSqft).toBe(500);
    expect(f.edited).toBe(false);
  });

  it("does NOT clear a facet's area on mouseUp while drawing (draw)", () => {
    seed([{ ...facet("f1"), serviceAreaSqft: 500, edited: false }], "draw");
    renderMap();
    fireLoad();
    fireTiles();

    fireEvent.mouseUp(screen.getByTestId("polygon"));

    const f = useRoofLensStore.getState().facets.find((x) => x.id === "f1")!;
    expect(f.serviceAreaSqft).toBe(500);
  });

  it("DOES sync the path (and clear the frozen area) on mouseUp in Adjust mode — no regression to real editing", () => {
    seed([{ ...facet("f1"), serviceAreaSqft: 500, edited: false }], "edit");
    renderMap();
    fireLoad();
    fireTiles();

    fireEvent.mouseUp(screen.getByTestId("polygon"));

    const f = useRoofLensStore.getState().facets.find((x) => x.id === "f1")!;
    expect(f.serviceAreaSqft).toBeNull();
    expect(f.edited).toBe(true);
  });
});

describe("RoofLensMap — manual pin placement (armed via the \"Move Pin\" tool only)", () => {
  // The locator pin only renders once mapObj exists (gated on `mapObj && hasLocation &&
  // showMarker`), so every test here needs a real onLoad first, same as the mouseUp suite.
  //
  // Gated on tool === "pin" (a dedicated toolbar toggle), NOT "view" — a real reported bug:
  // gating this on "view" meant every ordinary map click (just panning/looking around)
  // silently relocated the property pin, fighting normal map use. "view" must now be
  // completely inert for this feature; only explicitly arming "pin" mode moves the pin.

  it("the locator pin is draggable when onManualPinMove is provided and the Move Pin tool is armed", () => {
    seed([], "pin");
    renderMap(jest.fn());
    fireLoad();
    const marker = screen.getByTestId("marker");
    expect(marker).toHaveAttribute("data-draggable", "true");
  });

  it("the locator pin is NOT draggable when no onManualPinMove is provided (no regression to a plain read-only pin)", () => {
    seed([], "pin");
    renderMap(); // no callback
    fireLoad();
    const marker = screen.getByTestId("marker");
    expect(marker).toHaveAttribute("data-draggable", "false");
  });

  it("the locator pin is NOT draggable in plain view mode — the actual bug being fixed", () => {
    seed([], "view");
    renderMap(jest.fn());
    fireLoad();
    const marker = screen.getByTestId("marker");
    expect(marker).toHaveAttribute("data-draggable", "false");
  });

  it("the locator pin is NOT draggable while drawing (draw mode) — never fights facet drawing", () => {
    seed([], "draw");
    renderMap(jest.fn());
    fireLoad();
    const marker = screen.getByTestId("marker");
    expect(marker).toHaveAttribute("data-draggable", "false");
  });

  it("the locator pin is NOT draggable in Adjust mode (edit) — never fights a polygon vertex drag", () => {
    seed([facet("f1")], "edit");
    renderMap(jest.fn());
    fireLoad();
    fireTiles();
    const marker = screen.getByTestId("marker");
    expect(marker).toHaveAttribute("data-draggable", "false");
  });

  it("dragging the locator pin reports the dropped lat/lng via onManualPinMove when the Move Pin tool is armed", () => {
    seed([], "pin");
    const onManualPinMove = jest.fn();
    renderMap(onManualPinMove);
    fireLoad();

    fireEvent.dragEnd(screen.getByTestId("marker"));

    expect(onManualPinMove).toHaveBeenCalledWith({ lat: 45.5, lng: -91.5 });
  });

  it("clicking the map while the Move Pin tool is armed reports the clicked lat/lng via onManualPinMove", () => {
    seed([], "pin");
    const onManualPinMove = jest.fn();
    renderMap(onManualPinMove);
    fireLoad();

    fireEvent.click(screen.getByTestId("gmap"));

    expect(onManualPinMove).toHaveBeenCalledWith({ lat: 44.5, lng: -90.5 });
  });

  it("clicking the map in plain view mode does NOT move the pin — the actual reported bug: an ordinary click/pan must never relocate it", () => {
    seed([], "view");
    const onManualPinMove = jest.fn();
    renderMap(onManualPinMove);
    fireLoad();

    fireEvent.click(screen.getByTestId("gmap"));

    expect(onManualPinMove).not.toHaveBeenCalled();
  });

  it("clicking the map while drawing still adds a draft vertex and does NOT call onManualPinMove (no regression)", () => {
    seed([], "draw");
    const onManualPinMove = jest.fn();
    renderMap(onManualPinMove);
    fireLoad();

    fireEvent.click(screen.getByTestId("gmap"));

    expect(onManualPinMove).not.toHaveBeenCalled();
    // The click was consumed as a draft vertex instead: the locator pin marker plus one
    // new draft-vertex marker, proving the draw path — not the manual-pin path — ran.
    expect(screen.getAllByTestId("marker").length).toBeGreaterThan(1);
  });

  it("clicking the map in Adjust mode does neither — no facet drawing, no manual pin move", () => {
    seed([facet("f1")], "edit");
    const onManualPinMove = jest.fn();
    renderMap(onManualPinMove);
    fireLoad();
    fireTiles();

    fireEvent.click(screen.getByTestId("gmap"));

    expect(onManualPinMove).not.toHaveBeenCalled();
  });
});

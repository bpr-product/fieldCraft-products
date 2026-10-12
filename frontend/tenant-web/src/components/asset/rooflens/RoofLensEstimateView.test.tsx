import { render, screen, act, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";

const mockMap = { fitBounds: jest.fn(), setCenter: jest.fn(), setZoom: jest.fn() };
const handlers: { load?: (m: any) => void; tiles?: () => void } = {};

jest.mock("@react-google-maps/api", () => ({
  GoogleMap: ({ children, onLoad, onTilesLoaded }: any) => {
    handlers.load = onLoad;
    handlers.tiles = onTilesLoaded;
    return <div data-testid="gmap">{children}</div>;
  },
  Polygon: () => <div data-testid="polygon" />,
}));
jest.mock("../../../context/GoogleMapsContext", () => ({
  useGoogleMaps: () => ({ isLoaded: true, mapApiKey: "k" }),
}));

import RoofLensEstimateView from "./RoofLensEstimateView";

const ll = (n: number): [number, number][] => [
  [30.27 + n / 1000, -97.74],
  [30.271 + n / 1000, -97.741],
  [30.272 + n / 1000, -97.74],
];
const MEASUREMENT = JSON.stringify({
  product: "FIELDCRAFT RoofLens",
  address: "820 Congress Ave, Austin, TX",
  lat: 30.27, lng: -97.74,
  summary: { total_roof_area_sqft: 1515, facet_count: 2, total_edge_length_ft: 300, footprint_sqft: 1400, building_count: 1 },
  buildings: [],
  facets: [
    { facet_id: "f1", boundary_latlng: ll(0) },
    { facet_id: "f2", boundary_latlng: ll(1) },
  ],
});

const fireLoad = () => act(() => { handlers.load?.(mockMap); });
const fireTiles = () => act(() => { handlers.tiles?.(); });

beforeAll(() => {
  (global as any).google = { maps: { LatLngBounds: class { extend() {} } } };
});
beforeEach(() => {
  mockMap.fitBounds.mockClear();
  handlers.load = undefined;
  handlers.tiles = undefined;
});

describe("RoofLensEstimateView", () => {
  it("renders nothing when there is no valid measurement", () => {
    const { container } = render(<RoofLensEstimateView measurementJson={undefined} />);
    expect(container).toBeEmptyDOMElement();
    render(<RoofLensEstimateView measurementJson={'{"facets":[]}'} />);
    expect(screen.queryByTestId("gmap")).not.toBeInTheDocument();
  });

  it("renders the map, facet polygons, area callout and RoofLens attribution", () => {
    render(<RoofLensEstimateView measurementJson={MEASUREMENT} />);
    fireLoad();
    fireTiles();
    expect(screen.getAllByTestId("polygon")).toHaveLength(2);
    expect(screen.getByText(/1,515 ft²/)).toBeInTheDocument();
    expect(screen.getByText(/2 facets/)).toBeInTheDocument();
    expect(screen.getByText("FIELDCRAFT ROOFLENS")).toBeInTheDocument();
    expect(screen.getByText(/decision-support only/i)).toBeInTheDocument();
    expect(mockMap.fitBounds).toHaveBeenCalled(); // framed on the house
  });

  it("toggles the facet overlay off to reveal the bare satellite image", () => {
    render(<RoofLensEstimateView measurementJson={MEASUREMENT} />);
    fireLoad();
    fireTiles();
    expect(screen.getAllByTestId("polygon")).toHaveLength(2);
    // The "Roof facets" legend checkbox hides the overlay.
    fireEvent.click(screen.getByText("Roof facets"));
    expect(screen.queryAllByTestId("polygon")).toHaveLength(0);
  });
});

import { render, screen, fireEvent, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import RoofLensBuildingBar from "./RoofLensBuildingBar";
import { useRoofLensStore } from "./store/rooflensStore";
import type { RoofFacet } from "./types";

// A ~triangular facet with a frozen service area so areaFor() is deterministic.
const facet = (id: string, buildingId: string): RoofFacet => ({
  id,
  buildingId,
  path: [
    { lat: 43.0, lng: -89.0 },
    { lat: 43.001, lng: -89.001 },
    { lat: 43.002, lng: -89.0 },
  ],
  pitchRise: 6,
  isAuto: true,
  serviceAreaSqft: 1234,
  edited: false,
});

beforeEach(() => {
  useRoofLensStore.getState().reset();
});

describe("RoofLensBuildingBar", () => {
  it("renders a chip for each building and an Add Building chip", () => {
    useRoofLensStore.setState({
      buildings: [
        { id: "B0", name: "Building 1" },
        { id: "B1", name: "Garage" },
      ],
    });
    render(<RoofLensBuildingBar />);
    expect(screen.getByText("Building 1")).toBeInTheDocument();
    expect(screen.getByText("Garage")).toBeInTheDocument();
    expect(screen.getByText("Add Building")).toBeInTheDocument();
  });

  it("shows the rounded roof area on a building's chip when it has facets", () => {
    useRoofLensStore.setState({
      buildings: [{ id: "B0", name: "Building 1" }],
      activeBuildingId: "B0",
      facets: [facet("f1", "B0")],
    });
    render(<RoofLensBuildingBar />);
    expect(screen.getByText(/Building 1 · 1,234 sq ft/)).toBeInTheDocument();
  });

  it("switches the active building on chip click", () => {
    useRoofLensStore.setState({
      buildings: [
        { id: "B0", name: "Building 1" },
        { id: "B1", name: "Garage" },
      ],
      activeBuildingId: "B0",
    });
    render(<RoofLensBuildingBar />);
    fireEvent.click(screen.getByText("Garage"));
    expect(useRoofLensStore.getState().activeBuildingId).toBe("B1");
  });

  it("adds and activates a new building via Add Building", () => {
    render(<RoofLensBuildingBar />);
    expect(useRoofLensStore.getState().buildings).toHaveLength(1);
    fireEvent.click(screen.getByText("Add Building"));
    const s = useRoofLensStore.getState();
    expect(s.buildings).toHaveLength(2);
    expect(s.activeBuildingId).toBe(s.buildings[1].id);
  });

  it("does not render a delete affordance when only one building exists", () => {
    render(<RoofLensBuildingBar />);
    // The lone building chip has no Mui delete icon.
    expect(document.querySelector(".MuiChip-deleteIcon")).toBeNull();
  });

  it("removes a building when its chip delete icon is clicked (more than one present)", () => {
    useRoofLensStore.setState({
      buildings: [
        { id: "B0", name: "Building 1" },
        { id: "B1", name: "Garage" },
      ],
      activeBuildingId: "B0",
    });
    render(<RoofLensBuildingBar />);
    const garageChip = screen.getByText("Garage").closest(".MuiChip-root") as HTMLElement;
    const del = within(garageChip).getByTestId("CancelIcon");
    fireEvent.click(del);
    const s = useRoofLensStore.getState();
    expect(s.buildings.map((b) => b.id)).toEqual(["B0"]);
  });
});

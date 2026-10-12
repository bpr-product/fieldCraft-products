import { render, screen, fireEvent, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import RoofLensMeasurements from "./RoofLensMeasurements";
import { useRoofLensStore } from "./store/rooflensStore";
import type { RoofFacet, RoofMetrics } from "./types";

const facet = (id: string, over: Partial<RoofFacet> = {}): RoofFacet => ({
  id,
  buildingId: "B0",
  path: [
    { lat: 43.0, lng: -89.0 },
    { lat: 43.001, lng: -89.001 },
    { lat: 43.002, lng: -89.0 },
  ],
  pitchRise: 6,
  isAuto: true,
  serviceAreaSqft: 500,
  edited: false,
  ...over,
});

const edges = { ridge_ft: 10, hip_ft: 5, valley_ft: 4, rake_ft: 3, eave_ft: 20, parapet_ft: 0 };

beforeEach(() => {
  useRoofLensStore.getState().reset();
});

describe("RoofLensMeasurements — empty state", () => {
  it("prompts to Auto-Detect or draw when the active building has no facets", () => {
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    expect(screen.getByText(/Run/)).toBeInTheDocument();
    expect(screen.getByText(/Auto-Detect Roof/)).toBeInTheDocument();
  });

  it("shows the combined-buildings summary when more than one building exists", () => {
    useRoofLensStore.setState({
      buildings: [
        { id: "B0", name: "Building 1" },
        { id: "B1", name: "Garage" },
      ],
      activeBuildingId: "B0",
    });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    expect(screen.getByText(/All buildings — combined/)).toBeInTheDocument();
    expect(screen.getByText(/2 buildings/)).toBeInTheDocument();
  });
});

describe("RoofLensMeasurements — table", () => {
  it("renders a totals header and a row per facet of the active building", () => {
    useRoofLensStore.setState({ facets: [facet("f1"), facet("f2")] });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    expect(screen.getByText(/total roof area/)).toBeInTheDocument();
    expect(screen.getByText("Facet 1")).toBeInTheDocument();
    expect(screen.getByText("Facet 2")).toBeInTheDocument();
    // 1,000 sq ft total (500 + 500).
    expect(screen.getByText(/1,000/)).toBeInTheDocument();
  });

  it("shows the edge columns and roof report when Auto-Detect metrics are present", () => {
    useRoofLensStore.setState({
      facets: [facet("f1", { edgeMetrics: edges })],
      metricsByBuilding: {
        B0: { predominant_pitch: "6/12", ridge_hip_ft: 15, valley_ft: 4 } as RoofMetrics,
      },
    });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    expect(screen.getByText("Ridge")).toBeInTheDocument();
    expect(screen.getByText("Valley")).toBeInTheDocument();
    expect(screen.getByText(/Roof report/)).toBeInTheDocument();
    expect(screen.getByText("Predominant pitch")).toBeInTheDocument();
  });

  it("renders a rich roof report including the parapet column and confidence notes", () => {
    useRoofLensStore.setState({
      facets: [facet("f1", { edgeMetrics: { ...edges, parapet_ft: 12 } })],
      metricsByBuilding: {
        B0: {
          predominant_pitch: "7/12",
          ridge_hip_ft: 30,
          ridge_count: 2,
          hip_count: 1,
          valley_ft: 8,
          valley_count: 1,
          rake_ft: 6,
          rake_count: 2,
          eave_ft: 40,
          eave_count: 4,
          parapet_ft: 15,
          parapet_count: 1,
          estimated_attic_sqft: 900,
          estimated_attic_confidence_note: "rough",
          stories: 2,
          building_height_ft: 24,
          structure_complexity: "Moderate",
          structure_complexity_confidence_note: "estimated",
          metrics_confidence_note: "custom note",
        } as RoofMetrics,
      },
    });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    expect(screen.getByText("Parapets")).toBeInTheDocument(); // report parapet metric
    expect(screen.getByText("custom note")).toBeInTheDocument();
    expect(screen.getByText("Complexity")).toBeInTheDocument();
  });

  it("renders the per-building label, grand total, and combined summary with multiple buildings", () => {
    useRoofLensStore.setState({
      buildings: [
        { id: "B0", name: "House" },
        { id: "B1", name: "Garage" },
      ],
      activeBuildingId: "B0",
      facets: [facet("f1", { serviceAreaSqft: 500 }), facet("g1", { buildingId: "B1", serviceAreaSqft: 300 })],
      metricsByBuilding: {
        B0: { ridge_ft: 10, hip_ft: 5, valley_ft: 4, rake_ft: 3, eave_ft: 20, parapet_ft: 2, estimated_attic_sqft: 100 } as RoofMetrics,
        B1: { ridge_ft: 6 } as RoofMetrics,
      },
    });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    // Combined header + parapet line (some parapet present) + all-buildings grand total.
    expect(screen.getByText(/All buildings — combined/)).toBeInTheDocument();
    expect(screen.getByText(/Live measurements — House/)).toBeInTheDocument();
    expect(screen.getByText(/across 2 buildings/)).toBeInTheDocument();
  });

  it("selects a facet on row click", () => {
    useRoofLensStore.setState({ facets: [facet("f1")] });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    fireEvent.click(screen.getByText("Facet 1"));
    expect(useRoofLensStore.getState().selectedId).toBe("f1");
  });

  it("commits an edited roof area to the store on blur", () => {
    useRoofLensStore.setState({ facets: [facet("f1")] });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    const row = screen.getByText("Facet 1").closest("tr") as HTMLElement;
    const areaInput = within(row).getAllByRole("spinbutton")[0];
    fireEvent.change(areaInput, { target: { value: "777" } });
    fireEvent.blur(areaInput);
    const f = useRoofLensStore.getState().facets[0];
    expect(f.serviceAreaSqft).toBe(777);
    expect(f.edited).toBe(false);
  });

  it("clamps pitch edits to the 0–24 range", () => {
    useRoofLensStore.setState({ facets: [facet("f1")] });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    const row = screen.getByText("Facet 1").closest("tr") as HTMLElement;
    const pitchInput = within(row).getAllByRole("spinbutton")[1];
    fireEvent.change(pitchInput, { target: { value: "99" } });
    expect(useRoofLensStore.getState().facets[0].pitchRise).toBe(24);
  });

  it("deletes a facet from its row delete button", () => {
    useRoofLensStore.setState({ facets: [facet("f1")] });
    const { container } = render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    fireEvent.click(container.querySelector(".MuiIconButton-colorError") as HTMLElement);
    expect(useRoofLensStore.getState().facets).toHaveLength(0);
  });

  it("wires the JSON and PDF export buttons", () => {
    const onExportJson = jest.fn();
    const onExportPdf = jest.fn();
    useRoofLensStore.setState({ facets: [facet("f1")] });
    render(<RoofLensMeasurements onExportJson={onExportJson} onExportPdf={onExportPdf} />);
    fireEvent.click(screen.getByRole("button", { name: /Export JSON/i }));
    fireEvent.click(screen.getByRole("button", { name: /Export PDF/i }));
    expect(onExportJson).toHaveBeenCalledTimes(1);
    expect(onExportPdf).toHaveBeenCalledTimes(1);
  });

  it("commits an area edit on Enter (blur via keydown), ignoring other keys", () => {
    useRoofLensStore.setState({ facets: [facet("f1")] });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    const row = screen.getByText("Facet 1").closest("tr") as HTMLElement;
    const areaCell = within(row).getAllByRole("spinbutton")[0];
    areaCell.focus(); // so .blur() dispatches a real blur event
    fireEvent.click(areaCell); // clicking inside the NumCell stops row-selection propagation
    fireEvent.change(areaCell, { target: { value: "640" } });
    // A non-Enter key does not commit.
    fireEvent.keyDown(areaCell, { key: "a" });
    // Enter blurs → commits.
    fireEvent.keyDown(areaCell, { key: "Enter" });
    expect(useRoofLensStore.getState().facets[0].serviceAreaSqft).toBe(640);
  });

  it("stops row selection when the editable area / pitch cells are clicked", () => {
    useRoofLensStore.setState({ facets: [facet("f1")], selectedId: null });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    const row = screen.getByText("Facet 1").closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    // cells: [Facet label, roof-area, pitch, …, delete]
    fireEvent.click(cells[1]); // area cell → stopPropagation
    fireEvent.click(cells[2]); // pitch cell → stopPropagation
    expect(useRoofLensStore.getState().selectedId).toBeNull();
  });

  it("commits negative / NaN area input as 0", () => {
    useRoofLensStore.setState({ facets: [facet("f1")] });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    const row = screen.getByText("Facet 1").closest("tr") as HTMLElement;
    const areaCell = within(row).getAllByRole("spinbutton")[0];
    fireEvent.change(areaCell, { target: { value: "-5" } });
    fireEvent.blur(areaCell);
    expect(useRoofLensStore.getState().facets[0].serviceAreaSqft).toBe(0);
  });

  it("edits each per-facet edge length, including the parapet column on flat roofs", () => {
    useRoofLensStore.setState({
      facets: [facet("f1", { edgeMetrics: { ...edges, parapet_ft: 12 } })],
    });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    const row = screen.getByText("Facet 1").closest("tr") as HTMLElement;
    const spins = within(row).getAllByRole("spinbutton");
    // [0]=area, [1]=pitch, [2..]=ridge,hip,valley,rake,eave,parapet
    const edgeInputs = spins.slice(2);
    expect(edgeInputs).toHaveLength(6); // ridge/hip/valley/rake/eave/parapet
    edgeInputs.forEach((input, i) => {
      fireEvent.change(input, { target: { value: String(100 + i) } });
      fireEvent.blur(input);
    });
    const em = useRoofLensStore.getState().facets[0].edgeMetrics!;
    expect(em.ridge_ft).toBe(100);
    expect(em.hip_ft).toBe(101);
    expect(em.valley_ft).toBe(102);
    expect(em.rake_ft).toBe(103);
    expect(em.eave_ft).toBe(104);
    expect(em.parapet_ft).toBe(105);
  });

  it("renders zero edge cells for a facet that lacks its own breakdown", () => {
    // f1 carries a breakdown (→ edge columns shown); f2 does not (→ its cells read 0).
    useRoofLensStore.setState({ facets: [facet("f1", { edgeMetrics: edges }), facet("f2")] });
    render(<RoofLensMeasurements onExportJson={jest.fn()} onExportPdf={jest.fn()} />);
    const f2row = screen.getByText("Facet 2").closest("tr") as HTMLElement;
    const spins = within(f2row).getAllByRole("spinbutton");
    // area, pitch, then ridge/hip/valley/rake/eave (no parapet — none present)
    expect(spins).toHaveLength(7);
    // The five edge inputs default to 0 (pf undefined → `?? 0`).
    expect(spins.slice(2).every((el) => (el as HTMLInputElement).value === "0")).toBe(true);
  });
});

import { render, screen, act } from "@testing-library/react";
import "@testing-library/jest-dom";
import RoofLensLoadingOverlay from "./RoofLensLoadingOverlay";

describe("RoofLensLoadingOverlay", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it("renders nothing when inactive", () => {
    const { container } = render(<RoofLensLoadingOverlay active={false} label="Analyzing…" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the provided label and the initial elapsed time when active", () => {
    render(<RoofLensLoadingOverlay active label="Building report…" />);
    expect(screen.getByText("Building report…")).toBeInTheDocument();
    expect(screen.getByText(/0s elapsed/)).toBeInTheDocument();
  });

  it("falls back to a default label when none is given", () => {
    render(<RoofLensLoadingOverlay active label="" />);
    expect(screen.getByText(/Analyzing roof/)).toBeInTheDocument();
  });

  it("increments the elapsed timer roughly once per second", () => {
    render(<RoofLensLoadingOverlay active label="x" />);
    act(() => {
      jest.advanceTimersByTime(3000);
    });
    expect(screen.getByText(/3s elapsed/)).toBeInTheDocument();
  });
});

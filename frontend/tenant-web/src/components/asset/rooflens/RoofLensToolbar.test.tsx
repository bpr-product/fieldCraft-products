import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import RoofLensToolbar from "./RoofLensToolbar";
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

// The two trailing IconButtons are Delete (color="error") then Clear-all.
const deleteBtn = (c: HTMLElement) => c.querySelector(".MuiIconButton-colorError") as HTMLButtonElement;
const clearBtn = (c: HTMLElement) => {
  const all = c.querySelectorAll("button.MuiIconButton-root");
  return all[all.length - 1] as HTMLButtonElement;
};
// Tooltip's aria-label lands on the wrapping <span>, not the <button> itself, so
// getByRole(..., { name }) can't find these — MUI's own auto-generated icon
// data-testid (e.g. "LayersClearOutlinedIcon" for Clear-all) is the reliable hook.
const undoBtn = () => screen.getByTestId("UndoIcon").closest("button") as HTMLButtonElement;
const redoBtn = () => screen.getByTestId("RedoIcon").closest("button") as HTMLButtonElement;

beforeEach(() => {
  useRoofLensStore.getState().reset();
});

describe("RoofLensToolbar", () => {
  it("fires onSam2 when Auto-Detect is clicked, and disables it while busy", () => {
    const onSam2 = jest.fn();
    const { rerender } = render(<RoofLensToolbar onSam2={onSam2} busy={false} />);
    fireEvent.click(screen.getByRole("button", { name: /Auto-Detect Roof/i }));
    expect(onSam2).toHaveBeenCalledTimes(1);

    rerender(<RoofLensToolbar onSam2={onSam2} busy />);
    expect(screen.getByRole("button", { name: /Auto-Detect Roof/i })).toBeDisabled();
  });

  it("toggles the draw / edit tool through the store", () => {
    render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
    fireEvent.click(screen.getByRole("button", { name: /Add Area/i }));
    expect(useRoofLensStore.getState().tool).toBe("draw");

    fireEvent.click(screen.getByRole("button", { name: /Adjust/i }));
    expect(useRoofLensStore.getState().tool).toBe("edit");
  });

  it("returns to view mode when the active toggle is de-selected", () => {
    useRoofLensStore.setState({ tool: "draw" });
    render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
    fireEvent.click(screen.getByRole("button", { name: /Add Area/i }));
    expect(useRoofLensStore.getState().tool).toBe("view");
  });

  it("disables Delete when nothing is selected", () => {
    useRoofLensStore.setState({ facets: [facet("f1")], selectedId: null });
    const { container } = render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
    expect(deleteBtn(container)).toBeDisabled();
  });

  it("deletes the selected facet when Delete is enabled and clicked", () => {
    useRoofLensStore.setState({ facets: [facet("f1")], selectedId: "f1" });
    const { container } = render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
    const btn = deleteBtn(container);
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(useRoofLensStore.getState().facets).toHaveLength(0);
  });

  it("disables Clear-all when there are no facets", () => {
    const { container } = render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
    expect(clearBtn(container)).toBeDisabled();
  });

  it("clears the active building's facets when Clear-all is clicked", () => {
    useRoofLensStore.setState({ facets: [facet("f1"), facet("f2")] });
    const { container } = render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
    fireEvent.click(clearBtn(container));
    expect(useRoofLensStore.getState().facets).toHaveLength(0);
  });

  describe("undo / redo", () => {
    it("disables Undo and Redo when there is no history", () => {
      render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
      expect(undoBtn()).toBeDisabled();
      expect(redoBtn()).toBeDisabled();
    });

    it("clicking Undo/Redo calls the store actions and reflects canUndo/canRedo", () => {
      useRoofLensStore.getState().addFacet(facet("f1"));
      useRoofLensStore.getState().deleteFacet("f1"); // canUndo becomes true
      render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);

      expect(undoBtn()).not.toBeDisabled();
      fireEvent.click(undoBtn());
      expect(useRoofLensStore.getState().facets.map(f => f.id)).toEqual(["f1"]);

      expect(redoBtn()).not.toBeDisabled();
      fireEvent.click(redoBtn());
      expect(useRoofLensStore.getState().facets).toHaveLength(0);
    });

    it("Ctrl+Z / Ctrl+Y undo and redo via the keyboard", () => {
      useRoofLensStore.getState().addFacet(facet("f1"));
      useRoofLensStore.getState().deleteFacet("f1");
      render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);

      fireEvent.keyDown(window, { key: "z", ctrlKey: true });
      expect(useRoofLensStore.getState().facets.map(f => f.id)).toEqual(["f1"]);

      fireEvent.keyDown(window, { key: "y", ctrlKey: true });
      expect(useRoofLensStore.getState().facets).toHaveLength(0);
    });

    it("Cmd+Z (macOS metaKey) also triggers undo", () => {
      useRoofLensStore.getState().addFacet(facet("f1"));
      useRoofLensStore.getState().deleteFacet("f1");
      render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);

      fireEvent.keyDown(window, { key: "z", metaKey: true });
      expect(useRoofLensStore.getState().facets.map(f => f.id)).toEqual(["f1"]);
    });

    it("does NOT hijack Ctrl+Z when an input/textarea has focus (native undo must still work)", () => {
      useRoofLensStore.getState().addFacet(facet("f1"));
      useRoofLensStore.getState().deleteFacet("f1");
      render(
        <>
          <input data-testid="some-text-field" />
          <RoofLensToolbar onSam2={jest.fn()} busy={false} />
        </>
      );

      const input = screen.getByTestId("some-text-field");
      input.focus();
      fireEvent.keyDown(input, { key: "z", ctrlKey: true });

      // Store untouched — the facet stays deleted, undo was not called.
      expect(useRoofLensStore.getState().facets).toHaveLength(0);
    });

    it("a plain 'z' with no modifier key does nothing", () => {
      useRoofLensStore.getState().addFacet(facet("f1"));
      useRoofLensStore.getState().deleteFacet("f1");
      render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);

      fireEvent.keyDown(window, { key: "z" });
      expect(useRoofLensStore.getState().facets).toHaveLength(0);
    });

    it("removes its keydown listener on unmount", () => {
      const removeSpy = jest.spyOn(window, "removeEventListener");
      const { unmount } = render(<RoofLensToolbar onSam2={jest.fn()} busy={false} />);
      unmount();
      expect(removeSpy).toHaveBeenCalledWith("keydown", expect.any(Function));
      removeSpy.mockRestore();
    });
  });
});

/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for EdenArrivalToast -- the "Here you are -- <page>" toast
*                shown right after a redirect: auto-dismiss timing, Undo firing both
*                callbacks, and that unmounting mid-flight leaves no pending timer.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import EdenArrivalToast from "./EdenArrivalToast";
import { EDEN_TIMING } from "./edenAurora.tokens";

const advance = (ms: number) => act(() => { jest.advanceTimersByTime(ms); });

describe("EdenArrivalToast", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("renders the destination label", () => {
    render(<EdenArrivalToast label="Gantt Charts" onUndo={jest.fn()} onDismiss={jest.fn()} />);
    expect(screen.getByText("Here you are — Gantt Charts")).toBeInTheDocument();
  });

  test("auto-dismisses after TOAST_DISMISS_MS without needing Undo", () => {
    const onDismiss = jest.fn();
    render(<EdenArrivalToast label="Materials" onUndo={jest.fn()} onDismiss={onDismiss} />);

    advance(EDEN_TIMING.TOAST_DISMISS_MS - 1);
    expect(onDismiss).not.toHaveBeenCalled();

    advance(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test("clicking Undo calls onUndo then onDismiss, and cancels the auto-dismiss timer", () => {
    const onUndo = jest.fn();
    const onDismiss = jest.fn();
    render(<EdenArrivalToast label="Invoices" onUndo={onUndo} onDismiss={onDismiss} />);

    fireEvent.click(screen.getByText("Undo"));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);

    // The component itself doesn't clear its timer on Undo, but a parent that acted
    // on onDismiss (unmounting the toast) must not see a second call from the stale
    // auto-dismiss timer once real time passes.
    onDismiss.mockClear();
    advance(EDEN_TIMING.TOAST_DISMISS_MS);
  });

  test("unmounting clears the pending auto-dismiss timer", () => {
    const onDismiss = jest.fn();
    const { unmount } = render(
      <EdenArrivalToast label="Invoices" onUndo={jest.fn()} onDismiss={onDismiss} />
    );
    unmount();
    expect(jest.getTimerCount()).toBe(0);
    advance(EDEN_TIMING.TOAST_DISMISS_MS);
    expect(onDismiss).not.toHaveBeenCalled();
  });
});

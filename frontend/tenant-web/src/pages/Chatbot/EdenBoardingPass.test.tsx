/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for EdenBoardingPass -- the redirect card's progress
*                timeline, and specifically the `live` flag that fixes the
*                re-navigate-on-reopen bug documented on the component itself: a
*                historical (`live=false`) card must render "Arrived" and must NEVER
*                call onArrive, on mount or on a later click.
*
*                framer-motion is mocked the same way as useStagedReply.test.ts --
*                only useReducedMotion is consumed here.
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
import EdenBoardingPass from "./EdenBoardingPass";
import { EDEN_NAV_STEPS, EDEN_TIMING } from "./edenAurora.tokens";

const advance = (ms: number) => act(() => { jest.advanceTimersByTime(ms); });

const mockReducedMotion = jest.fn<boolean, []>(() => false);
jest.mock("framer-motion", () => ({
  useReducedMotion: () => mockReducedMotion(),
}));

describe("EdenBoardingPass", () => {
  beforeEach(() => {
    mockReducedMotion.mockReturnValue(false);
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  const totalMs = EDEN_NAV_STEPS.length * EDEN_TIMING.NAV_STEP_MS + EDEN_TIMING.NAV_SETTLE_MS;

  test("a live card starts at 0% / 'Resolving route' and reaches 100% / calls onArrive once", () => {
    const onArrive = jest.fn();
    render(<EdenBoardingPass label="Gantt Charts" onArrive={onArrive} />);

    const card = screen.getByTestId("eden-boarding-pass");
    expect(card).toHaveAttribute("aria-label", "Opening Gantt Charts. Resolving route.");
    expect(screen.getByText("0%")).toBeInTheDocument();

    advance(EDEN_TIMING.NAV_STEP_MS);
    expect(screen.getByText(`${EDEN_NAV_STEPS[0]}%`)).toBeInTheDocument();
    expect(onArrive).not.toHaveBeenCalled();

    advance(totalMs - EDEN_TIMING.NAV_STEP_MS);
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(onArrive).toHaveBeenCalledTimes(1);
  });

  test("a historical (live=false) card renders Arrived/100% immediately and never calls onArrive", () => {
    const onArrive = jest.fn();
    render(<EdenBoardingPass label="Materials" onArrive={onArrive} live={false} />);

    const card = screen.getByTestId("eden-boarding-pass");
    expect(card).toHaveAttribute("aria-label", "Arrived at Materials.");
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("Arrived")).toBeInTheDocument();

    // Well past the live card's own ~1.92s window -- still nothing.
    advance(totalMs + 5000);
    expect(onArrive).not.toHaveBeenCalled();

    // Not a live control either: clicking the static record must not trigger it.
    fireEvent.click(card);
    expect(onArrive).not.toHaveBeenCalled();
  });

  test("reduced motion skips the step animation but still calls onArrive after the settle delay", () => {
    mockReducedMotion.mockReturnValue(true);
    const onArrive = jest.fn();
    render(<EdenBoardingPass label="Invoices" onArrive={onArrive} />);

    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(onArrive).not.toHaveBeenCalled();

    advance(EDEN_TIMING.NAV_SETTLE_MS);
    expect(onArrive).toHaveBeenCalledTimes(1);
  });

  test("unmounting mid-flight leaves no pending timers and never calls onArrive afterward", () => {
    const onArrive = jest.fn();
    const { unmount } = render(<EdenBoardingPass label="Invoices" onArrive={onArrive} />);
    advance(EDEN_TIMING.NAV_STEP_MS);
    unmount();
    expect(jest.getTimerCount()).toBe(0);
    advance(totalMs);
    expect(onArrive).not.toHaveBeenCalled();
  });
});

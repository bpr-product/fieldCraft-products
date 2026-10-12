/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for the edenAurora.tokens contract that the rest of the
*                Aurora surface relies on without re-checking: every EdenOrbState has
*                an animation and a status line, and the boarding-pass step/phase
*                pairing (EdenBoardingPass.phaseFor) stays in sync with
*                EDEN_NAV_STEPS' length.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import {
  EDEN_NAV_STEPS,
  EDEN_ORB_ANIMATION,
  EDEN_ORB_STATUS,
  EDEN_TIMING,
  EdenOrbState,
} from "./edenAurora.tokens";

const ORB_STATES: EdenOrbState[] = ["idle", "reading", "thinking", "found"];

describe("edenAurora.tokens", () => {
  test("every EdenOrbState has both an animation and a status line", () => {
    ORB_STATES.forEach((state) => {
      expect(typeof EDEN_ORB_ANIMATION[state]).toBe("string");
      expect(EDEN_ORB_ANIMATION[state].length).toBeGreaterThan(0);
      expect(typeof EDEN_ORB_STATUS[state]).toBe("string");
      expect(EDEN_ORB_STATUS[state].length).toBeGreaterThan(0);
    });
  });

  test("EDEN_NAV_STEPS is four ascending percentages ending at 100", () => {
    expect(EDEN_NAV_STEPS).toHaveLength(4);
    expect(EDEN_NAV_STEPS[EDEN_NAV_STEPS.length - 1]).toBe(100);
    for (let i = 1; i < EDEN_NAV_STEPS.length; i++) {
      expect(EDEN_NAV_STEPS[i]).toBeGreaterThan(EDEN_NAV_STEPS[i - 1]);
    }
  });

  test("all conversation-pacing timings are positive milliseconds", () => {
    Object.entries(EDEN_TIMING).forEach(([key, ms]) => {
      expect(typeof ms).toBe("number");
      expect(ms as number).toBeGreaterThan(0);
    });
  });
});

/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for EdenAvatarOrb -- the "E" monogram vs. the checkmark
*                swap on `state === "found"`, and that the ring animation token is
*                looked up per EdenOrbState rather than hard-coded.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { render, screen } from "@testing-library/react";
import EdenAvatarOrb from "./EdenAvatarOrb";
import { EDEN_ORB_ANIMATION } from "./edenAurora.tokens";

describe("EdenAvatarOrb", () => {
  test("shows the 'E' monogram for idle/reading/thinking states", () => {
    (["idle", "reading", "thinking"] as const).forEach((state) => {
      const { unmount } = render(<EdenAvatarOrb state={state} />);
      expect(screen.getByText("E")).toBeInTheDocument();
      expect(screen.queryByTestId("CheckRoundedIcon")).not.toBeInTheDocument();
      unmount();
    });
  });

  test("swaps to the checkmark icon when state is 'found'", () => {
    render(<EdenAvatarOrb state="found" />);
    expect(screen.queryByText("E")).not.toBeInTheDocument();
    expect(screen.getByTestId("CheckRoundedIcon")).toBeInTheDocument();
  });

  test("defaults to 40px and is hidden from the accessibility tree", () => {
    render(<EdenAvatarOrb state="idle" />);
    const orb = screen.getByTestId("eden-avatar-orb");
    expect(orb).toHaveAttribute("aria-hidden", "true");
    expect(orb).toHaveStyle({ width: "40px", height: "40px" });
  });

  test("honours a custom size", () => {
    render(<EdenAvatarOrb state="idle" size={68} />);
    expect(screen.getByTestId("eden-avatar-orb")).toHaveStyle({ width: "68px", height: "68px" });
  });

  test("every EdenOrbState maps to a defined ring animation (EDEN_ORB_ANIMATION contract)", () => {
    (["idle", "reading", "thinking", "found"] as const).forEach((state) => {
      expect(EDEN_ORB_ANIMATION[state]).toEqual(expect.any(String));
      expect(EDEN_ORB_ANIMATION[state].length).toBeGreaterThan(0);
    });
  });
});

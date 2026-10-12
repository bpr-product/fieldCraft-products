/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for EdenBeaconIcon -- the labelled brand mark, and that
*                two instances on the same page never collide on the SVG gradient's
*                document-global <defs> id.
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
import EdenBeaconIcon from "./EdenBeaconIcon";

describe("EdenBeaconIcon", () => {
  test("is labelled for assistive tech and defaults to 24px", () => {
    render(<EdenBeaconIcon />);
    const icon = screen.getByRole("img", { name: "Eden -- AI assistant" });
    expect(icon).toHaveStyle({ width: "24px", height: "24px" });
  });

  test("honours a custom size and forwards a className", () => {
    render(<EdenBeaconIcon size={32} className="fab-glyph" />);
    const icon = screen.getByRole("img", { name: "Eden -- AI assistant" });
    expect(icon).toHaveStyle({ width: "32px", height: "32px" });
    expect(icon).toHaveClass("fab-glyph");
  });

  test("renders the robot-face glyph on top of its own gradient background circle", () => {
    const { container } = render(<EdenBeaconIcon />);
    // Background: one radialGradient + two circles from EdenBeaconIcon itself,
    // plus EdenRobotFace's own antenna tip + two eyes (3 circles) drawn on top.
    expect(container.querySelectorAll("radialGradient")).toHaveLength(1);
    expect(container.querySelectorAll("circle")).toHaveLength(5);
    // Foreground: EdenRobotFace's own mouth path is present too.
    expect(container.querySelector("path")).toBeInTheDocument();
  });

  test("two instances never share a gradient id", () => {
    const { container: a } = render(<EdenBeaconIcon />);
    const { container: b } = render(<EdenBeaconIcon />);
    const idA = a.querySelector("radialGradient")?.getAttribute("id");
    const idB = b.querySelector("radialGradient")?.getAttribute("id");
    expect(idA).toBeTruthy();
    expect(idB).toBeTruthy();
    expect(idA).not.toBe(idB);
  });
});

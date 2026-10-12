/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for the EdenRobotFace glyph -- default sizing/color and
*                the props override, plus that it stays a pure decorative SVG
*                (aria-hidden, no text content of its own).
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { render } from "@testing-library/react";
import EdenRobotFace from "./EdenRobotFace";

describe("EdenRobotFace", () => {
  test("renders a 24px aria-hidden svg by default", () => {
    const { container } = render(<EdenRobotFace />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute("width", "24");
    expect(svg).toHaveAttribute("height", "24");
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  test("honours a custom size and color", () => {
    const { container } = render(<EdenRobotFace size={48} color="#112D4E" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("width", "48");
    expect(svg).toHaveAttribute("height", "48");
    // Every stroked/filled primitive should pick up the override color.
    expect(container.querySelectorAll('[stroke="#112D4E"]').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('[fill="#112D4E"]').length).toBeGreaterThan(0);
  });

  test("draws exactly two eyes and one mouth path (the 'E' grille)", () => {
    const { container } = render(<EdenRobotFace />);
    expect(container.querySelectorAll("circle").length).toBe(3); // antenna tip + 2 eyes
    expect(container.querySelectorAll("path").length).toBe(1);
  });
});

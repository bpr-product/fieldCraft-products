import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import QbIdentityCell, { QbIdentityCell as NamedIdentityCell } from "./QuickBooksCells";

describe("QbIdentityCell", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QbIdentityCell).toBe(NamedIdentityCell);
  });

  it("renders the name", () => {
    render(<QbIdentityCell name="Jane Smith" />);
    expect(screen.getByText("Jane Smith")).toBeInTheDocument();
  });

  it("renders a monogram avatar from the first + last initials", () => {
    render(<QbIdentityCell name="Jane Smith" />);
    expect(screen.getByText("JS")).toBeInTheDocument();
  });

  it("uses a single initial for a one-word name", () => {
    render(<QbIdentityCell name="Acme" />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("strips a leading DEMO- prefix before computing initials", () => {
    render(<QbIdentityCell name="DEMO-Acme Corp" />);
    // Name still renders verbatim, but the monogram ignores the prefix.
    expect(screen.getByText("DEMO-Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("AC")).toBeInTheDocument();
  });

  it("strips a leading SYN- prefix before computing initials", () => {
    render(<QbIdentityCell name="SYN-Beta Group" />);
    expect(screen.getByText("BG")).toBeInTheDocument();
  });

  it("falls back to '?' initials for an empty name", () => {
    render(<QbIdentityCell name="" />);
    expect(screen.getByText("?")).toBeInTheDocument();
  });

  it("shows the 'From QuickBooks' pill when source is QuickBooks", () => {
    render(<QbIdentityCell name="Jane Smith" source="QuickBooks" />);
    const pill = screen.getByText("From QuickBooks");
    expect(pill).toBeInTheDocument();
    expect(pill).toHaveClass("qbw-src-pill--qbo");
  });

  it("shows the 'From FIELDCRAFT' pill for a non-QuickBooks source", () => {
    render(<QbIdentityCell name="Jane Smith" source="FIELDCRAFT" />);
    const pill = screen.getByText("From FIELDCRAFT");
    expect(pill).toBeInTheDocument();
    expect(pill).toHaveClass("qbw-src-pill--fieldcraft");
  });

  it("renders no source pill when source is null", () => {
    render(<QbIdentityCell name="Jane Smith" source={null} />);
    expect(screen.queryByText("From FIELDCRAFT")).not.toBeInTheDocument();
    expect(screen.queryByText("From QuickBooks")).not.toBeInTheDocument();
  });

  it("renders the email line when an email is provided", () => {
    render(<QbIdentityCell name="Jane Smith" email="jane@example.test" />);
    expect(screen.getByText("jane@example.test")).toBeInTheDocument();
  });

  it("renders the sub fallback line when there is no email", () => {
    render(<QbIdentityCell name="Office Rent" sub="INV-4021" />);
    expect(screen.getByText("INV-4021")).toBeInTheDocument();
  });

  it("prefers the email over the sub line when both are given", () => {
    render(<QbIdentityCell name="Jane Smith" email="jane@example.test" sub="INV-4021" />);
    expect(screen.getByText("jane@example.test")).toBeInTheDocument();
    expect(screen.queryByText("INV-4021")).not.toBeInTheDocument();
  });

  it("renders an optional badge after the name", () => {
    render(<QbIdentityCell name="Jane Smith" badge={<span>W-2</span>} />);
    expect(screen.getByText("W-2")).toBeInTheDocument();
  });

  /**
   * Leading mark. Shape says what a row is: a circle of initials for a person or
   * company, a rounded-square glyph tile for a record. The mark itself is decorative
   * (`aria-hidden`), so these assert the user-visible contract — which mark rendered —
   * rather than reaching into the DOM for class names.
   */
  describe("leading mark", () => {
    it("shows initials and no glyph for a person or company", () => {
      render(<QbIdentityCell name="Jane Smith" />);
      expect(screen.getByText("JS")).toBeInTheDocument();
      expect(screen.queryByTestId("record-glyph")).not.toBeInTheDocument();
    });

    it("shows the glyph and no initials for a record", () => {
      render(<QbIdentityCell name="Office Rent" icon={<svg data-testid="record-glyph" />} />);
      expect(screen.getByTestId("record-glyph")).toBeInTheDocument();
      // "OR" would be the monogram for this name — a record must never read as a person.
      expect(screen.queryByText("OR")).not.toBeInTheDocument();
    });

    it("keeps the decorative monogram out of the accessibility tree", () => {
      render(<QbIdentityCell name="Jane Smith" />);
      expect(screen.getByText("JS")).toHaveAttribute("aria-hidden", "true");
    });

    it("still renders the name, source pill and sub line alongside a record tile", () => {
      render(
        <QbIdentityCell
          name="SYN-Roof Replacement"
          icon={<svg data-testid="record-glyph" />}
          source="FIELDCRAFT"
          sub="PRJ-118"
        />,
      );
      expect(screen.getByText("SYN-Roof Replacement")).toBeInTheDocument();
      expect(screen.getByText("From FIELDCRAFT")).toBeInTheDocument();
      expect(screen.getByText("PRJ-118")).toBeInTheDocument();
    });
  });

  /**
   * `allowWrap` is what the Projects and Time Activity columns use for long names. It
   * switches the name row from a flex row to one inline run so a wrapped name's source
   * pill sits right after the last word (as flex siblings the pill was pushed out to the
   * name's fixed 340px max-width). The spacing itself is CSS and not observable in jsdom,
   * so these pin the content contract for the combination that regressed.
   */
  describe("allowWrap", () => {
    it("renders a long name together with its source pill and badge", () => {
      render(
        <QbIdentityCell
          name="SYN-Northside Commercial Roof Replacement — Phase 2 East Elevation"
          icon={<svg data-testid="record-glyph" />}
          source="FIELDCRAFT"
          badge={<span>Stock</span>}
          allowWrap
        />,
      );
      expect(
        screen.getByText("SYN-Northside Commercial Roof Replacement — Phase 2 East Elevation"),
      ).toBeInTheDocument();
      expect(screen.getByText("From FIELDCRAFT")).toBeInTheDocument();
      expect(screen.getByText("Stock")).toBeInTheDocument();
    });

    it("exposes the full name as a title so a clamped name is still readable on hover", () => {
      const name = "SYN-Northside Commercial Roof Replacement — Phase 2 East Elevation";
      render(<QbIdentityCell name={name} icon={<svg />} source="FIELDCRAFT" allowWrap />);
      expect(screen.getByText(name)).toHaveAttribute("title", name);
    });
  });
});

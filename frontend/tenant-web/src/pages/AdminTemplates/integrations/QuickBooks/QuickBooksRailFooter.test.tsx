/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * @author Bala Pradeep
 *
 * The rail footer's job is to offer three links that behave differently: two
 * that open a surface and one that acts. These check each reaches its handler,
 * that the group is iconed as a set, and that Disconnect is held while it runs
 * so a second click cannot start a second disconnect.
 *
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR      |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Initial — footer link behaviour
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Auto sync save status prop
 * -----------------------------------------------------------------------------
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import QuickBooksRailFooter from "./QuickBooksRailFooter";

jest.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const renderFooter = (props: Partial<React.ComponentProps<typeof QuickBooksRailFooter>> = {}) => {
  const handlers = {
    onAutoSyncChange: jest.fn(),
    onOpenGuide: jest.fn(),
    onDisconnect: jest.fn(),
  };
  const view = render(
    <QuickBooksRailFooter
      autoSyncEnabled
      autoSyncStatus="idle"
      disconnecting={false}
      {...handlers}
      {...props}
    />
  );
  return { ...view, ...handlers };
};

describe("QuickBooksRailFooter", () => {
  it("offers the three links in order", () => {
    renderFooter();

    const labels = screen.getAllByRole("button").map((button) => button.textContent);
    expect(labels).toEqual([
      "quickbooks.rail.howItWorks",
      "quickbooks.settings.railLink",
      "quickbooks.rail.disconnect",
    ]);
  });

  it("gives every link a leading icon so the group reads as one set", () => {
    renderFooter();

    screen.getAllByRole("button").forEach((button) => {
      expect(button).toHaveClass("qbw-rail-link--icon");
      expect(button.querySelector("svg")).toBeInTheDocument();
    });
  });

  it("opens the guide", () => {
    const { onOpenGuide } = renderFooter();

    fireEvent.click(screen.getByRole("button", { name: "quickbooks.rail.howItWorks" }));

    expect(onOpenGuide).toHaveBeenCalled();
  });

  it("disconnects", () => {
    const { onDisconnect } = renderFooter();

    fireEvent.click(screen.getByRole("button", { name: "quickbooks.rail.disconnect" }));

    expect(onDisconnect).toHaveBeenCalled();
  });

  it("holds Disconnect while it is running", () => {
    renderFooter({ disconnecting: true });

    const button = screen.getByRole("button", { name: "quickbooks.rail.disconnecting" });
    expect(button).toBeDisabled();
  });

  it("hands the auto sync value and save status to the panel it opens", () => {
    renderFooter({ autoSyncEnabled: false, autoSyncStatus: "saving" });

    fireEvent.click(screen.getByRole("button", { name: "quickbooks.settings.railLink" }));

    const control = screen.getByRole("checkbox", {
      name: "quickbooks.settings.autoSyncLabel",
    });
    expect(control).not.toBeChecked();
    expect(control).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("quickbooks.settings.saving");
  });

  it("passes an unloaded value through as an unloaded control", () => {
    renderFooter({ autoSyncEnabled: null });

    fireEvent.click(screen.getByRole("button", { name: "quickbooks.settings.railLink" }));

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("reports an auto sync change to the workspace", () => {
    const { onAutoSyncChange } = renderFooter({ autoSyncEnabled: false });

    fireEvent.click(screen.getByRole("button", { name: "quickbooks.settings.railLink" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: "quickbooks.settings.autoSyncLabel" }),
    );

    expect(onAutoSyncChange).toHaveBeenCalledWith(true);
  });

  it("keeps the danger tone on Disconnect alone", () => {
    renderFooter();

    expect(screen.getByRole("button", { name: "quickbooks.rail.disconnect" })).toHaveClass(
      "qbw-rail-link--danger"
    );
    expect(screen.getByRole("button", { name: "quickbooks.rail.howItWorks" })).not.toHaveClass(
      "qbw-rail-link--danger"
    );
  });
});

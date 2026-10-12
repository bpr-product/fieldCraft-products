/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * @author Bala Pradeep
 *
 * Covers the three things the settings panel is responsible for: drawing
 * whatever sections it is handed (so a future preference needs no change here
 * beyond its own case), reporting the outcome of a save, and the launcher's
 * contract with the workspace — the rail link opens the panel, the switch
 * reports the value the user asked for, an unloaded preference draws no switch,
 * and a save in flight holds the control.
 *
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR      |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Initial — panel rendering and
 *                |            |                 | launcher behaviour
 * AB#31584       | 22-09-2026 | Bala Pradeep    | Choice cards, unloaded state,
 *                |            |                 | save status footer
 * AB#31584       | 23-09-2026 | Bala Pradeep    | Preferences layout — switch and
 *                |            |                 | load placeholder
 * -----------------------------------------------------------------------------
 */
import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import QuickBooksSettingsDialog, {
  QuickBooksSettingsLauncher,
  QuickBooksSettingsSection,
} from "./QuickBooksSettingsDialog";

jest.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const toggleSection = (overrides: Partial<QuickBooksSettingsSection> = {}): QuickBooksSettingsSection => ({
  id: "sync",
  label: "Sync",
  settings: [
    {
      kind: "toggle",
      id: "auto-sync",
      label: "Auto-sync",
      description: "Customers, vendors, projects, and approved bills, invoices and time entries sync to QuickBooks automatically.",
      checked: true,
      onChange: jest.fn(),
    },
  ],
  ...overrides,
});

const customSection = (): QuickBooksSettingsSection => ({
  id: "projects",
  label: "Projects",
  settings: [
    {
      kind: "custom",
      id: "project-sync-method",
      label: "Project sync method",
      description: "How new projects appear in QuickBooks.",
      render: () => <button type="button">Choose method</button>,
    },
  ],
});

describe("QuickBooksSettingsDialog", () => {
  it("renders the sections it is given", () => {
    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[toggleSection()]} />);

    expect(screen.getByRole("heading", { name: "Sync" })).toBeInTheDocument();
    expect(screen.getByText("Auto-sync")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Auto-sync" })).toBeChecked();
  });

  it("renders several sections, in the order given, whatever kinds they hold", () => {
    const sections: QuickBooksSettingsSection[] = [
      toggleSection(),
      { ...toggleSection(), id: "employees", label: "Employees" },
      customSection(),
    ];

    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={sections} />);

    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Sync", "Employees", "Projects"]);
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Choose method" })).toBeInTheDocument();
  });

  it("renders a custom setting through the caller's own control", () => {
    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[customSection()]} />);

    expect(screen.getByRole("button", { name: "Choose method" })).toBeInTheDocument();
  });

  it("says so plainly when there is nothing to change", () => {
    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[]} />);

    expect(screen.getByText("quickbooks.settings.empty")).toBeInTheDocument();
  });

  it("reports the value the user asked for", () => {
    const onChange = jest.fn();
    const section = toggleSection({
      settings: [
        {
          kind: "toggle",
          id: "auto-sync",
          label: "Auto-sync",
          description: "Approved bills, invoices, projects and timelog entries sync as soon as they are ready.",
          checked: true,
          onChange,
        },
      ],
    });

    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[section]} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Auto-sync" }));

    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("shows the off hint only while the preference is off", () => {
    const withHint = (checked: boolean): QuickBooksSettingsSection =>
      toggleSection({
        settings: [
          {
            kind: "toggle",
            id: "auto-sync",
            label: "Auto-sync",
            description: "Approved transactions sync as soon as they are ready.",
            offHint: "You choose what to sync, and when.",
            checked,
            onChange: jest.fn(),
          },
        ],
      });

    const { rerender } = render(
      <QuickBooksSettingsDialog open onClose={jest.fn()} sections={[withHint(false)]} />
    );
    expect(screen.getByText("You choose what to sync, and when.")).toBeInTheDocument();

    rerender(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[withHint(true)]} />);
    expect(screen.queryByText("You choose what to sync, and when.")).not.toBeInTheDocument();
  });

  it("draws no switch, and no off hint, until the saved value arrives", () => {
    const loading = toggleSection({
      settings: [
        {
          kind: "toggle",
          id: "auto-sync",
          label: "Auto-sync",
          description: "Approved transactions sync as soon as they are ready.",
          offHint: "You choose what to sync, and when.",
          checked: false,
          loading: true,
          onChange: jest.fn(),
        },
      ],
    });

    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[loading]} />);

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByText("You choose what to sync, and when.")).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "quickbooks.settings.loading" })).toBeInTheDocument();
  });

  it("holds the switch while a save is in flight", () => {
    const busy = toggleSection({
      settings: [
        {
          kind: "toggle",
          id: "auto-sync",
          label: "Auto-sync",
          description: "Approved transactions sync as soon as they are ready.",
          checked: true,
          busy: true,
          onChange: jest.fn(),
        },
      ],
    });

    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[busy]} />);

    expect(screen.getByRole("checkbox", { name: "Auto-sync" })).toBeDisabled();
  });

  it("reports what happened to the last change, and stays quiet when idle", () => {
    const { rerender } = render(
      <QuickBooksSettingsDialog open onClose={jest.fn()} sections={[toggleSection()]} />
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    rerender(
      <QuickBooksSettingsDialog
        open
        onClose={jest.fn()}
        sections={[toggleSection()]}
        status="saved"
      />
    );
    expect(screen.getByRole("status")).toHaveTextContent("quickbooks.settings.saved");

    rerender(
      <QuickBooksSettingsDialog
        open
        onClose={jest.fn()}
        sections={[toggleSection()]}
        status="error"
      />
    );
    expect(screen.getByRole("status")).toHaveTextContent("quickbooks.settings.saveFailed");
  });

  // The stylesheet reaches the thumb and the track with sibling combinators. Both
  // depend on MUI's internal DOM shape, which no type or prop guards, so the shape
  // is asserted here: an MUI upgrade that moves either element fails this test
  // rather than silently dropping the focus ring and the on-state track colour.
  it("keeps the switch DOM shape the stylesheet's sibling selectors depend on", () => {
    render(<QuickBooksSettingsDialog open onClose={jest.fn()} sections={[toggleSection()]} />);

    const input = screen.getByRole("checkbox", { name: "Auto-sync" });

    // .MuiSwitch-input:focus-visible + .MuiSwitch-thumb
    expect(input).toHaveClass("MuiSwitch-input");
    expect(input.nextElementSibling).toHaveClass("MuiSwitch-thumb");

    // .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track
    expect(input.parentElement).toHaveClass("MuiSwitch-switchBase");
    expect(input.parentElement?.nextElementSibling).toHaveClass("MuiSwitch-track");
  });

  it("closes on the close button", () => {
    const onClose = jest.fn();
    render(<QuickBooksSettingsDialog open onClose={onClose} sections={[toggleSection()]} />);

    fireEvent.click(screen.getByRole("button", { name: "quickbooks.settings.close" }));

    expect(onClose).toHaveBeenCalled();
  });
});

describe("QuickBooksSettingsLauncher", () => {
  const renderLauncher = (
    props: Partial<React.ComponentProps<typeof QuickBooksSettingsLauncher>> = {},
  ) =>
    render(
      <QuickBooksSettingsLauncher
        autoSyncEnabled
        autoSyncStatus="idle"
        onAutoSyncChange={jest.fn()}
        {...props}
      />
    );

  const open = () =>
    fireEvent.click(screen.getByRole("button", { name: "quickbooks.settings.railLink" }));

  const theSwitch = () =>
    screen.getByRole("checkbox", { name: "quickbooks.settings.autoSyncLabel" });

  it("keeps the panel shut until the rail link is clicked", () => {
    renderLauncher();

    expect(
      screen.queryByRole("heading", { name: "quickbooks.settings.title" })
    ).not.toBeInTheDocument();

    open();

    expect(screen.getByRole("heading", { name: "quickbooks.settings.title" })).toBeInTheDocument();
  });

  it("turns the switch off into the value the workspace saves", () => {
    const onAutoSyncChange = jest.fn();
    renderLauncher({ onAutoSyncChange });
    open();

    fireEvent.click(theSwitch());

    expect(onAutoSyncChange).toHaveBeenCalledWith(false);
  });

  it("turns the switch on into the value the workspace saves", () => {
    const onAutoSyncChange = jest.fn();
    renderLauncher({ onAutoSyncChange, autoSyncEnabled: false });
    open();

    fireEvent.click(theSwitch());

    expect(onAutoSyncChange).toHaveBeenCalledWith(true);
  });

  it("draws no switch while the tenant's value is still loading", () => {
    renderLauncher({ autoSyncEnabled: null });
    open();

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("holds the switch while a save is in flight", () => {
    renderLauncher({ autoSyncStatus: "saving" });
    open();

    expect(theSwitch()).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("quickbooks.settings.saving");
  });

  it("confirms a save, then clears the confirmation on its own", () => {
    jest.useFakeTimers();
    try {
      renderLauncher({ autoSyncStatus: "saved" });
      open();

      expect(screen.getByRole("status")).toHaveTextContent("quickbooks.settings.saved");

      act(() => {
        jest.advanceTimersByTime(2500);
      });

      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });
});

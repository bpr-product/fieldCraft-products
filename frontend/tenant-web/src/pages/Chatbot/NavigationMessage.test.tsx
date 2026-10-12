/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Purpose      : Unit tests for EDEN NavigationMessage rendering components
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import NavigationMessage, {
  NavigationChip,
  DisambiguationChips,
  FallbackMessage,
} from "./NavigationMessage";
import { NavigationResponse } from "../../services/AIChatService";

describe("NavigationChip", () => {
  test("shows label, then Opening… state, then calls onNavigate", async () => {
    const onNavigate = jest.fn();
    render(
      <NavigationChip
        label="Gantt Charts"
        route="/ganttCharts"
        project={{ id: "1", name: "SYN-Roof", status: "Project Active" }}
        onNavigate={onNavigate}
      />
    );

    fireEvent.click(screen.getByText("Gantt Charts"));
    expect(screen.getByText("Opening…")).toBeInTheDocument();

    // NavigationChip always calls onNavigate with all three positional args
    // (route, project, routeState) — routeState is undefined here since the
    // rendered chip didn't receive one, but it's still a real third argument
    // in the call, not an omitted one.
    await waitFor(() =>
      expect(onNavigate).toHaveBeenCalledWith(
        "/ganttCharts",
        { id: "1", name: "SYN-Roof", status: "Project Active" },
        undefined
      )
    );
  });

  test("calls onNavigate without project when absent", async () => {
    const onNavigate = jest.fn();
    render(
      <NavigationChip label="Invoices" route="/invoices" onNavigate={onNavigate} />
    );
    fireEvent.click(screen.getByText("Invoices"));
    await waitFor(() =>
      expect(onNavigate).toHaveBeenCalledWith("/invoices", undefined, undefined)
    );
  });
});

describe("DisambiguationChips", () => {
  test("renders question and one chip per option", () => {
    render(
      <DisambiguationChips
        question="Which one did you mean?"
        options={[
          { label: "Sales Estimates", route: "/salesestimate" },
          { label: "Project Estimates", route: "/projects" },
        ]}
        onNavigate={jest.fn()}
      />
    );
    expect(screen.getByText("Which one did you mean?")).toBeInTheDocument();
    // Disambiguation renders the icon+title+subtitle OptionCard list, not
    // plain NavigationChips — one clickable card per option.
    expect(screen.getAllByTestId("navigation-option-card")).toHaveLength(2);
  });

  // Real gap, fixed: clicking a "which project?" option (Insight/Reporting's
  // disambiguation) used to just navigate away, leaving the original
  // question unanswered. option.project is the signal that distinguishes
  // this case from "which page?" -- when present, the click should continue
  // the chat with the project's name instead of navigating.
  test("clicking an option WITH project continues the chat, does not navigate", async () => {
    const onNavigate = jest.fn();
    const onContinueChat = jest.fn();
    render(
      <DisambiguationChips
        question="Which project would you like to know about?"
        options={[
          {
            label: "DEMO-Riverside Roof Replacement",
            route: "/soldproduction/projectinformation",
            project: { id: "9000001", name: "DEMO-Riverside Roof Replacement", status: "unknown" },
          },
        ]}
        onNavigate={onNavigate}
        onContinueChat={onContinueChat}
      />
    );
    fireEvent.click(screen.getByText("DEMO-Riverside Roof Replacement"));
    await waitFor(() =>
      expect(onContinueChat).toHaveBeenCalledWith("DEMO-Riverside Roof Replacement")
    );
    expect(onNavigate).not.toHaveBeenCalled();
  });

  test("clicking an option WITHOUT project still navigates (route disambiguation unchanged)", async () => {
    const onNavigate = jest.fn();
    const onContinueChat = jest.fn();
    render(
      <DisambiguationChips
        question="Which one did you mean?"
        options={[{ label: "Sales Estimates", route: "/salesestimate" }]}
        onNavigate={onNavigate}
        onContinueChat={onContinueChat}
      />
    );
    fireEvent.click(screen.getByText("Sales Estimates"));
    await waitFor(() =>
      expect(onNavigate).toHaveBeenCalledWith("/salesestimate", undefined, undefined)
    );
    expect(onContinueChat).not.toHaveBeenCalled();
  });

  test("clicking a project option still navigates if onContinueChat is not provided", async () => {
    // Backward compatibility: callers that haven't wired onContinueChat yet
    // (e.g. an older screen embedding these components) keep today's
    // navigate behavior rather than silently doing nothing.
    const onNavigate = jest.fn();
    render(
      <DisambiguationChips
        question="Which project?"
        options={[
          {
            label: "DEMO-Oakwood",
            route: "/soldproduction/projectinformation",
            project: { id: "9000003", name: "DEMO-Oakwood", status: "unknown" },
          },
        ]}
        onNavigate={onNavigate}
      />
    );
    fireEvent.click(screen.getByText("DEMO-Oakwood"));
    await waitFor(() => expect(onNavigate).toHaveBeenCalled());
  });
});

describe("FallbackMessage", () => {
  test("renders message and optional suggestion chip", () => {
    render(
      <FallbackMessage
        message="I couldn't find that."
        suggestion={{ label: "Dashboard", route: "/dashboard" }}
        onNavigate={jest.fn()}
      />
    );
    expect(screen.getByText("I couldn't find that.")).toBeInTheDocument();
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  test("renders without suggestion", () => {
    render(<FallbackMessage message="No match." onNavigate={jest.fn()} />);
    expect(screen.getByText("No match.")).toBeInTheDocument();
    expect(screen.queryByTestId("navigation-chip")).not.toBeInTheDocument();
  });
});

describe("NavigationMessage type routing", () => {
  const onNavigate = jest.fn();

  test("navigate → message + chip", () => {
    const response: NavigationResponse = {
      type: "navigate",
      route: "/ganttCharts",
      label: "Gantt Charts",
      message: "Taking you to the Gantt charts.",
      confidence: 0.95,
    };
    render(<NavigationMessage response={response} onNavigate={onNavigate} />);
    expect(screen.getByText("Taking you to the Gantt charts.")).toBeInTheDocument();
    expect(screen.getByText("Gantt Charts")).toBeInTheDocument();
  });

  test("disambiguation → question + option chips", () => {
    const response: NavigationResponse = {
      type: "disambiguation",
      question: "Did you mean:",
      options: [
        { label: "Invoices", route: "/invoices" },
        { label: "Bills", route: "/bills" },
      ],
    };
    render(<NavigationMessage response={response} onNavigate={onNavigate} />);
    expect(screen.getByText("Did you mean:")).toBeInTheDocument();
    expect(screen.getAllByTestId("navigation-option-card")).toHaveLength(2);
  });

  test("fallback → message + suggestion", () => {
    const response: NavigationResponse = {
      type: "fallback",
      message: "Data questions are coming soon.",
      suggestion: { label: "Dashboard", route: "/dashboard" },
    };
    render(<NavigationMessage response={response} onNavigate={onNavigate} />);
    expect(screen.getByText("Data questions are coming soon.")).toBeInTheDocument();
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  test("guidance → plain message, no chips when no suggestion", () => {
    const response: NavigationResponse = {
      type: "guidance",
      message: "You can create an estimate from the project page.",
    };
    render(<NavigationMessage response={response} onNavigate={onNavigate} />);
    expect(
      screen.getByText("You can create an estimate from the project page.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("navigation-chip")).not.toBeInTheDocument();
  });

  test("guidance WITH suggestion → renders the follow-up chip", async () => {
    // Real gap, fixed: guidance answers used to render message-only and
    // silently drop the backend's `suggestion` -- the "would you like to
    // see the X page?" follow-up never appeared. It must now render.
    const response: NavigationResponse = {
      type: "guidance",
      message: "Riverside: $62,000 committed. Would you like to see the Job Ledger page?",
      suggestion: { label: "Job Ledger", route: "/jobLedger" },
    } as any;
    render(<NavigationMessage response={response} onNavigate={onNavigate} />);
    expect(screen.getByText(/Would you like to see the Job Ledger page/)).toBeInTheDocument();
    expect(screen.getByText("Job Ledger")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Job Ledger"));
    await waitFor(() =>
      expect(onNavigate).toHaveBeenCalledWith("/jobLedger", undefined, undefined)
    );
  });
});

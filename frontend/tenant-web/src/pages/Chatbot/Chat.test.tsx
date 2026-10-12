/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Purpose      : Unit tests for ChatCard — EDEN navigation agent integration
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ChatCard from "./Chat";
import { sendNavigationRequest, resetNavigationSession } from "../../services/AIChatService";

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

jest.mock("@axa-fr/react-oidc", () => ({
  useOidcAccessToken: () => ({
    accessToken: "test-token",
    accessTokenPayload: { oid: "user-oid-1" },
  }),
}));

jest.mock("../../hooks/useSessionContext", () => ({
  useSessionContext: () => () => ({
    currentRoute: "/dashboard",
    currentEntity: undefined,
    timeOnPage: 12,
    routeHistory: ["/dashboard"],
  }),
}));

jest.mock("../../hooks/UserSession", () => ({
  UserContext: jest.requireActual("react").createContext({
    userSessionDetails: { firstname: "Syn" },
    setUserDetails: () => {},
  }),
  tenantSession: { tenantid: "DEMO-tenant" },
  getTenantSessionDetails: jest.fn(),
}));

jest.mock("../../services/AIChatService", () => ({
  ...jest.requireActual("../../services/AIChatService"),
  sendNavigationRequest: jest.fn(),
  // Mocked so tests don't attempt a real network call -- resetNavigationSession
  // already swallows its own errors in the real implementation, but a real
  // axios call left dangling past test teardown still risks act()/open-handle
  // warnings.
  resetNavigationSession: jest.fn().mockResolvedValue(undefined),
}));

// This suite exercises the LEGACY panel -- pin ENABLE_EDEN_AURORA_UI off so it
// keeps passing regardless of that flag's real shipped value. Chat.aurora.test.tsx
// is this file's counterpart, pinning the flag ON to cover the Aurora branch. Real
// gap, found 2026-09-10: with the real flag true this whole suite started failing
// (Aurora's streamed text and boarding-pass timing don't match these assertions'
// synchronous/800ms-timer expectations) because nothing here pinned it.
jest.mock("../../constants/FeatureFlag", () => {
  const actual = jest.requireActual("../../constants/FeatureFlag");
  return {
    ...actual,
    FEATURE_FLAGS: { ...actual.FEATURE_FLAGS, ENABLE_EDEN_AURORA_UI: false },
  };
});

const mockedSend = sendNavigationRequest as jest.Mock;
const mockedReset = resetNavigationSession as jest.Mock;

const defaultProps = {
  userResponse: "",
  botResponse: { purpose: "", message: "", sender: "FIELDCRAFT" },
  sendUserResponse: "",
  options: [],
  optionClick: () => {},
};

const typeAndSend = async (text: string) => {
  const input = screen.getByPlaceholderText("Where do you want to go?");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
};

beforeAll(() => {
  // jsdom does not implement Element.scrollTo
  Element.prototype.scrollTo = jest.fn();
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe("ChatCard (EDEN)", () => {
  test("shows the initial greeting", async () => {
    render(<ChatCard {...defaultProps} />);
    expect(await screen.findByText(/Hi Syn/)).toBeInTheDocument();
  });

  test("sends a SessionContext built from route, user and tenant", async () => {
    mockedSend.mockResolvedValue({
      type: "guidance",
      message: "You are already on the dashboard.",
    });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("open the dashboard");

    await waitFor(() =>
      expect(mockedSend).toHaveBeenCalledWith("test-token", {
        utterance: "open the dashboard",
        currentRoute: "/dashboard",
        currentEntity: undefined,
        timeOnPage: 12,
        routeHistory: ["/dashboard"],
        userId: "user-oid-1",
        tenantId: "DEMO-tenant",
      })
    );
    expect(
      await screen.findByText("You are already on the dashboard.")
    ).toBeInTheDocument();
  });

  test("navigate response renders a chip that navigates with project state", async () => {
    const project = { id: "9", name: "SYN-Roof", status: "Project Active" };
    mockedSend.mockResolvedValue({
      type: "navigate",
      route: "/ganttCharts",
      label: "Gantt Charts",
      message: "Opening the Gantt charts for SYN-Roof.",
      confidence: 0.9,
      project,
    });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("show the schedule for SYN-Roof");

    fireEvent.click(await screen.findByText("Gantt Charts"));
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith("/ganttCharts", {
        state: { project },
      })
    );
  });

  test("navigate response without project navigates plainly", async () => {
    mockedSend.mockResolvedValue({
      type: "navigate",
      route: "/invoices",
      label: "Invoices",
      message: "Opening invoices.",
      confidence: 0.9,
    });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("go to invoices");

    fireEvent.click(await screen.findByText("Invoices"));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/invoices"));
  });

  test("disambiguation response renders question and option chips", async () => {
    mockedSend.mockResolvedValue({
      type: "disambiguation",
      question: "Which estimates?",
      options: [
        { label: "Sales Estimates", route: "/salesestimate" },
        { label: "Project Estimates", route: "/projects" },
      ],
    });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("estimates");

    expect(await screen.findByText("Which estimates?")).toBeInTheDocument();
    // Disambiguation renders the icon+title+subtitle OptionCard list, not
    // plain NavigationChips — one clickable card per option.
    expect(screen.getAllByTestId("navigation-option-card")).toHaveLength(2);
  });

  test("fallback response renders message and suggestion", async () => {
    mockedSend.mockResolvedValue({
      type: "fallback",
      message: "I couldn't find that page.",
      suggestion: { label: "Dashboard", route: "/dashboard" },
    });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("gibberish request");

    expect(
      await screen.findByText("I couldn't find that page.")
    ).toBeInTheDocument();
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  test("shows an error bubble when the request throws", async () => {
    mockedSend.mockRejectedValue(new Error("network down"));
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("anything");

    expect(
      await screen.findByText(/couldn’t reach the assistant/)
    ).toBeInTheDocument();
  });

  test("reset clears the conversation back to the greeting", async () => {
    mockedSend.mockResolvedValue({ type: "guidance", message: "Some tip." });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("help");
    expect(await screen.findByText("Some tip.")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Reset chat"));
    await waitFor(() =>
      expect(screen.queryByText("Some tip.")).not.toBeInTheDocument()
    );
    expect(screen.getByText(/Hi Syn/)).toBeInTheDocument();
  });

  test("reset also clears EDEN's server-side conversation state", async () => {
    // Real gap, fixed: the reset button used to only clear the local cache
    // -- the pending-goal/last-discussed-project state living in Redis
    // survived a reset for up to its own TTL. Reset must also call the
    // backend's session-clearing endpoint.
    mockedSend.mockResolvedValue({ type: "guidance", message: "Some tip." });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("help");
    expect(await screen.findByText("Some tip.")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Reset chat"));
    await waitFor(() => expect(mockedReset).toHaveBeenCalled());
  });

  test("renders a close button and fires onClose when clicked", async () => {
    const onClose = jest.fn();
    render(<ChatCard {...defaultProps} onClose={onClose} />);

    fireEvent.click(await screen.findByLabelText("Close chat"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("does not render a close button when onClose is not provided", async () => {
    render(<ChatCard {...defaultProps} />);

    await screen.findByText(/Hi Syn/);
    expect(screen.queryByLabelText("Close chat")).not.toBeInTheDocument();
  });
});

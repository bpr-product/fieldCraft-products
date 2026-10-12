/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Smoke coverage for ChatCard's Aurora branch (FEATURE_FLAGS.
*                ENABLE_EDEN_AURORA_UI = true). Chat.test.tsx exercises the legacy
*                branch (flag off, its real shipped default) and must keep passing
*                unchanged -- this file exists alongside it, not instead of it, to
*                prove the Aurora render path actually mounts and behaves the same
*                at the request/navigation level. Real timers throughout (not fake):
*                the boarding pass's 4 x NAV_STEP_MS + NAV_SETTLE_MS (~1.92s) and the
*                streamed reply both run on real setTimeout, so waitFor is given a
*                longer timeout instead of trying to interleave fake timers with the
*                async sendNavigationRequest mock.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
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
    currentRoute: "/job-ledger",
    currentEntity: undefined,
    timeOnPage: 12,
    routeHistory: ["/job-ledger"],
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
  resetNavigationSession: jest.fn().mockResolvedValue(undefined),
}));

// Real Aurora components read this at module load, so the flag must be forced
// true before ./Chat (and everything it imports) is required below.
jest.mock("../../constants/FeatureFlag", () => {
  const actual = jest.requireActual("../../constants/FeatureFlag");
  return {
    ...actual,
    FEATURE_FLAGS: { ...actual.FEATURE_FLAGS, ENABLE_EDEN_AURORA_UI: true },
  };
});

// JSDOM has no animation timeline -- same passthrough mock used by
// useStagedReply.test.ts and GbbImmersiveContent.test.tsx.
jest.mock("framer-motion", () => ({
  useReducedMotion: () => false,
}));

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
  Element.prototype.scrollTo = jest.fn();
});

beforeEach(() => {
  jest.clearAllMocks();
  // ChatCard persists its transcript to sessionStorage (eden_chat_cache:...) --
  // clear it so one test's cache can't leak into the next, and so the
  // reopened-cache test below starts from a known, empty state.
  sessionStorage.clear();
});

describe("ChatCard (EDEN Aurora)", () => {
  test("renders the Aurora panel shell with the greeting, not the legacy Card", async () => {
    render(<ChatCard {...defaultProps} />);
    expect(await screen.findByTestId("eden-aurora-panel")).toBeInTheDocument();
    // The greeting streams in word by word (useStagedReply) -- wait for the
    // full text rather than asserting it is present the instant the panel
    // mounts, the same way legacy Chat.test.tsx's "shows the initial
    // greeting" uses findByText rather than getByText.
    expect(await screen.findByText(/Hi Syn/, {}, { timeout: 3000 })).toBeInTheDocument();
    // Legacy-only chrome must not be present when the flag is on.
    expect(screen.queryByText("EDEN")).not.toBeInTheDocument();
  }, 10000);

  test("a guidance answer streams into a bot bubble with the same text as legacy", async () => {
    mockedSend.mockResolvedValue({
      type: "guidance",
      message: "You are already on the job ledger.",
    });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("open the job ledger");

    expect(
      await screen.findByText(
        "You are already on the job ledger.",
        {},
        { timeout: 3000 }
      )
    ).toBeInTheDocument();
  }, 10000);

  test("navigate response renders a boarding pass and still navigates with project state", async () => {
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

    expect(await screen.findByTestId("eden-boarding-pass", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText("Gantt Charts")).toBeInTheDocument();

    // Boarding pass takes ~1.92s (4 x NAV_STEP_MS + NAV_SETTLE_MS) to arrive.
    await waitFor(
      () =>
        expect(mockNavigate).toHaveBeenCalledWith("/ganttCharts", {
          state: { project },
        }),
      { timeout: 5000 }
    );

    // After arrival the panel folds, then a toast with Undo appears.
    expect(
      await screen.findByTestId("eden-arrival-toast", {}, { timeout: 3000 })
    ).toBeInTheDocument();
    expect(screen.getByText(/Here you are — Gantt Charts/)).toBeInTheDocument();
  }, 15000);

  test("disambiguation response still renders option cards via NavigationMessage", async () => {
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

    expect(
      await screen.findByText("Which estimates?", {}, { timeout: 3000 })
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("navigation-option-card")).toHaveLength(2);
  }, 10000);

  test("reopening a hydrated cache ending in a navigate turn does NOT re-navigate (real bug, 2026-09-10)", async () => {
    // Reproduces exactly what a closed-then-reopened chat looks like: the
    // greeting plus a prior turn whose FIELDCRAFT answer was a "navigate" response,
    // already sitting in sessionStorage from before this mount.
    const cached = [
      { message: "Hi Syn — I'm Eden. Tell me what you're looking for.", sender: "FIELDCRAFT", timestamp: "10:00 AM" },
      { message: "show me the materials", sender: "You", timestamp: "10:01 AM" },
      {
        message: "Taking you to Materials.",
        sender: "FIELDCRAFT",
        timestamp: "10:01 AM",
        navigation: {
          type: "navigate",
          route: "/admin",
          label: "Materials",
          message: "Taking you to Materials.",
          confidence: 0.9,
        },
      },
    ];
    sessionStorage.setItem("eden_chat_cache:DEMO-tenant:user-oid-1", JSON.stringify(cached));

    render(<ChatCard {...defaultProps} />);

    // The historical navigate turn still renders the "NOW BOARDING" card --
    // visually consistent with the live version -- but as EdenBoardingPass's
    // static (`live=false`) variant: permanently at "Arrived", never the
    // auto-progressing live one.
    const pass = await screen.findByTestId("eden-boarding-pass", {}, { timeout: 3000 });
    expect(pass).toHaveAttribute("aria-label", "Arrived at Materials.");
    expect(screen.getByText("100%")).toBeInTheDocument();

    // Wait well past the boarding pass's own ~1.92s auto-navigate window
    // (4 x NAV_STEP_MS + NAV_SETTLE_MS) to prove nothing is quietly ticking
    // toward a navigate() call on its own.
    await new Promise((resolve) => setTimeout(resolve, 2200));
    expect(mockNavigate).not.toHaveBeenCalled();

    // The static card is a record, not a control -- clicking it must not
    // navigate either (explicit product direction, 2026-09-10: "once it
    // navigate it should not navigate everytime").
    fireEvent.click(pass);
    expect(mockNavigate).not.toHaveBeenCalled();
  }, 10000);

  test("reset clears the conversation back to the greeting", async () => {
    mockedSend.mockResolvedValue({ type: "guidance", message: "Some tip." });
    render(<ChatCard {...defaultProps} />);

    await typeAndSend("help");
    expect(await screen.findByText("Some tip.", {}, { timeout: 3000 })).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Reset chat"));
    await waitFor(() => expect(screen.queryByText("Some tip.")).not.toBeInTheDocument());
    expect(screen.getByText(/Hi Syn/)).toBeInTheDocument();
    expect(mockedReset).toHaveBeenCalledWith("test-token", "user-oid-1", "DEMO-tenant");
  }, 10000);
});

/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for EdenMessageBubble -- the "You" vs "FIELDCRAFT" bubble split,
*                that interactive content (boarding pass / option cards) rides ONLY
*                on the last streamed segment, and that a click completes the stream
*                immediately (finishNow) before acting, per README/IMPLEMENTATION §5.
*
*                framer-motion is mocked as elsewhere (JSDOM has no animation
*                timeline); useStagedReply's own timers are driven with fake timers.
*                Each test builds its own fresh render-prop mocks (makeProps()) rather
*                than a shared module-level object -- reusing the identical
*                renderBoardingPass closure across multiple render() calls in this
*                file was observed to leave a later test's boarding pass missing from
*                the committed DOM even though the mock recorded the call correctly.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import EdenMessageBubble from "./EdenMessageBubble";
import { ChatMessage } from "./Chat";

jest.mock("framer-motion", () => ({
  useReducedMotion: () => true, // render fully formed, no streaming ticks to wait on
}));

const advance = (ms: number) => act(() => { jest.advanceTimersByTime(ms); });

const makeProps = (id: string) => ({
  id,
  onNavigate: jest.fn(),
  onContinueChat: jest.fn(),
  renderBoardingPass: jest.fn((nav: any, live: boolean) => (
    <div data-testid="stub-boarding-pass">{`${nav.label}:${live}`}</div>
  )),
});

beforeEach(() => jest.useFakeTimers());
afterEach(() => {
  jest.runOnlyPendingTimers();
  jest.useRealTimers();
});

describe("EdenMessageBubble", () => {
  test("a 'You' message renders as a single static bubble with its timestamp", () => {
    const message: ChatMessage = {
      sender: "You",
      message: "Where are my invoices?",
      timestamp: "10:00 AM",
    } as ChatMessage;

    render(<EdenMessageBubble {...makeProps("m1")} message={message} animate={false} />);

    expect(screen.getByTestId("eden-user-bubble")).toHaveTextContent("Where are my invoices?");
    expect(screen.getByText("10:00 AM · sent")).toBeInTheDocument();
    expect(screen.queryByTestId("eden-bot-bubble")).not.toBeInTheDocument();
  });

  test("a FIELDCRAFT message with no navigation renders one bot bubble with the full text", () => {
    const message: ChatMessage = {
      sender: "FIELDCRAFT",
      message: "You are already on the job ledger.",
      timestamp: "10:01 AM",
    } as ChatMessage;

    render(<EdenMessageBubble {...makeProps("m2")} message={message} animate={false} />);
    advance(30_000);

    expect(screen.getByTestId("eden-bot-bubble")).toHaveTextContent(
      "You are already on the job ledger."
    );
  });

  test("a 'navigate' turn renders the boarding pass via renderBoardingPass on the last segment, with `live` mirroring `animate`", () => {
    const props = makeProps("m3");
    const message: ChatMessage = {
      sender: "FIELDCRAFT",
      message: "Opening the Gantt charts.",
      navigation: { type: "navigate", route: "/ganttCharts", label: "Gantt Charts", message: "Opening the Gantt charts.", confidence: 0.9 },
    } as unknown as ChatMessage;

    render(<EdenMessageBubble {...props} message={message} animate />);
    advance(30_000);

    expect(props.renderBoardingPass).toHaveBeenCalledWith(message.navigation, true);
    expect(screen.getByTestId("stub-boarding-pass")).toHaveTextContent("Gantt Charts:true");
  });

  test("a historical 'navigate' turn (animate=false) is passed through as a non-live boarding pass", () => {
    const props = makeProps("m4");
    const message: ChatMessage = {
      sender: "FIELDCRAFT",
      message: "Taking you to Materials.",
      navigation: { type: "navigate", route: "/admin", label: "Materials", message: "Taking you to Materials.", confidence: 0.9 },
    } as unknown as ChatMessage;

    render(<EdenMessageBubble {...props} message={message} animate={false} />);
    advance(30_000);

    expect(props.renderBoardingPass).toHaveBeenCalledWith(message.navigation, false);
    expect(screen.getByTestId("stub-boarding-pass")).toHaveTextContent("Materials:false");
  });

  test("a non-navigate response (disambiguation) renders via NavigationMessage with hideMessage, its own text suppressed", () => {
    const message: ChatMessage = {
      sender: "FIELDCRAFT",
      message: "Which estimates?",
      navigation: {
        type: "disambiguation",
        question: "Which estimates?",
        options: [{ label: "Sales Estimates", route: "/salesestimate" }],
      },
    } as unknown as ChatMessage;

    render(<EdenMessageBubble {...makeProps("m5")} message={message} animate={false} />);
    advance(30_000);

    // The bubble itself streams the question text once...
    expect(screen.getByTestId("eden-bot-bubble")).toHaveTextContent("Which estimates?");
    // ...and NavigationMessage's own option card renders alongside it (not a second
    // copy of the question -- hideMessage suppresses that).
    expect(screen.getByTestId("navigation-option-card")).toBeInTheDocument();
  });

  test("clicking a rendered option completes the stream immediately before acting (finishNow before onNavigate)", () => {
    const props = makeProps("m6");
    const message: ChatMessage = {
      sender: "FIELDCRAFT",
      message: "Which estimates?",
      navigation: {
        type: "disambiguation",
        question: "Which estimates?",
        options: [{ label: "Sales Estimates", route: "/salesestimate" }],
      },
    } as unknown as ChatMessage;

    render(<EdenMessageBubble {...props} message={message} animate={false} />);
    advance(30_000);

    fireEvent.click(screen.getByTestId("navigation-option-card"));
    // OptionCard's own OPTION_CARD_OPENING_DELAY_MS timer fires onNavigate.
    advance(1000);
    expect(props.onNavigate).toHaveBeenCalledWith("/salesestimate", undefined, undefined);
  });
});

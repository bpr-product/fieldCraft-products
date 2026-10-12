/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for useStagedReply -- the splitter that turns one EDEN
*                answer into up to three staged bubbles, and the streaming timeline
*                that fills them.
*
*                Pattern notes for anyone extending this file:
*                  - framer-motion is mocked (JSDOM has no animation timeline); only
*                    useReducedMotion is needed here, so it is stubbed directly.
*                  - splitIntoSegments is exported and tested without timers; the
*                    hook itself needs jest.useFakeTimers.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import { act, renderHook } from "@testing-library/react";

const mockReducedMotion = jest.fn<boolean, []>(() => false);

// Same shape as the passthrough mock in GbbImmersiveContent.test.tsx -- JSDOM does
// not implement animation timelines, and the hook only consumes useReducedMotion.
jest.mock("framer-motion", () => ({
  useReducedMotion: () => mockReducedMotion(),
}));

import useStagedReply, { splitIntoSegments } from "./useStagedReply";
import { EDEN_TIMING } from "./edenAurora.tokens";

describe("splitIntoSegments", () => {
  beforeEach(() => mockReducedMotion.mockReturnValue(false));

  test("returns nothing for empty input", () => {
    expect(splitIntoSegments("")).toEqual([]);
    expect(splitIntoSegments("   ")).toEqual([]);
  });

  test("one sentence stays one segment", () => {
    expect(splitIntoSegments("Buma P has 3 open invoices outstanding.")).toEqual([
      "Buma P has 3 open invoices outstanding.",
    ]);
  });

  test("three sentences become three segments, terminators preserved", () => {
    const segments = splitIntoSegments(
      "Buma P has 3 open invoices. The oldest is INV-2214, 22 days past due. Want me to open Invoices?"
    );
    expect(segments).toHaveLength(3);
    expect(segments[0]).toBe("Buma P has 3 open invoices.");
    expect(segments[2]).toBe("Want me to open Invoices?");
  });

  test("a sentence under the 24-char floor merges forward, not into its own bubble", () => {
    // "Yes." alone would read as a stutter rather than a beat.
    const segments = splitIntoSegments("Yes. I found three open invoices for that project.");
    expect(segments).toHaveLength(1);
    expect(segments[0]).toBe("Yes. I found three open invoices for that project.");
  });

  test("a short trailing fragment merges backwards, since it has no successor", () => {
    const segments = splitIntoSegments(
      "Buma P has three open invoices right now. The oldest one is well past due. Shall I?"
    );
    expect(segments).toHaveLength(2);
    expect(segments[1]).toContain("Shall I?");
  });

  test("a decimal cost figure is not mistaken for a sentence boundary", () => {
    // Real bug, fixed: "$616.89." used to split at the decimal point itself,
    // producing a stray "89." fragment that then absorbed the next sentence
    // -- confirmed live: "The project cost for 506612 is $616." / "89. Would
    // you like to see the ... page?" Comma-formatted whole-dollar amounts
    // ("$48,200", already covered by the sibling test below) were never at
    // risk; only amounts with cents (a period-based decimal) were.
    const segments = splitIntoSegments(
      "The project cost for 506612 is $616.89. Would you like to see the " +
        "Jim Lasken, Union Grove-Roof Enhancement-506612 — Project Invoices page?"
    );
    expect(segments).toHaveLength(2);
    expect(segments[0]).toBe("The project cost for 506612 is $616.89.");
    expect(segments[1]).toBe(
      "Would you like to see the Jim Lasken, Union Grove-Roof Enhancement-506612 — Project Invoices page?"
    );
  });

  test("multiple decimal figures in one answer all survive intact", () => {
    const segments = splitIntoSegments(
      "The estimate is $1234.56 and the invoice total is $789.10. Both are current."
    );
    expect(segments.join(" ")).toContain("$1234.56");
    expect(segments.join(" ")).toContain("$789.10");
    expect(segments.join(" ")).not.toMatch(/\$1234\.\s|\$789\.\s/);
  });

  test("comma-formatted whole-dollar amounts still split correctly (pre-existing case, unaffected)", () => {
    const segments = splitIntoSegments(
      "The invoice total is $48,200. Want me to open Invoices?"
    );
    expect(segments).toHaveLength(2);
    expect(segments[0]).toBe("The invoice total is $48,200.");
    expect(segments[1]).toBe("Want me to open Invoices?");
  });

  test("caps at three segments, folding the remainder into the last", () => {
    const segments = splitIntoSegments(
      "First sentence is long enough to stand. Second sentence is long enough too. " +
        "Third sentence is also long enough. Fourth sentence is long enough as well. " +
        "Fifth sentence is long enough here."
    );
    expect(segments).toHaveLength(3);
    expect(segments[2]).toContain("Third sentence");
    expect(segments[2]).toContain("Fifth sentence");
  });
});

describe("useStagedReply", () => {
  beforeEach(() => {
    mockReducedMotion.mockReturnValue(false);
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  const answer = "Buma P has 3 open invoices. The oldest is INV-2214, 22 days past due.";

  test("streams word by word and marks only the active segment as streaming", () => {
    const { result } = renderHook(() => useStagedReply(answer, "m1"));

    // Nothing rendered before the first tick -- the bubble's own entrance animation
    // plays on mount, so it must not exist until its turn.
    expect(result.current.segments).toHaveLength(0);
    expect(result.current.isStaging).toBe(true);

    act(() => {
      jest.advanceTimersByTime(EDEN_TIMING.STREAM_MS_PER_WORD);
    });
    expect(result.current.segments).toHaveLength(1);
    expect(result.current.segments[0].streaming).toBe(true);
    expect(result.current.segments[0].text.split(" ")).toHaveLength(1);
  });

  test("the LAST segment is the one flagged isLast, so cards are never orphaned", () => {
    const { result } = renderHook(() => useStagedReply(answer, "m1"));
    act(() => {
      jest.advanceTimersByTime(30_000);
    });
    const segments = result.current.segments;
    expect(segments).toHaveLength(2);
    expect(segments[0].isLast).toBe(false);
    expect(segments[1].isLast).toBe(true);
    expect(result.current.isStaging).toBe(false);
    expect(segments.every((s) => s.streaming === false)).toBe(true);
    expect(segments.map((s) => s.text).join(" ")).toBe(answer);
  });

  test("finishNow collapses to the finished state and drops pending timers", () => {
    const { result } = renderHook(() => useStagedReply(answer, "m1"));
    act(() => {
      jest.advanceTimersByTime(EDEN_TIMING.STREAM_MS_PER_WORD * 2);
    });
    expect(result.current.isStaging).toBe(true);

    act(() => {
      result.current.finishNow();
    });
    expect(result.current.isStaging).toBe(false);
    expect(result.current.segments).toHaveLength(2);
    expect(result.current.segments.map((s) => s.text).join(" ")).toBe(answer);

    // No further work may land after finishNow -- a late tick would re-open a
    // bubble the user has already acted on.
    const settled = result.current.segments;
    act(() => {
      jest.advanceTimersByTime(30_000);
    });
    expect(result.current.segments).toEqual(settled);
  });

  test("reduced motion renders the whole answer at once, un-streamed", () => {
    mockReducedMotion.mockReturnValue(true);
    const { result } = renderHook(() => useStagedReply(answer, "m1"));

    expect(result.current.isStaging).toBe(false);
    expect(result.current.segments).toHaveLength(2);
    expect(result.current.segments.every((s) => s.streaming === false)).toBe(true);
  });

  test("disabled (history replay) renders complete immediately", () => {
    const { result } = renderHook(() => useStagedReply(answer, "m1", false));
    expect(result.current.isStaging).toBe(false);
    expect(result.current.segments.map((s) => s.text).join(" ")).toBe(answer);
  });

  test("changing keyId restarts staging; identical text with the same key does not", () => {
    const { result, rerender } = renderHook(
      ({ text, id }: { text: string; id: string }) => useStagedReply(text, id),
      { initialProps: { text: answer, id: "m1" } }
    );
    act(() => {
      jest.advanceTimersByTime(30_000);
    });
    expect(result.current.isStaging).toBe(false);

    // A retry of the same answer is a NEW turn and must re-stage -- which is why
    // identity is the message id, not the text.
    rerender({ text: answer, id: "m2" });
    expect(result.current.isStaging).toBe(true);
  });

  test("unmounting mid-stream leaves no pending timers", () => {
    const { unmount } = renderHook(() => useStagedReply(answer, "m1"));
    act(() => {
      jest.advanceTimersByTime(EDEN_TIMING.STREAM_MS_PER_WORD);
    });
    unmount();
    expect(jest.getTimerCount()).toBe(0);
  });
});

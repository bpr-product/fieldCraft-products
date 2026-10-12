/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Splits one EDEN answer into up to three staged bubbles and streams
*                each of them word by word, so a single backend response reads as a
*                short exchange rather than one wall of text.
*
*                IMPORTANT -- this is presentation only. The complete
*                NavigationResponse is already in hand before the first timer fires;
*                nothing may be gated on the animation. finishNow() exists precisely
*                so any real interaction (option click, new message, reset, close,
*                navigation) can collapse the render to its final state and proceed
*                without waiting.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import { EDEN_TIMING } from "./edenAurora.tokens";

/**
 * A segment shorter than this is merged into the following one. Without the floor,
 * "Yes." or "$48,200." becomes its own bubble, which reads as a stutter rather than
 * a beat.
 */
const MIN_SEGMENT_CHARS = 24;

/** Hard cap on bubbles per answer. Three beats is a rhythm; five is a monologue. */
const MAX_SEGMENTS = 3;

export interface StagedSegment {
  /** Stable within one answer -- safe as a React key. */
  id: string;
  /** Text rendered so far. Equals `full` once this segment has finished streaming. */
  text: string;
  /** The whole segment, regardless of stream progress. */
  full: string;
  /** True while words are still arriving -- drives the caret and aria-busy. */
  streaming: boolean;
  /**
   * True for the last segment only. Chips, option cards and the boarding pass MUST
   * render against this one, so interactive content is never orphaned onto an
   * earlier bubble that the user reads as already answered.
   */
  isLast: boolean;
}

export interface UseStagedReplyResult {
  segments: StagedSegment[];
  /** True while any segment is streaming or a stage gap is pending. */
  isStaging: boolean;
  /** Collapse to the finished state immediately and drop every pending timer. */
  finishNow: () => void;
}

/**
 * Split an answer on sentence boundaries, honouring MIN_SEGMENT_CHARS and
 * MAX_SEGMENTS. Exported for direct unit testing -- the hook itself needs fake
 * timers, the splitter does not.
 */
export function splitIntoSegments(text: string): string[] {
  const trimmed = (text ?? "").trim();
  if (!trimmed) return [];

  // Keep the terminator with its sentence. Deliberately simple: EDEN's answers are
  // short product prose, not documents, and a heavier NLP split would mis-handle
  // the money/ID strings ("INV-2214", "$48,200") that appear in almost every answer.
  //
  // Real bug, fixed: this regex has no concept of a decimal point -- it treats
  // ANY "." as a sentence terminator. A cost answer with cents ("$616.89.
  // Would you like...") got split at the decimal point itself, producing a
  // stray "89." fragment as its own micro-segment (too short to stand alone,
  // so it absorbed the NEXT real sentence instead) -- confirmed live: "The
  // project cost for 506612 is $616." / "89. Would you like to see the...".
  // Comma-formatted whole-dollar amounts ("$48,200") were never at risk (this
  // regex doesn't split on commas), only the cents case. Fix: protect every
  // digit-period-digit run as a single placeholder character before
  // splitting, then restore it -- so a decimal point is never mistaken for
  // end-of-sentence, while a real sentence-ending period (always followed by
  // whitespace/EOF, never by a digit on both sides) still splits normally.
const DECIMAL_PLACEHOLDER = "";
  const protectedText = trimmed.replace(/(\d)\.(\d)/g, `$1${DECIMAL_PLACEHOLDER}$2`);
  const sentences = protectedText.match(/[^.!?]+[.!?]*\s*/g)
    ?.map((s) => s.trim().replace(new RegExp(DECIMAL_PLACEHOLDER, "g"), "."))
    .filter(Boolean) ?? [trimmed];

  const merged: string[] = [];
  for (const sentence of sentences) {
    const previous = merged[merged.length - 1];
    if (previous !== undefined && previous.length < MIN_SEGMENT_CHARS) {
      // The PREVIOUS segment is too short to stand alone -- absorb this one into it.
      merged[merged.length - 1] = `${previous} ${sentence}`;
      continue;
    }
    merged.push(sentence);
  }

  // A trailing short fragment has no successor to merge into, so it merges backwards.
  if (merged.length > 1) {
    const last = merged[merged.length - 1];
    if (last.length < MIN_SEGMENT_CHARS) {
      merged[merged.length - 2] = `${merged[merged.length - 2]} ${last}`;
      merged.pop();
    }
  }

  if (merged.length <= MAX_SEGMENTS) return merged;

  // Everything past the cap joins the final bubble rather than being dropped.
  const head = merged.slice(0, MAX_SEGMENTS - 1);
  const tail = merged.slice(MAX_SEGMENTS - 1).join(" ");
  return [...head, tail];
}

/**
 * @param text     The answer to stage. Pass "" to render nothing.
 * @param keyId    Identity of the answer -- changing it restarts staging. Use the
 *                 message id, never the text itself (a retry of the same answer must
 *                 re-stage, and two different answers can share text).
 * @param enabled  False renders the answer complete and un-staged (history replay).
 */
export function useStagedReply(
  text: string,
  keyId: string,
  enabled: boolean = true
): UseStagedReplyResult {
  const prefersReducedMotion = useReducedMotion();
  const full = useMemo(() => splitIntoSegments(text), [text]);

  // Reduced motion, disabled, or a single short segment: there is nothing worth
  // animating, and a screen-reader user gains nothing from a per-word reveal.
  const skipAnimation = !enabled || prefersReducedMotion === true;

  const finished = useMemo<StagedSegment[]>(
    () =>
      full.map((segment, index) => ({
        id: `${keyId}-${index}`,
        text: segment,
        full: segment,
        streaming: false,
        isLast: index === full.length - 1,
      })),
    [full, keyId]
  );

  const [segments, setSegments] = useState<StagedSegment[]>(skipAnimation ? finished : []);
  const [isStaging, setIsStaging] = useState<boolean>(!skipAnimation && full.length > 0);

  // Every timer this hook creates, so a remount, a finishNow() or a new answer can
  // clear the lot. An orphaned stream timer would keep calling setState against an
  // unmounted panel and, worse, leave isStaging true forever -- which is what makes
  // a stuck "still typing" state impossible to recover from without a reload.
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const clearTimers = useCallback(() => {
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];
  }, []);

  const finishNow = useCallback(() => {
    clearTimers();
    setSegments(finished);
    setIsStaging(false);
  }, [clearTimers, finished]);

  useEffect(() => {
    clearTimers();

    if (skipAnimation || full.length === 0) {
      setSegments(finished);
      setIsStaging(false);
      return clearTimers;
    }

    setSegments([]);
    setIsStaging(true);

    let elapsed = 0;
    const at = (delay: number, fn: () => void) => {
      timersRef.current.push(setTimeout(fn, delay));
    };

    full.forEach((segment, index) => {
      const isLast = index === full.length - 1;
      const id = `${keyId}-${index}`;
      const words = segment.split(" ");

      // Open the bubble empty, then fill it. The bubble's own entrance animation
      // plays on mount, so it must exist before the first word lands.
      at(elapsed, () => {
        setSegments((prev) => [
          ...prev,
          { id, text: "", full: segment, streaming: true, isLast },
        ]);
      });

      words.forEach((_, wordIndex) => {
        const delay = elapsed + (wordIndex + 1) * EDEN_TIMING.STREAM_MS_PER_WORD;
        const partial = words.slice(0, wordIndex + 1).join(" ");
        const done = wordIndex === words.length - 1;
        at(delay, () => {
          setSegments((prev) =>
            prev.map((s) => (s.id === id ? { ...s, text: partial, streaming: !done } : s))
          );
          if (done && isLast) setIsStaging(false);
        });
      });

      elapsed += words.length * EDEN_TIMING.STREAM_MS_PER_WORD;
      if (!isLast) elapsed += EDEN_TIMING.STAGE_GAP_MS;
    });

    return clearTimers;
    // `finished` is derived from full/keyId, so it moves with them.
  }, [full, keyId, skipAnimation, finished, clearTimers]);

  return { segments, isStaging, finishNow };
}

export default useStagedReply;

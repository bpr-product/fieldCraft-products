/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 16-June-2026
* @author      : Kanimozhi.S
* @since       : 0.0.1
* Requirement# :
* Purpose      : Create a parser component for project hub to convert raw composer text into structured segments and vice versa.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |      AUTHOR     |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 26674          | 03-09-2026 | Surya J         | Added countRefOccurrences/toRawRefBody,
*                                                  the #ref counterpart of
*                                                  countMentionOccurrences/toRawMentionBody —
*                                                  lets Composer.tsx reconstruct the raw
*                                                  #[Label](context:id) wire format at send
*                                                  time instead of typing it directly into the
*                                                  visible textarea.
*/

import type { Segment, RawText, EntityKind } from './types';

const MENTION_RE  = /@\[([^\]]+)\]\(user:([^)]+)\)/g;
// AB#26674 (reopened): a #ref now mirrors the @mention wire format --
// #[Roof Replacement Project](context:e3), bracketed label + id, symmetric with
// @[Name](user:uid). The first fix (bracket-only, no id) solved the rendering symptom -- a
// multi-word label with no delimiter only matched up to its first space against the old bare
// charset ([A-Za-z0-9_-.], no space), so the rest fell through as unhighlighted text -- but it
// never carried the entity id, so a stored ref couldn't be resolved back to a specific record
// (ambiguous with duplicate labels, breaks if the entity is later renamed). New refs always
// emit the bracketed+id form (see refToken below). The legacy bare form (#PO-3221, no delimiter,
// no id) is kept ONLY so refs already sent/stored before either fix existed keep parsing.
const REF_RE      = /#\[([^\]]+)\]\(context:[^)]+\)|#([A-Za-z0-9_\-.]+)/g;
const TOKEN_RE    = /@\[([^\]]+)\]\(user:[^)]+\)|#\[([^\]]+)\]\(context:[^)]+\)|#([A-Za-z0-9_\-.]+)/g;

/**
 * Build the raw #ref token for `label`/`id` -- bracketed with id (#[Roof Replacement
 * Project](context:e3)) when an id is available, mirroring @[Name](user:uid); bare (#PO-3221,
 * no id) only as a fallback when no id is known (e.g. reconstructing a legacy segment that
 * predates id tracking). Used both by the composer (inserting a selected context) and
 * bodyToRaw (round-tripping a stored ref back into editable raw text), so both paths stay in
 * sync with what REF_RE/TOKEN_RE accept.
 */
export function refToken(label: string, id?: string): string {
  return id ? `#[${label}](context:${id})` : `#${label}`;
}

// ─── parseComposerText ───────────────────────────────────────────────────────

/**
 * Convert raw composer text → Segment[].
 * Processes @[Name](user:id) and #LABEL tokens in a single pass.
 */
export function parseComposerText(raw: RawText): Segment[] {
  if (!raw) return [{ t: 'text', v: '' }];
  const out: Segment[] = [];
  let last = 0;
  for (const m of Array.from(raw.matchAll(TOKEN_RE))) {
    const idx = m.index as number;
    if (idx > last) out.push({ t: 'text', v: raw.slice(last, idx) });
    if (m[1] !== undefined) {
      // @mention
      const uid = /user:([^)]+)/.exec(m[0])![1];
      out.push({ t: 'mention', v: m[1], uid });
    } else if (m[2] !== undefined) {
      // #ref — bracketed form, carries an id (mirrors @mention's uid)
      const id = /\(context:([^)]+)\)/.exec(m[0])![1];
      out.push({ t: 'ref', v: m[2], refType: 'AUTO', id });
    } else {
      // #ref — legacy bare form, no id
      out.push({ t: 'ref', v: m[3] as string, refType: 'AUTO' });
    }
    last = idx + m[0].length;
  }
  if (last < raw.length) out.push({ t: 'text', v: raw.slice(last) });
  return out;
}

// ─── bodyToRaw ───────────────────────────────────────────────────────────────

/**
 * Convert Segment[] → raw composer text. Inverse of parseComposerText.
 * Used when opening edit mode on an existing message.
 */
export function bodyToRaw(body: Segment[]): RawText {
  return (body ?? []).map(s => {
    if (s.t === 'text')    return s.v;
    if (s.t === 'mention') return `@[${s.v}](user:${(s as any).uid ?? s.v})`;
    if (s.t === 'ref')     return refToken(s.v, s.id);
    return '';
  }).join('');
}

// ─── segmentsToPreviewText ───────────────────────────────────────────────────

/**
 * Convert Segment[] → a clean, human-readable preview string: `@Name` /
 * `#Label` instead of bodyToRaw's editable `@[Name](user:uid)` markup.
 *
 * Bug: reply-quote previews (Message['replyTo'].preview, rendered as plain
 * text by ReplyPreview and the Composer's reply strip — neither goes through
 * MessageBody's Segment-aware rendering) used to be built with bodyToRaw,
 * which leaked the internal user UUID into the UI whenever the quoted
 * message contained a mention. Use this instead of bodyToRaw anywhere the
 * result is DISPLAYED rather than fed back into the composer for editing.
 */
export function segmentsToPreviewText(body: Segment[]): string {
  return (body ?? []).map(s => {
    if (s.t === 'text')    return s.v;
    if (s.t === 'mention') return `@${s.v}`;
    if (s.t === 'ref')     return `#${s.v}`;
    return '';
  }).join('');
}

// ─── rawPreviewToDisplayText ─────────────────────────────────────────────────

/**
 * Convert a RAW preview string (still carrying `@[Name](user:uid)`/`#Ref`
 * markup) → clean display text. Just parseComposerText + segmentsToPreviewText
 * in one step.
 *
 * chat-api's `bodyPreview` fields (message.service.ts: `body.slice(0, 120)`)
 * are a raw slice of the stored composer text, unparsed — a mention in the
 * first 120 characters carries its markup straight through. Two UI surfaces
 * render that string as plain text without ever running it through
 * MessageBody's Segment-aware rendering: the reply-to preview on a message
 * loaded from history/WS (workspaceMappers.ts's toMessage) and the
 * "new message" notification toast/bell body (useChatWsEvents.ts). Both
 * leaked the mentioned user's internal UUID into the UI. Frontend-only fix —
 * no chat-api/schema change, per this fix's "don't touch the API contract
 * unless necessary" constraint; bodyPreview keeps its existing raw format on
 * the wire, and gets cleaned up right before display instead.
 */
export function rawPreviewToDisplayText(raw: RawText): string {
  return segmentsToPreviewText(parseComposerText(raw));
}

// ─── toRawMentionBody ────────────────────────────────────────────────────────

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Case-sensitive, word-boundary-safe count of "@name" occurrences in text. */
export function countMentionOccurrences(text: string, name: string): number {
  const re = new RegExp(`@${escapeRegExp(name)}(?![A-Za-z0-9_])`, 'g');
  return (text.match(re) ?? []).length;
}

/** Case-sensitive, word-boundary-safe count of "#label" occurrences in text — the #ref
 *  counterpart of countMentionOccurrences, used the same way to prune refsRef on edit. */
export function countRefOccurrences(text: string, label: string): number {
  const re = new RegExp(`#${escapeRegExp(label)}(?![A-Za-z0-9_])`, 'g');
  return (text.match(re) ?? []).length;
}

/**
 * Converts clean display text (`@Name` — no id/brackets, what the composer
 * and the edit-message box actually show while typing) into the raw
 * `@[Name](user:uid)` markup chat-api's wire format expects, using `mentions`
 * (uid+name pairs, consumed FIFO per name in left-to-right order). A `@Name`
 * occurrence beyond what's tracked (typed manually rather than inserted via
 * autocomplete, or left over after editing) is left as plain text — a safe
 * fallback that never attaches a wrong or stale uid to the wire.
 *
 * Fixes the raw `@[Name](user:uid)` markup leaking into the live composer/
 * edit textarea while the user is typing — those internal uids must never
 * be user-visible; only the human name should show.
 */
export function toRawMentionBody(
  display: string,
  mentions: { uid: string; name: string }[],
): string {
  if (mentions.length === 0) return display;
  const queues = new Map<string, string[]>();
  for (const m of mentions) queues.set(m.name, [...(queues.get(m.name) ?? []), m.uid]);
  // Longest name first so e.g. "Greg Smith" isn't shadowed by a shorter "Greg" match.
  const names = Array.from(queues.keys()).sort((a, b) => b.length - a.length).map(escapeRegExp);
  const re = new RegExp(`@(${names.join('|')})(?![A-Za-z0-9_])`, 'g');
  return display.replace(re, (full, name: string) => {
    const uid = queues.get(name)?.shift();
    return uid ? `@[${name}](user:${uid})` : full;
  });
}

/**
 * The #ref counterpart of toRawMentionBody — converts clean display text (`#Label`, what the
 * composer shows while typing) into the raw `#[Label](context:id)` markup, using `refs`
 * (id+label pairs, consumed FIFO per label). A `#Label` occurrence beyond what's tracked
 * (typed manually, or a legacy bare ref with no id) is left as plain text.
 *
 * Fixes the raw `#[Label](context:id)` markup — entity id and all — leaking into the live
 * composer textarea while the user is typing, the same class of bug toRawMentionBody already
 * fixes for @mentions. Unlike @mentions, refToken's insertion call sites used to build this raw
 * form directly and hand it straight to insertToken; this restores the clean-display/raw-at-send
 * split for refs too.
 */
export function toRawRefBody(
  display: string,
  refs: { id: string; label: string }[],
): string {
  if (refs.length === 0) return display;
  const queues = new Map<string, string[]>();
  for (const r of refs) queues.set(r.label, [...(queues.get(r.label) ?? []), r.id]);
  // Longest label first so e.g. "Roof Replacement" isn't shadowed by a shorter "Roof" match.
  const labels = Array.from(queues.keys()).sort((a, b) => b.length - a.length).map(escapeRegExp);
  const re = new RegExp(`#(${labels.join('|')})(?![A-Za-z0-9_])`, 'g');
  return display.replace(re, (full, label: string) => {
    const id = queues.get(label)?.shift();
    return id ? refToken(label, id) : full;
  });
}

// ─── extractTokens ───────────────────────────────────────────────────────────

/** Pull mention UIDs and ref labels from raw text without full parsing. */
export function extractTokens(raw: RawText): { mentions: string[]; refs: string[] } {
  const mentions = Array.from(raw.matchAll(/@\[([^\]]+)\]\(user:([^)]+)\)/g)).map(m => m[2]);
  const refs     = Array.from(raw.matchAll(REF_RE)).map(m => (m[1] ?? m[2]) as string);
  return { mentions, refs };
}

// ─── Autocomplete helpers ────────────────────────────────────────────────────

/**
 * Scan text backward from `cursorPos` to find an in-progress @mention or #ref.
 * Returns the trigger kind and query string, or null if no autocomplete applies.
 */
export function detectAutocomplete(
  text: string,
  cursorPos: number,
): { kind: 'mention' | 'ref'; query: string; triggerPos: number } | null {
  const before = text.slice(0, cursorPos);
  const mention = before.match(/(^|\s)@([A-Za-z0-9_]*)$/);
  if (mention) {
    return {
      kind: 'mention',
      query: mention[2],
      triggerPos: before.lastIndexOf('@'),
    };
  }
  const ref = before.match(/(^|\s)#([A-Za-z0-9_\-.]*)$/);
  if (ref) {
    return {
      kind: 'ref',
      query: ref[2],
      triggerPos: before.lastIndexOf('#'),
    };
  }
  return null;
}

// ─── SMS character counter ────────────────────────────────────────────────────

const GSM_CHARSET = /^[A-Za-z0-9 @£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ!"#¤%&'()*+,\-./:;<=>?¡ÄÖÑÜ§¿äöñüà^{}\\[~\]|€\t]*$/;

export function smsMeta(text: string) {
  const len   = Array.from(text).length; // codepoint-aware
  const unicode = !GSM_CHARSET.test(text);
  const single  = unicode ? 70  : 160;
  const concat  = unicode ? 67  : 153;
  const segments = len === 0 ? 0 : len <= single ? 1 : Math.ceil(len / concat);
  const limit    = len <= single ? single : concat * segments;
  return { used: len, limit, segments, unicode, overLimit: segments > 10 };
}

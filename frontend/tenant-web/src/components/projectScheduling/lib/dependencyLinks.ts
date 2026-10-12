/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 23-Sep-2026
 * @author      : Mohamed Riyaz A
 * Created By   : Mohamed Riyaz A
 * @since       : 1.5.3.9
 * Requirement# : AB#31742
 * Purpose      : A task's predecessor links as the scheduling engine reads them — the
 *                task, the link type (FS/SS/FF/SF) and lag hours from its dependencies
 *                (293), HARD/SOFT (558) and working/calendar hours (565) — read from the
 *                task API's `dependencyLinks` or from a raw 293 token, and written back
 *                as the engine's own token ("<uid>SS+2h").
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE        |   AUTHOR          |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#31742       |  23/09/2026   |  Mohamed Riyaz A  |  Initial — type, lag, class and
 *                |               |                   |  basis per link, read and write
 * AB#31742       |  24/09/2026   |  Mohamed Riyaz A  |  W28: successorUid on a link read
 *                |               |                   |  from its earlier task's side
 * AB#31742       |  24/09/2026   |  Mohamed Riyaz A  |  Loop refusal text; F21 carry-over
 */

import { Dependency, DependencyClass, DepType, LagBasis } from "../types";

/** One predecessor link as the task API returns it (TaskDTO / TaskViewData.dependencyLinks). */
export interface ApiDependencyLink {
  predecessorUid: string;
  type: string;
  lagHours: number | null;
  dependencyClass: string | null;
  lagBasis: string | null;
  /** W28 — the later task that stores the link; set on a task's `successorLinks` only. */
  successorUid?: string | null;
}

/** One predecessor link as the dependency editor holds it. */
export interface DependencyLinkDraft {
  predecessorUid: string;
  type: DepType;
  lagHours: number;
  dependencyClass: DependencyClass;
  lagBasis: LagBasis;
}

/** A task uid as every uid-keyed link map compares it. */
export const linkKey = (uid: string): string => uid.trim().toLowerCase();

export const DEP_TYPES: readonly DepType[] = ["FS", "SS", "FF", "SF"];
export const DEP_CLASSES: readonly DependencyClass[] = ["HARD", "SOFT"];
export const LAG_BASES: readonly LagBasis[] = ["working", "calendar"];

/** What the engine reads for a link that says nothing more: FS, no lag, HARD, working hours. */
export const DEFAULT_LINK: Omit<DependencyLinkDraft, "predecessorUid"> = {
  type: "FS",
  lagHours: 0,
  dependencyClass: "HARD",
  lagBasis: "working",
};

const UUID_TOKEN =
  /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[+\s]*(FS|SS|FF|SF)?(?:([+-]\d+)\s*h)?$/i;
const OTHER_TOKEN = /^(\S+?)[+\s]*(FS|SS|FF|SF)(?:([+-]\d+)\s*h)?$/;

const toType = (value: unknown): DepType => {
  const upper = String(value ?? "").toUpperCase() as DepType;
  return DEP_TYPES.includes(upper) ? upper : DEFAULT_LINK.type;
};
const toClass = (value: unknown): DependencyClass =>
  String(value ?? "").toUpperCase() === "SOFT" ? "SOFT" : "HARD";
const toBasis = (value: unknown): LagBasis =>
  String(value ?? "").toLowerCase() === "calendar" ? "calendar" : "working";

/**
 * One stored 293 token — "<uid>", "<uid>SS+2h", "<uid>FS-1h" — to its task, type and lag.
 * A uuid is matched by its exact shape first, so a uid ending in "ff" is never read as FF.
 */
export function parseDependencyToken(token: string): Pick<DependencyLinkDraft, "predecessorUid" | "type" | "lagHours"> {
  const text = String(token).trim();
  const match = text.match(UUID_TOKEN) ?? text.match(OTHER_TOKEN);
  if (!match) return { predecessorUid: text, type: DEFAULT_LINK.type, lagHours: 0 };
  return { predecessorUid: match[1], type: toType(match[2] ?? DEFAULT_LINK.type), lagHours: match[3] ? Number(match[3]) : 0 };
}

/** The engine's canonical token: the type glued to the uid, the lag only when it is not zero. */
export function formatDependencyToken(link: Pick<DependencyLinkDraft, "predecessorUid" | "type" | "lagHours">): string {
  const lag = link.lagHours ? `${link.lagHours > 0 ? "+" : ""}${link.lagHours}h` : "";
  return `${link.predecessorUid}${link.type}${lag}`;
}

/** The API's links as editor drafts, every field normalised to a value the engine accepts. */
export function linkDraftsFromApi(links: ApiDependencyLink[] | null | undefined): DependencyLinkDraft[] {
  return (links ?? [])
    .filter((l) => Boolean(l?.predecessorUid))
    .map((l) => ({
      predecessorUid: l.predecessorUid,
      type: toType(l.type),
      lagHours: Number.isFinite(Number(l.lagHours)) ? Number(l.lagHours) : 0,
      dependencyClass: toClass(l.dependencyClass),
      lagBasis: toBasis(l.lagBasis),
    }));
}

/** The API's links as the Gantt's Dependency shape (lag in minutes). */
export function dependenciesFromLinks(links: ApiDependencyLink[] | null | undefined): Dependency[] {
  return linkDraftsFromApi(links).map((l) => ({
    predecessorUid: l.predecessorUid,
    type: l.type,
    lagMin: l.lagHours * 60,
    dependencyClass: l.dependencyClass,
    lagBasis: l.lagBasis,
  }));
}

/**
 * A task's links for the edit dialog: the API's `dependencyLinks` when it sent them, else the
 * bare ";"-joined uid list (read as FS, no lag) that older responses carry.
 */
export function dialogDependencies(links: ApiDependencyLink[] | null | undefined, uidList: unknown): Dependency[] {
  if (Array.isArray(links)) return dependenciesFromLinks(links);
  const uids = uidList ? String(uidList).split(";").map((s) => s.trim()).filter(Boolean) : [];
  return uids.map((uid) => ({ predecessorUid: uid, type: DEFAULT_LINK.type, lagMin: 0 }));
}

type LinkSettingsOnly = Omit<DependencyLinkDraft, "predecessorUid">;

/**
 * F21 — a picker change that swaps exactly one saved link for exactly one new task is the
 * same link with its earlier task changed: the new task takes the old link's settings (the
 * engine's link writer carries them the same way). Keyed by the new task.
 */
export function carriedLinkSettings(loaded: Map<string, LinkSettingsOnly>, selected: string[]): Map<string, LinkSettingsOnly> {
  const picked = new Set(selected.map(linkKey));
  const removed = Array.from(loaded.keys()).filter((key) => !picked.has(key));
  const added = selected.filter((uid) => !loaded.has(linkKey(uid)));
  const old = removed.length === 1 && added.length === 1 ? loaded.get(removed[0]) : undefined;
  return old ? new Map([[linkKey(added[0]), old]]) : new Map();
}

/** The error codes of a save the engine refused because of its links (HTTP 422). */
export const LINK_REFUSAL_CODES: readonly string[] = ["DEPENDENCY_LOOP", "INVALID_DEPENDENCY_LINK"];

/** The refusal's own words when a save answer is a link refusal (a loop, a bad link), else null. */
export function linkRefusalText(result: unknown): string | null {
  const error = (result as { data?: { error?: { code?: string; message?: string; detail?: { errors?: string[] } } } } | null)
    ?.data?.error;
  if (!error?.code || !LINK_REFUSAL_CODES.includes(error.code)) return null;
  return error.detail?.errors?.[0] ?? error.message ?? null;
}

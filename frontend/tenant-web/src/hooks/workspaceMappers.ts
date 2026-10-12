/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 2026-06-26
* @author      : Shabnam Banu H
* Purpose      : Shared mappers — chat-api Workspace → hub Thread types.
*                Used by NotesFetch.tsx (initial fetch) and useChatWebSocket.ts
*                (real-time note.created / channel.created events).
*/
import type { Thread, Entity, ThreadKind, Message, AnyMessage, Person } from '../components/projectHub/hub/types';
import { parseComposerText, rawPreviewToDisplayText } from '../components/projectHub/hub/parser';

export interface ApiWorkspace {
  workspaceUid: string;
  type: string;
  name?: string;
  projectUid?: string;
  isDefault: boolean;
  isPrivate?: boolean;
  topic?: string;
  priority?: string;
  archived?: boolean;
  updatedAt?: string;
  // Note-specific fields
  // PR 7939 review (blocking): autoFilled previously round-tripped only via
  // an in-session client-side re-stamp heuristic (useWorkspaceFetch.ts) —
  // a hard reload, a second tab/device, or another user opening the same
  // note had no way to know the context was GoPAL-locked, so it silently
  // became editable again. chat-api's own NoteContext type already declares
  // this field and persists whatever it's sent (workspace.repository.ts's
  // updateNoteFields spreads `updates.context` verbatim onto the Mongo doc)
  // — the gap was entirely client-side (never sent, never read back).
  context?: { type?: string; entityId?: string; entityLabel?: string; autoFilled?: boolean };
  status?: string;
  noteOrderNo?: number;
  promotedFrom?: { workspaceUid: string; messageUid: string };
  /** Whether the requesting user is a member (notes-board reads only) */
  isMember?: boolean;
  /** True when this note's parent project was soft-deleted in Postgres —
   * mirrored onto the Workspace doc by chat-api's POST /projects/:uid/deleted. */
  projectDeleted?: boolean;
}

// ─── API shapes ───────────────────────────────────────────────────────────────

export interface ApiMessage {
  messageUid: string;
  workspaceUid: string;
  senderUserId: string;
  senderDisplayName?: string;
  body: string;
  type: string;
  isInitialPost?: boolean;
  edited?: boolean;
  editedAt?: string;
  deleted?: boolean;
  sentAt: string;
  attachmentUids?: string[];
  reactions?: Record<string, string[]>;
  replyTo?: { messageUid: string; senderDisplayName: string; bodyPreview: string; deleted?: boolean };
  /** Echoed back by chat-api's message.service.ts (the saved Mongo doc includes
   *  whatever the sender's POST /messages request carried) — lets the sender's
   *  own WS message.created event be matched to its optimistic local message
   *  even before the HTTP response has stamped the real messageUid onto it. */
  clientMessageUid?: string;
}

export interface ApiMember {
  userId: string;
  role: string;
  displayName?: string;
  email?: string;
  avatarUrl?: string;
}

/**
 * A row from the existing Project Members API — getAllProjectMembersById in
 * ProjectMembersService.ts (backed by ResourceScheduleRepository
 * .findAllProjectMembersByProjectId). `name` arrives pre-concatenated as
 * "First Last-RoleName". Reused for the Notes/Channels/DMs "Add member"
 * picker instead of a separate chat-api member endpoint.
 */
export interface ProjectMemberRow {
  id?: number;
  resourceUid?: string;
  resourceId?: number;
  name?: string;
  designation?: string;
  status?: string;
  email?: string;
  performance?: string;
  cost?: number;
}

// ─── Private helpers ──────────────────────────────────────────────────────────

export function nameToInitials(name: string): string {
  return name.split(' ').filter(Boolean).map(w => w[0]).join('').toUpperCase().slice(0, 2) || '?';
}

// Bug fix: 4 of these 6 seeds (av-sam, av-lee, av-pat, av-alex) were never
// defined in hub-tailwind.css — only av-jen/av-mike existed. Any avatar
// hashing into one of the other 4 buckets rendered with no background at
// all (transparent circle, invisible white-on-white initials), which is
// exactly the "missing avatar" rows seen across ~2/3 of any member list
// app-wide (New Direct Message picker, Add Member dialog, thread member
// chips, etc.) — not a data issue. Replaced with the CSS classes that
// actually exist and are meant for generic people (avoids av-ext/av-cust/
// av-email/av-sms/av-me, which carry other specific meanings elsewhere).
const AVATAR_SEEDS = ['av-jen', 'av-mike', 'av-tom', 'av-sof', 'av-sar', 'av-rob'];
export function avatarClass(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++) h = (h * 31 + userId.charCodeAt(i)) >>> 0;
  return AVATAR_SEEDS[h % AVATAR_SEEDS.length];
}

// ─── Message mapper ───────────────────────────────────────────────────────────

/**
 * Maps a chat-api message to the hub Message type.
 * Pass myUserId so the sender's own messages get author='me' (edit-button
 * visibility and bubble alignment in ThreadView depend on this).
 */
export function toMessage(m: ApiMessage, myUserId: string | null): AnyMessage {
  // Backend SYSTEM messages (e.g. "⚠ Attachment blocked by virus scan")
  // render as the hub's inline system line, not as a user bubble — there is
  // no 'system' entry in the people directory to attribute a bubble to.
  if (m.type === 'SYSTEM') {
    return {
      kind: 'system',
      text: m.body,
      time: new Date(m.sentAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };
  }
  const authorId = myUserId && m.senderUserId === myUserId ? 'me' : m.senderUserId;
  // Rewrite the current user's own id to 'me' inside each reaction's user list
  // so ReactionBar can highlight it and optimistic toggles stay consistent with
  // the WS echo (which also maps the sender's id → 'me'). Without this, reactions
  // loaded from history never render as "mine" and re-toggling corrupts the count.
  const reactions = m.reactions && myUserId
    ? Object.fromEntries(
        Object.entries(m.reactions).map(([emoji, users]) => [
          emoji,
          users.map((u) => (u === myUserId ? 'me' : u)),
        ]),
      )
    : m.reactions;
  return {
    kind: m.type === 'NOTE_POST' ? 'post' : 'msg',
    uid: m.messageUid,
    author: authorId,
    // Authoritative fallback name for when `author` (a raw id) misses the
    // local directory/membersById lookup — see Message.authorDisplayName's
    // doc comment. Not rewritten to anything for 'me' — ThreadView/Message
    // already special-case author === 'me' before ever consulting this.
    authorDisplayName: m.senderDisplayName,
    time: new Date(m.sentAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    sentAtIso: m.sentAt,
    // Bug: this wrapped the raw composer text as a single 'text' segment
    // verbatim, so a sent mention's `@[Name](user:uid)` markup rendered
    // literally in the chat window instead of the clean MentionPill chip
    // (Message.tsx already renders `t: 'mention'` segments correctly — this
    // mapper just never produced any). parseComposerText was already
    // imported here and used nowhere; it's the same parser the composer
    // itself uses to turn that markup into Segment[] before sending, so
    // history/live messages now render identically to what was typed.
    body: parseComposerText(m.body),
    isInitialPost: m.isInitialPost,
    edited: m.edited,
    editedAt: m.editedAt,
    attachmentUids: m.attachmentUids,
    clientMessageUid: m.clientMessageUid,
    reactions,
    replyTo: m.replyTo && {
      messageUid: m.replyTo.messageUid,
      author: m.replyTo.senderDisplayName,
      // rawPreviewToDisplayText, not the raw bodyPreview as-is — chat-api's
      // bodyPreview is an unparsed slice of the stored composer text
      // (message.service.ts: body.slice(0, 120)), so a mention in the first
      // 120 chars carries its `@[Name](user:uid)` markup straight through.
      // This preview renders as plain text (ReplyPreview), not through
      // MessageBody's Segment-aware rendering, so it used to leak the
      // mentioned user's internal UUID for any message loaded from history
      // or a live WS event that replies to a mention-containing message.
      preview: rawPreviewToDisplayText(m.replyTo.bodyPreview),
      deleted: m.replyTo.deleted,
    },
  };
}

// ─── Member mapper ────────────────────────────────────────────────────────────

/**
 * Maps a chat-api workspace member to the hub Person type.
 * Added to people[] so ProjectHub's directory lookup resolves author names.
 */
export function toPerson(m: ApiMember): Person {
  // Issue 25959: when displayName is missing, the previous fallback chain
  // (email local-part, then a raw userId fragment) surfaced exactly the kind
  // of internal metadata — email addresses, GUID fragments — the mention/DM/
  // channel-member pickers must not expose. Fall back to a clean, generic
  // label instead; `id` (used for lookups, never rendered as text — see
  // Composer.tsx's mention popover, which only renders name/role/avatar)
  // still carries the real userId, so nothing is lost functionally.
  const displayName = m.displayName ?? 'Unknown user';
  return {
    id: m.userId,
    name: displayName,
    role: m.role,
    initials: nameToInitials(displayName),
    avatar: avatarClass(m.userId),
  };
}

/**
 * True when a Project Members API row is a customer/client, not an internal
 * project member. The backing query (ResourceScheduleRepository
 * .findAllProjectMembersByProjectId) already only joins internal
 * Resource/UserRoleMap/UserAccount rows — customers are never returned — this
 * is a defensive belt-and-suspenders check on top, per the "exclude customer
 * users" requirement for the Notes/Channels/DMs Add Member picker.
 */
export function isCustomerProjectMember(m: ProjectMemberRow): boolean {
  const designation = (m.designation ?? '').toLowerCase();
  return designation.includes('customer') || designation.includes('client');
}

/**
 * A row from the tenant-wide Project Members API — getAllProjectMembers (no
 * projectId) in ProjectMembersService.ts, the same endpoint the Gantt
 * resource pickers already call. Unlike ProjectMemberRow (getAllProjectMembersById,
 * scoped to one project's crew), this list is tenant-wide, which is what New
 * Direct Message's "Add members" picker needs — a DM isn't tied to a project
 * (see Thread's "Not tied to a project" chip), so a project-scoped list would
 * often come back empty for it. Unlike ProjectMemberRow, `name` here arrives
 * as a plain display name (not "First Last-RoleName") — see AddMemberDialog.tsx,
 * which already consumes this same endpoint and does not split on '-'.
 */
export interface TenantMemberRow {
  resourceUid?: string;
  name?: string;
  designation?: string;
  email?: string;
  status?: string;
}

/** True for an explicit inactive marker on a tenant-wide member row — mirrors
 * AddMemberDialog.tsx's existing filter (no typed enum exists on this DTO). */
export function isInactiveTenantMember(m: TenantMemberRow): boolean {
  return (m.status ?? '').toUpperCase() === 'INACTIVE';
}

/**
 * Maps a row from the tenant-wide Project Members API (getAllProjectMembers)
 * to the hub Person type, for the New Direct Message "Add members" picker.
 * Reused instead of a separate chat-api member endpoint — same reuse
 * requirement as toPersonFromProjectMember below, just tenant-scoped.
 */
export function toPersonFromTenantMember(m: TenantMemberRow): Person {
  // Issue 25959: same fix as toPerson() above — don't fall back to an email
  // local-part or a raw resourceUid as the visible name.
  const displayName = (m.name ?? '').trim() || 'Unknown user';
  const id = m.resourceUid ?? displayName;
  return {
    id,
    name: displayName,
    role: m.designation ?? '',
    initials: nameToInitials(displayName),
    avatar: avatarClass(id),
  };
}

const escapeRegExp = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * Some `Resource.name` rows still carry their role baked into the name
 * string instead of keeping it in the separate `designation` column — a
 * leftover from a since-fixed backend query that used to CONCAT them
 * (dash-separated: "First Last-RoleName"; see the dash-split fallback
 * below/docs/project-member-role-fix.md). At least one observed row has the
 * role appended with no dash at all (e.g. "Greg   purchaseofficer"), which
 * that dash-split never catches. Strips a trailing occurrence of the KNOWN
 * `designation` value — matched literally, case-insensitively, with or
 * without internal whitespace, preceded by any run of dashes/spaces — so it
 * can only ever remove the actual role text, never accidentally eat part of
 * a real hyphenated name. Returns null when no match is found (including
 * when name/role are already clean and separate), so the caller can fall
 * back to the pre-existing dash-split/no-op behavior.
 */
export function stripKnownRoleSuffix(name: string, role: string | undefined): string | null {
  const trimmedRole = (role ?? '').trim();
  if (!name || !trimmedRole) return null;
  const variants = Array.from(new Set([trimmedRole, trimmedRole.replace(/\s+/g, '')]));
  for (const variant of variants) {
    if (!variant) continue;
    const re = new RegExp(`[-\\s]+${escapeRegExp(variant)}\\s*$`, 'i');
    if (re.test(name)) {
      const stripped = name.replace(re, '').trim();
      if (stripped) return stripped;
    }
  }
  return null;
}

/**
 * The clean, role-suffix-stripped display name for a Project Members API row —
 * shared by toPersonFromProjectMember (below) and dedupeProjectMemberRows,
 * so both agree on exactly what "the person's name" is.
 */
function resolveProjectMemberDisplayName(m: ProjectMemberRow): string {
  const rawName = m.name ?? '';
  // Prefer stripping the exact known `designation` value first — it's the
  // more reliable signal (backed by the actual role, not just "there's a
  // dash somewhere") and catches the no-dash "Greg   purchaseofficer" shape
  // the old dash-only split misses entirely. Only fall back to the blind
  // last-dash split when that doesn't apply, so already-clean rows and rows
  // with a legitimately hyphenated name are unaffected.
  const roleStripped = stripKnownRoleSuffix(rawName, m.designation);
  const dashIdx = rawName.lastIndexOf('-');
  const dashSplit = dashIdx > 0 ? rawName.slice(0, dashIdx).trim() : '';
  // Issue 25959: previously fell back to an email local-part, a raw
  // resourceUid, or a literal "resource-<internal id>" string as the
  // *visible name* — all backend metadata that must not be shown in place of
  // a clean identity. Fall back to a generic label instead; `id` below still
  // carries the real identifier for lookups.
  return roleStripped || dashSplit || rawName.trim() || 'Unknown user';
}

/**
 * Maps a row from the existing Project Members API (getAllProjectMembersById)
 * to the hub Person type, for the Add Member picker on Notes/Channels/DMs.
 * Reused instead of a separate chat-api member endpoint.
 */
export function toPersonFromProjectMember(m: ProjectMemberRow): Person {
  const displayName = resolveProjectMemberDisplayName(m);
  const id = m.resourceUid ?? String(m.id ?? m.resourceId ?? displayName);
  return {
    id,
    name: displayName,
    role: m.designation ?? '',
    initials: nameToInitials(displayName),
    avatar: avatarClass(id),
  };
}

/**
 * Collapses Project Members API rows that are clearly the same real person
 * into one, before they're mapped to Person and shown in any picker (DM
 * picker, channel/note member picker, @mention autocomplete).
 *
 * Root cause (frontend can't fix): getAllProjectMembersById's backing Java
 * query joins a resource's role by resource id only, not by the role they're
 * actually assigned as on this project — so one resource holding N tenant-
 * wide roles comes back as N rows, one per role, even when only assigned
 * under one of them here. Full write-up + the real (schema-migration) fix:
 * docs/project-member-role-fix.md — that fix is a multi-phase, cross-team
 * (Java + ASE 2.0) change, explicitly not yet implemented. This is a
 * frontend-only band-aid so the same human doesn't visibly appear twice in a
 * people list; it does not touch the Resource Ribbon or any other consumer
 * of the same backend endpoint outside chat's Person pickers.
 *
 * Prefers `email` as the person key (stable across a resource's role rows);
 * falls back to the stripped display name when email is missing, since two
 * rows for the literal same name are far more likely to be duplicate role
 * rows for one person than two different people who happen to share a name.
 * Keeps the first occurrence per key — deterministic, no reordering.
 */
export function dedupeProjectMemberRows(rows: ProjectMemberRow[]): ProjectMemberRow[] {
  const seen = new Set<string>();
  const out: ProjectMemberRow[] = [];
  for (const row of rows) {
    const email = row.email?.trim().toLowerCase();
    const key = email || resolveProjectMemberDisplayName(row).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

/**
 * Same root cause and same fix shape as dedupeProjectMemberRows above, for
 * the tenant-wide Project Members API (getAllProjectMembers — no projectId)
 * that backs `tenantPeople`/ChannelCreateDialog's "Add members" picker and
 * New Direct Message's picker: one resource holding N tenant-wide roles
 * comes back as N rows, one per role (docs/project-member-role-fix.md).
 * Reported symptom: "Joel Demet-labour" and "Joel Demet-sales" both appear
 * in ChannelCreateDialog's member list; since toPersonFromTenantMember's
 * `id` is `resourceUid` (identical for both rows — same underlying
 * resource), both checkboxes share the same `checked = memberIds.includes(id)`
 * state, so ticking one silently ticks the other too, and the channel is
 * created with only one deduplicated id in memberIds regardless of which of
 * the two the user meant to add.
 *
 * Unlike ProjectMemberRow, TenantMemberRow's `name` already arrives as a
 * plain display name (see TenantMemberRow's own doc comment) — no role
 * suffix ever needs stripping before comparing, so this dedupes on `email`
 * (preferred) falling back to the plain, trimmed, lowercased `name`, without
 * dedupeProjectMemberRows' resolveProjectMemberDisplayName step.
 */
export function dedupeTenantMemberRows(rows: TenantMemberRow[]): TenantMemberRow[] {
  const seen = new Set<string>();
  const out: TenantMemberRow[] = [];
  for (const row of rows) {
    const email = row.email?.trim().toLowerCase();
    const key = email || (row.name ?? '').trim().toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

// ─── Workspace mappers ────────────────────────────────────────────────────────

export function toThread(w: ApiWorkspace): Thread {
  // DMs/groups aren't guaranteed a backend-assigned name (1:1 DMs never get one) —
  // falling back to the raw workspaceUid would leak an internal id into the UI.
  // Leave it blank so the DM list can resolve a display name from the other
  // member instead; channels always have a name, so keep that fallback for them.
  const isDirect = w.type === 'DM' || w.type === 'GROUP';
  return {
    id: w.workspaceUid,
    kind: w.type as ThreadKind,
    projectId: w.projectUid ?? null,
    title: w.name ?? (isDirect ? '' : w.workspaceUid),
    isDefault: w.isDefault,
    isPrivate: w.isPrivate,
    desc: w.topic,
    priority: (w.priority as Thread['priority']) ?? null,
    members: [],
    messages: [],
    unread: 0,
    lastActivity: w.updatedAt ?? new Date().toISOString(),
  };
}

export function toNoteThread(w: ApiWorkspace): Thread {
  const contextType = (w.context?.type ?? null) as Thread['contextType'];
  const contextEntity: Entity | null = w.context?.entityId
    ? {
        id: w.context.entityId,
        kind: (w.context.type ?? 'TASK') as Entity['kind'],
        label: w.context.entityLabel ?? w.context.entityId,
        sub: '',
        // PR 7939 review (blocking): read the persisted flag directly instead
        // of relying solely on useWorkspaceFetch.ts's same-session re-stamp
        // heuristic — this is now the source of truth; that heuristic stays
        // only as defense-in-depth for the gap between an optimistic local
        // create and its first real fetch.
        ...(w.context.autoFilled ? { autoFilled: true } : {}),
      }
    : null;
  const noteLabel =
    w.name ?? (w.noteOrderNo != null ? `Note #${w.noteOrderNo}` : w.workspaceUid);
  return {
    id: w.workspaceUid,
    kind: 'NOTE',
    projectId: w.projectUid ?? null,
    title: noteLabel,
    contextType,
    contextEntity,
    priority: (w.priority as Thread['priority']) ?? null,
    promotedFrom: w.promotedFrom,
    isMember: w.isMember,
    projectDeleted: w.projectDeleted,
    members: [],
    messages: [],
    unread: 0,
    lastActivity: w.updatedAt ?? new Date().toISOString(),
  };
}

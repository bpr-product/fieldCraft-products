/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 16-June-2026
* @author      : Kanimozhi.S
* @since       : 0.0.1
* Requirement# :
* Purpose      : Create a types  component for project hub to define types for messages, threads, channels, DMs, notifications, and floating chat windows.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |      AUTHOR     |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 26674          | 03-09-2026 | Surya J         | Added optional `id` to RefSegment (mirrors
*                                                  MentionSegment's `uid`) so a #ref can carry
*                                                  the referenced entity's own id through to
*                                                  storage, enabling resolution by id instead
*                                                  of by label text alone.
*/


// ─── Icons ───────────────────────────────────────────────────────────────────

/** Lucide-style kebab-case name — see icon.tsx ICON_MAP for the full set */
export type IconName = string;

// ─── Palette / style tokens ───────────────────────────────────────────────────

/** Tailwind utility class string, e.g. 'bg-urgent-50 text-urgent-700' */
export type TwClasses = string;

/** @deprecated Phase 4 — retained only for TrackColorConfig until Gantt re-use lands */
export type PalettePath = string;

/** Raw hex colour, e.g. '#13507f' */
export type HexColor = string;

/** Chip colour classes used by PRIORITY_STYLE / CONTEXT_STYLE / CHANNEL_KIND_STYLE */
export type ChipStyle = TwClasses;

// ─── People & contacts ────────────────────────────────────────────────────────

/** Internal staff member */
export interface Person {
  id: string;
  name: string;
  role: string;
  /** Avatar gradient seed — e.g. 'av-jen'. Looked up on theme.avatarGradients */
  avatar: string;
  initials: string;
}

/** External contact (customer or vendor rep) — invited by email */
export interface Contact {
  id: string;
  name: string;
  initials: string;
  avatar: string;
  external: true;
  kind: 'customer' | 'vendor';
  company: string;
  email: string;
  role: string;
  /** Scopes this contact to a specific project in the invite UI */
  project: string;
}

/** Union — a resolved directory entry */
export type DirectoryEntry = Person | Contact;

/** A person as they appear inside a Thread's membership list */
export interface Member {
  id: string;
  name: string;
  initials: string;
  avatar: string;
  role: string;
  external?: boolean;
  company?: string;
  via?: 'email';
  contact?: string;       // email address
  contactKind?: 'customer' | 'vendor' | 'guest';
  inviteStatus?: 'invited' | 'accepted';
}

// ─── Entities ─────────────────────────────────────────────────────────────────

export type EntityKind = 'TASK' | 'PO' | 'INVOICE' | 'BID_LINE' | 'RFI';

/** A business object that a Note can reference (linked via # in the composer) */
export interface Entity {
  id: string;
  kind: EntityKind;
  /** Short display label, e.g. 'PO-3221' or 'Pour foundation slab' */
  label: string;
  /** Sub-label, e.g. 'PRJ-014 · Foundations' */
  sub: string;
  autoFilled?: boolean;
}

// ─── Messages ─────────────────────────────────────────────────────────────────

export interface TextSegment    { t: 'text';    v: string }
export interface MentionSegment { t: 'mention'; v: string; uid?: string }
export interface RefSegment     { t: 'ref';     v: string; refType: EntityKind | 'AUTO'; id?: string }
export type Segment = TextSegment | MentionSegment | RefSegment;

/** Raw composer format — @[Name](user:id) for mentions, #LABEL for refs */
export type RawText = string;

/**
 * 'scanning' covers both "still uploading" and "uploaded, AV scan pending" —
 * Composer tracks the upload/uploaded distinction itself (see StagedAttachment);
 * once a message exists, only the AV-scan meaning applies. 'failed' means the
 * upload itself failed — only ever seen while staged in the composer, never on
 * a sent message, since Send is disabled until the upload has resolved.
 */
export type ScanStatus = 'scanning' | 'clean' | 'threat' | 'failed';

export interface ImageAttachment {
  kind: 'image'; name: string; size: string; scanStatus: ScanStatus;
  /** chat-api attachmentUid — set once the upload resolves */
  uid?: string;
  /** Time-limited SAS URL, resolved once scanStatus is 'clean'. Expires after
   *  60 min server-side — Message.tsx re-fetches a fresh one on click rather
   *  than trusting this indefinitely, so a stale value here is fine as a preview. */
  previewUrl?: string;
}
export interface FileAttachment {
  kind: 'file'; name: string; size: string; scanStatus: ScanStatus;
  /** chat-api attachmentUid — set once the upload resolves */
  uid?: string;
}
export type Attachment = ImageAttachment | FileAttachment;

/**
 * Composer's local state for a picked-but-not-yet-sent attachment. Lives
 * entirely inside Composer — never part of Thread/Message state, since the
 * message doesn't exist until Send. `uploading` gates the Send button
 * (disabled until the upload resolves — see ComposeAPI.stageAttachment);
 * `file` is kept for retry-after-failure without asking the user to re-pick.
 */
export interface StagedAttachment {
  att: Attachment;
  file: File;
  uploading: boolean;
  abort: AbortController;
}

/** A row in the per-workspace Files panel — richer than Attachment (adds mime/
 *  createdAt for grouping/icon-by-type) since it's listing history, not rendering
 *  inline in a message bubble. */
export interface FileListItem {
  attachmentUid: string;
  name: string;
  size: string;
  scanStatus: ScanStatus;
  mime: string;
  createdAt: string;
}

export interface Message {
  kind: 'msg' | 'post';
  /** chat-api messageUid — present on fetched messages; absent on optimistic local sends */
  uid?: string;
  /** Client-generated correlation id for an optimistic send. Stamped locally,
   *  sent as SendMessagePayload.clientMessageUid, and used to reconcile the
   *  server response back to the exact optimistic message — never a positional
   *  lookup, so concurrent in-flight sends can't swap uids. */
  clientMessageUid?: string;
  /** True when the optimistic send was rejected by the server — surfaces a
   *  "not sent" state with retry/dismiss (mirrors an attachment's 'failed'). */
  failed?: boolean;
  author: string;
  /**
   * Sender's display name straight from chat-api (ApiMessage.senderDisplayName),
   * carried through as an authoritative fallback for when `author` (the raw
   * senderUserId) doesn't resolve in the local `directory`/`membersById` map —
   * e.g. a member who joined after this client's directory snapshot, or an
   * external/customer sender. Without this, ThreadView/Message rendering fell
   * back to showing the raw id/login string (e.g. "jon_demet_admin2") instead
   * of a real name.
   */
  authorDisplayName?: string;
  time: string;
  /** Raw ISO sentAt — kept alongside the locale-formatted `time` so read-receipt
   *  comparisons ("has this member seen this message?") don't need to re-parse
   *  a display string. Absent on optimistic local sends until the server echoes back. */
  sentAtIso?: string;
  body: Segment[];
  isInitialPost?: boolean;
  lang?: string;
  translation?: Record<string, Segment[]>;
  attachment?: Attachment;
  /** chat-api attachmentUids on the raw message — set on every fetched/received message,
   *  resolved into `attachment` (display metadata) lazily. See ProjectHubPanel. */
  attachmentUids?: string[];
  channel?: 'email' | 'sms';
  recipients?: Array<{ id: string; name: string; contact: string }>;
  subject?: string;        // email channel only
  segments?: number;       // sms segment count
  pending?: boolean;
  edited?: boolean;
  editedAt?: string;
  /** emoji → userIds who reacted. Toggled optimistically, reconciled on 'reaction.toggled' WS events. */
  reactions?: Record<string, string[]>;
  /** Quoted-message preview when this is a reply-in-place. Snapshot, not a live reference. */
  replyTo?: { messageUid: string; author: string; preview: string; deleted?: boolean };
}

export interface SystemMessage { kind: 'system'; text: string; time: string }
export type AnyMessage = Message | SystemMessage;

// ─── Threads ─────────────────────────────────────────────────────────────────

export type ThreadKind = 'NOTE' | 'CHANNEL' | 'DM' | 'GROUP';
export type Priority   = 'HIGH' | 'MEDIUM' | 'LOW';
export type ContextType = EntityKind | null;
export type ChannelScope = 'project' | 'global';
export type ChannelKind  = 'team' | 'customer' | 'vendor' | 'global';

export interface Thread {
  id: string;
  kind: ThreadKind;
  mode?: 'compose';
  projectId: string | null;
  title: string;
  breadcrumb?: string[];
  // Note-specific
  contextType?: ContextType;
  contextEntity?: Entity | null;
  /**
   * Requirement 21843 follow-up: which EntityKind ContextPicker's otherwise
   * always-mixed list should default to for THIS note, set once at creation
   * from NewThreadOpts.contextKindHint (the Project Information tab that was
   * active). A default only — the picker's search still reaches every kind;
   * meaningless once contextEntity is set (a specific pick always wins).
   */
  preferredContextKind?: EntityKind;
  priority?: Priority | null;
  /** Set when this note was promoted from a channel message */
  promotedFrom?: { workspaceUid: string; messageUid: string };
  /** True when the current user is a member; false enables the self-join affordance */
  isMember?: boolean;
  /** True when this note's parent project was soft-deleted in Postgres — the
   * note stays visible but read-only (no edit/message/member changes); only
   * "Delete note" remains enabled. Notes-only; Channels never set this. */
  projectDeleted?: boolean;
  // Channel-specific
  scope?: ChannelScope;
  channelKind?: ChannelKind;
  isDefault?: boolean;
  desc?: string;
  isPrivate?: boolean;
  // Shared
  members: Member[];
  messages: AnyMessage[];
  unread: number;
  lastActivity: string;
  preview?: string;
  /** userId → ISO timestamp of that member's last-read watermark in this thread */
  readReceipts?: Record<string, string>;
  /** Members currently composing, per live 'typing' WS pings. Entries expire client-side (see ProjectHubPanel). */
  typingUsers?: { userId: string; displayName: string }[];
  /** False once a history page comes back shorter than the page size — no more to load. Undefined means unknown (not yet paged). */
  hasMoreHistory?: boolean;
  /** True while an older-history page is in flight, for a top-of-list spinner */
  loadingHistory?: boolean;
}

// ─── Data records ─────────────────────────────────────────────────────────────

export interface ChannelRecord {
  id: string;
  name: string;
  desc?: string;
  scope: ChannelScope;
  projectId: string | null;
  kind: ChannelKind;
  isDefault: boolean;
  isPrivate?: boolean;
  unread: number;
  memberIds: string[];
  inviteIds?: string[];
  seed?: AnyMessage[];
  /** Title resolved from Thread (injected by ProjectHub) */
  title?: string;
  /** Member count injected by ProjectHub */
  memberCount?: number;
}

export interface DMRecord {
  id: string;
  kind: 'DM' | 'GROUP';
  name?: string;
  members: string[];
  preview?: string;
  unread: number;
  lastActivity: string;
  seed?: AnyMessage[];
  /** Title resolved from Thread (injected by ProjectHub) */
  title?: string;
}

export interface NotificationItem {
  id: string;
  kind: 'mention' | 'batch' | 'dm' | 'created';
  priority?: 'HIGH';
  when: string;
  title: string;
  body: string;
  /** Display label — thread title (or a raw workspaceUid fallback if unresolved) */
  workspace: string;
  /** Thread id this notification targets — lets a click "jump to conversation and
   *  mark it read" and lets mark-read prune this notification. Optional only for
   *  legacy/malformed items that predate this field; new notifications always set it. */
  workspaceUid?: string;
  /**
   * PR 8024 review (Raja): the project this notification's thread belongs to,
   * best-effort — set only when the source thread was already resolved in
   * `threads` at classification time (see useChatWsEvents.pushNotification
   * call sites), which covers the common case of a NOTE/CHANNEL mention that
   * arrived while its project was active. Undefined for DM/GROUP (not
   * project-scoped) and for a workspace this client hadn't loaded yet when
   * the event arrived. Carried through shiloh:open-notification so
   * ProjectHubPanel can switch the active project before opening the thread,
   * instead of silently failing to find it in the wrong project's `threads`.
   */
  projectUid?: string;
  avatar?: string;
  initials?: string;
}

// ─── Style config maps ───────────────────────────────────────────────────────

export interface PriorityConfig {
  chip: ChipStyle;
  /** AB#26218: dark-surface counterpart to `chip` — used on the floating
   *  (popped-out) note window's tinted header, where the light-mode chip's
   *  pale background/text would be unreadable. Keeps the same per-priority
   *  hue (translucent tint) instead of collapsing every priority to one flat
   *  "filled dark" pill, which was the bug: the picker read the priority
   *  correctly but every level rendered identically once floated. */
  chipDark: ChipStyle;
  /** Tailwind classes for the dot indicator, e.g. 'bg-urgent-500' */
  dot: TwClasses;
  label: string;
  icon?: string;
}

export interface ContextConfig  { chip: ChipStyle; icon: IconName }
export interface ChannelKindConfig { label: string; icon: IconName; chip: ChipStyle }

export type PriorityStyleMap    = Record<Priority, PriorityConfig>;
export type ContextStyleMap     = Record<Exclude<ContextType, null>, ContextConfig>;
export type ChannelKindStyleMap = Record<ChannelKind, ChannelKindConfig>;

// ─── Gantt / PO (kept for potential re-use) ───────────────────────────────────

export type GanttTrack = 'sitework' | 'foundation' | 'framing' | 'mech';
export interface TrackColorConfig { bar: PalettePath; done: PalettePath }

// ─── App-level state atoms ───────────────────────────────────────────────────

export interface FloatingWindow {
  id: string;
  /** Stable React-list key, independent of `id`. AB#26069: a freshly-created
   * note/channel is opened optimistically under a local temp id, then
   * ProjectHub's create-swap effect patches `id` from the temp id to the
   * backend-assigned workspaceUid once the create call resolves. `id` was
   * also being used as this window's React key, so that one patch unmounted
   * and remounted <FloatingChat> (a different key === a different component
   * identity to React) right after the user clicked "New Note" — read as the
   * modal blinking/flickering. Generated once when the window is created
   * (see ProjectHub's popOut) and never changes afterward, including across
   * the id swap and minimize/expand round-trips (see BubbleState.uiKey).
   */
  uiKey: string;
  x: number;
  y: number;
  w?: number;
  h?: number;
  minimized?: boolean;
  maximized?: boolean;
  /** Stacking order among open floating windows — higher renders on top.
   * Bumped on click/drag-start so the interacted-with window comes to front. */
  zIndex?: number;
}

export interface BubbleState {
  id: string;
  /** Same stable-key purpose as FloatingWindow.uiKey (AB#26069) — carried
   * over verbatim when a window is minimized to a bubble and back. */
  uiKey: string;
  x: number; y: number; title: string; unread: number;
  /** The floating window's last position/size before it was minimized —
   * restored verbatim on expand so the window reopens where the user left
   * it instead of snapping back to a freshly-computed default slot. */
  lastWin?: { x: number; y: number; w?: number; h?: number };
}

export type HubFilter = 'all' | 'notes' | 'channels' | 'dms' | 'unread';

// ─── Compose API (prop passed to all chat surfaces) ──────────────────────────

/** Dialog-collected fields for a new thread — e.g. ChannelCreateDialog. */
export interface NewThreadOpts {
  name?: string;
  desc?: string;
  isPrivate?: boolean;
  /** Project members to add once the workspace is created (creator is added automatically) */
  memberIds?: string[];
  /** Optional first message collected before a DM workspace exists. */
  initialMessage?: RawText;
  /**
   * Issue 25954: the project a new CHANNEL should be created under, as
   * explicitly chosen in ChannelCreateDialog's project picker — falls back
   * to the currently active project (handleNewThread's prior, only
   * behavior) when omitted, so existing callers/tests are unaffected.
   */
  projectUid?: string;
  projectOrderNo?: number;
  projectName?: string;
  /**
   * "Additional Requirements for GoPal Notes Integration": the task GoPAL's
   * active-task header was showing when its "Create Note" icon was clicked.
   * Notes-only (handleNewThread ignores these for 'dm'/'channel') — used to
   * build the created Thread's `contextEntity` (kind 'TASK', autoFilled:
   * true), which renders read-only via ContextPicker's existing locked
   * mode — no separate/duplicate task field on Thread itself.
   */
  taskId?: string;
  taskName?: string;
  /**
   * Requirement 21843 follow-up (see ContextEntityService.ts's file header):
   * which "#" reference kind the note's ContextPicker should default its
   * (otherwise always-mixed, unfiltered) list to, sourced from whichever
   * Project Information tab was active when "Create Note" was clicked.
   * Ignored once taskId/taskName above has already produced an auto-filled,
   * locked context — that's a stronger, specific signal than a kind-level
   * default. Undefined for tabs with no matching EntityKind (BOM, Delivery
   * Slips, ...), same "show everything" behavior as before this existed.
   */
  contextKindHint?: EntityKind;
}

export interface ComposeAPI {
  /** Send raw composer text, optionally with a resolved attachment. `replyToMessageUid`
   *  quotes an existing message as a reply-in-place. `attachment` is always fully
   *  resolved (att.uid set) — Composer disables Send while a staged attachment is
   *  still uploading, so a sent message never carries an unresolved one. Passing the
   *  whole Attachment (not just the uid) lets the optimistic message render its name/
   *  size/kind immediately, no extra fetch, and the later attachment.scanned WS event
   *  matches on attachment.uid regardless of whether the messageUid has been stamped yet. */
  send: (threadId: string, raw: RawText, replyToMessageUid?: string, attachment?: Attachment) => void;
  /** Starts uploading a picked file immediately (before Send) — chat-api streams it
   *  straight into Blob Storage and creates the Attachment doc (avStatus PENDING).
   *  Resolves with the attachmentUid on success; rejects on failure (composer shows
   *  a retry). The file isn't part of any message until Send is pressed with this
   *  uid — pass `signal` through to allow the caller to abort a still-uploading pick. */
  stageAttachment?: (threadId: string, file: File, signal?: AbortSignal) => Promise<{ attachmentUid: string }>;
  /** Removes a still-staged (not yet sent) attachment — calls chat-api's orphan-delete
   *  if the upload already resolved to an attachmentUid, otherwise a no-op (the caller
   *  aborts the in-flight upload itself via the AbortController it already holds). */
  cancelStagedAttachment?: (threadId: string, attachmentUid: string) => void;
  /** Downloads an attachment (fresh short-lived SAS each call) — shared by the
   *  Message attachment cards and the workspace Files panel. */
  downloadAttachment?: (attachmentUid: string) => void;
  /** Lists every attachment ever shared in the workspace, newest first — backs the Files panel. */
  loadFiles?: (threadId: string) => Promise<FileListItem[]>;
  /** Kick off a new compose flow. opts carries dialog-collected fields (e.g. ChannelCreateDialog). */
  start: (kind: 'note' | 'dm' | 'channel', opts?: NewThreadOpts) => void;
  /** Edit an existing own-message by index in its thread */
  edit: (threadId: string, msgIndex: number, raw: RawText) => void;
  /** Ping "I'm typing" for this thread — the composer throttles its own calls */
  typing?: (threadId: string) => void;
  /** Toggle an emoji reaction on a message (optimistic; reconciled over WS) */
  react?: (threadId: string, messageUid: string, emoji: string) => void;
  /** Promote a channel message into a note (opens the promote dialog) */
  promote?: (threadId: string, messageUid: string) => void;
  /** Re-run a failed attachment upload (att.scanStatus === 'failed') */
  retryAttachment?: (threadId: string, att: Attachment) => void;
  /** Re-send a message whose optimistic send failed (msg.failed === true) */
  retryMessage?: (threadId: string, clientMessageUid: string) => void;
  /** Discard a failed optimistic message from the thread */
  dismissMessage?: (threadId: string, clientMessageUid: string) => void;
  /** Issue 25956: delete one of the current user's own sent messages */
  deleteMessage?: (threadId: string, messageUid: string) => void;
}

// ─── ProjectHub props ─────────────────────────────────────────────────────────

export interface ProjectHubProps {
  // ── Scope ──────────────────────────────────────────────────────────────────
  projectId: string;
  projectName: string;
  /** True when the active project was soft-deleted in Postgres — hides
   * "New note" from the + New menu (Notes-only; Channel/DM unaffected). */
  activeProjectDeleted?: boolean;

  // ── Data (managed externally — pass updated objects to re-render) ──────────
  threads: Record<string, Thread>;
  channels: ChannelRecord[];
  dms: DMRecord[];
  notifications?: NotificationItem[];

  /** Internal staff for @mention autocomplete */
  people?: Person[];
  /** External contacts for the email-invite flow */
  contacts?: Contact[];
  /** Entities for # reference autocomplete (notes only) */
  entities?: Entity[];

  // ── Load state (set by the data adapter; rendered by HubRail) ──────────────
  /** True while the initial notes/channels fetch is in flight */
  loading?: boolean;
  /** User-facing message when the initial fetch failed; null when healthy */
  loadError?: string | null;
  /** True while project members are being fetched for the mention/member pickers */
  membersLoading?: boolean;
  /** User-facing message when the members fetch failed; null when healthy */
  membersError?: string | null;
  onRetryMembers?: () => void;
  /** Tenant-wide members (getAllProjectMembers) for New Direct Message's
   * "Add members" picker — a DM isn't tied to a project, so `people` above
   * (project-scoped) isn't the right source for it. */
  tenantPeople?: Person[];
  tenantMembersLoading?: boolean;
  tenantMembersError?: string | null;
  onRetryTenantMembers?: () => void;
  /** Lazily fetches tenant members the first time the DM Add-members
   * dropdown opens. */
  onLoadTenantMembers?: () => void;
  /** Thread ids whose last context-entity save to the backend failed */
  contextSaveErrors?: Record<string, boolean>;
  onRetryContext?: (threadId: string) => void;

  // ── Mutations (all delegate to caller) ────────────────────────────────────
  onSend: (threadId: string, raw: RawText, replyToMessageUid?: string, attachment?: Attachment) => void;
  /** Starts uploading a picked file — see ComposeAPI.stageAttachment */
  onStageAttachment?: (threadId: string, file: File, signal?: AbortSignal) => Promise<{ attachmentUid: string }>;
  /** Removes a still-staged (not yet sent) attachment — orphan-delete if already uploaded */
  onCancelStagedAttachment?: (threadId: string, attachmentUid: string) => void;
  /** Downloads an attachment on demand (fresh SAS) — Message cards + Files panel */
  onDownloadAttachment?: (attachmentUid: string) => void;
  /** Lists every attachment ever shared in a workspace (backs the Files panel) */
  onLoadFiles?: (threadId: string) => Promise<FileListItem[]>;
  onNewThread: (kind: 'note' | 'dm' | 'channel', opts?: NewThreadOpts) => string;
  /**
   * Bumped (to a new number) by an external trigger — currently GoPAL's
   * active-task header "Create Note" icon, wired via MainLayout — asking the
   * Hub to start a brand-new note, exactly like HubRail's own "New Note"
   * button (same compose.start('note') path: creates the thread, pops it out,
   * shows the "New note opened" toast). Scoped to the correct project
   * automatically since this component is already driven by the active
   * project's projectId — no extra context is threaded through for that.
   * undefined/0 on mount is a no-op; only a change fires it.
   */
  externalNewNoteRequest?: number;
  /**
   * Companion payload for externalNewNoteRequest — the project/task GoPAL
   * was showing when its "Create Note" icon was clicked, carried through to
   * the resulting compose.start('note', opts) call so the new note pins to
   * that project/task (see NewThreadOpts.taskId/taskName) instead of
   * whatever project happens to be ambiently "active" by the time this
   * effect fires. null/undefined falls back to today's ambient-project
   * behavior (HubRail's own "New Note" button doesn't set this).
   */
  externalNoteContext?: NewThreadOpts | null;
  /**
   * Bumped by MainLayout when an external trigger (currently the Top Bar
   * Global Notification bell's "View" action on a bridged Shiloh
   * notification — see MainLayout's `shiloh:open-notification` listener)
   * asks the Hub to open a specific thread directly, same "counter" pattern
   * as externalNewNoteRequest above. undefined/0 on mount is a no-op; only
   * a change fires it.
   *
   * PR 8024 review (Raja): the target thread's project may not be the one
   * currently active — ProjectHubPanel switches it first (via its own
   * openThreadProjectUid prop and onSelectProject) when the notification
   * carried a projectUid, before this request even reaches here. ProjectHub
   * itself doesn't own that switch, so it isn't threaded any further than
   * ProjectHubPanel. Either way, `threads` may not contain the id the
   * instant this fires — the project switch's fetch is async. This effect
   * keeps the request pending and opens it the moment the id appears in
   * `threads`, or shows a toast instead of silently doing nothing if it
   * never does (see the effect in ProjectHub.tsx).
   */
  externalOpenThreadRequest?: number;
  /** Companion payload for externalOpenThreadRequest — the workspaceUid to open. */
  externalOpenThreadUid?: string | null;
  onEditTitle: (threadId: string, title: string) => void;
  onEditContext: (threadId: string, entity: Entity | null) => void;
  onEditPriority?: (threadId: string, priority: Priority) => void;
  onEditMembers: (threadId: string, members: Member[]) => void;
  onEditMessage: (threadId: string, msgIndex: number, raw: RawText) => void;
  onDeleteThread: (threadId: string) => void;
  onMarkRead: (threadId: string) => void;
  onMarkUnread: (threadId: string) => void;
  onSaveChannelSettings: (
    channelId: string,
    patch: { name: string; desc: string; isPrivate: boolean; memberList: Array<{ id: string; role: string }> }
  ) => void;
  onDeleteChannel: (channelId: string) => void;
  onVisibleThreadIdsChange?: (threadIds: string[]) => void;
  /** Clears the notification list (bell "Mark all read") */
  onMarkAllNotificationsRead?: () => void;
  /** Sends a live "I'm typing" ping for a thread over WebSocket */
  onTyping?: (threadId: string) => void;
  /** Toggles an emoji reaction on a message */
  onToggleReaction?: (threadId: string, messageUid: string, emoji: string) => void;
  /** Opens the promote-to-note flow for a channel message */
  onPromoteMessage?: (threadId: string, messageUid: string) => void;
  /** Re-runs a failed attachment upload */
  onRetryAttachment?: (threadId: string, att: Attachment) => void;
  /** Re-sends a message whose optimistic send failed */
  onRetryMessage?: (threadId: string, clientMessageUid: string) => void;
  /** Discards a failed optimistic message */
  onDismissMessage?: (threadId: string, clientMessageUid: string) => void;
  /** Issue 25956: deletes one of the current user's own sent messages */
  onDeleteMessage?: (threadId: string, messageUid: string) => void;
  /** Self-joins an open note */
  onJoinNote?: (threadId: string) => void;
  /** Fetches the next-older page of history for a thread (infinite scroll-up) */
  onLoadOlderMessages?: (threadId: string) => void;

  // ── Visual config ─────────────────────────────────────────────────────────
  /** Sidebar panel width in px. Default 360. */
  width?: number;
  /** Dense mode — tighter row heights and 1-row composer */
  compact?: boolean;
  /** Whether the host app is in dark mode (used for gradient rendering) */
  mode?: 'light' | 'dark';

  // ── Multi-project navigation ───────────────────────────────────────────────
  projectList?: Array<{ projectUid: string; projectName: string; projectOrderNo?: number }>;
  projectListTotal?: number;
  projectListPage?: number;
  projectListLoading?: boolean;
  /** Set when the project-list fetch itself failed — distinct from an empty
   *  "No projects found" result. */
  projectListError?: string | null;
  projectSearch?: string;
  onProjectSearch?: (q: string) => void;
  onProjectPageChange?: (page: number) => void;
  onSelectProject?: (projectUid: string, projectName: string) => void;
}

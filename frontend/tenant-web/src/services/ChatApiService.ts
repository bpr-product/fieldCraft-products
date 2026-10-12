/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 02-July-2026
* @author      : Claude (Phase 2 — Notes/Chat Tailwind migration)
* @since       : 0.0.1
* Requirement# : 21843
* Purpose      : Typed client for the Project Hub chat-api. Single home for every
*                chat-api call the Hub panel makes — no raw fetch in components.
*                Endpoint paths mirror fieldcraft-chat-api/src/routes/*.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |      AUTHOR     |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*
*/
import { ConfigUrl } from '../auth/OidcConfig';
import type { ApiWorkspace, ApiMessage, ApiMember } from '../hooks/workspaceMappers';

// ─── Response shapes not covered by workspaceMappers ─────────────────────────

export interface ApiUserProfile {
  userId: string;
  displayName?: string;
  email?: string;
}

export interface ApiProjectListItem {
  projectUid: string;
  projectName: string;
  projectOrderNo?: number;
  /** True when this project was soft-deleted in Postgres (ChatProjectIndex.projectDeleted) —
   * checked before creating a NEW note, since no Workspace doc exists yet to carry the flag. */
  projectDeleted?: boolean;
}

export interface ApiProjectListPage {
  data: ApiProjectListItem[];
  total: number;
}

// Field-for-field mirror of chat-api's CreateNoteRequest (domain/types.ts) —
// this used to be modeled on CreateChannelRequest (`isPrivate`, no `projectOrderNo`),
// which meant every "New Note" silently omitted the one required field the
// backend actually needs and sent one it doesn't accept.
export interface CreateNotePayload {
  projectUid: string | null;
  projectOrderNo: number;
  title: string;
  priority?: 'HIGH' | 'MEDIUM' | 'LOW';
  contextTag?: string;
  context?: { type: string; entityId?: string; entityLabel?: string };
  body?: string;
}

export interface CreateChannelPayload {
  projectUid: string | null;
  projectOrderNo: number;
  name: string;
  isPrivate?: boolean;
}

export interface CreateDmPayload {
  otherUserId: string;
  scope?: 'INTERNAL' | 'PROJECT' | 'CUSTOMER' | 'VENDOR';
  customerUid?: string;
  vendorUid?: string;
}

/** Map of workspaceUid → unread message count */
export type UnreadCounts = Record<string, number>;

// ─── Error type ───────────────────────────────────────────────────────────────

export class ChatApiError extends Error {
  readonly status: number;
  readonly path: string;
  /** True when the request was aborted by the client-side timeout rather than
   *  returning an HTTP error — lets callers distinguish "backend slow/hung"
   *  from "backend rejected the request". `status` is 408 in this case. */
  readonly timeout: boolean;

  constructor(path: string, status: number, message?: string, timeout = false) {
    super(message ?? `chat-api ${path} failed with HTTP ${status}`);
    this.name = 'ChatApiError';
    this.status = status;
    this.path = path;
    this.timeout = timeout;
  }
}

// ─── Request helper ───────────────────────────────────────────────────────────

/** Client-side timeout for chat-api REST calls. A hung/slow backend must not
 *  block the Hub UI indefinitely. Binary upload paths (direct-to-blob PUTs and
 *  the multipart fallback) are intentionally NOT bounded by this — large
 *  field-crew uploads are long-running by design. */
const REQUEST_TIMEOUT_MS = 20_000;

async function request<T>(
  path: string,
  accessToken: string,
  init?: Omit<RequestInit, 'headers'> & { body?: string },
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${ConfigUrl.chatApiUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch (err) {
    // An abort from our timer surfaces as a distinguishable ChatApiError;
    // any other network error propagates unchanged.
    if (controller.signal.aborted) {
      throw new ChatApiError(
        path,
        408,
        `chat-api ${path} timed out after ${REQUEST_TIMEOUT_MS}ms`,
        true,
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) throw new ChatApiError(path, res.status);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ─── Identity & directory ────────────────────────────────────────────────────

export const getMyProfile = (accessToken: string) =>
  request<ApiUserProfile>('/users/me', accessToken);

export const getProjectMembers = (projectUid: string, accessToken: string) =>
  request<ApiMember[]>(`/projects/${projectUid}/members`, accessToken);

// ─── Projects ─────────────────────────────────────────────────────────────────

export const getProjects = (
  { page, size, search }: { page: number; size: number; search?: string },
  accessToken: string,
) => {
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (search) params.set('search', search);
  return request<ApiProjectListPage>(`/projects?${params}`, accessToken);
};

// ─── Workspaces (notes / channels / dms / groups) ─────────────────────────────

export interface GetNotesFilters {
  priority?: 'HIGH' | 'MEDIUM' | 'LOW';
  contextType?: string;
  status?: 'OPEN' | 'RESOLVED';
  sortBy?: 'priority' | 'date';
}

export const getNotes = (projectUid: string, accessToken: string, filters?: GetNotesFilters) => {
  const params = new URLSearchParams({ projectUid });
  if (filters?.priority) params.set('priority', filters.priority);
  if (filters?.contextType) params.set('contextType', filters.contextType);
  if (filters?.status) params.set('status', filters.status);
  if (filters?.sortBy) params.set('sortBy', filters.sortBy);
  return request<ApiWorkspace[]>(`/notes?${params}`, accessToken);
};

export const getChannels = (projectUid: string, accessToken: string) =>
  request<ApiWorkspace[]>(`/channels?projectUid=${encodeURIComponent(projectUid)}`, accessToken);

export const getDms = (accessToken: string) =>
  request<ApiWorkspace[]>('/dms', accessToken);

// createOrFetchDm is idempotent server-side — calling it with the same
// otherUserId twice returns the existing DM instead of creating a duplicate.
export const createDm = (payload: CreateDmPayload, accessToken: string) =>
  request<ApiWorkspace>('/dms', accessToken, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const getGroups = (accessToken: string) =>
  request<ApiWorkspace[]>('/groups', accessToken);

// PATCH /chat/groups/:uid — renames a group. The ThreadView title input for
// GROUP threads existed before this endpoint did (edits were local-state
// only and reverted on reload) — this is what makes them persist.
export const renameGroup = (workspaceUid: string, name: string, accessToken: string) =>
  request<ApiWorkspace>(`/groups/${workspaceUid}`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  });

// PATCH /chat/dms/:uid — sets a custom display name on a DM, overriding the
// derived title (which otherwise falls back to the other participant's
// resolved name, or a raw truncated userId if that profile hasn't synced).
export const renameDm = (workspaceUid: string, name: string, accessToken: string) =>
  request<ApiWorkspace>(`/dms/${workspaceUid}`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  });

// PATCH /chat/dms/:uid/members — adds one or more members to an existing DM.
// If the resulting membership exceeds 2, chat-api promotes the workspace to
// GROUP in place and the response's `type` field flips to 'GROUP'; the
// caller (AddMemberDialog / ProjectHubPanel) should refetch/re-render off
// that returned workspace rather than assuming it's still a DM.
export const addDmMembers = (
  workspaceUid: string,
  userIds: string[],
  accessToken: string,
  groupName?: string,
) =>
  request<ApiWorkspace>(`/dms/${workspaceUid}/members`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify({ userIds, groupName }),
  });

// Backs removal from either a DM or a GROUP — chat-api's generic removeMember
// (reached via the /groups path) doesn't actually check workspace type, it
// just requires the caller to already be a member of the given workspaceUid.
export const removeGroupMember = (workspaceUid: string, userId: string, accessToken: string) =>
  request<void>(`/groups/${workspaceUid}/members/${userId}`, accessToken, { method: 'DELETE' });

export const getWorkspaceMessages = (
  workspaceUid: string,
  limit: number,
  accessToken: string,
  /** ISO timestamp cursor — fetches the page immediately older than this */
  before?: string,
) =>
  request<ApiMessage[]>(
    `/workspaces/${workspaceUid}/messages?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ''}`,
    accessToken,
  );

export const getWorkspaceMembers = (workspaceUid: string, accessToken: string) =>
  request<ApiMember[]>(`/workspaces/${workspaceUid}/members`, accessToken);

export const getUnreadCounts = (workspaceUids: string[], accessToken: string) =>
  request<UnreadCounts>(
    `/workspaces/unread-counts?uids=${encodeURIComponent(workspaceUids.join(','))}`,
    accessToken,
  );

export interface ApiReadReceipt {
  workspaceUid: string;
  userId: string;
  lastReadAt: string;
}

export const getReadReceipts = (workspaceUid: string, accessToken: string) =>
  request<ApiReadReceipt[]>(`/workspaces/${workspaceUid}/read-receipts`, accessToken);

// ─── Batch fetches (one request for many workspaces) ──────────────────────────
// These collapse the per-workspace fan-out the Hub made on project load into a
// single call each, mirroring getUnreadCounts. Each returns a map keyed by
// workspaceUid (workspaces the caller can't see are simply absent from the map).

const uidsParam = (workspaceUids: string[]) =>
  encodeURIComponent(workspaceUids.join(','));

export const getWorkspaceMessagesBatch = (
  workspaceUids: string[],
  limit: number,
  accessToken: string,
) =>
  request<Record<string, ApiMessage[]>>(
    `/workspaces/messages-batch?uids=${uidsParam(workspaceUids)}&limit=${limit}`,
    accessToken,
  );

export const getWorkspaceMembersBatch = (workspaceUids: string[], accessToken: string) =>
  request<Record<string, ApiMember[]>>(
    `/workspaces/members-batch?uids=${uidsParam(workspaceUids)}`,
    accessToken,
  );

export const getReadReceiptsBatch = (workspaceUids: string[], accessToken: string) =>
  request<Record<string, ApiReadReceipt[]>>(
    `/workspaces/read-receipts-batch?uids=${uidsParam(workspaceUids)}`,
    accessToken,
  );

// ─── Messages ─────────────────────────────────────────────────────────────────

export interface SendMessagePayload {
  body: string;
  bodyHtml?: string;
  /** Client-generated idempotency key for deduplication */
  clientMessageUid?: string;
  attachmentUids?: string[];
  /** Quotes an existing message as a reply-in-place; must belong to the same workspace. */
  replyToMessageUid?: string;
}

export const sendMessage = (
  workspaceUid: string,
  payload: SendMessagePayload,
  accessToken: string,
) =>
  request<ApiMessage>(`/workspaces/${workspaceUid}/messages`, accessToken, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

/** Toggle an emoji reaction on a message (add if absent, remove if present). */
export const toggleReaction = (messageUid: string, emoji: string, accessToken: string) =>
  request<ApiMessage>(`/messages/${messageUid}/reactions`, accessToken, {
    method: 'POST',
    body: JSON.stringify({ emoji }),
  });

export const editMessage = (messageUid: string, body: string, accessToken: string) =>
  request<void>(`/messages/${messageUid}`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify({ body }),
  });

// Issue 25956: backend soft-delete (message.service.ts's softDelete, wired to
// DELETE /chat/messages/:uid in routes/messages.ts) was already fully
// implemented and correct — this client call was simply missing, so nothing
// in the UI could ever reach it. The only "delete" affordance that existed
// (ThreadView.tsx's note-level trash icon) deletes an entire note/thread via
// a different endpoint (deleteNote, below), not a single message.
export const deleteMessage = (messageUid: string, accessToken: string) =>
  request<void>(`/messages/${messageUid}`, accessToken, {
    method: 'DELETE',
  });

// ─── Attachments ──────────────────────────────────────────────────────────────

export interface ApiAttachment {
  attachmentUid: string;
  workspaceUid?: string;
  originalFilename: string;
  mime: string;
  sizeBytes: number;
  avStatus: 'PENDING' | 'CLEAN' | 'INFECTED' | 'FAILED';
  createdAt: string;
}

/**
 * Uploads a file for a workspace. The file streams through chat-api and
 * straight into Blob Storage server-side (chat-api hashes and enforces the
 * size cap while streaming — see AttachmentService.upload) — there is no
 * direct-to-blob broker on the client side, so no SAS handling, no chunking,
 * and no client-side hashing here.
 *
 * Bypasses the shared `request()` helper — a multipart body must not carry a
 * JSON Content-Type, and the browser needs to set its own boundary parameter.
 *
 * `signal` lets the caller abort an in-flight upload (e.g. the user removes
 * a still-uploading attachment before hitting Send). Because chat-api only
 * creates the Attachment doc after it has fully received the stream, an
 * aborted request leaves nothing server-side to clean up — there's no
 * upload-session equivalent to cancel.
 */
export const uploadAttachment = async (
  workspaceUid: string,
  file: File,
  accessToken: string,
  signal?: AbortSignal,
): Promise<ApiAttachment> => {
  const path = `/workspaces/${workspaceUid}/attachments`;
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${ConfigUrl.chatApiUrl}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}` },
    body: form,
    signal,
  });
  if (!res.ok) throw new ChatApiError(path, res.status);
  return (await res.json()) as ApiAttachment;
};

export const getAttachment = (attachmentUid: string, accessToken: string) =>
  request<ApiAttachment>(`/attachments/${attachmentUid}`, accessToken);

export const getAttachmentDownloadUrl = (attachmentUid: string, accessToken: string) =>
  request<{ downloadUrl: string }>(`/attachments/${attachmentUid}/download-url`, accessToken);

/** Per-workspace file browser (hub/FilesPanel.tsx) — lists every attachment ever shared there. */
export const getWorkspaceAttachments = (workspaceUid: string, accessToken: string) =>
  request<ApiAttachment[]>(`/workspaces/${workspaceUid}/attachments`, accessToken);

/** Cancels an attachment the caller uploaded but never sent — orphan-only, mirrors
 *  AttachmentService.deleteAttachment's own scoping (chat-api rejects anything else). */
export const deleteAttachment = (attachmentUid: string, accessToken: string) =>
  request<void>(`/attachments/${attachmentUid}`, accessToken, { method: 'DELETE' });

// ─── Workspace state ──────────────────────────────────────────────────────────

export const markWorkspaceRead = (workspaceUid: string, accessToken: string) =>
  request<void>(`/workspaces/${workspaceUid}/read`, accessToken, { method: 'POST' });

// ─── Note mutations ───────────────────────────────────────────────────────────

export const createNote = (payload: CreateNotePayload, accessToken: string) =>
  request<ApiWorkspace>('/notes', accessToken, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const updateNote = (
  workspaceUid: string,
  patch: {
    title?: string;
    priority?: 'HIGH' | 'MEDIUM' | 'LOW';
    /** null clears the note's linked context. autoFilled: PR 7939 review
     * (blocking) — lets a GoPAL-pinned/locked context persist server-side
     * instead of only in the current browser session. */
    context?: { type: string; entityId?: string; entityLabel?: string; autoFilled?: boolean } | null;
  },
  accessToken: string,
) =>
  request<ApiWorkspace>(`/notes/${workspaceUid}`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });

export const deleteNote = (workspaceUid: string, accessToken: string) =>
  request<void>(`/notes/${workspaceUid}`, accessToken, { method: 'DELETE' });

/** Self-join an open note (spec §3.3 — members can join a note themselves). */
export const joinNote = (workspaceUid: string, accessToken: string) =>
  request<void>(`/notes/${workspaceUid}/join`, accessToken, { method: 'POST' });

export interface PromoteMessagePayload {
  messageUid: string;
  title?: string;
  priority?: 'HIGH' | 'MEDIUM' | 'LOW';
  contextTag?: string;
  context?: { type: string; entityId?: string; entityLabel?: string };
  confirmDuplicate?: boolean;
}

/** Promote a channel message into a note (spec §3.2 — #rfi-and-issues → note). */
export const promoteMessageToNote = (payload: PromoteMessagePayload, accessToken: string) =>
  request<ApiWorkspace>('/notes/promote', accessToken, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const addNoteMember = (workspaceUid: string, userId: string, accessToken: string) =>
  request<void>(`/notes/${workspaceUid}/members`, accessToken, {
    method: 'POST',
    body: JSON.stringify({ userId }),
  });

export const removeNoteMember = (workspaceUid: string, userId: string, accessToken: string) =>
  request<void>(`/notes/${workspaceUid}/members/${userId}`, accessToken, { method: 'DELETE' });

// ─── Channel mutations ────────────────────────────────────────────────────────

export const createChannel = (payload: CreateChannelPayload, accessToken: string) =>
  request<ApiWorkspace>('/channels', accessToken, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const setChannelTopic = (channelUid: string, topic: string, accessToken: string) =>
  request<void>(`/channels/${channelUid}/topic`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify({ topic }),
  });

export const deleteChannel = (channelUid: string, accessToken: string) =>
  request<void>(`/channels/${channelUid}`, accessToken, { method: 'DELETE' });

export const addChannelMember = (channelUid: string, userId: string, accessToken: string) =>
  request<void>(`/channels/${channelUid}/members`, accessToken, {
    method: 'POST',
    body: JSON.stringify({ userId }),
  });

export const removeChannelMember = (channelUid: string, userId: string, accessToken: string) =>
  request<void>(`/channels/${channelUid}/members/${userId}`, accessToken, { method: 'DELETE' });

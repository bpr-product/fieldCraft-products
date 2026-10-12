/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
* Purpose      : Unit tests for the real ChatApiService implementation (fetch
*                mocked, not the module) — every other test file in this repo
*                mocks this module wholesale, so nothing else exercises its
*                actual request-building logic.
 */
import * as ChatApi from './ChatApiService';
import { ChatApiError } from './ChatApiService';

const BASE = 'http://localhost:8086/chat';

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
  status,
    json: () => Promise.resolve(body),
  } as Response;
}

function noContentResponse() {
  return { ok: true, status: 204, json: () => Promise.reject(new Error('should not be called')) } as Response;
}

describe('ChatApiService — request() helper (exercised via getMyProfile)', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn();
    global.fetch = fetchMock as any;
});

  it('sends a bearer token and no Content-Type for a GET with no body', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ userId: 'u1' }));
    const result = await ChatApi.getMyProfile('tok-1');
    expect(result).toEqual({ userId: 'u1' });
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/users/me`, expect.objectContaining({
      headers: { Authorization: 'Bearer tok-1' },
    }));
  });

  it('adds Content-Type: application/json when a body is present', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ workspaceUid: 'ws-1' }));
    await ChatApi.createDm({ otherUserId: 'u2' }, 'tok-1');
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).toEqual({ Authorization: 'Bearer tok-1', 'Content-Type': 'application/json' });
    expect(init.body).toBe(JSON.stringify({ otherUserId: 'u2' }));
  });

  it('throws ChatApiError with the path and status on a non-ok response', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 403));
    await expect(ChatApi.getMyProfile('tok-1')).rejects.toMatchObject({
      name: 'ChatApiError', status: 403, path: '/users/me',
    });
    expect(new ChatApiError('/x', 500).message).toBe('chat-api /x failed with HTTP 500');
    expect(new ChatApiError('/x', 500, 'custom').message).toBe('custom');
  });

  it('returns undefined for a 204 without touching the body', async () => {
    fetchMock.mockResolvedValue(noContentResponse());
    await expect(ChatApi.deleteNote('ws-1', 'tok-1')).resolves.toBeUndefined();
  });
});

describe('ChatApiService — attachments', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn();
    global.fetch = fetchMock as any;
  });

  describe('uploadAttachment', () => {
    it('posts the file as multipart/form-data without setting Content-Type itself', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ attachmentUid: 'att-1', avStatus: 'PENDING' }));
      const file = new File(['x'], 'a.png', { type: 'image/png' });

      const result = await ChatApi.uploadAttachment('ws-1', file, 'tok-1');

      expect(result).toEqual({ attachmentUid: 'att-1', avStatus: 'PENDING' });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${BASE}/workspaces/ws-1/attachments`);
      expect(init.method).toBe('POST');
      expect(init.headers).toEqual({ Authorization: 'Bearer tok-1' });
      expect(init.body).toBeInstanceOf(FormData);
      expect(init.body.get('file')).toBe(file);
      expect(init.signal).toBeUndefined();
  });

    it('threads an AbortSignal through to fetch so an in-flight upload can be cancelled', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ attachmentUid: 'att-1', avStatus: 'PENDING' }));
      const controller = new AbortController();
      const file = new File(['x'], 'a.png', { type: 'image/png' });

      await ChatApi.uploadAttachment('ws-1', file, 'tok-1', controller.signal);

      expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

    it('throws ChatApiError on a non-ok response', async () => {
      fetchMock.mockResolvedValue(jsonResponse({}, 413));
      const file = new File(['x'], 'huge.bin', { type: 'application/octet-stream' });
      await expect(ChatApi.uploadAttachment('ws-1', file, 'tok-1')).rejects.toMatchObject({
        status: 413, path: '/workspaces/ws-1/attachments',
      });
    });

    it('propagates an AbortError from fetch when the caller aborts', async () => {
      const abortError = new DOMException('The operation was aborted', 'AbortError');
      fetchMock.mockRejectedValue(abortError);
      const file = new File(['x'], 'a.png', { type: 'image/png' });
      await expect(ChatApi.uploadAttachment('ws-1', file, 'tok-1')).rejects.toBe(abortError);
    });
  });

  it('getAttachment fetches attachment metadata by uid', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ attachmentUid: 'att-1' }));
    await ChatApi.getAttachment('att-1', 'tok-1');
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/attachments/att-1`, expect.objectContaining({ headers: { Authorization: 'Bearer tok-1' } }));
  });

  it('getAttachmentDownloadUrl returns the downloadUrl field chat-api actually sends', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ downloadUrl: 'https://blob/sas' }));
    const result = await ChatApi.getAttachmentDownloadUrl('att-1', 'tok-1');
    expect(result).toEqual({ downloadUrl: 'https://blob/sas' });
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/attachments/att-1/download-url`, expect.anything());
  });

  it('getWorkspaceAttachments lists every attachment for a workspace (Files panel)', async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ attachmentUid: 'att-1' }]));
    const result = await ChatApi.getWorkspaceAttachments('ws-1', 'tok-1');
    expect(result).toEqual([{ attachmentUid: 'att-1' }]);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/workspaces/ws-1/attachments`, expect.anything());
  });

  it('deleteAttachment issues a DELETE and resolves void on 204', async () => {
    fetchMock.mockResolvedValue(noContentResponse());
    await expect(ChatApi.deleteAttachment('att-1', 'tok-1')).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/attachments/att-1`, expect.objectContaining({
      method: 'DELETE', headers: { Authorization: 'Bearer tok-1' },
    }));
  });
});

describe('ChatApiService — remaining endpoints (query building + method/body wiring)', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn();
    global.fetch = fetchMock as any;
    fetchMock.mockResolvedValue(jsonResponse({}));
  });

  it('getProjectMembers', async () => {
    await ChatApi.getProjectMembers('prj-1', 'tok');
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/projects/prj-1/members`, expect.anything());
  });

  it('getProjects builds page/size/search query params, omitting search when absent', async () => {
    await ChatApi.getProjects({ page: 2, size: 20, search: 'riverside' }, 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/projects?page=2&size=20&search=riverside`);
    fetchMock.mockClear();
    await ChatApi.getProjects({ page: 1, size: 10 }, 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/projects?page=1&size=10`);
  });

  it('getNotes builds every optional filter param when provided', async () => {
    await ChatApi.getNotes('prj-1', 'tok', { priority: 'HIGH', contextType: 'TASK', status: 'OPEN', sortBy: 'date' });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain('projectUid=prj-1');
    expect(url).toContain('priority=HIGH');
    expect(url).toContain('contextType=TASK');
    expect(url).toContain('status=OPEN');
    expect(url).toContain('sortBy=date');
  });

  it('getNotes omits filter params entirely when no filters object is passed', async () => {
    await ChatApi.getNotes('prj-1', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/notes?projectUid=prj-1`);
  });

  it('getChannels', async () => {
    await ChatApi.getChannels('prj-1', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/channels?projectUid=prj-1`);
  });

  it('getDms', async () => {
    await ChatApi.getDms('tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/dms`);
  });

  it('createDm', async () => {
    await ChatApi.createDm({ otherUserId: 'u2', scope: 'PROJECT' }, 'tok');
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
  });

  it('getGroups', async () => {
    await ChatApi.getGroups('tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/groups`);
          });

  it('getWorkspaceMessages includes the before cursor only when provided', async () => {
    await ChatApi.getWorkspaceMessages('ws-1', 50, 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/workspaces/ws-1/messages?limit=50`);
    fetchMock.mockClear();
    await ChatApi.getWorkspaceMessages('ws-1', 50, 'tok', '2026-07-01T00:00:00Z');
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/workspaces/ws-1/messages?limit=50&before=2026-07-01T00%3A00%3A00Z`,
    );
  });

  it('getWorkspaceMembers', async () => {
    await ChatApi.getWorkspaceMembers('ws-1', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/workspaces/ws-1/members`);
  });

  it('getUnreadCounts joins workspace uids into a single query param', async () => {
    await ChatApi.getUnreadCounts(['ws-1', 'ws-2'], 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/workspaces/unread-counts?uids=ws-1%2Cws-2`);
  });

  it('getReadReceipts', async () => {
    await ChatApi.getReadReceipts('ws-1', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/workspaces/ws-1/read-receipts`);
  });

  it('sendMessage', async () => {
    await ChatApi.sendMessage('ws-1', { body: 'hi' }, 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/workspaces/ws-1/messages`);
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
  });

  it('toggleReaction', async () => {
    await ChatApi.toggleReaction('msg-1', '👍', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/messages/msg-1/reactions`);
  });

  it('editMessage', async () => {
    await ChatApi.editMessage('msg-1', 'edited', 'tok');
    expect(fetchMock.mock.calls[0][1].method).toBe('PATCH');
  });

  // Issue 25956: this client call didn't exist at all — chat-api's
  // DELETE /chat/messages/:uid was already implemented and correctly
  // permission-checked, but nothing in the frontend could ever reach it.
  it('deleteMessage', async () => {
    await ChatApi.deleteMessage('msg-1', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/messages/msg-1`);
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE');
  });

  it('markWorkspaceRead', async () => {
    await ChatApi.markWorkspaceRead('ws-1', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/workspaces/ws-1/read`);
  });

  it('createNote', async () => {
    await ChatApi.createNote({ projectUid: 'prj-1', projectOrderNo: 1, title: 'T' }, 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/notes`);
});

  it('updateNote', async () => {
    await ChatApi.updateNote('ws-1', { title: 'new' }, 'tok');
    expect(fetchMock.mock.calls[0][1].method).toBe('PATCH');
  });

  it('deleteNote', async () => {
    await ChatApi.deleteNote('ws-1', 'tok');
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE');
  });

  it('joinNote', async () => {
    await ChatApi.joinNote('ws-1', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/notes/ws-1/join`);
  });

  it('promoteMessageToNote', async () => {
    await ChatApi.promoteMessageToNote({ messageUid: 'msg-1' }, 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/notes/promote`);
  });

  it('addNoteMember', async () => {
    await ChatApi.addNoteMember('ws-1', 'u2', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/notes/ws-1/members`);
  });

  it('removeNoteMember', async () => {
    await ChatApi.removeNoteMember('ws-1', 'u2', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/notes/ws-1/members/u2`);
  });

  it('createChannel', async () => {
    await ChatApi.createChannel({ projectUid: 'prj-1', projectOrderNo: 1, name: 'general' }, 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/channels`);
  });

  it('setChannelTopic', async () => {
    await ChatApi.setChannelTopic('ch-1', 'new topic', 'tok');
    expect(fetchMock.mock.calls[0][1].method).toBe('PATCH');
  });

  it('deleteChannel', async () => {
    await ChatApi.deleteChannel('ch-1', 'tok');
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE');
  });

  it('addChannelMember', async () => {
    await ChatApi.addChannelMember('ch-1', 'u2', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/channels/ch-1/members`);
  });

  it('removeChannelMember', async () => {
    await ChatApi.removeChannelMember('ch-1', 'u2', 'tok');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/channels/ch-1/members/u2`);
  });
});

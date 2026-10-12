/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * Unit tests for QuickBooksService.ts
 *
 * Architecture under test:
 *   getQuickBooksCredential  →  executeGetData          (ConfigUrl.apiUrl)
 *   getQuickBooksSecretHeader →  getQuickBooksCredential → extracts "api-secret"
 *   qbGet   →  getQuickBooksSecretHeader + executeGetDataWithHeaders  (ConfigUrl.extservice_base_url)
 *   qbPost  →  getQuickBooksSecretHeader + executePostDataWithHeaders (ConfigUrl.extservice_base_url)
 *
 * Mocks: executeGetData (credential), executeGetDataWithHeaders, executePostDataWithHeaders,
 *        ConfigUrl, UserSession.userSession / getUserSessionDetails.
 * Does NOT mock the service itself — exercises real code paths.
 */

import {
  disconnectQuickBooks,
  exportQuickBooksPayments,
  getQuickBooksAccountCdcView,
  getQuickBooksBillCdcView,
  getQuickBooksAutoSyncConfig,
  getQuickBooksCredential,
  getQuickBooksSyncJob,
  getQuickBooksSyncState,
  getQuickBooksStatus,
  getQuickBooksVendorCdcView,
  importQuickBooksPayments,
  initiateQuickBooksAuth,
  resetQuickBooksAuditUserCache,
  saveQuickBooksAutoSyncConfig,
  startQuickBooksBillsSync,
  startQuickBooksChartOfAccountsSync,
  startQuickBooksPaymentsSync,
  startQuickBooksVendorsSync,
} from './QuickBooksService';
import {
  executeGetData,
  executeGetDataWithHeaders,
  executePostDataWithHeaders,
} from '../ApiBase';
import * as UserSessionModule from '../../hooks/UserSession';

// ── Module mocks ───────────────────────────────────────────────

jest.mock('../ApiBase', () => ({
  executeGetData:             jest.fn(),
  executePostData:            jest.fn(),
  executeGetDataWithHeaders:  jest.fn().mockResolvedValue({ data: {} }),
  executePostDataWithHeaders: jest.fn().mockResolvedValue({ data: {} }),
}));

jest.mock('../../auth/OidcConfig', () => ({
  ConfigUrl: {
    apiUrl:        'https://api.test.local',
    extservice_base_url: 'https://qb.test.local',
  },
}));

// userSession is a mutable module-level singleton; expose it as a writable
// property so individual tests can swap the active session without re-importing.
// __esModule is required: without it Babel's `import * as` interop hands the test a
// *copy* of the mock, so setUserSession() would never be visible to the service and
// every audit-enrichment path would fall through to the async fetch.
jest.mock('../../hooks/UserSession', () => ({
  __esModule:           true,
  userSession:          null,
  getUserSessionDetails: jest.fn(),
}));

// ── Typed mock handles ─────────────────────────────────────────

const mockGetData         = executeGetData             as jest.Mock;
const mockGetWithHeaders  = executeGetDataWithHeaders  as jest.Mock;
const mockPostWithHeaders = executePostDataWithHeaders as jest.Mock;

const TOKEN  = 'test-access-token';
const SECRET = 'qb-secret-42';

// ── Helpers ────────────────────────────────────────────────────

/**
 * Prime executeGetData (used only for credential fetches) to return
 * the given credential response body.  Defaults to the canonical shape.
 */
function mockCredential(response: any = { data: { secret_key: SECRET } }) {
  mockGetData.mockResolvedValue(response);
}

function setUserSession(session: any) {
  (UserSessionModule as any).userSession = session;
}

/** URL received by the most-recent executeGetDataWithHeaders call. */
function lastGetUrl(): string {
  const calls = mockGetWithHeaders.mock.calls;
  return calls[calls.length - 1][0];
}

/** Secret header (arg[2]) of the most-recent executeGetDataWithHeaders call. */
function lastGetHeaders(): Record<string, string> {
  const calls = mockGetWithHeaders.mock.calls;
  return calls[calls.length - 1][2];
}

/**
 * Returns [url, payload, accessToken, headers] of the most-recent
 * executePostDataWithHeaders call.
 */
function lastPost(): [string, any, string, Record<string, string>] {
  const calls = mockPostWithHeaders.mock.calls;
  const c = calls[calls.length - 1];
  return [c[0], c[1], c[2], c[3]];
}

// ── Setup ──────────────────────────────────────────────────────

beforeEach(() => {
  jest.clearAllMocks();
  setUserSession(null);
  mockCredential();           // happy-path credential by default
  // CRA's jest config sets resetMocks: true, so implementations declared in the
  // jest.mock factory are wiped before every test. With userSession null (the default
  // here) buildAuditUser awaits this fetch, so it has to be re-primed per test —
  // otherwise it returns undefined and every audited POST throws on `.then`.
  (UserSessionModule.getUserSessionDetails as jest.Mock).mockResolvedValue({});
  resetQuickBooksAuditUserCache();
});

// ══════════════════════════════════════════════════════════════
// getQuickBooksCredential
// ══════════════════════════════════════════════════════════════

describe('getQuickBooksCredential', () => {
  it('calls executeGetData with the credential URL and token', async () => {
    mockGetData.mockResolvedValue({ data: { secret_key: SECRET } });
    await getQuickBooksCredential(TOKEN);
    expect(mockGetData).toHaveBeenCalledWith(
      'https://api.test.local/v1/quickbooks/credential',
      TOKEN,
    );
  });

  it('returns whatever executeGetData returns', async () => {
    const stub = { data: { secret_key: 'x' } };
    mockGetData.mockResolvedValue(stub);
    const result = await getQuickBooksCredential(TOKEN);
    expect(result).toBe(stub);
  });
});

// ══════════════════════════════════════════════════════════════
// Secret key extraction  (getQuickBooksSecretHeader)
// Tested indirectly via any qbGet / qbPost call.
// ══════════════════════════════════════════════════════════════

describe('secret key extraction', () => {
  // All supported credential response shapes
  const shapes: [string, any][] = [
    ['data.secret_key',  { data: { secret_key: SECRET } }],
    ['data.secretKey',   { data: { secretKey:  SECRET } }],
    ['data.secret',      { data: { secret:     SECRET } }],
    ['top-level secret_key', { secret_key: SECRET }],
    ['top-level secretKey',  { secretKey:  SECRET }],
  ];

  it.each(shapes)('reads the secret from %s', async (_label, credResponse) => {
    mockCredential(credResponse);
    await getQuickBooksStatus(TOKEN, 'tid', 'sid');
    expect(lastGetHeaders()).toEqual({ 'api-secret': SECRET });
  });

  it('throws "QuickBooks secret key not found" when the credential response is empty', async () => {
    mockCredential({ data: {} });
    await expect(getQuickBooksStatus(TOKEN, 'tid', 'sid')).rejects.toThrow(
      'QuickBooks secret key not found',
    );
  });

  it('attaches the api-secret header on every executeGetDataWithHeaders call', async () => {
    await getQuickBooksStatus(TOKEN, 'tid', 'sid');
    expect(lastGetHeaders()).toEqual({ 'api-secret': SECRET });
  });

  it('attaches the api-secret header on every executePostDataWithHeaders call', async () => {
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, , , headers] = lastPost();
    expect(headers).toEqual({ 'api-secret': SECRET });
  });
});

// ══════════════════════════════════════════════════════════════
// Audit enrichment — withAudit / buildAuditUser
// ══════════════════════════════════════════════════════════════

describe('audit enrichment', () => {
  it('populates all audit fields from a full userSession', async () => {
    setUserSession({ objectId: 'obj-1', email: 'alice@example.com', role: ['admin', 'user'] });
    await initiateQuickBooksAuth(TOKEN, 'tenant-1', 'svc-1');
    const [, payload] = lastPost();

    expect(payload.object_id).toBe('obj-1');
    expect(payload.email).toBe('alice@example.com');
    expect(payload.user_role).toBe('admin,user');
    expect(payload.user_agent).toBe(navigator.userAgent);
    expect(payload.source).toBe('Web');
  });

  it('yields empty email and user_role when userSession is null and fetch resolves nothing', async () => {
    setUserSession(null);
    (UserSessionModule.getUserSessionDetails as jest.Mock).mockResolvedValue(null);
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, payload] = lastPost();
    expect(payload.email).toBeUndefined();
    expect(payload.user_role).toBe('');
  });

  it('yields empty user_role when session.role is a string, not an array', async () => {
    setUserSession({ objectId: 'u', email: 'u@t.com', role: 'admin' });
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, payload] = lastPost();
    expect(payload.user_role).toBe('');
  });

  it('preserves a caller-supplied object_id over the session objectId', async () => {
    setUserSession({ objectId: 'session-obj', email: 'a@b.com', role: [] });
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid', 'caller-obj');
    const [, payload] = lastPost();
    expect(payload.object_id).toBe('caller-obj');
  });

  it('falls back to session objectId when the caller omits object_id', async () => {
    setUserSession({ objectId: 'session-obj', email: 'a@b.com', role: [] });
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, payload] = lastPost();
    expect(payload.object_id).toBe('session-obj');
  });

  it('defaults source to "Web"', async () => {
    await startQuickBooksVendorsSync(TOKEN, { tenant_id: 'tid', service_id: 'sid' });
    const [, payload] = lastPost();
    expect(payload.source).toBe('Web');
  });

  it('preserves a caller-supplied source over the default', async () => {
    await startQuickBooksVendorsSync(TOKEN, { tenant_id: 'tid', service_id: 'sid', source: 'Mobile' });
    const [, payload] = lastPost();
    expect(payload.source).toBe('Mobile');
  });

  it('reads the live userSession on every call — no stale value', async () => {
    setUserSession({ objectId: 'first', email: 'first@t.com', role: [] });
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, first] = lastPost();
    expect(first.email).toBe('first@t.com');

    setUserSession({ objectId: 'second', email: 'second@t.com', role: [] });
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, second] = lastPost();
    expect(second.email).toBe('second@t.com');
  });
});

// ══════════════════════════════════════════════════════════════
// buildAuditUser — async fallback via getUserSessionDetails
// ══════════════════════════════════════════════════════════════

describe('buildAuditUser async fallback', () => {
  it('calls getUserSessionDetails when userSession is null', async () => {
    const mockGetSession = UserSessionModule.getUserSessionDetails as jest.Mock;
    mockGetSession.mockResolvedValue({ objectId: 'fetched', email: 'fetched@t.com', role: ['viewer'] });
    setUserSession(null);

    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, payload] = lastPost();

    expect(mockGetSession).toHaveBeenCalledWith(TOKEN);
    expect(payload.email).toBe('fetched@t.com');
    expect(payload.user_role).toBe('viewer');
  });

  it('deduplicates concurrent calls — getUserSessionDetails is called exactly once', async () => {
    const mockGetSession = UserSessionModule.getUserSessionDetails as jest.Mock;
    mockGetSession.mockResolvedValue({ objectId: 'u', email: 'u@t.com', role: [] });
    setUserSession(null);

    await Promise.all([
      initiateQuickBooksAuth(TOKEN, 'tid', 'sid'),
      initiateQuickBooksAuth(TOKEN, 'tid', 'sid'),
    ]);

    expect(mockGetSession).toHaveBeenCalledTimes(1);
  });

  it('makes a fresh fetch after the previous promise settled', async () => {
    const mockGetSession = UserSessionModule.getUserSessionDetails as jest.Mock;
    mockGetSession.mockResolvedValue({ objectId: 'u', email: 'u@t.com', role: [] });
    setUserSession(null);

    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid'); // sequential second call

    expect(mockGetSession).toHaveBeenCalledTimes(2);
  });
});

// ══════════════════════════════════════════════════════════════
// resetQuickBooksAuditUserCache
// ══════════════════════════════════════════════════════════════

describe('resetQuickBooksAuditUserCache', () => {
  it('does not throw', () => {
    expect(() => resetQuickBooksAuditUserCache()).not.toThrow();
  });

  it('subsequent calls still read the live userSession', async () => {
    setUserSession({ objectId: 'a', email: 'first@test.com', role: [] });
    resetQuickBooksAuditUserCache();
    setUserSession({ objectId: 'b', email: 'second@test.com', role: [] });

    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, payload] = lastPost();
    expect(payload.email).toBe('second@test.com');
  });
});

// ══════════════════════════════════════════════════════════════
// GET endpoints — URL routing
// ══════════════════════════════════════════════════════════════

describe('getQuickBooksStatus', () => {
  it('hits the status endpoint on extservice_base_url', async () => {
    await getQuickBooksStatus(TOKEN, 'ten-1', 'svc-1');
    expect(lastGetUrl()).toContain('https://qb.test.local/extsvc/api/v1/quickbooks/status');
  });

  it('includes tenantId and serviceId', async () => {
    await getQuickBooksStatus(TOKEN, 'ten-1', 'svc-1');
    const url = lastGetUrl();
    expect(url).toContain('tenantId=ten-1');
    expect(url).toContain('serviceId=svc-1');
  });

  it('appends object_id when provided', async () => {
    await getQuickBooksStatus(TOKEN, 'ten-1', 'svc-1', 'obj-1');
    expect(lastGetUrl()).toContain('object_id=obj-1');
  });

  it('omits object_id when not provided', async () => {
    await getQuickBooksStatus(TOKEN, 'ten-1', 'svc-1');
    expect(lastGetUrl()).not.toContain('object_id');
  });

  it('passes the access token to executeGetDataWithHeaders', async () => {
    await getQuickBooksStatus(TOKEN, 'ten-1', 'svc-1');
    expect(mockGetWithHeaders).toHaveBeenCalledWith(
      expect.any(String), TOKEN, expect.any(Object),
    );
  });
});

describe('QuickBooks auto sync config', () => {
  it('loads the tenant auto sync setting', async () => {
    await getQuickBooksAutoSyncConfig(TOKEN, 'ten-1', 'svc-1');
    const url = lastGetUrl();
    expect(url).toContain('/extsvc/api/v1/quickbooks/auto-sync-config');
    expect(url).toContain('tenantId=ten-1');
    expect(url).toContain('serviceId=svc-1');
  });

  it('saves the tenant auto sync setting with audit enrichment', async () => {
    setUserSession({ objectId: 'obj-1', email: 'admin@test.com', role: ['Admin'] });

    await saveQuickBooksAutoSyncConfig(TOKEN, {
      tenant_id: 'ten-1',
      service_id: 'svc-1',
      enabled: false,
      object_id: 'obj-1',
    });

    const [url, payload] = lastPost();
    expect(url).toContain('/extsvc/api/v1/quickbooks/auto-sync-config');
    expect(payload).toMatchObject({
      tenant_id: 'ten-1',
      service_id: 'svc-1',
      enabled: false,
      object_id: 'obj-1',
      email: 'admin@test.com',
      user_role: 'Admin',
    });
  });
});

describe('getQuickBooksSyncState', () => {
  it('builds the URL with all three required params', async () => {
    await getQuickBooksSyncState(TOKEN, 'tid', 'sid', 'oid');
    const url = lastGetUrl();
    expect(url).toContain('/extsvc/api/v1/quickbooks/sync/state');
    expect(url).toContain('tenantId=tid');
    expect(url).toContain('serviceId=sid');
    expect(url).toContain('objectId=oid');
  });
});

describe('getQuickBooksSyncJob', () => {
  it('builds the URL with tenantId and numeric jobId', async () => {
    await getQuickBooksSyncJob(TOKEN, 'tid', 42);
    const url = lastGetUrl();
    expect(url).toContain('/extsvc/api/v1/quickbooks/sync/job');
    expect(url).toContain('tenantId=tid');
    expect(url).toContain('jobId=42');
  });
});

// ══════════════════════════════════════════════════════════════
// CDC views — query parameter construction
// ══════════════════════════════════════════════════════════════

describe('getQuickBooksVendorCdcView', () => {
  const BASE = { tenantId: 'tid', serviceId: 'sid' };

  it('hits the vendors CDC endpoint', async () => {
    await getQuickBooksVendorCdcView(TOKEN, BASE);
    expect(lastGetUrl()).toContain('/extsvc/api/v1/quickbooks/vendors/cdc-view');
  });

  it('defaults page to 1 and pageSize to 25', async () => {
    await getQuickBooksVendorCdcView(TOKEN, BASE);
    const url = lastGetUrl();
    expect(url).toContain('page=1');
    expect(url).toContain('pageSize=25');
  });

  it('uses caller-supplied page and pageSize', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, page: 3, pageSize: 10 });
    const url = lastGetUrl();
    expect(url).toContain('page=3');
    expect(url).toContain('pageSize=10');
  });

  it('appends search when provided', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, search: 'acme' });
    expect(lastGetUrl()).toContain('search=acme');
  });

  it('omits search when empty string', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, search: '' });
    expect(lastGetUrl()).not.toContain('search');
  });

  it('appends syncStatus when not "ALL"', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, syncStatus: 'FAILED' });
    expect(lastGetUrl()).toContain('syncStatus=FAILED');
  });

  it('appends syncStatus=IMPORT_FAILED', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, syncStatus: 'IMPORT_FAILED' });
    expect(lastGetUrl()).toContain('syncStatus=IMPORT_FAILED');
  });

  it('omits syncStatus when "ALL"', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, syncStatus: 'ALL' });
    expect(lastGetUrl()).not.toContain('syncStatus');
  });

  it('omits syncStatus when not provided', async () => {
    await getQuickBooksVendorCdcView(TOKEN, BASE);
    expect(lastGetUrl()).not.toContain('syncStatus');
  });

  it('appends mappedOnly=true when set', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, mappedOnly: true });
    expect(lastGetUrl()).toContain('mappedOnly=true');
  });

  it('omits mappedOnly when false', async () => {
    await getQuickBooksVendorCdcView(TOKEN, { ...BASE, mappedOnly: false });
    expect(lastGetUrl()).not.toContain('mappedOnly');
  });
});

describe('getQuickBooksAccountCdcView', () => {
  const BASE = { tenantId: 'tid', serviceId: 'sid' };

  it('hits the accounts CDC endpoint with defaults', async () => {
    await getQuickBooksAccountCdcView(TOKEN, BASE);
    const url = lastGetUrl();
    expect(url).toContain('/extsvc/api/v1/quickbooks/accounts/cdc-view');
    expect(url).toContain('page=1');
    expect(url).toContain('pageSize=25');
  });

  it('appends syncStatus when not "ALL"', async () => {
    await getQuickBooksAccountCdcView(TOKEN, { ...BASE, syncStatus: 'SYNCED' });
    expect(lastGetUrl()).toContain('syncStatus=SYNCED');
  });

  it('omits syncStatus when "ALL"', async () => {
    await getQuickBooksAccountCdcView(TOKEN, { ...BASE, syncStatus: 'ALL' });
    expect(lastGetUrl()).not.toContain('syncStatus');
  });

  it('appends search when provided', async () => {
    await getQuickBooksAccountCdcView(TOKEN, { ...BASE, search: 'bank' });
    expect(lastGetUrl()).toContain('search=bank');
  });

  it('omits search when not provided', async () => {
    await getQuickBooksAccountCdcView(TOKEN, BASE);
    expect(lastGetUrl()).not.toContain('search');
  });
});

describe('getQuickBooksBillCdcView', () => {
  const BASE = { tenantId: 'tid', serviceId: 'sid' };

  it('hits the bills CDC endpoint', async () => {
    await getQuickBooksBillCdcView(TOKEN, BASE);
    expect(lastGetUrl()).toContain('/extsvc/api/v1/quickbooks/bills/cdc-view');
  });

  it('appends syncStatus=IMPORT_FAILED', async () => {
    await getQuickBooksBillCdcView(TOKEN, { ...BASE, syncStatus: 'IMPORT_FAILED' });
    expect(lastGetUrl()).toContain('syncStatus=IMPORT_FAILED');
  });

  it('appends syncStatus=FAILED', async () => {
    await getQuickBooksBillCdcView(TOKEN, { ...BASE, syncStatus: 'FAILED' });
    expect(lastGetUrl()).toContain('syncStatus=FAILED');
  });

  it('omits syncStatus when "ALL"', async () => {
    await getQuickBooksBillCdcView(TOKEN, { ...BASE, syncStatus: 'ALL' });
    expect(lastGetUrl()).not.toContain('syncStatus');
  });

  it('appends mappedOnly=true when set', async () => {
    await getQuickBooksBillCdcView(TOKEN, { ...BASE, mappedOnly: true });
    expect(lastGetUrl()).toContain('mappedOnly=true');
  });

  it('omits mappedOnly when false', async () => {
    await getQuickBooksBillCdcView(TOKEN, { ...BASE, mappedOnly: false });
    expect(lastGetUrl()).not.toContain('mappedOnly');
  });
});

// ══════════════════════════════════════════════════════════════
// POST endpoints — URL routing and payload integrity
// ══════════════════════════════════════════════════════════════

describe('initiateQuickBooksAuth', () => {
  it('posts to the correct endpoint', async () => {
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [url] = lastPost();
    expect(url).toBe('https://qb.test.local/extsvc/api/v1/quickbooksAuth');
  });

  it('includes tenant_id and service_id in the payload', async () => {
    await initiateQuickBooksAuth(TOKEN, 'tenant-abc', 'svc-xyz');
    const [, payload] = lastPost();
    expect(payload.tenant_id).toBe('tenant-abc');
    expect(payload.service_id).toBe('svc-xyz');
  });

  it('passes the access token to executePostDataWithHeaders', async () => {
    await initiateQuickBooksAuth(TOKEN, 'tid', 'sid');
    const [, , token] = lastPost();
    expect(token).toBe(TOKEN);
  });
});

describe('disconnectQuickBooks', () => {
  it('posts to the disconnect endpoint', async () => {
    await disconnectQuickBooks(TOKEN, 'tid', 'sid');
    const [url] = lastPost();
    expect(url).toBe('https://qb.test.local/extsvc/api/v1/quickbooks/disconnect');
  });

  it('payload contains tenant_id, service_id, and audit fields', async () => {
    setUserSession({ objectId: 'u1', email: 'u@t.com', role: ['user'] });
    await disconnectQuickBooks(TOKEN, 'tid', 'sid');
    const [, payload] = lastPost();
    expect(payload.tenant_id).toBe('tid');
    expect(payload.service_id).toBe('sid');
    expect(payload.email).toBe('u@t.com');
  });
});

describe('POST sync endpoints — URL routing', () => {
  const req = { tenant_id: 'tid', service_id: 'sid' };

  const endpoints: [string, (t: any, r: any) => Promise<any>, string][] = [
    ['startQuickBooksVendorsSync',         startQuickBooksVendorsSync,         '/extsvc/api/v1/quickbooks/vendors/sync'],
    ['exportQuickBooksPayments',           exportQuickBooksPayments,           '/extsvc/api/v1/quickbooks/payments/export'],
    ['importQuickBooksPayments',           importQuickBooksPayments,           '/extsvc/api/v1/quickbooks/payments/import'],
    ['startQuickBooksPaymentsSync',        startQuickBooksPaymentsSync,        '/extsvc/api/v1/quickbooks/payments/sync'],
    ['startQuickBooksChartOfAccountsSync', startQuickBooksChartOfAccountsSync, '/extsvc/api/v1/quickbooks/accounts/sync'],
    ['startQuickBooksBillsSync',           startQuickBooksBillsSync,           '/extsvc/api/v1/quickbooks/bills/sync'],
  ];

  it.each(endpoints)('%s posts to the correct endpoint', async (_name, fn, path) => {
    await fn(TOKEN, req);
    const [url] = lastPost();
    expect(url).toBe(`https://qb.test.local${path}`);
  });

  it('every sync POST includes audit fields from userSession', async () => {
    setUserSession({ objectId: 'u1', email: 'u@t.com', role: ['manager'] });
    await startQuickBooksVendorsSync(TOKEN, req);
    const [, payload] = lastPost();
    expect(payload.email).toBe('u@t.com');
    expect(payload.user_role).toBe('manager');
    expect(payload.source).toBe('Web');
  });

  it('original request fields survive audit enrichment', async () => {
    await startQuickBooksVendorsSync(TOKEN, { ...req, sync_mode: 'full' });
    const [, payload] = lastPost();
    expect(payload.sync_mode).toBe('full');
    expect(payload.tenant_id).toBe('tid');
    expect(payload.service_id).toBe('sid');
  });
});

// ══════════════════════════════════════════════════════════════
// Error paths
// ══════════════════════════════════════════════════════════════

describe('error paths', () => {
  it('throws "QuickBooks secret key not found" when credential response has no secret', async () => {
    mockCredential({ data: {} });
    await expect(getQuickBooksStatus(TOKEN, 'tid', 'sid')).rejects.toThrow(
      'QuickBooks secret key not found',
    );
  });

  it('propagates a credential fetch rejection to GET callers', async () => {
    mockGetData.mockRejectedValue(new Error('credential fetch failed'));
    await expect(getQuickBooksStatus(TOKEN, 'tid', 'sid')).rejects.toThrow(
      'credential fetch failed',
    );
  });

  it('propagates a credential fetch rejection to POST callers', async () => {
    mockGetData.mockRejectedValue(new Error('credential fetch failed'));
    await expect(initiateQuickBooksAuth(TOKEN, 'tid', 'sid')).rejects.toThrow(
      'credential fetch failed',
    );
  });

  it('propagates executeGetDataWithHeaders rejection to the caller', async () => {
    mockGetWithHeaders.mockRejectedValueOnce(new Error('network error'));
    await expect(getQuickBooksStatus(TOKEN, 'tid', 'sid')).rejects.toThrow('network error');
  });

  it('propagates executePostDataWithHeaders rejection to the caller', async () => {
    mockPostWithHeaders.mockRejectedValueOnce(new Error('server 500'));
    await expect(initiateQuickBooksAuth(TOKEN, 'tid', 'sid')).rejects.toThrow('server 500');
  });

  it('does not call executeGetDataWithHeaders when the credential secret is missing', async () => {
    mockCredential({ data: {} });
    await expect(getQuickBooksStatus(TOKEN, 'tid', 'sid')).rejects.toThrow();
    expect(mockGetWithHeaders).not.toHaveBeenCalled();
  });

  it('does not call executePostDataWithHeaders when the credential secret is missing', async () => {
    mockCredential({ data: {} });
    await expect(initiateQuickBooksAuth(TOKEN, 'tid', 'sid')).rejects.toThrow();
    expect(mockPostWithHeaders).not.toHaveBeenCalled();
  });
});

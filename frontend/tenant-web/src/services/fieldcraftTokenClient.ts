/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 *
 * fieldcraftTokenClient
 * --------------
 * Tiny in-memory cache for short-lived FIELDCRAFT service credentials.
 *
 * The Java tenant API mints per-kind credentials (recommendation engine,
 * Ball Park Estimate engine, vLLM inference) for the signed-in user. Each
 * credential carries an opaque `token` (X-FIELDCRAFT-Token) that the downstream
 * Python services decrypt and validate with their own configured secret. The
 * signing secret is never sent to the browser. Credentials expire
 * (`ttlSeconds`); we refresh on TTL expiry or on a 401 from the downstream
 * service via `invalidateFieldCraftCredential`.
 *
 * Access-token retrieval matches the rest of the SPA: components grab the
 * B2C token from `useOidcAccessToken()` and pass it down. Since this module
 * runs outside React, we read it through the non-hook handle that
 * @axa-fr/react-oidc exposes for the same session: `OidcClient.get().tokens`.
 */
import { OidcClient } from "@axa-fr/react-oidc";
import { ConfigUrl } from "../auth/OidcConfig";

export type FieldCraftCredentialKind = "recommendation" | "ballpark" | "vllm";

export interface FieldCraftCredential {
  token: string;       // X-FIELDCRAFT-Token (opaque)
  exp: number;         // unix seconds — absolute expiry
}

const cache: Partial<Record<FieldCraftCredentialKind, FieldCraftCredential>> = {};

/** Resolve the B2C access token the same way the rest of the SPA does. */
function getAccessToken(): string {
  try {
    const client = OidcClient.get();
    const token = client?.tokens?.accessToken;
    if (token) return token;
  } catch {
    // OidcClient not yet initialised — fall through to error below.
  }
  throw new Error("fieldcraftTokenClient: no B2C access token available");
}

/**
 * Fetch (or reuse) a credential for the given FIELDCRAFT service. Credentials are
 * cached in-memory and refreshed 30 s before expiry.
 */
export async function getFieldCraftCredential(
  kind: FieldCraftCredentialKind
): Promise<FieldCraftCredential> {
  const now = Math.floor(Date.now() / 1000);
  const hit = cache[kind];
  if (hit && hit.exp - 30 > now) return hit;

  const accessToken = getAccessToken();
  const res = await fetch(`${ConfigUrl.apiUrl}/v1/${kind}/credential`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    throw new Error(`fieldcraft credential fetch failed: ${res.status}`);
  }
  const body = (await res.json()) as {
    token: string;
    ttlSeconds: number | string;
  };
  const entry: FieldCraftCredential = {
    token: body.token,
    exp: now + Number(body.ttlSeconds),
  };
  cache[kind] = entry;
  return entry;
}

/** Drop the cached credential — call after a 401 from the downstream service. */
export function invalidateFieldCraftCredential(kind: FieldCraftCredentialKind): void {
  delete cache[kind];
}

/** Build the per-request auth headers for a given service kind. */
export async function fieldcraftAuthHeaders(
  kind: FieldCraftCredentialKind
): Promise<Record<string, string>> {
  const cred = await getFieldCraftCredential(kind);
  return {
    Authorization: `Bearer ${getAccessToken()}`,
    "X-FIELDCRAFT-Token": cred.token,
  };
}

/**
 * Bearer-only headers (no X-FIELDCRAFT-Token). Dev-resilience fallback for when the FIELDCRAFT
 * credential mint (Java /vN/<kind>/credential) is unavailable: still lets the
 * request reach the backend with the B2C bearer instead of being silently
 * dropped. A hardened backend that requires X-FIELDCRAFT-Token will reject these with
 * 401 (handled by the caller), so this never weakens production.
 */
export function bearerOnlyHeaders(): Record<string, string> {
  return { Authorization: `Bearer ${getAccessToken()}` };
}

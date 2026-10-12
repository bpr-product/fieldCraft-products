/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 */

export const QUICKBOOKS_OAUTH_RETURN_ROUTE = "/quickbooks/oauth-return";
export const QUICKBOOKS_OAUTH_CALLBACK_TYPE = "FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK";

export type QuickBooksOAuthProvider = "accounting";
export type QuickBooksOAuthResult =
  | "success"
  | "denied"
  | "incomplete"
  | "invalid_state"
  | "reconnect"
  | "failed";

export interface QuickBooksOAuthCallbackMessage {
  type: typeof QUICKBOOKS_OAUTH_CALLBACK_TYPE;
  provider: QuickBooksOAuthProvider;
  result: QuickBooksOAuthResult;
}

export const isQuickBooksOAuthProvider = (
  value: string | null,
): value is QuickBooksOAuthProvider => value === "accounting";

export const isQuickBooksOAuthResult = (
  value: string | null,
): value is QuickBooksOAuthResult =>
  value === "success" ||
  value === "denied" ||
  value === "incomplete" ||
  value === "invalid_state" ||
  value === "reconnect" ||
  value === "failed";

export const quickBooksOAuthErrorMessage = (
  provider: QuickBooksOAuthProvider,
  result: QuickBooksOAuthResult,
): string | null => {
  if (result === "success") return null;

  const messages: Record<Exclude<QuickBooksOAuthResult, "success">, string> = {
    denied: "QuickBooks authorization was cancelled or denied.",
    incomplete: "QuickBooks returned an incomplete authorization response. Please try again.",
    invalid_state: "QuickBooks sign-in expired or could not be verified. Please start again.",
    reconnect: "QuickBooks requires a new authorization. Please sign in again.",
    failed: "QuickBooks connection failed. Please try again.",
  };
  return messages[result];
};

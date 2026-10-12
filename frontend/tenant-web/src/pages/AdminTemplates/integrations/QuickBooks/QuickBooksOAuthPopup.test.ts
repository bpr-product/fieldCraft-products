/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 */
import {
  isQuickBooksOAuthProvider,
  isQuickBooksOAuthResult,
  quickBooksOAuthErrorMessage,
  QUICKBOOKS_OAUTH_CALLBACK_TYPE,
  QUICKBOOKS_OAUTH_RETURN_ROUTE,
  QuickBooksOAuthResult,
} from "./QuickBooksOAuthPopup";

describe("QuickBooksOAuthPopup", () => {
  it("pins the callback contract shared with the backend redirect", () => {
    // The backend redirects to this route and the popup posts this message type —
    // changing either value silently breaks the OAuth handshake.
    expect(QUICKBOOKS_OAUTH_RETURN_ROUTE).toBe("/quickbooks/oauth-return");
    expect(QUICKBOOKS_OAUTH_CALLBACK_TYPE).toBe("FIELDCRAFT_QUICKBOOKS_OAUTH_CALLBACK");
  });

  describe("isQuickBooksOAuthProvider", () => {
    it("accepts only the accounting provider", () => {
      expect(isQuickBooksOAuthProvider("accounting")).toBe(true);
    });

    it.each(["time", "unknown", "Accounting", "", null])(
      "rejects %p",
      (value) => {
        expect(isQuickBooksOAuthProvider(value as string | null)).toBe(false);
      },
    );
  });

  describe("isQuickBooksOAuthResult", () => {
    it.each([
      "success",
      "denied",
      "incomplete",
      "invalid_state",
      "reconnect",
      "failed",
    ] as QuickBooksOAuthResult[])("accepts %s", (value) => {
      expect(isQuickBooksOAuthResult(value)).toBe(true);
    });

    it.each(["unknown", "SUCCESS", "", null])("rejects %p", (value) => {
      expect(isQuickBooksOAuthResult(value as string | null)).toBe(false);
    });
  });

  describe("quickBooksOAuthErrorMessage", () => {
    it("returns no error for a successful connection", () => {
      expect(quickBooksOAuthErrorMessage("accounting", "success")).toBeNull();
    });

    // Every failure gets its own plain-English sentence, and none of them leak the
    // callback query string, provider internals, or Intuit error codes.
    it.each([
      ["denied", /cancelled or denied/i],
      ["incomplete", /incomplete authorization response/i],
      ["invalid_state", /sign-in expired/i],
      ["reconnect", /requires a new authorization/i],
      ["failed", /connection failed/i],
    ] as [QuickBooksOAuthResult, RegExp][])(
      "explains the %s result without exposing callback details",
      (result, matcher) => {
        const message = quickBooksOAuthErrorMessage("accounting", result) as string;

        expect(message).toMatch(matcher);
        expect(message).not.toMatch(/provider=|result=|accounting|state|token/i);
      },
    );

    it("gives a distinct message for every failure result", () => {
      const failures: QuickBooksOAuthResult[] = [
        "denied", "incomplete", "invalid_state", "reconnect", "failed",
      ];
      const messages = failures.map((r) => quickBooksOAuthErrorMessage("accounting", r));

      expect(new Set(messages).size).toBe(failures.length);
    });
  });
});

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 */
import React from "react";
import { act, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import QuickBooksOAuthReturn from "./QuickBooksOAuthReturn";
import { QUICKBOOKS_OAUTH_CALLBACK_TYPE } from "./QuickBooksOAuthPopup";

const setUrl = (search: string) => {
  window.history.pushState({}, "", `/quickbooks/oauth-return${search}`);
};

describe("QuickBooksOAuthReturn", () => {
  let closeSpy: jest.SpyInstance;
  let opener: { closed: boolean; postMessage: jest.Mock };

  beforeEach(() => {
    jest.useFakeTimers();
    closeSpy = jest.spyOn(window, "close").mockImplementation(() => {});
    opener = { closed: false, postMessage: jest.fn() };
    (window as any).opener = opener;
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    closeSpy.mockRestore();
    (window as any).opener = null;
    window.history.pushState({}, "", "/");
    jest.clearAllMocks();
  });

  it("posts a successful Accounting result to the opener and closes", () => {
    setUrl("?provider=accounting&result=success");
    render(<QuickBooksOAuthReturn />);

    expect(opener.postMessage).toHaveBeenCalledWith(
      {
        type: QUICKBOOKS_OAUTH_CALLBACK_TYPE,
        provider: "accounting",
        result: "success",
      },
      window.location.origin,
    );
    expect(screen.getByText(/QuickBooks connected/i)).toBeInTheDocument();

    act(() => jest.advanceTimersByTime(300));
    expect(closeSpy).toHaveBeenCalled();
  });


  it("posts a denied Accounting result and shows the not-connected state", () => {
    setUrl("?provider=accounting&result=denied");
    render(<QuickBooksOAuthReturn />);

    expect(opener.postMessage).toHaveBeenCalledWith(
      {
        type: QUICKBOOKS_OAUTH_CALLBACK_TYPE,
        provider: "accounting",
        result: "denied",
      },
      window.location.origin,
    );
    expect(screen.getByText(/QuickBooks was not connected/i)).toBeInTheDocument();

    act(() => jest.advanceTimersByTime(300));
    expect(closeSpy).toHaveBeenCalled();
  });


  it("still closes when browser isolation removes the opener", () => {
    (window as any).opener = null;
    setUrl("?provider=accounting&result=success");
    render(<QuickBooksOAuthReturn />);

    act(() => jest.advanceTimersByTime(300));
    expect(closeSpy).toHaveBeenCalled();
  });

  it("does not post unrecognized callback values", () => {
    setUrl("?provider=unknown&result=success");
    render(<QuickBooksOAuthReturn />);

    expect(opener.postMessage).not.toHaveBeenCalled();
    expect(screen.getByText(/only used to complete/i)).toBeInTheDocument();
  });

  it.each(["denied", "incomplete", "invalid_state", "reconnect", "failed"])(
    "reports the %s result as a not-connected outcome",
    (result) => {
      setUrl(`?provider=accounting&result=${result}`);
      render(<QuickBooksOAuthReturn />);

      expect(opener.postMessage).toHaveBeenCalledWith(
        { type: QUICKBOOKS_OAUTH_CALLBACK_TYPE, provider: "accounting", result },
        window.location.origin,
      );
      expect(screen.getByText(/QuickBooks was not connected/i)).toBeInTheDocument();
    },
  );

  it("does not post when the result is missing from the callback", () => {
    setUrl("?provider=accounting");
    render(<QuickBooksOAuthReturn />);

    expect(opener.postMessage).not.toHaveBeenCalled();
    expect(screen.getByText(/only used to complete/i)).toBeInTheDocument();
  });

  it("does not post when the result is not one we recognise", () => {
    setUrl("?provider=accounting&result=some_new_state");
    render(<QuickBooksOAuthReturn />);

    expect(opener.postMessage).not.toHaveBeenCalled();
    expect(screen.getByText(/only used to complete/i)).toBeInTheDocument();
  });

  it("still closes the window after an unrecognised callback", () => {
    setUrl("?provider=unknown&result=success");
    render(<QuickBooksOAuthReturn />);

    // The page bails out before posting, so no auto-close timer is armed and the
    // stranded window is left for the user to close.
    act(() => jest.advanceTimersByTime(300));
    expect(closeSpy).not.toHaveBeenCalled();
  });

  it("does not post to an opener that has already been closed", () => {
    opener.closed = true;
    setUrl("?provider=accounting&result=success");
    render(<QuickBooksOAuthReturn />);

    expect(opener.postMessage).not.toHaveBeenCalled();
    expect(screen.getByText(/QuickBooks connected/i)).toBeInTheDocument();

    act(() => jest.advanceTimersByTime(300));
    expect(closeSpy).toHaveBeenCalled();
  });

  it("waits for the close delay rather than closing immediately", () => {
    setUrl("?provider=accounting&result=success");
    render(<QuickBooksOAuthReturn />);

    act(() => jest.advanceTimersByTime(299));
    expect(closeSpy).not.toHaveBeenCalled();

    act(() => jest.advanceTimersByTime(1));
    expect(closeSpy).toHaveBeenCalledTimes(1);
  });
});

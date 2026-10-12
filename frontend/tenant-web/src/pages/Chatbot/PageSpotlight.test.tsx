/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose      : Unit tests for PageSpotlight -- EDEN's on-page highlight ring.
*/
import React from "react";
import { render, screen, act, fireEvent, cleanup } from "@testing-library/react";
import "@testing-library/jest-dom";
import PageSpotlight, { SpotlightTarget } from "./PageSpotlight";

describe("PageSpotlight", () => {
  let rafCallbacks: FrameRequestCallback[];
  let rafId: number;

  beforeEach(() => {
    jest.useFakeTimers();
    rafCallbacks = [];
    rafId = 0;
    // jsdom's requestAnimationFrame is throttled/async in a way that doesn't
    // play well with fake timers -- stub it so `track()`'s per-frame loop is
    // driven manually via flushRaf() below, deterministic under fake timers.
    jest.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafCallbacks.push(cb);
      return ++rafId;
    });
    jest.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    Element.prototype.scrollIntoView = jest.fn();
  });

  afterEach(() => {
    // Unmount RTL's rendered tree (and its portals) BEFORE wiping body
    // content -- otherwise RTL's own auto-cleanup runs afterward against
    // nodes we already removed and throws "not a child of this node".
    cleanup();
    jest.useRealTimers();
    jest.restoreAllMocks();
    document.body.innerHTML = "";
  });

  const flushRaf = () => {
    const pending = rafCallbacks.splice(0, rafCallbacks.length);
    pending.forEach((cb) => cb(0));
  };

  it("renders nothing when target is null", () => {
    const { container } = render(
      <PageSpotlight target={null} onDismiss={jest.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
    expect(document.querySelector('[role="status"]')).not.toBeInTheDocument();
  });

  it("locates a matching visible button by its text, scrolls it into view, and rings it", () => {
    const button = document.createElement("button");
    button.textContent = "Export Invoices";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 100, left: 50, width: 120, height: 30 }),
    });
    document.body.appendChild(button);

    const target: SpotlightTarget = {
      label: "Export Invoices button",
      targets: ["Export Invoices"],
    };

    render(<PageSpotlight target={target} onDismiss={jest.fn()} />);

    act(() => {
      flushRaf();
    });

    expect(button.scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "center",
      inline: "center",
    });
    expect(screen.getByRole("status")).toHaveTextContent("Export Invoices button");
  });

  it("clicks the located element when autoClick is set (action request, not a lookup)", () => {
    const button = document.createElement("button");
    button.textContent = "Add New Material";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 100, left: 50, width: 120, height: 30 }),
    });
    const onClick = jest.fn();
    button.addEventListener("click", onClick);
    document.body.appendChild(button);

    const target: SpotlightTarget = {
      label: "Add New Material button",
      targets: ["Add New Material"],
      autoClick: true,
    };

    render(<PageSpotlight target={target} onDismiss={jest.fn()} />);

    act(() => {
      flushRaf();
    });
    expect(onClick).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(400);
    });
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("never clicks the located element when autoClick is unset (a plain lookup/answer)", () => {
    const button = document.createElement("button");
    button.textContent = "Export Invoices";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 100, left: 50, width: 120, height: 30 }),
    });
    const onClick = jest.fn();
    button.addEventListener("click", onClick);
    document.body.appendChild(button);

    const target: SpotlightTarget = {
      label: "Export Invoices button",
      targets: ["Export Invoices"],
    };

    render(<PageSpotlight target={target} onDismiss={jest.fn()} />);

    act(() => {
      flushRaf();
      jest.advanceTimersByTime(1000);
    });
    expect(onClick).not.toHaveBeenCalled();
  });

  it("fires mousedown, not just click, so MUI Selects actually open", () => {
    // Real bug, fixed: MUI's Select opens from onMouseDown (and ignores
    // anything but button 0) and has no onClick at all, so el.click()
    // highlighted the dropdown and did nothing visible.
    const combobox = document.createElement("div");
    combobox.setAttribute("role", "combobox");
    combobox.textContent = "Material Fields";
    Object.defineProperty(combobox, "getBoundingClientRect", {
      value: () => ({ top: 100, left: 50, width: 120, height: 30 }),
    });
    const onMouseDown = jest.fn();
    combobox.addEventListener("mousedown", onMouseDown);
    document.body.appendChild(combobox);

    render(
      <PageSpotlight
        target={{
          label: "Material Fields dropdown",
          targets: ["Material Fields"],
          autoClick: true,
        }}
        onDismiss={jest.fn()}
      />
    );

    act(() => {
      flushRaf();
      jest.advanceTimersByTime(400);
    });

    expect(onMouseDown).toHaveBeenCalledTimes(1);
    expect(onMouseDown.mock.calls[0][0].button).toBe(0);
  });

  it("finds a bare MenuItem option and prefers an exact match over a substring", () => {
    // Real bug, fixed: Trade/Grade render options as <MenuItem>{value}</MenuItem>
    // -- a bare <li role="option">, with no ListItemText/Typography wrapper --
    // so they matched nothing. And a one-character option ("a") substring-matches
    // almost any text on the page, so an exact hit has to win.
    const decoy = document.createElement("td");
    decoy.textContent = "a"; // same length, but not an option
    Object.defineProperty(decoy, "getBoundingClientRect", {
      value: () => ({ top: 10, left: 10, width: 50, height: 20 }),
    });
    const option = document.createElement("li");
    option.setAttribute("role", "option");
    option.textContent = "a";
    Object.defineProperty(option, "getBoundingClientRect", {
      value: () => ({ top: 300, left: 60, width: 90, height: 24 }),
    });
    const longer = document.createElement("li");
    longer.setAttribute("role", "option");
    longer.textContent = "Grade a (premium)";
    Object.defineProperty(longer, "getBoundingClientRect", {
      value: () => ({ top: 330, left: 60, width: 90, height: 24 }),
    });
    document.body.append(decoy, longer, option);

    render(
      <PageSpotlight
        target={{ label: "Grade filter", targets: ["a"] }}
        onDismiss={jest.fn()}
      />
    );
    act(() => {
      flushRaf();
    });

    const scrollMock = Element.prototype.scrollIntoView as jest.Mock;
    expect(scrollMock).toHaveBeenCalledTimes(1);
    // exact-match "a" wins; never the longer option
    expect(scrollMock.mock.instances[0]).not.toBe(longer);
  });

  it("clicks the control an InputLabel is aria-labelledby'd to, not the inert label text itself", () => {
    // Real bug, fixed: MUI's InputLabel ("Material Fields", "Trade",
    // "Grade"...) is plain text with no `for`/click handler -- clicking
    // it does nothing. MUI wires it to its actual control via
    // aria-labelledby, not `for`.
    const label = document.createElement("label");
    label.id = "ml-columns-label";
    label.textContent = "Material Fields";
    Object.defineProperty(label, "getBoundingClientRect", {
      value: () => ({ top: 100, left: 50, width: 120, height: 20 }),
    });
    const combobox = document.createElement("div");
    combobox.setAttribute("role", "combobox");
    combobox.setAttribute("aria-labelledby", "ml-columns-label");
    const onClick = jest.fn();
    combobox.addEventListener("click", onClick);
    Object.defineProperty(combobox, "getBoundingClientRect", {
      value: () => ({ top: 130, left: 50, width: 200, height: 30 }),
    });
    document.body.append(label, combobox);

    const target: SpotlightTarget = {
      label: "Material Fields dropdown",
      targets: ["Material Fields"],
      autoClick: true,
    };

    render(<PageSpotlight target={target} onDismiss={jest.fn()} />);

    act(() => {
      flushRaf();
      jest.advanceTimersByTime(400);
    });
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("rings a second element that only appears after autoClick's click, using postClickTargets", () => {
    const dropdown = document.createElement("div");
    dropdown.setAttribute("role", "combobox");
    dropdown.textContent = "Material Fields";
    Object.defineProperty(dropdown, "getBoundingClientRect", {
      value: () => ({ top: 100, left: 50, width: 120, height: 30 }),
    });
    document.body.appendChild(dropdown);

    const target: SpotlightTarget = {
      label: "Material Fields dropdown",
      targets: ["Material Fields"],
      autoClick: true,
      postClickTargets: ["Selling Price"],
    };

    render(<PageSpotlight target={target} onDismiss={jest.fn()} />);

    act(() => {
      flushRaf();
    });
    const scrollMock = Element.prototype.scrollIntoView as jest.Mock;
    expect(scrollMock).toHaveBeenCalledTimes(1);
    expect(scrollMock.mock.instances[0]).toBe(dropdown);

    // The click "opens the menu" -- only now does the checkbox exist.
    act(() => {
      jest.advanceTimersByTime(400);
    });
    const checkbox = document.createElement("label");
    checkbox.textContent = "Selling Price";
    Object.defineProperty(checkbox, "getBoundingClientRect", {
      value: () => ({ top: 200, left: 50, width: 80, height: 20 }),
    });
    document.body.appendChild(checkbox);

    act(() => {
      jest.advanceTimersByTime(250);
    });

    expect(scrollMock).toHaveBeenCalledTimes(2);
    expect(scrollMock.mock.instances[1]).toBe(checkbox);
  });

  it("prefers the more specific (shortest matching text) element over a broader container", () => {
    const container = document.createElement("div");
    container.textContent = "Export Invoices panel with lots of extra text";
    Object.defineProperty(container, "getBoundingClientRect", {
      value: () => ({ top: 10, left: 10, width: 400, height: 200 }),
    });
    // Not in the selector list (plain div), so only the button below is a candidate.
    const button = document.createElement("button");
    button.textContent = "Export Invoices";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 100, left: 50, width: 120, height: 30 }),
    });
    container.appendChild(button);
    document.body.appendChild(container);

    const target: SpotlightTarget = {
      label: "Export Invoices button",
      targets: ["Export Invoices"],
    };
    render(<PageSpotlight target={target} onDismiss={jest.fn()} />);

    act(() => {
      flushRaf();
    });

    expect(button.scrollIntoView).toHaveBeenCalled();
  });

  it("never matches text inside EDEN's own chat panel", () => {
    const chatPopup = document.createElement("div");
    chatPopup.className = "chatPopup";
    const insideChat = document.createElement("button");
    insideChat.textContent = "Invoice #4021";
    Object.defineProperty(insideChat, "getBoundingClientRect", {
      value: () => ({ top: 10, left: 10, width: 100, height: 20 }),
    });
    chatPopup.appendChild(insideChat);
    document.body.appendChild(chatPopup);

    const onDismiss = jest.fn();
    const target: SpotlightTarget = {
      label: "Invoice #4021",
      targets: ["Invoice #4021"],
    };
    render(<PageSpotlight target={target} onDismiss={onDismiss} />);

    // No real match exists outside the chat panel -- runs out its 6 retries
    // (300ms apart) and falls back to the not-found toast.
    act(() => {
      jest.advanceTimersByTime(6 * 300);
    });

    expect(screen.getByRole("status")).toHaveTextContent("Invoice #4021");
    expect(insideChat.scrollIntoView).not.toHaveBeenCalled();
  });

  it("shows a not-found toast and auto-dismisses when no element matches after retries", () => {
    const onDismiss = jest.fn();
    const target: SpotlightTarget = {
      label: "Nonexistent Widget",
      targets: ["Nonexistent Widget"],
      hint: "Custom hint text",
    };
    render(<PageSpotlight target={target} onDismiss={onDismiss} />);

    act(() => {
      jest.advanceTimersByTime(6 * 300);
    });

    const toast = screen.getByRole("status");
    expect(toast).toHaveTextContent("Nonexistent Widget");
    expect(toast).toHaveTextContent("Custom hint text");
    expect(onDismiss).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(3500);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("falls back to a default hint when none is provided on a not-found target", () => {
    render(
      <PageSpotlight
        target={{ label: "Ghost Field", targets: ["Ghost Field"] }}
        onDismiss={jest.fn()}
      />
    );

    act(() => {
      jest.advanceTimersByTime(6 * 300);
    });

    expect(screen.getByRole("status")).toHaveTextContent(
      "It should be on this page — try scrolling."
    );
  });

  it("calls onDismiss when the caption's Got it button is clicked", () => {
    const button = document.createElement("button");
    button.textContent = "Save";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 200, left: 20, width: 60, height: 24 }),
    });
    document.body.appendChild(button);

    const onDismiss = jest.fn();
    render(
      <PageSpotlight
        target={{ label: "Save button", targets: ["Save"] }}
        onDismiss={onDismiss}
      />
    );

    act(() => {
      flushRaf();
    });

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("calls onDismiss when Escape is pressed", () => {
    const button = document.createElement("button");
    button.textContent = "Save";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 200, left: 20, width: 60, height: 24 }),
    });
    document.body.appendChild(button);

    const onDismiss = jest.fn();
    render(
      <PageSpotlight
        target={{ label: "Save button", targets: ["Save"] }}
        onDismiss={onDismiss}
      />
    );

    act(() => {
      flushRaf();
    });

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("auto-dismisses after finding the element once the auto-dismiss timer elapses", () => {
    const button = document.createElement("button");
    button.textContent = "Save";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 200, left: 20, width: 60, height: 24 }),
    });
    document.body.appendChild(button);

    const onDismiss = jest.fn();
    render(
      <PageSpotlight
        target={{ label: "Save button", targets: ["Save"] }}
        onDismiss={onDismiss}
      />
    );

    act(() => {
      flushRaf();
    });

    act(() => {
      jest.advanceTimersByTime(7000);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("clears rect/notFound state and cleans up when the target is cleared", () => {
    const button = document.createElement("button");
    button.textContent = "Save";
    Object.defineProperty(button, "getBoundingClientRect", {
      value: () => ({ top: 200, left: 20, width: 60, height: 24 }),
    });
    document.body.appendChild(button);

    const onDismiss = jest.fn();
    const { rerender } = render(
      <PageSpotlight
        target={{ label: "Save button", targets: ["Save"] }}
        onDismiss={onDismiss}
      />
    );

    act(() => {
      flushRaf();
    });
    expect(screen.getByRole("status")).toBeInTheDocument();

    rerender(<PageSpotlight target={null} onDismiss={onDismiss} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("ignores hidden/zero-size elements when locating a match", () => {
    const hidden = document.createElement("button");
    hidden.textContent = "Archive";
    Object.defineProperty(hidden, "getBoundingClientRect", {
      value: () => ({ top: 0, left: 0, width: 0, height: 0 }),
    });
    // scrollIntoView lives on Element.prototype -- every element shares the
    // SAME function reference unless given its own, so track each one
    // independently here rather than asserting against the shared mock.
    const hiddenScroll = jest.fn();
    hidden.scrollIntoView = hiddenScroll;
    document.body.appendChild(hidden);

    const visible = document.createElement("button");
    visible.textContent = "Archive";
    Object.defineProperty(visible, "getBoundingClientRect", {
      value: () => ({ top: 50, left: 50, width: 80, height: 30 }),
    });
    const visibleScroll = jest.fn();
    visible.scrollIntoView = visibleScroll;
    document.body.appendChild(visible);

    render(
      <PageSpotlight
        target={{ label: "Archive button", targets: ["Archive"] }}
        onDismiss={jest.fn()}
      />
    );

    act(() => {
      flushRaf();
    });

    expect(visibleScroll).toHaveBeenCalled();
    expect(hiddenScroll).not.toHaveBeenCalled();
  });
});

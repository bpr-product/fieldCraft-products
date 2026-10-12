/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : Unit tests for EdenAuroraShell -- the purely presentational panel
*                chrome. Verifies it stays a dumb shell (renders exactly what its
*                props say, calls exactly the handler its props say) since Chat.tsx
*                depends on that split to guarantee the redesign cannot change
*                conversation behaviour.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";
import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import EdenAuroraShell from "./EdenAuroraShell";
import { EDEN_ORB_STATUS } from "./edenAurora.tokens";

const baseProps = () => ({
  orbState: "idle" as const,
  scrollAreaRef: createRef<HTMLDivElement>(),
  inputRef: createRef<HTMLInputElement>(),
  value: "",
  onChange: jest.fn(),
  onSend: jest.fn(),
  sendDisabled: false,
  onReset: jest.fn(),
  isResetting: false,
});

describe("EdenAuroraShell", () => {
  test("renders the transcript children and the per-orb-state status line", () => {
    render(
      <EdenAuroraShell {...baseProps()}>
        <div data-testid="fake-message">Hi Syn</div>
      </EdenAuroraShell>
    );
    expect(screen.getByTestId("eden-aurora-panel")).toBeInTheDocument();
    expect(screen.getByTestId("fake-message")).toBeInTheDocument();
    expect(screen.getByText(EDEN_ORB_STATUS.idle)).toBeInTheDocument();
  });

  test("shows the context line only when contextEntity is provided", () => {
    const { rerender } = render(<EdenAuroraShell {...baseProps()}>{null}</EdenAuroraShell>);
    expect(screen.queryByTestId("eden-context-line")).not.toBeInTheDocument();

    rerender(
      <EdenAuroraShell {...baseProps()} contextEntity="Buma P">
        {null}
      </EdenAuroraShell>
    );
    expect(screen.getByTestId("eden-context-line")).toHaveTextContent("Buma P");
  });

  test("renders quick prompts and forwards a click to onQuickPrompt with the raw text", () => {
    const onQuickPrompt = jest.fn();
    render(
      <EdenAuroraShell
        {...baseProps()}
        quickPrompts={[{ text: "Where are my unpaid invoices?" }, { text: "add material", label: "Add a new material" }]}
        onQuickPrompt={onQuickPrompt}
      >
        {null}
      </EdenAuroraShell>
    );

    const prompts = screen.getAllByTestId("eden-quick-prompt");
    expect(prompts).toHaveLength(2);
    expect(prompts[1]).toHaveTextContent("Add a new material");

    fireEvent.click(prompts[1]);
    expect(onQuickPrompt).toHaveBeenCalledWith("add material");
  });

  test("no quick prompts render when the list is empty or omitted", () => {
    render(<EdenAuroraShell {...baseProps()}>{null}</EdenAuroraShell>);
    expect(screen.queryByTestId("eden-quick-prompt")).not.toBeInTheDocument();
  });

  test("typing calls onChange, and Enter (without shift) calls onSend when not disabled", () => {
    const onChange = jest.fn();
    const onSend = jest.fn();
    render(
      <EdenAuroraShell {...baseProps()} onChange={onChange} onSend={onSend}>
        {null}
      </EdenAuroraShell>
    );

    const input = screen.getByPlaceholderText("Where do you want to go?");
    fireEvent.change(input, { target: { value: "hello" } });
    expect(onChange).toHaveBeenCalledWith("hello");

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  test("Enter does not send while sendDisabled is true", () => {
    const onSend = jest.fn();
    render(
      <EdenAuroraShell {...baseProps()} onSend={onSend} sendDisabled>
        {null}
      </EdenAuroraShell>
    );
    fireEvent.keyDown(screen.getByPlaceholderText("Where do you want to go?"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByLabelText("send")).toBeDisabled();
  });

  test("Shift+Enter does not send (allows a newline)", () => {
    const onSend = jest.fn();
    render(
      <EdenAuroraShell {...baseProps()} onSend={onSend}>
        {null}
      </EdenAuroraShell>
    );
    fireEvent.keyDown(screen.getByPlaceholderText("Where do you want to go?"), {
      key: "Enter",
      shiftKey: true,
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  test("the send button click also fires onSend", () => {
    const onSend = jest.fn();
    render(
      <EdenAuroraShell {...baseProps()} onSend={onSend}>
        {null}
      </EdenAuroraShell>
    );
    fireEvent.click(screen.getByLabelText("send"));
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  test("the reset button calls onReset and is disabled while isResetting", () => {
    const onReset = jest.fn();
    const { rerender } = render(
      <EdenAuroraShell {...baseProps()} onReset={onReset}>
        {null}
      </EdenAuroraShell>
    );
    fireEvent.click(screen.getByLabelText("Reset chat"));
    expect(onReset).toHaveBeenCalledTimes(1);

    rerender(
      <EdenAuroraShell {...baseProps()} onReset={onReset} isResetting>
        {null}
      </EdenAuroraShell>
    );
    expect(screen.getByLabelText("Reset chat")).toBeDisabled();
    expect(screen.getByLabelText("Clearing the conversation")).toBeInTheDocument();
  });

  test("the close button renders and fires onClose only when provided", () => {
    const { rerender } = render(<EdenAuroraShell {...baseProps()}>{null}</EdenAuroraShell>);
    expect(screen.queryByLabelText("Close chat")).not.toBeInTheDocument();

    const onClose = jest.fn();
    rerender(
      <EdenAuroraShell {...baseProps()} onClose={onClose}>
        {null}
      </EdenAuroraShell>
    );
    fireEvent.click(screen.getByLabelText("Close chat"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

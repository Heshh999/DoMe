/**
 * The talk button's states (src/components/TalkButton.tsx): labels, aria-pressed only while listening, Cancel while
 * thinking with "Keep DoMe open until I answer." and "Still working…" after 8 s, Stop while working, Dictate.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { STILL_WORKING_MS, TalkButton, type TalkState } from "../../src/components/TalkButton.tsx";

afterEach(() => vi.useRealTimers());

const noop = () => undefined;

describe("TalkButton", () => {
  it.each([
    ["idle", "Tap to talk", "false"],
    ["starting", "Getting ready…", "false"],
    ["listening", "Listening… tap to stop", "true"],
    ["speaking", "Tap to interrupt", "false"],
  ] as Array<[TalkState, string, string]>)("%s: %s", (state, label, pressed) => {
    render(<TalkButton state={state} dictate={false} onTap={noop} onCancel={noop} onStop={noop} />);
    const button = screen.getByRole("button", { name: label });
    expect(button).toHaveAttribute("aria-pressed", pressed);
    expect(button).toHaveAttribute("data-talk-button");
    expect(button).toBeEnabled();
  });

  it("thinking: Cancel, 'Keep DoMe open until I answer.', then 'Still working…' after 8 s", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn();
    render(<TalkButton state="thinking" dictate={false} onTap={noop} onCancel={cancel} onStop={noop} />);
    expect(screen.getByRole("button", { name: "Thinking…" })).toBeDisabled();
    expect(screen.getByText("Keep DoMe open until I answer.")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(STILL_WORKING_MS));
    expect(screen.getByText("Still working…")).toBeInTheDocument();
    vi.useRealTimers();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(cancel).toHaveBeenCalled();
  });

  it("working: Stop", async () => {
    const stop = vi.fn();
    render(<TalkButton state="working" dictate={false} onTap={noop} onCancel={noop} onStop={stop} />);
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(stop).toHaveBeenCalled();
  });

  it("without speech recognition it is Dictate", async () => {
    const tap = vi.fn();
    render(<TalkButton state="idle" dictate onTap={tap} onCancel={noop} onStop={noop} />);
    await userEvent.click(screen.getByRole("button", { name: "Dictate" }));
    expect(tap).toHaveBeenCalled();
  });
});

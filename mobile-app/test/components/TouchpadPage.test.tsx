/**
 * Touchpad page + keyboard panel over the real Runtime and a fake socket: the session starts on
 * entry, gestures and buttons become signed batches, Drag has a conspicuous state and End Drag
 * releases, pointercancel releases a held drag without a click, the keyboard panel commits text once,
 * an uncertain edit switches to Compose and Send, Ctrl+L is gated on the browser, an uncertain send
 * enters review and never resends by itself, and leaving the page stops the session.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import { Runtime, setRuntimeForTests } from "../../src/app/runtime.ts";
import { configureApi } from "../../src/lib/api.ts";
import { clearLogs, recentLogs } from "../../src/lib/log.ts";
import { TouchpadPage } from "../../src/pages/app/TouchpadPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { CONTROLLER, FakeSocket, flush, freshStorage, HELLO_ACK, installDialogPolyfill, noTimers, OFFICE_PC, PC, signedIn, TS } from "../helpers/harness.ts";

installDialogPolyfill();

const SESSION_ID = "C".repeat(22);
const GRANT: rest.Grant = { id: "g1", controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "media", "pointer", "keyboard"], created_at: TS };

let rt: Runtime;
let sockets: FakeSocket[];
let socket: FakeSocket;
let frames: Array<() => void>;
const timers = { ...noTimers, setTimeout: (fn: () => void, ms: number): unknown => setTimeout(fn, ms), clearTimeout: (h: unknown): void => clearTimeout(h as ReturnType<typeof setTimeout>) };

async function runFrames(): Promise<void> {
  const fns = frames.splice(0, frames.length);
  await act(async () => {
    for (const fn of fns) fn();
    await flush(8);
  });
}

function payload(f: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse((f.envelope as { payload: string }).payload) as Record<string, unknown>;
}
function batches() {
  return socket
    .frames()
    .filter((f) => f.type === "input_batch")
    .map(payload);
}
function allEvents(): Array<Record<string, unknown>> {
  return batches().flatMap((p) => p.events as Array<Record<string, unknown>>);
}
function starts() {
  return socket
    .frames()
    .filter((f) => f.type === "command")
    .map(payload)
    .filter((p) => p.action === "input.session_start");
}
function stops() {
  return socket
    .frames()
    .filter((f) => f.type === "command")
    .map(payload)
    .filter((p) => p.action === "input.session_stop");
}

async function answerStart(foreground: Record<string, unknown> | null = { process_name: "chrome.exe", window_title: "Google - Chrome", browser: "chrome" }) {
  await waitFor(() => expect(starts().length).toBeGreaterThan(0));
  const s = starts()[starts().length - 1]!;
  await act(async () => {
    socket.receive({ type: "result", command_id: s.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 5, result: { input_session_id: SESSION_ID, lease_seconds: 3, input_age_budget_ms: 1000, max_batch_events: 64, pointer: true, keyboard: true, foreground_app: foreground } });
    await flush();
  });
  await waitFor(() => expect(rt.input.snapshot.phase).toBe("live"));
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/app/touchpad"]}>
      <TouchpadPage />
    </MemoryRouter>,
  );
}

let clock = 10_000;
/** Pointer events with an explicit, monotonically increasing timeStamp so the tap thresholds never depend on test speed. */
function pointer(el: Element, type: "pointerdown" | "pointermove" | "pointerup" | "pointercancel", id: number, x: number, y: number, dt = 20) {
  clock += dt;
  const ev = new (window.PointerEvent ?? MouseEvent)(type, { pointerId: id, clientX: x, clientY: y, bubbles: true, pointerType: "touch", isPrimary: id === 1, button: 0 } as PointerEventInit);
  Object.defineProperty(ev, "timeStamp", { value: clock });
  if (!("pointerId" in ev)) Object.defineProperty(ev, "pointerId", { value: id });
  fireEvent(el, ev);
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  clearLogs();
  frames = [];
  sockets = [];
  rt = new Runtime({
    relay: {
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s;
      },
      timers: noTimers,
    },
    clockIntervalMs: 600_000,
    inputTimers: timers,
    scheduleFrame: (fn) => {
      frames.push(fn);
      return frames.length;
    },
  });
  setRuntimeForTests(rt);
  rt.start();
  for (let i = 0; i < 50 && sockets.length === 0; i++) await flush();
  socket = sockets[0]!;
  socket.open();
  socket.receive(HELLO_ACK);
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC, controllers: [], grantsByPc: { [PC]: [GRANT] } });
  useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
  useLiveStore.getState().onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, foreground_app: { process_name: "chrome.exe", window_title: "Google - Chrome", browser: "chrome" } } });
  if (!(globalThis as { PointerEvent?: unknown }).PointerEvent) (globalThis as { PointerEvent?: unknown }).PointerEvent = MouseEvent;
  if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => undefined;
  if (!Element.prototype.releasePointerCapture) Element.prototype.releasePointerCapture = () => undefined;
});

afterEach(() => {
  rt.stop();
  setRuntimeForTests(null);
  configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
});

describe("TouchpadPage", () => {
  it("starts the session on entry, shows the PC and the foreground app, and stops it when the page is left", async () => {
    const { unmount } = renderPage();
    await answerStart();
    expect(screen.getAllByText("Office PC").length).toBeGreaterThan(0);
    expect(screen.getByText("Chrome — Google - Chrome")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop Input" })).toBeEnabled();
    unmount();
    await flush();
    expect(stops()).toHaveLength(1);
    expect(stops()[0]!.params).toEqual({ input_session_id: SESSION_ID });
  });

  it("a tap clicks, a slide moves, a two-finger tap right-clicks; the live pill reflects Windows acceptance from input_ack", async () => {
    renderPage();
    await answerStart();
    const surface = screen.getByTestId("touchpad-surface");
    pointer(surface, "pointerdown", 1, 100, 100);
    pointer(surface, "pointerup", 1, 100, 100);
    await runFrames();
    expect(allEvents()).toEqual([{ type: "pointer_button", button: "left", action: "click" }]);
    pointer(surface, "pointerdown", 1, 100, 100);
    pointer(surface, "pointermove", 1, 140, 110);
    pointer(surface, "pointermove", 1, 150, 112);
    pointer(surface, "pointerup", 1, 150, 112);
    await runFrames();
    const ev = allEvents();
    expect(ev.length).toBe(2);
    expect(ev[1]!.type).toBe("pointer_move");
    expect(ev[1]!.dx as number).toBeGreaterThan(0);
    pointer(surface, "pointerdown", 1, 100, 100);
    pointer(surface, "pointerdown", 2, 140, 100);
    pointer(surface, "pointerup", 1, 100, 100);
    pointer(surface, "pointerup", 2, 140, 100);
    await runFrames();
    expect(allEvents()[2]).toEqual({ type: "pointer_button", button: "right", action: "click" });
    await act(async () => {
      socket.receive({ type: "input_ack", pc_id: PC, input_session_id: SESSION_ID, last_seq: 3, accepted_events: 3, dropped_events: 0, held_buttons: [], held_keys: [], at: TS });
      await flush();
    });
    expect(screen.getByText("Live · Windows accepted 3")).toBeInTheDocument();
  });

  it("Drag mode has a conspicuous active state, holds the left button, End Drag releases; pointercancel during a drag releases without a click", async () => {
    renderPage();
    await answerStart();
    const surface = screen.getByTestId("touchpad-surface");
    await userEvent.click(screen.getByRole("button", { name: "Drag mode" }));
    pointer(surface, "pointerdown", 1, 50, 50);
    pointer(surface, "pointermove", 1, 90, 70);
    await runFrames();
    expect(screen.getByText(/DRAG MODE — holding the left button/)).toBeInTheDocument();
    expect(allEvents()[0]).toEqual({ type: "pointer_button", button: "left", action: "down" });
    expect(allEvents()[1]!.type).toBe("pointer_move");
    pointer(surface, "pointerup", 1, 90, 70);
    await runFrames();
    expect(allEvents().filter((e) => e.action === "up")).toHaveLength(0); // drag lock keeps holding
    await userEvent.click(screen.getByRole("button", { name: "End Drag" }));
    await runFrames();
    expect(allEvents()[allEvents().length - 1]).toEqual({ type: "pointer_button", button: "left", action: "up" });
    expect(screen.getByRole("button", { name: "Drag mode" })).toBeInTheDocument();
    // cancel path
    await userEvent.click(screen.getByRole("button", { name: "Drag mode" }));
    pointer(surface, "pointerdown", 1, 50, 50);
    await runFrames();
    const before = allEvents().length;
    pointer(surface, "pointercancel", 1, 50, 50);
    await runFrames();
    const after = allEvents().slice(before);
    expect(after).toEqual([{ type: "pointer_button", button: "left", action: "up" }]);
    expect(after.some((e) => e.action === "click")).toBe(false);
  });

  it("Left/Right/Double buttons send the matching primitives; Stop Input ends the session", async () => {
    renderPage();
    await answerStart();
    await userEvent.click(screen.getByRole("button", { name: "Left" }));
    await userEvent.click(screen.getByRole("button", { name: "Right" }));
    await userEvent.click(screen.getByRole("button", { name: "Double click" }));
    await runFrames();
    expect(allEvents()).toEqual([
      { type: "pointer_button", button: "left", action: "click" },
      { type: "pointer_button", button: "right", action: "click" },
      { type: "pointer_button", button: "left", action: "double_click" },
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Stop Input" }));
    await waitFor(() => expect(stops()).toHaveLength(1));
    expect(rt.input.snapshot.phase).toBe("idle");
    expect(screen.getByRole("button", { name: /Start touchpad on Office PC/ })).toBeInTheDocument();
  });

  it("INPUT_SESSION_OWNED shows a Take over prompt; the takeover start carries params.takeover", async () => {
    renderPage();
    await waitFor(() => expect(starts()).toHaveLength(1));
    await act(async () => {
      socket.receive({ type: "result", command_id: starts()[0]!.command_id, origin: "agent", state: "failed", at: TS, duration_ms: 5, error: { code: "INPUT_SESSION_OWNED", message: "owned", retryable: false } });
      await flush();
    });
    expect(screen.getAllByText(/Another phone is using the touchpad/).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Take over" }));
    await waitFor(() => expect(starts()).toHaveLength(2));
    expect(starts()[1]!.params).toEqual({ takeover: true });
  });

  it("without pointer/keyboard in this phone's grant, nothing is started and the broad-scope explanation with the PC-side steps is shown", async () => {
    useDevicesStore.setState({ grantsByPc: { [PC]: [{ ...GRANT, capabilities: ["status", "media"] }] } });
    renderPage();
    await flush();
    expect(starts()).toHaveLength(0);
    expect(screen.getByText(/no touchpad or keyboard permission on this PC/)).toBeInTheDocument();
    expect(screen.getByText(/reaches every app of the unlocked Windows session/)).toBeInTheDocument();
    expect(screen.getByText(/Paired phones and allow Touchpad/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Get help" })).toHaveAttribute("href", expect.stringContaining("category=input"));
  });
});

describe("KeyboardPanel", () => {
  async function openKeyboard() {
    renderPage();
    await answerStart();
    await userEvent.click(screen.getByRole("button", { name: "Keyboard" }));
    return screen.getByTestId("live-textarea") as HTMLTextAreaElement;
  }

  it("live typing commits appended text once, Enter is an explicit key, and nothing typed appears in the logs", async () => {
    const ta = await openKeyboard();
    expect(screen.getByText(/Typing into: Chrome — Google - Chrome/)).toBeInTheDocument();
    ta.value = "hel";
    fireEvent.input(ta);
    ta.value = "hello";
    fireEvent.input(ta);
    fireEvent.input(ta); // duplicate event: nothing new
    fireEvent(ta, new InputEvent("beforeinput", { inputType: "insertLineBreak", bubbles: true, cancelable: true }));
    await runFrames();
    expect(allEvents()).toEqual([
      { type: "text", text: "hel" },
      { type: "text", text: "lo" },
      { type: "key", key: "enter" },
    ]);
    expect(ta.value).toBe("hello"); // Enter never inserted a newline into the echo
    expect(JSON.stringify(recentLogs())).not.toContain("hello");
  });

  it("an IME composition is forwarded once at compositionend; candidates are not sent", async () => {
    const ta = await openKeyboard();
    fireEvent.compositionStart(ta);
    ta.value = "に";
    fireEvent.input(ta);
    ta.value = "にほ";
    fireEvent.input(ta);
    ta.value = "日本";
    fireEvent.compositionEnd(ta);
    fireEvent.input(ta);
    await runFrames();
    expect(allEvents()).toEqual([{ type: "text", text: "日本" }]);
  });

  it("an edit in the middle of sent text pauses live entry and switches to Compose and Send with an explanation; nothing is sent", async () => {
    const ta = await openKeyboard();
    ta.value = "hello world";
    fireEvent.input(ta);
    await runFrames();
    ta.value = "hello big world";
    fireEvent.input(ta);
    await runFrames();
    expect(allEvents()).toEqual([{ type: "text", text: "hello world" }]);
    expect(screen.getByText("Live typing paused")).toBeInTheDocument();
    expect(screen.getByText(/cannot mirror that edit on the PC/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Compose and Send" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("composer")).toBeInTheDocument();
  });

  it("Ctrl+L appears only when the foreground app is a browser; other shortcuts and keys always", async () => {
    await openKeyboard();
    expect(screen.getByRole("button", { name: /Address bar/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Select all/ }));
    await userEvent.click(screen.getByRole("button", { name: /Address bar/ }));
    await userEvent.click(screen.getByRole("button", { name: "Backspace" }));
    await runFrames();
    expect(allEvents()).toEqual([
      { type: "shortcut", name: "ctrl_a" },
      { type: "shortcut", name: "ctrl_l" },
      { type: "key", key: "backspace" },
    ]);
    await act(async () => {
      socket.receive({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, foreground_app: { process_name: "notepad.exe", window_title: "Untitled - Notepad" } } });
      await flush();
    });
    expect(screen.queryByRole("button", { name: /Address bar/ })).toBeNull();
    expect(screen.getByText(/Typing into: notepad — Untitled - Notepad/)).toBeInTheDocument();
  });

  it("Compose and Send: acknowledged send clears the buffer; an unacknowledged send enters review and is only resent deliberately", async () => {
    await openKeyboard();
    await userEvent.click(screen.getByRole("tab", { name: "Compose and Send" }));
    const composer = screen.getByTestId("composer") as HTMLTextAreaElement;
    await userEvent.type(composer, "search terms");
    await userEvent.click(screen.getByRole("button", { name: "Send to PC" }));
    await runFrames();
    const seq = batches()[batches().length - 1]!.seq as number;
    expect(allEvents()).toEqual([{ type: "text", text: "search terms" }]);
    await act(async () => {
      socket.receive({ type: "input_ack", pc_id: PC, input_session_id: SESSION_ID, last_seq: seq, accepted_events: 1, dropped_events: 0, held_buttons: [], held_keys: [], at: TS });
      await flush();
    });
    await waitFor(() => expect(screen.getByText(/Windows accepted 12 characters/)).toBeInTheDocument());
    expect((screen.getByTestId("composer") as HTMLTextAreaElement).value).toBe("");
    // second send: the session ends before any ack → review state, buffer kept, no automatic resend
    await userEvent.type(screen.getByTestId("composer"), "again");
    await userEvent.click(screen.getByRole("button", { name: "Send to PC" }));
    await runFrames();
    const sentBefore = batches().length;
    await act(async () => {
      socket.receive({ type: "input_session", pc_id: PC, input_session_id: SESSION_ID, controller_id: CONTROLLER, event: "ended", reason: "lease_expired", holds_released: 0, at: TS });
      await flush();
    });
    await waitFor(() => expect(screen.getByText(/cannot tell whether this text reached the PC/)).toBeInTheDocument());
    expect(screen.getByText(/Nothing is resent on its own/)).toBeInTheDocument();
    expect((screen.getByTestId("composer") as HTMLTextAreaElement).value).toBe("again");
    await runFrames();
    expect(batches().length).toBe(sentBefore);
    expect(screen.getByRole("button", { name: "Send again" })).toBeDisabled(); // session is over; a deliberate restart comes first
    expect(screen.getByRole("button", { name: "Discard" })).toBeEnabled();
    expect(JSON.stringify(recentLogs())).not.toContain("search terms");
    expect(JSON.stringify(recentLogs())).not.toContain("again");
  });
});

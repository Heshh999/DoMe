/**
 * The Screen view page (docs/design/screen-view.md §2.2, §8.3, §16) over the real Runtime, a fake socket and a fake
 * RTCPeerConnection: nothing starts on arrival; "Show screen" runs the whole signed path to a live picture that says
 * "Encrypted between your PC and this phone" only after the PC-signed answer verified; a tap becomes
 * `pointer_move_to` + click in a batch stamped 1.4, a pinch sends nothing, a two-finger slide becomes `pointer_wheel`;
 * a view-only grant sends nothing; Stop, leaving the page and the app going to the background each send `bye` and the
 * signed stop, and the background case starts one fresh session on return; a refused `play()` asks for a tap; a wake
 * lock is held only while a session runs. Then every §16 row the page can show, with its exact copy.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { relayFrames, rest } from "@dome/protocol";

import { Runtime, setRuntimeForTests } from "../../src/app/runtime.ts";
import { configureApi } from "../../src/lib/api.ts";
import { writePin } from "../../src/lib/controllerKey.ts";
import { EMPTY_INPUT_STATE, FLUSH_INTERVAL_MS } from "../../src/lib/input.ts";
import { EMPTY_SCREEN_STATE, type ScreenSessionState } from "../../src/lib/screen.ts";
import { COPY } from "../../src/lib/screenStatus.ts";
import { ScreenPage } from "../../src/pages/app/ScreenPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useInputStore } from "../../src/store/input.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { useScreenStore } from "../../src/store/screen.ts";
import { FakePcKey, fakePeerFactory, type FakePeerConnection } from "../helpers/fakePeer.ts";
import { FakeTimers } from "../helpers/fakeTimers.ts";
import { ACCOUNT, CONTROLLER, FakeSocket, flush, freshStorage, HELLO_ACK, jsonResponse, noTimers, OFFICE_PC, PC, signedIn, TS } from "../helpers/harness.ts";

const SCREEN_SESSION = "U2NyZWVuU2Vzc2lvbjAyVF";
const INPUT_SESSION = "C".repeat(22);
const GRANT: rest.Grant = { id: "99999999-9999-4999-8999-999999999999", controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "pointer", "keyboard", "screen"], created_at: TS };
const VIEW_ONLY: rest.Grant = { ...GRANT, capabilities: ["status", "screen"] };

let rt: Runtime;
let sockets: FakeSocket[];
let socket: FakeSocket;
let peers: FakePeerConnection[];
let timers: FakeTimers;
let pcKey: FakePcKey;
let frames: Array<() => void>;
/** The input client's clock: each round of frames is one flush interval later, so motion held by the rate gate goes out (DECISIONS 79). */
let inputClock: number;
let wakeRequests: number;
let wakeReleases: number;
const inputTimers = { ...noTimers, setTimeout: (fn: () => void, ms: number): unknown => setTimeout(fn, ms), clearTimeout: (h: unknown): void => clearTimeout(h as ReturnType<typeof setTimeout>) };

function payload(f: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse((f.envelope as { payload: string }).payload) as Record<string, unknown>;
}
const sent = (type: string) => socket.frames().filter((f) => f.type === type);
const commands = (action: string) =>
  sent("command")
    .map(payload)
    .filter((p) => p.action === action);
const batches = () => sent("input_batch").map(payload);
const allEvents = () => batches().flatMap((p) => p.events as Array<Record<string, unknown>>);

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/app/screen"]}>
      <ScreenPage />
    </MemoryRouter>,
  );
}

/** "Show screen" → ICE servers → the signed start → result + PC-signed answer → DTLS → first frame. */
async function goLive(): Promise<FakePeerConnection> {
  const asked = sent("screen_ice_request").length;
  const startsBefore = commands("screen.session_start").length;
  await userEvent.click(screen.getByRole("button", { name: "Show screen" }));
  await waitFor(() => expect(sent("screen_ice_request").length).toBeGreaterThan(asked));
  await act(async () => {
    socket.receive({ type: "screen_ice_servers", pc_id: PC, ice_servers: [{ urls: ["stun:stun.example.net:3478"] }], expires_at: null, allowance: { unlimited: false, remaining_seconds: 600, resets_at: null }, turn: "available" });
    await flush();
  });
  await waitFor(() => expect(commands("screen.session_start").length).toBeGreaterThan(startsBefore));
  const start = commands("screen.session_start")[startsBefore]!;
  const envelope = await pcKey.signAnswer({ accountId: ACCOUNT, pcId: PC, controllerId: CONTROLLER, commandId: String(start.command_id), screenSessionId: SCREEN_SESSION, offerSdp: (start.params as { offer_sdp: string }).offer_sdp, now: new Date(timers.now) });
  await act(async () => {
    socket.receive({ type: "result", command_id: start.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { screen_session_id: SCREEN_SESSION, surfaces: [{ index: 0, width: 2560, height: 1440, primary: true }], surface_index: 0, gen: 1, audio: false, lease_seconds: 8, allowance: { unlimited: false, remaining_seconds: 420, resets_at: "2026-10-21T17:00:00.000Z" }, consent: "not_required", encoder: "h264" } });
    socket.receive({ type: "screen_answer", pc_id: PC, envelope });
    await flush();
  });
  await waitFor(() => expect(rt.screen.snapshot.verified).toBe(true));
  const peer = peers[peers.length - 1]!;
  await act(async () => {
    peer.connect();
    await flush();
  });
  // the first decoded frame (jsdom has no requestVideoFrameCallback: the timeupdate fallback carries it)
  await act(async () => {
    fireEvent(screen.getByTestId("screen-video"), new Event("timeupdate"));
    await flush();
  });
  await waitFor(() => expect(rt.screen.snapshot.phase).toBe("live"));
  return peer;
}

let answered = new Set<string>();
async function answerInputStart() {
  const unanswered = () => commands("input.session_start").filter((p) => !answered.has(String(p.command_id)));
  await waitFor(() => expect(unanswered().length).toBeGreaterThan(0));
  const s = unanswered()[unanswered().length - 1]!;
  answered.add(String(s.command_id));
  await act(async () => {
    socket.receive({ type: "result", command_id: s.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 5, result: { input_session_id: INPUT_SESSION, lease_seconds: 3, input_age_budget_ms: 1000, max_batch_events: 64, pointer: true, keyboard: true, foreground_app: null } });
    await flush();
  });
  await waitFor(() => expect(rt.input.snapshot.phase).toBe("live"));
}

async function sendFrames(): Promise<void> {
  inputClock += FLUSH_INTERVAL_MS;
  const fns = frames.splice(0, frames.length);
  await act(async () => {
    for (const fn of fns) fn();
    await flush(4);
    await rt.input.whenSent();
    await flush(2);
  });
}

let clock = 50_000;
function pointer(el: Element, type: "pointerdown" | "pointermove" | "pointerup", id: number, x: number, y: number, dt = 20) {
  clock += dt;
  const ev = new (window.PointerEvent ?? MouseEvent)(type, { pointerId: id, clientX: x, clientY: y, bubbles: true, pointerType: "touch", isPrimary: id === 1, button: 0 } as PointerEventInit);
  Object.defineProperty(ev, "timeStamp", { value: clock });
  if (!("pointerId" in ev)) Object.defineProperty(ev, "pointerId", { value: id });
  fireEvent(el, ev);
}

function boxed(el: HTMLElement, width = 400, height = 300) {
  el.getBoundingClientRect = () => ({ left: 0, top: 0, right: width, bottom: height, width, height, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  answered = new Set<string>();
  sockets = [];
  frames = [];
  inputClock = Date.now();
  timers = new FakeTimers();
  wakeRequests = 0;
  wakeReleases = 0;
  Object.defineProperty(navigator, "wakeLock", {
    configurable: true,
    value: {
      request: async () => {
        wakeRequests += 1;
        const s = {
          released: false,
          release: async () => {
            s.released = true;
            wakeReleases += 1;
          },
        };
        return s;
      },
    },
  });
  HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  HTMLMediaElement.prototype.pause = vi.fn();
  if (!(globalThis as { PointerEvent?: unknown }).PointerEvent) (globalThis as { PointerEvent?: unknown }).PointerEvent = MouseEvent;
  if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => undefined;
  const fp = fakePeerFactory();
  peers = fp.peers;
  configureApi({ fetchImpl: async (url) => (url.endsWith("/v1/pcs") ? jsonResponse(200, { pcs: [OFFICE_PC] }) : url.includes("/grants") ? jsonResponse(200, { grants: [GRANT] }) : url.endsWith("/v1/controllers") ? jsonResponse(200, { controllers: [] }) : jsonResponse(500, { error: { code: "INTERNAL", message: url, retryable: false } })) });
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
    peerFactory: fp.factory,
    screenTimers: timers,
    screenNow: () => new Date(timers.now),
    inputTimers,
    inputNow: () => new Date(inputClock),
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
  useLiveStore.getState().onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: false } } as relayFrames.AgentState);
  useScreenStore.setState({ switch: "on", switchChecked: true, session: EMPTY_SCREEN_STATE, lastAllowanceByPc: {}, lastProblemByPc: {}, prefs: { touchpadMode: false, explainerSeen: false } });
  pcKey = await FakePcKey.create();
  await writePin(pcKey.pin(PC));
});

afterEach(() => {
  cleanup(); // unmount before the runtime stops, so the page never reacts to the teardown itself
  rt.stop();
  setRuntimeForTests(null);
  setVisibility("visible");
  configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
});

describe("ScreenPage: the live path", () => {
  it("starts nothing on arrival; Show screen runs the signed path; 'Encrypted…' appears only after the answer verified; the input session follows the picture", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("screen-status")).toHaveAttribute("data-kind", "ready"));
    expect(screen.getByRole("heading", { name: "Show Office PC's screen" })).toBeInTheDocument();
    expect(screen.getByText(COPY.explainer)).toBeInTheDocument();
    expect(screen.getAllByText(COPY.protectedContent).length).toBeGreaterThan(0); // the first-use hint
    expect(sent("screen_ice_request")).toHaveLength(0);
    expect(sent("command")).toHaveLength(0);

    await goLive();
    const stage = screen.getByTestId("screen-stage");
    expect(stage).toHaveAccessibleName("Screen view of Office PC");
    expect(screen.getByTestId("screen-encrypted")).toHaveTextContent(COPY.encrypted);
    expect(screen.getByTestId("screen-time-left")).toHaveTextContent("About 7 min left");
    expect(screen.queryByTestId("screen-overlay")).toBeNull();
    // the PC's start command was stamped 1.4 and carried the offer; the input session starts with the picture
    expect(commands("screen.session_start")[0]!.protocol_version).toBe("1.4");
    await answerInputStart();
    expect(wakeRequests).toBe(1);
  });

  it("a tap is pointer_move_to + click (stamped 1.4) at once; a pinch sends nothing; a two-finger slide scrolls with pointer_wheel", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive();
    await answerInputStart();
    const surface = screen.getByTestId("screen-surface");
    boxed(surface);
    // a 2560×1440 surface in a 400×300 box: content 400×225 from y = 37.5; the centre is (200, 150)
    pointer(surface, "pointerdown", 1, 200, 150);
    pointer(surface, "pointerup", 1, 200, 150);
    await act(async () => {
      await rt.input.whenSent();
      await flush(4);
    });
    expect(batches()).toHaveLength(1); // no frame wait: the click went out at once
    expect(batches()[0]!.protocol_version).toBe("1.4");
    expect(allEvents()).toEqual([
      { type: "pointer_move_to", x: 32768, y: 32768, gen: 1 },
      { type: "pointer_button", button: "left", action: "click" },
    ]);
    // a tap in the black bar above the picture sends nothing
    pointer(surface, "pointerdown", 1, 200, 10, 600);
    pointer(surface, "pointerup", 1, 200, 10);
    await sendFrames();
    expect(allEvents()).toHaveLength(2);
    // pinch: spread the fingers apart → local zoom only
    pointer(surface, "pointerdown", 1, 150, 150, 600);
    pointer(surface, "pointerdown", 2, 250, 150);
    pointer(surface, "pointermove", 1, 110, 150);
    pointer(surface, "pointermove", 2, 290, 150);
    pointer(surface, "pointerup", 1, 110, 150);
    pointer(surface, "pointerup", 2, 290, 150);
    await sendFrames();
    expect(allEvents()).toHaveLength(2);
    expect(screen.getByTestId("screen-view-layer").style.transform).toMatch(/scale\((1\.[5-9]|2)/);
    // two fingers sliding down together → the wheel under the fingers
    pointer(surface, "pointerdown", 1, 180, 120, 600);
    pointer(surface, "pointerdown", 2, 220, 120);
    for (let i = 1; i <= 4; i++) {
      pointer(surface, "pointermove", 1, 180, 120 + i * 10);
      pointer(surface, "pointermove", 2, 220, 120 + i * 10);
    }
    pointer(surface, "pointerup", 1, 180, 160);
    pointer(surface, "pointerup", 2, 220, 160);
    await sendFrames();
    const wheels = allEvents().filter((e) => e.type === "pointer_wheel");
    expect(wheels.length).toBeGreaterThan(0);
    expect(wheels.every((e) => (e.dy as number) > 0 && e.dx === 0)).toBe(true); // natural: fingers down → content down
    expect(allEvents().filter((e) => e.type === "pointer_button")).toHaveLength(1); // the second finger never clicked
  });

  it("Stop sends bye and the signed stop, closes the picture and the input session, releases the wake lock and says how long it ran", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    const peer = await goLive();
    await answerInputStart();
    await userEvent.click(screen.getByRole("button", { name: "Stop Screen view" }));
    await waitFor(() => expect(screen.queryByTestId("screen-stage")).toBeNull());
    expect(peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    expect(peer.closed).toBe(true);
    await waitFor(() => expect(commands("screen.session_stop")).toHaveLength(1));
    expect(commands("screen.session_stop")[0]!.params).toEqual({ screen_session_id: SCREEN_SESSION });
    await waitFor(() => expect(commands("input.session_stop")).toHaveLength(1));
    expect(screen.getByRole("heading", { name: /^Stopped · \d+ s$/ })).toBeInTheDocument();
    await waitFor(() => expect(wakeReleases).toBe(1));
  });

  it("leaving the page ends the picture (Phase 1 has no mini view)", async () => {
    const view = renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    const peer = await goLive();
    view.unmount();
    await waitFor(() => expect(commands("screen.session_stop")).toHaveLength(1));
    expect(peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    expect(rt.screen.snapshot.phase).toBe("idle");
  });

  it("the app going to the background sends bye and stops; back in front, one fresh session starts by itself", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    const peer = await goLive();
    await act(async () => {
      setVisibility("hidden");
      await flush(6);
    });
    expect(peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    await waitFor(() => expect(commands("screen.session_stop")).toHaveLength(1));
    expect(sent("screen_ice_request")).toHaveLength(1);
    await act(async () => {
      setVisibility("visible");
      await flush(6);
    });
    await waitFor(() => expect(sent("screen_ice_request")).toHaveLength(2));
    expect(screen.getByTestId("screen-stage")).toBeInTheDocument();
  });

  it("a view-only grant shows the picture, says so, and a tap sends nothing; no input session is started", async () => {
    useDevicesStore.setState({ grantsByPc: { [PC]: [VIEW_ONLY] } });
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive();
    expect(screen.getByTestId("screen-input-notice")).toHaveTextContent(COPY.viewOnly);
    const surface = screen.getByTestId("screen-surface");
    boxed(surface);
    pointer(surface, "pointerdown", 1, 200, 150, 600);
    pointer(surface, "pointerup", 1, 200, 150);
    await sendFrames();
    expect(batches()).toHaveLength(0);
    expect(commands("input.session_start")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Keyboard" })).toBeDisabled();
  });

  it("press and hold opens the loupe; sliding moves its aim at 1/2.5 of the finger; lifting clicks at the aim", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive();
    await answerInputStart();
    const surface = screen.getByTestId("screen-surface");
    boxed(surface);
    pointer(surface, "pointerdown", 1, 200, 150, 600);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 420)); // the 350 ms hold
    });
    expect(screen.getByTestId("screen-loupe")).toBeInTheDocument();
    pointer(surface, "pointermove", 1, 225, 150, 450); // 25 px of finger → 10 px of aim
    pointer(surface, "pointerup", 1, 225, 150);
    await act(async () => {
      await rt.input.whenSent();
      await flush(4);
    });
    expect(screen.queryByTestId("screen-loupe")).toBeNull();
    // aim (210, 150) in the 400 px wide picture → 210/400 of the surface, not the finger's 225/400
    expect(allEvents()).toEqual([
      { type: "pointer_move_to", x: Math.round((210 / 400) * 65535), y: 32768, gen: 1 },
      { type: "pointer_button", button: "left", action: "click" },
    ]);
  });

  it("Touchpad mode swaps in the relative touchpad over the picture (plain 1.1 batches); the choice is remembered", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive();
    await answerInputStart();
    await userEvent.click(screen.getByRole("button", { name: "Touchpad" }));
    expect(screen.getByRole("button", { name: "Touchpad" })).toHaveAttribute("aria-pressed", "true");
    expect(useScreenStore.getState().prefs.touchpadMode).toBe(true);
    const surface = screen.getByTestId("screen-surface");
    expect(surface).toHaveAttribute("data-mode", "touchpad");
    pointer(surface, "pointerdown", 1, 100, 100, 600);
    pointer(surface, "pointermove", 1, 140, 110);
    pointer(surface, "pointermove", 1, 150, 112);
    pointer(surface, "pointerup", 1, 150, 112);
    await sendFrames();
    expect(allEvents().length).toBeGreaterThan(0);
    expect(allEvents().every((e) => e.type === "pointer_move")).toBe(true);
    expect(batches().every((b) => b.protocol_version === "1.1")).toBe(true); // nothing 1.4-only in a touchpad batch
    expect(screen.getByRole("button", { name: "Drag" })).toBeEnabled();
  });

  it("a refused play() (Low Power Mode) asks for one tap, which plays again", async () => {
    const play = vi.fn().mockRejectedValueOnce(new DOMException("needs a gesture", "NotAllowedError")).mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = play;
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive();
    const tap = await screen.findByRole("button", { name: COPY.tapToPlay });
    await userEvent.click(tap);
    await waitFor(() => expect(screen.queryByRole("button", { name: COPY.tapToPlay })).toBeNull());
    expect(play.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

// ---- every §16 row the page shows, from the session state ---------------------------------------------------------------

function show(session: Partial<ScreenSessionState>, setup: () => void = () => undefined) {
  setup();
  useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, pcId: PC, ...session } });
  return renderPage();
}

describe("ScreenPage: every §16 state, with its copy", () => {
  it("before a session: switched off, no grant, not pinned, remote control off, another phone viewing, resume", async () => {
    const off = show({}, () => useScreenStore.setState({ switch: "off" }));
    expect(screen.getByText(COPY.notBuilt)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show screen" })).toBeNull();
    off.unmount();
    useScreenStore.setState({ switch: "on" });

    const noGrant = show({}, () => useDevicesStore.setState({ grantsByPc: { [PC]: [{ ...GRANT, capabilities: ["status", "pointer"] }] } }));
    await waitFor(() => expect(screen.getByText(COPY.noGrant)).toBeInTheDocument());
    noGrant.unmount();
    useDevicesStore.setState({ grantsByPc: { [PC]: [GRANT] } });

    const otherPc = "55555555-5555-4555-8555-555555555555";
    const unpinned = show({}, () => {
      useDevicesStore.setState({ pcs: [OFFICE_PC, { ...OFFICE_PC, id: otherPc, name: "Den PC" }], selectedPcId: otherPc, grantsByPc: { [PC]: [GRANT], [otherPc]: [{ ...GRANT, pc_id: otherPc }] } });
      useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: otherPc, connection: "online", last_seen: TS });
    });
    await waitFor(() => expect(screen.getByText(COPY.unpinned)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Pair again" })).toHaveAttribute("href", "/app/devices/pair");
    unpinned.unmount();
    useDevicesStore.setState({ selectedPcId: PC });

    const remoteOff = show({}, () => useLiveStore.getState().onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: false, session_locked: false, extension_connected: false } } as relayFrames.AgentState));
    await waitFor(() => expect(screen.getByText(COPY.remoteOff)).toBeInTheDocument());
    remoteOff.unmount();
    useLiveStore.getState().onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: false } } as relayFrames.AgentState);

    const other = show({ otherViewer: true });
    await waitFor(() => expect(screen.getByRole("heading", { name: COPY.owned })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Take over" })).toBeInTheDocument();
    other.unmount();

    show({ resume: "manual" });
    await waitFor(() => expect(screen.getByRole("heading", { name: COPY.resume })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("start refusals: identity changed (hard stop), answer invalid, PC too old, kill switch, consent declined / timed out / backing off, allowance used, owned, no path, capture, unsupported browser", async () => {
    const rows: Array<[Partial<ScreenSessionState>, RegExp | string, string | null]> = [
      [{ phase: "failed", problem: { code: "SCREEN_ANSWER_INVALID", detail: { reason: "kid is not the pinned PC key" } } }, COPY.identityChanged, "Pair again"],
      [{ phase: "failed", problem: { code: "SCREEN_ANSWER_INVALID", detail: { reason: "signature" } } }, COPY.answerInvalid, null],
      [{ phase: "failed", problem: { code: "PROTOCOL_INCOMPATIBLE" } }, COPY.updatePc, null],
      [{ phase: "failed", problem: { code: "SCREEN_VIEW_DISABLED" } }, COPY.disabled, null],
      [{ phase: "failed", problem: { code: "SCREEN_CONSENT_DECLINED" }, sentAt: 1, ackAt: 2 }, COPY.declined, null],
      [{ phase: "failed", problem: { code: "SCREEN_CONSENT_DECLINED" }, sentAt: Date.now(), ackAt: null }, COPY.declinedBackoff, null],
      [{ phase: "failed", problem: { code: "SCREEN_CONSENT_TIMEOUT" } }, COPY.timeout, "Try again"],
      [{ phase: "failed", problem: { code: "SCREEN_ALLOWANCE_EXHAUSTED" }, allowance: { unlimited: false, remainingSeconds: 0, resetsAt: new Date(Date.now() + 3_600_000).toISOString(), at: Date.now() } }, /^You've used Free Screen view for now\. Refreshes .+\. The touchpad and remote still work\.$/, null],
      [{ phase: "failed", problem: { code: "SCREEN_SESSION_OWNED" }, owned: true }, COPY.owned, "Take over"],
      [{ phase: "failed", problem: { code: "SCREEN_CONNECTION_FAILED" } }, COPY.noPath, "Try again"],
      [{ phase: "failed", problem: { code: "SCREEN_CONNECTION_FAILED" }, turn: "disabled" }, `${COPY.noPath} ${COPY.turnDisabled}`, "Try again"],
      [{ phase: "failed", problem: { code: "SCREEN_CAPTURE_UNAVAILABLE" } }, COPY.captureUnavailable, "Try again"],
      [{ phase: "failed", problem: { code: "SCREEN_UNSUPPORTED_BROWSER" } }, COPY.unsupported, null],
    ];
    for (const [state, text, action] of rows) {
      const view = show(state);
      await waitFor(() => expect(screen.getByTestId("screen-status")).toHaveTextContent(typeof text === "string" ? text : /.*/));
      if (typeof text !== "string") expect(screen.getByText(text)).toBeInTheDocument();
      if (action === "Pair again") expect(screen.getByRole("link", { name: "Pair again" })).toBeInTheDocument();
      else if (action) expect(screen.getByRole("button", { name: action })).toBeInTheDocument();
      expect(screen.getByTestId("failure-links")).toBeInTheDocument();
      view.unmount();
    }
  });

  it("the PC ended it: stopped on the PC (no restart), takeover, locked, worker crash, relay lost on the PC side, this phone offline, Stop", async () => {
    const rows: Array<[ScreenSessionState["endReason"], string, string | null]> = [
      ["stopped_on_pc", COPY.stoppedOnPc, null],
      ["takeover", COPY.takeover, "Take over"],
      ["session_locked", COPY.locked, "Try again"],
      ["capture_failed", COPY.crashed, "Try again"],
      ["allowance_unconfirmed", COPY.relayLostPc, "Try again"],
      ["controller_offline", COPY.controllerOffline, "Try again"],
      ["grant_removed", COPY.grantRemoved, null],
      ["max_duration", COPY.maxDuration, "Try again"],
    ];
    for (const [reason, text, action] of rows) {
      const view = show({ phase: "ended", endReason: reason, liveSeconds: 192 });
      await waitFor(() => expect(screen.getByTestId("screen-status")).toHaveTextContent(text));
      if (action) expect(screen.getByRole("button", { name: action })).toBeInTheDocument();
      else expect(screen.queryByRole("button", { name: /Show screen|Try again|Take over/ })).toBeNull();
      view.unmount();
    }
    show({ phase: "ended", endReason: "stopped", liveSeconds: 192 });
    expect(await screen.findByRole("heading", { name: "Stopped · 3 min 12 s" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show screen" })).toBeInTheDocument();
  });

  it("during a session: connecting, trying the relay, waiting for consent with its countdown, live with badges, frozen, paused for Windows' permission prompt, clicks paused", async () => {
    const stream = { id: "s" } as unknown as MediaStream;
    const conn = show({ phase: "starting" });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.connecting);
    expect(screen.getByRole("button", { name: "Stop Screen view" })).toBeInTheDocument();
    expect(screen.queryByTestId("screen-encrypted")).toBeNull(); // nothing claims encryption before the answer verified
    expect(screen.queryByTestId("screen-surface")).toBeNull();
    conn.unmount();

    const relay = show({ phase: "connecting", verified: true, tryingRelay: true });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.tryingRelay);
    relay.unmount();

    const consent = show({ phase: "starting", consentWaiting: true, consentDeadlineAt: Date.now() + 25_000 });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(/^Waiting for someone at the PC to allow this… \(2[45]\)/);
    consent.unmount();

    const liveBadges = show({ phase: "live", verified: true, stream, path: "relayed", dataSaver: true, encoder: "vp8", allowance: { unlimited: false, remainingSeconds: 420, resetsAt: null, at: Date.now() } });
    expect(await screen.findByTestId("screen-encrypted")).toHaveTextContent(COPY.encrypted);
    expect(screen.getByTestId("screen-badges")).toHaveTextContent(COPY.viaRelay);
    expect(screen.getByTestId("screen-badges")).toHaveTextContent(COPY.dataSaver);
    expect(screen.getByTestId("screen-badges")).toHaveTextContent(COPY.basicPicture);
    expect(screen.getByTestId("screen-time-left")).toHaveTextContent(/About [67] min left/);
    expect(screen.queryByTestId("screen-overlay")).toBeNull();
    liveBadges.unmount();

    const frozen = show({ phase: "live", verified: true, stream, stalled: true });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.frozen);
    frozen.unmount();

    const uac = show({ phase: "paused", verified: true, stream, pcState: "paused", pauseReason: "secure_desktop", allowance: { unlimited: true, remainingSeconds: null, resetsAt: null, at: Date.now() } });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.secureDesktop);
    uac.unmount();
    const uacFree = show({ phase: "paused", verified: true, stream, pcState: "paused", pauseReason: "secure_desktop", allowance: { unlimited: false, remainingSeconds: 300, resetsAt: null, at: Date.now() } });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.secureDesktop);
    expect(screen.getByTestId("screen-overlay")).toHaveTextContent(COPY.secureDesktopFree);
    uacFree.unmount();
    const bar = show({ phase: "paused", verified: true, stream, pcState: "paused", pauseReason: "indicator", allowance: { unlimited: false, remainingSeconds: 300, resetsAt: null, at: Date.now() } });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.indicatorPaused);
    expect(screen.getByTestId("screen-overlay")).not.toHaveTextContent("Windows");
    bar.unmount();

    show({ phase: "live", verified: true, stream, relayDown: true });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.clicksPaused);
  });

  it("the signed input stream's notices over the picture: an administrator window, a DoMe window open on the PC", async () => {
    const stream = { id: "s" } as unknown as MediaStream;
    const live: Partial<ScreenSessionState> = { phase: "live", verified: true, stream, sessionId: SCREEN_SESSION };
    const admin = show(live, () => useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase: "live", pcId: PC, pointer: true, keyboard: true, problem: { code: "INPUT_RESTRICTED" } } }));
    expect(await screen.findByTestId("screen-input-notice")).toHaveTextContent(COPY.adminWindow);
    admin.unmount();
    const restricted = show({ ...live, restricted: { restricted: true, reason: "elevated" } }, () => useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase: "live", pcId: PC, pointer: true } }));
    expect(await screen.findByTestId("screen-input-notice")).toHaveTextContent(COPY.adminWindow);
    restricted.unmount();
    show(live, () => useInputStore.setState({ session: { ...EMPTY_INPUT_STATE, phase: "live", pcId: PC, pointer: true, problem: { code: "INPUT_PAUSED_ON_PC" } } }));
    expect(await screen.findByTestId("screen-input-notice")).toHaveTextContent(COPY.pausedOnPc);
  });
});

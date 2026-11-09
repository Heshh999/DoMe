/**
 * The Screen view page (docs/design/screen-view.md §2.2, §8.3, §16) over the real Runtime, a fake socket and a fake
 * RTCPeerConnection: nothing starts on arrival; "Show screen" runs the whole signed path to a live picture that says
 * "Encrypted between your PC and this phone" only after the PC-signed answer verified; a tap becomes
 * `pointer_move_to` + click in a batch stamped 1.4, a pinch sends nothing, a two-finger slide becomes `pointer_wheel`;
 * a view-only grant sends nothing; Stop, leaving the page and the app going to the background each send `bye` and the
 * signed stop, and the background case starts one fresh session on return; a refused `play()` asks for a tap; a wake
 * lock is held only while a session runs. Then every §16 row the page can show, with its exact copy. Device round 2
 * (2026-10-27): the track that arrives inside setRemoteDescription reaches the `<video>`; the stage carries the
 * test-build readout (never in production); "Connected. Waiting for the picture…" once DTLS is up; the client's
 * play hint asks for one tap.
 *
 * Protocol 1.5 (design screen-view-2a §4.8): the screens button only with two or more screens, its label and number,
 * disabled with a spinner while switching; a switch keeps the stage and the last picture under "Switching to screen
 * 2…" (no idle card) and ends with "Screen 2 of 2"; the switching line gives way to "Tap to show the picture" and to
 * the consent wait; the short notes, their timings and order; the pointer note once per session after
 * `state{cursor: "drawn"}` in a session that asked for the pointer, never in a 1.4 session; the time left on line
 * two; the three gesture lists, the explainer and the "newer DoMe is ready" notice. DECISIONS 92: a newer version that
 * took control during Screen view never reloads the page while it is open; it does once the page is left.
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { relayFrames, rest } from "@dome/protocol";

import { Runtime, setRuntimeForTests } from "../../src/app/runtime.ts";
import { configureApi } from "../../src/lib/api.ts";
import { AFTER_BUSY_DELAY_MS, AppUpdater } from "../../src/lib/appUpdate.ts";
import { writePin } from "../../src/lib/controllerKey.ts";
import { EMPTY_INPUT_STATE, FLUSH_INTERVAL_MS } from "../../src/lib/input.ts";
import { EMPTY_SCREEN_STATE, type ScreenSessionState } from "../../src/lib/screen.ts";
import { DIRECT_GESTURES } from "../../src/lib/directGestures.ts";
import { TOUCHPAD_SCREEN_GESTURES } from "../../src/lib/gestures.ts";
import { COPY, SCREEN_BETA_LABEL, SWITCH_COPY } from "../../src/lib/screenStatus.ts";
import { ScreenPage } from "../../src/pages/app/ScreenPage.tsx";
import { currentBusy, subscribeBusy } from "../../src/store/busy.ts";
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

const client = () => rt.screen;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/app/screen"]}>
      <ScreenPage />
    </MemoryRouter>,
  );
}

const ICE_SERVERS = { type: "screen_ice_servers", pc_id: PC, ice_servers: [{ urls: ["stun:stun.example.net:3478"] }], expires_at: null, allowance: { unlimited: false, remaining_seconds: 600, resets_at: null }, turn: "available" };

function screenResult(commandId: unknown, extra: Record<string, unknown> = {}) {
  return { type: "result", command_id: commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { screen_session_id: SCREEN_SESSION, surfaces: [{ index: 0, width: 2560, height: 1440, primary: true }], surface_index: 0, gen: 1, audio: false, lease_seconds: 8, allowance: { unlimited: false, remaining_seconds: 420, resets_at: "2026-10-21T17:00:00.000Z" }, consent: "not_required", encoder: "h264", ...extra } };
}

/** The PC-signed answer to `start`, stamped like the start (as the agent signs it). */
async function answerTo(start: Record<string, unknown>, sessionId: string = SCREEN_SESSION) {
  const envelope = await pcKey.signAnswer({ accountId: ACCOUNT, pcId: PC, controllerId: CONTROLLER, commandId: String(start.command_id), screenSessionId: sessionId, offerSdp: (start.params as { offer_sdp: string }).offer_sdp, protocolVersion: String(start.protocol_version), now: new Date(timers.now) });
  return { type: "screen_answer", pc_id: PC, envelope };
}

/** "Show screen" → ICE servers → the signed start → result + PC-signed answer → DTLS → first frame. `extra` goes into the result. */
async function goLive(extra: Record<string, unknown> = {}): Promise<FakePeerConnection> {
  const asked = sent("screen_ice_request").length;
  const startsBefore = commands("screen.session_start").length;
  await userEvent.click(screen.getByRole("button", { name: "Show screen" }));
  await waitFor(() => expect(sent("screen_ice_request").length).toBeGreaterThan(asked));
  await act(async () => {
    socket.receive(ICE_SERVERS);
    await flush();
  });
  await waitFor(() => expect(commands("screen.session_start").length).toBeGreaterThan(startsBefore));
  const start = commands("screen.session_start")[startsBefore]!;
  const answer = await answerTo(start);
  await act(async () => {
    socket.receive(screenResult(start.command_id, extra));
    socket.receive(answer);
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
  vi.unstubAllEnvs();
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

  it("the stage carries the test-build readout from the first moment, before any stream; it follows the picture to live", async () => {
    vi.stubEnv("VITE_DOME_RELEASE_CHANNEL", "staging");
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await userEvent.click(screen.getByRole("button", { name: "Show screen" }));
    const readout = await screen.findByTestId("screen-readout");
    expect(readout).toHaveTextContent("verified: no");
    expect(readout).toHaveTextContent("stream set: no");
    expect(screen.queryByTestId("screen-surface")).toBeNull(); // no <video> yet: the readout is what says so
    await act(async () => {
      await client().stop("stopped");
    });
    await goLive();
    await waitFor(() => expect(screen.getByTestId("screen-readout")).toHaveTextContent("phase: live"));
    expect(screen.getByTestId("screen-readout")).toHaveTextContent("verified: yes");
    expect(screen.getByTestId("screen-readout")).toHaveTextContent("stream set: yes");
    expect(screen.getByTestId("screen-readout")).toHaveTextContent(/track seen: yes \+\d+ ms/);
    expect(screen.getByTestId("screen-readout")).toHaveTextContent(/rVFC ticks: 1 \(timeupdate\)/); // the first frame, through the probe
  });

  it("a production build has no readout on the stage", async () => {
    vi.stubEnv("VITE_DOME_RELEASE_CHANNEL", "production");
    try {
      show({ phase: "connecting", verified: true });
      expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.connecting);
      expect(screen.queryByTestId("screen-readout")).toBeNull();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("a refused play() (Low Power Mode) asks for one tap, which plays again", async () => {
    const play = vi.fn().mockRejectedValueOnce(new DOMException("needs a gesture", "NotAllowedError")).mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = play;
    const noted = vi.spyOn(client(), "notePlayBlocked");
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive();
    const tap = await screen.findByRole("button", { name: COPY.tapToPlay });
    // the client hears of the refusal too, so its first-frame watchdog does not restart a picture that needs this tap
    expect(noted).toHaveBeenCalledWith(true);
    await userEvent.click(tap);
    await waitFor(() => expect(screen.queryByRole("button", { name: COPY.tapToPlay })).toBeNull());
    expect(play.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(noted).toHaveBeenLastCalledWith(false);
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

    // DTLS is up and the picture has not come yet: the copy names that stage
    const waiting = show({ phase: "connecting", verified: true, transportConnected: true, stream });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(COPY.waitingPicture);
    expect(screen.getByTestId("screen-surface")).toBeInTheDocument();
    waiting.unmount();

    // frames decode but the <video> sits paused: the client's hint asks for one tap
    const hint = show({ phase: "connecting", verified: true, transportConnected: true, stream, playHint: true });
    expect(await screen.findByRole("button", { name: COPY.tapToPlay })).toBeInTheDocument();
    hint.unmount();

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

// ---- protocol 1.5: Next screen, the pointer note, the header, the gesture sheet (design screen-view-2a §4.8) ----------

const TWO_SURFACES = [
  { index: 0, width: 2560, height: 1440, primary: true },
  { index: 1, width: 1920, height: 1080, primary: false },
];
const NEW_SESSION = "U2NyZWVuU2Vzc2lvbjA0VF";
const V15 = ["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"];
const EXPLAINER = "Tap what you see to click it. Pinch to zoom in; press and hold for a magnifier. Touchpad moves the pointer like a laptop touchpad and lets you drag. If your PC has two screens, the screens button at the top switches between them.";

describe("ScreenPage: Next screen (1.5)", () => {
  it("the screens button exists only with two or more screens; a switch keeps the picture under 'Switching to screen 2…' and ends with 'Screen 2 of 2'", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive(); // one screen: no button
    expect(screen.queryByTestId("screen-next")).toBeNull();
    await act(async () => {
      await client().stop("stopped");
    });
    const a = await goLive({ surfaces: TWO_SURFACES });
    const next = screen.getByTestId("screen-next");
    expect(next).toHaveAccessibleName("Next screen. Showing screen 1 of 2. Switch to screen 2.");
    expect(next).toHaveTextContent("1/2");
    expect(next).toBeEnabled();
    expect(next.compareDocumentPosition(screen.getByRole("button", { name: "Touchpad" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // before Touchpad
    const shown = screen.getByTestId("screen-video");
    const asked = sent("screen_ice_request").length;
    const before = commands("screen.session_start").length;
    const stops = commands("screen.session_stop").length; // the one-screen session's Stop above
    await userEvent.click(next);
    await waitFor(() => expect(sent("screen_ice_request").length).toBeGreaterThan(asked));
    await act(async () => {
      socket.receive(ICE_SERVERS);
      await flush();
    });
    await waitFor(() => expect(commands("screen.session_start").length).toBeGreaterThan(before));
    const start = commands("screen.session_start")[before]!;
    expect(start.params).toMatchObject({ surface: 1, takeover: false });
    // in flight: the button spins and is disabled, the stage and the last picture stay, no idle card
    expect(screen.getByTestId("screen-next")).toBeDisabled();
    expect(within(screen.getByTestId("screen-next")).getByRole("status", { name: "Switching" })).toBeInTheDocument();
    expect(screen.getByTestId("screen-overlay")).toHaveTextContent(SWITCH_COPY.switching(2));
    expect(SWITCH_COPY.switching(2)).toBe("Switching to screen 2…");
    expect(screen.getByTestId("screen-video")).toBe(shown);
    expect(screen.queryByTestId("screen-status")).toBeNull();
    expect(a.channel.messages().some((m) => m.type === "bye")).toBe(false);
    const answer = await answerTo(start, NEW_SESSION);
    await act(async () => {
      socket.receive(screenResult(start.command_id, { screen_session_id: NEW_SESSION, surfaces: TWO_SURFACES, surface_index: 1 }));
      socket.receive(answer);
      await flush();
    });
    await waitFor(() => expect(rt.screen.snapshot.incomingStream).not.toBeNull());
    const b = peers[peers.length - 1]!;
    await act(async () => {
      b.connect();
      await flush();
    });
    expect(screen.getByTestId("screen-overlay")).toHaveTextContent("Switching to screen 2…"); // still the old picture
    expect(screen.getByTestId("screen-video")).toBe(shown);
    // the new picture's first frame, on the hidden element
    await act(async () => {
      fireEvent(screen.getByTestId("screen-video-incoming"), new Event("timeupdate"));
      await flush();
    });
    await waitFor(() => expect(rt.screen.snapshot.switching).toBeNull());
    expect(screen.getByTestId("screen-video")).not.toBe(shown); // the hidden element is the shown one now
    expect(screen.queryByTestId("screen-overlay")).toBeNull();
    expect(screen.getByTestId("screen-note")).toHaveTextContent("Screen 2 of 2");
    expect(screen.getByTestId("screen-next")).toHaveAccessibleName("Next screen. Showing screen 2 of 2. Switch to screen 1.");
    expect(screen.getByTestId("screen-next")).toHaveTextContent("2/2");
    expect(screen.getByTestId("screen-next")).toBeEnabled();
    expect(a.closed).toBe(true);
    expect(commands("screen.session_stop")).toHaveLength(stops); // a switch is never a stop
  });

  it("while switching: the line names the screen over the kept picture; it gives way to 'Tap to show the picture' and to the consent wait", async () => {
    const stream = { id: "s" } as unknown as MediaStream;
    const switching: Partial<ScreenSessionState> = { phase: "starting", stream, surfaces: TWO_SURFACES, surfaceIndex: 0, switching: { to: 1, from: 0, startedAt: Date.now() } };
    const a = show(switching);
    const overlay = await screen.findByTestId("screen-overlay");
    expect(overlay).toHaveTextContent("Switching to screen 2…");
    expect(within(overlay).getByRole("status", { name: "Switching to screen 2…" })).toBeInTheDocument(); // the spinner
    expect(overlay).not.toHaveTextContent(COPY.connecting);
    expect(screen.getByTestId("screen-surface")).toBeInTheDocument();
    expect(screen.getByTestId("screen-next")).toBeDisabled();
    a.unmount();
    // the new picture's play() was refused (Low Power Mode): the tap comes first
    const b = show({ ...switching, phase: "connecting", verified: true, playHint: true });
    expect(await screen.findByRole("button", { name: COPY.tapToPlay })).toBeInTheDocument();
    expect(screen.queryByTestId("screen-overlay")).toBeNull();
    b.unmount();
    // the PC asks someone (a 1.4 agent in ask mode): the consent wait shows as always
    show({ ...switching, consentWaiting: true, consentDeadlineAt: Date.now() + 25_000 });
    expect(await screen.findByTestId("screen-overlay")).toHaveTextContent(/^Waiting for someone at the PC to allow this… \(2[45]\)/);
  });

  it("the short notes: 'Screen 2 of 2' for 1.5 s, a gone screen and a failed switch for 3 s (none when the Free time ran out), in that order, under the input notice", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const stream = { id: "s" } as unknown as MediaStream;
      const base: Partial<ScreenSessionState> = { phase: "live", verified: true, stream, sessionId: SCREEN_SESSION, surfaces: TWO_SURFACES, surfaceIndex: 1 };
      const update = (patch: Partial<ScreenSessionState>) => act(() => useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, pcId: PC, ...base, ...patch } }));
      show({ ...base, switched: { index: 1, count: 2, at: 1 } });
      const note = () => screen.queryByTestId("screen-note");
      expect(note()).toHaveTextContent(SWITCH_COPY.switched(2, 2));
      expect(note()).toHaveAttribute("role", "status");
      act(() => vi.advanceTimersByTime(1499));
      expect(note()).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(note()).toBeNull();
      update({ switched: null, switchedGone: { human: 2, at: 2 }, surfaceIndex: 0 });
      expect(note()).toHaveTextContent("Screen 2 isn't connected any more. Showing screen 1.");
      act(() => vi.advanceTimersByTime(2999));
      expect(note()).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(note()).toBeNull();
      // a failed switch wins over a fresh "Screen 1 of 2" (the old screen is still shown)
      update({ switchedGone: null, surfaceIndex: 0, switched: { index: 0, count: 2, at: 3 }, switchFailed: { code: "RATE_LIMITED", human: 1, at: 3 } });
      expect(note()).toHaveTextContent("Couldn't switch screens. Still showing screen 1.");
      act(() => vi.advanceTimersByTime(3000));
      expect(note()).toBeNull();
      // the Free time ran out: the exhausted state takes over, no note in between
      update({ switched: null, switchFailed: { code: "SCREEN_ALLOWANCE_EXHAUSTED", human: 1, at: 4 } });
      expect(note()).toBeNull();
      // the input notice ("View only") comes first
      act(() => useDevicesStore.setState({ grantsByPc: { [PC]: [VIEW_ONLY] } }));
      update({ switchFailed: null, switched: { index: 0, count: 2, at: 5 } });
      expect(screen.getByTestId("screen-input-notice")).toHaveTextContent(COPY.viewOnly);
      expect(note()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("the time left sits on line two, after 'Encrypted…' or the beta label; amber only on its own span when low", async () => {
    const stream = { id: "s" } as unknown as MediaStream;
    const live = show({ phase: "live", verified: true, stream, allowance: { unlimited: false, remainingSeconds: 420, resetsAt: null, at: Date.now() } });
    const left = await screen.findByTestId("screen-time-left");
    expect(left).toHaveTextContent(/About [67] min left/);
    expect(left.parentElement).toBe(screen.getByTestId("screen-encrypted").parentElement);
    expect(left.parentElement).toHaveTextContent("·");
    expect(left).not.toHaveClass("text-amber-200");
    live.unmount();
    show({ phase: "connecting", allowance: { unlimited: false, remainingSeconds: 40, resetsAt: null, at: Date.now() } });
    const low = await screen.findByTestId("screen-time-left");
    expect(low.parentElement).toHaveTextContent(SCREEN_BETA_LABEL);
    expect(low).toHaveClass("text-amber-200");
    expect(low.parentElement).not.toHaveClass("text-amber-200");
  });
});

describe("ScreenPage: a switch and what stays on the stage (1.5)", () => {
  it("a switch refused because the Free time ran out: no 'Couldn't switch' note, the time left reads 0 over the kept picture, then the exhausted state", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive({ surfaces: TWO_SURFACES });
    const asked = sent("screen_ice_request").length;
    await userEvent.click(screen.getByTestId("screen-next"));
    await waitFor(() => expect(sent("screen_ice_request").length).toBeGreaterThan(asked));
    const resetsAt = new Date(Date.now() + 3_600_000).toISOString();
    await act(async () => {
      socket.receive({ type: "error", error: { code: "SCREEN_ALLOWANCE_EXHAUSTED", message: "used", retryable: false, detail: { resets_at: resetsAt } }, ref_pc_id: PC });
      await flush();
    });
    await waitFor(() => expect(rt.screen.snapshot.switching).toBeNull());
    expect(rt.screen.snapshot.phase).toBe("live");
    expect(screen.getByTestId("screen-time-left")).toHaveTextContent("About 0 s left");
    expect(screen.getByTestId("screen-time-left")).toHaveClass("text-amber-200");
    expect(screen.queryByTestId("screen-note")).toBeNull();
    // the PC ends the old session when its last chunk runs out: the existing exhausted state, with when it refreshes
    await act(async () => {
      socket.receive({ type: "screen_session", pc_id: PC, screen_session_id: SCREEN_SESSION, controller_id: CONTROLLER, event: "ended", reason: "allowance_exhausted", live_seconds: 30, at: TS });
      await flush();
    });
    const card = await screen.findByTestId("screen-status");
    expect(card).toHaveAttribute("data-kind", "exhausted");
    expect(within(card).getByText(/^You've used Free Screen view for now\. Refreshes .+\. The touchpad and remote still work\.$/)).toBeInTheDocument();
  });

  it("Touchpad mode: the Drag row stays through a switch, so the kept picture is not laid out again and End Drag stays reachable", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive({ surfaces: TWO_SURFACES });
    await answerInputStart();
    await userEvent.click(screen.getByRole("button", { name: "Touchpad" }));
    await userEvent.click(screen.getByRole("button", { name: "Drag" }));
    expect(screen.getByRole("button", { name: "End Drag" })).toBeEnabled();
    const asked = sent("screen_ice_request").length;
    await userEvent.click(screen.getByTestId("screen-next"));
    await waitFor(() => expect(sent("screen_ice_request").length).toBeGreaterThan(asked));
    expect(rt.screen.snapshot.switching).not.toBeNull();
    expect(rt.screen.snapshot.phase).not.toBe("live");
    expect(screen.getByTestId("screen-overlay")).toHaveTextContent("Switching to screen 2…");
    const endDrag = screen.getByRole("button", { name: "End Drag" });
    expect(endDrag).toBeEnabled();
    await userEvent.click(endDrag);
    expect(screen.getByRole("button", { name: "Drag" })).toHaveAttribute("aria-pressed", "false");
  });

  it("zoomed with a view-only grant: the 1× button is drawn above the 'View only' line that covers the bottom of the picture", async () => {
    const stream = { id: "s" } as unknown as MediaStream;
    show({ phase: "live", verified: true, stream, sessionId: SCREEN_SESSION }, () => useDevicesStore.setState({ grantsByPc: { [PC]: [VIEW_ONLY] } }));
    const surface = await screen.findByTestId("screen-surface");
    boxed(surface);
    // a pinch zooms without any grant (nothing is sent)
    pointer(surface, "pointerdown", 1, 150, 150, 600);
    pointer(surface, "pointerdown", 2, 250, 150);
    pointer(surface, "pointermove", 1, 110, 150);
    pointer(surface, "pointermove", 2, 290, 150);
    pointer(surface, "pointerup", 1, 110, 150);
    pointer(surface, "pointerup", 2, 290, 150);
    const reset = await screen.findByRole("button", { name: "Back to the whole screen" });
    const notice = screen.getByTestId("screen-input-notice");
    expect(notice).toHaveTextContent(COPY.viewOnly);
    // the same stacking context, both at the bottom, the notice later in the tree: only a higher z-index keeps the
    // button visible (the notice lets touches through)
    const z = (el: HTMLElement) => Number(/(?:^|\s)z-\[?(\d+)\]?(?=\s|$)/.exec(el.className)?.[1] ?? NaN);
    expect(reset.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(z(reset)).toBeGreaterThan(z(notice));
    expect(notice).toHaveClass("pointer-events-none");
  });
});

describe("ScreenPage: the pointer drawn by the phone (1.5)", () => {
  it("a PC that announced 1.5 is asked for the pointer; `state{cursor: \"drawn\"}` says once that it stays in the picture", async () => {
    useDevicesStore.setState({ pcs: [{ ...OFFICE_PC, protocol_versions: V15 as never }] });
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    const peer = await goLive({ cursor: "overlay" });
    const start = commands("screen.session_start")[0]!;
    expect(start.params).toMatchObject({ cursor: "overlay" });
    expect(start.protocol_version).toBe("1.5");
    expect(screen.queryByTestId("screen-note")).toBeNull();
    await act(async () => {
      peer.channel.receive({ type: "state", state: "sending", cursor: "drawn" });
      await flush();
    });
    expect(rt.screen.snapshot.cursorMode).toBe("drawn");
    expect(screen.getByTestId("screen-note")).toHaveTextContent(COPY.pointerInPicture);
    expect(COPY.pointerInPicture).toBe("On this PC the pointer stays in the picture, so it can lag a little behind your finger.");
  });

  it("a 1.4 PC was never asked: no note, whatever it says", async () => {
    renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    const peer = await goLive();
    expect(commands("screen.session_start")[0]!.params).not.toHaveProperty("cursor");
    await act(async () => {
      peer.channel.receive({ type: "state", state: "sending" });
      await flush();
    });
    expect(screen.queryByTestId("screen-note")).toBeNull();
    expect(screen.queryByTestId("screen-pointer")).toBeNull();
  });

  it("the note shows once per session, for 3 s", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const stream = { id: "s" } as unknown as MediaStream;
      const base: Partial<ScreenSessionState> = { phase: "live", verified: true, stream, sessionId: SCREEN_SESSION, pointerOverlayAsked: true, cursorMode: "drawn" };
      const update = (patch: Partial<ScreenSessionState>) => act(() => useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, pcId: PC, ...base, ...patch } }));
      const note = () => screen.queryByTestId("screen-note");
      show(base);
      expect(note()).toHaveTextContent(COPY.pointerInPicture);
      act(() => vi.advanceTimersByTime(3000));
      expect(note()).toBeNull();
      update({ dataSaver: true }); // a later state message of the same session
      expect(note()).toBeNull();
      update({ sessionId: NEW_SESSION }); // a new session (a switch, a fresh start)
      expect(note()).toHaveTextContent(COPY.pointerInPicture);
      act(() => vi.advanceTimersByTime(3000));
      update({ sessionId: SCREEN_SESSION, pointerOverlayAsked: false, cursorMode: "drawn" });
      expect(note()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("ScreenPage: the gesture sheet, the explainer, a newer app (1.5)", () => {
  it("the explainer before the first session; three gesture lists with their exact rows", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("screen-status")).toHaveAttribute("data-kind", "ready"));
    expect(screen.getByText(EXPLAINER)).toBeInTheDocument();
    expect(screen.queryByTestId("screen-update-ready")).toBeNull();
    await userEvent.click(screen.getByText("Gestures on the picture"));
    const rows = (title: string) =>
      within(screen.getByRole("heading", { name: title }).closest("section")!)
        .getAllByRole("listitem")
        .map((li) => [li.children[0]!.textContent, li.children[1]!.textContent]);
    expect(rows("With Touchpad off")).toEqual([
      ["Tap", "Clicks right there"],
      ["Tap twice, quickly", "Double-click"],
      ["Tap with two fingers", "Right-click"],
      ["Press and hold, then slide", "A magnifier to aim with; let go to click"],
      ["Slide two fingers", "Scrolls"],
      ["Pinch", "Zooms the picture on your phone only; nothing is sent to the PC"],
      ["Slide one finger while zoomed", "Moves the zoomed picture"],
    ]);
    expect(rows("With Touchpad off")).toEqual(DIRECT_GESTURES.map((g) => [g.phone, g.pc]));
    expect(rows("With Touchpad on")).toEqual([
      ["Slide one finger", "Moves the pointer, like a laptop touchpad"],
      ["Tap", "Clicks where the pointer is"],
      ["Tap with two fingers", "Right-click"],
      ["Slide two fingers", "Scrolls"],
      ["Pinch", "Zooms the picture; the view follows the pointer"],
      ["Drag, then slide", "Holds the mouse button while you move; End Drag lets go"],
    ]);
    expect(rows("With Touchpad on")).toEqual(TOUCHPAD_SCREEN_GESTURES.map((g) => [g.phone, g.pc]));
    expect(rows("Either way")).toEqual([
      ["Screens button (top), only with two or more screens", "Shows the PC's other screen. On an up-to-date PC the pointer comes along. DoMe calls the main screen 1; Windows may number them differently."],
      ["1× (bottom left, while zoomed)", "Back to the whole screen"],
      ["The pointer you see", "Drawn by your phone so it moves at once; the picture catches up. On some PCs it stays in the picture instead."],
    ]);
  });

  it("a newer DoMe waiting (the device list came back in a newer shape) is said on the page", async () => {
    useDevicesStore.setState({ updateReady: true });
    renderPage();
    expect(await screen.findByTestId("screen-update-ready")).toHaveTextContent("A newer DoMe is ready. Close and reopen the app to update.");
    expect(COPY.updateReady).toBe("A newer DoMe is ready. Close and reopen the app to update.");
  });
});

describe("ScreenPage: a newer DoMe during Screen view (DECISIONS 92)", () => {
  it("never reloads while the picture is live, through a trip to the background and the fresh session after it, or after Stop on the page; it reloads once the page is left", async () => {
    const reload = vi.fn();
    const updateTimers = new FakeTimers();
    const updater = new AppUpdater({ check: async () => undefined, reload, busy: currentBusy, subscribeBusy, onChange: () => undefined, timers: updateTimers, now: () => updateTimers.now });
    const view = renderPage();
    await waitFor(() => screen.getByRole("button", { name: "Show screen" }));
    await goLive();
    updater.onNewVersion(); // the newer worker took control in the middle of the session
    await updateTimers.advance(10 * AFTER_BUSY_DELAY_MS);
    expect(reload).not.toHaveBeenCalled();

    // the background and back: the session stops (resume pending), then one fresh session starts by itself
    await act(async () => {
      setVisibility("hidden");
      await flush(6);
    });
    expect(rt.screen.snapshot.phase).toBe("idle");
    await updateTimers.advance(10 * AFTER_BUSY_DELAY_MS);
    await act(async () => {
      setVisibility("visible");
      await flush(6);
    });
    await waitFor(() => expect(sent("screen_ice_request")).toHaveLength(2));
    await updateTimers.advance(10 * AFTER_BUSY_DELAY_MS);
    expect(reload).not.toHaveBeenCalled();

    // Stop: the page stays open with "Stopped · …"; still no reload
    await userEvent.click(screen.getByRole("button", { name: "Stop Screen view" }));
    await waitFor(() => expect(screen.queryByTestId("screen-stage")).toBeNull());
    expect(rt.screen.snapshot.phase).toBe("ended");
    await updateTimers.advance(10 * AFTER_BUSY_DELAY_MS);
    expect(reload).not.toHaveBeenCalled();

    // leaving Screen view: the reload follows after the short pause
    view.unmount();
    expect(currentBusy()).toBeNull();
    await updateTimers.advance(AFTER_BUSY_DELAY_MS - 1);
    expect(reload).not.toHaveBeenCalled();
    await updateTimers.advance(1);
    expect(reload).toHaveBeenCalledTimes(1);
    updater.stop();
  });
});

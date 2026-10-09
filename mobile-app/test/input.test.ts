/**
 * InputSessionClient over the real Runtime, a fake socket and controllable timers/frames: session
 * start/stop lifecycle, signed batches (seq, coalescing, size), keepalive cadence, ack/session frames,
 * error-frame routing, stop on hidden / PC switch / sign-out / lost socket, and no text in the logs.
 */
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Runtime, setRuntimeForTests } from "../src/app/runtime.ts";
import { configureApi } from "../src/lib/api.ts";
import { resetDbHandleForTests } from "../src/lib/controllerKey.ts";
import { coalesceMotion, KEEPALIVE_MS, type InputEvent } from "../src/lib/input.ts";
import { clearLogs, log, recentLogs } from "../src/lib/log.ts";
import { useDevicesStore } from "../src/store/devices.ts";
import { useInputStore } from "../src/store/input.ts";
import { useLiveStore } from "../src/store/live.ts";
import { ACCOUNT, CONTROLLER, FakeSocket, flush, HELLO_ACK, noTimers, OFFICE_PC, OTHER_PC, PC, signedIn, TS } from "./helpers/harness.ts";

const SESSION_ID = "A".repeat(22);
const OTHER_SESSION = "B".repeat(22);

/** Deterministic, manually advanced timers for the input client. */
class FakeTimers {
  now = Date.now();
  private seq = 0;
  private timeouts = new Map<number, { at: number; fn: () => void }>();
  private intervals = new Map<number, { next: number; every: number; fn: () => void }>();
  setTimeout = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.timeouts.set(id, { at: this.now + ms, fn });
    return id;
  };
  clearTimeout = (h: unknown): void => {
    this.timeouts.delete(h as number);
  };
  setInterval = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.intervals.set(id, { next: this.now + ms, every: ms, fn });
    return id;
  };
  clearInterval = (h: unknown): void => {
    this.intervals.delete(h as number);
  };
  async advance(ms: number): Promise<void> {
    const target = this.now + ms;
    for (;;) {
      let nextAt = Infinity;
      for (const t of this.timeouts.values()) nextAt = Math.min(nextAt, t.at);
      for (const i of this.intervals.values()) nextAt = Math.min(nextAt, i.next);
      if (nextAt > target) break;
      this.now = nextAt;
      for (const [id, t] of [...this.timeouts]) {
        if (t.at <= this.now) {
          this.timeouts.delete(id);
          t.fn();
        }
      }
      for (const i of this.intervals.values()) {
        if (i.next <= this.now) {
          i.next += i.every;
          i.fn();
        }
      }
      await flush(3);
    }
    this.now = target;
  }
}

let socket: FakeSocket;
let sockets: FakeSocket[];
let rt: Runtime;
let timers: FakeTimers;
let frames: Array<() => void>;

async function runFrames(): Promise<void> {
  const fns = frames.splice(0, frames.length);
  for (const fn of fns) fn();
  await flush(8);
}

function sentFrames(): Array<Record<string, unknown>> {
  return socket.frames();
}

function payloadOf(frame: Record<string, unknown>): Record<string, unknown> {
  const env = frame.envelope as { payload: string };
  return JSON.parse(env.payload) as Record<string, unknown>;
}

function commandFrames(action: string): Array<{ frame: Record<string, unknown>; payload: Record<string, unknown> }> {
  return sentFrames()
    .filter((f) => f.type === "command")
    .map((f) => ({ frame: f, payload: payloadOf(f) }))
    .filter((c) => c.payload.action === action);
}

function batchFrames(): Array<{ frame: Record<string, unknown>; payload: Record<string, unknown> }> {
  return sentFrames()
    .filter((f) => f.type === "input_batch")
    .map((f) => ({ frame: f, payload: payloadOf(f) }));
}

function answerStart(overrides: Partial<{ pointer: boolean; keyboard: boolean; sessionId: string }> = {}): void {
  const starts = commandFrames("input.session_start");
  const last = starts[starts.length - 1]!;
  socket.receive({
    type: "result",
    command_id: last.payload.command_id,
    origin: "agent",
    state: "succeeded",
    at: TS,
    duration_ms: 12,
    result: { input_session_id: overrides.sessionId ?? SESSION_ID, lease_seconds: 3, input_age_budget_ms: 1000, max_batch_events: 64, pointer: overrides.pointer ?? true, keyboard: overrides.keyboard ?? true, foreground_app: { process_name: "chrome.exe", window_title: "YouTube - Chrome", browser: "chrome" } },
  });
}

async function startLive(): Promise<void> {
  const p = rt.input.start(PC);
  await flush();
  answerStart();
  await p;
  expect(rt.input.snapshot.phase).toBe("live");
}

beforeEach(async () => {
  globalThis.indexedDB = new IDBFactory();
  resetDbHandleForTests();
  signedIn();
  clearLogs();
  timers = new FakeTimers();
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
    inputNow: () => new Date(timers.now),
    scheduleFrame: (fn) => {
      frames.push(fn);
      return frames.length;
    },
  });
  setRuntimeForTests(rt);
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC, { ...OFFICE_PC, id: OTHER_PC, name: "Den PC" }], selectedPcId: PC });
  rt.start();
  for (let i = 0; i < 50 && sockets.length === 0; i++) await flush(); // the first ECDSA key generation can take a moment
  socket = sockets[0]!;
  socket.open();
  socket.receive(HELLO_ACK);
  useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
});

afterEach(() => {
  rt.stop();
  setRuntimeForTests(null);
  configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
});

describe("InputSessionClient", () => {
  it("input.session_start travels as a signed command; the result makes the session live with the PC's flags and foreground app", async () => {
    await startLive();
    const starts = commandFrames("input.session_start");
    expect(starts).toHaveLength(1);
    expect(starts[0]!.frame.pc_id).toBe(PC);
    expect(starts[0]!.payload.params).toEqual({ takeover: false }); // schema default applied by buildCommandPayload
    expect(starts[0]!.payload.account_id).toBe(ACCOUNT);
    expect(starts[0]!.payload.controller_id).toBe(CONTROLLER);
    const s = rt.input.snapshot;
    expect(s).toMatchObject({ phase: "live", pcId: PC, sessionId: SESSION_ID, pointer: true, keyboard: true, seq: 0, leaseSeconds: 3, maxBatchEvents: 64 });
    expect(s.foregroundApp?.browser).toBe("chrome");
    expect(useInputStore.getState().session.phase).toBe("live"); // store mirror
  });

  it("builds signed input_batch frames: seq strictly increasing, adjacent motion summed, nothing merged across a click, ≤ 64 events per batch", async () => {
    await startLive();
    const client = rt.input;
    expect(client.enqueue({ type: "pointer_move", dx: 3, dy: 1 })).toBe(true);
    client.enqueue({ type: "pointer_move", dx: 4, dy: -1 });
    client.enqueue({ type: "pointer_button", button: "left", action: "click" });
    client.enqueue({ type: "pointer_move", dx: 1, dy: 1 });
    client.enqueue({ type: "pointer_move", dx: 1, dy: 1 });
    expect(frames).toHaveLength(1); // one flush per animation frame
    await runFrames();
    let batches = batchFrames();
    expect(batches).toHaveLength(1);
    expect(batches[0]!.frame.pc_id).toBe(PC);
    const p = batches[0]!.payload;
    expect(p).toMatchObject({ type: "input_batch", protocol_version: "1.1", account_id: ACCOUNT, controller_id: CONTROLLER, target_pc_id: PC, input_session_id: SESSION_ID, seq: 1 });
    expect(p.events).toEqual([
      { type: "pointer_move", dx: 7, dy: 0 },
      { type: "pointer_button", button: "left", action: "click" },
      { type: "pointer_move", dx: 2, dy: 2 },
    ]);
    expect(new Date(p.expires_at as string).getTime() - new Date(p.issued_at as string).getTime()).toBe(5000);
    // a burst of 70 distinct events splits into 64 + 6, seq 2 and 3
    for (let i = 0; i < 70; i++) client.enqueue({ type: "pointer_button", button: "left", action: i % 2 ? "down" : "up" });
    await runFrames();
    batches = batchFrames();
    expect(batches.map((b) => b.payload.seq)).toEqual([1, 2, 3]);
    expect((batches[1]!.payload.events as unknown[]).length).toBe(64);
    expect((batches[2]!.payload.events as unknown[]).length).toBe(6);
    expect(rt.input.snapshot.seq).toBe(3);
  });

  it("coalesceMotion sums only adjacent pointer_move events and clamps to ±4096", () => {
    const events: InputEvent[] = [
      { type: "pointer_move", dx: 4000, dy: 0 },
      { type: "pointer_move", dx: 4000, dy: 5 },
      { type: "text", text: "a" },
      { type: "pointer_move", dx: 1, dy: 1 },
      { type: "pointer_scroll", dx: 0, dy: 1 },
      { type: "pointer_move", dx: 1, dy: 1 },
    ];
    expect(coalesceMotion(events)).toEqual([
      { type: "pointer_move", dx: 4096, dy: 5 },
      { type: "text", text: "a" },
      { type: "pointer_move", dx: 1, dy: 1 },
      { type: "pointer_scroll", dx: 0, dy: 1 },
      { type: "pointer_move", dx: 1, dy: 1 },
    ]);
  });

  it("sends an empty keepalive batch about every second while idle, and none while real batches flow", async () => {
    await startLive();
    expect(batchFrames()).toHaveLength(0);
    await timers.advance(KEEPALIVE_MS);
    await flush();
    let batches = batchFrames();
    expect(batches).toHaveLength(1);
    expect(batches[0]!.payload.events).toEqual([]);
    expect(batches[0]!.payload.seq).toBe(1);
    await timers.advance(KEEPALIVE_MS * 3);
    await flush();
    expect(batchFrames()).toHaveLength(4);
    // real traffic resets the cadence: a batch just sent means no keepalive at the next tick
    rt.input.enqueue({ type: "pointer_move", dx: 1, dy: 0 });
    await runFrames();
    const before = batchFrames().length;
    await timers.advance(KEEPALIVE_MS - 100);
    await flush();
    batches = batchFrames();
    expect(batches.length).toBe(before);
    for (let i = 1; i < batches.length; i++) expect(batches[i]!.payload.seq).toBe((batches[i - 1]!.payload.seq as number) + 1);
  });

  it("input_ack updates the live indicator (Windows acceptance, held buttons) and resolves awaitAck; only acks for this session count", async () => {
    await startLive();
    rt.input.enqueue({ type: "pointer_button", button: "left", action: "down" });
    const seq = await (async () => {
      await runFrames();
      return rt.input.snapshot.seq;
    })();
    const pending = rt.input.awaitAck(seq, 3000);
    socket.receive({ type: "input_ack", pc_id: PC, input_session_id: OTHER_SESSION, last_seq: 9, accepted_events: 9, dropped_events: 0, held_buttons: [], held_keys: [], at: TS });
    expect(rt.input.snapshot.lastAck).toBeNull();
    socket.receive({ type: "input_ack", pc_id: PC, input_session_id: SESSION_ID, last_seq: seq, accepted_events: 1, dropped_events: 0, held_buttons: ["left"], held_keys: [], at: TS });
    expect(rt.input.snapshot.lastAck).toMatchObject({ lastSeq: seq, acceptedEvents: 1, droppedEvents: 0 });
    expect(rt.input.snapshot.heldButtons).toEqual(["left"]);
    await expect(pending).resolves.toBe("accepted");
    // a later ack that reports drops around a waited batch → "dropped" (uncertain, never resent)
    rt.input.enqueue({ type: "text", text: "hi" });
    await runFrames();
    const seq2 = rt.input.snapshot.seq;
    const p2 = rt.input.awaitAck(seq2, 3000);
    socket.receive({ type: "input_ack", pc_id: PC, input_session_id: SESSION_ID, last_seq: seq2, accepted_events: 1, dropped_events: 1, held_buttons: [], held_keys: [], at: TS });
    await expect(p2).resolves.toBe("dropped");
    // timeout path
    rt.input.enqueue({ type: "key", key: "enter" });
    await runFrames();
    const p3 = rt.input.awaitAck(rt.input.snapshot.seq, 500);
    await timers.advance(600);
    await expect(p3).resolves.toBe("timeout");
  });

  it("input_session ended/suspended frames end the stream with the agent's reason; nothing is queued or replayed afterwards", async () => {
    await startLive();
    socket.receive({ type: "input_session", pc_id: PC, input_session_id: SESSION_ID, controller_id: CONTROLLER, event: "ended", reason: "lease_expired", holds_released: 1, at: TS });
    const s = rt.input.snapshot;
    expect(s.phase).toBe("ended");
    expect(s.endReason).toBe("lease_expired");
    expect(s.holdsReleased).toBe(1);
    expect(s.problem?.code).toBe("INPUT_SESSION_EXPIRED");
    expect(rt.input.enqueue({ type: "pointer_move", dx: 1, dy: 1 })).toBe(false);
    const before = batchFrames().length;
    await timers.advance(KEEPALIVE_MS * 2);
    await runFrames();
    expect(batchFrames().length).toBe(before); // no keepalive, no replay
    // suspended → backpressure, fresh start required
    await startLive();
    socket.receive({ type: "input_session", pc_id: PC, input_session_id: SESSION_ID, controller_id: CONTROLLER, event: "suspended", reason: "backpressure", holds_released: 0, at: TS });
    expect(rt.input.snapshot.phase).toBe("suspended");
    expect(rt.input.snapshot.problem?.code).toBe("INPUT_SUSPENDED");
  });

  it("INPUT_SESSION_OWNED marks the takeover prompt; a takeover start carries params.takeover=true", async () => {
    const p = rt.input.start(PC);
    await flush();
    const start = commandFrames("input.session_start")[0]!;
    socket.receive({ type: "result", command_id: start.payload.command_id, origin: "agent", state: "failed", at: TS, duration_ms: 3, error: { code: "INPUT_SESSION_OWNED", message: "owned", retryable: false } });
    await p;
    expect(rt.input.snapshot.phase).toBe("failed");
    expect(rt.input.snapshot.owned).toBe(true);
    expect(rt.input.snapshot.problem?.code).toBe("INPUT_SESSION_OWNED");
    const p2 = rt.input.start(PC, { takeover: true });
    await flush();
    const starts = commandFrames("input.session_start");
    expect(starts).toHaveLength(2);
    expect(starts[1]!.payload.params).toEqual({ takeover: true });
    answerStart();
    await p2;
    expect(rt.input.snapshot.phase).toBe("live");
    expect(rt.input.snapshot.owned).toBe(false);
  });

  it("the page being hidden stops the session with input.session_stop for the live session id and discards queued events", async () => {
    await startLive();
    rt.input.enqueue({ type: "pointer_move", dx: 5, dy: 5 });
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await flush();
    const stops = commandFrames("input.session_stop");
    expect(stops).toHaveLength(1);
    expect(stops[0]!.payload.params).toEqual({ input_session_id: SESSION_ID });
    expect(rt.input.snapshot.phase).toBe("idle");
    await runFrames();
    expect(batchFrames()).toHaveLength(0); // the queued move was dropped, not sent late
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  it("switching the selected PC stops the old session before anything can target the new PC", async () => {
    await startLive();
    await useDevicesStore.getState().select(OTHER_PC);
    await flush();
    expect(commandFrames("input.session_stop")).toHaveLength(1);
    expect(rt.input.snapshot.phase).toBe("idle");
    // starting on the other PC while an old session is still recorded also stops it first
    useDevicesStore.setState({ selectedPcId: PC });
    await startLive();
    const p = rt.input.start(OTHER_PC);
    await flush();
    expect(commandFrames("input.session_stop")).toHaveLength(2);
    const starts = commandFrames("input.session_start");
    expect(starts[starts.length - 1]!.frame.pc_id).toBe(OTHER_PC);
    answerStart({ sessionId: OTHER_SESSION });
    await p;
    expect(rt.input.snapshot.pcId).toBe(OTHER_PC);
  });

  it("sign-out stops the session before the socket closes; a lost socket ends it locally without sending", async () => {
    await startLive();
    configureApi({ fetchImpl: async () => new Response(null, { status: 204 }) });
    await rt.signOut();
    const stops = commandFrames("input.session_stop");
    expect(stops).toHaveLength(1);
    expect(stops[0]!.payload.params).toEqual({ input_session_id: SESSION_ID });
    expect(socket.closed).not.toBeNull();
    expect(rt.input.snapshot.phase).toBe("idle");
    // fresh runtime state for the lost-socket case
    signedIn();
    rt.relay.connect();
    for (let i = 0; i < 50 && sockets.length < 2; i++) await flush();
    socket = sockets[sockets.length - 1]!;
    socket.open();
    socket.receive(HELLO_ACK);
    useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
    await startLive();
    const sentBefore = socket.sent.length;
    socket.serverClose(1006);
    await flush();
    expect(rt.input.snapshot.phase).toBe("ended");
    expect(rt.input.snapshot.endReason).toBe("controller_disconnected");
    expect(socket.sent.length).toBe(sentBefore); // nothing could be sent on a dead socket
    expect(rt.input.enqueue({ type: "pointer_move", dx: 1, dy: 1 })).toBe(false);
  });

  it("INPUT_* error frames with ref_pc_id go to the input client, never mark the subscription refused, and keep the session alive for non-fatal codes", async () => {
    await startLive();
    socket.receive({ type: "error", error: { code: "INPUT_SEQUENCE_INVALID", message: "x", retryable: false }, ref_pc_id: PC });
    expect(rt.input.snapshot.phase).toBe("live");
    expect(rt.input.snapshot.problem?.code).toBe("INPUT_SEQUENCE_INVALID");
    expect(useLiveStore.getState().pcs[PC]!.refused).toBeNull();
    rt.input.clearProblem();
    expect(rt.input.snapshot.problem).toBeNull();
    socket.receive({ type: "error", error: { code: "INPUT_NOT_PERMITTED", message: "x", retryable: false }, ref_pc_id: PC });
    expect(rt.input.snapshot.problem?.code).toBe("INPUT_NOT_PERMITTED");
    expect(useLiveStore.getState().pcs[PC]!.refused).toBeNull();
    // a rejection naming another (old) session never ends the current one
    socket.receive({ type: "error", error: { code: "INPUT_SESSION_EXPIRED", message: "x", retryable: true }, ref_pc_id: PC, ref_input_session_id: "Z".repeat(22) });
    expect(rt.input.snapshot.phase).toBe("live");
    const sid = rt.input.snapshot.sessionId!;
    socket.receive({ type: "error", error: { code: "INPUT_SESSION_EXPIRED", message: "x", retryable: true }, ref_pc_id: PC, ref_input_session_id: sid });
    expect(rt.input.snapshot.phase).toBe("ended");
    // a subscription refusal still works as before
    socket.receive({ type: "error", error: { code: "GRANT_MISSING", message: "x", retryable: false }, ref_pc_id: PC });
    expect(useLiveStore.getState().pcs[PC]!.refused).toBe("GRANT_MISSING");
  });

  it("a session start whose grant lacks keyboard refuses text events locally; pointer-only flags are honoured", async () => {
    const p = rt.input.start(PC);
    await flush();
    answerStart({ keyboard: false });
    await p;
    expect(rt.input.enqueue({ type: "text", text: "a" })).toBe(false);
    expect(rt.input.enqueue({ type: "shortcut", name: "ctrl_a" })).toBe(false);
    expect(rt.input.enqueue({ type: "pointer_move", dx: 1, dy: 0 })).toBe(true);
  });

  it("typed text never reaches the log: neither through the client nor through a careless log call", async () => {
    await startLive();
    rt.input.enqueue({ type: "text", text: "s3cret passphrase" });
    rt.input.enqueue({ type: "key", key: "enter" });
    rt.input.enqueue({ type: "shortcut", name: "ctrl_l" });
    await runFrames();
    socket.receive({ type: "error", error: { code: "INPUT_TARGET_CHANGED", message: "s3cret passphrase", retryable: false }, ref_pc_id: PC });
    log.info("careless", { text: "s3cret passphrase", composer: "s3cret passphrase", events: [{ type: "text", text: "s3cret" }], nested: { text: "s3cret" } });
    const dump = JSON.stringify(recentLogs());
    expect(dump).not.toContain("s3cret");
    expect(dump).not.toContain("passphrase");
    expect(dump).toContain("INPUT_TARGET_CHANGED"); // codes survive
    // the batch itself did carry the text (it is the payload), signed and validated
    const last = batchFrames().pop()!;
    expect(last.payload.events).toEqual([
      { type: "text", text: "s3cret passphrase" },
      { type: "key", key: "enter" },
      { type: "shortcut", name: "ctrl_l" },
    ]);
  });
});

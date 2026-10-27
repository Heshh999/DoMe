/**
 * Flow control of the manual-input stream (DECISIONS 79), on InputSessionClient alone with fake frames, a fake clock
 * and a stub PC: flushes never exceed limits.input_batches_per_second under a 120 Hz (or 60 Hz) animation frame, nor
 * under a cadence between the frame multiples, and lose no motion; motion coalesces across the frames it waits for
 * exactly as within one, while scroll, wheel and buttons keep order and count and a batch never exceeds 64 events; a
 * button, key, text or shortcut never waits for the gate; the rate halves while the PC's acks lag (with hysteresis) or
 * while INPUT_STALE notices arrive, and recovers when they stop; the unacknowledged table stays bounded; flush() still
 * sends at once; a new session starts with a clean gate.
 */
import { beforeAll, describe, expect, it } from "vitest";

import { exportPublicJwk, generateControllerKeyPair, kidFromJwk, LIMITS, type InputBatchPayload } from "@dome/protocol";

import type { CommandRecord } from "../src/lib/commands.ts";
import type { ControllerIdentity } from "../src/lib/controllerKey.ts";
import { FLUSH_INTERVAL_MS, InputSessionClient, type InputEvent, type InputSessionState } from "../src/lib/input.ts";
import { FakeTimers } from "./helpers/fakeTimers.ts";
import { ACCOUNT, CONTROLLER, PC, TS } from "./helpers/harness.ts";

const SESSION_ID = "A".repeat(22);
const RATE = LIMITS.input_batches_per_second;
const FRAME_120HZ_MS = 1000 / 120;
const FRAME_60HZ_MS = 1000 / 60;

const move = (dx: number, dy: number): InputEvent => ({ type: "pointer_move", dx, dy });
const moveTo = (x: number, y: number): InputEvent => ({ type: "pointer_move_to", x, y, gen: 1 });
const scroll = (dx: number, dy: number): InputEvent => ({ type: "pointer_scroll", dx, dy });
const wheel = (dx: number, dy: number): InputEvent => ({ type: "pointer_wheel", dx, dy });
const click: InputEvent = { type: "pointer_button", button: "left", action: "click" };

let identity: ControllerIdentity;

beforeAll(async () => {
  const keyPair = await generateControllerKeyPair();
  const jwk = await exportPublicJwk(keyPair.publicKey);
  identity = { keyPair, jwk, kid: await kidFromJwk(jwk) };
});

interface SentBatch {
  /** Fake clock when the frame left the client. */
  at: number;
  payload: InputBatchPayload;
}

interface Harness {
  client: InputSessionClient;
  timers: FakeTimers;
  frames: Array<() => void>;
  sent: SentBatch[];
  states: InputSessionState[];
  /** One animation frame `ms` later: run what was scheduled and wait for the signing. */
  frame(ms: number): Promise<void>;
  /** The PC's input_ack naming `lastSeq` as the newest accepted batch. */
  ack(lastSeq: number): void;
}

function record(action: string): CommandRecord {
  return { commandId: `cmd-${action}`, pcId: PC, action, params: {}, target: null, protocolVersion: "1.1", createdAt: 0, expiresAt: TS, state: "accepted", ackAt: null, confirmation: null, terminal: null, noAnswer: false, source: "system" };
}

function settledStart(): CommandRecord {
  const result = { input_session_id: SESSION_ID, lease_seconds: LIMITS.input_lease_seconds, input_age_budget_ms: LIMITS.input_age_budget_ms, max_batch_events: LIMITS.input_batch_max_events, pointer: true, keyboard: true };
  return { ...record("input.session_start"), state: "succeeded", terminal: { origin: "agent", state: "succeeded", at: TS, durationMs: 5, result, resultInvalid: false, error: null, warning: null } };
}

async function live(): Promise<Harness> {
  const timers = new FakeTimers();
  const frames: Array<() => void> = [];
  const sent: SentBatch[] = [];
  const states: InputSessionState[] = [];
  const client = new InputSessionClient({
    sendBatch: (frame) => sent.push({ at: timers.now, payload: JSON.parse(frame.envelope.payload) as InputBatchPayload }),
    sendCommand: async (input) => record(input.action),
    onceSettled: async () => settledStart(),
    identity: () => ({ accountId: ACCOUNT, controllerId: CONTROLLER }),
    signingKey: async () => identity,
    connected: () => true,
    onChange: (s) => states.push(s),
    timers,
    now: () => new Date(timers.now),
    scheduleFrame: (fn) => {
      frames.push(fn);
      return fn;
    },
    cancelFrame: (h) => {
      const i = frames.indexOf(h as () => void);
      if (i >= 0) frames.splice(i, 1);
    },
  });
  await client.start(PC);
  expect(client.snapshot.phase).toBe("live");
  return {
    client,
    timers,
    frames,
    sent,
    states,
    async frame(ms) {
      timers.now += ms;
      for (const fn of frames.splice(0, frames.length)) fn();
      await client.whenSent();
    },
    ack(lastSeq) {
      client.handleFrame({ type: "input_ack", pc_id: PC, input_session_id: SESSION_ID, last_seq: lastSeq, accepted_events: lastSeq, dropped_events: 0, held_buttons: [], held_keys: [], at: TS });
    },
  };
}

/** `seconds` of continuous motion, one move per frame of `frameMs`; `pc` runs after every frame (the acks). Returns how many batches went out. */
async function swipe(h: Harness, seconds: number, frameMs: number, pc: () => void = () => undefined): Promise<number> {
  const before = h.sent.length;
  const frames = Math.round((seconds * 1000) / frameMs);
  for (let i = 0; i < frames; i++) {
    h.client.enqueue(move(1, 2));
    await h.frame(frameMs);
    pc();
  }
  return h.sent.length - before;
}

/** A PC `transitMs` away that acks four times a second (rules.input_sessions (4)), each ack naming the newest batch that has had time to arrive. */
function pcAcking(h: Harness, transitMs: number): () => void {
  let lastAckAt = -Infinity;
  let lastSeq = 0;
  return () => {
    if (h.timers.now - lastAckAt < 250) return;
    const due = h.sent.filter((s) => s.at <= h.timers.now - transitMs).at(-1);
    if (!due || due.payload.seq <= lastSeq) return;
    lastAckAt = h.timers.now;
    lastSeq = due.payload.seq;
    h.ack(lastSeq);
  };
}

/** The relay's bucket (input_batches_per_second sustained) never drains: any RATE + 1 consecutive batches span at least a second (to within frame rounding). */
function expectWithinBudget(sent: SentBatch[], seconds: number): void {
  expect(sent.length).toBeLessThanOrEqual(RATE * seconds + 1);
  for (let i = 0; i + RATE < sent.length; i++) expect(sent[i + RATE]!.at - sent[i]!.at).toBeGreaterThanOrEqual(1000 - 1);
}

function sumMotion(sent: SentBatch[]): { dx: number; dy: number } {
  let dx = 0;
  let dy = 0;
  for (const s of sent) {
    for (const ev of s.payload.events) {
      if (ev.type === "pointer_move") {
        dx += ev.dx;
        dy += ev.dy;
      }
    }
  }
  return { dx, dy };
}

describe("flush rate", () => {
  it("never exceeds limits.input_batches_per_second under a 120 Hz animation frame, loses no motion, keeps seq consecutive", async () => {
    const h = await live();
    const frames = 240; // 2 s of continuous motion, a healthy PC acking
    await swipe(h, 2, FRAME_120HZ_MS, pcAcking(h, 100));
    await h.frame(FLUSH_INTERVAL_MS * 2); // the motion the last frames held goes out
    expectWithinBudget(h.sent, 2);
    // close to the budget, not far under it: the frames nearest the interval (3 × 8.33 ms = 25 ms → 40/s)
    expect(h.sent.length).toBeGreaterThanOrEqual(RATE * 2 - 2);
    expect(h.sent.map((s) => s.payload.seq)).toEqual(h.sent.map((_, i) => i + 1));
    expect(h.sent.every((s) => s.payload.events.length === 1 && s.payload.events[0]!.type === "pointer_move")).toBe(true);
    expect(sumMotion(h.sent)).toEqual({ dx: frames, dy: 2 * frames });
  });

  it("on a 60 Hz display flushes every second frame (30/s): the largest frame multiple under the budget", async () => {
    const h = await live();
    const frames = 120;
    await swipe(h, 2, FRAME_60HZ_MS, pcAcking(h, 100));
    await h.frame(FLUSH_INTERVAL_MS * 2);
    expectWithinBudget(h.sent, 2);
    expect(h.sent.length).toBeGreaterThanOrEqual(frames / 2 - 1);
    expect(h.sent.length).toBeLessThanOrEqual(frames / 2 + 2);
    expect(sumMotion(h.sent)).toEqual({ dx: frames, dy: 2 * frames });
  });
});

describe("frame cadences between the multiples", () => {
  it("a 23.5 ms cadence (just over the interval) never exceeds the budget: the last second's count is the gate's second guard", async () => {
    const h = await live();
    const frames = Math.round(5000 / 23.5);
    await swipe(h, 5, 23.5, pcAcking(h, 100));
    await h.frame(FLUSH_INTERVAL_MS * 2);
    await h.frame(FLUSH_INTERVAL_MS * 2); // whatever the window still held goes out
    expectWithinBudget(h.sent, 5);
    expect(h.sent.length).toBeGreaterThanOrEqual(RATE * 5 - 6); // held frames coalesce into the next, the rate stays near 40/s
    expect(sumMotion(h.sent)).toEqual({ dx: frames, dy: 2 * frames });
  });

  it("a 24 ms cadence (a hair under the interval; the clock is whole milliseconds) flushes every frame: the slack absorbs timestamp jitter, the window guard caps it", async () => {
    const h = await live();
    await swipe(h, 1, 24, pcAcking(h, 100));
    await h.frame(FLUSH_INTERVAL_MS * 2);
    expectWithinBudget(h.sent, 1);
    expect(h.sent.length).toBeGreaterThanOrEqual(RATE - 2); // without the slack every second frame would wait: 21/s
  });
});

describe("coalescing across held frames", () => {
  it("sums adjacent moves, keeps the last move_to, keeps scroll/wheel/button order and count, splits at 64 events", async () => {
    const h = await live();
    h.client.enqueue(move(1, 1));
    await h.frame(FRAME_120HZ_MS); // seq 1 arms the gate
    expect(h.sent).toHaveLength(1);
    // two more frames inside the interval: nothing goes out, everything coalesces as it would within one frame
    h.client.enqueue(move(3, 1));
    h.client.enqueue(scroll(0, 1));
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(1);
    h.client.enqueue(move(1, 1));
    h.client.enqueue(move(2, 2));
    h.client.enqueue(moveTo(1, 1));
    h.client.enqueue(moveTo(2, 2));
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(1);
    // the third frame is a flush interval after the last flush
    h.client.enqueue(wheel(0, 120));
    h.client.enqueue(wheel(0, 120));
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(2);
    expect(h.sent[1]!.payload.seq).toBe(2);
    expect(h.sent[1]!.payload.events).toEqual([move(3, 1), scroll(0, 1), move(3, 3), moveTo(2, 2), wheel(0, 120), wheel(0, 120)]);
    expect(h.sent[1]!.payload.protocol_version).toBe("1.4"); // a Screen view event is in it
    // 70 scroll notches gathered over held frames: never merged, split 64 + 6 in order, seq 3 and 4
    for (let i = 0; i < 70; i++) h.client.enqueue(scroll(0, i % 2 ? 1 : -1));
    await h.frame(FRAME_120HZ_MS);
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(2);
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(4);
    expect(h.sent.slice(2).map((s) => [s.payload.seq, s.payload.events.length])).toEqual([
      [3, LIMITS.input_batch_max_events],
      [4, 70 - LIMITS.input_batch_max_events],
    ]);
    const notches = h.sent.slice(2).flatMap((s) => s.payload.events);
    expect(notches.every((ev, i) => ev.type === "pointer_scroll" && ev.dy === (i % 2 ? 1 : -1))).toBe(true);
  });
});

describe("discrete events", () => {
  it("a button, key, text or shortcut goes on the very next frame even inside the interval, taking held motion along in order; motion alone waits", async () => {
    const h = await live();
    h.client.enqueue(move(1, 0));
    await h.frame(FRAME_120HZ_MS); // seq 1
    h.client.enqueue(move(1, 0));
    await h.frame(FRAME_120HZ_MS); // held
    expect(h.sent).toHaveLength(1);
    h.client.enqueue(click);
    await h.frame(FRAME_120HZ_MS); // 16.7 ms after the flush: the click does not wait
    expect(h.sent).toHaveLength(2);
    expect(h.sent[1]!.payload.events).toEqual([move(1, 0), click]);
    h.client.enqueue({ type: "key", key: "enter" });
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(3);
    h.client.enqueue({ type: "text", text: "hi" });
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(4);
    h.client.enqueue({ type: "shortcut", name: "ctrl_a" });
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(5);
    expect(h.sent.map((s) => s.payload.seq)).toEqual([1, 2, 3, 4, 5]);
    // motion alone after that waits for the interval again
    h.client.enqueue(move(2, 0));
    await h.frame(FRAME_120HZ_MS);
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(5);
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(6);
    expect(h.sent[5]!.payload.events).toEqual([move(2, 0)]);
  });
});

describe("back-pressure", () => {
  it("halves the flush rate while the PC's acks lag by more than half the age budget over the session's fastest round trip, and recovers when they catch up", async () => {
    const h = await live();
    const healthyPc = pcAcking(h, 100);
    const silentPc = () => undefined;
    expect(await swipe(h, 1, FRAME_120HZ_MS, healthyPc)).toBeGreaterThanOrEqual(RATE - 2);
    expect(h.client.snapshot.congested).toBe(false);
    // the acks stop: within the next second the oldest unacknowledged batch is more than half the budget late
    const stalling = await swipe(h, 1, FRAME_120HZ_MS, silentPc);
    expect(h.client.snapshot.congested).toBe(true);
    expect(stalling).toBeLessThan(RATE);
    const congested = await swipe(h, 1, FRAME_120HZ_MS, silentPc);
    expect(congested).toBeLessThanOrEqual(RATE / 2 + 1);
    expect(congested).toBeGreaterThanOrEqual(RATE / 2 - 2);
    // a tap still goes on the next frame while congested
    const before = h.sent.length;
    h.client.enqueue(click);
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent.length).toBe(before + 1);
    // the PC catches up: one ack for everything sent → recovered at once, full rate again
    h.ack(h.client.snapshot.seq);
    expect(h.client.snapshot.congested).toBe(false);
    expect(await swipe(h, 1, FRAME_120HZ_MS, healthyPc)).toBeGreaterThanOrEqual(RATE - 2);
    // the store saw both transitions; the stream itself stayed in order with nothing lost or replayed
    expect(h.states.some((s) => s.congested)).toBe(true);
    expect(h.states.at(-1)!.congested).toBe(false);
    expect(h.sent.map((s) => s.payload.seq)).toEqual(h.sent.map((_, i) => i + 1));
  });

  it("recovers only once the excess is back under a quarter of the budget (hysteresis), not already under a half", async () => {
    const h = await live();
    const t0 = h.timers.now;
    h.client.enqueue(move(1, 0));
    await h.client.flush(); // seq 1 at t0
    h.timers.now = t0 + 50;
    h.ack(1); // the floor: a 50 ms round trip
    h.timers.now = t0 + 100;
    h.client.enqueue(move(1, 0));
    await h.client.flush(); // seq 2
    h.timers.now = t0 + 400;
    h.client.enqueue(move(1, 0));
    await h.client.flush(); // seq 3
    // t0 + 750: the oldest unacknowledged batch (seq 2) is 650 ms out, 600 over the floor → congested
    h.client.enqueue(move(1, 0));
    await h.frame(350);
    expect(h.client.snapshot.congested).toBe(true);
    expect(h.sent).toHaveLength(4);
    // seq 2 acknowledged: seq 3 is 360 ms out, 310 over the floor — under a half, not yet under a quarter: still congested
    h.timers.now = t0 + 760;
    h.ack(2);
    expect(h.client.snapshot.congested).toBe(true);
    // seq 3 acknowledged: only the batch just sent is out → recovered
    h.timers.now = t0 + 770;
    h.ack(3);
    expect(h.client.snapshot.congested).toBe(false);
  });

  it("an INPUT_STALE notice spaces flushes out even when acks keep settling the batches the relay dropped", async () => {
    const h = await live();
    // the relay of DECISIONS cloud-api #112 under load: it routes only the newest batch every half second and says
    // INPUT_STALE once a second (ref_pc_id, no session id); the PC acks the routed batch 100 ms later, which settles
    // every older batch as if acknowledged, so the lag measure alone never sees the drops
    let lastRoute = -Infinity;
    let lastNotice = -Infinity;
    let routed = 0;
    const relay = () => {
      const now = h.timers.now;
      if (now - lastRoute >= 500 && h.sent.length > 0) {
        lastRoute = now;
        routed = h.sent.at(-1)!.payload.seq;
      }
      if (routed && h.sent.find((s) => s.payload.seq === routed)!.at <= now - 100) h.ack(routed);
      if (now - lastNotice >= 1000 && h.sent.length > 0) {
        lastNotice = now;
        h.client.handleFrame({ type: "error", error: { code: "INPUT_STALE", message: "Input was delayed too long to deliver safely and was discarded. Try again.", retryable: true }, ref_pc_id: PC });
      }
    };
    const sent = await swipe(h, 3, FRAME_120HZ_MS, relay);
    expect(h.states.some((s) => s.congested)).toBe(true);
    expect(sent).toBeLessThanOrEqual((RATE * 3) / 2 + 4); // the halved rate, from the first notice on
    expect(sent).toBeGreaterThanOrEqual((RATE * 3) / 2 - 4);
    expect(h.client.snapshot.problem).toEqual({ code: "INPUT_STALE" });
    // the notices stop and the PC catches up: recovered once the hold is over
    h.timers.now += 2000;
    h.ack(h.client.snapshot.seq);
    expect(h.client.snapshot.congested).toBe(false);
  });

  it("remembers at most input_batches_per_second × input_batch_lifetime_seconds unacknowledged batches", async () => {
    const h = await live();
    const max = LIMITS.input_batches_per_second * LIMITS.input_batch_lifetime_seconds;
    for (let i = 0; i < max + 20; i++) {
      h.client.enqueue(click);
      await h.client.flush();
      h.timers.now += FLUSH_INTERVAL_MS;
    }
    expect(h.sent).toHaveLength(max + 20);
    const table = (h.client as unknown as { sentAt: Map<number, number> }).sentAt;
    expect(table.size).toBe(max);
    expect(Math.min(...table.keys())).toBe(21); // the oldest were forgotten first (they have expired everywhere anyway)
  });

  it("a constant network delay never counts as congestion: the floor is this session's fastest round trip", async () => {
    const h = await live();
    // a far-away PC: every ack arrives 400 ms after the batch it names left
    expect(await swipe(h, 3, FRAME_120HZ_MS, pcAcking(h, 400))).toBeGreaterThanOrEqual(RATE * 3 - 4);
    expect(h.states.some((s) => s.congested)).toBe(false);
  });
});

describe("explicit flush and a fresh session", () => {
  it("flush() sends at once regardless of the gate (Screen view taps, Compose and Send) and resolves with the seq awaitAck waits for", async () => {
    const h = await live();
    h.client.enqueue(move(1, 0));
    await h.frame(FRAME_120HZ_MS);
    h.client.enqueue(moveTo(100, 200));
    h.client.enqueue(click);
    const seq = await h.client.flush();
    expect(seq).toBe(2);
    expect(h.sent).toHaveLength(2);
    expect(h.frames).toHaveLength(0); // the frame the enqueue had scheduled was cancelled
    expect(h.sent[1]!.payload.events).toEqual([moveTo(100, 200), click]);
    const outcome = h.client.awaitAck(seq!);
    h.ack(2);
    await expect(outcome).resolves.toBe("accepted");
    // the gate restarts from the explicit flush: motion right after it waits
    h.client.enqueue(move(1, 0));
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(2);
    await h.frame(FLUSH_INTERVAL_MS);
    expect(h.sent).toHaveLength(3);
  });

  it("a new session starts with a clean gate, floor and flag", async () => {
    const h = await live();
    h.client.enqueue(move(1, 0));
    await h.frame(FRAME_120HZ_MS);
    expect(h.sent).toHaveLength(1);
    await h.client.stop("stopped");
    await h.client.start(PC);
    expect(h.client.snapshot.phase).toBe("live");
    expect(h.client.snapshot.congested).toBe(false);
    h.client.enqueue(move(1, 0));
    await h.frame(FRAME_120HZ_MS); // 8 ms after the previous session's flush: the new session is not gated by it
    expect(h.sent).toHaveLength(2);
    expect(h.sent[1]!.payload.seq).toBe(1);
  });
});

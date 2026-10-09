/**
 * Manual-input session client (protocol 1.1, version.json rules.input_sessions).
 *
 * Lifecycle: `input.session_start` travels as an ordinary signed command (so it inherits the ack/result
 * semantics); the PC answers with the session id, lease and what the grant allows. From then on
 * events travel as `controller_input_batch` frames: ≤ max_batch_events per batch, `seq` strictly
 * increasing per session, adjacent pointer motion summed before signing (never across a button,
 * scroll, text, key or shortcut), one flush per animation frame, an empty keepalive batch about every
 * second while nothing else was sent. `input_ack` frames report Windows acceptance (not an application
 * effect); `input_session` frames end or suspend the session with the agent's reason. Leaving the
 * screen, Stop Input, the page being hidden, a PC switch, sign-out or a dropped socket stops the
 * session; nothing queued is ever replayed afterwards.
 *
 * No event content is logged: only counts, codes and phases (`log.ts` additionally drops any field
 * named text/composer).
 */
import { buildInputBatchPayload, LIMITS, ProtocolError, signInputBatch, type InputEvent, type relayFrames } from "@dome/protocol";

import type { CommandRecord, Identity } from "./commands.ts";
import { errorSummary, log } from "./log.ts";

export type { InputEvent };
export type ForegroundApp = relayFrames.ForegroundApp;
export type InputEndReason = relayFrames.AgentInputSession["reason"];
export type InputPhase = "idle" | "starting" | "live" | "suspended" | "ended" | "failed";

export interface InputAckSummary {
  lastSeq: number;
  acceptedEvents: number;
  droppedEvents: number;
  at: string;
  /** Wall-clock ms when this phone received the ack. */
  receivedAt: number;
}

export interface InputSessionState {
  phase: InputPhase;
  pcId: string | null;
  sessionId: string | null;
  pointer: boolean;
  keyboard: boolean;
  foregroundApp: ForegroundApp | null;
  leaseSeconds: number;
  ageBudgetMs: number;
  maxBatchEvents: number;
  /** Last sequence number sent in this session (0 before the first batch). */
  seq: number;
  lastAck: InputAckSummary | null;
  heldButtons: string[];
  heldKeys: string[];
  /** Why the session is not live (error code), or a non-fatal notice while it is (e.g. INPUT_SEQUENCE_INVALID). */
  problem: { code: string; message?: string } | null;
  endReason: InputEndReason | null;
  /** Count of buttons/keys the PC released when the session ended (from input_session). */
  holdsReleased: number;
  /** Another phone owns the PC's session: a takeover prompt is needed. */
  owned: boolean;
  startedAt: number | null;
  lastSentAt: number | null;
  /** Milliseconds since the last ack while live, re-evaluated when `tick()` runs. */
  batchesSent: number;
}

export const EMPTY_INPUT_STATE: InputSessionState = {
  phase: "idle",
  pcId: null,
  sessionId: null,
  pointer: false,
  keyboard: false,
  foregroundApp: null,
  leaseSeconds: LIMITS.input_lease_seconds,
  ageBudgetMs: LIMITS.input_age_budget_ms,
  maxBatchEvents: LIMITS.input_batch_max_events,
  seq: 0,
  lastAck: null,
  heldButtons: [],
  heldKeys: [],
  problem: null,
  endReason: null,
  holdsReleased: 0,
  owned: false,
  startedAt: null,
  lastSentAt: null,
  batchesSent: 0,
};

export interface InputTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(h: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(h: unknown): void;
}

export interface InputClientDeps {
  /** Validated `controller_input_batch` send; throws when the socket is not open. */
  sendBatch(frame: relayFrames.ControllerInputBatch): void;
  /** Signed command through the ordinary command path; resolves with the record once sent. */
  sendCommand(input: { pcId: string; action: string; params: Record<string, boolean | string>; source: CommandRecord["source"] }): Promise<CommandRecord>;
  /** Resolves when the record is terminal or flagged "no answer" (null: dropped by a reset). */
  onceSettled(commandId: string): Promise<CommandRecord | null>;
  identity(): Identity | null;
  keyPair(): Promise<CryptoKeyPair>;
  connected(): boolean;
  onChange(state: InputSessionState): void;
  timers?: InputTimers;
  now?: () => Date;
  /** Schedules the per-frame flush (requestAnimationFrame in the browser, a short timeout elsewhere). */
  scheduleFrame?: (fn: () => void) => unknown;
  cancelFrame?: (h: unknown) => void;
}

export type StopReason = "stopped" | "leave" | "hidden" | "pc_switch" | "sign_out" | "connection_lost";

/** Keepalive cadence: the lease is 3 s; an empty batch roughly every second keeps it renewed. */
export const KEEPALIVE_MS = 1000;
const ACK_WAIT_MS = 3000;

const globalTimers: InputTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

function defaultScheduleFrame(fn: () => void): unknown {
  if (typeof requestAnimationFrame === "function") return { raf: requestAnimationFrame(() => fn()) };
  return { t: setTimeout(fn, 16) };
}

function defaultCancelFrame(h: unknown): void {
  const x = h as { raf?: number; t?: ReturnType<typeof setTimeout> };
  if (x.raf !== undefined && typeof cancelAnimationFrame === "function") cancelAnimationFrame(x.raf);
  if (x.t !== undefined) clearTimeout(x.t);
}

const MOTION_MAX = LIMITS.input_motion_max;

export function needsPointer(ev: InputEvent): boolean {
  return ev.type === "pointer_move" || ev.type === "pointer_button" || ev.type === "pointer_scroll";
}

/** Sum adjacent pointer_move events (bounded); never merge anything else. Pure, used by flush(). */
export function coalesceMotion(events: readonly InputEvent[]): InputEvent[] {
  const out: InputEvent[] = [];
  for (const ev of events) {
    const prev = out[out.length - 1];
    if (ev.type === "pointer_move" && prev && prev.type === "pointer_move") {
      const dx = Math.max(-MOTION_MAX, Math.min(MOTION_MAX, prev.dx + ev.dx));
      const dy = Math.max(-MOTION_MAX, Math.min(MOTION_MAX, prev.dy + ev.dy));
      out[out.length - 1] = { type: "pointer_move", dx, dy };
      continue;
    }
    out.push(ev);
  }
  return out;
}

export class InputSessionClient {
  private readonly deps: InputClientDeps;
  private readonly timers: InputTimers;
  private state: InputSessionState = { ...EMPTY_INPUT_STATE };
  private pending: InputEvent[] = [];
  private frameHandle: unknown = null;
  private keepaliveHandle: unknown = null;
  private chain: Promise<void> = Promise.resolve();
  private droppedBaseline = 0;
  private ackWaiters: Array<{ seq: number; droppedBefore: number; resolve: (r: AckOutcome) => void; timer: unknown }> = [];
  private startNonce = 0;

  constructor(deps: InputClientDeps) {
    this.deps = deps;
    this.timers = deps.timers ?? globalTimers;
  }

  get snapshot(): InputSessionState {
    return this.state;
  }

  get isLive(): boolean {
    return this.state.phase === "live";
  }

  private set(patch: Partial<InputSessionState>): void {
    this.state = { ...this.state, ...patch };
    this.deps.onChange(this.state);
  }

  private now(): Date {
    return this.deps.now?.() ?? new Date();
  }

  /**
   * Start (or take over) the manual-input session on `pcId`. A session live on another PC is stopped
   * first (`pc_switch`). Resolves with the resulting state; never throws for a PC-side refusal — the
   * state carries the error code (INPUT_SESSION_OWNED sets `owned` for the takeover prompt).
   */
  async start(pcId: string, options: { takeover?: boolean } = {}): Promise<InputSessionState> {
    if (this.state.pcId && this.state.pcId !== pcId && this.state.phase !== "idle") await this.stop("pc_switch");
    if (this.state.phase === "live" && this.state.pcId === pcId) return this.state;
    if (this.state.phase === "starting") return this.state;
    const nonce = ++this.startNonce;
    this.clearTimers();
    this.pending = [];
    this.set({ ...EMPTY_INPUT_STATE, phase: "starting", pcId });
    if (!this.deps.connected()) {
      this.set({ phase: "failed", problem: { code: "PC_RECONNECTING" } });
      return this.state;
    }
    let record: CommandRecord;
    try {
      record = await this.deps.sendCommand({ pcId, action: "input.session_start", params: options.takeover ? { takeover: true } : {}, source: "system" });
    } catch (e) {
      const code = e instanceof ProtocolError ? e.code : "INTERNAL";
      log.warn("input.start_send_failed", errorSummary(e));
      if (nonce === this.startNonce) this.set({ phase: "failed", problem: { code } });
      return this.state;
    }
    const settled = await this.deps.onceSettled(record.commandId);
    if (nonce !== this.startNonce) return this.state; // superseded by stop()/another start
    const t = settled?.terminal ?? null;
    if (!t) {
      this.set({ phase: "failed", problem: { code: "NO_ANSWER" } });
      return this.state;
    }
    if (t.state !== "succeeded" || !t.result || t.resultInvalid) {
      const code = t.error?.code ?? (t.state === "succeeded" ? "MALFORMED_MESSAGE" : t.state === "outcome_unknown" ? "OUTCOME_UNKNOWN" : "COMMAND_EXPIRED");
      log.info("input.start_refused", { code, state: t.state });
      this.set({ phase: "failed", problem: { code, ...(t.error?.message ? { message: t.error.message } : {}) }, owned: code === "INPUT_SESSION_OWNED" });
      return this.state;
    }
    const r = t.result as { input_session_id: string; lease_seconds: number; input_age_budget_ms: number; max_batch_events: number; pointer: boolean; keyboard: boolean; foreground_app?: ForegroundApp | null };
    this.droppedBaseline = 0;
    this.set({
      phase: "live",
      sessionId: r.input_session_id,
      pointer: r.pointer,
      keyboard: r.keyboard,
      foregroundApp: r.foreground_app ?? null,
      leaseSeconds: r.lease_seconds,
      ageBudgetMs: r.input_age_budget_ms,
      maxBatchEvents: Math.min(r.max_batch_events, LIMITS.input_batch_max_events),
      seq: 0,
      startedAt: this.now().getTime(),
      lastSentAt: this.now().getTime(),
      problem: null,
      endReason: null,
      owned: false,
    });
    log.info("input.session_started", { pc_id: pcId, pointer: r.pointer, keyboard: r.keyboard });
    this.keepaliveHandle = this.timers.setInterval(() => this.keepalive(), KEEPALIVE_MS);
    return this.state;
  }

  /**
   * End the session: discard anything queued, stop the keepalive, tell the PC (`input.session_stop`
   * releases what it still holds) and go idle. Safe to call in any phase.
   */
  async stop(reason: StopReason = "stopped"): Promise<void> {
    this.startNonce += 1; // a start in flight must not resurrect the session
    const { phase, pcId, sessionId } = this.state;
    this.clearTimers();
    this.pending = [];
    this.resolveWaiters("ended");
    if (phase === "idle") return;
    this.set({ ...EMPTY_INPUT_STATE, pcId, phase: "idle" });
    if (sessionId && pcId && this.deps.connected() && (phase === "live" || phase === "suspended" || phase === "starting")) {
      try {
        await this.deps.sendCommand({ pcId, action: "input.session_stop", params: { input_session_id: sessionId }, source: "system" });
        log.info("input.session_stop_sent", { pc_id: pcId, reason });
      } catch (e) {
        // The PC's lease (3 s) ends the session and releases holds on its own.
        log.info("input.session_stop_not_sent", { reason, ...errorSummary(e) });
      }
    } else {
      log.info("input.session_left", { reason, phase });
    }
  }

  /** The relay socket left `open`: the stream is gone; the PC's lease releases any hold. */
  onConnectionLost(): void {
    if (this.state.phase !== "live" && this.state.phase !== "starting" && this.state.phase !== "suspended") return;
    this.startNonce += 1;
    this.clearTimers();
    this.pending = [];
    this.resolveWaiters("ended");
    this.set({ phase: "ended", endReason: "controller_disconnected", problem: { code: "PC_RECONNECTING" } });
    log.info("input.session_connection_lost");
  }

  /**
   * Queue one event for the next flush. Returns false (nothing queued) when the session is not live
   * or the grant does not cover the event type — the UI keeps those controls disabled anyway.
   */
  enqueue(event: InputEvent): boolean {
    if (this.state.phase !== "live") return false;
    if (needsPointer(event) ? !this.state.pointer : !this.state.keyboard) return false;
    const last = this.pending[this.pending.length - 1];
    if (event.type === "pointer_move" && last && last.type === "pointer_move") {
      this.pending[this.pending.length - 1] = coalesceMotion([last, event])[0]!;
    } else {
      this.pending.push(event);
    }
    this.scheduleFlush();
    return true;
  }

  private scheduleFlush(): void {
    if (this.frameHandle !== null) return;
    const schedule = this.deps.scheduleFrame ?? defaultScheduleFrame;
    this.frameHandle = schedule(() => {
      this.frameHandle = null;
      void this.flush();
    });
  }

  /**
   * Sign and send everything queued, in order, as batches of at most maxBatchEvents. Resolves with
   * the seq of the last batch sent (null when nothing was queued or the session is not live).
   */
  flush(): Promise<number | null> {
    if (this.frameHandle !== null) {
      (this.deps.cancelFrame ?? defaultCancelFrame)(this.frameHandle);
      this.frameHandle = null;
    }
    if (this.state.phase !== "live" || this.pending.length === 0) return Promise.resolve(null);
    const events = coalesceMotion(this.pending);
    this.pending = [];
    const batches: InputEvent[][] = [];
    for (let i = 0; i < events.length; i += this.state.maxBatchEvents) batches.push(events.slice(i, i + this.state.maxBatchEvents));
    let result: Promise<number | null> = Promise.resolve(null);
    for (const batch of batches) result = this.sendBatch(batch);
    return result;
  }

  /** Serialised through `chain` so signatures complete in seq order even though signing is async. */
  private sendBatch(events: InputEvent[]): Promise<number | null> {
    const sessionId = this.state.sessionId;
    const pcId = this.state.pcId;
    const nonce = this.startNonce;
    const run = async (): Promise<number | null> => {
      if (nonce !== this.startNonce || this.state.phase !== "live" || !sessionId || !pcId) return null;
      const identity = this.deps.identity();
      if (!identity) {
        this.set({ phase: "ended", endReason: "controller_disconnected", problem: { code: "UNKNOWN_KEY" } });
        return null;
      }
      const seq = this.state.seq + 1;
      const payload = buildInputBatchPayload({ accountId: identity.accountId, controllerId: identity.controllerId, targetPcId: pcId, inputSessionId: sessionId, seq, events, now: this.now() });
      try {
        const pair = await this.deps.keyPair();
        const envelope = await signInputBatch(pair.privateKey, pair.publicKey, payload);
        if (nonce !== this.startNonce || this.state.phase !== "live") return null;
        this.deps.sendBatch({ type: "input_batch", pc_id: pcId, envelope });
      } catch (e) {
        log.warn("input.batch_send_failed", { ...errorSummary(e), events: events.length });
        if (e instanceof ProtocolError && e.code === "PC_RECONNECTING") this.onConnectionLost();
        return null;
      }
      this.set({ seq, lastSentAt: this.now().getTime(), batchesSent: this.state.batchesSent + 1 });
      return seq;
    };
    const p = this.chain.then(run, run);
    this.chain = p.then(
      () => undefined,
      () => undefined,
    );
    return p;
  }

  private keepalive(): void {
    if (this.state.phase !== "live") return;
    const since = this.now().getTime() - (this.state.lastSentAt ?? 0);
    if (this.pending.length > 0 || since < KEEPALIVE_MS - 50) return;
    void this.sendBatch([]);
  }

  /** Returns true when the frame concerned this client (consumed). */
  handleFrame(frame: relayFrames.RelayToController): boolean {
    switch (frame.type) {
      case "input_ack":
        return this.onAck(frame);
      case "input_session":
        return this.onSession(frame);
      case "error":
        return this.onError(frame);
      default:
        return false;
    }
  }

  private onAck(frame: relayFrames.AgentInputAck): boolean {
    if (!this.state.sessionId || frame.input_session_id !== this.state.sessionId) return false;
    const ack: InputAckSummary = { lastSeq: frame.last_seq, acceptedEvents: frame.accepted_events, droppedEvents: frame.dropped_events, at: frame.at, receivedAt: this.now().getTime() };
    this.set({ lastAck: ack, heldButtons: [...frame.held_buttons], heldKeys: [...frame.held_keys] });
    const still: typeof this.ackWaiters = [];
    for (const w of this.ackWaiters) {
      if (ack.lastSeq >= w.seq) {
        this.timers.clearTimeout(w.timer);
        w.resolve(ack.droppedEvents > w.droppedBefore ? "dropped" : "accepted");
      } else still.push(w);
    }
    this.ackWaiters = still;
    return true;
  }

  private onSession(frame: relayFrames.AgentInputSession): boolean {
    if (frame.pc_id !== this.state.pcId) return false;
    if (!this.state.sessionId || frame.input_session_id !== this.state.sessionId) {
      // Someone else's session on our PC: only relevant if it supersedes ours (our own `ended{takeover}` follows).
      return false;
    }
    if (frame.event === "started") return true;
    this.clearTimers();
    this.pending = [];
    this.startNonce += 1;
    this.resolveWaiters("ended");
    const problem = frame.event === "suspended" ? { code: "INPUT_SUSPENDED" } : { code: endCode(frame.reason) };
    this.set({ phase: frame.event === "suspended" ? "suspended" : "ended", endReason: frame.reason, holdsReleased: frame.holds_released, problem, heldButtons: [], heldKeys: [] });
    log.info("input.session_" + frame.event, { reason: frame.reason, holds_released: frame.holds_released });
    return true;
  }

  private onError(frame: relayFrames.ErrorFrame): boolean {
    const code = frame.error.code;
    if (!frame.ref_pc_id || frame.ref_pc_id !== this.state.pcId) return false;
    const inputCode = code.startsWith("INPUT_") || code === "RATE_LIMITED";
    const live = this.state.phase === "live" || this.state.phase === "starting";
    if (!inputCode && !(live && (code === "PC_OFFLINE" || code === "PC_RECONNECTING"))) return false;
    log.info("input.batch_rejected", { code });
    if (code === "INPUT_SESSION_EXPIRED" || code === "INPUT_SESSION_REQUIRED" || code === "PC_OFFLINE" || code === "PC_RECONNECTING") {
      this.clearTimers();
      this.pending = [];
      this.startNonce += 1;
      this.resolveWaiters("ended");
      this.set({ phase: "ended", problem: { code }, endReason: this.state.endReason ?? (code === "INPUT_SESSION_EXPIRED" ? "lease_expired" : "controller_disconnected") });
    } else if (code === "INPUT_SUSPENDED") {
      this.clearTimers();
      this.pending = [];
      this.resolveWaiters("ended");
      this.set({ phase: "suspended", problem: { code }, endReason: "backpressure" });
    } else if (code === "INPUT_NOT_PERMITTED") {
      this.resolveWaiters("dropped");
      this.set({ problem: { code } });
    } else {
      // INPUT_SEQUENCE_INVALID / INPUT_STALE / INPUT_RESTRICTED / INPUT_INJECTION_FAILED / INPUT_TARGET_CHANGED / RATE_LIMITED:
      // the batch was dropped, the session continues; the UI shows the notice.
      this.resolveWaiters("dropped");
      this.set({ problem: { code } });
    }
    return inputCode;
  }

  /** Clears a non-fatal notice once the customer read it. */
  clearProblem(): void {
    if (this.state.problem && (this.state.phase === "live" || this.state.phase === "idle")) this.set({ problem: null });
  }

  /** The PC reported a new foreground window (state frame): live typing must pause when it changed. */
  setForegroundApp(app: ForegroundApp | null): void {
    if (JSON.stringify(app) === JSON.stringify(this.state.foregroundApp)) return;
    this.set({ foregroundApp: app });
  }

  /**
   * Wait for the PC's acceptance of batch `seq`: "accepted" (Windows accepted every event up to it),
   * "dropped" (the agent reported dropped events meanwhile — the batch may or may not have been among
   * them), "timeout" (no ack in time) or "ended" (the session stopped first). Never resends.
   */
  awaitAck(seq: number, timeoutMs = ACK_WAIT_MS): Promise<AckOutcome> {
    if (this.state.phase !== "live") return Promise.resolve("ended");
    if (this.state.lastAck && this.state.lastAck.lastSeq >= seq) return Promise.resolve("accepted");
    return new Promise<AckOutcome>((resolve) => {
      const waiter = { seq, droppedBefore: this.state.lastAck?.droppedEvents ?? 0, resolve, timer: null as unknown };
      waiter.timer = this.timers.setTimeout(() => {
        this.ackWaiters = this.ackWaiters.filter((w) => w !== waiter);
        resolve("timeout");
      }, timeoutMs);
      this.ackWaiters.push(waiter);
    });
  }

  private resolveWaiters(outcome: AckOutcome): void {
    for (const w of this.ackWaiters) {
      this.timers.clearTimeout(w.timer);
      w.resolve(outcome);
    }
    this.ackWaiters = [];
  }

  private clearTimers(): void {
    if (this.keepaliveHandle !== null) this.timers.clearInterval(this.keepaliveHandle);
    this.keepaliveHandle = null;
    if (this.frameHandle !== null) (this.deps.cancelFrame ?? defaultCancelFrame)(this.frameHandle);
    this.frameHandle = null;
  }

  /** Sign-out / runtime stop: forget everything without sending (the socket is already closed). */
  reset(): void {
    this.startNonce += 1;
    this.clearTimers();
    this.pending = [];
    this.resolveWaiters("ended");
    this.set({ ...EMPTY_INPUT_STATE });
  }
}

export type AckOutcome = "accepted" | "dropped" | "timeout" | "ended";

/** Error code that best explains an input_session end reason (copy lives in errors.json / labels.ts). */
export function endCode(reason: InputEndReason): string {
  switch (reason) {
    case "lease_expired":
      return "INPUT_SESSION_EXPIRED";
    case "takeover":
      return "INPUT_SESSION_OWNED";
    case "controller_revoked":
      return "CONTROLLER_REVOKED";
    case "grant_removed":
      return "INPUT_NOT_PERMITTED";
    case "session_locked":
      return "PC_SESSION_LOCKED";
    case "secure_desktop":
      return "INPUT_RESTRICTED";
    case "backpressure":
      return "INPUT_SUSPENDED";
    case "remote_disabled":
      return "PC_REMOTE_DISABLED";
    case "controller_disconnected":
    case "agent_restart":
      return "PC_RECONNECTING";
    case "pc_switch":
    case "stopped":
    case "started":
      return "INPUT_SESSION_REQUIRED";
  }
}

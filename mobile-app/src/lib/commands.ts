/**
 * Command lifecycle with truthful states. "Sent" means the frame left the phone; `accepted` means
 * the PC received it; only a terminal `result` says what happened. A local timer flags commands that
 * got no answer without claiming failure; the relay's own deadline rule eventually terminates them.
 */
import { buildCommandPayload, ProtocolError, registry, signCommand, type JsonValue, type relayFrames } from "@dome/protocol";

import { checkChallengeBinding, parseChallenge, type ParsedChallenge } from "./confirmations.ts";
import { buildConfirmationEnvelope } from "./confirmations.ts";
import { errorSummary, log } from "./log.ts";

export type LifecycleState = relayFrames.LifecycleState;

export interface TerminalOutcome {
  origin: "agent" | "relay";
  state: "succeeded" | "failed" | "expired" | "canceled" | "outcome_unknown";
  at: string;
  durationMs: number;
  /** Validated against the action's result schema; absent when the PC sent none or it was invalid. */
  result: Record<string, unknown> | null;
  /** The PC reported success but its result did not match the contract; details are not shown. */
  resultInvalid: boolean;
  error: relayFrames.Error | null;
  warning: string | null;
}

export interface PendingConfirmation {
  parsed: ParsedChallenge;
  /** null = bound to this command; otherwise why Approve is not offered. */
  bindingProblem: string | null;
  receivedAt: number;
  /**
   * "approve"/"decline" once the customer answered (the result frame still decides the outcome);
   * "dismissed" when the customer closed the modal locally — nothing was sent to the PC, which
   * discards the challenge when it expires.
   */
  decision: "approve" | "decline" | "dismissed" | null;
  /** The relay socket dropped while this was pending: the countdown is not a live promise any more. */
  connectionLost: boolean;
}

export interface CommandRecord {
  commandId: string;
  pcId: string;
  action: string;
  params: Record<string, JsonValue>;
  target: Record<string, JsonValue> | null;
  createdAt: number;
  expiresAt: string;
  state: LifecycleState;
  ackAt: string | null;
  confirmation: PendingConfirmation | null;
  terminal: TerminalOutcome | null;
  /** No frame arrived by expires_at + grace; the UI says "no answer yet", never "failed". */
  noAnswer: boolean;
  /** Where the command came from (for the per-page "last result"). */
  source: "button" | "text" | "slider" | "system";
}

export interface Identity {
  accountId: string;
  controllerId: string;
}

export interface CommandServiceDeps {
  send(frame: relayFrames.ControllerToRelay): void;
  /** Socket open? Checked before identity so a dropped connection reads as PC_RECONNECTING, never as "not paired". */
  connected?(): boolean;
  identity(): Identity | null;
  keyPair(): Promise<CryptoKeyPair>;
  onChange(record: CommandRecord): void;
  timers?: { setTimeout(fn: () => void, ms: number): unknown; clearTimeout(h: unknown): void };
  now?: () => Date;
}

export interface SendInput {
  pcId: string;
  action: string;
  params?: Record<string, JsonValue>;
  target?: Record<string, JsonValue> | null;
  lifetimeSeconds?: number;
  source?: CommandRecord["source"];
}

const NO_ANSWER_GRACE_MS = 10_000;
const MAX_RECORDS = 200;
const TERMINAL: ReadonlySet<string> = new Set(["succeeded", "failed", "expired", "canceled", "outcome_unknown"]);

export class CommandService {
  private readonly records = new Map<string, CommandRecord>();
  private readonly timers = new Map<string, unknown>();
  private readonly deps: CommandServiceDeps;

  constructor(deps: CommandServiceDeps) {
    this.deps = deps;
  }

  get(commandId: string): CommandRecord | undefined {
    return this.records.get(commandId);
  }

  list(): CommandRecord[] {
    return [...this.records.values()].sort((a, b) => b.createdAt - a.createdAt);
  }

  /** Build, validate, sign and send. Throws (and records nothing) when it cannot be sent at all. */
  async send(input: SendInput): Promise<CommandRecord> {
    this.requireConnection();
    const identity = this.deps.identity();
    if (!identity) throw new ProtocolError("UNKNOWN_KEY", "This phone is not paired yet.");
    const now = this.deps.now?.() ?? new Date();
    const payload = buildCommandPayload({
      accountId: identity.accountId,
      controllerId: identity.controllerId,
      targetPcId: input.pcId,
      action: input.action,
      params: input.params ?? {},
      target: input.target ?? null,
      ...(input.lifetimeSeconds ? { lifetimeSeconds: input.lifetimeSeconds } : {}),
      now,
    });
    const keyPair = await this.deps.keyPair();
    const envelope = await signCommand(keyPair.privateKey, keyPair.publicKey, payload);
    const frame: relayFrames.ControllerCommand = { type: "command", pc_id: input.pcId, envelope };
    this.deps.send(frame); // validates the frame; throws when the socket is not open
    const record: CommandRecord = {
      commandId: payload.command_id,
      pcId: input.pcId,
      action: payload.action,
      params: payload.params,
      target: payload.target,
      createdAt: now.getTime(),
      expiresAt: payload.expires_at,
      state: "created",
      ackAt: null,
      confirmation: null,
      terminal: null,
      noAnswer: false,
      source: input.source ?? "button",
    };
    this.store(record);
    const spec = registry.get(payload.action);
    const extra = spec.confirmation === "challenge" ? 60_000 : 0;
    const wait = Math.max(0, new Date(payload.expires_at).getTime() - now.getTime()) + spec.timeoutMs + extra + NO_ANSWER_GRACE_MS;
    this.arm(record.commandId, wait);
    log.info("command.sent", { action: record.action, command_id: record.commandId, pc_id: record.pcId });
    return record;
  }

  /** Best-effort cancel (queued or power countdown). The result frame still decides the outcome. */
  cancel(commandId: string): void {
    const record = this.records.get(commandId);
    if (!record || record.terminal) return;
    this.deps.send({ type: "cancel", pc_id: record.pcId, command_id: commandId });
    log.info("command.cancel_sent", { command_id: commandId });
  }

  /** Returns true when the frame belonged to a command this service tracks. */
  handleFrame(frame: relayFrames.RelayToController): boolean {
    switch (frame.type) {
      case "ack":
        return this.onAck(frame);
      case "confirmation_required":
        return this.onConfirmationRequired(frame);
      case "result":
        return this.onResult(frame);
      default:
        return false;
    }
  }

  async respondToChallenge(commandId: string, decision: "approve" | "decline"): Promise<void> {
    const record = this.records.get(commandId);
    if (!record?.confirmation) throw new ProtocolError("CONFIRMATION_INVALID", "nothing to confirm");
    if (record.terminal) throw new ProtocolError("CONFIRMATION_EXPIRED", "the command already finished");
    if (decision === "approve" && record.confirmation.bindingProblem) throw new ProtocolError("CONFIRMATION_INVALID", record.confirmation.bindingProblem);
    this.requireConnection();
    const identity = this.deps.identity();
    if (!identity) throw new ProtocolError("UNKNOWN_KEY", "This phone is not paired yet.");
    const keyPair = await this.deps.keyPair();
    const frame = await buildConfirmationEnvelope({ parsed: record.confirmation.parsed, controllerId: identity.controllerId, decision, keyPair, ...(this.deps.now ? { now: this.deps.now() } : {}) });
    this.deps.send(frame);
    record.confirmation = { ...record.confirmation, decision };
    this.store(record);
    log.info("confirmation.sent", { command_id: commandId, decision });
  }

  /**
   * Local escape hatch for the confirmation modal: closes it without answering the PC. No frame is
   * sent; the PC's challenge simply expires and the relay's deadline rule terminates the command.
   */
  dismissConfirmation(commandId: string): void {
    const record = this.records.get(commandId);
    if (!record?.confirmation || record.confirmation.decision !== null) return;
    record.confirmation = { ...record.confirmation, decision: "dismissed" };
    this.store(record);
    log.info("confirmation.dismissed", { command_id: commandId });
  }

  /** The relay socket left `open`: every pending confirmation's countdown is no longer backed by a live connection. */
  markConnectionLost(): void {
    this.setConnectionLost(true);
  }

  /** The socket is open again; a still-pending challenge can be answered. */
  markConnectionRestored(): void {
    this.setConnectionLost(false);
  }

  private setConnectionLost(lost: boolean): void {
    for (const record of this.records.values()) {
      if (!record.confirmation || record.terminal || record.confirmation.decision !== null || record.confirmation.connectionLost === lost) continue;
      record.confirmation = { ...record.confirmation, connectionLost: lost };
      this.store(record);
    }
  }

  /** Called on sign-out: drop every record and timer. */
  reset(): void {
    for (const h of this.timers.values()) (this.deps.timers ?? globalTimers).clearTimeout(h);
    this.timers.clear();
    this.records.clear();
    for (const w of this.settledWaiters.values()) for (const resolve of w) resolve(null);
    this.settledWaiters.clear();
  }

  /**
   * Resolves once the record is terminal or flagged "no answer yet" (null when the record is unknown or
   * dropped by reset()). Used by the input-session client for `input.session_start`.
   */
  onceSettled(commandId: string): Promise<CommandRecord | null> {
    const record = this.records.get(commandId);
    if (!record) return Promise.resolve(null);
    if (record.terminal || record.noAnswer) return Promise.resolve({ ...record });
    return new Promise((resolve) => {
      const list = this.settledWaiters.get(commandId) ?? [];
      list.push(resolve);
      this.settledWaiters.set(commandId, list);
    });
  }

  private readonly settledWaiters = new Map<string, Array<(r: CommandRecord | null) => void>>();

  private onAck(frame: relayFrames.AgentAck): boolean {
    const record = this.records.get(frame.command_id);
    if (!record) return false;
    if (record.terminal) return true; // late ack after terminal: ignore
    record.state = frame.state;
    record.ackAt = frame.at;
    record.noAnswer = false;
    this.store(record);
    return true;
  }

  private onConfirmationRequired(frame: relayFrames.AgentConfirmationRequired): boolean {
    const record = this.records.get(frame.command_id);
    if (!record) {
      log.warn("confirmation.unknown_command_dropped");
      return false;
    }
    if (record.terminal) return true;
    let parsed: ParsedChallenge;
    try {
      parsed = parseChallenge(frame.challenge_text);
    } catch (e) {
      log.warn("confirmation.invalid_challenge", errorSummary(e));
      return true; // nothing to show; the challenge will expire and the relay terminates the command
    }
    const identity = this.deps.identity();
    const bindingProblem = checkChallengeBinding(parsed.challenge, record, identity?.controllerId ?? null);
    if (bindingProblem) log.warn("confirmation.binding_mismatch", { command_id: record.commandId });
    record.state = "awaiting_confirmation";
    record.confirmation = { parsed, bindingProblem, receivedAt: Date.now(), decision: null, connectionLost: false };
    record.noAnswer = false;
    this.store(record);
    return true;
  }

  private onResult(frame: relayFrames.AgentResult): boolean {
    const record = this.records.get(frame.command_id);
    if (!record) return false;
    if (record.terminal) {
      // version.json rules.late_results: an agent result may correct a relay outcome_unknown once.
      const correctable = record.terminal.origin === "relay" && record.terminal.state === "outcome_unknown" && frame.origin === "agent";
      if (!correctable) {
        log.info("result.post_terminal_dropped", { command_id: frame.command_id });
        return true;
      }
    }
    let result: Record<string, unknown> | null = null;
    let resultInvalid = false;
    if (frame.result !== undefined) {
      try {
        result = registry.validateResult(record.action, frame.result);
      } catch (e) {
        resultInvalid = frame.state === "succeeded";
        log.warn("result.invalid_shape", { action: record.action, ...errorSummary(e) });
      }
    }
    record.terminal = {
      origin: frame.origin,
      state: frame.state,
      at: frame.at,
      durationMs: frame.duration_ms,
      result,
      resultInvalid,
      error: frame.error ?? null,
      warning: frame.warning ?? null,
    };
    record.state = frame.state;
    record.noAnswer = false;
    this.disarm(record.commandId);
    this.store(record);
    log.info("command.result", { action: record.action, command_id: record.commandId, state: frame.state, origin: frame.origin, code: frame.error?.code });
    return true;
  }

  private requireConnection(): void {
    if (this.deps.connected && !this.deps.connected()) throw new ProtocolError("PC_RECONNECTING", "not connected to DoMe", true);
  }

  private arm(commandId: string, ms: number): void {
    const timers = this.deps.timers ?? globalTimers;
    this.disarm(commandId);
    this.timers.set(
      commandId,
      timers.setTimeout(() => {
        this.timers.delete(commandId);
        const record = this.records.get(commandId);
        if (!record || record.terminal) return;
        record.noAnswer = true;
        this.store(record);
      }, ms),
    );
  }

  private disarm(commandId: string): void {
    const h = this.timers.get(commandId);
    if (h !== undefined) (this.deps.timers ?? globalTimers).clearTimeout(h);
    this.timers.delete(commandId);
  }

  private store(record: CommandRecord): void {
    this.records.set(record.commandId, record);
    if (this.records.size > MAX_RECORDS) {
      const oldest = [...this.records.values()].filter((r) => TERMINAL.has(r.state)).sort((a, b) => a.createdAt - b.createdAt)[0];
      if (oldest) this.records.delete(oldest.commandId);
    }
    this.deps.onChange({ ...record });
    if (record.terminal || record.noAnswer) {
      const waiters = this.settledWaiters.get(record.commandId);
      if (waiters) {
        this.settledWaiters.delete(record.commandId);
        for (const resolve of waiters) resolve({ ...record });
      }
    }
  }
}

const globalTimers = {
  setTimeout: (fn: () => void, ms: number): unknown => setTimeout(fn, ms),
  clearTimeout: (h: unknown): void => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export function isTerminal(state: LifecycleState): boolean {
  return TERMINAL.has(state);
}

/**
 * Native Messaging port to `com.dome.agent` (dome-native-host → agent IPC). Validates every frame in
 * both directions, performs the bridge_hello / bridge_hello_ack version negotiation and reconnects
 * with exponential backoff (1 → 60 s) through chrome.alarms, which fire even after the service
 * worker was terminated. While a port is open Chrome (≥116) keeps the worker alive.
 */
import { log } from "../shared/log.ts";
import type { ConnectionState, ConnectionStateName } from "../shared/messages.ts";
import { realTimers, type Timers } from "../shared/throttle.ts";
import { checkOutgoing, validateBridgeFrame } from "../shared/validate.ts";
import { protocolCompatible, SUPPORTED_PROTOCOL_VERSIONS } from "../shared/version.ts";
import type { ExtensionApi, NativePort } from "./api.ts";
import { errorFrame, type AgentToExtension, type BridgeHello, type BridgeRequest, type ExtensionToAgent } from "./frames.ts";

export const NATIVE_HOST_NAME = "com.dome.agent";
export const RECONNECT_ALARM = "dome-reconnect";
const BACKOFF_SECONDS = [1, 2, 4, 8, 16, 32, 60] as const;
const INCOMPATIBLE_RETRY_SECONDS = 300;
/** A host that opens the port but never acknowledges hello is treated as a failed connection after this long. */
export const HELLO_ACK_TIMEOUT_MS = 10_000;
/** Chrome's minimum alarm delay (30 s since Chrome 120; shorter delays are rounded up). */
const MIN_ALARM_MINUTES = 0.5;

export interface ConnectionDeps {
  api: ExtensionApi;
  buildHello: () => Promise<BridgeHello>;
  onRequest: (request: BridgeRequest) => void;
  onConnected: () => void;
  onStateChange?: (state: ConnectionState) => void;
  timers?: Timers;
  hostName?: string;
}

export class NativeConnection {
  private port: NativePort | null = null;
  private acked = false;
  private attempt = 0;
  private reconnectTimer: unknown = null;
  private ackTimer: unknown = null;
  private state: ConnectionState = { state: "disconnected", since: new Date().toISOString(), attempt: 0 };
  private readonly timers: Timers;
  private readonly hostName: string;

  constructor(private readonly deps: ConnectionDeps) {
    this.timers = deps.timers ?? realTimers;
    this.hostName = deps.hostName ?? NATIVE_HOST_NAME;
  }

  get connected(): boolean {
    return this.port !== null && this.acked;
  }

  current(): ConnectionState {
    return this.state;
  }

  installListeners(): void {
    this.deps.api.alarms.onAlarm.addListener((alarm) => {
      if (alarm.name === RECONNECT_ALARM) void this.connect();
    });
  }

  /** Idempotent: a no-op while a port is open. */
  async connect(): Promise<void> {
    if (this.port) return;
    this.clearReconnectTimer();
    this.setState("connecting");
    let port: NativePort;
    try {
      port = this.deps.api.runtime.connectNative(this.hostName);
    } catch (err) {
      this.setState("error", err instanceof Error ? err.message : "connectNative failed");
      this.scheduleReconnect();
      return;
    }
    this.port = port;
    this.acked = false;
    port.onMessage.addListener((message) => this.handleMessage(port, message));
    port.onDisconnect.addListener(() => this.handleDisconnect(port));
    this.armAckTimer(port);
    await this.sendHello();
  }

  /**
   * Without an ack the open port would keep the worker alive and connect() a no-op forever
   * (host blocked on its pipe, ack dropped by the host's schema check, stalled agent IPC). The
   * reconnect alarm is the termination-safe net; this timer is the in-process one.
   */
  private armAckTimer(port: NativePort): void {
    this.clearAckTimer();
    this.ackTimer = this.timers.setTimeout(() => {
      this.ackTimer = null;
      if (port !== this.port || this.acked) return;
      log.warn("no bridge_hello_ack from the native host", { timeout_ms: HELLO_ACK_TIMEOUT_MS, attempt: this.attempt });
      this.disconnect();
      this.setState("disconnected", "The DoMe agent did not answer the extension's hello");
      this.scheduleReconnect();
    }, HELLO_ACK_TIMEOUT_MS);
  }

  private clearAckTimer(): void {
    if (this.ackTimer !== null) this.timers.clearTimeout(this.ackTimer);
    this.ackTimer = null;
  }

  async sendHello(): Promise<void> {
    if (!this.port) return;
    try {
      const hello = await this.deps.buildHello();
      this.post(hello);
    } catch (err) {
      log.error("hello could not be sent", { error: err instanceof Error ? err.name : "unknown" });
    }
  }

  /** Validate and post a frame; false when no port is open or the frame is invalid. */
  post(frame: ExtensionToAgent): boolean {
    if (!this.port) return false;
    try {
      checkOutgoing(frame);
    } catch (err) {
      log.error("outgoing frame rejected", { type: frame.type, error: err instanceof Error ? err.message.slice(0, 120) : "invalid" });
      return false;
    }
    try {
      this.port.postMessage(frame);
      return true;
    } catch (err) {
      log.warn("postMessage failed", { error: err instanceof Error ? err.name : "unknown" });
      return false;
    }
  }

  disconnect(): void {
    const port = this.port;
    this.port = null;
    this.acked = false;
    this.clearAckTimer();
    try {
      port?.disconnect();
    } catch {
      // already gone
    }
  }

  private handleMessage(port: NativePort, message: unknown): void {
    if (port !== this.port) return;
    try {
      validateBridgeFrame("agent_to_extension", message);
    } catch (err) {
      const ref = typeof (message as { request_id?: unknown })?.request_id === "string" ? (message as { request_id: string }).request_id : undefined;
      log.warn("agent frame rejected by schema", { error: err instanceof Error ? err.message.slice(0, 160) : "invalid" });
      this.post(errorFrame("MALFORMED_MESSAGE", "frame does not match the bridge schema", ref ? { refRequestId: ref } : {}));
      return;
    }
    const frame = message as AgentToExtension;
    switch (frame.type) {
      case "bridge_hello_ack": {
        if (!protocolCompatible(frame.protocol_version, SUPPORTED_PROTOCOL_VERSIONS)) {
          log.warn("agent protocol incompatible", { agent_protocol: frame.protocol_version });
          this.post(
            errorFrame("PROTOCOL_INCOMPATIBLE", "The DoMe extension and agent speak incompatible protocol versions.", {
              detail: { peer: [frame.protocol_version], supported: [...SUPPORTED_PROTOCOL_VERSIONS] },
            }),
          );
          this.setState("incompatible", `agent protocol ${frame.protocol_version}`, { agent_version: frame.agent_version, protocol_version: frame.protocol_version });
          this.disconnect();
          this.scheduleReconnect(INCOMPATIBLE_RETRY_SECONDS);
          return;
        }
        this.acked = true;
        this.attempt = 0;
        this.clearAckTimer();
        this.setState("connected", undefined, { agent_version: frame.agent_version, protocol_version: frame.protocol_version });
        log.info("connected to agent", { agent_version: frame.agent_version, protocol_version: frame.protocol_version });
        this.deps.onConnected();
        return;
      }
      case "bridge_request":
        if (!this.acked) {
          this.post(errorFrame("MALFORMED_MESSAGE", "request before bridge_hello_ack", { refRequestId: frame.request_id }));
          return;
        }
        this.deps.onRequest(frame);
        return;
      case "bridge_error": {
        const code = frame.error.code;
        log.warn("bridge_error from agent", { code, ref_request_id: frame.ref_request_id ?? null });
        if (code === "PROTOCOL_INCOMPATIBLE") this.setState("incompatible", frame.error.message);
        else if (code === "AGENT_NOT_RUNNING") this.setState("agent_not_running", frame.error.message);
        else if (code === "AGENT_DISCONNECTED") this.setState("disconnected", frame.error.message);
        return;
      }
    }
  }

  private handleDisconnect(port: NativePort): void {
    if (port !== this.port) return;
    const reason = this.deps.api.runtime.lastError?.message ?? "";
    this.port = null;
    const wasAcked = this.acked;
    this.acked = false;
    this.clearAckTimer();
    const classified = classifyDisconnect(reason, this.state.state);
    log.info("native port disconnected", { reason: classified, acked: wasAcked });
    if (this.state.state !== "incompatible") this.setState(classified, reason || undefined);
    this.scheduleReconnect(this.state.state === "incompatible" ? INCOMPATIBLE_RETRY_SECONDS : undefined);
  }

  private scheduleReconnect(fixedSeconds?: number): void {
    this.clearReconnectTimer();
    this.attempt += 1;
    const seconds = fixedSeconds ?? BACKOFF_SECONDS[Math.min(this.attempt, BACKOFF_SECONDS.length) - 1]!;
    if (seconds < 30) this.reconnectTimer = this.timers.setTimeout(() => void this.connect(), seconds * 1000);
    // The alarm fires even if the worker is terminated meanwhile; connect() is idempotent.
    void this.deps.api.alarms.create(RECONNECT_ALARM, { delayInMinutes: Math.max(MIN_ALARM_MINUTES, seconds / 60) });
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) this.timers.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private setState(state: ConnectionStateName, message?: string, extra: Partial<ConnectionState> = {}): void {
    const next: ConnectionState = { state, since: new Date().toISOString(), attempt: this.attempt, ...extra };
    if (message) next.message = message.slice(0, 200);
    this.state = next;
    this.deps.onStateChange?.(next);
  }
}

/** Map chrome.runtime.lastError on native-port disconnect to a customer-explainable state. */
export function classifyDisconnect(reason: string, previous: ConnectionStateName): ConnectionStateName {
  if (/not found/i.test(reason)) return "host_missing";
  if (/forbidden/i.test(reason)) return "host_forbidden";
  if (previous === "agent_not_running" || previous === "incompatible") return previous;
  if (/exited|communicating/i.test(reason)) return "disconnected";
  return "disconnected";
}

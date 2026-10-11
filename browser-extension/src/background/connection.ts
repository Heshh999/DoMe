/**
 * Native Messaging port to `com.dome.agent` (dome-native-host → agent IPC). Validates every frame in
 * both directions, performs the bridge_hello / bridge_hello_ack version negotiation and reconnects:
 * fast (1, 2, 4, 8, 8 … s through setTimeout, with a chrome.alarms safety net that fires even after
 * the service worker was terminated) for about 5 minutes after the browser starts, the agent is lost
 * or the popup's Retry, then every 30 s through the alarm alone so the worker can sleep. While a port
 * is open Chrome (≥116) keeps the worker alive. A port whose host reports that the agent side is gone
 * is dropped at once: the host process may linger with the port open, and an open port would make
 * connect() a no-op.
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
/**
 * Capped low on purpose: the browser is often started (and the extension loaded) before DoMe, and a
 * freshly started agent must be found within ~10 s. Each attempt while the agent is down starts one
 * short-lived native host process, so this pace only lasts for the fast window below.
 */
const BACKOFF_SECONDS = [1, 2, 4, 8] as const;
/**
 * The fast pace ends this long after the first failure (or after this many failed attempts, which
 * the backoff reaches at about the same time). Browser start, extension install/reload, the popup's
 * Retry and every ack close it, so the next failure opens a new one; a worker restart keeps the
 * current one (storage.session).
 */
export const FAST_RETRY_WINDOW_MS = 5 * 60_000;
export const FAST_RETRY_MAX_ATTEMPTS = 40;
/** After the fast window: the alarm alone, so no timer or port keeps the worker awake in between. */
export const SLOW_RETRY_SECONDS = 30;
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
  /** Keeps the end of the fast-retry window across worker restarts (chrome.storage.session); in memory only when absent. */
  retryWindow?: { load(): Promise<number | null>; save(until: number | null): Promise<void> };
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
  /** End of the fast-retry window (epoch ms); null when none is open, and the next failure opens one. */
  private fastUntil: number | null = null;
  /** True once this worker changed the window, so a stored value that arrives later cannot undo it. */
  private fastChanged = false;
  private readonly fastLoaded: Promise<void>;
  private readonly timers: Timers;
  private readonly hostName: string;

  constructor(private readonly deps: ConnectionDeps) {
    this.timers = deps.timers ?? realTimers;
    this.hostName = deps.hostName ?? NATIVE_HOST_NAME;
    this.fastLoaded = this.loadFastWindow();
  }

  /** True only while a port is open and the agent acknowledged its hello; every failure path clears both. */
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
    // A worker woken by the slow alarm must keep the slow pace, so the stored window has to be known
    // before this attempt can fail.
    await this.fastLoaded;
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
   * Popup "Retry connection": unless the agent has acknowledged the current port, drop whatever is in
   * progress (a hello still waiting for its ack, a port the host left open, a scheduled retry) and
   * connect at once with a fresh backoff and a new fast-retry window.
   */
  async reconnectNow(): Promise<void> {
    if (this.connected && this.state.state === "connected") return;
    this.disconnect();
    this.restartFastRetries();
    await this.connect();
  }

  /**
   * Browser start, extension install/update/reload, Retry and an ack: the next failure starts the fast
   * backoff again with a new window. Does not connect by itself.
   */
  restartFastRetries(): void {
    this.attempt = 0;
    this.setFastWindow(null);
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
        this.restartFastRetries();
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
        // The host sends these when its agent side is gone or refused the hello. The port is useless
        // from then on, and the host may not exit by itself (it can keep the port open after the
        // agent quit), so close it here instead of waiting for onDisconnect.
        if (code === "PROTOCOL_INCOMPATIBLE") this.drop("incompatible", frame.error.message, INCOMPATIBLE_RETRY_SECONDS);
        else if (code === "AGENT_NOT_RUNNING") this.drop("agent_not_running", frame.error.message);
        else if (code === "AGENT_DISCONNECTED") this.drop("disconnected", frame.error.message);
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

  private drop(state: ConnectionStateName, message: string, fixedSeconds?: number): void {
    this.disconnect();
    this.setState(state, message);
    this.scheduleReconnect(fixedSeconds);
  }

  private scheduleReconnect(fixedSeconds?: number): void {
    this.clearReconnectTimer();
    this.attempt += 1;
    const seconds = fixedSeconds ?? (this.inFastWindow() ? BACKOFF_SECONDS[Math.min(this.attempt, BACKOFF_SECONDS.length) - 1]! : SLOW_RETRY_SECONDS);
    if (seconds < MIN_ALARM_MINUTES * 60) this.reconnectTimer = this.timers.setTimeout(() => void this.connect(), seconds * 1000);
    // The alarm fires even if the worker is terminated meanwhile; connect() is idempotent.
    void this.deps.api.alarms.create(RECONNECT_ALARM, { delayInMinutes: Math.max(MIN_ALARM_MINUTES, seconds / 60) });
  }

  /** Whether the failure being scheduled is still in the fast window; the first one after a reset opens it. */
  private inFastWindow(): boolean {
    const now = this.timers.now();
    if (this.fastUntil === null) this.setFastWindow(now + FAST_RETRY_WINDOW_MS);
    const left = (this.fastUntil ?? now) - now;
    // A clock set back would stretch a stored window; the attempt cap bounds the fast pace either way.
    return left > 0 && left <= FAST_RETRY_WINDOW_MS && this.attempt <= FAST_RETRY_MAX_ATTEMPTS;
  }

  private setFastWindow(until: number | null): void {
    this.fastChanged = true;
    this.fastUntil = until;
    void this.deps.retryWindow?.save(until).catch((err: unknown) => log.warn("reconnect window not saved", { error: err instanceof Error ? err.name : "unknown" }));
  }

  private async loadFastWindow(): Promise<void> {
    if (!this.deps.retryWindow) return;
    try {
      const stored = await this.deps.retryWindow.load();
      if (!this.fastChanged) this.fastUntil = stored;
    } catch (err) {
      log.warn("reconnect window not loaded", { error: err instanceof Error ? err.name : "unknown" });
    }
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

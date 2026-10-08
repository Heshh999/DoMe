/**
 * One WebSocket to `/ws/controller`. `hello{kid}` → `hello_ack{controller_id?}` → `subscribe`.
 * Every inbound frame is strict-parsed and validated against `relay_to_controller` before anyone
 * sees it; every outbound frame is validated against `controller_to_relay` before it is sent.
 * Reconnects with capped exponential backoff and jitter; a visibility/online change triggers an
 * immediate reconnect. Consumers treat all PC state as stale until fresh frames arrive after a
 * (re)connect — that is the store's job, signalled through `status` events.
 */
import { LIMITS, loadsStrict, PROTOCOL_VERSION, REGISTRY_VERSION, ProtocolError, schemas, type relayFrames } from "@dome/protocol";

import { errorSummary, log } from "./log.ts";

export type RelayStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "revoked" | "unauthenticated" | "refused" | "incompatible";

export interface WebSocketLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number; reason: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export type SocketFactory = (url: string) => WebSocketLike;

export interface RelayTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

export interface RelayClientOptions {
  url: string;
  kid: () => Promise<string>;
  componentVersion: string;
  socketFactory?: SocketFactory;
  timers?: RelayTimers;
  random?: () => number;
  /** Testing: shorten the backoff base. */
  backoffBaseMs?: number;
  backoffMaxMs?: number;
}

export interface RelayEvents {
  status: (status: RelayStatus, detail: { code?: number; error?: relayFrames.Error } | undefined) => void;
  frame: (frame: relayFrames.RelayToController) => void;
  controller: (controllerId: string | null) => void;
}

const defaultTimers: RelayTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

const HELLO_TIMEOUT_MS = 10_000;
const PING_INTERVAL_MS = 25_000;
const IDLE_LIMIT_MS = 75_000;
const WS_OPEN = 1;

export function relayUrl(apiOrigin: string, loc: { protocol: string; host: string }): string {
  if (apiOrigin) return apiOrigin.replace(/^http/, "ws") + "/ws/controller";
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${loc.host}/ws/controller`;
}

export class RelayClient {
  private readonly opts: Required<Pick<RelayClientOptions, "url" | "kid" | "componentVersion" | "backoffBaseMs" | "backoffMaxMs">> & RelayClientOptions;
  private readonly timers: RelayTimers;
  private readonly factory: SocketFactory;
  private readonly random: () => number;
  private socket: WebSocketLike | null = null;
  private _status: RelayStatus = "idle";
  private _controllerId: string | null = null;
  private subscriptions: string[] = [];
  private attempt = 0;
  private wantOpen = false;
  private helloTimer: unknown = null;
  private reconnectTimer: unknown = null;
  private pingTimer: unknown = null;
  private lastInbound = 0;
  private listeners: { [K in keyof RelayEvents]: Set<RelayEvents[K]> } = { status: new Set(), frame: new Set(), controller: new Set() };

  constructor(options: RelayClientOptions) {
    this.opts = { backoffBaseMs: 1000, backoffMaxMs: 30_000, ...options };
    this.timers = options.timers ?? defaultTimers;
    this.factory = options.socketFactory ?? ((url) => new WebSocket(url) as unknown as WebSocketLike);
    this.random = options.random ?? Math.random;
  }

  get status(): RelayStatus {
    return this._status;
  }
  get controllerId(): string | null {
    return this._controllerId;
  }
  get isOpen(): boolean {
    return this._status === "open" && this.socket?.readyState === WS_OPEN;
  }

  on<K extends keyof RelayEvents>(event: K, handler: RelayEvents[K]): () => void {
    const set = this.listeners[event] as Set<RelayEvents[K]>;
    set.add(handler);
    return () => set.delete(handler);
  }

  private emitStatus(status: RelayStatus, detail?: { code?: number; error?: relayFrames.Error }): void {
    this._status = status;
    for (const h of this.listeners.status) h(status, detail);
  }

  connect(): void {
    this.wantOpen = true;
    if (this.socket) return;
    this.clearReconnect();
    this.emitStatus(this.attempt === 0 ? "connecting" : "reconnecting");
    void this.open();
  }

  /** Close for good (sign-out, revocation). No reconnect until `connect()` is called again. */
  close(): void {
    this.wantOpen = false;
    this.clearReconnect();
    this.teardownSocket(1000, "client closed");
    this.setController(null);
    this.emitStatus("closed");
  }

  /** Visibility/online change: if we are not open, reconnect right now. */
  nudge(): void {
    if (!this.wantOpen) return;
    if (this.isOpen) {
      this.sendRaw({ type: "ping" });
      return;
    }
    this.clearReconnect();
    this.teardownSocket(4000, "stale socket");
    this.emitStatus("reconnecting");
    void this.open();
  }

  /** Force a fresh socket (e.g. after pairing, so `hello` binds the newly created controller). */
  reconnect(): void {
    if (!this.wantOpen) {
      this.connect();
      return;
    }
    this.clearReconnect();
    this.teardownSocket(4000, "rebind");
    this.setController(null);
    this.emitStatus("reconnecting");
    void this.open();
  }

  /** Replaces the subscription set; sent immediately when open and on every (re)connect. */
  setSubscriptions(pcIds: string[]): void {
    const ids = [...new Set(pcIds)].slice(0, 16);
    this.subscriptions = ids;
    if (this.isOpen && this._controllerId && ids.length > 0) this.sendRaw({ type: "subscribe", pc_ids: ids as [string] });
  }

  /** Validate and send a controller frame; throws when the socket is not open. */
  send(frame: relayFrames.ControllerToRelay): void {
    if (!this.isOpen) throw new ProtocolError("PC_RECONNECTING", "not connected to DoMe", true);
    this.sendRaw(frame);
  }

  private sendRaw(frame: relayFrames.ControllerToRelay): void {
    schemas.validateFrame("controller_to_relay", frame);
    const text = JSON.stringify(frame);
    if (text.length > LIMITS.max_frame_bytes) throw new ProtocolError("PAYLOAD_TOO_LARGE", "frame too large");
    this.socket?.send(text);
  }

  private async open(): Promise<void> {
    let kid: string;
    try {
      kid = await this.opts.kid();
    } catch (e) {
      log.warn("relay.no_kid", errorSummary(e));
      this.scheduleReconnect();
      return;
    }
    if (!this.wantOpen || this.socket) return;
    let ws: WebSocketLike;
    try {
      ws = this.factory(this.opts.url);
    } catch (e) {
      log.warn("relay.socket_create_failed", errorSummary(e));
      this.scheduleReconnect();
      return;
    }
    this.socket = ws;
    ws.onopen = () => {
      this.lastInbound = Date.now();
      try {
        this.sendRaw({ type: "hello", component: "controller", kid, component_version: this.opts.componentVersion, protocol_versions: [PROTOCOL_VERSION], registry_version: REGISTRY_VERSION });
      } catch (e) {
        log.error("relay.hello_failed", errorSummary(e));
        this.teardownSocket(4000, "hello failed");
        this.scheduleReconnect();
        return;
      }
      this.helloTimer = this.timers.setTimeout(() => {
        log.warn("relay.hello_timeout");
        this.teardownSocket(4000, "hello timeout");
        this.scheduleReconnect();
      }, HELLO_TIMEOUT_MS);
    };
    ws.onmessage = (ev) => this.onMessage(ev.data);
    ws.onerror = () => {
      /* onclose follows */
    };
    ws.onclose = (ev) => {
      if (this.socket !== ws) return;
      this.socket = null;
      this.clearHelloTimer();
      this.stopPing();
      this.setController(null);
      this.onClosed(ev.code);
    };
  }

  private onClosed(code: number): void {
    if (!this.wantOpen) return;
    log.info("relay.closed", { code });
    if (code === 4003) {
      this.wantOpen = false;
      this.emitStatus("revoked", { code });
      return;
    }
    if (code === 4008 || code === 1008) {
      this.wantOpen = false;
      this.emitStatus(code === 4008 ? "unauthenticated" : "refused", { code });
      return;
    }
    this.scheduleReconnect();
  }

  private onMessage(data: unknown): void {
    this.lastInbound = Date.now();
    if (typeof data !== "string") {
      log.warn("relay.binary_frame_dropped");
      return;
    }
    let frame: relayFrames.RelayToController;
    try {
      const parsed = loadsStrict(data, { maxBytes: LIMITS.max_frame_bytes, maxDepth: LIMITS.max_json_depth, requireObject: true });
      schemas.validateFrame("relay_to_controller", parsed);
      frame = parsed as unknown as relayFrames.RelayToController;
    } catch (e) {
      log.warn("relay.invalid_frame_dropped", errorSummary(e));
      return;
    }
    switch (frame.type) {
      case "ping":
        this.sendRaw({ type: "pong", ...(frame.t ? { t: frame.t } : {}) });
        return;
      case "pong":
        return;
      case "hello_ack": {
        this.clearHelloTimer();
        this.attempt = 0;
        this.setController(frame.controller_id ?? null);
        this.emitStatus("open");
        this.startPing();
        if (this.subscriptions.length > 0 && frame.controller_id) {
          this.sendRaw({ type: "subscribe", pc_ids: this.subscriptions as [string] });
        }
        for (const h of this.listeners.frame) h(frame);
        return;
      }
      case "revoked": {
        this.wantOpen = false;
        for (const h of this.listeners.frame) h(frame);
        this.emitStatus("revoked");
        return;
      }
      case "error": {
        if (frame.error.code === "PROTOCOL_INCOMPATIBLE") {
          this.wantOpen = false;
          this.emitStatus("incompatible", { error: frame.error });
        }
        for (const h of this.listeners.frame) h(frame);
        return;
      }
      default:
        for (const h of this.listeners.frame) h(frame);
    }
  }

  private setController(id: string | null): void {
    if (this._controllerId === id) return;
    this._controllerId = id;
    for (const h of this.listeners.controller) h(id);
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = this.timers.setInterval(() => {
      if (!this.isOpen) return;
      if (Date.now() - this.lastInbound > IDLE_LIMIT_MS) {
        log.warn("relay.idle_timeout");
        this.teardownSocket(4000, "idle");
        this.scheduleReconnect();
        return;
      }
      try {
        this.sendRaw({ type: "ping" });
      } catch {
        /* closing */
      }
    }, PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) this.timers.clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  private clearHelloTimer(): void {
    if (this.helloTimer !== null) this.timers.clearTimeout(this.helloTimer);
    this.helloTimer = null;
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) this.timers.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private teardownSocket(code: number, reason: string): void {
    const ws = this.socket;
    this.socket = null;
    this.clearHelloTimer();
    this.stopPing();
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      try {
        ws.close(code, reason);
      } catch {
        /* already closed */
      }
    }
  }

  /** Exponential backoff with ±30 % jitter, capped. */
  nextDelayMs(): number {
    const base = Math.min(this.opts.backoffMaxMs, this.opts.backoffBaseMs * 2 ** Math.min(this.attempt, 10));
    const jitter = 0.7 + this.random() * 0.6;
    return Math.round(base * jitter);
  }

  private scheduleReconnect(): void {
    if (!this.wantOpen || this.reconnectTimer !== null) return;
    const delay = this.nextDelayMs();
    this.attempt += 1;
    this.emitStatus("reconnecting");
    this.reconnectTimer = this.timers.setTimeout(() => {
      this.reconnectTimer = null;
      if (this.wantOpen && !this.socket) void this.open();
    }, delay);
  }
}

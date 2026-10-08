/**
 * Wires the libraries together for the running app: one RelayClient, one CommandService, the
 * zustand stores, and the browser lifecycle events (visibility/online) that trigger reconnects and
 * mark state stale. Pages talk to `runtime`, never to the socket directly.
 */
import { ProtocolError, type JsonValue, type relayFrames } from "@dome/protocol";

import { api, ApiError, configureApi, API_ORIGIN } from "../lib/api.ts";
import { CommandService, type CommandRecord } from "../lib/commands.ts";
import { clearAccountState, getControllerIdentity, getOrCreateKeyPair, setStoredControllerId } from "../lib/controllerKey.ts";
import { APP_VERSION } from "../lib/diagnostics.ts";
import { errorSummary, log } from "../lib/log.ts";
import { RelayClient, relayUrl, type RelayClientOptions, type RelayStatus } from "../lib/relay.ts";
import { useDevicesStore } from "../store/devices.ts";
import { useLiveStore } from "../store/live.ts";
import { useSessionStore } from "../store/session.ts";

export interface SendOptions {
  pcId: string;
  action: string;
  params?: Record<string, JsonValue>;
  target?: Record<string, JsonValue> | null;
  source?: CommandRecord["source"];
}

export interface RuntimeOptions {
  relay?: Partial<RelayClientOptions>;
  /** Testing: override the store clock interval (ms). */
  clockIntervalMs?: number;
}

/** How often the live store's clock advances so the 75 s freshness rule is re-evaluated without a frame. */
export const CLOCK_INTERVAL_MS = 5_000;

export class Runtime {
  readonly relay: RelayClient;
  readonly commands: CommandService;
  private started = false;
  private detach: Array<() => void> = [];
  private readonly clockIntervalMs: number;

  constructor(options: RuntimeOptions = {}) {
    const live = useLiveStore.getState();
    this.clockIntervalMs = options.clockIntervalMs ?? CLOCK_INTERVAL_MS;
    // A 401 on any REST call means the cookie session is gone: drop the session so RequireSession
    // redirects to sign-in on the next render, and stop the socket (its session is gone too).
    configureApi({
      onUnauthenticated: () => {
        if (useSessionStore.getState().status !== "signed_out") log.info("session.expired");
        useSessionStore.getState().clear();
        this.relay.close();
      },
    });
    this.relay = new RelayClient({
      url: relayUrl(API_ORIGIN, window.location),
      kid: async () => (await getControllerIdentity()).kid,
      componentVersion: APP_VERSION,
      ...options.relay,
    });
    this.commands = new CommandService({
      send: (frame) => this.relay.send(frame),
      connected: () => this.relay.isOpen,
      identity: () => {
        const accountId = useSessionStore.getState().session?.account.id ?? null;
        const controllerId = this.relay.controllerId;
        return accountId && controllerId ? { accountId, controllerId } : null;
      },
      keyPair: () => getOrCreateKeyPair(),
      onChange: (record) => useLiveStore.getState().upsertCommand(record),
    });
    this.relay.on("status", (status, detail) => {
      live.setRelay(status, detail?.error ?? null);
      if (status !== "open") {
        useLiveStore.getState().markAllStale();
        // A pending confirmation's countdown is not backed by a live connection any more; the modal
        // says so and offers Close instead of pretending Approve/Decline will reach the PC.
        this.commands.markConnectionLost();
      } else {
        this.commands.markConnectionRestored();
      }
      if (status === "unauthenticated") useSessionStore.getState().clear();
    });
    this.relay.on("controller", (id) => {
      useLiveStore.getState().setControllerId(id);
      void setStoredControllerId(id).catch(() => undefined);
    });
    this.relay.on("frame", (frame) => this.onFrame(frame));
  }

  /** Connect once the session is known; idempotent. */
  start(): void {
    if (this.started) return;
    this.started = true;
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        useLiveStore.getState().markAllStale();
        this.relay.nudge();
        void useDevicesStore.getState().refresh();
      }
    };
    const onOnline = () => {
      useLiveStore.getState().markAllStale();
      this.relay.nudge();
    };
    const onOffline = () => useLiveStore.getState().markAllStale();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    const clock = setInterval(() => useLiveStore.getState().tick(), this.clockIntervalMs);
    this.detach.push(() => document.removeEventListener("visibilitychange", onVisible), () => window.removeEventListener("online", onOnline), () => window.removeEventListener("offline", onOffline), () => clearInterval(clock));
    this.relay.connect();
  }

  /** Subscribe to every PC of the account (the relay refuses those this phone holds no grant on). */
  syncSubscriptions(pcIds: string[]): void {
    this.relay.setSubscriptions(pcIds);
  }

  async send(input: SendOptions): Promise<CommandRecord> {
    return this.commands.send({ pcId: input.pcId, action: input.action, ...(input.params ? { params: input.params } : {}), target: input.target ?? null, ...(input.source ? { source: input.source } : {}) });
  }

  cancel(commandId: string): void {
    try {
      this.commands.cancel(commandId);
    } catch (e) {
      log.warn("command.cancel_failed", errorSummary(e));
    }
  }

  respond(commandId: string, decision: "approve" | "decline"): Promise<void> {
    return this.commands.respondToChallenge(commandId, decision);
  }

  /** Close the confirmation modal locally (expired or disconnected). Sends nothing. */
  dismissConfirmation(commandId: string): void {
    this.commands.dismissConfirmation(commandId);
  }

  /** Sign out: server session, socket, account state (the installation key is kept). */
  async signOut(): Promise<void> {
    try {
      await api.logout();
    } catch (e) {
      if (!(e instanceof ApiError && (e.status === 401 || e.code === "UNAUTHENTICATED"))) log.warn("logout.failed", errorSummary(e));
    }
    this.relay.close();
    this.commands.reset();
    useLiveStore.getState().reset();
    useDevicesStore.getState().reset();
    useSessionStore.getState().clear();
    configureApi({ csrfToken: null });
    try {
      await clearAccountState();
    } catch {
      /* storage unavailable */
    }
  }

  stop(): void {
    for (const d of this.detach) d();
    this.detach = [];
    this.started = false;
    this.relay.close();
  }

  private onFrame(frame: relayFrames.RelayToController): void {
    const live = useLiveStore.getState();
    switch (frame.type) {
      case "pc_status":
        live.onPcStatus(frame);
        return;
      case "state":
        live.onState(frame);
        return;
      case "error":
        if (frame.ref_pc_id) live.onSubscribeRefused(frame.ref_pc_id, frame.error.code);
        else log.warn("relay.error_frame", { code: frame.error.code });
        return;
      case "ack":
      case "confirmation_required":
      case "result":
        if (!this.commands.handleFrame(frame)) log.info("relay.frame_for_unknown_command", { type: frame.type });
        return;
      default:
        return;
    }
  }
}

let instance: Runtime | null = null;

export function getRuntime(): Runtime {
  if (!instance) instance = new Runtime();
  return instance;
}

/** Tests only: install a runtime built with injected socket/timer factories. */
export function setRuntimeForTests(rt: Runtime | null): void {
  instance = rt;
}

export function describeSendError(e: unknown): { code: string; message: string } {
  if (e instanceof ProtocolError) return { code: e.code, message: e.message.replace(/^[A-Z_]+: /, "") };
  if (e instanceof ApiError) return { code: e.code, message: e.message };
  return { code: "INTERNAL", message: "Something went wrong on this phone." };
}

export type { RelayStatus };

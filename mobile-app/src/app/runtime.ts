/**
 * Wires the libraries together for the running app: one RelayClient, one CommandService, the
 * zustand stores, and the browser lifecycle events (visibility/online) that trigger reconnects and
 * mark state stale. Pages talk to `runtime`, never to the socket directly.
 */
import { buildHelloProofPayload, ProtocolError, signHelloProof, type JsonValue, type relayFrames } from "@dome/protocol";

import { api, ApiError, configureApi, API_ORIGIN } from "../lib/api.ts";
import { CommandService, type CommandRecord } from "../lib/commands.ts";
import { InputSessionClient, type InputTimers } from "../lib/input.ts";
import { clearAccountState, deleteInstallationKey, getControllerIdentity, getOrCreateKeyPair, setStoredControllerId } from "../lib/controllerKey.ts";
import { APP_VERSION } from "../lib/diagnostics.ts";
import { errorSummary, log } from "../lib/log.ts";
import { RelayClient, relayUrl, type RelayClientOptions, type RelayStatus } from "../lib/relay.ts";
import { useDevicesStore } from "../store/devices.ts";
import { useInputStore } from "../store/input.ts";
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
  /** Testing: timers for the input-session keepalive / ack waits. */
  inputTimers?: InputTimers;
  /** Testing: replace requestAnimationFrame for the input flush. */
  scheduleFrame?: (fn: () => void) => unknown;
  /** Testing: clock for the input client (issued_at, keepalive cadence). */
  inputNow?: () => Date;
}

/** How often the live store's clock advances so the 75 s freshness rule is re-evaluated without a frame. */
export const CLOCK_INTERVAL_MS = 5_000;

export class Runtime {
  readonly relay: RelayClient;
  readonly commands: CommandService;
  /** Manual touchpad/keyboard stream (protocol 1.1). Pages use it only through `useInputSession`. */
  readonly input: InputSessionClient;
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
      helloProof: async (kid) => {
        const accountId = useSessionStore.getState().session?.account.id;
        if (!accountId) return null;
        const pair = await getOrCreateKeyPair();
        return signHelloProof(pair.privateKey, pair.publicKey, buildHelloProofPayload({ kid, accountId }));
      },
      componentVersion: APP_VERSION,
      ...options.relay,
    });
    this.commands = new CommandService({
      send: (frame) => this.relay.send(frame),
      connected: () => this.relay.isOpen,
      identity: () => this.identity(),
      keyPair: () => getOrCreateKeyPair(),
      onChange: (record) => useLiveStore.getState().upsertCommand(record),
    });
    this.input = new InputSessionClient({
      sendBatch: (frame) => this.relay.sendInputBatch(frame),
      sendCommand: (input) => this.commands.send({ pcId: input.pcId, action: input.action, params: input.params, target: null, source: input.source }),
      onceSettled: (commandId) => this.commands.onceSettled(commandId),
      identity: () => this.identity(),
      keyPair: () => getOrCreateKeyPair(),
      connected: () => this.relay.isOpen,
      onChange: (state) => useInputStore.getState().setSession(state),
      ...(options.inputTimers ? { timers: options.inputTimers } : {}),
      ...(options.scheduleFrame ? { scheduleFrame: options.scheduleFrame, cancelFrame: () => undefined } : {}),
      ...(options.inputNow ? { now: options.inputNow } : {}),
    });
    this.relay.on("status", (status, detail) => {
      live.setRelay(status, detail?.error ?? null);
      if (status !== "open") {
        useLiveStore.getState().markAllStale();
        // A pending confirmation's countdown is not backed by a live connection any more; the modal
        // says so and offers Close instead of pretending Approve/Decline will reach the PC.
        this.commands.markConnectionLost();
        // The input stream is gone with the socket; the PC's lease releases anything still held.
        this.input.onConnectionLost();
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

  /**
   * Account + controller for signing. During a customer-initiated sign-out the session store is already
   * empty (`signing_out`), yet the `input.session_stop` that releases the PC's held input must still be
   * signed for the account it belongs to: the last known account id is kept for exactly that window.
   */
  private lastAccountId: string | null = null;
  private identity(): { accountId: string; controllerId: string } | null {
    const session = useSessionStore.getState();
    const accountId = session.session?.account.id ?? (session.status === "signing_out" ? this.lastAccountId : null);
    if (session.session) this.lastAccountId = session.session.account.id;
    const controllerId = this.relay.controllerId;
    return accountId && controllerId ? { accountId, controllerId } : null;
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
      } else {
        // Backgrounded: a held drag or key must not outlive the screen. Stop the input session now
        // (the PC's 3 s lease would end it anyway, but not before releasing is wanted).
        void this.input.stop("hidden");
      }
    };
    // Switching the selected PC ends the session on the old one before anything can target the new one.
    const unsubscribePc = useDevicesStore.subscribe((s, prev) => {
      if (s.selectedPcId !== prev.selectedPcId && this.input.snapshot.pcId && this.input.snapshot.pcId !== s.selectedPcId) void this.input.stop("pc_switch");
    });
    const onOnline = () => {
      useLiveStore.getState().markAllStale();
      this.relay.nudge();
    };
    const onOffline = () => useLiveStore.getState().markAllStale();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    const clock = setInterval(() => useLiveStore.getState().tick(), this.clockIntervalMs);
    this.detach.push(() => document.removeEventListener("visibilitychange", onVisible), () => window.removeEventListener("online", onOnline), () => window.removeEventListener("offline", onOffline), () => clearInterval(clock), unsubscribePc);
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

  /**
   * Customer-initiated sign-out. Order matters:
   * 1. the session store enters `signing_out` first, so `RequireSession` shows a neutral screen and
   *    never starts the automatic sign-in redirect while this runs (a 401 from `logout` or a 4008
   *    close would otherwise flip it to `signed_out` mid-way);
   * 2. the socket is closed, so no reconnect can mint a fresh key while the old one is being deleted;
   * 3. with `forgetInstallation`, the signing key is deleted and the IndexedDB transaction awaited —
   *    this is the step the customer asked for ("before handing the phone to someone else") and it
   *    must be complete before any navigation unloads the page;
   * 4. server session, in-memory stores and account state (the key is kept unless 3 ran).
   * Resolves only when everything is done; the caller then navigates exactly once.
   */
  async signOut(options: { forgetInstallation?: boolean } = {}): Promise<void> {
    useSessionStore.getState().beginSignOut();
    await this.input.stop("sign_out");
    this.relay.close();
    if (options.forgetInstallation) {
      try {
        await deleteInstallationKey();
      } catch (e) {
        log.warn("controller_key.delete_failed", errorSummary(e));
      }
    }
    try {
      await api.logout();
    } catch (e) {
      if (!(e instanceof ApiError && (e.status === 401 || e.code === "UNAUTHENTICATED"))) log.warn("logout.failed", errorSummary(e));
    }
    this.commands.reset();
    this.input.reset();
    this.lastAccountId = null;
    useLiveStore.getState().reset();
    useDevicesStore.getState().reset();
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
    this.input.reset();
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
        if (frame.pc_id === this.input.snapshot.pcId) this.input.setForegroundApp(frame.state.foreground_app ?? null);
        return;
      case "input_ack":
      case "input_session":
        if (!this.input.handleFrame(frame)) log.info("relay.input_frame_for_unknown_session", { type: frame.type });
        return;
      case "error":
        // INPUT_* / RATE_LIMITED errors name the PC (ref_pc_id) but concern the input stream, not the
        // subscription: the input client consumes them. Everything else with ref_pc_id is a refused
        // subscription (e.g. GRANT_MISSING).
        if (this.input.handleFrame(frame)) return;
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

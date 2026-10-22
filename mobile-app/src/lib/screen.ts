/**
 * Screen view session client (protocol 1.4; docs/design/screen-view.md §4, §7, §16; version.json rules.screen_signaling,
 * rules.screen_sessions, rules.pc_key_pinning, rules.screen_content).
 *
 * One session at a time, on the selected PC, in this order:
 *   1. the pin: no PC key pinned at pairing for this PC → SCREEN_PC_UNVERIFIED, nothing is sent ("pair again");
 *   2. `screen_ice_request` on the bound socket → `screen_ice_servers` (ICE servers minted for THIS phone, the Free
 *      allowance, whether a relay server is available) or an `error` frame (SCREEN_VIEW_DISABLED, allowance, …);
 *   3. a receive-only `RTCPeerConnection` from the injected factory (video + audio transceivers, the `dome-screen-v1`
 *      data channel), `createOffer`, wait for ICE gathering to complete or 3 s (vanilla ICE: no candidate travels on its
 *      own), the strict SDP check (`screenSdpAcceptable`);
 *   4. the offer inside the signed `screen.session_start` command (60 s lifetime, so a 30 s consent wait fits); the
 *      PC's `screen_session_result` and its `screen_answer` frame arrive in either order;
 *   5. the answer is accepted ONLY when `verifyScreenAnswer` passes against the pinned key for this account, controller,
 *      PC, command and the exact offer we sent; then, and only then, `setRemoteDescription` with exactly the signed
 *      string. Any failure is SCREEN_ANSWER_INVALID (a different key is the hard stop "this PC's identity changed") and
 *      nothing is ever shown. There is no "continue anyway";
 *   6. media flows DTLS-SRTP between the PC and this phone (never through the relay); `hb` every 2 s on the data
 *      channel renews the PC's 8 s viewer lease; `geometry` / `state` / `allowance` / `restricted` messages are parsed
 *      with the closed vocabulary and anything else is dropped; a `bye` ends the session when the page is hidden, left
 *      or stopped, together with the signed `screen.session_stop`.
 *
 * The phone never reads pixels: the stream only ever becomes a `<video>.srcObject` (test/no-pixel-access.test.ts).
 * Nothing here logs an SDP, a candidate, an address or a credential (log.ts also redacts those keys). The Free
 * allowance shown is a countdown for display; the PC's `ended{allowance_exhausted}` is authoritative.
 */
import { LIMITS, parseScreenDcMessage, ProtocolError, SCREEN_DATA_CHANNEL_LABEL, screenSdpAcceptable, verifyScreenAnswer, type relayFrames, type results, type ScreenDcPcToPhone } from "@dome/protocol";

import type { CommandRecord, Identity } from "./commands.ts";
import type { PcKeyPin } from "./controllerKey.ts";
import { SCREEN_UNSUPPORTED_BROWSER } from "./labels.ts";
import { errorSummary, log } from "./log.ts";
import { GeometryTracker, type Geometry } from "./screenMap.ts";

export type ScreenPhase = "idle" | "ice" | "offering" | "starting" | "connecting" | "live" | "paused" | "ended" | "failed";
export type ScreenEndReason = relayFrames.AgentScreenSession["reason"];
export type ScreenStopReason = "stopped" | "leave" | "hidden" | "pc_switch" | "sign_out" | "restart";
/** What the page may do when it is in front again after the app was hidden (§2.2 step 5). */
export type ScreenResume = "auto" | "manual" | "none";
export type ScreenTurn = relayFrames.RelayScreenIceServers["turn"];
export type ScreenSurface = results.ScreenSurface;

export interface ScreenAllowance {
  unlimited: boolean;
  remainingSeconds: number | null;
  resetsAt: string | null;
  /** Wall-clock ms when this figure was learned; the page counts down from it while live. */
  at: number;
}

export interface ScreenProblem {
  code: string;
  message?: string;
  detail?: Record<string, unknown>;
}

export interface ScreenSessionState {
  phase: ScreenPhase;
  pcId: string | null;
  commandId: string | null;
  sessionId: string | null;
  /** The PC acknowledged the start but has not answered: someone at the PC may be asked (§16 "consent: pending"). */
  consentWaiting: boolean;
  consentDeadlineAt: number | null;
  turn: ScreenTurn | null;
  path: "direct" | "relayed" | null;
  /** Connecting for a while with a relay server available: "Trying the relay server…". */
  tryingRelay: boolean;
  /** The PC-signed answer verified against the pinned key. Nothing is shown before this is true. */
  verified: boolean;
  /** The remote media stream, for `<video>.srcObject` only. */
  stream: MediaStream | null;
  startedAt: number | null;
  lastFrameAt: number | null;
  /** No frame for 2 s while the PC says it is sending: a keyframe was requested; a fresh session follows if it stays. */
  stalled: boolean;
  pcState: "sending" | "paused";
  pauseReason: "secure_desktop" | "indicator" | null;
  encoder: "h264" | "vp8" | null;
  dataSaver: boolean;
  restricted: { restricted: boolean; reason: string | null } | null;
  geometry: Geometry | null;
  surfaces: ScreenSurface[];
  consent: "not_required" | "granted" | null;
  allowance: ScreenAllowance | null;
  /** Why the session is not live (error code), or null. */
  problem: ScreenProblem | null;
  endReason: ScreenEndReason | null;
  /** How long the picture was live: the PC's figure from `ended`, else this phone's own count. */
  liveSeconds: number | null;
  /** Another phone owns the PC's session: a takeover prompt is needed. */
  owned: boolean;
  /** Another phone has a live Screen view on this PC (from `screen_session` frames). */
  otherViewer: boolean;
  /** Automatic fresh sessions after a stall or a failed transport in this run (at most MAX_RETRIES). */
  retries: number;
  resume: ScreenResume;
  /** The relay socket is down while media continues: clicks pause, the picture stays. */
  relayDown: boolean;
  /** Ms when the start command was acknowledged by the PC; null before. */
  ackAt: number | null;
  sentAt: number | null;
}

export const EMPTY_SCREEN_STATE: ScreenSessionState = {
  phase: "idle",
  pcId: null,
  commandId: null,
  sessionId: null,
  consentWaiting: false,
  consentDeadlineAt: null,
  turn: null,
  path: null,
  tryingRelay: false,
  verified: false,
  stream: null,
  startedAt: null,
  lastFrameAt: null,
  stalled: false,
  pcState: "sending",
  pauseReason: null,
  encoder: null,
  dataSaver: false,
  restricted: null,
  geometry: null,
  surfaces: [],
  consent: null,
  allowance: null,
  problem: null,
  endReason: null,
  liveSeconds: null,
  owned: false,
  otherViewer: false,
  retries: 0,
  resume: "none",
  relayDown: false,
  ackAt: null,
  sentAt: null,
};

/** The subset of RTCDataChannel this client uses (so tests can inject a fake). */
export interface DataChannelLike {
  readyState: "connecting" | "open" | "closing" | "closed";
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
}

export interface TransceiverLike {
  setCodecPreferences?(codecs: RTCRtpCodec[]): void;
}

/** The subset of RTCPeerConnection this client uses. `RTCPeerConnection` satisfies it structurally (see `defaultPeerFactory`). */
export interface PeerConnectionLike {
  addTransceiver(kind: "video" | "audio", init: { direction: "recvonly" }): TransceiverLike;
  createDataChannel(label: string, init: { ordered: boolean }): DataChannelLike;
  createOffer(): Promise<{ type?: string; sdp?: string }>;
  setLocalDescription(description: { type?: string; sdp?: string }): Promise<void>;
  setRemoteDescription(description: { type: "answer"; sdp: string }): Promise<void>;
  readonly localDescription: { sdp: string } | null;
  readonly iceGatheringState: "new" | "gathering" | "complete";
  readonly connectionState: string;
  readonly iceConnectionState: string;
  close(): void;
  onicegatheringstatechange: ((ev: unknown) => void) | null;
  onconnectionstatechange: ((ev: unknown) => void) | null;
  oniceconnectionstatechange: ((ev: unknown) => void) | null;
  ontrack: ((ev: { streams: readonly MediaStream[]; track: { kind: string } }) => void) | null;
}

export type PeerFactory = (config: { iceServers: RTCIceServer[] }) => PeerConnectionLike;

/** The browser's own RTCPeerConnection, or null where WebRTC does not exist (jsdom, a very old browser). */
export function defaultPeerFactory(): PeerFactory | null {
  if (typeof RTCPeerConnection !== "function") return null;
  return (config) => new RTCPeerConnection(config) as unknown as PeerConnectionLike;
}

export interface ScreenTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(h: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(h: unknown): void;
}

export interface ScreenClientDeps {
  /** Validated controller frame on the bound socket (`screen_ice_request`); throws when the socket is not open. */
  sendFrame(frame: relayFrames.ControllerToRelay): void;
  /** Signed command through the ordinary command path; resolves with the record once sent. */
  sendCommand(input: { pcId: string; action: string; params: Record<string, string | number | boolean>; lifetimeSeconds?: number; source: CommandRecord["source"] }): Promise<CommandRecord>;
  identity(): Identity | null;
  connected(): boolean;
  /** The PC key pinned at pairing for `pcId` (lib/pcKeyPins.ts), or null. */
  getPin(pcId: string): Promise<PcKeyPin | null>;
  /** null = no WebRTC in this browser: every start fails honestly with SCREEN_UNSUPPORTED_BROWSER. */
  peerFactory: PeerFactory | null;
  onChange(state: ScreenSessionState): void;
  timers?: ScreenTimers;
  now?: () => Date;
}

export const HEARTBEAT_MS = LIMITS.screen_heartbeat_seconds * 1000;
export const CONNECT_TIMEOUT_MS = LIMITS.screen_connect_timeout_seconds * 1000;
/** Waiting for `screen_ice_servers`: the relay answers at once or refuses; longer than this is a dead socket. */
export const ICE_SERVERS_TIMEOUT_MS = 10_000;
/** ICE gathering: complete, or this long (§4.2). */
export const GATHER_TIMEOUT_MS = 3_000;
/** After this long connecting with a relay server available, the copy says the relay is being tried. */
export const RELAY_HINT_MS = 5_000;
/** The PC acknowledged the start and this long passed without a result: someone at the PC is probably being asked. */
export const CONSENT_HINT_MS = 2_000;
/** No frame for this long while the PC says `sending`: a keyframe is asked for (§7 "Phone lifecycle"). */
export const STALL_MS = 2_000;
/** Still no frame this long after the keyframe request: a fresh session, at most MAX_RETRIES times. */
export const STALL_RESTART_MS = 5_000;
export const MAX_RETRIES = 2;
/** A consent refusal this soon after the start was answered without a window (the back-off): different copy. */
export const BACKOFF_REFUSAL_MS = 1_500;

const globalTimers: ScreenTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

type IceOutcome = { kind: "servers"; frame: relayFrames.RelayScreenIceServers } | { kind: "error"; error: relayFrames.Error } | { kind: "timeout" } | { kind: "aborted" };

/** End reasons after which the phone never restarts by itself (§2.2 step 5, §7). */
const NO_RESUME: ReadonlySet<ScreenEndReason> = new Set(["stopped", "stopped_on_pc", "replaced", "takeover", "controller_revoked", "grant_removed", "plan_disabled", "remote_disabled", "allowance_exhausted"]);

function allowanceFrom(a: relayFrames.ScreenAllowanceState, at: number): ScreenAllowance {
  return { unlimited: a.unlimited, remainingSeconds: a.remaining_seconds, resetsAt: a.resets_at, at };
}

export class ScreenSessionClient {
  private readonly deps: ScreenClientDeps;
  private readonly timers: ScreenTimers;
  private state: ScreenSessionState = { ...EMPTY_SCREEN_STATE };
  readonly geometry = new GeometryTracker();
  private pc: PeerConnectionLike | null = null;
  private dc: DataChannelLike | null = null;
  private offerSdp: string | null = null;
  private pin: PcKeyPin | null = null;
  private iceWaiter: { resolve: (o: IceOutcome) => void; timer: unknown } | null = null;
  /** Bumped by stop()/reset()/a new start so a start still in flight cannot resurrect anything. */
  private nonce = 0;
  private hbTimer: unknown = null;
  private connectTimer: unknown = null;
  private relayHintTimer: unknown = null;
  private consentTimer: unknown = null;
  private stallTimer: unknown = null;
  private answerApplied = false;
  private dcDropped = 0;
  /** The PC's active pause causes (relay frames come per cause; capture resumes only when none is left). */
  private pauseCauses = new Set<"secure_desktop" | "indicator">();

  constructor(deps: ScreenClientDeps) {
    this.deps = deps;
    this.timers = deps.timers ?? globalTimers;
  }

  get snapshot(): ScreenSessionState {
    return this.state;
  }

  get isLive(): boolean {
    return this.state.phase === "live" || this.state.phase === "paused";
  }

  private set(patch: Partial<ScreenSessionState>): void {
    this.state = { ...this.state, ...patch };
    this.deps.onChange(this.state);
  }

  private now(): number {
    return (this.deps.now?.() ?? new Date()).getTime();
  }

  // ---- starting ------------------------------------------------------------------------------------

  /**
   * Start, or take over, Screen view on `pcId`. Never throws for a refusal: the state carries the error code
   * (SCREEN_SESSION_OWNED sets `owned` for the takeover prompt). A session on another PC, or an earlier one on this PC,
   * is stopped first (a same-phone restart replaces its own session on the PC: `replaced`).
   */
  async start(pcId: string, options: { takeover?: boolean; retries?: number } = {}): Promise<ScreenSessionState> {
    if (this.state.phase !== "idle" && this.state.phase !== "ended" && this.state.phase !== "failed") await this.stop(this.state.pcId === pcId ? "restart" : "pc_switch");
    const nonce = ++this.nonce;
    const kept = { allowance: this.state.pcId === pcId ? this.state.allowance : null, otherViewer: this.state.pcId === pcId ? this.state.otherViewer : false };
    this.clearTimers();
    this.geometry.reset();
    this.offerSdp = null;
    this.answerApplied = false;
    this.dcDropped = 0;
    this.pauseCauses.clear();
    this.set({ ...EMPTY_SCREEN_STATE, ...kept, phase: "ice", pcId, retries: options.retries ?? 0 });
    if (!this.deps.peerFactory) return this.fail(SCREEN_UNSUPPORTED_BROWSER);
    if (!this.deps.connected()) return this.fail("PC_RECONNECTING");
    const identity = this.deps.identity();
    if (!identity) return this.fail("UNKNOWN_KEY");
    // 1. the pin (rules.pc_key_pinning (5)): without it there is nothing to verify an answer against, so nothing is asked for
    this.pin = await this.deps.getPin(pcId);
    if (nonce !== this.nonce) return this.state;
    if (!this.pin) return this.fail("SCREEN_PC_UNVERIFIED");
    // 2. ICE servers and the allowance
    let ice: IceOutcome;
    try {
      this.deps.sendFrame({ type: "screen_ice_request", pc_id: pcId });
      ice = await this.waitForIce();
    } catch (e) {
      log.warn("screen.ice_request_failed", errorSummary(e));
      if (nonce !== this.nonce) return this.state;
      return this.fail(e instanceof ProtocolError ? e.code : "INTERNAL");
    }
    if (nonce !== this.nonce) return this.state;
    if (ice.kind === "aborted") return this.state;
    if (ice.kind === "timeout") return this.fail("NO_ANSWER");
    if (ice.kind === "error") return this.fail(ice.error.code, ice.error.message, ice.error.detail);
    const servers = ice.frame;
    this.set({ turn: servers.turn, allowance: allowanceFrom(servers.allowance, this.now()), phase: "offering" });
    // 3. the offer
    let offerSdp: string;
    try {
      offerSdp = await this.buildOffer(servers.ice_servers as RTCIceServer[]);
    } catch (e) {
      log.warn("screen.offer_failed", errorSummary(e));
      if (nonce !== this.nonce) return this.state;
      return this.fail("SCREEN_OFFER_INVALID");
    }
    if (nonce !== this.nonce) return this.state;
    if (!screenSdpAcceptable(offerSdp)) return this.fail("SCREEN_OFFER_INVALID");
    this.offerSdp = offerSdp;
    // 4. the signed start command, with the lifetime a consent wait needs
    let record: CommandRecord;
    try {
      record = await this.deps.sendCommand({ pcId, action: "screen.session_start", params: { offer_sdp: offerSdp, surface: "primary", audio: false, takeover: options.takeover === true }, lifetimeSeconds: LIMITS.screen_start_command_lifetime_seconds, source: "system" });
    } catch (e) {
      log.warn("screen.start_send_failed", errorSummary(e));
      if (nonce !== this.nonce) return this.state;
      return this.fail(e instanceof ProtocolError ? e.code : "INTERNAL");
    }
    if (nonce !== this.nonce) return this.state;
    this.set({ phase: "starting", commandId: record.commandId, sentAt: this.now() });
    log.info("screen.start_sent", { pc_id: pcId, command_id: record.commandId, takeover: options.takeover === true, turn: servers.turn });
    return this.state;
  }

  private waitForIce(): Promise<IceOutcome> {
    return new Promise<IceOutcome>((resolve) => {
      const timer = this.timers.setTimeout(() => {
        if (this.iceWaiter?.timer === timer) this.iceWaiter = null;
        resolve({ kind: "timeout" });
      }, ICE_SERVERS_TIMEOUT_MS);
      this.iceWaiter = { resolve, timer };
    });
  }

  private resolveIce(outcome: IceOutcome): void {
    const w = this.iceWaiter;
    if (!w) return;
    this.iceWaiter = null;
    this.timers.clearTimeout(w.timer);
    w.resolve(outcome);
  }

  /** The receive-only offer (§4.2 step 2). The peer connection and data channel stay in this client. */
  private async buildOffer(iceServers: RTCIceServer[]): Promise<string> {
    const factory = this.deps.peerFactory;
    if (!factory) throw new ProtocolError(SCREEN_UNSUPPORTED_BROWSER, "no WebRTC");
    const pc = factory({ iceServers });
    this.pc = pc;
    const video = pc.addTransceiver("video", { direction: "recvonly" });
    // audio always, even in Phase 1 without sound: adding it later would pause an unmuted video on iOS (§2.2)
    const audio = pc.addTransceiver("audio", { direction: "recvonly" });
    preferCodecs(video, "video");
    preferCodecs(audio, "audio");
    const dc = pc.createDataChannel(SCREEN_DATA_CHANNEL_LABEL, { ordered: true });
    this.dc = dc;
    dc.onopen = () => this.onChannelOpen();
    dc.onmessage = (ev) => this.onChannelMessage(ev.data);
    dc.onclose = () => this.stopHeartbeat();
    pc.ontrack = (ev) => this.onTrack(ev);
    pc.onconnectionstatechange = () => this.onPeerState(pc);
    pc.oniceconnectionstatechange = () => this.onPeerState(pc);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await this.waitForGathering(pc);
    const sdp = pc.localDescription?.sdp ?? offer.sdp;
    if (typeof sdp !== "string") throw new ProtocolError("SCREEN_OFFER_INVALID", "no local description");
    return sdp;
  }

  /** Vanilla ICE: every candidate is in the offer. Gathering complete, or GATHER_TIMEOUT_MS. */
  private waitForGathering(pc: PeerConnectionLike): Promise<void> {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise<void>((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        pc.onicegatheringstatechange = null;
        this.timers.clearTimeout(timer);
        resolve();
      };
      const timer = this.timers.setTimeout(finish, GATHER_TIMEOUT_MS);
      pc.onicegatheringstatechange = () => {
        if (pc.iceGatheringState === "complete") finish();
      };
    });
  }

  // ---- the PC's two answers: the result and the signed answer, in either order ------------------------

  /** Every command record the service produces goes through here; only the pending start matters. */
  handleCommandRecord(record: CommandRecord): void {
    if (!this.state.commandId || record.commandId !== this.state.commandId) return;
    if (this.state.phase !== "starting" && this.state.phase !== "connecting") return;
    if (record.terminal) {
      const t = record.terminal;
      if (t.state === "succeeded" && t.result && !t.resultInvalid) {
        this.onResult(t.result as unknown as results.ScreenSessionResult);
        return;
      }
      const code = t.error?.code ?? (t.state === "succeeded" ? "MALFORMED_MESSAGE" : t.state === "outcome_unknown" ? "OUTCOME_UNKNOWN" : "COMMAND_EXPIRED");
      log.info("screen.start_refused", { code, state: t.state, origin: t.origin });
      this.fail(code, t.error?.message, t.error?.detail as Record<string, unknown> | undefined);
      return;
    }
    if (record.noAnswer) {
      this.fail("NO_ANSWER");
      return;
    }
    if ((record.state === "accepted" || record.state === "executing") && this.state.ackAt === null) {
      const ackAt = this.now();
      this.set({ ackAt });
      this.consentTimer = this.timers.setTimeout(() => {
        this.consentTimer = null;
        if (this.state.ackAt === ackAt && this.state.sessionId === null && (this.state.phase === "starting" || this.state.phase === "connecting")) {
          this.set({ consentWaiting: true, consentDeadlineAt: ackAt + LIMITS.screen_consent_timeout_seconds * 1000 });
        }
      }, CONSENT_HINT_MS);
    }
  }

  private onResult(r: results.ScreenSessionResult): void {
    if (this.state.sessionId && this.state.sessionId !== r.screen_session_id) {
      // the signed answer named another session than the result: the two must agree, or nothing is shown
      this.failHard("SCREEN_ANSWER_INVALID", { reason: "screen_session_id differs between answer and result" });
      return;
    }
    if (this.consentTimer !== null) this.timers.clearTimeout(this.consentTimer);
    this.consentTimer = null;
    const primary = r.surfaces.find((s) => s.index === r.surface_index) ?? r.surfaces[0];
    if (primary) this.geometry.seed(r.gen, r.surface_index, primary.width, primary.height);
    this.set({
      sessionId: r.screen_session_id,
      surfaces: [...r.surfaces],
      geometry: this.geometry.known,
      consent: r.consent,
      consentWaiting: false,
      consentDeadlineAt: null,
      allowance: allowanceFrom(r.allowance, this.now()),
      encoder: r.encoder ?? this.state.encoder,
    });
    log.info("screen.session_result", { pc_id: this.state.pcId, consent: r.consent, encoder: r.encoder ?? null, surfaces: r.surfaces.length });
  }

  /** Returns true when the frame concerned this client (consumed). */
  handleFrame(frame: relayFrames.RelayToController): boolean {
    switch (frame.type) {
      case "screen_ice_servers":
        return this.onIceServers(frame);
      case "screen_answer":
        void this.onAnswer(frame);
        return true;
      case "screen_session":
        return this.onSessionFrame(frame);
      case "error":
        return this.onError(frame);
      default:
        return false;
    }
  }

  private onIceServers(frame: relayFrames.RelayScreenIceServers): boolean {
    if (frame.pc_id !== this.state.pcId || this.state.phase !== "ice" || !this.iceWaiter) return false;
    this.resolveIce({ kind: "servers", frame });
    return true;
  }

  /** Error frames with ref_pc_id that answer our `screen_ice_request` (only while waiting for it). */
  private onError(frame: relayFrames.ErrorFrame): boolean {
    if (this.state.phase !== "ice" || !this.iceWaiter || !frame.ref_pc_id || frame.ref_pc_id !== this.state.pcId) return false;
    if (frame.ref_input_session_id) return false; // an input-batch rejection, never ours
    this.resolveIce({ kind: "error", error: frame.error });
    return true;
  }

  /** rules.screen_signaling (6): the acceptance test, then exactly the signed SDP, or nothing at all. */
  private async onAnswer(frame: relayFrames.RelayScreenAnswer): Promise<void> {
    if (frame.pc_id !== this.state.pcId) return;
    if (this.state.phase !== "starting" && this.state.phase !== "connecting") {
      log.info("screen.answer_ignored", { phase: this.state.phase });
      return;
    }
    if (this.answerApplied) {
      log.info("screen.answer_duplicate_dropped");
      return;
    }
    const identity = this.deps.identity();
    const pc = this.pc;
    if (!identity || !pc || !this.pin || !this.offerSdp || !this.state.commandId) {
      this.failHard("SCREEN_ANSWER_INVALID", { reason: "no pending start" });
      return;
    }
    const nonce = this.nonce;
    let verified;
    try {
      verified = await verifyScreenAnswer(frame.envelope, { pinnedJwk: this.pin.pc_public_jwk, accountId: identity.accountId, controllerId: identity.controllerId, pcId: this.state.pcId!, commandId: this.state.commandId, offerSdp: this.offerSdp, now: new Date(this.now()) });
    } catch (e) {
      if (nonce !== this.nonce) return;
      const code = e instanceof ProtocolError ? e.code : "SCREEN_ANSWER_INVALID";
      const detail = e instanceof ProtocolError && e.detail ? (e.detail as Record<string, unknown>) : undefined;
      log.warn("screen.answer_rejected", { code, reason: detail?.reason ?? null });
      this.failHard(code, detail);
      return;
    }
    if (nonce !== this.nonce) return;
    if (this.state.sessionId && this.state.sessionId !== verified.payload.screen_session_id) {
      this.failHard("SCREEN_ANSWER_INVALID", { reason: "screen_session_id differs between answer and result" });
      return;
    }
    this.answerApplied = true;
    try {
      await pc.setRemoteDescription({ type: "answer", sdp: verified.payload.answer_sdp });
    } catch (e) {
      if (nonce !== this.nonce) return;
      log.warn("screen.answer_apply_failed", errorSummary(e));
      this.failHard("SCREEN_CONNECTION_FAILED");
      return;
    }
    if (nonce !== this.nonce) return;
    this.set({ verified: true, phase: "connecting", sessionId: this.state.sessionId ?? verified.payload.screen_session_id, consentWaiting: false, consentDeadlineAt: null });
    log.info("screen.answer_verified", { pc_id: this.state.pcId });
    this.armConnectTimers();
    // the transport may already be connected (the fake peer, or a very fast path)
    this.onPeerState(pc);
  }

  private armConnectTimers(): void {
    this.connectTimer = this.timers.setTimeout(() => {
      this.connectTimer = null;
      if (this.state.phase === "connecting") {
        log.info("screen.connect_timeout");
        this.fail("SCREEN_CONNECTION_FAILED");
      }
    }, CONNECT_TIMEOUT_MS);
    this.relayHintTimer = this.timers.setTimeout(() => {
      this.relayHintTimer = null;
      if (this.state.phase === "connecting" && this.state.turn === "available") this.set({ tryingRelay: true });
    }, RELAY_HINT_MS);
  }

  // ---- the transport ----------------------------------------------------------------------------------

  private onTrack(ev: { streams: readonly MediaStream[]; track: { kind: string } }): void {
    if (!this.state.verified) {
      // cannot happen: no remote description is ever applied before verification. Kept as a structural guard.
      log.warn("screen.track_before_verification_dropped");
      return;
    }
    if (ev.track.kind !== "video") return;
    const stream = ev.streams[0] ?? null;
    if (stream) this.set({ stream });
  }

  private onPeerState(pc: PeerConnectionLike): void {
    if (this.pc !== pc) return;
    const failed = pc.connectionState === "failed" || pc.iceConnectionState === "failed";
    if (failed) {
      this.onTransportFailed();
      return;
    }
    if (pc.connectionState === "connected" && this.state.phase === "connecting") {
      // DTLS is up with the certificate the PC signed; the picture counts as live from the first decoded frame
      if (this.connectTimer !== null) this.timers.clearTimeout(this.connectTimer);
      this.connectTimer = null;
      if (this.relayHintTimer !== null) this.timers.clearTimeout(this.relayHintTimer);
      this.relayHintTimer = null;
      this.set({ tryingRelay: false });
    }
  }

  /** The `<video>` decoded a frame (requestVideoFrameCallback; metadata only, never pixels). */
  onVideoFrame(nowMs: number = this.now()): void {
    if (this.state.phase === "connecting" && this.state.verified) {
      this.set({ phase: "live", startedAt: nowMs, lastFrameAt: nowMs, stalled: false });
      log.info("screen.live", { pc_id: this.state.pcId, retries: this.state.retries });
      this.startStallWatchdog();
      return;
    }
    if (this.state.phase === "live" || this.state.phase === "paused") this.set({ lastFrameAt: nowMs, stalled: false });
  }

  /** The geometry a tap may use right now on this client's clock (null while held back after a resize), and whether one is held back. */
  tapGeometry(): { geometry: Geometry | null; stale: boolean } {
    return { geometry: this.geometry.current(this.now()), stale: this.geometry.isStale };
  }

  /** The `<video>` fired `resize`: taps wait for the matching geometry message (≤ 300 ms). */
  onVideoResize(nowMs: number = this.now()): void {
    this.geometry.markStale(nowMs);
  }

  private startStallWatchdog(): void {
    if (this.stallTimer !== null) this.timers.clearInterval(this.stallTimer);
    this.stallTimer = this.timers.setInterval(() => this.checkStall(), 1000);
  }

  private checkStall(): void {
    if (this.state.phase !== "live" || this.state.pcState !== "sending" || this.state.lastFrameAt === null) return;
    const silent = this.now() - this.state.lastFrameAt;
    if (silent >= STALL_RESTART_MS) {
      log.info("screen.stall_restart", { retries: this.state.retries });
      void this.restartFresh("stall");
      return;
    }
    if (silent >= STALL_MS && !this.state.stalled) {
      this.set({ stalled: true });
      this.requestKeyframe();
    }
  }

  /** Ask the PC for an IDR (a frozen or black picture after an app switch). */
  requestKeyframe(): void {
    this.sendChannel({ type: "keyframe" });
  }

  private onTransportFailed(): void {
    if (this.state.phase === "live" || this.state.phase === "paused") {
      log.info("screen.transport_failed_live", { retries: this.state.retries });
      void this.restartFresh("transport");
    } else if (this.state.phase === "connecting") {
      this.fail("SCREEN_CONNECTION_FAILED");
    }
  }

  /** A fresh signed session after a stall or a lost transport, at most MAX_RETRIES times; then the failure state. */
  private async restartFresh(cause: "stall" | "transport"): Promise<void> {
    const pcId = this.state.pcId;
    const retries = this.state.retries;
    if (!pcId) return;
    if (retries >= MAX_RETRIES) {
      log.info("screen.retries_exhausted", { cause });
      await this.stop("restart");
      this.set({ phase: "failed", problem: { code: "SCREEN_CONNECTION_FAILED", detail: { cause } }, resume: "none" });
      return;
    }
    await this.stop("restart");
    await this.start(pcId, { retries: retries + 1 });
  }

  // ---- the data channel -------------------------------------------------------------------------------

  private onChannelOpen(): void {
    this.sendChannel({ type: "hb" });
    this.stopHeartbeat();
    this.hbTimer = this.timers.setInterval(() => this.sendChannel({ type: "hb" }), HEARTBEAT_MS);
  }

  private stopHeartbeat(): void {
    if (this.hbTimer !== null) this.timers.clearInterval(this.hbTimer);
    this.hbTimer = null;
  }

  private sendChannel(message: { type: "hb" } | { type: "keyframe" } | { type: "bye" } | { type: "audio"; on: boolean }): boolean {
    const dc = this.dc;
    if (!dc || dc.readyState !== "open") return false;
    try {
      dc.send(JSON.stringify(message));
      return true;
    } catch (e) {
      log.info("screen.dc_send_failed", errorSummary(e));
      return false;
    }
  }

  /** rules.screen_content: the closed inbound vocabulary; anything else is dropped (counted, never shown or run). */
  private onChannelMessage(data: unknown): void {
    if (typeof data !== "string") {
      this.dcDropped += 1;
      return;
    }
    let msg: ScreenDcPcToPhone;
    try {
      msg = parseScreenDcMessage(data, "pc_to_phone");
    } catch (e) {
      this.dcDropped += 1;
      if (this.dcDropped <= 3) log.info("screen.dc_message_dropped", { ...errorSummary(e), dropped: this.dcDropped });
      return;
    }
    switch (msg.type) {
      case "geometry":
        if (this.geometry.apply(msg)) this.set({ geometry: this.geometry.known });
        return;
      case "state": {
        const paused = msg.state === "paused";
        if (paused && msg.reason) this.pauseCauses.add(msg.reason);
        if (!paused) this.pauseCauses.clear(); // the worker sends `sending` only once no cause is left
        const phase = this.state.phase === "live" && paused ? "paused" : this.state.phase === "paused" && !paused ? "live" : this.state.phase;
        this.set({ pcState: msg.state, pauseReason: paused ? (this.pauseShown() ?? msg.reason ?? null) : null, phase, ...(msg.encoder ? { encoder: msg.encoder } : {}), ...(msg.data_saver !== undefined ? { dataSaver: msg.data_saver } : {}), ...(paused ? {} : { stalled: false, lastFrameAt: this.now() }) });
        return;
      }
      case "allowance":
        this.set({ allowance: allowanceFrom(msg, this.now()) });
        return;
      case "restricted":
        this.set({ restricted: { restricted: msg.restricted, reason: msg.reason ?? null } });
        return;
    }
  }

  // ---- lifecycle frames from the relay ----------------------------------------------------------------

  private onSessionFrame(frame: relayFrames.AgentScreenSession): boolean {
    if (frame.pc_id !== this.state.pcId) return false;
    const identity = this.deps.identity();
    const ours = identity !== null && frame.controller_id === identity.controllerId && (this.state.sessionId === null || this.state.sessionId === frame.screen_session_id);
    if (!ours) {
      // another phone's session on our PC: ownership only (the relay broadcasts these to 1.4 subscribers)
      if (frame.event === "started") this.set({ otherViewer: true });
      else if (frame.event === "ended") this.set({ otherViewer: false });
      return true;
    }
    if (this.state.phase === "idle" || this.state.phase === "ended" || this.state.phase === "failed") {
      // a late frame for a session this phone already left (its own `bye` ended it): nothing to change
      return true;
    }
    switch (frame.event) {
      case "started":
        this.set({ path: frame.path ?? null, sessionId: this.state.sessionId ?? frame.screen_session_id });
        return true;
      case "paused":
        this.pauseCauses.add(frame.reason === "indicator" ? "indicator" : "secure_desktop");
        this.set({ pcState: "paused", pauseReason: this.pauseShown(), phase: this.state.phase === "live" ? "paused" : this.state.phase });
        return true;
      case "resumed":
        // per cause: a UAC prompt answered while a bar is still missing keeps the picture paused (and the other way)
        this.pauseCauses.delete(frame.reason === "indicator" ? "indicator" : "secure_desktop");
        if (this.pauseCauses.size > 0) {
          this.set({ pauseReason: this.pauseShown(), ...(frame.path ? { path: frame.path } : {}) });
          return true;
        }
        this.set({ pcState: "sending", pauseReason: null, phase: this.state.phase === "paused" ? "live" : this.state.phase, ...(frame.path ? { path: frame.path } : {}), lastFrameAt: this.now(), stalled: false });
        return true;
      case "ended":
        this.endSession(frame.reason, frame.live_seconds);
        return true;
    }
    return true;
  }

  /** The PC ended the session (rules.screen_sessions (6)): close the transport and show the reason. */
  /** The cause the page explains while paused: a Windows prompt first (someone must answer it), else a missing bar. */
  private pauseShown(): "secure_desktop" | "indicator" | null {
    if (this.pauseCauses.has("secure_desktop")) return "secure_desktop";
    return this.pauseCauses.has("indicator") ? "indicator" : null;
  }

  private endSession(reason: ScreenEndReason, liveSeconds: number): void {
    this.pauseCauses.clear();
    this.nonce += 1;
    this.resolveIce({ kind: "aborted" });
    this.clearTimers();
    this.closePeer();
    const resume: ScreenResume = NO_RESUME.has(reason) ? "none" : "manual";
    this.set({ phase: "ended", endReason: reason, liveSeconds, stream: null, verified: false, resume, consentWaiting: false, consentDeadlineAt: null, stalled: false, tryingRelay: false, owned: reason === "takeover" });
    log.info("screen.session_ended", { pc_id: this.state.pcId, reason, live_seconds: liveSeconds });
  }

  // ---- stopping -------------------------------------------------------------------------------------

  /**
   * End the session from this phone: `bye` on the data channel (the fastest path), the signed `screen.session_stop`
   * (idempotent on the PC), close the transport. Safe to call in any phase. `stopped` is the owner's Stop (shows
   * "Stopped · m min s"); the other reasons go back to idle; `hidden` remembers whether to resume (§2.2 step 5).
   */
  async stop(reason: ScreenStopReason = "stopped"): Promise<void> {
    this.nonce += 1;
    this.resolveIce({ kind: "aborted" });
    const { phase, pcId, sessionId, startedAt, consent, allowance, otherViewer, retries } = this.state;
    this.clearTimers();
    if (phase === "idle") return;
    const liveSeconds = startedAt !== null ? Math.max(0, Math.round((this.now() - startedAt) / 1000)) : 0;
    const inSession = phase === "connecting" || phase === "live" || phase === "paused" || phase === "starting";
    if (inSession) this.sendChannel({ type: "bye" });
    this.closePeer();
    const resume: ScreenResume = reason === "hidden" && (inSession || phase === "ice" || phase === "offering") ? (consent === "granted" ? "manual" : "auto") : "none";
    if (reason === "stopped") this.set({ ...EMPTY_SCREEN_STATE, pcId, allowance, otherViewer, phase: "ended", endReason: "stopped", liveSeconds, resume: "none" });
    else this.set({ ...EMPTY_SCREEN_STATE, pcId, allowance, otherViewer, phase: "idle", resume, retries: reason === "restart" ? retries : 0 });
    if (sessionId && pcId && inSession && this.deps.connected()) {
      try {
        await this.deps.sendCommand({ pcId, action: "screen.session_stop", params: { screen_session_id: sessionId }, source: "system" });
        log.info("screen.session_stop_sent", { pc_id: pcId, reason });
      } catch (e) {
        // the PC's 8 s viewer lease ends the session on its own
        log.info("screen.session_stop_not_sent", { reason, ...errorSummary(e) });
      }
    } else {
      log.info("screen.session_left", { reason, phase });
    }
  }

  /** The app went to the background or the phone locked: `bye`, close, and remember whether to resume. */
  onHidden(): Promise<void> {
    if (this.state.phase === "idle" || this.state.phase === "ended" || this.state.phase === "failed") return Promise.resolve();
    return this.stop("hidden");
  }

  /** The page is in front again: what it may do, read once. */
  takeResume(): ScreenResume {
    const r = this.state.resume;
    if (r !== "none") this.set({ resume: "none" });
    return r;
  }

  /** The relay socket left `open`: a start in flight fails; a live picture continues (media is not the relay's). */
  onConnectionLost(): void {
    if (this.state.phase === "ice" || this.state.phase === "offering" || this.state.phase === "starting") {
      this.resolveIce({ kind: "aborted" });
      this.fail("PC_RECONNECTING");
      return;
    }
    if (this.state.phase === "connecting" || this.state.phase === "live" || this.state.phase === "paused") this.set({ relayDown: true });
  }

  onConnectionRestored(): void {
    if (this.state.relayDown) this.set({ relayDown: false });
  }

  /** An input batch of this phone was refused for a screen reason: a stale `gen` holds taps until the PC's next geometry. */
  onInputRejected(code: string): void {
    if (code === "SCREEN_GEOMETRY_CHANGED") this.geometry.markStale(this.now());
  }

  /** Clears a non-fatal notice once the customer read it. */
  clearProblem(): void {
    if (this.state.problem && (this.state.phase === "live" || this.state.phase === "idle")) this.set({ problem: null });
  }

  /** Sign-out / runtime stop: forget everything without sending (the socket is already closed). */
  reset(): void {
    this.nonce += 1;
    this.resolveIce({ kind: "aborted" });
    this.clearTimers();
    this.closePeer();
    this.geometry.reset();
    this.offerSdp = null;
    this.pin = null;
    this.set({ ...EMPTY_SCREEN_STATE });
  }

  // ---- failures ------------------------------------------------------------------------------------

  private fail(code: string, message?: string, detail?: Record<string, unknown>): ScreenSessionState {
    this.nonce += 1;
    this.resolveIce({ kind: "aborted" });
    this.clearTimers();
    this.closePeer();
    const resetsAt = typeof detail?.resets_at === "string" ? detail.resets_at : null;
    const allowance = code === "SCREEN_ALLOWANCE_EXHAUSTED" ? { unlimited: false, remainingSeconds: 0, resetsAt, at: this.now() } : this.state.allowance;
    this.set({ phase: "failed", problem: { code, ...(message ? { message } : {}), ...(detail ? { detail } : {}) }, owned: code === "SCREEN_SESSION_OWNED", stream: null, verified: false, consentWaiting: false, consentDeadlineAt: null, tryingRelay: false, stalled: false, resume: "none", allowance });
    log.info("screen.failed", { code, phase: this.state.phase });
    return this.state;
  }

  /** The answer did not verify: abandon the session (rules.pc_key_pinning (5)); the PC's own stop is best effort. */
  private failHard(code: string, detail?: Record<string, unknown>): void {
    const { pcId, sessionId } = this.state;
    const connected = this.deps.connected();
    this.fail(code, undefined, detail);
    if (sessionId && pcId && connected) {
      void this.deps.sendCommand({ pcId, action: "screen.session_stop", params: { screen_session_id: sessionId }, source: "system" }).catch((e: unknown) => log.info("screen.session_stop_not_sent", errorSummary(e)));
    }
  }

  private closePeer(): void {
    const dc = this.dc;
    const pc = this.pc;
    this.dc = null;
    this.pc = null;
    this.answerApplied = false;
    this.stopHeartbeat();
    if (dc) {
      dc.onopen = dc.onmessage = dc.onclose = null;
      try {
        dc.close();
      } catch {
        /* already closed */
      }
    }
    if (pc) {
      pc.ontrack = pc.onconnectionstatechange = pc.oniceconnectionstatechange = pc.onicegatheringstatechange = null;
      try {
        pc.close();
      } catch {
        /* already closed */
      }
    }
  }

  private clearTimers(): void {
    this.stopHeartbeat();
    for (const h of [this.connectTimer, this.relayHintTimer, this.consentTimer]) if (h !== null) this.timers.clearTimeout(h);
    this.connectTimer = this.relayHintTimer = this.consentTimer = null;
    if (this.stallTimer !== null) this.timers.clearInterval(this.stallTimer);
    this.stallTimer = null;
  }
}

/** H.264 (packetization-mode 1) then VP8 for video, Opus for audio, where the browser lets a receiver say so. */
function preferCodecs(transceiver: TransceiverLike, kind: "video" | "audio"): void {
  try {
    const receiver = (globalThis as { RTCRtpReceiver?: { getCapabilities?: (kind: string) => { codecs: RTCRtpCodec[] } | null } }).RTCRtpReceiver;
    const caps = receiver?.getCapabilities?.(kind);
    if (!caps || typeof transceiver.setCodecPreferences !== "function") return;
    const rank = (c: RTCRtpCodec): number => {
      const mime = c.mimeType.toLowerCase();
      const fmtp = (c.sdpFmtpLine ?? "").toLowerCase();
      if (kind === "video") {
        if (mime === "video/h264" && fmtp.includes("packetization-mode=1")) return 0;
        if (mime === "video/h264") return 1;
        if (mime === "video/vp8") return 2;
        if (mime === "video/rtx") return 3;
        return 9;
      }
      return mime === "audio/opus" ? 0 : 9;
    };
    const ordered = [...caps.codecs].sort((a, b) => rank(a) - rank(b));
    transceiver.setCodecPreferences(ordered);
  } catch {
    /* preferences are an optimisation; the PC offers H.264 or VP8 either way */
  }
}

/** The error code that best explains a `screen_session` end reason (copy lives in screenStatus.ts / errors.json). */
export function screenEndCode(reason: ScreenEndReason | null): string | null {
  switch (reason) {
    case "allowance_exhausted":
      return "SCREEN_ALLOWANCE_EXHAUSTED";
    case "capture_failed":
      return "SCREEN_CAPTURE_UNAVAILABLE";
    case "connection_failed":
      return "SCREEN_CONNECTION_FAILED";
    case "session_locked":
      return "PC_SESSION_LOCKED";
    case "secure_desktop":
      return "INPUT_RESTRICTED";
    case "remote_disabled":
      return "PC_REMOTE_DISABLED";
    case "grant_removed":
      return "GRANT_MISSING";
    case "controller_revoked":
      return "CONTROLLER_REVOKED";
    case "plan_disabled":
      return "SCREEN_VIEW_DISABLED";
    case "takeover":
      return "SCREEN_SESSION_OWNED";
    case "controller_offline":
    case "allowance_unconfirmed":
    case "agent_restart":
    case "viewer_gone":
      return "PC_RECONNECTING";
    default:
      return null;
  }
}

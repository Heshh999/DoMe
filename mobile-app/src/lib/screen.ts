/**
 * Screen view session client (protocol 1.4; docs/design/screen-view.md §4, §7, §16; version.json rules.screen_signaling,
 * rules.screen_sessions, rules.pc_key_pinning, rules.screen_content).
 *
 * One session at a time, on the selected PC, in this order:
 *   1. the pin: no PC key pinned at pairing for this PC → SCREEN_PC_UNVERIFIED, nothing is sent ("pair again");
 *   2. `screen_ice_request` on the bound socket → `screen_ice_servers` (ICE servers minted for THIS phone, the Free
 *      allowance, whether a relay server is available) or an `error` frame (SCREEN_VIEW_DISABLED, allowance, …);
 *   3. a receive-only `RTCPeerConnection` from the injected factory (bundlePolicy max-bundle; video + audio
 *      transceivers, the `dome-screen-v1` data channel), `createOffer`, wait for ICE gathering to complete or 3 s
 *      (vanilla ICE: no candidate travels on its own), then `compactOffer` (lib/sdpCompact.ts, DECISIONS 91: the
 *      candidates of the bundled sections and every TCP candidate go, the lowest priorities last, down to
 *      screen_sdp_max_bytes; sizes and counts are logged, never a candidate) and the strict SDP check
 *      (`screenSdpAcceptable`); an offer still too long fails here with detail {origin: phone, reason: too_long, bytes};
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
 * The video track arrives inside `setRemoteDescription`, before its promise resolves (the browsers' order): it is kept
 * aside and shown together with `verified` (device round 2, 2026-10-27: the phone dropped it and never left
 * "Connecting…"). Once DTLS is up a first-frame watchdog asks for keyframes at 3 s and 8 s and starts a fresh session
 * at 15 s; a PC pause (a Windows prompt, a missing bar) holds it, and a picture that only waits for the owner's tap
 * (play() refused outside a gesture) is not restarted: the tap prompt stays and the watchdog counts again from the tap.
 * `getStats()` is read through a whitelist (lib/screenStats.ts) every 5 s in session and the stages reached are kept
 * as a timeline, so a session that shows nothing says where it stopped.
 *
 * The phone never reads pixels: the stream only ever becomes a `<video>.srcObject` (test/no-pixel-access.test.ts).
 * Nothing here logs an SDP, a candidate, an address or a credential (log.ts also redacts those keys). The Free
 * allowance shown is a countdown for display; the PC's `ended{allowance_exhausted}` is authoritative.
 *
 * Protocol 1.5 (design screen-view-2a §4.1):
 *   - the pointer drawn by the phone: a start carries `cursor: "overlay"` only to a PC whose agent announced 1.5
 *     (`pcProtocolVersions`), and only such a start opens the second, unordered data channel `dome-screen-cursor-v1`
 *     (a 1.4 PC's worker sends on the channel announced last). `cursor_pos` goes into one slot drained once per
 *     animation frame by `pointer` (no React state per message); `cursor_shape` is checked by `cursorShape.ts`. A relay
 *     refusal `PROTOCOL_INCOMPATIBLE {required: "1.5"}` marks the PC and starts once more without `cursor`;
 *   - Next screen (`switchSurface`): a fresh signed start on the other monitor while the old session stays up (no
 *     `bye`, no stop, its heartbeat kept so the lease never lapses); the picture swaps on the new session's first frame
 *     and a failed switch restores the old session unless the PC already ended it (a new picture that arrived but
 *     never showed means it did: a fresh session on the new screen). Every handler checks that its channel or peer is
 *     still the current one, so the old peer's late events change nothing (the old session's pause and resume are
 *     kept for a restore);
 *   - the last screen watched per PC is remembered for the app run (restarts and resumes go back to it).
 */
import { LIMITS, parseScreenDcMessage, protocolCompatible, ProtocolError, SCREEN_CURSOR_CHANNEL_LABEL, SCREEN_DATA_CHANNEL_LABEL, screenSdpAcceptable, utf8ByteLength, verifyScreenAnswer, type relayFrames, type results, type ScreenDcPcToPhone } from "@dome/protocol";

import type { CommandRecord, Identity } from "./commands.ts";
import type { PcKeyPin } from "./controllerKey.ts";
import { parseCursorShape } from "./cursorShape.ts";
import { SCREEN_UNSUPPORTED_BROWSER } from "./labels.ts";
import { errorSummary, log } from "./log.ts";
import { PointerModel, type PointerReport } from "./pointer.ts";
import { GeometryTracker, type Geometry } from "./screenMap.ts";
import { compactOffer } from "./sdpCompact.ts";
import { emptyTimeline, mediaSummary, reduceStats, statsSummary, type CodecFallback, type ScreenClientFacts, type ScreenMediaFacts, type ScreenOfferFacts, type ScreenStatsSnapshot, type ScreenTimeline, type StatsReportLike, type TimelineMark } from "./screenStats.ts";

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
  /** DTLS is up in this attempt: "Connected. Waiting for the picture…" until the first frame. */
  transportConnected: boolean;
  /** Frames are decoded but the `<video>` is paused without a frame callback: ask for one tap (play() again). */
  playHint: boolean;
  /** The remote media stream, for `<video>.srcObject` only. */
  stream: MediaStream | null;
  startedAt: number | null;
  /** The last frame as published: at live and whenever `stalled` flips (frames themselves are not published one by one). */
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
  /** The screen shown: `result.surface_index`, then `geometry.surface_index` (1.5 Next screen). */
  surfaceIndex: number | null;
  /** A switch to another screen is in flight; the old picture stays until the new session's first frame. */
  switching: { to: number; from: number | null; startedAt: number } | null;
  /** The last switch landed (drives the short "Screen 2 of 2" chip); also set when a session starts on a non-main screen. */
  switched: { index: number; count: number; at: number } | null;
  /** The remembered screen is not connected any more; the picture went back to the main one (`human` counts from 1). */
  switchedGone: { human: number; at: number } | null;
  /** A switch was refused or failed and the old session was kept (`human`: the screen still shown). */
  switchFailed: { code: string; human: number; at: number } | null;
  /** The new session's stream during a switch, for a second, hidden `<video>` (the visible `stream` is untouched). */
  incomingStream: MediaStream | null;
  /** The pointer mode: `result.cursor` (provisional), then `state.cursor` (authoritative); null = a 1.4 session. */
  cursorMode: "drawn" | "overlay" | null;
  /** This start carried `cursor: "overlay"` (the PC announced 1.5). */
  pointerOverlayAsked: boolean;
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
  transportConnected: false,
  playHint: false,
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
  surfaceIndex: null,
  switching: null,
  switched: null,
  switchedGone: null,
  switchFailed: null,
  incomingStream: null,
  cursorMode: null,
  pointerOverlayAsked: false,
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
  /** `maxRetransmits: 0` with `ordered: false` for the 1.5 cursor channel (partial reliability: a late position is useless). */
  createDataChannel(label: string, init: { ordered: boolean; maxRetransmits?: number }): DataChannelLike;
  createOffer(): Promise<{ type?: string; sdp?: string }>;
  setLocalDescription(description: { type?: string; sdp?: string }): Promise<void>;
  setRemoteDescription(description: { type: "answer"; sdp: string }): Promise<void>;
  /** Optional so a fake may leave it out; read only through the whitelist in lib/screenStats.ts. */
  getStats?(): Promise<StatsReportLike>;
  readonly localDescription: { sdp: string } | null;
  readonly iceGatheringState: "new" | "gathering" | "complete";
  readonly connectionState: string;
  readonly iceConnectionState: string;
  close(): void;
  onicegatheringstatechange: ((ev: unknown) => void) | null;
  onconnectionstatechange: ((ev: unknown) => void) | null;
  oniceconnectionstatechange: ((ev: unknown) => void) | null;
  ontrack: ((ev: TrackEventLike) => void) | null;
}

/** The `track` event as this client reads it (RTCTrackEvent satisfies it). */
export interface TrackEventLike {
  streams: readonly MediaStream[];
  track: MediaStreamTrack | { kind: string };
}

export type PeerFactory = (config: { iceServers: RTCIceServer[]; bundlePolicy: RTCBundlePolicy }) => PeerConnectionLike;

/**
 * Every media section bundles onto the first one's transport, so the browser writes its candidates into the first
 * section only (DECISIONS 91: with the default "balanced" policy Safari repeated every candidate in all three sections
 * and, once TURN servers were handed out, the offer went past screen_sdp_max_bytes). The PC's aiortc answers a
 * max-bundle offer with `a=group:BUNDLE 0 1 2` as before.
 */
export const BUNDLE_POLICY: RTCBundlePolicy = "max-bundle";

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
  /** The protocol versions the PC's agent announced (REST `pc.protocol_versions`; [] when unknown). */
  pcProtocolVersions(pcId: string): readonly string[];
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
/** After DTLS is up with no frame: a keyframe request (data channel) and a `screen.no_picture` line… */
export const NO_PICTURE_FIRST_MS = 3_000;
/** …a second one… */
export const NO_PICTURE_SECOND_MS = 8_000;
/** …then a fresh session, counted toward MAX_RETRIES (a PC build without the encoder pump took up to 9.2 s). */
export const NO_PICTURE_RESTART_MS = 15_000;
/** `getStats()` while in session (cheap; kept for the diagnostics bundle and the readout). */
export const STATS_INTERVAL_MS = 5_000;
/** A consent refusal this soon after the start was answered without a window (the back-off): different copy. */
export const BACKOFF_REFUSAL_MS = 1_500;

const globalTimers: ScreenTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

type IceOutcome = { kind: "servers"; frame: relayFrames.RelayScreenIceServers } | { kind: "error"; error: relayFrames.Error } | { kind: "timeout" } | { kind: "aborted" };

/**
 * Sessions this phone has left whose ids are still remembered: the PC ends a session up to 2 s after the phone's `bye`
 * and the relay forwards that `ended` to every socket of this controller, by when the automatic fresh session is in
 * flight with no id of its own yet (DECISIONS 83). A handful covers any realistic run of restarts.
 */
const RETIRED_SESSIONS_MAX = 8;

/** End reasons after which the phone never restarts by itself (§2.2 step 5, §7). */
const NO_RESUME: ReadonlySet<ScreenEndReason> = new Set(["stopped", "stopped_on_pc", "replaced", "takeover", "controller_revoked", "grant_removed", "plan_disabled", "remote_disabled", "allowance_exhausted"]);

function allowanceFrom(a: relayFrames.ScreenAllowanceState, at: number): ScreenAllowance {
  return { unlimited: a.unlimited, remainingSeconds: a.remaining_seconds, resetsAt: a.resets_at, at };
}

/** The protocol MINOR that added the pointer drawn by the phone (`screen.session_start.cursor`). */
export const CURSOR_PROTOCOL_VERSION = "1.5";

/** What `start()` takes. `switching` makes it a switch of screens: the live session stays up until the new one shows. */
export interface ScreenStartOptions {
  takeover?: boolean;
  retries?: number;
  /** A screen index of the PC, or its main screen. Default: the screen last watched on this PC in this app run. */
  surface?: number | "primary";
  switching?: { from: number | null };
}

/** DoMe's number for a screen (counted from 1): its position among the PC's screens sorted by index, + 1. */
export function humanSurface(surfaces: readonly ScreenSurface[], index: number | null): number {
  if (index === null) return 1;
  const sorted = [...surfaces].sort((a, b) => a.index - b.index);
  const at = sorted.findIndex((s) => s.index === index);
  return at >= 0 ? at + 1 : index + 1;
}

/** The live session kept aside during a switch (its peer, channels and heartbeat stay up until the new one shows). */
interface PreviousSession {
  pc: PeerConnectionLike | null;
  dc: DataChannelLike | null;
  cdc: DataChannelLike | null;
  sessionId: string | null;
  surfaceIndex: number | null;
  phase: ScreenPhase;
  stream: MediaStream | null;
  hbTimer: unknown;
  /** The PC's `ended` for this id was seen (it replaced the session): nothing to restore any more. */
  endedByPc: boolean;
  /** What a failed switch restores: the state and the per-session facts of the old session. */
  state: ScreenSessionState;
  geometry: Geometry | null;
  lastFrame: number | null;
  pauseCauses: Set<"secure_desktop" | "indicator">;
  timeline: ScreenTimeline;
  answerAt: number | null;
  dtlsAt: number | null;
  hbSent: number;
  dcReceived: number;
  dcDropped: number;
  lastStats: ScreenStatsSnapshot | null;
  offer: ScreenOfferFacts | null;
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
  private statsTimer: unknown = null;
  /** The first-frame watchdog: 1 s ticks from DTLS up until the first frame (NO_PICTURE_*). */
  private pictureTimer: unknown = null;
  /** Set only after `verifyScreenAnswer` passed, just before `setRemoteDescription`: the structural guard for tracks. */
  private answerApplied = false;
  /** The video stream that arrived inside setRemoteDescription, shown together with `verified` (never before). */
  private pendingStream: MediaStream | null = null;
  private dcDropped = 0;
  private hbSent = 0;
  private dcReceived = 0;
  /** When the answer verified, DTLS came up and the last frame was seen (wall-clock ms; frames are not published per frame). */
  private answerAt: number | null = null;
  private dtlsAt: number | null = null;
  private lastFrame: number | null = null;
  /** How many first-frame watchdog steps ran (1: +3 s, 2: +8 s, 3: +15 s restart). */
  private noPictureStep = 0;
  /** What the watchdog counts from: DTLS up, moved on by the time it was held, reset by the owner's tap. */
  private pictureFrom: number | null = null;
  /** The watchdog is held (a PC pause, or a picture waiting for the owner's tap) since this time. */
  private heldSince: number | null = null;
  /** The page reported `play()` refused (Low Power Mode, outside a gesture) and not played since. */
  private playBlocked = false;
  /** The +15 s step found a picture that only needs the owner's tap: no restart until the tap (a new `<video>`'s
   *  play() would be refused just the same). */
  private waitingForTap = false;
  private timeline: ScreenTimeline = emptyTimeline();
  private lastStats: ScreenStatsSnapshot | null = null;
  /** The offer as signed: sizes and counts after compaction (never the SDP; lib/sdpCompact.ts). */
  private offerFacts: ScreenOfferFacts | null = null;
  private lastMedia: ScreenMediaFacts | null = null;
  private mediaProbe: (() => ScreenMediaFacts) | null = null;
  /** The next automatic retry offers VP8 only (H.264 arrived but never decoded); a start by the owner clears it. */
  private codecFallback: CodecFallback | null = null;
  /** The PC's active pause causes (relay frames come per cause; capture resumes only when none is left). */
  private pauseCauses = new Set<"secure_desktop" | "indicator">();
  /** Ids of sessions this phone stopped, saw end or failed, oldest first (at most RETIRED_SESSIONS_MAX; DECISIONS 83). */
  private retiredSessionIds = new Set<string>();
  /** The 1.5 cursor channel (`dome-screen-cursor-v1`, unordered, no retransmits); only in a start that carried `cursor`. */
  private cdc: DataChannelLike | null = null;
  /** The PC refused `cursor` (PROTOCOL_INCOMPATIBLE required 1.5) for this PC in this app run… */
  private noCursorFor: string | null = null;
  /** …while its announced versions read like this (a changed list that includes 1.5 clears the mark). */
  private noCursorVersions: string | null = null;
  /** The live session kept aside while a switch is in flight. */
  private previous: PreviousSession | null = null;
  /** The screen watched last per PC, for the app run only (never persisted). */
  private readonly lastSurfaceByPc = new Map<string, number>();
  /** The surface the current start asked for. */
  private startSurface: number | "primary" = "primary";
  /** One slot: the latest accepted `cursor_pos`, drained once per animation frame by `pointer.sample()`. */
  private latestCursorPos: PointerReport | null = null;
  /** Set by an automatic retry of a refused start: what the next `start()` carries over. */
  private retryCarry: { gone: { human: number; at: number } | null } | null = null;
  private pointerFacts = { shapes: 0, custom: 0, rejected: 0, positions: 0, unreliable: 0, droppedGen: 0, lastKind: null as string | null };
  private surfaceFacts = { switches: 0, switchFailures: 0, gone: 0 };
  private zoomFacts = { maxScale: 1, followPans: 0 };
  /** The pointer drawn by the phone (lib/pointer.ts): fed here and by the page's gestures, read once per frame. */
  readonly pointer: PointerModel;

  constructor(deps: ScreenClientDeps) {
    this.deps = deps;
    this.timers = deps.timers ?? globalTimers;
    this.pointer = new PointerModel({ pending: () => this.takeLatestCursorPos(), surface: () => this.surfaceSize() });
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
  async start(pcId: string, options: ScreenStartOptions = {}): Promise<ScreenSessionState> {
    const carry = this.retryCarry;
    this.retryCarry = null;
    const switching = options.switching && this.state.pcId === pcId ? options.switching : undefined;
    if (switching) {
      // a switch: the live session stays up (no bye, no stop) until the new one shows its first frame
      if (this.previous === null) this.setPreviousAside();
      else this.closePeer(); // a retry within the switch: only the refused attempt's peer goes
    } else if (this.state.phase !== "idle" && this.state.phase !== "ended" && this.state.phase !== "failed") {
      await this.stop(this.state.pcId === pcId ? "restart" : "pc_switch");
    }
    const nonce = ++this.nonce;
    const surface = options.surface ?? this.lastSurfaceByPc.get(pcId) ?? "primary";
    this.startSurface = surface;
    const now = this.now();
    const samePc = this.state.pcId === pcId;
    const kept: Partial<ScreenSessionState> = { allowance: samePc ? this.state.allowance : null, otherViewer: samePc ? this.state.otherViewer : false };
    if (switching) {
      const to = surface === "primary" ? (this.state.surfaces.find((s) => s.primary)?.index ?? 0) : surface;
      // the viewing goes on: how long it was live (Stop), the consent the PC asked for (a hidden page offers Resume
      // instead of starting by itself) and a relay socket that is down (clicks paused) stay until the new session says
      Object.assign(kept, { surfaces: this.state.surfaces, surfaceIndex: this.state.surfaceIndex, stream: this.state.stream, startedAt: this.state.startedAt, consent: this.state.consent, relayDown: this.state.relayDown, switching: { to, from: switching.from, startedAt: this.state.switching?.startedAt ?? now } });
    }
    if (carry?.gone) kept.switchedGone = carry.gone;
    this.clearTimers();
    this.geometry.reset();
    this.offerSdp = null;
    this.answerApplied = false;
    this.pendingStream = null;
    this.latestCursorPos = null;
    this.dcDropped = 0;
    this.pauseCauses.clear();
    this.resetFacts();
    // the owner's own start (not a switch, not an automatic retry) starts the run's counts again
    if (!switching && !options.retries && !carry) this.resetRunFacts();
    // the VP8 retry applies to the automatic fresh session only; the owner's own start offers H.264 first again
    if (!options.retries) this.codecFallback = null;
    // 1.5: the pointer drawn by the phone only for a PC whose agent announced 1.5 (and did not refuse it in this run)
    const versions = this.deps.pcProtocolVersions(pcId);
    if (this.noCursorFor === pcId && versions.join(",") !== this.noCursorVersions) {
      if (protocolCompatible(CURSOR_PROTOCOL_VERSION, versions)) this.noCursorFor = this.noCursorVersions = null;
      else this.noCursorVersions = versions.join(",");
    }
    const overlay = protocolCompatible(CURSOR_PROTOCOL_VERSION, versions) && this.noCursorFor !== pcId;
    this.set({ ...EMPTY_SCREEN_STATE, ...kept, phase: "ice", pcId, retries: options.retries ?? 0, pointerOverlayAsked: overlay });
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
      // the second channel only in a start that carries `cursor`: a 1.4 PC's worker sends on the channel announced last
      offerSdp = await this.buildOffer(servers.ice_servers as RTCIceServer[], overlay);
    } catch (e) {
      log.warn("screen.offer_failed", errorSummary(e));
      if (nonce !== this.nonce) return this.state;
      return this.fail("SCREEN_OFFER_INVALID");
    }
    if (nonce !== this.nonce) return this.state;
    // the compacted offer is still above the PC's limit (nothing more may go): fail here, plainly, before any signing
    const offerBytes = utf8ByteLength(offerSdp);
    if (offerBytes > LIMITS.screen_sdp_max_bytes) {
      log.warn("screen.offer_too_long", { bytes: offerBytes, limit: LIMITS.screen_sdp_max_bytes });
      return this.fail("SCREEN_OFFER_INVALID", undefined, { origin: "phone", reason: "too_long", bytes: offerBytes });
    }
    if (!screenSdpAcceptable(offerSdp)) return this.fail("SCREEN_OFFER_INVALID", undefined, { origin: "phone", reason: "shape" });
    this.offerSdp = offerSdp;
    // 4. the signed start command, with the lifetime a consent wait needs
    let record: CommandRecord;
    // the shared builder stamps 1.5 exactly when `cursor` is present (no default fills it in), else 1.4
    const params: Record<string, string | number | boolean> = { offer_sdp: offerSdp, surface, audio: false, takeover: options.takeover === true, ...(overlay ? { cursor: "overlay" } : {}) };
    try {
      record = await this.deps.sendCommand({ pcId, action: "screen.session_start", params, lifetimeSeconds: LIMITS.screen_start_command_lifetime_seconds, source: "system" });
    } catch (e) {
      log.warn("screen.start_send_failed", errorSummary(e));
      if (nonce !== this.nonce) return this.state;
      return this.fail(e instanceof ProtocolError ? e.code : "INTERNAL");
    }
    if (nonce !== this.nonce) return this.state;
    this.set({ phase: "starting", commandId: record.commandId, sentAt: this.now() });
    log.info("screen.start_sent", { pc_id: pcId, command_id: record.commandId, takeover: options.takeover === true, turn: servers.turn, surface, cursor: overlay ? "overlay" : null, switching: switching !== undefined });
    return this.state;
  }

  /**
   * A switch starts: the live session's peer, channels and heartbeat are kept aside (handlers detached so its late
   * events change nothing, the heartbeat still renewing the PC's lease on the old channel), and its id is NOT retired
   * yet: its `ended` frame must still be recognised.
   */
  private setPreviousAside(): void {
    this.previous = {
      pc: this.pc,
      dc: this.dc,
      cdc: this.cdc,
      sessionId: this.state.sessionId,
      surfaceIndex: this.state.surfaceIndex,
      phase: this.state.phase,
      stream: this.state.stream,
      hbTimer: this.hbTimer,
      endedByPc: false,
      state: this.state,
      geometry: this.geometry.known,
      lastFrame: this.lastFrame,
      pauseCauses: new Set(this.pauseCauses),
      timeline: { ...this.timeline },
      answerAt: this.answerAt,
      dtlsAt: this.dtlsAt,
      hbSent: this.hbSent,
      dcReceived: this.dcReceived,
      dcDropped: this.dcDropped,
      lastStats: this.lastStats,
      offer: this.offerFacts,
    };
    detach(this.pc, this.dc, this.cdc);
    this.pc = this.dc = this.cdc = null;
    this.hbTimer = null;
  }

  /** Next screen: a fresh signed start on screen `index` while this session stays up (no-op unless live and idle). */
  async switchSurface(index: number): Promise<ScreenSessionState> {
    const s = this.state;
    if (!(s.phase === "live" || s.phase === "paused") || s.switching !== null || index === s.surfaceIndex || !s.surfaces.some((x) => x.index === index) || !s.pcId) return s;
    log.info("screen.switch_started", { to: index, from: s.surfaceIndex });
    return this.start(s.pcId, { surface: index, retries: s.retries, switching: { from: s.surfaceIndex } });
  }

  /** The screen after the one shown, in index order, wrapping; null with fewer than two screens. */
  nextSurfaceIndex(): number | null {
    const sorted = [...this.state.surfaces].sort((a, b) => a.index - b.index);
    if (sorted.length < 2) return null;
    const at = sorted.findIndex((x) => x.index === this.state.surfaceIndex);
    return sorted[(at + 1) % sorted.length]!.index;
  }

  /** The shown screen's PHYSICAL size (`screen_surface`), never the encode size of the picture; null before a result. */
  surfaceSize(): { width: number; height: number } | null {
    const s = this.state.surfaces.find((x) => x.index === this.state.surfaceIndex);
    return s ? { width: s.width, height: s.height } : null;
  }

  /** The latest accepted `cursor_pos`, once (the slot is emptied): `pointer.sample()` drains it every frame. */
  takeLatestCursorPos(): PointerReport | null {
    const r = this.latestCursorPos;
    this.latestCursorPos = null;
    return r;
  }

  /** The page zoomed the picture to `scale` (readout `zoom: max`). */
  noteZoom(scale: number): void {
    if (Number.isFinite(scale) && scale > this.zoomFacts.maxScale) this.zoomFacts.maxScale = scale;
  }

  /** The zoomed Touchpad view panned to keep the pointer in sight (readout `follow pans`). */
  noteFollowPan(): void {
    this.zoomFacts.followPans += 1;
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

  /**
   * The receive-only offer (§4.2 step 2). The peer connection and data channels stay in this client. With
   * `cursorChannel` (the same flag that puts `cursor: "overlay"` into the params) the 1.5 cursor channel is opened too,
   * unordered and without retransmits, after the main one in the same offer (no renegotiation). What comes back is the
   * COMPACTED offer (DECISIONS 91): the full description stays on this peer, the signed start carries the compact
   * string and the PC's answer names that string's digest.
   */
  private async buildOffer(iceServers: RTCIceServer[], cursorChannel: boolean): Promise<string> {
    const factory = this.deps.peerFactory;
    if (!factory) throw new ProtocolError(SCREEN_UNSUPPORTED_BROWSER, "no WebRTC");
    const pc = factory({ iceServers, bundlePolicy: BUNDLE_POLICY });
    this.pc = pc;
    const video = pc.addTransceiver("video", { direction: "recvonly" });
    // audio always, even in Phase 1 without sound: adding it later would pause an unmuted video on iOS (§2.2)
    const audio = pc.addTransceiver("audio", { direction: "recvonly" });
    if (this.codecFallback) preferVp8Only(video);
    else preferCodecs(video, "video");
    preferCodecs(audio, "audio");
    const dc = pc.createDataChannel(SCREEN_DATA_CHANNEL_LABEL, { ordered: true });
    this.dc = dc;
    const cdc = cursorChannel ? pc.createDataChannel(SCREEN_CURSOR_CHANNEL_LABEL, { ordered: false, maxRetransmits: 0 }) : null;
    this.cdc = cdc;
    this.attach(pc, dc, cdc);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await this.waitForGathering(pc);
    const gathered = pc.localDescription?.sdp ?? offer.sdp;
    if (typeof gathered !== "string") throw new ProtocolError("SCREEN_OFFER_INVALID", "no local description");
    const compact = compactOffer(gathered, LIMITS.screen_sdp_max_bytes);
    this.offerFacts = { bytes: compact.bytesAfter, candidates: compact.candidatesAfter, dropped: compact.droppedBundled + compact.droppedTcp + compact.droppedLow };
    // sizes and counts only: never a candidate, an address or a credential (rules.screen_content)
    log.info("screen.offer_built", { bytes: compact.bytesAfter, bytes_gathered: compact.bytesBefore, kept: compact.candidatesAfter, gathered: compact.candidatesBefore, dropped_bundled: compact.droppedBundled, dropped_tcp: compact.droppedTcp, dropped_low: compact.droppedLow, limit: LIMITS.screen_sdp_max_bytes });
    return compact.sdp;
  }

  /** Every handler checks that its peer or channel is still the current one: an old object's late event changes nothing. */
  private attach(pc: PeerConnectionLike, dc: DataChannelLike, cdc: DataChannelLike | null): void {
    dc.onopen = () => {
      if (dc === this.dc) this.onChannelOpen(dc);
    };
    dc.onmessage = (ev) => this.onChannelMessage(dc, ev.data);
    dc.onclose = () => {
      if (dc === this.dc) this.stopHeartbeat();
    };
    if (cdc) cdc.onmessage = (ev) => this.onCursorChannelMessage(cdc, ev.data);
    pc.ontrack = (ev) => this.onTrack(pc, ev);
    pc.onconnectionstatechange = () => this.onPeerState(pc);
    pc.oniceconnectionstatechange = () => this.onPeerState(pc);
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
      const detail = t.error?.detail as Record<string, unknown> | undefined;
      if (this.retryRefusedStart(code, t.error?.message, detail)) return;
      this.fail(code, t.error?.message, detail, t.origin);
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

  /**
   * The two automatic retries of a refused start (each happens at most once by construction):
   *   - PROTOCOL_INCOMPATIBLE `required: "1.5"` on a start that carried `cursor` (the PC's announced versions were
   *     stale): the PC is marked for this app run and the start goes again without `cursor` (same screen, same retry
   *     count, a switch stays a switch; not counted as a retry);
   *   - INVALID_PARAMETERS on a start for a numbered screen that carried no `cursor` (or whose message names the
   *     surface): that screen is not connected any more, so the start goes again on the main screen. A pre-1.5 agent
   *     answers INVALID_PARAMETERS for an unknown param too, so a start with `cursor` is never retried as a screen problem.
   */
  private retryRefusedStart(code: string, message: string | undefined, detail: Record<string, unknown> | undefined): boolean {
    const pcId = this.state.pcId;
    if (!pcId) return false;
    const switching = this.state.switching ? { from: this.state.switching.from } : undefined;
    const retries = this.state.retries;
    if (code === "PROTOCOL_INCOMPATIBLE" && detail?.required === CURSOR_PROTOCOL_VERSION && this.state.pointerOverlayAsked) {
      this.noCursorFor = pcId;
      this.noCursorVersions = this.deps.pcProtocolVersions(pcId).join(",");
      log.info("screen.cursor_param_refused", { pc_id: pcId });
      this.retryWith(pcId, { surface: this.startSurface, retries, ...(switching ? { switching } : {}) }, null);
      return true;
    }
    if (code === "INVALID_PARAMETERS" && this.startSurface !== "primary" && (!this.state.pointerOverlayAsked || /surface/i.test(message ?? ""))) {
      const gone = { human: humanSurface(this.state.surfaces, this.startSurface), at: this.now() };
      this.lastSurfaceByPc.delete(pcId);
      this.surfaceFacts.gone += 1;
      log.info("screen.surface_gone", { pc_id: pcId, surface: this.startSurface });
      this.retryWith(pcId, { surface: "primary", retries, ...(switching ? { switching } : {}) }, gone);
      return true;
    }
    return false;
  }

  private retryWith(pcId: string, options: ScreenStartOptions, gone: { human: number; at: number } | null): void {
    this.retryCarry = { gone };
    void this.start(pcId, options);
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
    const cursorMode = r.cursor ?? null;
    if (this.state.pcId) this.lastSurfaceByPc.set(this.state.pcId, r.surface_index);
    this.pointer.reset();
    this.pointer.setMode(cursorMode);
    // a switch: the new session is real, so late frames of the old one are dropped from now on (its `ended` is still
    // matched by id in onSessionFrame before the retired check)
    if (this.previous) this.retire(this.previous.sessionId);
    this.set({
      sessionId: r.screen_session_id,
      surfaces: [...r.surfaces],
      surfaceIndex: r.surface_index,
      cursorMode,
      geometry: this.geometry.known,
      consent: r.consent,
      consentWaiting: false,
      consentDeadlineAt: null,
      allowance: allowanceFrom(r.allowance, this.now()),
      encoder: r.encoder ?? this.state.encoder,
    });
    log.info("screen.session_result", { pc_id: this.state.pcId, consent: r.consent, encoder: r.encoder ?? null, surfaces: r.surfaces.length, surface_index: r.surface_index, cursor: cursorMode });
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
    this.answerAt = this.now();
    this.mark("answer_verified");
    // from here on a track is ours: the browser fires `track` inside setRemoteDescription, before it resolves
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
    // the stream becomes visible only together with `verified` (nothing is shown before verification); during a
    // switch it goes to the hidden second <video> and the old picture stays where it is
    const media = this.state.switching ? { incomingStream: this.pendingStream ?? this.state.incomingStream } : { stream: this.pendingStream ?? this.state.stream };
    this.set({ verified: true, phase: "connecting", sessionId: this.state.sessionId ?? verified.payload.screen_session_id, consentWaiting: false, consentDeadlineAt: null, ...media });
    this.pendingStream = null;
    log.info("screen.answer_verified", { pc_id: this.state.pcId, stream: this.state.stream !== null });
    this.armConnectTimers();
    this.startStatsLoop();
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

  /**
   * The browsers fire `track` inside setRemoteDescription, before its promise resolves (webrtc-pc; WebKit
   * PeerConnectionBackend::setRemoteDescriptionSucceeded), so `verified` is still false here. `answerApplied` is the
   * guard: it is set only after the signed answer verified, so a track can only come from the verified answer.
   */
  private onTrack(pc: PeerConnectionLike, ev: TrackEventLike): void {
    if (pc !== this.pc) return; // an old peer's late track
    if (ev.track.kind !== "video") return;
    log.info("screen.track", { streams: ev.streams.length, answer_applied: this.answerApplied, verified: this.state.verified });
    if (!this.answerApplied) {
      log.warn("screen.track_before_verification_dropped");
      return;
    }
    this.mark("track");
    const media = ev.streams[0] ?? streamOf(ev.track);
    if (!media) {
      log.warn("screen.track_without_stream");
      return;
    }
    if (!this.state.verified) this.pendingStream = media;
    else if (this.state.switching) this.set({ incomingStream: media }); // the visible `stream` stays the old picture
    else this.set({ stream: media });
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
      if (this.dtlsAt === null) {
        this.dtlsAt = this.now();
        this.pictureFrom = this.dtlsAt;
        this.mark("dtls_connected");
        log.info("screen.dtls_connected", { ms_since_answer: this.since(this.answerAt) });
        this.startPictureWatchdog();
      }
      this.set({ tryingRelay: false, transportConnected: true });
    }
  }

  // ---- the first-frame watchdog (nothing else ends "connecting" once DTLS is up) ----------------------------------

  private startPictureWatchdog(): void {
    if (this.pictureTimer !== null) this.timers.clearInterval(this.pictureTimer);
    this.noPictureStep = 0;
    this.pictureTimer = this.timers.setInterval(() => this.checkPicture(), 1000);
  }

  private stopPictureWatchdog(): void {
    if (this.pictureTimer !== null) this.timers.clearInterval(this.pictureTimer);
    this.pictureTimer = null;
  }

  private checkPicture(): void {
    if (this.state.phase !== "connecting" || this.pictureFrom === null) {
      this.stopPictureWatchdog();
      return;
    }
    const now = this.now();
    // a PC pause (no frame is due; the PC refuses a new session while its secure desktop is up) or a picture waiting
    // for the owner's tap: no keyframe request, no restart, and the held time does not count
    const held = this.pictureHeld();
    if (held) {
      if (this.heldSince === null) {
        this.heldSince = now;
        log.info("screen.no_picture_held", { reason: held, after_ms: now - this.pictureFrom });
      }
      void this.readStats();
      return;
    }
    if (this.heldSince !== null) {
      // the clock goes on from where the hold stopped it, with at least the second step's room before a restart
      this.pictureFrom = Math.max(this.pictureFrom + (now - this.heldSince), now - NO_PICTURE_SECOND_MS);
      this.heldSince = null;
    }
    const after = now - this.pictureFrom;
    const step = after >= NO_PICTURE_RESTART_MS ? 3 : after >= NO_PICTURE_SECOND_MS ? 2 : after >= NO_PICTURE_FIRST_MS ? 1 : 0;
    if (step > this.noPictureStep) {
      this.noPictureStep = step;
      void this.noPicture(step as 1 | 2 | 3, after);
      return;
    }
    // every second while waiting: the first RTP packet and the first decoded frame land on the timeline
    void this.readStats();
  }

  /** Why the first-frame watchdog is held, or null: the PC paused, or the picture waits for the owner's tap. */
  private pictureHeld(): "pc_paused" | "tap" | null {
    if (this.state.pcState === "paused") return "pc_paused";
    return this.waitingForTap ? "tap" : null;
  }

  /** +3 s and +8 s: a keyframe request and one log line; +15 s: one more line and a fresh session. */
  private async noPicture(step: 1 | 2 | 3, afterMs: number): Promise<void> {
    const nonce = this.nonce;
    const keyframe = step < 3 ? this.sendChannel({ type: "keyframe" }) : false;
    const stats = await this.readStats();
    // a first frame, a stop or an end while getStats() ran: nothing more to do for this step
    if (nonce !== this.nonce || this.state.phase !== "connecting") return;
    const media = this.probeMedia();
    log.info("screen.no_picture", { after_ms: afterMs, step, keyframe_sent: keyframe, stream: this.state.stream !== null, ...statsSummary(stats), ...mediaSummary(media) });
    const decoded = stats?.inbound?.frames_decoded ?? 0;
    // frames decode but the <video> never ran a frame callback and sits paused: one tap plays it (Low Power Mode)
    const tapPlays = decoded > 0 && media !== null && media.paused === true && media.frame_ticks === 0;
    if (step < 3) {
      if (tapPlays && !this.state.playHint) this.set({ playHint: true });
      return;
    }
    if (this.state.pcState === "paused") {
      this.noPictureStep = 2; // the PC paused while the stats were read: held, and this step comes again after it
      return;
    }
    if (this.playBlocked || tapPlays || this.state.playHint) {
      // only the owner's tap can play it: a fresh session's <video> would be refused outside a gesture as well
      this.waitingForTap = true;
      this.noPictureStep = 2;
      if (!this.state.playHint) this.set({ playHint: true });
      return;
    }
    const received = stats?.inbound?.packets_received ?? 0;
    if (!this.codecFallback && received > 0 && decoded === 0 && /h264/i.test(stats?.codec?.mime ?? "")) {
      // H.264 arrives but never decodes on this phone: the fresh session offers VP8 only (the PC answers VP8)
      this.codecFallback = { from: "h264", to: "vp8", reason: "not_decoded" };
      log.info("screen.codec_fallback", { ...this.codecFallback });
    }
    if (this.state.switching !== null) {
      const previous = this.previous;
      if (previous !== null && !previous.endedByPc && this.state.incomingStream === null) {
        // the new session delivered no picture at all and the PC still runs the old one: the switch failed, the old
        // session is restored
        this.fail("SCREEN_CONNECTION_FAILED", undefined, { cause: "no_picture" });
        return;
      }
      // the new picture arrived but never showed (H.264 this phone does not decode, say): the PC replaced the old
      // session when the new one started, so nothing is left to restore. A fresh session on the new screen, with the
      // VP8 retry recorded above, as after any start whose picture never came
      log.info("screen.switch_restarted", { cause: "no_picture", codec_fallback: this.codecFallback?.to ?? null });
      this.closePrevious(false);
      this.set({ switching: null });
    }
    void this.restartFresh("no_picture");
  }

  // ---- stats and facts (metadata only; lib/screenStats.ts) ---------------------------------------------------------

  private startStatsLoop(): void {
    if (this.statsTimer !== null) this.timers.clearInterval(this.statsTimer);
    this.statsTimer = this.timers.setInterval(() => {
      if (this.state.phase === "connecting" || this.state.phase === "live" || this.state.phase === "paused") void this.readStats();
    }, STATS_INTERVAL_MS);
  }

  /** `getStats()` through the whitelist; the snapshot is kept for the readout and the diagnostics bundle. */
  async readStats(): Promise<ScreenStatsSnapshot | null> {
    const pc = this.pc;
    if (!pc || typeof pc.getStats !== "function") return null;
    let report: StatsReportLike;
    try {
      report = await pc.getStats();
    } catch (e) {
      log.info("screen.stats_failed", errorSummary(e));
      return null;
    }
    if (this.pc !== pc) return null;
    const snapshot = reduceStats(report, this.now());
    this.lastStats = snapshot;
    if ((snapshot.inbound?.packets_received ?? 0) > 0) this.mark("first_rtp");
    if ((snapshot.inbound?.frames_decoded ?? 0) > 0) this.mark("first_decoded");
    return snapshot;
  }

  /** The `<video>` registers what it can say about itself (metadata only); null when it goes away. */
  setMediaProbe(probe: (() => ScreenMediaFacts) | null): void {
    this.mediaProbe = probe;
  }

  /** The `<video>` facts now, or the last ones read while it existed. */
  probeMedia(): ScreenMediaFacts | null {
    if (this.mediaProbe) {
      try {
        this.lastMedia = this.mediaProbe();
      } catch {
        /* keep the last facts */
      }
    }
    return this.lastMedia;
  }

  /** Everything the readout and the diagnostics bundle show, without reading the stats again. */
  get facts(): ScreenClientFacts {
    return {
      stats: this.lastStats,
      media: this.probeMedia(),
      timeline: { ...this.timeline },
      channel: { open: this.dc?.readyState === "open", hb_sent: this.hbSent, received: this.dcReceived, dropped: this.dcDropped },
      transport_connected: this.timeline.dtls_connected !== null,
      codec_fallback: this.codecFallback ? { ...this.codecFallback } : null,
      offer: this.offerFacts ? { ...this.offerFacts } : null,
      pointer: { mode: this.state.cursorMode, ...this.pointerFacts, predErrMaxPx: this.pointer.predErrMaxPx },
      surfaces: { count: this.state.surfaces.length, showing: this.state.surfaces.length > 0 && this.state.surfaceIndex !== null ? humanSurface(this.state.surfaces, this.state.surfaceIndex) : null, ...this.surfaceFacts },
      zoom: { maxScale: Math.round(this.zoomFacts.maxScale * 10) / 10, followPans: this.zoomFacts.followPans },
    };
  }

  /** The readout's refresh: fresh stats, then the facts. */
  async readFacts(): Promise<ScreenClientFacts> {
    await this.readStats();
    return this.facts;
  }

  private mark(name: TimelineMark): void {
    const base = this.state.sentAt;
    if (base === null || this.timeline[name] !== null) return;
    this.timeline[name] = Math.max(0, this.now() - base);
  }

  private since(at: number | null): number | null {
    return at === null ? null : Math.max(0, this.now() - at);
  }

  private resetFacts(): void {
    this.timeline = emptyTimeline();
    this.lastStats = null;
    this.offerFacts = null;
    this.lastMedia = null;
    this.answerAt = null;
    this.dtlsAt = null;
    this.lastFrame = null;
    this.noPictureStep = 0;
    this.pictureFrom = null;
    this.heldSince = null;
    this.playBlocked = false;
    this.waitingForTap = false;
    this.hbSent = 0;
    this.dcReceived = 0;
    this.pointerFacts = { shapes: 0, custom: 0, rejected: 0, positions: 0, unreliable: 0, droppedGen: 0, lastKind: null };
  }

  /** The counts that span a whole viewing (switches, zoom): reset by the owner's own start only. */
  private resetRunFacts(): void {
    this.surfaceFacts = { switches: 0, switchFailures: 0, gone: 0 };
    this.zoomFacts = { maxScale: 1, followPans: 0 };
  }

  /** The `<video>` decoded a frame (requestVideoFrameCallback; metadata only, never pixels). */
  onVideoFrame(nowMs: number = this.now()): void {
    if (this.state.phase === "connecting" && this.state.verified) {
      // during a switch only the new session's (hidden) <video> counts; it has a stream only once the new track came
      if (this.state.switching !== null && this.state.incomingStream === null) return;
      this.lastFrame = nowMs;
      this.stopPictureWatchdog();
      this.mark("first_frame");
      this.mark("live");
      log.info("screen.first_frame", { ms_since_dtls: this.since(this.dtlsAt), ms_since_answer: this.since(this.answerAt) });
      const s = this.state;
      const onOtherScreen = s.surfaces.find((x) => x.index === s.surfaceIndex)?.primary === false;
      const switched = s.switching !== null || onOtherScreen ? { switched: { index: s.surfaceIndex ?? 0, count: s.surfaces.length, at: nowMs } } : {};
      if (s.switching !== null) {
        // the new picture is there: the roles of the two <video> elements swap, and the old session goes
        this.surfaceFacts.switches += 1;
        log.info("screen.switched", { to: s.surfaceIndex, from: s.switching.from, ms: Math.max(0, nowMs - s.switching.startedAt) });
        this.closePrevious(false);
        this.set({ phase: "live", startedAt: nowMs, lastFrameAt: nowMs, stalled: false, playHint: false, stream: s.incomingStream, incomingStream: null, switching: null, ...switched });
      } else {
        this.set({ phase: "live", startedAt: nowMs, lastFrameAt: nowMs, stalled: false, playHint: false, ...switched });
      }
      log.info("screen.live", { pc_id: this.state.pcId, retries: this.state.retries });
      this.startStallWatchdog();
      return;
    }
    if (this.state.phase !== "live" && this.state.phase !== "paused") return;
    // ~30 frames a second: the store (and the page) hears about it only when `stalled` flips
    this.lastFrame = nowMs;
    if (this.state.stalled) this.set({ lastFrameAt: nowMs, stalled: false });
  }

  /**
   * The owner tapped "Tap to show the picture" (the page plays again inside the tap). While no frame has come the
   * first-frame watchdog counts again from this tap, and its +3 s step may ask once more if the picture still sits paused.
   */
  dismissPlayHint(): void {
    if (this.state.playHint) this.set({ playHint: false });
    if (this.state.phase === "connecting" && this.pictureFrom !== null) {
      this.waitingForTap = false;
      this.heldSince = null;
      this.pictureFrom = this.now();
      this.noPictureStep = 0;
    }
  }

  /** The page's `<video>` reports its `play()` outcome: refused (true) until a play resolves (false). */
  notePlayBlocked(blocked: boolean): void {
    this.playBlocked = blocked;
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
    if (this.state.phase !== "live" || this.state.pcState !== "sending" || this.lastFrame === null) return;
    const silent = this.now() - this.lastFrame;
    if (silent >= STALL_RESTART_MS) {
      log.info("screen.stall_restart", { retries: this.state.retries });
      void this.restartFresh("stall");
      return;
    }
    if (silent >= STALL_MS && !this.state.stalled) {
      this.set({ stalled: true, lastFrameAt: this.lastFrame });
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

  /** A fresh signed session after a stall, a lost transport or no first frame, at most MAX_RETRIES times; then the failure state. */
  private async restartFresh(cause: "stall" | "transport" | "no_picture"): Promise<void> {
    const pcId = this.state.pcId;
    const retries = this.state.retries;
    if (!pcId) return;
    if (this.state.switching !== null) {
      // a switch in flight is never restarted underneath; its own failure restores the old session
      log.info("screen.restart_skipped", { cause, switching: true });
      return;
    }
    if (retries >= MAX_RETRIES) {
      log.info("screen.retries_exhausted", { cause });
      await this.stop("restart");
      this.set({ phase: "failed", problem: { code: "SCREEN_CONNECTION_FAILED", detail: { cause } }, resume: "none" });
      return;
    }
    const surface = this.state.surfaceIndex ?? undefined;
    await this.stop("restart");
    await this.start(pcId, { retries: retries + 1, ...(surface !== undefined ? { surface } : {}) });
  }

  // ---- the data channel -------------------------------------------------------------------------------

  private onChannelOpen(dc: DataChannelLike): void {
    this.mark("dc_open");
    log.info("screen.dc_open", { ms_since_answer: this.since(this.answerAt) });
    this.sendHeartbeat(dc);
    this.stopHeartbeat();
    // bound to THIS channel: during a switch the old session's heartbeat goes on renewing its lease on its own channel
    this.hbTimer = this.timers.setInterval(() => this.sendHeartbeat(dc), HEARTBEAT_MS);
  }

  private sendHeartbeat(dc: DataChannelLike): void {
    if (dc.readyState !== "open") return;
    try {
      dc.send(JSON.stringify({ type: "hb" }));
      this.hbSent += 1;
    } catch (e) {
      log.info("screen.dc_send_failed", errorSummary(e));
    }
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
      if (message.type === "hb") this.hbSent += 1;
      return true;
    } catch (e) {
      log.info("screen.dc_send_failed", errorSummary(e));
      return false;
    }
  }

  /** Parse one inbound message for the receiving side, or null (dropped and counted; never shown, logged or run). */
  private parseInbound(data: unknown): ScreenDcPcToPhone | null {
    this.dcReceived += 1;
    if (typeof data !== "string") {
      this.dcDropped += 1;
      return null;
    }
    try {
      return parseScreenDcMessage(data, "pc_to_phone");
    } catch (e) {
      this.dcDropped += 1;
      if (this.dcDropped <= 3) log.info("screen.dc_message_dropped", { ...errorSummary(e), dropped: this.dcDropped });
      return null;
    }
  }

  /**
   * rules.screen_content: the closed inbound vocabulary on `dome-screen-v1`; anything else is dropped (counted, never
   * shown or run). A message of a channel that is not the current one (the old session's, during a switch) is ignored,
   * and a handler that throws drops the message (its error name is logged, never the message).
   */
  private onChannelMessage(dc: DataChannelLike, data: unknown): void {
    if (dc !== this.dc) return;
    const msg = this.parseInbound(data);
    if (!msg) return;
    try {
      this.applyMessage(msg);
    } catch (e) {
      this.dcDropped += 1;
      log.info("screen.dc_handler_failed", { name: e instanceof Error ? e.name : "Error", type: msg.type });
    }
  }

  private applyMessage(msg: ScreenDcPcToPhone): void {
    switch (msg.type) {
      case "geometry":
        if (this.geometry.apply(msg)) {
          // the old positions mean nothing on the new generation: the pointer waits for the next report
          this.pointer.geometryChanged();
          this.latestCursorPos = null;
          this.set({ geometry: this.geometry.known, surfaceIndex: msg.surface_index });
        }
        return;
      case "state": {
        const paused = msg.state === "paused";
        if (!paused) this.lastFrame = this.now(); // the stall clock starts again from a resume
        if (paused && msg.reason) this.pauseCauses.add(msg.reason);
        if (!paused) this.pauseCauses.clear(); // the worker sends `sending` only once no cause is left
        if (paused) this.pointer.hide(); // the PC sends no pointer while paused and re-sends it after the resume
        const cursorMode = msg.cursor ?? this.state.cursorMode;
        if (cursorMode !== this.state.cursorMode) this.pointer.setMode(cursorMode);
        const phase = this.state.phase === "live" && paused ? "paused" : this.state.phase === "paused" && !paused ? "live" : this.state.phase;
        this.set({ pcState: msg.state, pauseReason: paused ? (this.pauseShown() ?? msg.reason ?? null) : null, phase, cursorMode, ...(msg.encoder ? { encoder: msg.encoder } : {}), ...(msg.data_saver !== undefined ? { dataSaver: msg.data_saver } : {}), ...(paused ? {} : { stalled: false, lastFrameAt: this.now() }) });
        return;
      }
      case "allowance":
        this.set({ allowance: allowanceFrom(msg, this.now()) });
        return;
      case "restricted":
        this.set({ restricted: { restricted: msg.restricted, reason: msg.reason ?? null } });
        return;
      case "cursor_pos":
        // the reliable channel carries it when the cursor channel is not open (the PC's fallback)
        this.acceptCursorPos(msg, false);
        return;
      case "cursor_shape": {
        const shape = parseCursorShape(msg);
        if (!shape) {
          this.pointerFacts.rejected += 1;
          return;
        }
        this.pointer.shape(shape);
        this.pointerFacts.shapes += 1;
        if (shape.kind === "custom") this.pointerFacts.custom += 1;
        this.pointerFacts.lastKind = shape.kind;
        return;
      }
    }
  }

  /** `dome-screen-cursor-v1` (unordered, no retransmits): `cursor_pos` only; anything else is dropped and counted. */
  private onCursorChannelMessage(cdc: DataChannelLike, data: unknown): void {
    if (cdc !== this.cdc) return;
    const msg = this.parseInbound(data);
    if (!msg) return;
    if (msg.type !== "cursor_pos") {
      this.dcDropped += 1;
      return;
    }
    try {
      this.acceptCursorPos(msg, true);
    } catch (e) {
      this.dcDropped += 1;
      log.info("screen.dc_handler_failed", { name: e instanceof Error ? e.name : "Error", type: msg.type });
    }
  }

  /** One slot, last wins: the pointer reads it once per animation frame. No state change, no React render. */
  private acceptCursorPos(msg: Extract<ScreenDcPcToPhone, { type: "cursor_pos" }>, unreliable: boolean): void {
    if (msg.gen !== this.geometry.known?.gen) {
      this.pointerFacts.droppedGen += 1;
      return;
    }
    this.latestCursorPos = { x: msg.x, y: msg.y, visible: msg.visible, gen: msg.gen };
    this.pointerFacts.positions += 1;
    if (unreliable) this.pointerFacts.unreliable += 1;
  }

  // ---- lifecycle frames from the relay ----------------------------------------------------------------

  private onSessionFrame(frame: relayFrames.AgentScreenSession): boolean {
    if (frame.pc_id !== this.state.pcId) return false;
    const previous = this.previous;
    if (previous !== null && previous.sessionId !== null && frame.screen_session_id === previous.sessionId) {
      // the session kept aside during a switch changes nothing on screen: its end means the PC replaced it, and its
      // pause and resume are kept for a restore (a failed switch must not show a paused PC as sending)
      if (frame.event === "ended") this.onPreviousEnded(previous, frame.reason);
      else if (frame.event === "paused" || frame.event === "resumed") this.notePreviousPause(previous, frame);
      return true;
    }
    if (frame.screen_session_id !== this.state.sessionId && this.retiredSessionIds.has(frame.screen_session_id)) {
      // a session this phone already left: its late `ended` (the PC's answer to our `bye`, up to 2 s later) must not
      // end the automatic fresh session that is in flight with no id yet, nor count as another phone's (DECISIONS 83)
      return true;
    }
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
        this.lastFrame = this.now();
        this.set({ pcState: "sending", pauseReason: null, phase: this.state.phase === "paused" ? "live" : this.state.phase, ...(frame.path ? { path: frame.path } : {}), lastFrameAt: this.lastFrame, stalled: false });
        return true;
      case "ended":
        this.retire(frame.screen_session_id);
        this.endSession(frame.reason, frame.live_seconds);
        return true;
    }
    return true;
  }

  /**
   * The PC ended the session kept aside during a switch (normally `replaced`, once the new start landed): there is
   * nothing to restore any more. Its heartbeat stops; its peer closes at once if the new session's media already came,
   * else when the new picture shows (so the last picture stays as long as possible).
   */
  private onPreviousEnded(previous: PreviousSession, reason: ScreenEndReason): void {
    previous.endedByPc = true;
    if (previous.hbTimer !== null) this.timers.clearInterval(previous.hbTimer);
    previous.hbTimer = null;
    this.retire(previous.sessionId);
    log.info("screen.previous_ended", { reason });
    if (this.state.incomingStream !== null || this.state.stream === null) {
      closeObjects(previous.pc, previous.dc, previous.cdc);
      previous.pc = previous.dc = previous.cdc = null;
    }
  }

  /**
   * The PC paused or resumed the session kept aside during a switch: recorded in what a failed switch restores (its
   * pause causes, its state and its stall clock), exactly as the live session would have applied it.
   */
  private notePreviousPause(previous: PreviousSession, frame: relayFrames.AgentScreenSession): void {
    const cause = frame.reason === "indicator" ? "indicator" : "secure_desktop";
    const s = previous.state;
    if (frame.event === "paused") {
      previous.pauseCauses.add(cause);
      previous.state = { ...s, pcState: "paused", pauseReason: pauseShownOf(previous.pauseCauses), phase: s.phase === "live" ? "paused" : s.phase };
    } else {
      previous.pauseCauses.delete(cause);
      if (previous.pauseCauses.size > 0) {
        previous.state = { ...s, pauseReason: pauseShownOf(previous.pauseCauses), ...(frame.path ? { path: frame.path } : {}) };
      } else {
        previous.lastFrame = this.now();
        previous.state = { ...s, pcState: "sending", pauseReason: null, phase: s.phase === "paused" ? "live" : s.phase, ...(frame.path ? { path: frame.path } : {}), lastFrameAt: previous.lastFrame, stalled: false };
      }
    }
    previous.phase = previous.state.phase;
    log.info(frame.event === "paused" ? "screen.previous_paused" : "screen.previous_resumed", { reason: cause });
  }

  /** The cause the page explains while paused: a Windows prompt first (someone must answer it), else a missing bar. */
  private pauseShown(): "secure_desktop" | "indicator" | null {
    return pauseShownOf(this.pauseCauses);
  }

  /** Remember a session this phone has left, so its late lifecycle frames change nothing (bounded, oldest forgotten first). */
  private retire(sessionId: string | null): void {
    if (!sessionId) return;
    this.retiredSessionIds.delete(sessionId);
    this.retiredSessionIds.add(sessionId);
    for (const oldest of this.retiredSessionIds) {
      if (this.retiredSessionIds.size <= RETIRED_SESSIONS_MAX) break;
      this.retiredSessionIds.delete(oldest);
    }
  }

  private endSession(reason: ScreenEndReason, liveSeconds: number): void {
    this.pauseCauses.clear();
    this.nonce += 1;
    this.resolveIce({ kind: "aborted" });
    this.clearTimers();
    this.probeMedia(); // the <video>'s last facts, for the diagnostics bundle
    this.closePeer();
    this.closePrevious(false);
    this.pointer.reset();
    const resume: ScreenResume = NO_RESUME.has(reason) ? "none" : "manual";
    this.set({ phase: "ended", endReason: reason, liveSeconds, stream: null, incomingStream: null, switching: null, verified: false, transportConnected: false, playHint: false, resume, consentWaiting: false, consentDeadlineAt: null, stalled: false, tryingRelay: false, owned: reason === "takeover" });
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
    this.retire(sessionId);
    if (phase === "idle") return;
    const liveSeconds = startedAt !== null ? Math.max(0, Math.round((this.now() - startedAt) / 1000)) : 0;
    const inSession = phase === "connecting" || phase === "live" || phase === "paused" || phase === "starting";
    if (inSession) this.sendChannel({ type: "bye" });
    this.probeMedia(); // the <video>'s last facts, for the diagnostics bundle
    this.closePeer();
    // a stop during a switch ends the session kept aside too (`bye` on its own channel; its lease lapses otherwise)
    this.closePrevious(true);
    this.pointer.reset();
    this.latestCursorPos = null;
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
      // a switch in flight restored the old session: its picture continues like any live one
      if (this.isLive) this.set({ relayDown: true });
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
    this.closePrevious(false);
    this.geometry.reset();
    this.offerSdp = null;
    this.pin = null;
    this.resetFacts();
    this.resetRunFacts();
    this.codecFallback = null;
    this.pointer.reset();
    this.latestCursorPos = null;
    this.lastSurfaceByPc.clear();
    this.noCursorFor = this.noCursorVersions = null;
    this.retryCarry = null;
    this.set({ ...EMPTY_SCREEN_STATE });
  }

  // ---- failures ------------------------------------------------------------------------------------

  private fail(code: string, message?: string, detail?: Record<string, unknown>, origin?: string): ScreenSessionState {
    const previous = this.previous;
    // a switch that failed before its new picture arrived, while the PC still runs the old session: keep that one
    if (previous !== null && !previous.endedByPc && this.state.incomingStream === null) return this.restorePrevious(previous, code, origin, detail);
    this.nonce += 1;
    this.retire(this.state.sessionId);
    this.resolveIce({ kind: "aborted" });
    this.clearTimers();
    this.probeMedia();
    this.closePeer();
    this.closePrevious(false);
    this.pointer.reset();
    const resetsAt = typeof detail?.resets_at === "string" ? detail.resets_at : null;
    const allowance = code === "SCREEN_ALLOWANCE_EXHAUSTED" ? { unlimited: false, remainingSeconds: 0, resetsAt, at: this.now() } : this.state.allowance;
    this.set({ phase: "failed", problem: { code, ...(message ? { message } : {}), ...(detail ? { detail } : {}) }, owned: code === "SCREEN_SESSION_OWNED", stream: null, incomingStream: null, switching: null, verified: false, transportConnected: false, playHint: false, consentWaiting: false, consentDeadlineAt: null, tryingRelay: false, stalled: false, resume: "none", allowance });
    log.info("screen.failed", { code, phase: this.state.phase });
    return this.state;
  }

  /**
   * A switch failed (refused by the relay: RATE_LIMITED, SCREEN_ALLOWANCE_EXHAUSTED, PC_OFFLINE, the stamp gate; a
   * 1.4 agent's SCREEN_CONSENT_DECLINED, whose consent runs before it ends anything; no answer; a local error) and
   * the PC still runs the old session: the old peer, channels and heartbeat become current again and the old picture
   * simply stays, with a short "Couldn't switch screens" note. A later `ended{replaced}` for it ends it as usual. For
   * SCREEN_ALLOWANCE_EXHAUSTED the allowance reads 0 (with the refusal's `resets_at`) over the old picture instead: the
   * page's out-of-minutes countdown, then the PC's `ended{allowance_exhausted}` and its exhausted state, no note.
   */
  private restorePrevious(previous: PreviousSession, code: string, origin: string | undefined, detail: Record<string, unknown> | undefined): ScreenSessionState {
    this.previous = null;
    this.nonce += 1;
    this.retire(this.state.sessionId); // the failed attempt's own id, if it had one
    this.resolveIce({ kind: "aborted" });
    this.clearTimers();
    this.closePeer(); // the failed attempt's peer and its heartbeat
    this.pc = previous.pc;
    this.dc = previous.dc;
    this.cdc = previous.cdc;
    this.hbTimer = previous.hbTimer;
    if (this.pc && this.dc) this.attach(this.pc, this.dc, this.cdc);
    this.answerApplied = this.pc !== null;
    if (previous.sessionId) this.retiredSessionIds.delete(previous.sessionId);
    this.geometry.reset();
    if (previous.geometry) this.geometry.apply(previous.geometry);
    // the old picture stayed on screen the whole time: the stall clock counts again from now
    const now = this.now();
    this.lastFrame = previous.lastFrame === null ? null : now;
    this.pauseCauses = new Set(previous.pauseCauses);
    this.timeline = { ...previous.timeline };
    this.answerAt = previous.answerAt;
    this.dtlsAt = previous.dtlsAt;
    this.hbSent = previous.hbSent;
    this.dcReceived = previous.dcReceived;
    this.dcDropped = previous.dcDropped;
    this.lastStats = previous.lastStats;
    this.offerFacts = previous.offer;
    this.latestCursorPos = null;
    if (this.pointer.pointerMode !== previous.state.cursorMode) this.pointer.setMode(previous.state.cursorMode);
    if (previous.state.pcId && previous.surfaceIndex !== null) this.lastSurfaceByPc.set(previous.state.pcId, previous.surfaceIndex);
    this.surfaceFacts.switchFailures += 1;
    const human = humanSurface(previous.state.surfaces, previous.surfaceIndex);
    const known = this.state.allowance ?? previous.state.allowance;
    const resetsAt = typeof detail?.resets_at === "string" ? detail.resets_at : (known?.resetsAt ?? null);
    const allowance: ScreenAllowance | null = code === "SCREEN_ALLOWANCE_EXHAUSTED" ? { unlimited: false, remainingSeconds: 0, resetsAt, at: now } : known;
    // the relay socket may have gone during the switch (or been down when it began): clicks stay paused
    const relayDown = this.state.relayDown || !this.deps.connected();
    if (previous.state.pcState === "paused") this.pointer.hide(); // the PC sends no pointer while paused
    this.set({ ...previous.state, ...(this.lastFrame !== null ? { lastFrameAt: now, stalled: false } : {}), allowance, otherViewer: this.state.otherViewer, relayDown, switching: null, incomingStream: null, switched: null, switchedGone: this.state.switchedGone, switchFailed: { code, human, at: now } });
    log.info("screen.switch_failed", { code, origin: origin ?? "phone" });
    if (this.isLive) {
      this.startStallWatchdog();
      this.startStatsLoop();
    }
    // the old transport may have gone while it was aside
    if (this.pc) this.onPeerState(this.pc);
    return this.state;
  }

  /** The session kept aside during a switch goes: its heartbeat stops, its peer closes (`bye` first when asked). */
  private closePrevious(bye: boolean): void {
    const previous = this.previous;
    if (!previous) return;
    this.previous = null;
    if (previous.hbTimer !== null) this.timers.clearInterval(previous.hbTimer);
    if (bye && !previous.endedByPc && previous.dc?.readyState === "open") {
      try {
        previous.dc.send(JSON.stringify({ type: "bye" }));
      } catch (e) {
        log.info("screen.dc_send_failed", errorSummary(e));
      }
    }
    this.retire(previous.sessionId);
    closeObjects(previous.pc, previous.dc, previous.cdc);
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
    const { pc, dc, cdc } = this;
    this.dc = null;
    this.cdc = null;
    this.pc = null;
    this.answerApplied = false;
    this.pendingStream = null;
    this.stopHeartbeat();
    closeObjects(pc, dc, cdc);
  }

  private clearTimers(): void {
    this.stopHeartbeat();
    for (const h of [this.connectTimer, this.relayHintTimer, this.consentTimer]) if (h !== null) this.timers.clearTimeout(h);
    this.connectTimer = this.relayHintTimer = this.consentTimer = null;
    for (const h of [this.stallTimer, this.statsTimer, this.pictureTimer]) if (h !== null) this.timers.clearInterval(h);
    this.stallTimer = this.statsTimer = this.pictureTimer = null;
  }
}

/** The pause cause the page explains: a Windows prompt first (someone must answer it), else a missing bar. */
function pauseShownOf(causes: ReadonlySet<"secure_desktop" | "indicator">): "secure_desktop" | "indicator" | null {
  if (causes.has("secure_desktop")) return "secure_desktop";
  return causes.has("indicator") ? "indicator" : null;
}

/** Detach every handler of a peer and its channels (they stay open): their late events reach nothing. */
function detach(pc: PeerConnectionLike | null, dc: DataChannelLike | null, cdc: DataChannelLike | null): void {
  for (const ch of [dc, cdc]) if (ch) ch.onopen = ch.onmessage = ch.onclose = null;
  if (pc) pc.ontrack = pc.onconnectionstatechange = pc.oniceconnectionstatechange = pc.onicegatheringstatechange = null;
}

/** Detach, then close the channels and the peer (each may already be closed). */
function closeObjects(pc: PeerConnectionLike | null, dc: DataChannelLike | null, cdc: DataChannelLike | null): void {
  detach(pc, dc, cdc);
  for (const ch of [dc, cdc]) {
    if (!ch) continue;
    try {
      ch.close();
    } catch {
      /* already closed */
    }
  }
  if (pc) {
    try {
      pc.close();
    } catch {
      /* already closed */
    }
  }
}

/** A stream for a track that came without one (`ev.streams` empty: a sender without `a=msid`), where the browser can. */
function streamOf(track: TrackEventLike["track"]): MediaStream | null {
  if (typeof MediaStream !== "function") return null;
  try {
    return new MediaStream([track as MediaStreamTrack]);
  } catch {
    return null;
  }
}

/**
 * The automatic retry after H.264 arrived but never decoded: VP8 (and RTX for it) only, so the PC answers VP8. Both
 * offer checks accept a VP8-only offer (rules.screen_signaling (4)). Where the browser cannot say so, nothing changes.
 */
function preferVp8Only(transceiver: TransceiverLike): void {
  const receiver = (globalThis as { RTCRtpReceiver?: { getCapabilities?: (kind: string) => { codecs: RTCRtpCodec[] } | null } }).RTCRtpReceiver;
  let caps: { codecs: RTCRtpCodec[] } | null | undefined;
  try {
    caps = receiver?.getCapabilities?.("video");
  } catch {
    caps = null;
  }
  const vp8 = (caps?.codecs ?? []).filter((c) => c.mimeType.toLowerCase() === "video/vp8");
  if (typeof transceiver.setCodecPreferences !== "function" || vp8.length === 0) {
    log.info("screen.codec_fallback_skipped", { reason: typeof transceiver.setCodecPreferences !== "function" ? "no_codec_preferences" : "no_vp8" });
    preferCodecs(transceiver, "video");
    return;
  }
  const rtx = (caps?.codecs ?? []).filter((c) => c.mimeType.toLowerCase() === "video/rtx");
  try {
    transceiver.setCodecPreferences([...vp8, ...rtx]);
    log.info("screen.codec_fallback_offered", { to: "vp8" });
  } catch (e) {
    log.info("screen.codec_fallback_skipped", { reason: "refused", ...errorSummary(e) });
    preferCodecs(transceiver, "video");
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

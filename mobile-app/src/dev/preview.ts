/**
 * Design preview — DEVELOPMENT ONLY. Never part of a production bundle: `main.tsx` imports this module
 * only inside `if (import.meta.env.DEV)`, which `vite build` replaces with `false` and drops, and
 * `scripts/check-bundle.ts` fails `pnpm build` if the marker below ever shows up in `dist/`.
 *
 * Routes (dev server only; each one lands on the real page with a fake signed-in state):
 *   /preview                  Home ("Living Room PC" online, YouTube playing in Edge, volume 55 → 40)
 *   /preview/remote           Remote (YouTube tab with a live scrubber, Spotify paused, search works)
 *   /preview/assistant        Ask (a short butler conversation types itself in once the PC is ready)
 *   /preview/apps             Apps (ZStream and Edge running, Spotify closed; every button answers)
 *   /preview/devices          Devices (this phone, the PC with its grant, one paired phone)
 *   /preview/devices/pair     Pair (type ABCDE-FGHJK-MNPQR-STVWX → details → "waiting for approval")
 *   /preview/settings         Settings & help (AI on, Free's taste: 4 of 10 used; the consent sheet opens)
 *   /preview/health           Connection health (every layer answers; Retry and the walkthrough work)
 *   /preview/billing          Billing (the Free plan from the fake session)
 *   /preview/command          Type a command (parses locally; plans run against the fake PC)
 *   /preview/touchpad         Touchpad (a live input session: the keyboard panel opens, acks come back)
 *   /preview/routines, /preview/layouts, /preview/more
 *   /preview/link?user_code=ABCD-EFGH   Link a PC (the agent-link preview answers; approve is not in the preview)
 * Flags (on any route): `?offline=1` shows the offline screen; `?confirm=1` has the PC ask this phone
 * to confirm closing Edge, which opens the global confirmation modal; `?pc=offline` has the PC offline
 * since 3 hours ago (its last report still known): nothing is sent at start, and a command fails with PC_OFFLINE
 * as the relay answers it.
 *
 * This tab then runs the REAL app — real pages, stores, runtime, relay client, command signing and the butler —
 * against a fake account and a fake PC living in this module, instead of cloud-api:
 *   - REST answers (`configureApi({ fetchImpl })`): a signed-in account, one PC "Living Room PC", this
 *     phone's grant, the assistant's status (AI on, Free's taste: 6 of 10 requests left), pairing and agent-link stubs;
 *   - the relay socket (`Runtime({ relay: { socketFactory } })`): hello → hello_ack, subscribe → online +
 *     the PC's state (YouTube playing in Edge, Spotify paused, Windows volume 55 → 40 after the
 *     conversation), believable answers to the everyday commands, confirmation challenges for Close and
 *     power actions, and input acks for the touchpad, so buttons work;
 *   - the butler: a quiet speaker and a pretend microphone (tap, tap again: it "hears" "pause YouTube"),
 *     and a short conversation typed in through the real Assistant controller once the PC is ready.
 * Nothing here talks to a network. `/preview/off` leaves preview mode (it lasts for this tab only).
 */
import { ERRORS, type JsonValue, type rest } from "@dome/protocol";

import { Runtime, setRuntimeForTests } from "../app/runtime.ts";
import { getAssistant, setVoiceForTests } from "../app/voice.ts";
import { configureApi, type FetchLike } from "../lib/api.ts";
import { getControllerIdentity } from "../lib/controllerKey.ts";
import type { WebSocketLike } from "../lib/relay.ts";
import type { RecognitionHandlers, Recognizer, Speaker } from "../lib/speech.ts";
import { useAssistantStore } from "../store/assistant.ts";
import { useDevicesStore } from "../store/devices.ts";
import { isFresh, useLiveStore, type AppEntry, type PcState, type YoutubeTab } from "../store/live.ts";

/** sessionStorage key and bundle marker (scripts/check-bundle.ts PREVIEW_MARKER). */
export const PREVIEW_FLAG = "dome-design-preview";

/** Every preview route (the path under /preview and the app route it opens); the test checks each one exists. */
export const PREVIEW_ROUTES: ReadonlyArray<{ preview: string; app: string; note: string }> = [
  { preview: "/preview", app: "/app", note: "Home" },
  { preview: "/preview/remote", app: "/app/remote", note: "Remote" },
  { preview: "/preview/assistant", app: "/app/assistant", note: "Ask" },
  { preview: "/preview/apps", app: "/app/apps", note: "Apps" },
  { preview: "/preview/devices", app: "/app/devices", note: "Devices" },
  { preview: "/preview/devices/pair", app: "/app/devices/pair", note: "Pair this phone" },
  { preview: "/preview/settings", app: "/app/settings", note: "Settings & help" },
  { preview: "/preview/health", app: "/app/health", note: "Connection health" },
  { preview: "/preview/billing", app: "/app/billing", note: "Billing" },
  { preview: "/preview/command", app: "/app/command", note: "Type a command" },
  { preview: "/preview/touchpad", app: "/app/touchpad", note: "Touchpad" },
  { preview: "/preview/routines", app: "/app/routines", note: "Routines (Pro preview)" },
  { preview: "/preview/layouts", app: "/app/layouts", note: "Custom remotes (Pro preview)" },
  { preview: "/preview/more", app: "/app/more", note: "More" },
  { preview: "/preview/link", app: "/link", note: "Link a PC (outside the tab bar)" },
];

/** Query flags understood on any preview route. */
export const PREVIEW_FLAGS: ReadonlyArray<{ flag: string; note: string }> = [
  { flag: "offline=1", note: "the offline screen (navigator.onLine reads false)" },
  { flag: "confirm=1", note: "the PC asks this phone to confirm closing Edge: the global confirmation modal" },
  { flag: "pc=offline", note: "the PC went offline 3 hours ago: Home and Remote say what to do; nothing runs, a command fails with PC_OFFLINE" },
];

/** The app route a preview path opens (`/preview/link` is the only one outside `/app`). */
export function previewTarget(pathname: string): string {
  const rest = pathname.slice("/preview".length);
  return rest === "/link" ? "/link" : `/app${rest}`;
}

const ACCOUNT = "6f1c2a9e-3b4d-4e5f-8a6b-7c8d9e0f1a2b";
const CONTROLLER = "2b3c4d5e-6f70-4a81-92a3-b4c5d6e7f809";
const OTHER_CONTROLLER = "7c8d9e0f-1a2b-4c3d-8e4f-5a6b7c8d9e0f";
const PC = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const GRANT = "4d5e6f70-8192-4a3b-9c4d-5e6f7a8b9c0d";
const OTHER_GRANT = "1e2f3a4b-5c6d-4e7f-8a9b-0c1d2e3f4a5b";
const PAIRING = "0f1e2d3c-4b5a-4968-8776-655443322110";
const EDGE = "bi_preview_edge";
const CREATED = "2026-09-02T18:30:00.000Z";
const PC_NAME = "Living Room PC";

const now = () => new Date().toISOString();
const inSeconds = (s: number) => new Date(Date.now() + s * 1000).toISOString();

const SESSION: rest.SessionResponse = {
  account: { id: ACCOUNT, email: "alex@example.com", display_name: "Alex", created_at: CREATED },
  csrf_token: "preview-csrf-token-0000000000000000000000",
  plan: "free",
  limits: { max_enabled_pcs: 1, max_controllers: 2, routines: false, custom_layouts: false },
  protocol_version: "1.2",
};

let pcName = PC_NAME;
let pcEnabled = true;
const pcRow = (): rest.Pc => ({ id: PC, name: pcName, enabled: pcEnabled, connection: "online", last_seen: now(), created_at: CREATED, platform: "windows", agent_version: "0.1.0" });

const CONTROLLER_ROW: rest.Controller = { id: CONTROLLER, kid: "previewPhoneKid".padEnd(43, "0"), display_name: "Alex’s iPhone", status: "active", created_at: CREATED, last_seen: now() };
const OTHER_CONTROLLER_ROW: rest.Controller = { id: OTHER_CONTROLLER, kid: "previewOtherKid".padEnd(43, "0"), display_name: "Sam’s iPhone", status: "active", created_at: "2026-09-20T09:10:00.000Z", last_seen: "2026-10-11T19:42:00.000Z" };

const GRANT_ROW: rest.Grant = { id: GRANT, controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "media", "volume", "apps", "lock", "power", "pointer", "keyboard"], created_at: CREATED };
const OTHER_GRANT_ROW: rest.Grant = { id: OTHER_GRANT, controller_id: OTHER_CONTROLLER, pc_id: PC, capabilities: ["status", "media", "volume"], created_at: "2026-09-20T09:12:00.000Z" };

function assistantStatus(): rest.AssistantStatusResponse {
  return {
    available: true,
    unavailable_reason: null,
    provider: "fake",
    turn_deadline_seconds: 30,
    consent_version: "2026-10-11",
    consent: { version: "2026-10-11", given_at: CREATED },
    // Free's one-time taste (10 requests, 4 used → "6 AI requests left" on Ask, "4 of 10 AI requests to try used"
    // in Settings), as the website sells it: no window, no refresh, no top-up (protocol 1.3 assistant_allowance)
    allowance: { kind: "taste", taste: { limit: 10, used: 4 }, window: null, week: null, topup: { balance: 0, allowed: false }, exhausted: null, resets_at: null, warn_at_percent: 80 },
  };
}

function pairingStatus(): rest.PairingStatusResponse {
  return { pairing_id: PAIRING, state: "claimed", pc_id: PC, pc_name: pcName, pc_online: true, expires_at: inSeconds(290) };
}

function json(status: number, body: unknown): Response {
  return new Response(body === null ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const previewFetch: FetchLike = async (input, init) => {
  const path = new URL(input, window.location.origin).pathname;
  const method = (init.method ?? "GET").toUpperCase();
  const body = typeof init.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : {};
  if (method === "GET" && path === "/v1/session") return json(200, SESSION);
  if (method === "GET" && path === "/v1/pcs") return json(200, { pcs: [pcRow()] });
  if (method === "PATCH" && path === `/v1/pcs/${PC}`) {
    if (typeof body.name === "string") pcName = body.name;
    if (typeof body.enabled === "boolean") pcEnabled = body.enabled;
    return json(200, pcRow());
  }
  if (method === "DELETE" && path === `/v1/pcs/${PC}`) return json(204, null);
  // this phone's row carries this browser's real key id, so Devices and Settings recognise "this phone"
  if (method === "GET" && path === "/v1/controllers") return json(200, { controllers: [{ ...CONTROLLER_ROW, kid: (await getControllerIdentity()).kid }, OTHER_CONTROLLER_ROW] });
  if (method === "PATCH" && path.startsWith("/v1/controllers/")) {
    const row = path.endsWith(OTHER_CONTROLLER) ? OTHER_CONTROLLER_ROW : CONTROLLER_ROW;
    if (typeof body.display_name === "string") row.display_name = body.display_name;
    return json(200, { ...row, kid: row === CONTROLLER_ROW ? (await getControllerIdentity()).kid : row.kid });
  }
  if (method === "DELETE" && (path.startsWith("/v1/controllers/") || path.startsWith("/v1/grants/"))) return json(204, null);
  if (method === "GET" && path === `/v1/pcs/${PC}/grants`) return json(200, { grants: [GRANT_ROW, OTHER_GRANT_ROW] });
  if (method === "GET" && path === "/v1/assistant") return json(200, assistantStatus());
  if (method === "POST" && path === "/v1/assistant/consent") return json(200, assistantStatus());
  if (method === "DELETE" && path === "/v1/assistant/consent") return json(204, null);
  if (method === "GET" && path === "/v1/commands") return json(200, { commands: [] });
  if (method === "POST" && path === "/v1/auth/logout") return json(204, null);
  // pairing: the claim is accepted and the PC "keeps asking", so the waiting screen (verification code) stays up
  if (method === "POST" && path === "/v1/pairing/claim") return json(200, pairingStatus());
  if (method === "GET" && path === `/v1/pairing/${PAIRING}`) return json(200, pairingStatus());
  // agent link: the preview answers; approving a PC is not part of the design preview
  if (method === "GET" && path.startsWith("/v1/agent-link/")) {
    const code = decodeURIComponent(path.slice("/v1/agent-link/".length));
    return json(200, { user_code: code, agent_version: "0.1.0", platform: "windows", kid: "previewAgentKid".padEnd(43, "0"), pc_name_hint: "Study PC", expires_at: inSeconds(540) } satisfies rest.AgentLinkPreviewResponse);
  }
  return json(503, { error: { code: "AI_UNAVAILABLE", message: "This part of DoMe is not in the design preview.", retryable: false } });
};

// ---- the fake PC ---------------------------------------------------------------------------------

const VIDEOS = [
  { id: "jfKfPfyJRdk", title: "Lo-fi beats to relax and study to", duration: 1900 },
  { id: "5qap5aO4i9A", title: "Midnight jazz for slow evenings", duration: 2710 },
  { id: "DWcJFNfaw9c", title: "Deep focus: ambient music for work", duration: 3600 },
];

const SEARCH_RESULTS = [
  { index: 1, video_id: "jfKfPfyJRdk", title: "lofi hip hop radio — beats to relax/study to", channel: "Lofi Girl", is_live: true },
  { index: 2, video_id: "5qap5aO4i9A", title: "Midnight jazz for slow evenings", channel: "Jazz Lounge", duration_seconds: 2710 },
  { index: 3, video_id: "DWcJFNfaw9c", title: "Deep focus: ambient music for work", channel: "Calm Sound", duration_seconds: 3600 },
];

const EDGE_TITLE = "Lo-fi beats to relax and study to - YouTube — Microsoft Edge";

/** The 22-character base64url session id the contract expects. */
const INPUT_SESSION = "cHJldmlldy1pbnB1dC1zZQ";

type Reply = (f: Record<string, unknown>, delay?: number) => void;

/** Exported for the tests only (the preview PC's answers); nothing outside this module uses it. */
export class PreviewPc {
  /** `?pc=offline`: the PC went offline 3 hours ago (its last report is still known). */
  offline = false;
  private video = 0;
  private sockets = new Set<PreviewSocket>();
  /** Commands waiting for this phone's answer to a confirmation challenge. */
  private pendingConfirmations = new Map<string, { action: string; target: Record<string, unknown> | null; reply: Reply }>();
  tab: YoutubeTab = { browser_instance_id: EDGE, tab_id: 7, tab_token: "cHJldmlldy10YWItdG9rZW", script_attached: true, title: VIDEOS[0]!.title, video_id: VIDEOS[0]!.id, paused: false, muted: false, volume: 72, position_seconds: 724, duration_seconds: VIDEOS[0]!.duration, context: "watch", ad_showing: false, is_live: false, in_playlist: true, theater: false, fullscreen: false, has_next: true, has_previous: true, active: true };
  volume = { value: 55, muted: false };
  spotify = { session_id: "spotify", app_label: "Spotify", title: "Weightless", artist: "Marconi Union", status: "paused" as "paused" | "playing", controls: ["play", "next", "previous"] as Array<"play" | "pause" | "next" | "previous"> };
  apps: AppEntry[] = [
    { app_id: "zstream-app", display_name: "ZStream", running: true, windows: [{ window_id: "z1", title: "ZStream" }] },
    { app_id: "msedge", display_name: "Edge", running: true, windows: [{ window_id: "e1", title: EDGE_TITLE, foreground: true }] },
    { app_id: "spotify", display_name: "Spotify", running: false, windows: [] },
  ];
  inputAccepted = 0;

  constructor() {
    // the video plays on: move the position along so the remote's scrubber is alive
    setInterval(() => {
      if (this.tab.paused === false && typeof this.tab.position_seconds === "number") this.tab.position_seconds = Math.min((this.tab.duration_seconds ?? 0) - 1, this.tab.position_seconds + 1);
    }, 1000);
    // the agent re-sends its state every 30 s
    setInterval(() => this.pushState(), 25_000);
  }

  attach(s: PreviewSocket) {
    this.sockets.add(s);
  }
  detach(s: PreviewSocket) {
    this.sockets.delete(s);
  }

  state(): PcState {
    // foreground_app rides on every state frame (the runtime takes the touchpad's "in front" from it)
    return { remote_enabled: true, session_locked: false, extension_connected: true, platform: "windows", volume: { ...this.volume }, browser_instances: [{ browser_instance_id: EDGE, browser: "edge" }], youtube_tabs: [{ ...this.tab }], media_sessions: [{ ...this.spotify, controls: [...this.spotify.controls] }], pending_power_action: null, foreground_app: this.foregroundApp() } as PcState;
  }

  pushState() {
    for (const s of this.sockets) s.deliver({ type: "state", pc_id: PC, at: now(), state: this.state() as unknown as JsonValue });
  }

  private foregroundApp() {
    const edge = this.apps.find((a) => a.app_id === "msedge");
    return edge?.running && edge.windows.length > 0 ? { process_name: "msedge.exe", window_title: EDGE_TITLE, browser: "edge", elevated: false } : { process_name: "explorer.exe", window_title: "Desktop", elevated: false };
  }

  /** Actions the PC refuses to run until this phone confirms (ADR-0001 D7). */
  private needsConfirmation(action: string): boolean {
    return action === "app.close" || action === "power.sleep" || action === "power.restart" || action === "power.shutdown";
  }

  private challenge(cmd: { command_id: string; action: string; params?: Record<string, unknown>; target?: Record<string, unknown> | null }): string {
    const app = this.apps.find((a) => a.app_id === cmd.target?.app_id);
    const label = cmd.action === "app.close" ? `Close ${app?.display_name ?? "app"}` : `${cmd.action.replace("power.", "").replace(/^\w/, (c) => c.toUpperCase())} the PC`;
    const detail = cmd.action === "app.close" ? (app?.windows[0]?.title ?? app?.display_name ?? "") : "Unsaved work in open apps may be lost.";
    return JSON.stringify({ challenge_id: crypto.randomUUID(), command_id: cmd.command_id, controller_id: CONTROLLER, pc_id: PC, action: cmd.action, params: cmd.params ?? {}, target: cmd.target ?? null, target_state_digest: "A".repeat(43), issued_at: now(), expires_at: inSeconds(60), display: { pc_name: pcName, action_label: label, detail } });
  }

  /** One controller frame in, the relay's (and the PC's) answers out. */
  handle(frame: Record<string, unknown>, reply: Reply) {
    switch (frame.type) {
      case "hello": {
        const versions = (frame.protocol_versions as string[] | undefined) ?? ["1.2"];
        reply({ type: "hello_ack", protocol_version: versions[0] ?? "1.2", server_time: now(), connection_id: crypto.randomUUID(), controller_id: CONTROLLER });
        return;
      }
      case "subscribe":
        if (this.offline) {
          const seen = new Date(Date.now() - 3 * 3600_000).toISOString();
          reply({ type: "state", pc_id: PC, at: seen, state: this.state() });
          reply({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: seen, enabled: true }, 40);
          return;
        }
        reply({ type: "pc_status", pc_id: PC, connection: "online", last_seen: now(), enabled: true });
        reply({ type: "state", pc_id: PC, at: now(), state: this.state() }, 60);
        return;
      case "ping":
        reply({ type: "pong", ...(typeof frame.t === "string" ? { t: frame.t } : {}) });
        return;
      case "command": {
        const env = frame.envelope as { payload: string };
        const cmd = JSON.parse(env.payload) as { command_id: string; action: string; params?: Record<string, unknown>; target?: Record<string, unknown> | null };
        if (this.offline) {
          // as the relay does (routing step 8): an offline PC gets nothing queued, the command fails at once
          reply({ type: "result", command_id: cmd.command_id, origin: "relay", state: "failed", at: now(), duration_ms: 0, error: { code: "PC_OFFLINE", message: ERRORS.PC_OFFLINE?.user_message ?? "The PC is offline.", retryable: true } }, 60);
          return;
        }
        reply({ type: "ack", command_id: cmd.command_id, state: "executing", at: now() }, 40);
        if (this.needsConfirmation(cmd.action)) {
          // the PC answers with a challenge; the result follows this phone's Approve / Decline
          this.pendingConfirmations.set(cmd.command_id, { action: cmd.action, target: cmd.target ?? null, reply });
          reply({ type: "confirmation_required", command_id: cmd.command_id, challenge_text: this.challenge(cmd) }, 260);
          return;
        }
        const outcome = this.run(cmd.action, cmd.params ?? {}, cmd.target ?? null);
        const base = { type: "result", command_id: cmd.command_id, origin: "agent", at: now(), duration_ms: 160 };
        reply("error" in outcome ? { ...base, state: "failed", error: { code: outcome.error, message: outcome.message, retryable: false } } : { ...base, state: "succeeded", result: outcome.result }, 220);
        setTimeout(() => this.pushState(), 320);
        return;
      }
      case "confirmation": {
        const env = frame.envelope as { payload: string };
        const c = JSON.parse(env.payload) as { command_id: string; decision: "approve" | "decline" };
        const pending = this.pendingConfirmations.get(c.command_id);
        if (!pending) return;
        this.pendingConfirmations.delete(c.command_id);
        const base = { type: "result", command_id: c.command_id, origin: "agent", at: now(), duration_ms: 420 };
        if (c.decision === "approve") {
          const outcome = this.run(pending.action, {}, pending.target);
          reply("error" in outcome ? { ...base, state: "failed", error: { code: outcome.error, message: outcome.message, retryable: false } } : { ...base, state: "succeeded", result: outcome.result }, 300);
          setTimeout(() => this.pushState(), 400);
        } else {
          reply({ ...base, state: "canceled", error: { code: "CONFIRMATION_DECLINED", message: "You declined on this phone; nothing was done.", retryable: false } }, 200);
        }
        return;
      }
      case "input_batch": {
        // Windows accepted every event: an ack with the batch's sequence number and the running total
        const env = frame.envelope as { payload: string };
        const batch = JSON.parse(env.payload) as { seq: number; events?: unknown[] };
        this.inputAccepted += batch.events?.length ?? 0;
        reply({ type: "input_ack", pc_id: PC, input_session_id: INPUT_SESSION, last_seq: batch.seq, accepted_events: this.inputAccepted, dropped_events: 0, held_buttons: [], held_keys: [], at: now() }, 90);
        return;
      }
      default:
        return;
    }
  }

  private windowState(target: Record<string, unknown> | null, state: "normal" | "minimized" | "maximized", fullscreen = false): { result: Record<string, unknown> } | { error: string; message: string } {
    const app = this.apps.find((a) => a.app_id === target?.app_id);
    if (!app) return { error: "APP_NOT_APPROVED", message: "That app is not approved on the PC." };
    const win = app.windows.find((w) => w.window_id === target?.window_id) ?? app.windows[0];
    if (!win) return { error: "APP_NOT_RUNNING", message: "That app has no window right now." };
    for (const a of this.apps) for (const w of a.windows) w.foreground = a === app && w === win && state !== "minimized";
    return { result: { app_id: app.app_id, window_id: win.window_id, state, fullscreen, foreground: state !== "minimized" } };
  }

  private run(action: string, p: Record<string, unknown>, target: Record<string, unknown> | null): { result: Record<string, unknown> } | { error: string; message: string } {
    const tab = () => ({ result: { tab: { ...this.tab } } });
    switch (action) {
      case "system.ping":
        return { result: {} };
      case "app.list":
        return { result: { apps: this.apps } };
      case "youtube.set_paused":
        this.tab.paused = p.paused === true;
        return tab();
      case "youtube.seek_relative":
        this.tab.position_seconds = Math.max(0, (this.tab.position_seconds ?? 0) + Number(p.seconds ?? 0));
        return tab();
      case "youtube.seek_to":
        this.tab.position_seconds = Number(p.position_seconds ?? 0);
        return tab();
      case "youtube.set_volume":
        this.tab.volume = Number(p.value ?? 0);
        return tab();
      case "youtube.set_muted":
        this.tab.muted = p.muted === true;
        return tab();
      case "youtube.set_theater":
        this.tab.theater = p.enabled === true;
        return tab();
      case "youtube.next":
      case "youtube.previous": {
        const before = this.tab.video_id;
        this.video = (this.video + (action === "youtube.next" ? 1 : VIDEOS.length - 1)) % VIDEOS.length;
        const v = VIDEOS[this.video]!;
        this.tab = { ...this.tab, title: v.title, video_id: v.id, duration_seconds: v.duration, position_seconds: 0, paused: false };
        return { result: { tab: { ...this.tab }, ...(before ? { previous_video_id: before } : {}) } };
      }
      case "youtube.request_fullscreen":
        return { error: "ACTIVATION_REQUIRED", message: "The browser only allows full screen after a click on the PC." };
      case "youtube.search":
        return { result: { tab: { ...this.tab, context: "other", title: `${String(p.query ?? "")} - YouTube`, tab_id: 8, video_id: null, paused: null, has_next: false, has_previous: false }, results: SEARCH_RESULTS, truncated: false } };
      case "youtube.open_result": {
        const r = SEARCH_RESULTS.find((x) => x.video_id === p.video_id) ?? SEARCH_RESULTS[0]!;
        this.tab = { ...this.tab, title: r.title, video_id: r.video_id, position_seconds: 0, paused: false, is_live: r.is_live === true, duration_seconds: r.duration_seconds ?? 0 };
        return tab();
      }
      case "windows.set_volume":
        this.volume.value = Number(p.value ?? 0);
        return { result: { ...this.volume } };
      case "windows.set_muted":
        this.volume.muted = p.muted === true;
        return { result: { ...this.volume } };
      case "windows.lock":
        return { result: { accepted: true } };
      case "power.sleep":
      case "power.restart":
      case "power.shutdown":
        return { result: { accepted: true, countdown_seconds: 30, fires_at: inSeconds(30) } };
      case "power.cancel":
        return { result: { cancelled: true } };
      case "media.set_paused":
        this.spotify.status = p.paused === true ? "paused" : "playing";
        this.spotify.controls = [this.spotify.status === "playing" ? "pause" : "play", "next", "previous"];
        return { result: { session: { ...this.spotify } } };
      case "media.next":
      case "media.previous":
        return { result: { session: { ...this.spotify } } };
      case "app.launch": {
        const app = this.apps.find((a) => a.app_id === p.app_id);
        if (!app) return { error: "APP_NOT_APPROVED", message: "That app is not approved on the PC." };
        const launched = !app.running;
        app.running = true;
        if (app.windows.length === 0) app.windows = [{ window_id: `${app.app_id.replace(/[^A-Za-z0-9_-]/g, "")}1`, title: app.display_name, foreground: true }];
        return { result: { app_id: app.app_id, launched, running: true } };
      }
      case "app.close": {
        const app = this.apps.find((a) => a.app_id === target?.app_id);
        if (!app) return { error: "APP_NOT_APPROVED", message: "That app is not approved on the PC." };
        app.running = false;
        app.windows = [];
        return { result: { app_id: app.app_id, closed: true } };
      }
      case "app.focus":
        return this.windowState(target, "normal");
      case "app.minimize":
        return this.windowState(target, "minimized");
      case "app.maximize":
        return this.windowState(target, "maximized");
      case "app.restore":
        return this.windowState(target, "normal");
      case "app.set_fullscreen":
        return this.windowState(target, "normal", p.enabled === true);
      case "input.session_start":
        this.inputAccepted = 0;
        return { result: { input_session_id: INPUT_SESSION, lease_seconds: 3, input_age_budget_ms: 1500, max_batch_events: 64, pointer: true, keyboard: true, foreground_app: this.foregroundApp() } };
      case "input.session_stop":
        return { result: { stopped: true, released_holds: 0 } };
      default:
        return { error: "ACTION_UNAVAILABLE", message: "That action is not part of the design preview." };
    }
  }
}

class PreviewSocket implements WebSocketLike {
  readyState = 0;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  constructor(private readonly pc: PreviewPc) {
    pc.attach(this);
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.({});
    }, 40);
  }

  send(data: string): void {
    this.pc.handle(JSON.parse(data) as Record<string, unknown>, (frame, delay = 30) => this.deliver(frame, delay));
  }

  deliver(frame: Record<string, unknown>, delay = 30): void {
    setTimeout(() => {
      if (this.readyState === 1) this.onmessage?.({ data: JSON.stringify(frame) });
    }, delay);
  }

  close(): void {
    this.readyState = 3;
    this.pc.detach(this);
  }
}

// ---- the butler's voice ------------------------------------------------------------------------------

/** Says nothing aloud; reports each line as spoken so the page behaves as after a real reply. */
function quietSpeaker(): Speaker {
  return {
    available: true,
    unlocked: true,
    unlock: () => undefined,
    speak: (_text, options = {}) => {
      setTimeout(() => {
        options.onStart?.();
        setTimeout(() => options.onEnd?.(), 300);
      }, 0);
    },
    cancel: () => undefined,
    setListening: () => undefined,
    setPrefs: () => undefined,
    voices: () => [],
    dispose: () => undefined,
  };
}

/** A pretend microphone: tap to listen, tap again and it has heard "pause YouTube". */
function pretendMicrophone(): Recognizer {
  let handlers: RecognitionHandlers | null = null;
  return {
    available: () => true,
    start(h) {
      handlers = h;
      h.onState("starting");
      setTimeout(() => {
        h.onState("listening");
        h.onInterim("pause YouTube");
      }, 250);
    },
    stop() {
      const h = handlers;
      handlers = null;
      h?.onState("idle");
      h?.onFinal("pause YouTube", Date.now());
    },
    abort() {
      const h = handlers;
      handlers = null;
      h?.onState("idle");
    },
    tryAgain: () => undefined,
    dispose: () => undefined,
  };
}

/** Waits until the PC is selected, fresh and has listed its apps (false when that never happens). */
async function whenReady(): Promise<boolean> {
  const ready = () => {
    const id = useDevicesStore.getState().selectedPcId;
    const live = id ? useLiveStore.getState().pcs[id] : undefined;
    return id === PC && isFresh(live) && useLiveStore.getState().appsByPc[PC] !== undefined;
  };
  for (let i = 0; i < 200 && !ready(); i++) await new Promise((r) => setTimeout(r, 50));
  return ready();
}

/** Types a short conversation through the real Assistant controller once the PC is online and fresh. */
async function converse(): Promise<void> {
  if (!(await whenReady())) return;
  const butler = getAssistant();
  for (const words of ["Set the volume to 40", "Open Spotify"]) {
    await butler.handleText(words, "typed", { endedAt: Date.now() });
    for (let i = 0; i < 60 && useAssistantStore.getState().phase !== "idle"; i++) await new Promise((r) => setTimeout(r, 50));
  }
}

/** `?confirm=1`: this phone asks the PC to close Edge, and the PC answers with a confirmation challenge. */
async function askToConfirm(runtime: Runtime): Promise<void> {
  if (!(await whenReady())) return;
  await new Promise((r) => setTimeout(r, 600));
  await runtime.send({ pcId: PC, action: "app.close", target: { app_id: "msedge" }, source: "button" }).catch(() => undefined);
}

/** Enter (`/preview…`) or leave (`/preview/off`) preview mode, or start it when this tab is in it. Resolves when the app may render. */
export async function startPreviewIfAsked(): Promise<void> {
  const { pathname } = window.location;
  if (pathname === "/preview/off") {
    sessionStorage.removeItem(PREVIEW_FLAG);
    window.location.replace("/");
    return new Promise(() => undefined); // the page is leaving
  }
  if (pathname === "/preview" || pathname.startsWith("/preview/")) {
    sessionStorage.setItem(PREVIEW_FLAG, "1");
    window.location.replace(`${previewTarget(pathname)}${window.location.search}`);
    return new Promise(() => undefined);
  }
  if (sessionStorage.getItem(PREVIEW_FLAG) !== "1") return;

  const flags = new URLSearchParams(window.location.search);
  if (flags.get("offline") === "1") {
    // the shell reads navigator.onLine once on mount: the offline screen, nothing else
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
  }

  const pc = new PreviewPc();
  pc.offline = flags.get("pc") === "offline";
  const runtime = new Runtime({ relay: { socketFactory: () => new PreviewSocket(pc) } });
  configureApi({ fetchImpl: previewFetch });
  setVoiceForTests({ speaker: quietSpeaker(), recognizer: pretendMicrophone() });
  setRuntimeForTests(runtime);
  // the Ask tab, AI on and the one-time voice notice seen — for this tab only (not saved to the phone's preferences)
  useAssistantStore.setState((s) => ({ prefs: { ...s.prefs, showAssistant: true, useAi: true, voiceNoticeSeen: true } }));
  // the approved apps are asked for with the first state (as the Apps page would); the butler's conversation follows.
  // With the PC offline nothing is sent: Home's "Last command" must not show a command that ran on an offline PC.
  const unsubscribe = useLiveStore.subscribe((s) => {
    if (!s.pcs[PC]?.state) return;
    unsubscribe();
    if (pc.offline) return;
    void runtime.send({ pcId: PC, action: "app.list", source: "system" }).catch(() => undefined);
    if (flags.get("confirm") === "1") void askToConfirm(runtime);
    else void converse();
  });
  console.info("DoMe design preview: a fake account, PC and butler (development only). Leave with /preview/off.");
}

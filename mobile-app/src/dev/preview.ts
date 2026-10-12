/**
 * Design preview — DEVELOPMENT ONLY. Never part of a production bundle: `main.tsx` imports this module
 * only inside `if (import.meta.env.DEV)`, which `vite build` replaces with `false` and drops, and
 * `scripts/check-bundle.ts` fails `pnpm build` if the marker below ever shows up in `dist/`.
 *
 * Open `/preview` (or `/preview/remote`, `/preview/assistant`, …) on the dev server: this tab then runs
 * the REAL app — real pages, stores, runtime, relay client, command signing and the butler — against a
 * fake account and a fake PC living in this module, instead of cloud-api:
 *   - REST answers (`configureApi({ fetchImpl })`): a signed-in account, one PC "Living Room PC", this
 *     phone's grant, the assistant's status (AI on, 294 requests left);
 *   - the relay socket (`Runtime({ relay: { socketFactory } })`): hello → hello_ack, subscribe → online +
 *     the PC's state (YouTube playing in Edge, Spotify paused, Windows volume 55 → 40 after the
 *     conversation), and believable answers to the everyday commands, so buttons work;
 *   - the butler: a quiet speaker and a pretend microphone (tap, tap again: it "hears" "pause YouTube"),
 *     and a short conversation typed in through the real Assistant controller once the PC is ready.
 * Nothing here talks to a network. `/preview/off` leaves preview mode (it lasts for this tab only).
 */
import type { JsonValue, rest } from "@dome/protocol";

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

const ACCOUNT = "6f1c2a9e-3b4d-4e5f-8a6b-7c8d9e0f1a2b";
const CONTROLLER = "2b3c4d5e-6f70-4a81-92a3-b4c5d6e7f809";
const PC = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const GRANT = "4d5e6f70-8192-4a3b-9c4d-5e6f7a8b9c0d";
const EDGE = "bi_preview_edge";
const CREATED = "2026-09-02T18:30:00.000Z";

const now = () => new Date().toISOString();

const SESSION: rest.SessionResponse = {
  account: { id: ACCOUNT, email: "alex@example.com", display_name: "Alex", created_at: CREATED },
  csrf_token: "preview-csrf-token-0000000000000000000000",
  plan: "free",
  limits: { max_enabled_pcs: 1, max_controllers: 2, routines: false, custom_layouts: false },
  protocol_version: "1.2",
};

const pcRow = (): rest.Pc => ({ id: PC, name: "Living Room PC", enabled: true, connection: "online", last_seen: now(), created_at: CREATED, platform: "windows", agent_version: "0.1.0" });

const CONTROLLER_ROW: rest.Controller = { id: CONTROLLER, kid: "previewPhoneKid".padEnd(43, "0"), display_name: "Alex’s iPhone", status: "active", created_at: CREATED, last_seen: now() };

const GRANT_ROW: rest.Grant = { id: GRANT, controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "media", "volume", "apps", "lock", "power", "pointer", "keyboard"], created_at: CREATED };

function assistantStatus(): rest.AssistantStatusResponse {
  const start = new Date();
  start.setUTCDate(1);
  start.setUTCHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setUTCMonth(end.getUTCMonth() + 1);
  return {
    available: true,
    unavailable_reason: null,
    provider: "fake",
    turn_deadline_seconds: 30,
    consent_version: "2026-10-11",
    consent: { version: "2026-10-11", given_at: CREATED },
    allowance: { limit: 300, used: 6, period_start: start.toISOString(), period_end: end.toISOString(), exhausted: null },
  };
}

function json(status: number, body: unknown): Response {
  return new Response(body === null ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const previewFetch: FetchLike = async (input, init) => {
  const path = new URL(input, window.location.origin).pathname;
  const method = (init.method ?? "GET").toUpperCase();
  if (method === "GET" && path === "/v1/session") return json(200, SESSION);
  if (method === "GET" && path === "/v1/pcs") return json(200, { pcs: [pcRow()] });
  // this phone's row carries this browser's real key id, so Devices and Settings recognise "this phone"
  if (method === "GET" && path === "/v1/controllers") return json(200, { controllers: [{ ...CONTROLLER_ROW, kid: (await getControllerIdentity()).kid }] });
  if (method === "GET" && path === `/v1/pcs/${PC}/grants`) return json(200, { grants: [GRANT_ROW] });
  if (method === "GET" && path === "/v1/assistant") return json(200, assistantStatus());
  if (method === "GET" && path === "/v1/commands") return json(200, { commands: [] });
  if (method === "POST" && path === "/v1/auth/logout") return json(204, null);
  return json(503, { error: { code: "AI_UNAVAILABLE", message: "This part of DoMe is not in the design preview.", retryable: false } });
};

// ---- the fake PC ---------------------------------------------------------------------------------

const VIDEOS = [
  { id: "jfKfPfyJRdk", title: "Lo-fi beats to relax and study to", duration: 1900 },
  { id: "5qap5aO4i9A", title: "Midnight jazz for slow evenings", duration: 2710 },
  { id: "DWcJFNfaw9c", title: "Deep focus: ambient music for work", duration: 3600 },
];

class PreviewPc {
  private video = 0;
  private sockets = new Set<PreviewSocket>();
  tab: YoutubeTab = { browser_instance_id: EDGE, tab_id: 7, tab_token: "cHJldmlldy10YWItdG9rZW", script_attached: true, title: VIDEOS[0]!.title, video_id: VIDEOS[0]!.id, paused: false, muted: false, volume: 72, position_seconds: 724, duration_seconds: VIDEOS[0]!.duration, context: "watch", ad_showing: false, is_live: false, in_playlist: true, theater: false, fullscreen: false, has_next: true, has_previous: true, active: true };
  volume = { value: 55, muted: false };
  spotify = { session_id: "spotify", app_label: "Spotify", title: "Weightless", artist: "Marconi Union", status: "paused" as "paused" | "playing", controls: ["play", "next", "previous"] as Array<"play" | "pause" | "next" | "previous"> };
  apps: AppEntry[] = [
    { app_id: "zstream-app", display_name: "ZStream", running: true, windows: [{ window_id: "z1", title: "ZStream" }] },
    { app_id: "msedge", display_name: "Edge", running: true, windows: [{ window_id: "e1", title: "Lo-fi beats to relax and study to - YouTube — Microsoft Edge", foreground: true }] },
    { app_id: "spotify", display_name: "Spotify", running: false, windows: [] },
  ];

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
    return { remote_enabled: true, session_locked: false, extension_connected: true, platform: "windows", volume: { ...this.volume }, browser_instances: [{ browser_instance_id: EDGE, browser: "edge" }], youtube_tabs: [{ ...this.tab }], media_sessions: [{ ...this.spotify, controls: [...this.spotify.controls] }], pending_power_action: null };
  }

  pushState() {
    for (const s of this.sockets) s.deliver({ type: "state", pc_id: PC, at: now(), state: this.state() as unknown as JsonValue });
  }

  /** One controller frame in, the relay's (and the PC's) answers out. */
  handle(frame: Record<string, unknown>, reply: (f: Record<string, unknown>, delay?: number) => void) {
    switch (frame.type) {
      case "hello": {
        const versions = (frame.protocol_versions as string[] | undefined) ?? ["1.2"];
        reply({ type: "hello_ack", protocol_version: versions[0] ?? "1.2", server_time: now(), connection_id: crypto.randomUUID(), controller_id: CONTROLLER });
        return;
      }
      case "subscribe":
        reply({ type: "pc_status", pc_id: PC, connection: "online", last_seen: now(), enabled: true });
        reply({ type: "state", pc_id: PC, at: now(), state: this.state() }, 60);
        return;
      case "ping":
        reply({ type: "pong", ...(typeof frame.t === "string" ? { t: frame.t } : {}) });
        return;
      case "command": {
        const env = frame.envelope as { payload: string };
        const cmd = JSON.parse(env.payload) as { command_id: string; action: string; params?: Record<string, unknown>; target?: Record<string, unknown> | null };
        reply({ type: "ack", command_id: cmd.command_id, state: "executing", at: now() }, 40);
        const outcome = this.run(cmd.action, cmd.params ?? {});
        const base = { type: "result", command_id: cmd.command_id, origin: "agent", at: now(), duration_ms: 160 };
        reply("error" in outcome ? { ...base, state: "failed", error: { code: outcome.error, message: outcome.message, retryable: false } } : { ...base, state: "succeeded", result: outcome.result }, 220);
        setTimeout(() => this.pushState(), 320);
        return;
      }
      default:
        return;
    }
  }

  private run(action: string, p: Record<string, unknown>): { result: Record<string, unknown> } | { error: string; message: string } {
    const tab = () => ({ result: { tab: { ...this.tab } } });
    switch (action) {
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
      case "windows.set_volume":
        this.volume.value = Number(p.value ?? 0);
        return { result: { ...this.volume } };
      case "windows.set_muted":
        this.volume.muted = p.muted === true;
        return { result: { ...this.volume } };
      case "windows.lock":
        return { result: { accepted: true } };
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

/** Types a short conversation through the real Assistant controller once the PC is online and fresh. */
async function converse(): Promise<void> {
  const ready = () => {
    const id = useDevicesStore.getState().selectedPcId;
    const live = id ? useLiveStore.getState().pcs[id] : undefined;
    return id === PC && isFresh(live) && useLiveStore.getState().appsByPc[PC] !== undefined;
  };
  for (let i = 0; i < 200 && !ready(); i++) await new Promise((r) => setTimeout(r, 50));
  if (!ready()) return;
  const butler = getAssistant();
  for (const words of ["Set the volume to 40", "Open Spotify"]) {
    await butler.handleText(words, "typed", { endedAt: Date.now() });
    for (let i = 0; i < 60 && useAssistantStore.getState().phase !== "idle"; i++) await new Promise((r) => setTimeout(r, 50));
  }
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
    window.location.replace(`/app${pathname.slice("/preview".length)}${window.location.search}`);
    return new Promise(() => undefined);
  }
  if (sessionStorage.getItem(PREVIEW_FLAG) !== "1") return;

  const pc = new PreviewPc();
  const runtime = new Runtime({ relay: { socketFactory: () => new PreviewSocket(pc) } });
  configureApi({ fetchImpl: previewFetch });
  setVoiceForTests({ speaker: quietSpeaker(), recognizer: pretendMicrophone() });
  setRuntimeForTests(runtime);
  // the Ask tab, AI on and the one-time voice notice seen — for this tab only (not saved to the phone's preferences)
  useAssistantStore.setState((s) => ({ prefs: { ...s.prefs, showAssistant: true, useAi: true, voiceNoticeSeen: true } }));
  // the approved apps are asked for with the first state (as the Apps page would); the butler's conversation follows
  const unsubscribe = useLiveStore.subscribe((s) => {
    if (!s.pcs[PC]?.state) return;
    unsubscribe();
    void runtime.send({ pcId: PC, action: "app.list", source: "system" }).catch(() => undefined);
    void converse();
  });
  console.info("DoMe design preview: a fake account, PC and butler (development only). Leave with /preview/off.");
}

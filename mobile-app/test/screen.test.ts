/**
 * ScreenSessionClient over the real Runtime, a fake socket, a fake RTCPeerConnection and controllable timers
 * (docs/design/screen-view.md §4, §7, §16; version.json rules.screen_signaling, rules.pc_key_pinning): no pin → nothing
 * is asked for; the ICE request, the offer, the signed start command (60 s lifetime, stamped 1.4, the offer inside);
 * the PC-signed answer accepted only against the pinned key and applied as exactly the signed string, every §4.3
 * refusal shows nothing; heartbeats, the data-channel vocabulary, lifecycle frames, stall → keyframe → one fresh
 * session (twice at most), the connect timeout, hidden → `bye` + stop, stopped_on_pc → no restart, the relay refusals,
 * takeover, the lost socket, and no SDP in the logs or diagnostics. Device round 2 (2026-10-27): the video track
 * arrives inside setRemoteDescription and is kept until `verified`; the first-frame watchdog after DTLS (keyframes at
 * 3 s and 8 s, a fresh session at 15 s, the VP8 retry); the whitelisted stats, the timeline and the readout facts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { verifyAndParseCommand, exportPublicJwk, kidFromJwk, type relayFrames } from "@dome/protocol";

import { Runtime, setRuntimeForTests } from "../src/app/runtime.ts";
import { configureApi } from "../src/lib/api.ts";
import { getOrCreateKeyPair, writePin } from "../src/lib/controllerKey.ts";
import { buildDiagnostics } from "../src/lib/diagnostics.ts";
import { clearLogs, recentLogs } from "../src/lib/log.ts";
import { CONNECT_TIMEOUT_MS, HEARTBEAT_MS, MAX_RETRIES, NO_PICTURE_FIRST_MS, NO_PICTURE_RESTART_MS, NO_PICTURE_SECOND_MS, RELAY_HINT_MS, STALL_MS, STALL_RESTART_MS, STATS_INTERVAL_MS, type ScreenSessionState } from "../src/lib/screen.ts";
import type { ScreenMediaFacts } from "../src/lib/screenStats.ts";
import { COPY, screenStatus } from "../src/lib/screenStatus.ts";
import { useDevicesStore } from "../src/store/devices.ts";
import { useLiveStore } from "../src/store/live.ts";
import { useScreenStore } from "../src/store/screen.ts";
import { ANSWER_SDP, FakePcKey, fakePeerFactory, OFFER_SDP, safariLikeStats, SDP_SENTINEL, STATS_SECRETS, type FakePeerConnection } from "./helpers/fakePeer.ts";
import { FakeTimers } from "./helpers/fakeTimers.ts";
import { ACCOUNT, CONTROLLER, FakeSocket, flush, freshStorage, HELLO_ACK, noTimers, OTHER_PC, PC, signedIn, TS } from "./helpers/harness.ts";

const SESSION_ID = "U2NyZWVuU2Vzc2lvbjAyVF";
const OTHER_CONTROLLER = "77777777-7777-4777-8777-777777777777";
const STUN = { urls: ["stun:stun.example.net:3478"] };
const TURN = { urls: ["turn:turn.example.net:3478?transport=udp"], username: "1761048060:2222", credential: "never-logged-credential" };

let rt: Runtime;
let socket: FakeSocket;
let sockets: FakeSocket[];
let timers: FakeTimers;
let peers: FakePeerConnection[];
let knobs: ReturnType<typeof fakePeerFactory>["knobs"];
let pcKey: FakePcKey;
let states: ScreenSessionState[];

async function until(cond: () => boolean, ms = 3000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!cond() && Date.now() < deadline) await flush(2);
}

function payload(f: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse((f.envelope as { payload: string }).payload) as Record<string, unknown>;
}
function frames(type: string) {
  return socket.frames().filter((f) => f.type === type);
}
function commands(action: string) {
  return frames("command")
    .map(payload)
    .filter((p) => p.action === action);
}
function iceServers(overrides: Partial<relayFrames.RelayScreenIceServers> = {}) {
  return { type: "screen_ice_servers", pc_id: PC, ice_servers: [STUN, TURN], expires_at: "2026-10-21T13:00:00.000Z", allowance: { unlimited: false, remaining_seconds: 600, resets_at: null }, turn: "available", ...overrides };
}
function result(commandId: unknown, extra: Record<string, unknown> = {}) {
  return { type: "result", command_id: commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { screen_session_id: SESSION_ID, surfaces: [{ index: 0, width: 2560, height: 1440, primary: true }], surface_index: 0, gen: 1, audio: false, lease_seconds: 8, allowance: { unlimited: false, remaining_seconds: 540, resets_at: "2026-10-21T17:00:00.000Z" }, consent: "not_required", encoder: "h264", ...extra } };
}
function sessionFrame(event: string, reason: string, extra: Record<string, unknown> = {}) {
  return { type: "screen_session", pc_id: PC, screen_session_id: SESSION_ID, controller_id: CONTROLLER, event, reason, live_seconds: 12, at: TS, ...extra };
}

/** Start, answer the ICE request, wait for the signed start command. Returns its payload. */
async function startAndOffer(options: { takeover?: boolean } = {}): Promise<Record<string, unknown>> {
  const asked = frames("screen_ice_request").length;
  const before = commands("screen.session_start").length;
  void rt.screen.start(PC, options);
  await until(() => frames("screen_ice_request").length > asked);
  socket.receive(iceServers());
  await until(() => commands("screen.session_start").length > before);
  return commands("screen.session_start")[before]!;
}

async function answerFor(start: Record<string, unknown>, tamper?: (p: Record<string, unknown>) => void, key: FakePcKey = pcKey) {
  const envelope = await key.signAnswer({ accountId: ACCOUNT, pcId: PC, controllerId: CONTROLLER, commandId: String(start.command_id), screenSessionId: SESSION_ID, offerSdp: (start.params as { offer_sdp: string }).offer_sdp, now: new Date(timers.now), ...(tamper ? { tamper } : {}) });
  return { type: "screen_answer", pc_id: PC, envelope };
}

/** Advance the clock in 1 s steps while the `<video>` keeps decoding frames (so the stall watchdog stays quiet). */
async function watch(ms: number): Promise<void> {
  for (let left = ms; left > 0; left -= 1000) {
    await timers.advance(Math.min(1000, left));
    rt.screen.onVideoFrame(timers.now);
  }
}

/** The whole happy path up to a live picture. */
async function goLive(): Promise<{ start: Record<string, unknown>; peer: FakePeerConnection }> {
  const start = await startAndOffer();
  socket.receive(result(start.command_id));
  socket.receive(await answerFor(start));
  await until(() => rt.screen.snapshot.verified);
  const peer = peers[peers.length - 1]!;
  peer.connect();
  rt.screen.onVideoFrame(timers.now);
  expect(rt.screen.snapshot.phase).toBe("live");
  return { start, peer };
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  clearLogs();
  states = [];
  sockets = [];
  timers = new FakeTimers();
  const fp = fakePeerFactory();
  peers = fp.peers;
  knobs = fp.knobs;
  rt = new Runtime({
    relay: {
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s;
      },
      timers: noTimers,
    },
    clockIntervalMs: 600_000,
    peerFactory: fp.factory,
    screenTimers: timers,
    screenNow: () => new Date(timers.now),
  });
  setRuntimeForTests(rt);
  useScreenStore.subscribe((s) => states.push(s.session));
  rt.start();
  for (let i = 0; i < 50 && sockets.length === 0; i++) await flush();
  socket = sockets[0]!;
  socket.open();
  socket.receive(HELLO_ACK);
  pcKey = await FakePcKey.create();
  await writePin(pcKey.pin(PC));
});

afterEach(() => {
  rt.stop();
  setRuntimeForTests(null);
  configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
});

describe("ScreenSessionClient: starting", () => {
  it("without a pinned PC key nothing is sent at all: SCREEN_PC_UNVERIFIED ('pair again')", async () => {
    const state = await rt.screen.start(OTHER_PC);
    expect(state.phase).toBe("failed");
    expect(state.problem?.code).toBe("SCREEN_PC_UNVERIFIED");
    expect(frames("screen_ice_request")).toHaveLength(0);
    expect(frames("command")).toHaveLength(0);
    expect(peers).toHaveLength(0);
  });

  it("asks for ICE servers on the bound socket, builds a receive-only offer with the servers it got, and signs the offer into screen.session_start (lifetime 60 s, stamp 1.4)", async () => {
    const start = await startAndOffer();
    expect(frames("screen_ice_request")[0]).toEqual({ type: "screen_ice_request", pc_id: PC });
    const peer = peers[0]!;
    expect(peer.config.iceServers).toEqual([STUN, TURN]);
    expect(peer.transceivers.map((t) => `${t.kind}:${t.direction}`)).toEqual(["video:recvonly", "audio:recvonly"]);
    expect(peer.channels[0]!.label).toBe("dome-screen-v1");
    expect(peer.channels[0]!.init).toEqual({ ordered: true });
    expect(peer.iceGatheringState).toBe("complete"); // vanilla ICE: gathering completed before the offer was sent
    expect(start.protocol_version).toBe("1.4");
    expect(start.params).toEqual({ offer_sdp: peer.localDescription!.sdp, surface: "primary", audio: false, takeover: false });
    const issued = Date.parse(start.issued_at as string);
    expect(Date.parse(start.expires_at as string) - issued).toBe(60_000);
    // the command verifies with the real verifier under this phone's key: what the relay and the PC will check
    const pair = await getOrCreateKeyPair();
    const jwk = await exportPublicJwk(pair.publicKey);
    const kid = await kidFromJwk(jwk);
    const verified = await verifyAndParseCommand(frames("command")[0]!.envelope, (k) => (k === kid ? { controllerId: CONTROLLER, accountId: ACCOUNT, jwk } : null), new Date(issued));
    expect(verified.spec.name).toBe("screen.session_start");
    expect(rt.screen.snapshot.phase).toBe("starting");
    expect(rt.screen.snapshot.turn).toBe("available");
    expect(rt.screen.snapshot.allowance).toMatchObject({ unlimited: false, remainingSeconds: 600 });
  });

  it("a relay refusal of the ICE request is the failure state with its code and detail (SCREEN_VIEW_DISABLED, allowance with resets_at)", async () => {
    void rt.screen.start(PC);
    await until(() => frames("screen_ice_request").length === 1);
    socket.receive({ type: "error", error: { code: "SCREEN_VIEW_DISABLED", message: "off", retryable: false }, ref_pc_id: PC });
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_VIEW_DISABLED");
    expect(peers).toHaveLength(0);
    expect(frames("command")).toHaveLength(0);

    void rt.screen.start(PC);
    await until(() => frames("screen_ice_request").length === 2);
    socket.receive({ type: "error", error: { code: "SCREEN_ALLOWANCE_EXHAUSTED", message: "used", retryable: false, detail: { resets_at: "2026-10-21T17:12:00.000Z" } }, ref_pc_id: PC });
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_ALLOWANCE_EXHAUSTED");
    expect(rt.screen.snapshot.allowance).toMatchObject({ remainingSeconds: 0, resetsAt: "2026-10-21T17:12:00.000Z" });
    // an input-batch rejection naming the PC is never mistaken for the ICE refusal
    void rt.screen.start(PC);
    await until(() => frames("screen_ice_request").length === 3);
    socket.receive({ type: "error", error: { code: "INPUT_STALE", message: "x", retryable: false }, ref_pc_id: PC, ref_input_session_id: "A".repeat(22) });
    await flush();
    expect(rt.screen.snapshot.phase).toBe("ice");
  });

  it("an offer the strict check refuses is SCREEN_OFFER_INVALID before anything is signed", async () => {
    knobs.offerSdp = "v=0\nbare line feeds\n";
    void rt.screen.start(PC);
    await until(() => frames("screen_ice_request").length === 1);
    socket.receive(iceServers());
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_OFFER_INVALID");
    expect(commands("screen.session_start")).toHaveLength(0);
  });

  it("SCREEN_SESSION_OWNED marks the session as owned; Take over sends takeover: true", async () => {
    const start = await startAndOffer();
    socket.receive({ type: "result", command_id: start.command_id, origin: "agent", state: "failed", at: TS, duration_ms: 5, error: { code: "SCREEN_SESSION_OWNED", message: "owned", retryable: false } });
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.owned).toBe(true);
    expect(peers[0]!.closed).toBe(true);
    const again = await startAndOffer({ takeover: true });
    expect((again.params as { takeover: boolean }).takeover).toBe(true);
  });

  it("the ack without a result marks consent as waiting after 2 s (someone at the PC is asked); the result clears it", async () => {
    const start = await startAndOffer();
    socket.receive({ type: "ack", command_id: start.command_id, state: "executing", at: TS });
    await flush();
    expect(rt.screen.snapshot.consentWaiting).toBe(false);
    await timers.advance(2100);
    expect(rt.screen.snapshot.consentWaiting).toBe(true);
    const status = screenStatus({ state: rt.screen.snapshot, switchOn: "on", grant: undefined, pin: pcKey.pin(PC), pcName: "Office PC", pcOnline: true, remoteEnabled: true, relayUp: true, now: timers.now });
    expect(status.title).toMatch(/^Waiting for someone at the PC to allow this… \(\d+\)$/);
    socket.receive(result(start.command_id, { consent: "granted" }));
    await flush();
    expect(rt.screen.snapshot.consentWaiting).toBe(false);
    expect(rt.screen.snapshot.consent).toBe("granted");
    expect(rt.screen.snapshot.sessionId).toBe(SESSION_ID);
  });

  it("a refusal from the PC (consent declined, capture unavailable, incompatible agent) closes the peer and shows the code", async () => {
    for (const code of ["SCREEN_CONSENT_DECLINED", "SCREEN_CAPTURE_UNAVAILABLE", "PROTOCOL_INCOMPATIBLE"]) {
      const start = await startAndOffer();
      socket.receive({ type: "result", command_id: start.command_id, origin: code === "PROTOCOL_INCOMPATIBLE" ? "relay" : "agent", state: "failed", at: TS, duration_ms: 5, error: { code, message: "no", retryable: false } });
      await until(() => rt.screen.snapshot.phase === "failed");
      expect(rt.screen.snapshot.problem?.code).toBe(code);
      expect(peers[peers.length - 1]!.closed).toBe(true);
      expect(peers[peers.length - 1]!.remoteDescriptions).toHaveLength(0);
    }
  });
});

describe("ScreenSessionClient: the PC-signed answer", () => {
  it("accepts the answer only after verifying it against the pinned key and applies exactly the signed SDP; the result may arrive after it", async () => {
    const start = await startAndOffer();
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.verified);
    const peer = peers[0]!;
    expect(peer.remoteDescriptions).toEqual([{ type: "answer", sdp: ANSWER_SDP }]);
    expect(rt.screen.snapshot.phase).toBe("connecting");
    socket.receive(result(start.command_id));
    await flush();
    expect(rt.screen.snapshot.sessionId).toBe(SESSION_ID);
    expect(rt.screen.snapshot.surfaces).toEqual([{ index: 0, width: 2560, height: 1440, primary: true }]);
    expect(rt.screen.snapshot.encoder).toBe("h264");
    expect(rt.screen.snapshot.geometry).toMatchObject({ gen: 1, width: 2560, height: 1440 });
  });

  it("an answer signed by a key other than the pin is the hard stop: nothing is applied, the session is abandoned and the PC is told to stop", async () => {
    const other = await FakePcKey.create();
    const start = await startAndOffer();
    socket.receive(result(start.command_id));
    socket.receive(await answerFor(start, undefined, other));
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_ANSWER_INVALID");
    expect(rt.screen.snapshot.problem?.detail?.reason).toBe("kid is not the pinned PC key");
    expect(rt.screen.snapshot.verified).toBe(false);
    expect(rt.screen.snapshot.stream).toBeNull();
    expect(peers[0]!.remoteDescriptions).toHaveLength(0);
    expect(peers[0]!.closed).toBe(true);
    await until(() => commands("screen.session_stop").length === 1);
    expect(commands("screen.session_stop")[0]!.params).toEqual({ screen_session_id: SESSION_ID });
    const status = screenStatus({ state: rt.screen.snapshot, switchOn: "on", grant: undefined, pin: pcKey.pin(PC), pcName: null, pcOnline: true, remoteEnabled: true, relayUp: true, now: timers.now });
    expect(status.title).toBe("This PC's identity changed. Pair again.");
  });

  it("every other §4.3 refusal shows nothing: wrong account, wrong command, foreign offer digest, stale window, altered SDP, mismatched fingerprints, a newer stamp", async () => {
    const cases: Array<[string, (p: Record<string, unknown>) => void, string]> = [
      ["account", (p) => (p.account_id = OTHER_CONTROLLER), "SCREEN_ANSWER_INVALID"],
      ["controller", (p) => (p.controller_id = OTHER_CONTROLLER), "SCREEN_ANSWER_INVALID"],
      ["command", (p) => (p.command_id = "9a1c2d3e-4a5b-4c6d-8e7f-901234567890"), "SCREEN_ANSWER_INVALID"],
      ["offer digest", (p) => (p.offer_digest = "A".repeat(43)), "SCREEN_ANSWER_INVALID"],
      ["window", (p) => ((p.issued_at = "2026-10-21T10:00:00.000Z"), (p.expires_at = "2026-10-21T10:01:00.000Z")), "SCREEN_ANSWER_INVALID"],
      ["fingerprints", (p) => (p.answer_sdp = ANSWER_SDP.replace("80:81:82:83", "81:81:82:83")), "SCREEN_ANSWER_INVALID"],
      ["bare LF", (p) => (p.answer_sdp = "v=0\na=fingerprint:sha-256 " + "80:81:82:83:84:85:86:87:88:89:8A:8B:8C:8D:8E:8F:90:91:92:93:94:95:96:97:98:99:9A:9B:9C:9D:9E:9F" + "\n"), "SCREEN_ANSWER_INVALID"],
      ["newer MINOR", (p) => (p.protocol_version = "1.5"), "PROTOCOL_INCOMPATIBLE"],
    ];
    for (const [name, tamper, code] of cases) {
      const start = await startAndOffer();
      socket.receive(await answerFor(start, tamper));
      await until(() => rt.screen.snapshot.phase === "failed");
      expect(rt.screen.snapshot.problem?.code, name).toBe(code);
      expect(peers[peers.length - 1]!.remoteDescriptions, name).toHaveLength(0);
      expect(rt.screen.snapshot.stream, name).toBeNull();
    }
    // the signature itself: an envelope whose payload was altered after signing
    const start = await startAndOffer();
    const good = await answerFor(start);
    const broken = { ...good, envelope: { ...good.envelope, payload: good.envelope.payload.replace(SESSION_ID, "U2NyZWVuU2Vzc2lvbjAzVF") } };
    socket.receive(broken);
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_ANSWER_INVALID");
    expect(peers[peers.length - 1]!.remoteDescriptions).toHaveLength(0);
  });

  it("an answer whose session id differs from the result's is refused", async () => {
    const start = await startAndOffer();
    socket.receive(result(start.command_id, { screen_session_id: "U2NyZWVuU2Vzc2lvbjAzVF" }));
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_ANSWER_INVALID");
    expect(peers[0]!.remoteDescriptions).toHaveLength(0);
  });

  it("a second answer for the same start is dropped", async () => {
    const start = await startAndOffer();
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.verified);
    socket.receive(await answerFor(start));
    await flush(5);
    expect(peers[0]!.remoteDescriptions).toHaveLength(1);
    expect(rt.screen.snapshot.phase).toBe("connecting");
  });
});

describe("ScreenSessionClient: live", () => {
  it("DTLS connected → the stream; the first decoded frame → live; heartbeats every 2 s on the channel; the PC's messages are applied", async () => {
    const { peer } = await goLive();
    expect(rt.screen.snapshot.stream).toEqual({ id: "fake-stream" });
    expect(peer.channel.messages()).toEqual([{ type: "hb" }]);
    await watch(HEARTBEAT_MS * 3);
    expect(peer.channel.messages().filter((m) => m.type === "hb")).toHaveLength(4);
    peer.channel.receive({ type: "geometry", gen: 2, surface_index: 0, width: 2560, height: 1440, rotation: 0, reserved: [{ x: 0, y: 0, w: 65535, h: 1200 }] });
    peer.channel.receive({ type: "allowance", unlimited: false, remaining_seconds: 480, resets_at: "2026-10-21T17:00:00.000Z" });
    peer.channel.receive({ type: "restricted", restricted: true, reason: "elevated" });
    peer.channel.receive({ type: "state", state: "sending", encoder: "vp8", data_saver: true });
    const s = rt.screen.snapshot;
    expect(s.geometry).toMatchObject({ gen: 2, reserved: [{ x: 0, y: 0, w: 65535, h: 1200 }] });
    expect(s.allowance).toMatchObject({ remainingSeconds: 480, resetsAt: "2026-10-21T17:00:00.000Z" });
    expect(s.restricted).toEqual({ restricted: true, reason: "elevated" });
    expect(s.encoder).toBe("vp8");
    expect(s.dataSaver).toBe(true);
    socket.receive(sessionFrame("started", "started", { path: "relayed", live_seconds: 0 }));
    await flush();
    expect(rt.screen.snapshot.path).toBe("relayed");
  });

  it("drops anything outside the closed data-channel vocabulary and never runs or shows it", async () => {
    const { peer } = await goLive();
    const before = rt.screen.snapshot;
    for (const m of [{ type: "input", events: [{ type: "pointer_button", button: "left", action: "click" }] }, { type: "hb" }, { type: "geometry", gen: 0 }, "not json", "[1,2]", JSON.stringify({ type: "state", state: "sending" }) + " ", { type: "roi", gen_seen: 1, rect: {} }, { type: "allowance", unlimited: true, remaining_seconds: null, resets_at: null, extra: 1 }]) {
      peer.channel.receive(m);
    }
    const after = rt.screen.snapshot;
    expect(after.geometry).toEqual(before.geometry);
    expect(after.allowance).toEqual(before.allowance);
    expect(after.phase).toBe("live");
    expect(JSON.stringify(recentLogs())).not.toContain("pointer_button");
  });

  it("the secure desktop pauses the picture (state message or screen_session paused) and resumes", async () => {
    const { peer } = await goLive();
    peer.channel.receive({ type: "state", state: "paused", reason: "secure_desktop" });
    expect(rt.screen.snapshot.phase).toBe("paused");
    expect(rt.screen.snapshot.pauseReason).toBe("secure_desktop");
    peer.channel.receive({ type: "state", state: "sending" });
    expect(rt.screen.snapshot.phase).toBe("live");
    socket.receive(sessionFrame("paused", "indicator"));
    await flush();
    expect(rt.screen.snapshot.phase).toBe("paused");
    expect(rt.screen.snapshot.pauseReason).toBe("indicator");
    socket.receive(sessionFrame("resumed", "indicator", { path: "direct" }));
    await flush();
    expect(rt.screen.snapshot.phase).toBe("live");
    // a pause is not a stall: the watchdog asks for nothing while the PC says paused
    peer.channel.receive({ type: "state", state: "paused", reason: "secure_desktop" });
    const sentBefore = peer.channel.messages().filter((m) => m.type === "keyframe").length;
    await timers.advance(STALL_RESTART_MS + 2000);
    expect(peer.channel.messages().filter((m) => m.type === "keyframe")).toHaveLength(sentBefore);
    expect(rt.screen.snapshot.phase).toBe("paused");
  });

  it("pause causes are a set: a UAC prompt answered while a bar is missing keeps the picture paused, in either order", async () => {
    await goLive();
    socket.receive(sessionFrame("paused", "secure_desktop"));
    socket.receive(sessionFrame("paused", "indicator"));
    await flush();
    expect(rt.screen.snapshot).toMatchObject({ phase: "paused", pauseReason: "secure_desktop" });
    socket.receive(sessionFrame("resumed", "secure_desktop", { path: "direct" }));
    await flush();
    expect(rt.screen.snapshot).toMatchObject({ phase: "paused", pcState: "paused", pauseReason: "indicator" });
    socket.receive(sessionFrame("resumed", "indicator", { path: "direct" }));
    await flush();
    expect(rt.screen.snapshot).toMatchObject({ phase: "live", pcState: "sending", pauseReason: null });
    // the other order
    socket.receive(sessionFrame("paused", "indicator"));
    socket.receive(sessionFrame("paused", "secure_desktop"));
    await flush();
    socket.receive(sessionFrame("resumed", "indicator", { path: "direct" }));
    await flush();
    expect(rt.screen.snapshot).toMatchObject({ phase: "paused", pauseReason: "secure_desktop" });
    socket.receive(sessionFrame("resumed", "secure_desktop", { path: "direct" }));
    await flush();
    expect(rt.screen.snapshot.phase).toBe("live");
  });

  it("no frame for 2 s while sending asks for a keyframe; 5 s → one fresh signed session, at most twice, then the failure state", async () => {
    const { peer } = await goLive();
    await timers.advance(STALL_MS + 100);
    expect(rt.screen.snapshot.stalled).toBe(true);
    expect(peer.channel.messages().some((m) => m.type === "keyframe")).toBe(true);
    rt.screen.onVideoFrame(timers.now); // a frame arrived: not stalled any more
    expect(rt.screen.snapshot.stalled).toBe(false);
    // now a real stall
    await timers.advance(STALL_RESTART_MS + 1100);
    await until(() => frames("screen_ice_request").length === 2);
    expect(peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    expect(peer.closed).toBe(true);
    await until(() => commands("screen.session_stop").length === 1);
    expect(rt.screen.snapshot.retries).toBe(1);
    expect(rt.screen.snapshot.phase).toBe("ice");
    // second and third stalls
    for (let n = 2; n <= MAX_RETRIES + 1; n++) {
      socket.receive(iceServers());
      await until(() => commands("screen.session_start").length === n);
      const start = commands("screen.session_start")[n - 1]!;
      socket.receive(result(start.command_id));
      socket.receive(await answerFor(start));
      await until(() => rt.screen.snapshot.verified);
      peers[peers.length - 1]!.connect();
      rt.screen.onVideoFrame(timers.now);
      expect(rt.screen.snapshot.phase).toBe("live");
      await timers.advance(STALL_RESTART_MS + 1100);
      if (n <= MAX_RETRIES) await until(() => frames("screen_ice_request").length === n + 1);
    }
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_CONNECTION_FAILED");
    expect(frames("screen_ice_request")).toHaveLength(MAX_RETRIES + 1);
  });

  it("a failed transport while live is the same fresh-session path; while connecting it is SCREEN_CONNECTION_FAILED", async () => {
    const { peer } = await goLive();
    peer.fail();
    await until(() => frames("screen_ice_request").length === 2);
    expect(rt.screen.snapshot.retries).toBe(1);
    socket.receive(iceServers());
    await until(() => commands("screen.session_start").length === 2);
    const start = commands("screen.session_start")[1]!;
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.verified);
    peers[1]!.fail();
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_CONNECTION_FAILED");
  });

  it("no DTLS within 20 s of the answer is SCREEN_CONNECTION_FAILED; after 5 s with a relay server available the copy says it is being tried", async () => {
    const start = await startAndOffer();
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.verified);
    await timers.advance(RELAY_HINT_MS + 10);
    expect(rt.screen.snapshot.tryingRelay).toBe(true);
    await timers.advance(CONNECT_TIMEOUT_MS);
    expect(rt.screen.snapshot.phase).toBe("failed");
    expect(rt.screen.snapshot.problem?.code).toBe("SCREEN_CONNECTION_FAILED");
    expect(peers[0]!.closed).toBe(true);
  });

  it("the PC's `ended` frames end the session with the reason: stopped_on_pc never resumes by itself, viewer_gone offers Start again", async () => {
    await goLive();
    socket.receive(sessionFrame("ended", "stopped_on_pc", { live_seconds: 73 }));
    await flush();
    let s = rt.screen.snapshot;
    expect(s.phase).toBe("ended");
    expect(s.endReason).toBe("stopped_on_pc");
    expect(s.liveSeconds).toBe(73);
    expect(s.resume).toBe("none");
    expect(s.stream).toBeNull();
    expect(peers[0]!.closed).toBe(true);
    await goLive();
    socket.receive(sessionFrame("ended", "viewer_gone"));
    await flush();
    s = rt.screen.snapshot;
    expect(s.endReason).toBe("viewer_gone");
    expect(s.resume).toBe("manual");
    // another phone's lifecycle frames only mark ownership
    socket.receive({ ...sessionFrame("started", "started", { path: "direct" }), controller_id: OTHER_CONTROLLER, screen_session_id: "T".repeat(22) });
    await flush();
    expect(rt.screen.snapshot.otherViewer).toBe(true);
    socket.receive({ ...sessionFrame("ended", "stopped"), controller_id: OTHER_CONTROLLER, screen_session_id: "T".repeat(22) });
    await flush();
    expect(rt.screen.snapshot.otherViewer).toBe(false);
  });

  it("hidden → `bye` on the channel, the signed screen.session_stop, the peer closed, and the page may resume once (not after a consent the PC granted)", async () => {
    const { peer } = await goLive();
    await rt.screen.onHidden();
    expect(peer.channel.messages()[peer.channel.messages().length - 1]).toEqual({ type: "bye" });
    expect(peer.closed).toBe(true);
    expect(rt.screen.snapshot.phase).toBe("idle");
    expect(rt.screen.snapshot.resume).toBe("auto");
    await until(() => commands("screen.session_stop").length === 1);
    expect(rt.screen.takeResume()).toBe("auto");
    expect(rt.screen.takeResume()).toBe("none");
    // ask mode: the PC granted consent for this start → only a manual Resume
    const start = await startAndOffer();
    socket.receive(result(start.command_id, { consent: "granted" }));
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.verified);
    peers[1]!.connect();
    rt.screen.onVideoFrame(timers.now);
    await rt.screen.onHidden();
    expect(rt.screen.snapshot.resume).toBe("manual");
  });

  it("Stop from the phone: bye, the stop command, 'ended · stopped' with the seconds watched; a PC switch goes idle", async () => {
    const { peer } = await goLive();
    await watch(192_000);
    await rt.screen.stop("stopped");
    expect(peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    expect(rt.screen.snapshot.phase).toBe("ended");
    expect(rt.screen.snapshot.endReason).toBe("stopped");
    expect(rt.screen.snapshot.liveSeconds).toBe(192);
    await until(() => commands("screen.session_stop").length === 1);
    await goLive();
    await rt.screen.stop("pc_switch");
    expect(rt.screen.snapshot.phase).toBe("idle");
    expect(rt.screen.snapshot.resume).toBe("none");
  });

  it("the runtime stops a live picture when another PC is selected and on sign-out (bye + the signed stop each time)", async () => {
    const { peer } = await goLive();
    useDevicesStore.setState({ selectedPcId: OTHER_PC });
    await until(() => rt.screen.snapshot.phase === "idle");
    expect(peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    await until(() => commands("screen.session_stop").length === 1);
    useDevicesStore.setState({ selectedPcId: PC });
    const second = await goLive();
    configureApi({ fetchImpl: async () => new Response(null, { status: 204 }) }); // the logout call itself
    await rt.signOut();
    expect(second.peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    expect(second.peer.closed).toBe(true);
    expect(rt.screen.snapshot.phase).toBe("idle");
    expect(useScreenStore.getState().session.phase).toBe("idle");
  });

  it("the relay socket lost while live keeps the picture and flags clicks as paused; lost while starting fails the start", async () => {
    await goLive();
    socket.serverClose(1006);
    await flush();
    expect(rt.screen.snapshot.phase).toBe("live");
    expect(rt.screen.snapshot.relayDown).toBe(true);
    await rt.screen.stop("leave");
    const s2 = sockets[sockets.length - 1]!;
    if (s2 !== socket) {
      socket = s2;
      socket.open();
      socket.receive(HELLO_ACK);
    }
    await until(() => rt.relay.isOpen);
    void rt.screen.start(PC);
    await until(() => frames("screen_ice_request").length >= 1);
    socket.serverClose(1006);
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem?.code).toBe("PC_RECONNECTING");
  });

  it("a stale geometry generation reported by the PC holds taps until the next geometry message", async () => {
    const { peer } = await goLive();
    expect(rt.screen.geometry.current(timers.now)).not.toBeNull();
    rt.screen.onInputRejected("SCREEN_GEOMETRY_CHANGED");
    expect(rt.screen.geometry.current(timers.now)).toBeNull();
    peer.channel.receive({ type: "geometry", gen: 3, surface_index: 0, width: 1920, height: 1080, rotation: 0, reserved: [] });
    expect(rt.screen.geometry.current(timers.now)?.gen).toBe(3);
  });
});

/** Start, the result and the PC-signed answer, up to `verified` (the track arrives inside setRemoteDescription). */
async function answered(): Promise<{ start: Record<string, unknown>; peer: FakePeerConnection }> {
  const start = await startAndOffer();
  socket.receive(result(start.command_id));
  socket.receive(await answerFor(start));
  await until(() => rt.screen.snapshot.verified);
  return { start, peer: peers[peers.length - 1]! };
}

const events = () => recentLogs().map((l) => l.event);
const logged = (event: string) => recentLogs().filter((l) => l.event === event);
const keyframes = (peer: FakePeerConnection) => peer.channel.messages().filter((m) => m.type === "keyframe").length;
const statusOf = (state: ScreenSessionState) => screenStatus({ state, switchOn: "on", grant: undefined, pin: pcKey.pin(PC), pcName: "Office PC", pcOnline: true, remoteEnabled: true, relayUp: true, now: timers.now });

describe("ScreenSessionClient: the video track in the browsers' order (device round 2)", () => {
  it("the track fires inside setRemoteDescription, before `verified`: it is kept and shown together with `verified`; one frame then makes it live", async () => {
    const { peer } = await answered();
    // the fake dispatched `track` before its setRemoteDescription resolved (the order WebKit and Chromium use)
    expect(logged("screen.track")[0]?.fields).toEqual({ streams: 1, answer_applied: true, verified: false });
    expect(events()).not.toContain("screen.track_before_verification_dropped");
    expect(rt.screen.snapshot.stream).toEqual({ id: "fake-stream" });
    expect(rt.screen.snapshot.phase).toBe("connecting");
    // nothing was shown before verification: every published state with a stream is a verified one
    expect(states.filter((st) => st.stream !== null).every((st) => st.verified)).toBe(true);
    expect(events().indexOf("screen.track")).toBeLessThan(events().indexOf("screen.answer_verified"));
    peer.connect();
    expect(statusOf(rt.screen.snapshot)).toMatchObject({ kind: "waiting_picture", title: COPY.waitingPicture });
    rt.screen.onVideoFrame(timers.now);
    expect(rt.screen.snapshot.phase).toBe("live");
    expect(events()).toEqual(expect.arrayContaining(["screen.dtls_connected", "screen.dc_open", "screen.first_frame", "screen.live"]));
  });

  it("a track whose `streams` is empty (a sender without a=msid) still becomes a stream of its own", async () => {
    class FakeMediaStream {
      constructor(readonly tracks: unknown[]) {}
    }
    vi.stubGlobal("MediaStream", FakeMediaStream);
    try {
      const start = await startAndOffer();
      peers[0]!.trackStreams = [];
      socket.receive(await answerFor(start));
      await until(() => rt.screen.snapshot.verified);
      expect(logged("screen.track")[0]?.fields).toMatchObject({ streams: 0 });
      const shown = rt.screen.snapshot.stream as unknown as FakeMediaStream;
      expect(shown).toBeInstanceOf(FakeMediaStream);
      expect(shown.tracks).toEqual([{ kind: "video" }]);
    } finally {
      vi.unstubAllGlobals();
    }
    // a browser that cannot build one says so, and shows nothing rather than something unverified
    const start = await startAndOffer();
    peers[1]!.trackStreams = [];
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.verified);
    expect(events()).toContain("screen.track_without_stream");
    expect(rt.screen.snapshot.stream).toBeNull();
  });

  it("a track before the signed answer was applied is dropped; audio tracks are ignored; the verified answer's track is kept", async () => {
    const start = await startAndOffer();
    const peer = peers[0]!;
    peer.fireTrack(); // no answer applied yet: cannot be ours
    expect(events()).toContain("screen.track_before_verification_dropped");
    expect(rt.screen.snapshot.stream).toBeNull();
    peer.fireTrack([{ id: "audio-stream" } as unknown as MediaStream], "audio");
    expect(logged("screen.track")).toHaveLength(1); // audio is not even logged
    socket.receive(await answerFor(start));
    await until(() => rt.screen.snapshot.verified);
    expect(rt.screen.snapshot.stream).toEqual({ id: "fake-stream" });
    // a late video track after verification replaces the stream at once (still only after verification)
    peer.fireTrack([{ id: "late-stream" } as unknown as MediaStream]);
    expect(rt.screen.snapshot.stream).toEqual({ id: "late-stream" });
  });

  it("a stop while setRemoteDescription is pending forgets the kept stream", async () => {
    const start = await startAndOffer();
    const peer = peers[0]!;
    let release: () => void = () => undefined;
    const original = peer.setRemoteDescription.bind(peer);
    peer.setRemoteDescription = async (d) => {
      await original(d); // the track fires here
      await new Promise<void>((r) => (release = r));
    };
    socket.receive(await answerFor(start));
    await until(() => logged("screen.track").length === 1);
    await rt.screen.stop("stopped");
    release();
    await flush(5);
    expect(rt.screen.snapshot).toMatchObject({ phase: "ended", verified: false, stream: null });
  });
});

describe("ScreenSessionClient: the first-frame watchdog after DTLS (device round 2)", () => {
  it("DTLS up and no frame: 'Connected. Waiting for the picture…', a keyframe request and screen.no_picture at 3 s and 8 s, a fresh session at 15 s", async () => {
    knobs.stats = safariLikeStats({ packets: 0, received: 0, decoded: 0, keyDecoded: 0 });
    const { peer } = await answered();
    expect(statusOf(rt.screen.snapshot).title).toBe(COPY.connecting);
    peer.connect();
    expect(rt.screen.snapshot.transportConnected).toBe(true);
    expect(statusOf(rt.screen.snapshot)).toMatchObject({ kind: "waiting_picture", title: COPY.waitingPicture });
    await timers.advance(NO_PICTURE_FIRST_MS - 100);
    expect(keyframes(peer)).toBe(0);
    await timers.advance(200);
    expect(keyframes(peer)).toBe(1);
    expect(logged("screen.no_picture")[0]?.fields).toMatchObject({ step: 1, keyframe_sent: true, stream: true, rtp_packets: 0, frames_decoded: 0, codec: "video/H264", pair: "succeeded", local_type: "srflx", remote_type: "host", proto: "udp" });
    await timers.advance(NO_PICTURE_SECOND_MS - NO_PICTURE_FIRST_MS);
    expect(keyframes(peer)).toBe(2);
    expect(logged("screen.no_picture")).toHaveLength(2);
    expect(rt.screen.snapshot.phase).toBe("connecting");
    await timers.advance(NO_PICTURE_RESTART_MS - NO_PICTURE_SECOND_MS);
    await until(() => frames("screen_ice_request").length === 2);
    expect(logged("screen.no_picture")).toHaveLength(3);
    expect(keyframes(peer)).toBe(2); // the third step restarts instead
    expect(peer.channel.messages().some((m) => m.type === "bye")).toBe(true);
    await until(() => commands("screen.session_stop").length === 1);
    expect(rt.screen.snapshot).toMatchObject({ phase: "ice", retries: 1, transportConnected: false });
    // nothing in the log names an address, a port or the certificate
    const text = JSON.stringify(recentLogs());
    for (const secret of STATS_SECRETS) expect(text, secret).not.toContain(secret);
  });

  it("the fresh sessions count toward MAX_RETRIES; then SCREEN_CONNECTION_FAILED {cause: no_picture} with its own copy", async () => {
    let { peer } = await answered();
    peer.connect();
    for (let n = 1; n <= MAX_RETRIES; n++) {
      await timers.advance(NO_PICTURE_RESTART_MS);
      await until(() => frames("screen_ice_request").length === n + 1);
      socket.receive(iceServers());
      await until(() => commands("screen.session_start").length === n + 1);
      const start = commands("screen.session_start")[n]!;
      socket.receive(result(start.command_id));
      socket.receive(await answerFor(start));
      await until(() => rt.screen.snapshot.verified);
      peer = peers[peers.length - 1]!;
      peer.connect();
    }
    await timers.advance(NO_PICTURE_RESTART_MS);
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(rt.screen.snapshot.problem).toEqual({ code: "SCREEN_CONNECTION_FAILED", detail: { cause: "no_picture" } });
    expect(frames("screen_ice_request")).toHaveLength(MAX_RETRIES + 1);
    expect(statusOf(rt.screen.snapshot)).toMatchObject({ kind: "no_picture", title: COPY.noPicture, code: "SCREEN_CONNECTION_FAILED", action: "retry" });
    expect(logged("screen.retries_exhausted")[0]?.fields).toEqual({ cause: "no_picture" });
  });

  it("a frame before 15 s ends the watchdog: no keyframe request, no restart", async () => {
    const { peer } = await answered();
    peer.connect();
    await timers.advance(2_000);
    rt.screen.onVideoFrame(timers.now);
    expect(rt.screen.snapshot.phase).toBe("live");
    expect(logged("screen.first_frame")[0]?.fields).toMatchObject({ ms_since_dtls: 2_000 });
    await watch(NO_PICTURE_RESTART_MS + 5_000);
    expect(keyframes(peer)).toBe(0);
    expect(logged("screen.no_picture")).toHaveLength(0);
    expect(frames("screen_ice_request")).toHaveLength(1);
  });

  it("frames decoded but a paused <video> without a frame callback: 'Tap to show the picture' at 3 s; the tap clears it", async () => {
    knobs.stats = safariLikeStats({ decoded: 12 });
    const media: ScreenMediaFacts = { at: 0, ready_state: 1, network_state: 2, paused: true, ended: false, width: 0, height: 0, current_time: 0, error: null, total_frames: 0, dropped_frames: 0, play: "rejected:NotAllowedError", rvfc: true, frame_ticks: 0, first_tick_ms: null, stream_active: true, track_state: "live", track_muted: false, track_enabled: true };
    rt.screen.setMediaProbe(() => media);
    const { peer } = await answered();
    peer.connect();
    await timers.advance(NO_PICTURE_FIRST_MS);
    expect(rt.screen.snapshot.playHint).toBe(true);
    expect(logged("screen.no_picture")[0]?.fields).toMatchObject({ frames_decoded: 12, paused: true, play: "rejected:NotAllowedError", frame_ticks: 0, ready: 1 });
    rt.screen.dismissPlayHint();
    expect(rt.screen.snapshot.playHint).toBe(false);
    rt.screen.onVideoFrame(timers.now);
    expect(rt.screen.snapshot.phase).toBe("live");
    rt.screen.setMediaProbe(null);
  });
});

describe("ScreenSessionClient: what holds the first-frame watchdog (review of device round 2)", () => {
  const pausedMedia = (over: Partial<ScreenMediaFacts> = {}): ScreenMediaFacts => ({ at: 0, ready_state: 1, network_state: 2, paused: true, ended: false, width: 0, height: 0, current_time: 0, error: null, total_frames: 0, dropped_frames: 0, play: "rejected:NotAllowedError", rvfc: true, frame_ticks: 0, first_tick_ms: null, stream_active: true, track_state: "live", track_muted: false, track_enabled: true, ...over });
  afterEach(() => rt.screen.setMediaProbe(null));

  it("a PC pause before the first frame (a UAC prompt, a missing bar) asks for nothing and restarts nothing; the clock goes on after the resume", async () => {
    const { peer } = await answered();
    peer.connect();
    await timers.advance(1_000);
    peer.channel.receive({ type: "state", state: "paused", reason: "secure_desktop" });
    socket.receive(sessionFrame("paused", "secure_desktop"));
    await flush();
    expect(rt.screen.snapshot).toMatchObject({ phase: "connecting", pcState: "paused" });
    await timers.advance(NO_PICTURE_RESTART_MS + 30_000);
    expect(keyframes(peer)).toBe(0);
    expect(frames("screen_ice_request")).toHaveLength(1); // no fresh session (the PC would refuse it: PC_SESSION_LOCKED)
    expect(rt.screen.snapshot).toMatchObject({ phase: "connecting", retries: 0 });
    expect(logged("screen.no_picture")).toHaveLength(0);
    expect(logged("screen.no_picture_held")[0]?.fields).toMatchObject({ reason: "pc_paused" });
    // the prompt is answered: what was counted before the pause (to the 1 s tick that saw it: 2 s) counts on, the
    // paused time does not
    peer.channel.receive({ type: "state", state: "sending" });
    socket.receive(sessionFrame("resumed", "secure_desktop"));
    await flush();
    await timers.advance(1_000);
    expect(keyframes(peer)).toBe(0);
    await timers.advance(1_000);
    expect(keyframes(peer)).toBe(1); // 3 s of watching
    await timers.advance(NO_PICTURE_RESTART_MS - NO_PICTURE_FIRST_MS - 1_000);
    expect(keyframes(peer)).toBe(2);
    expect(frames("screen_ice_request")).toHaveLength(1);
    await timers.advance(1_000);
    await until(() => frames("screen_ice_request").length === 2);
    expect(rt.screen.snapshot.retries).toBe(1);
  });

  it("a picture that decodes but waits for the owner's tap is not restarted at 15 s; the watchdog counts again from the tap", async () => {
    knobs.stats = safariLikeStats({ decoded: 12 });
    let media = pausedMedia();
    rt.screen.setMediaProbe(() => media);
    const { peer } = await answered();
    peer.connect();
    await timers.advance(NO_PICTURE_FIRST_MS);
    expect(rt.screen.snapshot.playHint).toBe(true);
    await timers.advance(NO_PICTURE_RESTART_MS + 60_000);
    // a fresh session would only bring a new <video> whose play() is refused outside a gesture again
    expect(frames("screen_ice_request")).toHaveLength(1);
    expect(rt.screen.snapshot).toMatchObject({ phase: "connecting", playHint: true, retries: 0 });
    expect(logged("screen.no_picture_held")[0]?.fields).toMatchObject({ reason: "tap" });
    expect(keyframes(peer)).toBe(2);
    // the tap: the page plays again; here the picture still does not come and nothing decodes any more
    media = pausedMedia({ paused: false, play: "resolved" });
    peer.stats = safariLikeStats({ packets: 0, received: 0, decoded: 0, keyDecoded: 0 });
    rt.screen.dismissPlayHint();
    expect(rt.screen.snapshot.playHint).toBe(false);
    await timers.advance(NO_PICTURE_FIRST_MS);
    expect(keyframes(peer)).toBe(3); // the steps start again from the tap
    await timers.advance(NO_PICTURE_RESTART_MS - NO_PICTURE_FIRST_MS - 1_000);
    expect(frames("screen_ice_request")).toHaveLength(1);
    await timers.advance(1_000);
    await until(() => frames("screen_ice_request").length === 2);
  });

  it("play() refused before anything decoded: the tap prompt stays instead of a restart, until the tap", async () => {
    knobs.stats = safariLikeStats({ packets: 0, received: 0, decoded: 0, keyDecoded: 0 });
    const { peer } = await answered();
    rt.screen.notePlayBlocked(true); // what the page reports from the <video>'s refused play()
    peer.connect();
    await timers.advance(NO_PICTURE_RESTART_MS + 5_000);
    expect(frames("screen_ice_request")).toHaveLength(1);
    expect(rt.screen.snapshot).toMatchObject({ phase: "connecting", playHint: true });
    rt.screen.dismissPlayHint();
    rt.screen.notePlayBlocked(false); // the play() inside the tap resolved
    await timers.advance(NO_PICTURE_RESTART_MS);
    await until(() => frames("screen_ice_request").length === 2);
  });

  it("no tap prompt while nothing decoded, even with a paused <video>: the restart goes ahead", async () => {
    knobs.stats = safariLikeStats({ packets: 0, received: 0, decoded: 0, keyDecoded: 0 });
    rt.screen.setMediaProbe(() => pausedMedia({ play: "pending" }));
    const { peer } = await answered();
    peer.connect();
    await timers.advance(NO_PICTURE_SECOND_MS);
    expect(rt.screen.snapshot.playHint).toBe(false);
    await timers.advance(NO_PICTURE_RESTART_MS - NO_PICTURE_SECOND_MS);
    await until(() => frames("screen_ice_request").length === 2);
    expect(states.every((st) => !st.playHint)).toBe(true);
  });

  it("a first frame while the +15 s step reads the stats: no restart of the live picture", async () => {
    const { peer } = await answered();
    peer.connect();
    await timers.advance(NO_PICTURE_RESTART_MS - 500);
    const pending: Array<() => void> = [];
    const real = peer.getStats.bind(peer);
    peer.getStats = () => new Promise((resolve) => pending.push(() => void real().then(resolve)));
    await timers.advance(500);
    expect(logged("screen.no_picture")).toHaveLength(2); // the third step is waiting for getStats()
    rt.screen.onVideoFrame(timers.now);
    expect(rt.screen.snapshot.phase).toBe("live");
    for (const release of pending) release();
    await flush(5);
    await watch(3_000);
    expect(frames("screen_ice_request")).toHaveLength(1);
    expect(rt.screen.snapshot).toMatchObject({ phase: "live", retries: 0 });
    expect(logged("screen.no_picture")).toHaveLength(2);
  });
});

describe("ScreenSessionClient: the VP8 retry after H.264 that never decodes (device round 2)", () => {
  const CAPS: RTCRtpCodec[] = [
    { mimeType: "video/H264", clockRate: 90000, sdpFmtpLine: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f" },
    { mimeType: "video/H264", clockRate: 90000, sdpFmtpLine: "level-asymmetry-allowed=1;packetization-mode=0;profile-level-id=42e01f" },
    { mimeType: "video/VP8", clockRate: 90000 },
    { mimeType: "video/rtx", clockRate: 90000 },
    { mimeType: "video/red", clockRate: 90000 },
  ];
  const VP8_ONLY_OFFER = OFFER_SDP.replace("m=video 9 UDP/TLS/RTP/SAVPF 96 97", "m=video 9 UDP/TLS/RTP/SAVPF 97").replace("a=rtpmap:96 H264/90000\r\na=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f\r\n", "");

  beforeEach(() => {
    vi.stubGlobal("RTCRtpReceiver", { getCapabilities: (kind: string) => (kind === "video" ? { codecs: CAPS } : { codecs: [{ mimeType: "audio/opus", clockRate: 48000, channels: 2 }] }) });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("H.264 packets arrive and nothing decodes for 15 s: the fresh session offers VP8 + RTX only (screen.codec_fallback); the owner's own start offers H.264 first again", async () => {
    knobs.stats = safariLikeStats({ packets: 900, received: 0, decoded: 0, keyDecoded: 0 });
    const { peer } = await answered();
    expect(peer.transceivers[0]!.codecs!.map((c) => c.mimeType)).toEqual(["video/H264", "video/H264", "video/VP8", "video/rtx", "video/red"]);
    peer.connect();
    expect(VP8_ONLY_OFFER).not.toMatch(/H264/);
    knobs.offerSdp = VP8_ONLY_OFFER; // what Safari writes for VP8-only preferences
    await timers.advance(NO_PICTURE_RESTART_MS);
    await until(() => frames("screen_ice_request").length === 2);
    expect(logged("screen.codec_fallback")[0]?.fields).toEqual({ from: "h264", to: "vp8", reason: "not_decoded" });
    socket.receive(iceServers());
    await until(() => commands("screen.session_start").length === 2);
    const retry = peers[1]!;
    expect(retry.transceivers[0]!.codecs!.map((c) => c.mimeType)).toEqual(["video/VP8", "video/rtx"]);
    expect(retry.transceivers[1]!.codecs!.map((c) => c.mimeType)).toEqual(["audio/opus"]);
    // the VP8-only offer passes the phone's strict check and is signed as it is
    expect((commands("screen.session_start")[1]!.params as { offer_sdp: string }).offer_sdp).toBe(VP8_ONLY_OFFER);
    expect(rt.screen.facts.codec_fallback).toEqual({ from: "h264", to: "vp8", reason: "not_decoded" });
    // the owner's Stop and Show screen: H.264 first again
    await rt.screen.stop("stopped");
    knobs.offerSdp = OFFER_SDP;
    await startAndOffer();
    expect(peers[2]!.transceivers[0]!.codecs!.map((c) => c.mimeType)[0]).toBe("video/H264");
    expect(rt.screen.facts.codec_fallback).toBeNull();
  });

  it("no fallback when nothing arrived (a transport or PC problem, not a decoder one), or when VP8 is already in use", async () => {
    knobs.stats = safariLikeStats({ packets: 0, received: 0, decoded: 0, keyDecoded: 0 });
    const { peer } = await answered();
    peer.connect();
    await timers.advance(NO_PICTURE_RESTART_MS);
    await until(() => frames("screen_ice_request").length === 2);
    expect(events()).not.toContain("screen.codec_fallback");
    await rt.screen.stop("stopped");
    knobs.stats = safariLikeStats({ packets: 900, decoded: 0, mime: "video/VP8", fmtp: "" });
    const second = await answered();
    second.peer.connect();
    await timers.advance(NO_PICTURE_RESTART_MS);
    await until(() => frames("screen_ice_request").length === 4);
    expect(events()).not.toContain("screen.codec_fallback");
  });

  it("a browser without VP8 in its capabilities keeps its usual preferences and says so", async () => {
    vi.stubGlobal("RTCRtpReceiver", { getCapabilities: () => ({ codecs: CAPS.filter((c) => c.mimeType !== "video/VP8") }) });
    knobs.stats = safariLikeStats({ packets: 900, received: 0, decoded: 0, keyDecoded: 0 });
    const { peer } = await answered();
    peer.connect();
    await timers.advance(NO_PICTURE_RESTART_MS);
    await until(() => frames("screen_ice_request").length === 2);
    socket.receive(iceServers());
    await until(() => commands("screen.session_start").length === 2);
    expect(logged("screen.codec_fallback_skipped")[0]?.fields).toEqual({ reason: "no_vp8" });
    expect(peers[1]!.transceivers[0]!.codecs!.map((c) => c.mimeType)[0]).toBe("video/H264");
  });
});

describe("ScreenSessionClient: stats, timeline and facts (device round 2)", () => {
  it("getStats every 5 s in session through the whitelist; the timeline marks each stage once, in order; nothing after the session", async () => {
    knobs.stats = safariLikeStats();
    const { peer } = await answered();
    expect(rt.screen.facts.timeline).toMatchObject({ answer_verified: expect.any(Number), track: expect.any(Number), dtls_connected: null, first_rtp: null });
    await timers.advance(1_000);
    peer.connect();
    await timers.advance(1_000); // the watchdog's 1 s tick reads the stats while no frame has come
    expect(rt.screen.facts.timeline.first_rtp).not.toBeNull();
    expect(rt.screen.facts.timeline.first_decoded).not.toBeNull();
    rt.screen.onVideoFrame(timers.now);
    const t = rt.screen.facts.timeline;
    expect(t.dtls_connected! - t.answer_verified!).toBe(1_000);
    const order = [t.answer_verified!, t.track!, t.dtls_connected!, t.dc_open!, t.first_rtp!, t.first_frame!, t.live!];
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    const before = peer.getStatsCalls;
    await watch(STATS_INTERVAL_MS * 3);
    expect(peer.getStatsCalls - before).toBe(3);
    const facts = rt.screen.facts;
    expect(facts.stats).toMatchObject({ dtls: "connected", ice: "connected", dc_sent: 12, dc_received: 3, inbound: { packets_received: 400, key_frames_decoded: 1, width: 1280, height: 720, jitter_ms: 3.1, last_packet_age_ms: 40, pli: 1, nack: 3 } });
    expect(facts.channel).toMatchObject({ open: true, hb_sent: expect.any(Number) });
    expect(facts.channel.hb_sent).toBeGreaterThan(1);
    expect(facts.transport_connected).toBe(true);
    const text = JSON.stringify(facts);
    for (const secret of STATS_SECRETS) expect(text, secret).not.toContain(secret);
    await rt.screen.stop("stopped");
    const after = peer.getStatsCalls;
    expect(timers.pending).toBe(0); // no interval of the session outlives it
    await timers.advance(STATS_INTERVAL_MS * 2);
    expect(peer.getStatsCalls).toBe(after);
    expect(rt.screen.facts.stats).not.toBeNull(); // kept for the diagnostics bundle until the next start
    // a second session reads once per interval: nothing left over from the first one reads along
    const second = await goLive();
    const base = second.peer.getStatsCalls;
    await watch(STATS_INTERVAL_MS * 3);
    expect(second.peer.getStatsCalls - base).toBe(3);
  });

  it("frames while live are not published one by one: the store hears only of the stall flipping", async () => {
    await goLive();
    let published = 0;
    const unsubscribe = useScreenStore.subscribe(() => (published += 1));
    try {
      for (let i = 0; i < 30; i++) rt.screen.onVideoFrame(timers.now);
      expect(published).toBe(0);
      await timers.advance(STALL_MS + 100);
      expect(rt.screen.snapshot.stalled).toBe(true);
      expect(published).toBe(1);
      rt.screen.onVideoFrame(timers.now);
      rt.screen.onVideoFrame(timers.now + 33);
      expect(published).toBe(2);
      expect(rt.screen.snapshot).toMatchObject({ stalled: false, lastFrameAt: timers.now });
    } finally {
      unsubscribe();
    }
  });

  it("a probe of the <video> is read for the facts, kept after Stop, and forgotten at the next start", async () => {
    let calls = 0;
    const media = (ticks: number): ScreenMediaFacts => ({ at: 0, ready_state: 4, network_state: 2, paused: false, ended: false, width: 1280, height: 720, current_time: 3.2, error: null, total_frames: 90, dropped_frames: 0, play: "resolved", rvfc: true, frame_ticks: ticks, first_tick_ms: 120, stream_active: true, track_state: "live", track_muted: false, track_enabled: true });
    rt.screen.setMediaProbe(() => media(++calls));
    await goLive();
    expect(rt.screen.facts.media).toMatchObject({ ready_state: 4, frame_ticks: calls });
    await rt.screen.stop("stopped");
    rt.screen.setMediaProbe(null); // the page unmounted the <video>
    const kept = rt.screen.facts.media;
    expect(kept).toMatchObject({ play: "resolved", width: 1280 });
    expect(rt.screen.facts.media).toEqual(kept);
    void rt.screen.start(PC);
    await until(() => frames("screen_ice_request").length === 2);
    expect(rt.screen.facts.media).toBeNull();
    expect(rt.screen.facts.stats).toBeNull();
    expect(rt.screen.facts.timeline.answer_verified).toBeNull();
  });

  it("readStats without a peer or without getStats returns null, and a failing getStats is logged without its message", async () => {
    expect(await rt.screen.readStats()).toBeNull();
    const { peer } = await answered();
    peer.getStats = () => Promise.reject(new DOMException("secret detail 192.0.2.44", "InvalidStateError"));
    expect(await rt.screen.readStats()).toBeNull();
    expect(logged("screen.stats_failed")[0]?.fields).toMatchObject({ name: "InvalidStateError" });
    expect(JSON.stringify(recentLogs())).not.toContain("192.0.2.44");
  });
});

describe("ScreenSessionClient: nothing recorded, nothing leaked", () => {
  it("no SDP, candidate, fingerprint or credential reaches the log buffer or the diagnostics report", async () => {
    knobs.stats = safariLikeStats(); // with candidate addresses, the certificate's fingerprint and the SPS in the fmtp line
    const { peer } = await goLive();
    peer.channel.receive({ type: "nonsense", sdp: "v=0 leaked" });
    await watch(STATS_INTERVAL_MS); // frames keep coming; a stats snapshot is kept
    await rt.screen.stop("stopped");
    const text = JSON.stringify(recentLogs());
    expect(text).not.toContain(SDP_SENTINEL);
    expect(text).not.toContain("never-logged-credential");
    expect(text).not.toContain("a=fingerprint");
    expect(text).not.toContain("0.0.0.0");
    expect(text).not.toContain("turn.example.net");
    for (const secret of STATS_SECRETS) expect(text, secret).not.toContain(secret);
    const report = buildDiagnostics({ relayStatus: "open", controllerBound: true, keyAvailable: true, selectedPcId: PC, pcs: [], commands: useLiveStore.getState().commands, sessionPresent: true, plan: "free", screen: states[states.length - 1]!, screenFacts: rt.screen.facts });
    const json = JSON.stringify(report);
    expect(json).not.toContain(SDP_SENTINEL);
    expect(json).not.toContain("never-logged-credential");
    expect(json).not.toContain("a=fingerprint");
    for (const secret of STATS_SECRETS) expect(json, secret).not.toContain(secret);
    expect(report.screen).toMatchObject({ phase: "ended", pc_id: PC, end_reason: "stopped", transport_connected: true, codec_fallback: null });
    expect(Object.keys(report.screen!).sort()).toEqual(["codec_fallback", "encoder", "end_reason", "live_seconds", "media", "path", "pc_id", "phase", "problem_code", "retries", "stalled", "stats", "timeline", "transport_connected", "turn", "verified"]);
    // what survived Stop: the last snapshot (whitelisted) and the stages the attempt reached
    expect(report.screen!.stats).toMatchObject({ inbound: { packets_received: 400, frames_decoded: 28, decoder: "VideoToolbox" }, codec: { mime: "video/H264", pt: 98, fmtp: "profile-level-id=42e01f;packetization-mode=1" }, pair: { state: "succeeded", local_type: "srflx", remote_type: "host", proto: "udp", rtt_ms: 12 } });
    expect(report.screen!.timeline).toMatchObject({ answer_verified: expect.any(Number), track: expect.any(Number), dtls_connected: expect.any(Number), dc_open: expect.any(Number), first_rtp: expect.any(Number), first_decoded: expect.any(Number), first_frame: expect.any(Number), live: expect.any(Number) });
  });

  it("the store keeps the last allowance per PC for the Remote tile and remembers an incompatible PC", async () => {
    await goLive();
    expect(useScreenStore.getState().lastAllowanceByPc[PC]).toMatchObject({ remainingSeconds: 540 });
    await rt.screen.stop("stopped");
    const start = await startAndOffer();
    socket.receive({ type: "result", command_id: start.command_id, origin: "relay", state: "failed", at: TS, duration_ms: 1, error: { code: "PROTOCOL_INCOMPATIBLE", message: "x", retryable: false, detail: { peer: "agent", required: "1.4" } } });
    await until(() => rt.screen.snapshot.phase === "failed");
    expect(useScreenStore.getState().lastProblemByPc[PC]).toBe("PROTOCOL_INCOMPATIBLE");
  });
});

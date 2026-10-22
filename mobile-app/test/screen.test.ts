/**
 * ScreenSessionClient over the real Runtime, a fake socket, a fake RTCPeerConnection and controllable timers
 * (docs/design/screen-view.md §4, §7, §16; version.json rules.screen_signaling, rules.pc_key_pinning): no pin → nothing
 * is asked for; the ICE request, the offer, the signed start command (60 s lifetime, stamped 1.4, the offer inside);
 * the PC-signed answer accepted only against the pinned key and applied as exactly the signed string, every §4.3
 * refusal shows nothing; heartbeats, the data-channel vocabulary, lifecycle frames, stall → keyframe → one fresh
 * session (twice at most), the connect timeout, hidden → `bye` + stop, stopped_on_pc → no restart, the relay refusals,
 * takeover, the lost socket, and no SDP in the logs or diagnostics.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { verifyAndParseCommand, exportPublicJwk, kidFromJwk, type relayFrames } from "@dome/protocol";

import { Runtime, setRuntimeForTests } from "../src/app/runtime.ts";
import { configureApi } from "../src/lib/api.ts";
import { getOrCreateKeyPair, writePin } from "../src/lib/controllerKey.ts";
import { buildDiagnostics } from "../src/lib/diagnostics.ts";
import { clearLogs, recentLogs } from "../src/lib/log.ts";
import { CONNECT_TIMEOUT_MS, HEARTBEAT_MS, MAX_RETRIES, RELAY_HINT_MS, STALL_MS, STALL_RESTART_MS, type ScreenSessionState } from "../src/lib/screen.ts";
import { screenStatus } from "../src/lib/screenStatus.ts";
import { useDevicesStore } from "../src/store/devices.ts";
import { useLiveStore } from "../src/store/live.ts";
import { useScreenStore } from "../src/store/screen.ts";
import { ANSWER_SDP, FakePcKey, fakePeerFactory, SDP_SENTINEL, type FakePeerConnection } from "./helpers/fakePeer.ts";
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

describe("ScreenSessionClient: nothing recorded, nothing leaked", () => {
  it("no SDP, candidate, fingerprint or credential reaches the log buffer or the diagnostics report", async () => {
    const { peer } = await goLive();
    peer.channel.receive({ type: "nonsense", sdp: "v=0 leaked" });
    await rt.screen.stop("stopped");
    const text = JSON.stringify(recentLogs());
    expect(text).not.toContain(SDP_SENTINEL);
    expect(text).not.toContain("never-logged-credential");
    expect(text).not.toContain("a=fingerprint");
    expect(text).not.toContain("0.0.0.0");
    expect(text).not.toContain("turn.example.net");
    const report = buildDiagnostics({ relayStatus: "open", controllerBound: true, keyAvailable: true, selectedPcId: PC, pcs: [], commands: useLiveStore.getState().commands, sessionPresent: true, plan: "free", screen: states[states.length - 1]! });
    const json = JSON.stringify(report);
    expect(json).not.toContain(SDP_SENTINEL);
    expect(json).not.toContain("never-logged-credential");
    expect(json).not.toContain("a=fingerprint");
    expect(report.screen).toMatchObject({ phase: "ended", pc_id: PC, end_reason: "stopped" });
    expect(Object.keys(report.screen!).sort()).toEqual(["encoder", "end_reason", "live_seconds", "path", "pc_id", "phase", "problem_code", "retries", "stalled", "turn", "verified"]);
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

/**
 * Protocol 1.4, Screen view (docs/PROTOCOL.md §20; docs/design/screen-view.md §11): the same accept/refuse decisions as
 * shared/python/tests/test_screen_contract.py, the content-based input-batch stamp and the 1.4 check order, the phone's
 * acceptance test for the PC-signed answer, PC key pinning and the data-channel vocabulary.
 * Protocol 1.5 (docs/design/screen-view-2a.md §1): content-based command stamping (`param_since`), the answer carrying the
 * start's stamp, the pointer messages and the per-type size cap of the data channel.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CAPABILITIES,
  ERRORS,
  INPUT_BATCH_PROTOCOL_VERSION,
  LIMITS,
  PLANS,
  PROTOCOL_VERSION,
  ProtocolError,
  REGISTRY_VERSION,
  SCREEN_CURSOR_CHANNEL_LABEL,
  SCREEN_DATA_CHANNEL_LABEL,
  SCREEN_INPUT_PROTOCOL_VERSION,
  buildCommandPayload,
  buildInputBatchPayload,
  buildScreenAnswerPayload,
  commandProtocolVersion,
  dumpsCompact,
  exportPublicJwk,
  generateControllerKeyPair,
  inputBatchProtocolVersion,
  inputEventCapabilities,
  inputEventSince,
  kidFromJwk,
  minVersion,
  offerDigest,
  parseScreenDcMessage,
  pcKeyMac,
  registry,
  schemas,
  screenSdpAcceptable,
  sdpFingerprints,
  signCommand,
  signInputBatch,
  signPayload,
  signScreenAnswer,
  verifyAndParseCommand,
  verifyAndParseInputBatch,
  verifyPcKeyMac,
  verifyScreenAnswer,
  type CursorKind,
  type EcPublicJwk,
  type InputEvent,
  type KeyRecord,
  type ScreenAnswerPayload,
  type ScreenDcPcToPhone,
} from "../src/index.ts";

const here = dirname(fileURLToPath(import.meta.url));
interface ScreenFixture {
  kid: string;
  digests: { pairing_code: string; pairing_id: string; pc_id: string };
  screen: { pc_public_jwk: EcPublicJwk; pc_kid: string; offer_sdp: string; offer_digest: string; answer_sdp: string; fingerprint: string; pc_key_mac: string };
}
const FIXTURE = JSON.parse(readFileSync(resolve(here, "../../protocol/fixtures/es256-typescript.json"), "utf8")) as ScreenFixture;
const OFFER = FIXTURE.screen.offer_sdp;
const ANSWER = FIXTURE.screen.answer_sdp;

const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const CONTROLLER = "22222222-2222-4222-8222-222222222222";
const PC = "33333333-3333-4333-8333-333333333333";
const COMMAND = "9b1c2d3e-4a5b-4c6d-8e7f-901234567890";
const OTHER = "44444444-4444-4444-8444-444444444444";
const SID = "U2NyZWVuU2Vzc2lvbjAyVF";
const TS = "2026-10-21T12:00:00.000Z";
const ENVELOPE = { v: 1, alg: "ES256", kid: "A".repeat(43), payload: "{}", sig: "B".repeat(86) };
const ALLOWANCE = { unlimited: false, remaining_seconds: 540, resets_at: TS };
const base = { accountId: ACCOUNT, controllerId: CONTROLLER, targetPcId: PC };

function codeOf(fn: () => unknown): string {
  try {
    fn();
    return "OK";
  } catch (e) {
    if (e instanceof ProtocolError) return e.code;
    throw e;
  }
}

async function rejection(p: Promise<unknown>): Promise<ProtocolError> {
  try {
    await p;
  } catch (e) {
    if (e instanceof ProtocolError) return e;
    throw e;
  }
  throw new Error("accepted");
}

async function pcKeyPair() {
  const kp = await generateControllerKeyPair(); // any P-256 pair serves as a PC identity key in tests
  const jwk = await exportPublicJwk(kp.publicKey);
  return { kp, jwk, kid: await kidFromJwk(jwk) };
}

async function controller() {
  const kp = await generateControllerKeyPair();
  const jwk = await exportPublicJwk(kp.publicKey);
  const kid = await kidFromJwk(jwk);
  const record: KeyRecord = { controllerId: CONTROLLER, accountId: ACCOUNT, jwk, capabilities: ["screen", "pointer"] };
  return { kp, resolve: (k: string) => (k === kid ? record : null) };
}

describe("protocol 1.4 registry", () => {
  it("is 1.5 with the screen capabilities, actions, limits, errors and plan fields", () => {
    expect(PROTOCOL_VERSION).toBe("1.5");
    expect(REGISTRY_VERSION).toBe("1.5");
    expect(Object.keys(CAPABILITIES)).toEqual(expect.arrayContaining(["screen", "screen_audio"]));
    const start = registry.get("screen.session_start");
    const stop = registry.get("screen.session_stop");
    for (const spec of [start, stop]) {
      expect(spec.since).toBe("1.4");
      expect([spec.capability, spec.aiEligible, spec.routineAllowed, spec.confirmation, spec.targetName]).toEqual(["screen", false, false, "none", null]);
    }
    expect([start.risk, start.timeoutMs, start.idempotent, start.resultName]).toEqual(["moderate", 45000, false, "screen_session_result"]);
    expect([...start.availability].sort()).toEqual(["session_unlocked", "windows"]);
    expect([stop.risk, stop.timeoutMs, stop.idempotent, stop.resultName, stop.availability]).toEqual(["low", 5000, true, "screen_session_stop_result", []]);
    expect(LIMITS).toMatchObject({
      screen_sdp_max_bytes: 12288,
      screen_start_command_lifetime_seconds: 60,
      screen_answer_lifetime_seconds: 60,
      screen_heartbeat_seconds: 2,
      screen_lease_seconds: 8,
      screen_connect_timeout_seconds: 20,
      screen_allowance_chunk_seconds: 60,
      screen_allowance_renew_before_seconds: 20,
      screen_session_max_seconds: 14400,
      screen_consent_timeout_seconds: 30,
      screen_pc_stop_cooldown_default_seconds: 600,
      screen_secure_desktop_pause_max_seconds: 120,
      screen_dc_message_max_bytes: 1024,
      screen_geometry_generations_kept: 4,
      input_wheel_max: 1200,
    });
    const retryable: Record<string, boolean> = {
      SCREEN_SESSION_OWNED: false,
      SCREEN_SESSION_REQUIRED: false,
      SCREEN_ALLOWANCE_EXHAUSTED: false,
      SCREEN_OFFER_INVALID: false,
      SCREEN_CAPTURE_UNAVAILABLE: false,
      SCREEN_ENCODER_UNAVAILABLE: false,
      SCREEN_CONNECTION_FAILED: true,
      SCREEN_CONSENT_DECLINED: false,
      SCREEN_CONSENT_TIMEOUT: false,
      SCREEN_GEOMETRY_CHANGED: true,
      SCREEN_VIEW_DISABLED: false,
      INPUT_PAUSED_ON_PC: true,
      INPUT_TARGET_RESERVED: false,
      SCREEN_PC_UNVERIFIED: false,
      SCREEN_ANSWER_INVALID: false,
    };
    for (const [code, flag] of Object.entries(retryable)) {
      expect(ERRORS[code]?.retryable, code).toBe(flag);
      expect(ERRORS[code]?.user_message.endsWith("."), code).toBe(true);
    }
    for (const id of ["free", "pro", "pro_plus"] as const) {
      const plan = PLANS.plans[id];
      expect(plan.screen_view_window_hours).toBe(5);
      expect(plan.screen_view_seconds_per_window).toBe(id === "free" ? 600 : null);
      expect(plan.screen_ice_rate_limit).toEqual({ per_minute: 6, burst: 6, account_per_hour: 60 });
      expect(plan.screen_start_rate_limit).toEqual({ per_minute: 6, burst: 6 });
    }
  });

  it("validates screen.session_start params with defaults and the strict SDP pattern", () => {
    expect(registry.validateParams("screen.session_start", { offer_sdp: OFFER })).toEqual({ offer_sdp: OFFER, surface: "primary", audio: true, takeover: false });
    expect(registry.validateParams("screen.session_start", { offer_sdp: OFFER, surface: 7, audio: false, takeover: true }).surface).toBe(7);
    for (const bad of [
      {},
      { offer_sdp: "" },
      { offer_sdp: "v=0\r\ns=ü\r\n" },
      { offer_sdp: "v=0\r\ns=\u0000\r\n" },
      { offer_sdp: "v=0\r\na=" + "x".repeat(12290) },
      { offer_sdp: OFFER, surface: 8 },
      { offer_sdp: OFFER, surface: "secondary" },
      { offer_sdp: OFFER, audio: "yes" },
      { offer_sdp: OFFER, extra: 1 },
    ]) {
      expect(codeOf(() => registry.validateParams("screen.session_start", bad)), JSON.stringify(bad).slice(0, 40)).toBe("INVALID_PARAMETERS");
    }
    expect(codeOf(() => registry.validateParams("screen.session_stop", { screen_session_id: "short" }))).toBe("INVALID_PARAMETERS");
    expect(buildCommandPayload({ ...base, action: "screen.session_start", params: { offer_sdp: OFFER }, lifetimeSeconds: 60 }).protocol_version).toBe("1.4");
    expect(buildCommandPayload({ ...base, action: "screen.session_stop", params: { screen_session_id: SID } }).protocol_version).toBe("1.4");
  });
});

describe("protocol 1.4 frames and shapes", () => {
  const ok = (direction: Parameters<typeof schemas.validateFrame>[0], frame: unknown) => expect(codeOf(() => schemas.validateFrame(direction, frame))).toBe("OK");
  const bad = (direction: Parameters<typeof schemas.validateFrame>[0], frame: unknown) => expect(codeOf(() => schemas.validateFrame(direction, frame))).toBe("MALFORMED_MESSAGE");

  it("screen_ice_request / screen_ice_servers", () => {
    ok("controller_to_relay", { type: "screen_ice_request", pc_id: PC });
    bad("controller_to_relay", { type: "screen_ice_request" });
    bad("agent_to_relay", { type: "screen_ice_request", pc_id: PC });
    const servers = {
      type: "screen_ice_servers",
      pc_id: PC,
      ice_servers: [{ urls: ["stun:stun.example.net:3478"] }, { urls: ["turn:turn.example.net:3478?transport=udp", "turns:turn.example.net:443?transport=tcp"], username: "u", credential: "c" }],
      expires_at: TS,
      allowance: ALLOWANCE,
      turn: "available",
    };
    ok("relay_to_controller", servers);
    ok("relay_to_controller", { ...servers, ice_servers: [], expires_at: null, turn: "disabled", allowance: { unlimited: true, remaining_seconds: null, resets_at: null } });
    bad("relay_to_controller", { ...servers, turn: "maybe" });
    bad("relay_to_controller", { ...servers, ice_servers: [{ urls: ["http://x"] }] });
    bad("relay_to_controller", { ...servers, pc_name: "Desk" });
    bad("relay_to_agent", servers);
  });

  it("screen_answer in both directions, screen_session, screen_usage, screen_allowance, relay.screen", () => {
    ok("agent_to_relay", { type: "screen_answer", envelope: ENVELOPE, ref_controller_id: CONTROLLER });
    bad("agent_to_relay", { type: "screen_answer", envelope: ENVELOPE });
    ok("relay_to_controller", { type: "screen_answer", pc_id: PC, envelope: ENVELOPE });
    bad("relay_to_controller", { type: "screen_answer", pc_id: PC, envelope: ENVELOPE, ref_controller_id: CONTROLLER });
    bad("relay_to_agent", { type: "screen_answer", pc_id: PC, envelope: ENVELOPE });
    const session = { type: "screen_session", pc_id: PC, screen_session_id: SID, controller_id: CONTROLLER, event: "started", reason: "started", path: "relayed", live_seconds: 0, at: TS };
    ok("agent_to_relay", session);
    ok("relay_to_controller", { ...session, event: "ended", reason: "controller_offline", live_seconds: 3000 });
    ok("relay_to_controller", { ...session, event: "paused", reason: "indicator" });
    bad("agent_to_relay", { ...session, reason: "lease_expired" });
    bad("agent_to_relay", { ...session, phone_name: "Maya" });
    bad("relay_to_agent", session);
    ok("agent_to_relay", { type: "screen_usage", pc_id: PC, screen_session_id: SID, live_seconds: 40, relayed: false, relayed_bytes: 0 });
    bad("agent_to_relay", { type: "screen_usage", pc_id: PC, screen_session_id: SID, live_seconds: 40, relayed: false, relayed_bytes: 0, granted_until: TS });
    ok("relay_to_agent", { type: "screen_allowance", screen_session_id: SID, granted_until: TS, unlimited: false, final: false });
    ok("relay_to_agent", { type: "screen_allowance", screen_session_id: SID, granted_until: null, unlimited: false, final: true, reason: "controller_offline", resets_at: null });
    bad("relay_to_agent", { type: "screen_allowance", screen_session_id: SID, granted_until: TS, unlimited: false, final: false, reason: "viewer_gone" });
    bad("relay_to_controller", { type: "screen_allowance", screen_session_id: SID, granted_until: TS, unlimited: false, final: false });
    const relay = { received_at: TS, connection_id: OTHER };
    ok("relay_to_agent", { type: "command", envelope: ENVELOPE, relay: { ...relay, screen: { granted_until: TS, unlimited: false, stun_urls: ["stun:stun.example.net:3478"], relayed_max_kbps: 500 } } });
    bad("relay_to_agent", { type: "command", envelope: ENVELOPE, relay: { ...relay, screen: { granted_until: TS, unlimited: false, stun_urls: ["turn:turn.example.net:3478"] } } });
    bad("relay_to_agent", { type: "input_batch", envelope: ENVELOPE, relay: { ...relay, screen: { granted_until: TS, unlimited: false, stun_urls: [] } } });
  });

  it("pairing_decision.pc_key_mac, pairing_status_response.pc_key, the capability enums", () => {
    const decision = { type: "pairing_decision", pairing_id: OTHER, decision: "approve", kid: "A".repeat(43), granted_capabilities: ["status", "screen", "screen_audio"] };
    ok("agent_to_relay", decision);
    ok("agent_to_relay", { ...decision, pc_key_mac: "C".repeat(43) });
    bad("agent_to_relay", { ...decision, pc_key_mac: "C".repeat(42) });
    const status = { pairing_id: OTHER, state: "approved", pc_id: PC, pc_name: "Desk PC", expires_at: TS, controller_id: CONTROLLER, granted_capabilities: ["screen"] };
    expect(codeOf(() => schemas.validateRest("pairing_status_response", status))).toBe("OK");
    expect(codeOf(() => schemas.validateRest("pairing_status_response", { ...status, pc_key: { public_jwk: FIXTURE.screen.pc_public_jwk, mac: "C".repeat(43) } }))).toBe("OK");
    expect(codeOf(() => schemas.validateRest("pairing_status_response", { ...status, pc_key: { mac: "C".repeat(43) } }))).toBe("MALFORMED_MESSAGE");
    expect(codeOf(() => schemas.validateRest("pairing_status_response", { ...status, pc_key: null }))).toBe("MALFORMED_MESSAGE");
    ok("agent_to_relay", { type: "grant_update", controller_id: CONTROLLER, kid: "A".repeat(43), capabilities: ["status", "screen", "screen_audio"] });
    bad("agent_to_relay", { type: "grant_update", controller_id: CONTROLLER, kid: "A".repeat(43), capabilities: ["status", "camera"] });
  });

  it("result shapes carry ids, geometry, flags and the allowance, never SDP", () => {
    const result = {
      screen_session_id: SID,
      surfaces: [{ index: 0, width: 2560, height: 1440, primary: true }],
      surface_index: 0,
      gen: 1,
      audio: false,
      lease_seconds: 8,
      allowance: ALLOWANCE,
      consent: "not_required",
    };
    expect(codeOf(() => registry.validateResult("screen.session_start", result))).toBe("OK");
    expect(codeOf(() => registry.validateResult("screen.session_start", { ...result, consent: "granted", encoder: "vp8" }))).toBe("OK");
    expect(codeOf(() => registry.validateResult("screen.session_start", { ...result, consent: "pending" }))).toBe("MALFORMED_MESSAGE");
    expect(codeOf(() => registry.validateResult("screen.session_start", { ...result, answer_sdp: ANSWER }))).toBe("MALFORMED_MESSAGE");
    expect(codeOf(() => registry.validateResult("screen.session_start", { ...result, surfaces: [] }))).toBe("MALFORMED_MESSAGE");
    expect(codeOf(() => registry.validateResult("screen.session_stop", { stopped: false, live_seconds: 0 }))).toBe("OK");
    expect(codeOf(() => registry.validateResult("screen.session_stop", { stopped: true }))).toBe("MALFORMED_MESSAGE");
  });
});

describe("screen data channel", () => {
  it("parses the closed vocabulary per direction and drops everything else", () => {
    expect(SCREEN_DATA_CHANNEL_LABEL).toBe("dome-screen-v1");
    for (const text of ['{"type":"hb"}', '{"type":"keyframe"}', '{"type":"bye"}', '{"type":"audio","on":true}']) {
      expect(parseScreenDcMessage(text, "phone_to_pc").type).toBe((JSON.parse(text) as { type: string }).type);
    }
    const geometry = { type: "geometry", gen: 2, surface_index: 0, width: 2560, height: 1440, rotation: 90, reserved: [{ x: 0, y: 0, w: 65535, h: 1200 }] };
    for (const msg of [
      geometry,
      { type: "state", state: "sending", encoder: "h264", data_saver: true },
      { type: "state", state: "paused", reason: "secure_desktop" },
      { type: "allowance", unlimited: true, remaining_seconds: null, resets_at: null },
      { type: "restricted", restricted: true, reason: "permission_ui" },
    ]) {
      expect(parseScreenDcMessage(dumpsCompact(msg), "pc_to_phone")).toEqual(msg);
    }
    const dropped: Array<[string, "phone_to_pc" | "pc_to_phone"]> = [
      [dumpsCompact(geometry), "phone_to_pc"],
      ['{"type":"hb"}', "pc_to_phone"],
      ['{"type":"pointer_button","button":"left","action":"click"}', "phone_to_pc"],
      ['{"type":"roi","gen_seen":1,"rect":{"x":0,"y":0,"w":1,"h":1}}', "phone_to_pc"],
      ['{"type":"cursor_pos","x":1,"y":1}', "pc_to_phone"],
      ['{"type":"hb","extra":1}', "phone_to_pc"],
      ['{"type":"state","state":"recording"}', "pc_to_phone"],
      ["[]", "phone_to_pc"],
      ['{"type":"hb","type":"bye"}', "phone_to_pc"],
    ];
    for (const [text, direction] of dropped) {
      const parse = direction === "pc_to_phone" ? () => parseScreenDcMessage(text, "pc_to_phone") : () => parseScreenDcMessage(text, "phone_to_pc");
      expect(codeOf(parse), text).toBe("MALFORMED_MESSAGE");
    }
    // 1.5: an over-long message is refused before parsing, as MALFORMED_MESSAGE
    expect(codeOf(() => parseScreenDcMessage('{"type":"hb","pad":"' + "x".repeat(1100) + '"}', "phone_to_pc"))).toBe("MALFORMED_MESSAGE");
  });
});

describe("strict SDP helper", () => {
  it("accepts CRLF printable ASCII within the limit and extracts fingerprints", () => {
    expect(screenSdpAcceptable(OFFER) && screenSdpAcceptable(ANSWER)).toBe(true);
    expect(screenSdpAcceptable(OFFER.replace(/\r\n/g, "\n"))).toBe(false);
    expect(screenSdpAcceptable(null)).toBe(false);
    expect(screenSdpAcceptable("v=0\r\n", 5)).toBe(true);
    expect(screenSdpAcceptable("v=0\r\n", 4)).toBe(false);
    expect(sdpFingerprints(ANSWER)).toEqual([FIXTURE.screen.fingerprint, FIXTURE.screen.fingerprint, FIXTURE.screen.fingerprint]);
    expect(sdpFingerprints(ANSWER.toLowerCase().replace(/a=fingerprint:sha-256/g, "a=fingerprint:sha-256"))).toEqual(sdpFingerprints(ANSWER));
    expect(sdpFingerprints("a=fingerprint:sha-1 00:11\r\na=fingerprint:sha-256 zz\r\n")).toEqual([]);
  });

  it("digests the offer exactly as the fixture does", async () => {
    expect(await offerDigest(OFFER)).toBe(FIXTURE.screen.offer_digest);
  });
});

describe("the PC-signed screen answer", () => {
  async function signed(pc: Awaited<ReturnType<typeof pcKeyPair>>, overrides: Partial<ScreenAnswerPayload> = {}, now = new Date()) {
    const payload = await buildScreenAnswerPayload({ accountId: ACCOUNT, pcId: PC, controllerId: CONTROLLER, commandId: COMMAND, screenSessionId: SID, offerSdp: OFFER, answerSdp: ANSWER, protocolVersion: "1.5", now });
    return signPayload(pc.kp.privateKey, pc.kp.publicKey, dumpsCompact({ ...payload, ...overrides }));
  }
  const expectation = (pc: Awaited<ReturnType<typeof pcKeyPair>>) => ({ pinnedJwk: pc.jwk, accountId: ACCOUNT, controllerId: CONTROLLER, pcId: PC, commandId: COMMAND, offerSdp: OFFER });

  it("round-trips through build, sign and verify against the pinned key", async () => {
    const pc = await pcKeyPair();
    const payload = await buildScreenAnswerPayload({ accountId: ACCOUNT, pcId: PC, controllerId: CONTROLLER, commandId: COMMAND, screenSessionId: SID, offerSdp: OFFER, answerSdp: ANSWER, protocolVersion: "1.5", now: new Date(TS) });
    expect(payload).toMatchObject({ type: "screen_answer", protocol_version: "1.5", offer_digest: FIXTURE.screen.offer_digest, issued_at: TS, expires_at: "2026-10-21T12:01:00.000Z" });
    schemas.validateScreenAnswerPayload(payload);
    const envelope = await signScreenAnswer(pc.kp.privateKey, pc.kp.publicKey, payload);
    expect(envelope.kid).toBe(pc.kid);
    const verified = await verifyScreenAnswer(envelope, { ...expectation(pc), now: new Date("2026-10-21T12:00:03.000Z") });
    expect([verified.pcKid, verified.fingerprint, verified.payload.answer_sdp]).toEqual([pc.kid, FIXTURE.screen.fingerprint, ANSWER]);
    await expect(buildScreenAnswerPayload({ accountId: ACCOUNT, pcId: PC, controllerId: CONTROLLER, commandId: COMMAND, screenSessionId: SID, offerSdp: OFFER, answerSdp: ANSWER.replace(/\r\n/g, "\n"), protocolVersion: "1.5" })).rejects.toBeInstanceOf(ProtocolError);
  });

  it("refuses everything else as SCREEN_ANSWER_INVALID with the reason, never the SDP", async () => {
    const pc = await pcKeyPair();
    const other = await pcKeyPair();
    const good = await signed(pc);
    const zeros = Array.from({ length: 32 }, () => "00").join(":");
    const cases: Array<[unknown, Partial<ReturnType<typeof expectation>> & { now?: Date }, string, string | undefined]> = [
      [good, { pinnedJwk: other.jwk }, "kid is not the pinned PC key", "UNKNOWN_KEY"],
      [await signed(other), {}, "kid is not the pinned PC key", "UNKNOWN_KEY"],
      [{ ...good, payload: good.payload.replace("a=setup:active", "a=setup:passive") }, {}, "envelope or signature", "SIGNATURE_INVALID"],
      [good, { accountId: OTHER }, "account_id is not ours", undefined],
      [good, { controllerId: OTHER }, "controller_id is not ours", undefined],
      [good, { pcId: OTHER }, "pc_id is not ours", undefined],
      [good, { commandId: OTHER }, "command_id is not ours", undefined],
      [good, { offerSdp: OFFER + "a=x\r\n" }, "offer_digest does not match the offer we sent", undefined],
      [good, { now: new Date(Date.now() + 120_000) }, "window", "COMMAND_EXPIRED"],
      [good, { now: new Date(Date.now() - 120_000) }, "window", "CLOCK_SKEW"],
      [await signed(pc, { answer_sdp: ANSWER + "a=fingerprint:sha-256 " + zeros + "\r\n" }), {}, "fingerprints missing or not identical", undefined],
      [await signed(pc, { answer_sdp: "v=0\r\na=sendonly\r\n" }), {}, "fingerprints missing or not identical", undefined],
      [await signed(pc, { type: "answer" as never }), {}, "payload shape", "MALFORMED_MESSAGE"],
      [await signed(pc, { screen_session_id: "short" }), {}, "payload shape", "MALFORMED_MESSAGE"],
    ];
    for (const [raw, kw, reason, cause] of cases) {
      const err = await rejection(verifyScreenAnswer(raw, { ...expectation(pc), ...kw }));
      expect(err.code, reason).toBe("SCREEN_ANSWER_INVALID");
      expect(err.detail.reason, reason).toBe(reason);
      if (cause) expect(err.detail.cause, reason).toBe(cause);
      expect(err.message).not.toContain(ANSWER.slice(0, 40));
      expect(err.message).not.toContain(zeros);
    }
    const newer = await rejection(verifyScreenAnswer(await signed(pc, { protocol_version: "1.6" }), expectation(pc)));
    expect([newer.code, newer.detail]).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: "1.6", supported: ["1.5"] }]);
    const unusable = await rejection(verifyScreenAnswer(good, { ...expectation(pc), pinnedJwk: { kty: "EC", crv: "P-256", x: "A".repeat(43), y: "A".repeat(43) } }));
    expect([unusable.code, unusable.detail.reason]).toEqual(["SCREEN_ANSWER_INVALID", "pinned key unusable"]);
  });
});

describe("PC key pinning", () => {
  it("matches the fixture vector and verifies in constant time with WebCrypto", async () => {
    const d = FIXTURE.digests;
    const mac = await pcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid);
    expect(mac).toBe(FIXTURE.screen.pc_key_mac);
    expect(mac).toHaveLength(43);
    expect(await pcKeyMac(d.pairing_code.toLowerCase().replace(/-/g, " "), d.pairing_id, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid)).toBe(mac);
    expect(await verifyPcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid, mac)).toBe(true);
    const other = await pcKeyPair();
    expect(await verifyPcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, FIXTURE.kid, other.kid, mac)).toBe(false); // substituted PC key
    expect(await verifyPcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, other.kid, FIXTURE.screen.pc_kid, mac)).toBe(false);
    expect(await verifyPcKeyMac(d.pairing_code, OTHER, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid, mac)).toBe(false);
    expect(await verifyPcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid, mac.slice(0, -1) + "A")).toBe(false);
    expect(await verifyPcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid, "not base64url!")).toBe(false);
    expect(await verifyPcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid, null)).toBe(false);
    await expect(pcKeyMac("not a code", d.pairing_id, d.pc_id, FIXTURE.kid, FIXTURE.screen.pc_kid)).rejects.toBeInstanceOf(ProtocolError);
  });
});

describe("input events added in 1.4", () => {
  const moveTo: InputEvent = { type: "pointer_move_to", x: 32768, y: 100, gen: 7 };
  const wheel: InputEvent = { type: "pointer_wheel", dx: 0, dy: -240 };
  const f11: InputEvent = { type: "key", key: "f11" };
  const enter: InputEvent = { type: "key", key: "enter" };
  const batch = (events: InputEvent[], seq = 1) => buildInputBatchPayload({ ...base, inputSessionId: SID, seq, events });

  it("maps capabilities and stamps a batch with the lowest version that defines every event", () => {
    expect([...inputEventCapabilities([moveTo, wheel])]).toEqual(["pointer"]);
    expect([...inputEventCapabilities([f11])]).toEqual(["keyboard"]);
    expect([inputEventSince(moveTo), inputEventSince(wheel), inputEventSince(f11)]).toEqual([SCREEN_INPUT_PROTOCOL_VERSION, "1.4", "1.4"]);
    expect([inputEventSince(enter), inputEventSince({ type: "pointer_move" })]).toEqual([INPUT_BATCH_PROTOCOL_VERSION, "1.1"]);
    expect(inputBatchProtocolVersion([])).toBe("1.1");
    expect(inputBatchProtocolVersion([enter])).toBe("1.1");
    for (const events of [[moveTo], [wheel], [f11], [enter, moveTo]]) expect(inputBatchProtocolVersion(events)).toBe("1.4");
    expect(batch([enter]).protocol_version).toBe("1.1");
    expect(batch([moveTo, wheel, f11]).protocol_version).toBe("1.4");
    schemas.validateInputBatchPayload(batch([moveTo, wheel, f11]));
    for (const bad of [
      { type: "pointer_move_to", x: 65536, y: 0, gen: 1 },
      { type: "pointer_move_to", x: 0, y: 0, gen: 0 },
      { type: "pointer_move_to", x: 0, y: 0 },
      { type: "pointer_wheel", dx: 1201, dy: 0 },
      { type: "key", key: "f12" },
    ]) {
      expect(codeOf(() => schemas.validateInputBatchPayload({ ...batch([]), events: [bad] })), JSON.stringify(bad)).toBe("MALFORMED_MESSAGE");
    }
  });

  it("verifies stamps in the 1.4 order: compatibility and since before the schema, then the per-event floor", async () => {
    const { kp, resolve } = await controller();
    const sign = (payload: unknown) => signPayload(kp.privateKey, kp.publicKey, dumpsCompact(payload));
    const good = batch([moveTo]);
    const verified = await verifyAndParseInputBatch(await signInputBatch(kp.privateKey, kp.publicKey, good), resolve);
    expect([verified.requiredProtocolVersion, [...verified.requiredCapabilities]]).toEqual(["1.4", ["pointer"]]);
    for (const stamp of ["1.1", "1.2", "1.3"]) {
      const err = await rejection(verifyAndParseInputBatch(await sign({ ...good, protocol_version: stamp }), resolve));
      expect([err.code, err.detail], stamp).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: stamp, required: "1.4" }]);
    }
    const plain = batch([enter], 2);
    for (const stamp of ["1.1", "1.2", "1.3", "1.4", "1.5"]) {
      expect((await verifyAndParseInputBatch(await sign({ ...plain, protocol_version: stamp }), resolve)).requiredProtocolVersion).toBe("1.1");
    }
    // a future event: PROTOCOL_INCOMPATIBLE when stamped with a newer MINOR, MALFORMED_MESSAGE when stamped with ours
    const future = { ...batch([]), events: [{ type: "pointer_tilt", angle: 3 }] };
    const newer = await rejection(verifyAndParseInputBatch(await sign({ ...future, protocol_version: "1.6" }), resolve));
    expect([newer.code, newer.detail]).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: "1.6", supported: ["1.5"] }]);
    expect((await rejection(verifyAndParseInputBatch(await sign({ ...future, protocol_version: "1.5" }), resolve))).code).toBe("MALFORMED_MESSAGE");
    const floor = await rejection(verifyAndParseInputBatch(await sign({ ...future, protocol_version: "1.0" }), resolve));
    expect([floor.code, floor.detail]).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: "1.0", required: "1.1" }]);
    for (const junk of ["1", "v1.4", "1.4\n", 14, null]) {
      expect((await rejection(verifyAndParseInputBatch(await sign({ ...future, protocol_version: junk }), resolve))).code, String(junk)).toBe("MALFORMED_MESSAGE");
    }
    // a wrong signer, a stale window and a foreign controller are still refused as before
    const foreign = await controller();
    expect((await rejection(verifyAndParseInputBatch(await sign(good), foreign.resolve))).code).toBe("UNKNOWN_KEY");
    const renamed = (k: string): KeyRecord | null => {
      const record = resolve(k);
      return record ? { ...record, controllerId: OTHER } : null;
    };
    expect((await rejection(verifyAndParseInputBatch(await sign(good), renamed))).code).toBe("CONTROLLER_MISMATCH");
    const stale = buildInputBatchPayload({ ...base, inputSessionId: SID, seq: 3, events: [], now: new Date(Date.now() - 30_000) });
    expect((await rejection(verifyAndParseInputBatch(await sign(stale), resolve))).code).toBe("COMMAND_EXPIRED");
  });

  it("applies the same order to commands: a 1.6 command with unknown fields is PROTOCOL_INCOMPATIBLE", async () => {
    const { kp, resolve } = await controller();
    const sign = (payload: unknown) => signPayload(kp.privateKey, kp.publicKey, dumpsCompact(payload));
    const start = buildCommandPayload({ ...base, action: "screen.session_start", params: { offer_sdp: OFFER }, lifetimeSeconds: 60 });
    const verified = await verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, start), resolve);
    expect([verified.spec.name, verified.params.surface, verified.params.offer_sdp]).toEqual(["screen.session_start", "primary", OFFER]);
    const newer = await rejection(verifyAndParseCommand(await sign({ ...start, protocol_version: "1.6", params: { ...start.params, hdr: true }, priority: "high" }), resolve));
    expect([newer.code, newer.detail]).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: "1.6", supported: ["1.5"] }]);
    const below = await rejection(verifyAndParseCommand(await sign({ ...start, protocol_version: "1.3", priority: "high" }), resolve));
    expect([below.code, below.detail]).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: "1.3", required: "1.4" }]);
    expect((await rejection(verifyAndParseCommand(await sign({ ...start, protocol_version: "1.4", priority: "high" }), resolve))).code).toBe("MALFORMED_MESSAGE");
    expect((await rejection(verifyAndParseCommand(await sign({ ...start, protocol_version: "x" }), resolve))).code).toBe("MALFORMED_MESSAGE");
  });
});

const DC_CASES = JSON.parse(readFileSync(resolve(here, "../../protocol/fixtures/screen-dc-cases.json"), "utf8")) as { accepted: Array<{ name: string; text: string }> };
const CURSOR_PNG = (JSON.parse(DC_CASES.accepted.find((c) => c.name === "custom cursor_shape with a real 1x1 RGBA PNG")!.text) as { png: string }).png;
const CURSOR_KINDS: CursorKind[] = ["arrow", "ibeam", "hand", "wait", "appstarting", "cross", "no", "sizeall", "sizens", "sizewe", "sizenesw", "sizenwse", "uparrow", "help"];

/** The same JSON object with spaces before its closing brace, exactly `size` bytes. */
function padded(text: string, size: number): string {
  const out = text.slice(0, -1) + " ".repeat(size - text.length) + "}";
  if (out.length !== size) throw new Error("bad padding");
  return out;
}

describe("protocol 1.5: the cursor param and content-based command stamping", () => {
  it("has the new limit, the second channel label and a cursor param without a default", () => {
    expect([LIMITS.screen_dc_cursor_shape_max_bytes, LIMITS.screen_dc_message_max_bytes]).toEqual([12288, 1024]);
    expect([SCREEN_CURSOR_CHANNEL_LABEL, SCREEN_DATA_CHANNEL_LABEL]).toEqual(["dome-screen-cursor-v1", "dome-screen-v1"]);
    const start = registry.get("screen.session_start");
    expect([start.since, start.paramSince]).toEqual(["1.4", { cursor: "1.5" }]);
    const cursor = (start.paramsSchema.properties as Record<string, Record<string, unknown>>).cursor!;
    expect(cursor.enum).toEqual(["drawn", "overlay"]);
    expect("default" in cursor).toBe(false);
    for (const bad of ["video", "", 1, null, true]) {
      expect(codeOf(() => registry.validateParams("screen.session_start", { offer_sdp: OFFER, cursor: bad })), String(bad)).toBe("INVALID_PARAMETERS");
    }
  });

  it("stamps a start 1.4 without cursor and 1.5 with it, from the raw params", () => {
    const plain = { offer_sdp: OFFER, surface: 1 };
    expect(commandProtocolVersion("screen.session_start", plain)).toBe("1.4");
    expect(minVersion("screen.session_start", plain)).toBe("1.4");
    for (const mode of ["overlay", "drawn"]) expect(commandProtocolVersion("screen.session_start", { ...plain, cursor: mode })).toBe("1.5");
    const old = buildCommandPayload({ ...base, action: "screen.session_start", params: plain, lifetimeSeconds: 60 });
    expect([old.protocol_version, "cursor" in old.params]).toEqual(["1.4", false]);
    const overlay = buildCommandPayload({ ...base, action: "screen.session_start", params: { ...plain, cursor: "overlay" }, lifetimeSeconds: 60 });
    expect([overlay.protocol_version, overlay.params.cursor]).toEqual(["1.5", "overlay"]);
    // no other action has a param_since, so a `cursor` key never moves their stamp
    for (const [name, spec] of registry.actions()) if (name !== "screen.session_start") expect([name, commandProtocolVersion(name, { cursor: "overlay" })]).toEqual([name, spec.since]);
    expect(() => commandProtocolVersion("app.nope", {})).toThrow(/UNKNOWN_ACTION/);
  });

  it("refuses a 1.4-stamped start carrying cursor after the schema, and passes one without it", async () => {
    const { kp, resolve } = await controller();
    const sign = (payload: unknown) => signPayload(kp.privateKey, kp.publicKey, dumpsCompact(payload));
    const overlay = buildCommandPayload({ ...base, action: "screen.session_start", params: { offer_sdp: OFFER, cursor: "overlay" }, lifetimeSeconds: 60 });
    const verified = await verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, overlay), resolve);
    expect([verified.payload.protocol_version, verified.params.cursor]).toEqual(["1.5", "overlay"]);
    const below = await rejection(verifyAndParseCommand(await sign({ ...overlay, protocol_version: "1.4" }), resolve));
    expect([below.code, below.detail]).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: "1.4", required: "1.5" }]);
    // judged after the schema and the params check: an unknown field or a bad value keeps its own code
    expect((await rejection(verifyAndParseCommand(await sign({ ...overlay, protocol_version: "1.4", priority: "high" }), resolve))).code).toBe("MALFORMED_MESSAGE");
    expect((await rejection(verifyAndParseCommand(await sign({ ...overlay, protocol_version: "1.4", params: { ...overlay.params, cursor: "video" } }), resolve))).code).toBe("INVALID_PARAMETERS");
    const plain = buildCommandPayload({ ...base, action: "screen.session_start", params: { offer_sdp: OFFER }, lifetimeSeconds: 60 });
    const old = await verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, plain), resolve);
    expect([old.payload.protocol_version, "cursor" in old.params, "cursor" in old.payload.params]).toEqual(["1.4", false, false]);
    expect((await verifyAndParseCommand(await sign({ ...plain, protocol_version: "1.5" }), resolve)).payload.protocol_version).toBe("1.5");
  });
});

describe("protocol 1.5: the answer stamp", () => {
  const args = { accountId: ACCOUNT, pcId: PC, controllerId: CONTROLLER, commandId: COMMAND, screenSessionId: SID, offerSdp: OFFER, answerSdp: ANSWER };

  it("carries the stamp of the start it answers; a 1.4 answer verifies", async () => {
    const pc = await pcKeyPair();
    const payload = await buildScreenAnswerPayload({ ...args, protocolVersion: "1.4" });
    expect(payload.protocol_version).toBe("1.4");
    const envelope = await signScreenAnswer(pc.kp.privateKey, pc.kp.publicKey, payload);
    const verified = await verifyScreenAnswer(envelope, { pinnedJwk: pc.jwk, accountId: ACCOUNT, controllerId: CONTROLLER, pcId: PC, commandId: COMMAND, offerSdp: OFFER });
    expect(verified.payload.protocol_version).toBe("1.4");
  });

  it("refuses a stamp this library does not speak, and the stamp is required", async () => {
    for (const bad of ["1.6", "2.0", "x", ""]) {
      const err = await rejection(buildScreenAnswerPayload({ ...args, protocolVersion: bad }));
      expect([err.code, err.detail], bad).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: bad, supported: ["1.5"] }]);
    }
    // @ts-expect-error protocolVersion is a required field (no silent default to the library's version)
    expect((await rejection(buildScreenAnswerPayload(args))).code).toBe("PROTOCOL_INCOMPATIBLE");
  });
});

describe("protocol 1.5: pointer messages and the per-type size cap", () => {
  const pos = { type: "cursor_pos", gen: 3, x: 0, y: 65535, visible: false };
  const custom = { type: "cursor_shape", kind: "custom", width: 64, height: 64, hotspot_x: 63, hotspot_y: 0, png: CURSOR_PNG };
  const state = { type: "state", state: "sending", encoder: "vp8", data_saver: false, cursor: "drawn" };

  it("accepts the pointer messages from the PC only and narrows them in the hand-written union", () => {
    const shapes = CURSOR_KINDS.map((kind) => ({ type: "cursor_shape", kind }));
    for (const msg of [pos, custom, state, { ...state, cursor: "overlay" }, { ...custom, png: "A".repeat(8192) }, ...shapes]) {
      expect(parseScreenDcMessage(dumpsCompact(msg), "pc_to_phone")).toEqual(msg);
      if (msg.type !== "state") expect(codeOf(() => parseScreenDcMessage(dumpsCompact(msg), "phone_to_pc")), JSON.stringify(msg).slice(0, 40)).toBe("MALFORMED_MESSAGE");
    }
    const parsed: ScreenDcPcToPhone = parseScreenDcMessage(dumpsCompact(custom), "pc_to_phone");
    if (parsed.type === "cursor_shape" && parsed.kind === "custom") expect([parsed.width, parsed.hotspot_x, parsed.png]).toEqual([64, 63, CURSOR_PNG]);
    else throw new Error("not narrowed to the custom shape");
    const moved: ScreenDcPcToPhone = parseScreenDcMessage(dumpsCompact(pos), "pc_to_phone");
    if (moved.type === "cursor_pos") expect([moved.gen, moved.x, moved.y, moved.visible]).toEqual([3, 0, 65535, false]);
    else throw new Error("not narrowed to cursor_pos");
    const st: ScreenDcPcToPhone = parseScreenDcMessage(dumpsCompact(state), "pc_to_phone");
    if (st.type === "state") expect(st.cursor).toBe("drawn");
    else throw new Error("not narrowed to state");
  });

  it("refuses out-of-bounds pointer messages", () => {
    const without = (o: Record<string, unknown>, key: string) => Object.fromEntries(Object.entries(o).filter(([k]) => k !== key));
    for (const bad of [
      without(pos, "visible"),
      { ...pos, gen: 0 },
      { ...pos, x: 65536 },
      { ...pos, y: -1 },
      { ...pos, on_surface: true },
      { type: "cursor_shape", kind: "pointer" },
      { type: "cursor_shape", kind: "arrow", width: 1 },
      { type: "cursor_shape", kind: "other" },
      without(custom, "png"),
      { ...custom, width: 0 },
      { ...custom, height: 65 },
      { ...custom, hotspot_x: 64 },
      { ...custom, visible: true },
      { ...custom, png: "A".repeat(28) },
      { ...custom, png: "A".repeat(32) + "=" },
      { ...custom, png: "A".repeat(8196) },
      { ...custom, png: CURSOR_PNG.slice(0, 10) + "-" + CURSOR_PNG.slice(11) },
      { ...custom, png: "AA==" + "A".repeat(32) },
      { ...custom, png: "A".repeat(32) + "\n" + "A".repeat(32) },
      { ...state, cursor: "video" },
    ]) {
      expect(codeOf(() => parseScreenDcMessage(dumpsCompact(bad as never), "pc_to_phone")), JSON.stringify(bad).slice(0, 60)).toBe("MALFORMED_MESSAGE");
    }
  });

  it("caps every message at 1024 bytes except a pc_to_phone cursor_shape (12288); every refusal is MALFORMED_MESSAGE", () => {
    const big = dumpsCompact({ ...custom, width: 1, height: 1, hotspot_x: 0, png: "A".repeat(8192) });
    const named = '{"type":"cursor_shape","kind":"arrow"}';
    const hb = '{"type":"hb"}';
    const st = '{"type":"state","state":"sending"}';
    expect(parseScreenDcMessage(padded(hb, 1024), "phone_to_pc")).toEqual({ type: "hb" });
    expect(parseScreenDcMessage(padded(st, 1024), "pc_to_phone")).toEqual({ type: "state", state: "sending" });
    expect(parseScreenDcMessage(padded(named, 1025), "pc_to_phone")).toEqual({ type: "cursor_shape", kind: "arrow" });
    for (const size of [9000, 12288]) expect((parseScreenDcMessage(padded(big, size), "pc_to_phone") as { kind: string }).kind).toBe("custom");
    const refusals: Array<[string, "phone_to_pc" | "pc_to_phone", string]> = [
      [padded(hb, 1025), "phone_to_pc", "data-channel message too long"],
      [padded(named, 1025), "phone_to_pc", "data-channel message too long"],
      [padded(st, 1025), "pc_to_phone", "only cursor_shape may exceed screen_dc_message_max_bytes"],
      [padded(big, 12289), "pc_to_phone", "data-channel message too long"],
      [padded(hb, 20000), "phone_to_pc", "data-channel message too long"],
      ['{"type":"cursor_shape","kind":"arrow","pad":"' + "é".repeat(6200) + '"}', "pc_to_phone", "data-channel message too long"], // 12400 bytes, 6240 code units
    ];
    for (const [text, direction, why] of refusals) {
      let err: unknown = null;
      try {
        if (direction === "pc_to_phone") parseScreenDcMessage(text, "pc_to_phone");
        else parseScreenDcMessage(text, "phone_to_pc");
      } catch (e) {
        err = e;
      }
      expect(err instanceof ProtocolError && err.code === "MALFORMED_MESSAGE" && err.message.includes(why), `${direction} ${text.length}`).toBe(true);
    }
  });

  it("validates result.cursor and the REST pc.protocol_versions", () => {
    const result = { screen_session_id: SID, surfaces: [{ index: 0, width: 2560, height: 1440, primary: true }], surface_index: 0, gen: 1, audio: false, lease_seconds: 8, allowance: ALLOWANCE, consent: "granted" };
    for (const mode of ["drawn", "overlay"]) expect(codeOf(() => registry.validateResult("screen.session_start", { ...result, cursor: mode }))).toBe("OK");
    expect(codeOf(() => registry.validateResult("screen.session_start", { ...result, cursor: "video" }))).toBe("MALFORMED_MESSAGE");
    const pc = { id: PC, name: "Office", enabled: true, connection: "online", last_seen: null, created_at: TS, platform: "windows" };
    for (const row of [pc, { ...pc, protocol_versions: [] }, { ...pc, protocol_versions: ["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"] }]) expect(codeOf(() => schemas.validateRest("pc", row))).toBe("OK");
    for (const versions of [["1.5\n"], ["v1.5"], ["1"], [15], Array(9).fill("1.0"), "1.5"]) {
      expect(codeOf(() => schemas.validateRest("pc", { ...pc, protocol_versions: versions })), JSON.stringify(versions)).toBe("MALFORMED_MESSAGE");
    }
  });
});

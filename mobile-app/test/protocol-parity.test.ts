/**
 * The browser bundle swaps `@dome/protocol`'s Ajv-compiling registry for `src/protocol/registry.ts`
 * (see vite.config.ts). This suite proves the swap is faithful: the generated validators are fresh,
 * the facade agrees with the real module on accept/reject decisions and error codes, and envelopes
 * signed through the swapped library verify with the real one.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Through the package (registry substituted by the Vite plugin — the code path the app ships).
import * as viaPackage from "@dome/protocol";
import { buildCommandPayload, generateControllerKeyPair, signCommand, verifyAndParseCommand, exportPublicJwk, kidFromJwk, ProtocolError, type JsonValue } from "@dome/protocol";
// The real module, imported by path (the plugin only redirects imports made from inside shared/ts/src).
import * as realRegistry from "../../shared/ts/src/registry.ts";
import * as pwaRegistry from "../src/protocol/registry.ts";
import { generateValidatorsSource, generateDtsSource, OUTPUT_JS, OUTPUT_DTS } from "../scripts/gen-validators.ts";

const UUID = "33333333-3333-4333-8333-333333333333";
const UUID2 = "11111111-1111-4111-8111-111111111111";
const TS = "2026-10-08T12:00:00.000Z";

/** Valid params/targets per action and target schema, so every action can be built through the facade. */
const SAMPLE_PARAMS: Record<string, Record<string, JsonValue>> = {
  "youtube.set_paused": { paused: true },
  "youtube.seek_relative": { seconds: 10 },
  "youtube.seek_to": { position_seconds: 5 },
  "youtube.set_muted": { muted: true },
  "youtube.set_volume": { value: 10 },
  "youtube.set_theater": { enabled: true },
  "youtube.search": { query: "lofi" },
  "youtube.open_result": { index: 1 },
  "media.set_paused": { paused: true },
  "windows.set_volume": { value: 10 },
  "windows.set_muted": { muted: true },
  "app.launch": { app_id: "notepad" },
  "app.set_fullscreen": { enabled: true },
  "input.session_stop": { input_session_id: "S".repeat(22) },
  // protocol 1.4 (Screen view): the offer travels inside the signed command; the stop names the agent-issued id
  "screen.session_start": { offer_sdp: "v=0\r\no=- 1 2 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 40:41:42:43:44:45:46:47:48:49:4A:4B:4C:4D:4E:4F:50:51:52:53:54:55:56:57:58:59:5A:5B:5C:5D:5E:5F\r\n" },
  "screen.session_stop": { screen_session_id: "S".repeat(22) },
};
const SAMPLE_TARGETS: Record<string, Record<string, JsonValue>> = {
  youtube_target: { browser_instance_id: "abcdefgh", tab_id: 1, tab_token: "A".repeat(22) },
  youtube_browser_target: { browser_instance_id: "abcdefgh" },
  media_session_target: { session_id: "s1" },
  window_target: { app_id: "notepad" },
};

function codeOf(fn: () => unknown): string {
  try {
    fn();
    return "OK";
  } catch (e) {
    if (e instanceof ProtocolError) return e.code;
    throw e;
  }
}

describe("protocol facade parity", () => {
  it("the Vite plugin substituted the registry inside the package", () => {
    expect(viaPackage.registry).toBe(pwaRegistry.registry);
    expect(viaPackage.registry).not.toBe(realRegistry.registry);
    expect(viaPackage.schemas).toBe(pwaRegistry.schemas);
  });

  it("generated validators are up to date with shared/protocol", () => {
    expect(readFileSync(OUTPUT_JS, "utf8")).toBe(generateValidatorsSource());
    expect(readFileSync(OUTPUT_DTS, "utf8")).toBe(generateDtsSource());
    expect(readFileSync(OUTPUT_JS, "utf8")).not.toMatch(/new Function|\beval\(/);
  });

  it("exposes the same constants and action specs", () => {
    expect(pwaRegistry.PROTOCOL_VERSION).toBe(realRegistry.PROTOCOL_VERSION);
    expect(pwaRegistry.REGISTRY_VERSION).toBe(realRegistry.REGISTRY_VERSION);
    expect(pwaRegistry.LIMITS).toEqual(realRegistry.LIMITS);
    expect(pwaRegistry.PLANS).toEqual(realRegistry.PLANS);
    expect(pwaRegistry.ERRORS).toEqual(realRegistry.ERRORS);
    expect(pwaRegistry.CAPABILITIES).toEqual(realRegistry.CAPABILITIES);
    expect([...pwaRegistry.registry.actions().entries()]).toEqual([...realRegistry.registry.actions().entries()]);
    expect(Object.keys(pwaRegistry.registry).sort()).toEqual(Object.keys(realRegistry.registry).sort());
    expect(Object.keys(pwaRegistry.schemas).sort()).toEqual(Object.keys(realRegistry.schemas).sort());
  });

  it("agrees on params, targets and results for every action", () => {
    const samples: Record<string, unknown[]> = {
      params: [
        {},
        { paused: true },
        { paused: "yes" },
        { value: 50 },
        { value: 101 },
        { seconds: -10 },
        { seconds: 0 },
        { position_seconds: 12.5 },
        { app_id: "discord" },
        { app_id: "Dis cord" },
        { countdown_seconds: 10 },
        { countdown_seconds: 61 },
        { extra: 1 },
        { muted: false },
        { enabled: true },
        { enabled: "yes" },
        // protocol 1.2: youtube.search / youtube.open_result
        { query: "lofi beats" },
        { query: "Ünïcode ✓ 🎵" },
        { query: "" },
        { query: " leading" },
        { query: "x\n" },
        { query: "x\u0085" },
        { query: "x".repeat(100) },
        { query: "x".repeat(101) },
        { index: 2 },
        { index: 2, video_id: "dQw4w9WgXcQ" },
        { index: 0 },
        { index: 11 },
        { index: 1, video_id: "bad id" },
      ],
      target: [
        null,
        {},
        { browser_instance_id: "abcdefgh", tab_id: 1, tab_token: "A".repeat(22) },
        { browser_instance_id: "abcdefgh", tab_id: 1 },
        { browser_instance_id: "abcdefgh" },
        { browser_instance_id: "abcdefgh", tab_token: "A".repeat(22) },
        { browser_instance_id: "short" },
        { session_id: "s1" },
        { app_id: "notepad" },
        { app_id: "notepad", window_id: "w1" },
        { app_id: "Bad Id" },
      ],
      result: [
        {},
        { value: 35, muted: false },
        { value: 135, muted: false },
        { accepted: true },
        { accepted: false },
        { tabs: [] },
        { tab: { browser_instance_id: "b", tab_id: 1, script_attached: true, context: "watch", ad_showing: false, is_live: false, in_playlist: false } },
        { sessions: [] },
        { session: { session_id: "s", status: "playing", controls: ["pause"] } },
        { apps: [{ app_id: "notepad", display_name: "Notepad", running: true, windows: [] }] },
        { app_id: "notepad", launched: true, running: true },
        { app_id: "notepad", focused: false },
        { app_id: "notepad", minimized: true },
        { app_id: "notepad", closed: false },
        { accepted: true, countdown_seconds: 10, fires_at: TS },
        { canceled: false },
        { agent_time: TS, agent_version: "0.1", protocol_version: "1.1" },
        { remote_enabled: true, session_locked: false, extension_connected: false },
        // protocol 1.2: app_window_state_result, youtube_search_result
        { app_id: "notepad", window_id: "w1", state: "maximized", fullscreen: false, foreground: true },
        { app_id: "notepad", window_id: "w1", state: "fullscreen", fullscreen: true, foreground: true },
        { app_id: "notepad", window_id: "w1", state: "normal", fullscreen: false },
        { tab: { browser_instance_id: "b", tab_id: 1, script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false }, results: [{ index: 1, video_id: "abc", title: "A", channel: "C", duration_seconds: 61, is_live: false }] },
        { tab: { browser_instance_id: "b", tab_id: 1, script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false }, results: Array.from({ length: 11 }, (_, i) => ({ index: (i % 10) + 1, video_id: "abc", title: "A" })) },
        { tab: { browser_instance_id: "b", tab_id: 1, script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false }, results: [{ index: 1, video_id: "abc" }] },
      ],
    };
    for (const [action] of realRegistry.registry.actions()) {
      for (const p of samples.params!) {
        expect(codeOf(() => pwaRegistry.registry.validateParams(action, p)), `${action} params ${JSON.stringify(p)}`).toBe(codeOf(() => realRegistry.registry.validateParams(action, p)));
      }
      for (const t of samples.target!) {
        expect(codeOf(() => pwaRegistry.registry.validateTarget(action, t)), `${action} target ${JSON.stringify(t)}`).toBe(codeOf(() => realRegistry.registry.validateTarget(action, t)));
      }
      for (const r of samples.result!) {
        expect(codeOf(() => pwaRegistry.registry.validateResult(action, r)), `${action} result ${JSON.stringify(r)}`).toBe(codeOf(() => realRegistry.registry.validateResult(action, r)));
      }
    }
    expect(pwaRegistry.registry.validateParams("power.sleep", {})).toEqual({ countdown_seconds: 10 });
  });

  it("agrees on relay frames, challenges and REST bodies", () => {
    const frames: unknown[] = [
      { type: "ping" },
      { type: "ping", extra: 1 },
      { type: "hello_ack", protocol_version: "1.1", server_time: TS, connection_id: UUID },
      { type: "hello_ack", protocol_version: "1.1", server_time: TS, connection_id: UUID, controller_id: UUID2 },
      { type: "result", command_id: UUID, origin: "relay", state: "failed", at: TS, duration_ms: 0, error: { code: "PC_OFFLINE", message: "x", retryable: true } },
      { type: "result", command_id: UUID, origin: "relay", state: "accepted", at: TS, duration_ms: 0 },
      { type: "ack", command_id: UUID, state: "executing", at: TS },
      { type: "state", pc_id: UUID, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [] } },
      { type: "pc_status", pc_id: UUID, connection: "online", last_seen: TS },
      { type: "revoked", reason: "controller_revoked" },
      { type: "confirmation_required", command_id: UUID, challenge_text: "{}" },
      { type: "grants_snapshot" },
      { type: "subscribe", pc_ids: [UUID] },
      { type: "subscribe", pc_ids: [] },
      { type: "hello", component: "controller", kid: "A".repeat(43), component_version: "0.1", protocol_versions: ["1.0"], registry_version: "1.0" },
      { type: "command", pc_id: UUID, envelope: { v: 1, alg: "ES256", kid: "A".repeat(43), payload: "{}", sig: "B".repeat(86) } },
      { type: "cancel", pc_id: UUID, command_id: UUID },
      // protocol 1.4 (Screen view): the four frames the phone sends or receives, plus shapes it must refuse
      { type: "screen_ice_request", pc_id: UUID },
      { type: "screen_ice_request" },
      { type: "screen_ice_servers", pc_id: UUID, ice_servers: [{ urls: ["stun:stun.example.net:3478"] }], expires_at: null, allowance: { unlimited: false, remaining_seconds: 600, resets_at: null }, turn: "unavailable" },
      { type: "screen_ice_servers", pc_id: UUID, ice_servers: [{ urls: ["http://not-ice"] }], expires_at: null, allowance: { unlimited: true, remaining_seconds: null, resets_at: null }, turn: "available" },
      { type: "screen_answer", pc_id: UUID, envelope: { v: 1, alg: "ES256", kid: "A".repeat(43), payload: "{}", sig: "B".repeat(86) } },
      { type: "screen_answer", pc_id: UUID, envelope: { v: 1, alg: "ES256", kid: "A".repeat(43), payload: "{}", sig: "B".repeat(86) }, ref_controller_id: UUID2 },
      { type: "screen_session", pc_id: UUID, screen_session_id: "S".repeat(22), controller_id: UUID2, event: "ended", reason: "stopped_on_pc", live_seconds: 12, at: TS },
      { type: "screen_session", pc_id: UUID, screen_session_id: "S".repeat(22), controller_id: UUID2, event: "started", reason: "started", path: "direct", live_seconds: 0, at: TS },
      { type: "screen_session", pc_id: UUID, screen_session_id: "S".repeat(22), controller_id: UUID2, event: "ended", reason: "made_up", live_seconds: 0, at: TS },
      { type: "screen_usage", pc_id: UUID, screen_session_id: "S".repeat(22), live_seconds: 1, relayed: false, relayed_bytes: 0 },
    ];
    for (const dir of ["relay_to_controller", "controller_to_relay"] as const) {
      for (const f of frames) {
        expect(codeOf(() => pwaRegistry.schemas.validateFrame(dir, f)), `${dir} ${JSON.stringify(f)}`).toBe(codeOf(() => realRegistry.schemas.validateFrame(dir, f)));
      }
    }
    const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
    const challengeTexts = [fixture.digests.challenge_text, "{}", '{"challenge_id":"x"}', "[]", fixture.digests.challenge_text.replace("}}", ',"extra":1}}')];
    for (const c of challengeTexts) {
      expect(codeOf(() => pwaRegistry.schemas.validateChallengeText(c))).toBe(codeOf(() => realRegistry.schemas.validateChallengeText(c)));
    }
    expect(pwaRegistry.schemas.validateChallengeText(fixture.digests.challenge_text)).toEqual(realRegistry.schemas.validateChallengeText(fixture.digests.challenge_text));

    const session = { account: { id: UUID2, email: "a@example.test", display_name: "A", created_at: TS }, csrf_token: "C".repeat(40), plan: "free", limits: { max_enabled_pcs: 1, max_controllers: 2, routines: false, custom_layouts: false }, protocol_version: "1.1" };
    const bodies: Array<[string, unknown]> = [
      ["session_response", session],
      ["session_response", { ...session, plan: "gold" }],
      ["session_response", { ...session, extra: 1 }],
      ["pcs_response", { pcs: [] }],
      ["pcs_response", { pcs: [{ id: UUID, name: "Office", enabled: true, connection: "online", last_seen: null, created_at: TS, platform: "windows" }] }],
      ["controllers_response", { controllers: [{ id: UUID, kid: "A".repeat(43), display_name: "Phone", status: "active", created_at: TS, last_seen: null }] }],
      ["grants_response", { grants: [{ id: UUID, controller_id: UUID, pc_id: UUID, capabilities: ["media"], created_at: TS }] }],
      ["pairing_status_response", { pairing_id: UUID, state: "claimed", pc_id: UUID, pc_name: "Office", expires_at: TS, pc_online: true }],
      ["pairing_status_response", { pairing_id: UUID, state: "pending", pc_id: UUID, pc_name: "Office", expires_at: TS }],
      ["error_body", { error: { code: "PC_OFFLINE", message: "x", retryable: true } }],
      ["error_body", { error: { code: "lower", message: "x", retryable: true } }],
      ["plans_response", { plans: {}, pricing: { currency: "USD", monthly_cents: 599, annual_cents: 4999 }, billing_enabled: false }],
      ["agent_link_preview_response", { user_code: "ABCD-EFGH", agent_version: "0.1", platform: "windows", kid: "A".repeat(43), expires_at: TS }],
      ["agent_link_approve_response", { pc_id: UUID, enabled: false, reason: "DEVICE_LIMIT_REACHED" }],
      ["agent_link_poll_response", { pc_id: UUID, account_id: UUID2, pc_credential: "A".repeat(43), relay_url: "wss://x/ws", api_url: "https://x", pc_name: "a", enabled: true }],
      ["agent_link_poll_response", { pc_id: UUID, account_id: UUID2, pc_credential: "A".repeat(43), relay_url: "nope", api_url: "https://x", pc_name: "a", enabled: true }],
      ["commands_response", { commands: [{ command_id: UUID, pc_id: UUID, controller_id: UUID, action: "youtube.next", state: "succeeded", created_at: TS }] }],
      ["security_events_response", { events: [{ id: 1, kind: "login", severity: "info", actor: "account", created_at: TS }] }],
    ];
    for (const [name, body] of bodies) {
      expect(codeOf(() => pwaRegistry.schemas.validateRest(name, body)), `${name} ${JSON.stringify(body)}`).toBe(codeOf(() => realRegistry.schemas.validateRest(name, body)));
    }
  });

  it("agrees on the PC-signed screen answer payload and both data-channel vocabularies (1.4)", () => {
    const answer = { type: "screen_answer", protocol_version: "1.4", account_id: UUID2, pc_id: UUID, controller_id: UUID2, command_id: UUID, screen_session_id: "S".repeat(22), offer_digest: "A".repeat(43), answer_sdp: "v=0\r\n", issued_at: TS, expires_at: TS };
    const answers: unknown[] = [answer, { ...answer, answer_sdp: "" }, { ...answer, answer_sdp: "v=0\nbare lf passes the pattern" }, { ...answer, answer_sdp: "ünïcode" }, { ...answer, extra: 1 }, { ...answer, type: "command" }, {}];
    for (const a of answers) expect(codeOf(() => pwaRegistry.schemas.validateScreenAnswerPayload(a)), JSON.stringify(a).slice(0, 80)).toBe(codeOf(() => realRegistry.schemas.validateScreenAnswerPayload(a)));
    const messages: unknown[] = [
      { type: "hb" },
      { type: "keyframe" },
      { type: "bye" },
      { type: "audio", on: true },
      { type: "audio" },
      { type: "geometry", gen: 1, surface_index: 0, width: 1920, height: 1080, rotation: 0, reserved: [] },
      { type: "geometry", gen: 1, surface_index: 0, width: 1920, height: 1080, rotation: 45, reserved: [] },
      { type: "state", state: "paused", reason: "secure_desktop" },
      { type: "state", state: "sending", encoder: "vp8", data_saver: true },
      { type: "state", state: "off" },
      { type: "allowance", unlimited: false, remaining_seconds: 480, resets_at: TS },
      { type: "restricted", restricted: true, reason: "elevated" },
      { type: "roi", gen_seen: 1 },
      { type: "input", events: [] },
      {},
    ];
    for (const dir of ["pc_to_phone", "phone_to_pc"] as const) {
      for (const m of messages) expect(codeOf(() => pwaRegistry.schemas.validateScreenDcMessage(dir, m)), `${dir} ${JSON.stringify(m)}`).toBe(codeOf(() => realRegistry.schemas.validateScreenDcMessage(dir, m)));
    }
    // a message no side may ever accept: the vocabulary is closed in both directions
    expect(codeOf(() => pwaRegistry.schemas.validateScreenDcMessage("pc_to_phone", { type: "input", events: [] }))).toBe("MALFORMED_MESSAGE");
    expect(codeOf(() => pwaRegistry.schemas.validateScreenDcMessage("phone_to_pc", { type: "geometry", gen: 1, surface_index: 0, width: 1, height: 1, rotation: 0, reserved: [] }))).toBe("MALFORMED_MESSAGE");
  });

  it("protocolCompatible matches", () => {
    for (const [peer, supported] of [["1.0", ["1.0"]], ["1.1", ["1.0"]], ["1.0", ["1.2"]], ["2.0", ["1.0"]], ["x", ["1.0"]]] as const) {
      expect(pwaRegistry.protocolCompatible(peer, supported)).toBe(realRegistry.protocolCompatible(peer, supported));
    }
  });

  it("both registries agree on every action's `since`, and the facade stamps commands with it", () => {
    for (const [name, spec] of realRegistry.registry.actions()) {
      expect(pwaRegistry.registry.get(name).since, name).toBe(spec.since);
      const payload = buildCommandPayload({ accountId: UUID2, controllerId: UUID, targetPcId: UUID, action: name, params: SAMPLE_PARAMS[name] ?? {}, target: SAMPLE_TARGETS[spec.targetName ?? ""] ?? null });
      expect(payload.protocol_version, name).toBe(spec.since);
    }
    expect(pwaRegistry.registry.get("screen.session_start").since).toBe("1.4");
    expect(pwaRegistry.registry.get("screen.session_stop").since).toBe("1.4");
    expect(pwaRegistry.registry.get("app.set_fullscreen").since).toBe("1.2");
    expect(pwaRegistry.registry.get("input.session_start").since).toBe("1.1");
    expect(pwaRegistry.registry.get("app.list").since).toBe("1.0");
  });

  it("commands built and signed through the swapped library verify with the real verifier", async () => {
    const pair = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(pair.publicKey);
    const kid = await kidFromJwk(jwk);
    const payload = buildCommandPayload({ accountId: UUID2, controllerId: UUID, targetPcId: UUID, action: "windows.set_volume", params: { value: 35 } });
    const envelope = await signCommand(pair.privateKey, pair.publicKey, payload);
    expect(envelope.kid).toBe(kid);
    const verified = await verifyAndParseCommand(envelope, (k) => (k === kid ? { controllerId: UUID, accountId: UUID2, jwk } : null));
    expect(verified.params).toEqual({ value: 35 });
    expect(verified.spec.name).toBe("windows.set_volume");
    expect(() => buildCommandPayload({ accountId: UUID2, controllerId: UUID, targetPcId: UUID, action: "windows.set_volume", params: { value: 135 } })).toThrow(/INVALID_PARAMETERS/);

    // A 1.2 action is stamped 1.2 and verifies; the same command stamped below its since is refused by the real verifier.
    const search = buildCommandPayload({ accountId: UUID2, controllerId: UUID, targetPcId: UUID, action: "youtube.search", params: { query: "lofi" }, target: { browser_instance_id: "abcdefgh" } });
    expect(search.protocol_version).toBe("1.2");
    const resolve = (k: string) => (k === kid ? { controllerId: UUID, accountId: UUID2, jwk } : null);
    expect((await verifyAndParseCommand(await signCommand(pair.privateKey, pair.publicKey, search), resolve)).payload.protocol_version).toBe("1.2");
    await expect(verifyAndParseCommand(await signCommand(pair.privateKey, pair.publicKey, { ...search, protocol_version: "1.1" }), resolve)).rejects.toThrow(/PROTOCOL_INCOMPATIBLE/);
  });
});

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
import { buildCommandPayload, generateControllerKeyPair, signCommand, verifyAndParseCommand, exportPublicJwk, kidFromJwk, ProtocolError } from "@dome/protocol";
// The real module, imported by path (the plugin only redirects imports made from inside shared/ts/src).
import * as realRegistry from "../../shared/ts/src/registry.ts";
import * as pwaRegistry from "../src/protocol/registry.ts";
import { generateValidatorsSource, generateDtsSource, OUTPUT_JS, OUTPUT_DTS } from "../scripts/gen-validators.ts";

const UUID = "33333333-3333-4333-8333-333333333333";
const UUID2 = "11111111-1111-4111-8111-111111111111";
const TS = "2026-10-08T12:00:00.000Z";

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
      params: [{}, { paused: true }, { paused: "yes" }, { value: 50 }, { value: 101 }, { seconds: -10 }, { seconds: 0 }, { position_seconds: 12.5 }, { app_id: "discord" }, { app_id: "Dis cord" }, { countdown_seconds: 10 }, { countdown_seconds: 61 }, { extra: 1 }, { muted: false }, { enabled: true }],
      target: [null, {}, { browser_instance_id: "abcdefgh", tab_id: 1, tab_token: "A".repeat(22) }, { browser_instance_id: "abcdefgh", tab_id: 1 }, { session_id: "s1" }, { app_id: "notepad" }, { app_id: "notepad", window_id: "w1" }, { app_id: "Bad Id" }],
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

  it("protocolCompatible matches", () => {
    for (const [peer, supported] of [["1.0", ["1.0"]], ["1.1", ["1.0"]], ["1.0", ["1.2"]], ["2.0", ["1.0"]], ["x", ["1.0"]]] as const) {
      expect(pwaRegistry.protocolCompatible(peer, supported)).toBe(realRegistry.protocolCompatible(peer, supported));
    }
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
  });
});

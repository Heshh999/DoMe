import { describe, expect, it } from "vitest";

import { CAPABILITIES, ERRORS, PROTOCOL_VERSION, ProtocolError, protocolCompatible, registry, schemas } from "../src/index.ts";

describe("registry", () => {
  it("loads a consistent action registry", () => {
    expect(PROTOCOL_VERSION).toBe("1.0");
    for (const [name, spec] of registry.actions()) {
      expect(spec.name).toBe(name);
      expect(spec.paramsSchema.additionalProperties).toBe(false);
      expect(CAPABILITIES[spec.capability]).toBeDefined();
      if (spec.risk === "disruptive") {
        expect(spec.confirmation).toBe("challenge");
        expect(spec.routineAllowed).toBe(false);
      }
      if (spec.routineAllowed) expect(spec.confirmation).toBe("none");
    }
    expect(Object.keys(ERRORS).every((c) => /^[A-Z_]+$/.test(c))).toBe(true);
  });

  it("validates params and targets", () => {
    expect(registry.validateParams("youtube.set_volume", { value: 50 })).toEqual({ value: 50 });
    expect(() => registry.validateParams("youtube.set_volume", { value: 101 })).toThrow(ProtocolError);
    expect(() => registry.validateParams("youtube.seek_relative", { seconds: 0 })).toThrow(/INVALID_PARAMETERS/);
    expect(() => registry.validateParams("windows.lock", { extra: 1 })).toThrow(/INVALID_PARAMETERS/);
    expect(() => registry.validateTarget("youtube.next", null)).toThrow(/TARGET_REQUIRED/);
    expect(() => registry.validateTarget("youtube.next", { browser_instance_id: "abcdefgh", tab_id: 1 })).toThrow(/INVALID_PARAMETERS/);
    expect(registry.validateTarget("youtube.next", { browser_instance_id: "abcdefgh", tab_id: 1, tab_token: "AAAAAAAAAAAAAAAAAAAAAA" })).toBeTruthy();
    expect(() => registry.validateTarget("windows.lock", { app_id: "x" })).toThrow(/INVALID_PARAMETERS/);
    expect(() => registry.validateTarget("app.focus", {})).toThrow(/INVALID_PARAMETERS/);
    expect(registry.validateTarget("app.focus", { app_id: "discord" })).toEqual({ app_id: "discord" });
    expect(() => registry.get("nope.nope")).toThrow(/UNKNOWN_ACTION/);
  });

  it("validates frames by direction", () => {
    schemas.validateFrame("controller_to_relay", { type: "ping" });
    schemas.validateFrame("controller_to_relay", { type: "subscribe", pc_ids: ["33333333-3333-4333-8333-333333333333"] });
    expect(() => schemas.validateFrame("controller_to_relay", { type: "ping", extra: 1 })).toThrow(ProtocolError);
    expect(() => schemas.validateFrame("controller_to_relay", { type: "grants_snapshot" })).toThrow(ProtocolError);
    schemas.validateFrame("agent_to_relay", { type: "ack", command_id: "33333333-3333-4333-8333-333333333333", state: "accepted", at: "2026-10-08T12:00:00.000Z" });
    expect(() => schemas.validateFrame("agent_to_relay", { type: "ack", command_id: "33333333-3333-4333-8333-333333333333", state: "succeeded", at: "2026-10-08T12:00:00.000Z" })).toThrow(ProtocolError);
    expect(() => schemas.validateFrame("agent_to_relay", { type: "result", command_id: "33333333-3333-4333-8333-333333333333", state: "succeeded", at: "2026-10-08T12:00:00.000Z", duration_ms: 1 })).toThrow(ProtocolError); // origin required
    schemas.validateFrame("relay_to_controller", { type: "result", command_id: "33333333-3333-4333-8333-333333333333", origin: "relay", state: "failed", at: "2026-10-08T12:00:00.000Z", duration_ms: 0, error: { code: "PC_OFFLINE", message: "x", retryable: true } });
    schemas.validateFrame("relay_to_agent", { type: "revoked", reason: "pc_unlinked" });
    schemas.validateFrame("agent_to_relay", { type: "revoke_controller", controller_id: "33333333-3333-4333-8333-333333333333", kid: "A".repeat(43), reason: "local_revocation" });
    const snap = { type: "grants_snapshot", pc_id: "33333333-3333-4333-8333-333333333333", account_id: "11111111-1111-4111-8111-111111111111", snapshot_id: "33333333-3333-4333-8333-333333333333", pc_enabled: true, controllers: [{ controller_id: "22222222-2222-4222-8222-222222222222", kid: "A".repeat(43), capabilities: ["media"], display_name: "Phone", status: "active" }] };
    schemas.validateFrame("controller_to_relay", { type: "hello", component: "controller", kid: "A".repeat(43), component_version: "0.1", protocol_versions: ["1.0"], registry_version: "1.0" });
    expect(registry.validateResult("windows.set_volume", { value: 35, muted: false })).toEqual({ value: 35, muted: false });
    expect(() => registry.validateResult("windows.set_volume", { value: 135, muted: false })).toThrow(ProtocolError);
    expect(() => registry.validateResult("windows.lock", { accepted: false })).toThrow(ProtocolError);
    schemas.validateRest("agent_token_response", { access_token: "A".repeat(43), expires_in: 3600, pc_id: "33333333-3333-4333-8333-333333333333", account_id: "11111111-1111-4111-8111-111111111111" });
    expect(() => schemas.validateRest("agent_token_response", { access_token: "A".repeat(43) })).toThrow(ProtocolError);
    const poll = { pc_id: "33333333-3333-4333-8333-333333333333", account_id: "11111111-1111-4111-8111-111111111111", pc_credential: "A".repeat(43), relay_url: "wss://relay.example/ws/agent", api_url: "https://api.example", pc_name: "Office", enabled: true };
    schemas.validateRest("agent_link_poll_response", poll);
    expect(() => schemas.validateRest("agent_link_poll_response", { ...poll, api_url: "not a url" })).toThrow(ProtocolError);
    schemas.validateFrame("relay_to_agent", snap);
    expect(() => schemas.validateFrame("relay_to_agent", { ...snap, controllers: [{ ...snap.controllers[0], public_jwk: { kty: "EC", crv: "P-256", x: "A".repeat(43), y: "A".repeat(43) } }] })).toThrow(ProtocolError);
    expect(() => schemas.validateFrame("relay_to_agent", { ...snap, controllers: [{ ...snap.controllers[0], capabilities: ["shell"] }] })).toThrow(ProtocolError);
    expect(() => schemas.validateFrame("agent_to_relay", { type: "ack", command_id: "x", state: "accepted", at: "2026-10-08T12:00:00.000Z" })).toThrow(ProtocolError);
    schemas.validateBridgeFrame("agent_to_extension", { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "next", args: { tab_id: 1 } });
    expect(() => schemas.validateBridgeFrame("agent_to_extension", { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "eval", args: {} })).toThrow(ProtocolError);
  });

  it("negotiates versions", () => {
    expect(protocolCompatible("1.0", ["1.2"])).toBe(true);
    expect(protocolCompatible("1.3", ["1.2"])).toBe(false);
    expect(protocolCompatible("2.0", ["1.2"])).toBe(false);
    expect(protocolCompatible("x", ["1.0"])).toBe(false);
    expect(protocolCompatible("1.0\n", ["1.0"])).toBe(false);
    expect(protocolCompatible("１.０", ["1.0"])).toBe(false);
  });
});

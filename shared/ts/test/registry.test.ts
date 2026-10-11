import { describe, expect, it } from "vitest";

import { CAPABILITIES, ERRORS, LIMITS, PROTOCOL_VERSION, REGISTRY_VERSION, ProtocolError, minVersion, negotiateVersion, protocolCompatible, registry, schemas, capabilitySatisfied, versionBelow } from "../src/index.ts";

const NEW_IN_1_2 = ["app.maximize", "app.restore", "app.set_fullscreen", "youtube.search", "youtube.open_result"];
const TAB = { browser_instance_id: "abcdefgh", tab_id: 5, tab_token: "AAAAAAAAAAAAAAAAAAAAAA" };
const RESULTS_TAB = { ...TAB, tab_token: "BBBBBBBBBBBBBBBBBBBBBB", script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false };

describe("registry", () => {
  it("loads a consistent action registry", () => {
    expect(PROTOCOL_VERSION).toBe("1.2");
    expect(REGISTRY_VERSION).toBe("1.2");
    const start = registry.get("input.session_start");
    expect(capabilitySatisfied(start, ["keyboard"])).toBe(true);
    expect(capabilitySatisfied(start, ["media"])).toBe(false);
    expect(start.aiEligible).toBe(false);
    expect(start.routineAllowed).toBe(false);
    expect(registry.get("youtube.next").aiEligible).toBe(true);
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
    // rules.bridge_negotiation (the full table is in the shared negotiation_cases)
    expect(negotiateVersion(["1.0"], ["1.0", "1.1"])).toBe("1.0");
    expect(negotiateVersion(["1.0", "1.1", "1.2"], ["1.0", "1.1"])).toBe("1.1");
    expect(negotiateVersion(["2.0"], ["1.0", "1.1"])).toBeNull();
    expect(versionBelow("1.1", "1.2")).toBe(true);
    expect(versionBelow("1.2", "1.2")).toBe(false);
    expect(versionBelow("1.10", "1.2")).toBe(false);
    expect(() => versionBelow("x", "1.2")).toThrow();
  });
});

describe("protocol 1.2", () => {
  it("marks every action with the version that introduced it", () => {
    for (const [name, spec] of registry.actions()) {
      const expected = NEW_IN_1_2.includes(name) ? "1.2" : name.startsWith("input.") ? "1.1" : "1.0";
      expect([name, spec.since, minVersion(name)]).toEqual([name, expected, expected]);
    }
    expect(() => minVersion("app.nope")).toThrow(/UNKNOWN_ACTION/);
    const fullscreen = registry.get("app.set_fullscreen");
    expect([fullscreen.capability, fullscreen.targetName, fullscreen.resultName, fullscreen.risk, fullscreen.idempotent, fullscreen.routineAllowed]).toEqual(["apps", "window_target", "app_window_state_result", "moderate", false, false]);
    expect(registry.get("app.maximize").routineAllowed).toBe(true);
    expect(registry.get("youtube.search").targetName).toBe("youtube_browser_target");
    expect(registry.get("youtube.search").coalesce).toBeNull();
    for (const code of ["WINDOW_STATE_UNCHANGED", "FULLSCREEN_UNSUPPORTED", "APP_ELEVATED", "SEARCH_RESULT_UNAVAILABLE", "EXTENSION_UPDATE_REQUIRED"]) {
      expect(ERRORS[code]?.retryable).toBe(false);
    }
    expect(LIMITS.youtube_search_query_max_chars).toBe(100);
    expect(LIMITS.youtube_search_results_max).toBe(10);
  });

  it("validates the new params and the youtube_browser_target (tab_id and tab_token only together)", () => {
    expect(registry.validateParams("youtube.search", { query: "lofi beats" })).toEqual({ query: "lofi beats" });
    expect(registry.validateParams("youtube.search", { query: "🎵".repeat(100) })).toBeTruthy(); // 100 code points
    for (const params of [{}, { query: "" }, { query: "x".repeat(101) }, { query: "lofi\n" }, { query: " lofi" }, { query: "lo\u0085fi" }, { query: "lofi", open_index: 1 }]) {
      expect(() => registry.validateParams("youtube.search", params)).toThrow(/INVALID_PARAMETERS/);
    }
    expect(registry.validateTarget("youtube.search", { browser_instance_id: "abcdefgh" })).toEqual({ browser_instance_id: "abcdefgh" });
    expect(registry.validateTarget("youtube.search", TAB)).toEqual(TAB);
    expect(() => registry.validateTarget("youtube.search", { browser_instance_id: "abcdefgh", tab_id: 5 })).toThrow(/INVALID_PARAMETERS/);
    expect(() => registry.validateTarget("youtube.search", { browser_instance_id: "abcdefgh", tab_token: TAB.tab_token })).toThrow(/INVALID_PARAMETERS/);
    expect(() => registry.validateTarget("youtube.search", { tab_id: 5, tab_token: TAB.tab_token })).toThrow(/INVALID_PARAMETERS/);
    expect(() => registry.validateTarget("youtube.search", null)).toThrow(/TARGET_REQUIRED/);
    expect(registry.validateParams("youtube.open_result", { index: 3, video_id: "dQw4w9WgXcQ" })).toBeTruthy();
    for (const params of [{ index: 0 }, { index: 11 }, { index: "1" }, { index: 1, video_id: "../x" }, {}]) {
      expect(() => registry.validateParams("youtube.open_result", params)).toThrow(/INVALID_PARAMETERS/);
    }
    expect(() => registry.validateTarget("youtube.open_result", { browser_instance_id: "abcdefgh" })).toThrow(/INVALID_PARAMETERS/);
    expect(registry.validateParams("app.set_fullscreen", { enabled: true })).toEqual({ enabled: true });
    expect(() => registry.validateParams("app.set_fullscreen", {})).toThrow(/INVALID_PARAMETERS/);
    expect(() => registry.validateParams("app.maximize", { state: "maximized" })).toThrow(/INVALID_PARAMETERS/);
    expect(registry.validateTarget("app.restore", { app_id: "notepad", window_id: "h1234" })).toBeTruthy();
  });

  it("never echoes a rejected query in the error message", () => {
    const sentinel = `SENTINEL-${"q".repeat(100)}`;
    for (const query of [sentinel, `${sentinel.slice(0, 20)}\n`, ` ${sentinel.slice(0, 20)}`]) {
      try {
        registry.validateParams("youtube.search", { query });
        throw new Error("accepted");
      } catch (e) {
        expect(e).toBeInstanceOf(ProtocolError);
        expect(String((e as ProtocolError).message)).not.toContain("SENTINEL");
      }
    }
  });

  it("validates the new result shapes and leaves the 1.1 shapes unchanged", () => {
    const state = { app_id: "notepad", window_id: "h1234", state: "maximized", fullscreen: false, foreground: true };
    for (const action of ["app.maximize", "app.restore", "app.set_fullscreen"]) expect(registry.validateResult(action, state)).toEqual(state);
    expect(() => registry.validateResult("app.maximize", { ...state, state: "fullscreen" })).toThrow(ProtocolError);
    expect(() => registry.validateResult("app.maximize", { ...state, title: "x" })).toThrow(ProtocolError);
    const items = Array.from({ length: 10 }, (_, i) => ({ index: i + 1, video_id: `vid${i + 1}`, title: `Video ${i + 1}` }));
    expect(registry.validateResult("youtube.search", { tab: RESULTS_TAB, results: items })).toBeTruthy();
    expect(registry.validateResult("youtube.search", { tab: RESULTS_TAB, results: [] })).toBeTruthy();
    expect(() => registry.validateResult("youtube.search", { tab: RESULTS_TAB, results: [...items, { index: 10, video_id: "x", title: "y" }] })).toThrow(ProtocolError);
    expect(() => registry.validateResult("youtube.search", { tab: RESULTS_TAB, results: items, query: "lofi" })).toThrow(ProtocolError);
    expect(() => registry.validateResult("youtube.search", { tab: RESULTS_TAB, results: [{ index: 0, video_id: "x", title: "y" }] })).toThrow(ProtocolError);
    // app_window is unchanged: a 1.1 phone must accept a new agent's app.list
    expect(() => registry.validateResult("app.list", { apps: [{ app_id: "notepad", display_name: "Notepad", running: true, windows: [{ window_id: "h1", title: "x", maximized: true }] }] })).toThrow(ProtocolError);
  });

  it("validates the new bridge ops and the search result", () => {
    const rid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    schemas.validateBridgeFrame("agent_to_extension", { type: "bridge_request", request_id: rid, op: "search", args: { query: "lofi beats" }, timeout_ms: 15000 });
    schemas.validateBridgeFrame("agent_to_extension", { type: "bridge_request", request_id: rid, op: "open_result", args: { tab_id: 5, tab_token: TAB.tab_token, index: 2, video_id: "dQw4w9WgXcQ" } });
    expect(() => schemas.validateBridgeFrame("agent_to_extension", { type: "bridge_request", request_id: rid, op: "search", args: { query: "x".repeat(101) } })).toThrow(ProtocolError);
    expect(() => schemas.validateBridgeFrame("agent_to_extension", { type: "bridge_request", request_id: rid, op: "read_results", args: {} })).toThrow(ProtocolError);
    schemas.validateBridgeFrame("extension_to_agent", { type: "bridge_response", request_id: rid, ok: true, result: { tab: RESULTS_TAB, results: [{ index: 1, video_id: "v1", title: "t" }] } });
    schemas.validateBridgeFrame("extension_to_agent", { type: "bridge_response", request_id: rid, ok: true, result: { tab: RESULTS_TAB } });
  });
});

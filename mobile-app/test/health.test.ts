import { describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import type { CommandRecord } from "../src/lib/commands.ts";
import { assessHealth, lastVerifiedResult, onboardingSteps, type HealthInput } from "../src/lib/health.ts";
import { EMPTY_INPUT_STATE } from "../src/lib/input.ts";
import { EMPTY_LIVE_PC, type LivePc, type PcState } from "../src/store/live.ts";

const PC = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:00.000Z";
const ME = "44444444-4444-4444-8444-444444444444";
const OTHER = "55555555-5555-4555-8555-555555555555";
const pc: rest.Pc = { id: PC, name: "Office PC", enabled: true, connection: "online", last_seen: TS, created_at: TS, platform: "windows", agent_version: "0.1.0" };
const grant: rest.Grant = { id: "g", controller_id: "c", pc_id: PC, capabilities: ["status", "media", "pointer", "keyboard"], created_at: TS };
const state: PcState = { remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [{ browser_instance_id: "b", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, context: "watch", ad_showing: false, is_live: false, in_playlist: false, paused: false, title: "Video" }] };
const live: LivePc = { ...EMPTY_LIVE_PC, connection: "online", lastSeen: TS, state, stateReceivedAt: 1_000_000, stateAt: TS, stale: false };

function base(over: Partial<HealthInput> = {}): HealthInput {
  return { online: true, sessionStatus: "signed_in", relayStatus: "open", controllerBound: true, controllerId: ME, pc, live, grant, input: EMPTY_INPUT_STATE, selectedTab: undefined, selectedSession: undefined, commands: [], appVersion: "0.1.0", protocolVersion: "1.1", now: 1_000_500, ...over };
}

function layer(input: HealthInput, id: string) {
  const l = assessHealth(input).find((x) => x.id === id);
  if (!l) throw new Error(`no layer ${id}`);
  return l;
}

describe("assessHealth", () => {
  it("everything healthy: seven layers, all ok, no action", () => {
    const layers = assessHealth(base());
    expect(layers.map((l) => l.id)).toEqual(["phone", "account", "pc", "remote", "input", "extension", "target"]);
    expect(layers.every((l) => l.status === "ok")).toBe(true);
    expect(layers.every((l) => l.action.kind === "none")).toBe(true);
    expect(layer(base(), "phone").details).toContain("protocol 1.1");
  });
  it("phone offline → reconnect this phone; relay reconnecting → waiting, not a problem", () => {
    expect(layer(base({ online: false }), "phone")).toMatchObject({ status: "problem", action: { kind: "reconnect_phone" } });
    expect(layer(base({ relayStatus: "reconnecting" }), "phone")).toMatchObject({ status: "waiting", action: { kind: "reconnect_phone" } });
    expect(layer(base({ relayStatus: "revoked" }), "phone")).toMatchObject({ status: "problem", action: { kind: "pair_phone" } });
  });
  it("account session ended → sign in again", () => {
    expect(layer(base({ sessionStatus: "signed_out" }), "account")).toMatchObject({ status: "problem", action: { kind: "sign_in" } });
    expect(layer(base({ sessionStatus: "loading" }), "account").status).toBe("waiting");
  });
  it("PC layer: unreachable PC has an unknown cause and asks to open DoMe on the PC; refused subscription asks to pair; no PC asks to link", () => {
    const off = layer(base({ live: { ...live, connection: "offline", stale: true } }), "pc");
    expect(off).toMatchObject({ status: "problem", action: { kind: "open_pc_app" } });
    expect(off.summary).toMatch(/unknown/i);
    expect(layer(base({ live: { ...live, connection: "reconnecting", stale: true } }), "pc").summary).toMatch(/cannot tell/i);
    expect(layer(base({ live: { ...live, stale: true } }), "pc").status).toBe("waiting");
    expect(layer(base({ live: { ...live, refused: "GRANT_MISSING" } }), "pc")).toMatchObject({ status: "problem", action: { kind: "pair_phone" } });
    expect(layer(base({ pc: undefined }), "pc")).toMatchObject({ status: "problem", action: { kind: "link_pc" } });
    expect(layer(base({ controllerBound: false }), "pc").action.kind).toBe("pair_phone");
  });
  it("remote-control layer: off → enable on the PC; locked → unlock; unknown without state", () => {
    expect(layer(base({ live: { ...live, state: { ...state, remote_enabled: false } } }), "remote")).toMatchObject({ status: "problem", action: { kind: "enable_remote" } });
    expect(layer(base({ live: { ...live, state: { ...state, session_locked: true } } }), "remote")).toMatchObject({ status: "problem", action: { kind: "unlock_pc" } });
    expect(layer(base({ live: { ...live, state: null } }), "remote").status).toBe("unknown");
  });
  it("manual-input layer: missing grant → grant on the PC (INPUT_NOT_PERMITTED); restricted screen → unlock; granted → ok", () => {
    const none = layer(base({ grant: { ...grant, capabilities: ["status", "media"] } }), "input");
    expect(none).toMatchObject({ status: "problem", action: { kind: "grant_input" } });
    expect(none.details).toContain("INPUT_NOT_PERMITTED");
    expect(layer(base({ grant: null }), "input").status).toBe("problem");
    expect(layer(base({ grant: undefined }), "input").status).toBe("unknown");
    expect(layer(base({ live: { ...live, state: { ...state, input_restricted: true } } }), "input")).toMatchObject({ status: "problem", action: { kind: "unlock_pc" } });
    expect(layer(base(), "input").summary).toMatch(/Touchpad and keyboard allowed/);
  });
  it("manual-input layer: the PC's input session is compared with THIS phone's controller id, never with the input_session_id", () => {
    const pcSession = (controller: string): LivePc => ({ ...live, state: { ...state, input_session: { controller_id: controller, pointer: true, keyboard: true, lease_expires_at: TS } } });
    // this phone's own session lingering after Stop/hide (phase idle here, an agent-issued id earlier)
    const own = layer(base({ live: pcSession(ME), input: { ...EMPTY_INPUT_STATE, phase: "idle", sessionId: "C".repeat(22) } }), "input");
    expect(own.summary).not.toMatch(/Another phone/);
    expect(own.summary).toMatch(/this phone’s last input session/);
    expect(own.details).toContain("pc input session: this phone");
    // while starting, the PC may already list this phone: still not "another phone"
    expect(layer(base({ live: pcSession(ME), input: { ...EMPTY_INPUT_STATE, phase: "starting" } }), "input").summary).not.toMatch(/Another phone/);
    // a different controller id is an observed foreign session
    const other = layer(base({ live: pcSession(OTHER) }), "input");
    expect(other.summary).toMatch(/Another phone currently holds the PC’s input session/);
    expect(other.details).toContain("pc input session: another controller");
    // while this phone is live nothing about ownership is claimed
    expect(layer(base({ live: pcSession(OTHER), input: { ...EMPTY_INPUT_STATE, phase: "live", sessionId: "C".repeat(22) } }), "input").summary).not.toMatch(/Another phone/);
    // without a bound controller id nothing can be compared, so nothing is diagnosed
    expect(layer(base({ controllerId: null, live: pcSession(OTHER) }), "input").summary).not.toMatch(/Another phone|this phone’s last/);
    // no PC session reported: no note at all
    expect(layer(base(), "input").summary).not.toMatch(/Another phone|this phone’s last/);
  });
  it("extension layer: disconnected → install/update, with the note that only YouTube needs it", () => {
    const l = layer(base({ live: { ...live, state: { ...state, extension_connected: false } } }), "extension");
    expect(l).toMatchObject({ status: "problem", action: { kind: "install_extension" } });
    expect(l.summary).toMatch(/touchpad work without it/);
  });
  it("target layer: single tab ok; two playing tabs → choose; closed tab + no media → open YouTube; unattached → reload", () => {
    const two: PcState = { ...state, youtube_tabs: [state.youtube_tabs![0]!, { ...state.youtube_tabs![0]!, tab_id: 2, tab_token: "dGFiLXRva2VuLTAwMDAwMX", title: "Other" }] };
    expect(layer(base({ live: { ...live, state: two } }), "target")).toMatchObject({ status: "problem", action: { kind: "choose_target" } });
    expect(layer(base({ live: { ...live, state: two }, selectedTab: "b:2:dGFiLXRva2VuLTAwMDAwMX" }), "target")).toMatchObject({ status: "ok" });
    expect(layer(base({ live: { ...live, state: { ...state, youtube_tabs: [] } } }), "target")).toMatchObject({ status: "problem", action: { kind: "open_youtube" } });
    expect(layer(base({ live: { ...live, state: { ...state, youtube_tabs: [{ ...state.youtube_tabs![0]!, script_attached: false }] } } }), "target").details).toContain("TAB_NOT_CONTROLLABLE");
    expect(layer(base({ live: { ...live, state: { ...state, youtube_tabs: [], media_sessions: [{ session_id: "s1", status: "playing", controls: ["pause"], app_label: "Spotify" }] } } }), "target")).toMatchObject({ status: "ok" });
  });
});

describe("onboarding walkthrough", () => {
  const cmd = (over: Partial<CommandRecord>): CommandRecord => ({ commandId: "c", pcId: PC, action: "youtube.set_paused", params: {}, target: null, createdAt: 1, expiresAt: TS, state: "succeeded", ackAt: null, confirmation: null, terminal: { origin: "agent", state: "succeeded", at: TS, durationMs: 1, result: {}, resultInvalid: false, error: null, warning: null }, noAnswer: false, source: "button", ...over });
  it("returns to the first undone step and marks the rest", () => {
    const steps = onboardingSteps(base({ live: { ...live, connection: "offline", state: null, stale: true } }));
    expect(steps.map((s) => s.done)).toEqual([true, true, true, false, false, false]);
    expect(steps.find((s) => !s.done)?.id).toBe("online");
    const notPaired = onboardingSteps(base({ grant: null }));
    expect(notPaired.find((s) => !s.done)?.id).toBe("pair");
    expect(notPaired.find((s) => s.id === "pair")?.to).toBe("/app/devices/pair");
  });
  it("completes with the first verified media action", () => {
    expect(onboardingSteps(base()).find((s) => !s.done)?.id).toBe("first_action");
    expect(onboardingSteps(base({ commands: [cmd({})] })).every((s) => s.done)).toBe(true);
    expect(onboardingSteps(base({ commands: [cmd({ action: "app.launch" })] })).every((s) => s.done)).toBe(false);
    expect(lastVerifiedResult([cmd({ terminal: null }), cmd({ commandId: "d" })], PC)?.commandId).toBe("d");
  });
});

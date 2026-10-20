import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ERRORS, PLANS, PROTOCOL_VERSION, ProtocolError, registry, schemas } from "../src/index.ts";

// The AI assistant's REST additions (docs/design/assistant.md §1, docs/PROTOCOL.md §19): the same accept/refuse
// decisions as shared/python/tests/test_assistant_contract.py, through Ajv's ECMA-262 patterns.

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(readFileSync(resolve(here, "../../protocol/fixtures/assistant-plan-cases.json"), "utf8")) as {
  apps: Array<Record<string, unknown>>;
  cases: Array<{ name: string; verdict: string; steps: Array<{ action: string; params: Record<string, unknown>; app?: string }> }>;
};

const TURN_ID = "44444444-4444-4444-8444-444444444444";
const CONTEXT = {
  persona: { name: "DoMe", address: "sir" },
  pc_connection: "online",
  playback: { youtube: "playing", media: "none" },
  pc_volume: 40,
  search_results: 5,
  capabilities: ["apps", "media", "status", "volume"],
  part_of_day: "evening",
};
const TURN = {
  turn_id: TURN_ID,
  source: "speech",
  text: "turn it up a bit",
  recent: [
    { who: "owner", text: "(DoMe handled this itself: pause YouTube)" },
    { who: "butler", text: "YouTube is paused." },
  ],
  context: CONTEXT,
};
const WINDOW = { limit: 30, used: 3, starts_at: "2026-10-24T13:00:00.000Z", ends_at: "2026-10-24T18:00:00.000Z" };
const WEEK = { limit: 120, used: 7, starts_at: "2026-10-24T13:00:00.000Z", ends_at: "2026-10-31T13:00:00.000Z" };
const ALLOWANCE = { kind: "windows", taste: null, window: WINDOW, week: WEEK, topup: { balance: 0, allowed: true }, exhausted: null, resets_at: null, warn_at_percent: 80 };
const TASTE_ALLOWANCE = { kind: "taste", taste: { limit: 10, used: 3 }, window: null, week: null, topup: { balance: 0, allowed: false }, exhausted: null, resets_at: null, warn_at_percent: 80 };
const RESPONSE = {
  turn_id: TURN_ID,
  mode: "act",
  reply: "",
  choices: [],
  handoff: "none",
  steps: [
    { action: "windows.set_volume", params: { value: 50 } },
    { action: "app.launch", params: {}, app: "Spotify" },
  ],
  charged: true,
  charged_from: "window",
  allowance: ALLOWANCE,
};
const STATUS = {
  available: true,
  unavailable_reason: null,
  provider: "anthropic",
  turn_deadline_seconds: 20,
  consent_version: "2026-10-11",
  consent: { version: "2026-10-11", given_at: "2026-10-11T18:00:00.000Z" },
  allowance: ALLOWANCE,
};
const UNPRINTABLE = ["a\u0000b", "a\nb", "a\tb", "a\u0085b", "a\u202eb", "a\u200bb", "a\u2028b", "a\u2029b", "a\ufeffb", "a\u00adb", "a\u2066b"];

function ok(name: string, value: unknown): void {
  schemas.validateRest(name, value);
}

function bad(name: string, value: unknown): void {
  let code = "OK";
  try {
    schemas.validateRest(name, value);
  } catch (e) {
    if (!(e instanceof ProtocolError)) throw e;
    code = e.code;
  }
  expect(code).toBe("MALFORMED_MESSAGE");
}

describe("assistant REST bodies", () => {
  it("accepts valid examples", () => {
    ok("assistant_turn_request", TURN);
    ok("assistant_turn_response", RESPONSE);
    ok("assistant_turn_response", { ...RESPONSE, mode: "ask", reply: "YouTube or the PC?", choices: ["The PC", "YouTube"], steps: [] });
    ok("assistant_status_response", STATUS);
    ok("assistant_status_response", { ...STATUS, available: false, unavailable_reason: "not_configured", provider: "none", consent: null });
    ok("assistant_status_response", { ...STATUS, allowance: TASTE_ALLOWANCE });
    ok("assistant_turn_response", { ...RESPONSE, charged: false, charged_from: null, mode: "decline", reply: "No.", steps: [] });
    ok("assistant_turn_response", { ...RESPONSE, charged_from: "topup" });
    ok("assistant_allowance", { ...ALLOWANCE, window: null, week: null });
    ok("assistant_consent_request", { version: "2026-10-11", accept: true });
    for (const text of ["Hello, sir. 40% louder? Ünïcode ✓ 🎵", "x".repeat(400)]) ok("assistant_text", text);
    for (const name of ["DoMe", "Mr. Smith", "O'Neil-Brown", "A".repeat(20)]) ok("assistant_persona", { name });
  });

  it("refuses unknown fields at every level", () => {
    bad("assistant_turn_request", { ...TURN, extra: 1 });
    bad("assistant_turn_request", { ...TURN, context: { ...CONTEXT, title: "x" } });
    bad("assistant_turn_request", { ...TURN, context: { ...CONTEXT, playback: { ...CONTEXT.playback, title: "x" } } });
    bad("assistant_turn_response", { ...RESPONSE, steps: [{ action: "power.sleep", params: {}, confirmed: true }] });
    bad("assistant_status_response", { ...STATUS, extra: 1 });
  });

  it("refuses unprintable, empty and over-long text", () => {
    for (const text of [...UNPRINTABLE, "", "x".repeat(401)]) {
      bad("assistant_text", text);
      bad("assistant_turn_request", { ...TURN, text });
    }
    for (const text of UNPRINTABLE) {
      bad("assistant_turn_response", { ...RESPONSE, reply: text });
      bad("assistant_turn_response", { ...RESPONSE, choices: [text] });
    }
  });

  it("bounds lists, persona words and capabilities", () => {
    bad("assistant_turn_request", { ...TURN, recent: Array(7).fill({ who: "owner", text: "x" }) });
    bad("assistant_turn_request", { ...TURN, context: { ...CONTEXT, capabilities: ["apps", "pointer"] } });
    bad("assistant_turn_response", { ...RESPONSE, steps: Array(4).fill({ action: "system.ping", params: {} }) });
    bad("assistant_turn_response", { ...RESPONSE, choices: Array(5).fill("a") });
    for (const name of ["R2D2", "DoMe!", "", "Do\nMe", "A".repeat(21)]) bad("assistant_persona", { name });
  });

  it("keeps paths and shell characters out of step app names", () => {
    ok("assistant_step", { action: "app.launch", params: {}, app: "Microsoft Edge" });
    for (const app of ["C:\\Windows\\cmd.exe", "a/b", "a|b", "<x>", "x".repeat(41), "Spo\u202etify"]) {
      bad("assistant_step", { action: "app.launch", params: {}, app });
    }
  });

  it("pins the consent version and allowance shape", () => {
    bad("assistant_consent_request", { version: "2026-10-10", accept: true });
    bad("assistant_consent_request", { version: "2026-10-11", accept: false });
    const { exhausted: _omit, ...withoutExhausted } = ALLOWANCE;
    bad("assistant_allowance", withoutExhausted);
    bad("assistant_allowance", { ...ALLOWANCE, exhausted: "money" });
    bad("assistant_allowance", { ...ALLOWANCE, exhausted: "requests" }); // the 1.2 reason is gone
    bad("assistant_allowance", { ...ALLOWANCE, kind: "monthly" });
    bad("assistant_allowance", { ...ALLOWANCE, warn_at_percent: 90 });
    bad("assistant_allowance", { ...ALLOWANCE, window: { ...WINDOW, reserved: 1 } });
    bad("assistant_allowance", { ...ALLOWANCE, topup: { balance: -1, allowed: true } });
    for (const reason of ["window", "week", "cost_cap", "failures"]) ok("assistant_allowance", { ...ALLOWANCE, exhausted: reason, resets_at: WINDOW.ends_at });
    ok("assistant_allowance", { ...TASTE_ALLOWANCE, taste: { limit: 10, used: 10 }, exhausted: "taste" });
    const { charged_from: _omit2, ...withoutChargedFrom } = RESPONSE;
    bad("assistant_turn_response", withoutChargedFrom);
    bad("assistant_turn_response", { ...RESPONSE, charged_from: "month" });
  });

  it("has the error codes and plan fields (protocol 1.3 reshaped the AI plan fields)", () => {
    expect(PROTOCOL_VERSION).toBe("1.3");
    for (const code of ["AI_NOT_AVAILABLE", "AI_CONSENT_REQUIRED", "AI_ALLOWANCE_EXHAUSTED", "AI_SPEND_LIMIT_REACHED", "AI_UNAVAILABLE", "AI_DECLINED", "AI_BUSY", "AI_TURN_DUPLICATE"]) {
      expect(ERRORS[code]?.user_message).toBeTruthy();
    }
    expect(ERRORS.AI_UNAVAILABLE?.retryable).toBe(true);
    expect(ERRORS.AI_DECLINED?.retryable).toBe(false);
    for (const plan of Object.values(PLANS.plans)) {
      expect(plan.ai_cost_cap_cents_per_week).toBeGreaterThan(0);
      expect(plan.ai_window.hours).toBe(5);
      expect(plan.ai_week.days).toBe(7);
      expect(plan.ai_turn_rate_limit).toEqual({ per_minute: 10, burst: 5 });
    }
    expect([PLANS.plans.free.ai_taste_requests, PLANS.plans.free.ai_window.requests, PLANS.plans.free.ai_week.requests, PLANS.plans.free.ai_topup_allowed]).toEqual([10, 0, 0, false]);
    expect([PLANS.plans.pro.ai_window.requests, PLANS.plans.pro.ai_week.requests, PLANS.plans.pro.ai_topup_allowed]).toEqual([30, 120, true]);
    expect([PLANS.plans.pro_plus.ai_window.requests, PLANS.plans.pro_plus.ai_week.requests, PLANS.plans.pro_plus.ai_topup_allowed]).toEqual([90, 300, true]);
  });

  it("the plan fixture's valid cases name only AI-eligible registry actions", () => {
    for (const c of FIXTURE.cases.filter((x) => x.verdict === "valid")) {
      for (const step of c.steps) {
        ok("assistant_step", step);
        expect(registry.get(step.action).aiEligible).toBe(true);
      }
    }
  });
});

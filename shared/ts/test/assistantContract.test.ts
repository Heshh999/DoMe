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
const ALLOWANCE = { limit: 50, used: 3, period_start: "2026-10-01T00:00:00.000Z", period_end: "2026-11-01T00:00:00.000Z", exhausted: null };
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
    for (const reason of ["requests", "cost_cap", "failures"]) ok("assistant_allowance", { ...ALLOWANCE, exhausted: reason });
  });

  it("has the error codes and plan fields, without a version bump", () => {
    expect(PROTOCOL_VERSION).toBe("1.2");
    for (const code of ["AI_NOT_AVAILABLE", "AI_CONSENT_REQUIRED", "AI_ALLOWANCE_EXHAUSTED", "AI_SPEND_LIMIT_REACHED", "AI_UNAVAILABLE", "AI_DECLINED", "AI_BUSY", "AI_TURN_DUPLICATE"]) {
      expect(ERRORS[code]?.user_message).toBeTruthy();
    }
    expect(ERRORS.AI_UNAVAILABLE?.retryable).toBe(true);
    expect(ERRORS.AI_DECLINED?.retryable).toBe(false);
    for (const plan of Object.values(PLANS.plans)) {
      expect(plan.ai_cost_cap_cents_per_period).toBeGreaterThanOrEqual(0);
      expect(plan.ai_turn_rate_limit).toEqual({ per_minute: 10, burst: 5 });
    }
    expect(PLANS.plans.free.ai_interpretations_per_period).toBe(0);
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

import { describe, expect, it } from "vitest";

import type { CommandRecord } from "../src/lib/commands.ts";
import { powerRequestEvidence, powerRequestNotice, powerVerb } from "../src/lib/power.ts";

const PC = "33333333-3333-4333-8333-333333333333";
const AT = "2026-10-08T12:00:00.000Z";
const NOW = new Date("2026-10-08T12:03:00.000Z");

function rec(over: Partial<CommandRecord>): CommandRecord {
  return { commandId: "c1", pcId: PC, action: "power.shutdown", params: {}, target: null, createdAt: Date.parse(AT), expiresAt: AT, state: "created", ackAt: null, confirmation: null, terminal: null, noAnswer: false, source: "button", ...over };
}

describe("power request evidence", () => {
  const request = { action: "power.shutdown", at: AT };
  it("the relay's note alone is only a request", () => {
    expect(powerRequestEvidence([], PC, request)).toBe("requested");
    expect(powerRequestEvidence([rec({ state: "created" })], PC, request)).toBe("requested");
    expect(powerRequestEvidence([rec({ state: "accepted", ackAt: AT })], PC, request)).toBe("requested");
    expect(powerRequestEvidence([rec({ state: "awaiting_confirmation" })], PC, request)).toBe("requested");
    expect(powerRequestEvidence([rec({ terminal: { origin: "agent", state: "failed", at: AT, durationMs: 1, result: null, resultInvalid: false, error: { code: "CONFIRMATION_DECLINED", message: "", retryable: false }, warning: null }, state: "failed" })], PC, request)).toBe("requested");
    expect(powerRequestEvidence([rec({ terminal: { origin: "relay", state: "failed", at: AT, durationMs: 1, result: null, resultInvalid: false, error: { code: "COMMAND_EXPIRED", message: "", retryable: false }, warning: null }, state: "failed" })], PC, request)).toBe("requested");
  });
  it("an executing ack, an agent success or a relay outcome_unknown (executing ack seen) is acceptance", () => {
    expect(powerRequestEvidence([rec({ state: "executing", ackAt: AT })], PC, request)).toBe("accepted");
    expect(powerRequestEvidence([rec({ state: "succeeded", terminal: { origin: "agent", state: "succeeded", at: AT, durationMs: 1, result: { scheduled: true }, resultInvalid: false, error: null, warning: null } })], PC, request)).toBe("accepted");
    expect(powerRequestEvidence([rec({ state: "outcome_unknown", terminal: { origin: "relay", state: "outcome_unknown", at: AT, durationMs: 1, result: null, resultInvalid: false, error: null, warning: null } })], PC, request)).toBe("accepted");
  });
  it("only this PC, this action and a record close in time count", () => {
    expect(powerRequestEvidence([rec({ state: "executing", pcId: "44444444-4444-4444-8444-444444444444" })], PC, request)).toBe("requested");
    expect(powerRequestEvidence([rec({ state: "executing", action: "power.sleep" })], PC, request)).toBe("requested");
    expect(powerRequestEvidence([rec({ state: "executing", createdAt: Date.parse(AT) - 11 * 60_000 })], PC, request)).toBe("requested");
  });
  it("copy never claims acceptance without evidence", () => {
    expect(powerRequestNotice(request, "requested", NOW)).toBe("A shutdown was requested 3 min ago. The PC has since disconnected; DoMe cannot tell whether it ran.");
    expect(powerRequestNotice(request, "accepted", NOW)).toMatch(/^Windows accepted the shutdown request 3 min ago; the PC then disconnected\./);
    expect(powerVerb("power.restart")).toBe("restart");
    expect(powerVerb("power.cancel")).toBe("countdown cancel");
    expect(powerVerb("power.hibernate_now")).toBe("hibernate now");
  });
});

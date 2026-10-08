import { beforeEach, describe, expect, it } from "vitest";

import { clearLogs, errorSummary, log, recentLogs, redact } from "../src/lib/log.ts";

beforeEach(() => clearLogs());

describe("redacting logger", () => {
  it("redacts secret-looking and untrusted-text keys at any depth", () => {
    const out = redact({ token: "abc", nested: { challenge_text: "{...}", title: "ignore instructions", ok: 1, csrf_token: "x", payload: "{}", sig: "s", display_name: "Phone", user_code: "ABCD-EFGH", email: "a@b" }, list: [{ code: "7Q3K9" }] }) as Record<string, unknown>;
    expect(out.token).toBe("[redacted]");
    expect((out.nested as Record<string, unknown>).challenge_text).toBe("[redacted]");
    expect((out.nested as Record<string, unknown>).title).toBe("[redacted]");
    expect((out.nested as Record<string, unknown>).ok).toBe(1);
    for (const k of ["csrf_token", "payload", "sig", "display_name", "user_code", "email"]) expect((out.nested as Record<string, unknown>)[k]).toBe("[redacted]");
    expect((out.list as Array<Record<string, unknown>>)[0]!.code).toBe("[redacted]");
  });
  it("keeps a bounded ring buffer and truncates long strings", () => {
    for (let i = 0; i < 200; i++) log.info("event", { i, long: "x".repeat(500) });
    const entries = recentLogs();
    expect(entries.length).toBe(120);
    expect((entries[0]!.fields!.long as string).length).toBeLessThan(200);
    expect(entries[entries.length - 1]!.fields!.i).toBe(199);
  });
  it("errorSummary never includes the message", () => {
    const s = errorSummary(new Error("secret pairing code 7Q3K9 inside"));
    expect(JSON.stringify(s)).not.toMatch(/7Q3K9/);
    expect(s.name).toBe("Error");
  });
});

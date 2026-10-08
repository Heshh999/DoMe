import { beforeEach, describe, expect, it } from "vitest";

import { clearLogs, errorSummary, log, recentLogs, redact } from "../src/lib/log.ts";

beforeEach(() => clearLogs());

describe("redacting logger", () => {
  it("redacts secret-looking and untrusted-text keys at any depth", () => {
    const out = redact({ token: "abc", nested: { challenge_text: "{...}", title: "ignore instructions", ok: 1, csrf_token: "x", payload: "{}", sig: "s", display_name: "Phone", user_code: "ABCD-EFGH", email: "a@b" }, list: [{ pairing_code: "7Q3K9" }] }) as Record<string, unknown>;
    expect(out.token).toBe("[redacted]");
    expect((out.nested as Record<string, unknown>).challenge_text).toBe("[redacted]");
    expect((out.nested as Record<string, unknown>).title).toBe("[redacted]");
    expect((out.nested as Record<string, unknown>).ok).toBe(1);
    for (const k of ["csrf_token", "payload", "sig", "display_name", "user_code", "email"]) expect((out.nested as Record<string, unknown>)[k]).toBe("[redacted]");
    expect((out.list as Array<Record<string, unknown>>)[0]!.pairing_code).toBe("[redacted]");
  });
  it("keeps stable protocol error codes for diagnostics while redacting pairing material", () => {
    const out = redact({ code: "PC_OFFLINE", error_code: "COMMAND_EXPIRED", pairing_code: "ABCDE-FGHJK-MNPQR-STVWX", code_hash: "abc", user_code: "ABCD-EFGH", verification: "123456", verification_code: "123456" }) as Record<string, unknown>;
    expect(out.code).toBe("PC_OFFLINE");
    expect(out.error_code).toBe("COMMAND_EXPIRED");
    for (const k of ["pairing_code", "code_hash", "user_code", "verification", "verification_code"]) expect(out[k]).toBe("[redacted]");
    log.info("api.error", { method: "GET", path: "/v1/pcs", status: 503, code: "PC_OFFLINE" });
    expect(recentLogs()[0]!.fields!.code).toBe("PC_OFFLINE");
    expect(errorSummary(Object.assign(new Error("x"), { code: "GRANT_MISSING" })).code).toBe("GRANT_MISSING");
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

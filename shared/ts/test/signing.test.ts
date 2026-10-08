import { describe, expect, it } from "vitest";

import {
  ProtocolError,
  b64url,
  buildCommandPayload,
  dumpsCompact,
  exportPublicJwk,
  generateControllerKeyPair,
  kidFromJwk,
  signCommand,
  signPayload,
  unb64url,
  verifyAndParseCommand,
  verifyEnvelope,
  type EcPublicJwk,
} from "../src/index.ts";

async function codeOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
    return "OK";
  } catch (e) {
    if (e instanceof ProtocolError) return e.code;
    throw e;
  }
}

describe("ES256 envelopes", () => {
  it("signs and verifies, then parses once", async () => {
    const kp = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(kp.publicKey);
    const kid = await kidFromJwk(jwk);
    const env = await signPayload(kp.privateKey, kp.publicKey, '{"type":"command","x":1}');
    expect(env.kid).toBe(kid);
    expect(unb64url(env.sig).length).toBe(64);
    const { payload } = await verifyEnvelope(env, (k) => (k === kid ? jwk : null));
    expect(payload).toEqual({ type: "command", x: 1 });
  });

  it("rejects tampering, unknown kid, wrong key, bad alg, extra fields", async () => {
    const kp = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(kp.publicKey);
    const kid = await kidFromJwk(jwk);
    const resolve = (k: string) => (k === kid ? jwk : null);
    const env = await signPayload(kp.privateKey, kp.publicKey, '{"a":1}');
    expect(await codeOf(verifyEnvelope({ ...env, payload: '{"a":2}' }, resolve))).toBe("SIGNATURE_INVALID");
    expect(await codeOf(verifyEnvelope({ ...env, payload: '{"a": 1}' }, resolve))).toBe("SIGNATURE_INVALID");
    expect(await codeOf(verifyEnvelope(env, () => null))).toBe("UNKNOWN_KEY");
    expect(await codeOf(verifyEnvelope({ ...env, alg: "none" }, resolve))).toBe("SIGNATURE_INVALID");
    expect(await codeOf(verifyEnvelope({ ...env, v: 2 }, resolve))).toBe("PROTOCOL_INCOMPATIBLE");
    expect(await codeOf(verifyEnvelope({ ...env, extra: 1 }, resolve))).toBe("MALFORMED_MESSAGE");
    expect(await codeOf(verifyEnvelope({ ...env, sig: b64url(new Uint8Array(64)) }, resolve))).toBe("SIGNATURE_INVALID");
    const other = await generateControllerKeyPair();
    const otherJwk = await exportPublicJwk(other.publicKey);
    expect(await codeOf(verifyEnvelope(env, () => otherJwk))).toBe("UNKNOWN_KEY"); // store mismatch
  });

  it("rejects signed-but-malformed payloads after signature verification", async () => {
    const kp = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(kp.publicKey);
    const env = await signPayload(kp.privateKey, kp.publicKey, '{"a":1,"a":2}');
    expect(await codeOf(verifyEnvelope(env, () => jwk))).toBe("MALFORMED_MESSAGE");
  });

  it("validates JWKs strictly", async () => {
    const bad: EcPublicJwk = { kty: "EC", crv: "P-256", x: "A".repeat(43), y: "A".repeat(43) };
    expect(await codeOf(kidFromJwk(bad))).toBe("MALFORMED_MESSAGE");
    expect(await codeOf(kidFromJwk({ ...bad, d: "x" }))).toBe("MALFORMED_MESSAGE");
  });
});

describe("commands", () => {
  it("builds, signs and verifies a command end to end", async () => {
    const kp = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(kp.publicKey);
    const payload = buildCommandPayload({
      accountId: "11111111-1111-4111-8111-111111111111",
      controllerId: "22222222-2222-4222-8222-222222222222",
      targetPcId: "33333333-3333-4333-8333-333333333333",
      action: "youtube.seek_relative",
      params: { seconds: -10 },
      target: { browser_instance_id: "bi_test0001", tab_id: 3 },
    });
    const env = await signCommand(kp.privateKey, kp.publicKey, payload);
    const vc = await verifyAndParseCommand(env, () => jwk);
    expect(vc.spec.name).toBe("youtube.seek_relative");
    expect(vc.params).toEqual({ seconds: -10 });
    expect(dumpsCompact(vc.payload)).toBe(env.payload);
  });

  it("refuses to build invalid commands", () => {
    const base = { accountId: "11111111-1111-4111-8111-111111111111", controllerId: "22222222-2222-4222-8222-222222222222", targetPcId: "33333333-3333-4333-8333-333333333333" };
    expect(() => buildCommandPayload({ ...base, action: "shell.exec" })).toThrow(/UNKNOWN_ACTION/);
    expect(() => buildCommandPayload({ ...base, action: "windows.set_volume", params: { value: 150 } })).toThrow(/INVALID_PARAMETERS/);
    expect(() => buildCommandPayload({ ...base, action: "youtube.next" })).toThrow(/TARGET_REQUIRED/);
    expect(() => buildCommandPayload({ ...base, action: "app.launch", params: { app_id: "Chrome.exe --flag" } })).toThrow(/INVALID_PARAMETERS/);
    expect(() => buildCommandPayload({ ...base, action: "windows.lock", lifetimeSeconds: 9999 })).toThrow(/INVALID_PARAMETERS/);
  });

  it("applies schema defaults", () => {
    const p = buildCommandPayload({ accountId: "11111111-1111-4111-8111-111111111111", controllerId: "22222222-2222-4222-8222-222222222222", targetPcId: "33333333-3333-4333-8333-333333333333", action: "power.sleep" });
    expect(p.params).toEqual({ countdown_seconds: 10 });
  });

  it("rejects expired and future commands", async () => {
    const kp = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(kp.publicKey);
    const base = { accountId: "11111111-1111-4111-8111-111111111111", controllerId: "22222222-2222-4222-8222-222222222222", targetPcId: "33333333-3333-4333-8333-333333333333", action: "windows.lock" };
    const old = buildCommandPayload({ ...base, now: new Date(Date.now() - 120_000) });
    expect(await codeOf(verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, old), () => jwk))).toBe("COMMAND_EXPIRED");
    const future = buildCommandPayload({ ...base, now: new Date(Date.now() + 120_000) });
    expect(await codeOf(verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, future), () => jwk))).toBe("CLOCK_SKEW");
  });
});

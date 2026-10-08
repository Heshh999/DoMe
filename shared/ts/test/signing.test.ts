import { describe, expect, it } from "vitest";

import {
  ProtocolError,
  b64url,
  buildCommandPayload,
  buildConfirmationPayload,
  dumpsCompact,
  exportPublicJwk,
  generateControllerKeyPair,
  kidFromJwk,
  schemas,
  signCommand,
  signConfirmation,
  signPayload,
  unb64url,
  verifyAndParseCommand,
  verifyAndParseConfirmation,
  verifyEnvelope,
  type EcPublicJwk,
  type KeyRecord,
} from "../src/index.ts";

const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const CONTROLLER = "22222222-2222-4222-8222-222222222222";
const PC = "33333333-3333-4333-8333-333333333333";
const TARGET = { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "AAAAAAAAAAAAAAAAAAAAAA" };

async function codeOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
    return "OK";
  } catch (e) {
    if (e instanceof ProtocolError) return e.code;
    throw e;
  }
}

async function controller() {
  const kp = await generateControllerKeyPair();
  const jwk = await exportPublicJwk(kp.publicKey);
  const kid = await kidFromJwk(jwk);
  const record: KeyRecord = { controllerId: CONTROLLER, accountId: ACCOUNT, jwk, capabilities: ["media", "status"] };
  return { kp, jwk, kid, record, resolve: (k: string) => (k === kid ? record : null) };
}

describe("ES256 envelopes", () => {
  it("signs and verifies, then parses once", async () => {
    const { kp, jwk, kid } = await controller();
    const env = await signPayload(kp.privateKey, kp.publicKey, '{"type":"command","x":1}');
    expect(env.kid).toBe(kid);
    expect(unb64url(env.sig).length).toBe(64);
    const { payload } = await verifyEnvelope(env, (k) => (k === kid ? jwk : null));
    expect(payload).toEqual({ type: "command", x: 1 });
  });

  it("rejects tampering, unknown kid, wrong key, bad alg, extra fields", async () => {
    const { kp, jwk, kid } = await controller();
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
    const { kp, jwk } = await controller();
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
  const base = { accountId: ACCOUNT, controllerId: CONTROLLER, targetPcId: PC };

  it("builds, signs and verifies a command end to end", async () => {
    const { kp, resolve } = await controller();
    const payload = buildCommandPayload({ ...base, action: "youtube.seek_relative", params: { seconds: -10 }, target: TARGET });
    const env = await signCommand(kp.privateKey, kp.publicKey, payload);
    const vc = await verifyAndParseCommand(env, resolve);
    expect(vc.spec.name).toBe("youtube.seek_relative");
    expect(vc.params).toEqual({ seconds: -10 });
    expect(dumpsCompact(vc.payload)).toBe(env.payload);
    expect(vc.key.controllerId).toBe(CONTROLLER);
    expect(vc.digest).toHaveLength(43);
  });

  it("refuses to build invalid commands", () => {
    expect(() => buildCommandPayload({ ...base, action: "shell.exec" })).toThrow(/UNKNOWN_ACTION/);
    expect(() => buildCommandPayload({ ...base, action: "windows.set_volume", params: { value: 150 } })).toThrow(/INVALID_PARAMETERS/);
    expect(() => buildCommandPayload({ ...base, action: "youtube.next" })).toThrow(/TARGET_REQUIRED/);
    expect(() => buildCommandPayload({ ...base, action: "youtube.next", target: { browser_instance_id: "bi_test0001", tab_id: 3 } })).toThrow(/INVALID_PARAMETERS/); // tab_token
    expect(() => buildCommandPayload({ ...base, action: "app.launch", params: { app_id: "Chrome.exe --flag" } })).toThrow(/INVALID_PARAMETERS/);
    expect(() => buildCommandPayload({ ...base, action: "windows.lock", lifetimeSeconds: 9999 })).toThrow(/INVALID_PARAMETERS/);
  });

  it("applies schema defaults and a longer default lifetime for confirmed actions", () => {
    const p = buildCommandPayload({ ...base, action: "power.sleep" });
    expect(p.params).toEqual({ countdown_seconds: 10 });
    const lifetime = (new Date(p.expires_at).getTime() - new Date(p.issued_at).getTime()) / 1000;
    expect(lifetime).toBe(90);
    const q = buildCommandPayload({ ...base, action: "windows.lock" });
    expect((new Date(q.expires_at).getTime() - new Date(q.issued_at).getTime()) / 1000).toBe(30);
  });

  it("rejects expired and future commands", async () => {
    const { kp, resolve } = await controller();
    const old = buildCommandPayload({ ...base, action: "windows.lock", now: new Date(Date.now() - 120_000) });
    expect(await codeOf(verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, old), resolve))).toBe("COMMAND_EXPIRED");
    const future = buildCommandPayload({ ...base, action: "windows.lock", now: new Date(Date.now() + 120_000) });
    expect(await codeOf(verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, future), resolve))).toBe("CLOCK_SKEW");
  });

  it("rejects a controller signing another controller's id (cross-controller forgery)", async () => {
    const attacker = await controller();
    const victimRecord: KeyRecord = { ...attacker.record, controllerId: "99999999-9999-4999-8999-999999999999" };
    const payload = buildCommandPayload({ ...base, action: "power.shutdown" }); // names CONTROLLER
    const env = await signCommand(attacker.kp.privateKey, attacker.kp.publicKey, payload);
    expect(await codeOf(verifyAndParseCommand(env, () => victimRecord))).toBe("CONTROLLER_MISMATCH");
    const otherAccount: KeyRecord = { ...attacker.record, accountId: "99999999-9999-4999-8999-999999999999" };
    expect(await codeOf(verifyAndParseCommand(env, () => otherAccount))).toBe("ACCOUNT_MISMATCH");
  });
});

describe("confirmations", () => {
  it("builds from the raw challenge text and verifies", async () => {
    const { kp, resolve } = await controller();
    const challengeText = '{"challenge_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","command_id":"6f1c2d3e-4a5b-4c6d-8e7f-901234567890","controller_id":"22222222-2222-4222-8222-222222222222","pc_id":"33333333-3333-4333-8333-333333333333","action":"power.sleep","params":{"countdown_seconds":10},"target":null,"target_state_digest":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","issued_at":"2026-10-08T12:00:00.000Z","expires_at":"2026-10-08T12:01:00.000Z","display":{"pc_name":"Büro-PC","action_label":"Sleep","detail":"Sleep in 10 s"}}';
    const parsed = schemas.validateChallengeText(challengeText);
    expect(parsed.action).toBe("power.sleep");
    const payload = await buildConfirmationPayload({
      commandId: "6f1c2d3e-4a5b-4c6d-8e7f-901234567890",
      challengeId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      challengeText,
      controllerId: CONTROLLER,
      targetPcId: PC,
      decision: "approve",
    });
    const env = await signConfirmation(kp.privateKey, kp.publicKey, payload);
    const vc = await verifyAndParseConfirmation(env, resolve);
    expect(vc.approved).toBe(true);
    expect(vc.payload.challenge_digest).toHaveLength(43);
    expect(() => schemas.validateChallengeText(challengeText.replace('"target":null', '"target":null,"extra":1'))).toThrow(ProtocolError);
  });
});

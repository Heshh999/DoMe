import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { challengeDigest, generateControllerKeyPair, verifyAndParseConfirmation, exportPublicJwk, kidFromJwk } from "@dome/protocol";

import { buildConfirmationEnvelope, checkChallengeBinding, describeChallenge, parseChallenge } from "../src/lib/confirmations.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string; challenge_digest: string } };
const TEXT = fixture.digests.challenge_text;

describe("confirmations", () => {
  it("parses the challenge once and preserves the raw text verbatim", () => {
    const parsed = parseChallenge(TEXT);
    expect(parsed.raw).toBe(TEXT);
    expect(parsed.challenge.action).toBe("app.close");
    expect(parsed.challenge.display.detail).toBe("Untitled — Notepad / 📝");
  });

  it("the digest of the raw text matches the shared fixture even after whitespace/ordering would differ", async () => {
    const parsed = parseChallenge(TEXT);
    expect(await challengeDigest(parsed.raw)).toBe(fixture.digests.challenge_digest);
    // re-serialising would NOT reproduce the digest; proving we must hash the raw string
    expect(await challengeDigest(JSON.stringify(parsed.challenge, null, 1))).not.toBe(fixture.digests.challenge_digest);
  });

  it("rejects malformed, oversized and unknown-field challenge text", () => {
    expect(() => parseChallenge("{}")).toThrow(/MALFORMED_MESSAGE/);
    expect(() => parseChallenge("[]")).toThrow(/MALFORMED_MESSAGE/);
    expect(() => parseChallenge(TEXT.replace("}}", ',"extra":1}}'))).toThrow(/MALFORMED_MESSAGE/);
    expect(() => parseChallenge(TEXT.replace('"action":"app.close"', '"action":"app.close","action":"power.shutdown"'))).toThrow(/MALFORMED_MESSAGE/);
    expect(() => parseChallenge("{" + '"x":'.repeat(1) + "1".repeat(5000) + "}")).toThrow(/MALFORMED_MESSAGE|PAYLOAD_TOO_LARGE/);
  });

  it("binding: only the exact command this phone sent may be approved", () => {
    const c = parseChallenge(TEXT).challenge;
    const record = { commandId: c.command_id, pcId: c.pc_id, action: "app.close", params: {}, target: { app_id: "notepad" } };
    expect(checkChallengeBinding(c, record, c.controller_id)).toBeNull();
    expect(checkChallengeBinding(c, { ...record, commandId: "00000000-0000-4000-8000-000000000000" }, c.controller_id)).toMatch(/different command/);
    expect(checkChallengeBinding(c, { ...record, pcId: "00000000-0000-4000-8000-000000000000" }, c.controller_id)).toMatch(/different PC/);
    expect(checkChallengeBinding(c, record, "00000000-0000-4000-8000-000000000000")).toMatch(/not addressed to this phone/);
    expect(checkChallengeBinding(c, record, null)).toMatch(/not addressed/);
    expect(checkChallengeBinding(c, { ...record, action: "power.shutdown" }, c.controller_id)).toMatch(/different action/);
    expect(checkChallengeBinding(c, { ...record, params: { countdown_seconds: 0 } }, c.controller_id)).toMatch(/settings differ/);
    expect(checkChallengeBinding(c, { ...record, target: { app_id: "chrome" } }, c.controller_id)).toMatch(/targets something else/);
  });

  it("describes the challenge from the registry, never from the PC's label, and treats detail as secondary text", () => {
    const c = parseChallenge(TEXT.replace('"action_label":"Close Notepad"', '"action_label":"Harmless action"')).challenge;
    const d = describeChallenge(c, "Office PC", { appNames: { notepad: "Notepad" } });
    expect(d.primary).toBe("Close Notepad");
    expect(d.primary).not.toMatch(/Harmless/);
    expect(d.pcName).toBe("Office PC");
    expect(d.detail).toBe("Untitled — Notepad / 📝");
    // the PC-supplied pc_name is never used, even as a fallback
    expect(describeChallenge(c, null).pcName).toBe("your PC");
    expect(describeChallenge(c, null).primary).toBe("Close notepad");
  });

  it("builds a confirmation envelope the PC can verify with the same key, hashing the raw text", async () => {
    const parsed = parseChallenge(TEXT);
    const keyPair = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(keyPair.publicKey);
    const kid = await kidFromJwk(jwk);
    const frame = await buildConfirmationEnvelope({ parsed, controllerId: parsed.challenge.controller_id, decision: "approve", keyPair, now: new Date("2026-10-08T12:00:05.000Z") });
    expect(frame.type).toBe("confirmation");
    expect(frame.pc_id).toBe(parsed.challenge.pc_id);
    const verified = await verifyAndParseConfirmation(frame.envelope, (k) => (k === kid ? { controllerId: parsed.challenge.controller_id, accountId: "11111111-1111-4111-8111-111111111111", jwk } : null), new Date("2026-10-08T12:00:06.000Z"));
    expect(verified.approved).toBe(true);
    expect(verified.payload.challenge_digest).toBe(fixture.digests.challenge_digest);
    expect(verified.payload.challenge_id).toBe(parsed.challenge.challenge_id);
    expect(verified.payload.command_id).toBe(parsed.challenge.command_id);
  });
});

import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ProtocolError, challengeDigest, commandDigest, dumpsCompact, offerDigest, pairingCodeHandle, pairingVerificationCode, parseRfc3339, pcKeyMac, sdpFingerprints, verifyEnvelope, verifyScreenAnswer, type EcPublicJwk } from "../src/index.ts";

const fixtureDir = resolve(dirname(fileURLToPath(import.meta.url)), "../../protocol/fixtures");
const files = readdirSync(fixtureDir).filter((f) => /^es256-.*\.json$/.test(f)).sort();

interface Fixture {
  public_jwk: EcPublicJwk;
  kid: string;
  cases: Array<{ payload: string; envelope: unknown; expect: string; command_digest?: string }>;
  digests: { challenge_text: string; challenge_digest: string; pairing_code: string; pairing_code_handle: string; pairing_id: string; pc_id: string; pairing_verification_code: string };
  screen: {
    pc_public_jwk: EcPublicJwk;
    pc_kid: string;
    account_id: string;
    controller_id: string;
    pc_id: string;
    command_id: string;
    screen_session_id: string;
    offer_sdp: string;
    offer_digest: string;
    answer_sdp: string;
    fingerprint: string;
    now: string;
    cases: Array<{ name: string; envelope: unknown; expect: string; cause?: string }>;
    pc_key_mac: string;
  };
}

describe("cross-language fixtures", () => {
  it("has fixture files", () => {
    expect(files.length).toBeGreaterThan(0);
  });
  for (const file of files) {
    it(`verifies ${file}`, async () => {
      const data = JSON.parse(readFileSync(resolve(fixtureDir, file), "utf8")) as Fixture;
      const resolveJwk = (k: string) => (k === data.kid ? data.public_jwk : null);
      expect(data.cases.length).toBeGreaterThan(0);
      for (const c of data.cases) {
        if (c.expect === "valid") {
          const { payload } = await verifyEnvelope(c.envelope, resolveJwk);
          expect(dumpsCompact(payload)).toBe(c.payload);
          expect(await commandDigest(c.payload)).toBe(c.command_digest);
        } else {
          let code = "OK";
          try {
            await verifyEnvelope(c.envelope, resolveJwk);
          } catch (e) {
            if (!(e instanceof ProtocolError)) throw e;
            code = e.code;
          }
          if (c.expect === "reject") expect(code).not.toBe("OK");
          else expect(code).toBe(c.expect);
        }
      }
      const d = data.digests;
      expect(await challengeDigest(d.challenge_text)).toBe(d.challenge_digest);
      expect(await pairingCodeHandle(d.pairing_code)).toBe(d.pairing_code_handle);
      expect(await pairingVerificationCode(d.pairing_code, d.pairing_id, d.pc_id, data.kid)).toBe(d.pairing_verification_code);
      // protocol 1.4: the PC-signed screen answer verified against the pinned PC key, and the pairing pc_key_mac
      const sc = data.screen;
      expect(sc.cases.length).toBeGreaterThan(0);
      const expectation = { pinnedJwk: sc.pc_public_jwk, accountId: sc.account_id, controllerId: sc.controller_id, pcId: sc.pc_id, commandId: sc.command_id, offerSdp: sc.offer_sdp, now: parseRfc3339(sc.now) };
      for (const c of sc.cases) {
        if (c.expect === "valid") {
          const verified = await verifyScreenAnswer(c.envelope, expectation);
          expect([verified.pcKid, verified.payload.answer_sdp, verified.fingerprint, verified.payload.screen_session_id]).toEqual([sc.pc_kid, sc.answer_sdp, sc.fingerprint, sc.screen_session_id]);
        } else {
          let err: ProtocolError | null = null;
          try {
            await verifyScreenAnswer(c.envelope, expectation);
          } catch (e) {
            if (!(e instanceof ProtocolError)) throw e;
            err = e;
          }
          expect(err?.code, c.name).toBe(c.expect);
          if (c.cause) expect(err?.detail.cause, c.name).toBe(c.cause);
        }
      }
      expect(await offerDigest(sc.offer_sdp)).toBe(sc.offer_digest);
      expect(new Set(sdpFingerprints(sc.answer_sdp))).toEqual(new Set([sc.fingerprint]));
      expect(await pcKeyMac(d.pairing_code, d.pairing_id, d.pc_id, data.kid, sc.pc_kid)).toBe(sc.pc_key_mac);
    });
  }
});

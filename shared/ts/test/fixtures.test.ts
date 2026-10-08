import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ProtocolError, challengeDigest, commandDigest, dumpsCompact, pairingCodeHandle, pairingVerificationCode, verifyEnvelope, type EcPublicJwk } from "../src/index.ts";

const fixtureDir = resolve(dirname(fileURLToPath(import.meta.url)), "../../protocol/fixtures");
const files = readdirSync(fixtureDir).filter((f) => /^es256-.*\.json$/.test(f)).sort();

interface Fixture {
  public_jwk: EcPublicJwk;
  kid: string;
  cases: Array<{ payload: string; envelope: unknown; expect: string; command_digest?: string }>;
  digests: { challenge_text: string; challenge_digest: string; pairing_code: string; pairing_code_handle: string; pairing_id: string; pc_id: string; pairing_verification_code: string };
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
    });
  }
});

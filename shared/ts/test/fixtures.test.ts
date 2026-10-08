import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ProtocolError, challengeDigest, dumpsCompact, pairingVerificationCode, verifyEnvelope, type EcPublicJwk } from "../src/index.ts";

const fixtureDir = resolve(dirname(fileURLToPath(import.meta.url)), "../../protocol/fixtures");
const files = readdirSync(fixtureDir).filter((f) => /^es256-.*\.json$/.test(f)).sort();

interface Fixture {
  public_jwk: EcPublicJwk;
  kid: string;
  cases: Array<{ payload: string; envelope: unknown; expect: string }>;
  digests: { challenge_text: string; challenge_digest: string; pairing_verification_code: string };
}

describe("cross-language fixtures", () => {
  expect(files.length).toBeGreaterThan(0);
  for (const file of files) {
    it(`verifies ${file}`, async () => {
      const data = JSON.parse(readFileSync(resolve(fixtureDir, file), "utf8")) as Fixture;
      const resolveJwk = (k: string) => (k === data.kid ? data.public_jwk : null);
      expect(data.cases.length).toBeGreaterThan(0);
      for (const c of data.cases) {
        if (c.expect === "valid") {
          const { payload } = await verifyEnvelope(c.envelope, resolveJwk);
          expect(dumpsCompact(payload)).toBe(c.payload);
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
      expect(await challengeDigest(data.digests.challenge_text)).toBe(data.digests.challenge_digest);
      expect(await pairingVerificationCode("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "33333333-3333-4333-8333-333333333333", data.kid)).toBe(data.digests.pairing_verification_code);
    });
  }
});

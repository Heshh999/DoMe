/**
 * `signInputBatchWithKid` (lib/signing.ts, DECISIONS 79) produces the shared library's envelope without re-deriving
 * the kid per batch: the shared verifier accepts it, every field but the (randomised) signature equals
 * `signInputBatch`'s, a kid that is not the key's is refused by the verifier, and an invalid payload is refused before
 * anything is signed, with the shared signer's code.
 */
import { describe, expect, it, vi } from "vitest";

import { buildInputBatchPayload, exportPublicJwk, generateControllerKeyPair, kidFromJwk, LIMITS, parseEnvelopeShape, signInputBatch, verifyEnvelope, type InputBatchPayload } from "@dome/protocol";

import { signInputBatchWithKid } from "../src/lib/signing.ts";
import { ACCOUNT, CONTROLLER, PC } from "./helpers/harness.ts";

describe("signInputBatchWithKid", () => {
  it("signs the shared envelope with a precomputed kid; the shared verifier accepts it", async () => {
    const keyPair = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(keyPair.publicKey);
    const kid = await kidFromJwk(jwk);
    const payload = buildInputBatchPayload({ accountId: ACCOUNT, controllerId: CONTROLLER, targetPcId: PC, inputSessionId: "A".repeat(22), seq: 7, events: [{ type: "pointer_move", dx: 3, dy: -1 }], now: new Date("2026-10-26T18:00:00.000Z") });
    const mine = await signInputBatchWithKid(keyPair.privateKey, kid, payload);
    const shared = await signInputBatch(keyPair.privateKey, keyPair.publicKey, payload);
    expect({ ...mine, sig: "" }).toEqual({ ...shared, sig: "" }); // ECDSA signatures are randomised; everything else is identical
    expect(parseEnvelopeShape(mine)).toEqual(mine);
    const resolver = (k: string) => (k === kid ? jwk : null);
    const verified = await verifyEnvelope(mine, resolver);
    expect(verified.payload.seq).toBe(7);
    expect(verified.payload.events).toEqual([{ type: "pointer_move", dx: 3, dy: -1 }]);
    // the kid is bound to the key on the verifying side: a wrong one is UNKNOWN_KEY, never a verified batch
    await expect(verifyEnvelope({ ...mine, kid: "B".repeat(43) }, resolver)).rejects.toMatchObject({ code: "UNKNOWN_KEY" });
    // an invalid payload (one event too many) is refused before anything is signed, exactly like the shared signer
    const tooMany: InputBatchPayload = { ...payload, events: Array.from({ length: LIMITS.input_batch_max_events + 1 }, () => ({ type: "pointer_move" as const, dx: 1, dy: 0 })) };
    await expect(signInputBatchWithKid(keyPair.privateKey, kid, tooMany)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    await expect(signInputBatch(keyPair.privateKey, keyPair.publicKey, tooMany)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
  });

  it("refuses a signature that is not the raw 64-byte r‖s the envelope promises", async () => {
    const keyPair = await generateControllerKeyPair();
    const kid = await kidFromJwk(await exportPublicJwk(keyPair.publicKey));
    const payload = buildInputBatchPayload({ accountId: ACCOUNT, controllerId: CONTROLLER, targetPcId: PC, inputSessionId: "A".repeat(22), seq: 1, events: [], now: new Date("2026-10-26T18:00:00.000Z") });
    const sign = vi.spyOn(crypto.subtle, "sign").mockResolvedValueOnce(new ArrayBuffer(70)); // a DER-shaped answer
    try {
      await expect(signInputBatchWithKid(keyPair.privateKey, kid, payload)).rejects.toMatchObject({ code: "INTERNAL" });
    } finally {
      sign.mockRestore();
    }
    expect((await signInputBatchWithKid(keyPair.privateKey, kid, payload)).sig).toMatch(/^[A-Za-z0-9_-]{86}$/); // 64 bytes
  });
});

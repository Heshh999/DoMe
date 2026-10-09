import { describe, expect, it } from "vitest";

import { buildHelloProofPayload, exportPublicJwk, generateControllerKeyPair, kidFromJwk, ProtocolError, schemas, signHelloProof, verifyEnvelope } from "../src/index.ts";

const ACCOUNT = "11111111-1111-4111-8111-111111111111";

describe("hello proof", () => {
  it("builds a schema-valid payload with a 60 s window and signs it with the controller key", async () => {
    const pair = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(pair.publicKey);
    const kid = await kidFromJwk(jwk);
    const now = new Date("2026-10-08T12:00:00.000Z");
    const payload = buildHelloProofPayload({ kid, accountId: ACCOUNT, now });
    expect(payload).toMatchObject({ type: "hello_proof", protocol_version: "1.1", kid, account_id: ACCOUNT, issued_at: "2026-10-08T12:00:00.000Z", expires_at: "2026-10-08T12:01:00.000Z" });
    expect(payload.nonce).toMatch(/^[A-Za-z0-9_-]{22}$/);
    schemas.validateHelloProofPayload(payload);
    const envelope = await signHelloProof(pair.privateKey, pair.publicKey, payload);
    expect(envelope.kid).toBe(kid);
    const verified = await verifyEnvelope(envelope, (k) => (k === kid ? jwk : null));
    expect(verified.payload).toEqual(payload);
    // the frame the PWA sends is a valid controller hello
    schemas.validateFrame("controller_to_relay", { type: "hello", component: "controller", kid, proof: envelope, component_version: "0.1.0", protocol_versions: ["1.1"], registry_version: "1.1" });
  });

  it("refuses to sign a malformed payload", async () => {
    const pair = await generateControllerKeyPair();
    const payload = buildHelloProofPayload({ kid: "x", accountId: ACCOUNT });
    await expect(signHelloProof(pair.privateKey, pair.publicKey, payload)).rejects.toBeInstanceOf(ProtocolError);
  });
});

/**
 * Input-batch signing with the controller identity held in memory (DECISIONS 79). The shared `signInputBatch`
 * derives the `kid` from the public key on every call — exportKey, importKey and a SHA-256 digest, three WebCrypto
 * round trips — and the touchpad signs 30–40 batches a second inside each batch's age budget. This signer takes the
 * kid that `lib/controllerKey.ts` derived once and does the one thing that has to happen per batch: the ES256
 * signature. The envelope is the shared library's exactly (`parseEnvelopeShape`: v 1, ES256, the RFC 7638 kid, the
 * compact payload text, the raw r‖s signature), the payload is validated against the frozen schema as `signInputBatch`
 * does, and the private key never leaves WebCrypto. `test/signing.test.ts` checks that the shared verifier accepts it.
 */
import { b64url, dumpsCompact, ES256_SIGN, ProtocolError, schemas, type Envelope, type InputBatchPayload, type JsonValue } from "@dome/protocol";

export async function signInputBatchWithKid(privateKey: CryptoKey, kid: string, payload: InputBatchPayload): Promise<Envelope> {
  schemas.validateInputBatchPayload(payload);
  const text = dumpsCompact(payload as unknown as JsonValue);
  const sig = await crypto.subtle.sign(ES256_SIGN, privateKey, new TextEncoder().encode(text));
  if (sig.byteLength !== 64) throw new ProtocolError("INTERNAL", "unexpected signature length");
  return { v: 1, alg: "ES256", kid, payload: text, sig: b64url(sig) };
}

/**
 * Detached ES256 envelopes: sign the exact UTF-8 bytes of a payload string; verify the
 * signature first and only then parse the payload once with the strict parser.
 */
import { ProtocolError } from "./errors.ts";
import { ES256_SIGN, b64url, exportPublicJwk, importPublicJwk, kidFromJwk, unb64url, type EcPublicJwk } from "./keys.ts";
import { DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH, loadsStrict, utf8ByteLength, type JsonValue } from "./strictJson.ts";

export interface Envelope {
  v: 1;
  alg: "ES256";
  kid: string;
  payload: string;
  sig: string;
}

const ENVELOPE_FIELDS = ["alg", "kid", "payload", "sig", "v"];
const SUPPORTED_ALGS = new Set(["ES256"]);

export function parseEnvelopeShape(raw: unknown): Envelope {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw new ProtocolError("MALFORMED_MESSAGE", "envelope must be an object");
  const keys = Object.keys(raw).sort();
  if (keys.join(",") !== ENVELOPE_FIELDS.join(",")) throw new ProtocolError("MALFORMED_MESSAGE", "envelope must have exactly v, alg, kid, payload, sig");
  const e = raw as Record<string, unknown>;
  if (e.v !== 1) throw new ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported envelope version");
  if (typeof e.alg !== "string" || !SUPPORTED_ALGS.has(e.alg)) throw new ProtocolError("SIGNATURE_INVALID", "unsupported signature algorithm");
  if (typeof e.kid !== "string" || e.kid.length !== 43) throw new ProtocolError("MALFORMED_MESSAGE", "invalid kid");
  if (typeof e.payload !== "string" || e.payload.length < 2) throw new ProtocolError("MALFORMED_MESSAGE", "invalid payload");
  if (typeof e.sig !== "string" || e.sig.length !== 86) throw new ProtocolError("SIGNATURE_INVALID", "invalid signature encoding");
  return { v: 1, alg: "ES256", kid: e.kid, payload: e.payload, sig: e.sig };
}

export async function signPayload(privateKey: CryptoKey, publicKey: CryptoKey, payloadText: string): Promise<Envelope> {
  const jwk = await exportPublicJwk(publicKey);
  const kid = await kidFromJwk(jwk);
  const sig = await crypto.subtle.sign(ES256_SIGN, privateKey, new TextEncoder().encode(payloadText));
  if (sig.byteLength !== 64) throw new ProtocolError("INTERNAL", "unexpected signature length");
  return { v: 1, alg: "ES256", kid, payload: payloadText, sig: b64url(sig) };
}

export async function verifySignature(publicJwk: EcPublicJwk, payloadText: string, sigB64url: string): Promise<void> {
  const raw = unb64url(sigB64url, 64);
  const allZeroR = raw.slice(0, 32).every((b) => b === 0);
  const allZeroS = raw.slice(32).every((b) => b === 0);
  if (allZeroR || allZeroS) throw new ProtocolError("SIGNATURE_INVALID", "signature verification failed");
  const key = await importPublicJwk(publicJwk);
  const ok = await crypto.subtle.verify(ES256_SIGN, key, raw as BufferSource, new TextEncoder().encode(payloadText) as BufferSource);
  if (!ok) throw new ProtocolError("SIGNATURE_INVALID", "signature verification failed");
}

export type JwkResolver = (kid: string) => EcPublicJwk | null | undefined | Promise<EcPublicJwk | null | undefined>;

export interface VerifyOptions {
  maxPayloadBytes?: number;
  maxDepth?: number;
}

/** Verify, then parse once. Resolver must only return keys for currently paired, non-revoked controllers. */
export async function verifyEnvelope(
  raw: unknown,
  resolveJwk: JwkResolver,
  options: VerifyOptions = {},
): Promise<{ envelope: Envelope; payload: { [key: string]: JsonValue } }> {
  const maxBytes = options.maxPayloadBytes ?? DEFAULT_MAX_BYTES;
  const env = parseEnvelopeShape(raw);
  if (utf8ByteLength(env.payload) > maxBytes) throw new ProtocolError("PAYLOAD_TOO_LARGE", "payload too large");
  const jwk = await resolveJwk(env.kid);
  if (!jwk) throw new ProtocolError("UNKNOWN_KEY", "no paired key for kid");
  if ((await kidFromJwk(jwk)) !== env.kid) throw new ProtocolError("UNKNOWN_KEY", "key id does not match stored key");
  await verifySignature(jwk, env.payload, env.sig);
  const payload = loadsStrict(env.payload, { maxBytes, maxDepth: options.maxDepth ?? DEFAULT_MAX_DEPTH, requireObject: true });
  return { envelope: env, payload: payload as { [key: string]: JsonValue } };
}

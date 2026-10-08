/**
 * Controller key handling with WebCrypto. Keys are ECDSA P-256, generated non-extractable and
 * intended to be persisted as CryptoKey objects in IndexedDB (never exported, never in
 * localStorage).
 */
import { ProtocolError } from "./errors.ts";

export interface EcPublicJwk {
  kty: "EC";
  crv: "P-256";
  x: string;
  y: string;
}

const B64URL_RE = /^[A-Za-z0-9_-]*$/;

export function b64url(bytes: Uint8Array | ArrayBuffer): string {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let bin = "";
  for (const b of u8) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64url(text: string, expectedLength?: number): Uint8Array {
  if (typeof text !== "string" || !B64URL_RE.test(text)) throw new ProtocolError("MALFORMED_MESSAGE", "invalid base64url");
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  let bin: string;
  try {
    bin = atob(padded);
  } catch {
    throw new ProtocolError("MALFORMED_MESSAGE", "invalid base64url");
  }
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  if (expectedLength !== undefined && out.length !== expectedLength) {
    throw new ProtocolError("MALFORMED_MESSAGE", `expected ${expectedLength} bytes`);
  }
  return out;
}

const subtle = (): SubtleCrypto => {
  const s = globalThis.crypto?.subtle;
  if (!s) throw new ProtocolError("PLATFORM_UNSUPPORTED", "WebCrypto is not available in this context");
  return s;
};

export const ECDSA_P256: EcKeyGenParams & EcKeyImportParams = { name: "ECDSA", namedCurve: "P-256" };
export const ES256_SIGN: EcdsaParams = { name: "ECDSA", hash: "SHA-256" };

/** Generate a non-extractable controller signing key pair. */
export async function generateControllerKeyPair(): Promise<CryptoKeyPair> {
  return subtle().generateKey(ECDSA_P256, false, ["sign", "verify"]);
}

export function assertEcPublicJwk(jwk: unknown): EcPublicJwk {
  if (typeof jwk !== "object" || jwk === null || Array.isArray(jwk)) throw new ProtocolError("MALFORMED_MESSAGE", "JWK must be an object");
  const keys = Object.keys(jwk).sort();
  if (keys.join(",") !== "crv,kty,x,y") throw new ProtocolError("MALFORMED_MESSAGE", "JWK must have exactly kty, crv, x, y");
  const j = jwk as Record<string, unknown>;
  if (j.kty !== "EC" || j.crv !== "P-256") throw new ProtocolError("MALFORMED_MESSAGE", "JWK must be EC P-256");
  if (typeof j.x !== "string" || typeof j.y !== "string") throw new ProtocolError("MALFORMED_MESSAGE", "JWK coordinates must be strings");
  unb64url(j.x, 32);
  unb64url(j.y, 32);
  return { kty: "EC", crv: "P-256", x: j.x, y: j.y };
}

/** Export only the public members (drops ext/key_ops/alg that WebCrypto adds). */
export async function exportPublicJwk(publicKey: CryptoKey): Promise<EcPublicJwk> {
  const raw = (await subtle().exportKey("jwk", publicKey)) as JsonWebKey;
  return assertEcPublicJwk({ kty: raw.kty, crv: raw.crv, x: raw.x, y: raw.y });
}

export async function importPublicJwk(jwk: unknown): Promise<CryptoKey> {
  const clean = assertEcPublicJwk(jwk);
  try {
    return await subtle().importKey("jwk", { ...clean, ext: true }, ECDSA_P256, true, ["verify"]);
  } catch {
    throw new ProtocolError("MALFORMED_MESSAGE", "JWK point is not on the curve");
  }
}

/** RFC 7638 JWK thumbprint (SHA-256), base64url — the controller `kid`. */
export async function kidFromJwk(jwk: unknown): Promise<string> {
  const clean = assertEcPublicJwk(jwk);
  await importPublicJwk(clean); // validates the point
  const canonical = `{"crv":"P-256","kty":"EC","x":"${clean.x}","y":"${clean.y}"}`;
  const digest = await subtle().digest("SHA-256", new TextEncoder().encode(canonical));
  return b64url(digest);
}

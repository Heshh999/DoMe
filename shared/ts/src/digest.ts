import { b64url } from "./keys.ts";

export async function sha256B64url(data: Uint8Array | string): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  return b64url(await crypto.subtle.digest("SHA-256", bytes as BufferSource));
}

/** Digest of the challenge exactly as serialised by the PC (keep the raw text, never re-stringify). */
export async function challengeDigest(challengeText: string): Promise<string> {
  return sha256B64url(challengeText);
}

/** 6-digit pairing verification code; identical formula to the Python implementation. */
export async function pairingVerificationCode(pairingId: string, pcId: string, kid: string): Promise<string> {
  const material = new TextEncoder().encode(`dome-pair-v1|${pairingId}|${pcId}|${kid}`);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", material));
  let n = 0n;
  for (let i = 0; i < 8; i++) n = (n << 8n) | BigInt(digest[i] ?? 0);
  return (n % 1_000_000n).toString().padStart(6, "0");
}

export function randomNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return b64url(bytes);
}

export function randomUuid(): string {
  return crypto.randomUUID();
}

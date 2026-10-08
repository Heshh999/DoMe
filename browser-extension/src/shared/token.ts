/** 22-character base64url tokens (16 random bytes): tab_token and browser_instance_id. */

export const TOKEN_RE = /^[A-Za-z0-9_-]{22}$/;
export const BROWSER_INSTANCE_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

export function bytesToBase64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomToken22(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return bytesToBase64url(bytes);
}

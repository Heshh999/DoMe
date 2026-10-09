/**
 * Pairing helpers around the shared implementation. The pairing code is a secret the backend never
 * sees: it only ever leaves this module as `pairingCodeHandle(code)` (SHA-256) and as the HMAC key for
 * the 6-digit verification code. It is never logged, stored or placed in a URL by the app.
 */
import { ProtocolError, formatPairingCode, normalizePairingCode, pairingCodeHandle, pairingVerificationCode, type Capability } from "@dome/protocol";

export { formatPairingCode, normalizePairingCode, pairingCodeHandle, pairingVerificationCode };

/** Requested by default at pairing time (the PC owner decides what is actually granted, on the PC). */
export const ALL_CAPABILITIES: readonly Capability[] = ["status", "media", "volume", "apps", "lock", "power", "pointer", "keyboard"];

/**
 * Accept what a QR scan or a paste produces: `https://<app>/pair#code=XXXXX-XXXXX-XXXXX-XXXXX`, the
 * bare fragment, or the 20-symbol code in any grouping/case. Returns the normalised code.
 */
export function parsePairingInput(text: string): string {
  const trimmed = text.trim();
  let candidate = trimmed;
  const hash = trimmed.indexOf("#");
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) || hash >= 0) {
    const fragment = hash >= 0 ? trimmed.slice(hash + 1) : "";
    const params = new URLSearchParams(fragment);
    const code = params.get("code");
    if (!code) throw new ProtocolError("PAIRING_CODE_INVALID", "the link does not carry a pairing code");
    candidate = code;
  } else if (trimmed.toLowerCase().startsWith("code=")) {
    candidate = trimmed.slice(5);
  }
  return normalizePairingCode(candidate);
}

/** Read `#code=…` from the current location (deep link) and scrub it from the URL/history immediately. */
export function takeCodeFromLocation(loc: { hash: string }, history: { replaceState(data: unknown, unused: string, url?: string | URL | null): void }, pathname: string): string | null {
  if (!loc.hash || loc.hash.length < 2) return null;
  let code: string | null = null;
  try {
    code = parsePairingInput(`#${loc.hash.replace(/^#/, "")}`);
  } catch {
    code = null;
  }
  history.replaceState(null, "", pathname);
  return code;
}

/**
 * The signed-out deep-link path (`app/navigation.ts`) drops the pairing code and comes back with a
 * non-secret `scan_again=1` marker. Read it once and remove it from the address bar.
 */
export function takeScanAgainHint(loc: { search: string; hash: string }, history: { replaceState(data: unknown, unused: string, url?: string | URL | null): void }, pathname: string, param = "scan_again"): boolean {
  const params = new URLSearchParams(loc.search);
  if (params.get(param) !== "1") return false;
  params.delete(param);
  const rest = params.toString();
  // Keep a (non-code) fragment only if one is present; the code fragment is scrubbed by takeCodeFromLocation.
  history.replaceState(null, "", `${pathname}${rest ? `?${rest}` : ""}${loc.hash && loc.hash !== "#" ? loc.hash : ""}`);
  return true;
}

export function defaultControllerName(userAgent: string): string {
  if (/iPhone/.test(userAgent)) return "iPhone";
  if (/iPad|Macintosh.*Mobile/.test(userAgent)) return "iPad";
  if (/Android/.test(userAgent)) return "Android phone";
  if (/Windows/.test(userAgent)) return "Windows browser";
  if (/Macintosh/.test(userAgent)) return "Mac browser";
  return "This phone";
}

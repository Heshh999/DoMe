import { ProtocolError } from "./errors.ts";

const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?Z$/;

/** `YYYY-MM-DDTHH:MM:SS.mmmZ` — exactly what Date#toISOString produces. */
export function formatRfc3339(date: Date = new Date()): string {
  return date.toISOString();
}

/** Strict RFC 3339 UTC parse. Rejects impossible dates (V8 would otherwise roll 2026-02-30 to March 2). */
export function parseRfc3339(text: unknown): Date {
  if (typeof text !== "string") throw new ProtocolError("MALFORMED_MESSAGE", "timestamp must be a string");
  const m = RFC3339.exec(text);
  if (!m || /[^\x00-\x7f]/.test(text)) throw new ProtocolError("MALFORMED_MESSAGE", "timestamp must be RFC 3339 UTC (…Z)");
  const ms = (m[7] ?? "0").padEnd(3, "0");
  const normalised = `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}.${ms}Z`;
  const d = new Date(normalised);
  if (Number.isNaN(d.getTime()) || d.toISOString() !== normalised) {
    throw new ProtocolError("MALFORMED_MESSAGE", "invalid timestamp");
  }
  return d;
}

export interface WindowOptions {
  now?: Date;
  maxLifetimeSeconds?: number;
  maxSkewSeconds?: number;
}

/** Reject expired, future-dated, or over-long lifetimes (commands and confirmations alike). */
export function checkCommandWindow(issuedAt: string, expiresAt: string, options: WindowOptions = {}): void {
  const now = options.now ?? new Date();
  const maxLifetime = (options.maxLifetimeSeconds ?? 300) * 1000;
  const skew = (options.maxSkewSeconds ?? 5) * 1000;
  const issued = parseRfc3339(issuedAt).getTime();
  const expires = parseRfc3339(expiresAt).getTime();
  if (expires <= issued) throw new ProtocolError("MALFORMED_MESSAGE", "expires_at must be after issued_at");
  if (expires - issued > maxLifetime) throw new ProtocolError("MALFORMED_MESSAGE", "lifetime too long");
  if (issued > now.getTime() + skew) throw new ProtocolError("CLOCK_SKEW", "issued in the future", true);
  if (expires < now.getTime() - skew) throw new ProtocolError("COMMAND_EXPIRED", "expired", true);
}

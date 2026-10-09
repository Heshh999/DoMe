/**
 * Redacting in-memory logger. Nothing here persists; the ring buffer feeds the customer-initiated
 * diagnostics download (Settings → Diagnostics). Field values whose key looks like a secret or like
 * untrusted display text (tokens, codes, pairing material, challenge text, payloads, media titles,
 * e-mail) are replaced before they are stored or printed. Typed keyboard content (`text`, `composer`,
 * input `events`/`key`) is dropped the same way: no call site passes it, and the regex guarantees it
 * would not survive if one did.
 */
export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LogEntry {
  at: string;
  level: LogLevel;
  event: string;
  fields?: Record<string, unknown>;
}

/**
 * Keys whose values are never kept. `code` alone is NOT matched: it carries stable protocol error
 * codes (api.error, command.result, invalid_frame_dropped) that diagnostics exist to show. Pairing
 * material has explicit names.
 */
const REDACT_KEY = /(token|pairing_code|code_hash|user_code|verification|secret|challenge|payload|^sig$|signature|title|artist|detail|email|csrf|jwk|cookie|password|display_name|nonce|url|^text$|_text$|composer|^events$|^key$|^keys$)/i;
const MAX_ENTRIES = 120;
const MAX_STRING = 160;

const buffer: LogEntry[] = [];

export function redact(value: unknown, depth = 0): unknown {
  if (depth > 6) return "[depth]";
  if (typeof value === "string") return value.length > MAX_STRING ? `${value.slice(0, MAX_STRING)}…` : value;
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.slice(0, 32).map((v) => redact(v, depth + 1));
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = REDACT_KEY.test(k) ? "[redacted]" : redact(v, depth + 1);
  }
  return out;
}

function record(level: LogLevel, event: string, fields?: Record<string, unknown>): void {
  const entry: LogEntry = { at: new Date().toISOString(), level, event };
  if (fields) entry.fields = redact(fields) as Record<string, unknown>;
  buffer.push(entry);
  if (buffer.length > MAX_ENTRIES) buffer.splice(0, buffer.length - MAX_ENTRIES);
  if (level === "error") console.error(`[dome] ${event}`, entry.fields ?? "");
  else if (level === "warn") console.warn(`[dome] ${event}`, entry.fields ?? "");
}

export const log = {
  debug: (event: string, fields?: Record<string, unknown>) => record("debug", event, fields),
  info: (event: string, fields?: Record<string, unknown>) => record("info", event, fields),
  warn: (event: string, fields?: Record<string, unknown>) => record("warn", event, fields),
  error: (event: string, fields?: Record<string, unknown>) => record("error", event, fields),
};

export function recentLogs(): LogEntry[] {
  return buffer.slice();
}

export function clearLogs(): void {
  buffer.length = 0;
}

/** Summarise an unknown thrown value without leaking message contents that may include remote text. */
export function errorSummary(e: unknown): Record<string, unknown> {
  if (e && typeof e === "object") {
    const o = e as { name?: unknown; code?: unknown; status?: unknown };
    return { name: typeof o.name === "string" ? o.name : "Error", code: typeof o.code === "string" ? o.code : undefined, status: typeof o.status === "number" ? o.status : undefined };
  }
  return { name: typeof e };
}

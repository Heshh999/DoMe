/**
 * Structured console logging with a fixed field vocabulary. Rules (spec §15): never log tokens,
 * pairing material, media titles, URLs, DOM text or whole frames; only codes, counts, ids that are
 * not secrets (request ids, tab ids, browser_instance_id) and durations.
 */
export type LogValue = string | number | boolean | null | undefined;

function emit(level: "info" | "warn" | "error", event: string, fields?: Record<string, LogValue>): void {
  const line = `[DoMe] ${event}`;
  const payload = fields ? { ...fields } : undefined;
  if (level === "info") console.info(line, payload ?? "");
  else if (level === "warn") console.warn(line, payload ?? "");
  else console.error(line, payload ?? "");
}

export const log = {
  info: (event: string, fields?: Record<string, LogValue>) => emit("info", event, fields),
  warn: (event: string, fields?: Record<string, LogValue>) => emit("warn", event, fields),
  error: (event: string, fields?: Record<string, LogValue>) => emit("error", event, fields),
};

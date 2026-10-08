/**
 * Everything the content script reads from the page is untrusted display data. The background
 * re-checks it structurally before it becomes part of a contract frame; invalid optional fields are
 * dropped, invalid required fields make the snapshot unusable (null).
 */
import type { Context, PlayerSnapshot } from "./messages.ts";
import { VIDEO_ID_RE } from "./youtubeUrl.ts";

const CONTEXTS: readonly Context[] = ["watch", "shorts", "music", "other"];
// eslint-disable-next-line no-control-regex -- stripping control characters is the point
const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\ufeff]/g;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function cleanText(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const cleaned = value.replace(CONTROL_CHARS, " ").replace(/\s+/g, " ").trim();
  if (!cleaned) return undefined;
  return cleaned.length > maxLength ? cleaned.slice(0, maxLength) : cleaned;
}

function bool(v: unknown): boolean | undefined {
  return typeof v === "boolean" ? v : undefined;
}

function nonNegativeNumber(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : undefined;
}

export function sanitizeSnapshot(raw: unknown): PlayerSnapshot | null {
  if (!isRecord(raw)) return null;
  const context = raw.context;
  if (typeof context !== "string" || !CONTEXTS.includes(context as Context)) return null;
  const ad = bool(raw.ad_showing);
  const live = bool(raw.is_live);
  const playlist = bool(raw.in_playlist);
  if (ad === undefined || live === undefined || playlist === undefined) return null;
  const out: PlayerSnapshot = { context: context as Context, ad_showing: ad, is_live: live, in_playlist: playlist };
  const title = cleanText(raw.title, 200);
  if (title !== undefined) out.title = title;
  if (typeof raw.video_id === "string" && VIDEO_ID_RE.test(raw.video_id)) out.video_id = raw.video_id;
  for (const key of ["paused", "muted", "theater", "fullscreen", "has_next", "has_previous"] as const) {
    const b = bool(raw[key]);
    if (b !== undefined) out[key] = b;
  }
  if (typeof raw.volume === "number" && Number.isFinite(raw.volume)) {
    out.volume = Math.min(100, Math.max(0, Math.round(raw.volume)));
  }
  const pos = nonNegativeNumber(raw.position_seconds);
  if (pos !== undefined) out.position_seconds = pos;
  const dur = nonNegativeNumber(raw.duration_seconds);
  if (dur !== undefined) out.duration_seconds = dur;
  return out;
}

/** Profile label typed by the customer in the popup: printable, at most 64 characters. */
export function sanitizeLabel(raw: unknown): string {
  return cleanText(raw, 64) ?? "";
}

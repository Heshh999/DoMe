/**
 * The PC's `cursor_shape` messages, checked before anything is drawn (protocol 1.5, rules.screen_cursor; design
 * screen-view-2a §4.3, finding 4). Pure and DOM-free.
 *
 * A named kind is drawn with the phone's own art. A custom shape is a small RGBA PNG in standard base64; the schema
 * already bounds it (at most 64 × 64, at most 8192 characters, a base64-valid length). Here only its header is read,
 * as bytes: the 8-byte PNG signature, the `IHDR` chunk and the width and height it states, which must equal the
 * message's own `width`/`height`, with the hotspot inside the bitmap. Nothing is decoded into pixels and nothing is
 * drawn here: the overlay shows the bitmap as an `<img>` from a `data:` URL (CSP `img-src 'self' data:`).
 *
 * A cursor bitmap is screen content (rules.screen_content): it is never logged or stored. A refusal logs one reason
 * word only.
 */
import type { ScreenDcPcToPhone } from "@dome/protocol";

import { log } from "./log.ts";
import type { CursorShape } from "./pointer.ts";

/** The largest custom pointer the PC sends (schema `width`/`height` maximum). */
export const CURSOR_BITMAP_MAX_PX = 64;

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
/** "IHDR" at bytes 12..15: the first chunk of every PNG. */
const IHDR = [0x49, 0x48, 0x44, 0x52];
/** 44 base64 characters are 33 bytes: the signature, the IHDR length and type, width and height (bytes 0..23) and more. */
const HEADER_CHARS = 44;

export type CursorShapeMessage = Extract<ScreenDcPcToPhone, { type: "cursor_shape" }>;

/** Why a custom shape was refused (logged as this word only). */
export type CursorShapeRejection = "bad_header" | "size_mismatch" | "too_large" | "hotspot_outside";

/**
 * The width and height a base64 PNG states in its IHDR chunk, or null when the prefix is not a PNG header (or cannot
 * be decoded at all: this never throws, whatever the string).
 */
export function pngDimensions(b64: string): { width: number; height: number } | null {
  try {
    const head = atob(b64.slice(0, HEADER_CHARS));
    if (head.length < 24) return null;
    for (let i = 0; i < PNG_SIGNATURE.length; i++) if (head.charCodeAt(i) !== PNG_SIGNATURE[i]) return null;
    for (let i = 0; i < IHDR.length; i++) if (head.charCodeAt(12 + i) !== IHDR[i]) return null;
    const u32 = (at: number): number => ((head.charCodeAt(at) << 24) >>> 0) + (head.charCodeAt(at + 1) << 16) + (head.charCodeAt(at + 2) << 8) + head.charCodeAt(at + 3);
    return { width: u32(16), height: u32(20) };
  } catch {
    return null;
  }
}

function reject(reason: CursorShapeRejection): null {
  log.info("screen.cursor_shape_rejected", { reason });
  return null;
}

/** A shape to draw, or null when a custom bitmap is not what its message says (the caller counts it). */
export function parseCursorShape(msg: CursorShapeMessage): CursorShape | null {
  if (msg.kind !== "custom") return { kind: msg.kind };
  const custom = msg as Extract<CursorShapeMessage, { kind: "custom" }>;
  if (custom.width > CURSOR_BITMAP_MAX_PX || custom.height > CURSOR_BITMAP_MAX_PX || custom.width < 1 || custom.height < 1) return reject("too_large");
  const dims = pngDimensions(custom.png);
  if (!dims) return reject("bad_header");
  if (dims.width !== custom.width || dims.height !== custom.height) return reject("size_mismatch");
  if (custom.hotspot_x < 0 || custom.hotspot_y < 0 || custom.hotspot_x >= custom.width || custom.hotspot_y >= custom.height) return reject("hotspot_outside");
  return { kind: "custom", width: custom.width, height: custom.height, hotspotX: custom.hotspot_x, hotspotY: custom.hotspot_y, src: `data:image/png;base64,${custom.png}` };
}

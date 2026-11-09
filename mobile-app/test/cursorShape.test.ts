/**
 * cursorShape.ts (design screen-view-2a §4.3, finding 4): a custom pointer is checked by its PNG header bytes only (no
 * canvas, no decoding into pixels). The real 1×1 RGBA PNG of the shared fixture cases is accepted; a wrong signature,
 * a truncated or undecodable string, a size that differs from the message, more than 64 px and a hotspot outside the
 * bitmap are refused and logged with a reason word only, never the bitmap. `pngDimensions` never throws, whatever the
 * string (a schema-valid 33-character `png` used to throw out of the data-channel handler).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { parseCursorShape, pngDimensions, type CursorShapeMessage } from "../src/lib/cursorShape.ts";
import { clearLogs, recentLogs } from "../src/lib/log.ts";

const cases = JSON.parse(readFileSync(resolve(__dirname, "../../shared/protocol/fixtures/screen-dc-cases.json"), "utf8")) as { accepted: Array<{ name: string; text: string }> };
const realCustom = JSON.parse(cases.accepted.find((c) => c.name.includes("real 1x1"))!.text) as Extract<CursorShapeMessage, { kind: "custom" }>;
const PNG_1X1 = realCustom.png;

/** A PNG header stating `w × h` (signature, IHDR length 13, "IHDR", width, height, then filler), in base64. */
function header(w: number, h: number, signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]): string {
  const bytes = [...signature, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52, (w >>> 24) & 255, (w >>> 16) & 255, (w >>> 8) & 255, w & 255, (h >>> 24) & 255, (h >>> 16) & 255, (h >>> 8) & 255, h & 255, 8, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  return btoa(String.fromCharCode(...bytes));
}

function custom(over: Partial<Extract<CursorShapeMessage, { kind: "custom" }>> = {}): CursorShapeMessage {
  return { ...realCustom, ...over };
}

const rejections = () => recentLogs().filter((l) => l.event === "screen.cursor_shape_rejected");

beforeEach(() => clearLogs());

describe("pngDimensions", () => {
  it("reads width and height from the real 1×1 PNG and from a stated header", () => {
    expect(pngDimensions(PNG_1X1)).toEqual({ width: 1, height: 1 });
    expect(pngDimensions(header(24, 32))).toEqual({ width: 24, height: 32 });
    expect(pngDimensions(header(64, 64))).toEqual({ width: 64, height: 64 });
  });

  it("is null for a wrong signature, a truncated header and anything atob refuses; it never throws", () => {
    expect(pngDimensions(header(1, 1, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0b]))).toBeNull();
    expect(pngDimensions(PNG_1X1.slice(0, 28))).toBeNull(); // 21 bytes: no room for the height
    for (const s of ["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "!!!!", "", "A", "iVBORw0KGg-AAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"]) expect(() => pngDimensions(s), s).not.toThrow();
    expect(pngDimensions("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")).toBeNull();
    // a valid PNG signature without the IHDR chunk type in its place
    expect(pngDimensions(btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x44, 0x41, 0x54, 0, 0, 0, 1, 0, 0, 0, 1)))).toBeNull();
  });
});

describe("parseCursorShape", () => {
  it("a named kind is drawn with the phone's own art", () => {
    expect(parseCursorShape({ type: "cursor_shape", kind: "ibeam" })).toEqual({ kind: "ibeam" });
    expect(rejections()).toHaveLength(0);
  });

  it("the real 1×1 PNG is accepted as a data: URL with its hotspot", () => {
    expect(parseCursorShape(custom())).toEqual({ kind: "custom", width: 1, height: 1, hotspotX: 0, hotspotY: 0, src: `data:image/png;base64,${PNG_1X1}` });
  });

  it("a wrong signature, a truncated png, a mismatched size, 65 px and a hotspot outside are refused with a reason word only", () => {
    const refused: Array<[string, CursorShapeMessage, string]> = [
      ["signature", custom({ png: header(1, 1, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0b]) }), "bad_header"],
      ["truncated", custom({ png: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" }), "bad_header"],
      ["size", custom({ width: 2 }), "size_mismatch"],
      ["65 px", custom({ width: 65, height: 65, png: header(65, 65) }), "too_large"],
      ["hotspot x", custom({ png: header(24, 24), width: 24, height: 24, hotspot_x: 24 }), "hotspot_outside"],
      ["hotspot y", custom({ png: header(24, 24), width: 24, height: 24, hotspot_y: 30 }), "hotspot_outside"],
    ];
    for (const [name, msg, reason] of refused) {
      clearLogs();
      expect(parseCursorShape(msg), name).toBeNull();
      expect(rejections().map((l) => l.fields), name).toEqual([{ reason }]);
    }
    expect(JSON.stringify(recentLogs())).not.toContain("iVBOR");
  });
});

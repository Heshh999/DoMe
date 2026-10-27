/**
 * "The phone never reads pixels" (docs/design/screen-view.md §17.2; version.json rules.screen_content): the Screen view
 * picture only ever becomes a `<video>.srcObject`. This scan fails the build if any file under `src/` uses an API that
 * can read, copy or record pixels or media — outside one allow-listed file, the pairing page's camera QR scanner,
 * which reads the CAMERA (never the Screen view stream) and is imported by nothing in the Screen view code.
 * `requestVideoFrameCallback` (frame timing metadata only) is allowed and is not matched by the word patterns.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");

/** Pixel or media readers and recorders, as whole words (so `onVideoFrame` / `requestVideoFrameCallback` do not match). */
const FORBIDDEN = /\b(drawImage|getImageData|putImageData|toDataURL|toBlob|captureStream|MediaRecorder|ImageCapture|VideoFrame|VideoDecoder|MediaStreamTrackProcessor|OffscreenCanvas|createImageBitmap|readPixels|transferToImageBitmap)\b/g;

/** The same patterns without the global flag, for single tests (a /g regex keeps state between `.test` calls). */
const FORBIDDEN_ANY = new RegExp(FORBIDDEN.source);

/** file → why it may read pixels. Anything added here needs the same kind of reason, in review. */
const ALLOW: Record<string, string> = {
  "lib/qr.ts": "the pairing page's camera QR scanner (jsQR needs the camera frame's pixels); never the Screen view stream",
};

/** The Screen view modules: stricter still, not even a canvas. */
const SCREEN_FILES = ["lib/screen.ts", "lib/screenMap.ts", "lib/screenInput.ts", "lib/screenStatus.ts", "lib/directGestures.ts", "lib/pcKeyPins.ts", "lib/wakeLock.ts", "store/screen.ts", "components/ScreenSurface.tsx", "components/ScreenLoupe.tsx", "components/ScreenViewTile.tsx", "components/ScreenReadout.tsx", "lib/screenStats.ts", "pages/app/ScreenPage.tsx"];

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return walk(path);
    return /\.(ts|tsx|js|mjs)$/.test(name) ? [path] : [];
  });
}

const files = walk(SRC).map((path) => ({ rel: relative(SRC, path).split("\\").join("/"), text: readFileSync(path, "utf8") }));

describe("no pixel access", () => {
  it("no file under src/ reads, copies or records pixels or media, except the allow-listed camera QR scanner", () => {
    const hits = files.flatMap((f) => [...f.text.matchAll(FORBIDDEN)].map((m) => `${f.rel}: ${m[0]}`)).filter((hit) => !Object.keys(ALLOW).some((file) => hit.startsWith(`${file}:`)));
    expect(hits).toEqual([]);
    // the allow-list stays honest: every entry still needs it
    for (const file of Object.keys(ALLOW)) expect(files.find((f) => f.rel === file)?.text.match(FORBIDDEN)?.length ?? 0, file).toBeGreaterThan(0);
  });

  it("the Screen view modules use no canvas at all, import nothing that reads pixels, and hand the stream only to srcObject", () => {
    for (const rel of SCREEN_FILES) {
      const f = files.find((x) => x.rel === rel);
      expect(f, rel).toBeDefined();
      const text = f!.text;
      expect(text, rel).not.toMatch(/getContext\(|<canvas|createElement\(\s*["'`]canvas|HTMLCanvasElement/i);
      expect(text, rel).not.toMatch(FORBIDDEN_ANY);
      for (const file of Object.keys(ALLOW)) expect(text, rel).not.toContain(file.replace(/^lib\//, "/").replace(/\.ts$/, ".ts"));
      // the stream object goes to a <video> and nowhere else
      for (const m of text.matchAll(/(\w+(?:\.\w+)*)\s*=\s*stream\b/g)) expect(m[1], rel).toMatch(/srcObject$/);
    }
  });

  it("the Screen view code sends no media anywhere: no fetch, no WebSocket and no storage of the stream", () => {
    for (const rel of SCREEN_FILES.filter((r) => r !== "lib/pcKeyPins.ts")) {
      const text = files.find((x) => x.rel === rel)!.text;
      expect(text, rel).not.toMatch(/\bfetch\(|new WebSocket|localStorage\.setItem\([^)]*stream|indexedDB/);
    }
  });
});

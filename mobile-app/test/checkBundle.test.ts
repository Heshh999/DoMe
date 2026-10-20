/**
 * The build's key-material check (scripts/check-bundle.ts, docs/design/assistant.md §6 T5): any file of the bundle, at
 * any depth, holding "sk-ant" is reported, a clean bundle passes, and `pnpm build` runs the check after `vite build`
 * (so CI's "mobile-app - build" step and the deploy image fail on key material).
 */
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { filesWithKeyMaterial, filesWithPreview, KEY_MARKER, PREVIEW_MARKER } from "../scripts/check-bundle.ts";
import { PREVIEW_FLAG } from "../src/dev/preview.ts";

let dir: string | null = null;

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = null;
});

function bundle(files: Record<string, string>): string {
  dir = mkdtempSync(join(tmpdir(), "dome-bundle-"));
  for (const [path, content] of Object.entries(files)) {
    mkdirSync(join(dir, path, ".."), { recursive: true });
    writeFileSync(join(dir, path), content);
  }
  return dir;
}

describe("check-bundle", () => {
  it("a clean bundle has no key material", () => {
    expect(filesWithKeyMaterial(bundle({ "index.html": "<!doctype html>", "assets/index-abc.js": "const a = 'skip-ant';", "sw.js": "precache()" }))).toEqual([]);
  });

  it("reports every file holding key material, at any depth", () => {
    const key = `${KEY_MARKER}-api03-${"x".repeat(20)}`;
    expect(filesWithKeyMaterial(bundle({ "index.html": "ok", "assets/deep/chunk.js": `fetch(u,{headers:{"x-api-key":"${key}"}})`, "assets/map.json": `{"k":"${key}"}` }))).toEqual(["assets/deep/chunk.js", "assets/map.json"]);
  });

  it("reports a bundle that carries the development-only design preview", () => {
    expect(filesWithPreview(bundle({ "index.html": "ok", "assets/index-abc.js": "const a = 'preview';" }))).toEqual([]);
    rmSync(dir!, { recursive: true, force: true });
    expect(filesWithPreview(bundle({ "assets/preview-xyz.js": `sessionStorage.getItem("${PREVIEW_MARKER}")` }))).toEqual(["assets/preview-xyz.js"]);
  });

  it("the preview carries the marker and is loaded only behind import.meta.env.DEV, never imported statically", () => {
    expect(PREVIEW_FLAG).toBe(PREVIEW_MARKER);
    const src = resolve(__dirname, "../src");
    const main = readFileSync(resolve(src, "main.tsx"), "utf8");
    expect(main).toMatch(/if \(import\.meta\.env\.DEV\) await \(await import\("\.\/dev\/preview\.ts"\)\)\.startPreviewIfAsked\(\);/);
    const offenders: string[] = [];
    const walk = (folder: string) => {
      for (const entry of readdirSync(folder, { withFileTypes: true })) {
        const path = join(folder, entry.name);
        if (entry.isDirectory()) walk(path);
        else if (/\.(ts|tsx)$/.test(entry.name) && /from ["'][./]*(?:\.\.\/)*dev\//.test(readFileSync(path, "utf8"))) offenders.push(relative(src, path));
      }
    };
    walk(src);
    expect(offenders).toEqual([]);
  });

  it("pnpm build runs it after vite build, and the signed-release check (scripts/check-release.ts) before it", () => {
    const pkg = JSON.parse(readFileSync(resolve(__dirname, "../package.json"), "utf8")) as { scripts: Record<string, string> };
    expect(pkg.scripts.build).toBe("tsx scripts/check-release.ts && vite build && tsx scripts/check-bundle.ts");
  });
});

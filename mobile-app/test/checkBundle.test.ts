/**
 * The build's key-material check (scripts/check-bundle.ts, docs/design/assistant.md §6 T5): any file of the bundle, at
 * any depth, holding "sk-ant" is reported, a clean bundle passes, and `pnpm build` runs the check after `vite build`
 * (so CI's "mobile-app - build" step and the deploy image fail on key material).
 */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { filesWithKeyMaterial, KEY_MARKER } from "../scripts/check-bundle.ts";

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

  it("pnpm build runs it after vite build", () => {
    const pkg = JSON.parse(readFileSync(resolve(__dirname, "../package.json"), "utf8")) as { scripts: Record<string, string> };
    expect(pkg.scripts.build).toBe("vite build && tsx scripts/check-bundle.ts");
  });
});

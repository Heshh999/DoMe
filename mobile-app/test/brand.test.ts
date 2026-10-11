/**
 * The app draws the logo inline from src/components/brandPaths.ts (the deploy image builds without brand/). Those paths
 * must stay the brand's own: every one equals the path of the same id in brand/wordmark.svg, brand/mark.svg and
 * brand/icon.svg, so a change to the logo cannot leave the app drawing an old one.
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { BRAND_PATHS } from "../src/components/brandPaths.ts";

const brand = resolve(__dirname, "../../brand");

function pathData(file: string, id: string): string | null {
  const svg = readFileSync(resolve(brand, file), "utf8");
  const tag = svg.match(new RegExp(`<path[^>]*\\bid="${id}"[^>]*>`))?.[0];
  return tag?.match(/\sd="([^"]+)"/)?.[1] ?? null;
}

describe.skipIf(!existsSync(brand))("brand paths", () => {
  it.each([
    ["wordmark.svg", "dome-wordmark", BRAND_PATHS.wordmark],
    ["mark.svg", "dome-mark", BRAND_PATHS.mark],
    ["icon.svg", "dome-icon", BRAND_PATHS.icon],
  ] as const)("%s", (file, prefix, paths) => {
    for (const [part, d] of Object.entries(paths)) expect(d, `${prefix}-${part}`).toBe(pathData(file, `${prefix}-${part}`));
  });

  it("the compact and dark lockups draw the same letters and glyph", () => {
    for (const part of ["d", "m", "e", "ring", "tip", "cursor"]) {
      expect(pathData("wordmark-compact.svg", `dome-wordmark-compact-${part}`)).toBe(pathData("wordmark.svg", `dome-wordmark-${part}`));
      expect(pathData("wordmark-dark.svg", `dome-wordmark-dark-${part}`)).toBe(pathData("wordmark.svg", `dome-wordmark-${part}`));
    }
  });
});

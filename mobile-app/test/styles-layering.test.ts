/**
 * In Tailwind 4 an unlayered rule beats every utility, whatever its specificity. Element defaults for
 * controls therefore live in `@layer base`; an unlayered `font: inherit` on buttons and links once
 * silently dropped every text-* / font-* utility on them (the bottom tab labels overflowed on phones).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../src/styles.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** The stylesheet with every `@layer … { … }` block removed (brace-matched). */
function unlayered(source: string): string {
  let out = "";
  let i = 0;
  while (i < source.length) {
    const at = source.indexOf("@layer", i);
    if (at === -1) return out + source.slice(i);
    out += source.slice(i, at);
    const open = source.indexOf("{", at);
    const semi = source.indexOf(";", at);
    if (open === -1 || (semi !== -1 && semi < open)) {
      i = semi + 1; // `@layer a, b;` declaration, no block
      continue;
    }
    let depth = 0;
    let j = open;
    for (; j < source.length; j++) {
      if (source[j] === "{") depth++;
      else if (source[j] === "}" && --depth === 0) break;
    }
    i = j + 1;
  }
  return out;
}

describe("styles.css layering", () => {
  it("keeps control element defaults in the base layer so utilities can override them", () => {
    const outside = unlayered(css);
    expect(outside).not.toMatch(/font:\s*inherit/);
    expect(outside).not.toMatch(/min-height:\s*44px/);
    expect(css).toMatch(/@layer base\s*\{[\s\S]*font:\s*inherit[\s\S]*\}/);
  });

  it("styles no bare control element outside a layer", () => {
    const selectors = [...unlayered(css).matchAll(/([^{}]+)\{/g)].map((m) => m[1]!.trim());
    const bare = selectors.filter((sel) => !sel.startsWith("@") && sel.split(",").some((s) => /^(button|a|input|select|textarea)$/.test(s.trim())));
    expect(bare).toEqual([]);
  });
});

/**
 * The translucent bars (tab bar, Ask dock) must never let page text read through their labels: the fill is
 * nearly opaque in both themes, and where the browser has no backdrop blur the bar falls back to a solid
 * raised fill. Icon wells are tinted (solid DoMe Blue stays reserved for the one primary control).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../src/styles.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function opacityOf(fill: string): number {
  const m = /\/\s*([0-9.]+)\s*\)/.exec(fill);
  return m ? Number(m[1]) : 1;
}

describe("styles.css bars", () => {
  it("fills the bars at 0.9 or more in light and in both dark blocks", () => {
    const fills = [...css.matchAll(/--dome-bar-fill:\s*([^;]+);/g)].map((m) => m[1]!.trim());
    expect(fills).toHaveLength(3); // light + prefers-dark + chosen dark
    for (const fill of fills) expect(opacityOf(fill), fill).toBeGreaterThanOrEqual(0.9);
    expect(fills[0]).toMatch(/255 255 255/);
    expect(fills[1]).toMatch(/0 14 50/);
    expect(fills[2]).toBe(fills[1]);
  });

  it("falls back to the solid raised surface where backdrop-filter is not supported", () => {
    const block = /@supports not \(backdrop-filter: blur\(1px\)\)\s*\{\s*\.dome-bar\s*\{([^}]*)\}\s*\}/.exec(css);
    expect(block).not.toBeNull();
    expect(block![1]).toMatch(/background:\s*var\(--dome-color-bg-raised\)/);
  });

  it("defines a tinted icon well in both themes, never a gradient", () => {
    expect(css).toMatch(/\.dome-well\s*\{[^}]*background:\s*var\(--dome-well\)/);
    const wells = [...css.matchAll(/--dome-well:\s*([^;]+);/g)].map((m) => m[1]!.trim());
    expect(wells).toHaveLength(3);
    for (const w of wells) {
      expect(w).not.toMatch(/gradient/);
      expect(opacityOf(w)).toBeLessThanOrEqual(0.2);
    }
  });
});

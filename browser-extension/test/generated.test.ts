/** The committed validators must match what the frozen schemas generate today (contract drift guard). */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { DTS_SOURCE, generateValidatorsSource, OUTPUT_DTS, OUTPUT_JS } from "../scripts/gen-validators.ts";

describe("src/generated/validators.js", () => {
  it("is up to date with shared/protocol/schemas (run `pnpm gen:validators` if this fails)", () => {
    expect(readFileSync(OUTPUT_JS, "utf8")).toBe(generateValidatorsSource());
    expect(readFileSync(OUTPUT_DTS, "utf8")).toBe(DTS_SOURCE);
  });

  it("contains no runtime code generation", () => {
    const code = readFileSync(OUTPUT_JS, "utf8");
    expect(code).not.toMatch(/new Function|\beval\(|require\(/);
  });
});

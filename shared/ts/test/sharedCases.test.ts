import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ProtocolError, loadsStrict, parseRfc3339 } from "../src/index.ts";

const cases = JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../../protocol/fixtures/strict-json-cases.json"), "utf8")) as {
  parser_cases: Array<{ name: string; text: string; expect: string }>;
  pattern_cases: Array<{ pattern: string; value: string; expect: boolean }>;
  timestamp_cases: Array<{ value: string; expect: string }>;
};

const codeOf = (fn: () => unknown): string => {
  try {
    fn();
    return "accept";
  } catch (e) {
    if (e instanceof ProtocolError) return e.code;
    throw e;
  }
};

describe("shared strict-json cases", () => {
  for (const c of cases.parser_cases) {
    it(`parser: ${c.name}`, () => {
      expect(codeOf(() => loadsStrict(c.text))).toBe(c.expect);
    });
  }
  for (const c of cases.pattern_cases) {
    it(`pattern ${c.pattern} vs ${JSON.stringify(c.value)}`, () => {
      // Ajv uses the ECMA-262 `u` flag semantics; plain RegExp here mirrors what Ajv compiles.
      expect(new RegExp(c.pattern, "u").test(c.value)).toBe(c.expect);
    });
  }
  for (const c of cases.timestamp_cases) {
    it(`timestamp ${JSON.stringify(c.value)}`, () => {
      expect(codeOf(() => parseRfc3339(c.value))).toBe(c.expect);
    });
  }
});

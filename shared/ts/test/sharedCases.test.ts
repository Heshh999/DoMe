import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ProtocolError, loadsStrict, negotiateVersion, parseRfc3339, screenSdpAcceptable, utf8ByteLength } from "../src/index.ts";

const cases = JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../../protocol/fixtures/strict-json-cases.json"), "utf8")) as {
  parser_cases: Array<{ name: string; text: string; expect: string }>;
  pattern_cases: Array<{ pattern: string; value: string; expect: boolean }>;
  timestamp_cases: Array<{ value: string; expect: string }>;
  negotiation_cases: Array<{ peer: string[]; supported: string[]; expect: string | null }>;
  sdp_cases: Array<{ name: string; value: string; pad_to_bytes?: number; expect: boolean }>;
};

/** One extra attribute line so the text is exactly pad_to_bytes bytes (the case file stays readable). */
function padded(c: { value: string; pad_to_bytes?: number }): string {
  if (c.pad_to_bytes === undefined) return c.value;
  const value = c.value + "a=" + "x".repeat(c.pad_to_bytes - utf8ByteLength(c.value) - 4) + "\r\n";
  if (utf8ByteLength(value) !== c.pad_to_bytes) throw new Error("bad padding");
  return value;
}

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
  for (const c of cases.negotiation_cases) {
    it(`negotiation ${JSON.stringify(c.peer)} vs ${JSON.stringify(c.supported)}`, () => {
      expect(negotiateVersion(c.peer, c.supported)).toBe(c.expect);
    });
  }
  for (const c of cases.sdp_cases) {
    it(`sdp: ${c.name}`, () => {
      // rules.screen_signaling (4): both languages accept and refuse the same SDP shapes (protocol 1.4)
      expect(screenSdpAcceptable(padded(c))).toBe(c.expect);
    });
  }
});

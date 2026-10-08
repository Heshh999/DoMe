import { describe, expect, it } from "vitest";

import { ProtocolError, dumpsCompact, loadsStrict } from "../src/index.ts";

const code = (fn: () => unknown): string => {
  try {
    fn();
  } catch (e) {
    if (e instanceof ProtocolError) return e.code;
    throw e;
  }
  return "OK";
};

describe("loadsStrict", () => {
  it("round-trips preserving key order and unicode", () => {
    const text = '{"b":1,"a":[1,2,{"z":"ü→😀"}]}';
    expect(dumpsCompact(loadsStrict(text))).toBe(text);
  });
  it("rejects duplicate keys at any level", () => {
    expect(code(() => loadsStrict('{"a":1,"a":2}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":{"b":1,"b":2}}'))).toBe("MALFORMED_MESSAGE");
  });
  it("rejects NaN/Infinity literals and overflow", () => {
    expect(code(() => loadsStrict('{"a":NaN}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":Infinity}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":1e999}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":9007199254740993}'))).toBe("MALFORMED_MESSAGE");
  });
  it("enforces depth", () => {
    expect(code(() => loadsStrict('{"a":'.repeat(9) + "1" + "}".repeat(9)))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":'.repeat(7) + "1" + "}".repeat(7)))).toBe("OK");
    expect(code(() => loadsStrict("[".repeat(100000) + "]".repeat(100000), { maxBytes: 10_000_000, requireObject: false }))).toBe("MALFORMED_MESSAGE");
  });
  it("enforces size", () => {
    expect(code(() => loadsStrict('{"a":"' + "x".repeat(20000) + '"}'))).toBe("PAYLOAD_TOO_LARGE");
    expect(code(() => loadsStrict('{"a":"' + "😀".repeat(5000) + '"}'))).toBe("PAYLOAD_TOO_LARGE"); // 20k bytes, 10k code units
  });
  it("requires object top level by default", () => {
    expect(code(() => loadsStrict("[1,2]"))).toBe("MALFORMED_MESSAGE");
    expect(loadsStrict("[1,2]", { requireObject: false })).toEqual([1, 2]);
  });
  it("rejects lone surrogates, control chars, bad escapes, trailing data", () => {
    expect(code(() => loadsStrict('{"a":"\\ud800"}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":"\\ud83d\\ude00"}'))).toBe("OK");
    expect(code(() => loadsStrict('{"a":"x\u0001"}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":"\\x"}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":1} x'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict('{"a":1,}'))).toBe("MALFORMED_MESSAGE");
    expect(code(() => loadsStrict("{not json}"))).toBe("MALFORMED_MESSAGE");
  });
  it("refuses __proto__ keys and does not pollute prototypes", () => {
    expect(code(() => loadsStrict('{"__proto__":{"polluted":1}}'))).toBe("MALFORMED_MESSAGE");
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
  it("dumpsCompact refuses non-finite numbers", () => {
    expect(() => dumpsCompact({ a: Number.NaN })).toThrow();
  });
});

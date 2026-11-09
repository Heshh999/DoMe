import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { crc32, inflateSync } from "node:zlib";

import { describe, expect, it } from "vitest";

import { ProtocolError, loadsStrict, negotiateVersion, parseRfc3339, parseScreenDcMessage, screenSdpAcceptable, utf8ByteLength } from "../src/index.ts";

const fixtureDir = resolve(dirname(fileURLToPath(import.meta.url)), "../../protocol/fixtures");
const dcCases = JSON.parse(readFileSync(resolve(fixtureDir, "screen-dc-cases.json"), "utf8")) as {
  accepted: Array<{ name: string; direction: "phone_to_pc" | "pc_to_phone"; text: string }>;
  rejected: Array<{ name: string; direction: "phone_to_pc" | "pc_to_phone"; text: string }>;
};
const cases = JSON.parse(readFileSync(resolve(fixtureDir, "strict-json-cases.json"), "utf8")) as {
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

const parseDc = (text: string, direction: "phone_to_pc" | "pc_to_phone"): unknown => (direction === "pc_to_phone" ? parseScreenDcMessage(text, "pc_to_phone") : parseScreenDcMessage(text, "phone_to_pc"));

/** PNG chunks with their CRC checked: [type, body]. */
function pngChunks(data: Buffer): Array<[string, Buffer]> {
  expect([...data.subarray(0, 8)]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const out: Array<[string, Buffer]> = [];
  let pos = 8;
  while (pos < data.length) {
    const length = data.readUInt32BE(pos);
    const kind = data.subarray(pos + 4, pos + 8).toString("latin1");
    expect(data.readUInt32BE(pos + 8 + length), kind).toBe(crc32(data.subarray(pos + 4, pos + 8 + length)) >>> 0);
    out.push([kind, data.subarray(pos + 8, pos + 8 + length)]);
    pos += 12 + length;
  }
  return out;
}

describe("shared screen data-channel cases (1.5)", () => {
  for (const c of dcCases.accepted) {
    it(`accepts: ${c.name}`, () => {
      expect(parseDc(c.text, c.direction)).toEqual(JSON.parse(c.text));
    });
  }
  for (const c of dcCases.rejected) {
    it(`refuses: ${c.name}`, () => {
      // rules.screen_content: the receiver drops it; every refusal is MALFORMED_MESSAGE in both languages
      expect(codeOf(() => parseDc(c.text, c.direction))).toBe("MALFORMED_MESSAGE");
    });
  }
  it("the custom cursor_shape cases carry real RGBA PNGs of exactly width x height", () => {
    const customs = dcCases.accepted.map((c) => JSON.parse(c.text) as { kind?: string; width: number; height: number; png: string }).filter((m) => m.kind === "custom");
    expect(customs).toHaveLength(2);
    for (const msg of customs) {
      const chunks = pngChunks(Buffer.from(msg.png, "base64"));
      expect(Buffer.from(msg.png, "base64").toString("base64")).toBe(msg.png); // canonical standard base64
      const ihdr = chunks[0]!;
      expect([ihdr[0], ihdr[1].readUInt32BE(0), ihdr[1].readUInt32BE(4), ihdr[1][8], ihdr[1][9]]).toEqual(["IHDR", msg.width, msg.height, 8, 6]);
      expect(chunks[chunks.length - 1]![0]).toBe("IEND");
      expect([...inflateSync(Buffer.concat(chunks.filter(([k]) => k === "IDAT").map(([, b]) => b)))]).toEqual([0, 0, 0, 0, 255]);
    }
    expect(customs[1]!.png).toHaveLength(8192);
    expect(pngChunks(Buffer.from(customs[1]!.png, "base64")).some(([k]) => k === "tEXt")).toBe(true);
  });
});

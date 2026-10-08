/**
 * Strict JSON parser — the only parser allowed for anything that crossed a trust boundary.
 *
 * Rejects oversized text, nesting deeper than `maxDepth`, duplicate object keys, non-finite
 * numbers, integers outside the safe range, prototype-pollution key names, lone surrogates, raw
 * control characters, and non-object top-level values when `requireObject` is set. The Python
 * implementation makes exactly the same decisions; `shared/protocol/fixtures/strict-json-cases.json`
 * is run by both.
 */
import { ProtocolError } from "./errors.ts";

export const DEFAULT_MAX_BYTES = 16384;
export const DEFAULT_MAX_DEPTH = 8;
const FORBIDDEN_KEYS = new Set(["__proto__", "constructor", "prototype"]);

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export interface StrictJsonOptions {
  maxBytes?: number;
  maxDepth?: number;
  requireObject?: boolean;
}

const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
const encoder = new TextEncoder();

export function utf8ByteLength(text: string): number {
  return encoder.encode(text).length;
}

class Parser {
  private pos = 0;
  constructor(
    private readonly text: string,
    private readonly maxDepth: number,
  ) {}

  parse(): JsonValue {
    this.skipWs();
    if (this.pos >= this.text.length) this.fail("empty input");
    const value = this.value(0);
    this.skipWs();
    if (this.pos !== this.text.length) this.fail("trailing characters");
    return value;
  }

  private fail(reason: string): never {
    throw new ProtocolError("MALFORMED_MESSAGE", `invalid JSON: ${reason} at position ${this.pos}`);
  }

  private skipWs(): void {
    while (this.pos < this.text.length) {
      const c = this.text.charCodeAt(this.pos);
      if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) this.pos++;
      else break;
    }
  }

  private value(depth: number): JsonValue {
    if (depth > this.maxDepth) throw new ProtocolError("MALFORMED_MESSAGE", "JSON nesting too deep");
    const c = this.text[this.pos];
    switch (c) {
      case "{":
        return this.object(depth);
      case "[":
        return this.array(depth);
      case '"':
        return this.string();
      case "t":
        return this.literal("true", true);
      case "f":
        return this.literal("false", false);
      case "n":
        return this.literal("null", null);
      default:
        if (c === "-" || (c !== undefined && c >= "0" && c <= "9")) return this.number();
        return this.fail("unexpected character");
    }
  }

  private literal<T extends JsonValue>(word: string, value: T): T {
    if (this.text.startsWith(word, this.pos)) {
      this.pos += word.length;
      return value;
    }
    return this.fail("unexpected literal");
  }

  private number(): number {
    const m = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(this.text.slice(this.pos, this.pos + 400));
    if (!m) this.fail("invalid number");
    const literal = m[0];
    this.pos += literal.length;
    const n = Number(literal);
    if (!Number.isFinite(n)) throw new ProtocolError("MALFORMED_MESSAGE", "non-finite number");
    const isIntegerLiteral = !literal.includes(".") && !/[eE]/.test(literal);
    if (isIntegerLiteral && !Number.isSafeInteger(n)) throw new ProtocolError("MALFORMED_MESSAGE", "integer outside safe range");
    return n;
  }

  private string(): string {
    this.pos++; // opening quote
    let out = "";
    let start = this.pos;
    for (;;) {
      if (this.pos >= this.text.length) this.fail("unterminated string");
      const code = this.text.charCodeAt(this.pos);
      if (code === 0x22) {
        out += this.text.slice(start, this.pos);
        this.pos++;
        break;
      }
      if (code === 0x5c) {
        out += this.text.slice(start, this.pos);
        this.pos++;
        const esc = this.text[this.pos];
        switch (esc) {
          case '"': out += '"'; break;
          case "\\": out += "\\"; break;
          case "/": out += "/"; break;
          case "b": out += "\b"; break;
          case "f": out += "\f"; break;
          case "n": out += "\n"; break;
          case "r": out += "\r"; break;
          case "t": out += "\t"; break;
          case "u": {
            const hex = this.text.slice(this.pos + 1, this.pos + 5);
            if (!/^[0-9a-fA-F]{4}$/.test(hex)) this.fail("invalid unicode escape");
            out += String.fromCharCode(parseInt(hex, 16));
            this.pos += 4;
            break;
          }
          default:
            this.fail("invalid escape");
        }
        this.pos++;
        start = this.pos;
        continue;
      }
      if (code < 0x20) this.fail("control character in string");
      this.pos++;
    }
    if (LONE_SURROGATE.test(out)) throw new ProtocolError("MALFORMED_MESSAGE", "invalid string encoding");
    return out;
  }

  private array(depth: number): JsonValue[] {
    this.pos++; // [
    const out: JsonValue[] = [];
    this.skipWs();
    if (this.text[this.pos] === "]") {
      this.pos++;
      return out;
    }
    for (;;) {
      this.skipWs();
      out.push(this.value(depth + 1));
      this.skipWs();
      const c = this.text[this.pos];
      if (c === ",") {
        this.pos++;
        continue;
      }
      if (c === "]") {
        this.pos++;
        return out;
      }
      this.fail("expected , or ]");
    }
  }

  private object(depth: number): { [key: string]: JsonValue } {
    this.pos++; // {
    const out: { [key: string]: JsonValue } = {};
    const seen = new Set<string>();
    this.skipWs();
    if (this.text[this.pos] === "}") {
      this.pos++;
      return out;
    }
    for (;;) {
      this.skipWs();
      if (this.text[this.pos] !== '"') this.fail("expected string key");
      const key = this.string();
      if (seen.has(key)) throw new ProtocolError("MALFORMED_MESSAGE", `duplicate JSON key ${JSON.stringify(key)}`);
      if (FORBIDDEN_KEYS.has(key)) throw new ProtocolError("MALFORMED_MESSAGE", "forbidden key");
      seen.add(key);
      this.skipWs();
      if (this.text[this.pos] !== ":") this.fail("expected :");
      this.pos++;
      this.skipWs();
      const v = this.value(depth + 1);
      Object.defineProperty(out, key, { value: v, enumerable: true, writable: true, configurable: true });
      this.skipWs();
      const c = this.text[this.pos];
      if (c === ",") {
        this.pos++;
        continue;
      }
      if (c === "}") {
        this.pos++;
        return out;
      }
      this.fail("expected , or }");
    }
  }
}

export function loadsStrict(text: string, options: StrictJsonOptions = {}): JsonValue {
  const maxBytes = options.maxBytes ?? DEFAULT_MAX_BYTES;
  const maxDepth = options.maxDepth ?? DEFAULT_MAX_DEPTH;
  const requireObject = options.requireObject ?? true;
  if (typeof text !== "string") throw new ProtocolError("MALFORMED_MESSAGE", "JSON input must be a string");
  // Each UTF-16 unit is at most 3 UTF-8 bytes, so short strings skip the encode.
  if (text.length * 3 > maxBytes && utf8ByteLength(text) > maxBytes) {
    throw new ProtocolError("PAYLOAD_TOO_LARGE", `JSON exceeds ${maxBytes} bytes`);
  }
  const value = new Parser(text, maxDepth).parse();
  if (requireObject && (value === null || typeof value !== "object" || Array.isArray(value))) {
    throw new ProtocolError("MALFORMED_MESSAGE", "JSON top level must be an object");
  }
  return value;
}

/** Compact serialisation; insertion order preserved; the bytes we emit are the bytes we sign. */
export function dumpsCompact(value: unknown): string {
  const text = JSON.stringify(value, (_k, v) => {
    if (typeof v === "number" && !Number.isFinite(v)) throw new TypeError("non-finite number");
    if (typeof v === "bigint") throw new TypeError("bigint not supported");
    return v;
  });
  if (text === undefined) throw new TypeError("value is not JSON-serialisable");
  return text;
}

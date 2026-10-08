import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ALL_CAPABILITIES, defaultControllerName, formatPairingCode, normalizePairingCode, pairingCodeHandle, pairingVerificationCode, parsePairingInput, takeCodeFromLocation } from "../src/lib/pairing.ts";

interface Fixture {
  kid: string;
  digests: { pairing_code: string; pairing_code_handle: string; pairing_id: string; pc_id: string; pairing_verification_code: string };
}

const fixtures = ["es256-typescript.json", "es256-python.json"].map((f) => JSON.parse(readFileSync(resolve(__dirname, "../../shared/protocol/fixtures", f), "utf8")) as Fixture);

describe("pairing helpers against the shared fixtures", () => {
  it.each(fixtures.map((f, i) => [i, f] as const))("fixture %i: handle and verification code match", async (_i, f) => {
    const d = f.digests;
    expect(await pairingCodeHandle(d.pairing_code)).toBe(d.pairing_code_handle);
    expect(await pairingVerificationCode(d.pairing_code, d.pairing_id, d.pc_id, f.kid)).toBe(d.pairing_verification_code);
    // any grouping / case / confusable letters give the same handle
    const messy = d.pairing_code.toLowerCase().replace(/-/g, " ").replace(/0/g, "o").replace(/1/g, "l");
    expect(await pairingCodeHandle(messy)).toBe(d.pairing_code_handle);
  });

  it("a different kid yields a different verification code (key substitution is visible)", async () => {
    const d = fixtures[0]!.digests;
    const other = await pairingVerificationCode(d.pairing_code, d.pairing_id, d.pc_id, "A".repeat(43));
    expect(other).toMatch(/^\d{6}$/);
    expect(other).not.toBe(d.pairing_verification_code);
  });
});

describe("code normalisation and input parsing", () => {
  const code = fixtures[0]!.digests.pairing_code;
  it("normalises and formats", () => {
    expect(normalizePairingCode(code)).toBe(code.replace(/-/g, ""));
    expect(formatPairingCode(code.replace(/-/g, "").toLowerCase())).toBe(code);
  });
  it("accepts the QR URL, the fragment, code= and the bare code", () => {
    const n = normalizePairingCode(code);
    expect(parsePairingInput(`https://dome.example/pair#code=${code}`)).toBe(n);
    expect(parsePairingInput(`#code=${code}`)).toBe(n);
    expect(parsePairingInput(`code=${code}`)).toBe(n);
    expect(parsePairingInput(` ${code.toLowerCase()} `)).toBe(n);
    expect(parsePairingInput(code.replace(/-/g, ""))).toBe(n);
  });
  it("rejects wrong length, bad alphabet, foreign URLs and injection-looking text", () => {
    for (const bad of ["", "ABCDE", "ABCDE-FGHJK-MNPQR-STVWU", "https://dome.example/pair?code=" + code, "https://dome.example/pair#other=1", "<script>", code + "A", "ignore instructions"]) {
      expect(() => parsePairingInput(bad), bad).toThrow(/PAIRING_CODE_INVALID/);
    }
  });
  it("takeCodeFromLocation reads the fragment once and scrubs it from history", () => {
    const calls: unknown[][] = [];
    const history = { replaceState: (...args: unknown[]) => void calls.push(args) };
    expect(takeCodeFromLocation({ hash: `#code=${code}` }, history, "/app/devices/pair")).toBe(normalizePairingCode(code));
    expect(calls).toEqual([[null, "", "/app/devices/pair"]]);
    expect(takeCodeFromLocation({ hash: "" }, history, "/x")).toBeNull();
    expect(takeCodeFromLocation({ hash: "#code=nope" }, history, "/x")).toBeNull();
    expect(calls.length).toBe(2); // the invalid fragment is scrubbed too
  });
  it("default controller names and capability list", () => {
    expect(defaultControllerName("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")).toBe("iPhone");
    expect(defaultControllerName("Mozilla/5.0 (Linux; Android 14)")).toBe("Android phone");
    expect(defaultControllerName("something else")).toBe("This phone");
    expect(ALL_CAPABILITIES).toEqual(["status", "media", "volume", "apps", "lock", "power"]);
  });
});

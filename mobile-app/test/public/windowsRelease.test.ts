/**
 * The Windows release content (src/content/windowsRelease.ts): the same rules as the PC app's update check,
 * and the integrity of what is committed. A published windows-release.json must come with
 * windows-release.sig, an Ed25519 signature over its exact bytes by a key pinned in
 * WINDOWS_RELEASE_PUBLIC_KEYS; the placeholder must come without one. Keys in these tests are generated in
 * memory: no key material exists in the repository. The same check runs at build time (scripts/check-release.ts,
 * first step of `pnpm build`), so a red CI run cannot be deployed with an unsigned manifest either.
 */
import { generateKeyPairSync, sign } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { releaseProblem, signedByPinnedKey } from "../../scripts/check-release.ts";
import { expectedUrl, formatDate, formatSize, getWindowsRelease, REPO, WINDOWS_RELEASE_PUBLIC_KEYS } from "../../src/content/windowsRelease.ts";

const contentDir = resolve(__dirname, "../../src/content");
const JSON_PATH = resolve(contentDir, "windows-release.json");
const SIG_PATH = resolve(contentDir, "windows-release.sig");
const VALID = {
  browser_addon: false,
  product: "DoMe for Windows",
  published_at: "2026-11-02T14:03:11Z",
  publisher: "Jane Example",
  release_page: "https://github.com/Heshh999/DoMe/releases/tag/v0.2.0",
  schema: 1,
  sha256: "ab".repeat(32),
  size: 41_234_567,
  url: "https://github.com/Heshh999/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe",
  version: "0.2.0",
};

describe("getWindowsRelease", () => {
  it("reads a valid release", () => {
    expect(getWindowsRelease(VALID)).toEqual({
      version: "0.2.0",
      url: VALID.url,
      sha256: VALID.sha256,
      size: 41_234_567,
      publishedAt: "2026-11-02T14:03:11Z",
      releasePage: VALID.release_page,
      publisher: "Jane Example",
      browserAddon: false,
    });
    expect(expectedUrl("1.2.3")).toBe(`https://github.com/${REPO}/releases/download/v1.2.3/DoMe-Setup-1.2.3.exe`);
  });

  it.each([
    ["the placeholder", { status: "not-published" }],
    ["null", null],
    ["an array", [VALID]],
    ["a download address off GitHub", { ...VALID, url: "https://evil.example/DoMe-Setup-0.2.0.exe" }],
    ["plain http", { ...VALID, url: VALID.url.replace("https:", "http:") }],
    ["another file name", { ...VALID, url: VALID.url.replace("DoMe-Setup-0.2.0.exe", "Other.exe") }],
    ["a URL for another version", { ...VALID, version: "0.3.0" }],
    ["an uppercase SHA-256", { ...VALID, sha256: "AB".repeat(32) }],
    ["a short SHA-256", { ...VALID, sha256: "ab".repeat(31) }],
    ["schema 2", { ...VALID, schema: 2 }],
    ["another product", { ...VALID, product: "Something" }],
    ["a two-part version", { ...VALID, version: "0.2" }],
    ["size 0", { ...VALID, size: 0 }],
    ["a fractional size", { ...VALID, size: 1.5 }],
    ["a size over the cap", { ...VALID, size: 300_000_001 }],
    ["a size as text", { ...VALID, size: "41234567" }],
    ["an impossible date", { ...VALID, published_at: "2026-02-30T10:00:00Z" }],
    ["a date without Z", { ...VALID, published_at: "2026-11-02T14:03:11" }],
    ["a release page elsewhere", { ...VALID, release_page: "https://evil.example/releases/tag/v0.2.0" }],
    ["an empty publisher", { ...VALID, publisher: " " }],
    ["a publisher with a newline", { ...VALID, publisher: "Jane\nExample" }],
    ["a 201-character publisher", { ...VALID, publisher: "x".repeat(201) }],
    ["browser_addon as text", { ...VALID, browser_addon: "false" }],
    ["an extra field", { ...VALID, extra: true }],
    ["a missing field", (({ publisher: _p, ...rest }) => rest)(VALID)],
  ])("refuses %s", (_label, raw) => {
    expect(getWindowsRelease(raw)).toBeNull();
  });

  it("formats size and date for people", () => {
    expect(formatSize(41_234_567)).toBe("41 MB");
    expect(formatSize(400_000)).toBe("1 MB");
    expect(formatDate("2026-11-02T23:59:59Z")).toBe("2 November 2026");
  });
});

describe("signature check (Node crypto, the same check as the content-integrity test below)", () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("hex");
  const data = Buffer.from(JSON.stringify(VALID, null, 2) + "\n", "utf8");
  const signatureFile = sign(null, data, privateKey).toString("base64") + "\n";

  it("accepts the exact bytes signed by a pinned key", () => {
    expect(signedByPinnedKey(data, signatureFile, [raw])).toBe(true);
  });

  it("refuses one changed byte, another key, no key and a garbled signature", () => {
    const tampered = Buffer.from(data);
    tampered[20] = (tampered[20] ?? 0) ^ 1;
    expect(signedByPinnedKey(tampered, signatureFile, [raw])).toBe(false);
    const other = generateKeyPairSync("ed25519").publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("hex");
    expect(signedByPinnedKey(data, signatureFile, [other])).toBe(false);
    expect(signedByPinnedKey(data, signatureFile, [])).toBe(false);
    expect(signedByPinnedKey(data, "not a signature", [raw])).toBe(false);
  });
});

describe("the committed release content", () => {
  const bytes = readFileSync(JSON_PATH);
  const parsed: unknown = JSON.parse(bytes.toString("utf8"));
  const placeholder = JSON.stringify(parsed) === JSON.stringify({ status: "not-published" });

  it("pins only well-formed keys", () => {
    for (const key of WINDOWS_RELEASE_PUBLIC_KEYS) expect(key).toMatch(/^[0-9a-f]{64}$/);
  });

  it.runIf(placeholder)("is the placeholder, without a signature file, and shows nothing as published", () => {
    expect(existsSync(SIG_PATH)).toBe(false);
    expect(getWindowsRelease()).toBeNull();
  });

  it.runIf(!placeholder)("is a release signed by a pinned key, byte for byte", () => {
    expect(WINDOWS_RELEASE_PUBLIC_KEYS.length).toBeGreaterThan(0);
    expect(existsSync(SIG_PATH)).toBe(true);
    expect(signedByPinnedKey(bytes, readFileSync(SIG_PATH, "utf8"), WINDOWS_RELEASE_PUBLIC_KEYS)).toBe(true);
    expect(getWindowsRelease()).not.toBeNull();
  });
});

describe("the build's release check (scripts/check-release.ts)", () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("hex");
  const manifest = Buffer.from(JSON.stringify(VALID, null, 2) + "\n", "utf8");
  const signature = sign(null, manifest, privateKey).toString("base64") + "\n";
  const folder = (files: Record<string, string | Buffer>) => {
    const dir = mkdtempSync(join(tmpdir(), "dome-release-"));
    for (const [name, content] of Object.entries(files)) writeFileSync(join(dir, name), content);
    return dir;
  };

  it("passes the placeholder without a signature, and fails it with one", () => {
    expect(releaseProblem(folder({ "windows-release.json": '{ "status": "not-published" }\n' }), [raw])).toBeNull();
    expect(releaseProblem(folder({ "windows-release.json": '{ "status": "not-published" }\n', "windows-release.sig": signature }), [raw])).toMatch(/beside the placeholder/);
  });

  it("passes a manifest signed by a pinned key, byte for byte", () => {
    expect(releaseProblem(folder({ "windows-release.json": manifest, "windows-release.sig": signature }), [raw])).toBeNull();
  });

  it("fails an unsigned manifest, a tampered byte, an unpinned key, no pinned key, and a signed manifest the page would refuse", () => {
    expect(releaseProblem(folder({ "windows-release.json": manifest }), [raw])).toMatch(/windows-release.sig is missing/);
    const tampered = Buffer.from(manifest);
    tampered[30] = (tampered[30] ?? 0) ^ 1;
    expect(releaseProblem(folder({ "windows-release.json": tampered, "windows-release.sig": signature }), [raw])).toMatch(/does not verify|not JSON/);
    const other = generateKeyPairSync("ed25519").publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("hex");
    expect(releaseProblem(folder({ "windows-release.json": manifest, "windows-release.sig": signature }), [other])).toMatch(/does not verify/);
    expect(releaseProblem(folder({ "windows-release.json": manifest, "windows-release.sig": signature }), [])).toMatch(/no update key is pinned/);
    const wrongUrl = Buffer.from(JSON.stringify({ ...VALID, url: "https://example.com/DoMe-Setup.exe" }, null, 2) + "\n", "utf8");
    expect(releaseProblem(folder({ "windows-release.json": wrongUrl, "windows-release.sig": sign(null, wrongUrl, privateKey).toString("base64") }), [raw])).toMatch(/breaks the Download page's rules/);
  });

  it("passes the committed content with the pinned keys", () => {
    expect(releaseProblem(resolve(__dirname, "../../src/content"))).toBeNull();
  });
});

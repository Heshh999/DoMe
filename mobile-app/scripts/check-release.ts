/**
 * Fails the build when the Windows release the Download page would offer is not signed (src/content/windowsRelease.ts).
 * `pnpm build` runs this before `vite build`, so the deploy image's build refuses it as well, whatever CI said: the
 * download button can never light up for a hand-typed, stale or tampered `windows-release.json`.
 *
 * Passes only for
 *   - exactly the placeholder `{ "status": "not-published" }` with no `windows-release.sig`, or
 *   - a manifest whose `windows-release.sig` (base64 Ed25519, as release.yml writes `latest.json.sig`) verifies over the
 *     file's exact bytes with a key in WINDOWS_RELEASE_PUBLIC_KEYS, and that passes the page's own rules.
 *
 *   tsx scripts/check-release.ts [content dir]      (default: src/content)
 */
import { createPublicKey, verify } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { getWindowsRelease, WINDOWS_RELEASE_PUBLIC_KEYS } from "../src/content/windowsRelease.ts";

export const MANIFEST_FILE = "windows-release.json";
export const SIGNATURE_FILE = "windows-release.sig";
// DER prefix of an Ed25519 SubjectPublicKeyInfo; the 32 raw key bytes follow it.
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

/** Does `signatureFile` (base64 + newline) sign exactly `data` with one of the raw hex `keys`? */
export function signedByPinnedKey(data: Buffer, signatureFile: string, keys: readonly string[]): boolean {
  const signature = Buffer.from(signatureFile.trim(), "base64");
  if (signature.length !== 64) return false;
  return keys.some((hex) => {
    if (!/^[0-9a-f]{64}$/.test(hex)) return false;
    const key = createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, Buffer.from(hex, "hex")]), format: "der", type: "spki" });
    return verify(null, data, key, signature);
  });
}

/** Why the release content in `dir` must not be built, or `null` when it may. */
export function releaseProblem(dir: string, keys: readonly string[] = WINDOWS_RELEASE_PUBLIC_KEYS): string | null {
  const bytes = readFileSync(join(dir, MANIFEST_FILE));
  const sigPath = join(dir, SIGNATURE_FILE);
  let parsed: unknown;
  try {
    parsed = JSON.parse(bytes.toString("utf8"));
  } catch {
    return `${MANIFEST_FILE} is not JSON`;
  }
  if (JSON.stringify(parsed) === JSON.stringify({ status: "not-published" })) {
    return existsSync(sigPath) ? `${SIGNATURE_FILE} exists beside the placeholder; remove it` : null;
  }
  if (!existsSync(sigPath)) return `${MANIFEST_FILE} is not the placeholder but ${SIGNATURE_FILE} is missing`;
  if (keys.length === 0) return "no update key is pinned in WINDOWS_RELEASE_PUBLIC_KEYS";
  if (!signedByPinnedKey(bytes, readFileSync(sigPath, "utf8"), keys)) return `${SIGNATURE_FILE} does not verify over ${MANIFEST_FILE}'s exact bytes with a pinned key`;
  if (getWindowsRelease(parsed) === null) return `${MANIFEST_FILE} is signed but breaks the Download page's rules`;
  return null;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const dir = resolve(dirname(fileURLToPath(import.meta.url)), "..", process.argv[2] ?? "src/content");
  const problem = releaseProblem(dir);
  if (problem) {
    console.error(`check-release: ${problem}. The Download page offers only a release signed by the update key (docs/WINDOWS_INSTALL.md).`);
    process.exit(1);
  }
  console.info("check-release: the Windows release content is the placeholder or a release signed by a pinned key.");
}

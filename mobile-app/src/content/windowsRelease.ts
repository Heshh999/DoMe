/**
 * The Windows release the Download page offers: the signed version information of the newest published
 * release (`latest.json` from the GitHub release made by .github/workflows/release.yml), copied verbatim
 * into `windows-release.json`, with its detached Ed25519 signature `latest.json.sig` copied verbatim into
 * `windows-release.sig`. The site cannot ask GitHub itself (the server allows the page to connect only
 * to its own origin), so the page shows exactly what is committed at the deployed commit.
 *
 * Honesty: until a signed release exists the file is the placeholder `{ "status": "not-published" }` and
 * the page keeps its disabled "not yet published" state. The button lights up only for a manifest that
 * passes the same rules as the PC app's update check (pc-agent/dome_agent/update_check.py): an X.Y.Z
 * version, a 64-hex SHA-256, the size, a publisher, and exactly DoMe's GitHub download address. The
 * signature is checked against `WINDOWS_RELEASE_PUBLIC_KEYS` (the same keys the PC app pins; a pc-agent
 * test compares both lists) by scripts/check-release.ts, the first step of `pnpm build`, and by
 * test/public/windowsRelease.test.ts: a hand-typed, stale or tampered file fails the build itself (CI and
 * the deploy image alike), so it can never light the button, even if a red CI run is deployed.
 */
import releaseJson from "./windows-release.json";

export const REPO = "Heshh999/DoMe";

/** Raw Ed25519 public keys, 64 hex characters each; empty until the founder's update key exists. */
export const WINDOWS_RELEASE_PUBLIC_KEYS: readonly string[] = [];

const MAX_INSTALLER_BYTES = 300_000_000;
const VERSION_RE = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const TIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const RELEASE_PAGE_RE = new RegExp(`^https://github\\.com/${REPO.replace("/", "\\/")}/releases/[A-Za-z0-9._/-]{1,100}$`);
const MANIFEST_KEYS = ["browser_addon", "product", "published_at", "publisher", "release_page", "schema", "sha256", "size", "url", "version"];

export interface WindowsRelease {
  version: string;
  /** `https://github.com/Heshh999/DoMe/releases/download/vX.Y.Z/DoMe-Setup-X.Y.Z.exe`, nothing else. */
  url: string;
  sha256: string;
  size: number;
  /** ISO-8601 UTC, when the release build wrote the file (the GitHub release appears minutes later). */
  publishedAt: string;
  releasePage: string;
  /** The name in the code-signing certificate: what Windows shows as the publisher. */
  publisher: string;
  /** False: this version does not include YouTube control in Chrome or Edge. */
  browserAddon: boolean;
}

/** The installer's only accepted download address for `version`. */
export function expectedUrl(version: string): string {
  return `https://github.com/${REPO}/releases/download/v${version}/DoMe-Setup-${version}.exe`;
}

function validDate(iso: string): boolean {
  if (!TIME_RE.test(iso)) return false;
  const d = new Date(iso);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 19) === iso.slice(0, 19);
}

function validPublisher(name: unknown): name is string {
  if (typeof name !== "string" || name.trim() === "" || name.length > 200) return false;
  for (const ch of name) {
    const code = ch.charCodeAt(0);
    if (code < 32 || code === 127) return false;
  }
  return true;
}

/** The published release, or `null` for the placeholder or anything that breaks a rule. */
export function getWindowsRelease(raw: unknown = releaseJson): WindowsRelease | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const m = raw as Record<string, unknown>;
  const keys = Object.keys(m).sort();
  if (keys.length !== MANIFEST_KEYS.length || keys.some((k, i) => k !== MANIFEST_KEYS[i])) return null;
  const { version, sha256, url, size, published_at: publishedAt, release_page: releasePage, publisher, browser_addon: browserAddon } = m;
  if (m.schema !== 1 || m.product !== "DoMe for Windows") return null;
  if (typeof version !== "string" || !VERSION_RE.test(version)) return null;
  if (typeof sha256 !== "string" || !SHA256_RE.test(sha256)) return null;
  if (url !== expectedUrl(version)) return null;
  if (typeof size !== "number" || !Number.isInteger(size) || size <= 0 || size > MAX_INSTALLER_BYTES) return null;
  if (typeof publishedAt !== "string" || !validDate(publishedAt)) return null;
  if (typeof releasePage !== "string" || !RELEASE_PAGE_RE.test(releasePage)) return null;
  if (!validPublisher(publisher)) return null;
  if (typeof browserAddon !== "boolean") return null;
  return { version, url, sha256, size, publishedAt, releasePage, publisher, browserAddon };
}

/** "39 MB" (decimal megabytes, rounded). */
export function formatSize(bytes: number): string {
  return `${Math.max(1, Math.round(bytes / 1_000_000))} MB`;
}

/** "2 November 2026" (UTC). */
export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "long", year: "numeric" }).format(new Date(iso));
}

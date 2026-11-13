/**
 * The Windows release committed in src/content/windows-release.json, read from disk rather than imported, so a test
 * that mocks that module (to render the not-yet-published state too) still checks the real page against the real
 * file. Its signature is checked in test/public/windowsRelease.test.ts; here only the fields the pages show.
 *
 * A test that renders both states mocks the module with `releaseOrPlaceholder`, a pass-through to the real file that
 * switches to the placeholder while `state.placeholder` is set:
 *
 *   const release = vi.hoisted(() => ({ placeholder: false }));
 *   vi.mock("../../src/content/windows-release.json", async (importOriginal) =>
 *     (await import("../helpers/committedRelease.ts")).releaseOrPlaceholder(importOriginal, release));
 *
 * (vi.mock factories are hoisted above the imports, so the helper is imported inside the factory.)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/** What src/content/windows-release.json held before the first release. */
export const PLACEHOLDER_RELEASE = { status: "not-published" } as const;

/** The mocked module: the real committed file, or the placeholder while `state.placeholder` is set. */
export async function releaseOrPlaceholder(importOriginal: <T>() => Promise<T>, state: { placeholder: boolean }): Promise<{ readonly default: unknown }> {
  const actual = await importOriginal<{ default: unknown }>();
  return {
    get default() {
      return state.placeholder ? PLACEHOLDER_RELEASE : actual.default;
    },
  };
}

export interface CommittedRelease {
  version: string;
  url: string;
  sha256: string;
  publisher: string;
  publishedAt: string;
  releasePage: string;
  browserAddon: boolean;
}

export function committedRelease(): CommittedRelease {
  const raw = JSON.parse(readFileSync(resolve(__dirname, "../../src/content/windows-release.json"), "utf8")) as Record<string, unknown>;
  if (typeof raw.version !== "string") throw new Error("src/content/windows-release.json holds no published release");
  return {
    version: raw.version,
    url: String(raw.url),
    sha256: String(raw.sha256),
    publisher: String(raw.publisher),
    publishedAt: String(raw.published_at),
    releasePage: String(raw.release_page),
    browserAddon: raw.browser_addon === true,
  };
}

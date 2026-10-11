/**
 * Fails the build when the PWA bundle contains anything that looks like an Anthropic API key (docs/design/assistant.md
 * §6 T5, §8.3 step 4). The key lives only in cloud-api's settings: the phone never holds, asks for or sends one.
 * `pnpm build` runs this right after `vite build`, so CI's "mobile-app - build" step and the deploy image's build both
 * fail on key material, whoever builds the bundle.
 *
 *   tsx scripts/check-bundle.ts [dir]      (default: dist)
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** The start of every Anthropic API key. */
export const KEY_MARKER = "sk-ant";

/** Files under `dir` (any depth, any type) whose bytes contain KEY_MARKER, as sorted paths relative to `dir`. */
export function filesWithKeyMaterial(dir: string): string[] {
  const found: string[] = [];
  const walk = (folder: string) => {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const path = join(folder, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.isFile() && readFileSync(path).includes(KEY_MARKER)) found.push(relative(dir, path));
    }
  };
  walk(dir);
  return found.sort();
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const dir = resolve(dirname(fileURLToPath(import.meta.url)), "..", process.argv[2] ?? "dist");
  if (!existsSync(dir)) {
    console.error(`check-bundle: ${dir} does not exist; run vite build first.`);
    process.exit(1);
  }
  const files = filesWithKeyMaterial(dir);
  if (files.length > 0) {
    console.error(`check-bundle: key material ("${KEY_MARKER}") in the PWA bundle: ${files.join(", ")}. API keys belong in cloud-api's settings only.`);
    process.exit(1);
  }
  console.info(`check-bundle: no key material in ${relative(process.cwd(), dir) || "."}.`);
}

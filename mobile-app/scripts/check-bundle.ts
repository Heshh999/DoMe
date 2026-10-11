/**
 * Fails the build when the PWA bundle contains anything that looks like an Anthropic API key (docs/design/assistant.md
 * §6 T5, §8.3 step 4). The key lives only in cloud-api's settings: the phone never holds, asks for or sends one.
 * `pnpm build` runs this right after `vite build`, so CI's "mobile-app - build" step and the deploy image's build both
 * fail on key material, whoever builds the bundle.
 *
 * It also fails when the development-only design preview (src/dev/preview.ts: a fake account, PC and butler) made it
 * into the bundle: `main.tsx` loads it only under `import.meta.env.DEV`, which a production build drops.
 *
 *   tsx scripts/check-bundle.ts [dir]      (default: dist)
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** The start of every Anthropic API key. */
export const KEY_MARKER = "sk-ant";
/** The design preview's own name (its sessionStorage key, src/dev/preview.ts PREVIEW_FLAG). */
export const PREVIEW_MARKER = "dome-design-preview";

/** Files under `dir` (any depth, any type) whose bytes contain `marker`, as sorted paths relative to `dir`. */
export function filesContaining(dir: string, marker: string): string[] {
  const found: string[] = [];
  const walk = (folder: string) => {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const path = join(folder, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.isFile() && readFileSync(path).includes(marker)) found.push(relative(dir, path));
    }
  };
  walk(dir);
  return found.sort();
}

/** Files under `dir` (any depth, any type) whose bytes contain KEY_MARKER, as sorted paths relative to `dir`. */
export function filesWithKeyMaterial(dir: string): string[] {
  return filesContaining(dir, KEY_MARKER);
}

/** Files under `dir` that carry the development-only design preview. */
export function filesWithPreview(dir: string): string[] {
  return filesContaining(dir, PREVIEW_MARKER);
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
  const preview = filesWithPreview(dir);
  if (preview.length > 0) {
    console.error(`check-bundle: the development-only design preview ("${PREVIEW_MARKER}") is in the PWA bundle: ${preview.join(", ")}. It must load only under import.meta.env.DEV.`);
    process.exit(1);
  }
  console.info(`check-bundle: no key material and no design preview in ${relative(process.cwd(), dir) || "."}.`);
}

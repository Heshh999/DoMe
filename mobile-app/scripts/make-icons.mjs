// Installs the DoMe brand icon into public/icons for the PWA manifest, the iPhone home screen and the
// favicon. Source of truth: brand/icon.svg (editable SVG; see brand/BRAND.md). The PNG/ICO renders
// come from brand/exports/, which brand/scripts/export.mjs produces from that same SVG and which the
// brand test suite drift-checks against it (brand/exports/manifest.json records source and mode per
// file). Nothing outside mobile-app/ is written. Run: pnpm gen:icons
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const brandDir = resolve(here, "../../brand");
const outDir = resolve(here, "../public/icons");
const iconSvg = resolve(brandDir, "icon.svg");
const manifestPath = resolve(brandDir, "exports/manifest.json");

if (!existsSync(iconSvg)) {
  console.error(`brand icon not found at ${iconSvg}; icons left unchanged`);
  process.exit(2);
}
if (!existsSync(manifestPath)) {
  console.error(`brand exports missing (${manifestPath}); run \`node brand/scripts/export.mjs\` from the repository root first`);
  process.exit(2);
}

const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const bySource = new Map(manifest.files.map((f) => [f.file, f]));

/** [brand export → public/icons name]; every PNG/ICO must be rendered from icon.svg. */
const COPIES = [
  ["pwa/icon-192.png", "icon-192.png"],
  ["pwa/icon-512.png", "icon-512.png"],
  ["pwa/icon-192-maskable.png", "icon-192-maskable.png"],
  ["pwa/icon-512-maskable.png", "icon-512-maskable.png"],
  ["pwa/apple-touch-icon-180.png", "apple-touch-icon.png"],
  ["favicon/favicon-32.png", "favicon-32.png"],
  ["favicon/favicon-16.png", "favicon-16.png"],
  ["favicon/favicon.ico", "favicon.ico"],
];

mkdirSync(outDir, { recursive: true });
let n = 0;
for (const [src, dest] of COPIES) {
  const entry = bySource.get(src);
  if (!entry) throw new Error(`brand export ${src} is not listed in exports/manifest.json`);
  if (entry.source !== "icon.svg") throw new Error(`brand export ${src} was rendered from ${entry.source}, expected icon.svg`);
  const from = resolve(brandDir, "exports", src);
  if (!existsSync(from)) throw new Error(`brand export ${from} is missing`);
  copyFileSync(from, resolve(outDir, dest));
  n += 1;
}

// The SVG favicon is the brand icon itself (the PNG renders are for browsers without SVG favicon support).
writeFileSync(resolve(outDir, "favicon.svg"), readFileSync(iconSvg));
console.log(`installed ${n} brand renders + favicon.svg from ${iconSvg} into ${outDir}`);

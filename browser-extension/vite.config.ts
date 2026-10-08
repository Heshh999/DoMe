/**
 * Three separate builds into one dist/ (run by `pnpm build`):
 *   --mode background  → dist/background.js  (ES module service worker)
 *   --mode content     → dist/content.js     (classic script: content scripts cannot use ES module imports,
 *                                             so everything is inlined; no shared chunks)
 *   --mode popup       → dist/popup.html + dist/popup.js + dist/popup.css
 * public/manifest.json is copied verbatim by the first build.
 * No minification: the unpacked extension stays reviewable. No remote code, no eval (MV3 CSP).
 */
import { fileURLToPath } from "node:url";

import { defineConfig, type UserConfig } from "vite";

const here = fileURLToPath(new URL(".", import.meta.url));
const dist = `${here}dist`;

const common = {
  target: "chrome116",
  minify: false as const,
  sourcemap: false,
  modulePreload: false as const,
  assetsInlineLimit: 0,
};

export default defineConfig(({ mode }): UserConfig => {
  if (mode === "background") {
    return {
      root: here,
      publicDir: `${here}public`,
      build: {
        ...common,
        outDir: dist,
        emptyOutDir: true,
        lib: { entry: `${here}src/background/index.ts`, formats: ["es"], fileName: () => "background.js" },
        rollupOptions: { output: { inlineDynamicImports: true } },
      },
    };
  }
  if (mode === "content") {
    return {
      root: here,
      publicDir: false,
      build: {
        ...common,
        outDir: dist,
        emptyOutDir: false,
        lib: { entry: `${here}src/content/index.ts`, formats: ["iife"], name: "DoMeContent", fileName: () => "content.js" },
        rollupOptions: { output: { inlineDynamicImports: true } },
      },
    };
  }
  if (mode === "popup") {
    return {
      root: `${here}src/popup`,
      publicDir: false,
      base: "./",
      build: {
        ...common,
        outDir: dist,
        emptyOutDir: false,
        rollupOptions: {
          input: `${here}src/popup/popup.html`,
          output: {
            entryFileNames: "popup.js",
            chunkFileNames: "popup-[name].js",
            assetFileNames: "[name][extname]",
            inlineDynamicImports: true,
          },
        },
      },
    };
  }
  throw new Error(`unknown build mode ${JSON.stringify(mode)}; use --mode background|content|popup`);
});

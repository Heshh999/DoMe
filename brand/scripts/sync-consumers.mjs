#!/usr/bin/env node
// brand/scripts/sync-consumers.mjs — copies the brand exports that the browser extension and the PC agent
// ship into their source trees. (The PWA has its own installer, mobile-app/scripts/make-icons.mjs.)
// The copies are committed in those components so their builds need nothing from brand/;
// tests/brand.test.mjs fails when a copy no longer matches the export it came from.
//
// Run:            node brand/scripts/sync-consumers.mjs          (pnpm sync in brand/)
// Check:          node brand/scripts/sync-consumers.mjs --check  (exit 1 if a copy is missing or stale)
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { BRAND_DIR, EXPORTS_DIR, EXPORT_PLAN, exportsEquivalent } from "./export.mjs";

const REPO_DIR = resolve(BRAND_DIR, "..");

/** [export under exports/ → path under the repository root] */
export const CONSUMER_COPIES = Object.freeze([
  // Chrome/Edge extension: manifest "icons" (16/32/48/128) and "action.default_icon" (16/32/48)
  { from: "extension/icon-16.png", to: "browser-extension/public/icons/icon-16.png" },
  { from: "extension/icon-32.png", to: "browser-extension/public/icons/icon-32.png" },
  { from: "extension/icon-48.png", to: "browser-extension/public/icons/icon-48.png" },
  { from: "extension/icon-128.png", to: "browser-extension/public/icons/icon-128.png" },
  // Windows tray: pc-agent/dome_agent/tray.py loads this and draws the connection badge on it
  { from: "windows/tray-256.png", to: "pc-agent/dome_agent/assets/tray-256.png" },
]);

/**
 * Copies every consumer file (or, with check=true, reports the ones that are missing or whose pixels
 * differ from the export).
 * @param {{check?: boolean, repoDir?: string}} [options]
 * @returns {{written: string[], stale: string[]}}
 */
export function syncConsumers({ check = false, repoDir = REPO_DIR } = {}) {
  const byOut = new Map(EXPORT_PLAN.map((e) => [e.out, e]));
  /** @type {string[]} */
  const written = [];
  /** @type {string[]} */
  const stale = [];
  for (const { from, to } of CONSUMER_COPIES) {
    const entry = byOut.get(from);
    if (!entry) throw new Error(`${from} is not in the export plan`);
    const source = resolve(EXPORTS_DIR, from);
    if (!existsSync(source)) throw new Error(`${source} is missing; run node brand/scripts/export.mjs first`);
    const target = resolve(repoDir, to);
    if (check) {
      if (!existsSync(target) || !exportsEquivalent(entry, readFileSync(target), readFileSync(source))) stale.push(to);
      continue;
    }
    mkdirSync(dirname(target), { recursive: true });
    copyFileSync(source, target);
    written.push(to);
  }
  return { written, stale };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const check = process.argv.includes("--check");
  const report = syncConsumers({ check });
  if (check) {
    if (report.stale.length) {
      console.error(`brand copies are stale (${report.stale.length}): ${report.stale.join(", ")}\nrun: node brand/scripts/sync-consumers.mjs`);
      process.exit(1);
    }
    console.error(`brand copies up to date (${CONSUMER_COPIES.length} files)`);
  } else {
    console.error(`copied ${report.written.length} files: ${report.written.join(", ")}`);
  }
}

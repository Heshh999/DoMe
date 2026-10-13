import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { CONTENT_SCRIPT_FILE } from "../src/background/tabs.ts";

const manifest = JSON.parse(readFileSync(resolve(import.meta.dirname, "../public/manifest.json"), "utf8")) as Record<string, unknown>;
const pkg = JSON.parse(readFileSync(resolve(import.meta.dirname, "../package.json"), "utf8")) as { version: string };

describe("manifest.json (spec §9/§15, design)", () => {
  it("has exactly the designed permissions and the narrow YouTube host permission", () => {
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.name).toBe("DoMe for YouTube");
    // scripting: re-run the extension's own content.js in YouTube tabs open before install/reload
    // (DECISIONS D26); the YouTube host permission below is what limits where it can run.
    // YouTube search (protocol 1.2) uses tabs.update/tabs.create, which need no "tabs" permission,
    // and reads only YouTube tabs' URLs, which the host permission already covers (DECISIONS D27).
    expect([...(manifest.permissions as string[])].sort()).toEqual(["alarms", "nativeMessaging", "scripting", "storage"]);
    expect(manifest.host_permissions).toEqual(["https://www.youtube.com/*"]);
    // A plain link shown on the extension's store and details pages; it grants nothing.
    expect(manifest.homepage_url).toBe("https://domeremote.com");
    expect(manifest.optional_permissions).toBeUndefined();
    expect(manifest.optional_host_permissions).toBeUndefined();
    for (const forbidden of ["tabs", "history", "cookies", "debugger", "webRequest", "<all_urls>"]) {
      expect(JSON.stringify(manifest)).not.toContain(`"${forbidden}"`);
    }
  });

  it("carries the package version (0.2.0 speaks protocol 1.2: YouTube search and open result)", () => {
    expect(manifest.version).toBe(pkg.version);
    expect(manifest.version).toBe("0.2.0");
  });

  it("uses a module service worker, a top-frame-only YouTube content script and the status popup", () => {
    expect(manifest.background).toEqual({ service_worker: "background.js", type: "module" });
    expect(manifest.content_scripts).toEqual([{ matches: ["https://www.youtube.com/*"], js: [CONTENT_SCRIPT_FILE], run_at: "document_idle" }]);
    expect(manifest.action).toEqual({
      default_title: "DoMe",
      default_popup: "popup.html",
      default_icon: { "16": "icons/icon-16.png", "32": "icons/icon-32.png", "48": "icons/icon-48.png" },
    });
    expect(manifest.minimum_chrome_version).toBe("116");
  });

  it("ships the DoMe brand icons it names (copied from brand/exports/extension by brand/scripts/sync-consumers.mjs)", () => {
    const icons = manifest.icons as Record<string, string>;
    expect(icons).toEqual({ "16": "icons/icon-16.png", "32": "icons/icon-32.png", "48": "icons/icon-48.png", "128": "icons/icon-128.png" });
    for (const [size, rel] of Object.entries(icons)) {
      const png = readFileSync(resolve(import.meta.dirname, "../public", rel));
      expect(png.subarray(1, 4).toString("ascii")).toBe("PNG");
      expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([Number(size), Number(size)]);
    }
  });

  it("has a restrictive CSP with no remote code or eval, and no pinned key in the development build", () => {
    const csp = (manifest.content_security_policy as { extension_pages: string }).extension_pages;
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).not.toMatch(/unsafe-eval|unsafe-inline|https?:/);
    expect(manifest.key).toBeUndefined();
    expect(manifest.externally_connectable).toBeUndefined();
  });
});

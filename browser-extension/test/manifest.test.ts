import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const manifest = JSON.parse(readFileSync(resolve(import.meta.dirname, "../public/manifest.json"), "utf8")) as Record<string, unknown>;

describe("manifest.json (spec §9/§15, design)", () => {
  it("has exactly the designed permissions and the narrow YouTube host permission", () => {
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.name).toBe("DoMe for YouTube");
    expect([...(manifest.permissions as string[])].sort()).toEqual(["alarms", "nativeMessaging", "storage"]);
    expect(manifest.host_permissions).toEqual(["https://www.youtube.com/*"]);
    expect(manifest.optional_permissions).toBeUndefined();
    expect(manifest.optional_host_permissions).toBeUndefined();
    for (const forbidden of ["tabs", "history", "cookies", "debugger", "scripting", "webRequest", "<all_urls>"]) {
      expect(JSON.stringify(manifest)).not.toContain(`"${forbidden}"`);
    }
  });

  it("uses a module service worker, a top-frame-only YouTube content script and the status popup", () => {
    expect(manifest.background).toEqual({ service_worker: "background.js", type: "module" });
    expect(manifest.content_scripts).toEqual([{ matches: ["https://www.youtube.com/*"], js: ["content.js"], run_at: "document_idle" }]);
    expect(manifest.action).toEqual({ default_title: "DoMe", default_popup: "popup.html" });
    expect(manifest.minimum_chrome_version).toBe("116");
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

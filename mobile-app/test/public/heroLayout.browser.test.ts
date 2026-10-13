// @vitest-environment node
/**
 * The home hero's floating "Volume set to 40%" toast must never cover the hero's calls to action or the required
 * "DoMe is in development…" caveat (rollout brief §4). That is a question of real layout, which jsdom cannot answer,
 * so this test serves the site with Vite and measures it in headless Chromium at common desktop widths. It runs
 * where Playwright and its headless shell are installed (the design workstation) and is skipped elsewhere.
 */
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

type Rect = { x: number; y: number; width: number; height: number };
type Browser = { newPage(o: { viewport: { width: number; height: number } }): Promise<Page>; close(): Promise<void> };
type Page = {
  goto(url: string, o: { waitUntil: string }): Promise<unknown>;
  waitForSelector(sel: string): Promise<unknown>;
  evaluate<T>(fn: () => T): Promise<T>;
  close(): Promise<void>;
};

const appDir = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const CHROMIUM = process.env.DOME_CHROMIUM ?? "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell";

function loadPlaywright(): { chromium: { launch(o: { executablePath: string }): Promise<Browser> } } | null {
  for (const from of [import.meta.url, "/opt/node-tools/node_modules/"]) {
    try {
      return createRequire(from)("playwright-core");
    } catch {
      /* try the next place */
    }
  }
  return null;
}

const playwright = existsSync(CHROMIUM) ? loadPlaywright() : null;

const intersects = (a: Rect, b: Rect) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

describe.skipIf(!playwright)("home hero layout (real browser)", () => {
  let server: { close(): Promise<void>; resolvedUrls: { local: string[] } | null } | null = null;
  let browser: Browser | null = null;
  let base = "";

  beforeAll(async () => {
    const { createServer } = await import("vite");
    const s = await createServer({ root: appDir, configFile: resolve(appDir, "vite.config.ts"), logLevel: "silent", server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false } });
    await s.listen();
    server = s;
    base = (s.resolvedUrls?.local[0] ?? "").replace(/\/$/, "");
    browser = await playwright!.chromium.launch({ executablePath: CHROMIUM });
  }, 60_000);

  afterAll(async () => {
    await browser?.close();
    await server?.close();
  });

  for (const width of [1024, 1280, 1440, 1920]) {
    it(`at ${width} px the toast covers neither hero button nor the caveat`, async () => {
      const page = await browser!.newPage({ viewport: { width, height: 900 } });
      try {
        await page.goto(`${base}/`, { waitUntil: "networkidle" });
        await page.waitForSelector('[data-testid="hero-caveat"]');
        const m = await page.evaluate(() => {
          const rect = (id: string) => {
            const el = document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
            if (!el || getComputedStyle(el).display === "none") return null;
            const r = el.getBoundingClientRect();
            return { x: r.x, y: r.y, width: r.width, height: r.height };
          };
          return { toast: rect("hero-toast"), caveat: rect("hero-caveat"), open: rect("hero-open"), windows: rect("hero-windows") };
        });
        expect(m.caveat).not.toBeNull();
        expect(m.open).not.toBeNull();
        expect(m.windows).not.toBeNull();
        // below 1280 the toast is not shown at all; from 1280 up it overlaps only the phone's bezel
        if (width < 1280) expect(m.toast).toBeNull();
        else expect(m.toast).not.toBeNull();
        if (m.toast) {
          for (const other of [m.caveat!, m.open!, m.windows!]) expect(intersects(m.toast, other)).toBe(false);
        }
      } finally {
        await page.close();
      }
    }, 60_000);
  }
});

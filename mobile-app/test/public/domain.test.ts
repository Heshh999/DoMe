/**
 * The website's one canonical address is https://domeremote.com (domeremote.app redirects there). Link previews,
 * robots.txt and the sitemap name it; the sitemap lists exactly the public pages the router serves; the private
 * parts (the app, device links, the API) stay out of search engines; the preview image stays out of the PWA cache.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const root = resolve(__dirname, "../..");
const read = (path: string) => readFileSync(resolve(root, path), "utf8");
const ORIGIN = "https://domeremote.com";

/** The paths of the routes rendered inside PublicLayout (the first children block of the router). */
function publicRoutes(): string[] {
  const router = read("src/app/router.tsx");
  const block = router.slice(router.indexOf("element: <PublicLayout />"), router.indexOf('path: "/app"'));
  return [...block.matchAll(/path: "([^"]+)"/g)].map((m) => m[1]!).sort();
}

describe("domeremote.com", () => {
  it("gives link previews an absolute image on the canonical origin, 1200 x 630", () => {
    const html = read("index.html");
    expect(html).toContain(`<meta property="og:image" content="${ORIGIN}/og-image.png" />`);
    expect(html).toContain('<meta name="twitter:card" content="summary_large_image" />');
    const png = readFileSync(resolve(root, "public/og-image.png"));
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
  });

  it("lists exactly the public pages in the sitemap", () => {
    const sitemap = read("public/sitemap.xml");
    const locs = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]!);
    expect(locs.every((l) => l.startsWith(`${ORIGIN}/`))).toBe(true);
    expect(locs.map((l) => l.slice(ORIGIN.length)).sort()).toEqual(publicRoutes());
  });

  it("keeps the signed-in app, device links and the API out of search engines", () => {
    const robots = read("public/robots.txt");
    for (const path of ["/app", "/link", "/pair", "/v1/", "/ws/"]) expect(robots).toContain(`Disallow: ${path}\n`);
    expect(robots).toContain(`Sitemap: ${ORIGIN}/sitemap.xml`);
  });

  it("does not store the preview image on phones, and lets the crawler files bypass the app shell", () => {
    const config = read("vite.config.ts");
    expect(config).toContain('globIgnores: ["og-image.png"]');
    expect(config).toContain(String.raw`/^\/(robots\.txt|sitemap\.xml|og-image\.png)$/`);
  });
});

/**
 * The design preview's route list (src/dev/preview.ts PREVIEW_ROUTES) names every app page: each preview path maps
 * onto a real route of the app (never the catch-all 404), and the mapping is the one `startPreviewIfAsked` uses.
 */
import { matchRoutes } from "react-router";
import { describe, expect, it } from "vitest";

import { router } from "../src/app/router.tsx";
import { PREVIEW_FLAGS, PREVIEW_ROUTES, previewTarget } from "../src/dev/preview.ts";

describe("design preview routes", () => {
  it("maps /preview… onto the app's routes, and /preview/link onto the link page outside the tab bar", () => {
    expect(previewTarget("/preview")).toBe("/app");
    expect(previewTarget("/preview/remote")).toBe("/app/remote");
    expect(previewTarget("/preview/devices/pair")).toBe("/app/devices/pair");
    expect(previewTarget("/preview/link")).toBe("/link");
    for (const r of PREVIEW_ROUTES) expect(previewTarget(r.preview), r.preview).toBe(r.app);
  });

  it("every listed route exists in the router (none falls through to the 404 page)", () => {
    for (const r of PREVIEW_ROUTES) {
      const matches = matchRoutes(router.routes, r.app) ?? [];
      const leaf = matches[matches.length - 1];
      expect(leaf, r.app).toBeDefined();
      expect(leaf!.route.path, r.app).not.toBe("*");
    }
  });

  it("covers every page under /app, so a new page cannot be forgotten", () => {
    const app = router.routes.find((r) => r.path === "/app");
    const children = (app?.children ?? []).map((c) => (c.index ? "/app" : `/app/${c.path}`));
    expect(children.length).toBeGreaterThan(5);
    for (const path of children) expect(PREVIEW_ROUTES.map((r) => r.app), path).toContain(path);
  });

  it("documents the offline and confirmation flags", () => {
    expect(PREVIEW_FLAGS.map((f) => f.flag)).toEqual(["offline=1", "confirm=1"]);
  });
});

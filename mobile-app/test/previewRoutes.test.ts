/**
 * The design preview's route list (src/dev/preview.ts PREVIEW_ROUTES) names every app page: each preview path maps
 * onto a real route of the app (never the catch-all 404), and the mapping is the one `startPreviewIfAsked` uses.
 */
import { matchRoutes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { router } from "../src/app/router.tsx";
import { PREVIEW_FLAGS, PREVIEW_ROUTES, PreviewPc, previewTarget } from "../src/dev/preview.ts";

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
    expect(PREVIEW_FLAGS.map((f) => f.flag)).toEqual(["offline=1", "confirm=1", "pc=offline"]);
  });
});

describe("design preview PC", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const command = (action: string) => ({ type: "command", envelope: { payload: JSON.stringify({ command_id: "11111111-1111-4111-8111-111111111111", action, params: {}, target: null }) } });

  it("with ?pc=offline a command fails at once with PC_OFFLINE, as the relay answers it: nothing is acked or run", () => {
    vi.useFakeTimers();
    const pc = new PreviewPc();
    pc.offline = true;
    const replies: Array<Record<string, unknown>> = [];
    pc.handle(command("app.list"), (f) => replies.push(f));
    expect(replies).toHaveLength(1);
    expect(replies[0]).toMatchObject({ type: "result", origin: "relay", state: "failed", error: { code: "PC_OFFLINE", retryable: true } });
  });

  it("online, the same command is acked and succeeds", () => {
    vi.useFakeTimers();
    const pc = new PreviewPc();
    const replies: Array<Record<string, unknown>> = [];
    pc.handle(command("app.list"), (f) => replies.push(f));
    expect(replies.map((r) => r.type)).toEqual(["ack", "result"]);
    expect(replies[1]).toMatchObject({ state: "succeeded" });
  });
});

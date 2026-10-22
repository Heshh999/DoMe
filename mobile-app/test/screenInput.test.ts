/**
 * Screen view taps and scrolls → signed input events (docs/design/screen-view.md §8.1, §8.3): a tap is
 * `[pointer_move_to, click]` at the generation the phone saw, a two-finger scroll is `[pointer_move_to(midpoint),
 * pointer_wheel]`, the loupe clicks at its slowed aim; letterbox bars, DoMe's reserved bars, a held-back geometry and a
 * missing one are dropped with a reason and never clamped; pinch and pan send nothing. The batch these events go into
 * is stamped 1.4 by the shared library.
 */
import { describe, expect, it } from "vitest";

import { inputBatchProtocolVersion } from "@dome/protocol";

import { directEvents, dropHint, locate, LOUPE_ZOOM, loupeAim, type SurfaceContext } from "../src/lib/screenInput.ts";
import type { Geometry } from "../src/lib/screenMap.ts";

const GEOMETRY: Geometry = { type: "geometry", gen: 7, surface_index: 0, width: 1920, height: 1080, rotation: 0, reserved: [{ x: 0, y: 0, w: 65535, h: 2000 }] };
/** A 400 × 300 box showing a 16:9 frame: content 400 × 225, letterbox bars of 37.5 px above and below. */
function ctx(patch: Partial<SurfaceContext> = {}): SurfaceContext {
  return { box: { width: 400, height: 300 }, videoWidth: 1280, videoHeight: 720, view: { s: 1, tx: 0, ty: 0 }, geometry: GEOMETRY, geometryStale: false, ...patch };
}

describe("locate", () => {
  it("maps the content to 0..65535 at the generation the phone saw, using the geometry's size before the first frame", () => {
    expect(locate(200, 150, ctx())).toEqual({ ok: true, x: 32768, y: 32768, gen: 7 });
    expect(locate(200, 150, ctx({ videoWidth: 0, videoHeight: 0 }))).toEqual({ ok: true, x: 32768, y: 32768, gen: 7 });
    expect(locate(0, 37.5 + 112.5, ctx())).toMatchObject({ ok: true, x: 0 });
  });
  it("drops the letterbox bars, DoMe's own bar, a held-back geometry and a missing one, each with its reason", () => {
    expect(locate(200, 10, ctx())).toEqual({ ok: false, reason: "outside" });
    expect(locate(200, 290, ctx())).toEqual({ ok: false, reason: "outside" });
    expect(locate(200, 38, ctx())).toEqual({ ok: false, reason: "reserved" }); // the top 2000/65535 of the surface is the bar
    expect(locate(200, 150, ctx({ geometry: null, geometryStale: true }))).toEqual({ ok: false, reason: "settling" });
    expect(locate(200, 150, ctx({ geometry: null }))).toEqual({ ok: false, reason: "no_geometry" });
    expect(locate(200, 150, ctx({ box: { width: 0, height: 0 } }))).toEqual({ ok: false, reason: "no_geometry" });
  });
  it("undoes zoom and pan: at 2× with the content panned left by the box width, the box centre is three quarters across", () => {
    expect(locate(200, 150, ctx({ view: { s: 2, tx: -400, ty: -150 } }))).toMatchObject({ ok: true, x: 49151, y: 32768 });
  });
});

describe("directEvents", () => {
  it("a tap is a move to the point plus a click, and the batch that carries it is stamped 1.4", () => {
    const r = directEvents({ type: "tap", button: "left", x: 200, y: 150 }, ctx());
    expect(r).toEqual({ kind: "send", events: [{ type: "pointer_move_to", x: 32768, y: 32768, gen: 7 }, { type: "pointer_button", button: "left", action: "click" }] });
    if (r.kind === "send") expect(inputBatchProtocolVersion(r.events)).toBe("1.4");
    expect(directEvents({ type: "tap", button: "right", x: 200, y: 150 }, ctx())).toMatchObject({ kind: "send", events: [{ type: "pointer_move_to" }, { type: "pointer_button", button: "right" }] });
  });
  it("a scroll moves under the fingers and turns the wheel; a zero scroll sends nothing; a scroll in the bars is dropped", () => {
    expect(directEvents({ type: "scroll", x: 200, y: 150, dx: 0, dy: 240 }, ctx())).toEqual({ kind: "send", events: [{ type: "pointer_move_to", x: 32768, y: 32768, gen: 7 }, { type: "pointer_wheel", dx: 0, dy: 240 }] });
    expect(directEvents({ type: "scroll", x: 200, y: 150, dx: 0, dy: 0 }, ctx())).toEqual({ kind: "local" });
    expect(directEvents({ type: "scroll", x: 200, y: 5, dx: 0, dy: 120 }, ctx())).toEqual({ kind: "drop", reason: "outside" });
  });
  it("the loupe clicks only on lift, at the aim it was given; pinch and pan are local", () => {
    expect(directEvents({ type: "loupe", phase: "start", x: 200, y: 150 }, ctx())).toEqual({ kind: "local" });
    expect(directEvents({ type: "loupe", phase: "move", x: 210, y: 150 }, ctx())).toEqual({ kind: "local" });
    expect(directEvents({ type: "loupe", phase: "cancel", x: 210, y: 150 }, ctx())).toEqual({ kind: "local" });
    expect(directEvents({ type: "loupe", phase: "end", x: 300, y: 150 }, ctx(), { x: 200, y: 150 })).toMatchObject({ kind: "send", events: [{ type: "pointer_move_to", x: 32768, y: 32768 }, { type: "pointer_button", button: "left", action: "click" }] });
    expect(directEvents({ type: "pinch", scale: 1.2, cx: 200, cy: 150 }, ctx())).toEqual({ kind: "local" });
    expect(directEvents({ type: "pan", dx: 5, dy: 5 }, ctx())).toEqual({ kind: "local" });
  });
  it("never clamps a dropped tap onto the surface", () => {
    for (const [x, y] of [
      [200, 0],
      [-5, 150],
      [405, 150],
      [200, 299],
    ] as const) {
      expect(directEvents({ type: "tap", button: "left", x, y }, ctx()).kind).toBe("drop");
    }
  });
});

describe("loupe aim and hints", () => {
  it("the crosshair moves at 1/2.5 of the finger's travel from where the loupe opened", () => {
    expect(LOUPE_ZOOM).toBe(2.5);
    expect(loupeAim({ x: 100, y: 100 }, { x: 125, y: 90 })).toEqual({ x: 110, y: 96 });
    expect(loupeAim({ x: 100, y: 100 }, { x: 100, y: 100 })).toEqual({ x: 100, y: 100 });
  });
  it("a tap in the black bars needs no words; the reserved bar and a changing picture say why nothing happened", () => {
    expect(dropHint("outside")).toBeNull();
    expect(dropHint("reserved")).toMatch(/Screen view on/);
    expect(dropHint("settling")).toMatch(/tap again/);
    expect(dropHint("no_geometry")).toMatch(/tap again/);
  });
});

/**
 * screenMap.ts (design §8.3): letterbox fit, zoom and pan inverse, rounding to 0..65535, taps in the bars and in the PC's
 * reserved rectangles dropped (never clamped), and the geometry generation held back after a video resize.
 */
import { describe, expect, it } from "vitest";

import { clampView, fitContent, GEOMETRY_STALE_MS, GeometryTracker, IDENTITY_VIEW, inReserved, mapPoint, normalise, panBy, toSurface, viewTransform, zoomAt, ZOOM_MAX } from "../src/lib/screenMap.ts";

const BOX = { width: 400, height: 300 };
const GEOMETRY = { type: "geometry" as const, gen: 2, surface_index: 0, width: 2560, height: 1440, rotation: 0 as const, reserved: [] };

describe("fit and inverse mapping", () => {
  it("letterboxes a wide frame in a tall box and a tall frame in a wide box", () => {
    const wide = fitContent(BOX, 1920, 1080)!; // k = min(400/1920, 300/1080) = 0.2083…
    expect(wide.k).toBeCloseTo(400 / 1920, 6);
    expect(wide.cw).toBeCloseTo(400, 6);
    expect(wide.ch).toBeCloseTo(225, 6);
    expect(wide.offX).toBeCloseTo(0, 6);
    expect(wide.offY).toBeCloseTo(37.5, 6);
    const tall = fitContent(BOX, 1080, 1920)!;
    expect(tall.ch).toBeCloseTo(300, 6);
    expect(tall.offX).toBeCloseTo((400 - 1080 * (300 / 1920)) / 2, 6);
    expect(fitContent({ width: 0, height: 300 }, 1920, 1080)).toBeNull();
    expect(fitContent(BOX, 0, 1080)).toBeNull();
  });

  it("maps the content's corners and centre, drops the letterbox bars and anything outside the box", () => {
    const fit = fitContent(BOX, 1920, 1080)!;
    expect(toSurface(0, 37.5, fit, IDENTITY_VIEW)).toEqual({ u: 0, v: 0 });
    const centre = toSurface(200, 150, fit, IDENTITY_VIEW)!;
    expect(centre.u).toBeCloseTo(0.5, 6);
    expect(centre.v).toBeCloseTo(0.5, 6);
    expect(toSurface(200, 10, fit, IDENTITY_VIEW)).toBeNull(); // top bar
    expect(toSurface(200, 290, fit, IDENTITY_VIEW)).toBeNull(); // bottom bar
    expect(toSurface(-1, 150, fit, IDENTITY_VIEW)).toBeNull();
    expect(toSurface(400, 150, fit, IDENTITY_VIEW)).toBeNull(); // the right edge is exclusive
  });

  it("rounds to integers in 0..65535 and never past the ends", () => {
    expect(normalise(0, 0)).toEqual({ x: 0, y: 0 });
    expect(normalise(0.5, 0.25)).toEqual({ x: 32768, y: 16384 });
    expect(normalise(0.99999, 1)).toEqual({ x: 65534, y: 65535 });
    expect(normalise(-0.1, 1.2)).toEqual({ x: 0, y: 65535 });
  });

  it("the whole chain: a tap inside a reserved rectangle (DoMe's own bar) is dropped, next to it is sent", () => {
    const bar = [{ x: 0, y: 0, w: 65535, h: 1200 }];
    expect(mapPoint(200, 38, BOX, 1920, 1080, IDENTITY_VIEW, bar)).toBeNull(); // the top rows of the frame are the bar
    const below = mapPoint(200, 60, BOX, 1920, 1080, IDENTITY_VIEW, bar)!;
    expect(below.y).toBeGreaterThan(1200);
    expect(inReserved(100, 1200, bar)).toBe(true); // inclusive
    expect(inReserved(100, 1201, bar)).toBe(false);
    expect(mapPoint(200, 10, BOX, 1920, 1080, IDENTITY_VIEW)).toBeNull(); // letterbox: dropped, not clamped
  });
});

describe("zoom and pan", () => {
  it("clamps the zoom to [1, 4] and keeps the content covering the box", () => {
    expect(clampView({ s: 0.5, tx: 10, ty: 10 }, BOX)).toEqual(IDENTITY_VIEW);
    expect(clampView({ s: 9, tx: 0, ty: 0 }, BOX).s).toBe(ZOOM_MAX);
    const v = clampView({ s: 2, tx: 50, ty: -900 }, BOX);
    expect(v.tx).toBe(0); // never shows past the left edge
    expect(v.ty).toBe(-300); // never past the bottom edge: box.height − box.height·s
  });

  it("zooming about a point keeps that point under the finger; zooming out to 1× resets the pan", () => {
    const fit = fitContent(BOX, 1920, 1080)!;
    const before = toSurface(300, 200, fit, IDENTITY_VIEW)!;
    const zoomed = zoomAt(IDENTITY_VIEW, 2, 300, 200, BOX);
    expect(zoomed.s).toBe(2);
    const after = toSurface(300, 200, fit, zoomed)!;
    expect(after.u).toBeCloseTo(before.u, 6);
    expect(after.v).toBeCloseTo(before.v, 6);
    expect(zoomAt(zoomed, 0.5, 300, 200, BOX)).toEqual(IDENTITY_VIEW);
    expect(zoomAt(zoomed, Number.NaN, 300, 200, BOX)).toEqual(zoomed);
    expect(zoomAt(zoomed, 0, 300, 200, BOX)).toEqual(zoomed);
  });

  it("pans only while zoomed, within the content", () => {
    expect(panBy(IDENTITY_VIEW, -40, -40, BOX)).toEqual(IDENTITY_VIEW);
    const zoomed = { s: 2, tx: -100, ty: -100 };
    expect(panBy(zoomed, -50, 20, BOX)).toEqual({ s: 2, tx: -150, ty: -80 });
    expect(panBy(zoomed, 500, 500, BOX)).toEqual({ s: 2, tx: 0, ty: 0 });
    expect(viewTransform({ s: 2, tx: -150, ty: -80 })).toBe("translate(-150px, -80px) scale(2)");
  });
});

describe("GeometryTracker", () => {
  it("seeds from the result, takes newer generations only, and holds taps for up to 300 ms after a video resize", () => {
    const t = new GeometryTracker();
    expect(t.current(0)).toBeNull();
    t.seed(1, 0, 2560, 1440);
    expect(t.current(0)).toMatchObject({ gen: 1, width: 2560, reserved: [] });
    expect(t.apply(GEOMETRY)).toBe(true);
    expect(t.apply({ ...GEOMETRY, gen: 1 })).toBe(false); // older: ignored
    expect(t.current(0)?.gen).toBe(2);
    t.markStale(1000);
    expect(t.isStale).toBe(true);
    expect(t.current(1000)).toBeNull();
    expect(t.current(1000 + GEOMETRY_STALE_MS)).toBeNull();
    expect(t.current(1000 + GEOMETRY_STALE_MS + 1)?.gen).toBe(2); // no message came: the known geometry is used again
    t.markStale(5000);
    t.apply({ ...GEOMETRY, gen: 3 });
    expect(t.isStale).toBe(false);
    expect(t.current(5001)?.gen).toBe(3);
    t.reset();
    expect(t.known).toBeNull();
  });
});

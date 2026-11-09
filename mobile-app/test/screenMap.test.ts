/**
 * screenMap.ts (design §8.3): letterbox fit, zoom and pan inverse, rounding to 0..65535, taps in the bars and in the PC's
 * reserved rectangles dropped (never clamped), and the geometry generation held back after a video resize. 1.5: the
 * inverse `fromSurface` (where a normalised point is drawn), the zoomed view following the pointer, the snap to 1×.
 */
import { describe, expect, it } from "vitest";

import { clampView, fitContent, followPointer, fromSurface, GEOMETRY_STALE_MS, GeometryTracker, IDENTITY_VIEW, inReserved, mapPoint, normalise, NORM_MAX, panBy, snapIdentity, toSurface, viewTransform, zoomAt, ZOOM_MAX, ZOOM_SNAP_IDENTITY } from "../src/lib/screenMap.ts";

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

describe("1.5: fromSurface, followPointer, snapIdentity", () => {
  const fit = fitContent(BOX, 1920, 1080)!;

  it("fromSurface is the inverse of toSurface through fit and view, at 1× and zoomed", () => {
    for (const view of [IDENTITY_VIEW, { s: 2.5, tx: -300, ty: -120 }]) {
      for (const [x, y] of [
        [0, 0],
        [32768, 16384],
        [65535, 65535],
        [1234, 54321],
      ] as const) {
        const { px, py } = fromSurface(x, y, fit, view);
        const back = toSurface(px - 1e-9, py - 1e-9, fit, view) ?? toSurface(px, py, fit, view);
        if (x < NORM_MAX && y < NORM_MAX) {
          expect(back!.u * NORM_MAX).toBeCloseTo(x, 3);
          expect(back!.v * NORM_MAX).toBeCloseTo(y, 3);
        }
      }
      // and the other way round: a box point maps there and back
      const uv = toSurface(123, 160, fit, view)!;
      const again = fromSurface(uv.u * NORM_MAX, uv.v * NORM_MAX, fit, view);
      expect(again.px).toBeCloseTo(123, 6);
      expect(again.py).toBeCloseTo(160, 6);
    }
    expect(fromSurface(0, 0, fit, IDENTITY_VIEW)).toEqual({ px: 0, py: 37.5 }); // the letterbox offset
  });

  it("followPointer: nothing moves at 1× or while the point is inside the inner window", () => {
    expect(followPointer(IDENTITY_VIEW, { px: 0, py: 0 }, BOX)).toBe(IDENTITY_VIEW);
    const view = { s: 2, tx: -200, ty: -150 };
    // window margin: max(48, 0.2 · 300) = 60 → [60, 340] × [60, 240]
    expect(followPointer(view, { px: 200, py: 150 }, BOX)).toBe(view);
    expect(followPointer(view, { px: 60, py: 240 }, BOX)).toBe(view); // on the edges: inside
  });

  it("followPointer: a point outside pans the view by the overshoot so it sits on the window's edge", () => {
    const view = { s: 2, tx: -200, ty: -150 };
    const right = followPointer(view, { px: 370, py: 150 }, BOX);
    expect(right).toEqual({ s: 2, tx: -230, ty: -150 }); // 30 px beyond 340
    const up = followPointer(view, { px: 200, py: 20 }, BOX);
    expect(up).toEqual({ s: 2, tx: -200, ty: -110 }); // 40 px above 60
    const corner = followPointer(view, { px: 10, py: 290 }, BOX);
    expect(corner).toEqual({ s: 2, tx: -150, ty: -200 });
  });

  it("followPointer stays within the content's edges (clampView) and keeps the 48 px floor on a small box", () => {
    const view = { s: 2, tx: -390, ty: -290 }; // near the bottom-right edge already
    expect(followPointer(view, { px: 399, py: 299 }, BOX)).toEqual({ s: 2, tx: -400, ty: -300 });
    const small = { width: 200, height: 150 }; // 0.2 · 150 = 30 → the 48 px floor
    expect(followPointer({ s: 2, tx: -100, ty: -75 }, { px: 20, py: 75 }, small)).toEqual({ s: 2, tx: -72, ty: -75 });
    // a box so small that 48 px exceeds half of it: the margin is half the box, never an inverted window
    const tiny = { width: 80, height: 60 };
    expect(followPointer({ s: 2, tx: -40, ty: -30 }, { px: 70, py: 30 }, tiny)).toEqual({ s: 2, tx: -60, ty: -30 }); // margin 30: window [30, 50]
  });

  it("snapIdentity: a zoom ending under 1.1× is the whole picture again", () => {
    expect(ZOOM_SNAP_IDENTITY).toBe(1.1);
    expect(snapIdentity({ s: 1.09, tx: -5, ty: -3 })).toBe(IDENTITY_VIEW);
    const kept = { s: 1.1, tx: -5, ty: -3 };
    expect(snapIdentity(kept)).toBe(kept);
    expect(snapIdentity(zoomAt(IDENTITY_VIEW, 1.05, 200, 150, BOX))).toBe(IDENTITY_VIEW);
    expect(snapIdentity(zoomAt(IDENTITY_VIEW, 2, 200, 150, BOX)).s).toBe(2);
    expect(clampView(snapIdentity({ s: 1, tx: 0, ty: 0 }), BOX)).toBe(IDENTITY_VIEW);
  });
});

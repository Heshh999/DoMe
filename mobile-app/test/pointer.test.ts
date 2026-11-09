/**
 * PointerModel (design screen-view-2a §4.2, D18/D19): nothing visible before the session's first report; touchpad
 * moves scale by 65535/(W−1) of the PHYSICAL surface and clamp; a direct tap snaps the prediction; reports blend 35 %
 * while a finger moves, ease over 80 ms once idle for 120 ms, snap above 10 % of the surface; the last report of a
 * slide, only blended, is reached once the moves and the reports went quiet for 120 ms (the PC never repeats one);
 * `drawn`/1.4 never shows a pointer; a new geometry hides it until the next report; the follow point works in every
 * mode (seeded at the pinch centre when nothing is known); the client's one-slot report is drained once per frame.
 */
import { describe, expect, it } from "vitest";

import { BLEND_WHILE_MOVING, EASE_MS, IDLE_MS, PointerModel, SNAP_FRACTION, type PointerReport } from "../src/lib/pointer.ts";

const SURFACE = { width: 1920, height: 1080 };
const rep = (x: number, y: number, visible = true): PointerReport => ({ x, y, visible, gen: 1 });

function overlay(): PointerModel {
  const m = new PointerModel({ surface: () => SURFACE });
  m.setMode("overlay");
  return m;
}

describe("PointerModel: visibility", () => {
  it("is hidden before the first report of the session, never a guess at 0,0", () => {
    const m = overlay();
    expect(m.sample(0)).toMatchObject({ visible: false });
    m.predictTo(30000, 30000, 0);
    m.predictMoveBy(100, 0, SURFACE, 10);
    expect(m.sample(20).visible).toBe(false);
    m.reported(rep(32768, 32768), 30);
    expect(m.sample(30).visible).toBe(true);
  });

  it("is never visible in a drawn session or a 1.4 session (mode null), whatever reports say", () => {
    for (const mode of ["drawn", null] as const) {
      const m = new PointerModel();
      m.setMode(mode);
      m.reported(rep(1000, 1000), 0);
      expect(m.sample(1).visible, String(mode)).toBe(false);
    }
  });

  it("follows the PC's visible flag; hide() (paused) and geometryChanged() hold it until the next report", () => {
    const m = overlay();
    m.reported(rep(100, 100), 0);
    expect(m.sample(0).visible).toBe(true);
    m.reported(rep(100, 100, false), 10);
    expect(m.sample(10).visible).toBe(false);
    m.reported(rep(100, 100), 20);
    m.hide();
    expect(m.sample(20).visible).toBe(false);
    m.reported(rep(120, 120), 30);
    expect(m.sample(30).visible).toBe(true);
    m.geometryChanged();
    expect(m.sample(40).visible).toBe(false);
    expect(m.followSample()).toBeNull(); // the old position means nothing on the new generation
    m.reported(rep(500, 600), 50);
    expect(m.sample(50)).toMatchObject({ visible: true, x: 500, y: 600 });
  });

  it("reset() forgets everything, the mode included; setMode() forgets positions and the shape", () => {
    const m = overlay();
    m.shape({ kind: "ibeam" });
    m.reported(rep(100, 100), 0);
    m.setMode("overlay");
    expect(m.sample(1)).toMatchObject({ visible: false, shape: null });
    m.reported(rep(100, 100), 2);
    m.reset();
    expect(m.pointerMode).toBeNull();
    m.reported(rep(100, 100), 3);
    expect(m.sample(3).visible).toBe(false);
    expect(m.predErrMaxPx).toBeNull();
  });

  it("keeps only the latest shape", () => {
    const m = overlay();
    m.shape({ kind: "arrow" });
    m.shape({ kind: "custom", width: 24, height: 24, hotspotX: 3, hotspotY: 2, src: "data:image/png;base64,AAAA" });
    expect(m.sample(0).shape).toEqual({ kind: "custom", width: 24, height: 24, hotspotX: 3, hotspotY: 2, src: "data:image/png;base64,AAAA" });
  });
});

describe("PointerModel: prediction", () => {
  it("touchpad: a move adds dx·65535/(W−1) of the physical surface, at once", () => {
    const m = overlay();
    m.reported(rep(30000, 20000), 0);
    m.predictMoveBy(100, 0, SURFACE, 500);
    // 100 · 65535 / 1919 = 3415.06…
    expect(m.sample(500).x).toBeCloseTo(30000 + (100 * 65535) / 1919, 6);
    m.predictMoveBy(0, -50, SURFACE, 510);
    expect(m.sample(510).y).toBeCloseTo(20000 - (50 * 65535) / 1079, 6);
  });

  it("the same move covers half the distance on a 2560-wide physical surface as on 1280 (never the encode size)", () => {
    const wide = overlay();
    const narrow = overlay();
    for (const m of [wide, narrow]) m.reported(rep(10000, 10000), 0);
    wide.predictMoveBy(256, 0, { width: 2560, height: 1440 }, 500);
    narrow.predictMoveBy(256, 0, { width: 1280, height: 720 }, 500);
    const dWide = wide.sample(500).x - 10000;
    const dNarrow = narrow.sample(500).x - 10000;
    expect(dWide).toBeCloseTo((256 * 65535) / 2559, 6);
    expect(dNarrow / dWide).toBeCloseTo(2559 / 1279, 6);
  });

  it("clamps to the surface, and predicts nothing without a known surface or a known point", () => {
    const m = overlay();
    m.reported(rep(65000, 100), 0);
    m.predictMoveBy(4096, -4096, SURFACE, 500);
    expect(m.sample(500)).toMatchObject({ x: 65535, y: 0 });
    m.predictMoveBy(-100, 0, null, 510);
    expect(m.sample(510).x).toBe(65535);
    const empty = overlay();
    empty.predictMoveBy(100, 100, SURFACE, 0);
    expect(empty.followSample()).toBeNull();
  });

  it("direct: a tap snaps the prediction to the tap point", () => {
    const m = overlay();
    m.reported(rep(1000, 1000), 0);
    m.predictTo(40000, 50000, 500);
    expect(m.sample(500)).toMatchObject({ x: 40000, y: 50000, busy: false });
  });
});

describe("PointerModel: reconciliation with the PC's reports", () => {
  it("while a finger moves, each report pulls the prediction 35 % of the way", () => {
    const m = overlay();
    m.reported(rep(10000, 10000), 0);
    m.predictMoveBy(100, 0, SURFACE, 1000); // predicted ≈ 13415
    const predicted = m.followSample()!.x;
    m.reported(rep(12000, 10000), 1040); // 40 ms after the move: still moving
    expect(m.followSample()!.x).toBeCloseTo(predicted + BLEND_WHILE_MOVING * (12000 - predicted), 6);
    expect(m.sample(1040).busy).toBe(false);
  });

  it("a finger resting on the glass counts as moving (fingerActive), so reports blend instead of easing", () => {
    const m = overlay();
    m.reported(rep(10000, 10000), 0);
    m.fingerActive(true, 0);
    m.reported(rep(12000, 10000), 5000);
    expect(m.followSample()!.x).toBeCloseTo(10000 + BLEND_WHILE_MOVING * 2000, 6);
    m.fingerActive(false, 5000);
    m.reported(rep(12000, 10000), 6000);
    expect(m.sample(6000).busy).toBe(true); // idle now: an ease
  });

  it("idle for 120 ms: the shown point eases to the report over 80 ms (cubic ease-out), busy meanwhile", () => {
    const m = overlay();
    m.reported(rep(10000, 10000), 0);
    m.predictMoveBy(10, 0, SURFACE, 1000);
    const from = m.followSample()!.x;
    m.reported(rep(12000, 10000), 1000 + IDLE_MS + 1);
    const t0 = 1000 + IDLE_MS + 1;
    expect(m.sample(t0)).toMatchObject({ busy: true });
    expect(m.sample(t0).x).toBeCloseTo(from, 6);
    const half = m.sample(t0 + EASE_MS / 2);
    expect(half.x).toBeCloseTo(from + (12000 - from) * (1 - 0.5 ** 3), 6);
    expect(half.busy).toBe(true);
    const done = m.sample(t0 + EASE_MS);
    expect(done).toMatchObject({ x: 12000, busy: false });
    expect(m.followSample()).toEqual({ x: 12000, y: 10000 });
  });

  it("the PC never repeats a report: the last one of a slide, only blended, is reached once the moves and the reports went quiet for 120 ms (finger lifted or resting), once per report", () => {
    for (const lift of [true, false]) {
      const m = overlay();
      m.reported(rep(10000, 10000), 0);
      m.fingerActive(true, 1000);
      m.predictMoveBy(100, 0, SURFACE, 1000); // predicted ≈ 13415
      m.reported(rep(12000, 10000), 1040); // late: blended, and no other report ever comes
      const blended = m.followSample()!.x;
      expect(blended).toBeGreaterThan(12000);
      if (lift) m.fingerActive(false, 1050);
      // a report may still be in flight: nothing eases until the reports, too, went quiet for IDLE_MS
      expect(m.sample(1040 + IDLE_MS)).toMatchObject({ x: blended, busy: false });
      const t0 = 1040 + IDLE_MS + 1;
      expect(m.sample(t0)).toMatchObject({ busy: true });
      expect(m.sample(t0).x).toBeCloseTo(blended, 6);
      expect(m.sample(t0 + EASE_MS), String(lift)).toMatchObject({ x: 12000, y: 10000, busy: false });
      expect(m.followSample()).toEqual({ x: 12000, y: 10000 });
      // once per report: a later move with no report after it is not pulled back to the old report
      m.predictMoveBy(10, 0, SURFACE, 2000);
      const moved = m.followSample()!.x;
      expect(m.sample(2000 + IDLE_MS + EASE_MS + 50)).toMatchObject({ x: moved, busy: false });
    }
  });

  it("a touch that stops an ease short of the report leaves the rest for the next quiet moment", () => {
    const m = overlay();
    m.reported(rep(10000, 10000), 0);
    m.reported(rep(14000, 10000), 1000); // idle: ease 10000 → 14000
    const mid = m.sample(1040).x;
    m.fingerActive(true, 1040);
    expect(m.sample(1041)).toMatchObject({ x: mid, busy: false });
    expect(m.sample(1000 + IDLE_MS)).toMatchObject({ x: mid, busy: false });
    const t0 = 1000 + IDLE_MS + 1;
    expect(m.sample(t0).busy).toBe(true);
    expect(m.sample(t0 + EASE_MS)).toMatchObject({ x: 14000, busy: false });
  });

  it("an error above 10 % of the surface snaps (no blend, no ease)", () => {
    const m = overlay();
    m.reported(rep(10000, 10000), 0);
    m.predictMoveBy(10, 0, SURFACE, 1000);
    m.reported(rep(10000 + SNAP_FRACTION * 65535 + 500, 10000), 1010);
    expect(m.sample(1010)).toMatchObject({ x: 10000 + SNAP_FRACTION * 65535 + 500, busy: false });
  });

  it("a touch stops a running ease where it is shown; a move during an ease carries the eased point along", () => {
    const m = overlay();
    m.reported(rep(10000, 10000), 0);
    m.reported(rep(14000, 10000), 1000); // idle: ease 10000 → 14000
    const mid = m.sample(1040).x;
    m.fingerActive(true, 1040);
    expect(m.sample(1041)).toMatchObject({ x: mid, busy: false });
    const e = overlay();
    e.reported(rep(10000, 10000), 0);
    e.reported(rep(14000, 10000), 1000);
    e.predictMoveBy(100, 0, SURFACE, 1040); // shifts the ease by the move
    const shift = (100 * 65535) / 1919;
    expect(e.sample(1080 + EASE_MS).x).toBeCloseTo(14000 + shift, 6);
  });

  it("the prediction error is kept in PC pixels (the readout's pred err max)", () => {
    const m = overlay();
    m.reported(rep(10000, 10000), 0);
    expect(m.predErrMaxPx).toBeNull(); // the first report is adopted, not an error
    m.predictMoveBy(100, 0, SURFACE, 1000); // + 100 PC px
    m.reported(rep(10000 + Math.round((91 * 65535) / 1919), 10000), 1010); // the PC moved 91 px: 9 px short
    expect(m.predErrMaxPx).toBe(9);
    m.reported(rep(10000, 10000), 1020); // no local move since the last report: not a prediction error
    expect(m.predErrMaxPx).toBe(9);
  });
});

describe("PointerModel: the follow point and the client's slot", () => {
  it("the follow point exists in a drawn or 1.4 session once seeded at the pinch centre, and moves with the touchpad", () => {
    const m = new PointerModel();
    m.setMode(null);
    expect(m.followSample()).toBeNull();
    m.seedFollow({ u: 0.5, v: 0.25 });
    expect(m.followSample()).toEqual({ x: 32768, y: 16384 });
    m.seedFollow({ u: 0.9, v: 0.9 }); // only when nothing is known
    expect(m.followSample()).toEqual({ x: 32768, y: 16384 });
    m.predictMoveBy(1919, 0, SURFACE, 10);
    expect(m.followSample()!.x).toBe(65535);
    expect(m.sample(10).visible).toBe(false); // seeding never shows a pointer
    m.seedFollow(null);
    expect(m.followSample()).not.toBeNull();
  });

  it("sample() drains the client's one-slot report once per frame; reported(null) is nothing", () => {
    let slot: PointerReport | null = rep(2000, 3000);
    let takes = 0;
    const m = new PointerModel({
      pending: () => {
        takes += 1;
        const r = slot;
        slot = null;
        return r;
      },
    });
    m.setMode("overlay");
    expect(m.sample(0)).toMatchObject({ visible: true, x: 2000, y: 3000 });
    expect(m.sample(16)).toMatchObject({ visible: true, x: 2000, y: 3000 });
    expect(takes).toBe(2);
    m.reported(null, 20);
    expect(m.sample(20)).toMatchObject({ x: 2000, y: 3000 });
  });
});

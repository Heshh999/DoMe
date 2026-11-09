import { describe, expect, it } from "vitest";

import { DEFAULT_GESTURE_OPTIONS, GestureMachine, GESTURES, type GestureOutput } from "../src/lib/gestures.ts";

function tap(m: GestureMachine, id: number, x: number, y: number, t: number, ms = 80): GestureOutput[] {
  m.pointerDown(id, x, y, t);
  m.pointerUp(id, t + ms);
  return m.take();
}

describe("GestureMachine", () => {
  it("a short stationary tap is one left click; a long press or a moved finger is not", () => {
    const m = new GestureMachine({ sensitivity: 1 });
    expect(tap(m, 1, 100, 100, 0)).toEqual([{ type: "click", button: "left", double: false }]);
    // long press: no click
    m.pointerDown(1, 100, 100, 1000);
    m.pointerUp(1, 1000 + DEFAULT_GESTURE_OPTIONS.tapMaxMs + 1);
    expect(m.take()).toEqual([]);
    // moved past the threshold: motion, no click
    m.pointerDown(1, 100, 100, 2000);
    m.pointerMove(1, 130, 100, 2050);
    m.pointerUp(1, 2080);
    const out = m.take();
    expect(out.some((o) => o.type === "click")).toBe(false);
    expect(out).toEqual([{ type: "move", dx: 30, dy: 0 }]);
  });

  it("two quick taps in the same spot are two clicks with the second marked double; a late or distant second tap is a plain click", () => {
    const m = new GestureMachine();
    expect(tap(m, 1, 50, 50, 0)).toEqual([{ type: "click", button: "left", double: false }]);
    expect(tap(m, 1, 52, 51, 200)).toEqual([{ type: "click", button: "left", double: true }]);
    // the pair is consumed: a third quick tap starts over
    expect(tap(m, 1, 52, 51, 400)).toEqual([{ type: "click", button: "left", double: false }]);
    // too late
    expect(tap(m, 1, 52, 51, 400 + 80 + DEFAULT_GESTURE_OPTIONS.doubleTapMs + 1)).toEqual([{ type: "click", button: "left", double: false }]);
    // too far away
    expect(tap(m, 1, 300, 300, 1200)).toEqual([{ type: "click", button: "left", double: false }]);
  });

  it("changing from one finger to two never clicks: a two-finger stationary tap is one right click", () => {
    const m = new GestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 140, 100, 30);
    m.pointerUp(1, 90);
    m.pointerUp(2, 100);
    expect(m.take()).toEqual([{ type: "click", button: "right", double: false }]);
    // first finger lifted early, second lingers and moves: nothing extra, no cursor jump
    m.pointerDown(1, 100, 100, 1000);
    m.pointerDown(2, 140, 100, 1030);
    m.pointerUp(1, 1060);
    m.pointerMove(2, 200, 160, 1100);
    m.pointerUp(2, 1500);
    expect(m.take()).toEqual([]);
  });

  it("two-finger movement scrolls in notches, produces no tap, and honours the scroll-direction preference", () => {
    const m = new GestureMachine({ scrollPxPerNotch: 40, scrollDirection: "natural" });
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 140, 100, 10);
    m.pointerMove(1, 100, 185, 50);
    m.pointerMove(2, 140, 185, 60);
    m.pointerUp(1, 100);
    m.pointerUp(2, 110);
    let out = m.take();
    expect(out.filter((o) => o.type === "click")).toEqual([]);
    expect(out.filter((o) => o.type === "move")).toEqual([]);
    const notches = out.filter((o): o is Extract<GestureOutput, { type: "scroll" }> => o.type === "scroll").reduce((a, o) => a + o.dy, 0);
    expect(notches).toBe(2); // 85 px down → 2 notches, natural: content follows (positive)
    m.setOptions({ scrollDirection: "standard" });
    m.pointerDown(1, 100, 100, 1000);
    m.pointerDown(2, 140, 100, 1010);
    m.pointerMove(1, 100, 185, 1050);
    m.pointerMove(2, 140, 185, 1060);
    m.pointerUp(1, 1100);
    m.pointerUp(2, 1110);
    out = m.take();
    expect(out.filter((o): o is Extract<GestureOutput, { type: "scroll" }> => o.type === "scroll").reduce((a, o) => a + o.dy, 0)).toBe(-2);
  });

  it("drag mode presses the left button on touch, moves while held, keeps the hold when the finger lifts, and End Drag releases it once", () => {
    const m = new GestureMachine({ sensitivity: 1 });
    m.setDragMode(true);
    m.pointerDown(1, 10, 10, 0);
    m.pointerMove(1, 40, 25, 50);
    m.pointerUp(1, 400); // lift: hold stays (drag lock)
    expect(m.take()).toEqual([
      { type: "button", button: "left", action: "down" },
      { type: "move", dx: 30, dy: 15 },
    ]);
    expect(m.isLeftHeld).toBe(true);
    m.pointerDown(1, 200, 200, 1000); // reposition and continue without a second press
    m.pointerMove(1, 210, 200, 1050);
    expect(m.take()).toEqual([{ type: "move", dx: 10, dy: 0 }]);
    m.pointerUp(1, 1100);
    m.endDrag();
    m.endDrag();
    expect(m.take()).toEqual([{ type: "button", button: "left", action: "up" }]);
    expect(m.isLeftHeld).toBe(false);
    expect(m.isDragMode).toBe(true); // mode stays on; next touch starts a new drag
  });

  it("hasFinger reports only pointers that are still down (a lifted finger's lost capture is not a cancel)", () => {
    const m = new GestureMachine();
    m.pointerDown(1, 10, 10, 0);
    m.pointerDown(2, 50, 10, 5);
    expect(m.hasFinger(1)).toBe(true);
    m.pointerUp(1, 40);
    expect(m.hasFinger(1)).toBe(false);
    expect(m.hasFinger(2)).toBe(true);
    m.pointerUp(2, 45);
    expect(m.take()).toEqual([{ type: "click", button: "right", double: false }]);
    expect(m.hasFinger(2)).toBe(false);
  });

  it("cancel (pointercancel / lost capture / rotation / hidden) releases a held drag and discards the gesture without a tap", () => {
    const m = new GestureMachine();
    m.setDragMode(true);
    m.pointerDown(1, 10, 10, 0);
    m.take();
    m.cancel();
    expect(m.take()).toEqual([{ type: "button", button: "left", action: "up" }]);
    expect(m.activeFingers).toBe(0);
    // a pending tap that is cancelled does not click, and the next tap is not a double
    m.setDragMode(false);
    m.pointerDown(1, 10, 10, 100);
    m.cancel();
    expect(m.take()).toEqual([]);
    expect(tap(m, 1, 10, 10, 150)).toEqual([{ type: "click", button: "left", double: false }]);
  });

  it("applies sensitivity with fractional carry and clamps to the contract's motion bound", () => {
    const m = new GestureMachine({ sensitivity: 1.5 });
    m.pointerDown(1, 0, 0, 0);
    m.pointerMove(1, 11, 0, 20); // past the tap threshold → 16.5 → 16, carry 0.5
    m.pointerMove(1, 12, 0, 40); // 1.5 + 0.5 = 2
    m.pointerMove(1, 5000, 0, 60); // clamped
    m.pointerUp(1, 100);
    const moves = m.take().filter((o): o is Extract<GestureOutput, { type: "move" }> => o.type === "move");
    expect(moves[0]).toEqual({ type: "move", dx: 16, dy: 0 });
    expect(moves[1]).toEqual({ type: "move", dx: 2, dy: 0 });
    expect(moves[2]!.dx).toBe(4096);
  });

  it("pinch vs scroll (1.5): a spread change first is a pinch about the fingers, the midpoint moving first a scroll", () => {
    const m = new GestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 200, 100, 10);
    m.pointerMove(1, 92, 100, 50);
    m.pointerMove(2, 207, 100, 51); // 100 → 115 px: +15 %, the midpoint stays put
    m.pointerMove(1, 85, 100, 100);
    m.pointerMove(2, 215, 100, 101);
    const pinches = m.take();
    expect(pinches.map((o) => o.type)).toEqual(["pinch", "pinch", "pinch"]);
    const first = pinches[0] as Extract<GestureOutput, { type: "pinch" }>;
    expect(first.scale).toBeCloseTo(1.15, 6); // measured from the spread at touch-down: the dead zone is not lost
    expect(first.cx).toBeCloseTo(149.5, 6); // in the coordinates the machine was fed
    expect(first.cy).toBe(100);
    expect(pinches.reduce((a, o) => a * (o as Extract<GestureOutput, { type: "pinch" }>).scale, 1)).toBeCloseTo(1.3, 6);
    m.pointerUp(1, 150);
    m.pointerUp(2, 160);
    expect(m.take()).toEqual([]); // a pinch never clicks, even when both fingers lift quickly
  });

  it("a fast two-finger scroll stays a scroll through the pair guard; once decided it stays decided", () => {
    const m = new GestureMachine({ scrollPxPerNotch: 36 });
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 140, 100, 5);
    m.pointerMove(1, 100, 140, 20); // one finger's half of the move: a 40 px spread change if judged alone
    expect(m.take()).toEqual([]);
    m.pointerMove(2, 140, 140, 21);
    m.pointerMove(1, 40, 180, 40); // a later spread change does not turn the scroll into a pinch
    m.pointerMove(2, 200, 180, 41);
    const out = m.take();
    expect(out.every((o) => o.type === "scroll")).toBe(true);
    expect(out.reduce((a, o) => a + (o as Extract<GestureOutput, { type: "scroll" }>).dy, 0)).toBe(2); // 80 px / 36
    m.pointerUp(1, 100);
    m.pointerUp(2, 110);
    expect(m.take()).toEqual([]); // no right click after a scroll
  });

  it("a pinch never scrolls or moves the cursor; a still two-finger tap is still a right click", () => {
    const m = new GestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 200, 100, 5);
    m.pointerMove(1, 80, 100, 20);
    m.pointerMove(2, 220, 100, 21); // +40 %: a pinch
    m.pointerMove(1, 80, 180, 40); // then both slide down together: still the pinch, never a scroll
    m.pointerMove(2, 220, 180, 41);
    const out = m.take();
    expect(out.length).toBeGreaterThan(0);
    expect(out.every((o) => o.type === "pinch")).toBe(true);
    m.pointerUp(1, 60);
    m.pointerMove(2, 260, 260, 70); // the remaining finger neither moves the cursor nor clicks
    m.pointerUp(2, 80);
    expect(m.take()).toEqual([]);
    m.pointerDown(1, 100, 100, 1000);
    m.pointerDown(2, 140, 100, 1010);
    m.pointerUp(1, 1060);
    m.pointerUp(2, 1070);
    expect(m.take()).toEqual([{ type: "click", button: "right", double: false }]);
  });

  it("with Drag on, a second finger does not pinch and the held button survives (no pinch zoom while dragging)", () => {
    const m = new GestureMachine({ sensitivity: 1 });
    m.setDragMode(true);
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 200, 100, 10);
    m.pointerMove(2, 260, 100, 30);
    const out = m.take();
    expect(out[0]).toEqual({ type: "button", button: "left", action: "down" });
    expect(out.some((o) => o.type === "pinch" || (o.type === "button" && o.action === "up"))).toBe(false);
    expect(m.isLeftHeld).toBe(true);
  });

  it("a pinch is cancelled like any gesture: nothing clicks afterwards", () => {
    const m = new GestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 200, 100, 5);
    m.pointerMove(1, 80, 100, 20);
    m.pointerMove(2, 220, 100, 21);
    m.take();
    m.cancel();
    expect(m.take()).toEqual([]);
    expect(m.activeFingers).toBe(0);
  });

  it("pinchRatio ∞ (the Touchpad page, nothing to zoom): a jittery two-finger tap right-clicks, a sloppy slide and an anchored thumb scroll, never a pinch", () => {
    const touchpadPage = () => new GestureMachine({ sensitivity: 1.4, scrollDirection: "natural", pinchRatio: Number.POSITIVE_INFINITY });
    // a quick two-finger tap whose fingers each settle 4 px outward (a 13 % spread change: a pinch with the default)
    const jittery = (m: GestureMachine) => {
      m.pointerDown(1, 100, 300, 0);
      m.pointerDown(2, 160, 300, 10);
      m.pointerMove(1, 98, 300, 30);
      m.pointerMove(2, 162, 300, 35);
      m.pointerMove(1, 96, 300, 50);
      m.pointerMove(2, 164, 300, 55);
      m.pointerUp(1, 120);
      m.pointerUp(2, 125);
      return m.take();
    };
    expect(jittery(new GestureMachine()).map((o) => o.type)).toEqual(["pinch"]); // what Screen view's Touchpad mode needs
    expect(jittery(touchpadPage())).toEqual([{ type: "click", button: "right", double: false }]);
    // fingers 60 px apart sliding down 90 px while drifting 1.5 px outward per 3 px step
    const sloppy = touchpadPage();
    sloppy.pointerDown(1, 100, 300, 0);
    sloppy.pointerDown(2, 160, 300, 5);
    for (let i = 1, t = 10; i <= 30; i++, t += 16) {
      sloppy.pointerMove(1, 100 - 1.5 * i, 300 + 3 * i, t);
      sloppy.pointerMove(2, 160 + 1.5 * i, 300 + 3 * i, t + 1);
    }
    sloppy.pointerUp(1, 600);
    sloppy.pointerUp(2, 602);
    const slid = sloppy.take();
    expect(slid.filter((o) => o.type === "scroll")).toEqual([
      { type: "scroll", dx: 0, dy: 1 },
      { type: "scroll", dx: 0, dy: 1 },
    ]);
    expect(slid.every((o) => o.type === "scroll")).toBe(true);
    // a thumb anchored at y = 420 while the index slides down 100 px
    const thumb = touchpadPage();
    thumb.pointerDown(1, 200, 420, 0);
    thumb.pointerDown(2, 200, 300, 5);
    for (let i = 1, t = 10; i <= 20; i++, t += 16) thumb.pointerMove(2, 200, 300 + 5 * i, t);
    thumb.pointerUp(2, 400);
    thumb.pointerUp(1, 405);
    expect(thumb.take()).toEqual([{ type: "scroll", dx: 0, dy: 1 }]);
  });

  it("the gesture guide covers every row of the spec table", () => {
    const text = GESTURES.map((g) => `${g.phone} ${g.pc}`).join(" ").toLowerCase();
    for (const needle of ["slide", "lift", "tap", "double", "right click", "two fingers", "drag", "keyboard"]) expect(text).toContain(needle);
  });
});

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

  it("the gesture guide covers every row of the spec table", () => {
    const text = GESTURES.map((g) => `${g.phone} ${g.pc}`).join(" ").toLowerCase();
    for (const needle of ["slide", "lift", "tap", "double", "right click", "two fingers", "drag", "keyboard"]) expect(text).toContain(needle);
  });
});

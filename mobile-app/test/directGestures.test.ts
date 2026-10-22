/**
 * DirectGestureMachine (design §8.3): tap → left click there; a second quick tap reuses the first position; two-finger
 * tap → right click at the midpoint; press and hold → the loupe, lift → click at the crosshair; two fingers moving →
 * pinch when the spread changes first, scroll otherwise (wheel units, direction preference, ±1200 clamp); one finger
 * pans only while zoomed; a second finger never clicks; cancel never clicks.
 */
import { describe, expect, it } from "vitest";

import { DEFAULT_DIRECT_OPTIONS, DIRECT_GESTURES, DirectGestureMachine, WHEEL_MAX, type DirectOutput } from "../src/lib/directGestures.ts";

function tap(m: DirectGestureMachine, x: number, y: number, t: number, id = 1): DirectOutput[] {
  m.pointerDown(id, x, y, t);
  m.pointerUp(id, t + 80);
  return m.take();
}

describe("taps", () => {
  it("a short still tap is a left click at the finger; the second within 500 ms and 12 px lands on the FIRST position", () => {
    const m = new DirectGestureMachine();
    expect(tap(m, 100, 120, 1000)).toEqual([{ type: "tap", button: "left", x: 100, y: 120 }]);
    expect(tap(m, 106, 124, 1300)).toEqual([{ type: "tap", button: "left", x: 100, y: 120 }]);
    // a third tap starts a new pair (the double was consumed)
    expect(tap(m, 106, 124, 1500)).toEqual([{ type: "tap", button: "left", x: 106, y: 124 }]);
    // too far or too late: its own position
    expect(tap(m, 140, 124, 1700)).toEqual([{ type: "tap", button: "left", x: 140, y: 124 }]);
    expect(tap(m, 141, 124, 2900)).toEqual([{ type: "tap", button: "left", x: 141, y: 124 }]);
  });

  it("a long press or a moved finger is not a tap; at 1× a moved finger produces nothing", () => {
    const m = new DirectGestureMachine();
    m.pointerDown(1, 10, 10, 0);
    m.pointerUp(1, 400);
    expect(m.take()).toEqual([]);
    m.pointerDown(1, 10, 10, 1000);
    m.pointerMove(1, 40, 10, 1050);
    m.pointerMove(1, 60, 12, 1100);
    m.pointerUp(1, 1150);
    expect(m.take()).toEqual([]);
  });

  it("a two-finger still tap is a right click at the midpoint; a second finger never clicks", () => {
    const m = new DirectGestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 140, 100, 20);
    m.pointerUp(1, 120);
    m.pointerUp(2, 140);
    expect(m.take()).toEqual([{ type: "tap", button: "right", x: 120, y: 100 }]);
    // the first finger's pending tap was discarded when the second arrived; lifting one and keeping one clicks nothing
    m.pointerDown(1, 100, 100, 1000);
    m.pointerDown(2, 140, 100, 1020);
    m.pointerUp(2, 1100);
    m.pointerMove(1, 160, 100, 1200);
    m.pointerUp(1, 1250);
    expect(m.take()).toEqual([]);
    // three fingers: nothing until everything lifted
    m.pointerDown(1, 100, 100, 2000);
    m.pointerDown(2, 140, 100, 2010);
    m.pointerDown(3, 180, 100, 2020);
    m.pointerUp(1, 2050);
    m.pointerUp(2, 2060);
    m.pointerUp(3, 2070);
    expect(m.take()).toEqual([]);
  });
});

describe("press and hold: the loupe", () => {
  it("opens after 350 ms still, follows the finger and clicks at the crosshair on lift", () => {
    const m = new DirectGestureMachine();
    m.pointerDown(1, 100, 100, 0);
    expect(m.holdDeadline()).toEqual({ id: 1, at: DEFAULT_DIRECT_OPTIONS.holdMs });
    m.hold(1, 349); // too early: the surface's timer would not fire yet, but a stray call changes nothing
    expect(m.loupeOpen).toBe(false);
    m.hold(1, 350);
    expect(m.loupeOpen).toBe(true);
    m.pointerMove(1, 130, 90, 500);
    m.pointerMove(1, 131, 91, 600);
    m.pointerUp(1, 900);
    expect(m.take()).toEqual([
      { type: "loupe", phase: "start", x: 100, y: 100 },
      { type: "loupe", phase: "move", x: 130, y: 90 },
      { type: "loupe", phase: "move", x: 131, y: 91 },
      { type: "loupe", phase: "end", x: 131, y: 91 },
    ]);
    expect(m.holdDeadline()).toBeNull();
  });

  it("a finger that slid before the timer never opens the loupe; cancel and a second finger close it without a click", () => {
    const m = new DirectGestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerMove(1, 130, 100, 100);
    m.hold(1, 350);
    expect(m.loupeOpen).toBe(false);
    m.pointerUp(1, 400);
    expect(m.take()).toEqual([]);
    m.pointerDown(1, 100, 100, 1000);
    m.hold(1, 1350);
    m.cancel();
    expect(m.take()).toEqual([
      { type: "loupe", phase: "start", x: 100, y: 100 },
      { type: "loupe", phase: "cancel", x: 100, y: 100 },
    ]);
    m.pointerDown(1, 100, 100, 2000);
    m.hold(1, 2350);
    m.pointerDown(2, 150, 100, 2400);
    m.pointerUp(1, 2450);
    m.pointerUp(2, 2460);
    const out = m.take();
    expect(out[0]).toEqual({ type: "loupe", phase: "start", x: 100, y: 100 });
    expect(out[1]).toEqual({ type: "loupe", phase: "cancel", x: 100, y: 100 });
    expect(out.some((o) => o.type === "tap")).toBe(false); // neither a left click nor a right click
  });
});

describe("two fingers moving", () => {
  it("a spread change over 10 % before the midpoint moves 8 px is a pinch about the fingers; nothing is scrolled", () => {
    const m = new DirectGestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 200, 100, 10);
    m.pointerMove(1, 92, 100, 50); // both fingers spread: 100 → 115 (+15 %), the midpoint stays put
    m.pointerMove(2, 207, 100, 51);
    m.pointerMove(1, 85, 100, 100);
    m.pointerMove(2, 215, 100, 101);
    m.pointerUp(1, 200);
    m.pointerUp(2, 210);
    const out = m.take();
    expect(out.map((o) => o.type)).toEqual(["pinch", "pinch", "pinch"]);
    const first = out[0] as Extract<DirectOutput, { type: "pinch" }>;
    expect(first.scale).toBeCloseTo(1.15, 6);
    expect(first.cx).toBeCloseTo(149.5, 6);
    const total = out.reduce((p, o) => p * (o as Extract<DirectOutput, { type: "pinch" }>).scale, 1);
    expect(total).toBeCloseTo(1.3, 6); // the zoom follows the spread exactly: 100 → 130
  });

  it("a pinch with one finger held still (an anchored thumb) is decided when the moving finger reports again", () => {
    const m = new DirectGestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 200, 100, 10);
    m.pointerMove(2, 215, 100, 50);
    expect(m.take()).toEqual([]); // one finger's move alone could be half of a two-finger scroll: not judged yet
    m.pointerMove(2, 230, 100, 66); // it moved again and the other did not: the other is really still
    const out = m.take() as Array<Extract<DirectOutput, { type: "pinch" }>>;
    expect(out.map((o) => o.type)).toEqual(["pinch"]);
    expect(out[0]!.scale).toBeCloseTo(1.3, 6);
    expect(out[0]!.cx).toBeCloseTo(165, 6);
  });

  it("the midpoint moving first is a scroll: wheel units at 120 per 36 px, natural direction, no spread change", () => {
    const m = new DirectGestureMachine();
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 140, 100, 10);
    m.pointerMove(1, 100, 106, 50); // midpoint moves 3 px, then 6 px: still undecided (under 8)
    m.pointerMove(2, 140, 106, 60);
    expect(m.take()).toEqual([]);
    m.pointerMove(1, 100, 136, 100); // midpoint 121: decided, measured from the start (36 px of travel in all)
    m.pointerMove(2, 140, 136, 110);
    const out = m.take() as Array<Extract<DirectOutput, { type: "scroll" }>>;
    expect(out.length).toBeGreaterThan(0);
    for (const s of out) {
      expect(s.type).toBe("scroll");
      expect(s.x).toBe(120);
      expect(s.dx).toBe(0);
      expect(s.dy).toBeGreaterThan(0); // positive = content follows the fingers (natural), like gestures.ts
    }
    expect(out.reduce((sum, s) => sum + s.dy, 0)).toBe(120); // 36 px = exactly one notch, whatever the number of events
    m.pointerUp(1, 200);
    m.pointerUp(2, 210);
    expect(m.take()).toEqual([]); // no right click after a scroll
  });

  it("standard direction flips the sign, one event never exceeds ±1200 (the rest is carried), sub-unit travel accumulates", () => {
    // a huge single jump of one finger changes the spread and is a pinch, so the clamp is reached with a high units-per-px setting
    const m = new DirectGestureMachine({ scrollDirection: "standard", wheelUnitsPerPx: 500 });
    m.pointerDown(1, 100, 100, 0);
    m.pointerDown(2, 500, 100, 10);
    m.pointerMove(1, 100, 120, 50);
    m.pointerMove(2, 500, 120, 51); // both 20 px down: no spread change; a scroll of 10000 units, clamped
    const out = m.take() as Array<Extract<DirectOutput, { type: "scroll" }>>;
    expect(out).toHaveLength(1);
    expect(out[0]!.dy).toBe(-WHEEL_MAX);
    m.pointerMove(1, 100, 121, 60); // the carried 8800 plus 250 more: again clamped, never above the schema's bound
    const next = m.take() as Array<Extract<DirectOutput, { type: "scroll" }>>;
    expect(next).toHaveLength(1);
    expect(next[0]!.dy).toBe(-WHEEL_MAX);
    const fine = new DirectGestureMachine();
    fine.pointerDown(1, 0, 0, 0);
    fine.pointerDown(2, 50, 0, 1);
    fine.pointerMove(1, 0, 9, 10);
    fine.pointerMove(2, 50, 9, 11); // 9 px: decided (over 8), 9·3.33 = 30 units
    expect((fine.take()[0] as Extract<DirectOutput, { type: "scroll" }>).dy).toBe(30);
    fine.pointerMove(1, 0, 9.1, 20);
    expect(fine.take()).toEqual([]); // 0.05 px of midpoint travel is below one unit: accumulated, not emitted
  });
});

describe("pan and cancel", () => {
  it("one finger pans only while zoomed, and lost capture or rotation cancels without a click", () => {
    const m = new DirectGestureMachine({ zoomed: true });
    m.pointerDown(1, 100, 100, 0);
    m.pointerMove(1, 120, 100, 50);
    m.pointerMove(1, 125, 103, 100);
    m.pointerUp(1, 150);
    expect(m.take()).toEqual([
      { type: "pan", dx: 20, dy: 0 },
      { type: "pan", dx: 5, dy: 3 },
    ]);
    m.setOptions({ zoomed: false });
    m.pointerDown(1, 100, 100, 1000);
    m.pointerMove(1, 140, 100, 1050);
    m.pointerUp(1, 1100);
    expect(m.take()).toEqual([]);
    m.pointerDown(1, 100, 100, 2000);
    expect(m.hasFinger(1)).toBe(true);
    m.cancel();
    expect(m.hasFinger(1)).toBe(false);
    m.pointerUp(1, 2050);
    expect(m.take()).toEqual([]);
    expect(m.activeFingers).toBe(0);
  });

  it("the guide names every gesture once and promises nothing the machine does not do", () => {
    const phones = DIRECT_GESTURES.map((g) => g.phone);
    expect(new Set(phones).size).toBe(phones.length);
    expect(DIRECT_GESTURES.some((g) => /pinch/i.test(g.phone))).toBe(true);
    expect(DIRECT_GESTURES.find((g) => /pinch/i.test(g.phone))!.pc).toMatch(/nothing is sent/i);
    expect(DIRECT_GESTURES.some((g) => /drag/i.test(g.phone) && !/touchpad/i.test(g.phone))).toBe(false); // direct-mode drag is Phase 2
  });
});

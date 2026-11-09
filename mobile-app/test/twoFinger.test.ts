/**
 * TwoFingerClassifier (design screen-view-2a §4.5): the direct machine's pinch-or-scroll rule moved into a module of its
 * own, shared with the touchpad machine. Twins of the cases in directGestures.test.ts: a spread change over 10 %
 * before the midpoint moved 8 px is a pinch; the midpoint moving first is a scroll; the decision waits for a completed
 * PAIR of moves (one finger's half of a two-finger scroll is not judged), or for one finger moving again while the other
 * stays (an anchored thumb); once decided it stays decided; reset forgets.
 */
import { describe, expect, it } from "vitest";

import { spread, TwoFingerClassifier, type Pt } from "../src/lib/twoFinger.ts";

const OPTS = { pinchRatio: 0.1, scrollStartPx: 8 };
const p = (x: number, y: number): Pt => ({ x, y });

describe("TwoFingerClassifier", () => {
  it("a spread change over 10 % before the midpoint moves 8 px is a pinch", () => {
    const c = new TwoFingerClassifier(OPTS);
    c.start(p(100, 100), p(200, 100));
    expect(c.startDistance).toBe(100);
    expect(c.startMid).toEqual({ x: 150, y: 100 });
    expect(c.move(1, p(92, 100), p(200, 100))).toBe("undecided"); // half of the pair
    expect(c.move(2, p(92, 100), p(207, 100))).toBe("pinch"); // 115 px: +15 %
    expect(c.decided).toBe("pinch");
  });

  it("the midpoint moving first is a scroll; under 8 px it is still undecided", () => {
    const c = new TwoFingerClassifier(OPTS);
    c.start(p(100, 100), p(140, 100));
    expect(c.move(1, p(100, 106), p(140, 100))).toBe("undecided");
    expect(c.move(2, p(100, 106), p(140, 106))).toBe("undecided"); // 6 px
    expect(c.move(1, p(100, 136), p(140, 106))).toBe("undecided"); // half of the next pair
    expect(c.move(2, p(100, 136), p(140, 136))).toBe("scroll");
  });

  it("the pair guard: one finger's half of a fast scroll is never judged as a spread change", () => {
    const c = new TwoFingerClassifier(OPTS);
    c.start(p(100, 100), p(140, 100));
    // finger 1 alone moved 30 px down: judged now, the spread would look 25 % larger (a pinch)
    expect(c.move(1, p(100, 130), p(140, 100))).toBe("undecided");
    expect(c.move(2, p(100, 130), p(140, 130))).toBe("scroll");
  });

  it("an anchored thumb: the moving finger reporting again decides with the other still", () => {
    const c = new TwoFingerClassifier(OPTS);
    c.start(p(100, 100), p(200, 100));
    expect(c.move(2, p(100, 100), p(215, 100))).toBe("undecided");
    expect(c.move(2, p(100, 100), p(230, 100))).toBe("pinch");
  });

  it("once decided it stays decided; reset and start forget the decision and the pair guard", () => {
    const c = new TwoFingerClassifier(OPTS);
    c.start(p(0, 0), p(50, 0));
    c.move(1, p(0, 20), p(50, 0));
    expect(c.move(2, p(0, 20), p(50, 20))).toBe("scroll");
    expect(c.move(1, p(-100, 20), p(50, 20))).toBe("scroll"); // a later spread change does not turn it into a pinch
    c.reset();
    expect(c.decided).toBeNull();
    expect(c.move(1, p(0, 20), p(50, 20))).toBe("undecided");
    c.start(p(0, 0), p(50, 0));
    expect(c.move(2, p(0, 0), p(50, 0))).toBe("undecided"); // the pending finger from before the start is forgotten
  });

  it("spread() is the distance (at least 1 px) and the midpoint", () => {
    expect(spread(p(0, 0), p(3, 4))).toEqual({ distance: 5, mid: { x: 1.5, y: 2 } });
    expect(spread(p(5, 5), p(5, 5)).distance).toBe(1);
  });
});

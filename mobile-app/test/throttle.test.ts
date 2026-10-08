import { describe, expect, it } from "vitest";

import { createThrottledEmitter, type ThrottleTimers } from "../src/lib/throttle.ts";

function fakeTimers() {
  let now = 0;
  const queue: Array<{ at: number; fn: () => void; id: number }> = [];
  let seq = 0;
  const timers: ThrottleTimers = {
    now: () => now,
    setTimeout(fn, ms) {
      const id = ++seq;
      queue.push({ at: now + ms, fn, id });
      return id;
    },
    clearTimeout(h) {
      const i = queue.findIndex((q) => q.id === h);
      if (i >= 0) queue.splice(i, 1);
    },
  };
  const advance = (ms: number) => {
    const target = now + ms;
    for (;;) {
      queue.sort((a, b) => a.at - b.at);
      const next = queue[0];
      if (!next || next.at > target) break;
      now = next.at;
      queue.shift();
      next.fn();
    }
    now = target;
  };
  return { timers, advance };
}

describe("slider throttle (plans.json: one command per 250 ms plus the final value)", () => {
  it("emits the first value immediately, then at most one per interval, then the final value on flush", () => {
    const { timers, advance } = fakeTimers();
    const out: number[] = [];
    const t = createThrottledEmitter<number>((v) => out.push(v), 250, timers);
    t.push(10);
    expect(out).toEqual([10]);
    for (let v = 11; v <= 30; v++) {
      t.push(v);
      advance(10); // 20 pushes over 200 ms
    }
    expect(out.length).toBeLessThanOrEqual(2);
    advance(300);
    expect(out[out.length - 1]).toBe(30);
    const before = out.length;
    t.push(31);
    t.flush();
    expect(out[out.length - 1]).toBe(31);
    expect(out.length).toBe(before + 1);
    // flushing again does not re-emit the same final value
    t.flush();
    expect(out.length).toBe(before + 1);
  });
  it("a 2 s burst of 40 values yields at most 9 commands and ends with the last value", () => {
    const { timers, advance } = fakeTimers();
    const out: number[] = [];
    const t = createThrottledEmitter<number>((v) => out.push(v), 250, timers);
    for (let i = 0; i < 40; i++) {
      t.push(i);
      advance(50);
    }
    t.flush();
    expect(out.length).toBeLessThanOrEqual(9);
    expect(out[out.length - 1]).toBe(39);
  });
  it("cancel drops the pending value", () => {
    const { timers, advance } = fakeTimers();
    const out: number[] = [];
    const t = createThrottledEmitter<number>((v) => out.push(v), 250, timers);
    t.push(1);
    t.push(2);
    t.cancel();
    advance(1000);
    expect(out).toEqual([1]);
  });
});

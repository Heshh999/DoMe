/** Deterministic, manually advanced timers for the input and Screen view clients (test-only). */
import { flush } from "./harness.ts";

export class FakeTimers {
  now = Date.now();
  private seq = 0;
  private timeouts = new Map<number, { at: number; fn: () => void }>();
  private intervals = new Map<number, { next: number; every: number; fn: () => void }>();
  setTimeout = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.timeouts.set(id, { at: this.now + ms, fn });
    return id;
  };
  clearTimeout = (h: unknown): void => {
    this.timeouts.delete(h as number);
  };
  setInterval = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.intervals.set(id, { next: this.now + ms, every: ms, fn });
    return id;
  };
  clearInterval = (h: unknown): void => {
    this.intervals.delete(h as number);
  };
  get pending(): number {
    return this.timeouts.size + this.intervals.size;
  }
  /** Advance the clock, firing everything due in order; microtasks run between firings. */
  async advance(ms: number): Promise<void> {
    const target = this.now + ms;
    for (;;) {
      let nextAt = Infinity;
      for (const t of this.timeouts.values()) nextAt = Math.min(nextAt, t.at);
      for (const i of this.intervals.values()) nextAt = Math.min(nextAt, i.next);
      if (nextAt > target) break;
      this.now = nextAt;
      for (const [id, t] of [...this.timeouts]) {
        if (t.at <= this.now) {
          this.timeouts.delete(id);
          t.fn();
        }
      }
      for (const i of this.intervals.values()) {
        if (i.next <= this.now) {
          i.next += i.every;
          i.fn();
        }
      }
      await flush(3);
    }
    this.now = target;
  }
}

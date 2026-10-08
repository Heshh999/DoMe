/**
 * Event-driven waiting. Background tabs in Chrome throttle timers (chained timers can be limited
 * to once per minute), so every wait is driven by DOM events / mutations with a single one-shot
 * deadline timer and never a polling chain.
 */
import type { Timers } from "../shared/throttle.ts";
import { realTimers } from "../shared/throttle.ts";

export interface WaitOptions {
  check: () => boolean;
  listen?: Array<{ target: EventTarget | null; events: readonly string[] }>;
  observe?: { node: Node | null; options: MutationObserverInit };
  timeoutMs: number;
  timers?: Timers;
  MutationObserverCtor?: typeof MutationObserver;
}

/** Resolves true as soon as `check()` holds (checked now, on every event, on every mutation batch); false at the deadline. */
export function waitFor(opts: WaitOptions): Promise<boolean> {
  if (opts.check()) return Promise.resolve(true);
  const timers = opts.timers ?? realTimers;
  return new Promise<boolean>((resolve) => {
    const cleanups: Array<() => void> = [];
    let settled = false;
    const finish = (value: boolean): void => {
      if (settled) return;
      settled = true;
      for (const c of cleanups) {
        try {
          c();
        } catch {
          // cleanup must never throw
        }
      }
      resolve(value);
    };
    const probe = (): void => {
      if (settled) return;
      let ok = false;
      try {
        ok = opts.check();
      } catch {
        ok = false;
      }
      if (ok) finish(true);
    };
    for (const { target, events } of opts.listen ?? []) {
      if (!target) continue;
      for (const ev of events) {
        target.addEventListener(ev, probe);
        cleanups.push(() => target.removeEventListener(ev, probe));
      }
    }
    const Observer = opts.MutationObserverCtor ?? (typeof MutationObserver === "function" ? MutationObserver : undefined);
    if (opts.observe?.node && Observer) {
      const mo = new Observer(probe);
      mo.observe(opts.observe.node, opts.observe.options);
      cleanups.push(() => mo.disconnect());
    }
    const handle = timers.setTimeout(() => finish(false), Math.max(0, opts.timeoutMs));
    cleanups.push(() => timers.clearTimeout(handle));
    probe();
  });
}

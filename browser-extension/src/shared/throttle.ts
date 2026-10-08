/** Timer helpers; `timers` is injectable so the same code runs under fake timers in tests. */
export interface Timers {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
  now: () => number;
}

export const realTimers: Timers = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (h) => globalThis.clearTimeout(h as ReturnType<typeof setTimeout>),
  now: () => Date.now(),
};

export interface Throttled {
  trigger(): void;
  cancel(): void;
  pending(): boolean;
}

/**
 * Leading + trailing throttle: the first trigger fires immediately, later triggers within
 * `minIntervalMs` are coalesced into one trailing call. Never chains timers (background-tab
 * timer throttling in Chrome applies to chained timers).
 */
export function createThrottle(minIntervalMs: number, fn: () => void, timers: Timers = realTimers): Throttled {
  let last = -Infinity;
  let handle: unknown = null;
  const fire = (): void => {
    handle = null;
    last = timers.now();
    fn();
  };
  return {
    trigger() {
      if (handle !== null) return;
      const elapsed = timers.now() - last;
      if (elapsed >= minIntervalMs) fire();
      else handle = timers.setTimeout(fire, minIntervalMs - elapsed);
    },
    cancel() {
      if (handle !== null) timers.clearTimeout(handle);
      handle = null;
    },
    pending: () => handle !== null,
  };
}

/** Trailing debounce: fires once `delayMs` after the last trigger. */
export function createDebounce(delayMs: number, fn: () => void, timers: Timers = realTimers): Throttled {
  let handle: unknown = null;
  return {
    trigger() {
      if (handle !== null) timers.clearTimeout(handle);
      handle = timers.setTimeout(() => {
        handle = null;
        fn();
      }, delayMs);
    },
    cancel() {
      if (handle !== null) timers.clearTimeout(handle);
      handle = null;
    },
    pending: () => handle !== null,
  };
}

/** Promise that rejects with `error` after `ms` unless `promise` settles first. */
export function withTimeout<T>(promise: Promise<T>, ms: number, error: () => Error, timers: Timers = realTimers): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const handle = timers.setTimeout(() => reject(error()), ms);
    promise.then(
      (v) => {
        timers.clearTimeout(handle);
        resolve(v);
      },
      (e: unknown) => {
        timers.clearTimeout(handle);
        reject(e instanceof Error ? e : new Error(String(e)));
      },
    );
  });
}

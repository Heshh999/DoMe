/**
 * Slider throttle: emit at most one value per `intervalMs`, and always emit the latest value on
 * `flush()` (pointer release). Matches plans.json: "the PWA throttles sliders to one command per
 * 250 ms plus the final value on release".
 */
export interface ThrottledEmitter<T> {
  push(value: T): void;
  flush(): void;
  cancel(): void;
}

export interface ThrottleTimers {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const defaultTimers: ThrottleTimers = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export function createThrottledEmitter<T>(emit: (value: T) => void, intervalMs = 250, timers: ThrottleTimers = defaultTimers): ThrottledEmitter<T> {
  let lastEmit = -Infinity;
  let pending: { value: T } | null = null;
  let timer: unknown = null;
  let lastEmitted: { value: T } | null = null;

  const fire = (value: T) => {
    lastEmit = timers.now();
    lastEmitted = { value };
    emit(value);
  };

  return {
    push(value) {
      const elapsed = timers.now() - lastEmit;
      if (elapsed >= intervalMs && timer === null) {
        fire(value);
        return;
      }
      pending = { value };
      if (timer === null) {
        timer = timers.setTimeout(
          () => {
            timer = null;
            if (pending) {
              const v = pending.value;
              pending = null;
              fire(v);
            }
          },
          Math.max(0, intervalMs - elapsed),
        );
      }
    },
    flush() {
      if (timer !== null) {
        timers.clearTimeout(timer);
        timer = null;
      }
      if (pending) {
        const v = pending.value;
        pending = null;
        if (!lastEmitted || lastEmitted.value !== v) fire(v);
      }
    },
    cancel() {
      if (timer !== null) timers.clearTimeout(timer);
      timer = null;
      pending = null;
    },
  };
}

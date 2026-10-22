/**
 * A screen wake lock while a Screen view picture is shown (docs/design/screen-view.md §2.2 step 3, §7), where the
 * browser offers one (the home-screen app gets it from iOS 18.4 [research]). It is a convenience, never a requirement:
 * no API, a refusal or a lock the browser dropped on its own (it always does when the page is hidden) just means the
 * phone may dim as usual. The holder re-requests when the page is visible again and the picture is still wanted.
 */
import { errorSummary, log } from "./log.ts";

interface SentinelLike {
  released?: boolean;
  release(): Promise<void>;
}

interface WakeLockApi {
  request(type: "screen"): Promise<SentinelLike>;
}

export function wakeLockApi(nav: unknown = typeof navigator !== "undefined" ? navigator : undefined): WakeLockApi | null {
  const api = (nav as { wakeLock?: WakeLockApi } | undefined)?.wakeLock;
  return api && typeof api.request === "function" ? api : null;
}

/** Holds at most one screen wake lock; `want(true)` acquires (again after a drop), `want(false)` releases. */
export class WakeLockHolder {
  private sentinel: SentinelLike | null = null;
  private wanted = false;
  private pending: Promise<void> | null = null;

  constructor(private readonly api: WakeLockApi | null = wakeLockApi()) {}

  get held(): boolean {
    return this.sentinel !== null && this.sentinel.released !== true;
  }

  want(on: boolean): Promise<void> {
    this.wanted = on;
    return on ? this.acquire() : this.release();
  }

  /** The page became visible again: a lock the browser released while hidden is requested anew. */
  revisit(): Promise<void> {
    return this.wanted ? this.acquire() : Promise.resolve();
  }

  private acquire(): Promise<void> {
    if (!this.api || this.held) return Promise.resolve();
    if (this.pending) return this.pending;
    this.pending = this.api
      .request("screen")
      .then(async (s) => {
        if (!this.wanted) {
          await s.release().catch(() => undefined);
          return;
        }
        this.sentinel = s;
      })
      .catch((e: unknown) => log.info("screen.wake_lock_refused", errorSummary(e)))
      .finally(() => {
        this.pending = null;
      });
    return this.pending;
  }

  private async release(): Promise<void> {
    const s = this.sentinel;
    this.sentinel = null;
    if (s && s.released !== true) await s.release().catch(() => undefined);
  }
}

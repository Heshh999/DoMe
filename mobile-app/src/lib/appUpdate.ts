/**
 * Keeping an open app current (DECISIONS 92). On the founder's iPhone (2026-11-09) the app kept running the bundle from
 * before a staging deploy: closing and reopening Safari or the home-screen app brings back the page that is already
 * loaded, so the service worker never looked for a newer version and a bug fixed on the server side lived on.
 *
 * When to look (`check`: the service-worker registration's `update()`, which asks the server for `sw.js`):
 *   - once when the app starts (pwa.ts, as soon as the registration is known);
 *   - each time the app comes back to the front (`visibilitychange` to visible; `pageshow` with `persisted`, iOS's
 *     back-forward cache), at most once a minute;
 *   - every 30 minutes while the app is in front (a tick while it is hidden does nothing; the 30 minutes count again
 *     from each return, and a return event iOS might not send never stops the timer).
 *
 * When to reload: the newer worker installs and takes control by itself (`autoUpdate`: skipWaiting + clientsClaim),
 * which changes nothing for the page already running; only a reload loads the newer bundle, and the app decides when
 * (`onNewVersion`: vite-plugin-pwa's `onNeedReload`, and the browser's `controllerchange` in pwa.ts). At a safe moment it reloads at once, without asking. While
 * something is busy (store/busy.ts: Screen view, the touchpad, pairing, a payment return, a PC confirmation, the
 * Assistant), it says so in a small notice and reloads when the busy state ends, after a short pause so the session's
 * own stop messages go out first. "Update now" on the notice reloads after the person confirmed it ends what runs.
 */
import type { BusyKind } from "../store/busy.ts";
import { log } from "./log.ts";

/** A return to the front checks at most this often (a phone switching apps back and forth asks once). */
export const FOREGROUND_CHECK_GAP_MS = 60_000;
/** While in front, the app checks this often. */
export const VISIBLE_CHECK_INTERVAL_MS = 30 * 60_000;
/** After the busy state ends, this long without it before the reload (a Stop's signed message, a page change). */
export const AFTER_BUSY_DELAY_MS = 1_500;

export type CheckReason = "start" | "foreground" | "timer";

/** What the notice shows. */
export interface AppUpdateView {
  /** A newer version controls the app; the page has not reloaded into it yet. */
  ready: boolean;
  /** What the reload waits for; null when nothing does (it is happening). */
  waitingFor: BusyKind | null;
}

export const NO_UPDATE: AppUpdateView = { ready: false, waitingFor: null };

export interface UpdateTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

/** The browser's timers, looked up at each call (so a test's fake timers apply). */
const browserTimers: UpdateTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

export interface AppUpdaterDeps {
  /** Ask for a newer version (the registration's `update()`). May reject (offline). */
  check(): Promise<unknown>;
  /** Load the newer version (`location.reload()`: the newer worker already controls the page). */
  reload(): void;
  /** What keeps the app from reloading now, or null at a safe moment (store/busy.ts `currentBusy`). */
  busy(): BusyKind | null;
  /** Calls `listener` whenever the busy state may have changed (store/busy.ts `subscribeBusy`). */
  subscribeBusy(listener: () => void): () => void;
  /** The notice's state. */
  onChange(view: AppUpdateView): void;
  now?: () => number;
  timers?: UpdateTimers;
  doc?: Pick<Document, "visibilityState" | "addEventListener" | "removeEventListener">;
  win?: Pick<Window, "addEventListener" | "removeEventListener">;
}

export class AppUpdater {
  private readonly deps: AppUpdaterDeps;
  private readonly timers: UpdateTimers;
  private readonly now: () => number;
  private lastCheck: number | null = null;
  private interval: unknown = null;
  private afterBusy: unknown = null;
  private detach: Array<() => void> = [];
  private ready = false;
  private waitingFor: BusyKind | null = null;
  private stopWatchingBusy: (() => void) | null = null;
  private reloaded = false;

  constructor(deps: AppUpdaterDeps) {
    this.deps = deps;
    this.timers = deps.timers ?? browserTimers;
    this.now = deps.now ?? (() => Date.now());
  }

  private get doc(): NonNullable<AppUpdaterDeps["doc"]> {
    return this.deps.doc ?? document;
  }

  /**
   * Listen for returns to the front and start the 30-minute timer (its ticks check only while in front). The start
   * check itself is `check("start")`, once the registration is known.
   */
  start(): void {
    if (this.detach.length > 0) return;
    const win = this.deps.win ?? window;
    const onVisibility = () => {
      if (this.doc.visibilityState === "visible") this.inFront();
    };
    const onPageShow = (e: Event) => {
      if ((e as PageTransitionEvent).persisted === true) this.inFront();
    };
    this.doc.addEventListener("visibilitychange", onVisibility);
    win.addEventListener("pageshow", onPageShow);
    this.detach.push(
      () => this.doc.removeEventListener("visibilitychange", onVisibility),
      () => win.removeEventListener("pageshow", onPageShow),
    );
    this.arm();
  }

  stop(): void {
    for (const d of this.detach) d();
    this.detach = [];
    this.disarm();
    this.stopWatching();
  }

  /** Ask for a newer version now (nothing once one is waiting to be loaded). Never throws. */
  check(reason: CheckReason): void {
    if (this.ready) return;
    this.lastCheck = this.now();
    log.info("pwa.update_check", { reason });
    let pending: Promise<unknown>;
    try {
      pending = this.deps.check();
    } catch (e) {
      pending = Promise.reject(e);
    }
    pending.catch((e: unknown) => log.info("pwa.update_check_failed", { reason, name: e instanceof Error ? e.name : "error" }));
  }

  /**
   * A newer version took control of the app (vite-plugin-pwa `onNeedReload`, or the browser's `controllerchange`):
   * reload now, or when nothing is busy. One arrival usually comes through both; the second changes nothing (it must
   * not cut short the pause after a busy state that ended between the two).
   */
  onNewVersion(): void {
    if (this.reloaded || this.ready) return;
    log.info("pwa.update_ready", { busy: this.deps.busy() ?? "none" });
    this.ready = true;
    this.disarm();
    this.settle(true);
  }

  /** "Update now", after the person confirmed it ends what is running. */
  applyNow(): void {
    if (!this.ready) return;
    this.apply("now");
  }

  get view(): AppUpdateView {
    return { ready: this.ready, waitingFor: this.waitingFor };
  }

  // ---- internals ----------------------------------------------------------------------------------

  /** Back in front: one check unless one ran within the last minute; the 30 minutes count again from now. */
  private inFront(): void {
    if (this.lastCheck === null || this.now() - this.lastCheck >= FOREGROUND_CHECK_GAP_MS) this.check("foreground");
    this.disarm();
    this.arm();
  }

  private arm(): void {
    if (this.interval !== null || this.ready) return;
    this.interval = this.timers.setInterval(() => {
      if (this.doc.visibilityState === "visible") this.check("timer");
    }, VISIBLE_CHECK_INTERVAL_MS);
  }

  private disarm(): void {
    if (this.interval !== null) this.timers.clearInterval(this.interval);
    this.interval = null;
  }

  /** `arrived`: the newer version has just taken control (a safe moment reloads at once, without a pause). */
  private settle(arrived = false): void {
    if (this.reloaded) return;
    const busy = this.deps.busy();
    if (busy === null) {
      if (arrived) {
        this.apply("idle");
        return;
      }
      // the busy state just ended: reload once it has stayed ended for a moment
      if (this.afterBusy === null) {
        this.afterBusy = this.timers.setTimeout(() => {
          this.afterBusy = null;
          if (this.deps.busy() === null) this.apply("busy_ended");
          else this.settle();
        }, AFTER_BUSY_DELAY_MS);
      }
      return;
    }
    if (this.afterBusy !== null) {
      this.timers.clearTimeout(this.afterBusy);
      this.afterBusy = null;
    }
    if (busy !== this.waitingFor) {
      this.waitingFor = busy;
      log.info("pwa.update_waits", { busy });
      this.deps.onChange(this.view);
    }
    this.stopWatchingBusy ??= this.deps.subscribeBusy(() => this.settle());
  }

  private apply(how: "idle" | "busy_ended" | "now"): void {
    if (this.reloaded) return;
    this.reloaded = true;
    this.stopWatching();
    this.disarm();
    this.waitingFor = null;
    this.deps.onChange(this.view);
    log.info("pwa.update_applied", { how });
    this.deps.reload();
  }

  private stopWatching(): void {
    this.stopWatchingBusy?.();
    this.stopWatchingBusy = null;
    if (this.afterBusy !== null) this.timers.clearTimeout(this.afterBusy);
    this.afterBusy = null;
  }
}

/** What the notice says, per busy state: what it waits for, and what "Update now" ends. */
export const UPDATE_COPY = {
  ready: "A newer DoMe is ready.",
  when: {
    screen: "It will update when you leave Screen view.",
    input: "It will update when you leave the touchpad.",
    pairing: "It will update when you leave pairing.",
    checkout: "It will update when you leave the Billing page.",
    confirmation: "It will update when you have answered the confirmation.",
    assistant: "It will update when DoMe has finished your request.",
  } satisfies Record<BusyKind, string>,
  ends: {
    screen: "Updating now ends Screen view.",
    input: "Updating now ends the touchpad session.",
    pairing: "Updating now stops pairing; you would start it again.",
    checkout: "Updating now leaves this page. Your payment is not affected.",
    confirmation: "Updating now closes the confirmation without an answer, so the PC will not do it.",
    assistant: "Updating now stops your request. Anything already sent to the PC still happens.",
  } satisfies Record<BusyKind, string>,
  updateNow: "Update now",
  confirm: "End it and update",
  notNow: "Not now",
  hide: "Hide",
} as const;

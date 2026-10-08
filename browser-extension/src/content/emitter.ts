/**
 * Emits `attached` / `player_state` / `detached` messages to the service worker, re-acquiring the
 * video element across YouTube's SPA navigations. At most one `player_state` per 500 ms.
 */
import type { ContentToBackground, PlayerSnapshot } from "../shared/messages.ts";
import { CS_KIND } from "../shared/messages.ts";
import { createThrottle, realTimers, type Timers } from "../shared/throttle.ts";
import { findPlayer } from "./detect.ts";

export interface EmitterDeps {
  doc: Document;
  win: Window;
  token: string;
  snapshot: () => PlayerSnapshot;
  send: (message: ContentToBackground) => Promise<void>;
  timers?: Timers;
  minIntervalMs?: number;
}

const VIDEO_EVENTS = ["play", "pause", "playing", "seeked", "volumechange", "ended", "durationchange", "loadedmetadata", "emptied"] as const;
const NAV_EVENTS = ["yt-navigate-finish", "yt-page-data-updated"] as const;

export interface Emitter {
  start(): void;
  stop(): void;
  /** For tests: force an immediate (throttled) state emission. */
  notify(): void;
}

export function createEmitter(deps: EmitterDeps): Emitter {
  const { doc, win, token } = deps;
  const timers = deps.timers ?? realTimers;
  let dead = false;
  let video: HTMLVideoElement | null = null;
  let observer: MutationObserver | null = null;
  let lastTimeUpdate = -Infinity;

  const deliver = async (message: ContentToBackground): Promise<void> => {
    if (dead) return;
    try {
      await deps.send(message);
    } catch {
      // The worker is unreachable or the extension was reloaded: this attachment is dead.
      dead = true;
    }
  };

  const throttle = createThrottle(deps.minIntervalMs ?? 500, () => void deliver({ kind: CS_KIND, type: "player_state", token, state: deps.snapshot() }), timers);

  const onVideoEvent = (): void => throttle.trigger();
  const onTimeUpdate = (): void => {
    // timeupdate fires ~4×/s while playing; keep position fresh at most every 5 s.
    const now = timers.now();
    if (now - lastTimeUpdate < 5000) return;
    lastTimeUpdate = now;
    throttle.trigger();
  };

  function bindVideo(next: HTMLVideoElement | null): void {
    if (next === video) return;
    if (video) {
      for (const ev of VIDEO_EVENTS) video.removeEventListener(ev, onVideoEvent);
      video.removeEventListener("timeupdate", onTimeUpdate);
    }
    video = next;
    if (video) {
      for (const ev of VIDEO_EVENTS) video.addEventListener(ev, onVideoEvent);
      video.addEventListener("timeupdate", onTimeUpdate);
    }
  }

  function reacquire(): void {
    const dom = findPlayer(doc);
    bindVideo(dom.video);
    const container = dom.player ?? doc.querySelector("#movie_player") ?? doc.querySelector("ytd-player");
    const w = win as Window & typeof globalThis;
    if (container && typeof w.MutationObserver === "function") {
      observer?.disconnect();
      const mo = new w.MutationObserver(() => {
        const current = findPlayer(doc).video;
        if (current !== video) {
          bindVideo(current);
          throttle.trigger();
        }
      });
      mo.observe(container, { childList: true, subtree: true });
      observer = mo;
    }
    throttle.trigger();
  }

  const onNavigate = (): void => reacquire();
  const onFullscreen = (): void => throttle.trigger();
  const onPageHide = (): void => {
    void deliver({ kind: CS_KIND, type: "detached", token });
  };

  return {
    start() {
      void deliver({ kind: CS_KIND, type: "attached", token, state: deps.snapshot() });
      for (const ev of NAV_EVENTS) doc.addEventListener(ev, onNavigate);
      doc.addEventListener("fullscreenchange", onFullscreen);
      win.addEventListener("pagehide", onPageHide);
      reacquire();
      if (!video) {
        // Player not in the DOM yet (document_idle can precede YouTube's hydration): one retry.
        timers.setTimeout(() => {
          if (!video) reacquire();
        }, 1500);
      }
    },
    stop() {
      dead = true;
      throttle.cancel();
      observer?.disconnect();
      observer = null;
      bindVideo(null);
      for (const ev of NAV_EVENTS) doc.removeEventListener(ev, onNavigate);
      doc.removeEventListener("fullscreenchange", onFullscreen);
      win.removeEventListener("pagehide", onPageHide);
    },
    notify: () => throttle.trigger(),
  };
}

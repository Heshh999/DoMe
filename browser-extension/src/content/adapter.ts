/**
 * YouTube player adapter: executes bridge ops against the page through the HTMLVideoElement and
 * YouTube's own control buttons, with the success criteria from docs/design/browser-extension.md.
 * Nothing is inserted into the page and no page JavaScript is called.
 */
import { OpError, toErrorObject } from "../shared/errors.ts";
import type { ContentReply, OpArgs, PlayerSnapshot, TabOp } from "../shared/messages.ts";
import type { Timers } from "../shared/throttle.ts";
import { realTimers } from "../shared/throttle.ts";
import { TOKEN_RE } from "../shared/token.ts";
import { buttonUsable, currentVideoId, findPlayer, isTheater, nextButton, prevButton, readSnapshot, type PlayerDom } from "./detect.ts";
import { waitFor } from "./wait.ts";

export interface AdapterDeps {
  doc: Document;
  win: Window;
  token: string;
  timers?: Timers;
}

const NAV_EVENTS = ["yt-navigate-finish", "yt-page-data-updated", "yt-navigate-start", "popstate"] as const;
const VIDEO_SWAP_EVENTS = ["loadstart", "emptied", "loadedmetadata", "playing", "timeupdate", "durationchange"] as const;
/** Ops allowed while an advertisement is showing (no ad bypass: nothing here skips the ad). */
const AD_SAFE_OPS: ReadonlySet<TabOp> = new Set(["get_state", "set_paused", "set_muted", "set_volume"]);
const SHORTS_UNSUPPORTED: ReadonlySet<TabOp> = new Set(["next", "previous", "set_theater"]);
const SEEK_OPS: ReadonlySet<TabOp> = new Set(["seek_relative", "seek_to"]);
const MUTATING_OPS: ReadonlySet<TabOp> = new Set(["set_paused", "next", "previous", "seek_relative", "seek_to", "set_muted", "set_volume", "set_theater", "request_fullscreen"]);

export function isMutatingOp(op: TabOp): boolean {
  return MUTATING_OPS.has(op);
}

export interface PlayerAdapter {
  readonly token: string;
  snapshot(): PlayerSnapshot;
  run(op: TabOp, args: OpArgs, deadlineMs: number): Promise<ContentReply>;
}

export function createPlayerAdapter(deps: AdapterDeps): PlayerAdapter {
  const { doc, win, token } = deps;
  const timers = deps.timers ?? realTimers;

  const snapshot = (dom?: PlayerDom): PlayerSnapshot => readSnapshot(doc, win, dom);
  const ok = (state: PlayerSnapshot, previousVideoId?: string): ContentReply =>
    previousVideoId === undefined ? { ok: true, token, state } : { ok: true, token, state, previous_video_id: previousVideoId };

  function requireVideo(dom: PlayerDom): HTMLVideoElement {
    if (!dom.video) throw new OpError("ACTION_UNAVAILABLE", "No YouTube player on this page");
    return dom.video;
  }

  function guard(op: TabOp, args: OpArgs, dom: PlayerDom): void {
    if (typeof args.tab_token !== "string" || !TOKEN_RE.test(args.tab_token) || args.tab_token !== token) {
      throw new OpError("TARGET_CHANGED", "The tab was reloaded since it was selected; choose it again");
    }
    const current = currentVideoId(win);
    if (args.expected_video_id !== undefined && args.expected_video_id !== current) {
      throw new OpError("TARGET_CHANGED", "The page changed to a different video before the action ran");
    }
    if (op === "get_state") return;
    const state = snapshot(dom);
    if (state.ad_showing && !AD_SAFE_OPS.has(op)) throw new OpError("UNSUPPORTED_CONTEXT", "An advertisement is showing; only pause, mute and volume are available");
    if (state.context === "shorts" && SHORTS_UNSUPPORTED.has(op)) throw new OpError("UNSUPPORTED_CONTEXT", "Not supported on YouTube Shorts");
    if (state.context === "other") throw new OpError("UNSUPPORTED_CONTEXT", "This YouTube page has no supported player");
    if (state.is_live && SEEK_OPS.has(op)) throw new OpError("UNSUPPORTED_CONTEXT", "Seeking is not available on a live stream");
  }

  async function setPaused(dom: PlayerDom, paused: boolean): Promise<void> {
    const video = requireVideo(dom);
    if (video.paused === paused) return;
    if (paused) {
      video.pause();
    } else {
      try {
        await video.play();
      } catch (err) {
        const name = err instanceof Error ? err.name : "Error";
        throw new OpError("ACTION_UNAVAILABLE", `Playback could not start (${name})`);
      }
    }
    const reached = await waitFor({
      check: () => video.paused === paused,
      listen: [{ target: video, events: ["play", "playing", "pause"] }],
      timeoutMs: 1000,
      timers,
    });
    if (!reached) throw new OpError("ACTION_UNAVAILABLE", paused ? "The player did not pause" : "The player did not start playing");
  }

  async function transition(dom: PlayerDom, button: Element, deadlineMs: number, kind: "next" | "previous"): Promise<string | undefined> {
    const before = currentVideoId(win);
    (button as HTMLElement).click();
    const changed = await waitFor({
      check: () => {
        const now = currentVideoId(win);
        return now !== undefined && now !== before;
      },
      listen: [
        { target: doc, events: NAV_EVENTS },
        { target: win, events: ["popstate", "hashchange"] },
        { target: dom.video, events: VIDEO_SWAP_EVENTS },
      ],
      observe: { node: doc.head, options: { childList: true, subtree: true, characterData: true } },
      timeoutMs: deadlineMs,
      timers,
    });
    if (!changed) {
      throw new OpError("OUTCOME_UNKNOWN", `${kind === "next" ? "Next" : "Previous"} was clicked but no video change was observed within ${deadlineMs} ms`);
    }
    return before;
  }

  async function seek(dom: PlayerDom, target: number): Promise<void> {
    const video = requireVideo(dom);
    const max = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : Infinity;
    const clamped = Math.min(max, Math.max(0, target));
    video.currentTime = clamped;
    const reached = await waitFor({
      check: () => Math.abs(video.currentTime - clamped) < 1.5 || video.ended,
      listen: [{ target: video, events: ["seeked", "timeupdate", "ended"] }],
      timeoutMs: 2000,
      timers,
    });
    if (!reached) throw new OpError("ACTION_UNAVAILABLE", "The seek did not take effect");
  }

  async function setMuted(dom: PlayerDom, muted: boolean): Promise<void> {
    const video = requireVideo(dom);
    if (video.muted === muted) return;
    // Prefer YouTube's own button so its UI stays consistent; fall back to the element.
    const button = dom.player?.querySelector(".ytp-mute-button") ?? null;
    if (buttonUsable(button, win)) {
      button.click();
      const viaButton = await waitFor({ check: () => video.muted === muted, listen: [{ target: video, events: ["volumechange"] }], timeoutMs: 800, timers });
      if (viaButton) return;
    }
    video.muted = muted;
    const reached = await waitFor({ check: () => video.muted === muted, listen: [{ target: video, events: ["volumechange"] }], timeoutMs: 500, timers });
    if (!reached) throw new OpError("ACTION_UNAVAILABLE", muted ? "The player did not mute" : "The player did not unmute");
  }

  async function setVolume(dom: PlayerDom, value: number): Promise<void> {
    const video = requireVideo(dom);
    if (!Number.isInteger(value) || value < 0 || value > 100) throw new OpError("INVALID_PARAMETERS", "value must be an integer 0-100");
    const target = value / 100;
    if (Math.abs(video.volume - target) < 0.005) return;
    video.volume = target;
    const reached = await waitFor({ check: () => Math.abs(video.volume - target) < 0.01, listen: [{ target: video, events: ["volumechange"] }], timeoutMs: 800, timers });
    if (!reached) throw new OpError("ACTION_UNAVAILABLE", "The player volume did not change");
  }

  async function setTheater(dom: PlayerDom, enabled: boolean): Promise<void> {
    requireVideo(dom);
    const flexy = doc.querySelector("ytd-watch-flexy");
    if (!flexy) throw new OpError("UNSUPPORTED_CONTEXT", "Theater mode is only available on watch pages");
    if (isTheater(doc) === enabled) return;
    const button = dom.player?.querySelector(".ytp-size-button") ?? null;
    if (!buttonUsable(button, win)) throw new OpError("ACTION_UNAVAILABLE", "The theater-mode button is not available");
    button.click();
    const reached = await waitFor({
      check: () => isTheater(doc) === enabled,
      observe: { node: flexy, options: { attributes: true, attributeFilter: ["theater"] } },
      listen: [{ target: doc, events: ["yt-set-theater-mode-enabled", "yt-navigate-finish"] }],
      timeoutMs: 1500,
      timers,
    });
    if (!reached) throw new OpError("ACTION_UNAVAILABLE", "Theater mode did not change");
  }

  async function requestFullscreen(dom: PlayerDom): Promise<void> {
    if (doc.fullscreenElement) return;
    const target = (dom.player ?? dom.video) as (Element & { requestFullscreen?: () => Promise<void> }) | null;
    if (!target || typeof target.requestFullscreen !== "function") throw new OpError("ACTION_UNAVAILABLE", "Fullscreen is not available for this player");
    try {
      await target.requestFullscreen();
    } catch (err) {
      const name = err instanceof Error ? err.name : "Error";
      throw new OpError("ACTIVATION_REQUIRED", `The browser refused fullscreen without a user gesture on the PC (${name})`);
    }
    const reached = await waitFor({ check: () => !!doc.fullscreenElement, listen: [{ target: doc, events: ["fullscreenchange"] }], timeoutMs: 1000, timers });
    if (!reached) throw new OpError("ACTIVATION_REQUIRED", "Fullscreen did not start; it must be started on the PC itself");
  }

  async function execute(op: TabOp, args: OpArgs, deadlineMs: number): Promise<ContentReply> {
    const dom = findPlayer(doc);
    guard(op, args, dom);
    switch (op) {
      case "get_state":
        return ok(snapshot(dom));
      case "set_paused":
        if (typeof args.paused !== "boolean") throw new OpError("INVALID_PARAMETERS", "paused must be a boolean");
        await setPaused(dom, args.paused);
        return ok(snapshot(dom));
      case "next": {
        requireVideo(dom);
        const button = nextButton(dom, doc);
        if (!buttonUsable(button, win)) throw new OpError("NO_NEXT_VIDEO", "The player has no next video");
        const previous = await transition(dom, button, deadlineMs, "next");
        return ok(snapshot(), previous);
      }
      case "previous": {
        requireVideo(dom);
        const button = prevButton(dom, doc);
        if (!buttonUsable(button, win)) throw new OpError("NO_PREVIOUS_VIDEO", "The player has no previous video");
        const previous = await transition(dom, button, deadlineMs, "previous");
        return ok(snapshot(), previous);
      }
      case "seek_relative": {
        if (typeof args.seconds !== "number" || !Number.isFinite(args.seconds)) throw new OpError("INVALID_PARAMETERS", "seconds must be a number");
        const video = requireVideo(dom);
        await seek(dom, video.currentTime + args.seconds);
        return ok(snapshot(dom));
      }
      case "seek_to":
        if (typeof args.position_seconds !== "number" || !Number.isFinite(args.position_seconds)) throw new OpError("INVALID_PARAMETERS", "position_seconds must be a number");
        await seek(dom, args.position_seconds);
        return ok(snapshot(dom));
      case "set_muted":
        if (typeof args.muted !== "boolean") throw new OpError("INVALID_PARAMETERS", "muted must be a boolean");
        await setMuted(dom, args.muted);
        return ok(snapshot(dom));
      case "set_volume":
        if (typeof args.value !== "number") throw new OpError("INVALID_PARAMETERS", "value must be a number");
        await setVolume(dom, args.value);
        return ok(snapshot(dom));
      case "set_theater":
        if (typeof args.enabled !== "boolean") throw new OpError("INVALID_PARAMETERS", "enabled must be a boolean");
        await setTheater(dom, args.enabled);
        return ok(snapshot(dom));
      case "request_fullscreen":
        requireVideo(dom);
        await requestFullscreen(dom);
        return ok(snapshot(dom));
    }
  }

  return {
    token,
    snapshot: () => snapshot(),
    async run(op, args, deadlineMs) {
      try {
        return await execute(op, args, deadlineMs);
      } catch (err) {
        const e = toErrorObject(err);
        let state: PlayerSnapshot | undefined;
        try {
          state = snapshot();
        } catch {
          state = undefined;
        }
        return state ? { ok: false, code: e.code, message: e.message, token, state } : { ok: false, code: e.code, message: e.message, token };
      }
    },
  };
}

/**
 * YouTube player adapter: executes bridge ops against the page through the HTMLVideoElement and
 * YouTube's own control buttons and links, with the success criteria from
 * docs/design/browser-extension.md. Nothing is inserted into the page and no page JavaScript is called.
 */
import { OpError, toErrorObject } from "../shared/errors.ts";
import type { ContentOp, ContentReply, OpArgs, PlayerSnapshot } from "../shared/messages.ts";
import { MAX_SEARCH_RESULTS } from "../shared/sanitize.ts";
import type { Timers } from "../shared/throttle.ts";
import { realTimers } from "../shared/throttle.ts";
import { TOKEN_RE } from "../shared/token.ts";
import { VIDEO_ID_RE } from "../shared/youtubeUrl.ts";
import { buttonUsable, currentVideoId, documentTitle, findPlayer, headingTitle, isTheater, nextButton, prevButton, readSnapshot, readTitle, type PlayerDom } from "./detect.ts";
import { isResultsPage, readResultsPage, toSearchItem } from "./results.ts";
import { waitFor } from "./wait.ts";

export interface AdapterDeps {
  doc: Document;
  win: Window;
  token: string;
  timers?: Timers;
}

const NAV_EVENTS = ["yt-navigate-finish", "yt-page-data-updated", "yt-navigate-start", "popstate"] as const;
const VIDEO_SWAP_EVENTS = ["loadstart", "emptied", "loadedmetadata", "playing", "timeupdate", "durationchange"] as const;
/** After the URL changed, YouTube still has to deliver the new page data (title, metadata, media). */
const SETTLE_EVENTS = ["yt-navigate-finish", "yt-page-data-updated"] as const;
const SETTLE_VIDEO_EVENTS = ["loadedmetadata", "durationchange"] as const;
const SETTLE_MAX_MS = 1500;
/** Ops allowed while an advertisement is showing (no ad bypass: nothing here skips the ad). */
const AD_SAFE_OPS: ReadonlySet<ContentOp> = new Set(["get_state", "set_paused", "set_muted", "set_volume"]);
const SHORTS_UNSUPPORTED: ReadonlySet<ContentOp> = new Set(["next", "previous", "set_theater"]);
const SEEK_OPS: ReadonlySet<ContentOp> = new Set(["seek_relative", "seek_to"]);
/** Ops on a search results page; they need neither a player nor a watch page. */
const RESULTS_OPS: ReadonlySet<ContentOp> = new Set(["read_results", "open_result"]);
const MUTATING_OPS: ReadonlySet<ContentOp> = new Set(["set_paused", "next", "previous", "seek_relative", "seek_to", "set_muted", "set_volume", "set_theater", "request_fullscreen", "open_result"]);
/** Once the first results are on the page, read_results waits this long for the first page to fill (YouTube renders lists in chunks). */
const RESULTS_FILL_MS = 800;
/** open_result waits at most this long for the addressed result to be rendered. */
const RESULT_PRESENT_MS = 1500;
const LAYOUT_UNRECOGNISED = "YouTube changed its search results page; DoMe needs an update to read it";

export function isMutatingOp(op: ContentOp): boolean {
  return MUTATING_OPS.has(op);
}

/**
 * The DOM error name of a rejected media/fullscreen promise ("NotAllowedError", "AbortError", …). Read from the
 * object rather than through `instanceof Error`, which a DOMException created in another realm (the page's) fails.
 */
function domErrorName(err: unknown): string {
  const name = typeof err === "object" && err !== null ? (err as { name?: unknown }).name : undefined;
  return typeof name === "string" && /^[A-Za-z]{1,64}$/.test(name) ? name : "Error";
}

export interface PlayerAdapter {
  readonly token: string;
  snapshot(): PlayerSnapshot;
  run(op: ContentOp, args: OpArgs, deadlineMs: number): Promise<ContentReply>;
}

export function createPlayerAdapter(deps: AdapterDeps): PlayerAdapter {
  const { doc, win, token } = deps;
  const timers = deps.timers ?? realTimers;

  /**
   * The document title last seen on a page without a player (results, channel, history). Right after
   * an in-site navigation from such a page to a video, YouTube can still show the old document title,
   * and on a results page that is the search query (rules.search_queries); a snapshot never reports it
   * as the video's title. A title taken from the watch page's own heading is the video's, whatever it
   * says.
   */
  let playerlessTitle: string | undefined;
  const snapshot = (dom?: PlayerDom): PlayerSnapshot => {
    const state = readSnapshot(doc, win, dom);
    if (state.context === "other") playerlessTitle = documentTitle(doc);
    else if (state.title !== undefined && state.title === playerlessTitle && headingTitle(doc) === undefined) delete state.title;
    return state;
  };
  const ok = (state: PlayerSnapshot, previousVideoId?: string): ContentReply =>
    previousVideoId === undefined ? { ok: true, token, state } : { ok: true, token, state, previous_video_id: previousVideoId };

  function requireVideo(dom: PlayerDom): HTMLVideoElement {
    if (!dom.video) throw new OpError("ACTION_UNAVAILABLE", "No YouTube player on this page");
    return dom.video;
  }

  function guard(op: ContentOp, args: OpArgs, dom: PlayerDom): void {
    if (typeof args.tab_token !== "string" || !TOKEN_RE.test(args.tab_token) || args.tab_token !== token) {
      throw new OpError("TARGET_CHANGED", "The tab was reloaded since it was selected; choose it again");
    }
    const current = currentVideoId(win);
    if (args.expected_video_id !== undefined && args.expected_video_id !== current) {
      throw new OpError("TARGET_CHANGED", "The page changed to a different video before the action ran");
    }
    if (RESULTS_OPS.has(op)) {
      if (!isResultsPage(win)) throw new OpError("UNSUPPORTED_CONTEXT", "This tab is not showing YouTube search results");
      return;
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
        const name = domErrorName(err);
        // Autoplay policy: without a recent click or key press on the PC the browser refuses play() (KNOWN_ISSUES K7, D32).
        if (name === "NotAllowedError") throw new OpError("ACTIVATION_REQUIRED", "The browser's autoplay rules blocked starting the video without a click on the PC (NotAllowedError); press Play on the PC or click the video with the touchpad");
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

  /**
   * Run `act` (a click on one of YouTube's own buttons or links) and wait until the URL names a video
   * that `reached` accepts (the transition itself). Then wait, bounded by the remaining deadline, for
   * the page to settle (navigate-finish / page data / media metadata) so the reported title, duration
   * and position describe the new video, not the previous page. `reached` is false when no such video
   * was observed before the deadline; `settled` says whether the snapshot can be trusted.
   */
  async function navigateAndSettle(act: () => void, reached: (videoId: string | undefined) => boolean, deadlineMs: number, video: HTMLVideoElement | null): Promise<{ reached: boolean; settled: boolean }> {
    const titleBefore = readTitle(doc);
    const startedAt = timers.now();
    // Armed before the click so a settle event that fires during the transition wait is not missed.
    let settleSeen = false;
    const markSettled = (): void => {
      settleSeen = true;
    };
    const settleTargets: Array<[EventTarget | null, readonly string[]]> = [
      [doc, SETTLE_EVENTS],
      [video, SETTLE_VIDEO_EVENTS],
    ];
    for (const [target, events] of settleTargets) for (const ev of events) target?.addEventListener(ev, markSettled);
    const unarm = (): void => {
      for (const [target, events] of settleTargets) for (const ev of events) target?.removeEventListener(ev, markSettled);
    };
    act();
    const changed = await waitFor({
      check: () => reached(currentVideoId(win)),
      listen: [
        { target: doc, events: NAV_EVENTS },
        { target: win, events: ["popstate", "hashchange"] },
        { target: video, events: VIDEO_SWAP_EVENTS },
      ],
      observe: { node: doc.head, options: { childList: true, subtree: true, characterData: true } },
      timeoutMs: deadlineMs,
      timers,
    });
    if (!changed) {
      unarm();
      return { reached: false, settled: false };
    }
    const remaining = Math.min(SETTLE_MAX_MS, deadlineMs - (timers.now() - startedAt));
    const settled = await waitFor({
      // The new page's title appearing in the DOM is as good a signal as the navigation events.
      check: () => settleSeen || readTitle(doc) !== titleBefore,
      listen: [
        { target: doc, events: SETTLE_EVENTS },
        { target: findPlayer(doc).video, events: SETTLE_VIDEO_EVENTS },
      ],
      observe: { node: doc.body, options: { childList: true, subtree: true, characterData: true } },
      timeoutMs: Math.max(0, remaining),
      timers,
    });
    unarm();
    return { reached: true, settled };
  }

  /** Click Next/Previous and wait for a new video_id, then for the page to settle. Returns the previous video_id. */
  async function transition(dom: PlayerDom, button: Element, deadlineMs: number, kind: "next" | "previous"): Promise<{ previous: string | undefined; settled: boolean }> {
    const before = currentVideoId(win);
    const { reached, settled } = await navigateAndSettle(() => (button as HTMLElement).click(), (now) => now !== undefined && now !== before, deadlineMs, dom.video);
    if (!reached) throw new OpError("OUTCOME_UNKNOWN", `${kind === "next" ? "Next" : "Previous"} was clicked but no video change was observed within ${deadlineMs} ms`);
    return { previous: before, settled };
  }

  /** Snapshot after an observed transition; without a settled page the fields that could still describe the previous video are omitted. */
  function transitionSnapshot(settled: boolean): PlayerSnapshot {
    const state = snapshot();
    if (settled) return state;
    const rest: PlayerSnapshot = { ...state };
    delete rest.title;
    delete rest.duration_seconds;
    delete rest.position_seconds;
    return rest;
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
      const name = domErrorName(err);
      throw new OpError("ACTIVATION_REQUIRED", `The browser refused fullscreen without a user gesture on the PC (${name})`);
    }
    const reached = await waitFor({ check: () => !!doc.fullscreenElement, listen: [{ target: doc, events: ["fullscreenchange"] }], timeoutMs: 1000, timers });
    if (!reached) throw new OpError("ACTIVATION_REQUIRED", "Fullscreen did not start; it must be started on the PC itself");
  }

  /** Wait options shared by the results waits: YouTube renders the results list after the page attached. */
  function resultsWait(check: () => boolean, timeoutMs: number): Promise<boolean> {
    return waitFor({
      check,
      listen: [{ target: doc, events: SETTLE_EVENTS }],
      observe: { node: doc.body ?? doc.documentElement, options: { childList: true, subtree: true, characterData: true } },
      timeoutMs: Math.max(0, timeoutMs),
      timers,
    });
  }

  /**
   * The video results of this results page (`search`). Waits for YouTube to render them, then briefly
   * for the first page to fill. Links to videos but no recognised result is ACTION_UNAVAILABLE (the
   * layout changed), never an empty success; YouTube's "No results found" panel is an empty success.
   */
  async function readResults(deadlineMs: number): Promise<ContentReply> {
    const startedAt = timers.now();
    await resultsWait(() => {
      const page = readResultsPage(doc, win);
      return page.entries.length > 0 || page.noResults;
    }, deadlineMs);
    if (readResultsPage(doc, win).entries.length > 0) {
      const left = deadlineMs - (timers.now() - startedAt);
      await resultsWait(() => readResultsPage(doc, win).entries.length >= MAX_SEARCH_RESULTS, Math.min(RESULTS_FILL_MS, left));
    }
    const page = readResultsPage(doc, win);
    if (page.entries.length === 0) {
      if (page.watchLinks > 0) throw new OpError("ACTION_UNAVAILABLE", LAYOUT_UNRECOGNISED);
      if (!page.noResults) throw new OpError("ACTION_UNAVAILABLE", "The YouTube search results did not appear in time");
    }
    return { ok: true, token, state: snapshot(), results: page.entries.map(toSearchItem) };
  }

  /**
   * Click YouTube's own link of result `index` (1-based, as read_results numbered it) and succeed only
   * when the tab reaches that video. With `video_id` the result must still be that video, so a page
   * that changed since the results were listed opens nothing (TARGET_CHANGED).
   */
  async function openResult(args: OpArgs, deadlineMs: number): Promise<ContentReply> {
    const index = args.index;
    if (typeof index !== "number" || !Number.isInteger(index) || index < 1 || index > MAX_SEARCH_RESULTS) throw new OpError("INVALID_PARAMETERS", `index must be an integer 1-${MAX_SEARCH_RESULTS}`);
    if (args.video_id !== undefined && (typeof args.video_id !== "string" || !VIDEO_ID_RE.test(args.video_id))) throw new OpError("INVALID_PARAMETERS", "video_id is not a YouTube video id");
    const startedAt = timers.now();
    // The page may still be rendering the list (just loaded, or re-rendered by YouTube).
    await resultsWait(() => readResultsPage(doc, win).entries.length >= index, Math.min(RESULT_PRESENT_MS, deadlineMs / 2));
    const page = readResultsPage(doc, win);
    if (page.entries.length === 0 && page.watchLinks > 0) throw new OpError("ACTION_UNAVAILABLE", LAYOUT_UNRECOGNISED);
    const entry = page.entries[index - 1];
    if (!entry) throw new OpError("SEARCH_RESULT_UNAVAILABLE", "That search result is no longer on the page. Search again.");
    if (args.video_id !== undefined && entry.video_id !== args.video_id) throw new OpError("TARGET_CHANGED", "The search results changed since they were listed. Nothing was opened.");
    const left = deadlineMs - (timers.now() - startedAt);
    playerlessTitle = documentTitle(doc);
    const { reached, settled } = await navigateAndSettle(() => entry.link.click(), (now) => now === entry.video_id, left, findPlayer(doc).video);
    if (!reached) throw new OpError("OUTCOME_UNKNOWN", `The search result was clicked but its video did not open within ${Math.round(left)} ms`);
    return ok(transitionSnapshot(settled));
  }

  async function execute(op: ContentOp, args: OpArgs, deadlineMs: number): Promise<ContentReply> {
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
        const { previous, settled } = await transition(dom, button, deadlineMs, "next");
        return ok(transitionSnapshot(settled), previous);
      }
      case "previous": {
        requireVideo(dom);
        const button = prevButton(dom, doc);
        if (!buttonUsable(button, win)) throw new OpError("NO_PREVIOUS_VIDEO", "The player has no previous video");
        const { previous, settled } = await transition(dom, button, deadlineMs, "previous");
        return ok(transitionSnapshot(settled), previous);
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
      case "read_results":
        return readResults(deadlineMs);
      case "open_result":
        return openResult(args, deadlineMs);
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

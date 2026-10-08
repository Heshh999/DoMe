/**
 * Read-only YouTube DOM inspection. Runs in the content script's isolated world: full DOM access,
 * no access to page JavaScript. All strings read here are untrusted page data.
 */
import type { PlayerSnapshot } from "../shared/messages.ts";
import { cleanText } from "../shared/sanitize.ts";
import { parseYoutubeUrl } from "../shared/youtubeUrl.ts";

export interface PlayerDom {
  video: HTMLVideoElement | null;
  /** The `.html5-video-player` container (`#movie_player` on watch pages, `#shorts-player` on Shorts). */
  player: Element | null;
}

export function findPlayer(doc: Document): PlayerDom {
  const video =
    doc.querySelector<HTMLVideoElement>("video.html5-main-video") ??
    doc.querySelector<HTMLVideoElement>("#movie_player video") ??
    doc.querySelector<HTMLVideoElement>(".html5-video-player video");
  const player = video?.closest(".html5-video-player") ?? doc.querySelector("#movie_player");
  return { video, player };
}

export function readTitle(doc: Document): string | undefined {
  const heading = doc.querySelector("h1.ytd-watch-metadata yt-formatted-string") ?? doc.querySelector("h1.ytd-watch-metadata");
  const fromHeading = cleanText(heading?.textContent, 200);
  if (fromHeading) return fromHeading;
  const t = cleanText(doc.title, 220);
  if (!t) return undefined;
  return cleanText(t.replace(/\s*-\s*YouTube\s*$/u, ""), 200);
}

export function isAdShowing(player: Element | null): boolean {
  return player !== null && (player.classList.contains("ad-showing") || player.classList.contains("ad-interrupting"));
}

/**
 * Live detection. YouTube keeps a `<button class="ytp-live-badge">` inside `.ytp-time-display` on
 * every watch page and only shows it (CSS) when the time display carries `.ytp-live`; the badge's
 * mere presence therefore says nothing. Live means: the time display is marked live, or the badge
 * is actually rendered, or the media has no finite duration.
 */
export function isLive(player: Element | null, video: HTMLVideoElement | null, win: Window): boolean {
  if (player?.querySelector(".ytp-time-display.ytp-live")) return true;
  const badge = player?.querySelector(".ytp-live-badge") ?? null;
  if (badge && buttonUsable(badge, win)) return true;
  return video !== null && video.duration === Infinity;
}

export function isTheater(doc: Document): boolean {
  const flexy = doc.querySelector("ytd-watch-flexy");
  return flexy !== null && flexy.hasAttribute("theater");
}

/** A YouTube control button that exists, is not aria-disabled and is not display:none. */
export function buttonUsable(el: Element | null, win: Window): el is HTMLElement {
  const w = win as Window & typeof globalThis;
  if (!el || !(el instanceof w.HTMLElement)) return false;
  if (el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled")) return false;
  if (el.hidden) return false;
  try {
    if (win.getComputedStyle(el).display === "none") return false;
  } catch {
    // getComputedStyle can throw for detached nodes; treat as unusable
    return false;
  }
  return true;
}

export function nextButton(dom: PlayerDom, doc: Document): Element | null {
  return (dom.player ?? doc).querySelector(".ytp-next-button");
}

export function prevButton(dom: PlayerDom, doc: Document): Element | null {
  return (dom.player ?? doc).querySelector(".ytp-prev-button");
}

export function currentVideoId(win: Window): string | undefined {
  return parseYoutubeUrl(win.location.href).video_id;
}

export function readSnapshot(doc: Document, win: Window, dom: PlayerDom = findPlayer(doc)): PlayerSnapshot {
  const url = parseYoutubeUrl(win.location.href);
  const { video, player } = dom;
  const snapshot: PlayerSnapshot = {
    context: url.context,
    ad_showing: isAdShowing(player),
    is_live: isLive(player, video, win),
    in_playlist: url.in_playlist,
  };
  if (url.video_id) snapshot.video_id = url.video_id;
  const title = readTitle(doc);
  if (title) snapshot.title = title;
  if (video) {
    snapshot.paused = video.paused;
    snapshot.muted = video.muted;
    if (Number.isFinite(video.volume)) snapshot.volume = Math.round(video.volume * 100);
    if (Number.isFinite(video.currentTime) && video.currentTime >= 0) snapshot.position_seconds = video.currentTime;
    if (Number.isFinite(video.duration) && video.duration >= 0) snapshot.duration_seconds = video.duration;
  }
  if (url.context === "watch") snapshot.theater = isTheater(doc);
  snapshot.fullscreen = doc.fullscreenElement !== null && doc.fullscreenElement !== undefined;
  if (player) {
    snapshot.has_next = buttonUsable(nextButton(dom, doc), win);
    snapshot.has_previous = buttonUsable(prevButton(dom, doc), win);
  }
  return snapshot;
}

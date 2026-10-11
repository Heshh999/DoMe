/**
 * Read-only inspection of a YouTube search results page (`/results`) for the `search` and
 * `open_result` bridge ops. Runs in the content script's isolated world; every string read here is
 * untrusted page data.
 *
 * Only primary video results count: the direct children of a result section's `#contents` that are a
 * video renderer (the classic `ytd-video-renderer` or the newer `yt-lockup-view-model`) and link to a
 * plain watch page. Ads (`ytd-ad-slot-renderer` and the promoted renderers), Shorts shelves and Shorts
 * links, channels, playlists and mixes, and the videos inside recommendation shelves ("People also
 * watched") are skipped. read_results and open_result use the same reader, so an index names the same
 * element in both.
 */
import type { SearchItem } from "../shared/messages.ts";
import { cleanText, MAX_SEARCH_RESULTS } from "../shared/sanitize.ts";
import { parseWatchLink, RESULTS_PATH } from "../shared/youtubeUrl.ts";

/** Upper-case tag names, as Element.tagName reports them for these custom elements. */
const RESULT_TAGS: ReadonlySet<string> = new Set(["YTD-VIDEO-RENDERER", "YT-LOCKUP-VIEW-MODEL"]);
const AD_CONTAINERS = "ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ytd-promoted-video-renderer, ytd-promoted-sparkles-web-renderer, ytd-search-pyv-renderer";
/** Containers whose videos are deliberately not results; their links do not make a layout "unrecognised". */
const SKIPPED_CONTAINERS = `${AD_CONTAINERS}, ytd-shelf-renderer, ytd-reel-shelf-renderer, ytd-horizontal-card-list-renderer, ytd-vertical-list-renderer, grid-shelf-view-model, ytd-playlist-renderer, ytd-radio-renderer`;
/** Title element of a result, classic renderer first, then the lockup's (class names with and without the `-wiz` infix). */
const TITLE_SELECTORS = ["#video-title", "h3 [class*='lockup-metadata-view-model'][class*='__title']", "h3[title]", "h3"] as const;
const CHANNEL_SELECTORS = ["ytd-channel-name a", "ytd-channel-name #text", "a[href^='/@']", "a[href^='/channel/']", "a[href^='/c/']", "a[href^='/user/']"] as const;
/** Thumbnail overlay badges that carry the duration ("3:33") or "LIVE". */
const BADGE_SELECTORS = "ytd-thumbnail-overlay-time-status-renderer, badge-shape";
const LIVE_MARKERS = "ytd-thumbnail-overlay-time-status-renderer[overlay-style='LIVE'], .badge-style-type-live-now, .badge-style-type-live-now-alternate";
/** YouTube's "No results found" panel. */
const NO_RESULTS = "ytd-background-promo-renderer";
const DURATION_RE = /^(?:(\d{1,3}):)?(\d{1,2}):(\d{2})$/;

export interface ResultEntry extends SearchItem {
  /** YouTube's own link to the video (the title link when there is one): what open_result clicks. */
  link: HTMLElement;
}

export interface ResultsPage {
  /** At most MAX_SEARCH_RESULTS, numbered from 1 in page order, one per video. */
  entries: ResultEntry[];
  /** Plain watch links outside ads and skipped shelves; links but no entries means the layout is not recognised. */
  watchLinks: number;
  /** YouTube's "No results found" panel is on the page. */
  noResults: boolean;
}

export function isResultsPage(win: Window): boolean {
  return win.location.pathname === RESULTS_PATH;
}

/** The visible `ytd-search` page (YouTube's page manager can keep a hidden one around). */
function searchRoot(doc: Document): Element | null {
  const pages = [...doc.querySelectorAll("ytd-search")];
  return pages.find((p) => !p.hasAttribute("hidden")) ?? pages[0] ?? null;
}

export function parseDuration(text: string): number | undefined {
  const m = DURATION_RE.exec(text);
  if (!m) return undefined;
  return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]);
}

function firstText(el: Element, selectors: readonly string[], maxLength: number): string | undefined {
  for (const selector of selectors) {
    for (const found of el.querySelectorAll(selector)) {
      const text = cleanText(found.textContent, maxLength) ?? cleanText(found.getAttribute("title"), maxLength);
      if (text) return text;
    }
  }
  return undefined;
}

function readEntry(el: Element, index: number, win: Window): ResultEntry | null {
  if (el.closest(AD_CONTAINERS)) return null;
  const w = win as Window & typeof globalThis;
  let videoId: string | undefined;
  let firstLink: HTMLElement | undefined;
  for (const a of el.querySelectorAll("a[href]")) {
    const watch = parseWatchLink(a.getAttribute("href"));
    if (!watch) continue;
    // A playlist or mix renders like a video but opens a list: not a video result.
    if (watch.playlist) return null;
    if (videoId === undefined) {
      videoId = watch.video_id;
      if (a instanceof w.HTMLElement) firstLink = a;
    }
  }
  if (videoId === undefined || firstLink === undefined) return null;
  const title = firstText(el, TITLE_SELECTORS, 200);
  if (!title) return null;
  let link = firstLink;
  for (const selector of TITLE_SELECTORS) {
    const candidate = el.querySelector(selector);
    const anchor = candidate?.closest("a[href]") ?? candidate?.querySelector("a[href]") ?? null;
    if (anchor instanceof w.HTMLElement && parseWatchLink(anchor.getAttribute("href"))?.video_id === videoId) {
      link = anchor;
      break;
    }
  }
  const entry: ResultEntry = { index, video_id: videoId, title, link };
  const channel = firstText(el, CHANNEL_SELECTORS, 100);
  if (channel) entry.channel = channel;
  let live = el.querySelector(LIVE_MARKERS) !== null;
  for (const badge of el.querySelectorAll(BADGE_SELECTORS)) {
    const text = cleanText(badge.textContent, 32);
    if (!text) continue;
    if (/^live$/i.test(text)) live = true;
    const seconds = parseDuration(text);
    if (seconds !== undefined && entry.duration_seconds === undefined) entry.duration_seconds = seconds;
  }
  if (live) {
    entry.is_live = true;
    delete entry.duration_seconds;
  } else if (entry.duration_seconds !== undefined) {
    entry.is_live = false;
  }
  return entry;
}

export function readResultsPage(doc: Document, win: Window): ResultsPage {
  const root = searchRoot(doc);
  const entries: ResultEntry[] = [];
  const seen = new Set<string>();
  if (root) {
    for (const section of root.querySelectorAll("ytd-item-section-renderer")) {
      if (section.closest(AD_CONTAINERS)) continue;
      const contents = [...section.children].find((c) => c.id === "contents");
      for (const el of contents?.children ?? []) {
        if (entries.length >= MAX_SEARCH_RESULTS) break;
        if (!RESULT_TAGS.has(el.tagName)) continue;
        const entry = readEntry(el, entries.length + 1, win);
        if (!entry || seen.has(entry.video_id)) continue;
        seen.add(entry.video_id);
        entries.push(entry);
      }
    }
  }
  let watchLinks = 0;
  for (const a of (root ?? doc.body ?? doc.documentElement).querySelectorAll("a[href]")) {
    const watch = parseWatchLink(a.getAttribute("href"));
    if (watch && !watch.playlist && !a.closest(SKIPPED_CONTAINERS)) watchLinks += 1;
  }
  return { entries, watchLinks, noResults: root !== null && root.querySelector(NO_RESULTS) !== null };
}

/** The contract shape of an entry (drops the element reference). */
export function toSearchItem(entry: ResultEntry): SearchItem {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the element never leaves the content script
  const { link: _link, ...item } = entry;
  return item;
}

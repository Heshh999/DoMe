import type { Context } from "./messages.ts";

export const YT_ORIGIN = "https://www.youtube.com";
export const YT_URL_PATTERN = "https://www.youtube.com/*";
export const VIDEO_ID_RE = /^[A-Za-z0-9_-]{1,32}$/;

export function isYoutubeUrl(url: unknown): url is string {
  return typeof url === "string" && (url === YT_ORIGIN || url.startsWith(`${YT_ORIGIN}/`));
}

export interface UrlFacts {
  context: Context;
  video_id?: string;
  in_playlist: boolean;
}

/** Page kind, video id and playlist flag from a URL alone (used by both script contexts). */
export function parseYoutubeUrl(href: string): UrlFacts {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return { context: "other", in_playlist: false };
  }
  const facts: UrlFacts = { context: "other", in_playlist: url.searchParams.has("list") && url.searchParams.get("list") !== "" };
  if (url.hostname === "music.youtube.com") facts.context = "music";
  const shorts = /^\/shorts\/([A-Za-z0-9_-]{1,32})(?:[/?#]|$)/.exec(url.pathname);
  if (shorts && shorts[1]) {
    facts.context = "shorts";
    facts.video_id = shorts[1];
    return facts;
  }
  if (url.pathname === "/watch" || url.pathname === "/watch/") {
    if (facts.context === "other") facts.context = "watch";
  }
  const v = url.searchParams.get("v");
  if (v && VIDEO_ID_RE.test(v)) facts.video_id = v;
  return facts;
}

/** Path of YouTube's search results page (bridge op `search`). */
export const RESULTS_PATH = "/results";

/**
 * The results page for a customer-typed query. The query only ever becomes the `search_query`
 * parameter, set through URLSearchParams so no character of it can change the URL's origin, path or
 * other parameters (rules.search_queries). Callers never log the returned URL.
 */
export function searchResultsUrl(query: string): string {
  const url = new URL(RESULTS_PATH, YT_ORIGIN);
  url.searchParams.set("search_query", query);
  return url.href;
}

/** Whitespace runs collapsed, ends trimmed: how a query is compared with the one in a results URL. */
function normalizeQuery(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** True when `href` is the www.youtube.com results page for `query` (compared after whitespace normalization). */
export function isResultsUrlFor(href: unknown, query: string): boolean {
  if (!isYoutubeUrl(href)) return false;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return false;
  }
  if (url.pathname !== RESULTS_PATH) return false;
  const shown = url.searchParams.get("search_query");
  return shown !== null && normalizeQuery(shown) === normalizeQuery(query);
}

/**
 * A watch link (`/watch?v=<id>`, relative or on www.youtube.com): its video id and whether it opens a
 * playlist or mix (`list` parameter). Undefined for anything else: Shorts, channels, other hosts.
 */
export function parseWatchLink(href: string | null | undefined): { video_id: string; playlist: boolean } | undefined {
  if (!href) return undefined;
  let url: URL;
  try {
    url = new URL(href, YT_ORIGIN);
  } catch {
    return undefined;
  }
  if (url.origin !== YT_ORIGIN || url.pathname !== "/watch") return undefined;
  const v = url.searchParams.get("v");
  if (!v || !VIDEO_ID_RE.test(v)) return undefined;
  return { video_id: v, playlist: url.searchParams.has("list") };
}

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

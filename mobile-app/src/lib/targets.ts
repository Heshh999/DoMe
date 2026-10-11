/**
 * Target selection (spec §9): explicit customer selection first; otherwise a single unambiguous
 * playing tab; otherwise ask. A selection is only honoured while the tab (same attachment token)
 * still exists. Targets always carry `tab_token`, so a command can never re-bind to another tab.
 * A tab with no player (context "other": search results, the home page) is only ever a target when
 * the customer picks it; it never becomes the implicit one, so player controls never land on a list.
 */
import type { JsonValue } from "@dome/protocol";

import type { MediaSession, PcState, YoutubeTab } from "../store/live.ts";

export function tabKey(tab: YoutubeTab): string {
  return `${tab.browser_instance_id}:${tab.tab_id}:${tab.tab_token ?? ""}`;
}

export type YoutubeResolution = { ok: true; tab: YoutubeTab; target: Record<string, JsonValue>; explicit: boolean } | { ok: false; reason: YoutubeProblem };
/** no_player: the only YouTube tabs show no player (search results, the home page). */
export type YoutubeProblem = "no_tabs" | "none_attached" | "no_player" | "ambiguous" | "no_extension";

export function controllableTabs(state: PcState | null): YoutubeTab[] {
  return (state?.youtube_tabs ?? []).filter((t) => t.script_attached && typeof t.tab_token === "string");
}

export function youtubeTargetFor(tab: YoutubeTab): Record<string, JsonValue> {
  const target: Record<string, JsonValue> = { browser_instance_id: tab.browser_instance_id, tab_id: tab.tab_id, tab_token: tab.tab_token ?? "" };
  if (tab.video_id) target.expected_video_id = tab.video_id;
  return target;
}

export function resolveYoutubeTarget(state: PcState | null, selectedKey: string | null | undefined): YoutubeResolution {
  if (state && state.extension_connected === false) return { ok: false, reason: "no_extension" };
  const all = state?.youtube_tabs ?? [];
  if (all.length === 0) return { ok: false, reason: "no_tabs" };
  const tabs = controllableTabs(state);
  if (tabs.length === 0) return { ok: false, reason: "none_attached" };
  if (selectedKey) {
    const chosen = tabs.find((t) => tabKey(t) === selectedKey);
    if (chosen) return { ok: true, tab: chosen, target: youtubeTargetFor(chosen), explicit: true };
  }
  const players = tabs.filter((t) => t.context !== "other");
  if (players.length === 0) return { ok: false, reason: "no_player" };
  if (players.length === 1) return { ok: true, tab: players[0]!, target: youtubeTargetFor(players[0]!), explicit: false };
  const playing = players.filter((t) => t.paused === false);
  if (playing.length === 1) return { ok: true, tab: playing[0]!, target: youtubeTargetFor(playing[0]!), explicit: false };
  return { ok: false, reason: "ambiguous" };
}

const sameTab = (a: YoutubeTab, b: { browser_instance_id: string; tab_id: number }) => a.browser_instance_id === b.browser_instance_id && a.tab_id === b.tab_id;

/**
 * Controllable tabs playing a video (paused === false), other than `besides` (the tab a result opens in): a result
 * opened elsewhere would play over them, so DoMe pauses them first.
 */
export function playingElsewhere(state: PcState | null, besides: { browser_instance_id: string; tab_id: number } | null): YoutubeTab[] {
  return controllableTabs(state).filter((t) => t.paused === false && t.context !== "other" && !(besides && sameTab(t, besides)));
}

/**
 * Where `youtube.search` runs (target youtube_browser_target). The page the customer is watching is never navigated
 * away implicitly: only a tab the customer chose explicitly (`selectedKey`) is reused; otherwise the results open in a
 * new tab of the one connected browser, or of the browser the customer picked (`chosenBrowserId`) when several are.
 */
export type YoutubeBrowserResolution =
  | { ok: true; mode: "tab"; tab: YoutubeTab; browserInstanceId: string; target: Record<string, JsonValue> }
  | { ok: true; mode: "new_tab"; tab: null; browserInstanceId: string; target: Record<string, JsonValue> }
  | { ok: false; reason: "no_extension" | "no_browser" | "ambiguous" };

/** Browser instances the extension reported, plus any only known from their tabs (in report order, no duplicates). */
export function browserInstanceIds(state: PcState | null): string[] {
  const ids: string[] = [];
  for (const b of state?.browser_instances ?? []) if (!ids.includes(b.browser_instance_id)) ids.push(b.browser_instance_id);
  for (const t of state?.youtube_tabs ?? []) if (!ids.includes(t.browser_instance_id)) ids.push(t.browser_instance_id);
  return ids;
}

export function resolveYoutubeBrowserTarget(state: PcState | null, selectedKey: string | null | undefined, chosenBrowserId?: string | null): YoutubeBrowserResolution {
  if (state && state.extension_connected === false) return { ok: false, reason: "no_extension" };
  if (selectedKey) {
    const chosen = controllableTabs(state).find((t) => tabKey(t) === selectedKey);
    if (chosen) return { ok: true, mode: "tab", tab: chosen, browserInstanceId: chosen.browser_instance_id, target: attachmentTarget(chosen) };
  }
  const browsers = browserInstanceIds(state);
  const browser = chosenBrowserId && browsers.includes(chosenBrowserId) ? chosenBrowserId : browsers.length === 1 ? browsers[0]! : null;
  if (browser) return { ok: true, mode: "new_tab", tab: null, browserInstanceId: browser, target: { browser_instance_id: browser } };
  return { ok: false, reason: browsers.length === 0 ? "no_browser" : "ambiguous" };
}

/** One attachment of a tab (browser, tab id, token) with no expectation about what it is playing. */
function attachmentTarget(tab: YoutubeTab): Record<string, JsonValue> {
  return { browser_instance_id: tab.browser_instance_id, tab_id: tab.tab_id, tab_token: tab.tab_token ?? "" };
}

/**
 * youtube_target for `youtube.open_result` on the results tab a search returned. No expected_video_id: a results page
 * has no current video, and the result's own video_id travels in the params instead.
 */
export function targetForSearchResult(tab: YoutubeTab): Record<string, JsonValue> {
  return attachmentTarget(tab);
}

export type MediaResolution = { ok: true; session: MediaSession; target: Record<string, JsonValue>; explicit: boolean } | { ok: false; reason: "no_sessions" | "ambiguous" };

export function resolveMediaTarget(state: PcState | null, selectedId: string | null | undefined): MediaResolution {
  const sessions = (state?.media_sessions ?? []).filter((s) => s.status !== "closed");
  if (sessions.length === 0) return { ok: false, reason: "no_sessions" };
  if (selectedId) {
    const chosen = sessions.find((s) => s.session_id === selectedId);
    if (chosen) return { ok: true, session: chosen, target: { session_id: chosen.session_id }, explicit: true };
  }
  if (sessions.length === 1) return { ok: true, session: sessions[0]!, target: { session_id: sessions[0]!.session_id }, explicit: false };
  const playing = sessions.filter((s) => s.status === "playing");
  if (playing.length === 1) return { ok: true, session: playing[0]!, target: { session_id: playing[0]!.session_id }, explicit: false };
  return { ok: false, reason: "ambiguous" };
}

export function browserLabel(state: PcState | null, browserInstanceId: string): string {
  const inst = state?.browser_instances?.find((b) => b.browser_instance_id === browserInstanceId);
  if (!inst) return "Browser";
  const name = inst.browser === "chrome" ? "Chrome" : inst.browser === "edge" ? "Edge" : "Browser";
  return inst.profile_label ? `${name} · ${inst.profile_label}` : name;
}

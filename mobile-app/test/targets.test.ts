import { describe, expect, it } from "vitest";

import { registry } from "@dome/protocol";

import { browserInstanceIds, browserLabel, playingElsewhere, resolveMediaTarget, resolveYoutubeBrowserTarget, resolveYoutubeTarget, tabKey, targetForSearchResult } from "../src/lib/targets.ts";
import type { PcState, YoutubeTab } from "../src/store/live.ts";

const tab = (over: Partial<YoutubeTab>): YoutubeTab => ({ browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, context: "watch", ad_showing: false, is_live: false, in_playlist: false, paused: true, video_id: "abc", ...over });
const state = (tabs: YoutubeTab[], extra: Partial<PcState> = {}): PcState => ({ remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: tabs, ...extra });

describe("YouTube target resolution (spec §9 order)", () => {
  it("single controllable tab is used and the target validates against the registry", () => {
    const r = resolveYoutubeTarget(state([tab({})]), null);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.explicit).toBe(false);
      expect(registry.validateTarget("youtube.next", r.target)).toEqual({ browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", expected_video_id: "abc" });
    }
  });
  it("explicit selection wins while that attachment still exists; a stale selection falls back", () => {
    const a = tab({ tab_id: 1, paused: false });
    const b = tab({ tab_id: 2, tab_token: "dGFiLXRva2VuLTAwMDAwMX" });
    const r = resolveYoutubeTarget(state([a, b]), tabKey(b));
    expect(r.ok && r.tab.tab_id).toBe(2);
    expect(r.ok && r.explicit).toBe(true);
    const stale = resolveYoutubeTarget(state([a, b]), "bi_test0001:2:otherTokenOtherTokenXX");
    expect(stale.ok && stale.tab.tab_id).toBe(1); // the single playing tab
  });
  it("one playing tab among several is unambiguous; several playing → ask", () => {
    const tabs = [tab({ tab_id: 1, paused: false }), tab({ tab_id: 2, paused: true, tab_token: "dGFiLXRva2VuLTAwMDAwMX" })];
    expect(resolveYoutubeTarget(state(tabs), null)).toMatchObject({ ok: true, tab: { tab_id: 1 } });
    tabs[1]!.paused = false;
    expect(resolveYoutubeTarget(state(tabs), null)).toEqual({ ok: false, reason: "ambiguous" });
  });
  it("tabs without an attached content script can never be targets", () => {
    const r = resolveYoutubeTarget(state([tab({ script_attached: false, tab_token: undefined as unknown as string })]), null);
    expect(r).toEqual({ ok: false, reason: "none_attached" });
    const noToken = { ...tab({}) } as Partial<YoutubeTab>;
    delete noToken.tab_token;
    expect(resolveYoutubeTarget(state([noToken as YoutubeTab]), null)).toEqual({ ok: false, reason: "none_attached" });
  });
  it("a page without a player (search results) is never the implicit target; picked by hand it still is", () => {
    const watching = tab({ tab_id: 1, paused: false });
    const results = tab({ tab_id: 2, tab_token: "cmVzdWx0cy10b2tlbi0wMD", context: "other", paused: undefined as unknown as boolean, video_id: undefined as unknown as string });
    // A playing plus B results: A, also when B is listed first
    expect(resolveYoutubeTarget(state([results, watching]), null)).toMatchObject({ ok: true, tab: { tab_id: 1 }, explicit: false });
    // a paused video plus a results page: still the video (the only player), never the list
    expect(resolveYoutubeTarget(state([results, tab({ tab_id: 1, paused: true })]), null)).toMatchObject({ ok: true, tab: { tab_id: 1 } });
    expect(resolveYoutubeTarget(state([results]), null)).toEqual({ ok: false, reason: "no_player" });
    expect(resolveYoutubeTarget(state([results, watching]), tabKey(results))).toMatchObject({ ok: true, tab: { tab_id: 2 }, explicit: true });
  });
  it("playingElsewhere: playing videos other than the tab a result opens in", () => {
    const a = tab({ tab_id: 1, paused: false, title: "A" });
    const b = tab({ tab_id: 2, tab_token: "dGFiLXRva2VuLTAwMDAwMX", paused: false, title: "B" });
    const results = tab({ tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD", context: "other" });
    const s = state([a, b, results, tab({ tab_id: 4, paused: true }), tab({ tab_id: 5, paused: false, script_attached: false })]);
    expect(playingElsewhere(s, results).map((t) => t.tab_id)).toEqual([1, 2]);
    expect(playingElsewhere(s, a).map((t) => t.tab_id)).toEqual([2]);
    expect(playingElsewhere(s, null).map((t) => t.tab_id)).toEqual([1, 2]);
    expect(playingElsewhere(null, null)).toEqual([]);
  });
  it("reports no extension / no tabs / unknown state", () => {
    expect(resolveYoutubeTarget(state([], { extension_connected: false }), null)).toEqual({ ok: false, reason: "no_extension" });
    expect(resolveYoutubeTarget(state([]), null)).toEqual({ ok: false, reason: "no_tabs" });
    expect(resolveYoutubeTarget(null, null)).toEqual({ ok: false, reason: "no_tabs" });
  });
  it("browserLabel includes browser and profile without trusting anything else", () => {
    const s = state([], { browser_instances: [{ browser_instance_id: "bi_test0001", browser: "edge", profile_label: "Work" }] });
    expect(browserLabel(s, "bi_test0001")).toBe("Edge · Work");
    expect(browserLabel(s, "nope")).toBe("Browser");
  });
});

describe("YouTube search target (youtube_browser_target)", () => {
  const edge = { browser_instance_id: "bi_test0001", browser: "edge" as const };
  const chrome = { browser_instance_id: "bi_test0002", browser: "chrome" as const };

  it("an explicitly chosen controllable tab is searched in place, token-bound; the target validates", () => {
    const t = tab({ tab_id: 7, paused: false });
    const r = resolveYoutubeBrowserTarget(state([t], { browser_instances: [edge] }), tabKey(t));
    expect(r).toMatchObject({ ok: true, mode: "tab", browserInstanceId: "bi_test0001" });
    if (r.ok) {
      expect(r.target).toEqual({ browser_instance_id: "bi_test0001", tab_id: 7, tab_token: "dGFiLXRva2VuLTAwMDAwMD" });
      expect(registry.validateTarget("youtube.search", r.target)).toEqual(r.target);
    }
  });

  it("without an explicit choice the video being watched is left alone: a new tab in the one browser", () => {
    const r = resolveYoutubeBrowserTarget(state([tab({ paused: false })], { browser_instances: [edge] }), null);
    expect(r).toEqual({ ok: true, mode: "new_tab", tab: null, browserInstanceId: "bi_test0001", target: { browser_instance_id: "bi_test0001" } });
    if (r.ok) expect(registry.validateTarget("youtube.search", r.target)).toEqual({ browser_instance_id: "bi_test0001" });
    // a stale selection (the tab re-attached with a new token) also falls back to a new tab
    expect(resolveYoutubeBrowserTarget(state([tab({})], { browser_instances: [edge] }), "bi_test0001:1:otherTokenOtherTokenXX")).toMatchObject({ ok: true, mode: "new_tab" });
  });

  it("several browsers: ambiguous until one is picked; a vanished pick is ignored", () => {
    const s = state([], { browser_instances: [edge, chrome] });
    expect(resolveYoutubeBrowserTarget(s, null)).toEqual({ ok: false, reason: "ambiguous" });
    expect(resolveYoutubeBrowserTarget(s, null, "bi_test0002")).toMatchObject({ ok: true, mode: "new_tab", target: { browser_instance_id: "bi_test0002" } });
    expect(resolveYoutubeBrowserTarget(s, null, "bi_gone00001")).toEqual({ ok: false, reason: "ambiguous" });
  });

  it("no extension or no browser cannot search; a browser only known from its tabs still counts", () => {
    expect(resolveYoutubeBrowserTarget(state([], { extension_connected: false, browser_instances: [edge] }), null)).toEqual({ ok: false, reason: "no_extension" });
    expect(resolveYoutubeBrowserTarget(state([]), null)).toEqual({ ok: false, reason: "no_browser" });
    expect(resolveYoutubeBrowserTarget(null, null)).toEqual({ ok: false, reason: "no_browser" });
    expect(browserInstanceIds(state([tab({}), tab({ tab_id: 2 })], { browser_instances: [chrome] }))).toEqual(["bi_test0002", "bi_test0001"]);
    expect(resolveYoutubeBrowserTarget(state([tab({ script_attached: false })]), null)).toMatchObject({ ok: true, mode: "new_tab", browserInstanceId: "bi_test0001" });
  });

  it("open_result targets the results tab by its new token and never expects a current video", () => {
    const results = tab({ tab_id: 9, tab_token: "cmVzdWx0cy10b2tlbi0wMD", context: "other", video_id: undefined as unknown as string });
    const target = targetForSearchResult(results);
    expect(target).toEqual({ browser_instance_id: "bi_test0001", tab_id: 9, tab_token: "cmVzdWx0cy10b2tlbi0wMD" });
    expect(registry.validateTarget("youtube.open_result", target)).toEqual(target);
    expect(registry.validateParams("youtube.open_result", { index: 2, video_id: "abc" })).toEqual({ index: 2, video_id: "abc" });
  });
});

describe("media session target resolution", () => {
  const sessions: PcState["media_sessions"] = [
    { session_id: "spotify", status: "playing", controls: ["pause", "next"], app_label: "Spotify" },
    { session_id: "groove", status: "paused", controls: ["play"] },
  ];
  it("explicit, single-playing and ambiguous", () => {
    expect(resolveMediaTarget(state([], { media_sessions: sessions }), "groove")).toMatchObject({ ok: true, session: { session_id: "groove" }, explicit: true });
    expect(resolveMediaTarget(state([], { media_sessions: sessions }), null)).toMatchObject({ ok: true, session: { session_id: "spotify" }, target: { session_id: "spotify" } });
    const both = sessions.map((s) => ({ ...s, status: "playing" as const })) as NonNullable<PcState["media_sessions"]>;
    expect(resolveMediaTarget(state([], { media_sessions: both }), null)).toEqual({ ok: false, reason: "ambiguous" });
    expect(resolveMediaTarget(state([]), null)).toEqual({ ok: false, reason: "no_sessions" });
  });
});

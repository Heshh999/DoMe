import { describe, expect, it } from "vitest";

import { registry } from "@dome/protocol";

import { browserLabel, resolveMediaTarget, resolveYoutubeTarget, tabKey } from "../src/lib/targets.ts";
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

/**
 * Live-store state derived from validated success results, whichever page sent the command: the approved-app list
 * (`app.list`) and the last YouTube search (`youtube.search`). A search does not select its results tab (a list has
 * no player, so Pause and volume keep reaching the video being watched); opening a result selects the tab it plays
 * in. Search results stay in memory only and are dropped on PC switch and sign-out.
 */
import { beforeEach, describe, expect, it } from "vitest";

import type { CommandRecord } from "../src/lib/commands.ts";
import { resolveYoutubeTarget, tabKey } from "../src/lib/targets.ts";
import { useDevicesStore } from "../src/store/devices.ts";
import { useLiveStore, type YoutubeTab } from "../src/store/live.ts";
import { freshStorage, makeRuntime, OFFICE_PC, OTHER_PC, PC, signedIn } from "./helpers/harness.ts";

const TS = "2026-10-08T12:00:00.000Z";
const RESULTS_TAB: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 4, tab_token: "cmVzdWx0cy10b2tlbi0wMD", script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false };
const WATCHING: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, context: "watch", title: "Watching", video_id: "watch1", paused: false, ad_showing: false, is_live: false, in_playlist: false };

function record(over: Partial<CommandRecord> & Pick<CommandRecord, "commandId" | "action">, result: Record<string, unknown> | null, state: "succeeded" | "failed" = "succeeded"): CommandRecord {
  return {
    pcId: PC,
    params: {},
    target: null,
    protocolVersion: "1.0",
    createdAt: 1000,
    expiresAt: TS,
    state,
    ackAt: null,
    confirmation: null,
    terminal: { origin: "agent", state, at: TS, durationMs: 5, result, resultInvalid: false, error: state === "failed" ? { code: "INTERNAL", message: "", retryable: false } : null, warning: null },
    noAnswer: false,
    source: "button",
    ...over,
  };
}

const search = (commandId: string, createdAt: number, tab: YoutubeTab = RESULTS_TAB) => record({ commandId, action: "youtube.search", createdAt, params: { query: "lofi" }, protocolVersion: "1.2" }, { tab, results: [{ index: 1, video_id: "vid1", title: "One" }] });

beforeEach(() => {
  useLiveStore.getState().reset();
});

describe("app.list results", () => {
  it("update the PC's approved apps from any page; an older answer never replaces a newer one", () => {
    const live = useLiveStore.getState();
    live.upsertCommand(record({ commandId: "new", action: "app.list", createdAt: 2000 }, { apps: [{ app_id: "edge", display_name: "Edge", running: true, windows: [] }] }));
    expect(useLiveStore.getState().appsByPc[PC]!.apps.map((a) => a.app_id)).toEqual(["edge"]);
    live.upsertCommand(record({ commandId: "old", action: "app.list", createdAt: 1000 }, { apps: [] }));
    expect(useLiveStore.getState().appsByPc[PC]!.apps.map((a) => a.app_id)).toEqual(["edge"]);
    live.upsertCommand(record({ commandId: "newer", action: "app.list", createdAt: 3000 }, { apps: [] }));
    expect(useLiveStore.getState().appsByPc[PC]!.apps).toEqual([]);
  });

  it("failures and in-flight records leave the list alone", () => {
    const live = useLiveStore.getState();
    live.setApps(PC, [{ app_id: "edge", display_name: "Edge", running: false, windows: [] }]);
    live.upsertCommand(record({ commandId: "f", action: "app.list", createdAt: 5000 }, null, "failed"));
    live.upsertCommand({ ...record({ commandId: "p", action: "app.list", createdAt: 6000 }, null), state: "accepted", terminal: null });
    expect(useLiveStore.getState().appsByPc[PC]!.apps.map((a) => a.app_id)).toEqual(["edge"]);
  });
});

describe("youtube.search results", () => {
  it("record the results (not the query) without selecting the results tab", () => {
    const live = useLiveStore.getState();
    live.selectTab(PC, tabKey(WATCHING));
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [WATCHING] } });
    live.upsertCommand(search("s1", 1000));
    const s = useLiveStore.getState();
    expect(s.lastSearchByPc[PC]).toEqual({ commandId: "s1", createdAt: 1000, stateSeq: 1, tab: RESULTS_TAB, results: [{ index: 1, video_id: "vid1", title: "One" }] });
    expect(JSON.stringify(s.lastSearchByPc)).not.toContain("lofi");
    expect(s.selectedTab[PC]).toBe(tabKey(WATCHING));
  });

  it("a video playing in tab A and a search in tab B: Pause and volume still reach A", () => {
    const live = useLiveStore.getState();
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [WATCHING] } });
    expect(resolveYoutubeTarget(useLiveStore.getState().pcs[PC]!.state, useLiveStore.getState().selectedTab[PC])).toMatchObject({ ok: true, tab: { tab_id: 1 }, explicit: false });
    live.upsertCommand(search("s1", 1000));
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [WATCHING, RESULTS_TAB] } });
    const s = useLiveStore.getState();
    expect(s.selectedTab[PC]).toBeUndefined();
    expect(resolveYoutubeTarget(s.pcs[PC]!.state, s.selectedTab[PC])).toMatchObject({ ok: true, tab: { tab_id: 1 }, explicit: false });
  });

  it("opening a result selects the tab it plays in (its token may have changed), once", () => {
    const live = useLiveStore.getState();
    live.selectTab(PC, tabKey(WATCHING));
    const playing = { ...RESULTS_TAB, tab_token: "bmV3LXRva2VuLTAwMDAwMD", context: "watch" as const, video_id: "vid1", paused: false };
    const opened = record({ commandId: "o1", action: "youtube.open_result", createdAt: 2000, params: { index: 1, video_id: "vid1" }, protocolVersion: "1.2" }, { tab: playing });
    live.upsertCommand(opened);
    expect(useLiveStore.getState().selectedTab[PC]).toBe(tabKey(playing));
    // the customer picks the other tab again; re-upserting the same finished command does not take it back
    live.selectTab(PC, tabKey(WATCHING));
    live.upsertCommand(opened);
    expect(useLiveStore.getState().selectedTab[PC]).toBe(tabKey(WATCHING));
    // a failed open selects nothing
    live.upsertCommand(record({ commandId: "o2", action: "youtube.open_result", createdAt: 3000 }, null, "failed"));
    expect(useLiveStore.getState().selectedTab[PC]).toBe(tabKey(WATCHING));
  });

  it("a slower, older search never replaces a newer one", () => {
    const live = useLiveStore.getState();
    live.upsertCommand(search("newer", 2000, { ...RESULTS_TAB, tab_id: 5 }));
    live.upsertCommand(search("older", 1000));
    expect(useLiveStore.getState().lastSearchByPc[PC]!.commandId).toBe("newer");
    expect(useLiveStore.getState().lastSearchByPc[PC]!.tab.tab_id).toBe(5);
  });

  it("is cleared per PC, entirely, and on reset (sign-out)", () => {
    const live = useLiveStore.getState();
    live.upsertCommand(search("a", 1000));
    live.upsertCommand({ ...search("b", 1000), pcId: OTHER_PC });
    live.clearLastSearch(PC);
    expect(Object.keys(useLiveStore.getState().lastSearchByPc)).toEqual([OTHER_PC]);
    live.clearLastSearch();
    expect(useLiveStore.getState().lastSearchByPc).toEqual({});
    live.upsertCommand(search("c", 1000));
    live.reset();
    expect(useLiveStore.getState().lastSearchByPc).toEqual({});
  });

  it("switching the selected PC forgets the search results", async () => {
    freshStorage();
    signedIn();
    const h = makeRuntime();
    await h.connect();
    try {
      useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC, { ...OFFICE_PC, id: OTHER_PC, name: "Den PC" }], selectedPcId: PC });
      useLiveStore.getState().upsertCommand(search("s", 1000));
      expect(useLiveStore.getState().lastSearchByPc[PC]).toBeDefined();
      useDevicesStore.setState({ selectedPcId: OTHER_PC });
      expect(useLiveStore.getState().lastSearchByPc).toEqual({});
    } finally {
      h.teardown();
    }
  });
});

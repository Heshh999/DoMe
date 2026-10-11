import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { schemas } from "@dome/protocol";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FAST_RETRY_MAX_ATTEMPTS, FAST_RETRY_WINDOW_MS, HELLO_ACK_TIMEOUT_MS, NATIVE_HOST_NAME, RECONNECT_ALARM, SLOW_RETRY_SECONDS } from "../src/background/connection.ts";
import type { BridgeEvent, BridgeHello, BridgeResponse } from "../src/background/frames.ts";
import { computeBudget, MIN_BUDGET_MS } from "../src/background/requests.ts";
import { CONTENT_SCRIPT_FILE } from "../src/background/tabs.ts";
import { createBackground, type Background } from "../src/background/service.ts";
import { KEY_INSTANCE_ID } from "../src/background/storage.ts";
import { BG_KIND, CS_KIND, POPUP_KIND, type ContentReply, type PlayerSnapshot, type StatusReport } from "../src/shared/messages.ts";
import { FakeChrome, type ContentHandler } from "./chrome-stub.ts";

interface FixtureTab {
  id: number;
  url: string;
  title: string;
  active: boolean;
  attached: boolean;
  video_id?: string;
  paused?: boolean;
}
const FIXTURE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fixtures/tabs.json"), "utf8")) as { tabs: FixtureTab[] };
const TOKENS: Record<number, string> = { 11: "AAAAAAAAAAAAAAAAAAAAAA", 12: "BBBBBBBBBBBBBBBBBBBBBB" };
const REQ = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ACK = { type: "bridge_hello_ack", protocol_version: "1.1", agent_version: "0.1.0" };
const NOT_RUNNING = { type: "bridge_error", error: { code: "AGENT_NOT_RUNNING", message: "The DoMe agent is not running on this PC." } };
const AGENT_GONE = { type: "bridge_error", error: { code: "AGENT_DISCONNECTED", message: "The DoMe agent disconnected." } };

function snapshotFor(tab: FixtureTab): PlayerSnapshot {
  const s: PlayerSnapshot = { context: "watch", ad_showing: false, is_live: false, in_playlist: tab.url.includes("list="), paused: tab.paused ?? false, muted: false, volume: 80, position_seconds: 10, duration_seconds: 200, theater: false, fullscreen: false, has_next: true, has_previous: false, title: tab.title.replace(" - YouTube", "") };
  if (tab.video_id) s.video_id = tab.video_id;
  return s;
}

/** A scripted content script: answers probes and ops like the real adapter would. */
function scriptedContent(tab: FixtureTab, overrides: Partial<Record<string, (msg: { op: string; args: Record<string, unknown>; deadline_ms: number }) => ContentReply | Promise<ContentReply>>> = {}): ContentHandler {
  const token = TOKENS[tab.id]!;
  const state = snapshotFor(tab);
  return (raw) => {
    const msg = raw as { kind: string; type: string; op: string; args: Record<string, unknown>; deadline_ms: number };
    if (msg.type === "probe") return { ok: true, token, state } satisfies ContentReply;
    const override = overrides[msg.op];
    if (override) return override(msg);
    if (msg.args.tab_token !== token) return { ok: false, code: "TARGET_CHANGED", message: "token mismatch", token, state } satisfies ContentReply;
    if (msg.op === "set_paused") state.paused = msg.args.paused as boolean;
    if (msg.op === "next") {
      const previous = state.video_id!;
      state.video_id = "9bZkp7q19f0";
      return { ok: true, token, state, previous_video_id: previous } satisfies ContentReply;
    }
    return { ok: true, token, state } satisfies ContentReply;
  };
}

function setupTabs(chrome: FakeChrome, overrides: Parameters<typeof scriptedContent>[1] = {}): void {
  for (const tab of FIXTURE.tabs) chrome.addTab({ id: tab.id, url: tab.url, title: tab.title, active: tab.active }, tab.attached ? scriptedContent(tab, overrides) : undefined);
}

async function boot(chrome: FakeChrome, options: { ack?: boolean } = {}): Promise<Background> {
  const bg = createBackground(chrome, { browser: "chrome" });
  const started = bg.start();
  await vi.advanceTimersByTimeAsync(0);
  if (options.ack !== false) {
    chrome.lastPort.receive(ACK);
    await vi.advanceTimersByTimeAsync(0);
  }
  await started;
  await vi.advanceTimersByTimeAsync(0);
  return bg;
}

function validateAllSent(chrome: FakeChrome): void {
  for (const port of chrome.ports) for (const frame of port.sent) schemas.validateBridgeFrame("extension_to_agent", frame);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, "info").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("startup and hello", () => {
  it("connects to com.dome.agent and sends a valid bridge_hello with a stored browser_instance_id", async () => {
    const chrome = new FakeChrome();
    await boot(chrome, { ack: false });
    const port = chrome.lastPort;
    expect(port.name).toBe(NATIVE_HOST_NAME);
    const hello = port.framesOfType<BridgeHello>("bridge_hello")[0]!;
    expect(hello.browser).toBe("chrome");
    expect(hello.protocol_versions).toEqual(["1.0", "1.1"]); // every MINOR, so 1.0 and 1.1 agents both accept it
    expect(hello.extension_version).toBe("0.1.0-test");
    expect(hello.browser_instance_id).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(chrome.store.get(KEY_INSTANCE_ID)).toBe(hello.browser_instance_id);
    validateAllSent(chrome);
    // Nothing but hello before the ack.
    expect(port.sent).toHaveLength(1);
  });

  it("browser_instance_id survives a worker restart", async () => {
    const first = new FakeChrome();
    await boot(first);
    const id = first.lastPort.framesOfType<BridgeHello>("bridge_hello")[0]!.browser_instance_id;
    const second = first.restartWorker();
    await boot(second);
    expect(second.lastPort.framesOfType<BridgeHello>("bridge_hello")[0]!.browser_instance_id).toBe(id);
  });

  it("announces all YouTube tabs after the ack, marking unattached tabs", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const events = chrome.lastPort.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed");
    expect(events.length).toBeGreaterThanOrEqual(1);
    const tabs = events[events.length - 1]!.tabs!;
    expect(tabs.map((t) => t.tab_id)).toEqual([11, 12, 13]); // example.com excluded
    const t11 = tabs.find((t) => t.tab_id === 11)!;
    expect(t11.script_attached).toBe(true);
    expect(t11.tab_token).toBe(TOKENS[11]);
    expect(t11.active).toBe(true);
    expect(t11.video_id).toBe("dQw4w9WgXcQ");
    const t13 = tabs.find((t) => t.tab_id === 13)!;
    expect(t13.script_attached).toBe(false);
    expect(t13.tab_token).toBeUndefined();
    expect(t13.video_id).toBe("9bZkp7q19f0"); // from the URL only
    expect(t13.context).toBe("watch");
    validateAllSent(chrome);
  });
});

describe("request routing", () => {
  async function request(chrome: FakeChrome, frame: Record<string, unknown>): Promise<BridgeResponse> {
    const port = chrome.lastPort;
    const before = port.framesOfType<BridgeResponse>("bridge_response").length;
    port.receive({ type: "bridge_request", request_id: REQ, ...frame });
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 20 && port.framesOfType("bridge_response").length === before; i++) await vi.advanceTimersByTimeAsync(50);
    const responses = port.framesOfType<BridgeResponse>("bridge_response");
    expect(responses.length).toBe(before + 1);
    schemas.validateBridgeFrame("extension_to_agent", responses[before]);
    return responses[before]!;
  }

  it("list_tabs returns a tabs_result with fresh state", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const r = await request(chrome, { op: "list_tabs", args: {} });
    expect(r.ok).toBe(true);
    const tabs = (r.result as { tabs: Array<{ tab_id: number; script_attached: boolean }> }).tabs;
    expect(tabs.map((t) => [t.tab_id, t.script_attached])).toEqual([
      [11, true],
      [12, true],
      [13, false],
    ]);
  });

  it("routes a tab op to the content script and returns youtube_state_result", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const r = await request(chrome, { op: "set_paused", args: { tab_id: 11, tab_token: TOKENS[11], paused: true }, timeout_ms: 8000 });
    expect(r.ok).toBe(true);
    const result = r.result as { tab: { tab_id: number; paused: boolean; tab_token: string } };
    expect(result.tab.tab_id).toBe(11);
    expect(result.tab.paused).toBe(true);
    expect(result.tab.tab_token).toBe(TOKENS[11]);
  });

  it("next carries previous_video_id and emits a player_state event", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const r = await request(chrome, { op: "next", args: { tab_id: 11, tab_token: TOKENS[11], expected_video_id: "dQw4w9WgXcQ" }, timeout_ms: 10000 });
    expect(r.ok).toBe(true);
    const result = r.result as { tab: { video_id: string }; previous_video_id: string };
    expect(result.previous_video_id).toBe("dQw4w9WgXcQ");
    expect(result.tab.video_id).toBe("9bZkp7q19f0");
    const states = chrome.lastPort.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "player_state");
    expect(states.length).toBeGreaterThanOrEqual(1);
    expect(states[states.length - 1]!.tab!.video_id).toBe("9bZkp7q19f0");
  });

  it("token mismatch from the content script is passed through as TARGET_CHANGED", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const r = await request(chrome, { op: "set_paused", args: { tab_id: 11, tab_token: "ZZZZZZZZZZZZZZZZZZZZZZ", paused: true } });
    expect(r.ok).toBe(false);
    expect(r.error?.code).toBe("TARGET_CHANGED");
  });

  it("TARGET_GONE for a closed tab or a tab that left YouTube", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    expect((await request(chrome, { op: "get_state", args: { tab_id: 99, tab_token: TOKENS[11] } })).error?.code).toBe("TARGET_GONE");
    expect((await request(chrome, { op: "get_state", args: { tab_id: 20, tab_token: TOKENS[11] } })).error?.code).toBe("TARGET_GONE");
  });

  it("TAB_NOT_CONTROLLABLE when the content script is not attached or the tab is discarded", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const r = await request(chrome, { op: "set_paused", args: { tab_id: 13, tab_token: TOKENS[11], paused: true } });
    expect(r.error?.code).toBe("TAB_NOT_CONTROLLABLE");
    chrome.updateTab(12, { discarded: true });
    const d = await request(chrome, { op: "set_paused", args: { tab_id: 12, tab_token: TOKENS[12], paused: true } });
    expect(d.error?.code).toBe("TAB_NOT_CONTROLLABLE");
  });

  it("a content script that never answers yields OUTCOME_UNKNOWN for mutating ops and TAB_NOT_CONTROLLABLE for get_state", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome, { next: () => new Promise<ContentReply>(() => undefined), get_state: () => new Promise<ContentReply>(() => undefined) });
    await boot(chrome);
    const port = chrome.lastPort;
    port.receive({ type: "bridge_request", request_id: REQ, op: "next", args: { tab_id: 11, tab_token: TOKENS[11] }, timeout_ms: 3000 });
    await vi.advanceTimersByTimeAsync(2500);
    expect(port.framesOfType("bridge_response")).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(600);
    const responses = port.framesOfType<BridgeResponse>("bridge_response");
    expect(responses).toHaveLength(1);
    expect(responses[0]!.error?.code).toBe("OUTCOME_UNKNOWN");
    port.receive({ type: "bridge_request", request_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", op: "get_state", args: { tab_id: 11, tab_token: TOKENS[11] }, timeout_ms: 1000 });
    await vi.advanceTimersByTimeAsync(1100);
    expect(port.framesOfType<BridgeResponse>("bridge_response")[1]!.error?.code).toBe("TAB_NOT_CONTROLLABLE");
    validateAllSent(chrome);
  });

  it("content script deadline is shorter than the agent timeout", async () => {
    const chrome = new FakeChrome();
    let seenDeadline = 0;
    setupTabs(chrome, {
      get_state: (msg) => {
        seenDeadline = msg.deadline_ms;
        return { ok: true, token: TOKENS[11]!, state: snapshotFor(FIXTURE.tabs[0]!) };
      },
    });
    await boot(chrome);
    await request(chrome, { op: "get_state", args: { tab_id: 11, tab_token: TOKENS[11] }, timeout_ms: 10000 });
    expect(seenDeadline).toBeLessThan(10000);
    expect(seenDeadline).toBeGreaterThanOrEqual(5000);
  });

  it("a tabs.onUpdated(loading) caused by next (SPA navigation) does not turn the observed success into TARGET_GONE", async () => {
    const chrome = new FakeChrome();
    const NEW_URL = "https://www.youtube.com/watch?v=9bZkp7q19f0";
    setupTabs(chrome, {
      next: () =>
        new Promise<ContentReply>((resolve) => {
          // The URL change of the navigation is reported by Chrome while the content script is still
          // alive and before it has replied (it waits for the transition and the page to settle).
          chrome.updateTab(11, { status: "loading", url: NEW_URL });
          setTimeout(() => resolve({ ok: true, token: TOKENS[11]!, state: { ...snapshotFor(FIXTURE.tabs[0]!), video_id: "9bZkp7q19f0" }, previous_video_id: "dQw4w9WgXcQ" }), 450);
        }),
    });
    const bg = await boot(chrome);
    const port = chrome.lastPort;
    const flickers = () => port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed" && e.tabs!.some((t) => t.tab_id === 11 && !t.script_attached)).length;
    const n0 = flickers();
    const r = await request(chrome, { op: "next", args: { tab_id: 11, tab_token: TOKENS[11], expected_video_id: "dQw4w9WgXcQ" }, timeout_ms: 10000 });
    expect(r.ok).toBe(true);
    const result = r.result as { tab: { video_id: string; script_attached: boolean; tab_token: string }; previous_video_id: string };
    expect(result.previous_video_id).toBe("dQw4w9WgXcQ");
    expect(result.tab.video_id).toBe("9bZkp7q19f0");
    expect(result.tab.script_attached).toBe(true);
    expect(result.tab.tab_token).toBe(TOKENS[11]);
    await vi.advanceTimersByTimeAsync(2500);
    expect(flickers()).toBe(n0); // no "reload needed" announcement for an in-site navigation
    expect(bg.registry.get(11)?.token).toBe(TOKENS[11]);
    chrome.updateTab(11, { status: "complete" });
    await vi.advanceTimersByTimeAsync(2500);
    expect(flickers()).toBe(n0);
    validateAllSent(chrome);
  });

  it("composes the result from the attachment recorded from the reply even if the cache entry vanished meanwhile", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    const bg = await boot(chrome);
    const realGet = chrome.tabs.get;
    let calls = 0;
    chrome.tabs.get = async (tabId: number) => {
      const tab = await realGet(tabId);
      // The first tabs.get is the pre-op check; the second is composeById after the reply was recorded.
      if (tabId === 11 && ++calls === 2) bg.registry.detach(11); // concurrent deletion between record() and compose()
      return tab;
    };
    const r = await request(chrome, { op: "set_paused", args: { tab_id: 11, tab_token: TOKENS[11], paused: true }, timeout_ms: 8000 });
    expect(r.ok).toBe(true);
    expect((r.result as { tab: { paused: boolean } }).tab.paused).toBe(true);
  });

  it("the time budget keeps the content deadline strictly shorter than the background wait for every schema value", async () => {
    for (const t of [100, 250, 699, 900, 999, 1000, 1500, 8000, 10000, 60000, 90000, undefined]) {
      const b = computeBudget(t);
      expect(b.deadlineMs, String(t)).toBeLessThan(b.bgWaitMs);
      expect(b.deadlineMs, String(t)).toBeGreaterThanOrEqual(MIN_BUDGET_MS - 700);
      expect(b.bgWaitMs, String(t)).toBeLessThanOrEqual(60000);
    }
    expect(computeBudget(100)).toEqual({ bgWaitMs: 800, deadlineMs: 300 });
    expect(computeBudget(8000)).toEqual({ bgWaitMs: 7800, deadlineMs: 7300 });
    const chrome = new FakeChrome();
    let seenDeadline = 0;
    setupTabs(chrome, {
      set_paused: (msg) => {
        seenDeadline = msg.deadline_ms;
        return { ok: true, token: TOKENS[11]!, state: { ...snapshotFor(FIXTURE.tabs[0]!), paused: true } };
      },
    });
    await boot(chrome);
    const r = await request(chrome, { op: "set_paused", args: { tab_id: 11, tab_token: TOKENS[11], paused: true }, timeout_ms: 100 });
    expect(r.ok).toBe(true); // a prompt reply is never reported as OUTCOME_UNKNOWN
    expect(seenDeadline).toBe(300);
    expect(seenDeadline).toBeLessThan(computeBudget(100).bgWaitMs);
  });

  it("missing tab_id is INVALID_PARAMETERS", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    expect((await request(chrome, { op: "set_paused", args: { paused: true } })).error?.code).toBe("INVALID_PARAMETERS");
  });

  it("a malformed agent frame gets bridge_error MALFORMED_MESSAGE and the worker keeps working", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const port = chrome.lastPort;
    port.receive({ type: "bridge_request", request_id: REQ, op: "eval", args: {} });
    port.receive({ type: "bridge_request", request_id: REQ, op: "next", args: { tab_id: 11 }, extra: true });
    port.receive("garbage");
    await vi.advanceTimersByTimeAsync(0);
    const errors = port.framesOfType<{ error: { code: string }; ref_request_id?: string }>("bridge_error");
    expect(errors).toHaveLength(3);
    expect(errors.every((e) => e.error.code === "MALFORMED_MESSAGE")).toBe(true);
    expect(errors[0]!.ref_request_id).toBe(REQ);
    const r = await request(chrome, { op: "list_tabs", args: {} });
    expect(r.ok).toBe(true);
    validateAllSent(chrome);
  });
});

describe("events", () => {
  it("player_state from a tab is forwarded at most twice per second", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const port = chrome.lastPort;
    const before = port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "player_state").length;
    const state = snapshotFor(FIXTURE.tabs[0]!);
    for (let i = 0; i < 10; i++) {
      await chrome.messageFromContent(11, { kind: CS_KIND, type: "player_state", token: TOKENS[11], state: { ...state, position_seconds: i } });
      await vi.advanceTimersByTimeAsync(50);
    }
    await vi.advanceTimersByTimeAsync(600);
    const after = port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "player_state").length;
    expect(after - before).toBeLessThanOrEqual(3);
    expect(after - before).toBeGreaterThanOrEqual(2);
    const last = port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "player_state").pop()!;
    expect(last.tab!.position_seconds).toBe(9); // trailing emission carries the latest state
    validateAllSent(chrome);
  });

  it("tab close / reload / attach produce debounced tabs_changed with correct attachment flags", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const port = chrome.lastPort;
    const count = () => port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed").length;
    const n0 = count();
    chrome.removeTab(12);
    chrome.reloadTab(11);
    chrome.tabs.onActivated.dispatch({ tabId: 13, windowId: 1 });
    await vi.advanceTimersByTimeAsync(100);
    expect(count()).toBe(n0); // still debouncing
    await vi.advanceTimersByTimeAsync(300);
    expect(count()).toBe(n0 + 1);
    let tabs = port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed").pop()!.tabs!;
    expect(tabs.map((t) => t.tab_id)).toEqual([11, 13]);
    expect(tabs[0]!.script_attached).toBe(false); // reloading tab lost its content script
    // New attachment with a fresh token after the reload.
    chrome.contentHandlers.set(11, scriptedContent(FIXTURE.tabs[0]!));
    const fresh = "CCCCCCCCCCCCCCCCCCCCCC";
    await chrome.messageFromContent(11, { kind: CS_KIND, type: "attached", token: fresh, state: snapshotFor(FIXTURE.tabs[0]!) });
    await vi.advanceTimersByTimeAsync(400);
    tabs = port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed").pop()!.tabs!;
    expect(tabs[0]!.tab_token).toBe(fresh);
    expect(tabs[0]!.script_attached).toBe(true);
    // detached message removes the attachment.
    await chrome.messageFromContent(11, { kind: CS_KIND, type: "detached", token: fresh });
    await vi.advanceTimersByTimeAsync(400);
    tabs = port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed").pop()!.tabs!;
    expect(tabs[0]!.script_attached).toBe(false);
    validateAllSent(chrome);
  });

  it("an in-site (SPA) URL change keeps the attachment: no script_attached:false, same token", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    const bg = await boot(chrome);
    const port = chrome.lastPort;
    const changed = () => port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed");
    const n0 = changed().length;
    chrome.updateTab(11, { status: "loading", url: "https://www.youtube.com/watch?v=9bZkp7q19f0" });
    chrome.updateTab(11, { status: "complete" });
    await vi.advanceTimersByTimeAsync(400);
    const events = changed().slice(n0);
    expect(events.length).toBeGreaterThanOrEqual(1);
    for (const e of events) {
      const t11 = e.tabs!.find((t) => t.tab_id === 11)!;
      expect(t11.script_attached).toBe(true);
      expect(t11.tab_token).toBe(TOKENS[11]);
    }
    expect(bg.registry.get(11)?.token).toBe(TOKENS[11]);
    // A full reload (content script gone, detached message lost) is still detected by the probe.
    chrome.reloadTab(11);
    await vi.advanceTimersByTimeAsync(400);
    expect(bg.registry.get(11)).toBeUndefined();
    expect(changed().pop()!.tabs!.find((t) => t.tab_id === 11)!.script_attached).toBe(false);
    validateAllSent(chrome);
  });

  it("a tabs_changed sent on an early ack is corrected once the slow probe finishes", async () => {
    const chrome = new FakeChrome();
    const slow = scriptedContent(FIXTURE.tabs[0]!);
    chrome.addTab({ id: 11, url: FIXTURE.tabs[0]!.url, title: FIXTURE.tabs[0]!.title, active: true }, (msg) => new Promise((resolve) => setTimeout(() => resolve(slow(msg)), 1000)));
    chrome.addTab({ id: 12, url: FIXTURE.tabs[1]!.url, title: FIXTURE.tabs[1]!.title }, scriptedContent(FIXTURE.tabs[1]!));
    const bg = createBackground(chrome, { browser: "chrome" });
    const started = bg.start();
    // Chrome's onStartup opens the port independently of start(), so the ack can land mid-rebuild.
    chrome.runtime.onStartup.dispatch();
    await vi.advanceTimersByTimeAsync(0);
    chrome.lastPort.receive(ACK);
    await vi.advanceTimersByTimeAsync(0);
    const port = chrome.lastPort;
    const changed = () => port.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed");
    expect(changed().length).toBe(1);
    expect(changed()[0]!.tabs!.find((t) => t.tab_id === 11)!.script_attached).toBe(false); // honest at that moment
    await vi.advanceTimersByTimeAsync(1500);
    await started;
    const last = changed().pop()!;
    expect(changed().length).toBeGreaterThanOrEqual(2);
    expect(last.tabs!.find((t) => t.tab_id === 11)!.script_attached).toBe(true);
    expect(last.tabs!.find((t) => t.tab_id === 11)!.tab_token).toBe(TOKENS[11]);
    validateAllSent(chrome);
  });

  it("ignores content messages from other senders, frames or non-YouTube pages", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    const bg = await boot(chrome);
    const state = snapshotFor(FIXTURE.tabs[0]!);
    const tab = chrome.tabList.find((t) => t.id === 20)!;
    chrome.runtime.onMessage.dispatch({ kind: CS_KIND, type: "attached", token: "DDDDDDDDDDDDDDDDDDDDDD", state }, { id: chrome.runtime.id, tab, frameId: 0, url: tab.url } as chrome.runtime.MessageSender, () => undefined);
    const yt = chrome.tabList.find((t) => t.id === 13)!;
    chrome.runtime.onMessage.dispatch({ kind: CS_KIND, type: "attached", token: "DDDDDDDDDDDDDDDDDDDDDD", state }, { id: "otherextensionidotherextensionid", tab: yt, frameId: 0, url: yt.url } as chrome.runtime.MessageSender, () => undefined);
    chrome.runtime.onMessage.dispatch({ kind: CS_KIND, type: "attached", token: "DDDDDDDDDDDDDDDDDDDDDD", state }, { id: chrome.runtime.id, tab: yt, frameId: 3, url: yt.url } as chrome.runtime.MessageSender, () => undefined);
    await vi.advanceTimersByTimeAsync(500);
    expect(bg.registry.get(20)).toBeUndefined();
    expect(bg.registry.get(13)).toBeUndefined();
    // Invalid snapshot payloads are rejected too.
    await chrome.messageFromContent(13, { kind: CS_KIND, type: "attached", token: "DDDDDDDDDDDDDDDDDDDDDD", state: { context: "evil" } });
    expect(bg.registry.get(13)).toBeUndefined();
  });

  it("clips and sanitizes untrusted page strings before they enter a frame", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    const bg = await boot(chrome);
    const state = { ...snapshotFor(FIXTURE.tabs[0]!), title: "x".repeat(500) + "\u0000", video_id: "not valid!", volume: 250.7 };
    await chrome.messageFromContent(11, { kind: CS_KIND, type: "player_state", token: TOKENS[11], state });
    const entry = bg.registry.get(11)!;
    expect(entry.snapshot.title!.length).toBe(200);
    expect(entry.snapshot.video_id).toBeUndefined();
    expect(entry.snapshot.volume).toBe(100);
  });
});

describe("reconnect and version negotiation", () => {
  it("backs off 1→2→4 s with alarms when the host is missing and reconnects on alarm", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    chrome.lastPort.fail("Specified native messaging host not found.");
    expect(bg.connection.current().state).toBe("host_missing");
    expect(chrome.alarmsCreated.at(-1)).toEqual({ name: RECONNECT_ALARM, info: { delayInMinutes: 0.5 } });
    expect(chrome.ports).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(chrome.ports).toHaveLength(2);
    chrome.lastPort.fail("Specified native messaging host not found.");
    await vi.advanceTimersByTimeAsync(1999);
    expect(chrome.ports).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(2);
    expect(chrome.ports).toHaveLength(3);
    chrome.lastPort.fail("Native host has exited.");
    // Worker could have been terminated here; the alarm alone must reconnect.
    chrome.fireAlarm(RECONNECT_ALARM);
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.ports).toHaveLength(4);
    // Later ports still send a valid hello; the stored connection state is persisted.
    expect(chrome.lastPort.framesOfType("bridge_hello")).toHaveLength(1);
    expect((chrome.store.get("connection_state") as { state: string }).state).toBe("connecting");
    // An unrelated alarm does nothing; connect() is idempotent while a port is open.
    chrome.fireAlarm("other");
    chrome.fireAlarm(RECONNECT_ALARM);
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.ports).toHaveLength(4);
  });

  it("a host that never acknowledges hello is disconnected after the ack deadline and retried with backoff", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const first = chrome.lastPort;
    await vi.advanceTimersByTimeAsync(HELLO_ACK_TIMEOUT_MS - 1);
    expect(first.disconnected).toBe(false);
    expect(bg.connection.current().state).toBe("connecting");
    await vi.advanceTimersByTimeAsync(2);
    expect(first.disconnected).toBe(true);
    expect(bg.connection.current().state).toBe("disconnected");
    expect(bg.connection.connected).toBe(false);
    expect(chrome.ports).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1000); // first backoff step
    expect(chrome.ports).toHaveLength(2);
    expect(chrome.lastPort.framesOfType("bridge_hello")).toHaveLength(1);
    chrome.lastPort.receive(ACK);
    expect(bg.connection.connected).toBe(true);
    // Once acked, the ack timer must not fire later.
    await vi.advanceTimersByTimeAsync(HELLO_ACK_TIMEOUT_MS + 1000);
    expect(bg.connection.connected).toBe(true);
    expect(chrome.ports).toHaveLength(2);
    validateAllSent(chrome);
  });

  it("forbidden host (extension id not registered) is reported as host_forbidden", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    chrome.lastPort.fail("Access to the specified native messaging host is forbidden.");
    expect(bg.connection.current().state).toBe("host_forbidden");
  });

  it("AGENT_NOT_RUNNING from the host is surfaced, the port is closed at once and retried", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const port = chrome.lastPort;
    port.receive({ type: "bridge_error", error: { code: "AGENT_NOT_RUNNING", message: "The DoMe agent is not running on this PC." } });
    expect(port.disconnected).toBe(true); // not left to the host's exit
    expect(bg.connection.current().state).toBe("agent_not_running");
    expect(bg.connection.current().message).toBe("The DoMe agent is not running on this PC.");
    port.fail("Native host has exited."); // the stale port's onDisconnect changes nothing
    expect(bg.connection.current().state).toBe("agent_not_running");
    await vi.advanceTimersByTimeAsync(1000);
    expect(chrome.ports).toHaveLength(2);
  });

  it("AGENT_DISCONNECTED after the ack drops the port at once and reconnects after the backoff", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    const bg = await boot(chrome);
    const first = chrome.lastPort;
    expect(bg.connection.connected).toBe(true);
    // The agent quit; the old host process may keep its end of the port open (it does not exit).
    first.receive({ type: "bridge_error", error: { code: "AGENT_DISCONNECTED", message: "The DoMe agent disconnected." } });
    expect(first.disconnected).toBe(true);
    expect(bg.connection.connected).toBe(false);
    expect(bg.connection.current().state).toBe("disconnected");
    expect(chrome.alarmsCreated.at(-1)).toEqual({ name: RECONNECT_ALARM, info: { delayInMinutes: 0.5 } });
    // A request still arriving on the dropped port is ignored, not answered.
    first.receive({ type: "bridge_request", request_id: REQ, op: "list_tabs", args: {} });
    await vi.advanceTimersByTimeAsync(999);
    expect(chrome.ports).toHaveLength(1);
    expect(first.framesOfType("bridge_response")).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1); // backoff restarted at 1 s because the last attempt was acked
    expect(chrome.ports).toHaveLength(2);
    const second = chrome.lastPort;
    expect(second.framesOfType("bridge_hello")).toHaveLength(1);
    second.receive(ACK);
    await vi.advanceTimersByTimeAsync(400);
    expect(bg.connection.connected).toBe(true);
    expect(bg.connection.current().state).toBe("connected");
    // The new agent learns the tabs again.
    expect(second.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed").length).toBeGreaterThanOrEqual(1);
    validateAllSent(chrome);
  });

  it("caps the backoff at 8 s so an agent started later is found within ~10 s, and resets it after an ack", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const notRunning = () => chrome.lastPort.receive({ type: "bridge_error", error: { code: "AGENT_NOT_RUNNING", message: "The DoMe agent is not running on this PC." } });
    const delays: number[] = [];
    for (let i = 0; i < 7; i++) {
      notRunning();
      const before = chrome.ports.length;
      let waited = 0;
      while (chrome.ports.length === before) {
        await vi.advanceTimersByTimeAsync(500);
        waited += 500;
        expect(waited).toBeLessThanOrEqual(10_000);
      }
      delays.push(waited);
    }
    expect(delays).toEqual([1000, 2000, 4000, 8000, 8000, 8000, 8000]);
    expect(Math.max(...chrome.alarmsCreated.map((a) => a.info.delayInMinutes ?? 0))).toBe(0.5);
    // The agent is up now: ack, then lose it again → the next retry is after 1 s, not 8 s.
    chrome.lastPort.receive(ACK);
    expect(bg.connection.connected).toBe(true);
    chrome.lastPort.receive({ type: "bridge_error", error: { code: "AGENT_DISCONNECTED", message: "The DoMe agent disconnected." } });
    const before = chrome.ports.length;
    await vi.advanceTimersByTimeAsync(1000);
    expect(chrome.ports).toHaveLength(before + 1);
  });

  /** Milliseconds until the next native port opens without an alarm (500 ms steps), or null if none opens within `limitMs`. */
  async function nextAttemptAfter(chrome: FakeChrome, limitMs = 10_000): Promise<number | null> {
    const before = chrome.ports.length;
    let waited = 0;
    while (chrome.ports.length === before && waited < limitMs) {
      await vi.advanceTimersByTimeAsync(500);
      waited += 500;
    }
    return chrome.ports.length === before ? null : waited;
  }

  /** Answer every attempt with AGENT_NOT_RUNNING until the timers stop retrying; returns the delays between attempts. */
  async function failUntilSlow(chrome: FakeChrome): Promise<number[]> {
    const delays: number[] = [];
    for (;;) {
      chrome.lastPort.receive(NOT_RUNNING);
      const waited = await nextAttemptAfter(chrome);
      if (waited === null) return delays;
      delays.push(waited);
      expect(delays.length).toBeLessThanOrEqual(FAST_RETRY_MAX_ATTEMPTS);
    }
  }

  it("retries fast for about 5 minutes, then only through the 30 s alarm so the worker can sleep", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const delays = await failUntilSlow(chrome);
    expect(delays.slice(0, 4)).toEqual([1000, 2000, 4000, 8000]);
    expect(new Set(delays.slice(3))).toEqual(new Set([8000]));
    const fastPhase = delays.reduce((a, b) => a + b, 0);
    expect(fastPhase).toBeGreaterThanOrEqual(FAST_RETRY_WINDOW_MS);
    expect(fastPhase).toBeLessThan(FAST_RETRY_WINDOW_MS + 8000);
    expect(chrome.ports).toHaveLength(delays.length + 1);
    // Slow phase: no timer is left in the worker, only the alarm.
    expect(vi.getTimerCount()).toBe(0);
    expect(chrome.alarmsCreated.at(-1)).toEqual({ name: RECONNECT_ALARM, info: { delayInMinutes: SLOW_RETRY_SECONDS / 60 } });
    expect(bg.connection.current().state).toBe("agent_not_running");
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(chrome.ports).toHaveLength(delays.length + 1);
    chrome.fireAlarm(RECONNECT_ALARM);
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.ports).toHaveLength(delays.length + 2);
    chrome.lastPort.receive(NOT_RUNNING);
    expect(await nextAttemptAfter(chrome, 60_000)).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    // The agent starts: the next alarm finds it and the ack ends the slow phase.
    chrome.fireAlarm(RECONNECT_ALARM);
    await vi.advanceTimersByTimeAsync(0);
    chrome.lastPort.receive(ACK);
    expect(bg.connection.connected).toBe(true);
  });

  it("a worker woken by the alarm in the slow phase stays slow; a browser restart is fast again", async () => {
    const chrome = new FakeChrome();
    await boot(chrome, { ack: false });
    await failUntilSlow(chrome);
    // The idle worker was stopped; the alarm starts a new one, which tries once at start.
    const woken = chrome.restartWorker();
    await boot(woken, { ack: false });
    expect(woken.ports).toHaveLength(1);
    woken.lastPort.receive(NOT_RUNNING);
    expect(await nextAttemptAfter(woken, 60_000)).toBeNull();
    expect(woken.alarmsCreated.at(-1)!.info.delayInMinutes).toBe(SLOW_RETRY_SECONDS / 60);
    expect(vi.getTimerCount()).toBe(0);
    // A browser restart clears storage.session (and fires onStartup): fast retries again.
    const restarted = new FakeChrome(chrome.store);
    await boot(restarted, { ack: false });
    restarted.lastPort.receive(NOT_RUNNING);
    expect(await nextAttemptAfter(restarted)).toBe(1000);
  });

  it("browser start, Retry and an ack each bring back the fast retries", async () => {
    const chrome = new FakeChrome();
    await boot(chrome, { ack: false });
    await failUntilSlow(chrome);
    chrome.runtime.onStartup.dispatch();
    await vi.advanceTimersByTimeAsync(0);
    chrome.lastPort.receive(NOT_RUNNING);
    expect(await nextAttemptAfter(chrome)).toBe(1000);

    await failUntilSlow(chrome);
    await chrome.messageFromPopup({ kind: POPUP_KIND, type: "reconnect" });
    chrome.lastPort.receive(NOT_RUNNING);
    expect(await nextAttemptAfter(chrome)).toBe(1000);

    await failUntilSlow(chrome);
    chrome.fireAlarm(RECONNECT_ALARM);
    await vi.advanceTimersByTimeAsync(0);
    chrome.lastPort.receive(ACK);
    chrome.lastPort.receive(AGENT_GONE);
    expect(await nextAttemptAfter(chrome)).toBe(1000);
    chrome.lastPort.receive(NOT_RUNNING);
    expect(await nextAttemptAfter(chrome)).toBe(2000);
  });

  it("PROTOCOL_INCOMPATIBLE from the agent closes the port and retries slowly, even when the host then reports AGENT_DISCONNECTED", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const port = chrome.lastPort;
    port.receive({ type: "bridge_error", error: { code: "PROTOCOL_INCOMPATIBLE", message: "The DoMe extension and agent speak incompatible protocol versions.", detail: { peer: ["1.0", "1.1"], supported: ["2.0"] } } });
    expect(port.disconnected).toBe(true);
    expect(bg.connection.current().state).toBe("incompatible");
    expect(chrome.alarmsCreated.at(-1)!.info.delayInMinutes).toBe(5);
    port.receive({ type: "bridge_error", error: { code: "AGENT_DISCONNECTED", message: "The DoMe agent disconnected." } });
    expect(bg.connection.current().state).toBe("incompatible");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(chrome.ports).toHaveLength(1);
  });

  it("incompatible agent protocol: bridge_error PROTOCOL_INCOMPATIBLE with both version lists, port closed, slow retry", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const port = chrome.lastPort;
    port.receive({ type: "bridge_hello_ack", protocol_version: "2.0", agent_version: "9.9.9" });
    const err = port.framesOfType<{ error: { code: string; detail: { peer: string[]; supported: string[] } } }>("bridge_error")[0]!;
    expect(err.error.code).toBe("PROTOCOL_INCOMPATIBLE");
    expect(err.error.detail).toEqual({ peer: ["2.0"], supported: ["1.0", "1.1"] });
    expect(port.disconnected).toBe(true);
    expect(bg.connection.current().state).toBe("incompatible");
    expect(bg.connection.connected).toBe(false);
    expect(chrome.alarmsCreated.at(-1)!.info.delayInMinutes).toBe(5);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(chrome.ports).toHaveLength(1); // no fast retry
    validateAllSent(chrome);
  });

  it("an ack of 1.0 or 1.1 is accepted; 1.2 is refused until the extension speaks it", async () => {
    for (const version of ["1.0", "1.1"]) {
      const chrome = new FakeChrome();
      const bg = await boot(chrome, { ack: false });
      chrome.lastPort.receive({ type: "bridge_hello_ack", protocol_version: version, agent_version: "0.2.0" });
      expect(bg.connection.connected, version).toBe(true);
      expect(bg.connection.current()).toMatchObject({ state: "connected", protocol_version: version });
      expect(chrome.lastPort.framesOfType("bridge_error")).toHaveLength(0);
    }
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    chrome.lastPort.receive({ type: "bridge_hello_ack", protocol_version: "1.2", agent_version: "0.3.0" });
    expect(bg.connection.connected).toBe(false);
    expect(bg.connection.current().state).toBe("incompatible");
    const err = chrome.lastPort.framesOfType<{ error: { code: string; detail: { peer: string[]; supported: string[] } } }>("bridge_error")[0]!;
    expect(err.error).toMatchObject({ code: "PROTOCOL_INCOMPATIBLE", detail: { peer: ["1.2"], supported: ["1.0", "1.1"] } });
    validateAllSent(chrome);
  });

  it("requests before the ack are refused, and connectNative throwing is retried", async () => {
    const chrome = new FakeChrome();
    await boot(chrome, { ack: false });
    chrome.lastPort.receive({ type: "bridge_request", request_id: REQ, op: "list_tabs", args: {} });
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.lastPort.framesOfType("bridge_response")).toHaveLength(0);
    expect(chrome.lastPort.framesOfType<{ ref_request_id: string }>("bridge_error")[0]!.ref_request_id).toBe(REQ);

    const broken = new FakeChrome();
    broken.connectNativeError = new Error("connectNative unavailable");
    const bg = createBackground(broken, { browser: "edge" });
    await bg.start();
    expect(bg.connection.current().state).toBe("error");
    broken.connectNativeError = null;
    await vi.advanceTimersByTimeAsync(1000);
    expect(broken.ports).toHaveLength(1);
    expect(broken.lastPort.framesOfType<BridgeHello>("bridge_hello")[0]!.browser).toBe("edge");
  });
});

describe("content script injection into tabs open before install", () => {
  it("on install, runs content.js once in every open YouTube tab, skipping discarded and non-YouTube tabs", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    chrome.addTab({ id: 14, url: "https://www.youtube.com/watch?v=abcdefghijk", title: "Sleeping - YouTube", discarded: true });
    const bg = await boot(chrome);
    // A worker start alone injects only where the probe found no content script (see the next block).
    expect(chrome.injections.map((i) => i.tabId)).toEqual([13]);
    chrome.injections.splice(0);
    chrome.runtime.onInstalled.dispatch({ reason: "install" });
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.injections.map((i) => i.tabId).sort()).toEqual([11, 12, 13]);
    for (const i of chrome.injections) expect(i.files).toEqual([CONTENT_SCRIPT_FILE]);
    expect(chrome.ports).toHaveLength(1); // connect() stays idempotent while the port is open
    // The injected script in the tab that had none attaches like a freshly loaded page.
    const tab13 = FIXTURE.tabs.find((t) => t.id === 13)!;
    const token = "DDDDDDDDDDDDDDDDDDDDDD";
    await chrome.messageFromContent(13, { kind: CS_KIND, type: "attached", token, state: snapshotFor(tab13) });
    await vi.advanceTimersByTimeAsync(400);
    const tabs = chrome.lastPort.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed").pop()!.tabs!;
    expect(tabs.find((t) => t.tab_id === 13)).toMatchObject({ script_attached: true, tab_token: token });
    expect(bg.registry.attachedCount()).toBe(3);
    validateAllSent(chrome);
  });

  it("on browser start, injects the same way; a tab that refuses injection does not stop the others", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    chrome.injectionErrors.set(11, "Frame with ID 0 is showing error page");
    await boot(chrome);
    chrome.injections.splice(0);
    chrome.runtime.onStartup.dispatch();
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.injections.map((i) => i.tabId).sort()).toEqual([12, 13]);
    expect(chrome.ports).toHaveLength(1);
  });

  it("a worker start gives content.js to YouTube tabs that have none (extension disabled and enabled again, crashed renderer)", async () => {
    const before = new FakeChrome();
    setupTabs(before);
    before.addTab({ id: 14, url: "https://www.youtube.com/watch?v=abcdefghijk", title: "Sleeping - YouTube", discarded: true });
    await boot(before);
    // Disabling the extension cuts off the content scripts in open tabs; enabling it starts a new
    // worker with neither onInstalled nor onStartup. Tab 12 still has a live script (a renderer that
    // was not affected), so it must not be touched.
    const chrome = before.restartWorker();
    chrome.contentHandlers.delete(11);
    chrome.injectionErrors.set(13, "Frame with ID 0 is showing error page");
    const bg = await boot(chrome);
    expect(chrome.injections.map((i) => i.tabId)).toEqual([11]); // 13 refused, 14 discarded, 20 not YouTube
    expect(chrome.injections[0]!.files).toEqual([CONTENT_SCRIPT_FILE]);
    // The injected script attaches like a freshly loaded page and the agent learns about it.
    const fresh = "EEEEEEEEEEEEEEEEEEEEEE";
    await chrome.messageFromContent(11, { kind: CS_KIND, type: "attached", token: fresh, state: snapshotFor(FIXTURE.tabs[0]!) });
    await vi.advanceTimersByTimeAsync(400);
    const tabs = chrome.lastPort.framesOfType<BridgeEvent>("bridge_event").filter((e) => e.event === "tabs_changed").pop()!.tabs!;
    expect(tabs.find((t) => t.tab_id === 11)).toMatchObject({ script_attached: true, tab_token: fresh });
    expect(tabs.find((t) => t.tab_id === 12)).toMatchObject({ script_attached: true, tab_token: TOKENS[12] });
    expect(bg.registry.attachedCount()).toBe(2);
    validateAllSent(chrome);
  });

  it("an injection that never finishes does not hold up connecting to the agent", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    chrome.injectionHangs.add(13);
    const bg = createBackground(chrome, { browser: "chrome" });
    void bg.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.ports).toHaveLength(1);
    chrome.lastPort.receive(ACK);
    expect(bg.connection.connected).toBe(true);
    expect(chrome.injections).toHaveLength(0); // still pending in tab 13
  });

  it("install before the agent runs: injects and starts connecting", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    const bg = createBackground(chrome, { browser: "edge" });
    chrome.runtime.onInstalled.dispatch({ reason: "install" });
    await vi.advanceTimersByTimeAsync(0);
    expect(chrome.ports).toHaveLength(1);
    expect(chrome.injections.map((i) => i.tabId).sort()).toEqual([11, 12, 13]);
    expect(bg.connection.current().state).toBe("connecting");
  });
});

describe("popup messages", () => {
  it("reports status and applies a sanitized profile label with a re-hello", async () => {
    const chrome = new FakeChrome();
    setupTabs(chrome);
    await boot(chrome);
    const status = (await chrome.messageFromPopup({ kind: POPUP_KIND, type: "status" })) as StatusReport;
    expect(status.connection.state).toBe("connected");
    expect(status.connection.agent_version).toBe("0.1.0");
    expect(status.extension_id).toBe(chrome.runtime.id);
    expect(status.attached_tabs).toBe(2);
    expect(status.profile_label).toBe("");
    const updated = (await chrome.messageFromPopup({ kind: POPUP_KIND, type: "set_profile_label", value: "  Work\u0000 Chrome " + "x".repeat(100) })) as StatusReport;
    expect(updated.profile_label.startsWith("Work Chrome")).toBe(true);
    expect(updated.profile_label.length).toBe(64);
    await vi.advanceTimersByTimeAsync(0);
    const hellos = chrome.lastPort.framesOfType<BridgeHello>("bridge_hello");
    expect(hellos).toHaveLength(2);
    expect(hellos[1]!.profile_label).toBe(updated.profile_label);
    validateAllSent(chrome);
  });

  it("reconnect request opens a new port when disconnected", async () => {
    const chrome = new FakeChrome();
    await boot(chrome, { ack: false });
    chrome.lastPort.fail("Native host has exited.");
    await chrome.messageFromPopup({ kind: POPUP_KIND, type: "reconnect" });
    expect(chrome.ports).toHaveLength(2);
  });

  it("Retry after the agent went away opens a new port at once, without waiting for the backoff", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome);
    const first = chrome.lastPort;
    first.receive({ type: "bridge_error", error: { code: "AGENT_DISCONNECTED", message: "The DoMe agent disconnected." } });
    const status = (await chrome.messageFromPopup({ kind: POPUP_KIND, type: "reconnect" })) as StatusReport;
    expect(chrome.ports).toHaveLength(2);
    expect(status.connection.state).toBe("connecting");
    expect(chrome.lastPort.framesOfType("bridge_hello")).toHaveLength(1);
    // The pending backoff timer must not open a third port.
    await vi.advanceTimersByTimeAsync(9000);
    expect(chrome.ports).toHaveLength(2);
    chrome.lastPort.receive(ACK);
    expect(bg.connection.connected).toBe(true);
  });

  it("Retry while a hello is still unanswered replaces the port; Retry while connected does nothing", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const stuck = chrome.lastPort;
    await chrome.messageFromPopup({ kind: POPUP_KIND, type: "reconnect" });
    expect(stuck.disconnected).toBe(true);
    expect(chrome.ports).toHaveLength(2);
    chrome.lastPort.receive(ACK);
    expect(bg.connection.connected).toBe(true);
    const status = (await chrome.messageFromPopup({ kind: POPUP_KIND, type: "reconnect" })) as StatusReport;
    expect(status.connection.state).toBe("connected");
    expect(chrome.ports).toHaveLength(2);
    expect(chrome.lastPort.disconnected).toBe(false);
  });

  it("ignores unknown messages", async () => {
    const chrome = new FakeChrome();
    await boot(chrome);
    expect(await chrome.messageFromPopup({ kind: "something_else" })).toBeUndefined();
    expect(await chrome.messageFromPopup({ kind: BG_KIND, type: "op" })).toBeUndefined();
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { schemas } from "@dome/protocol";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NATIVE_HOST_NAME, RECONNECT_ALARM } from "../src/background/connection.ts";
import type { BridgeEvent, BridgeHello, BridgeResponse } from "../src/background/frames.ts";
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
const ACK = { type: "bridge_hello_ack", protocol_version: "1.0", agent_version: "0.1.0" };

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
    expect(hello.protocol_versions).toEqual(["1.0"]);
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
    chrome.updateTab(11, { status: "loading" });
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

  it("forbidden host (extension id not registered) is reported as host_forbidden", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    chrome.lastPort.fail("Access to the specified native messaging host is forbidden.");
    expect(bg.connection.current().state).toBe("host_forbidden");
  });

  it("AGENT_NOT_RUNNING from the host is surfaced and retried", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    chrome.lastPort.receive({ type: "bridge_error", error: { code: "AGENT_NOT_RUNNING", message: "The DoMe agent is not running on this PC." } });
    chrome.lastPort.fail("Native host has exited.");
    expect(bg.connection.current().state).toBe("agent_not_running");
    await vi.advanceTimersByTimeAsync(1000);
    expect(chrome.ports).toHaveLength(2);
  });

  it("incompatible agent protocol: bridge_error PROTOCOL_INCOMPATIBLE with both version lists, port closed, slow retry", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    const port = chrome.lastPort;
    port.receive({ type: "bridge_hello_ack", protocol_version: "2.0", agent_version: "9.9.9" });
    const err = port.framesOfType<{ error: { code: string; detail: { peer: string[]; supported: string[] } } }>("bridge_error")[0]!;
    expect(err.error.code).toBe("PROTOCOL_INCOMPATIBLE");
    expect(err.error.detail).toEqual({ peer: ["2.0"], supported: ["1.0"] });
    expect(port.disconnected).toBe(true);
    expect(bg.connection.current().state).toBe("incompatible");
    expect(bg.connection.connected).toBe(false);
    expect(chrome.alarmsCreated.at(-1)!.info.delayInMinutes).toBe(5);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(chrome.ports).toHaveLength(1); // no fast retry
    validateAllSent(chrome);
  });

  it("a newer compatible minor version from the agent is accepted", async () => {
    const chrome = new FakeChrome();
    const bg = await boot(chrome, { ack: false });
    chrome.lastPort.receive({ type: "bridge_hello_ack", protocol_version: "1.0", agent_version: "0.2.0" });
    expect(bg.connection.connected).toBe(true);
    const chrome2 = new FakeChrome();
    const bg2 = await boot(chrome2, { ack: false });
    chrome2.lastPort.receive({ type: "bridge_hello_ack", protocol_version: "1.3", agent_version: "0.2.0" });
    expect(bg2.connection.connected).toBe(false); // extension would need the update
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

  it("ignores unknown messages", async () => {
    const chrome = new FakeChrome();
    await boot(chrome);
    expect(await chrome.messageFromPopup({ kind: "something_else" })).toBeUndefined();
    expect(await chrome.messageFromPopup({ kind: BG_KIND, type: "op" })).toBeUndefined();
  });
});

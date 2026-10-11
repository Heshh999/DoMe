// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { INSTANCE_KEY, startContentScript, startContentScriptOnce, type ContentRuntime } from "../src/content/index.ts";
import type { ContentReply, ContentToBackground } from "../src/shared/messages.ts";
import { BG_KIND, CS_KIND } from "../src/shared/messages.ts";
import { installFakeVideo, loadFixture, video, wireNavigation } from "./dom-helpers.ts";

type Listener = (message: unknown, sender: chrome.runtime.MessageSender, sendResponse: (reply: ContentReply) => void) => boolean | undefined | void;

function fakeRuntime(): { runtime: ContentRuntime; sent: ContentToBackground[]; listeners: Listener[]; failSends: () => void } {
  const sent: ContentToBackground[] = [];
  const listeners: Listener[] = [];
  let failing = false;
  return {
    sent,
    listeners,
    failSends: () => {
      failing = true;
    },
    runtime: {
      id: "abcdefghijklmnopabcdefghijklmnop",
      onMessage: { addListener: (cb) => listeners.push(cb) },
      sendMessage: async (message) => {
        if (failing) throw new Error("Extension context invalidated.");
        sent.push(structuredClone(message));
        return undefined;
      },
    },
  };
}

const EXT_SENDER = { id: "abcdefghijklmnopabcdefghijklmnop" } as chrome.runtime.MessageSender;

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("content script entry", () => {
  it("announces attachment with a fresh token, answers probes and routes ops", async () => {
    loadFixture("watch-playing", "/watch?v=dQw4w9WgXcQ");
    const fake = installFakeVideo(video());
    const rt = fakeRuntime();
    const { token } = startContentScript(rt.runtime, document, window);
    await vi.advanceTimersByTimeAsync(0);
    expect(token).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(rt.sent[0]).toMatchObject({ kind: CS_KIND, type: "attached", token });
    expect((rt.sent[0] as { state: { video_id: string } }).state.video_id).toBe("dQw4w9WgXcQ");

    const listener = rt.listeners[0]!;
    let reply: ContentReply | undefined;
    const ret = listener({ kind: BG_KIND, type: "probe" }, EXT_SENDER, (r) => (reply = r));
    expect(ret).toBeUndefined();
    expect(reply).toMatchObject({ ok: true, token });

    reply = undefined;
    const keepOpen = listener({ kind: BG_KIND, type: "op", op: "set_paused", args: { tab_token: token, paused: true }, deadline_ms: 5000 }, EXT_SENDER, (r) => (reply = r));
    expect(keepOpen).toBe(true);
    await vi.advanceTimersByTimeAsync(10);
    expect(reply).toMatchObject({ ok: true, token });
    expect(fake.paused).toBe(true);
  });

  it("ignores messages from other extensions, from tabs, and malformed ones", async () => {
    loadFixture("watch-playing", "/watch?v=dQw4w9WgXcQ");
    installFakeVideo(video());
    const rt = fakeRuntime();
    startContentScript(rt.runtime, document, window);
    const listener = rt.listeners[0]!;
    let replies = 0;
    listener({ kind: BG_KIND, type: "probe" }, { id: "otherotherotherotherotherotherot" } as chrome.runtime.MessageSender, () => replies++);
    listener({ kind: BG_KIND, type: "probe" }, { ...EXT_SENDER, tab: { id: 5 } as chrome.tabs.Tab }, () => replies++);
    listener({ kind: BG_KIND, type: "op", op: "eval", args: {}, deadline_ms: 1 }, EXT_SENDER, () => replies++);
    listener("hello", EXT_SENDER, () => replies++);
    expect(replies).toBe(0);
  });

  it("emits player_state on video events at most every 500 ms and re-acquires after SPA navigation", async () => {
    loadFixture("watch-playing", "/watch?v=dQw4w9WgXcQ");
    installFakeVideo(video());
    wireNavigation(".ytp-next-button", "/watch?v=9bZkp7q19f0");
    const rt = fakeRuntime();
    startContentScript(rt.runtime, document, window);
    await vi.advanceTimersByTimeAsync(0);
    const statesBefore = rt.sent.filter((m) => m.type === "player_state").length;
    for (let i = 0; i < 6; i++) {
      video().dispatchEvent(new Event("pause"));
      video().dispatchEvent(new Event("play"));
      await vi.advanceTimersByTimeAsync(100);
    }
    await vi.advanceTimersByTimeAsync(600);
    const states = rt.sent.filter((m) => m.type === "player_state").length - statesBefore;
    expect(states).toBeGreaterThanOrEqual(1);
    expect(states).toBeLessThanOrEqual(3); // ≤ 2 per second plus one trailing emission

    (document.querySelector(".ytp-next-button") as HTMLElement).click();
    await vi.advanceTimersByTimeAsync(600);
    const last = rt.sent.filter((m) => m.type === "player_state").pop() as { state: { video_id: string } };
    expect(last.state.video_id).toBe("9bZkp7q19f0");
  });

  it("starts once per page: a second injection keeps the running instance and its token", async () => {
    loadFixture("watch-playing", "/watch?v=dQw4w9WgXcQ");
    installFakeVideo(video());
    const rt = fakeRuntime();
    const scope: Record<string, unknown> = {};
    const first = startContentScriptOnce(scope, rt.runtime, document, window);
    const second = startContentScriptOnce(scope, rt.runtime, document, window);
    await vi.advanceTimersByTimeAsync(0);
    expect(second).toBe(first);
    expect(scope[INSTANCE_KEY]).toBe(first);
    expect(rt.listeners).toHaveLength(1);
    expect(rt.sent.filter((m) => m.type === "attached")).toEqual([expect.objectContaining({ token: first.token })]);
    first.stop();
  });

  it("replaces an instance orphaned by an extension reload", async () => {
    loadFixture("watch-playing", "/watch?v=dQw4w9WgXcQ");
    installFakeVideo(video());
    const old = fakeRuntime();
    const scope: Record<string, unknown> = {};
    const orphan = startContentScriptOnce(scope, old.runtime, document, window);
    await vi.advanceTimersByTimeAsync(0);
    (old.runtime as { id: string | undefined }).id = undefined; // what chrome.runtime.id reads after the reload
    expect(orphan.alive()).toBe(false);
    const fresh = fakeRuntime();
    const replacement = startContentScriptOnce(scope, fresh.runtime, document, window);
    await vi.advanceTimersByTimeAsync(0);
    expect(replacement).not.toBe(orphan);
    expect(replacement.token).not.toBe(orphan.token);
    expect(fresh.sent[0]).toMatchObject({ type: "attached", token: replacement.token });
    // The orphan stopped listening to the player.
    const oldCount = old.sent.length;
    video().dispatchEvent(new Event("pause"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(old.sent.length).toBe(oldCount);
    replacement.stop();
  });

  it("stops emitting once the worker is unreachable, and sends detached on pagehide", async () => {
    loadFixture("watch-playing", "/watch?v=dQw4w9WgXcQ");
    installFakeVideo(video());
    const rt = fakeRuntime();
    startContentScript(rt.runtime, document, window);
    await vi.advanceTimersByTimeAsync(0);
    window.dispatchEvent(new Event("pagehide"));
    await vi.advanceTimersByTimeAsync(0);
    expect(rt.sent.at(-1)).toMatchObject({ type: "detached" });
    rt.failSends();
    video().dispatchEvent(new Event("pause"));
    await vi.advanceTimersByTimeAsync(1000);
    const n = rt.sent.length;
    video().dispatchEvent(new Event("play"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(rt.sent.length).toBe(n);
  });
});

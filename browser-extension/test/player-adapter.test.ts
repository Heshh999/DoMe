// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createPlayerAdapter, type PlayerAdapter } from "../src/content/adapter.ts";
import type { ContentOp, ContentReply, OpArgs, SearchItem } from "../src/shared/messages.ts";
import { installFakeVideo, loadFixture, TOKEN, video, wireLinkNavigation, wireMuteButton, wireNavigation, wireTheaterButton, type FakeVideoState } from "./dom-helpers.ts";

const WATCH_URL = "/watch?v=dQw4w9WgXcQ";

function adapter(): PlayerAdapter {
  return createPlayerAdapter({ doc: document, win: window, token: TOKEN });
}

/** Run an op under fake timers, advancing the clock in 50 ms steps until it settles (max 10 s). */
async function run(a: PlayerAdapter, op: ContentOp, args: OpArgs = {}, deadline = 5000) {
  let settled = false;
  const promise = a.run(op, { tab_token: TOKEN, ...args }, deadline).finally(() => {
    settled = true;
  });
  await vi.advanceTimersByTimeAsync(0);
  for (let i = 0; i < 200 && !settled; i++) await vi.advanceTimersByTimeAsync(50);
  return promise;
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("get_state on each fixture", () => {
  it("watch page playing", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video(), { paused: false, currentTime: 10, duration: 213, volume: 0.8 });
    const r = await run(adapter(), "get_state");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.state).toEqual({
      context: "watch",
      ad_showing: false,
      is_live: false,
      in_playlist: false,
      video_id: "dQw4w9WgXcQ",
      title: "Never Gonna Give You Up",
      paused: false,
      muted: false,
      volume: 80,
      position_seconds: 10,
      duration_seconds: 213,
      theater: false,
      fullscreen: false,
      has_next: true,
      has_previous: false,
    });
  });

  it("ad showing", async () => {
    loadFixture("ad-showing", WATCH_URL);
    installFakeVideo(video());
    const r = await run(adapter(), "get_state");
    expect(r.ok && r.state.ad_showing).toBe(true);
  });

  it("live stream: is_live, no duration", async () => {
    loadFixture("live", "/watch?v=5qap5aO4i9A");
    installFakeVideo(video(), { duration: Infinity });
    const r = await run(adapter(), "get_state");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.state.is_live).toBe(true);
    expect(r.state.duration_seconds).toBeUndefined();
    expect(r.state.video_id).toBe("5qap5aO4i9A");
  });

  it("the always-present but hidden live badge on a VOD page is not live; seeking works there", async () => {
    loadFixture("watch-playing", WATCH_URL);
    expect(document.querySelector(".ytp-live-badge")).not.toBeNull(); // the fixture carries the badge like youtube.com
    expect(getComputedStyle(document.querySelector(".ytp-live-badge")!).display).toBe("none");
    installFakeVideo(video(), { duration: 213 });
    const a = adapter();
    const r = await run(a, "get_state");
    expect(r.ok && r.state.is_live).toBe(false);
    const seek = await run(a, "seek_relative", { seconds: 10 });
    expect(seek.ok && seek.state.position_seconds).toBe(20);
  });

  it("live stream with a finite (DVR) duration is still live because the time display is marked live", async () => {
    loadFixture("live", "/watch?v=5qap5aO4i9A");
    installFakeVideo(video(), { duration: 3600 });
    const r = await run(adapter(), "get_state");
    expect(r.ok && r.state.is_live).toBe(true);
    const seek = await run(adapter(), "seek_to", { position_seconds: 5 });
    expect(!seek.ok && seek.code).toBe("UNSUPPORTED_CONTEXT");
  });

  it("shorts: context shorts, no next/previous", async () => {
    loadFixture("shorts", "/shorts/aBcDeFgHiJk");
    installFakeVideo(video());
    const r = await run(adapter(), "get_state");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.state.context).toBe("shorts");
    expect(r.state.video_id).toBe("aBcDeFgHiJk");
    expect(r.state.has_next).toBe(false);
    expect(r.state.has_previous).toBe(false);
    expect(r.state.theater).toBeUndefined();
  });

  it("end of playlist: in_playlist, has_next false, has_previous true", async () => {
    loadFixture("no-next", "/watch?v=kJQP7kiw5Fk&list=PL123");
    installFakeVideo(video(), { paused: true });
    const r = await run(adapter(), "get_state");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.state.in_playlist).toBe(true);
    expect(r.state.has_next).toBe(false);
    expect(r.state.has_previous).toBe(true);
    expect(r.state.paused).toBe(true);
  });

  it("page without a player reports context other and no player fields", async () => {
    loadFixture("home-no-player", "/");
    const r = await run(adapter(), "get_state");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.state.context).toBe("other");
    expect(r.state.paused).toBeUndefined();
    expect(r.state.has_next).toBeUndefined();
  });
});

describe("guards", () => {
  let fake: FakeVideoState;
  beforeEach(() => {
    loadFixture("watch-playing", WATCH_URL);
    fake = installFakeVideo(video());
  });

  it("rejects a stale or missing tab_token with TARGET_CHANGED without acting", async () => {
    const a = adapter();
    const stale = await a.run("set_paused", { tab_token: "BBBBBBBBBBBBBBBBBBBBBB", paused: true }, 5000);
    expect(stale.ok).toBe(false);
    if (!stale.ok) expect(stale.code).toBe("TARGET_CHANGED");
    const missing = await a.run("set_paused", { paused: true }, 5000);
    expect(!missing.ok && missing.code).toBe("TARGET_CHANGED");
    expect(fake.pauseCalls).toBe(0);
    expect(!stale.ok && stale.state?.context).toBe("watch"); // best-known state still reported
  });

  it("rejects expected_video_id mismatch with TARGET_CHANGED", async () => {
    const r = await run(adapter(), "next", { expected_video_id: "9bZkp7q19f0" });
    expect(!r.ok && r.code).toBe("TARGET_CHANGED");
  });

  it("accepts a matching expected_video_id", async () => {
    const r = await run(adapter(), "get_state", { expected_video_id: "dQw4w9WgXcQ" });
    expect(r.ok).toBe(true);
  });

  it("ad showing: next/seek/theater unsupported, pause/mute/volume allowed", async () => {
    loadFixture("ad-showing", WATCH_URL);
    const f = installFakeVideo(video());
    const a = adapter();
    for (const op of ["next", "previous", "seek_relative", "set_theater", "request_fullscreen"] as const) {
      const r = await run(a, op, { seconds: 10, enabled: true });
      expect(!r.ok && r.code, op).toBe("UNSUPPORTED_CONTEXT");
    }
    const paused = await run(a, "set_paused", { paused: true });
    expect(paused.ok).toBe(true);
    expect(f.paused).toBe(true);
    const muted = await run(a, "set_muted", { muted: true });
    expect(muted.ok && muted.state.muted).toBe(true);
  });

  it("shorts: next/previous/theater unsupported, pause allowed", async () => {
    loadFixture("shorts", "/shorts/aBcDeFgHiJk");
    installFakeVideo(video());
    const a = adapter();
    for (const op of ["next", "previous", "set_theater"] as const) {
      const r = await run(a, op, { enabled: true });
      expect(!r.ok && r.code, op).toBe("UNSUPPORTED_CONTEXT");
    }
    const r = await run(a, "set_paused", { paused: true });
    expect(r.ok).toBe(true);
  });

  it("live: seeking unsupported, next allowed to run", async () => {
    loadFixture("live", "/watch?v=5qap5aO4i9A");
    installFakeVideo(video(), { duration: Infinity });
    wireNavigation(".ytp-next-button", "/watch?v=9bZkp7q19f0");
    const a = adapter();
    expect((await run(a, "seek_relative", { seconds: 10 })).ok).toBe(false);
    const seek = await run(a, "seek_to", { position_seconds: 5 });
    expect(!seek.ok && seek.code).toBe("UNSUPPORTED_CONTEXT");
    const next = await run(a, "next");
    expect(next.ok).toBe(true);
  });

  it("no player: control ops are ACTION_UNAVAILABLE / UNSUPPORTED_CONTEXT", async () => {
    loadFixture("home-no-player", "/");
    const r = await run(adapter(), "set_paused", { paused: true });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(["ACTION_UNAVAILABLE", "UNSUPPORTED_CONTEXT"]).toContain(r.code);
  });
});

describe("set_paused", () => {
  it("pauses and plays with read-back", async () => {
    loadFixture("watch-playing", WATCH_URL);
    const fake = installFakeVideo(video(), { paused: false });
    const a = adapter();
    const paused = await run(a, "set_paused", { paused: true });
    expect(paused.ok && paused.state.paused).toBe(true);
    expect(fake.pauseCalls).toBe(1);
    const playing = await run(a, "set_paused", { paused: false });
    expect(playing.ok && playing.state.paused).toBe(false);
    // Absolute state: pausing an already paused player is a no-op success.
    const again = await run(a, "set_paused", { paused: false });
    expect(again.ok).toBe(true);
    expect(fake.playCalls).toBe(1);
  });

  it("play() refused by the autoplay policy (NotAllowedError) is ACTIVATION_REQUIRED and says what to do on the PC", async () => {
    loadFixture("watch-playing", WATCH_URL);
    const fake = installFakeVideo(video(), { paused: true, playRejects: "NotAllowedError" });
    const r = await run(adapter(), "set_paused", { paused: false });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.code).toBe("ACTIVATION_REQUIRED");
    expect(r.message).toContain("autoplay");
    expect(r.message).toContain("NotAllowedError");
    expect(r.message).toMatch(/press Play on the PC/);
    // The failure still reports the player as it is: paused, nothing else touched.
    expect(r.state?.paused).toBe(true);
    expect(fake.playCalls).toBe(1);
  });

  it("a NotAllowedError from another realm (not instanceof Error) is still recognised", async () => {
    loadFixture("watch-playing", WATCH_URL);
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    const Foreign = (frame.contentWindow as unknown as { DOMException: typeof DOMException }).DOMException;
    const foreign = new Foreign("play() failed", "NotAllowedError");
    expect(foreign instanceof Error).toBe(false);
    installFakeVideo(video(), { paused: true });
    Object.defineProperty(video(), "play", { value: () => Promise.reject(foreign), configurable: true });
    const r = await run(adapter(), "set_paused", { paused: false });
    expect(!r.ok && r.code).toBe("ACTIVATION_REQUIRED");
  });

  it.each(["AbortError", "NotSupportedError"])("any other play() rejection (%s) stays ACTION_UNAVAILABLE and names it", async (name) => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video(), { paused: true, playRejects: name });
    const r = await run(adapter(), "set_paused", { paused: false });
    expect(!r.ok && r.code).toBe("ACTION_UNAVAILABLE");
    expect(!r.ok && r.message).toBe(`Playback could not start (${name})`);
  });

  it("pausing never calls play(), so the autoplay policy cannot refuse a pause", async () => {
    loadFixture("watch-playing", WATCH_URL);
    const fake = installFakeVideo(video(), { paused: false, playRejects: "NotAllowedError" });
    const r = await run(adapter(), "set_paused", { paused: true });
    expect(r.ok && r.state.paused).toBe(true);
    expect(fake.playCalls).toBe(0);
  });

  it("a player that ignores pause() fails after the 1 s read-back window", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video(), { paused: false, inert: true });
    const promise = adapter().run("set_paused", { tab_token: TOKEN, paused: true }, 5000);
    await vi.advanceTimersByTimeAsync(1100);
    const r = await promise;
    expect(!r.ok && r.code).toBe("ACTION_UNAVAILABLE");
  });
});

describe("next / previous", () => {
  it("next succeeds only when a video transition is observed and reports previous_video_id", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    wireNavigation(".ytp-next-button", "/watch?v=9bZkp7q19f0");
    const r = await run(adapter(), "next", { expected_video_id: "dQw4w9WgXcQ" });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.previous_video_id).toBe("dQw4w9WgXcQ");
    expect(r.state.video_id).toBe("9bZkp7q19f0");
  });

  it("next reports the new video's title once the navigation finished, within the deadline", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    wireNavigation(".ytp-next-button", "/watch?v=9bZkp7q19f0", { newTitle: "Gangnam Style", finishDelayMs: 400 });
    const r = await run(adapter(), "next", { expected_video_id: "dQw4w9WgXcQ" });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.previous_video_id).toBe("dQw4w9WgXcQ");
    expect(r.state.video_id).toBe("9bZkp7q19f0");
    expect(r.state.title).toBe("Gangnam Style");
    expect(r.state.duration_seconds).toBe(213);
  });

  it("next whose page never settles is still ok but omits title/duration/position instead of reporting the previous video's", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    wireNavigation(".ytp-next-button", "/watch?v=9bZkp7q19f0", { finish: false });
    const started = Date.now();
    const r = await run(adapter(), "next", { expected_video_id: "dQw4w9WgXcQ" });
    expect(Date.now() - started).toBeLessThanOrEqual(1600); // bounded settle wait
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.previous_video_id).toBe("dQw4w9WgXcQ");
    expect(r.state.video_id).toBe("9bZkp7q19f0");
    expect(r.state.title).toBeUndefined();
    expect(r.state.duration_seconds).toBeUndefined();
    expect(r.state.position_seconds).toBeUndefined();
    expect(r.state.paused).toBe(false);
  });

  it("the settle wait never exceeds the remaining deadline", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    wireNavigation(".ytp-next-button", "/watch?v=9bZkp7q19f0", { finish: false });
    const started = Date.now();
    const r = await run(adapter(), "next", {}, 400);
    expect(Date.now() - started).toBeLessThanOrEqual(500);
    expect(r.ok).toBe(true);
  });

  it("next with a click that produces no transition is OUTCOME_UNKNOWN at the deadline (never retried)", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    let clicks = 0;
    document.querySelector(".ytp-next-button")?.addEventListener("click", () => {
      clicks += 1;
    });
    const promise = adapter().run("next", { tab_token: TOKEN }, 3000);
    await vi.advanceTimersByTimeAsync(2999);
    await vi.advanceTimersByTimeAsync(2);
    const r = await promise;
    expect(!r.ok && r.code).toBe("OUTCOME_UNKNOWN");
    expect(clicks).toBe(1);
  });

  it("disabled next button is NO_NEXT_VIDEO; previous works at the end of a playlist", async () => {
    loadFixture("no-next", "/watch?v=kJQP7kiw5Fk&list=PL123");
    installFakeVideo(video());
    wireNavigation(".ytp-prev-button", "/watch?v=dQw4w9WgXcQ&list=PL123");
    const a = adapter();
    const next = await run(a, "next");
    expect(!next.ok && next.code).toBe("NO_NEXT_VIDEO");
    const prev = await run(a, "previous");
    expect(prev.ok).toBe(true);
    if (!prev.ok) return;
    expect(prev.previous_video_id).toBe("kJQP7kiw5Fk");
    expect(prev.state.video_id).toBe("dQw4w9WgXcQ");
    expect(prev.state.in_playlist).toBe(true);
  });

  it("hidden previous button (not in a playlist) is NO_PREVIOUS_VIDEO", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    const r = await run(adapter(), "previous");
    expect(!r.ok && r.code).toBe("NO_PREVIOUS_VIDEO");
  });
});

describe("seek", () => {
  it("seek_relative clamps to [0, duration] and reads back", async () => {
    loadFixture("watch-playing", WATCH_URL);
    const fake = installFakeVideo(video(), { currentTime: 10, duration: 213 });
    const a = adapter();
    const fwd = await run(a, "seek_relative", { seconds: 10 });
    expect(fwd.ok && fwd.state.position_seconds).toBe(20);
    const back = await run(a, "seek_relative", { seconds: -600 });
    expect(back.ok && back.state.position_seconds).toBe(0);
    const over = await run(a, "seek_to", { position_seconds: 9999 });
    expect(over.ok && over.state.position_seconds).toBe(213);
    expect(fake.currentTime).toBe(213);
  });

  it("seek that does not take effect fails after 2 s", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video(), { inert: true });
    const promise = adapter().run("seek_to", { tab_token: TOKEN, position_seconds: 100 }, 5000);
    await vi.advanceTimersByTimeAsync(2100);
    const r = await promise;
    expect(!r.ok && r.code).toBe("ACTION_UNAVAILABLE");
  });
});

describe("mute / volume", () => {
  it("set_muted uses YouTube's mute button when available", async () => {
    loadFixture("watch-playing", WATCH_URL);
    const fake = installFakeVideo(video(), { muted: false });
    let clicks = 0;
    wireMuteButton(video());
    document.querySelector(".ytp-mute-button")?.addEventListener("click", () => {
      clicks += 1;
    });
    const a = adapter();
    const muted = await run(a, "set_muted", { muted: true });
    expect(muted.ok && muted.state.muted).toBe(true);
    expect(clicks).toBe(1);
    const same = await run(a, "set_muted", { muted: true });
    expect(same.ok).toBe(true);
    expect(clicks).toBe(1); // absolute state: no toggle for a no-op
    const unmuted = await run(a, "set_muted", { muted: false });
    expect(unmuted.ok && unmuted.state.muted).toBe(false);
    expect(fake.muted).toBe(false);
  });

  it("set_muted falls back to the element when the button does nothing", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video(), { muted: false });
    const promise = adapter().run("set_muted", { tab_token: TOKEN, muted: true }, 5000);
    await vi.advanceTimersByTimeAsync(900);
    const r = await promise;
    expect(r.ok && r.state.muted).toBe(true);
  });

  it("set_volume sets the element volume as a percentage", async () => {
    loadFixture("watch-playing", WATCH_URL);
    const fake = installFakeVideo(video(), { volume: 0.8 });
    const r = await run(adapter(), "set_volume", { value: 35 });
    expect(r.ok && r.state.volume).toBe(35);
    expect(fake.volume).toBeCloseTo(0.35);
    const bad = await run(adapter(), "set_volume", { value: 101 });
    expect(!bad.ok && bad.code).toBe("INVALID_PARAMETERS");
  });
});

describe("theater and fullscreen", () => {
  it("set_theater toggles via the size button and reads the attribute back", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    wireTheaterButton();
    const a = adapter();
    const on = await run(a, "set_theater", { enabled: true });
    expect(on.ok && on.state.theater).toBe(true);
    const still = await run(a, "set_theater", { enabled: true });
    expect(still.ok).toBe(true);
    const off = await run(a, "set_theater", { enabled: false });
    expect(off.ok && off.state.theater).toBe(false);
  });

  it("request_fullscreen without user activation is ACTIVATION_REQUIRED", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    const player = document.querySelector("#movie_player") as HTMLElement & { requestFullscreen: () => Promise<void> };
    player.requestFullscreen = () => {
      const err = new Error("Permissions check failed");
      err.name = "TypeError";
      return Promise.reject(err);
    };
    const r = await run(adapter(), "request_fullscreen");
    expect(!r.ok && r.code).toBe("ACTIVATION_REQUIRED");
  });

  it("request_fullscreen refused with a DOMException names it in the message", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    const player = document.querySelector("#movie_player") as HTMLElement & { requestFullscreen: () => Promise<void> };
    player.requestFullscreen = () => Promise.reject(new DOMException("Permissions check failed", "NotAllowedError"));
    const r = await run(adapter(), "request_fullscreen");
    expect(!r.ok && r.code).toBe("ACTIVATION_REQUIRED");
    expect(!r.ok && r.message).toBe("The browser refused fullscreen without a user gesture on the PC (NotAllowedError)");
  });

  it("request_fullscreen succeeds when the browser allows it", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    const player = document.querySelector("#movie_player") as HTMLElement & { requestFullscreen: () => Promise<void> };
    let fsElement: Element | null = null;
    Object.defineProperty(document, "fullscreenElement", { get: () => fsElement, configurable: true });
    player.requestFullscreen = () => {
      fsElement = player;
      queueMicrotask(() => document.dispatchEvent(new Event("fullscreenchange")));
      return Promise.resolve();
    };
    const r = await run(adapter(), "request_fullscreen");
    expect(r.ok && r.state.fullscreen).toBe(true);
  });
});

const RESULTS_URL = "/results?search_query=lofi+beats";

function results(r: ContentReply): SearchItem[] {
  if (!r.ok) throw new Error(`expected success, got ${r.code}: ${r.message}`);
  return r.results ?? [];
}

describe("read_results (search)", () => {
  it("classic layout: the first 10 primary videos in page order, across sections, without channel, shelf, playlist, mix or duplicate", async () => {
    loadFixture("search-results", RESULTS_URL);
    const r = await run(adapter(), "read_results");
    const items = results(r);
    expect(items.map((i) => i.video_id)).toEqual(["jfKfPfyJRdk", "5qap5aO4i9A", "7NOSDKb0HlU", "DWcJFNfaw9c", "lTRiuFIWV54", "rUxyKA_-grg", "4xDzrJKXOOY", "n61ULEU7CO0", "kgx4WGK0oNU", "tNkZsRW7h2c"]);
    expect(items.map((i) => i.index)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(items[0]).toEqual({ index: 1, video_id: "jfKfPfyJRdk", title: "lofi hip hop radio 📚 beats to relax/study to", channel: "Lofi Girl", is_live: true });
    expect(items[1]).toEqual({ index: 2, video_id: "5qap5aO4i9A", title: "lofi hip hop radio - beats to sleep/chill to", channel: "Lofi Girl", duration_seconds: 3723, is_live: false });
    // Untrusted text: zero-width characters stripped, clipped to the contract's 200 characters.
    expect(items[2]!.title.startsWith("A very long title")).toBe(true);
    expect(items[2]!.title).toHaveLength(200);
    expect(items[2]!.duration_seconds).toBe(187);
    expect(items.map((i) => i.duration_seconds)).toEqual([undefined, 3723, 187, 134, 3600, 213, 1501, 600, 3599, 260]);
    for (const skipped of ["dQw4w9WgXcQ", "9bZkp7q19f0", "aaaaaaaaaaa", "bbbbbbbbbbb", "HuFYqnbVbzY"]) expect(items.some((i) => i.video_id === skipped), skipped).toBe(false);
    // The results page publishes no title: it is the search query.
    if (r.ok) {
      expect(r.state.context).toBe("other");
      expect(r.state.title).toBeUndefined();
      expect(JSON.stringify(r)).not.toContain("lofi beats");
    }
  });

  it("newer lockup layout (with and without the -wiz class infix); the mix lockup and the Shorts grid are skipped", async () => {
    loadFixture("search-results-lockup", RESULTS_URL);
    const items = results(await run(adapter(), "read_results"));
    expect(items).toEqual([
      { index: 1, video_id: "jfKfPfyJRdk", title: "lofi hip hop radio 📚 beats to relax/study to", channel: "Lofi Girl", is_live: true },
      { index: 2, video_id: "DWcJFNfaw9c", title: "Coffee shop lofi", channel: "Cafe Beats", duration_seconds: 754, is_live: false },
      { index: 3, video_id: "rUxyKA_-grg", title: "Rainy lofi", channel: "Rain Tunes", duration_seconds: 213, is_live: false },
    ]);
  });

  it("ads, promoted videos, the Shorts shelf and a Short shown as a row are not results", async () => {
    loadFixture("search-results-ads-shorts", RESULTS_URL);
    const items = results(await run(adapter(), "read_results"));
    expect(items.map((i) => [i.index, i.video_id])).toEqual([
      [1, "DWcJFNfaw9c"],
      [2, "rUxyKA_-grg"],
    ]);
  });

  it("YouTube's 'No results found' panel is an empty success", async () => {
    loadFixture("search-no-results", "/results?search_query=qzxqzxqzx");
    expect(results(await run(adapter(), "read_results"))).toEqual([]);
  });

  it("an unrecognised layout with watch links is ACTION_UNAVAILABLE, never an empty success", async () => {
    loadFixture("search-results", RESULTS_URL);
    for (const el of [...document.querySelectorAll("ytd-video-renderer")]) {
      const renamed = document.createElement("ytd-video-renderer-v2");
      renamed.innerHTML = el.innerHTML;
      el.replaceWith(renamed);
    }
    const r = await run(adapter(), "read_results", {}, 2000);
    expect(!r.ok && r.code).toBe("ACTION_UNAVAILABLE");
    expect(!r.ok && r.message).toMatch(/YouTube changed its search results page/);
  });

  it("a results page that never renders anything is ACTION_UNAVAILABLE at the deadline", async () => {
    loadFixture("search-no-results", RESULTS_URL);
    document.querySelector("ytd-background-promo-renderer")!.remove();
    const r = await run(adapter(), "read_results", {}, 1500);
    expect(!r.ok && r.code).toBe("ACTION_UNAVAILABLE");
  });

  it("waits for YouTube to render the results after the page attached (event-driven)", async () => {
    loadFixture("search-results-ads-shorts", RESULTS_URL);
    const contents = document.querySelector("ytd-item-section-renderer > #contents")!;
    const rows = [...contents.children];
    for (const row of rows) row.remove();
    let settled = false;
    const promise = adapter()
      .run("read_results", { tab_token: TOKEN }, 5000)
      .finally(() => (settled = true));
    await vi.advanceTimersByTimeAsync(300);
    expect(settled).toBe(false);
    contents.append(...rows);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(900); // fewer than 10 results: the short fill wait ends it
    expect(settled).toBe(true);
    expect(results(await promise).map((i) => i.video_id)).toEqual(["DWcJFNfaw9c", "rUxyKA_-grg"]);
  });

  it("is refused outside a results page and with a stale token", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    const r = await run(adapter(), "read_results");
    expect(!r.ok && r.code).toBe("UNSUPPORTED_CONTEXT");
    loadFixture("search-results", RESULTS_URL);
    const stale = await adapter().run("read_results", { tab_token: "BBBBBBBBBBBBBBBBBBBBBB" }, 5000);
    expect(!stale.ok && stale.code).toBe("TARGET_CHANGED");
  });
});

describe("open_result", () => {
  it("clicks YouTube's own title link of result N and succeeds once the tab shows that video", async () => {
    loadFixture("search-results", RESULTS_URL);
    const clicked = wireLinkNavigation({ newTitle: "Coffee shop lofi" });
    const r = await run(adapter(), "open_result", { index: 4, video_id: "DWcJFNfaw9c" });
    expect(clicked).toEqual(["/watch?v=DWcJFNfaw9c&pp=ygUKbG9maSBiZWF0cw%3D%3D"]);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.state.context).toBe("watch");
    expect(r.state.video_id).toBe("DWcJFNfaw9c");
    expect(r.state.title).toBe("Coffee shop lofi");
    expect(r.previous_video_id).toBeUndefined();
  });

  it("works on the lockup layout and without a video_id", async () => {
    loadFixture("search-results-lockup", RESULTS_URL);
    const clicked = wireLinkNavigation();
    const r = await run(adapter(), "open_result", { index: 3 });
    expect(clicked).toEqual(["/watch?v=rUxyKA_-grg"]);
    expect(r.ok && r.state.video_id).toBe("rUxyKA_-grg");
  });

  it("never reports the results page's title (the query) as the opened video's title", async () => {
    loadFixture("search-results", RESULTS_URL);
    // The navigation finishes but YouTube has not replaced the document title or rendered a heading yet.
    wireLinkNavigation();
    const a = adapter();
    const r = await run(a, "open_result", { index: 2 });
    expect(r.ok && r.state.video_id).toBe("5qap5aO4i9A");
    expect(document.title).toBe("lofi beats - YouTube");
    expect(r.ok && r.state.title).toBeUndefined();
    expect(a.snapshot().title).toBeUndefined(); // later player_state emissions neither
    document.title = "lofi hip hop radio - beats to sleep/chill to - YouTube";
    expect(a.snapshot().title).toBe("lofi hip hop radio - beats to sleep/chill to");
  });

  it("a video whose own heading happens to read like the query keeps its title", async () => {
    loadFixture("search-results", RESULTS_URL);
    wireLinkNavigation({ newTitle: "lofi beats" });
    const r = await run(adapter(), "open_result", { index: 4 });
    expect(r.ok && r.state.video_id).toBe("DWcJFNfaw9c");
    expect(r.ok && r.state.title).toBe("lofi beats"); // from the watch heading, not the stale document title
  });

  it("an index beyond the results is SEARCH_RESULT_UNAVAILABLE and clicks nothing", async () => {
    loadFixture("search-results-ads-shorts", RESULTS_URL);
    const clicked = wireLinkNavigation();
    const r = await run(adapter(), "open_result", { index: 3 });
    expect(!r.ok && r.code).toBe("SEARCH_RESULT_UNAVAILABLE");
    expect(clicked).toEqual([]);
    expect(window.location.pathname).toBe("/results");
  });

  it("a result that is no longer the chosen video is TARGET_CHANGED and clicks nothing", async () => {
    loadFixture("search-results", RESULTS_URL);
    const clicked = wireLinkNavigation();
    const r = await run(adapter(), "open_result", { index: 1, video_id: "5qap5aO4i9A" });
    expect(!r.ok && r.code).toBe("TARGET_CHANGED");
    expect(clicked).toEqual([]);
  });

  it("invalid index or video_id is INVALID_PARAMETERS", async () => {
    loadFixture("search-results", RESULTS_URL);
    for (const args of [{ index: 0 }, { index: 11 }, { index: 1.5 }, {}, { index: 1, video_id: "not valid!" }] as OpArgs[]) {
      const r = await run(adapter(), "open_result", args);
      expect(!r.ok && r.code, JSON.stringify(args)).toBe("INVALID_PARAMETERS");
    }
  });

  it("is refused outside a results page and with a stale token", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video());
    expect(await run(adapter(), "open_result", { index: 1 })).toMatchObject({ ok: false, code: "UNSUPPORTED_CONTEXT" });
    loadFixture("search-results", RESULTS_URL);
    const clicked = wireLinkNavigation();
    const stale = await adapter().run("open_result", { tab_token: "BBBBBBBBBBBBBBBBBBBBBB", index: 1 }, 5000);
    expect(!stale.ok && stale.code).toBe("TARGET_CHANGED");
    expect(clicked).toEqual([]);
  });

  it("a click that opens nothing is OUTCOME_UNKNOWN at the deadline (never clicked twice)", async () => {
    loadFixture("search-results", RESULTS_URL);
    const clicked = wireLinkNavigation({ navigate: false });
    const r = await run(adapter(), "open_result", { index: 1 }, 3000);
    expect(!r.ok && r.code).toBe("OUTCOME_UNKNOWN");
    expect(clicked).toHaveLength(1);
  });

  it("an unrecognised layout is ACTION_UNAVAILABLE", async () => {
    loadFixture("search-results", RESULTS_URL);
    for (const el of [...document.querySelectorAll("ytd-video-renderer")]) {
      const renamed = document.createElement("ytd-video-renderer-v2");
      renamed.innerHTML = el.innerHTML;
      el.replaceWith(renamed);
    }
    const r = await run(adapter(), "open_result", { index: 1 });
    expect(!r.ok && r.code).toBe("ACTION_UNAVAILABLE");
  });
});

describe("page titles that must not leave the PC", () => {
  it("results, channel and home pages report no title", async () => {
    for (const [fixture, url] of [
      ["search-results", RESULTS_URL],
      ["home-no-player", "/@LofiGirl"],
      ["home-no-player", "/feed/history"],
    ] as const) {
      loadFixture(fixture, url);
      document.title = "private words - YouTube";
      const r = await run(adapter(), "get_state");
      expect(r.ok && r.state.context, url).toBe("other");
      expect(r.ok && r.state.title, url).toBeUndefined();
    }
  });
});

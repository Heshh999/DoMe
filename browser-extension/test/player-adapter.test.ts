// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createPlayerAdapter, type PlayerAdapter } from "../src/content/adapter.ts";
import type { OpArgs, TabOp } from "../src/shared/messages.ts";
import { installFakeVideo, loadFixture, TOKEN, video, wireMuteButton, wireNavigation, wireTheaterButton, type FakeVideoState } from "./dom-helpers.ts";

const WATCH_URL = "/watch?v=dQw4w9WgXcQ";

function adapter(): PlayerAdapter {
  return createPlayerAdapter({ doc: document, win: window, token: TOKEN });
}

/** Run an op under fake timers, advancing the clock in 50 ms steps until it settles (max 10 s). */
async function run(a: PlayerAdapter, op: TabOp, args: OpArgs = {}, deadline = 5000) {
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

  it("play() rejection (autoplay policy) is ACTION_UNAVAILABLE", async () => {
    loadFixture("watch-playing", WATCH_URL);
    installFakeVideo(video(), { paused: true, playRejects: "NotAllowedError" });
    const r = await run(adapter(), "set_paused", { paused: false });
    expect(!r.ok && r.code).toBe("ACTION_UNAVAILABLE");
    expect(!r.ok && r.message).toContain("NotAllowedError");
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

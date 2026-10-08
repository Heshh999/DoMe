import { describe, expect, it, vi } from "vitest";

import { detectBrowser } from "../src/background/api.ts";
import { classifyDisconnect } from "../src/background/connection.ts";
import { toErrorObject, OpError } from "../src/shared/errors.ts";
import { isBackgroundToContent, isContentToBackground, isPopupToBackground } from "../src/shared/messages.ts";
import { sanitizeLabel, sanitizeSnapshot } from "../src/shared/sanitize.ts";
import { createDebounce, createThrottle, withTimeout, type Timers } from "../src/shared/throttle.ts";
import { randomToken22, TOKEN_RE } from "../src/shared/token.ts";
import { isYoutubeUrl, parseYoutubeUrl } from "../src/shared/youtubeUrl.ts";

function fakeTimers(): Timers {
  return { setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>), now: () => Date.now() };
}

describe("youtube urls", () => {
  it("classifies pages", () => {
    expect(parseYoutubeUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ")).toEqual({ context: "watch", video_id: "dQw4w9WgXcQ", in_playlist: false });
    expect(parseYoutubeUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1")).toEqual({ context: "watch", video_id: "dQw4w9WgXcQ", in_playlist: true });
    expect(parseYoutubeUrl("https://www.youtube.com/shorts/aBcDeFgHiJk?feature=share")).toEqual({ context: "shorts", video_id: "aBcDeFgHiJk", in_playlist: false });
    expect(parseYoutubeUrl("https://www.youtube.com/")).toEqual({ context: "other", in_playlist: false });
    expect(parseYoutubeUrl("https://www.youtube.com/watch?v=<script>")).toEqual({ context: "watch", in_playlist: false });
    expect(parseYoutubeUrl("https://music.youtube.com/watch?v=abc").context).toBe("music");
    expect(parseYoutubeUrl("not a url")).toEqual({ context: "other", in_playlist: false });
    expect(isYoutubeUrl("https://www.youtube.com/watch?v=x")).toBe(true);
    expect(isYoutubeUrl("https://www.youtube.com.evil.com/")).toBe(false);
    expect(isYoutubeUrl("http://www.youtube.com/")).toBe(false);
    expect(isYoutubeUrl(undefined)).toBe(false);
  });
});

describe("sanitize", () => {
  it("drops invalid optionals and rejects invalid required fields", () => {
    expect(sanitizeSnapshot({ context: "watch", ad_showing: false, is_live: false, in_playlist: false, title: " a\u0000b  c ", video_id: "ok_-1", volume: 42.4, position_seconds: -3, paused: "yes" })).toEqual({ context: "watch", ad_showing: false, is_live: false, in_playlist: false, title: "a b c", video_id: "ok_-1", volume: 42 });
    expect(sanitizeSnapshot({ context: "watch", ad_showing: false, is_live: false })).toBeNull();
    expect(sanitizeSnapshot({ context: "tv", ad_showing: false, is_live: false, in_playlist: false })).toBeNull();
    expect(sanitizeSnapshot(null)).toBeNull();
    expect(sanitizeLabel("  My\tChrome​ ")).toBe("My Chrome");
    expect(sanitizeLabel("x".repeat(100)).length).toBe(64);
    expect(sanitizeLabel(42)).toBe("");
  });
});

describe("tokens and errors", () => {
  it("generates 22-char base64url tokens", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 50; i++) {
      const t = randomToken22();
      expect(t).toMatch(TOKEN_RE);
      seen.add(t);
    }
    expect(seen.size).toBe(50);
  });

  it("maps errors to contract-shaped objects", () => {
    expect(toErrorObject(new OpError("NO_NEXT_VIDEO", "none"))).toEqual({ code: "NO_NEXT_VIDEO", message: "none" });
    expect(toErrorObject(new OpError("bad code", "x")).code).toBe("INTERNAL");
    expect(toErrorObject(new TypeError("boom"))).toEqual({ code: "INTERNAL", message: "TypeError: boom" });
    expect(toErrorObject("x")).toEqual({ code: "INTERNAL", message: "unexpected failure" });
    expect(toErrorObject(new OpError("X", "m".repeat(1000))).message.length).toBe(512);
  });
});

describe("message guards", () => {
  it("accept only well-formed internal messages", () => {
    expect(isBackgroundToContent({ kind: "dome_bg", type: "probe" })).toBe(true);
    expect(isBackgroundToContent({ kind: "dome_bg", type: "op", op: "next", args: {}, deadline_ms: 1 })).toBe(true);
    expect(isBackgroundToContent({ kind: "dome_bg", type: "op", op: "list_tabs", args: {}, deadline_ms: 1 })).toBe(false);
    expect(isBackgroundToContent({ kind: "dome_cs", type: "probe" })).toBe(false);
    expect(isContentToBackground({ kind: "dome_cs", type: "attached", token: "t", state: {} })).toBe(true);
    expect(isContentToBackground({ kind: "dome_cs", type: "attached", state: {} })).toBe(false);
    expect(isPopupToBackground({ kind: "dome_popup", type: "set_profile_label", value: 1 })).toBe(false);
    expect(isPopupToBackground({ kind: "dome_popup", type: "reconnect" })).toBe(true);
  });
});

describe("browser detection and disconnect classification", () => {
  it("detects brands", () => {
    expect(detectBrowser({ userAgentData: { brands: [{ brand: "Chromium" }, { brand: "Microsoft Edge" }] } })).toBe("edge");
    expect(detectBrowser({ userAgentData: { brands: [{ brand: "Chromium" }, { brand: "Google Chrome" }] } })).toBe("chrome");
    expect(detectBrowser({ userAgentData: { brands: [{ brand: "Brave" }] } })).toBe("unknown");
    expect(detectBrowser(undefined)).toBe("unknown");
  });
  it("classifies lastError messages", () => {
    expect(classifyDisconnect("Specified native messaging host not found.", "connecting")).toBe("host_missing");
    expect(classifyDisconnect("Access to the specified native messaging host is forbidden.", "connecting")).toBe("host_forbidden");
    expect(classifyDisconnect("Native host has exited.", "connected")).toBe("disconnected");
    expect(classifyDisconnect("", "agent_not_running")).toBe("agent_not_running");
  });
});

describe("timers", () => {
  it("throttle fires leading and trailing only", () => {
    vi.useFakeTimers();
    const calls: number[] = [];
    const t = createThrottle(500, () => calls.push(Date.now()), fakeTimers());
    t.trigger();
    t.trigger();
    t.trigger();
    expect(calls).toHaveLength(1);
    vi.advanceTimersByTime(499);
    expect(calls).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(calls).toHaveLength(2);
    t.cancel();
    vi.useRealTimers();
  });

  it("debounce fires once after quiet period; withTimeout rejects late promises", async () => {
    vi.useFakeTimers();
    let n = 0;
    const d = createDebounce(300, () => n++, fakeTimers());
    d.trigger();
    vi.advanceTimersByTime(200);
    d.trigger();
    vi.advanceTimersByTime(299);
    expect(n).toBe(0);
    vi.advanceTimersByTime(1);
    expect(n).toBe(1);
    const never = withTimeout(new Promise<void>(() => undefined), 1000, () => new Error("late"), fakeTimers());
    const rejection = expect(never).rejects.toThrow("late");
    vi.advanceTimersByTime(1000);
    await rejection;
    await expect(withTimeout(Promise.resolve(7), 1000, () => new Error("late"), fakeTimers())).resolves.toBe(7);
    vi.useRealTimers();
  });
});

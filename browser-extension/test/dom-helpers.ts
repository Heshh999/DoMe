/** TEST HELPERS for the jsdom player-adapter tests: fixture loading and a scriptable HTMLVideoElement. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const FIXTURES = resolve(import.meta.dirname, "../fixtures");

/** Replace the current jsdom document with a fixture and set the page URL (same origin). */
export function loadFixture(name: string, url: string): void {
  const html = readFileSync(resolve(FIXTURES, `${name}.html`), "utf8");
  const parsed = new DOMParser().parseFromString(html, "text/html");
  document.replaceChild(document.adoptNode(parsed.documentElement), document.documentElement);
  window.history.replaceState({}, "", url);
}

export interface FakeVideoOptions {
  paused?: boolean;
  currentTime?: number;
  duration?: number;
  volume?: number;
  muted?: boolean;
  ended?: boolean;
  /** When set, play() rejects with an error of this name (e.g. "NotAllowedError"). */
  playRejects?: string;
  /** When true, property writes are ignored (simulates a player that refuses changes). */
  inert?: boolean;
}

export interface FakeVideoState {
  paused: boolean;
  currentTime: number;
  duration: number;
  volume: number;
  muted: boolean;
  ended: boolean;
  playCalls: number;
  pauseCalls: number;
}

/** Shadow jsdom's unimplemented media API on one <video> element with observable state + events. */
export function installFakeVideo(video: HTMLVideoElement, opts: FakeVideoOptions = {}): FakeVideoState {
  const state: FakeVideoState = {
    paused: opts.paused ?? false,
    currentTime: opts.currentTime ?? 10,
    duration: opts.duration ?? 213,
    volume: opts.volume ?? 0.8,
    muted: opts.muted ?? false,
    ended: opts.ended ?? false,
    playCalls: 0,
    pauseCalls: 0,
  };
  const inert = opts.inert === true;
  const fire = (name: string): void => {
    queueMicrotask(() => video.dispatchEvent(new Event(name)));
  };
  Object.defineProperties(video, {
    paused: { get: () => state.paused, configurable: true },
    ended: { get: () => state.ended, configurable: true },
    duration: { get: () => state.duration, configurable: true },
    currentTime: {
      get: () => state.currentTime,
      set: (v: number) => {
        if (inert) return;
        state.currentTime = v;
        fire("seeked");
      },
      configurable: true,
    },
    volume: {
      get: () => state.volume,
      set: (v: number) => {
        if (inert) return;
        state.volume = v;
        fire("volumechange");
      },
      configurable: true,
    },
    muted: {
      get: () => state.muted,
      set: (v: boolean) => {
        if (inert) return;
        state.muted = v;
        fire("volumechange");
      },
      configurable: true,
    },
    play: {
      value: (): Promise<void> => {
        state.playCalls += 1;
        if (opts.playRejects) {
          const err = new Error("play() failed");
          err.name = opts.playRejects;
          return Promise.reject(err);
        }
        if (inert) return Promise.resolve();
        state.paused = false;
        fire("play");
        fire("playing");
        return Promise.resolve();
      },
      configurable: true,
    },
    pause: {
      value: (): void => {
        state.pauseCalls += 1;
        if (inert) return;
        state.paused = true;
        fire("pause");
      },
      configurable: true,
    },
  });
  return state;
}

/** Make the YouTube mute button behave like the real one: toggles the video's muted flag. */
export function wireMuteButton(video: HTMLVideoElement): void {
  document.querySelector(".ytp-mute-button")?.addEventListener("click", () => {
    video.muted = !video.muted;
  });
}

/** Make the size button toggle ytd-watch-flexy[theater] like YouTube does. */
export function wireTheaterButton(): void {
  document.querySelector(".ytp-size-button")?.addEventListener("click", () => {
    const flexy = document.querySelector("ytd-watch-flexy");
    if (!flexy) return;
    if (flexy.hasAttribute("theater")) flexy.removeAttribute("theater");
    else flexy.setAttribute("theater", "");
  });
}

export interface NavigationOptions {
  /** false: the click does nothing (a dead button). */
  navigate?: boolean;
  /** Title of the new video, written into the heading and document.title when the navigation finishes. */
  newTitle?: string;
  /** Delay between the URL change and `yt-navigate-finish` (YouTube fetches the page data in between). Default 80 ms. */
  finishDelayMs?: number;
  /** false: the navigation never finishes (no finish event, heading unchanged). */
  finish?: boolean;
}

/**
 * Make a next/previous button perform a YouTube-style SPA navigation: pushState + `yt-navigate-start`
 * synchronously, then (asynchronously, like the real site) the title update and `yt-navigate-finish`.
 */
export function wireNavigation(selector: string, newUrl: string, options: NavigationOptions = {}): void {
  document.querySelector(selector)?.addEventListener("click", () => {
    if (options.navigate === false) return;
    window.history.pushState({}, "", newUrl);
    document.dispatchEvent(new Event("yt-navigate-start"));
    if (options.finish === false) return;
    setTimeout(() => {
      if (options.newTitle !== undefined) {
        const heading = document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ?? document.querySelector("h1.ytd-watch-metadata");
        if (heading) heading.textContent = options.newTitle;
        document.title = `${options.newTitle} - YouTube`;
      }
      document.dispatchEvent(new Event("yt-navigate-finish"));
    }, options.finishDelayMs ?? 80);
  });
}

export function video(): HTMLVideoElement {
  const v = document.querySelector<HTMLVideoElement>("video.html5-main-video");
  if (!v) throw new Error("fixture has no video element");
  return v;
}

export const TOKEN = "AAAAAAAAAAAAAAAAAAAAAA";

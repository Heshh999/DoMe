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
  /**
   * When set, play() rejects with a DOMException of this name, as browsers do (e.g. "NotAllowedError" under the
   * autoplay policy). jsdom's DOMException, like one from the page's realm, is not `instanceof Error` here.
   */
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
        if (opts.playRejects) return Promise.reject(new DOMException("play() failed", opts.playRejects));
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

export interface LinkNavigationOptions {
  /** Delay between the URL change and `yt-navigate-finish`. Default 80 ms. */
  finishDelayMs?: number;
  /** false: clicks on result links do nothing (a dead link). */
  navigate?: boolean;
  /** Title of the opened video, written into a watch heading when the navigation finishes; omitted: the document title is left as it was. */
  newTitle?: string;
}

/**
 * Make every watch link in the page navigate like YouTube's own link handler does: the click is
 * handled in the page (no browser navigation), pushState to the link, `yt-navigate-start`, then
 * (asynchronously) `yt-navigate-finish`. Returns the hrefs clicked, in order.
 */
export function wireLinkNavigation(options: LinkNavigationOptions = {}): string[] {
  const clicked: string[] = [];
  // On the body, which the next loadFixture() replaces, so listeners never leak into later tests.
  document.body.addEventListener("click", (event) => {
    const link = (event.target as Element | null)?.closest("a[href]");
    const href = link?.getAttribute("href");
    if (!href?.startsWith("/watch")) return;
    event.preventDefault();
    clicked.push(href);
    if (options.navigate === false) return;
    window.history.pushState({}, "", href);
    document.dispatchEvent(new Event("yt-navigate-start"));
    setTimeout(() => {
      if (options.newTitle !== undefined) {
        const heading = document.createElement("h1");
        heading.className = "style-scope ytd-watch-metadata";
        heading.textContent = options.newTitle;
        document.body.append(heading);
      }
      document.dispatchEvent(new Event("yt-navigate-finish"));
    }, options.finishDelayMs ?? 80);
  });
  return clicked;
}

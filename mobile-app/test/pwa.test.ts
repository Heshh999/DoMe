/**
 * pwa.ts (design screen-view-2a §4.9, D28): `requestAppUpdate()` always asks the service-worker registration for an
 * update (with `autoUpdate` the update function `registerSW()` returns does nothing, so it is only ever called as well);
 * it is a no-op in development and where service workers do not exist, and it never throws. DECISIONS 92: the
 * registration handed over is asked at start and on a return to the front; a newer version reloads the page at once
 * when nothing is busy, waits during Screen view, and "Update now" reloads. The browser's own `controllerchange` counts
 * as a newer version too (Workbox, and so `onNeedReload`, miss some), except the first install claiming a page that had
 * no controller.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sw = vi.hoisted(() => ({ updateSW: vi.fn(async (_reload?: boolean) => undefined), registerSW: vi.fn() }));
vi.mock("virtual:pwa-register", () => ({
  registerSW: (options: unknown) => {
    sw.registerSW(options);
    return sw.updateSW;
  },
}));

import { AFTER_BUSY_DELAY_MS, NO_UPDATE } from "../src/lib/appUpdate.ts";
import { EMPTY_SCREEN_STATE } from "../src/lib/screen.ts";
import { applyAppUpdateNow, registerServiceWorker, requestAppUpdate, resetPwaForTests } from "../src/pwa.ts";
import { useAppUpdateStore } from "../src/store/appUpdate.ts";
import { useScreenStore } from "../src/store/screen.ts";

const update = vi.fn(async () => undefined);
const getRegistration = vi.fn(async () => ({ update }));

/** `navigator.serviceWorker`: carries events like the browser's, with the worker controlling the page (null: none). */
let container: EventTarget & { getRegistration: typeof getRegistration; controller: object | null };

function withServiceWorker(on: boolean, controller: object | null = {}): void {
  if (on) {
    container = Object.assign(new EventTarget(), { getRegistration, controller });
    Object.defineProperty(navigator, "serviceWorker", { value: container, configurable: true });
  } else Reflect.deleteProperty(navigator, "serviceWorker");
}

/** A newer worker took control of the page (clients.claim after skipWaiting). */
function newControllerTakesOver(): void {
  container.controller = {};
  container.dispatchEvent(new Event("controllerchange"));
}

beforeEach(() => {
  resetPwaForTests();
  sw.updateSW.mockClear();
  sw.registerSW.mockClear();
  update.mockClear();
  getRegistration.mockClear();
  vi.stubEnv("DEV", false);
});

afterEach(() => {
  vi.unstubAllEnvs();
  withServiceWorker(false);
});

describe("requestAppUpdate", () => {
  it("once registered: asks the registration for an update (autoUpdate's update function alone does nothing), and calls that function as well", async () => {
    withServiceWorker(true);
    registerServiceWorker();
    expect(sw.registerSW).toHaveBeenCalledTimes(1);
    requestAppUpdate();
    await Promise.resolve();
    await Promise.resolve();
    expect(getRegistration).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);
    expect(sw.updateSW).toHaveBeenCalledWith(true);
  });

  it("without the update function: asks the service worker registration for an update", async () => {
    withServiceWorker(true);
    requestAppUpdate();
    await Promise.resolve();
    await Promise.resolve();
    expect(getRegistration).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("is a no-op in development and where service workers do not exist; a failing update never throws", async () => {
    vi.stubEnv("DEV", true);
    withServiceWorker(true);
    requestAppUpdate();
    expect(getRegistration).not.toHaveBeenCalled();
    vi.stubEnv("DEV", false);
    withServiceWorker(false);
    expect(() => requestAppUpdate()).not.toThrow();
    withServiceWorker(true);
    getRegistration.mockRejectedValueOnce(new Error("gone"));
    expect(() => requestAppUpdate()).not.toThrow();
    await Promise.resolve();
  });
});

describe("registerServiceWorker: the app decides when to reload (DECISIONS 92)", () => {
  type Options = { onRegisteredSW?: (url: string, r: unknown) => void; onNeedReload?: () => void };
  const options = (): Options => sw.registerSW.mock.calls.at(-1)![0] as Options;
  const regUpdate = vi.fn(async () => undefined);
  const registration = { update: regUpdate };
  let reload: ReturnType<typeof vi.fn>;

  function setVisibility(state: "visible" | "hidden") {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new Event("visibilitychange"));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    regUpdate.mockClear();
    reload = vi.fn();
    useScreenStore.setState({ session: EMPTY_SCREEN_STATE });
    withServiceWorker(true);
    registerServiceWorker({ reload });
  });
  afterEach(() => {
    resetPwaForTests();
    useScreenStore.setState({ session: EMPTY_SCREEN_STATE });
    setVisibility("visible");
    vi.useRealTimers();
  });

  it("the registration, once known, is asked for a newer version at start; a return to the front asks again after a minute", async () => {
    expect(options().onNeedReload).toBeTypeOf("function");
    expect(regUpdate).not.toHaveBeenCalled();
    options().onRegisteredSW!("/sw.js", registration);
    expect(regUpdate).toHaveBeenCalledTimes(1);
    setVisibility("hidden");
    setVisibility("visible"); // within the minute
    await vi.advanceTimersByTimeAsync(0);
    expect(regUpdate).toHaveBeenCalledTimes(1);
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(61_000);
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(regUpdate).toHaveBeenCalledTimes(2);
    expect(getRegistration).not.toHaveBeenCalled(); // the registration handed over is used
  });

  it("a newer version that took control reloads at once when nothing is busy", () => {
    options().onNeedReload!();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("during Screen view it waits, the notice's state says so, and 'Update now' reloads", () => {
    useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "live" } });
    options().onNeedReload!();
    expect(reload).not.toHaveBeenCalled();
    expect(useAppUpdateStore.getState()).toEqual({ ready: true, waitingFor: "screen" });
    applyAppUpdateNow();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("a newer worker that takes control with no onNeedReload (Workbox no longer watching) reloads at once when nothing is busy; the late onNeedReload changes nothing", () => {
    newControllerTakesOver();
    expect(reload).toHaveBeenCalledTimes(1);
    options().onNeedReload!();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("the same, during Screen view: it waits, the notice says so, and it reloads once Screen view has ended for a moment", async () => {
    useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "live" } });
    newControllerTakesOver();
    expect(reload).not.toHaveBeenCalled();
    expect(useAppUpdateStore.getState()).toEqual({ ready: true, waitingFor: "screen" });
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(reload).not.toHaveBeenCalled();
    useScreenStore.setState({ session: { ...EMPTY_SCREEN_STATE, phase: "ended" } });
    await vi.advanceTimersByTimeAsync(AFTER_BUSY_DELAY_MS);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("the first worker ever to control a page that had none is not a newer version (the page runs that bundle); the next one is", () => {
    resetPwaForTests();
    withServiceWorker(true, null);
    registerServiceWorker({ reload });
    newControllerTakesOver(); // the first install claims the page
    expect(reload).not.toHaveBeenCalled();
    expect(useAppUpdateStore.getState()).toEqual(NO_UPDATE);
    newControllerTakesOver(); // a release later
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("requestAppUpdate (the MALFORMED_MESSAGE path) still asks the registration and the update function", async () => {
    requestAppUpdate();
    await vi.advanceTimersByTimeAsync(0);
    expect(getRegistration).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);
    expect(sw.updateSW).toHaveBeenCalledWith(true);
  });
});

/**
 * pwa.ts (design screen-view-2a §4.9, D28): `requestAppUpdate()` always asks the service-worker registration for an
 * update (with `autoUpdate` the update function `registerSW()` returns does nothing, so it is only ever called as well);
 * it is a no-op in development and where service workers do not exist, and it never throws.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sw = vi.hoisted(() => ({ updateSW: vi.fn(async (_reload?: boolean) => undefined), registerSW: vi.fn() }));
vi.mock("virtual:pwa-register", () => ({
  registerSW: (options: unknown) => {
    sw.registerSW(options);
    return sw.updateSW;
  },
}));

import { registerServiceWorker, requestAppUpdate, resetPwaForTests } from "../src/pwa.ts";

const update = vi.fn(async () => undefined);
const getRegistration = vi.fn(async () => ({ update }));

function withServiceWorker(on: boolean): void {
  if (on) Object.defineProperty(navigator, "serviceWorker", { value: { getRegistration }, configurable: true });
  else Reflect.deleteProperty(navigator, "serviceWorker");
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

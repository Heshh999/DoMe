/**
 * The shared plan-running helpers (src/lib/planRun.ts): whether the selected PC can be controlled and why not, the
 * Stop that wakes waiting steps, waiting for live state, and the app-list refresh that is skipped while the list is fresh.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APPS_FRESH_MS, controlProblem, createStopper, refreshAppsAndWait, STATUS_LABEL, waitForLive } from "../src/lib/planRun.ts";
import { useDevicesStore } from "../src/store/devices.ts";
import { useLiveStore } from "../src/store/live.ts";
import { OFFICE_PC, PC, TS } from "./helpers/harness.ts";

beforeEach(() => {
  useLiveStore.getState().reset();
  useDevicesStore.getState().reset();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
});

const state = (extra: Record<string, unknown> = {}) => useLiveStore.getState().onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: false, ...extra } });

describe("controlProblem", () => {
  it("names why the PC cannot take commands, in the Dashboard's terms", () => {
    expect(controlProblem(null)).toBe("no_pc");
    expect(controlProblem("unknown-pc")).toBe("no_pc");
    expect(controlProblem(PC)).toBe("not_current");
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    expect(controlProblem(PC)).toBe("offline");
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    state();
    expect(controlProblem(PC)).toBeNull();
    state({ remote_enabled: false });
    expect(controlProblem(PC)).toBe("remote_off");
    useDevicesStore.setState({ pcs: [{ ...OFFICE_PC, enabled: false }] });
    expect(controlProblem(PC)).toBe("disabled");
  });
});

describe("helpers", () => {
  it("Stop wakes every waiting step once and a new run starts un-stopped", () => {
    const stopper = createStopper();
    const wake = vi.fn();
    const off = stopper.onStop(wake);
    stopper.stop();
    expect(stopper.requested).toBe(true);
    expect(wake).toHaveBeenCalledTimes(1);
    off();
    stopper.reset();
    stopper.stop();
    expect(wake).toHaveBeenCalledTimes(1);
  });

  it("waitForLive resolves when the store changes, or false after the time", async () => {
    const waiting = waitForLive(() => useLiveStore.getState().pcs[PC]?.state !== undefined && useLiveStore.getState().pcs[PC]?.state !== null, 1_000);
    state();
    await expect(waiting).resolves.toBe(true);
    await expect(waitForLive(() => false, 10)).resolves.toBe(false);
  });

  it("an app list younger than APPS_FRESH_MS is trusted without asking the PC", async () => {
    useLiveStore.getState().setApps(PC, []);
    const before = Date.now();
    await refreshAppsAndWait(PC);
    expect(Date.now() - before).toBeLessThan(APPS_FRESH_MS);
    expect(useLiveStore.getState().commands).toEqual([]);
  });

  it("status labels are shared with the Type page", () => {
    expect(STATUS_LABEL.unknown).toBe("Result unknown");
  });
});

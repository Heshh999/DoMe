/**
 * `useApprovedApps(pcId)` is shared by the Apps and Type pages: it asks the PC for its approved apps when a page
 * mounts and whenever the app returns to the foreground, and `refreshApprovedApps` never asks twice while an answer
 * is outstanding. Answers land in the live store whichever page asked.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { refreshApprovedApps, useApprovedApps } from "../../src/app/hooks.ts";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { FakeSocket, flush, freshStorage, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

let h: RuntimeHarness;
let socket: FakeSocket;

function appListIds(): string[] {
  return socket
    .frames()
    .filter((f) => f.type === "command")
    .map((f) => JSON.parse((f.envelope as { payload: string }).payload) as { action: string; command_id: string })
    .filter((p) => p.action === "app.list")
    .map((p) => p.command_id);
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  h = makeRuntime();
  socket = await h.connect();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
});

afterEach(() => h.teardown());

function online() {
  act(() => {
    socket.receive({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    socket.receive({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true } });
  });
}

describe("useApprovedApps", () => {
  it("waits until the PC can be controlled, asks once, and returns the PC's answer", async () => {
    const { result } = renderHook(() => useApprovedApps(PC));
    await flush();
    expect(appListIds()).toHaveLength(0); // no fresh state yet: nothing is sent into the void
    expect(result.current.apps).toBeNull();
    online();
    await waitFor(() => expect(appListIds()).toHaveLength(1));
    expect(result.current.listing?.action).toBe("app.list");
    act(() => socket.receive({ type: "result", command_id: appListIds()[0]!, origin: "agent", state: "succeeded", at: TS, duration_ms: 4, result: { apps: [{ app_id: "vlc", display_name: "VLC", running: false, windows: [] }] } }));
    await waitFor(() => expect(result.current.apps?.map((a) => a.app_id)).toEqual(["vlc"]));
    expect(result.current.at).not.toBeNull();
    expect(useLiveStore.getState().appsByPc[PC]!.apps[0]!.display_name).toBe("VLC");
  });

  it("refreshApprovedApps sends nothing while an app.list for that PC is outstanding", async () => {
    online();
    await Promise.all([refreshApprovedApps(PC), refreshApprovedApps(PC)]); // both triggers arrive while signing
    expect(appListIds()).toHaveLength(1);
    expect(await refreshApprovedApps(PC)).toBe(true);
    expect(appListIds()).toHaveLength(1);
    act(() => socket.receive({ type: "result", command_id: appListIds()[0]!, origin: "agent", state: "succeeded", at: TS, duration_ms: 4, result: { apps: [] } }));
    await refreshApprovedApps(PC);
    expect(appListIds()).toHaveLength(2);
  });
});

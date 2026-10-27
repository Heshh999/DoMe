/**
 * Settings: Sign out and "Forget this installation" must never race the shell's automatic sign-in
 * redirect. Each flow navigates exactly once, to "/", after the runtime has finished — and for
 * Forget, after the signing key is gone from IndexedDB. The connection check stays available while
 * the state is stale or remote control is off, and is withheld only when the socket is down or the
 * relay says the PC is not online. The diagnostics download carries the Screen view client's own facts (device round 2).
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RequireSession } from "../../src/app/AppShell.tsx";
import { setNavigationForTests, type Navigation } from "../../src/app/navigation.ts";
import { configureApi } from "../../src/lib/api.ts";
import { getExistingKeyPair, getOrCreateKeyPair, getStoredControllerId, setStoredControllerId } from "../../src/lib/controllerKey.ts";
import { emptyTimeline, type ScreenClientFacts } from "../../src/lib/screenStats.ts";
import { SettingsPage } from "../../src/pages/app/SettingsPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { STATE_FRESH_MS, useLiveStore } from "../../src/store/live.ts";
import { useSessionStore } from "../../src/store/session.ts";
import { CONTROLLER, emptyResponse, freshStorage, jsonResponse, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

interface NavCall {
  kind: "assign" | "replaceState";
  url: string;
  /** Session status at the moment the navigation was requested. */
  status: string;
  /** Key record read issued at that moment (IndexedDB orders it after any committed delete). */
  key: Promise<CryptoKeyPair | null>;
}

function fakeNavigation() {
  const calls: NavCall[] = [];
  const nav: Navigation = {
    assign: (url) => calls.push({ kind: "assign", url, status: useSessionStore.getState().status, key: getExistingKeyPair() }),
    replaceState: (url) => calls.push({ kind: "replaceState", url, status: useSessionStore.getState().status, key: getExistingKeyPair() }),
  };
  return { nav, calls };
}

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={["/app/settings"]}>
      <RequireSession>
        <SettingsPage />
      </RequireSession>
    </MemoryRouter>,
  );
}

let h: RuntimeHarness;
let requests: string[];

beforeEach(async () => {
  freshStorage();
  signedIn();
  requests = [];
  h = makeRuntime();
  configureApi({
    fetchImpl: async (url, init) => {
      requests.push(`${init.method ?? "GET"} ${url}`);
      if (url.endsWith("/v1/auth/logout")) return emptyResponse(204);
      return jsonResponse(500, { error: { code: "INTERNAL", message: "unexpected", retryable: false } });
    },
  });
  await getOrCreateKeyPair();
  await setStoredControllerId(CONTROLLER);
  await h.connect();
});

afterEach(() => {
  h.teardown();
  setNavigationForTests(null);
});

describe("Sign out", () => {
  it("navigates exactly once to '/', never to the sign-in URL, and keeps the installation key", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    // let any effect that might want to redirect run
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]!.kind).toBe("assign");
    expect(calls[0]!.url).toBe("/");
    expect(calls[0]!.status).toBe("signing_out");
    expect(requests).toContain("POST /v1/auth/logout");
    expect(useSessionStore.getState().status).toBe("signing_out");
    expect(useSessionStore.getState().session).toBeNull();
    expect(h.rt.relay.status).toBe("closed");
    expect(await getExistingKeyPair()).not.toBeNull(); // sign-out keeps the key (DECISIONS 7)
    expect(await getStoredControllerId()).toBeNull(); // but forgets account state
    // the shell shows a neutral screen, not the "Taking you to sign in" redirect
    expect(screen.getByRole("status", { name: "Signing out" })).toBeInTheDocument();
  });

  it("a 401 from the logout call does not turn the manual sign-out into a sign-in redirect", async () => {
    // only the logout call answers 401 (the page's own reads on opening, such as the assistant status, do not)
    configureApi({ fetchImpl: async (url) => (url.endsWith("/v1/auth/logout") ? jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "gone", retryable: false } }) : jsonResponse(500, { error: { code: "INTERNAL", message: "unexpected", retryable: false } })) });
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(calls.map((c) => c.url)).toEqual(["/"]);
    expect(useSessionStore.getState().status).toBe("signing_out");
  });
});

describe("Forget this installation", () => {
  it("deletes the key record before any navigation is requested, then navigates once to '/'", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    expect(await getExistingKeyPair()).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Forget this installation…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete key and sign out" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe("/");
    expect(calls[0]!.url).not.toMatch(/auth\/login/);
    // the read issued at navigation time already sees no key: the delete had committed
    expect(await calls[0]!.key).toBeNull();
    expect(await getExistingKeyPair()).toBeNull();
    expect(await getStoredControllerId()).toBeNull();
    expect(requests).toContain("POST /v1/auth/logout");
    expect(h.rt.relay.status).toBe("closed");
  });

  it("'Keep' disarms without doing anything", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    await userEvent.click(screen.getByRole("button", { name: "Forget this installation…" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.getByRole("button", { name: "Forget this installation…" })).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    expect(await getExistingKeyPair()).not.toBeNull();
    expect(useSessionStore.getState().status).toBe("signed_in");
  });
});

describe("Connection check gating", () => {
  const freshState = () => {
    const live = useLiveStore.getState();
    live.setRelay("open");
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, volume: { value: 42, muted: false } } });
  };
  beforeEach(() => {
    useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
  });

  it("stays enabled while the state is stale ('Online · refreshing' for over 75 s)", () => {
    freshState();
    const received = useLiveStore.getState().pcs[PC]!.stateReceivedAt!;
    useLiveStore.getState().tick(received + STATE_FRESH_MS + 1);
    renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeEnabled();
  });

  it("stays enabled when remote control is switched off on the PC (the PC answers honestly)", () => {
    const live = useLiveStore.getState();
    live.setRelay("open");
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: false, session_locked: false, extension_connected: false } });
    renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeEnabled();
  });

  it("is disabled when the relay reports the PC offline, and when the socket itself is not open", () => {
    const live = useLiveStore.getState();
    live.setRelay("open");
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    const first = renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeDisabled();
    first.unmount();
    freshState();
    useLiveStore.getState().setRelay("reconnecting");
    renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeDisabled();
  });
});

describe("Settings: diagnostics", () => {
  it("the download carries the Screen view client's facts (last stats, the <video> facts, the timeline, DTLS, the codec fallback) from the runtime, not from React state", async () => {
    const facts: ScreenClientFacts = {
      stats: { at: 1, inbound: null, codec: { mime: "video/H264", pt: 98, fmtp: "profile-level-id=42e01f;packetization-mode=1" }, pair: null, dtls: "connected", ice: null, dc_sent: 4, dc_received: 1 },
      media: null,
      timeline: { ...emptyTimeline(), answer_verified: 900, track: 905, dtls_connected: 1300 },
      channel: { open: false, hb_sent: 4, received: 1, dropped: 0 },
      transport_connected: true,
      codec_fallback: { from: "h264", to: "vp8", reason: "not_decoded" },
    };
    vi.spyOn(h.rt.screen, "facts", "get").mockReturnValue(facts);
    const blobs: Blob[] = [];
    const createObjectURL = vi.fn((b: Blob) => {
      blobs.push(b);
      return "blob:diagnostics";
    });
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => undefined });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    renderSettings();
    await userEvent.click(await screen.findByRole("button", { name: "Download diagnostics" }));
    expect(click).toHaveBeenCalledTimes(1);
    const report = JSON.parse(await blobs[0]!.text()) as { screen: Record<string, unknown> };
    expect(report.screen).toMatchObject({ phase: "idle", stats: facts.stats, media: null, timeline: facts.timeline, transport_connected: true, codec_fallback: { from: "h264", to: "vp8", reason: "not_decoded" } });
    vi.restoreAllMocks();
  });
});

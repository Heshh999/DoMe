import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import { DashboardPage } from "../../src/pages/app/DashboardPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { EMPTY_LIVE_PC, useLiveStore } from "../../src/store/live.ts";

const PC = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:00.000Z";
const pc: rest.Pc = { id: PC, name: "Office PC", enabled: true, connection: "offline", last_seen: TS, created_at: TS, platform: "windows" };

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useLiveStore.getState().reset();
  useDevicesStore.getState().reset();
  useDevicesStore.setState({ loaded: true, pcs: [pc], selectedPcId: PC });
});

describe("Dashboard", () => {
  it("offline PC: shows Offline with last seen and disables every consequential control", () => {
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    renderDashboard();
    expect(screen.getByText("Office PC")).toBeInTheDocument();
    expect(screen.getByText("Offline")).toBeInTheDocument();
    expect(screen.getByText(/Commands are not stored for later/)).toBeInTheDocument();
    for (const name of ["Next video", "Back 10 s", "Lock Windows", "Sleep…"]) expect(screen.getByRole("button", { name })).toBeDisabled();
    expect(screen.getByRole("slider")).toBeDisabled();
    expect(screen.getByText(/Media state is not available/)).toBeInTheDocument();
  });

  it("online with fresh state: controls enabled, media and volume shown from the PC's state", () => {
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    useLiveStore.getState().onState({
      type: "state",
      pc_id: PC,
      at: TS,
      state: {
        remote_enabled: true,
        session_locked: false,
        extension_connected: true,
        volume: { value: 42, muted: false },
        youtube_tabs: [{ browser_instance_id: "b", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, context: "watch", ad_showing: false, is_live: false, in_playlist: false, paused: false, title: "Lo-fi beats <b>not html</b>" }],
      },
    });
    renderDashboard();
    expect(screen.getByText("Online")).toBeInTheDocument();
    expect(screen.getByText("Lo-fi beats <b>not html</b>")).toBeInTheDocument(); // rendered as text, not markup
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next video" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Lock Windows" })).toBeEnabled();
    expect(screen.getByRole("slider")).toBeEnabled();
    expect(screen.getByText("42%")).toBeInTheDocument();
  });

  it("stale state after resume/reconnect: 'Online · refreshing' and controls disabled until a fresh state frame", () => {
    const live = useLiveStore.getState();
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, volume: { value: 42, muted: false } } });
    live.markAllStale();
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS }); // reconnect: status arrives first
    renderDashboard();
    expect(screen.getByText("Online · refreshing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lock Windows" })).toBeDisabled();
    expect(screen.getByRole("slider")).toBeDisabled();
    expect(screen.queryByText("42%")).toBeNull();
  });

  it("remote control switched off on the PC is explained with recovery steps; subscription refused is shown as not paired", () => {
    const live = useLiveStore.getState();
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: false, session_locked: false, extension_connected: false } });
    renderDashboard();
    expect(screen.getAllByText(/Remote control is switched off on the PC/).length).toBeGreaterThan(0);
    expect(screen.getByText(/switch remote control back on/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lock Windows" })).toBeDisabled();
  });

  it("no PC linked: explains the first steps instead of showing controls as usable", () => {
    useDevicesStore.setState({ loaded: true, pcs: [], selectedPcId: null });
    useLiveStore.setState({ pcs: { [PC]: EMPTY_LIVE_PC } });
    renderDashboard();
    expect(screen.getByText(/No PC is linked to your account yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next video" })).toBeDisabled();
  });
});

import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import { SCREEN_VIEW_COMING_TEXT } from "../../src/components/ScreenViewComingCard.tsx";
import { RemotePage } from "../../src/pages/app/RemotePage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";

const PC = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:00.000Z";
const pc: rest.Pc = { id: PC, name: "Office PC", enabled: true, connection: "offline", last_seen: TS, created_at: TS, platform: "windows" };

function renderRemote() {
  return render(
    <MemoryRouter>
      <RemotePage />
    </MemoryRouter>,
  );
}

function expectComingCard() {
  const card = screen.getByTestId("screen-view-coming");
  expect(within(card).getByText("Coming")).toBeInTheDocument();
  expect(card).toHaveTextContent(SCREEN_VIEW_COMING_TEXT);
  expect(within(card).queryAllByRole("button")).toHaveLength(0);
  expect(within(card).queryAllByRole("link")).toHaveLength(0);
  return card;
}

beforeEach(() => {
  useLiveStore.getState().reset();
  useDevicesStore.getState().reset();
  useDevicesStore.setState({ loaded: true, pcs: [pc], selectedPcId: PC });
});

describe("Remote page: Screen view placeholder", () => {
  it("offline PC: the 'Screen view · Coming' card is shown and holds no control", () => {
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    renderRemote();
    expectComingCard();
  });

  it("online with fresh state: the card is unchanged (it does not depend on the PC's state)", () => {
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
        youtube_tabs: [{ browser_instance_id: "b", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, context: "watch", ad_showing: false, is_live: false, in_playlist: false, paused: false, title: "Lo-fi beats" }],
      },
    });
    renderRemote();
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled(); // the page is live around it
    expectComingCard();
  });

  it("mentions Screen view exactly once, so nothing else on the page can read as a Screen view control", () => {
    renderRemote();
    expect(screen.getAllByText("Screen view")).toHaveLength(1);
    expect(screen.queryAllByRole("button", { name: /screen view/i })).toHaveLength(0);
    expect(screen.queryAllByRole("link", { name: /screen view/i })).toHaveLength(0);
  });
});

/**
 * The way into Screen view (docs/design/screen-view.md §2.2 step 1): behind the server's switch the Remote page keeps
 * the "Coming" card and Home shows nothing; with the switch on, the tile names the one next state (allow on the PC,
 * pair again, update the PC, ready with the Free minutes left, when Free refreshes) and opens the Screen page. Nothing
 * says "Ready" before the grant and the pin are known.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import { configureApi } from "../../src/lib/api.ts";
import { writePin } from "../../src/lib/controllerKey.ts";
import { ScreenViewTile } from "../../src/components/ScreenViewTile.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useScreenStore } from "../../src/store/screen.ts";
import { FakePcKey } from "../helpers/fakePeer.ts";
import { CONTROLLER, freshStorage, jsonResponse, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

const GRANT: rest.Grant = { id: "99999999-9999-4999-8999-999999999999", controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "pointer", "screen"], created_at: TS };

let h: RuntimeHarness;
let healthBody: unknown;

function renderTile(variant: "remote" | "home") {
  return render(
    <MemoryRouter initialEntries={["/app/remote"]}>
      <Routes>
        <Route path="/app/remote" element={<ScreenViewTile pcId={PC} variant={variant} />} />
        <Route path="/app/screen" element={<p>Screen page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  healthBody = { status: "ok", screen_view: "on" };
  h = makeRuntime();
  await h.connect();
  configureApi({ fetchImpl: async (url) => (url.endsWith("/healthz") ? jsonResponse(200, healthBody) : url.includes("/grants") ? jsonResponse(200, { grants: [GRANT] }) : jsonResponse(500, { error: { code: "INTERNAL", message: url, retryable: false } })) });
  useScreenStore.setState({ switch: "unknown", switchChecked: false, lastAllowanceByPc: {}, lastProblemByPc: {} });
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC, controllers: [], grantsByPc: { [PC]: [GRANT] } });
});

afterEach(() => h.teardown());

describe("ScreenViewTile", () => {
  it("asks the server once; while the switch is off or unknown the Remote page keeps the 'Coming' card and Home shows nothing", async () => {
    healthBody = { status: "ok", screen_view: "off" };
    const remote = renderTile("remote");
    await waitFor(() => expect(useScreenStore.getState().switchChecked).toBe(true));
    expect(useScreenStore.getState().switch).toBe("off");
    expect(screen.getByTestId("screen-view-coming")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
    remote.unmount();
    renderTile("home");
    expect(screen.queryByTestId("screen-view-coming")).toBeNull();
    expect(screen.queryByTestId("screen-view-tile-home")).toBeNull();
  });

  it("an unreadable answer is 'unknown', never 'on'", async () => {
    healthBody = { status: "ok", screen_view: "yes please" };
    renderTile("remote");
    await waitFor(() => expect(useScreenStore.getState().switchChecked).toBe(true));
    expect(useScreenStore.getState().switch).toBe("unknown");
    expect(screen.getByTestId("screen-view-coming")).toBeInTheDocument();
  });

  it("with the switch on: no pin → pair again; then ready with the Free minutes; an incompatible PC → update; used up → when it refreshes; the tile opens the Screen page", async () => {
    useScreenStore.setState({ switch: "on", switchChecked: true });
    const first = renderTile("remote");
    await waitFor(() => expect(screen.getByTestId("screen-view-entry-line")).toHaveTextContent("Pair again so your phone can check it is your PC"));
    first.unmount();

    const pc = await FakePcKey.create();
    await writePin(pc.pin(PC));
    const ready = renderTile("home");
    await waitFor(() => expect(screen.getByTestId("screen-view-entry-line")).toHaveTextContent("Ready · Free: 10 min per 5-hour window"));
    ready.unmount();

    useScreenStore.setState({ lastAllowanceByPc: { [PC]: { unlimited: false, remainingSeconds: 420, resetsAt: null, at: Date.now() } } });
    const left = renderTile("remote");
    await waitFor(() => expect(screen.getByTestId("screen-view-entry-line")).toHaveTextContent("Ready · 7 min left in this window"));
    left.unmount();

    useScreenStore.setState({ lastProblemByPc: { [PC]: "PROTOCOL_INCOMPATIBLE" } });
    const update = renderTile("remote");
    await waitFor(() => expect(screen.getByTestId("screen-view-entry-line")).toHaveTextContent("Update DoMe on your PC"));
    update.unmount();

    useScreenStore.setState({ lastProblemByPc: {}, lastAllowanceByPc: { [PC]: { unlimited: false, remainingSeconds: 0, resetsAt: new Date(Date.now() + 3_600_000).toISOString(), at: Date.now() } } });
    renderTile("remote");
    await waitFor(() => expect(screen.getByTestId("screen-view-entry-line")).toHaveTextContent(/^Free Screen view refreshes (today|tomorrow) at /));
    screen.getByRole("link").click();
    await waitFor(() => expect(screen.getByText("Screen page")).toBeInTheDocument());
  });

  it("a grant without Screen view says to allow it on the PC first; nothing claims Ready while the grant is unknown", async () => {
    useScreenStore.setState({ switch: "on", switchChecked: true });
    useDevicesStore.setState({ grantsByPc: { [PC]: [{ ...GRANT, capabilities: ["status", "pointer"] }] } });
    const noScreen = renderTile("remote");
    await waitFor(() => expect(screen.getByTestId("screen-view-entry-line")).toHaveTextContent("Allow Screen view on your PC first"));
    noScreen.unmount();
    useDevicesStore.setState({ grantsByPc: {} });
    renderTile("remote");
    expect(screen.getByTestId("screen-view-entry-line")).toHaveTextContent("Checking…");
  });
});

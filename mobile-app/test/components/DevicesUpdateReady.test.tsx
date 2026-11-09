/**
 * Devices page, protocol 1.5 (design screen-view-2a D28): an app left open across a deploy gets a `/v1/pcs` answer
 * whose new field its schema does not know. The page then says "A newer DoMe is ready…" and never claims this phone
 * is "Not paired yet" when nothing about it was loaded; the controllers still come from their own answer.
 */
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { configureApi } from "../../src/lib/api.ts";
import { getControllerIdentity } from "../../src/lib/controllerKey.ts";
import { COPY } from "../../src/lib/screenStatus.ts";
import { DevicesPage } from "../../src/pages/app/DevicesPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { CONTROLLER, emptyResponse, flush, freshStorage, installDialogPolyfill, jsonResponse, makeRuntime, OFFICE_PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

installDialogPolyfill();
let h: RuntimeHarness;

beforeEach(async () => {
  freshStorage();
  signedIn();
  h = makeRuntime();
  await h.connect();
});
afterEach(() => h.teardown());

function serve(controllers: unknown[]): void {
  configureApi({
    fetchImpl: async (url) => {
      const u = String(url);
      if (u.endsWith("/v1/pcs")) return jsonResponse(200, { pcs: [{ ...OFFICE_PC, a_field_from_the_future: true }] });
      if (u.endsWith("/v1/controllers")) return jsonResponse(200, { controllers });
      if (u.includes("/grants")) return jsonResponse(200, { grants: [] });
      return emptyResponse(404);
    },
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/app/devices"]}>
      <DevicesPage />
    </MemoryRouter>,
  );
}

describe("Devices: a newer DoMe is ready", () => {
  it("MALFORMED_MESSAGE on /v1/pcs: the update notice, and no 'Not paired yet' while nothing about this phone is known", async () => {
    useDevicesStore.getState().reset();
    serve([]);
    await act(async () => {
      await useDevicesStore.getState().refresh();
    });
    expect(useDevicesStore.getState().updateReady).toBe(true);
    renderPage();
    expect(screen.getByTestId("devices-update-ready")).toHaveTextContent(COPY.updateReady);
    // this installation's key is known by now: only updateReady keeps the false "Not paired yet" away
    await act(async () => {
      await getControllerIdentity();
      await flush();
    });
    expect(screen.queryByText("Not paired yet")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    // the same page without the notice says it, as before
    act(() => useDevicesStore.setState({ updateReady: false }));
    expect(await screen.findByText("Not paired yet")).toBeInTheDocument();
  });

  it("the controllers still load from their own answer: this phone is named under the notice", async () => {
    useDevicesStore.getState().reset();
    const { kid } = await getControllerIdentity();
    serve([{ id: CONTROLLER, kid, display_name: "Lena's iPhone", status: "active", created_at: TS, last_seen: TS }]);
    await act(async () => {
      await useDevicesStore.getState().refresh();
    });
    renderPage();
    expect(screen.getByTestId("devices-update-ready")).toBeInTheDocument();
    expect(await screen.findByText("Lena's iPhone")).toBeInTheDocument();
    expect(screen.queryByText("Not paired yet")).toBeNull();
  });
});

/**
 * Devices page, protocol 1.4: a grant that includes Screen view says what it lets the phone see (the fixed scope
 * explanation, docs/design/screen-view.md §2.1) and is flagged per phone like manual input; a grant without it says
 * nothing about Screen view. The phone never offers to grant it: only the PC owner can, in a DoMe window on the PC.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SCREEN_SCOPE_EXPLANATION } from "../../src/lib/labels.ts";
import { DevicesPage } from "../../src/pages/app/DevicesPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { CONTROLLER, freshStorage, installDialogPolyfill, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

installDialogPolyfill();
let h: RuntimeHarness;

beforeEach(async () => {
  freshStorage();
  signedIn();
  h = makeRuntime();
  await h.connect();
});
afterEach(() => h.teardown());

function show(capabilities: string[]) {
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC, controllers: [{ id: CONTROLLER, kid: "k".repeat(43), display_name: "iPhone", status: "active", created_at: TS, last_seen: TS }], grantsByPc: { [PC]: [{ id: "99999999-9999-4999-8999-999999999999", controller_id: CONTROLLER, pc_id: PC, capabilities: capabilities as never, created_at: TS }] } });
  return render(
    <MemoryRouter initialEntries={["/app/devices"]}>
      <DevicesPage />
    </MemoryRouter>,
  );
}

describe("Devices: Screen view grants", () => {
  it("a Screen view grant shows its scope and is flagged per phone; the phone offers no way to grant it", () => {
    show(["status", "pointer", "screen"]);
    expect(screen.getByTestId(`screen-scope-${PC}`)).toHaveTextContent(SCREEN_SCOPE_EXPLANATION);
    expect(screen.getByText("Screen view: sees everything on the PC’s screens while it is on")).toBeInTheDocument();
    expect(screen.getAllByText(/Screen view: see this PC's screens live on the phone/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /allow screen view|grant/i })).toBeNull();
  });
  it("a grant without it says nothing about Screen view", () => {
    show(["status", "pointer"]);
    expect(screen.queryByTestId(`screen-scope-${PC}`)).toBeNull();
    expect(screen.queryByText(/Screen view: sees everything/)).toBeNull();
  });
});

/**
 * Spec §12: no upgrade modal or banner on any Free flow; a deliberate Pro selection gets a clear,
 * dismissible explanation naming the benefit. Spec §11A: the Health page renders every layer with its
 * next action, bounded retries and the support path, and the walkthrough points at the failed step.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppShell } from "../../src/app/AppShell.tsx";
import { DashboardPage } from "../../src/pages/app/DashboardPage.tsx";
import { DevicesPage } from "../../src/pages/app/DevicesPage.tsx";
import { HealthPage, MAX_RETRIES } from "../../src/pages/app/HealthPage.tsx";
import { LayoutsPage } from "../../src/pages/app/LayoutsPage.tsx";
import { RemotePage } from "../../src/pages/app/RemotePage.tsx";
import { RoutinesPage } from "../../src/pages/app/RoutinesPage.tsx";
import { TouchpadPage } from "../../src/pages/app/TouchpadPage.tsx";
import { DownloadPage } from "../../src/pages/public/DownloadPage.tsx";
import { ReleaseNotesPage } from "../../src/pages/public/ReleaseNotesPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { CONTROLLER, freshStorage, installDialogPolyfill, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

installDialogPolyfill();
const UPGRADE = /upgrade|subscribe|DoMe Pro|checkout|\$5\.99|per month/i;

let h: RuntimeHarness;

beforeEach(async () => {
  freshStorage();
  signedIn();
  h = makeRuntime();
  await h.connect();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC, controllers: [{ id: CONTROLLER, kid: "k".repeat(43), display_name: "iPhone", status: "active", created_at: TS, last_seen: TS }], grantsByPc: { [PC]: [{ id: "g", controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "media", "pointer", "keyboard"], created_at: TS }] } });
  useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
  if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => undefined;
});

afterEach(() => h.teardown());

function page(el: React.ReactElement, path = "/app") {
  return render(<MemoryRouter initialEntries={[path]}>{el}</MemoryRouter>);
}

describe("Upgrade experience (spec §12)", () => {
  it("Free flows carry no upgrade copy: Dashboard, Remote, Touchpad, Devices, Health — also while the PC is offline", () => {
    for (const [el, path] of [
      [<DashboardPage key="d" />, "/app"],
      [<RemotePage key="r" />, "/app/remote"],
      [<TouchpadPage key="t" />, "/app/touchpad"],
      [<DevicesPage key="v" />, "/app/devices"],
      [<HealthPage key="h" />, "/app/health"],
    ] as Array<[React.ReactElement, string]>) {
      const { container, unmount } = page(el, path);
      expect(container.textContent).not.toMatch(UPGRADE);
      unmount();
    }
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    useLiveStore.getState().onSubscribeRefused(PC, "GRANT_MISSING");
    for (const [el, path] of [
      [<DashboardPage key="d" />, "/app"],
      [<HealthPage key="h" />, "/app/health"],
    ] as Array<[React.ReactElement, string]>) {
      const { container, unmount } = page(el, path);
      expect(container.textContent).not.toMatch(UPGRADE);
      unmount();
    }
  });

  it("the app shell has no upgrade banner and no dialog on load; the Touchpad tab is present", () => {
    const { container } = page(<AppShell />, "/app");
    expect(container.textContent).not.toMatch(UPGRADE);
    expect(container.querySelector("dialog")).toBeNull();
    expect(screen.getByRole("link", { name: /Touchpad/ })).toBeInTheDocument();
  });

  it("a deliberate Pro selection (Routines, Custom remotes) shows a dismissible explanation naming the specific benefit, never a connectivity claim", async () => {
    const r = page(<RoutinesPage />, "/app/routines");
    const exp = screen.getByTestId("pro-explanation");
    expect(exp).toHaveTextContent(/One-tap routines is part of DoMe Pro/);
    expect(exp).toHaveTextContent(/stays free/);
    expect(exp.textContent).not.toMatch(/connection|reconnect|compatib/i);
    await userEvent.click(within(exp).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("pro-explanation")).toBeNull();
    r.unmount();
    page(<LayoutsPage />, "/app/layouts");
    expect(screen.getByTestId("pro-explanation")).toHaveTextContent(/Saving your own remote layouts is part of DoMe Pro/);
  });

  it("public pages: the official Windows download is visibly not yet published, and the release notes say pre-release with no device verification", () => {
    const d = page(<DownloadPage />, "/download");
    const button = screen.getByTestId("download-button");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent(/not yet published/);
    expect(d.container.querySelector('a[href$=".exe"], a[href$=".msi"]')).toBeNull();
    expect(d.container.textContent).toMatch(/never to disable antivirus/); // the only mention tells customers we will never ask for it
    d.unmount();
    const n = page(<ReleaseNotesPage />, "/release-notes");
    expect(n.container.textContent).toMatch(/pre-release/);
    expect(screen.getByText("Known issues")).toBeInTheDocument();
    expect(n.container.textContent).toMatch(/Real iPhone Safari/);
  });
});

describe("HealthPage", () => {
  it("renders every layer with a status pill, one next action per problem, details behind a control and the V1 requirements", async () => {
    useLiveStore.getState().onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: false, session_locked: false, extension_connected: false } });
    page(<HealthPage />, "/app/health");
    const list = screen.getByRole("list", { name: "Health checks" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((i) => i.getAttribute("aria-label"))).toEqual(["This phone", "Your account", "Office PC", "Remote control on the PC", "Touchpad and keyboard permission", "Browser extension", "Media target"]);
    expect(within(items[3]!).getByText("Needs attention")).toBeInTheDocument();
    expect(within(items[3]!).getByText("Enable remote control on the PC")).toBeInTheDocument();
    expect(within(items[5]!).getByText("Install or update the extension on the PC")).toBeInTheDocument();
    expect(within(items[4]!).getByText("OK")).toBeInTheDocument();
    expect(screen.queryByText("PC_REMOTE_DISABLED")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Show technical details/ }));
    expect(screen.getByText("PC_REMOTE_DISABLED")).toBeInTheDocument();
    expect(screen.getByText(/What DoMe needs \(V1\)/)).toBeInTheDocument();
    expect(screen.getByText(/cannot wake or power on a PC remotely/)).toBeInTheDocument();
    expect(screen.getByText(/A green row never implies the others work/)).toBeInTheDocument();
  });

  it("retries are bounded and end in the support path; a retry never resends a command; the walkthrough points at the failed step", async () => {
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    page(<HealthPage />, "/app/health");
    const sentBefore = h.sockets[0]!.sent.length;
    const retry = screen.getByRole("button", { name: /^Retry/ });
    for (let i = 0; i < MAX_RETRIES; i++) await userEvent.click(screen.getByRole("button", { name: /^Retry/ }));
    expect(retry).toBeDisabled();
    expect(screen.getByText(/Still not working after several retries/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contact support" })).toHaveAttribute("href", expect.stringContaining("/support?category="));
    const frames = h.sockets[0]!.frames().slice(sentBefore);
    expect(frames.filter((f) => f.type === "command")).toHaveLength(0); // only subscribe/ping, never a command
    expect(screen.getByText(/Continue at step 4: PC online with remote control enabled/)).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Setup steps" })).getAllByRole("listitem");
    expect(within(steps[3]!).getByRole("link", { name: "Go" })).toBeInTheDocument();
    expect(within(steps[0]!).queryByRole("link")).toBeNull(); // done steps have no link
    expect(screen.getByText(/The cause is unknown from here/)).toBeInTheDocument();
  });
});

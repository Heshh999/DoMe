/**
 * Spec §12: no upgrade modal or banner on any Free flow; a deliberate Pro selection gets a clear,
 * dismissible explanation naming the benefit. Spec §11A: the Health page renders every layer with its
 * next action, bounded retries and the support path, and the walkthrough points at the failed step.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { rest } from "@dome/protocol";

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
import { api } from "../../src/lib/api.ts";
import { useSessionStore } from "../../src/store/session.ts";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { CONTROLLER, freshStorage, installDialogPolyfill, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

installDialogPolyfill();
// the website's prices ($9.99 / $24.99) and the old planned one ($5.99) must never show up on a Free flow
const UPGRADE = /upgrade|subscribe|DoMe Pro|checkout|\$5\.99|\$9\.99|\$24\.99|per month|\/ month/i;
/** GET /v1/plans on a server without Stripe keys (the default here) and on one that sells. */
const PLANS_OFF: rest.PlansResponse = { plans: {}, pricing: { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } }, billing_enabled: false, billing_mode: "off", ai_enabled: false };
const PLANS_SELLING: rest.PlansResponse = { ...PLANS_OFF, billing_enabled: true, billing_mode: "live", ai_enabled: true };
let plans: ReturnType<typeof vi.spyOn>;

let h: RuntimeHarness;

beforeEach(async () => {
  freshStorage();
  localStorage.clear();
  signedIn();
  h = makeRuntime();
  await h.connect();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC, controllers: [{ id: CONTROLLER, kid: "k".repeat(43), display_name: "iPhone", status: "active", created_at: TS, last_seen: TS }], grantsByPc: { [PC]: [{ id: "g", controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "media", "pointer", "keyboard"], created_at: TS }] } });
  useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
  if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => undefined;
  plans = vi.spyOn(api, "plans").mockResolvedValue(PLANS_OFF);
});

afterEach(() => {
  plans.mockRestore();
  h.teardown();
});

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
    expect(exp).toHaveTextContent(/Part of Pro and Pro Plus: One-tap routines/);
    expect(exp).toHaveTextContent(/stays free/);
    expect(exp.textContent).not.toMatch(/connection|reconnect|compatib/i);
    // the explanation agrees with the website's plan content: Pro's price, coming at paid launch, nothing to buy
    expect(exp).toHaveTextContent(/nothing to buy yet/);
    expect(exp).toHaveTextContent(/Coming at paid launch \(Pro from \$9\.99 \/ month\)/);
    expect(exp.textContent).not.toMatch(/5\.99|planned pricing|not final/);
    expect(within(exp).getByRole("link", { name: "Billing" })).toHaveAttribute("href", "/app/billing");
    expect(within(exp).getByRole("link", { name: "What’s in Pro" })).toHaveAttribute("href", "/pricing");
    // the example routines end in a status line, never a disabled button that looks tappable
    expect(screen.queryByRole("button", { name: /Run/ })).toBeNull();
    expect(screen.getAllByText("Runs with Pro and Pro Plus, at paid launch")).toHaveLength(3);
    await userEvent.click(within(exp).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("pro-explanation")).toBeNull();
    r.unmount();
    // the dismissal is remembered on this phone, per page
    const again = page(<RoutinesPage />, "/app/routines");
    expect(screen.queryByTestId("pro-explanation")).toBeNull();
    again.unmount();
    page(<LayoutsPage />, "/app/layouts");
    expect(screen.getByTestId("pro-explanation")).toHaveTextContent(/Part of Pro and Pro Plus: Saving your own remote layouts/);
  });

  it("follows what the server sells (GET /v1/plans): nothing to buy while it sells nothing, Pro on sale once it does, and nothing about buying on a paid plan", async () => {
    // the server answers that it sells nothing: the static wording stays
    const off = page(<LayoutsPage />, "/app/layouts");
    await waitFor(() => expect(plans).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId("pro-explanation")).toHaveTextContent(/Coming at paid launch \(Pro from \$9\.99 \/ month\); there is nothing to buy yet/);
    off.unmount();

    // the server sells Pro: no "nothing to buy", and buying Pro does not add a feature that is not built
    plans.mockResolvedValue(PLANS_SELLING);
    const selling = page(<LayoutsPage />, "/app/layouts");
    const exp = screen.getByTestId("pro-explanation");
    await waitFor(() => expect(exp).toHaveTextContent(/Pro is on sale in Billing from \$9\.99 \/ month/));
    expect(exp).toHaveTextContent(/This is not in the app yet, so buying Pro does not add it now\./);
    expect(exp).toHaveTextContent(/stays free/);
    expect(exp.textContent).not.toMatch(/nothing to buy|Coming at paid launch|Test mode/);
    expect(within(exp).getByRole("link", { name: "Billing" })).toHaveAttribute("href", "/app/billing");
    selling.unmount();

    // in Stripe test mode it says so, as the pricing page does
    plans.mockResolvedValue({ ...PLANS_SELLING, billing_mode: "test" });
    const test = page(<LayoutsPage />, "/app/layouts");
    await waitFor(() => expect(screen.getByTestId("pro-explanation")).toHaveTextContent(/Test mode: no real charges/));
    test.unmount();

    // already on a paid plan: nothing about buying, only that the feature is not in the app yet
    useSessionStore.setState((st) => ({ session: st.session ? { ...st.session, plan: "pro" } : st.session }));
    page(<RoutinesPage />, "/app/routines");
    await waitFor(() => expect(plans).toHaveBeenCalledTimes(4));
    await new Promise((r) => setTimeout(r, 0));
    const paid = screen.getByTestId("pro-explanation");
    expect(paid).toHaveTextContent(/Your plan includes this once it arrives; it is not in the app yet\./);
    expect(paid.textContent).not.toMatch(/on sale|nothing to buy|\$9\.99/);
  });

  it("a failed GET /v1/plans keeps the static wording: nothing to buy yet", async () => {
    plans.mockRejectedValue(new Error("offline"));
    page(<RoutinesPage />, "/app/routines");
    await waitFor(() => expect(plans).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId("pro-explanation")).toHaveTextContent(/Coming at paid launch \(Pro from \$9\.99 \/ month\); there is nothing to buy yet/);
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
    // one glyph per status pill: its icon, never a dot and an icon side by side
    const pill = within(items[3]!).getByText("Needs attention");
    expect(pill.querySelectorAll("svg")).toHaveLength(1);
    expect(pill.querySelector("span[aria-hidden]")).toBeNull();
    expect(within(items[3]!).getByText("Enable remote control on the PC")).toBeInTheDocument();
    expect(within(items[5]!).getByText("Install or update the extension on the PC")).toBeInTheDocument();
    expect(within(items[4]!).getByText("OK")).toBeInTheDocument();
    expect(screen.queryByText("PC_REMOTE_DISABLED")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Technical details" }));
    expect(screen.getByText("PC_REMOTE_DISABLED")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What DoMe needs" })).toBeInTheDocument();
    expect(screen.getByText(/cannot wake or power on a PC remotely/)).toBeInTheDocument();
    expect(screen.getByText(/A green row never implies the others work/)).toBeInTheDocument();
  });

  it("retries are bounded and end in the support path; a retry never resends a command; the walkthrough points at the failed step", async () => {
    useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    page(<HealthPage />, "/app/health");
    const sentBefore = h.sockets[0]!.sent.length;
    const retry = screen.getByRole("button", { name: /^Check again/ });
    for (let i = 0; i < MAX_RETRIES; i++) await userEvent.click(screen.getByRole("button", { name: /^Check again/ }));
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

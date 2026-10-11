/**
 * Apps → Close… is the most common confirmation-required action. The phone sends only ids (never the
 * PC-supplied display name), the PC's `confirmation_required` lands on that record, and the global
 * modal renders the registry label with the inventory PC name and offers Approve because the
 * challenge binds to the command this phone sent.
 *
 * Protocol 1.2 window controls (Maximise, Restore, Full screen) act on apps that show a window only
 * (an app running with none, like Edge's Startup boost, offers Open), are stamped with their `since`,
 * never fall back to a silent maximise, and the list re-reads itself from the PC whenever the app comes
 * back to the foreground (apps added with the tray's "Add an app…") and after an app was opened or closed.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { GlobalConfirmation } from "../../src/app/AppShell.tsx";
import { configureApi } from "../../src/lib/api.ts";
import { AppsPage } from "../../src/pages/app/AppsPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { FakeSocket, flush, freshStorage, installDialogPolyfill, jsonResponse, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
const fixtureChallenge = JSON.parse(fixture.digests.challenge_text) as { command_id: string; pc_id: string; controller_id: string };

installDialogPolyfill();

let h: RuntimeHarness;
let socket: FakeSocket;

beforeEach(async () => {
  freshStorage();
  signedIn();
  h = makeRuntime();
  socket = await h.connect();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
  const live = useLiveStore.getState();
  live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
  live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true } });
  live.setApps(PC, [{ app_id: "notepad", display_name: "Notepad <b>x</b>", running: true, windows: [{ window_id: "w1", title: "Untitled — Notepad" }] }]);
});

afterEach(() => h.teardown());

function renderApps() {
  return render(
    <MemoryRouter initialEntries={["/app/apps"]}>
      <AppsPage />
      <GlobalConfirmation />
    </MemoryRouter>,
  );
}

describe("AppsPage", () => {
  it("Close… sends a signed command carrying only the app id; the PC's challenge opens the modal with the registry label and the inventory PC name", async () => {
    renderApps();
    expect(screen.getByText("Notepad <b>x</b>")).toBeInTheDocument(); // display name rendered as text
    const sentBefore = socket.sent.length;
    await userEvent.click(screen.getByRole("button", { name: "Close…" }));
    await waitFor(() => expect(useLiveStore.getState().commands.find((c) => c.action === "app.close")).toBeDefined());
    const rec = useLiveStore.getState().commands.find((c) => c.action === "app.close")!;
    expect(rec.pcId).toBe(PC);
    expect(rec.target).toEqual({ app_id: "notepad" });
    expect(rec.params).toEqual({});
    const frames = socket.frames().slice(sentBefore);
    const cmd = frames.find((f) => f.type === "command")!;
    expect(cmd).toBeDefined();
    expect(cmd.pc_id).toBe(PC);
    expect(JSON.stringify(cmd)).not.toContain("Notepad"); // only ids leave the phone
    expect(screen.queryByTestId("confirm-primary")).toBeNull();

    // the PC answers with a challenge for exactly this command (same controller/PC as the fixture)
    expect(fixtureChallenge.pc_id).toBe(PC);
    const text = fixture.digests.challenge_text.replace(fixtureChallenge.command_id, rec.commandId).replace('"expires_at":"2026-10-08T12:01:00.000Z"', `"expires_at":"${new Date(Date.now() + 60_000).toISOString()}"`);
    socket.receive({ type: "confirmation_required", command_id: rec.commandId, challenge_text: text });
    await waitFor(() => expect(screen.getByTestId("confirm-primary")).toBeInTheDocument());
    expect(screen.getByTestId("confirm-primary")).toHaveTextContent("Close Notepad <b>x</b>"); // registry label + app.list name, as text
    expect(screen.queryByText(/Close Notepad$/)).toBeNull(); // never the challenge's display.action_label
    expect(screen.getAllByText("Office PC")).toHaveLength(2); // PC switcher + the modal header
    expect(screen.queryByText(/Wohnzimmer/)).toBeNull();
    expect(screen.getByTestId("confirm-detail")).toHaveTextContent("Untitled — Notepad / 📝");
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(useLiveStore.getState().commands.find((c) => c.commandId === rec.commandId)!.confirmation!.bindingProblem).toBeNull();
  });

  it("Approve from the modal sends a signed confirmation frame for the challenge's PC", async () => {
    renderApps();
    await userEvent.click(screen.getByRole("button", { name: "Close…" }));
    await waitFor(() => expect(useLiveStore.getState().commands.find((c) => c.action === "app.close")).toBeDefined());
    const rec = useLiveStore.getState().commands.find((c) => c.action === "app.close")!;
    const text = fixture.digests.challenge_text.replace(fixtureChallenge.command_id, rec.commandId).replace('"expires_at":"2026-10-08T12:01:00.000Z"', `"expires_at":"${new Date(Date.now() + 60_000).toISOString()}"`);
    socket.receive({ type: "confirmation_required", command_id: rec.commandId, challenge_text: text });
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    const before = socket.sent.length;
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(socket.sent.length).toBeGreaterThan(before));
    const frame = socket.frames().at(-1)!;
    expect(frame.type).toBe("confirmation");
    expect(frame.pc_id).toBe(PC);
    expect(useLiveStore.getState().commands.find((c) => c.commandId === rec.commandId)!.confirmation!.decision).toBe("approve");
    await waitFor(() => expect(screen.queryByTestId("confirm-primary")).toBeNull()); // answered: modal gone
  });

  it("controls are withheld while the PC's state is stale", () => {
    useLiveStore.getState().markAllStale();
    renderApps();
    expect(screen.getByRole("button", { name: "Close…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Open again" })).toBeDisabled();
  });
});

interface SentCommand {
  command_id: string;
  action: string;
  protocol_version: string;
  params: Record<string, unknown>;
  target: Record<string, unknown> | null;
}

function sentCommands(action: string): SentCommand[] {
  return socket
    .frames()
    .filter((f) => f.type === "command")
    .map((f) => JSON.parse((f.envelope as { payload: string }).payload) as SentCommand)
    .filter((p) => p.action === action);
}

function answer(commandId: string, frame: Record<string, unknown>) {
  act(() => socket.receive({ type: "result", command_id: commandId, origin: "agent", at: TS, duration_ms: 5, ...frame }));
}

/** What the relay answers a (re-)subscribe with: the PC's status and its current state. */
function freshState(extra: Record<string, unknown> = {}) {
  act(() => {
    socket.receive({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    socket.receive({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, ...extra } });
  });
}

const APPS = [
  { app_id: "notepad", display_name: "Notepad", running: true, windows: [{ window_id: "w1", title: "Untitled — Notepad" }] },
  { app_id: "spotify", display_name: "Spotify", running: false, windows: [] },
  { app_id: "edge", display_name: "Edge", running: true, windows: [{ window_id: "e1", title: "News" }, { window_id: "e2", title: "Mail" }] },
];

/** What the agent says when it does not press F11 in an app: why, and how to allow full screen for it on the PC. */
const PC_SAYS_NO_F11 = "DoMe presses F11 only in apps where it means full screen. Nothing was pressed. To allow it for this app, open the DoMe tray menu on the PC → Approved apps… and turn on full screen (F11) for it.";

function card(name: string): HTMLElement {
  return screen.getByText(name).closest("li")!;
}

describe("AppsPage window controls (protocol 1.2)", () => {
  beforeEach(() => {
    useLiveStore.getState().setApps(PC, APPS);
  });

  it("Maximise, Restore and Full screen send ids only, stamped with the actions' since; app.list stays 1.0", async () => {
    renderApps();
    const notepad = within(card("Notepad"));
    await userEvent.click(notepad.getByRole("button", { name: "Maximise" }));
    await userEvent.click(notepad.getByRole("button", { name: "Restore" }));
    await userEvent.click(notepad.getByRole("button", { name: "Full screen" }));
    await waitFor(() => expect(sentCommands("app.set_fullscreen")).toHaveLength(1));
    expect(sentCommands("app.maximize")).toEqual([expect.objectContaining({ protocol_version: "1.2", params: {}, target: { app_id: "notepad" } })]);
    expect(sentCommands("app.restore")).toEqual([expect.objectContaining({ protocol_version: "1.2", params: {}, target: { app_id: "notepad" } })]);
    expect(sentCommands("app.set_fullscreen")).toEqual([expect.objectContaining({ protocol_version: "1.2", params: { enabled: true }, target: { app_id: "notepad" } })]);
    await waitFor(() => expect(sentCommands("app.list").length).toBeGreaterThan(0));
    expect(sentCommands("app.list").every((c) => c.protocol_version === "1.0")).toBe(true);
    expect(useLiveStore.getState().commands.find((c) => c.action === "app.maximize")!.protocolVersion).toBe("1.2");
  });

  it("each window of a multi-window app has its own Max / Restore / Full, targeted by window id", async () => {
    renderApps();
    const edge = within(card("Edge"));
    await userEvent.click(edge.getByText("Windows"));
    const mail = within(edge.getByText("Mail").closest("li")!);
    await userEvent.click(mail.getByRole("button", { name: "Max" }));
    await userEvent.click(mail.getByRole("button", { name: "Restore" }));
    await userEvent.click(mail.getByRole("button", { name: "Full" }));
    await waitFor(() => expect(sentCommands("app.set_fullscreen")).toHaveLength(1));
    expect(sentCommands("app.maximize")[0]!.target).toEqual({ app_id: "edge", window_id: "e2" });
    expect(sentCommands("app.restore")[0]!.target).toEqual({ app_id: "edge", window_id: "e2" });
    expect(sentCommands("app.set_fullscreen")[0]!.target).toEqual({ app_id: "edge", window_id: "e2" });
  });

  it("window controls are off for an app that is not running (they never start it) and for every app while locked", () => {
    const { unmount } = renderApps();
    const spotify = within(card("Spotify"));
    for (const name of ["Bring to front", "Minimise", "Maximise", "Restore", "Full screen", "Close…"]) expect(spotify.getByRole("button", { name }), name).toBeDisabled();
    expect(spotify.getByRole("button", { name: "Open" })).toBeEnabled();
    expect(within(card("Notepad")).getByRole("button", { name: "Maximise" })).toBeEnabled();
    unmount();
    freshState({ session_locked: true });
    renderApps();
    for (const app of ["Notepad", "Edge"]) {
      // every Maximise/Restore/Full screen of the app, and each of its windows' rows
      for (const name of ["Maximise", "Restore", "Full screen", "Max", "Full"]) for (const b of within(card(app)).queryAllByRole("button", { name })) expect(b, `${app} ${name}`).toBeDisabled();
    }
    expect(within(card("Edge")).getAllByRole("button", { name: "Restore" })).toHaveLength(3);
  });

  it("the Full screen toggle follows the state the PC read back", async () => {
    renderApps();
    const notepad = within(card("Notepad"));
    expect(notepad.getByRole("button", { name: "Full screen" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(notepad.getByRole("button", { name: "Full screen" }));
    await waitFor(() => expect(sentCommands("app.set_fullscreen")).toHaveLength(1));
    answer(sentCommands("app.set_fullscreen")[0]!.command_id, { state: "succeeded", result: { app_id: "notepad", window_id: "w1", state: "normal", fullscreen: true, foreground: true } });
    await waitFor(() => expect(notepad.getByRole("button", { name: "Leave full screen" })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByText("The window is in full screen.")).toBeInTheDocument();
    await userEvent.click(notepad.getByRole("button", { name: "Leave full screen" }));
    await waitFor(() => expect(sentCommands("app.set_fullscreen")).toHaveLength(2));
    expect(sentCommands("app.set_fullscreen")[1]!.params).toEqual({ enabled: false });
  });

  it("FULLSCREEN_UNSUPPORTED shows the PC's own explanation and offers 'Maximise instead' as a separate command; nothing is maximised before it is tapped", async () => {
    renderApps();
    await userEvent.click(within(card("Notepad")).getByRole("button", { name: "Full screen" }));
    await waitFor(() => expect(sentCommands("app.set_fullscreen")).toHaveLength(1));
    answer(sentCommands("app.set_fullscreen")[0]!.command_id, { state: "failed", error: { code: "FULLSCREEN_UNSUPPORTED", message: PC_SAYS_NO_F11, retryable: false }, result: { app_id: "notepad", window_id: "w1", state: "normal", fullscreen: false, foreground: true } });
    const instead = await screen.findByRole("button", { name: "Maximise instead" });
    // the agent's words (how to allow full screen for that app on the PC), not the generic copy
    expect(within(screen.getByTestId("command-outcome")).getByText(PC_SAYS_NO_F11)).toBeInTheDocument();
    expect(screen.queryByText(/That app did not switch to full screen with F11/)).toBeNull();
    expect(screen.getByText("Try Maximise instead.")).toBeInTheDocument();
    expect(sentCommands("app.maximize")).toHaveLength(0);
    await userEvent.click(instead);
    await waitFor(() => expect(sentCommands("app.maximize")).toHaveLength(1));
    expect(sentCommands("app.maximize")[0]!.target).toEqual({ app_id: "notepad" });
    // the new command's outcome replaces the failure, so the offer goes away
    await waitFor(() => expect(screen.queryByRole("button", { name: "Maximise instead" })).toBeNull());
  });

  it("an app running without a window (Edge's Startup boost) offers Open; its window buttons are off", () => {
    useLiveStore.getState().setApps(PC, [{ app_id: "edge", display_name: "Edge", running: true, windows: [] }]);
    renderApps();
    const edge = within(card("Edge"));
    expect(edge.getByText("Running in the background · no open window")).toBeInTheDocument();
    expect(edge.getByText("Background")).toBeInTheDocument();
    for (const name of ["Bring to front", "Minimise", "Maximise", "Restore", "Full screen", "Close…"]) expect(edge.getByRole("button", { name }), name).toBeDisabled();
    expect(edge.getByRole("button", { name: "Open" })).toBeEnabled();
  });

  it("TARGET_GONE for an app's window says it has no open window and to open it (not to refresh a tab)", async () => {
    renderApps();
    await userEvent.click(within(card("Notepad")).getByRole("button", { name: "Maximise" }));
    await waitFor(() => expect(sentCommands("app.maximize")).toHaveLength(1));
    answer(sentCommands("app.maximize")[0]!.command_id, { state: "failed", error: { code: "TARGET_GONE", message: "The app is running but has no window to control", retryable: false } });
    expect(await screen.findByText("The app has no open window on the PC.")).toBeInTheDocument();
    expect(screen.getByText(/Open it from Apps to show its window/)).toBeInTheDocument();
    expect(screen.queryByText(/Refresh and pick the current one/)).toBeNull();
  });

  it("APP_NOT_RUNNING says to open the app first", async () => {
    renderApps();
    await userEvent.click(within(card("Notepad")).getByRole("button", { name: "Maximise" }));
    await waitFor(() => expect(sentCommands("app.maximize")).toHaveLength(1));
    answer(sentCommands("app.maximize")[0]!.command_id, { state: "failed", error: { code: "APP_NOT_RUNNING", message: "x", retryable: false } });
    expect(await screen.findByText(/Open it first from Apps/)).toBeInTheDocument();
  });
});

describe("AppsPage list refresh", () => {
  it("after Open succeeds the list is read again (now and a moment later), so Maximise works without Refresh", async () => {
    useLiveStore.getState().setApps(PC, [{ app_id: "spotify", display_name: "Spotify", running: false, windows: [] }]);
    renderApps();
    await waitFor(() => expect(sentCommands("app.list")).toHaveLength(1)); // on entry
    answer(sentCommands("app.list")[0]!.command_id, { state: "succeeded", result: { apps: [{ app_id: "spotify", display_name: "Spotify", running: false, windows: [] }] } });
    const spotify = () => within(card("Spotify"));
    expect(spotify().getByRole("button", { name: "Maximise" })).toBeDisabled();
    await userEvent.click(spotify().getByRole("button", { name: "Open" }));
    await waitFor(() => expect(sentCommands("app.launch")).toHaveLength(1));
    answer(sentCommands("app.launch")[0]!.command_id, { state: "succeeded", result: { app_id: "spotify", launched: true, running: true } });
    await waitFor(() => expect(sentCommands("app.list")).toHaveLength(2)); // at once: running, no window yet
    answer(sentCommands("app.list")[1]!.command_id, { state: "succeeded", result: { apps: [{ app_id: "spotify", display_name: "Spotify", running: true, windows: [] }] } });
    expect(await screen.findByText("Running in the background · no open window")).toBeInTheDocument();
    await waitFor(() => expect(sentCommands("app.list")).toHaveLength(3), { timeout: 3000 }); // again ~1.5 s later
    answer(sentCommands("app.list")[2]!.command_id, { state: "succeeded", result: { apps: [{ app_id: "spotify", display_name: "Spotify", running: true, windows: [{ window_id: "s1", title: "Spotify" }] }] } });
    await waitFor(() => expect(spotify().getByRole("button", { name: "Maximise" })).toBeEnabled());
    expect(spotify().getByText("Running · 1 window")).toBeInTheDocument();
  }, 10_000);

  it("with no approved apps, points to the tray's Add an app… (never to typing commands on the PC)", () => {
    useLiveStore.getState().setApps(PC, []);
    renderApps();
    expect(screen.getByText("No approved apps yet")).toBeInTheDocument();
    expect(screen.getByText(/open the DoMe tray menu, choose Add an app…/)).toBeInTheDocument();
  });

  it("asks the PC on entry and again whenever the app returns to the foreground, so apps added on the PC appear by themselves", async () => {
    configureApi({ fetchImpl: async (url) => (String(url).includes("/v1/pcs") ? jsonResponse(200, { pcs: [OFFICE_PC] }) : jsonResponse(200, { controllers: [] })) });
    useLiveStore.getState().setApps(PC, [APPS[0]!]);
    renderApps();
    await waitFor(() => expect(sentCommands("app.list")).toHaveLength(1));
    answer(sentCommands("app.list")[0]!.command_id, { state: "succeeded", result: { apps: [APPS[0]] } });
    expect(screen.queryByText("Spotify")).toBeNull();

    // Back from the PC's tray: the page becomes visible; once the PC's state is fresh again the list is re-read.
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await flush();
    expect(sentCommands("app.list")).toHaveLength(1); // state is stale right after resuming: nothing sent into a reconnect
    freshState();
    await waitFor(() => expect(sentCommands("app.list")).toHaveLength(2));
    // A second trigger while that answer is outstanding does not ask twice.
    act(() => window.dispatchEvent(new Event("focus")));
    await flush();
    expect(sentCommands("app.list")).toHaveLength(2);
    answer(sentCommands("app.list")[1]!.command_id, { state: "succeeded", result: { apps: [APPS[0], APPS[1]] } });
    expect(await screen.findByText("Spotify")).toBeInTheDocument();

    // Window focus alone (e.g. returning from another tab) also re-reads it.
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(sentCommands("app.list")).toHaveLength(3));
  });
});

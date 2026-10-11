/**
 * Type page over the real runtime and a fake relay socket: it loads the approved apps itself, previews the plan
 * step by step (describeAction), sends a plan's steps one at a time — the next only after the previous one's result
 * succeeded — stops at the first failure saying which step and why, feeds a search's results into the following
 * open_result, re-reads the app list once before calling a name "not approved", and its chips and examples only
 * fill the text box. Before a result opens, a video playing in another tab is paused (a listed step); a window step
 * right after its app was started waits for the first window; Stop and a dropped connection end the plan at once.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import { clearLogs, recentLogs } from "../../src/lib/log.ts";
import { CommandPage } from "../../src/pages/app/CommandPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore, type YoutubeTab } from "../../src/store/live.ts";
import { CONTROLLER, FakeSocket, freshStorage, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

const ALL_CAPS: rest.Grant["capabilities"] = ["status", "media", "volume", "apps", "lock", "power"];
const GRANT: rest.Grant = { id: "66666666-6666-4666-8666-666666666666", controller_id: CONTROLLER, pc_id: PC, capabilities: ALL_CAPS, created_at: TS };
const EDGE = { browser_instance_id: "bi_test0001", browser: "edge" as const };
const PLAYING: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, title: "Now playing", video_id: "playing1", paused: false, context: "watch", ad_showing: false, is_live: false, in_playlist: false };
const RESULTS: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD", script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false };
const NOTEPAD = { app_id: "notepad", display_name: "Notepad", running: true, windows: [] };
const NOTEPADPP = { app_id: "notepadpp", display_name: "Notepad++", running: true, windows: [] };
const VLC = { app_id: "vlc", display_name: "VLC", running: false, windows: [] };
/** The owner's own app, approved with the tray's Add an app… from C:\zstream\zstream-app.exe. */
const ZSTREAM = { app_id: "zstream-app", display_name: "ZStream", running: true, windows: [{ window_id: "z1", title: "ZStream" }] };
/** What the agent says when it does not press F11 in an app: why, and how to allow full screen for it on the PC. */
const PC_SAYS_NO_F11 = "DoMe presses F11 only in apps where it means full screen. Nothing was pressed. To allow it for ZStream, open the DoMe tray menu on the PC → Approved apps… and turn on full screen (F11) for it.";

let h: RuntimeHarness;
let socket: FakeSocket;

interface SentCommand {
  command_id: string;
  action: string;
  protocol_version: string;
  params: Record<string, unknown>;
  target: Record<string, unknown> | null;
}

function sent(action?: string): SentCommand[] {
  return socket
    .frames()
    .filter((f) => f.type === "command")
    .map((f) => JSON.parse((f.envelope as { payload: string }).payload) as SentCommand)
    .filter((p) => action === undefined || p.action === action);
}

function pcState(extra: Record<string, unknown> = {}) {
  act(() => socket.receive({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, volume: { value: 40, muted: false }, browser_instances: [EDGE], youtube_tabs: [PLAYING], ...extra } }));
}

function answer(commandId: string, result: Record<string, unknown> | null, error?: { code: string; message: string }) {
  act(() =>
    socket.receive(
      error ? { type: "result", command_id: commandId, origin: "agent", state: "failed", at: TS, duration_ms: 20, error: { ...error, retryable: false } } : { type: "result", command_id: commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 20, result },
    ),
  );
}

async function answerAppList(apps: unknown[], index = 0) {
  await waitFor(() => expect(sent("app.list").length).toBeGreaterThan(index));
  answer(sent("app.list")[index]!.command_id, { apps });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/app/command"]}>
      <CommandPage />
    </MemoryRouter>,
  );
}

const box = () => screen.getByRole("textbox", { name: /What should Office PC do/ });
const preview = () => document.getElementById("command-preview")!;

async function typeCommand(text: string) {
  await userEvent.clear(box());
  await userEvent.type(box(), text);
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  clearLogs();
  h = makeRuntime();
  socket = await h.connect();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC, controllers: [], grantsByPc: { [PC]: [GRANT] } });
  act(() => socket.receive({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS }));
  pcState();
});

afterEach(() => h.teardown());

describe("Type a command", () => {
  it("loads the approved apps itself, previews one step and sends exactly that command", async () => {
    renderPage();
    await answerAppList([NOTEPAD]);
    await typeCommand("Set my PC volume to 35 percent");
    expect(preview()).toHaveTextContent("Will send: Set PC volume to 35% on Office PC.");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent("windows.set_volume")).toHaveLength(1));
    expect(sent("windows.set_volume")[0]).toMatchObject({ params: { value: 35 }, target: null });
    expect(box()).toHaveValue("");
    answer(sent("windows.set_volume")[0]!.command_id, { value: 35, muted: false });
    expect(await screen.findByText("PC volume is now 35%.")).toBeInTheDocument();
  });

  it("a double tap sends the command once", async () => {
    renderPage();
    await typeCommand("lock my computer");
    const form = box().closest("form")!;
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    await waitFor(() => expect(sent("windows.lock")).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(sent("windows.lock")).toHaveLength(1);
  });

  it("the owner's phrase 'make the volume 100%' is understood", async () => {
    renderPage();
    await typeCommand("make the volume 100%");
    expect(preview()).toHaveTextContent("Will send: Set PC volume to 100% on Office PC.");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("a two-step plan is listed, then sent one step at a time: step 2 only after step 1 succeeded", async () => {
    renderPage();
    await answerAppList([NOTEPAD]);
    await typeCommand("open notepad and maximise it");
    expect(preview()).toHaveTextContent("Will run 2 steps on Office PC, one after another:");
    expect(screen.getByRole("list", { name: "Steps" })).toHaveTextContent("Open Notepad");
    expect(screen.getByRole("list", { name: "Steps" })).toHaveTextContent("Maximise Notepad");
    await userEvent.click(screen.getByRole("button", { name: "Run 2 steps" }));
    await waitFor(() => expect(sent("app.launch")).toHaveLength(1));
    expect(sent("app.maximize")).toHaveLength(0);
    answer(sent("app.launch")[0]!.command_id, { app_id: "notepad", launched: true, running: true });
    await waitFor(() => expect(sent("app.maximize")).toHaveLength(1));
    expect(sent("app.maximize")[0]).toMatchObject({ protocol_version: "1.2", target: { app_id: "notepad" } });
    answer(sent("app.maximize")[0]!.command_id, { app_id: "notepad", window_id: "w1", state: "maximized", fullscreen: false, foreground: true });
    await waitFor(() => expect(document.querySelectorAll('[data-step-status="succeeded"]')).toHaveLength(2));
    // the app was opened: the app list is read again so "running" is current for the next command
    await waitFor(() => expect(sent("app.list")).toHaveLength(2));
  });

  it("stops at the first failed step and says which step failed and why; the rest is never sent", async () => {
    renderPage();
    await typeCommand("pause youtube then lock my computer");
    await userEvent.click(screen.getByRole("button", { name: "Run 2 steps" }));
    await waitFor(() => expect(sent("youtube.set_paused")).toHaveLength(1));
    answer(sent("youtube.set_paused")[0]!.command_id, null, { code: "TAB_NOT_CONTROLLABLE", message: "not attached" });
    expect(await screen.findByText("Stopped at step 1 of 2")).toBeInTheDocument();
    expect(screen.getByText("The steps after it were not sent.")).toBeInTheDocument();
    expect(screen.getByText("Reload the YouTube tab on the PC so the DoMe extension can attach to it.")).toBeInTheDocument();
    expect(sent("windows.lock")).toHaveLength(0);
  });

  it("'play … on YouTube' pauses the video playing elsewhere, searches, then opens the first result of that search in its results tab; the query is never logged", async () => {
    renderPage();
    await typeCommand("play lofi beats on YouTube");
    const steps = screen.getByRole("list", { name: "Steps" });
    expect(steps).toHaveTextContent("Pause YouTube");
    expect(steps).toHaveTextContent("“Now playing” is playing in another tab; paused first so two videos don't play at once");
    expect(steps).toHaveTextContent("Search YouTube for “lofi beats”");
    expect(steps).toHaveTextContent("Opens a new tab in Edge");
    await userEvent.click(screen.getByRole("button", { name: "Run 3 steps" }));
    await waitFor(() => expect(sent("youtube.set_paused")).toHaveLength(1));
    expect(sent("youtube.set_paused")[0]).toMatchObject({ params: { paused: true }, target: { tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD" } });
    expect(sent("youtube.search")).toHaveLength(0);
    answer(sent("youtube.set_paused")[0]!.command_id, { tab: { ...PLAYING, paused: true } });
    await waitFor(() => expect(sent("youtube.search")).toHaveLength(1));
    expect(sent("youtube.search")[0]).toMatchObject({ params: { query: "lofi beats" }, target: { browser_instance_id: "bi_test0001" } });
    expect(sent("youtube.open_result")).toHaveLength(0);
    answer(sent("youtube.search")[0]!.command_id, { tab: RESULTS, results: [{ index: 1, video_id: "vidA", title: "Lofi" }, { index: 2, video_id: "vidB", title: "More" }] });
    await waitFor(() => expect(sent("youtube.open_result")).toHaveLength(1));
    expect(sent("youtube.open_result")[0]).toMatchObject({ params: { index: 1, video_id: "vidA" }, target: { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD" } });
    expect(JSON.stringify(recentLogs())).not.toContain("lofi");
  });

  it("after a search, its results are chips that fill 'play the Nth video' (never sent by a tap)", async () => {
    renderPage();
    await typeCommand("search youtube for jazz");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent("youtube.search")).toHaveLength(1));
    answer(sent("youtube.search")[0]!.command_id, { tab: RESULTS, results: [{ index: 1, video_id: "vidA", title: "Jazz <i>live</i>" }] });
    pcState({ youtube_tabs: [{ ...PLAYING, paused: true }, RESULTS] });
    await userEvent.click(await screen.findByRole("button", { name: "1. Jazz <i>live</i>" }));
    expect(box()).toHaveValue("play the first video");
    expect(preview()).toHaveTextContent("Will send: Open search result 1 on Office PC.");
    expect(preview()).toHaveTextContent("“Jazz <i>live</i>”");
    expect(sent("youtube.open_result")).toHaveLength(0);
    // with a video playing in another tab, the preview lists pausing it first
    pcState({ youtube_tabs: [PLAYING, RESULTS] });
    expect(preview()).toHaveTextContent("Will run 2 steps on Office PC, one after another:");
    expect(screen.getByRole("list", { name: "Steps" })).toHaveTextContent("Pause YouTube");
  });

  it("'click on that movie' is an honest handoff to the Touchpad; nothing can be sent", async () => {
    renderPage();
    await typeCommand("click on that movie");
    expect(preview()).toHaveTextContent("DoMe can't see your screen");
    expect(screen.getByRole("link", { name: "Open Touchpad" })).toHaveAttribute("href", "/app/touchpad");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("an ambiguous name asks; tapping a choice fills the box and sends nothing", async () => {
    renderPage();
    await answerAppList([NOTEPAD, NOTEPADPP]);
    await typeCommand("open note");
    expect(preview()).toHaveTextContent("Which one: Notepad or Notepad++?");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "open Notepad++" }));
    expect(box()).toHaveValue("open Notepad++");
    expect(preview()).toHaveTextContent("Will send: Open Notepad++ on Office PC.");
    expect(sent("app.launch")).toHaveLength(0);
  });

  it("an unknown app name re-reads the PC's app list once before saying it is not approved", async () => {
    renderPage();
    await answerAppList([NOTEPAD]);
    // the list is old by now (an app may have been added on the PC since: DoMe tray → Add an app…)
    act(() => useLiveStore.setState((s) => ({ appsByPc: { ...s.appsByPc, [PC]: { ...s.appsByPc[PC]!, at: Date.now() - 60_000 } } })));
    await typeCommand("open vlc");
    expect(preview()).toHaveTextContent("Checking the approved apps on Office PC…");
    await answerAppList([NOTEPAD, VLC], 1);
    await waitFor(() => expect(preview()).toHaveTextContent("Will send: Open VLC on Office PC."));

    await typeCommand("open powershell");
    await waitFor(() => expect(preview()).toHaveTextContent("“powershell” is not an approved app on this PC. Add it on the PC: DoMe tray → Add an app…"), { timeout: 2000 });
    expect(sent("app.list")).toHaveLength(2); // the list was just read: not asked again
  });

  it("a step this phone has no permission for, or that needs the PC unlocked, cannot be sent and says why", async () => {
    useDevicesStore.setState({ grantsByPc: { [PC]: [{ ...GRANT, capabilities: ["status", "media"] }] } });
    renderPage();
    await typeCommand("make the volume 100%");
    expect(preview()).toHaveTextContent("This phone does not have the “Change PC volume” permission on this PC.");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    useDevicesStore.setState({ grantsByPc: { [PC]: [GRANT] } });
    pcState({ session_locked: true });
    await typeCommand("lock");
    expect(preview()).toHaveTextContent("The PC is locked. Unlock Windows on the PC first.");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("Stop ends the run at once, says the running step may still finish, and sends nothing after it", async () => {
    renderPage();
    await answerAppList([NOTEPAD]);
    await typeCommand("open notepad and maximise it");
    await userEvent.click(screen.getByRole("button", { name: "Run 2 steps" }));
    await waitFor(() => expect(sent("app.launch")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    // no answer from the PC needed to see that it stopped
    expect(await screen.findByText("Stopped. Step 1 was already on its way and may still finish on the PC; the steps after it were not sent.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    answer(sent("app.launch")[0]!.command_id, { app_id: "notepad", launched: true, running: true });
    // its outcome still shows when it arrives
    await waitFor(() => expect(document.querySelector('[data-step-status="unknown"]')).toHaveTextContent("Done"));
    expect(sent("app.maximize")).toHaveLength(0);
  });

  it("the connection dropping while a step runs ends the plan and says that step may still have run", async () => {
    renderPage();
    await typeCommand("mute the pc and lock my computer");
    await userEvent.click(screen.getByRole("button", { name: "Run 2 steps" }));
    await waitFor(() => expect(sent("windows.set_muted")).toHaveLength(1));
    act(() => socket.serverClose(1006));
    expect(await screen.findByText("Stopped at step 1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/The connection to DoMe dropped while step 1 was running, so its result is unknown: it may still have run on the PC/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing was sent/)).toBeNull();
    expect(sent("windows.lock")).toHaveLength(0);
  });

  it("a window step right after its app was started waits for the app's first window, then runs", async () => {
    renderPage();
    await answerAppList([NOTEPAD]);
    await typeCommand("open notepad and maximise it");
    await userEvent.click(screen.getByRole("button", { name: "Run 2 steps" }));
    await waitFor(() => expect(sent("app.launch")).toHaveLength(1));
    answer(sent("app.launch")[0]!.command_id, { app_id: "notepad", launched: true, running: true });
    await waitFor(() => expect(sent("app.maximize")).toHaveLength(1));
    answer(sent("app.maximize")[0]!.command_id, null, { code: "TARGET_GONE", message: "The app is running but has no window to control" });
    expect(await screen.findByText("Waiting for Notepad to open its window…")).toBeInTheDocument();
    await waitFor(() => expect(sent("app.maximize")).toHaveLength(2), { timeout: 4000 });
    answer(sent("app.maximize")[1]!.command_id, { app_id: "notepad", window_id: "w1", state: "maximized", fullscreen: false, foreground: true });
    await waitFor(() => expect(document.querySelectorAll('[data-step-status="succeeded"]')).toHaveLength(2));
    expect(screen.queryByText(/Waiting for Notepad/)).toBeNull();
    expect(screen.queryByText(/Stopped at step/)).toBeNull();
  }, 10_000);

  it("app examples name the app so they parse back: Spotify first, and an id when the name can't be typed", async () => {
    renderPage();
    await answerAppList([{ app_id: "spotify", display_name: "Spotify", running: true, windows: [{ window_id: "s1", title: "Spotify" }] }, NOTEPAD]);
    for (const ex of ["Open Spotify", "Bring Spotify to the front", "Full screen Spotify", "Exit full screen Spotify", "Open Spotify and maximise it"]) {
      await userEvent.click(screen.getByRole("button", { name: ex }));
      expect(preview(), ex).toHaveTextContent(/Will (send|run)/);
    }
    expect(preview()).toHaveTextContent("Maximise Spotify");
  });

  it("app examples use the app id when the display name could not be typed back as a command", async () => {
    renderPage();
    await answerAppList([{ app_id: "player", display_name: "<b>Player</b>", running: true, windows: [] }]);
    await userEvent.click(screen.getByRole("button", { name: "Open player" }));
    expect(preview()).toHaveTextContent("Will send: Open <b>Player</b> on Office PC.");
  });

  it("examples teach the new phrases and only fill the box", async () => {
    renderPage();
    await answerAppList([NOTEPAD]);
    for (const ex of ["Make the volume 100%", "Search YouTube for lofi beats", "Play the second video", "Full screen Notepad", "Open Notepad and maximise it"]) expect(screen.getByRole("button", { name: ex })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Full screen Notepad" }));
    expect(box()).toHaveValue("Full screen Notepad");
    expect(preview()).toHaveTextContent("Will send: Make Notepad full screen (F11) on Office PC.");
    expect(sent().filter((c) => c.action !== "app.list")).toHaveLength(0);
  });
  it("the owner's 'full screen zstream': the examples use the approved app's own name, and each one previews the window step", async () => {
    renderPage();
    await answerAppList([ZSTREAM, NOTEPAD]);
    const expected: Record<string, string> = {
      "Full screen ZStream": "Will send: Make ZStream full screen (F11) on Office PC.",
      "Exit full screen ZStream": "Will send: Leave full screen in ZStream on Office PC.",
      "Open ZStream and maximise it": "Maximise ZStream",
    };
    for (const [ex, says] of Object.entries(expected)) {
      await userEvent.click(screen.getByRole("button", { name: ex }));
      expect(preview(), ex).toHaveTextContent(says);
    }
    expect(screen.queryByRole("button", { name: /Notepad/ })).toBeNull(); // no made-up app while real ones are approved
    await typeCommand("full screen zstream");
    expect(preview()).toHaveTextContent("Will send: Make ZStream full screen (F11) on Office PC.");
    expect(sent().filter((c) => c.action !== "app.list")).toHaveLength(0);
  });

  it("FULLSCREEN_UNSUPPORTED: the outcome shows the PC's own explanation, and a 'Maximise instead' chip fills a separate command", async () => {
    renderPage();
    await answerAppList([ZSTREAM]);
    await typeCommand("full screen zstream");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent("app.set_fullscreen")).toHaveLength(1));
    expect(sent("app.set_fullscreen")[0]).toMatchObject({ params: { enabled: true }, target: { app_id: "zstream-app" } });
    answer(sent("app.set_fullscreen")[0]!.command_id, null, { code: "FULLSCREEN_UNSUPPORTED", message: PC_SAYS_NO_F11 });
    const chip = await screen.findByRole("button", { name: "Maximise instead" });
    expect(screen.getByText(PC_SAYS_NO_F11)).toBeInTheDocument();
    expect(screen.getByText("Try Maximise instead.")).toBeInTheDocument();
    expect(screen.queryByText(/That app did not switch to full screen with F11/)).toBeNull();
    await userEvent.click(chip);
    expect(box()).toHaveValue("maximise ZStream");
    expect(preview()).toHaveTextContent("Will send: Maximise ZStream on Office PC.");
    expect(sent("app.maximize")).toHaveLength(0); // never maximised in its place without a tap on Send
  });

  it("FULLSCREEN_UNSUPPORTED in a later step: the stop notice gives the PC's explanation too, with the 'Maximise instead' chip", async () => {
    renderPage();
    await answerAppList([{ ...ZSTREAM, running: false, windows: [] }]);
    await typeCommand("open zstream and full screen");
    await userEvent.click(screen.getByRole("button", { name: "Run 2 steps" }));
    await waitFor(() => expect(sent("app.launch")).toHaveLength(1));
    answer(sent("app.launch")[0]!.command_id, { app_id: "zstream-app", launched: true, running: true });
    await waitFor(() => expect(sent("app.set_fullscreen")).toHaveLength(1));
    answer(sent("app.set_fullscreen")[0]!.command_id, null, { code: "FULLSCREEN_UNSUPPORTED", message: PC_SAYS_NO_F11 });
    expect(await screen.findByText("Stopped at step 2 of 2")).toBeInTheDocument();
    // in the failed step's outcome and in the notice that ends the plan
    expect(screen.getAllByText(PC_SAYS_NO_F11)).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Maximise instead" }));
    expect(box()).toHaveValue("maximise ZStream");
    expect(sent("app.maximize")).toHaveLength(0);
  });

  it("other failures offer no 'Maximise instead' chip", async () => {
    renderPage();
    await answerAppList([ZSTREAM]);
    await typeCommand("full screen zstream");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent("app.set_fullscreen")).toHaveLength(1));
    answer(sent("app.set_fullscreen")[0]!.command_id, null, { code: "APP_ELEVATED", message: "elevated" });
    await waitFor(() => expect(document.querySelector('[data-step-status="failed"]')).not.toBeNull());
    expect(screen.getAllByText(/runs as administrator/).length).toBeGreaterThan(0);
    expect(screen.queryByText("elevated")).toBeNull(); // the PC's text is shown only where it explains the code
    expect(screen.queryByRole("button", { name: "Maximise instead" })).toBeNull();
  });
});

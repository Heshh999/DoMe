/**
 * Remote → Search YouTube (protocol 1.2). The search goes to a new tab unless the customer chose a tab (or a results
 * tab is still open: the next search reuses it), carries only the trimmed query, clears the box once sent and never
 * reaches the log or diagnostics. The results tab is not selected (a list has no player: Pause keeps reaching the
 * video being watched), results render as plain text, and tapping one pauses a video playing in another tab, then
 * sends youtube.open_result with its index and video id to the results tab. Once a video was opened from the list,
 * the list is off until the next search.
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { buildDiagnostics } from "../../src/lib/diagnostics.ts";
import { clearLogs, recentLogs } from "../../src/lib/log.ts";
import { tabKey } from "../../src/lib/targets.ts";
import { RemotePage } from "../../src/pages/app/RemotePage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore, type YoutubeTab } from "../../src/store/live.ts";
import { FakeSocket, freshStorage, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

const EDGE = { browser_instance_id: "bi_test0001", browser: "edge" as const };
const CHROME = { browser_instance_id: "bi_test0002", browser: "chrome" as const };
const PLAYING: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, title: "Now playing", video_id: "playing1", paused: false, context: "watch", ad_showing: false, is_live: false, in_playlist: false };
const OTHER: YoutubeTab = { ...PLAYING, tab_id: 2, tab_token: "dGFiLXRva2VuLTAwMDAwMX", title: "Other tab", video_id: "other1", paused: true };
const RESULTS: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD", script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false };
const QUERY = "lofi beats to study";

let h: RuntimeHarness;
let socket: FakeSocket;

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

function pcState(extra: Record<string, unknown>) {
  act(() => socket.receive({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, browser_instances: [EDGE], youtube_tabs: [PLAYING], ...extra } }));
}

function renderRemote() {
  return render(
    <MemoryRouter initialEntries={["/app/remote"]}>
      <RemotePage />
    </MemoryRouter>,
  );
}

async function search(text: string) {
  const box = screen.getByRole("searchbox", { name: "Search YouTube" });
  await userEvent.clear(box);
  await userEvent.type(box, text);
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  clearLogs();
  h = makeRuntime();
  socket = await h.connect();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
  act(() => socket.receive({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS }));
  pcState({});
});

afterEach(() => h.teardown());

describe("Search YouTube", () => {
  it("opens a new tab by default (the playing video is left alone), sends only the trimmed query, then clears the box", async () => {
    renderRemote();
    expect(screen.getByRole("searchbox", { name: "Search YouTube" })).toHaveAttribute("maxLength", "100");
    expect(screen.getByText("Opens a new tab in Edge")).toBeInTheDocument();
    await search(`  ${QUERY}  `);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    expect(sentCommands("youtube.search")[0]).toMatchObject({ protocol_version: "1.2", params: { query: QUERY }, target: { browser_instance_id: "bi_test0001" } });
    expect(screen.getByRole("searchbox", { name: "Search YouTube" })).toHaveValue("");
  });

  it("the search text never reaches the log or the diagnostics report", async () => {
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: RESULTS, results: [{ index: 1, video_id: "vidA", title: "A" }] } }));
    await screen.findByText("1. A");
    expect(JSON.stringify(recentLogs())).not.toContain("lofi");
    const report = buildDiagnostics({ relayStatus: "open", controllerBound: true, keyAvailable: true, selectedPcId: PC, pcs: [], commands: useLiveStore.getState().commands, sessionPresent: true, plan: "free" });
    expect(report.commands.some((c) => c.action === "youtube.search")).toBe(true);
    expect(JSON.stringify(report)).not.toContain("lofi");
    expect(JSON.stringify(useLiveStore.getState().lastSearchByPc)).not.toContain("lofi");
  });

  it("keeps the playing video as the controlled tab; a tapped result pauses it first, then opens by index and video id in the results tab; results are plain text", async () => {
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    const results = [
      { index: 1, video_id: "vidA", title: "First <b>video</b>", channel: "Chan" },
      { index: 2, video_id: "vidB", title: "Second", duration_seconds: 125 },
    ];
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: RESULTS, results } }));
    pcState({ youtube_tabs: [PLAYING, RESULTS] });
    expect(await screen.findByText("1. First <b>video</b>")).toBeInTheDocument(); // as text, never markup
    expect(screen.getByText("Chan")).toBeInTheDocument();
    expect(screen.getByText("2:05")).toBeInTheDocument();
    expect(screen.getByText("Found 2 videos. Tap one to play it.")).toBeInTheDocument();
    // the list is no player: the main card and Pause still control the video being watched
    expect(useLiveStore.getState().selectedTab[PC]).toBeUndefined();
    expect(screen.getByTitle("Now playing")).toHaveTextContent("Now playing"); // the main card
    expect(screen.getByText(/“Now playing” is playing in another tab; it is paused first/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /2\. Second/ }));
    await waitFor(() => expect(sentCommands("youtube.open_result")).toHaveLength(1));
    expect(sentCommands("youtube.set_paused")).toEqual([expect.objectContaining({ params: { paused: true }, target: { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", expected_video_id: "playing1" } })]);
    const all = socket.frames().filter((f) => f.type === "command").map((f) => (JSON.parse((f.envelope as { payload: string }).payload) as SentCommand).action);
    expect(all.indexOf("youtube.set_paused")).toBeLessThan(all.indexOf("youtube.open_result"));
    expect(sentCommands("youtube.open_result")[0]).toMatchObject({ protocol_version: "1.2", params: { index: 2, video_id: "vidB" }, target: { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD" } });
    expect(sentCommands("youtube.open_result")[0]!.target).not.toHaveProperty("expected_video_id");
  });

  it("with nothing else playing, a result opens without a pause", async () => {
    pcState({ youtube_tabs: [{ ...PLAYING, paused: true }] });
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: RESULTS, results: [{ index: 1, video_id: "vidA", title: "A" }] } }));
    pcState({ youtube_tabs: [{ ...PLAYING, paused: true }, RESULTS] });
    await userEvent.click(await screen.findByRole("button", { name: /1\. A/ }));
    await waitFor(() => expect(sentCommands("youtube.open_result")).toHaveLength(1));
    expect(sentCommands("youtube.set_paused")).toHaveLength(0);
  });

  it("once a video was opened from the list, the list is off and says to search again; nothing more is sent", async () => {
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: RESULTS, results: [{ index: 1, video_id: "vidA", title: "A" }, { index: 2, video_id: "vidB", title: "B" }] } }));
    pcState({ youtube_tabs: [{ ...PLAYING, paused: true }, RESULTS] });
    await userEvent.click(await screen.findByRole("button", { name: /1\. A/ }));
    await waitFor(() => expect(sentCommands("youtube.open_result")).toHaveLength(1));
    const watching = { ...RESULTS, context: "watch" as const, video_id: "vidA", title: "A", paused: false };
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.open_result")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: watching } }));
    pcState({ youtube_tabs: [{ ...PLAYING, paused: true }, watching] });
    expect(await screen.findByText("You opened a video from this list, so that tab plays it now. Search again to pick another one.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /2\. B/ })).toBeDisabled();
    expect(screen.queryByText(/Tap a result to play it/)).toBeNull();
    // the opened video is now the one the remote controls
    expect(useLiveStore.getState().selectedTab[PC]).toBe(tabKey(watching));
    await userEvent.click(screen.getByRole("button", { name: /2\. B/ }));
    expect(sentCommands("youtube.open_result")).toHaveLength(1);
  });

  it("right after the answer, before the PC's next state report lists the new tab, the results are not 'closed'", async () => {
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: RESULTS, results: [{ index: 1, video_id: "vidA", title: "A" }] } }));
    // no state frame yet (the PC sends it debounced, about half a second later)
    expect(await screen.findByRole("button", { name: /1\. A/ })).toBeEnabled();
    expect(screen.queryByText(/The results tab was closed/)).toBeNull();
  });

  it("a later search runs again in the results tab while it shows results; the toggle switches back to a new tab", async () => {
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: RESULTS, results: [] } }));
    pcState({ youtube_tabs: [PLAYING, RESULTS] });
    expect(await screen.findByText("Searches again in the results tab")).toBeInTheDocument();
    expect(screen.getByText("No video results.")).toBeInTheDocument();
    await search("jazz");
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(2));
    expect(sentCommands("youtube.search")[1]!.target).toEqual({ browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD" });
    await userEvent.click(screen.getByRole("switch", { name: "Search in the results tab" }));
    expect(screen.getByText("Opens a new tab in Edge")).toBeInTheDocument();
    await search("jazz");
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(3));
    expect(sentCommands("youtube.search")[2]!.target).toEqual({ browser_instance_id: "bi_test0001" });
  });

  it("an explicitly chosen tab is searched in place", async () => {
    pcState({ youtube_tabs: [PLAYING, OTHER] });
    useLiveStore.getState().selectTab(PC, tabKey(OTHER));
    renderRemote();
    expect(screen.getByText("Searches in this tab: Other tab")).toBeInTheDocument();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    expect(sentCommands("youtube.search")[0]!.target).toEqual({ browser_instance_id: "bi_test0001", tab_id: 2, tab_token: "dGFiLXRva2VuLTAwMDAwMX" });
  });

  it("with two browsers and no tab the customer picks the browser first; without the extension nothing can be sent", async () => {
    pcState({ browser_instances: [EDGE, CHROME], youtube_tabs: [] });
    const { unmount } = renderRemote();
    expect(screen.getByText("Choose the browser to search in.")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox", { name: "Search YouTube" }), QUERY);
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
    await userEvent.click(within(screen.getByRole("list", { name: "Browser" })).getByRole("button", { name: "Chrome" }));
    expect(screen.getByText("Opens a new tab in Chrome")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    expect(sentCommands("youtube.search")[0]!.target).toEqual({ browser_instance_id: "bi_test0002" });
    unmount();

    pcState({ extension_connected: false, youtube_tabs: [] });
    renderRemote();
    expect(screen.getByText("Searching needs the DoMe browser extension, which is not connected on the PC.")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox", { name: "Search YouTube" }), QUERY);
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  });

  it("a results page picked by hand in the remote shows no player buttons, and Pause never goes there", async () => {
    pcState({ youtube_tabs: [PLAYING, RESULTS] });
    useLiveStore.getState().selectTab(PC, tabKey(RESULTS));
    renderRemote();
    expect(screen.getByText("YouTube search results: pick a video below")).toBeInTheDocument();
    for (const name of ["Previous video", "Back 10 seconds", "Pause", "Forward 10 seconds", "Next video"]) expect(screen.getByRole("button", { name }), name).toBeDisabled();
    expect(screen.getByRole("button", { name: /Theater mode/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Fullscreen" })).toBeDisabled();
  });

  it("with only a results page open, the remote says there is no video instead of controlling the list", () => {
    pcState({ youtube_tabs: [RESULTS] });
    renderRemote();
    expect(screen.getByText("No YouTube video is playing: the YouTube tab shows a list. Pick a video below or on the PC.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next video" })).toBeDisabled();
  });

  it("a search the PC could not confirm (consent page, slow load) points at the browser on the PC, not the network", async () => {
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "outcome_unknown", at: TS, duration_ms: 15000, error: { code: "OUTCOME_UNKNOWN", message: "YouTube was asked to search, but the results page did not report back in time; check the PC", retryable: false } }));
    expect(await screen.findByText("YouTube did not finish loading the results on the PC, so DoMe can't tell what it shows.")).toBeInTheDocument();
    expect(screen.getByText(/if YouTube asks you to accept cookies or to sign in, do that once there/)).toBeInTheDocument();
    expect(screen.queryByText(/connection was lost/)).toBeNull();
  });

  it("results whose tab was closed on the PC cannot be opened", async () => {
    renderRemote();
    await search(QUERY);
    await waitFor(() => expect(sentCommands("youtube.search")).toHaveLength(1));
    act(() => socket.receive({ type: "result", command_id: sentCommands("youtube.search")[0]!.command_id, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab: RESULTS, results: [{ index: 1, video_id: "vidA", title: "A" }] } }));
    pcState({ youtube_tabs: [PLAYING] });
    expect(await screen.findByText(/The results tab was closed on the PC/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /1\. A/ })).toBeDisabled();
  });
});

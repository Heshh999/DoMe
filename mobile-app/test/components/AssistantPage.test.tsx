/**
 * The Ask page over the real runtime and a fake relay socket, with fake speech recognition and synthesis: the talk
 * button's states (the one-time voice notice first; getting ready until the microphone is live; listening), the live
 * transcript outside the live region and a live region silent while listening, a low-risk plan running at once and a
 * lock waiting for Run, a shutdown proposed by the AI going to the PC's own confirmation modal (only a tap approves;
 * a spoken "yes" does nothing) and the large Cancel button after it, AI bubble tags and the allowance chip, the limit
 * banners, model text rendered as text, and the dictation fallback.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { rest } from "@dome/protocol";

import { GlobalConfirmation } from "../../src/app/AppShell.tsx";
import { getAssistant, setVoiceForTests } from "../../src/app/voice.ts";
import { configureApi } from "../../src/lib/api.ts";
import type { RecognitionHandlers, Recognizer, Speaker } from "../../src/lib/speech.ts";
import { AssistantPage } from "../../src/pages/app/AssistantPage.tsx";
import { DEFAULT_ASSISTANT_PREFS, useAssistantStore } from "../../src/store/assistant.ts";
import { useDevicesStore } from "../../src/store/devices.ts";
import type { YoutubeTab } from "../../src/store/live.ts";
import { CONTROLLER, FakeSocket, freshStorage, installDialogPolyfill, jsonResponse, makeRuntime, OFFICE_PC, OTHER_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

installDialogPolyfill();

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
const ALL_CAPS: rest.Grant["capabilities"] = ["status", "media", "volume", "apps", "lock", "power"];
const GRANT: rest.Grant = { id: "66666666-6666-4666-8666-666666666666", controller_id: CONTROLLER, pc_id: PC, capabilities: ALL_CAPS, created_at: TS };
const PLAYING: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, title: "Now playing", video_id: "playing1", paused: false, context: "watch", ad_showing: false, is_live: false, in_playlist: false };
const ALLOWANCE: rest.AssistantAllowance = { limit: 300, used: 5, period_start: "2026-10-01T00:00:00Z", period_end: "2026-11-01T00:00:00Z", exhausted: null };
const STATUS: rest.AssistantStatusResponse = { available: true, unavailable_reason: null, provider: "fake", turn_deadline_seconds: 20, consent_version: "2026-10-11", consent: { version: "2026-10-11", given_at: TS }, allowance: ALLOWANCE };

let h: RuntimeHarness;
let socket: FakeSocket;
let status: rest.AssistantStatusResponse;
let replies: Array<Partial<rest.AssistantTurnResponse>>;
let turns: rest.AssistantTurnRequest[];
/** Turn requests wait until aborted (DoMe is still thinking). */
let holdTurns: boolean;

function fakeRecognizer(isAvailable = true) {
  let handlers: RecognitionHandlers | null = null;
  let available = isAvailable;
  const r = {
    starts: 0,
    stops: 0,
    available: () => available,
    start(next: RecognitionHandlers) {
      handlers = next;
      r.starts++;
      next.onState("starting");
    },
    stop() {
      r.stops++;
    },
    abort() {},
    tryAgain() {
      available = true;
    },
    dispose() {},
    handlers: () => handlers!,
  } satisfies Recognizer & Record<string, unknown>;
  return r;
}

function fakeSpeaker() {
  const said: string[] = [];
  const sp: Speaker & { said: string[] } = {
    said,
    available: true,
    unlocked: false,
    unlock: vi.fn(),
    speak: (text: string) => void said.push(text),
    cancel: vi.fn(),
    setListening: vi.fn(),
    setPrefs: vi.fn(),
    voices: () => [],
    dispose: vi.fn(),
  };
  return sp;
}

interface SentCommand {
  command_id: string;
  action: string;
  params: Record<string, unknown>;
}

function sent(action?: string): SentCommand[] {
  return socket
    .frames()
    .filter((f) => f.type === "command")
    .map((f) => JSON.parse((f.envelope as { payload: string }).payload) as SentCommand)
    .filter((p) => action === undefined || p.action === action);
}

function answer(commandId: string, result: Record<string, unknown>) {
  act(() => socket.receive({ type: "result", command_id: commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 20, result }));
}

/** The PC's signed challenge for a power step this phone sent (the shared fixture, re-pointed at it). */
function challengeFor(rec: SentCommand, action = "power.shutdown"): string {
  return fixture.digests.challenge_text
    .replace("8f1c2d3e-4a5b-4c6d-8e7f-901234567890", rec.command_id)
    .replace('"action":"app.close"', `"action":"${action}"`)
    .replace('"params":{}', '"params":{"countdown_seconds":10}')
    .replace('"target":{"app_id":"notepad"}', '"target":null')
    .replace('"expires_at":"2026-10-08T12:01:00.000Z"', `"expires_at":"${new Date(Date.now() + 60_000).toISOString()}"`);
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/app/assistant"]}>
      <AssistantPage />
      <GlobalConfirmation />
    </MemoryRouter>,
  );
}

let recognizer: ReturnType<typeof fakeRecognizer>;
let speaker: ReturnType<typeof fakeSpeaker>;

beforeEach(async () => {
  freshStorage();
  signedIn();
  localStorage.clear();
  status = STATUS;
  replies = [];
  turns = [];
  holdTurns = false;
  useAssistantStore.setState({ prefs: { ...DEFAULT_ASSISTANT_PREFS, useAi: true, voiceNoticeSeen: true }, status: null, statusError: null, allowance: null, aiBlocked: null, items: [], recent: [], lastAppId: null, lastActivity: null, phase: "idle", notice: null, speaking: false });
  recognizer = fakeRecognizer();
  speaker = fakeSpeaker();
  setVoiceForTests({ recognizer, speaker, controller: null });
  h = makeRuntime();
  socket = await h.connect();
  configureApi({
    fetchImpl: async (url, init) => {
      if (url.endsWith("/v1/assistant")) return jsonResponse(200, status);
      if (url.endsWith("/v1/assistant/turn")) {
        const body = JSON.parse(init.body as string) as rest.AssistantTurnRequest;
        turns.push(body);
        if (holdTurns) return new Promise<Response>((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
        return jsonResponse(200, { turn_id: body.turn_id, mode: "answer", reply: "Very good.", choices: [], handoff: "none", steps: [], charged: true, allowance: { ...ALLOWANCE, used: 6 }, ...replies.shift() });
      }
      return jsonResponse(500, { error: { code: "INTERNAL", message: "unexpected", retryable: false } });
    },
  });
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC, controllers: [], grantsByPc: { [PC]: [GRANT] } });
  act(() => socket.receive({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS }));
  act(() => socket.receive({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, volume: { value: 40, muted: false }, browser_instances: [{ browser_instance_id: "bi_test0001", browser: "edge" }], youtube_tabs: [PLAYING] } }));
});

afterEach(() => {
  h.teardown();
  setVoiceForTests({ recognizer: null, speaker: null, controller: null });
});

const talk = () => screen.getByRole("button", { name: /Tap to talk|Getting ready|Listening|Dictate|Tap to interrupt/ });
const liveRegion = () => screen.getByTestId("assistant-live");
const conversation = () => within(screen.getByRole("list", { name: "Conversation" }));
/** The AI status has loaded (the allowance chip shows). */
const aiReady = () => screen.findByText(/\d+ left/);

async function speakWords(words: string) {
  await userEvent.click(talk());
  const hs = recognizer.handlers();
  act(() => hs.onState("listening"));
  act(() => hs.onState("idle"));
  await act(async () => hs.onFinal(words, Date.now()));
}

describe("talking", () => {
  it("the first tap shows the voice notice; Continue starts listening; getting ready → listening → the words run a low-risk plan", async () => {
    useAssistantStore.getState().setPrefs({ voiceNoticeSeen: false });
    renderPage();
    await userEvent.click(talk());
    expect(recognizer.starts).toBe(0);
    const sheet = screen.getByRole("dialog", { name: "Talk to DoMe" });
    expect(sheet).toHaveTextContent("Apple may process them on its servers");
    expect(sheet).toHaveTextContent("DoMe never records or keeps audio");
    await userEvent.click(within(sheet).getByRole("button", { name: "Continue" }));
    expect(recognizer.starts).toBe(1);
    expect(useAssistantStore.getState().prefs.voiceNoticeSeen).toBe(true);
    expect(talk()).toHaveAccessibleName("Getting ready…");
    const hs = recognizer.handlers();
    act(() => hs.onState("listening"));
    expect(talk()).toHaveAccessibleName("Listening… tap to stop");
    expect(talk()).toHaveAttribute("aria-pressed", "true");
    act(() => hs.onInterim("pause you"));
    const interim = screen.getByText("pause you");
    expect(interim).toHaveClass("italic");
    expect(liveRegion()).toBeEmptyDOMElement();
    expect(liveRegion().contains(interim)).toBe(false);
    // tap again: stop and deliver
    await userEvent.click(talk());
    expect(recognizer.stops).toBe(1);
    act(() => hs.onState("idle"));
    await act(async () => hs.onFinal("pause youtube", Date.now()));
    await waitFor(() => expect(sent("youtube.set_paused")).toHaveLength(1));
    answer(sent("youtube.set_paused")[0]!.command_id, { tab: { ...PLAYING, paused: true } });
    await waitFor(() => expect(speaker.said.at(-1)).toBe("Very good. YouTube is paused."));
    expect(turns).toHaveLength(0); // handled without AI
    expect(conversation().getByText("Very good. YouTube is paused.").closest("li")).not.toHaveTextContent("Asked Claude");
    expect(liveRegion()).toHaveTextContent("Very good. YouTube is paused.");
  });

  it("the voice notice is shown only once", async () => {
    renderPage();
    await userEvent.click(talk());
    expect(screen.queryByRole("dialog", { name: "Talk to DoMe" })).toBeNull();
    expect(recognizer.starts).toBe(1);
  });

  it("a lock waits for Run: the card shows the step, nothing is sent until the tap", async () => {
    renderPage();
    await speakWords("lock my computer");
    const card = await screen.findByRole("list", { name: "Steps" });
    expect(card).toHaveTextContent("Lock Windows");
    expect(speaker.said.at(-1)).toBe("Ready to lock the PC. Tap Run.");
    expect(sent("windows.lock")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(sent("windows.lock")).toHaveLength(1));
  });

  it("Cancel on a waiting card leaves it", async () => {
    renderPage();
    await speakWords("lock my computer");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    expect(screen.getByText("Not run.")).toBeInTheDocument();
    expect(sent("windows.lock")).toHaveLength(0);
  });
});

describe("the AI assistant", () => {
  it("a shutdown it proposes goes to the PC's confirmation; a spoken yes does nothing; only the tap approves; then Cancel shut-down", async () => {
    replies.push({ mode: "act", steps: [{ action: "power.shutdown", params: {} }], charged: true });
    renderPage();
    await aiReady();
    await speakWords("I'm off to bed, please power everything down");
    await waitFor(() => expect(sent("power.shutdown")).toHaveLength(1));
    const rec = sent("power.shutdown")[0]!;
    expect(rec.params).toEqual({ countdown_seconds: 10 });
    act(() => socket.receive({ type: "confirmation_required", command_id: rec.command_id, challenge_text: challengeFor(rec) }));
    const modal = await screen.findByTestId("confirm-primary");
    expect(modal).toHaveTextContent("Shut down the PC");
    await waitFor(() => expect(speaker.said).toContain("Please confirm on the screen."));
    // a spoken "yes" (the PC's own speakers, a TV) never approves
    await act(async () => getAssistant().handleText("yes", "speech"));
    expect(socket.frames().filter((f) => f.type === "confirmation")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(socket.frames().filter((f) => f.type === "confirmation")).toHaveLength(1));
    answer(rec.command_id, { accepted: true, countdown_seconds: 10, fires_at: new Date(Date.now() + 10_000).toISOString() });
    await waitFor(() => expect(speaker.said.at(-1)).toBe("Very good. The PC will shut down in 10 seconds. Tap Cancel to stop it."));
    await userEvent.click(screen.getByRole("button", { name: "Cancel shut-down" }));
    await waitFor(() => expect(sent("power.cancel")).toHaveLength(1));
  });

  it("AI bubbles say whether they counted; the allowance chip follows the answers", async () => {
    replies.push({ mode: "answer", reply: "Good evening.", charged: true }, { mode: "answer", reply: "Nothing to count.", charged: false, allowance: { ...ALLOWANCE, used: 6 } });
    renderPage();
    expect(await screen.findByText("295 left")).toBeInTheDocument();
    await speakWords("good evening to you");
    expect(await conversation().findByText("Good evening.")).toBeInTheDocument();
    expect(conversation().getByText("Good evening.").closest("li")).toHaveTextContent("Asked Claude · counts as 1 request");
    expect(screen.getByText("294 left")).toBeInTheDocument();
    await speakWords("tell me nothing in particular");
    expect(await conversation().findByText("Nothing to count.")).toBeInTheDocument();
    expect(conversation().getByText("Nothing to count.").closest("li")).toHaveTextContent("Asked Claude · not counted");
    expect(screen.getByText(/AI on · requests DoMe can't work out go to Anthropic/)).toBeInTheDocument();
  });

  it("model text is plain text: markup in a reply or a choice is shown, never rendered", async () => {
    replies.push({ mode: "ask", reply: "Which <img src=x onerror=alert(1)> one?", choices: ["<img src=x onerror=alert(2)>", "Pause YouTube"], charged: true });
    const { container } = renderPage();
    await aiReady();
    await speakWords("do the thing");
    expect(await conversation().findByText("Which <img src=x onerror=alert(1)> one?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "<img src=x onerror=alert(2)>" })).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("a reply with a bidi override does not pass the contract: the phone answers by itself and shows none of it", async () => {
    replies.push({ mode: "answer", reply: "Evil\u202e reversed", charged: true });
    const { container } = renderPage();
    await aiReady();
    await speakWords("do the thing");
    expect(await conversation().findByText(/The AI assistant didn't answer/)).toBeInTheDocument();
    expect(container.textContent).not.toContain("\u202e");
    expect(container.textContent).not.toContain("reversed");
  });

  it("an AI choice chip is a new turn: understood on the phone when it can be", async () => {
    replies.push({ mode: "ask", reply: "YouTube or the music?", choices: ["Pause YouTube"], charged: true });
    renderPage();
    await aiReady();
    await speakWords("make it stop");
    await userEvent.click(await screen.findByRole("button", { name: "Pause YouTube" }));
    await waitFor(() => expect(sent("youtube.set_paused")).toHaveLength(1));
    expect(turns).toHaveLength(1);
  });

  it.each([
    ["the requests are used", { ...STATUS, available: false, unavailable_reason: "allowance_exhausted" as const, allowance: { ...ALLOWANCE, used: 300, exhausted: "requests" as const } }, /You've used this month's 300 AI requests\. Renews /],
    ["the cost cap", { ...STATUS, available: false, unavailable_reason: "allowance_exhausted" as const, allowance: { ...ALLOWANCE, exhausted: "cost_cap" as const } }, /needed more processing than your plan includes/],
    ["too many failures", { ...STATUS, available: false, unavailable_reason: "allowance_exhausted" as const, allowance: { ...ALLOWANCE, exhausted: "failures" as const } }, /Too many AI requests failed this month/],
    ["DoMe's spending limit", { ...STATUS, available: false, unavailable_reason: "spend_limit" as const }, /paused until next month because DoMe reached its AI spending limit/],
  ])("a limit reached (%s): a banner says which, the chip says Limit reached, voice without AI goes on", async (_name, st, text) => {
    status = st;
    renderPage();
    expect(await screen.findByText(text)).toBeInTheDocument();
    if (st.unavailable_reason === "allowance_exhausted") expect(screen.getByText("Limit reached")).toBeInTheDocument();
    expect(screen.getByText(/Voice without AI · only commands DoMe understands by itself/)).toBeInTheDocument();
    await speakWords("it's a little quiet in here");
    expect(turns).toHaveLength(0);
  });
});

/** "Shut down my PC" (understood on the phone), approved in the modal, accepted with a 10 s countdown. */
async function shutdownCounting(): Promise<SentCommand> {
  renderPage();
  await speakWords("shut down my pc");
  await waitFor(() => expect(sent("power.shutdown")).toHaveLength(1));
  const rec = sent("power.shutdown")[0]!;
  act(() => socket.receive({ type: "confirmation_required", command_id: rec.command_id, challenge_text: challengeFor(rec) }));
  await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
  await waitFor(() => expect(socket.frames().filter((f) => f.type === "confirmation")).toHaveLength(1));
  answer(rec.command_id, { accepted: true, countdown_seconds: 10, fires_at: new Date(Date.now() + 10_000).toISOString() });
  await screen.findByRole("button", { name: "Cancel shut-down" });
  return rec;
}

const pcState = (pending: { action: string; command_id: string; fires_at: string } | null) => ({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, volume: { value: 40, muted: false }, browser_instances: [], youtube_tabs: [], pending_power_action: pending } });

describe("Cancel shut-down on the card", () => {
  it("is signed and sent through the Assistant: 'Cancelling…', then the outcome, 'Cancelled.' aloud, and the button goes", async () => {
    const rec = await shutdownCounting();
    act(() => socket.receive(pcState({ action: "power.shutdown", command_id: rec.command_id, fires_at: new Date(Date.now() + 10_000).toISOString() })));
    await userEvent.click(screen.getByRole("button", { name: "Cancel shut-down" }));
    await waitFor(() => expect(sent("power.cancel")).toHaveLength(1));
    expect(screen.getByRole("button", { name: /Cancelling…/ })).toBeDisabled();
    answer(sent("power.cancel")[0]!.command_id, { canceled: true });
    await waitFor(() => expect(speaker.said.at(-1)).toBe("Cancelled."));
    expect(screen.queryByRole("button", { name: /Cancel shut-down|Cancelling/ })).toBeNull();
    // the PC's next state no longer names the countdown: still no button
    act(() => socket.receive(pcState(null)));
    expect(screen.queryByRole("button", { name: /Cancel shut-down/ })).toBeNull();
    expect(useAssistantStore.getState().recent.at(-1)).toMatchObject({ who: "butler", text: "Cancelled." });
  });

  it("nothing counting down any more: says so, and the button goes", async () => {
    await shutdownCounting();
    await userEvent.click(screen.getByRole("button", { name: "Cancel shut-down" }));
    await waitFor(() => expect(sent("power.cancel")).toHaveLength(1));
    answer(sent("power.cancel")[0]!.command_id, { canceled: false });
    await waitFor(() => expect(speaker.said.at(-1)).toBe("Nothing was counting down."));
    expect(screen.queryByRole("button", { name: /Cancel shut-down/ })).toBeNull();
  });

  it("could not be sent (relay down): the reason is shown and said, and the button stays to try again", async () => {
    await shutdownCounting();
    act(() => socket.serverClose(1006));
    await userEvent.click(screen.getByRole("button", { name: "Cancel shut-down" }));
    await waitFor(() => expect(speaker.said.at(-1)).toMatch(/^I'm afraid I couldn't cancel it: /));
    expect(sent("power.cancel")).toHaveLength(0);
    expect(screen.getByRole("alert")).not.toBeEmptyDOMElement();
    expect(screen.getByRole("button", { name: "Cancel shut-down" })).toBeEnabled();
  });

  it("cancelled elsewhere: once a PC state that named the countdown no longer does, the button goes", async () => {
    const rec = await shutdownCounting();
    act(() => socket.receive(pcState({ action: "power.shutdown", command_id: rec.command_id, fires_at: new Date(Date.now() + 10_000).toISOString() })));
    expect(screen.getByRole("button", { name: "Cancel shut-down" })).toBeInTheDocument();
    act(() => socket.receive(pcState(null)));
    expect(screen.queryByRole("button", { name: "Cancel shut-down" })).toBeNull();
  });
});

describe("screen readers and layout", () => {
  it("the live region reads a new line once and is not refilled with the old one when listening ends", async () => {
    renderPage();
    await speakWords("set the pc volume to 45");
    await waitFor(() => expect(sent("windows.set_volume")).toHaveLength(1));
    answer(sent("windows.set_volume")[0]!.command_id, { value: 45, muted: false });
    await waitFor(() => expect(liveRegion()).toHaveTextContent("Very good. The volume is at 45 per cent."));
    const seen: string[] = [];
    const observer = new MutationObserver(() => seen.push(liveRegion().textContent ?? ""));
    observer.observe(liveRegion(), { childList: true, characterData: true, subtree: true });
    // listen, hear nothing
    await userEvent.click(talk());
    const hs = recognizer.handlers();
    act(() => hs.onState("listening"));
    expect(liveRegion()).toBeEmptyDOMElement();
    act(() => hs.onState("idle"));
    act(() => hs.onProblem({ kind: "no_speech" }));
    await act(async () => undefined);
    observer.disconnect();
    expect(liveRegion()).toBeEmptyDOMElement();
    expect(seen.filter((t) => t !== "")).toEqual([]);
  });

  it("bubbles say who spoke; the footer links are full-size tap targets; passing notices are a status", async () => {
    useAssistantStore.getState().setPrefs({ butlerName: "Jenkins" });
    renderPage();
    await speakWords("set the pc volume to 45");
    await waitFor(() => expect(sent("windows.set_volume")).toHaveLength(1));
    answer(sent("windows.set_volume")[0]!.command_id, { value: 45, muted: false });
    await waitFor(() => expect(speaker.said.at(-1)).toBe("Very good. The volume is at 45 per cent."));
    expect(conversation().getByText("You said:", { exact: false })).toHaveClass("sr-only");
    expect(conversation().getByText("set the pc volume to 45", { exact: false }).textContent).toBe("You said: set the pc volume to 45");
    expect(conversation().getByText("Very good. The volume is at 45 per cent.", { exact: false }).textContent).toBe("Jenkins: Very good. The volume is at 45 per cent.");
    for (const name of [/AI on|Voice without AI/, /Type a command without AI/]) expect(screen.getByRole("link", { name })).toHaveClass("tap", "inline-flex");
    const status = screen.getByTestId("assistant-notice");
    expect(status).toHaveAttribute("role", "status");
    act(() => useAssistantStore.getState().setNotice("Connecting to Office PC…"));
    expect(status).toHaveTextContent("Connecting to Office PC…");
  });

  it("the space under the conversation follows the dock's measured height, so the newest card's buttons can scroll above it", async () => {
    let height = 121;
    const observers: Array<() => void> = [];
    const original = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(private readonly cb: () => void) {}
      observe() {
        observers.push(() => this.cb());
        this.cb();
      }
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({ height }) as DOMRect);
    try {
      renderPage();
      const spacer = screen.getByTestId("assistant-dock-spacer");
      expect(spacer.style.height).toContain("121px");
      // the text field opens: the dock grows, and so does the space
      height = 240;
      act(() => observers.forEach((fire) => fire()));
      expect(spacer.style.height).toContain("240px");
      expect(spacer.style.height).toContain("57px");
    } finally {
      rect.mockRestore();
      globalThis.ResizeObserver = original;
    }
  });
});

describe("switching PC while DoMe is thinking", () => {
  it("cancels the request in flight and drops its answer: nothing is sent to either PC, and the new conversation says why", async () => {
    holdTurns = true;
    renderPage();
    await aiReady();
    await speakWords("it's a little quiet in here");
    await waitFor(() => expect(turns).toHaveLength(1));
    expect(useAssistantStore.getState().phase).toBe("thinking");
    act(() => useDevicesStore.setState({ pcs: [OFFICE_PC, { ...OFFICE_PC, id: OTHER_PC, name: "Home PC" }], selectedPcId: OTHER_PC }));
    // the conversation was cleared for the new PC; the only line in it is why nothing happened
    await waitFor(() => expect(liveRegion()).toHaveTextContent("You switched PCs, so I've left that request. This request may still count towards your allowance."));
    expect(conversation().getAllByRole("listitem")).toHaveLength(1);
    expect(conversation().getByRole("listitem")).toHaveTextContent("You switched PCs, so I've left that request.");
    expect(useAssistantStore.getState().phase).toBe("idle");
    // (the page asks the newly selected PC for its approved apps, as on any visit)
    expect(sent().filter((c) => c.action !== "app.list")).toEqual([]);
    expect(useAssistantStore.getState().recent).toEqual([]);
    expect(speaker.said).not.toContain("Cancelled.");
  });
});

describe("dictation and typing", () => {
  it("without web speech recognition the button is Dictate: it focuses the field, and the words travel as speech", async () => {
    recognizer = fakeRecognizer(false);
    setVoiceForTests({ recognizer, controller: null });
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Dictate" }));
    const field = screen.getByRole("textbox", { name: "Ask DoMe" });
    expect(field).toHaveFocus();
    expect(screen.getByText(/No microphone key\? iPhone Settings → General → Keyboard → Enable Dictation/)).toBeInTheDocument();
    await userEvent.type(field, "how do I take a screenshot");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(turns).toHaveLength(1));
    expect(turns[0]).toMatchObject({ source: "speech", text: "how do I take a screenshot" });
    expect(recognizer.starts).toBe(0);
  });

  it("in Dictate the field is always there, so there is no Type instead / Hide keyboard toggle", () => {
    recognizer = fakeRecognizer(false);
    setVoiceForTests({ recognizer, controller: null });
    renderPage();
    expect(screen.getByRole("textbox", { name: "Ask DoMe" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Type instead|Hide keyboard/ })).toBeNull();
  });

  it("Type instead sends typed words as typed", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Type instead" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Ask DoMe" }), "how do I take a screenshot");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(turns).toHaveLength(1));
    expect(turns[0]!.source).toBe("typed");
  });

  it("service-not-allowed switches to Dictate with 'Try voice again'", async () => {
    renderPage();
    await userEvent.click(talk());
    act(() => recognizer.handlers().onProblem({ kind: "unavailable" }));
    act(() => recognizer.handlers().onState("idle"));
    expect(screen.getByText(/Voice input isn't available in this app on your iPhone/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dictate" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try voice again" }));
    expect(screen.getByRole("button", { name: "Tap to talk" })).toBeInTheDocument();
  });

  it("a denied microphone explains how to allow it", async () => {
    renderPage();
    await userEvent.click(talk());
    act(() => recognizer.handlers().onProblem({ kind: "denied" }));
    expect(screen.getByText("How to allow it")).toBeInTheDocument();
  });
});

describe("header", () => {
  it("the butler's name is the title; the speaker toggle mutes", async () => {
    useAssistantStore.getState().setPrefs({ butlerName: "Jenkins" });
    renderPage();
    expect(screen.getByRole("heading", { name: "Jenkins" })).toBeInTheDocument();
    const mute = screen.getByRole("button", { name: "Mute replies" });
    expect(mute).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(mute);
    expect(useAssistantStore.getState().prefs.muted).toBe(true);
    expect(mute).toHaveAttribute("aria-pressed", "true");
  });
});

describe("leaving the page", () => {
  it("stops listening", async () => {
    const abort = vi.spyOn(recognizer, "abort");
    const page = renderPage();
    await userEvent.click(talk());
    page.unmount();
    expect(abort).toHaveBeenCalled();
    expect(speaker.setListening).toHaveBeenLastCalledWith(false);
  });
});

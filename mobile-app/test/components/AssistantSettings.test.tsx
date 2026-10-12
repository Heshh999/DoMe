/**
 * Settings → Assistant (src/components/AssistantSettingsCard.tsx), the tab swap and the More entries: why the
 * assistant is unavailable, the allowance line, consent given with the pinned version and CSRF (and only once per
 * account: another phone only flips its own switch), withdrawal for every phone, outdated consent, the phone
 * preferences persisted and validated, the test voice, the voice picker order; the fourth tab is Type or Ask.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { rest } from "@dome/protocol";

import { AppShell } from "../../src/app/AppShell.tsx";
import { setVoiceForTests } from "../../src/app/voice.ts";
import { AssistantSettingsCard } from "../../src/components/AssistantSettingsCard.tsx";
import { VoiceNoticeSheet } from "../../src/components/VoiceNoticeSheet.tsx";
import { configureApi } from "../../src/lib/api.ts";
import type { Speaker, VoiceLike } from "../../src/lib/speech.ts";
import { MorePage } from "../../src/pages/app/MorePage.tsx";
import { DEFAULT_ASSISTANT_PREFS, PREFS_KEY, useAssistantStore } from "../../src/store/assistant.ts";
import { useDevicesStore } from "../../src/store/devices.ts";
import { freshStorage, installDialogPolyfill, jsonResponse, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

installDialogPolyfill();

const ALLOWANCE: rest.AssistantAllowance = { limit: 300, used: 5, period_start: "2026-10-01T00:00:00Z", period_end: "2026-11-01T00:00:00Z", exhausted: null };
const NO_CONSENT: rest.AssistantStatusResponse = { available: true, unavailable_reason: null, provider: "fake", turn_deadline_seconds: 20, consent_version: "2026-10-11", consent: null, allowance: ALLOWANCE };
const CONSENTED: rest.AssistantStatusResponse = { ...NO_CONSENT, consent: { version: "2026-10-11", given_at: TS } };
const V = (name: string, lang: string): VoiceLike => ({ name, lang, voiceURI: `uri:${name}` });

let status: rest.AssistantStatusResponse;
let calls: Array<{ method: string; url: string; body: unknown; csrf: string | undefined }>;
let speaker: Speaker & { said: string[]; gestures: boolean[]; unlocked: boolean };
let h: RuntimeHarness | undefined;

beforeEach(async () => {
  freshStorage();
  signedIn();
  localStorage.clear();
  useAssistantStore.setState({ prefs: { ...DEFAULT_ASSISTANT_PREFS }, status: null, statusError: null, allowance: null, aiBlocked: null, items: [] });
  status = NO_CONSENT;
  calls = [];
  const said: string[] = [];
  const gestures: boolean[] = [];
  speaker = {
    said,
    gestures,
    available: true,
    unlocked: false,
    unlock: vi.fn(),
    speak: (t: string, o?: { gesture?: boolean }) => {
      said.push(t);
      gestures.push(!!o?.gesture);
    },
    cancel: vi.fn(),
    setListening: vi.fn(),
    setPrefs: vi.fn(),
    voices: () => [V("Daniel (Enhanced)", "en-GB"), V("Kate", "en-GB"), V("Samantha", "en-US"), V("Zarvox", "en-US")],
    dispose: vi.fn(),
  };
  setVoiceForTests({ speaker, controller: null });
  configureApi({
    csrfToken: "C".repeat(40),
    fetchImpl: async (url, init) => {
      const method = init.method ?? "GET";
      calls.push({ method, url, body: init.body ? JSON.parse(init.body as string) : undefined, csrf: (init.headers as Record<string, string>)["X-DoMe-CSRF"] });
      if (url.endsWith("/v1/assistant") && method === "GET") return jsonResponse(200, status);
      if (url.endsWith("/v1/assistant/consent") && method === "POST") {
        status = CONSENTED;
        return jsonResponse(200, status);
      }
      if (url.endsWith("/v1/assistant/consent") && method === "DELETE") {
        status = NO_CONSENT;
        return new Response(null, { status: 204 });
      }
      return jsonResponse(500, { error: { code: "INTERNAL", message: "x", retryable: false } });
    },
  });
});

afterEach(() => {
  setVoiceForTests({ speaker: null, controller: null });
  h?.teardown();
  h = undefined;
});

function card() {
  return render(
    <MemoryRouter>
      <AssistantSettingsCard />
    </MemoryRouter>,
  );
}

describe("status and consent", () => {
  it.each([
    ["not_configured", "The AI assistant isn't switched on for this DoMe service."],
    ["not_in_plan", "AI requests aren't part of your plan."],
    ["spend_limit", "The AI assistant is paused until next month because DoMe reached its AI spending limit."],
  ] as const)("%s: says why, offers no consent where it cannot be given", async (reason, text) => {
    status = { ...NO_CONSENT, available: false, unavailable_reason: reason };
    card();
    expect(await screen.findByText(new RegExp(text.replace(/[.?]/g, "\\$&")))).toBeInTheDocument();
    if (reason !== "spend_limit") expect(screen.queryByRole("button", { name: "Turn on the AI assistant…" })).toBeNull();
  });

  it("allowance exhausted names the limit", async () => {
    status = { ...NO_CONSENT, available: false, unavailable_reason: "allowance_exhausted", allowance: { ...ALLOWANCE, used: 300, exhausted: "requests" } };
    card();
    expect(await screen.findByText(/You've used this month's 300 AI requests\. Renews/)).toBeInTheDocument();
  });

  it("Turn on: the consent text, then POST with the pinned version and CSRF; this phone uses AI and shows the Ask tab", async () => {
    card();
    expect(await screen.findByText(/5 of 300 used this month · renews/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Turn on the AI assistant…" }));
    const sheet = screen.getByRole("dialog", { name: "Turn on the AI assistant?" });
    expect(sheet).toHaveTextContent("Claude, an AI model made by Anthropic");
    expect(sheet).toHaveTextContent("Never sent: audio, your screen, the list of apps on your PC");
    expect(sheet).toHaveTextContent("300 requests a month");
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
    await userEvent.click(within(sheet).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(useAssistantStore.getState().prefs.useAi).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toBe("/v1/assistant/consent");
    expect(post.body).toEqual({ version: "2026-10-11", accept: true });
    expect(post.csrf).toBe("C".repeat(40));
    expect(useAssistantStore.getState().prefs.showAssistant).toBe(true);
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)!)).toMatchObject({ useAi: true, showAssistant: true });
  });

  it("Not now sends nothing and changes nothing", async () => {
    card();
    await userEvent.click(await screen.findByRole("button", { name: "Turn on the AI assistant…" }));
    await userEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
    expect(useAssistantStore.getState().prefs.useAi).toBe(false);
  });

  it("consent given on another phone: this phone's switch shows the text once and sends nothing", async () => {
    status = CONSENTED;
    card();
    const toggle = await screen.findByRole("switch", { name: "Use AI on this phone" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    await userEvent.click(within(screen.getByRole("dialog", { name: "Turn on the AI assistant?" })).getByRole("button", { name: "Turn on" }));
    expect(useAssistantStore.getState().prefs.useAi).toBe(true);
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
    // switching it off needs no sheet
    await userEvent.click(screen.getByRole("switch", { name: "Use AI on this phone" }));
    expect(useAssistantStore.getState().prefs.useAi).toBe(false);
  });

  it("Turn off withdraws for the account (DELETE with CSRF) and this phone stops using AI", async () => {
    status = CONSENTED;
    useAssistantStore.getState().setPrefs({ useAi: true });
    card();
    await userEvent.click(await screen.findByRole("button", { name: "Turn off the AI assistant" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")!.csrf).toBe("C".repeat(40));
    await waitFor(() => expect(useAssistantStore.getState().prefs.useAi).toBe(false));
    expect(await screen.findByRole("button", { name: "Turn on the AI assistant…" })).toBeInTheDocument();
  });

  it("an older consent asks again", async () => {
    status = { ...NO_CONSENT, consent: { version: "2026-01-01", given_at: TS } };
    card();
    expect(await screen.findByText("The terms changed; read and turn on again.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Turn on the AI assistant…" })).toBeInTheDocument();
  });
});

describe("phone preferences", () => {
  it("name and form of address are validated and saved", async () => {
    card();
    const name = screen.getByRole("textbox", { name: "Assistant's name" });
    await userEvent.clear(name);
    await userEvent.type(name, "Jenkins");
    await userEvent.tab();
    expect(useAssistantStore.getState().prefs.butlerName).toBe("Jenkins");
    await userEvent.clear(name);
    await userEvent.type(name, "Bad;Name1");
    await userEvent.tab();
    expect(useAssistantStore.getState().prefs.butlerName).toBe("Jenkins");
    expect(screen.getByText(/Letters, spaces, full stops, apostrophes and hyphens only, up to 20/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "How it addresses you" }), "madam");
    expect(useAssistantStore.getState().prefs.address).toBe("madam");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "How it addresses you" }), "custom");
    await userEvent.type(screen.getByRole("textbox", { name: "Form of address" }), "Captain");
    await userEvent.tab();
    expect(useAssistantStore.getState().prefs.address).toBe("Captain");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "How it addresses you" }), "");
    expect(useAssistantStore.getState().prefs.address).toBe("");
  });

  it("emptying a custom form of address clears it (None), and the address in use is shown", async () => {
    useAssistantStore.getState().setPrefs({ address: "Mr Smith" });
    card();
    const select = screen.getByRole("combobox", { name: "How it addresses you" });
    expect(select).toHaveValue("custom");
    expect(screen.getByTestId("address-in-use")).toHaveTextContent("In use: “Mr Smith”");
    await userEvent.clear(screen.getByRole("textbox", { name: "Form of address" }));
    await userEvent.tab();
    expect(useAssistantStore.getState().prefs.address).toBe("");
    expect(select).toHaveValue("");
    expect(screen.queryByRole("textbox", { name: "Form of address" })).toBeNull();
    expect(screen.getByTestId("address-in-use")).toHaveTextContent("In use: none");
  });

  it("switching to 'Something else…' with no valid text uses no address until one is typed (never the old preset)", async () => {
    useAssistantStore.getState().setPrefs({ address: "sir" });
    card();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "How it addresses you" }), "custom");
    expect(screen.getByRole("textbox", { name: "Form of address" })).toHaveValue("");
    expect(useAssistantStore.getState().prefs.address).toBe("");
    expect(screen.getByTestId("address-in-use")).toHaveTextContent("In use: none");
    await userEvent.type(screen.getByRole("textbox", { name: "Form of address" }), "Captain");
    await userEvent.tab();
    expect(useAssistantStore.getState().prefs.address).toBe("Captain");
    expect(screen.getByTestId("address-in-use")).toHaveTextContent("In use: “Captain”");
  });

  it("Ask before every action, Read replies aloud and the Ask tab are switches", async () => {
    card();
    await userEvent.click(screen.getByRole("switch", { name: "Ask before every action" }));
    await userEvent.click(screen.getByRole("switch", { name: "Read replies aloud" }));
    await userEvent.click(screen.getByRole("switch", { name: "Show the Assistant tab" }));
    expect(useAssistantStore.getState().prefs).toMatchObject({ confirmEveryAction: true, muted: true, showAssistant: true });
  });

  it("Test voice speaks the sample line; the picker lists British voices first and novelty voices last", async () => {
    card();
    await userEvent.click(screen.getByRole("button", { name: "Test voice" }));
    expect(speaker.said).toEqual(["Good evening. How may I help?"]);
    expect(speaker.gestures).toEqual([true]); // spoken inside the tap, not after waiting for the voice list
    const picker = screen.getByRole("combobox", { name: "Voice" });
    expect([...picker.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Automatic (a British voice)", "Daniel (Enhanced) (en-GB)", "Kate (en-GB)", "Samantha (en-US)", "Zarvox (en-US)"]);
    expect(picker.querySelector("optgroup")?.getAttribute("label")).toBe("Novelty voices");
    await userEvent.selectOptions(picker, "uri:Kate");
    expect(useAssistantStore.getState().prefs.voiceURI).toBe("uri:Kate");
  });
});

describe("navigation", () => {
  beforeEach(async () => {
    h = makeRuntime();
    await h.connect();
    useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
  });

  it("the fourth tab is Type, or Ask when the Assistant tab is on", () => {
    const first = render(
      <MemoryRouter initialEntries={["/app"]}>
        <AppShell />
      </MemoryRouter>,
    );
    const nav = () => within(screen.getByRole("navigation", { name: "Main" }));
    expect(nav().getByRole("link", { name: /Type/ })).toHaveAttribute("href", "/app/command");
    expect(nav().queryByRole("link", { name: /Ask/ })).toBeNull();
    first.unmount();
    useAssistantStore.getState().setPrefs({ showAssistant: true });
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <AppShell />
      </MemoryRouter>,
    );
    expect(nav().getByRole("link", { name: /Ask/ })).toHaveAttribute("href", "/app/assistant");
    expect(nav().queryByRole("link", { name: /Type/ })).toBeNull();
    // five tabs at most on a phone: Apps lives under More (and on Home)
    expect(nav().getAllByRole("link").map((l) => l.textContent)).toEqual(["Home", "Remote", "Touchpad", "Ask", "More"]);
  });

  it("taps that are not the talk button unlock speech, again on later taps until it has really started", async () => {
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <AppShell />
        <button type="button" data-talk-button="">talk</button>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "talk" }));
    expect(speaker.unlock).not.toHaveBeenCalled();
    const nav = within(screen.getByRole("navigation", { name: "Main" }));
    await userEvent.click(nav.getByRole("link", { name: /Remote/ }));
    expect(speaker.unlock).toHaveBeenCalled();
    // iOS did not let the first try through: the next tap tries again
    const tries = vi.mocked(speaker.unlock).mock.calls.length;
    await userEvent.click(nav.getByRole("link", { name: /Home/ }));
    expect(vi.mocked(speaker.unlock).mock.calls.length).toBeGreaterThan(tries);
    // once speech has started, taps leave it alone
    speaker.unlocked = true;
    const done = vi.mocked(speaker.unlock).mock.calls.length;
    await userEvent.click(nav.getByRole("link", { name: /Remote/ }));
    await userEvent.click(nav.getByRole("link", { name: /Home/ }));
    expect(vi.mocked(speaker.unlock).mock.calls.length).toBe(done);
  });

  it("the voice notice's Continue starts the microphone, so it is marked like the talk button (never the unlock tap)", () => {
    render(<VoiceNoticeSheet open onContinue={() => undefined} onDictate={() => undefined} onClose={() => undefined} />);
    expect(screen.getByRole("button", { name: "Continue" })).toHaveAttribute("data-talk-button");
    expect(screen.getByRole("button", { name: "Dictate instead" })).not.toHaveAttribute("data-talk-button");
  });

  it("More lists both the Assistant and Type a command", () => {
    render(
      <MemoryRouter>
        <MorePage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: /Assistant/ })).toHaveAttribute("href", "/app/assistant");
    expect(screen.getByRole("link", { name: /Type a command/ })).toHaveAttribute("href", "/app/command");
  });
});

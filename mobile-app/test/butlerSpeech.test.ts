/**
 * What the butler says (src/lib/butlerSpeech.ts): the §4.6 sentence table from real results, openers and the form of
 * address, failures and stops, the voice copy that replaces Type-page wording, `speakable`, and `recentLine` text that
 * never carries anything the PC reported. Spoken lines never contain a search query or a title (one exception: what is
 * playing, asked on its own).
 */
import { describe, expect, it } from "vitest";

import { ERRORS, registry } from "@dome/protocol";

import { actionPhrase, certainly, cleanText, CONNECTION_DROPPED, HELP_SCREEN, HELP_VOICE, KEYBOARD_VOICE, planSpeech, playerName, POINTER_VOICE, powerCancelSpeech, recentClarifyLine, recentOwnerNote, recentPlanLine, runPrompt, SCREEN_HAS_DETAILS, screenForMessage, speakable, stepSentence, UNKNOWN_VOICE, voiceForAiError, voiceForMessage, withAddress, type PlanOutcome, type SpeechOptions } from "../src/lib/butlerSpeech.ts";
import type { PlanRun, StepRun } from "../src/lib/commandPlan.ts";
import type { CommandRecord } from "../src/lib/commands.ts";
import { HELP_MESSAGE, KEYBOARD_HANDOFF, parseCommand, planFromAssistant, POINTER_HANDOFF, THAT_ONE_HANDOFF, type AssistantStepInput, type IntentContext, type PlanStep } from "../src/lib/intents.ts";

const TS = "2026-10-11T12:00:00.000Z";
const QUERY = "SECRETQUERY lofi";
const TITLE = "SECRETTITLE Rain";
const APPS = [{ app_id: "spotify", display_name: "Spotify", running: true, windows: [{ foreground: false }] }];
const CTX: IntentContext = { apps: APPS, lastSearch: { results: [{ index: 1, video_id: "v1", title: TITLE }] } };

function step(action: string, params: Record<string, unknown> = {}, app?: string): PlanStep {
  const r = planFromAssistant([{ action, params, ...(app ? { app } : {}) } as AssistantStepInput], CTX);
  if (r.kind !== "plan") throw new Error(JSON.stringify(r));
  return r.steps[0]!;
}

function rec(s: PlanStep, result: Record<string, unknown> | null, error?: { code: string; message: string }): CommandRecord {
  return {
    commandId: `c-${s.action}`,
    pcId: "pc",
    action: s.action,
    params: s.params,
    target: s.target,
    protocolVersion: "1.2",
    createdAt: 1,
    expiresAt: TS,
    state: error ? "failed" : "succeeded",
    ackAt: null,
    confirmation: null,
    terminal: { origin: "agent", state: error ? "failed" : "succeeded", at: TS, durationMs: 3, result, resultInvalid: false, error: error ? { ...error, retryable: false } : null, warning: null },
    noAnswer: false,
    source: "assistant",
  };
}

function outcome(steps: PlanStep[], statuses: StepRun["status"][], records: Array<CommandRecord | null>, extra: Partial<PlanRun> = {}): PlanOutcome {
  const failedAt = statuses.findIndex((x) => x === "failed");
  return { steps, records, run: { steps: statuses.map((status, i) => ({ status, commandId: records[i]?.commandId ?? null, failure: status === "failed" ? { code: records[i]?.terminal?.error?.code ?? null, message: records[i]?.terminal?.error?.message ?? "Not possible." } : null })), done: true, failedAt: failedAt < 0 ? null : failedAt, stopped: false, ...extra } };
}

const opts = (over: Partial<SpeechOptions> = {}): SpeechOptions => ({ persona: { name: "DoMe", address: "" }, appName: (s) => (s.appId === "spotify" ? "Spotify" : null), turn: 0, certaintySaid: false, failureText: (_code, message) => message, ...over });

describe("the sentence table (§4.6)", () => {
  const cases: Array<[string, PlanStep, Record<string, unknown>, string]> = [
    ["pause", step("youtube.set_paused", { paused: true }), { tab: { paused: true } }, "YouTube is paused."],
    ["play", step("youtube.set_paused", { paused: false }), { tab: { paused: false } }, "YouTube is playing."],
    ["next video", step("youtube.next"), { tab: {} }, "Next video."],
    ["previous video", step("youtube.previous"), { tab: {} }, "Previous video."],
    ["skip ahead", step("youtube.seek_relative", { seconds: 30 }), { tab: {} }, "Skipped ahead 30 seconds."],
    ["back", step("youtube.seek_relative", { seconds: -10 }), { tab: {} }, "Back 10 seconds."],
    ["start", step("youtube.seek_to", { position_seconds: 0 }), { tab: {} }, "Back to the start."],
    ["seek to", step("youtube.seek_to", { position_seconds: 95 }), { tab: {} }, "Now at 1:35."],
    ["mute yt", step("youtube.set_muted", { muted: true }), { tab: {} }, "YouTube is muted."],
    ["unmute yt", step("youtube.set_muted", { muted: false }), { tab: {} }, "YouTube's sound is back on."],
    ["yt volume (read back)", step("youtube.set_volume", { value: 50 }), { tab: { volume: 48 } }, "YouTube's volume is at 48 per cent."],
    ["theater on", step("youtube.set_theater", { enabled: true }), { tab: {} }, "Theater mode is on."],
    ["theater off", step("youtube.set_theater", { enabled: false }), { tab: {} }, "Theater mode is off."],
    ["fullscreen", step("youtube.request_fullscreen"), { tab: {} }, "The video is full screen."],
    ["search", step("youtube.search", { query: QUERY }), { tab: {}, results: [{}, {}, {}] }, "I've found 3 videos. Tap one, or tap the microphone and say 'play the first one'."],
    ["search none", step("youtube.search", { query: QUERY }), { tab: {}, results: [] }, "YouTube found nothing for that."],
    ["open result", step("youtube.open_result", { index: 1 }), { tab: {} }, "Playing result 1."],
    ["tabs", step("youtube.list_tabs"), { tabs: [{}, {}] }, "2 YouTube tabs are open."],
    ["media pause (plain label)", step("media.set_paused", { paused: true }), { session: { app_label: "Spotify.exe" } }, "Spotify is paused."],
    ["media play (AUMID)", step("media.set_paused", { paused: false }), { session: { app_label: "SpotifyAB.SpotifyMusic_zpdnekdrzrea0" } }, "The player is playing."],
    ["next track", step("media.next"), { session: {} }, "Next track."],
    ["previous track", step("media.previous"), { session: {} }, "Previous track."],
    ["sessions", step("media.get_sessions"), { sessions: [{}, {}, {}] }, "3 media players are open."],
    ["get volume", step("windows.get_volume"), { value: 40, muted: true }, "The volume is at 40 per cent, and muted."],
    ["set volume (read back)", step("windows.set_volume", { value: 45 }), { value: 44, muted: false }, "The volume is at 44 per cent."],
    ["mute pc", step("windows.set_muted", { muted: true }), { value: 40, muted: true }, "The PC is muted."],
    ["unmute pc", step("windows.set_muted", { muted: false }), { value: 40, muted: false }, "The PC's sound is back on."],
    ["lock", step("windows.lock"), { accepted: true }, "The PC is locked."],
    ["ping", step("system.ping"), { agent_time: TS, agent_version: "1", protocol_version: "1.2" }, "Your PC is connected."],
    ["status locked", step("system.get_status"), { session_locked: true }, "Your PC is online and locked."],
    ["launch", step("app.launch", {}, "Spotify"), { app_id: "spotify", launched: true, running: true }, "Spotify is open."],
    ["already open", step("app.launch", {}, "Spotify"), { app_id: "spotify", launched: false, running: true }, "Spotify was already open."],
    ["focus", step("app.focus", {}, "Spotify"), { app_id: "spotify", focused: true }, "Spotify is in front."],
    ["minimise", step("app.minimize", {}, "Spotify"), { app_id: "spotify", minimized: true }, "Spotify is minimised."],
    ["maximise", step("app.maximize", {}, "Spotify"), {}, "Spotify is maximised."],
    ["restore", step("app.restore", {}, "Spotify"), {}, "Spotify is back to its normal size."],
    ["full screen", step("app.set_fullscreen", { enabled: true }, "Spotify"), {}, "Spotify is full screen."],
    ["leave full screen", step("app.set_fullscreen", { enabled: false }, "Spotify"), {}, "Spotify has left full screen."],
    ["close", step("app.close", {}, "Spotify"), { app_id: "spotify", closed: true }, "Spotify is closed."],
    ["sleep in 10", step("power.sleep"), { accepted: true, countdown_seconds: 10 }, "The PC will sleep in 10 seconds. Tap Cancel to stop it."],
    ["shut down now", step("power.shutdown"), { accepted: true, countdown_seconds: 0 }, "The PC is shutting down now."],
    ["restart in 10", step("power.restart"), { accepted: true, countdown_seconds: 10 }, "The PC will restart in 10 seconds. Tap Cancel to stop it."],
    ["cancel", step("power.cancel"), { canceled: true }, "Cancelled."],
    ["nothing to cancel", step("power.cancel"), { canceled: false }, "Nothing was counting down."],
  ];
  it.each(cases)("%s", (_n, s, result, sentence) => {
    expect(stepSentence(s, rec(s, result), { appName: s.appId ? "Spotify" : null })).toBe(sentence);
  });

  it("app.list names up to five apps on the phone, and only a count in recent", () => {
    const s = step("app.list");
    const apps = ["A", "B", "C", "D", "E", "F", "G"].map((n) => ({ app_id: n.toLowerCase(), display_name: `App ${n}`, running: false, windows: [] }));
    expect(stepSentence(s, rec(s, { apps }))).toBe("You can open App A, App B, App C, App D, App E and 2 more.");
    expect(stepSentence(s, rec(s, { apps: apps.slice(0, 2) }))).toBe("You can open App A and App B.");
    expect(stepSentence(s, rec(s, { apps: [] }))).toBe("No apps are approved on the PC yet.");
    expect(stepSentence(s, rec(s, { apps }), { forRecent: true })).toBe("I listed 7 apps on the screen.");
  });

  it("what is playing names the title only when asked on its own, and never for recent", () => {
    const s = step("youtube.get_state");
    const r = rec(s, { tab: { paused: false, title: TITLE, position_seconds: 125 } });
    expect(stepSentence(s, r, { alone: true })).toBe(`Playing: ${TITLE}, at 2:05.`);
    expect(stepSentence(s, r)).toBe("YouTube is playing.");
    expect(stepSentence(s, r, { alone: true, forRecent: true })).toBe("YouTube is playing.");
  });

  it("a player label is spoken only when it is a plain word", () => {
    expect(playerName("Spotify.exe")).toBe("Spotify");
    expect(playerName("VLC media player")).toBe("VLC media player");
    expect(playerName("SpotifyAB.SpotifyMusic_zpdnekdrzrea0")).toBe("The player");
    expect(playerName("<script>")).toBe("The player");
    expect(playerName(undefined)).toBe("The player");
  });
});

describe("action phrases never read a string parameter", () => {
  it("every action has a phrase; none contains the query", () => {
    for (const spec of registry.actions().values()) {
      const phrase = actionPhrase({ action: spec.name, params: { query: QUERY, app_id: "secretapp", video_id: "SECRETVID", value: 5, seconds: 5, index: 2, enabled: true, paused: true, muted: true, position_seconds: 5 } });
      expect(phrase, spec.name).not.toMatch(/SECRET|secretapp/);
      expect(phrase.length).toBeGreaterThan(0);
    }
  });

  it("uses the app's display name on the phone and 'the app' otherwise", () => {
    const s = step("app.launch", {}, "Spotify");
    expect(actionPhrase(s, "Spotify")).toBe("open Spotify");
    expect(actionPhrase(s)).toBe("open the app");
  });

  it("the Run prompt is a statement and never reads the search text", () => {
    const search = step("youtube.search", { query: QUERY });
    expect(runPrompt([search], () => null)).toBe("Ready to search YouTube. Tap Run.");
    expect(runPrompt([search, step("windows.lock")], () => null)).toBe("Ready to do these 2 things. Tap Run.");
    expect(runPrompt([step("windows.lock")], () => null)).not.toContain("?");
  });
});

describe("whole plans", () => {
  it("openers rotate per turn; with an address: 'Very good, sir.'", () => {
    const s = step("youtube.set_paused", { paused: true });
    const o = outcome([s], ["succeeded"], [rec(s, { tab: { paused: true } })]);
    expect(planSpeech(o, opts({ turn: 0 }))).toBe("Very good. YouTube is paused.");
    expect(planSpeech(o, opts({ turn: 1 }))).toBe("Certainly. YouTube is paused.");
    expect(planSpeech(o, opts({ turn: 2 }))).toBe("Done. YouTube is paused.");
    expect(planSpeech(o, opts({ persona: { name: "DoMe", address: "sir" } }))).toBe("Very good, sir. YouTube is paused.");
    expect(planSpeech(o, opts({ persona: { name: "DoMe", address: "Captain Smith" } }))).toBe("Very good, Captain Smith. YouTube is paused.");
  });

  it("no opener once 'Certainly.' was said while it ran", () => {
    const s = step("youtube.next");
    expect(planSpeech(outcome([s], ["succeeded"], [rec(s, { tab: {} })]), opts({ certaintySaid: true }))).toBe("Next video.");
    expect(certainly({ name: "DoMe", address: "" })).toBe("Certainly.");
    expect(certainly({ name: "DoMe", address: "madam" })).toBe("Certainly, madam.");
  });

  it("status-only plans have no opener and put the address before the last full stop", () => {
    const s = step("windows.get_volume");
    const o = outcome([s], ["succeeded"], [rec(s, { value: 40, muted: false })]);
    expect(planSpeech(o, opts())).toBe("The volume is at 40 per cent.");
    expect(planSpeech(o, opts({ persona: { name: "DoMe", address: "madam" } }))).toBe("The volume is at 40 per cent, madam.");
  });

  it("a failure: what worked, then the failed step's phrase (never the query), then 'I stopped there.'", () => {
    const a = step("windows.set_volume", { value: 30 });
    const b = step("youtube.search", { query: QUERY });
    const c = step("youtube.open_result", { index: 1 });
    const o = outcome([a, b, c], ["succeeded", "failed", "not_sent"], [rec(a, { value: 30, muted: false }), rec(b, null, { code: "EXTENSION_DISCONNECTED", message: "x" }), null]);
    const said = planSpeech(o, opts({ failureText: (code) => ERRORS[code!]!.user_message }));
    expect(said).toBe(`The volume is at 30 per cent. I'm afraid I couldn't search YouTube: ${speakable(ERRORS.EXTENSION_DISCONNECTED!.user_message, 1)} I stopped there.`);
    expect(said).not.toContain("SECRETQUERY");
  });

  it("an app failure says the app's name on the phone, and the contract's message in recent", () => {
    const s = step("app.set_fullscreen", { enabled: true }, "Spotify");
    const o = outcome([s], ["failed"], [rec(s, null, { code: "FULLSCREEN_UNSUPPORTED", message: "PC SAYS SECRETPC" })]);
    expect(planSpeech(o, opts({ failureText: (_c, m) => m }))).toMatch(/^I'm afraid I couldn't make Spotify full screen: /);
    expect(recentPlanLine(o)).toBe(`I couldn't make the app full screen: ${ERRORS.FULLSCREEN_UNSUPPORTED!.user_message}`);
  });

  it("stopped, dropped, declined and expired have their fixed sentences", () => {
    const s = step("youtube.next");
    const t = step("windows.set_volume", { value: 5 });
    expect(planSpeech(outcome([s, t], ["succeeded", "not_sent"], [rec(s, { tab: {} }), null], { stopped: true }), opts())).toBe("Next video. Stopped.");
    expect(planSpeech(outcome([s], ["unknown"], [null], { failedAt: 0 }), opts())).toBe(CONNECTION_DROPPED);
    const p = step("power.shutdown");
    expect(planSpeech(outcome([p], ["failed"], [rec(p, null, { code: "CONFIRMATION_DECLINED", message: "" })]), opts())).toBe("Very well, I've left it.");
    expect(planSpeech(outcome([p], ["failed"], [rec(p, null, { code: "CONFIRMATION_EXPIRED", message: "" })]), opts())).toBe("The confirmation timed out, so nothing happened.");
  });
});

describe("voice copy", () => {
  it("replaces the Type page's handoffs and the unknown message", () => {
    expect(voiceForMessage(POINTER_HANDOFF)).toBe(POINTER_VOICE);
    expect(voiceForMessage(`${THAT_ONE_HANDOFF} Use the Touchpad to point and tap.`)).toBe(POINTER_VOICE);
    expect(voiceForMessage(KEYBOARD_HANDOFF)).toBe(KEYBOARD_VOICE);
    expect(voiceForMessage(null)).toBe(UNKNOWN_VOICE);
  });

  it("help is about asking, on screen and aloud", () => {
    expect(voiceForMessage(HELP_MESSAGE)).toBe(HELP_VOICE);
    expect(HELP_VOICE).toBe("Here are a few things you can ask me. You can join up to three with 'and' or 'then'.");
    expect(screenForMessage(HELP_MESSAGE)).toBe(HELP_SCREEN);
    expect(HELP_SCREEN).toMatch(/^Here are some things you can say\./);
    expect(screenForMessage("Which one?")).toBe("Which one?");
  });

  // words the owner said (a query, a step's text) are shown on screen, never read back; a question becomes a statement
  const APPS2 = [
    { app_id: "notepad", display_name: "Notepad", running: true, windows: [{ foreground: true }] },
    { app_id: "notes", display_name: "Notes", running: false, windows: [] },
  ];
  const TYPED: IntentContext = { apps: APPS2, pcVolume: 40, youtubeVolume: 70, browsers: ["edge"], playback: { youtube: { available: true, playing: true }, media: { available: false, playing: false, label: null } } };
  it.each([
    ["play SECRETQUERY beats", "I only play videos by searching YouTube. Tap the suggestion to search for that."],
    ["set the volume to 20 and set the volume to 30", `Two of those steps control the same thing. ${SCREEN_HAS_DETAILS}`],
    ["shut down my pc and mute the pc", "One of those steps needs your confirmation on the phone, so please ask for it on its own."],
    ["pause youtube and SECRETQUERY the lights", `I understood part of that, but not all of it. ${SCREEN_HAS_DETAILS}`],
    ["pause youtube, mute the pc, set the volume to 20 and lock my computer", "I can do up to three things at a time. Please ask for the rest separately."],
  ])("'%s': a fixed spoken line, without the owner's words", (words, spoken) => {
    const r = parseCommand(words, TYPED);
    expect(r.kind === "plan" ? null : r.message).not.toBeNull();
    const said = voiceForMessage(r.kind === "plan" ? null : (r.message ?? null));
    expect(said).toBe(spoken);
    expect(said).not.toMatch(/SECRETQUERY|“|”/);
    expect(said).not.toMatch(/\?$/);
  });

  it("'Did you mean …?' is said as a statement: one name, or several", () => {
    const one = parseCommand("open notpad", { ...TYPED, apps: [APPS2[0]!] });
    expect(one).toMatchObject({ kind: "clarify", message: "Did you mean Notepad?" });
    expect(voiceForMessage(one.kind === "clarify" ? one.message : null)).toBe("I think you meant Notepad. Tap it on the screen to go ahead.");
    expect(voiceForMessage("Did you mean Notepad or Notes?")).toBe("I think you meant Notepad or Notes. Tap the one you want on the screen.");
  });

  it("AI errors: one line per code, never 'say it another way'", () => {
    expect(voiceForAiError("AI_UNAVAILABLE")).toBe("The assistant didn't answer, so nothing was done. Please try again, or use the buttons.");
    expect(voiceForAiError("MALFORMED_MESSAGE")).toBe(voiceForAiError("AI_UNAVAILABLE"));
    expect(voiceForAiError("NETWORK")).toBe("I'm afraid I can't reach DoMe's service just now. Please try again in a moment.");
    expect(voiceForAiError("RATE_LIMITED")).toBe("Too many requests just now. Please try again in a moment.");
    expect(voiceForAiError("AI_BUSY")).toBe("I'm still answering your last request. Please try again in a moment.");
    expect(voiceForAiError("AI_ALLOWANCE_EXHAUSTED", { reason: "window", renews: "today at 2 pm" })).toBe("I'm afraid this window's AI requests are used up. They refresh today at 2 pm. I can still do the things I know by myself.");
    expect(voiceForAiError("AI_ALLOWANCE_EXHAUSTED", { reason: "week", renews: "Saturday at 2 pm" })).toBe("I'm afraid this week's AI requests are used up. They refresh Saturday at 2 pm. I can still do the things I know by myself.");
    expect(voiceForAiError("AI_ALLOWANCE_EXHAUSTED", { reason: "taste" })).toBe("I'm afraid your one-time taste of AI requests is used up, and it does not renew. I can still do the things I know by myself.");
    expect(voiceForAiError("AI_ALLOWANCE_EXHAUSTED", { reason: "cost_cap", renews: "tomorrow at 9 am" })).toBe("I'm afraid this week's AI requests needed more processing than your plan includes. It refreshes tomorrow at 9 am. I can still do the things I know by myself.");
    expect(voiceForAiError("AI_ALLOWANCE_EXHAUSTED", { reason: "failures", renews: "tomorrow at 9 am" })).toBe("I'm afraid the AI assistant is paused after too many failed requests this week. It starts again tomorrow at 9 am. I can still do the things I know by myself.");
    expect(voiceForAiError("AI_ALLOWANCE_EXHAUSTED")).toBe("I'm afraid this window's AI requests are used up. I can still do the things I know by myself.");
    for (const reason of ["taste", "window", "week", "cost_cap", "failures"]) expect(voiceForAiError("AI_ALLOWANCE_EXHAUSTED", { reason, renews: "today at 2 pm" })).not.toMatch(/month/i);
    expect(voiceForAiError("AI_SPEND_LIMIT_REACHED")).toMatch(/^I'm afraid the AI assistant is paused until next month\./);
    for (const code of ["AI_UNAVAILABLE", "NETWORK", "RATE_LIMITED", "AI_BUSY", "AI_ALLOWANCE_EXHAUSTED", "AI_SPEND_LIMIT_REACHED", "AI_NOT_AVAILABLE", "AI_CONSENT_REQUIRED"]) {
      expect(voiceForAiError(code)).not.toMatch(/didn't understand|another way/);
    }
  });

  it("the Cancel shut-down button's result", () => {
    expect(powerCancelSpeech({ ok: true, canceled: true })).toBe("Cancelled.");
    expect(powerCancelSpeech({ ok: true, canceled: false })).toBe("Nothing was counting down.");
    expect(powerCancelSpeech({ ok: false, why: "The PC isn't connected right now. More words here." })).toBe(`I'm afraid I couldn't cancel it: the PC isn't connected right now. ${SCREEN_HAS_DETAILS}`);
  });
});

describe("speakable", () => {
  it("drops links, markup and symbols", () => {
    const said = speakable("Open <b>https://x.test/a=1</b> & enjoy `code` {x} 😀");
    expect(said).not.toMatch(/[<>/&=`{}]|😀/u);
    expect(said.startsWith("Open b https:")).toBe(true);
  });

  it("speaks at most two sentences and points at the screen when it shortened", () => {
    expect(speakable("One. Two. Three.")).toBe(`One. Two. ${SCREEN_HAS_DETAILS}`);
    expect(speakable("One. Two.")).toBe("One. Two.");
    const long = `${"word ".repeat(80)}end.`;
    expect(speakable(long).endsWith(SCREEN_HAS_DETAILS)).toBe(true);
    expect(speakable(long).length).toBeLessThanOrEqual(220 + SCREEN_HAS_DETAILS.length + 2);
  });

  it("removes format characters and line separators", () => {
    expect(cleanText("a\u202eb\u200bc\u2028d")).toBe("abc d");
    expect(speakable("Hello\u202e there")).toBe("Hello there");
  });

  it("withAddress puts the address before the final full stop", () => {
    expect(withAddress("Very good.", "sir")).toBe("Very good, sir.");
    expect(withAddress("Very good.", "")).toBe("Very good.");
  });
});

describe("recent lines are closed templates", () => {
  it("a command DoMe handled itself is a note of what it did, never the words or an app name", () => {
    expect(recentOwnerNote([step("app.launch", {}, "Spotify"), step("youtube.search", { query: QUERY })])).toBe("(DoMe handled this itself: open the app, search YouTube)");
    expect(recentOwnerNote(null)).toBe("(DoMe answered this itself)");
  });

  it("clarification topics", () => {
    expect(recentClarifyLine("app")).toBe("I asked which app you meant.");
    expect(recentClarifyLine("player")).toBe("I asked whether you meant YouTube or the other player.");
    expect(recentClarifyLine("volume_scope")).toBe("I asked whether you meant the PC's volume or YouTube's.");
    expect(recentClarifyLine("volume_unknown")).toBe("I asked for a volume number.");
    expect(recentClarifyLine("safety")).toBe("I asked you to put it another way.");
    expect(recentClarifyLine(undefined)).toBe("I asked you to put it another way.");
  });

  it("result lines replace every PC-sourced part", () => {
    const launch = step("app.launch", {}, "Spotify");
    const media = step("media.set_paused", { paused: true });
    const search = step("youtube.search", { query: QUERY });
    const o = outcome([launch, media, search], ["succeeded", "succeeded", "succeeded"], [rec(launch, { app_id: "spotify", launched: true, running: true }), rec(media, { session: { app_label: "Spotify.exe" } }), rec(search, { tab: { title: TITLE }, results: [{ title: TITLE }] })]);
    expect(recentPlanLine(o)).toBe("The app is open. The player is paused. I've found 1 video.");
    expect(recentPlanLine(o)).not.toMatch(/Spotify|SECRET/);
  });
});

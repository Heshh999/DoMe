/**
 * From the assistant's steps to a plan (src/lib/intents.ts `planFromAssistant`, docs/design/assistant.md §4.4): the
 * same PlanSteps the typed grammar builds for the same request, app names resolved against the PC's approved apps on
 * the phone ("it" from the step before or the conversation's last app), every action checked again (AI-eligible,
 * params, fields the assistant may not set) and the same plan rules (`assemblePlan`). Anything DoMe will not run is a
 * clarification, never a partial plan. `commandText` gives every step words that parse back to it. The shared
 * fixture keeps the phone and cloud-api agreeing on which AI plans are valid.
 */
import { describe, expect, it } from "vitest";

import { registry, type JsonValue } from "@dome/protocol";

import fixture from "../../shared/protocol/fixtures/assistant-plan-cases.json";
import { assemblePlan, commandText, expressible, NOT_FROM_HERE, parseCommand, planFromAssistant, type AssistantStepInput, type CommandParse, type IntentContext, type PlanStep } from "../src/lib/intents.ts";

const APPS = [
  { app_id: "msedge", display_name: "msedge", running: true, windows: [{ foreground: false }] },
  { app_id: "notepad", display_name: "Notepad", running: true, windows: [{ foreground: true }] },
  { app_id: "notepadpp", display_name: "Notepad++", running: false, windows: [] },
  { app_id: "zstream", display_name: "ZStream", running: true, windows: [{ foreground: false }] },
  { app_id: "calculator", display_name: "Calculator", running: false, windows: [] },
];
const SEARCH = { results: [{ index: 1, video_id: "vidA", title: "Lofi Girl radio" }, { index: 2, video_id: "vidB", title: "Jazz" }] };
const YT_ONLY = { youtube: { available: true, playing: true }, media: { available: false, playing: false, label: null } };
const CTX: IntentContext = { apps: APPS, pcVolume: 40, youtubeVolume: 70, browsers: ["edge"], playback: YT_ONLY, lastSearch: SEARCH };

const s = (action: string, params: Record<string, JsonValue> = {}, app?: string): AssistantStepInput => ({ action, params, ...(app !== undefined ? { app } : {}) });

function planOf(r: CommandParse): PlanStep[] {
  if (r.kind !== "plan") throw new Error(`not a plan: ${JSON.stringify(r)}`);
  return r.steps;
}

/** A plan's steps without the words they came from (typed text and canonical text differ by design). */
const shape = (steps: PlanStep[]) => steps.map(({ text: _text, ...rest }) => rest);

describe("planFromAssistant builds the plan the typed words build", () => {
  const cases: Array<[string, AssistantStepInput[]]> = [
    ["open edge", [s("app.launch", {}, "edge")]],
    ["play lofi on youtube", [s("youtube.search", { query: "lofi" }), s("youtube.open_result", { index: 1 })]],
    ["set the volume to 40", [s("windows.set_volume", { value: 40 })]],
    ["pause youtube", [s("youtube.set_paused", { paused: true })]],
    ["maximise notepad", [s("app.maximize", {}, "notepad")]],
    ["shut down my pc", [s("power.shutdown")]],
    ["open calculator and maximise it", [s("app.launch", {}, "Calculator"), s("app.maximize", {}, "it")]],
    ["make the video full screen", [s("youtube.request_fullscreen")]],
    ["full screen youtube", [s("app.set_fullscreen", { enabled: true }, "YouTube")]],
    ["play the second video", [s("youtube.open_result", { index: 2 })]],
    ["skip ahead 30 seconds", [s("youtube.seek_relative", { seconds: 30 })]],
    ["close zstream", [s("app.close", {}, "ZStream")]],
    ["next track", [s("media.next")]],
  ];
  it.each(cases)("%s", (typed, steps) => {
    expect(shape(planOf(planFromAssistant(steps, CTX)))).toEqual(shape(planOf(parseCommand(typed, CTX))));
  });

  it("gives every step words that parse back to it", () => {
    const plan = planOf(planFromAssistant([s("app.launch", {}, "edge"), s("app.maximize", {}, "it")], CTX));
    expect(plan.map((p) => p.text)).toEqual(["open msedge", "maximise msedge"]);
  });

  it("keeps the YouTube-as-app substitution visible: the card shows the action that will really run", () => {
    const plan = planOf(planFromAssistant([s("app.set_fullscreen", { enabled: true }, "the video")], CTX));
    expect(plan.map((p) => p.action)).toEqual(["youtube.request_fullscreen"]);
  });
});

describe("app names are resolved on the phone", () => {
  it("an app that is not approved asks to add it (and names it, so the page re-reads the list once)", () => {
    const r = planFromAssistant([s("app.launch", {}, "Spotify")], CTX);
    expect(r).toMatchObject({ kind: "clarify", message: "“Spotify” is not an approved app on this PC. Add it on the PC: DoMe tray → Add an app…", missingApp: "Spotify", topic: "app" });
  });

  it.each(["music", "screen", "Calc!", "C:\\Windows\\cmd.exe", "notepad --evil"])("%s is never an app: a clarification, not unknown", (name) => {
    const r = planFromAssistant([s("app.launch", {}, name)], CTX);
    expect(r.kind).toBe("clarify");
    expect(r).toMatchObject({ missingApp: name, topic: "app" });
  });

  it("all apps: one app at a time", () => {
    expect(planFromAssistant([s("app.close", {}, "all apps")], CTX)).toMatchObject({ kind: "clarify", message: "DoMe acts on one app at a time. Which one?", topic: "app" });
  });

  it("an ambiguous or near name asks which one, as for typed words", () => {
    expect(planFromAssistant([s("app.focus", {}, "note")], CTX)).toMatchObject({ kind: "clarify", topic: "app" });
    expect(planFromAssistant([s("app.focus", {}, "zstreem")], CTX)).toMatchObject({ kind: "clarify", message: "Did you mean ZStream?", topic: "app" });
  });

  it("'it' is the app of the step before, else the conversation's last app, else a question", () => {
    expect(planFromAssistant([s("app.maximize", {}, "it")], CTX)).toMatchObject({ kind: "clarify", topic: "app" });
    const fromLast = planOf(planFromAssistant([s("app.maximize", {}, "it")], { ...CTX, lastAppId: "zstream" }));
    expect(fromLast[0]).toMatchObject({ action: "app.maximize", target: { app_id: "zstream" } });
    expect(planOf(planFromAssistant([s("app.minimize", {}, "the app")], { ...CTX, lastAppId: "notepad" }))[0]).toMatchObject({ target: { app_id: "notepad" } });
    // a last app that is no longer approved is not used
    expect(planFromAssistant([s("app.maximize", {}, "it")], { ...CTX, lastAppId: "gone" })).toMatchObject({ kind: "clarify", topic: "app" });
    // the step before wins over the conversation's last app
    const chained = planOf(planFromAssistant([s("app.launch", {}, "Calculator"), s("app.maximize", {}, "it")], { ...CTX, lastAppId: "zstream" }));
    expect(chained[1]).toMatchObject({ target: { app_id: "calculator" } });
  });

  it("the typed grammar uses the conversation's last app for 'it' only when the Assistant sets it", () => {
    expect(parseCommand("maximise it", CTX)).toMatchObject({ kind: "clarify" });
    expect(planOf(parseCommand("maximise it", { ...CTX, lastAppId: "zstream" }))[0]).toMatchObject({ action: "app.maximize", target: { app_id: "zstream" } });
  });

  it("the app list not loaded yet asks to wait", () => {
    expect(planFromAssistant([s("app.launch", {}, "Notepad")], { ...CTX, apps: null })).toMatchObject({ kind: "clarify", appsUnknown: true, topic: "app" });
  });

  it("app.launch gets its app_id from the phone's own match", () => {
    expect(planOf(planFromAssistant([s("app.launch", {}, "notepad")], CTX))[0]).toMatchObject({ action: "app.launch", params: { app_id: "notepad" }, target: null });
  });
});

describe("YouTube results and players", () => {
  it("open_result straight after a search uses that search's results tab", () => {
    const plan = planOf(planFromAssistant([s("youtube.search", { query: "rain sounds" }), s("youtube.open_result", { index: 2 })], CTX));
    expect(plan.map((p) => [p.action, p.needsTarget, p.fromPrevious])).toEqual([
      ["youtube.search", "youtube_browser", false],
      ["youtube.open_result", "search_results", true],
    ]);
    expect(plan[1]!.params).toEqual({ index: 2 });
  });

  it("open_result alone takes the video id from the last search on this phone", () => {
    expect(planOf(planFromAssistant([s("youtube.open_result", { index: 2 })], CTX))[0]).toMatchObject({ params: { index: 2, video_id: "vidB" }, needsTarget: "search_results" });
    expect(planFromAssistant([s("youtube.open_result", { index: 2 })], { ...CTX, lastSearch: null }).kind).toBe("clarify");
    expect(planFromAssistant([s("youtube.open_result", { index: 5 })], CTX)).toMatchObject({ kind: "clarify", message: "The last search found only 2 videos." });
  });

  it("a player step straight after a search asks to pick a result first, with a chip that parses to the whole command", () => {
    const r = planFromAssistant([s("youtube.search", { query: "lofi and then jazz" }), s("youtube.set_paused", { paused: true })], CTX);
    expect(r).toMatchObject({ kind: "clarify", topic: "safety" });
    const chip = r.kind === "clarify" ? r.choices[0]! : null;
    expect(chip?.text).toBe('search youtube for "lofi and then jazz" and play the first video and pause youtube');
    expect(planOf(parseCommand(chip!.text, CTX)).map((p) => [p.action, p.params])).toEqual([
      ["youtube.search", { query: "lofi and then jazz" }],
      ["youtube.open_result", { index: 1 }],
      ["youtube.set_paused", { paused: true }],
    ]);
  });

  it("a player step after an opened result acts on that result's tab", () => {
    const plan = planOf(planFromAssistant([s("youtube.search", { query: "rain" }), s("youtube.open_result", { index: 1 }), s("youtube.set_volume", { value: 30 })], CTX));
    expect(plan[2]).toMatchObject({ action: "youtube.set_volume", needsTarget: "youtube", fromPrevious: true });
  });

  it("the video playing in another tab is paused first, as a listed step DoMe added", () => {
    const plan = planOf(planFromAssistant([s("youtube.search", { query: "rain" }), s("youtube.open_result", { index: 1 })], { ...CTX, playingElsewhere: { afterSearch: { title: "Other" }, lastResults: null } }));
    expect(plan.map((p) => [p.action, p.added])).toEqual([
      ["youtube.set_paused", true],
      ["youtube.search", false],
      ["youtube.open_result", false],
    ]);
  });

  it("targets come from live state the way typed steps' do", () => {
    const plan = planOf(planFromAssistant([s("media.set_paused", { paused: true }), s("youtube.next"), s("windows.set_muted", { muted: true })], CTX));
    expect(plan.map((p) => p.needsTarget)).toEqual(["media", "youtube", null]);
  });
});

describe("plan rules and refusals", () => {
  it("a step the PC asks to confirm stays on its own", () => {
    expect(planFromAssistant([s("windows.set_volume", { value: 10 }), s("power.shutdown")], CTX)).toMatchObject({ kind: "clarify", message: "“shut down my pc” needs your confirmation on the phone, so send it on its own.", topic: "safety" });
    expect(planOf(planFromAssistant([s("power.sleep")], CTX))[0]).toMatchObject({ action: "power.sleep", params: { countdown_seconds: 10 }, confirmation: true });
  });

  it("two steps controlling the same thing are refused", () => {
    expect(planFromAssistant([s("youtube.set_paused", { paused: true }), s("youtube.set_paused", { paused: false })], CTX)).toMatchObject({ kind: "clarify", topic: "safety" });
  });

  it("four steps are refused", () => {
    expect(planFromAssistant([s("windows.set_volume", { value: 1 }), s("youtube.next"), s("media.next"), s("windows.lock")], CTX)).toMatchObject({ kind: "clarify", message: "DoMe runs up to 3 steps at a time. Send the rest as a separate command." });
  });

  const refused: Array<[string, AssistantStepInput[]]> = [
    ["no steps", []],
    ["an action the registry does not have", [s("system.run_shell", { command: "format c:" })]],
    ["a human-only action (pointer/keyboard)", [s("input.session_start")]],
    ["a human-only action after a valid one", [s("windows.set_volume", { value: 30 }), s("input.session_stop", { input_session_id: "A".repeat(22) })]],
    ["a parameter out of range", [s("windows.set_volume", { value: 150 })]],
    ["an extra parameter", [s("windows.lock", { confirmed: true })]],
    ["a wrong parameter type", [s("youtube.set_paused", { paused: "yes" })]],
    ["a countdown on a power action", [s("power.shutdown", { countdown_seconds: 0 })]],
    ["a video id on open_result", [s("youtube.open_result", { index: 1, video_id: "evil" })]],
    ["an app id instead of a spoken name", [s("app.launch", { app_id: "cmd" }, "cmd")]],
    ["an app on an action that takes none", [s("windows.lock", {}, "Notepad")]],
    ["no app on an app action", [s("app.close")]],
    ["a search with a control character", [s("youtube.search", { query: "a\u0000b" })]],
  ];
  it.each(refused)("%s: nothing DoMe would send", (_name, steps) => {
    const r = planFromAssistant(steps, CTX);
    expect(r).toMatchObject({ kind: "clarify", message: NOT_FROM_HERE });
  });

  it("assemblePlan is the parser's own plan tail: parseCommand still refuses the same structures", () => {
    expect(parseCommand("lock my computer and shut down my pc", CTX)).toMatchObject({ kind: "clarify", topic: "safety" });
    const a = planOf(planFromAssistant([s("windows.set_volume", { value: 20 })], CTX));
    expect(assemblePlan([...a, ...a], [], CTX)).toMatchObject({ kind: "clarify", topic: "safety" });
  });
});

describe("commandText", () => {
  /** One representative step per AI-eligible action. */
  const EXAMPLES: Record<string, AssistantStepInput> = {
    "app.close": s("app.close", {}, "ZStream"),
    "app.focus": s("app.focus", {}, "Notepad"),
    "app.launch": s("app.launch", {}, "Calculator"),
    "app.list": s("app.list"),
    "app.maximize": s("app.maximize", {}, "notepad"),
    "app.minimize": s("app.minimize", {}, "edge"),
    "app.restore": s("app.restore", {}, "zstream"),
    "app.set_fullscreen": s("app.set_fullscreen", { enabled: false }, "ZStream"),
    "media.get_sessions": s("media.get_sessions"),
    "media.next": s("media.next"),
    "media.previous": s("media.previous"),
    "media.set_paused": s("media.set_paused", { paused: false }),
    "power.cancel": s("power.cancel"),
    "power.restart": s("power.restart"),
    "power.shutdown": s("power.shutdown"),
    "power.sleep": s("power.sleep"),
    "system.get_status": s("system.get_status"),
    "system.ping": s("system.ping"),
    "windows.get_volume": s("windows.get_volume"),
    "windows.lock": s("windows.lock"),
    "windows.set_muted": s("windows.set_muted", { muted: true }),
    "windows.set_volume": s("windows.set_volume", { value: 35 }),
    "youtube.get_state": s("youtube.get_state"),
    "youtube.list_tabs": s("youtube.list_tabs"),
    "youtube.next": s("youtube.next"),
    "youtube.open_result": s("youtube.open_result", { index: 2 }),
    "youtube.previous": s("youtube.previous"),
    "youtube.request_fullscreen": s("youtube.request_fullscreen"),
    "youtube.search": s("youtube.search", { query: "Pop and lock, then dance please" }),
    "youtube.seek_relative": s("youtube.seek_relative", { seconds: -45 }),
    "youtube.seek_to": s("youtube.seek_to", { position_seconds: 3725 }),
    "youtube.set_muted": s("youtube.set_muted", { muted: false }),
    "youtube.set_paused": s("youtube.set_paused", { paused: false }),
    "youtube.set_theater": s("youtube.set_theater", { enabled: true }),
    "youtube.set_volume": s("youtube.set_volume", { value: 55 }),
  };
  /** The grammar has no words for these (nothing to type them with yet); no chip ever carries them. */
  const NO_WORDS: Record<string, string> = {
    "media.get_sessions": "no typed phrase lists Windows media players",
    "youtube.get_state": "no typed phrase reads the YouTube player",
    "youtube.list_tabs": "no typed phrase lists YouTube tabs",
  };

  it("has an example for every AI-eligible action", () => {
    const eligible = [...registry.actions().values()].filter((a) => a.aiEligible).map((a) => a.name).sort();
    expect(Object.keys(EXAMPLES).sort()).toEqual(eligible);
  });

  it.each(Object.entries(EXAMPLES))("%s parses back to the same action and params", (action, input) => {
    const step = planOf(planFromAssistant([input], CTX)).find((p) => !p.added)!;
    expect(step.action).toBe(action);
    expect(step.text).toBe(commandText(step, APPS));
    const back = parseCommand(step.text, CTX);
    if (action in NO_WORDS) {
      expect(expressible(action)).toBe(false);
      expect(back.kind === "plan" && back.steps.some((p) => p.action === action)).toBe(false);
      return;
    }
    expect(expressible(action)).toBe(true);
    const again = planOf(back).filter((p) => !p.added);
    expect(again.map((p) => [p.action, p.params])).toEqual([[step.action, step.params]]);
  });

  it("other positions and values read back too", () => {
    for (const input of [s("youtube.seek_to", { position_seconds: 0 }), s("youtube.seek_to", { position_seconds: 90 }), s("youtube.seek_relative", { seconds: 600 }), s("app.set_fullscreen", { enabled: true }, "Notepad"), s("youtube.set_paused", { paused: true }), s("windows.set_muted", { muted: false }), s("youtube.search", { query: 'say "hi"' })]) {
      const step = planOf(planFromAssistant([input], CTX))[0]!;
      expect(planOf(parseCommand(step.text, CTX)).map((p) => [p.action, p.params])).toEqual([[step.action, step.params]]);
    }
  });
});

describe("the shared AI plan fixture (cloud-api runs the same cases)", () => {
  const ctx: IntentContext = { apps: fixture.apps, lastSearch: null };
  it.each(fixture.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const r = planFromAssistant(c.steps as AssistantStepInput[], ctx);
    if (c.verdict === "valid") expect(r.kind, JSON.stringify(r)).toBe("plan");
    else expect(r.kind, JSON.stringify(r)).not.toBe("plan");
  });
});

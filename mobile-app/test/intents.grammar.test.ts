/**
 * The typed-command grammar as a table: phrase → the exact plan (actions, params, fixed targets, where the target
 * comes from). Includes the owner's own phrases verbatim ("full screen <app>", "make the volume 100%", "bring <app>
 * to the front", "search <something> on YouTube", "play the second video", "click on that movie"), the clarifications
 * with chips, and the honest "not yet" answers. Every chip must parse back into a plan.
 */
import { describe, expect, it } from "vitest";

import { appCommandText, exampleAppPhrase, parseCommand, type CommandParse, type IntentContext, type PlanStep } from "../src/lib/intents.ts";

const APPS = [
  { app_id: "msedge", display_name: "msedge", running: true, windows: [{ foreground: false }] },
  { app_id: "chrome", display_name: "Google Chrome", running: true, windows: [{ foreground: false }] },
  { app_id: "code", display_name: "Visual Studio Code", running: true, windows: [{ foreground: false }] },
  { app_id: "notepad", display_name: "Notepad", running: true, windows: [{ foreground: true }] },
  { app_id: "notepadpp", display_name: "Notepad++", running: false, windows: [] },
  { app_id: "vlc", display_name: "VLC media player", running: false, windows: [] },
  // a likely approved app whose name is also a media word ("play spotify" is the media player)
  { app_id: "spotify", display_name: "Spotify", running: true, windows: [{ foreground: false }] },
  { app_id: "zstream", display_name: "ZStream", running: true, windows: [{ foreground: false }] },
];
const SEARCH = {
  results: [
    { index: 1, video_id: "vidA", title: "Lofi Girl radio" },
    { index: 2, video_id: "vidB", title: "Jazz <b>live</b>" },
    { index: 3, video_id: "vidC", title: "Rain sounds" },
  ],
};
const YT_ONLY = { youtube: { available: true, playing: true }, media: { available: false, playing: false, label: null } };
const CTX: IntentContext = { apps: APPS, pcVolume: 40, youtubeVolume: 70, browsers: ["edge"], playback: YT_ONLY, lastSearch: SEARCH };

type Expected = [action: string, params: Record<string, unknown>, target?: Record<string, unknown> | null | string];

/** [action, params, fixed target | needsTarget source] per step, for compact comparison. */
function brief(steps: PlanStep[]): Expected[] {
  return steps.map((s) => [s.action, s.params, s.target ?? (s.needsTarget ? `${s.needsTarget}${s.fromPrevious ? ":previous" : ""}` : null)]);
}

function planOf(text: string, ctx: IntentContext = CTX): PlanStep[] {
  const r = parseCommand(text, ctx);
  if (r.kind !== "plan") throw new Error(`expected a plan for ${JSON.stringify(text)}, got ${JSON.stringify(r)}`);
  return r.steps;
}

const W = (app_id: string) => ({ app_id });

describe("owner's phrases (verbatim)", () => {
  it.each<[string, Expected[]]>([
    ["full screen Notepad", [["app.set_fullscreen", { enabled: true }, W("notepad")]]],
    ["make the volume 100%", [["windows.set_volume", { value: 100 }, null]]],
    ["bring Notepad to the front", [["app.focus", {}, W("notepad")]]],
    ["search lofi beats on YouTube", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    ["play the second video", [["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results"]]],
  ])("%s", (text, expected) => {
    expect(brief(planOf(text))).toEqual(expected);
  });

  it("click on that movie: on YouTube with results, asks which one with the results as chips", () => {
    const r = parseCommand("click on that movie", CTX);
    expect(r).toMatchObject({ kind: "clarify", message: "Which one? Tap a result, or say “play the first video”." });
    expect(r.kind === "clarify" && r.choices).toEqual([
      { label: "1. Lofi Girl radio", text: "play the first video" },
      { label: "2. Jazz <b>live</b>", text: "play the second video" },
      { label: "3. Rain sounds", text: "play the third video" },
    ]);
  });
  it("click on that movie: anywhere else, an honest handoff to the Touchpad (DoMe cannot see the screen)", () => {
    const r = parseCommand("click on that movie", { ...CTX, lastSearch: null, playback: { youtube: { available: false, playing: false }, media: { available: false, playing: false, label: null } } });
    expect(r).toEqual({ kind: "handoff", message: "DoMe can't see your screen, so it can't tell which one “that” is. Use the Touchpad to point and tap.", choices: [] });
    const onYoutube = parseCommand("play that one", { ...CTX, lastSearch: null });
    expect(onYoutube.kind).toBe("handoff");
    expect(onYoutube.kind === "handoff" && onYoutube.message).toMatch(/“search YouTube for …” and then “play the first video”/);
  });
});

describe("volume", () => {
  it.each<[string, Expected[]]>([
    ["Make the volume 100 percent", [["windows.set_volume", { value: 100 }, null]]],
    ["volume to max", [["windows.set_volume", { value: 100 }, null]]],
    ["set volume to max", [["windows.set_volume", { value: 100 }, null]]],
    ["set the pc volume at 40", [["windows.set_volume", { value: 40 }, null]]],
    ["Set my PC's volume to 35 per cent", [["windows.set_volume", { value: 35 }, null]]],
    ["volume 35 pct", [["windows.set_volume", { value: 35 }, null]]],
    ["50 percent volume", [["windows.set_volume", { value: 50 }, null]]],
    ["a hundred percent volume", [["windows.set_volume", { value: 100 }, null]]],
    ["seventy five percent volume", [["windows.set_volume", { value: 75 }, null]]],
    ["max volume", [["windows.set_volume", { value: 100 }, null]]],
    ["full volume", [["windows.set_volume", { value: 100 }, null]]],
    ["half volume", [["windows.set_volume", { value: 50 }, null]]],
    ["set the volume to half", [["windows.set_volume", { value: 50 }, null]]],
    ["volume to a quarter", [["windows.set_volume", { value: 25 }, null]]],
    ["set the volume to minimum", [["windows.set_volume", { value: 0 }, null]]],
    ["turn the volume all the way up", [["windows.set_volume", { value: 100 }, null]]],
    ["volume all the way down", [["windows.set_volume", { value: 0 }, null]]],
    ["max out the volume", [["windows.set_volume", { value: 100 }, null]]],
    ["set youtube volume to 30", [["youtube.set_volume", { value: 30 }, "youtube"]]],
    ["set the volume on youtube to 30", [["youtube.set_volume", { value: 30 }, "youtube"]]],
    // relative: PC at 40, YouTube at 70
    ["turn it up", [["windows.set_volume", { value: 50 }, null]]],
    ["turn it down", [["windows.set_volume", { value: 30 }, null]]],
    ["turn up the volume", [["windows.set_volume", { value: 50 }, null]]],
    ["turn the volume down", [["windows.set_volume", { value: 30 }, null]]],
    ["louder", [["windows.set_volume", { value: 50 }, null]]],
    ["make it louder", [["windows.set_volume", { value: 50 }, null]]],
    ["a bit louder", [["windows.set_volume", { value: 45 }, null]]],
    ["a little quieter", [["windows.set_volume", { value: 35 }, null]]],
    ["much quieter", [["windows.set_volume", { value: 20 }, null]]],
    ["turn it up a lot", [["windows.set_volume", { value: 60 }, null]]],
    ["lower the volume by 20", [["windows.set_volume", { value: 20 }, null]]],
    ["raise the volume by 15 percent", [["windows.set_volume", { value: 55 }, null]]],
    ["volume up by twenty", [["windows.set_volume", { value: 60 }, null]]],
    ["increase the volume", [["windows.set_volume", { value: 50 }, null]]],
    ["turn the youtube volume up a lot", [["youtube.set_volume", { value: 90 }, "youtube"]]],
    ["make youtube quieter", [["youtube.set_volume", { value: 60 }, "youtube"]]],
    ["turn up the volume on my pc", [["windows.set_volume", { value: 50 }, null]]],
    // mute, with a clear scope
    ["mute the pc", [["windows.set_muted", { muted: true }, null]]],
    ["unmute the computer", [["windows.set_muted", { muted: false }, null]]],
    ["mute everything", [["windows.set_muted", { muted: true }, null]]],
    ["silence the pc", [["windows.set_muted", { muted: true }, null]]],
    ["turn the sound off", [["windows.set_muted", { muted: true }, null]]],
    ["turn on the sound", [["windows.set_muted", { muted: false }, null]]],
    ["mute youtube", [["youtube.set_muted", { muted: true }, "youtube"]]],
    ["unmute the video", [["youtube.set_muted", { muted: false }, "youtube"]]],
  ])("%s", (text, expected) => {
    expect(brief(planOf(text))).toEqual(expected);
  });

  it("a relative change says what it is based on", () => {
    expect(planOf("a bit louder")[0]!.detail).toBe("Up 5 from 40%");
    expect(planOf("lower the volume by 20")[0]!.detail).toBe("Down 20 from 40%");
  });
  it("clamps to 0–100 and refuses without a known current level", () => {
    expect(planOf("much louder", { ...CTX, pcVolume: 95 })[0]!.params).toEqual({ value: 100 });
    expect(parseCommand("turn it up", { ...CTX, pcVolume: null })).toMatchObject({ kind: "clarify", message: "I don't know the current PC volume yet. Try “set the volume to 40”." });
  });
  it.each<[string, string]>([
    ["set volume to banana", "I didn't catch the volume level. Say a number from 0 to 100, for example “set the volume to 40”."],
    ["set volume to 101", "Volume must be between 0 and 100 percent."],
    ["volume -5", "Volume must be between 0 and 100 percent."],
    ["set volume", "To what level? Say a number from 0 to 100, for example “set the volume to 40”."],
    ["turn it up by a million", "How much? Say a number of percent, for example “turn it up by 20”."],
  ])("%s → clarify", (text, message) => {
    expect(parseCommand(text, CTX)).toMatchObject({ kind: "clarify", message });
  });
  it("one app's volume or mute is not something DoMe has: it says so and offers the PC or YouTube", () => {
    expect(parseCommand("set the spotify volume to 20", CTX)).toMatchObject({ kind: "clarify", choices: [{ text: "set the pc volume to 20" }, { text: "set youtube volume to 20" }] });
    expect(parseCommand("mute music", CTX)).toMatchObject({ kind: "clarify", choices: [{ text: "mute the pc" }, { text: "mute youtube" }] });
  });
});

describe("playback: YouTube or a media player, never a hidden fallback", () => {
  it.each<[string, Expected[]]>([
    ["pause youtube", [["youtube.set_paused", { paused: true }, "youtube"]]],
    ["play the video", [["youtube.set_paused", { paused: false }, "youtube"]]],
    ["resume youtube", [["youtube.set_paused", { paused: false }, "youtube"]]],
    ["pause the music", [["media.set_paused", { paused: true }, "media"]]],
    ["play spotify", [["media.set_paused", { paused: false }, "media"]]],
    ["next video", [["youtube.next", {}, "youtube"]]],
    ["skip this video", [["youtube.next", {}, "youtube"]]],
    ["next track", [["media.next", {}, "media"]]],
    ["previous song", [["media.previous", {}, "media"]]],
    ["pause", [["youtube.set_paused", { paused: true }, "youtube"]]],
    ["next", [["youtube.next", {}, "youtube"]]],
  ])("%s", (text, expected) => {
    expect(brief(planOf(text))).toEqual(expected);
  });

  const both = (youtubePlaying: boolean, mediaPlaying: boolean): IntentContext => ({ ...CTX, playback: { youtube: { available: true, playing: youtubePlaying }, media: { available: true, playing: mediaPlaying, label: "Spotify" } } });
  it("bare 'pause' with YouTube and Spotify both playing asks, with a chip for each", () => {
    const r = parseCommand("pause", both(true, true));
    expect(r).toEqual({ kind: "clarify", message: "YouTube and Spotify are both playing. Which one?", choices: [{ label: "Pause YouTube", text: "pause youtube" }, { label: "Pause Spotify", text: "pause the media player" }], link: null, topic: "player" });
    for (const c of r.kind === "clarify" ? r.choices : []) expect(parseCommand(c.text, both(true, true)).kind).toBe("plan");
  });
  it("whatever is playing pauses, whatever is paused resumes", () => {
    expect(brief(planOf("pause", both(false, true)))).toEqual([["media.set_paused", { paused: true }, "media"]]);
    expect(brief(planOf("pause", both(true, false)))).toEqual([["youtube.set_paused", { paused: true }, "youtube"]]);
    expect(brief(planOf("play", both(false, true)))).toEqual([["youtube.set_paused", { paused: false }, "youtube"]]);
    expect(brief(planOf("next", { ...CTX, playback: { youtube: { available: false, playing: false }, media: { available: true, playing: true, label: null } } }))).toEqual([["media.next", {}, "media"]]);
  });
});

describe("seek, theater, full screen, captions and speed", () => {
  it.each<[string, Expected[]]>([
    ["skip ahead 30 seconds", [["youtube.seek_relative", { seconds: 30 }, "youtube"]]],
    ["go back a minute", [["youtube.seek_relative", { seconds: -60 }, "youtube"]]],
    ["rewind 15 seconds", [["youtube.seek_relative", { seconds: -15 }, "youtube"]]],
    ["fast forward 2 minutes", [["youtube.seek_relative", { seconds: 120 }, "youtube"]]],
    ["skip ahead a minute and a half", [["youtube.seek_relative", { seconds: 90 }, "youtube"]]],
    ["jump ahead 30s", [["youtube.seek_relative", { seconds: 30 }, "youtube"]]],
    ["go to 1:30", [["youtube.seek_to", { position_seconds: 90 }, "youtube"]]],
    ["jump to 2 minutes", [["youtube.seek_to", { position_seconds: 120 }, "youtube"]]],
    ["go to minute 3", [["youtube.seek_to", { position_seconds: 180 }, "youtube"]]],
    ["start over", [["youtube.seek_to", { position_seconds: 0 }, "youtube"]]],
    ["restart the video", [["youtube.seek_to", { position_seconds: 0 }, "youtube"]]],
    ["go to the beginning", [["youtube.seek_to", { position_seconds: 0 }, "youtube"]]],
    ["theater mode on", [["youtube.set_theater", { enabled: true }, "youtube"]]],
    ["turn off theater mode", [["youtube.set_theater", { enabled: false }, "youtube"]]],
    ["exit theatre mode", [["youtube.set_theater", { enabled: false }, "youtube"]]],
    ["cinema mode", [["youtube.set_theater", { enabled: true }, "youtube"]]],
    ["full screen", [["youtube.request_fullscreen", {}, "youtube"]]],
    ["go fullscreen", [["youtube.request_fullscreen", {}, "youtube"]]],
    ["make the video full screen", [["youtube.request_fullscreen", {}, "youtube"]]],
    ["youtube full screen", [["youtube.request_fullscreen", {}, "youtube"]]],
  ])("%s", (text, expected) => {
    expect(brief(planOf(text))).toEqual(expected);
  });

  it("YouTube full screen is honest about the browser rule, and offers the browser's own full screen when Edge is approved", () => {
    const r = parseCommand("full screen", CTX);
    expect(r.kind === "plan" && r.steps[0]!.detail).toBe("Browsers usually allow this only after a click on the PC itself; theater mode always works.");
    expect(r.kind === "plan" && r.choices).toEqual([
      { label: "Make msedge full screen instead", text: "make msedge full screen" },
      { label: "Theater mode on", text: "theater mode on" },
    ]);
    expect(brief(planOf("make msedge full screen"))).toEqual([["app.set_fullscreen", { enabled: true }, W("msedge")]]);
    // no browser approved as an app: only theater mode is offered
    const noBrowser = parseCommand("full screen", { ...CTX, apps: APPS.filter((a) => a.app_id === "notepad") });
    expect(noBrowser.kind === "plan" && noBrowser.choices).toEqual([{ label: "Theater mode on", text: "theater mode on" }]);
  });
  it("leaving the video's own full screen is not possible yet: says so, offers the browser app and the Touchpad", () => {
    expect(parseCommand("exit full screen", CTX)).toMatchObject({ kind: "clarify", link: "touchpad", choices: [{ text: "exit full screen in msedge" }] });
  });
  it.each(["turn on captions", "captions off", "show subtitles", "cc on"])("%s → not supported yet (handoff)", (text) => {
    expect(parseCommand(text, CTX)).toEqual({ kind: "handoff", message: "DoMe can't turn captions on or off yet. Press C on the PC, or tap the CC button in the player with the Touchpad.", choices: [] });
  });
  it.each(["play at 1.5x", "1.5x speed", "set the playback speed to 2", "speed up the video", "normal speed", "slower"])("%s → not supported yet (handoff)", (text) => {
    expect(parseCommand(text, CTX)).toMatchObject({ kind: "handoff", message: "DoMe can't change the playback speed yet. Use the Touchpad: the player's gear button → Playback speed." });
  });
  it("seeking further than 10 minutes is refused", () => {
    expect(parseCommand("skip ahead 20 minutes", CTX)).toMatchObject({ kind: "clarify" });
  });
});

describe("YouTube search and results", () => {
  it.each<[string, Expected[]]>([
    ["search youtube for lofi beats", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    ["search on youtube for lofi beats", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    ["look up lofi beats on youtube", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    ["search for lofi beats on youtube", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    ["youtube lofi beats", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    ["youtube search lofi beats", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    ["search for lofi beats", [["youtube.search", { query: "lofi beats" }, "youtube_browser"]]],
    // the customer's capitalisation is kept, quotes are dropped, and a bare "and" inside a query never splits it
    ["Search YouTube for Tom and Jerry", [["youtube.search", { query: "Tom and Jerry" }, "youtube_browser"]]],
    ['search youtube for "pause and play"', [["youtube.search", { query: "pause and play" }, "youtube_browser"]]],
    // play/watch = search, then open the first result of that search
    ["play despacito on youtube", [["youtube.search", { query: "despacito" }, "youtube_browser"], ["youtube.open_result", { index: 1 }, "search_results:previous"]]],
    ["watch tom and jerry on YouTube", [["youtube.search", { query: "tom and jerry" }, "youtube_browser"], ["youtube.open_result", { index: 1 }, "search_results:previous"]]],
    // results of the last search
    ["open the 3rd result", [["youtube.open_result", { index: 3, video_id: "vidC" }, "search_results"]]],
    ["play the first one", [["youtube.open_result", { index: 1, video_id: "vidA" }, "search_results"]]],
    ["click on the first video", [["youtube.open_result", { index: 1, video_id: "vidA" }, "search_results"]]],
    ["play video 2", [["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results"]]],
    ["number 3", [["youtube.open_result", { index: 3, video_id: "vidC" }, "search_results"]]],
  ])("%s", (text, expected) => {
    expect(brief(planOf(text))).toEqual(expected);
  });

  it("a search then a result in one command: the result comes from that search", () => {
    expect(brief(planOf("search youtube for cats then play the first video"))).toEqual([
      ["youtube.search", { query: "cats" }, "youtube_browser"],
      ["youtube.open_result", { index: 1 }, "search_results:previous"],
    ]);
    expect(brief(planOf("search youtube for cats and play it"))).toEqual([
      ["youtube.search", { query: "cats" }, "youtube_browser"],
      ["youtube.open_result", { index: 1 }, "search_results:previous"],
    ]);
    // without a search before it, "play the second video" needs one
    expect(brief(planOf("search youtube for cats and play the second one", { ...CTX, lastSearch: null }))).toEqual([
      ["youtube.search", { query: "cats" }, "youtube_browser"],
      ["youtube.open_result", { index: 2 }, "search_results:previous"],
    ]);
  });
  it("the result's title is shown before sending (as text)", () => {
    expect(planOf("play the second video")[0]!.detail).toBe("“Jazz <b>live</b>”");
  });
  it.each<[string, IntentContext, string]>([
    ["play the second video", { ...CTX, lastSearch: null }, "Search YouTube first, for example “search YouTube for lofi beats”, then say “play the second video”."],
    ["play the fifth video", CTX, "The last search found only 3 videos."],
    ["play the 11th video", CTX, "DoMe can open search results 1 to 10."],
    ["play the first video", { ...CTX, lastSearch: { results: [] } }, "The last search found no videos. Search again with other words."],
    ["search youtube", CTX, "What should I search YouTube for? For example “search YouTube for lofi beats”."],
    ["search youtube for " + "x".repeat(101), CTX, "That search is too long. Searches from DoMe can be up to 100 characters."],
  ])("%s → clarify", (text, ctx, message) => {
    expect(parseCommand(text, ctx)).toMatchObject({ kind: "clarify", message });
  });
  it.each<[string, Expected[]]>([
    ["youtube next", [["youtube.next", {}, "youtube"]]],
    ["youtube back", [["youtube.previous", {}, "youtube"]]],
    ["youtube mute", [["youtube.set_muted", { muted: true }, "youtube"]]],
    ["youtube louder", [["youtube.set_volume", { value: 80 }, "youtube"]]],
    ["youtube pause", [["youtube.set_paused", { paused: true }, "youtube"]]],
  ])("one control word after 'youtube' is that control, never a search for the word: %s", (text, expected) => {
    expect(brief(planOf(text))).toEqual(expected);
  });
  it("'play X' without YouTube never searches by itself: it offers the search as a chip", () => {
    expect(parseCommand("play notepad", CTX)).toEqual({ kind: "clarify", message: "Play “notepad” on YouTube? DoMe only plays videos by searching YouTube.", choices: [{ label: "Play “notepad” on YouTube", text: "play notepad on youtube" }], link: null });
    expect(parseCommand("google cats", CTX)).toMatchObject({ kind: "clarify", message: "DoMe can only search YouTube.", choices: [{ text: "search youtube for cats" }] });
  });
});

describe("apps (fuzzy matching against the approved apps)", () => {
  const NO_SPOTIFY: IntentContext = { ...CTX, apps: APPS.filter((a) => a.app_id !== "spotify") };

  it.each(["open spotify", "launch spotify", "close spotify", "switch to spotify", "maximise spotify", "minimise spotify", "restore spotify", "full screen spotify"])("'%s' without Spotify approved: not an approved app (never unknown, so no AI turn)", (text) => {
    expect(parseCommand(text, NO_SPOTIFY)).toMatchObject({ kind: "clarify", message: "“spotify” is not an approved app on this PC. Add it on the PC: DoMe tray → Add an app…", missingApp: "spotify", topic: "app" });
  });

  it("without Spotify approved, 'play spotify' and 'pause the music' stay the media player; 'open music' is still not a name", () => {
    expect(brief(planOf("play spotify", NO_SPOTIFY))).toEqual([["media.set_paused", { paused: false }, "media"]]);
    expect(brief(planOf("pause the music", NO_SPOTIFY))).toEqual([["media.set_paused", { paused: true }, "media"]]);
    expect(parseCommand("open music", NO_SPOTIFY).kind).toBe("unknown");
  });

  it.each<[string, Expected[]]>([
    ["open edge", [["app.launch", { app_id: "msedge" }, null]]],
    ["open Microsoft Edge", [["app.launch", { app_id: "msedge" }, null]]],
    ["launch chrome", [["app.launch", { app_id: "chrome" }, null]]],
    ["start vs code", [["app.launch", { app_id: "code" }, null]]],
    ["run notepad", [["app.launch", { app_id: "notepad" }, null]]],
    ["open vlc", [["app.launch", { app_id: "vlc" }, null]]],
    ["switch to chrome", [["app.focus", {}, W("chrome")]]],
    ["focus on vs code", [["app.focus", {}, W("code")]]],
    ["show notepad", [["app.focus", {}, W("notepad")]]],
    ["go back to notepad", [["app.focus", {}, W("notepad")]]],
    ["bring up the chrome window", [["app.focus", {}, W("chrome")]]],
    ["minimise chrome", [["app.minimize", {}, W("chrome")]]],
    ["hide notepad", [["app.minimize", {}, W("notepad")]]],
    ["maximise notepad", [["app.maximize", {}, W("notepad")]]],
    ["enlarge chrome", [["app.maximize", {}, W("chrome")]]],
    ["make notepad bigger", [["app.maximize", {}, W("notepad")]]],
    ["make edge full screen", [["app.set_fullscreen", { enabled: true }, W("msedge")]]],
    ["chrome full screen", [["app.set_fullscreen", { enabled: true }, W("chrome")]]],
    ["exit full screen in chrome", [["app.set_fullscreen", { enabled: false }, W("chrome")]]],
    ["take edge out of full screen", [["app.set_fullscreen", { enabled: false }, W("msedge")]]],
    ["restore notepad", [["app.restore", {}, W("notepad")]]],
    ["make notepad normal", [["app.restore", {}, W("notepad")]]],
    ["unmaximise chrome", [["app.restore", {}, W("chrome")]]],
    ["close chrome", [["app.close", {}, W("chrome")]]],
    ["quit notepad", [["app.close", {}, W("notepad")]]],
    ["minimise youtube", [["app.minimize", {}, W("msedge")]]],
    ["open chrome full screen", [["app.launch", { app_id: "chrome" }, null], ["app.set_fullscreen", { enabled: true }, W("chrome")]]],
    ["open notepad and maximise it", [["app.launch", { app_id: "notepad" }, null], ["app.maximize", {}, W("notepad")]]],
    ["open edge and make it full screen", [["app.launch", { app_id: "msedge" }, null], ["app.set_fullscreen", { enabled: true }, W("msedge")]]],
  ])("%s", (text, expected) => {
    expect(brief(planOf(text))).toEqual(expected);
  });

  it("closing needs confirmation", () => {
    expect(planOf("close chrome")[0]!.confirmation).toBe(true);
  });
  it("two equally good matches ask, with chips that each parse to one app", () => {
    const r = parseCommand("open note", CTX);
    expect(r).toMatchObject({ kind: "clarify", message: "Which one: Notepad or Notepad++?", choices: [{ text: "open Notepad" }, { text: "open Notepad++" }] });
    expect((r.kind === "clarify" ? r.choices : []).map((c) => brief(planOf(c.text)))).toEqual([[["app.launch", { app_id: "notepad" }, null]], [["app.launch", { app_id: "notepadpp" }, null]]]);
  });
  it("a near-miss spelling only suggests (never an action)", () => {
    const r = parseCommand("maximise notpad", CTX);
    expect(r.kind).toBe("clarify");
    expect(r.kind === "clarify" && r.message).toMatch(/^Did you mean Notepad/);
  });
  it("an unknown app points at the tray's Add an app…", () => {
    expect(parseCommand("open powershell", CTX)).toEqual({ kind: "clarify", message: "“powershell” is not an approved app on this PC. Add it on the PC: DoMe tray → Add an app…", choices: [], link: null, missingApp: "powershell", topic: "app" });
  });
  it("'it' without an app before it asks, offering the running apps (the one in front first)", () => {
    const r = parseCommand("maximise it", CTX);
    expect(r).toMatchObject({ kind: "clarify", message: "Which app? Say “Maximize Notepad”, for example." });
    expect(r.kind === "clarify" && r.choices.map((c) => c.text)).toEqual(["maximize Notepad", "maximize msedge", "maximize Google Chrome", "maximize Visual Studio Code"]);
  });
  it("the 'which app' help names one of the customer's own approved apps, never a made-up one", () => {
    const zstream = { app_id: "zstream-app", display_name: "ZStream", running: true, windows: [{ foreground: false }] };
    const only = (apps: NonNullable<IntentContext["apps"]>): IntentContext => ({ ...CTX, apps });
    expect(parseCommand("maximize it", only([zstream]))).toMatchObject({ kind: "clarify", message: "Which app? Say “Maximize ZStream”, for example.", choices: [{ text: "maximize ZStream" }] });
    expect(parseCommand("full screen the app", only([zstream]))).toMatchObject({ kind: "clarify", message: "Which app? Say “Full screen ZStream”, for example." });
    // nothing running: no chips, but the example still names an approved app
    expect(parseCommand("maximize it", only([{ ...zstream, running: false, windows: [] }]))).toMatchObject({ kind: "clarify", message: "Which app? Say “Maximize ZStream”, for example.", choices: [] });
    // only while no app is approved does the example fall back to Notepad
    expect(parseCommand("maximize it", only([]))).toMatchObject({ kind: "clarify", message: "Which app? Say “Maximize Notepad”, for example." });
    expect(brief(planOf("full screen zstream", only([zstream])))).toEqual([["app.set_fullscreen", { enabled: true }, W("zstream-app")]]);
  });

  it("example phrases parse back to the app: a running one first, its id when the name can't be typed back", () => {
    const zstream = { app_id: "zstream-app", display_name: "ZStream", running: false, windows: [] };
    const edge = { app_id: "msedge", display_name: "Microsoft Edge", running: true, windows: [{ foreground: true }] };
    expect(exampleAppPhrase([zstream, edge])).toBe("Microsoft Edge");
    expect(exampleAppPhrase([zstream])).toBe("ZStream");
    expect(exampleAppPhrase([{ app_id: "player", display_name: "<b>Player</b>", running: true }])).toBe("player");
    expect(exampleAppPhrase(null)).toBe("Notepad");
    expect(appCommandText("maximize", zstream, [zstream, edge])).toBe("maximize ZStream");
    expect(brief(planOf(appCommandText("maximize", zstream, [zstream, edge]), { ...CTX, apps: [zstream, edge] }))).toEqual([["app.maximize", {}, W("zstream-app")]]);
  });

  it("a window command for an app that was not running warns and offers to open it first", () => {
    const r = parseCommand("maximise vlc", CTX);
    expect(r.kind === "plan" && r.steps[0]!.warning).toBe("VLC media player was not running when DoMe last checked. If it still isn't, the PC will say so.");
    expect(r.kind === "plan" && r.choices).toEqual([{ label: "Open VLC media player first", text: "open VLC media player and maximize it" }]);
    expect(brief(planOf("open VLC media player and maximise it"))).toEqual([["app.launch", { app_id: "vlc" }, null], ["app.maximize", {}, W("vlc")]]);
    // opened in the step before: no warning
    expect(planOf("open vlc and maximise it")[1]!.warning).toBeNull();
  });
  it("YouTube is not an app: opening it is a search, closing it would close the browser", () => {
    expect(parseCommand("open youtube", CTX)).toMatchObject({ kind: "clarify", choices: [{ text: "open msedge" }] });
    expect(parseCommand("close youtube", CTX)).toMatchObject({ kind: "clarify", message: "DoMe can't close a single tab. Closing the browser closes all of its tabs." });
  });
  it("until the PC's app list arrives, app commands wait for it", () => {
    expect(parseCommand("open notepad", { ...CTX, apps: null })).toMatchObject({ kind: "clarify", appsUnknown: true });
  });
});

describe("compound commands", () => {
  it("'it' binds to the previous step's app, and a YouTube step after a YouTube step uses that step's tab", () => {
    const steps = planOf("play the second video and make it full screen");
    expect(brief(steps)).toEqual([
      ["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results"],
      ["youtube.request_fullscreen", {}, "youtube:previous"],
    ]);
  });
  it("splits only where the next words are a command of their own", () => {
    expect(planOf("search youtube for tom and jerry")).toHaveLength(1);
    expect(planOf("youtube cats and dogs")).toHaveLength(1);
    expect(brief(planOf("set the volume to 50 and mute youtube"))).toEqual([
      ["windows.set_volume", { value: 50 }, null],
      ["youtube.set_muted", { muted: true }, "youtube"],
    ]);
    expect(brief(planOf("pause youtube, open notepad and then maximise it")).map((s) => s[0])).toEqual(["youtube.set_paused", "app.launch", "app.maximize"]);
  });
  it("a question about one part keeps the rest: its chips fill the whole command", () => {
    const r = parseCommand("open notepad and pause", { ...CTX, playback: { youtube: { available: true, playing: true }, media: { available: true, playing: true, label: "Spotify" } } });
    expect(r).toMatchObject({ kind: "clarify", choices: [{ text: "open notepad and pause youtube" }, { text: "open notepad and pause the media player" }] });
  });
  it("'play X on youtube' counts as two steps toward the limit of three", () => {
    expect(planOf("play lofi on youtube and make it full screen")).toHaveLength(3);
    expect(parseCommand("play lofi on youtube, make it full screen and mute the pc", CTX)).toMatchObject({ kind: "clarify", message: "DoMe runs up to 3 steps at a time. Send the rest as a separate command." });
  });
});

describe("chips and determinism", () => {
  const ASKING = ["pause", "mute", "open note", "maximise it", "full screen", "play notepad", "google cats", "click on that movie", "open youtube", "maximise vlc", "play the fifth video", "help"];
  it("every chip parses back into a plan (tapping one only fills the box; the customer still sends)", () => {
    const ctx = { ...CTX, playback: { youtube: { available: true, playing: true }, media: { available: true, playing: true, label: "Spotify" } } };
    for (const text of ASKING) {
      const r: CommandParse = parseCommand(text, ctx);
      const chips = r.kind === "unknown" ? [] : r.choices;
      expect(chips.length, text).toBeGreaterThan(0);
      for (const c of chips) expect(parseCommand(c.text, ctx).kind, `${text} → ${c.text}`).toBe("plan");
    }
  });
  it("the same text and context always give the same answer", () => {
    for (const text of [...ASKING, "make the volume 100%", "open notepad and maximise it"]) expect(parseCommand(text, CTX)).toEqual(parseCommand(text, CTX));
  });
  it("help lists examples", () => {
    expect(parseCommand("what can you do", CTX)).toMatchObject({ kind: "clarify", message: "Here are some things you can type. You can also join up to three with “and” or “then”." });
  });
  it.each(["type hello", "press enter", "scroll down", "double click", "move the mouse left"])("%s → handoff to the Touchpad (never typed or clicked)", (text) => {
    expect(parseCommand(text, CTX).kind).toBe("handoff");
  });
});

describe("review findings (owner phrases that went wrong)", () => {
  it.each<[string, Expected[]]>([
    // a bare "and" inside an open-ended query belongs to the query, even when the words after it are a command
    ["search youtube for pop and lock", [["youtube.search", { query: "pop and lock" }, "youtube_browser"]]],
    ["search pop and lock", [["youtube.search", { query: "pop and lock" }, "youtube_browser"]]],
    ["youtube pop and lock", [["youtube.search", { query: "pop and lock" }, "youtube_browser"]]],
    ["search youtube for push and pause", [["youtube.search", { query: "push and pause" }, "youtube_browser"]]],
    ["search youtube for rewind and replay", [["youtube.search", { query: "rewind and replay" }, "youtube_browser"]]],
    ["search youtube for love and next", [["youtube.search", { query: "love and next" }, "youtube_browser"]]],
    ["search youtube for rest and status", [["youtube.search", { query: "rest and status" }, "youtube_browser"]]],
    ["search youtube for pause and play", [["youtube.search", { query: "pause and play" }, "youtube_browser"]]],
    // ...unless what follows picks a result, or "then" starts the next step
    ["search youtube for cats and play the first one", [["youtube.search", { query: "cats" }, "youtube_browser"], ["youtube.open_result", { index: 1 }, "search_results:previous"]]],
    ["search for lofi and play the 2nd", [["youtube.search", { query: "lofi" }, "youtube_browser"], ["youtube.open_result", { index: 2 }, "search_results:previous"]]],
    ["search for cats, then lock", [["youtube.search", { query: "cats" }, "youtube_browser"], ["windows.lock", {}, null]]],
    // "then" with nothing that is a command after it stays in the query
    ["search youtube for tom then jerry", [["youtube.search", { query: "tom then jerry" }, "youtube_browser"]]],
    // the query is sent as typed: no rewrites, no trimmed "now"
    ["search youtube for don't stop me now", [["youtube.search", { query: "don't stop me now" }, "youtube_browser"]]],
    ["search youtube for here and now", [["youtube.search", { query: "here and now" }, "youtube_browser"]]],
    ["search youtube for love you for me", [["youtube.search", { query: "love you for me" }, "youtube_browser"]]],
    ["search youtube for right now", [["youtube.search", { query: "right now" }, "youtube_browser"]]],
    ["search youtube for thank you", [["youtube.search", { query: "thank you" }, "youtube_browser"]]],
    ["search youtube for 50% off", [["youtube.search", { query: "50% off" }, "youtube_browser"]]],
    ["search youtube for 100% pure", [["youtube.search", { query: "100% pure" }, "youtube_browser"]]],
    ["search youtube for pct exam", [["youtube.search", { query: "pct exam" }, "youtube_browser"]]],
    ["search youtube for my PC's fan", [["youtube.search", { query: "my PC's fan" }, "youtube_browser"]]],
    ["search youtube for the video's ending", [["youtube.search", { query: "the video's ending" }, "youtube_browser"]]],
    ["search youtube for lofi, thanks", [["youtube.search", { query: "lofi" }, "youtube_browser"]]],
    // an approved app named Spotify (an exact name) is that app; "play spotify" stays the media player
    ["open spotify", [["app.launch", { app_id: "spotify" }, null]]],
    ["Open Spotify", [["app.launch", { app_id: "spotify" }, null]]],
    ["launch spotify", [["app.launch", { app_id: "spotify" }, null]]],
    ["close spotify", [["app.close", {}, W("spotify")]]],
    ["maximise spotify", [["app.maximize", {}, W("spotify")]]],
    ["full screen spotify", [["app.set_fullscreen", { enabled: true }, W("spotify")]]],
    ["switch to spotify", [["app.focus", {}, W("spotify")]]],
    ["bring spotify to the front", [["app.focus", {}, W("spotify")]]],
    ["open zstream and spotify", [["app.launch", { app_id: "zstream" }, null], ["app.launch", { app_id: "spotify" }, null]]],
    ["minimise zstream and open spotify", [["app.minimize", {}, W("zstream")], ["app.launch", { app_id: "spotify" }, null]]],
    ["Open Spotify and maximise it", [["app.launch", { app_id: "spotify" }, null], ["app.maximize", {}, W("spotify")]]],
    ["play spotify", [["media.set_paused", { paused: false }, "media"]]],
    ["pause the music", [["media.set_paused", { paused: true }, "media"]]],
    // "the window", "the app" and "the video" are words, not app names
    ["open notepad and maximize the window", [["app.launch", { app_id: "notepad" }, null], ["app.maximize", {}, W("notepad")]]],
    ["open zstream and make the window full screen", [["app.launch", { app_id: "zstream" }, null], ["app.set_fullscreen", { enabled: true }, W("zstream")]]],
    ["minimize the video", [["app.minimize", {}, W("msedge")]]],
    // leaving an app's full screen, with or without "in"
    ["exit full screen zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["close full screen zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["quit full screen zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["end full screen zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["exit fullscreen zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["zstream exit full screen", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["turn off full screen zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["stop full screen zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    ["exit full screen in zstream", [["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    // after an app step, a bare window phrase is about that app (not YouTube)
    ["open zstream and full screen", [["app.launch", { app_id: "zstream" }, null], ["app.set_fullscreen", { enabled: true }, W("zstream")]]],
    ["open zstream and go full screen", [["app.launch", { app_id: "zstream" }, null], ["app.set_fullscreen", { enabled: true }, W("zstream")]]],
    ["open zstream then full screen", [["app.launch", { app_id: "zstream" }, null], ["app.set_fullscreen", { enabled: true }, W("zstream")]]],
    ["bring zstream to the front and go full screen", [["app.focus", {}, W("zstream")], ["app.set_fullscreen", { enabled: true }, W("zstream")]]],
    ["maximize zstream and full screen", [["app.maximize", {}, W("zstream")], ["app.set_fullscreen", { enabled: true }, W("zstream")]]],
    ["open zstream and maximise", [["app.launch", { app_id: "zstream" }, null], ["app.maximize", {}, W("zstream")]]],
    ["open zstream and exit full screen", [["app.launch", { app_id: "zstream" }, null], ["app.set_fullscreen", { enabled: false }, W("zstream")]]],
    // volume phrasings
    ["turn the volume up on my pc", [["windows.set_volume", { value: 50 }, null]]],
    ["turn the volume off", [["windows.set_muted", { muted: true }, null]]],
    ["turn the volume on", [["windows.set_muted", { muted: false }, null]]],
    ["put the volume on 50", [["windows.set_volume", { value: 50 }, null]]],
    ["set the volume on 30", [["windows.set_volume", { value: 30 }, null]]],
    ["increase volume by a lot", [["windows.set_volume", { value: 60 }, null]]],
    ["turn it up by a little bit", [["windows.set_volume", { value: 45 }, null]]],
    // result picks without a noun
    ["play the second", [["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results"]]],
    ["play the 3rd", [["youtube.open_result", { index: 3, video_id: "vidC" }, "search_results"]]],
    ["play the 2nd please", [["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results"]]],
    ["pick the 2nd", [["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results"]]],
    ["open the second", [["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results"]]],
    ["play the next one", [["youtube.next", {}, "youtube"]]],
    // the words after "close" are an app only when they name one
    ["close down zstream", [["app.close", {}, W("zstream")]]],
    ["open up zstream", [["app.launch", { app_id: "zstream" }, null]]],
    // non-ASCII app names
    ["open Météo", [["app.launch", { app_id: "meteo" }, null]]],
    ["Full screen Météo", [["app.set_fullscreen", { enabled: true }, W("meteo")]]],
    ["Bring Météo to the front", [["app.focus", {}, W("meteo")]]],
  ])("%s", (text, expected) => {
    const ctx = { ...CTX, apps: [...APPS, { app_id: "meteo", display_name: "Météo", running: true, windows: [{ foreground: false }] }] };
    expect(brief(planOf(text, ctx))).toEqual(expected);
  });

  it("'search for cats then mute' asks what to mute (with whole-command chips) instead of searching for 'cats then mute'", () => {
    expect(parseCommand("search for cats then mute", CTX)).toMatchObject({ kind: "clarify", message: "Mute YouTube or the whole PC?", choices: [{ text: "search for cats then mute the pc" }, { text: "search for cats then mute youtube" }] });
    expect(brief(planOf("search for cats then mute the pc"))).toEqual([["youtube.search", { query: "cats" }, "youtube_browser"], ["windows.set_muted", { muted: true }, null]]);
  });
  it("a player step right after a search asks to pick a result first (the search shows a list, not a player)", () => {
    for (const text of ["search lofi on youtube and make it full screen", "search lofi on youtube and theater mode", "search lofi on youtube and turn youtube up"]) {
      const r = parseCommand(text, CTX);
      expect(r, text).toMatchObject({ kind: "clarify", message: "Pick a result first: a search shows a list of videos, not a player. For example “play the first video and make it full screen”." });
    }
    const r = parseCommand("search lofi on youtube and make it full screen", CTX);
    const chip = r.kind === "clarify" ? r.choices[0]!.text : "";
    expect(chip).toBe("search lofi on youtube and play the first video and make it full screen");
    expect(brief(planOf(chip))).toEqual([["youtube.search", { query: "lofi" }, "youtube_browser"], ["youtube.open_result", { index: 1 }, "search_results:previous"], ["youtube.request_fullscreen", {}, "youtube:previous"]]);
    // a step that is not about the player still follows a search
    expect(brief(planOf("search lofi on youtube and mute the pc"))).toEqual([["youtube.search", { query: "lofi" }, "youtube_browser"], ["windows.set_muted", { muted: true }, null]]);
  });
  it("'the window' or 'the app' with no app before it asks which app; 'close the video' explains closing the browser", () => {
    expect(parseCommand("maximize the window", CTX)).toMatchObject({ kind: "clarify", message: "Which app? Say “Maximize Notepad”, for example." });
    expect(parseCommand("close the app", CTX)).toMatchObject({ kind: "clarify", message: "Which app? Say “Close Notepad”, for example." });
    expect(parseCommand("close the video", CTX)).toMatchObject({ kind: "clarify", message: "DoMe can't close a single tab. Closing the browser closes all of its tabs." });
    for (const text of ["maximize the window", "close the app", "minimise the window"]) expect(parseCommand(text, CTX), text).not.toHaveProperty("missingApp");
  });
  it("a position or a pronoun is never offered as a YouTube search", () => {
    expect(parseCommand("play the last one", CTX)).toMatchObject({ kind: "clarify", message: "Which one? Tap a result, or say “play the first video”.", choices: [{ text: "play the first video" }, { text: "play the second video" }, { text: "play the third video" }] });
    expect(parseCommand("play the second", { ...CTX, lastSearch: null })).toMatchObject({ kind: "clarify", message: "Search YouTube first, for example “search YouTube for lofi beats”, then say “play the second video”." });
    for (const text of ["play the last one", "play that one", "play the last video"]) {
      const r = parseCommand(text, CTX);
      expect(r.kind === "clarify" && r.choices.some((c) => / on youtube$/.test(c.text)), text).toBe(false);
    }
  });
  it("words after 'shut down' or 'close' that name no app are never reported as an unapproved app", () => {
    expect(parseCommand("shut down in 5 minutes", CTX)).toEqual({ kind: "clarify", message: "DoMe can't schedule a shutdown. Send “shut down my pc” when you're ready.", choices: [{ label: "Shut down my pc", text: "shut down my pc" }], link: null, topic: "safety" });
    expect(parseCommand("shut down the pc in an hour", CTX)).toMatchObject({ kind: "clarify", message: "DoMe can't schedule a shutdown. Send “shut down my pc” when you're ready." });
    expect(parseCommand("restart in 10 minutes", CTX)).toMatchObject({ kind: "clarify", message: "DoMe can't schedule a restart. Send “restart my pc” when you're ready." });
    const all = parseCommand("close all windows", CTX);
    expect(all).toMatchObject({ kind: "clarify", message: "DoMe acts on one app at a time. Which one?" });
    expect(all.kind === "clarify" && all.choices[0]).toEqual({ label: "Close Notepad", text: "close Notepad" });
    for (const text of ["shut down in 5 minutes", "shut down the pc in an hour", "close down zstream", "close all windows", "open zstream --flag"]) expect(parseCommand(text, CTX), text).not.toHaveProperty("missingApp");
  });
  it("a window command for an app running without a window says so and offers to open it first", () => {
    const apps = APPS.map((a) => (a.app_id === "msedge" ? { ...a, windows: [] } : a));
    const r = parseCommand("maximise edge", { ...CTX, apps });
    expect(r.kind === "plan" && r.steps[0]!.warning).toBe("msedge is running without a window on the PC. Open it first to show its window.");
    expect(r.kind === "plan" && r.choices).toEqual([{ label: "Open msedge first", text: "open msedge and maximize it" }]);
    expect(parseCommand("full screen edge", { ...CTX, apps })).toMatchObject({ kind: "plan", choices: [{ text: "open msedge and make it full screen" }] });
  });
});

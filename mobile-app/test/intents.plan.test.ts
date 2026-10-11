/**
 * Running typed-command plans (src/lib/commandPlan.ts): the parser's context from live state, the per-step preview
 * (permission, locked PC, target), targets chosen again right before each step, and the sequential runner — step 2
 * is only sent after step 1's terminal result says it succeeded, a failure stops the plan, and a search feeds its
 * results tab and video id into the following open_result. A video playing in another tab is paused (a step of its
 * own) before a result opens; a window step right after its app was started waits for the app's first window; Stop
 * and a dropped connection end the plan at once, saying the step on its way may still run.
 */
import { describe, expect, it } from "vitest";

import type { JsonValue } from "@dome/protocol";

import { intentContext, previewStep, resolveStep, resultsTabProblem, runPlan, stepFailure, WINDOW_RETRIES, type LiveView, type PlanRun, type PlanRunDeps } from "../src/lib/commandPlan.ts";
import type { CommandRecord, TerminalOutcome } from "../src/lib/commands.ts";
import { parseCommand, type IntentContext, type PlanStep } from "../src/lib/intents.ts";
import type { LastSearch, PcState, YoutubeTab } from "../src/store/live.ts";

const TS = "2026-10-08T12:00:00.000Z";
const PLAYING: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, title: "Now playing", video_id: "playing1", paused: false, volume: 30, context: "watch", ad_showing: false, is_live: false, in_playlist: false };
const RESULTS_TAB: YoutubeTab = { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD", script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false };
const STATE: PcState = { remote_enabled: true, session_locked: false, extension_connected: true, volume: { value: 40, muted: false }, browser_instances: [{ browser_instance_id: "bi_test0001", browser: "edge" }], youtube_tabs: [PLAYING, RESULTS_TAB] };
const LAST: LastSearch = { commandId: "c-search", createdAt: 1, stateSeq: 0, tab: RESULTS_TAB, results: [{ index: 1, video_id: "vidA", title: "A" }, { index: 2, video_id: "vidB", title: "B" }] };
const APPS = [{ app_id: "notepad", display_name: "Notepad", running: true, windows: [] }];

function steps(text: string, ctx: IntentContext): PlanStep[] {
  const r = parseCommand(text, ctx);
  if (r.kind !== "plan") throw new Error(`no plan for ${text}: ${JSON.stringify(r)}`);
  return r.steps;
}

/** The steps the customer typed (without the "pause the other video" step DoMe adds). */
const typed = (text: string, ctx: IntentContext) => steps(text, ctx).filter((s) => !s.added);
/** Nothing playing anywhere: plans have only the typed steps. */
const QUIET: PcState = { ...STATE, youtube_tabs: [{ ...PLAYING, paused: true }, RESULTS_TAB] };

function ctxFor(view: LiveView): IntentContext {
  return intentContext({ ...view, fresh: true, apps: APPS });
}

let seq = 0;
function record(action: string, terminal: Partial<TerminalOutcome> | null, extra: Partial<CommandRecord> = {}): CommandRecord {
  seq++;
  return {
    commandId: `cmd-${seq}`,
    pcId: "pc",
    action,
    params: {},
    target: null,
    protocolVersion: "1.0",
    createdAt: seq,
    expiresAt: TS,
    state: terminal ? (terminal.state ?? "succeeded") : "accepted",
    ackAt: null,
    confirmation: null,
    terminal: terminal ? { origin: "agent", state: "succeeded", at: TS, durationMs: 5, result: null, resultInvalid: false, error: null, warning: null, ...terminal } : null,
    noAnswer: false,
    source: "text",
    ...extra,
  };
}

describe("intentContext", () => {
  it("takes volumes, playback and browsers from live state; a browser's own media session is not a separate player", () => {
    const ctx = intentContext({
      state: { ...STATE, media_sessions: [{ session_id: "MSEdge", app_label: "MSEdge", status: "playing", controls: ["pause"] }, { session_id: "Spotify.exe", app_label: "Spotify.exe", status: "paused", controls: ["play"] }] },
      selectedTab: undefined,
      fresh: true,
      apps: APPS,
      lastSearch: LAST,
    });
    expect(ctx.pcVolume).toBe(40);
    expect(ctx.youtubeVolume).toBe(30);
    expect(ctx.playback).toEqual({ youtube: { available: true, playing: true }, media: { available: true, playing: false, label: "Spotify" } });
    expect(ctx.browsers).toEqual(["edge"]);
    expect(ctx.lastSearch?.results.map((r) => r.video_id)).toEqual(["vidA", "vidB"]);
    expect(ctx.playingElsewhere).toEqual({ afterSearch: { title: "Now playing" }, lastResults: { title: "Now playing" } });
    // a bare "pause" therefore goes to YouTube (playing) and not to the paused Spotify
    expect(steps("pause", ctx)[0]!.action).toBe("youtube.set_paused");
  });
  it("a stale state gives no PC volume (relative volume asks instead of guessing)", () => {
    expect(intentContext({ state: STATE, fresh: false, apps: null }).pcVolume).toBeNull();
  });
  it("a results page is never the implicit YouTube target, so a bare 'pause' reaches the video being watched", () => {
    const ctx = intentContext({ state: { ...STATE, youtube_tabs: [RESULTS_TAB, PLAYING] }, fresh: true, apps: APPS });
    expect(ctx.playback?.youtube).toEqual({ available: true, playing: true });
    expect(resolveStep(steps("pause", ctx)[0]!, null, { state: { ...STATE, youtube_tabs: [RESULTS_TAB, PLAYING] } })).toMatchObject({ ok: true, target: { tab_id: 1, expected_video_id: "playing1" } });
    // only a results page open: there is no player to pause
    expect(resolveStep(steps("pause youtube", ctx)[0]!, null, { state: { ...STATE, youtube_tabs: [RESULTS_TAB] } })).toEqual({ ok: false, message: "No YouTube video is open on the PC: the YouTube tab shows a list, not a player." });
  });
  it("what plays elsewhere depends on where the result opens: a search reusing the playing tab replaces it", () => {
    const picked = intentContext({ state: STATE, selectedTab: "bi_test0001:1:dGFiLXRva2VuLTAwMDAwMD", fresh: true, apps: APPS, lastSearch: LAST });
    expect(picked.playingElsewhere).toEqual({ afterSearch: null, lastResults: { title: "Now playing" } });
    expect(intentContext({ state: QUIET, fresh: true, apps: APPS, lastSearch: LAST }).playingElsewhere).toEqual({ afterSearch: null, lastResults: null });
  });
});

describe("previewStep", () => {
  const view: LiveView = { state: STATE, lastSearch: LAST };
  it("says where a search runs and which results tab a result opens in", () => {
    const fresh: LiveView = { state: { ...STATE, youtube_tabs: [PLAYING] }, lastSearch: null };
    const [search] = steps("search youtube for cats", ctxFor(fresh));
    expect(previewStep(search!, 0, fresh, null)).toEqual({ problem: null, where: "Opens a new tab in Edge" });
    // a later search runs again in the results tab (one list tab, not a new one per search)
    expect(previewStep(search!, 0, view, null)).toEqual({ problem: null, where: "In the results tab of the last search" });
    // a tab the customer picked is reused, and the preview says what it replaces
    expect(previewStep(search!, 0, { ...view, selectedTab: "bi_test0001:1:dGFiLXRva2VuLTAwMDAwMD" }, null)).toEqual({ problem: null, where: "In the tab you picked, replacing “Now playing”" });
    const [open] = typed("play the second video", ctxFor(view));
    expect(previewStep(open!, 0, view, null)).toEqual({ problem: null, where: "In the results tab" });
    const chained = typed("play cats on youtube", ctxFor(view));
    expect(previewStep(chained[1]!, 1, view, null)).toEqual({ problem: null, where: "In the results tab from step 1" });
  });
  it("flags a missing permission (when this phone's grant is known) and a locked PC", () => {
    const [vol] = steps("make the volume 100%", ctxFor(view));
    expect(previewStep(vol!, 0, view, ["status", "media"]).problem).toBe("This phone does not have the “Change PC volume” permission on this PC. The PC owner can allow it in the DoMe tray → Paired phones.");
    expect(previewStep(vol!, 0, view, ["volume"]).problem).toBeNull();
    const [open] = steps("open notepad", ctxFor(view));
    expect(previewStep(open!, 0, { state: { ...STATE, session_locked: true } }, null).problem).toBe("The PC is locked. Unlock Windows on the PC first.");
    const [pause] = steps("pause youtube", ctxFor(view));
    expect(previewStep(pause!, 0, { state: { ...STATE, session_locked: true, media_while_locked: false } }, null).problem).toMatch(/media controls while locked are off/);
    expect(previewStep(pause!, 0, { state: { ...STATE, session_locked: true, media_while_locked: true } }, null).problem).toBeNull();
  });
  it("explains a missing target", () => {
    const [pause] = steps("pause youtube", ctxFor(view));
    expect(previewStep(pause!, 0, { state: { ...STATE, extension_connected: false } }, null).problem).toBe("The DoMe browser extension is not connected on the PC.");
    expect(previewStep(pause!, 0, { state: { ...STATE, youtube_tabs: [] } }, null).problem).toBe("No YouTube tab is open on the PC.");
    const [open] = typed("play the second video", ctxFor(view));
    expect(previewStep(open!, 0, { state: { ...STATE, youtube_tabs: [PLAYING] }, lastSearch: LAST }, null).problem).toBe("The results tab was closed on the PC. Search again.");
    expect(previewStep(open!, 0, { state: { ...STATE, youtube_tabs: [PLAYING, { ...RESULTS_TAB, context: "watch" }] }, lastSearch: LAST }, null).problem).toBe("You opened a video from this list, so that tab plays it now. Search again to pick another one.");
  });
});

describe("resultsTabProblem", () => {
  const last = { ...LAST, stateSeq: 7 };
  it("a new results tab missing from a state report older than the search's answer is not yet listed, not closed", () => {
    const before = { ...STATE, youtube_tabs: [PLAYING] };
    expect(resultsTabProblem(before, last, 7)).toBeNull(); // the PC's next (debounced) report has not arrived yet
    expect(resultsTabProblem(before, last, 8)).toBe("The results tab was closed on the PC. Search again.");
    expect(resultsTabProblem(before, last)).toBe("The results tab was closed on the PC. Search again.");
    expect(resultsTabProblem(STATE, last, 8)).toBeNull();
    expect(resultsTabProblem(STATE, null, 8)).toMatch(/^Search YouTube first/);
  });
});

describe("pausing the video that plays in another tab before a result opens", () => {
  const view: LiveView = { state: STATE, lastSearch: LAST };
  it("'play the second video' while another tab plays: a listed, added pause step goes first", () => {
    const plan = steps("play the second video", ctxFor(view));
    expect(plan.map((s) => [s.action, s.params, s.needsTarget, s.added])).toEqual([
      ["youtube.set_paused", { paused: true }, "playing_elsewhere", true],
      ["youtube.open_result", { index: 2, video_id: "vidB" }, "search_results", false],
    ]);
    expect(plan[0]!.detail).toBe("“Now playing” is playing in another tab; paused first so two videos don't play at once");
    expect(resolveStep(plan[0]!, null, view, plan[1]!)).toEqual({ ok: true, params: { paused: true }, target: { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", expected_video_id: "playing1" } });
    // nothing else playing: no extra step
    expect(steps("play the second video", ctxFor({ state: QUIET, lastSearch: LAST })).map((s) => s.action)).toEqual(["youtube.open_result"]);
  });
  it("'play X on youtube' pauses before the search, keeps the search's results for the open, and does not count toward three steps", () => {
    const plan = steps("play lofi on youtube and make it full screen", ctxFor({ state: { ...STATE, youtube_tabs: [PLAYING] } }));
    expect(plan.map((s) => s.action)).toEqual(["youtube.set_paused", "youtube.search", "youtube.open_result", "youtube.request_fullscreen"]);
    expect(plan.map((s) => s.fromPrevious)).toEqual([false, false, true, true]);
    expect(previewStep(plan[2]!, 2, { state: STATE }, null).where).toBe("In the results tab from step 2");
  });
  it("a pause typed before the result is enough; no second one is added", () => {
    expect(steps("pause youtube and play the second video", ctxFor(view)).map((s) => s.needsTarget)).toEqual(["youtube", "search_results"]);
  });
  it("at send time the pause skips the tab the result plays in, and is skipped when nothing else plays any more", () => {
    const plan = steps("play the second video", ctxFor(view));
    expect(resolveStep(plan[0]!, null, { state: QUIET, lastSearch: LAST }, plan[1]!)).toEqual({ ok: "skip", message: "Nothing else was playing any more." });
    // the results tab itself started playing (opened by hand on the PC): it is where the result goes, never paused
    const resultsPlaying = { ...STATE, youtube_tabs: [{ ...PLAYING, paused: true }, { ...RESULTS_TAB, paused: false }] };
    expect(resolveStep(plan[0]!, null, { state: resultsPlaying, lastSearch: LAST }, plan[1]!)).toMatchObject({ ok: "skip" });
  });
});

describe("resolveStep", () => {
  const view: LiveView = { state: STATE, lastSearch: LAST };
  it("an open_result after a search in the same plan takes the search's results tab and the Nth result's video id", () => {
    const plan = typed("play cats on youtube", ctxFor(view));
    const search = record("youtube.search", { result: { tab: { ...RESULTS_TAB, tab_token: "bmV3LXRva2VuLTAwMDAwMD" }, results: [{ index: 1, video_id: "first1", title: "x" }] } });
    expect(resolveStep(plan[1]!, search, view)).toEqual({ ok: true, params: { index: 1, video_id: "first1" }, target: { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "bmV3LXRva2VuLTAwMDAwMD" } });
    expect(resolveStep(plan[1]!, record("youtube.search", { result: { tab: RESULTS_TAB, results: [] } }), view)).toEqual({ ok: false, message: "YouTube found no videos for that search." });
  });
  it("'play the second video' pairs the last search's tab with that same search's video", () => {
    const [open] = typed("play the second video", ctxFor(view));
    expect(resolveStep(open!, null, view)).toEqual({ ok: true, params: { index: 2, video_id: "vidB" }, target: { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD" } });
  });
  it("a YouTube step after a YouTube step acts on the tab that step reported", () => {
    const plan = typed("play the second video and make it full screen", ctxFor(view));
    const opened = record("youtube.open_result", { result: { tab: { ...RESULTS_TAB, context: "watch", video_id: "vidB" } } });
    expect(resolveStep(plan[1]!, opened, view)).toEqual({ ok: true, params: {}, target: { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD", expected_video_id: "vidB" } });
    expect(resolveStep(plan[1]!, record("youtube.open_result", { result: null }), view)).toMatchObject({ ok: false });
  });
  it("live targets are chosen from the view at that moment", () => {
    const [pause] = steps("pause youtube", ctxFor(view));
    expect(resolveStep(pause!, null, { state: { ...STATE, youtube_tabs: [PLAYING] } })).toMatchObject({ ok: true, target: { tab_id: 1, expected_video_id: "playing1" } });
    expect(resolveStep(pause!, null, { state: { ...STATE, youtube_tabs: [] } })).toEqual({ ok: false, message: "No YouTube tab is open on the PC." });
    const [search] = steps("search youtube for cats", ctxFor(view));
    expect(resolveStep(search!, null, { state: STATE })).toEqual({ ok: true, params: { query: "cats" }, target: { browser_instance_id: "bi_test0001" } });
    expect(resolveStep(search!, null, view)).toEqual({ ok: true, params: { query: "cats" }, target: { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "cmVzdWx0cy10b2tlbi0wMD" } });
  });
});

describe("stepFailure", () => {
  it("success is null; anything else says why", () => {
    expect(stepFailure(record("x", { state: "succeeded" }))).toBeNull();
    expect(stepFailure(record("x", { state: "failed", error: { code: "APP_NOT_RUNNING", message: "m", retryable: false } }))).toEqual({ code: "APP_NOT_RUNNING", message: "m" });
    expect(stepFailure(record("x", { state: "expired" }))).toMatchObject({ code: "COMMAND_EXPIRED" });
    expect(stepFailure(record("x", null, { noAnswer: true }))).toMatchObject({ code: "NO_ANSWER" });
    expect(stepFailure(null)).toMatchObject({ code: "NO_ANSWER" });
  });
});

/** A fake runtime: records sends, settles each command when the test says so. */
function fakeRuntime(view: LiveView = { state: STATE, lastSearch: LAST }) {
  const sent: Array<{ action: string; params: Record<string, JsonValue>; target: Record<string, JsonValue> | null; id: string }> = [];
  const waiting = new Map<string, (r: CommandRecord | null) => void>();
  const updates: PlanRun[] = [];
  const wakers = new Set<() => void>();
  const waits: number[] = [];
  let stop = false;
  let online = true;
  let failSend: Error | null = null;
  const deps: PlanRunDeps = {
    view: () => view,
    send: async (action: string, params: Record<string, JsonValue>, target: Record<string, JsonValue> | null) => {
      if (failSend) throw failSend;
      const rec = record(action, null, { params, target });
      sent.push({ action, params, target, id: rec.commandId });
      return rec;
    },
    settled: (id: string) => new Promise<CommandRecord | null>((resolve) => waiting.set(id, resolve)),
    describeError: (e: unknown) => ({ code: "PC_RECONNECTING", message: e instanceof Error ? e.message : "?" }),
    stopRequested: () => stop,
    update: (r: PlanRun) => updates.push(r),
    connected: () => online,
    onInterrupt: (wake) => {
      wakers.add(wake);
      return () => wakers.delete(wake);
    },
    wait: (ms) => {
      waits.push(ms);
      return Promise.resolve();
    },
  };
  const flush = () => new Promise((r) => setTimeout(r, 0));
  return {
    deps,
    sent,
    updates,
    waits,
    /** Stop pressed in the page: the flag, then a wake-up. */
    pressStop() {
      stop = true;
      for (const w of [...wakers]) w();
    },
    dropConnection() {
      online = false;
      for (const w of [...wakers]) w();
    },
    async settle(index: number, terminal: Partial<TerminalOutcome> | null, extra: Partial<CommandRecord> = {}) {
      await flush();
      const s = sent[index]!;
      waiting.get(s.id)!(record(s.action, terminal, { commandId: s.id, ...extra }));
      await flush();
    },
    stopNow() {
      stop = true;
    },
    failNextSend(e: Error) {
      failSend = e;
    },
    flush,
  };
}

describe("runPlan", () => {
  const view: LiveView = { state: STATE, lastSearch: LAST };
  it("sends step 2 only after step 1 succeeded, then finishes", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("open notepad and maximise it", ctxFor(view)), rt.deps);
    await rt.flush();
    expect(rt.sent.map((s) => s.action)).toEqual(["app.launch"]);
    expect(rt.updates.at(-1)!.steps.map((s) => s.status)).toEqual(["running", "waiting"]);
    await rt.settle(0, { state: "succeeded", result: { app_id: "notepad", launched: true } });
    expect(rt.sent.map((s) => s.action)).toEqual(["app.launch", "app.maximize"]);
    expect(rt.sent[1]!.target).toEqual({ app_id: "notepad" });
    await rt.settle(1, { state: "succeeded" });
    const run = await done;
    expect(run).toMatchObject({ done: true, failedAt: null, stopped: false });
    expect(run.steps.map((s) => s.status)).toEqual(["succeeded", "succeeded"]);
  });
  it("stops at the first step that fails; the rest are not sent", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("open notepad and maximise it", ctxFor(view)), rt.deps);
    await rt.settle(0, { state: "failed", error: { code: "APP_LAUNCH_FAILED", message: "no", retryable: false } });
    const run = await done;
    expect(rt.sent).toHaveLength(1);
    expect(run.failedAt).toBe(0);
    expect(run.steps.map((s) => s.status)).toEqual(["failed", "not_sent"]);
    expect(run.steps[0]!.failure).toEqual({ code: "APP_LAUNCH_FAILED", message: "no" });
  });
  it("a search's results feed the following open_result (its results tab and the first result's video id)", async () => {
    const rt = fakeRuntime({ state: QUIET, lastSearch: null });
    const done = runPlan(steps("play lofi beats on youtube", ctxFor({ state: QUIET })), rt.deps);
    await rt.flush();
    expect(rt.sent[0]).toMatchObject({ action: "youtube.search", params: { query: "lofi beats" }, target: { browser_instance_id: "bi_test0001" } });
    await rt.settle(0, { state: "succeeded", result: { tab: { ...RESULTS_TAB, tab_token: "bmV3LXRva2VuLTAwMDAwMD" }, results: [{ index: 1, video_id: "lofi1", title: "Lofi" }] } });
    expect(rt.sent[1]).toMatchObject({ action: "youtube.open_result", params: { index: 1, video_id: "lofi1" }, target: { browser_instance_id: "bi_test0001", tab_id: 3, tab_token: "bmV3LXRva2VuLTAwMDAwMD" } });
    await rt.settle(1, { state: "succeeded" });
    expect((await done).steps.map((s) => s.status)).toEqual(["succeeded", "succeeded"]);
  });
  it("a search that found nothing stops before opening anything", async () => {
    const rt = fakeRuntime({ state: QUIET, lastSearch: null });
    const done = runPlan(steps("play lofi beats on youtube", ctxFor({ state: QUIET })), rt.deps);
    await rt.settle(0, { state: "succeeded", result: { tab: RESULTS_TAB, results: [] } });
    const run = await done;
    expect(rt.sent).toHaveLength(1);
    expect(run.steps[1]).toMatchObject({ status: "failed", failure: { code: null, message: "YouTube found no videos for that search." } });
  });
  it("a target that is gone, a send that fails and no answer all stop the plan with the reason", async () => {
    const noTab = fakeRuntime({ state: { ...STATE, youtube_tabs: [] } });
    const run1 = await runPlan(steps("pause youtube and lock", ctxFor(view)), noTab.deps);
    expect(noTab.sent).toHaveLength(0);
    expect(run1.steps[0]!.failure).toEqual({ code: null, message: "No YouTube tab is open on the PC." });
    expect(run1.steps[1]!.status).toBe("not_sent");

    const offline = fakeRuntime();
    offline.failNextSend(new Error("reconnecting"));
    const run2 = await runPlan(steps("lock", ctxFor(view)), offline.deps);
    expect(run2.steps[0]!.failure).toEqual({ code: "PC_RECONNECTING", message: "reconnecting" });

    const silent = fakeRuntime();
    const done = runPlan(steps("mute the pc and lock", ctxFor(view)), silent.deps);
    await silent.settle(0, null, { noAnswer: true });
    const run3 = await done;
    expect(run3.steps.map((s) => s.status)).toEqual(["failed", "not_sent"]);
    expect(run3.steps[0]!.failure?.code).toBe("NO_ANSWER");
  });
  it("Stop sends nothing further", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("open notepad and maximise it", ctxFor(view)), rt.deps);
    await rt.flush();
    rt.stopNow();
    await rt.settle(0, { state: "succeeded" });
    const run = await done;
    expect(rt.sent).toHaveLength(1);
    expect(run).toMatchObject({ stopped: true, failedAt: null });
    expect(run.steps.map((s) => s.status)).toEqual(["succeeded", "not_sent"]);
  });
  it("Stop while a step waits for its answer ends the plan at once: that step's result is unknown, nothing more is sent", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("open notepad and maximise it", ctxFor(view)), rt.deps);
    await rt.flush();
    rt.pressStop();
    const run = await done; // no answer needed
    expect(rt.sent.map((s) => s.action)).toEqual(["app.launch"]);
    expect(run).toMatchObject({ done: true, stopped: true, failedAt: null });
    expect(run.steps.map((s) => s.status)).toEqual(["unknown", "not_sent"]);
  });
  it("the connection dropping mid-step ends the plan, saying the step may still have run", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("mute the pc and lock", ctxFor(view)), rt.deps);
    await rt.flush();
    rt.dropConnection();
    const run = await done;
    expect(rt.sent.map((s) => s.action)).toEqual(["windows.set_muted"]);
    expect(run.failedAt).toBe(0);
    expect(run.steps.map((s) => s.status)).toEqual(["unknown", "not_sent"]);
    expect(run.steps[0]!.failure).toEqual({ code: "PC_RECONNECTING", message: "The connection to DoMe dropped while step 1 was running, so its result is unknown: it may still have run on the PC. Check the PC before sending it again.", mayHaveRun: true });
  });
  it("the pause DoMe added is sent to the other tab first; when nothing else plays any more it is skipped", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("play the second video", ctxFor(view)), rt.deps);
    await rt.flush();
    expect(rt.sent[0]).toMatchObject({ action: "youtube.set_paused", params: { paused: true }, target: { tab_id: 1 } });
    await rt.settle(0, { state: "succeeded" });
    expect(rt.sent[1]).toMatchObject({ action: "youtube.open_result", params: { index: 2, video_id: "vidB" }, target: { tab_id: 3 } });
    await rt.settle(1, { state: "succeeded" });
    expect((await done).steps.map((s) => s.status)).toEqual(["succeeded", "succeeded"]);

    const quiet = fakeRuntime({ state: QUIET, lastSearch: LAST });
    const done2 = runPlan(steps("play the second video", ctxFor(view)), quiet.deps);
    await quiet.flush();
    expect(quiet.sent.map((s) => s.action)).toEqual(["youtube.open_result"]);
    await quiet.settle(0, { state: "succeeded" });
    expect((await done2).steps.map((s) => s.status)).toEqual(["skipped", "succeeded"]);
  });
});

describe("runPlan: a window step right after its app was started", () => {
  const view: LiveView = { state: STATE, lastSearch: LAST };
  const gone = { state: "failed" as const, error: { code: "TARGET_GONE", message: "The app is running but has no window to control", retryable: false } };

  it("tries again (up to twice, a moment apart) while the app has no window yet, then goes on", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("open notepad and maximise it", ctxFor(view)), rt.deps);
    await rt.settle(0, { state: "succeeded", result: { app_id: "notepad", launched: true, running: true } });
    await rt.settle(1, gone);
    expect(rt.updates.some((u) => u.steps[1]!.waitingForWindow === true)).toBe(true);
    expect(rt.sent.map((s) => s.action)).toEqual(["app.launch", "app.maximize", "app.maximize"]);
    expect(rt.sent[2]!.target).toEqual({ app_id: "notepad" });
    await rt.settle(2, { state: "succeeded" });
    const run = await done;
    expect(run.steps.map((s) => s.status)).toEqual(["succeeded", "succeeded"]);
    expect(run.steps[1]!.waitingForWindow).toBe(false);
    expect(rt.waits).toEqual([2_000]);
  });
  it("gives up after the retries with the PC's answer", async () => {
    const rt = fakeRuntime();
    const done = runPlan(steps("open notepad and make it full screen", ctxFor(view)), rt.deps);
    await rt.settle(0, { state: "succeeded", result: { app_id: "notepad", launched: true, running: true } });
    for (let i = 1; i <= WINDOW_RETRIES + 1; i++) await rt.settle(i, gone);
    const run = await done;
    expect(rt.sent.filter((s) => s.action === "app.set_fullscreen")).toHaveLength(WINDOW_RETRIES + 1);
    expect(run.failedAt).toBe(1);
    expect(run.steps[1]!.failure?.code).toBe("TARGET_GONE");
  });
  it("never retries without a launch just before, nor another error", async () => {
    const lone = fakeRuntime();
    const done = runPlan(steps("maximise notepad", ctxFor(view)), lone.deps);
    await lone.settle(0, gone);
    expect((await done).failedAt).toBe(0);
    expect(lone.sent).toHaveLength(1);

    const other = fakeRuntime();
    const done2 = runPlan(steps("open notepad and maximise it", ctxFor(view)), other.deps);
    await other.settle(0, { state: "succeeded", result: { app_id: "notepad", launched: true, running: true } });
    await other.settle(1, { state: "failed", error: { code: "WINDOW_STATE_UNCHANGED", message: "x", retryable: false } });
    expect((await done2).failedAt).toBe(1);
    expect(other.sent).toHaveLength(2);
  });
});

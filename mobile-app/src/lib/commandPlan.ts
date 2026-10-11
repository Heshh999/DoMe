/**
 * Typed-command plans on a live PC (spec §13): the parser's context taken from live state, what each step will
 * target (or why it cannot be sent yet) for the preview, and running the steps one after another. Every step is its
 * own signed command; the next one is only sent once the previous one's terminal result says it succeeded, so a
 * plan stops at the first step that fails, expires or gets no answer, and Stop or a dropped connection ends it at
 * once (the step on its way may still finish on the PC; that is said). Targets are chosen again from the live store
 * right before each step is sent, and a step that follows a YouTube step uses the tab that step reported (a search
 * opens a results tab with a new attachment token; "play the first video" goes there).
 */
import { capabilitySatisfied, registry, type JsonValue } from "@dome/protocol";

import type { CommandRecord } from "./commands.ts";
import type { IntentApp, IntentContext, PlanStep } from "./intents.ts";
import { capabilityLabel } from "./labels.ts";
import { browserLabel, controllableTabs, playingElsewhere, resolveMediaTarget, resolveYoutubeBrowserTarget, resolveYoutubeTarget, tabKey, targetForSearchResult, youtubeTargetFor, type YoutubeBrowserResolution, type YoutubeProblem } from "./targets.ts";
import type { LastSearch, MediaSession, PcState, YoutubeSearchItem, YoutubeTab } from "../store/live.ts";

/** The live facts a plan is previewed and resolved against (read again from the store before every step). */
export interface LiveView {
  state: PcState | null;
  /** The PC's `stateSeq` (live store): tells a state report from before the last search from one after it. */
  stateSeq?: number | undefined;
  selectedTab?: string | undefined;
  selectedSession?: string | undefined;
  lastSearch?: LastSearch | null | undefined;
}

/** A browser's own Windows media session is its YouTube tab, not a separate player. */
function isBrowserSession(s: MediaSession): boolean {
  return /msedge|edge|chrome|firefox|brave|opera|vivaldi/i.test(`${s.app_label ?? ""} ${s.session_id}`);
}

/** Context for `parseCommand` from the selected PC's live state and approved apps. */
export function intentContext(view: LiveView & { fresh: boolean; apps: readonly IntentApp[] | null }): IntentContext {
  const state = view.state;
  const yt = resolveYoutubeTarget(state, view.selectedTab);
  const players = (state?.media_sessions ?? []).filter((s) => s.status !== "closed" && !isBrowserSession(s));
  const browsers: Array<"chrome" | "edge" | "unknown"> = [];
  const own = yt.ok ? state?.browser_instances?.find((b) => b.browser_instance_id === yt.tab.browser_instance_id)?.browser : undefined;
  for (const b of [own, ...(state?.browser_instances ?? []).map((i) => i.browser)]) if (b && !browsers.includes(b)) browsers.push(b);
  return {
    apps: view.apps,
    pcVolume: view.fresh && typeof state?.volume?.value === "number" ? state.volume.value : null,
    youtubeVolume: yt.ok && typeof yt.tab.volume === "number" ? yt.tab.volume : null,
    playback: {
      youtube: { available: yt.ok, playing: yt.ok && yt.tab.paused === false },
      media: { available: players.length > 0, playing: players.some((s) => s.status === "playing"), label: players.length === 1 ? (players[0]!.app_label?.replace(/\.exe$/i, "") ?? null) : null },
    },
    browsers,
    lastSearch: view.lastSearch ? { results: view.lastSearch.results } : null,
    playingElsewhere: {
      afterSearch: playingTitle(playingElsewhere(state, searchTab(view))),
      lastResults: view.lastSearch ? playingTitle(playingElsewhere(state, view.lastSearch.tab)) : null,
    },
  };
}

/** The last search's results tab while it still shows the results (same attachment token, no player). */
export function reusableResultsTab(state: PcState | null, last: LastSearch | null | undefined): YoutubeTab | null {
  if (!last) return null;
  return controllableTabs(state).find((t) => tabKey(t) === tabKey(last.tab) && t.context === "other") ?? null;
}

/**
 * Where a typed search runs: in the tab the customer picked, else again in the last search's results tab (one list
 * tab, not a new one per search), else in a new tab. A video being watched is never navigated away unasked.
 */
function searchWhere(view: LiveView): YoutubeBrowserResolution {
  const picked = resolveYoutubeTarget(view.state, view.selectedTab);
  const results = reusableResultsTab(view.state, view.lastSearch);
  return resolveYoutubeBrowserTarget(view.state, picked.ok && picked.explicit ? tabKey(picked.tab) : results ? tabKey(results) : null);
}

/** The tab a search would run in when it reuses one (null: it opens a new tab). */
function searchTab(view: LiveView): YoutubeTab | null {
  const where = searchWhere(view);
  return where.ok && where.mode === "tab" ? where.tab : null;
}

const playingTitle = (tabs: readonly YoutubeTab[]) => (tabs.length > 0 ? { title: tabs[0]!.title ?? null } : null);

function youtubeProblem(reason: YoutubeProblem): string {
  switch (reason) {
    case "ambiguous":
      return "More than one YouTube tab is playing. Choose one in the remote first.";
    case "no_extension":
      return "The DoMe browser extension is not connected on the PC.";
    case "none_attached":
      return "Reload the YouTube tab on the PC so DoMe can control it.";
    case "no_player":
      return "No YouTube video is open on the PC: the YouTube tab shows a list, not a player.";
    case "no_tabs":
      return "No YouTube tab is open on the PC.";
  }
}

function browserProblem(reason: "no_extension" | "no_browser" | "ambiguous"): string {
  switch (reason) {
    case "no_extension":
      return "Searching needs the DoMe browser extension, which is not connected on the PC.";
    case "no_browser":
      return "Open Chrome or Edge with the DoMe extension on the PC to search.";
    case "ambiguous":
      return "DoMe is connected in more than one browser. Pick a YouTube tab in the remote first; the search then runs in it.";
  }
}

/**
 * Why the last search's results cannot be opened any more, or null when they can: its tab still shows the results
 * (a video opened from them navigates that same tab away). The search's answer arrives before the PC's next state
 * report lists the new tab, so a state received before the search answered never counts as "closed".
 */
export function resultsTabProblem(state: PcState | null, last: LastSearch | null | undefined, stateSeq?: number): string | null {
  if (!last) return "Search YouTube first, for example “search YouTube for lofi beats”.";
  if (!state) return null;
  const now = (state.youtube_tabs ?? []).find((t) => t.browser_instance_id === last.tab.browser_instance_id && t.tab_id === last.tab.tab_id);
  if (!now) return stateSeq !== undefined && stateSeq <= last.stateSeq ? null : "The results tab was closed on the PC. Search again.";
  if (now.context === "watch" || now.context === "shorts") return "You opened a video from this list, so that tab plays it now. Search again to pick another one.";
  return null;
}

export interface StepPreview {
  /** Why this step cannot be sent right now (Send stays disabled). */
  problem: string | null;
  /** Where it will run, when that is worth saying ("Opens a new tab in Edge"). */
  where: string | null;
}

/**
 * What the preview says about step `index` of a plan: permission (when this phone's grant is known), whether the PC
 * is locked for actions that need it unlocked, and the target the step would use if it were sent now.
 */
export function previewStep(step: PlanStep, index: number, view: LiveView, capabilities: readonly string[] | null): StepPreview {
  const spec = registry.get(step.action);
  if (capabilities && !capabilitySatisfied(spec, capabilities)) {
    return { problem: `This phone does not have the “${capabilityLabel(spec.capability)}” permission on this PC. The PC owner can allow it in the DoMe tray → Paired phones.`, where: null };
  }
  const state = view.state;
  if (state?.session_locked && spec.availability.includes("session_unlocked")) return { problem: "The PC is locked. Unlock Windows on the PC first.", where: null };
  if (state?.session_locked && spec.availability.includes("session_media_allowed") && state.media_while_locked !== true) {
    return { problem: "The PC is locked, and media controls while locked are off in the DoMe tray settings on the PC.", where: null };
  }
  if (step.fromPrevious) return { problem: null, where: step.needsTarget === "search_results" ? `In the results tab from step ${index}` : `In the tab from step ${index}` };
  switch (step.needsTarget) {
    case null:
      return { problem: null, where: null };
    case "youtube": {
      const yt = resolveYoutubeTarget(state, view.selectedTab);
      return yt.ok ? { problem: null, where: null } : { problem: youtubeProblem(yt.reason), where: null };
    }
    case "media": {
      const media = resolveMediaTarget(state, view.selectedSession);
      if (!media.ok) return { problem: media.reason === "ambiguous" ? "More than one media player is active. Choose one in the remote first." : "No Windows media player is active.", where: null };
      return { problem: null, where: media.session.app_label ? `In ${media.session.app_label.replace(/\.exe$/i, "")}` : null };
    }
    case "youtube_browser": {
      const where = searchWhere(view);
      if (!where.ok) return { problem: browserProblem(where.reason), where: null };
      if (where.mode === "new_tab") return { problem: null, where: `Opens a new tab in ${browserLabel(state, where.browserInstanceId)}` };
      return { problem: null, where: where.tab.context === "other" ? "In the results tab of the last search" : where.tab.title ? `In the tab you picked, replacing “${where.tab.title}”` : "In the YouTube tab you picked" };
    }
    case "search_results": {
      const problem = resultsTabProblem(state, view.lastSearch, view.stateSeq);
      return { problem, where: problem ? null : "In the results tab" };
    }
    case "playing_elsewhere":
      return { problem: null, where: null };
  }
}

/** `skip`: nothing to do any more (the other video stopped by itself), so nothing is sent and the plan goes on. */
export type ResolvedStep = { ok: true; params: Record<string, JsonValue>; target: Record<string, JsonValue> | null } | { ok: false; message: string } | { ok: "skip"; message: string };

type TabResult = { tab?: YoutubeTab; results?: YoutubeSearchItem[] };

function noResult(results: readonly YoutubeSearchItem[], index: number): string {
  if (results.length === 0) return "YouTube found no videos for that search.";
  return `The search found only ${results.length} video${results.length === 1 ? "" : "s"}, so there is no result ${index}.`;
}

/** The tab the step after a "pause the other video" step will play in: the search's tab, or the last results tab. */
function playsIn(next: PlanStep | null, view: LiveView): YoutubeTab | null {
  if (next?.action === "youtube.search") return searchTab(view);
  return view.lastSearch?.tab ?? null;
}

/**
 * Params and target for a step at the moment it is sent. `prev` is the previous step's settled record (it
 * succeeded, or the plan would have stopped); `view` is the live store now; `next` is the step after this one.
 */
export function resolveStep(step: PlanStep, prev: CommandRecord | null, view: LiveView, next: PlanStep | null = null): ResolvedStep {
  if (step.fromPrevious) {
    const result = (prev?.terminal?.result ?? null) as TabResult | null;
    const tab = result?.tab;
    if (!tab) return { ok: false, message: "The previous step did not report which YouTube tab it used, so this step was not sent." };
    if (step.needsTarget === "search_results") {
      const results = result?.results ?? [];
      const index = Number(step.params.index);
      const hit = results.find((r) => r.index === index);
      if (!hit) return { ok: false, message: noResult(results, index) };
      return { ok: true, params: { ...step.params, video_id: hit.video_id }, target: targetForSearchResult(tab) };
    }
    return { ok: true, params: step.params, target: youtubeTargetFor(tab) };
  }
  switch (step.needsTarget) {
    case null:
      return { ok: true, params: step.params, target: step.target };
    case "youtube": {
      const yt = resolveYoutubeTarget(view.state, view.selectedTab);
      return yt.ok ? { ok: true, params: step.params, target: yt.target } : { ok: false, message: youtubeProblem(yt.reason) };
    }
    case "media": {
      const media = resolveMediaTarget(view.state, view.selectedSession);
      return media.ok ? { ok: true, params: step.params, target: media.target } : { ok: false, message: media.reason === "ambiguous" ? "More than one media player is active. Choose one in the remote first." : "No Windows media player is active." };
    }
    case "youtube_browser": {
      const where = searchWhere(view);
      return where.ok ? { ok: true, params: step.params, target: where.target } : { ok: false, message: browserProblem(where.reason) };
    }
    case "search_results": {
      const last = view.lastSearch;
      const problem = resultsTabProblem(view.state, last, view.stateSeq);
      if (problem || !last) return { ok: false, message: problem ?? "Search YouTube first." };
      // The video id comes from the same search as the tab, so a newer search never pairs one's tab with the other's video.
      const index = Number(step.params.index);
      const hit = last.results.find((r) => r.index === index);
      if (!hit) return { ok: false, message: noResult(last.results, index) };
      return { ok: true, params: { ...step.params, video_id: hit.video_id }, target: targetForSearchResult(last.tab) };
    }
    case "playing_elsewhere": {
      const other = playingElsewhere(view.state, playsIn(next, view))[0];
      return other ? { ok: true, params: step.params, target: youtubeTargetFor(other) } : { ok: "skip", message: "Nothing else was playing any more." };
    }
  }
}

/** `skipped`: nothing needed doing; `unknown`: Stop or a dropped connection ended the plan while it was running. */
export type StepStatus = "waiting" | "sending" | "running" | "succeeded" | "skipped" | "failed" | "not_sent" | "unknown";

/** Why a step did not succeed: an error code from the PC, relay or phone, or (code null) a reason found before sending. */
export interface StepFailure {
  code: string | null;
  message: string;
  /** The step was on its way when the plan ended (`message` says so): it may still have run on the PC. */
  mayHaveRun?: boolean;
}

export interface StepRun {
  status: StepStatus;
  commandId: string | null;
  failure: StepFailure | null;
  /** The app was just started and has not shown its window yet: the step is tried again shortly. */
  waitingForWindow?: boolean;
}

export interface PlanRun {
  steps: StepRun[];
  done: boolean;
  /** Index of the step that failed (the rest were not sent). */
  failedAt: number | null;
  /** The customer pressed Stop; the steps after the current one were not sent. */
  stopped: boolean;
}

export interface PlanRunDeps {
  /** The live store now (targets are chosen again for every step). */
  view(): LiveView;
  /** Sign and send one command; throws when it cannot be sent at all. */
  send(action: string, params: Record<string, JsonValue>, target: Record<string, JsonValue> | null): Promise<CommandRecord>;
  /** Resolves once the command has a terminal result, or got no answer in time. */
  settled(commandId: string): Promise<CommandRecord | null>;
  describeError(e: unknown): { code: string; message: string };
  stopRequested(): boolean;
  update(run: PlanRun): void;
  /** False while the connection to DoMe is not open: no answer can arrive then. */
  connected?(): boolean;
  /** Calls `wake` when Stop is pressed or the connection changes, so waiting ends at once; returns the unsubscribe. */
  onInterrupt?(wake: () => void): () => void;
  /** Waits `ms` (tests pass a fast one). */
  wait?(ms: number): Promise<void>;
}

const STATE_CODES: Record<string, string> = { expired: "COMMAND_EXPIRED", outcome_unknown: "OUTCOME_UNKNOWN", canceled: "CANCELED", failed: "INTERNAL" };

/** null when the step succeeded; otherwise why it did not. */
export function stepFailure(record: CommandRecord | null): StepFailure | null {
  if (!record || (!record.terminal && record.noAnswer)) return { code: "NO_ANSWER", message: "The PC did not answer in time." };
  const t = record.terminal;
  if (!t) return { code: "NO_ANSWER", message: "The PC did not answer in time." };
  if (t.state === "succeeded") return null;
  return { code: t.error?.code ?? STATE_CODES[t.state] ?? "INTERNAL", message: t.error?.message ?? "" };
}

/** Window steps that act on an app's first window; right after the app was started it may not have one yet. */
const WINDOW_STEPS: ReadonlySet<string> = new Set(["app.focus", "app.minimize", "app.maximize", "app.restore", "app.set_fullscreen"]);
/**
 * Tries again for a window step whose app was started by the step before and answered TARGET_GONE (running, no
 * window yet: apps such as Spotify or Steam take several seconds to show one). Safe to repeat: the PC refuses
 * TARGET_GONE before it touches anything (`_select_window` runs before any side effect).
 */
export const WINDOW_RETRIES = 2;
export const WINDOW_RETRY_MS = 2_000;

type Interruption = "stopped" | "disconnected";

/** Resolves with the settled record, or as soon as Stop is pressed or the connection drops. */
function settledOrInterrupted(deps: PlanRunDeps, commandId: string): Promise<{ record: CommandRecord | null } | { interrupted: Interruption }> {
  return new Promise((resolve) => {
    let done = false;
    let unsubscribe: () => void = () => undefined;
    const finish = (r: { record: CommandRecord | null } | { interrupted: Interruption }) => {
      if (done) return;
      done = true;
      unsubscribe();
      resolve(r);
    };
    const check = () => {
      if (deps.stopRequested()) finish({ interrupted: "stopped" });
      else if (deps.connected && !deps.connected()) finish({ interrupted: "disconnected" });
    };
    void deps.settled(commandId).then((record) => finish({ record }));
    unsubscribe = deps.onInterrupt?.(check) ?? (() => undefined);
    check();
  });
}

/** Waits `ms`, ending early (false) when Stop is pressed or the connection drops. */
function pause(deps: PlanRunDeps, ms: number): Promise<boolean> {
  return new Promise((resolve) => {
    let done = false;
    let unsubscribe: () => void = () => undefined;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      unsubscribe();
      resolve(ok);
    };
    void (deps.wait ?? ((t: number) => new Promise<void>((r) => setTimeout(r, t))))(ms).then(() => finish(true));
    unsubscribe = deps.onInterrupt?.(() => {
      if (deps.stopRequested() || (deps.connected && !deps.connected())) finish(false);
    }) ?? (() => undefined);
  });
}

/** Run a plan's steps in order (see the module comment). Never throws; the returned run says how far it got. */
export async function runPlan(steps: readonly PlanStep[], deps: PlanRunDeps): Promise<PlanRun> {
  let run: PlanRun = { steps: steps.map(() => ({ status: "waiting", commandId: null, failure: null })), done: false, failedAt: null, stopped: false };
  const patch = (i: number, change: Partial<StepRun>, more: Partial<PlanRun> = {}) => {
    run = { ...run, ...more, steps: run.steps.map((s, j) => (j === i ? { ...s, ...change } : s)) };
    deps.update(run);
  };
  let prev: CommandRecord | null = null;
  let prevStep: PlanStep | null = null;
  plan: for (let i = 0; i < steps.length; i++) {
    const step = steps[i]!;
    if (deps.stopRequested()) {
      run = { ...run, stopped: true };
      break;
    }
    const resolved = resolveStep(step, prev, deps.view(), steps[i + 1] ?? null);
    if (resolved.ok === false) {
      patch(i, { status: "failed", failure: { code: null, message: resolved.message } }, { failedAt: i });
      break;
    }
    if (resolved.ok === "skip") {
      patch(i, { status: "skipped" });
      continue;
    }
    const afterLaunch = WINDOW_STEPS.has(step.action) && prevStep?.action === "app.launch" && prevStep.params.app_id === resolved.target?.app_id;
    for (let attempt = 0; ; attempt++) {
      patch(i, { status: "sending" });
      let record: CommandRecord;
      try {
        record = await deps.send(step.action, resolved.params, resolved.target);
      } catch (e) {
        patch(i, { status: "failed", failure: deps.describeError(e), waitingForWindow: false }, { failedAt: i });
        break plan;
      }
      patch(i, { status: "running", commandId: record.commandId });
      const outcome = await settledOrInterrupted(deps, record.commandId);
      if ("interrupted" in outcome) {
        if (outcome.interrupted === "stopped") {
          patch(i, { status: "unknown", waitingForWindow: false }, { stopped: true });
        } else {
          const message = `The connection to DoMe dropped while step ${i + 1} was running, so its result is unknown: it may still have run on the PC. Check the PC before sending it again.`;
          patch(i, { status: "unknown", failure: { code: "PC_RECONNECTING", message, mayHaveRun: true }, waitingForWindow: false }, { failedAt: i });
        }
        break plan;
      }
      const failure = stepFailure(outcome.record);
      if (failure?.code === "TARGET_GONE" && afterLaunch && attempt < WINDOW_RETRIES) {
        patch(i, { status: "waiting", waitingForWindow: true });
        if (!(await pause(deps, WINDOW_RETRY_MS))) {
          // Stop or a dropped connection while waiting: the PC's last answer (no window yet) stands
          patch(i, { status: "failed", failure, waitingForWindow: false }, { failedAt: i, stopped: deps.stopRequested() });
          break plan;
        }
        continue;
      }
      if (failure) {
        patch(i, { status: "failed", failure, waitingForWindow: false }, { failedAt: i });
        break plan;
      }
      patch(i, { status: "succeeded", waitingForWindow: false });
      prev = outcome.record;
      prevStep = step;
      break;
    }
  }
  run = { ...run, done: true, steps: run.steps.map((s) => (s.status === "waiting" ? { ...s, status: "not_sent" } : s)) };
  deps.update(run);
  return run;
}

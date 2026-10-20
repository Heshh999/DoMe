/**
 * The Assistant's turn pipeline (docs/design/assistant.md §4.3–§4.5), pure logic with its dependencies injected so the
 * tests drive it without React, a socket or a microphone.
 *
 * Deterministic first: every request goes through the Type page's parser and is handled on the phone when it can be
 * (instant, free, works with AI off). Only words the parser does not know reach the AI, and only when the owner turned
 * it on; its steps come back as the same plan representation and are checked again here (`planFromAssistant`). Every
 * plan is shown before anything runs; only plans of low-risk steps run without a tap on Run, a step the PC asks to
 * confirm waits for the owner's tap in the confirmation modal, and a spoken "yes" never runs anything. Each step is
 * signed by this phone through the one command path. What is said afterwards comes from the real results
 * (butlerSpeech.ts); what is remembered for a later AI turn comes from closed templates (`recentLine`).
 */
import { ERRORS, registry, type rest } from "@dome/protocol";

import { refreshApprovedApps } from "../app/hooks.ts";
import { getRuntime } from "../app/runtime.ts";
import { useAssistantStore, IDLE_CLEAR_MS, type AiBlock, type AssistantState, type ChatChip, type RecentLine } from "../store/assistant.ts";
import { useDevicesStore } from "../store/devices.ts";
import { isFresh, useLiveStore } from "../store/live.ts";
import { ApiError } from "./api.ts";
import { buildAssistantContext } from "./assistantContext.ts";
import { limitExplanation, lowerFirst, refreshText } from "./billing.ts";
import { actionPhrase, certainly, cleanText, CONNECTION_DROPPED, DECLINED, planSpeech, powerCancelSpeech, recentClarifyLine, recentOwnerNote, recentPlanLine, recentText, RECENT_AI_ERROR, RECENT_HANDOFF, RECENT_UNKNOWN, runPrompt, screenForMessage, speakable, SWITCHED_PC, TAP_YOUR_CHOICE, UNKNOWN_SCREEN, voiceForAiError, voiceForMessage, type Persona } from "./butlerSpeech.ts";
import { intentContext as liveIntentContext, previewStep, type LiveView, type PlanRun, type StepPreview } from "./commandPlan.ts";
import type { CommandRecord } from "./commands.ts";
import { normalizeText, parseCommand, planFromAssistant, type CommandParse, type IntentContext, type PlanStep } from "./intents.ts";
import { ordinalWord } from "./intentWords.ts";
import { errorMessage } from "./labels.ts";
import { log } from "./log.ts";
import { controlProblem, createStopper, liveView, refreshAppsAndWait, runPlanOnPc, waitForLive, type ControlProblem, type PlanStopper, type RunOnPcOptions } from "./planRun.ts";

/** How the words arrived. `dictated`: the text field opened through Dictate (sent to the AI as `speech`); `local`: a chip DoMe built itself, never sent to the AI. */
export type TurnSource = "speech" | "typed" | "dictated" | "choice" | "local";

export interface TurnOptions {
  /** When the words ended (the recognizer's speech end, or the Send tap); defaults to now. */
  endedAt?: number;
  /** A deterministic chip was tapped: its label names one part, its text may be a whole command. */
  fromChip?: boolean;
  /** "Ask the assistant": these words go to the AI without the parser. */
  forceAi?: boolean;
}

/** The selected PC as the pipeline sees it (live stores in the app, fakes in tests). */
export interface PcAccess {
  pcId(): string | null;
  pcName(): string | null;
  problem(): ControlProblem | null;
  waitForControl(ms: number): Promise<boolean>;
  view(): LiveView;
  fresh(): boolean;
  /** The parser's context (approved apps, volumes, players, last search), without `lastAppId`. */
  intentContext(): IntentContext;
  waitForApps(ms: number): Promise<boolean>;
  /** Re-read the approved apps unless the list is fresh, and wait for the PC's answer. */
  refreshApps(): Promise<void>;
  /** This phone's grant on the PC (null while unknown). */
  capabilities(): string[] | null;
  /** A sleep, restart or shut-down countdown is running on the PC. */
  powerPending(): boolean;
  previews(steps: readonly PlanStep[]): StepPreview[];
  runPlan(pcId: string, steps: readonly PlanStep[], options: RunOnPcOptions): Promise<PlanRun>;
  record(commandId: string): CommandRecord | null;
  /** Calls `fn` whenever a command record changes; returns the unsubscribe. */
  onRecords(fn: () => void): () => void;
  /** The PC's display name for an approved app (spoken and shown on this phone only). */
  appName(appId: string | null): string | null;
  failureText(code: string | null, message: string, step: PlanStep, record: CommandRecord | null): string;
}

export interface Visibility {
  visible(): boolean;
  subscribe(fn: (visible: boolean) => void): () => void;
}

export interface SpeakerLike {
  speak(text: string, options?: { gesture?: boolean; onNotStarted?(): void; onStart?(): void; onEnd?(): void }): void;
  cancel(): void;
}

export interface AssistantDeps {
  api: { turn(body: rest.AssistantTurnRequest, signal: AbortSignal): Promise<rest.AssistantTurnResponse> };
  pc: PcAccess;
  speaker: SpeakerLike;
  store?: { getState(): AssistantState };
  visibility?: Visibility;
  now?: () => number;
  uuid?: () => string;
}

export interface AssistantController {
  handleText(text: string, source: TurnSource, options?: TurnOptions): Promise<void>;
  /** The Run tap on a waiting plan card. */
  run(itemId: number): Promise<void>;
  /** The Cancel tap on a waiting plan card. */
  dismiss(itemId: number): void;
  /** Stop the running plan (the steps not sent yet stay unsent). */
  stop(): void;
  /** Cancel the AI turn in flight (also when the owner switches PC: its answer would be about the other PC). */
  cancelTurn(): void;
  /** The large Cancel shut-down / restart / sleep tap on a plan card: power.cancel, signed and announced like a plan. */
  cancelPower(itemId: number): Promise<void>;
  /** "Tap to hear": speak a bubble again from a gesture. */
  hear(itemId: number): void;
  /** Clear the conversation if nobody spoke for 10 minutes. */
  expireIdle(): void;
}

/** Waits (ms): current PC state after a relaunch, the app list, a slow first result, the AI's first word. */
export const CONNECT_WAIT_MS = 5_000;
export const APPS_WAIT_MS = 3_000;
export const ONE_MOMENT_MS = 2_000;
export const CERTAINLY_MS = 1_000;
/** A plan ready later than this after the words ended waits for Run. */
export const LATE_PLAN_MS = 15_000;
export const BUSY_RETRY_MS = 2_000;
/** The phone aborts its own request this long after the server's turn deadline. */
export const CLIENT_GRACE_MS = 5_000;
export const MAX_TEXT = 400;
const RECENT_LINES = 6;

const YES = /^(?:yes|yes please|yeah|yep|sure|ok|okay|go ahead|do it|run it|please do)$/;
const NO = /^(?:no|nope|no thanks|no thank you|cancel|never mind|nevermind|stop|don't|do not|forget it|leave it)$/;
const CANCEL = /^(?:cancel|never mind|nevermind)$/;
const MAY_COUNT = "This request may still count towards your allowance.";
/** The step behind a card's Cancel shut-down button (what the parser builds for "cancel the countdown"). */
const CANCEL_COUNTDOWN: PlanStep = { action: "power.cancel", params: {}, target: null, needsTarget: null, fromPrevious: false, confirmation: false, detail: null, warning: null, appId: null, text: "cancel the countdown", added: false };
/** iOS ends recognition at a pause: words ending on a joiner may not be the whole request. */
const ENDS_ON_JOINER = /(?:\b(?:and|then|and then|also)|,)\s*$/i;

/** The owner's words as sent: format characters removed, separators as spaces, at most 400 characters. */
export function normalizeTurnText(raw: string): string {
  return [...cleanText(raw)].slice(0, MAX_TEXT).join("").trim();
}

/** The AI can be asked: the account's assistant is available, consent is current, this phone uses it, and no limit hit. */
export function aiUsable(s: Pick<AssistantState, "status" | "prefs" | "aiBlocked">): boolean {
  const st = s.status;
  return !!st && st.available && st.consent?.version === st.consent_version && s.prefs.useAi && s.aiBlocked === null;
}

const RELEASE_CHANNEL: string | undefined = import.meta.env.VITE_DOME_RELEASE_CHANNEL;

/** Why the assistant cannot be used on this account (null: it can). */
export function unavailableText(status: rest.AssistantStatusResponse): string | null {
  switch (status.unavailable_reason) {
    case null:
      return null;
    case "not_configured":
      return `The AI assistant isn't switched on for this DoMe service.${RELEASE_CHANNEL === "test" ? " Test kit: put your Anthropic API key in testkit\\ai-key.txt and run '4 Apply AI settings' on the PC." : ""}`;
    case "not_in_plan":
      return "AI requests aren't part of your plan.";
    case "allowance_exhausted":
      return aiErrorText({ code: "AI_ALLOWANCE_EXHAUSTED", ...(status.allowance.exhausted ? { reason: status.allowance.exhausted } : {}) }, status.allowance);
    case "spend_limit":
      return ERRORS.AI_SPEND_LIMIT_REACHED!.user_message;
  }
}

/** After a named limit: what still works (the contract's message would name the limit and its refresh again). */
const STILL_WORKS = "Buttons, typed commands and simple voice commands still work.";

/**
 * Explanation of an AI error code for the conversation and the banner: the limit when it is known (lib/billing.ts
 * `limitExplanation`, with the error's own resets_at when the server sent one), else the contract's message.
 */
export function aiErrorText(block: AiBlock, allowance: rest.AssistantAllowance | null): string {
  const limit = block.code === "AI_ALLOWANCE_EXHAUSTED" ? limitExplanation(block.reason ?? allowance?.exhausted ?? undefined, allowance, { resetsAt: block.resetsAt ?? null }) : "";
  if (limit) return `${limit} ${STILL_WORKS}`;
  return ERRORS[block.code]?.user_message ?? errorMessage({ code: block.code });
}

export const AI_HINT = "The AI assistant can help with requests like this: turn it on in Settings.";

const AI_STOPPING: ReadonlySet<string> = new Set(["AI_ALLOWANCE_EXHAUSTED", "AI_SPEND_LIMIT_REACHED", "AI_NOT_AVAILABLE", "AI_CONSENT_REQUIRED"]);

const defaultVisibility: Visibility = {
  visible: () => document.visibilityState !== "hidden",
  subscribe(fn) {
    const on = () => fn(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  },
};

interface Turn {
  start: number;
  hidden: boolean;
  /** Words ended on "and", "then" or a comma. */
  unfinished: boolean;
  fromChip: boolean;
  /** The PC selected and the conversation when the turn started: its plan is only ever for that PC. */
  pcId: string | null;
  conversation: number;
  /** The previous turn ended on a clarification with choices to tap (a yes or no then answers it on the phone). */
  question: boolean;
}

export function createAssistantController(deps: AssistantDeps): AssistantController {
  const store = deps.store ?? useAssistantStore;
  const pc = deps.pc;
  const visibility = deps.visibility ?? defaultVisibility;
  const now = deps.now ?? (() => Date.now());
  const uuid = deps.uuid ?? (() => crypto.randomUUID());
  const stopper: PlanStopper = createStopper();
  let inflight: { abort: AbortController; cancelled: boolean } | null = null;
  /** The last AI turn the owner cancelled: an AI_BUSY until its deadline is that turn finishing on the server. */
  let lastCancel: { start: number; until: number } | null = null;
  let turns = 0;
  /** A turn or a Run is under way, including its waits (the PC's state, its apps): nothing else starts meanwhile. */
  let busy = false;

  const s = () => store.getState();
  const personaOf = (): Persona => ({ name: s().prefs.butlerName, address: s().prefs.address });
  const ctxNow = (): IntentContext => ({ ...pc.intentContext(), lastAppId: s().lastAppId });
  const appNameOf = (step: PlanStep) => pc.appName(step.appId ?? (typeof step.target?.app_id === "string" ? step.target.app_id : typeof step.params.app_id === "string" ? step.params.app_id : null));

  /** Speak a line; a bubble whose speech did not start offers "Tap to hear". `gesture`: called inside a tap. */
  const speak = (text: string, itemId?: number, gesture = false) => {
    if (!text) return;
    deps.speaker.speak(text, {
      ...(gesture ? { gesture: true } : {}),
      onNotStarted: () => {
        if (itemId !== undefined && !s().prefs.muted) s().updateItem(itemId, { tapToHear: true });
      },
    });
  };

  /** A butler line on screen (`text`) and aloud (`spoken`). */
  const say = (text: string, spoken: string, extra: { ai?: { charged: boolean } | null; chips?: ChatChip[]; touchpad?: boolean; tone?: "warning" } = {}) => {
    const id = s().addItem({ kind: "butler", text, spoken, ai: extra.ai ?? null, chips: extra.chips ?? [], touchpad: extra.touchpad ?? false, tapToHear: false, ...(extra.tone ? { tone: extra.tone } : {}) });
    speak(spoken, id);
    return id;
  };

  const remember = (owner: string, butler: string) => {
    const lines: RecentLine[] = [
      { who: "owner", text: recentText(owner), at: 0 },
      { who: "butler", text: recentText(butler), at: 0 },
    ];
    s().pushRecent(
      lines.filter((l) => l.text !== "").map(({ who, text }) => ({ who, text })),
      now(),
    );
  };

  const recentForRequest = (): rest.AssistantRecent[] => {
    const since = now() - IDLE_CLEAR_MS;
    return s()
      .recent.filter((l) => l.at >= since && l.text !== "")
      .slice(-RECENT_LINES)
      .map((l) => ({ who: l.who, text: recentText(l.text) })) as rest.AssistantRecent[];
  };

  const pcReason = (problem: ControlProblem): string => {
    const name = pc.pcName() ?? "Your PC";
    switch (problem) {
      case "no_pc":
        return "There's no PC to control yet. Link one under Devices.";
      case "disabled":
        return `${name} is switched off on your plan, so I can't do that. Choose which PC stays enabled under Devices.`;
      case "remote_off":
        return `Remote control is switched off on ${name}. Switch it back on from the DoMe tray menu on the PC.`;
      case "offline":
      case "not_current":
        return `${name} isn't connected right now, so I can't do that.`;
    }
  };

  /** The owner switched PC (or the conversation was cleared) since the turn started: nothing of it may be used now. */
  const stale = (turn: Turn) => pc.pcId() !== turn.pcId || s().conversation !== turn.conversation;
  /** A stale turn ends without a plan and without `recent` lines; with a PC selected it says why nothing happened. */
  const leave = (screenExtra = "", ai: { charged: boolean } | null = null) => {
    if (pc.pcId() !== null) say(`${SWITCHED_PC}${screenExtra}`, SWITCHED_PC, { ai });
  };

  /** A parse that names an app the list may not have yet: wait for the list, or re-read it once, then parse again. */
  const settleApps = async (parsed: CommandParse, again: () => CommandParse): Promise<CommandParse> => {
    if (parsed.kind !== "clarify" || pc.problem() !== null || (!parsed.appsUnknown && parsed.missingApp === undefined)) return parsed;
    // shown as busy for the whole wait (up to 5 s): the talk button, Send and Run wait too
    s().setPhase("checking");
    s().setNotice("Checking the PC's apps…");
    try {
      if (parsed.appsUnknown) await pc.waitForApps(APPS_WAIT_MS);
      else await pc.refreshApps();
    } finally {
      s().setNotice(null);
      s().setPhase("idle");
    }
    return again();
  };

  const resultChips = (): ChatChip[] => (pc.view().lastSearch?.results ?? []).slice(0, 5).map((r) => ({ label: `${r.index}. ${r.title || "Untitled video"}`, text: `play the ${ordinalWord(r.index)} video`, kind: "local" }));

  /** Run a shown plan. `wordsEndedAt`: when the words of the turn that runs it at once ended (for the timings). */
  const runItem = async (itemId: number, wordsEndedAt: number | null = null): Promise<void> => {
    const item = s().items.find((it) => it.id === itemId);
    if (!item || item.kind !== "plan" || (item.status !== "waiting_run" && item.status !== "running")) return;
    if (item.pcId !== pc.pcId()) {
      // a card is only ever run on the PC it was shown for
      s().updateItem(itemId, { status: "blocked" });
      say(SWITCHED_PC, SWITCHED_PC);
      return;
    }
    const conversation = s().conversation;
    const problem = pc.problem();
    if (problem) {
      s().updateItem(itemId, { status: "blocked" });
      say(pcReason(problem), pcReason(problem));
      return;
    }
    const previews = pc.previews(item.steps);
    const blocked = previews.find((p) => p.problem)?.problem ?? null;
    if (blocked) {
      s().updateItem(itemId, { status: "blocked", previews });
      say(blocked, speakable(blocked));
      return;
    }
    const steps = item.steps;
    s().setPhase("working");
    s().updateItem(itemId, { status: "running", previews });
    stopper.reset();
    let certaintySaid = false;
    let confirmSaid = false;
    let current: PlanRun | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const pending = () => {
      const i = current?.steps.findIndex((st) => st.status === "sending" || st.status === "running") ?? -1;
      const id = i >= 0 ? current?.steps[i]?.commandId : null;
      return id ? pc.record(id) : null;
    };
    // "Please confirm on the screen." once, when the PC's challenge arrives: only a tap in the modal approves
    const offRecords = pc.onRecords(() => {
      const rec = pending();
      if (!confirmSaid && rec?.confirmation && rec.confirmation.decision === null) {
        confirmSaid = true;
        speak("Please confirm on the screen.");
      }
    });
    const sentAt = now();
    let firstResultAt: number | null = null;
    try {
      const run = await pc.runPlan(item.pcId, steps, {
        source: "assistant",
        stopper,
        onFirstSend: () => {
          timer = setTimeout(() => {
            const first = current?.steps[0];
            if (!first || first.status === "sending" || first.status === "running") {
              if (!confirmSaid && !pending()?.confirmation) {
                certaintySaid = true;
                speak(certainly(personaOf()));
              }
            }
          }, CERTAINLY_MS);
        },
        onUpdate: (r) => {
          current = r;
          if (firstResultAt === null && r.steps.some((st) => st.status === "succeeded" || st.status === "failed")) firstResultAt = now();
          s().updateItem(itemId, { run: r });
        },
      });
      // the owner switched PC while it ran: its card is gone and its lines would land in the other PC's conversation
      if (s().conversation !== conversation) return;
      const records = run.steps.map((st) => (st.commandId ? pc.record(st.commandId) : null));
      const outcome = { steps, run, records };
      // "it" next time is the app this plan last acted on (kept on the phone only)
      const lastApp = [...steps].reverse().find((st, k) => st.appId && run.steps[steps.length - 1 - k]?.status === "succeeded");
      if (lastApp?.appId) s().setLastAppId(lastApp.appId);
      const searched = steps.some((st, i) => st.action === "youtube.search" && run.steps[i]?.status === "succeeded");
      s().updateItem(itemId, { status: "done", run, results: searched ? resultChips() : [] });
      const spoken = planSpeech(outcome, { persona: personaOf(), appName: appNameOf, turn: turns++, certaintySaid, failureText: pc.failureText });
      const shown = spoken;
      const speechAt = now();
      say(shown, speakable(spoken, 8));
      s().pushRecent([{ who: "butler", text: recentPlanLine(outcome) }], now());
      // durations only, never words (Settings → Diagnostics)
      const from = wordsEndedAt ?? sentAt;
      log.info("assistant.timing", { stage: "plan", ran_at_once: wordsEndedAt !== null, to_first_result_ms: firstResultAt === null ? null : firstResultAt - from, to_speech_ms: speechAt - from });
    } finally {
      if (timer) clearTimeout(timer);
      offRecords();
      s().setPhase("idle");
    }
  };

  /** Show a plan, then run it at once (low risk, prompt, visible, finished words) or wait for Run (§4.5). */
  const presentPlan = async (parsed: Extract<CommandParse, { kind: "plan" }>, turn: Turn, ai: { charged: boolean } | null, ownerLine: string): Promise<void> => {
    const pcId = turn.pcId;
    const problem = pc.problem();
    if (!pcId || problem) {
      const reason = pcReason(problem ?? "no_pc");
      say(reason, reason, { ai });
      remember(ownerLine, `I said the PC isn't available right now.`);
      return;
    }
    const steps = parsed.steps;
    const previews = pc.previews(steps);
    const blocked = previews.find((p) => p.problem)?.problem ?? null;
    const chips: ChatChip[] = parsed.choices.map((c) => ({ label: c.label, text: c.text, kind: "local" }));
    const itemId = s().addItem({ kind: "plan", pcId, pcName: pc.pcName(), steps, previews, status: blocked ? "blocked" : "waiting_run", run: null, chips, results: [], ai });
    if (blocked) {
      say(blocked, speakable(blocked), { ai });
      remember(ownerLine, `I couldn't ${actionPhrase(steps[0]!)} right now.`);
      return;
    }
    const lowRisk = steps.every((st) => registry.get(st.action).risk === "low");
    const loneChallenge = steps.length === 1 && steps[0]!.confirmation;
    const late = turn.hidden || !visibility.visible() || now() - turn.start > LATE_PLAN_MS;
    const chipPlan = turn.fromChip && steps.filter((st) => !st.added).length > 1;
    const runsNow = (lowRisk || loneChallenge) && !s().prefs.confirmEveryAction && !late && !chipPlan && !turn.unfinished;
    if (runsNow) {
      remember(ownerLine, "");
      await runItem(itemId, turn.start);
      return;
    }
    const prompt = late ? "You asked this a moment ago. Run it now?" : runPrompt(steps, appNameOf);
    say(prompt, prompt, { ai });
    remember(ownerLine, "I showed the steps and waited for a tap on Run.");
  };

  /** Words DoMe cannot handle without AI, when no AI turn follows (with a hint when the assistant could help). */
  const unknownLocally = (message: string | null, ownerLine: string) => {
    const st = s();
    const hint = st.status?.available && !aiUsable(st) && st.aiBlocked === null ? ` ${AI_HINT}` : "";
    say(`${message === null ? UNKNOWN_SCREEN : screenForMessage(message)}${hint}`, voiceForMessage(message));
    remember(ownerLine, RECENT_UNKNOWN);
  };

  const errorCode = (e: unknown, timedOut: boolean): { code: string; detail?: Record<string, unknown> } => {
    if (timedOut) return { code: "NETWORK" };
    if (e instanceof ApiError) return { code: e.code, ...(e.detail ? { detail: e.detail } : {}) };
    if (e instanceof DOMException && e.name === "AbortError") return { code: "NETWORK" };
    return { code: "MALFORMED_MESSAGE" };
  };

  const aiTurn = async (text: string, source: Exclude<TurnSource, "local">, turn: Turn, parserMessage: string | null): Promise<void> => {
    const st = s();
    const deadlineMs = (st.status?.turn_deadline_seconds ?? 20) * 1000 + CLIENT_GRACE_MS;
    const wireSource: rest.AssistantTurnRequest["source"] = source === "dictated" ? "speech" : source;
    const request = (): rest.AssistantTurnRequest => ({
      turn_id: uuid(),
      source: wireSource,
      text,
      recent: recentForRequest() as rest.AssistantTurnRequest["recent"],
      context: buildAssistantContext({ ...pc.view(), controllable: pc.problem() === null, fresh: pc.fresh() }, pc.intentContext(), pc.capabilities(), s().prefs, new Date(now())),
    });
    s().setPhase("thinking");
    const started = now();
    const turnStart = turn.start;
    let oneMomentSaid = false;
    const oneMoment = setTimeout(() => {
      oneMomentSaid = true;
      speak("One moment.");
    }, ONE_MOMENT_MS);
    let response: rest.AssistantTurnResponse | null = null;
    let failure: { code: string; detail?: Record<string, unknown> } | null = null;
    let finishing = false;
    let cancelled = false;
    try {
      for (;;) {
        const abort = new AbortController();
        const mine = { abort, cancelled: false };
        inflight = mine;
        let timedOut = false;
        const deadline = setTimeout(() => {
          timedOut = true;
          abort.abort();
        }, deadlineMs);
        let body: rest.AssistantTurnRequest | null = null;
        try {
          body = request();
          const answer = await deps.api.turn(body, abort.signal);
          if (answer.turn_id !== body.turn_id) throw new ApiError(200, "MALFORMED_MESSAGE", "mismatched turn", false);
          response = answer;
          failure = null;
        } catch (e) {
          if (mine.cancelled) {
            lastCancel = { start: turnStart, until: turnStart + deadlineMs };
            cancelled = true;
          } else failure = errorCode(e, timedOut);
        } finally {
          clearTimeout(deadline);
          if (inflight === mine) inflight = null;
        }
        if (cancelled) break;
        // AI_BUSY right after a Cancel: that turn is still finishing on the server; send the words again shortly
        if (failure?.code === "AI_BUSY" && lastCancel && now() + BUSY_RETRY_MS < lastCancel.until) {
          if (!finishing) {
            finishing = true;
            s().setNotice("Finishing your last request…");
          }
          const waited = await new Promise<boolean>((resolve) => {
            const mineWait = { abort: new AbortController(), cancelled: false };
            inflight = mineWait;
            const t = setTimeout(() => resolve(true), BUSY_RETRY_MS);
            mineWait.abort.signal.addEventListener("abort", () => {
              clearTimeout(t);
              resolve(false);
            });
          });
          if (!waited) {
            cancelled = true;
            break;
          }
          continue;
        }
        break;
      }
    } finally {
      clearTimeout(oneMoment);
      if (finishing) s().setNotice(null);
      s().setPhase("idle");
      log.info("assistant.timing", { stage: "ai", ai: true, to_response_ms: now() - turnStart, request_ms: now() - started, one_moment: oneMomentSaid, outcome: response ? response.mode : cancelled ? "cancelled" : (failure?.code ?? "cancelled") });
    }
    if (response) s().setAllowance(response.allowance);
    // the owner switched PC while DoMe was thinking: the answer was worked out for the other PC
    if (stale(turn)) {
      leave(cancelled ? ` ${MAY_COUNT}` : "", response ? { charged: response.charged } : null);
      return;
    }
    if (cancelled) {
      say(`Cancelled. ${MAY_COUNT}`, "Cancelled.");
      remember(text, RECENT_AI_ERROR);
      return;
    }
    if (!response) {
      const code = failure?.code ?? "AI_UNAVAILABLE";
      if (code === "AI_DECLINED") {
        const msg = ERRORS.AI_DECLINED!.user_message;
        say(msg, speakable(msg), { tone: "warning" });
        remember(text, "I said I can't help with that.");
        return;
      }
      const reason = typeof failure?.detail?.reason === "string" ? failure.detail.reason : undefined;
      const resetsAt = typeof failure?.detail?.resets_at === "string" ? failure.detail.resets_at : undefined;
      const block: AiBlock = { code, ...(reason ? { reason } : {}), ...(resetsAt ? { resetsAt } : {}) };
      if (AI_STOPPING.has(code)) s().blockAi(block);
      const allowance = s().allowance;
      const known = code.startsWith("AI_") || code === "RATE_LIMITED" || code === "NETWORK";
      const explanation = code === "NETWORK" ? "I can't reach DoMe's service right now." : known ? aiErrorText(block, allowance) : ERRORS.AI_UNAVAILABLE!.user_message;
      // the voice says when the limit refreshes the way the screen does ("today at 2 pm"), never a date the phone invents
      const resets = resetsAt ?? allowance?.resets_at ?? null;
      const renews = resets && !Number.isNaN(Date.parse(resets)) ? lowerFirst(refreshText(resets)).replace(/^refreshes /, "") : null;
      const voice = voiceForAiError(known ? code : "AI_UNAVAILABLE", { reason: reason ?? allowance?.exhausted ?? undefined, renews });
      // the error is the whole answer; only a part the parser did understand is added ("I understood … but not …")
      const screen = parserMessage === null ? explanation : `${explanation} ${screenForMessage(parserMessage)}`;
      say(screen, parserMessage === null ? voice : `${voice} ${voiceForMessage(parserMessage)}`, { tone: "warning" });
      remember(text, RECENT_AI_ERROR);
      return;
    }
    const ai = { charged: response.charged, chargedFrom: response.charged_from };
    switch (response.mode) {
      case "act": {
        // The reply is ignored: what happens is announced from the validated steps and their real results.
        const again = () => planFromAssistant(response.steps as Parameters<typeof planFromAssistant>[0], ctxNow());
        const parsed = await settleApps(again(), again);
        if (stale(turn)) {
          leave("", ai);
          return;
        }
        if (parsed.kind === "plan") {
          await presentPlan(parsed, turn, ai, text);
          return;
        }
        const message = parsed.kind === "clarify" || parsed.kind === "handoff" ? parsed.message : (parsed.message ?? UNKNOWN_SCREEN);
        say(screenForMessage(message), voiceForMessage(message), { ai, chips: parsed.kind === "clarify" ? parsed.choices.map((c) => ({ ...c, kind: "local" as const })) : [] });
        remember(text, parsed.kind === "clarify" ? recentClarifyLine(parsed.topic) : RECENT_UNKNOWN);
        return;
      }
      case "ask":
      case "answer":
      case "decline": {
        const reply = cleanText(response.reply);
        const chips: ChatChip[] = response.mode === "ask" ? response.choices.map((c) => cleanText(c)).filter(Boolean).map((c) => ({ label: c, text: c, kind: "choice" })) : [];
        say(reply, speakable(reply), { ai, chips, touchpad: response.handoff === "touchpad" && response.mode !== "ask" });
        remember(text, reply);
        return;
      }
    }
  };

  /** A new request: Run cards still waiting from an earlier turn are not run any more ("Not run."). */
  const supersede = () => {
    for (const it of s().items) if (it.kind === "plan" && it.status === "waiting_run") s().updateItem(it.id, { status: "dismissed" });
  };

  const pipeline = async (text: string, source: TurnSource, options: TurnOptions, turn: Turn): Promise<void> => {
    const words = normalizeText(text);
    // 2. a countdown, a waiting Run card or a question first, on the phone: a spoken "yes" never runs or chooses
    //    anything. "Cancel" while a countdown runs is about the countdown, whatever card waits (the parser sends
    //    power.cancel below).
    const countdown = CANCEL.test(words) && pc.powerPending();
    if (!countdown) {
      const waiting = [...s().items].reverse().find((it) => it.kind === "plan" && it.status === "waiting_run");
      if (waiting && YES.test(words)) {
        say("Please tap Run on the screen.", "Please tap Run on the screen.");
        remember(recentOwnerNote(null), "I asked you to tap Run on the screen.");
        return;
      }
      if (waiting && NO.test(words)) {
        s().updateItem(waiting.id, { status: "dismissed" });
        say(DECLINED, DECLINED);
        remember(recentOwnerNote(null), "I left it.");
        return;
      }
      if (!waiting && turn.question && YES.test(words)) {
        say(TAP_YOUR_CHOICE, TAP_YOUR_CHOICE);
        remember(recentOwnerNote(null), "I asked you to tap your choice on the screen.");
        return;
      }
      if (!waiting && turn.question && NO.test(words)) {
        say("Very well.", "Very well.");
        remember(recentOwnerNote(null), "I left it.");
        return;
      }
      if (!waiting && CANCEL.test(words)) {
        say("All right.", "All right.");
        remember(recentOwnerNote(null), "All right.");
        return;
      }
    }
    supersede();
    if (options.forceAi && aiUsable(s()) && source !== "local") {
      await aiTurn(text, source, turn, null);
      return;
    }
    // 3. right after a relaunch the PC's state may not be current yet
    if (pc.problem() === "not_current") {
      s().setPhase("connecting");
      s().setNotice(`Connecting to ${pc.pcName() ?? "your PC"}…`);
      try {
        await pc.waitForControl(CONNECT_WAIT_MS);
      } finally {
        s().setNotice(null);
        s().setPhase("idle");
      }
    }
    // 4. the Type page's parser and context
    const again = () => parseCommand(text, ctxNow());
    const parsed = await settleApps(again(), again);
    if (stale(turn)) {
      leave();
      return;
    }
    const note = (steps: readonly PlanStep[] | null) => recentOwnerNote(steps);
    switch (parsed.kind) {
      case "plan":
        await presentPlan(parsed, turn, null, note(parsed.steps));
        return;
      case "handoff":
        say(screenForMessage(parsed.message), voiceForMessage(parsed.message), { chips: parsed.choices.map((c) => ({ ...c, kind: "local" })), touchpad: true });
        remember(note(null), RECENT_HANDOFF);
        return;
      case "clarify": {
        const chips: ChatChip[] = parsed.choices.map((c) => ({ ...c, kind: "local" }));
        if ((parsed.topic ?? "other") === "other" && aiUsable(s()) && source !== "local") chips.push({ label: "Ask the assistant", text, kind: "ask_ai", source: source === "choice" ? "typed" : source });
        say(screenForMessage(parsed.message), voiceForMessage(parsed.message), { chips, touchpad: parsed.link === "touchpad" });
        remember(note(null), recentClarifyLine(parsed.topic));
        return;
      }
      case "unknown":
        if (source !== "local" && aiUsable(s())) {
          await aiTurn(text, source, turn, parsed.message);
          return;
        }
        unknownLocally(parsed.message, note(null));
        return;
    }
  };

  /** The card's Cancel shut-down / restart / sleep: power.cancel through the plan runner, its result shown and said. */
  const cancelPower = async (itemId: number): Promise<void> => {
    const item = s().items.find((it) => it.id === itemId);
    if (item?.kind !== "plan" || item.cancel?.status === "sending") return;
    const conversation = s().conversation;
    const steps = [CANCEL_COUNTDOWN];
    s().updateItem(itemId, { cancel: { status: "sending", commandId: null, error: null } });
    // its own stopper: Stop on another plan never stops a cancel, and a cancel never waits for another plan
    const run = await pc.runPlan(item.pcId, steps, {
      source: "assistant",
      stopper: createStopper(),
      onUpdate: (r) => {
        const commandId = r.steps[0]?.commandId ?? null;
        if (commandId) s().updateItem(itemId, { cancel: { status: "sending", commandId, error: null } });
      },
    });
    const ran = run.steps[0];
    const record = ran?.commandId ? pc.record(ran.commandId) : null;
    const ok = ran?.status === "succeeded";
    const why = ok ? null : ran?.status === "unknown" ? CONNECTION_DROPPED : pc.failureText(ran?.failure?.code ?? null, ran?.failure?.message ?? "", CANCEL_COUNTDOWN, record);
    s().updateItem(itemId, { cancel: { status: ok ? "done" : "failed", commandId: ran?.commandId ?? null, error: why } });
    if (s().conversation !== conversation) return;
    const line = ok ? powerCancelSpeech({ ok: true, canceled: record?.terminal?.result?.canceled === true }) : ran?.status === "unknown" ? CONNECTION_DROPPED : powerCancelSpeech({ ok: false, why: why ?? "" });
    say(line, speakable(line));
    s().pushRecent([{ who: "butler", text: recentPlanLine({ steps, run, records: [record] }) }], now());
  };

  const expireIdle = () => {
    const last = s().lastActivity;
    if (last !== null && now() - last > IDLE_CLEAR_MS) s().clearConversation();
  };

  return {
    async handleText(raw, source, options = {}) {
      const text = normalizeTurnText(raw);
      if (text === "" || busy || s().phase !== "idle") return;
      busy = true;
      try {
        deps.speaker.cancel();
        expireIdle();
        // the turn is activity: the conversation is never cleared as idle while it runs
        s().touch(now());
        const last = s().items.at(-1);
        const question = last?.kind === "butler" && last.chips.some((c) => c.kind === "local");
        s().addItem({ kind: "owner", text });
        const turn: Turn = { start: options.endedAt ?? now(), hidden: !visibility.visible(), unfinished: ENDS_ON_JOINER.test(raw.trim()), fromChip: !!options.fromChip, pcId: pc.pcId(), conversation: s().conversation, question };
        const off = visibility.subscribe((visible) => {
          if (!visible) turn.hidden = true;
        });
        try {
          await pipeline(text, source, options, turn);
        } finally {
          off();
          s().touch(now());
        }
      } finally {
        busy = false;
      }
    },
    async run(itemId) {
      if (busy || s().phase !== "idle") return;
      busy = true;
      try {
        await runItem(itemId);
      } finally {
        busy = false;
      }
    },
    dismiss(itemId) {
      const item = s().items.find((it) => it.id === itemId);
      if (item?.kind === "plan" && item.status === "waiting_run") s().updateItem(itemId, { status: "dismissed" });
    },
    stop() {
      stopper.stop();
    },
    cancelTurn() {
      if (!inflight) return;
      inflight.cancelled = true;
      inflight.abort.abort();
    },
    cancelPower,
    hear(itemId) {
      const item = s().items.find((it) => it.id === itemId);
      if (item?.kind !== "butler" || !item.spoken) return;
      s().updateItem(itemId, { tapToHear: false });
      speak(item.spoken, itemId, true);
    },
    expireIdle,
  };
}

// --- the live implementation of PcAccess -------------------------------------------------------------------------------

/** PcAccess over the app's stores and runtime, for the selected PC. */
export function livePcAccess(): PcAccess {
  const selected = () => useDevicesStore.getState().selectedPcId;
  const apps = () => {
    const id = selected();
    return id ? (useLiveStore.getState().appsByPc[id]?.apps ?? null) : null;
  };
  const fresh = () => {
    const id = selected();
    const live = id ? useLiveStore.getState().pcs[id] : undefined;
    return isFresh(live, Math.max(Date.now(), live?.stateReceivedAt ?? 0));
  };
  const capabilities = () => {
    const id = selected();
    const controllerId = useLiveStore.getState().controllerId;
    const grants = id ? useDevicesStore.getState().grantsByPc[id] : undefined;
    const grant = grants && controllerId ? grants.find((g) => g.controller_id === controllerId) : undefined;
    return grant ? [...grant.capabilities] : null;
  };
  return {
    pcId: selected,
    pcName: () => useDevicesStore.getState().pcs.find((p) => p.id === selected())?.name ?? null,
    problem: () => controlProblem(selected()),
    waitForControl: (ms) => waitForLive(() => controlProblem(selected()) === null, ms),
    view: () => {
      const id = selected();
      return id ? liveView(id) : { state: null };
    },
    fresh,
    intentContext: () => {
      const id = selected();
      return liveIntentContext({ ...(id ? liveView(id) : { state: null }), fresh: fresh(), apps: apps() });
    },
    waitForApps: (ms) => {
      const id = selected();
      if (id) void refreshApprovedApps(id);
      return waitForLive(() => apps() !== null, ms);
    },
    refreshApps: async () => {
      const id = selected();
      if (id) await refreshAppsAndWait(id);
    },
    capabilities,
    powerPending: () => {
      const id = selected();
      return !!(id && useLiveStore.getState().pcs[id]?.state?.pending_power_action);
    },
    previews: (steps) => {
      const id = selected();
      const view = id ? liveView(id) : { state: null };
      return steps.map((st, i) => previewStep(st, i, view, capabilities()));
    },
    runPlan: (pcId, steps, options) => runPlanOnPc(pcId, steps, options),
    record: (commandId) => useLiveStore.getState().commands.find((c) => c.commandId === commandId) ?? getRuntime().commands.get(commandId) ?? null,
    onRecords: (fn) =>
      useLiveStore.subscribe((now, before) => {
        if (now.commands !== before.commands) fn();
      }),
    appName: (appId) => (appId ? (apps()?.find((a) => a.app_id === appId)?.display_name ?? null) : null),
    failureText: (code, message, step, record) => (code === null ? message : errorMessage({ code, message }, { action: step.action, origin: record?.terminal?.origin, target: step.target })),
  };
}

/**
 * What the Assistant says (docs/design/assistant.md §4.6, §4.10): deterministic sentences from the plan, the real
 * results and the persona. No second model call: in mode "act" the model's reply is ignored and every sentence comes
 * from these templates and the PC's validated results.
 *
 * Spoken lines never read out a search query, a video or window title (except "what is playing" asked on its own),
 * a URL, an id or a key name: steps are spoken through `actionPhrase`, which uses only an action and its boolean and
 * number parameters. App display names are spoken on this phone only. Everything spoken goes through `speakable`.
 *
 * `recent` (the short memory sent with a later AI turn) never receives these spoken lines. It receives `recentLine`
 * text from closed templates in which every PC-sourced part is replaced ("The app is open."), so nothing the PC
 * reports and no words of a command DoMe handled itself can reach the AI provider.
 */
import { ERRORS, registry, type ActionName } from "@dome/protocol";

import type { PlanRun } from "./commandPlan.ts";
import type { CommandRecord } from "./commands.ts";
import { CONFIRM_ALONE, DID_YOU_MEAN, HELP_MESSAGE, KEYBOARD_HANDOFF, ONLY_SEARCH_PLAYS, POINTER_HANDOFF, SAME_THING, THAT_ONE_HANDOFF, TOO_MANY_STEPS, UNDERSTOOD_PART, type ClarifyTopic, type PlanStep } from "./intents.ts";

export interface Persona {
  name: string;
  /** "" = none: never sir, madam or a name. */
  address: string;
}

// --- text hygiene ------------------------------------------------------------------------------------------------------

/** Text from a model or a PC as plain text: format characters (bidi, zero-width) removed, line separators as spaces. */
export function cleanText(text: string): string {
  return text
    .replace(/\p{Cf}/gu, "")
    .replace(/[\p{Zl}\p{Zp}\p{Cc}]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const SPEAK_CHARS = /[^\p{L}\p{N} .,'?!%:\-‘’“”]/gu;
export const SCREEN_HAS_DETAILS = "The details are on the screen.";

/**
 * What the speech synthesiser may read: letters, digits, spaces and . , ' ? ! % : - (and curly quotes) only, and at
 * most the first `sentences` sentences (110 characters each on average); a shortened text ends by pointing at the
 * screen. Links, markup, code and emoji never reach the voice.
 */
export function speakable(text: string, sentences = 2): string {
  const plain = cleanText(text).replace(SPEAK_CHARS, " ").replace(/\s+/g, " ").trim();
  if (plain === "") return "";
  const max = 110 * sentences;
  const parts = plain.match(/[^.?!]+(?:[.?!]+|$)/g)?.map((p) => p.trim()).filter(Boolean) ?? [plain];
  let out = "";
  let kept = 0;
  for (const part of parts) {
    if (kept === sentences || (out + " " + part).trim().length > max) break;
    out = `${out} ${part}`.trim();
    kept++;
  }
  if (out === "") {
    const cut = plain.slice(0, max).replace(/\s+\S*$/, "");
    out = `${cut || plain.slice(0, max)}.`;
  }
  return kept === parts.length ? out : `${out} ${SCREEN_HAS_DETAILS}`;
}

/** "Very good." → "Very good, sir." (address before the final full stop; unchanged without an address). */
export function withAddress(sentence: string, address: string): string {
  if (!address) return sentence;
  const m = /^(.*?)([.?!]*)$/.exec(sentence.trim());
  return `${m?.[1] ?? sentence}, ${address}${m?.[2] || "."}`;
}

// --- action phrases ----------------------------------------------------------------------------------------------------

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** 90 → "1:30". */
export function clockWords(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/**
 * A short verb phrase for a step ("pause YouTube", "set the PC volume to 40", "open Spotify"). Uses only the action
 * and its boolean and number params, never a string param (a search query), a target or a title. `appName` is the
 * PC's display name for the step's app; without it (always for `recent`) the app is "the app".
 */
export function actionPhrase(step: { action: string; params: Record<string, unknown> }, appName?: string | null): string {
  const p = step.params;
  const app = appName || "the app";
  if (!registry.has(step.action)) return "do that";
  const name: ActionName = step.action;
  switch (name) {
    case "app.close":
      return `close ${app}`;
    case "app.focus":
      return `bring ${app} to the front`;
    case "app.launch":
      return `open ${app}`;
    case "app.list":
      return "list your apps";
    case "app.maximize":
      return `maximise ${app}`;
    case "app.minimize":
      return `minimise ${app}`;
    case "app.restore":
      return `restore ${app}`;
    case "app.set_fullscreen":
      return p.enabled ? `make ${app} full screen` : `take ${app} out of full screen`;
    case "media.get_sessions":
      return "list the media players";
    case "media.next":
      return "skip to the next track";
    case "media.previous":
      return "go back a track";
    case "media.set_paused":
      return p.paused ? "pause the player" : "play the player";
    case "power.cancel":
      return "cancel the countdown";
    case "power.restart":
      return "restart the PC";
    case "power.shutdown":
      return "shut down the PC";
    case "power.sleep":
      return "put the PC to sleep";
    case "system.get_status":
      return "check the PC's status";
    case "system.ping":
      return "check the connection";
    case "windows.get_volume":
      return "check the volume";
    case "windows.lock":
      return "lock the PC";
    case "windows.set_muted":
      return p.muted ? "mute the PC" : "unmute the PC";
    case "windows.set_volume":
      return `set the PC volume to ${num(p.value)}`;
    case "youtube.get_state":
      return "check YouTube";
    case "youtube.list_tabs":
      return "list the YouTube tabs";
    case "youtube.next":
      return "skip to the next video";
    case "youtube.previous":
      return "go back a video";
    case "youtube.open_result":
      return `play result ${num(p.index)}`;
    case "youtube.request_fullscreen":
      return "make the video full screen";
    case "youtube.search":
      return "search YouTube";
    case "youtube.seek_relative":
      return num(p.seconds) < 0 ? `go back ${Math.abs(num(p.seconds))} seconds` : `skip ahead ${num(p.seconds)} seconds`;
    case "youtube.seek_to":
      return num(p.position_seconds) === 0 ? "go back to the start" : `jump to ${clockWords(num(p.position_seconds))}`;
    case "youtube.set_muted":
      return p.muted ? "mute YouTube" : "unmute YouTube";
    case "youtube.set_paused":
      return p.paused ? "pause YouTube" : "play YouTube";
    case "youtube.set_theater":
      return p.enabled ? "turn theater mode on" : "turn theater mode off";
    case "youtube.set_volume":
      return `set YouTube's volume to ${num(p.value)}`;
    case "input.session_start":
    case "input.session_stop":
      return "use the touchpad";
  }
}

/** Spoken while a plan waits for Run: a statement, never a question, so nobody answers it aloud. */
export function runPrompt(steps: readonly PlanStep[], appName: (step: PlanStep) => string | null): string {
  const shown = steps.filter((s) => !s.added);
  if (shown.length === 1) return `Ready to ${actionPhrase(shown[0]!, appName(shown[0]!))}. Tap Run.`;
  return `Ready to do these ${steps.length} things. Tap Run.`;
}

// --- result sentences --------------------------------------------------------------------------------------------------

/** A player's label is spoken only when it is a plain word ("Spotify"); a Store app id is "The player". */
export function playerName(label: unknown): string {
  const plain = typeof label === "string" ? label.replace(/\.exe$/i, "").trim() : "";
  return /^[A-Za-z][A-Za-z0-9 ]{0,20}$/.test(plain) ? plain : "The player";
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const POWER_SOON: Record<string, string> = { "power.sleep": "sleep", "power.restart": "restart", "power.shutdown": "shut down" };
const POWER_NOW: Record<string, string> = { "power.sleep": "going to sleep", "power.restart": "restarting", "power.shutdown": "shutting down" };

/** Which way to say a step's result: on this phone (with the PC's names) or for `recent` (every PC-sourced part replaced). */
export interface SentenceOptions {
  /** The PC's display name for the step's app; ignored for `recent`. */
  appName?: string | null;
  /** The step is the plan's only one (youtube.get_state may then say what is playing). */
  alone?: boolean;
  forRecent?: boolean;
}

/** The sentence for one succeeded step, from its validated result (§4.6 table; §4.10 for `recent`). */
export function stepSentence(step: { action: string; params: Record<string, unknown> }, record: CommandRecord | null, opts: SentenceOptions = {}): string {
  const r = (record?.terminal?.state === "succeeded" ? record.terminal.result : null) ?? {};
  const p = step.params;
  const App = opts.forRecent ? "The app" : opts.appName || "The app";
  const tab = (r.tab ?? {}) as { paused?: boolean; volume?: number; title?: string; position_seconds?: number };
  if (!registry.has(step.action)) return "Done.";
  const name: ActionName = step.action;
  switch (name) {
    case "youtube.set_paused":
      return (tab.paused ?? p.paused) ? "YouTube is paused." : "YouTube is playing.";
    case "youtube.next":
      return "Next video.";
    case "youtube.previous":
      return "Previous video.";
    case "youtube.seek_relative":
      return num(p.seconds) < 0 ? `Back ${Math.abs(num(p.seconds))} seconds.` : `Skipped ahead ${num(p.seconds)} seconds.`;
    case "youtube.seek_to":
      return num(p.position_seconds) === 0 ? "Back to the start." : `Now at ${clockWords(num(p.position_seconds))}.`;
    case "youtube.set_muted":
      return p.muted ? "YouTube is muted." : "YouTube's sound is back on.";
    case "youtube.set_volume":
      return `YouTube's volume is at ${typeof tab.volume === "number" ? tab.volume : num(p.value)} per cent.`;
    case "youtube.set_theater":
      return p.enabled ? "Theater mode is on." : "Theater mode is off.";
    case "youtube.request_fullscreen":
      return "The video is full screen.";
    case "youtube.search": {
      const n = Array.isArray(r.results) ? r.results.length : 0;
      if (n === 0) return "YouTube found nothing for that.";
      return opts.forRecent ? `I've found ${plural(n, "video", "videos")}.` : `I've found ${plural(n, "video", "videos")}. Tap one, or tap the microphone and say 'play the first one'.`;
    }
    case "youtube.open_result":
      return `Playing result ${num(p.index)}.`;
    case "youtube.list_tabs": {
      const n = Array.isArray(r.tabs) ? r.tabs.length : 0;
      return n === 1 ? "1 YouTube tab is open." : `${n} YouTube tabs are open.`;
    }
    case "youtube.get_state": {
      const playing = tab.paused === false;
      if (opts.alone && !opts.forRecent && tab.title) {
        const title = [...cleanText(tab.title)].slice(0, 80).join("");
        return `${playing ? "Playing" : "Paused"}: ${title}, at ${clockWords(num(tab.position_seconds))}.`;
      }
      return `YouTube is ${playing ? "playing" : "paused"}.`;
    }
    case "media.set_paused": {
      const session = (r.session ?? {}) as { app_label?: string };
      const who = opts.forRecent ? "The player" : playerName(session.app_label);
      return p.paused ? `${who} is paused.` : `${who} is playing.`;
    }
    case "media.next":
      return "Next track.";
    case "media.previous":
      return "Previous track.";
    case "media.get_sessions": {
      const n = Array.isArray(r.sessions) ? r.sessions.length : 0;
      return n === 1 ? "1 media player is open." : `${n} media players are open.`;
    }
    case "windows.get_volume":
      return `The volume is at ${num(r.value)} per cent${r.muted ? ", and muted" : ""}.`;
    case "windows.set_volume":
      return `The volume is at ${num(r.value ?? p.value)} per cent.`;
    case "windows.set_muted":
      return p.muted ? "The PC is muted." : "The PC's sound is back on.";
    case "windows.lock":
      return "The PC is locked.";
    case "system.ping":
      return "Your PC is connected.";
    case "system.get_status":
      return r.session_locked ? "Your PC is online and locked." : "Your PC is online.";
    case "app.list": {
      const apps = Array.isArray(r.apps) ? (r.apps as Array<{ display_name?: unknown }>) : [];
      if (apps.length === 0) return "No apps are approved on the PC yet.";
      if (opts.forRecent) return `I listed ${plural(apps.length, "app", "apps")} on the screen.`;
      const names = apps.slice(0, 5).map((a) => cleanText(String(a.display_name ?? ""))).filter(Boolean);
      const more = apps.length - names.length;
      const list = more > 0 ? `${names.join(", ")} and ${more} more` : names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : (names[0] ?? "");
      return `You can open ${list}.`;
    }
    case "app.launch":
      return r.launched === false && r.running === true ? `${App} was already open.` : `${App} is open.`;
    case "app.focus":
      return `${App} is in front.`;
    case "app.minimize":
      return `${App} is minimised.`;
    case "app.maximize":
      return `${App} is maximised.`;
    case "app.restore":
      return `${App} is back to its normal size.`;
    case "app.set_fullscreen":
      return p.enabled ? `${App} is full screen.` : `${App} has left full screen.`;
    case "app.close":
      return `${App} is closed.`;
    case "power.sleep":
    case "power.restart":
    case "power.shutdown": {
      const n = typeof r.countdown_seconds === "number" ? r.countdown_seconds : num(p.countdown_seconds);
      return n > 0 ? `The PC will ${POWER_SOON[name]} in ${n} seconds. Tap Cancel to stop it.` : `The PC is ${POWER_NOW[name]} now.`;
    }
    case "power.cancel":
      return r.canceled ? "Cancelled." : "Nothing was counting down.";
    case "input.session_start":
    case "input.session_stop":
      return "Done.";
  }
}

// --- whole plans ---------------------------------------------------------------------------------------------------------

export const STOPPED = "Stopped.";
export const DECLINED = "Very well, I've left it.";
export const CONFIRMATION_TIMED_OUT = "The confirmation timed out, so nothing happened.";
export const CONNECTION_DROPPED = "I'm not sure that worked: the connection dropped. Please check the PC before trying again.";
const OPENERS = ["Very good.", "Certainly.", "Done."] as const;
/** Steps whose sentence is itself the acknowledgement ("Cancelled."): no opener before it. */
const NO_OPENER: ReadonlySet<string> = new Set(["power.cancel"]);

export interface PlanOutcome {
  steps: readonly PlanStep[];
  run: PlanRun;
  /** The settled record per step (null when it was never sent). */
  records: ReadonlyArray<CommandRecord | null>;
}

/** Only reads (status, volume, lists): answered without an opener, the address before the last full stop. */
function statusOnly(steps: readonly PlanStep[]): boolean {
  return steps.every((s) => registry.has(s.action) && registry.get(s.action).capability === "status");
}

/** How a finished plan ended, for both the spoken sentence and `recent`. */
type Ending = { kind: "done" } | { kind: "stopped" } | { kind: "dropped" } | { kind: "declined" } | { kind: "expired" } | { kind: "failed"; index: number; code: string | null; message: string; later: boolean };

function ending(o: PlanOutcome): Ending {
  const { run } = o;
  if (run.stopped) return { kind: "stopped" };
  const unknown = run.steps.findIndex((s) => s.status === "unknown");
  if (unknown >= 0) return { kind: "dropped" };
  const at = run.failedAt;
  if (at === null) return { kind: "done" };
  const failure = run.steps[at]?.failure;
  const code = failure?.code ?? null;
  if (code === "CONFIRMATION_DECLINED") return { kind: "declined" };
  if (code === "CONFIRMATION_EXPIRED") return { kind: "expired" };
  if (code === "OUTCOME_UNKNOWN" || failure?.mayHaveRun) return { kind: "dropped" };
  return { kind: "failed", index: at, code, message: failure?.message ?? "", later: at < o.steps.length - 1 };
}

export interface SpeechOptions {
  persona: Persona;
  /** The PC's display name for a step's app (spoken on this phone only). */
  appName(step: PlanStep): string | null;
  /** A per-page counter: the opener rotates "Very good", "Certainly", "Done". */
  turn: number;
  /** "Certainly." was already said while the plan ran: no opener now. */
  certaintySaid: boolean;
  /** On-screen text for a failure (errorMessage with the step's context). */
  failureText(code: string | null, message: string, step: PlanStep, record: CommandRecord | null): string;
}

/** A failure's on-screen sentence as the end of "I'm afraid I couldn't …: {it}" (one sentence, lower-case start). */
function becauseClause(text: string): string {
  return speakable(text, 1).replace(/^(?:The|This|That|There|No|Nothing|A|An|Your|It)\b/, (w) => w.toLowerCase());
}

/** What the butler says once a plan has finished, from the real results (§4.6). */
export function planSpeech(o: PlanOutcome, opts: SpeechOptions): string {
  const done = o.steps.map((s, i) => (o.run.steps[i]?.status === "succeeded" ? stepSentence(s, o.records[i] ?? null, { appName: opts.appName(s), alone: o.steps.length === 1 }) : null)).filter((x): x is string => x !== null);
  const end = ending(o);
  const address = opts.persona.address;
  switch (end.kind) {
    case "stopped":
      return [...done, STOPPED].join(" ");
    case "dropped":
      return [...done, CONNECTION_DROPPED].join(" ");
    case "declined":
      return withAddress(DECLINED, address);
    case "expired":
      return CONFIRMATION_TIMED_OUT;
    case "failed": {
      const step = o.steps[end.index]!;
      const line = `I'm afraid I couldn't ${actionPhrase(step, opts.appName(step))}: ${becauseClause(opts.failureText(end.code, end.message, step, o.records[end.index] ?? null))}`;
      return [...done, line, ...(end.later ? ["I stopped there."] : [])].join(" ");
    }
    case "done": {
      if (done.length === 0) return withAddress("Done.", address);
      if (statusOnly(o.steps) || o.steps.every((s) => NO_OPENER.has(s.action))) return [...done.slice(0, -1), withAddress(done.at(-1)!, address)].join(" ");
      if (opts.certaintySaid) return done.join(" ");
      const opener = withAddress(OPENERS[opts.turn % OPENERS.length]!, address);
      return [opener, ...done].join(" ");
    }
  }
}

/** Said once when the first result has not arrived 1 s after sending. */
export function certainly(persona: Persona): string {
  return withAddress("Certainly.", persona.address);
}

// --- voice copy ----------------------------------------------------------------------------------------------------------

export const POINTER_VOICE = "I can't see your screen, so I can't click things for you. The Touchpad can, or for YouTube results say 'play the first video'.";
export const KEYBOARD_VOICE = "I can't type or press keys on the PC. The keyboard on the Touchpad page can.";
export const UNKNOWN_VOICE = "I'm sorry, I didn't understand that. Tap the microphone and try saying it another way.";
/** On screen for text the parser does not know when no AI turn follows. */
export const UNKNOWN_SCREEN = "DoMe didn't understand that. Try saying it another way, or use the buttons in the remote.";
/** The Assistant's own screen text for the Type page's help ("things you can say", not "type"). */
export const HELP_SCREEN = "Here are some things you can say. You can also join up to three with “and” or “then”.";
export const HELP_VOICE = "Here are a few things you can ask me. You can join up to three with 'and' or 'then'.";
/** A spoken "yes" after a clarification with choices: a choice is only ever made by a tap. */
export const TAP_YOUR_CHOICE = "Please tap your choice on the screen.";
/** A turn whose PC was switched (or whose conversation was cleared) before it finished: nothing of it is used. */
export const SWITCHED_PC = "You switched PCs, so I've left that request.";

/**
 * Spoken statements for the Type page's sentences (§4.6), by message constant: a clarification that is a question on
 * the Type page ("Did you mean Notepad?") is said as a statement, so nobody answers it aloud, and a message that quotes
 * the owner's words (a search query, a step's text) is said without them.
 */
const MESSAGE_VOICE: ReadonlyArray<readonly [test: (message: string) => boolean, voice: (message: string) => string]> = [
  [(m) => m === POINTER_HANDOFF || m.startsWith(THAT_ONE_HANDOFF), () => POINTER_VOICE],
  [(m) => m === KEYBOARD_HANDOFF, () => KEYBOARD_VOICE],
  [(m) => m === HELP_MESSAGE, () => HELP_VOICE],
  [(m) => m.endsWith(ONLY_SEARCH_PLAYS), () => "I only play videos by searching YouTube. Tap the suggestion to search for that."],
  [
    (m) => m.startsWith(DID_YOU_MEAN) && m.endsWith("?"),
    (m) => {
      // the names are the PC's approved apps, spoken on this phone only
      const names = speakable(m.slice(DID_YOU_MEAN.length, -1), 1).replace(/[.?!]+$/, "");
      return / or /.test(names) ? `I think you meant ${names}. Tap the one you want on the screen.` : `I think you meant ${names}. Tap it on the screen to go ahead.`;
    },
  ],
  [(m) => m.endsWith(SAME_THING), () => `Two of those steps control the same thing. ${SCREEN_HAS_DETAILS}`],
  [(m) => m.endsWith(CONFIRM_ALONE), () => "One of those steps needs your confirmation on the phone, so please ask for it on its own."],
  [(m) => m === TOO_MANY_STEPS, () => "I can do up to three things at a time. Please ask for the rest separately."],
  [(m) => m.startsWith(UNDERSTOOD_PART), () => `I understood part of that, but not all of it. ${SCREEN_HAS_DETAILS}`],
];

/** What to say for a parser message shown on screen (handoff, clarification, unknown). */
export function voiceForMessage(message: string | null): string {
  if (message === null) return UNKNOWN_VOICE;
  const spoken = MESSAGE_VOICE.find(([test]) => test(message));
  return spoken ? spoken[1](message) : speakable(message);
}

/** What the Assistant shows for a parser message (the Type page's own wording, except where it says "type"). */
export function screenForMessage(message: string): string {
  return message === HELP_MESSAGE ? HELP_SCREEN : message;
}

const STILL_WORKS = "I can still do the things I know by myself.";

/** Spoken lines for the errors of an AI turn (§4.6), by error code; each says what happened, never "say it another way". */
const ERROR_VOICE: Readonly<Record<string, string>> = {
  AI_UNAVAILABLE: "The assistant didn't answer, so nothing was done. Please try again, or use the buttons.",
  NETWORK: "I'm afraid I can't reach DoMe's service just now. Please try again in a moment.",
  RATE_LIMITED: "Too many requests just now. Please try again in a moment.",
  AI_BUSY: "I'm still answering your last request. Please try again in a moment.",
  AI_SPEND_LIMIT_REACHED: `I'm afraid the AI assistant is paused until next month. ${STILL_WORKS}`,
  AI_NOT_AVAILABLE: `I'm afraid the AI assistant isn't available on your account. ${STILL_WORKS}`,
  AI_CONSENT_REQUIRED: `The AI assistant needs to be turned on in Settings first. ${STILL_WORKS}`,
};

/**
 * What to say for an AI turn's error code. `reason` and `renews` (a date already formatted for this phone) describe
 * a reached allowance; any code without its own line is spoken as AI_UNAVAILABLE.
 */
export function voiceForAiError(code: string, limit: { reason?: string | undefined; renews?: string | null } = {}): string {
  if (code === "AI_ALLOWANCE_EXHAUSTED") {
    const paused = limit.reason === "failures";
    const what = paused ? "I'm afraid the AI assistant is paused after too many failed requests this month." : "I'm afraid this month's AI requests are used up.";
    const when = limit.renews ? (paused ? ` It starts again on ${limit.renews}.` : ` They renew on ${limit.renews}.`) : "";
    return `${what}${when} ${STILL_WORKS}`;
  }
  return ERROR_VOICE[code] ?? ERROR_VOICE.AI_UNAVAILABLE!;
}

/** The Cancel shut-down / restart / sleep button's result, said aloud (§4.2). */
export function powerCancelSpeech(outcome: { ok: true; canceled: boolean } | { ok: false; why: string }): string {
  if (outcome.ok) return outcome.canceled ? "Cancelled." : "Nothing was counting down.";
  return `I'm afraid I couldn't cancel it: ${becauseClause(outcome.why)}`;
}

// --- recent (sent with a later AI turn) -----------------------------------------------------------------------------------

/** assistant_text bounds: printable and at most 400 characters. */
export function recentText(text: string): string {
  return [...cleanText(text)].slice(0, 400).join("");
}

/** The owner's line for a turn DoMe handled itself: what it did, never the words. */
export function recentOwnerNote(steps: readonly PlanStep[] | null): string {
  if (!steps || steps.length === 0) return "(DoMe answered this itself)";
  return recentText(`(DoMe handled this itself: ${steps.map((s) => actionPhrase(s)).join(", ")})`);
}

const TOPIC_LINE: Record<ClarifyTopic, string> = {
  app: "I asked which app you meant.",
  player: "I asked whether you meant YouTube or the other player.",
  volume_scope: "I asked whether you meant the PC's volume or YouTube's.",
  volume_unknown: "I asked for a volume number.",
  safety: "I asked you to put it another way.",
  other: "I asked you to put it another way.",
};

export function recentClarifyLine(topic: ClarifyTopic | undefined): string {
  return TOPIC_LINE[topic ?? "other"];
}

export const RECENT_HANDOFF = "I said the Touchpad can do that.";
export const RECENT_UNKNOWN = "I said I didn't understand.";
export const RECENT_AI_ERROR = "The assistant didn't answer.";

/** The butler's `recent` line for a finished plan: result lines with every PC-sourced part replaced (§4.10). */
export function recentPlanLine(o: PlanOutcome): string {
  const done = o.steps.map((s, i) => (o.run.steps[i]?.status === "succeeded" ? stepSentence(s, o.records[i] ?? null, { forRecent: true, alone: o.steps.length === 1 }) : null)).filter((x): x is string => x !== null);
  const end = ending(o);
  let tail: string[] = [];
  switch (end.kind) {
    case "stopped":
      tail = [STOPPED];
      break;
    case "dropped":
      tail = [CONNECTION_DROPPED];
      break;
    case "declined":
      return DECLINED;
    case "expired":
      return CONFIRMATION_TIMED_OUT;
    case "failed": {
      // a contract string for the code, never the agent's own text or a step label
      const known = end.code ? ERRORS[end.code]?.user_message : undefined;
      tail = [`I couldn't ${actionPhrase(o.steps[end.index]!)}${known ? `: ${known}` : "."}`];
      break;
    }
    case "done":
      if (done.length === 0) tail = ["Done."];
  }
  return recentText([...done, ...tail].join(" "));
}

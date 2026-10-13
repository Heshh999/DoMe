/**
 * Deterministic typed commands (spec §13). No language model is involved: the text is normalised, split into at
 * most three steps — only where the words after "and", "then" or a comma are themselves a command, so "search
 * YouTube for tom and jerry" stays one step — and each step is matched against an ordered rule table. Anything that
 * does not match is `unknown` (naming the part that was not understood) or a clarification whose chips only fill
 * the text box; nothing is ever guessed or sent to a fallback the preview did not show. Media titles and other
 * remote text never reach this parser; it only sees what the customer typed, and even that can never become a path,
 * argument or shell text — the output is registry action names plus parameters validated against the contract.
 *
 * The optional AI assistant (docs/design/assistant.md §4.4) proposes steps in the same shape the grammar produces —
 * an action, its params and an app name as spoken — and `planFromAssistant` turns them into the same `PlanStep`s with
 * the same code (app names resolved against the PC's approved apps here, never by the model), then the same
 * plan-level checks (`assemblePlan`). Every step keeps a `text` that parses back to it (`commandText`).
 */
import { registry, type ActionName, type JsonValue } from "@dome/protocol";

import { APP_NAME, appNameKey, appPhrase, isBrowserApp, matchApp, strongMatch } from "./appMatch.ts";
import { mmss } from "./format.ts";
import { normalizeParts, ordinalWord, parseDuration, parseLevel, parseNumberWords, parseOrdinal, parsePosition, unquote } from "./intentWords.ts";

export { normalizeText, parseNumberWords } from "./intentWords.ts";

export interface IntentApp {
  app_id: string;
  /** Untrusted display name from the PC's `app.list`. */
  display_name: string;
  running?: boolean;
  /** The app's open windows (an app can run with none: Edge's Startup boost, a player closed to the tray). */
  windows?: ReadonlyArray<{ foreground?: boolean }>;
}

export interface PlaybackSide {
  /** A target would resolve (a controllable YouTube tab; a Windows media player). */
  available: boolean;
  playing: boolean;
}

export interface IntentContext {
  /** Approved apps from the PC's `app.list`; null/undefined until the PC answered. */
  apps?: ReadonlyArray<IntentApp> | null;
  /** Last known Windows volume (for "volume up/down"). */
  pcVolume?: number | null;
  /** Last known YouTube player volume of the tab that would be targeted. */
  youtubeVolume?: number | null;
  /**
   * Who a bare "pause", "play" or "next" would reach: the YouTube tab DoMe controls, and Windows media players other
   * than the browsers' own sessions (a browser's media session is the YouTube tab itself). Undefined = YouTube.
   */
  playback?: { youtube: PlaybackSide; media: PlaybackSide & { label: string | null } } | null;
  /** Browser families with the DoMe extension connected (for "make the browser full screen instead"). */
  browsers?: ReadonlyArray<"chrome" | "edge" | "unknown">;
  /** The last YouTube search on this PC: its results (untrusted titles; never the query). */
  lastSearch?: { results: ReadonlyArray<{ index: number; video_id: string; title: string }> } | null;
  /**
   * A video playing in another YouTube tab, which would keep playing under a result opened from a search: for a
   * search in this command (`afterSearch`: any tab but the one the search reuses) and for the last search's results
   * (`lastResults`). Its title is untrusted text, shown as text. null/undefined = nothing else is playing.
   */
  playingElsewhere?: { afterSearch: PlayingTab | null; lastResults: PlayingTab | null } | null;
  /**
   * The approved app the conversation's most recent app step acted on (the Assistant keeps it on the phone only):
   * "maximise it" with no app earlier in the same command means this app. Ignored unless it is still approved.
   */
  lastAppId?: string | null;
}

export interface PlayingTab {
  title: string | null;
}

/**
 * Where a step's target comes from when it is not fixed by the parser: the YouTube tab DoMe controls, a Windows
 * media player, a browser for `youtube.search`, the results tab of the last search, or the tab playing a video other
 * than the one the following result opens in (all chosen at send time).
 */
export type TargetSource = "youtube" | "media" | "youtube_browser" | "search_results" | "playing_elsewhere";

export interface PlanStep {
  action: ActionName;
  params: Record<string, JsonValue>;
  /** Fixed target (an approved app's window); null when it comes from live state (`needsTarget`) or there is none. */
  target: Record<string, JsonValue> | null;
  needsTarget: TargetSource | null;
  /**
   * The target (and, for `youtube.open_result`, the video id) comes from the previous step's result: the tab a
   * search opened, or the tab the previous YouTube step acted on.
   */
  fromPrevious: boolean;
  /** The PC asks for confirmation on the phone before running this. */
  confirmation: boolean;
  /** One more line for the preview ("up 10 from 40%", a result title). Never sent. */
  detail: string | null;
  /** Something the customer should know before sending (e.g. the app did not seem to be running). */
  warning: string | null;
  /** The approved app this step acts on (so "it" in the next step means the same app). */
  appId: string | null;
  /** What the customer typed for this step (their capitalisation), for messages. */
  text: string;
  /**
   * Added by DoMe rather than typed (pausing the video playing in another tab before a result opens). It is listed
   * in the preview like every step, but does not count toward the three steps a command may have.
   */
  added: boolean;
}

/** A tap target under a clarification: it only fills the text box with `text` (never sends). */
export interface Choice {
  label: string;
  text: string;
}

/**
 * What a clarification asks about. The Assistant keeps every clarification on the phone (only the phone knows the
 * PC's apps and players), remembers only this closed topic of it, and offers "Ask the assistant" for `other` alone.
 * Absent = `other`, so a new message never puts its text anywhere by default.
 */
export type ClarifyTopic = "app" | "player" | "volume_scope" | "volume_unknown" | "safety" | "other";

export type CommandParse =
  | { kind: "plan"; steps: PlanStep[]; choices: Choice[] }
  | {
      kind: "clarify";
      message: string;
      choices: Choice[];
      /** A button to the Touchpad page when the honest answer includes "do it by hand". */
      link: "touchpad" | null;
      /** Set when an app name matched nothing: the page re-reads the PC's app list once before trusting this. */
      missingApp?: string;
      /** The approved-app list has not arrived yet. */
      appsUnknown?: boolean;
      /** What it asks about (absent = "other"). */
      topic?: ClarifyTopic;
    }
  /** DoMe cannot do this with a command (it cannot see the screen or type); the Touchpad can. */
  | { kind: "handoff"; message: string; choices: Choice[] }
  | { kind: "unknown"; message: string | null };

/** Longest text the page accepts, longest single step, most steps in one plan. */
export const MAX_COMMAND_CHARS = 200;
const MAX_STEP_CHARS = 120;
export const MAX_PLAN_STEPS = 3;

interface Piece {
  text: string;
  display: string;
  /** What the customer typed for `text.slice(a, b)` (see `normalizeParts`): search queries come from here. */
  typed(a: number, b: number): string;
  ctx: IntentContext;
  prev: PlanStep | null;
}

type PieceResult = { kind: "steps"; steps: PlanStep[]; choices: Choice[] } | Exclude<CommandParse, { kind: "plan" } | { kind: "unknown" }>;
type Rule = (p: Piece) => PieceResult | null;

const UNKNOWN: CommandParse = { kind: "unknown", message: null };

/**
 * Text that never becomes a command, whatever else it says: instructions aimed at an assistant, links and shell or
 * markup characters. Typed commands are short and plain; a part with anything like this outside a search query is
 * refused. A search query itself is free text: it only ever becomes the search_query value of a YouTube results URL
 * on the PC (no model reads it), so "IKEA assembly instructions" or "<3 songs" are searched as typed.
 */
const INJECTION = /(ignore|instruction|disregard|override|system prompt|https?:|www\.|<|>|\{|\}|`|\$\(|;|&&|\|\|)/i;
const INJECTION_MESSAGE = "DoMe commands can't contain links, code or symbols such as ; < > { }, or words like “ignore” or “instructions”, except in a YouTube search. Rephrase it.";

/** Search steps whose query runs to the end of the words they were parsed from ("search YouTube for …"). */
const OPEN_ENDED = new WeakSet<PlanStep>();

function step(
  p: Piece,
  name: ActionName,
  params: Record<string, JsonValue>,
  opts: { target?: Record<string, JsonValue>; needsTarget?: TargetSource; fromPrevious?: boolean; detail?: string; warning?: string; appId?: string } = {},
): PlanStep {
  const spec = registry.get(name);
  const validated = registry.validateParams(name, params) as Record<string, JsonValue>;
  const target = opts.target ? (registry.validateTarget(name, opts.target) as Record<string, JsonValue>) : null;
  const appId = opts.appId ?? (typeof validated.app_id === "string" ? validated.app_id : typeof target?.app_id === "string" ? target.app_id : null);
  return { action: name, params: validated, target, needsTarget: opts.needsTarget ?? null, fromPrevious: opts.fromPrevious ?? false, confirmation: spec.confirmation === "challenge", detail: opts.detail ?? null, warning: opts.warning ?? null, appId, text: p.display, added: false };
}

const steps = (list: PlanStep[], choices: Choice[] = []): PieceResult => ({ kind: "steps", steps: list, choices });
const clarify = (message: string, choices: Choice[] = [], extra: { link?: "touchpad"; missingApp?: string; appsUnknown?: boolean; topic?: ClarifyTopic } = {}): PieceResult => ({
  kind: "clarify",
  message,
  choices,
  link: extra.link ?? null,
  ...(extra.missingApp !== undefined ? { missingApp: extra.missingApp } : {}),
  ...(extra.appsUnknown ? { appsUnknown: true } : {}),
  ...(extra.topic && extra.topic !== "other" ? { topic: extra.topic } : {}),
});
const APP: { topic: ClarifyTopic } = { topic: "app" };
const VOLUME_SCOPE: { topic: ClarifyTopic } = { topic: "volume_scope" };
const VOLUME_UNKNOWN: { topic: ClarifyTopic } = { topic: "volume_unknown" };
const SAFETY: { topic: ClarifyTopic } = { topic: "safety" };
const handoff = (message: string, choices: Choice[] = []): PieceResult => ({ kind: "handoff", message, choices });
const choice = (label: string, text: string): Choice => ({ label, text });

/** A capture group with the customer's capitalisation (needs the `d` flag on the regex); else the lowercase text. */
function shown(p: Piece, m: RegExpExecArray, group: string): string {
  const at = m.indices?.groups?.[group];
  return at ? p.display.slice(at[0], at[1]) : (m.groups?.[group] ?? "");
}

/** A capture group exactly as typed (no "%" or "'s" rewrites; needs the `d` flag): for search queries. */
function typedGroup(p: Piece, m: RegExpExecArray, group: string): string {
  const at = m.indices?.groups?.[group];
  return at ? p.typed(at[0], at[1]) : (m.groups?.[group] ?? "");
}

/** The same piece with other words (a rewrite DoMe made, e.g. "maximise" → "maximise it"): nothing typed to slice. */
function reworded(p: Piece, text: string, display: string = text): Piece {
  return { ...p, text, display, typed: (a, b) => display.slice(a, b) };
}

// --- shared vocabulary -----------------------------------------------------------------------------------------------

const DET = String.raw`(?:the |my |your )?`;
const PC_WORDS = String.raw`pc|computer|laptop|windows|system|master|speakers?|main`;
const YT_WORDS = String.raw`youtube(?: player| video| tab)?|video|player|tab`;
const MEDIA_WORDS = String.raw`music|spotify|songs?|media(?: player)?|tracks?`;
const SCOPE = `(?:${YT_WORDS}|${MEDIA_WORDS}|${PC_WORDS})`;
const VOL = String.raw`(?:volume|sound|audio)`;
/** "the volume", "my pc volume", "the volume on youtube", "the sound of the pc". */
const VOLP = `${DET}(?:${SCOPE} )?${VOL}(?: level)?(?: (?:of|on|for|in) ${DET}${SCOPE})?`;
const DIRP = String.raw`(?:(?<all>all the way) )?(?<dir>up|down)`;
const MOD = String.raw`a (?:little )?bit|a little|a touch|a tad|slightly|a lot|lots|much|way`;
const AMOUNT = String.raw`(?: (?<mod>${MOD})| by (?<by>\S+(?: \S+){0,2}))?`;
const IT = /^(?:it|this|that|this one|that one|(?:the |this |that |my )?(?:window|app|application|program))$/;
const YT_OBJECT = /^(?:youtube|the youtube tab|youtube tab|the youtube video|the video|youtube video|video|this video)$/;
const FS = String.raw`(?:full ?screen|full-screen)`;
const THEATER = String.raw`(?:theat(?:er|re)|cinema)(?: mode| view)?`;

type Scope = "pc" | "youtube" | "media";

/** The first scope word in the text decides whose volume it is; none means the PC's. */
function scopeIn(text: string): Scope {
  const m = new RegExp(`\\b(${YT_WORDS}|${MEDIA_WORDS}|${PC_WORDS})\\b`).exec(text);
  if (!m) return "pc";
  if (new RegExp(`^(?:${MEDIA_WORDS})$`).test(m[1]!)) return "media";
  if (new RegExp(`^(?:${YT_WORDS})$`).test(m[1]!)) return "youtube";
  return "pc";
}

const isYoutubeStep = (s: PlanStep | null): boolean => !!s && s.action.startsWith("youtube.");

// --- volume ----------------------------------------------------------------------------------------------------------

const MEDIA_VOLUME = "DoMe sets the whole PC's volume or the YouTube player's, not one app's. Which one?";

function volumeStep(p: Piece, scope: Scope, value: number, detail?: string): PieceResult {
  if (scope === "media") return clarify(MEDIA_VOLUME, [choice(`Set the PC volume to ${value}%`, `set the pc volume to ${value}`), choice(`Set YouTube volume to ${value}%`, `set youtube volume to ${value}`)], VOLUME_SCOPE);
  const opts = detail ? { detail } : {};
  if (scope === "youtube") return steps([step(p, "youtube.set_volume", { value }, { needsTarget: "youtube", ...opts })]);
  return steps([step(p, "windows.set_volume", { value }, opts)]);
}

function relativeVolume(p: Piece, g: Record<string, string | undefined>, up: boolean): PieceResult {
  const scope = scopeIn(p.text);
  if (scope === "media") return clarify(MEDIA_VOLUME, [choice(up ? "Turn the PC up" : "Turn the PC down", up ? "turn the pc volume up" : "turn the pc volume down"), choice(up ? "Turn YouTube up" : "Turn YouTube down", up ? "turn the youtube volume up" : "turn the youtube volume down")], VOLUME_SCOPE);
  if (g.all) return volumeStep(p, scope, up ? 100 : 0);
  let delta = 10;
  // "by a lot" and "by a bit" are amounts in words, like "a lot" on its own
  const mod = g.mod ?? (g.by !== undefined && new RegExp(`^(?:${MOD})$`).test(g.by) ? g.by : undefined);
  if (mod) {
    delta = /^(?:a lot|lots|much|way)$/.test(mod) ? 20 : 5;
  } else if (g.by !== undefined) {
    const n = parseNumberWords(g.by.replace(/ percent$/, ""));
    if (n === null || n < 1 || n > 100) return clarify("How much? Say a number of percent, for example “turn it up by 20”.", [], VOLUME_UNKNOWN);
    delta = n;
  }
  const base = scope === "youtube" ? p.ctx.youtubeVolume : p.ctx.pcVolume;
  if (base === null || base === undefined) {
    return scope === "youtube" ? clarify("I don't know the current YouTube volume yet. Try “set YouTube volume to 40”.", [], VOLUME_UNKNOWN) : clarify("I don't know the current PC volume yet. Try “set the volume to 40”.", [], VOLUME_UNKNOWN);
  }
  const value = Math.min(100, Math.max(0, base + (up ? delta : -delta)));
  return volumeStep(p, scope, value, `${up ? "Up" : "Down"} ${delta} from ${base}%`);
}

function levelResult(p: Piece, scope: Scope, level: string, explicit: boolean): PieceResult | null {
  const parsed = parseLevel(level);
  if ("value" in parsed) return volumeStep(p, scope, parsed.value);
  if (parsed.invalid === "range") return clarify("Volume must be between 0 and 100 percent.", [], VOLUME_UNKNOWN);
  return explicit ? clarify("I didn't catch the volume level. Say a number from 0 to 100, for example “set the volume to 40”.", [], VOLUME_UNKNOWN) : null;
}

const volumeRules: Rule = (p) => {
  const t = p.text;
  let m: RegExpExecArray | null;
  // relative: "turn up the volume", "turn it down a bit", "lower the volume by 20", "volume up", "a bit louder"
  m = new RegExp(`^(?:turn|crank|bump|push|pump) ${DIRP} ${VOLP}${AMOUNT}$`).exec(t);
  if (m) return relativeVolume(p, m.groups ?? {}, m.groups?.dir === "up");
  m = new RegExp(`^(?:turn|crank|bump|put|set) (?:it|this|everything|${VOLP}|${SCOPE}) ${DIRP}${AMOUNT}$`).exec(t);
  if (m) return relativeVolume(p, m.groups ?? {}, m.groups?.dir === "up");
  m = new RegExp(`^(?<verb>raise|increase|boost|lower|decrease|reduce|drop|lessen|bring up|bring down) ${VOLP}${AMOUNT}$`).exec(t);
  if (m) return relativeVolume(p, m.groups ?? {}, /^(?:raise|increase|boost|bring up)$/.test(m.groups?.verb ?? ""));
  m = new RegExp(`^${VOLP} ${DIRP}${AMOUNT}$`).exec(t);
  if (m) return relativeVolume(p, m.groups ?? {}, m.groups?.dir === "up");
  m = new RegExp(`^(?:(?:make|turn|get) (?:it |this |everything |${VOLP} |${SCOPE} )?)?(?:(?<pre>a (?:little )?bit|a little|a touch|a tad|slightly|much|a lot|way|even) )?(?<word>louder|quieter|softer)${AMOUNT}$`).exec(t);
  if (m) {
    const g = { ...m.groups };
    if (!g.mod && g.pre && g.pre !== "even") g.mod = g.pre;
    return relativeVolume(p, g, g.word === "louder");
  }
  // absolute: "make the volume 100%", "set the pc volume at 40", "put the volume on 50", "volume to max", "max volume"
  m = new RegExp(`^(?:(?<verb>set|change|put|turn|make|adjust|bring|raise|lower|increase|decrease|reduce|drop|move|switch|get) )?${VOLP} (?<to>(?:(?:up |down )?(?:to|at|on) )|= ?)?(?<level>-?[a-z0-9]+(?:[ -][a-z0-9]+){0,3})$`).exec(t);
  if (m) {
    const r = levelResult(p, scopeIn(t), m.groups!.level!, !!(m.groups?.verb ?? m.groups?.to));
    if (r) return r;
  }
  m = new RegExp(`^(?:(?:set|put|turn|make) (?:it |the volume |everything )?(?:to |at )?)?(?<level>[a-z0-9]+(?: [a-z0-9]+){0,2})(?: percent)? (?:${SCOPE} )?${VOL}$`).exec(t);
  if (m) {
    const r = levelResult(p, scopeIn(t), m.groups!.level!, false);
    if (r) return r;
  }
  if (new RegExp(`^(?:max out|max|maximi[sz]e|crank up|crank) ${VOLP}$`).test(t)) return volumeStep(p, scopeIn(t), 100);
  if (new RegExp(`^(?:set|change|adjust) ${VOLP}$`).test(t)) return clarify("To what level? Say a number from 0 to 100, for example “set the volume to 40”.", [], VOLUME_UNKNOWN);
  return null;
};

// --- mute --------------------------------------------------------------------------------------------------------------

function muteChoices(muted: boolean): Choice[] {
  const verb = muted ? "Mute" : "Unmute";
  return [choice(`${verb} the PC`, `${verb.toLowerCase()} the pc`), choice(`${verb} YouTube`, `${verb.toLowerCase()} youtube`)];
}

function muteResult(p: Piece, muted: boolean, scope: Scope | null): PieceResult {
  if (scope === null) return clarify(`${muted ? "Mute" : "Unmute"} YouTube or the whole PC?`, muteChoices(muted), VOLUME_SCOPE);
  if (scope === "media") return clarify("DoMe can mute the whole PC or the YouTube player, not a single app. Which one?", muteChoices(muted), VOLUME_SCOPE);
  if (scope === "youtube") return steps([step(p, "youtube.set_muted", { muted }, { needsTarget: "youtube" })]);
  return steps([step(p, "windows.set_muted", { muted })]);
}

const muteRules: Rule = (p) => {
  const t = p.text;
  let m = /^(?<un>un)?(?:mute|silence)(?: (?<obj>.+))?$/.exec(t);
  if (m) {
    const muted = !m.groups?.un;
    const obj = (m.groups?.obj ?? "").replace(/^(?:the|my|your) /, "");
    if (obj === "") return muteResult(p, muted, null);
    if (IT.test(obj)) return muteResult(p, muted, isYoutubeStep(p.prev) ? "youtube" : p.prev?.action.startsWith("windows.") ? "pc" : null);
    if (/^(?:everything|all|all sound|all audio|the whole pc|whole pc|everything on the pc)$/.test(obj)) return muteResult(p, muted, "pc");
    if (new RegExp(`^(?:(?:${SCOPE})(?: ${VOL})?|${VOL}(?: (?:of|on|for|in) ${DET}${SCOPE})?)$`).test(obj)) return muteResult(p, muted, scopeIn(obj));
    return null;
  }
  // "turn the volume off" mutes and "turn the volume on" unmutes, like "turn off the volume"
  m = new RegExp(`^turn (?<onoff>on|off) ${DET}(?:(?:${SCOPE}) )?(?:sound|audio|volume)$|^turn ${DET}(?:(?:${SCOPE}) )?(?:sound|audio|volume) (?<onoff2>on|off)$|^(?:sound|audio|volume) (?<onoff3>on|off)$`).exec(t);
  if (m) return muteResult(p, (m.groups?.onoff ?? m.groups?.onoff2 ?? m.groups?.onoff3) === "off", scopeIn(t));
  return null;
};

// --- playback ----------------------------------------------------------------------------------------------------------

type Want = "pause" | "play" | "next" | "previous";

/** Who a bare "pause"/"play"/"next"/"previous" is for; "ask" when both YouTube and a media player could be meant. */
function choosePlayer(p: Piece, want: Want): "youtube" | "media" | "ask" {
  if (isYoutubeStep(p.prev)) return "youtube";
  if (p.prev?.action.startsWith("media.")) return "media";
  const pb = p.ctx.playback;
  if (!pb || !pb.media.available) return "youtube";
  if (!pb.youtube.available) return "media";
  // Both could be meant: whatever is playing pauses/skips; whatever is not playing resumes. A tie asks.
  const y = want === "play" ? !pb.youtube.playing : pb.youtube.playing;
  const md = want === "play" ? !pb.media.playing : pb.media.playing;
  return y && !md ? "youtube" : md && !y ? "media" : "ask";
}

function playbackResult(p: Piece, want: Want, who: "youtube" | "media" | "ask"): PieceResult {
  if (who === "ask") {
    const label = p.ctx.playback?.media.label || "the media player";
    const both = p.ctx.playback?.youtube.playing && p.ctx.playback.media.playing ? "are both playing" : p.ctx.playback?.youtube.playing === false && p.ctx.playback.media.playing === false ? "are both paused" : "are both open";
    const options: Record<Want, [Choice, Choice]> = {
      pause: [choice("Pause YouTube", "pause youtube"), choice(`Pause ${label}`, "pause the media player")],
      play: [choice("Play YouTube", "play youtube"), choice(`Play ${label}`, "play the media player")],
      next: [choice("Next video", "next video"), choice(`Next track (${label})`, "next track")],
      previous: [choice("Previous video", "previous video"), choice(`Previous track (${label})`, "previous track")],
    };
    return clarify(`YouTube and ${label} ${both}. Which one?`, options[want], { topic: "player" });
  }
  if (who === "youtube") {
    if (want === "pause" || want === "play") return steps([step(p, "youtube.set_paused", { paused: want === "pause" }, { needsTarget: "youtube" })]);
    return steps([step(p, want === "next" ? "youtube.next" : "youtube.previous", {}, { needsTarget: "youtube" })]);
  }
  if (want === "pause" || want === "play") return steps([step(p, "media.set_paused", { paused: want === "pause" }, { needsTarget: "media" })]);
  return steps([step(p, want === "next" ? "media.next" : "media.previous", {}, { needsTarget: "media" })]);
}

const YT_NOUN = String.raw`(?:youtube(?: video| player| tab)?|(?:youtube )?video|this video)`;
const MEDIA_NOUN = String.raw`(?:music|media(?: player)?|songs?|track|spotify|audio player)`;

const playbackRules: Rule = (p) => {
  const t = p.text;
  if (new RegExp(`^(?:pause|stop|freeze|hold) ${DET}${YT_NOUN}$|^youtube (?:pause|stop)$`).test(t)) return playbackResult(p, "pause", "youtube");
  if (new RegExp(`^(?:play|resume|unpause|continue|start|keep playing|continue playing|start playing|resume playing) ${DET}${YT_NOUN}$|^youtube (?:play|resume|unpause)$|^play youtube$`).test(t)) return playbackResult(p, "play", "youtube");
  if (new RegExp(`^(?:pause|stop) (?:the |my |this )?${MEDIA_NOUN}$`).test(t)) return playbackResult(p, "pause", "media");
  if (new RegExp(`^(?:play|resume|unpause|continue|start) (?:the |my |this )?${MEDIA_NOUN}$`).test(t)) return playbackResult(p, "play", "media");
  if (/^(?:pause|freeze|hold on|press pause|pause (?:it|this|that|playback|the player|playing)|stop (?:it|playing|playback|the player))$/.test(t)) return playbackResult(p, "pause", choosePlayer(p, "pause"));
  if (/^(?:play|resume|unpause|continue|keep playing|start playing|press play|play (?:it|this|that)|resume (?:it|this|playback|playing)|unpause (?:it|this)|continue playing)$/.test(t)) {
    // "search YouTube for cats and play it": the thing to play is the first result of that search
    if (p.prev?.action === "youtube.search" && /^play (?:it|this|that)$/.test(t)) return openResult(p, 1);
    return playbackResult(p, "play", choosePlayer(p, "play"));
  }
  // next / previous
  if (/^(?:(?:next|skip)(?: to)?(?: the)? (?:next )?video|(?:play|go to|skip to) (?:the )?next video|skip (?:the |this )?video|next youtube video)$/.test(t)) return playbackResult(p, "next", "youtube");
  if (/^(?:(?:next|skip)(?: the| this| to the next)? (?:track|song)|(?:play|go to|skip to) (?:the )?next (?:track|song))$/.test(t)) return playbackResult(p, "next", "media");
  if (/^(?:next|skip|next one|skip it|skip this|skip this one|skip that|go next|play next|go to the next one|play the next one|play next one)$/.test(t)) return playbackResult(p, "next", choosePlayer(p, "next"));
  if (/^(?:(?:previous|last|prev) video|(?:go )?back a video|(?:go to|play|go back to) (?:the )?previous video)$/.test(t)) return playbackResult(p, "previous", "youtube");
  if (/^(?:(?:previous|last|prev) (?:track|song)|(?:go )?back a (?:track|song)|(?:go to|play|go back to) (?:the )?previous (?:track|song))$/.test(t)) return playbackResult(p, "previous", "media");
  if (/^(?:previous|previous one|go back one)$/.test(t)) return playbackResult(p, "previous", choosePlayer(p, "previous"));
  return null;
};

// --- seek ----------------------------------------------------------------------------------------------------------------

const seekRules: Rule = (p) => {
  const t = p.text;
  if (/^(?:start over|start again|restart the video|restart this video|replay(?: the video| this video| it)?|(?:start |play |watch )?from the (?:beginning|start|top)|(?:go|jump|skip|rewind) (?:back )?to the (?:beginning|start)|back to the (?:beginning|start))$/.test(t)) {
    return steps([step(p, "youtube.seek_to", { position_seconds: 0 }, { needsTarget: "youtube", detail: "Back to the start" })]);
  }
  // A word after the verb that is not a duration ("go back to notepad", "jump to 2:00") is another rule's.
  let m = /^(?:go back|skip back|jump back|seek back|move back|step back|rewind|back|go backwards?|skip backwards?|rewind back)(?: by)?(?: (?<dur>.+))?$/.exec(t);
  const back = m ? (m.groups?.dur ? parseDuration(m.groups.dur) : 10) : null;
  if (back !== null) {
    if (back <= 0 || back > 600) return clarify("How far back? Up to 10 minutes, for example “go back 30 seconds”.");
    return steps([step(p, "youtube.seek_relative", { seconds: -back }, { needsTarget: "youtube" })]);
  }
  m = /^(?<verb>skip ahead|skip forward|go forward|forward|fast forward|fast-forward|jump ahead|jump forward|seek ahead|seek forward|move forward|step forward|skip|jump)(?: by)?(?: (?<dur>.+))?$/.exec(t);
  const ahead = m && (m.groups?.dur || !/^(?:skip|jump)$/.test(m.groups?.verb ?? "")) ? (m.groups?.dur ? parseDuration(m.groups.dur) : 10) : null;
  if (ahead !== null) {
    if (ahead <= 0 || ahead > 600) return clarify("How far ahead? Up to 10 minutes, for example “skip ahead 30 seconds”.");
    return steps([step(p, "youtube.seek_relative", { seconds: ahead }, { needsTarget: "youtube" })]);
  }
  m = /^(?:go|jump|skip|seek|move|fast forward|rewind|scrub|take me|bring me|start|play|watch)(?: (?:back|ahead|forward))? (?:to|from) (?<pos>.+)$/.exec(t);
  if (m) {
    const s = parsePosition(m.groups!.pos!);
    if (s === null) return null; // "go to notepad", "go to sleep"
    if (s > 172800) return clarify("That is further than any video DoMe can seek in.");
    return steps([step(p, "youtube.seek_to", { position_seconds: s }, { needsTarget: "youtube", detail: `To ${mmss(s)}` })]);
  }
  return null;
};

// --- YouTube view: theater, full screen, captions, speed --------------------------------------------------------------

function theater(p: Piece, enabled: boolean): PieceResult {
  return steps([step(p, "youtube.set_theater", { enabled }, { needsTarget: "youtube" })]);
}

/** Approved apps that are the browser(s) the DoMe extension is connected in (for window controls on "YouTube"). */
function browserApps(ctx: IntentContext): IntentApp[] {
  const apps = ctx.apps ?? [];
  const families = (ctx.browsers ?? []).filter((b) => b !== "unknown");
  const out = families.length > 0 ? apps.filter((a) => families.some((f) => isBrowserApp(a, f))) : [];
  return out.length > 0 ? out : apps.filter((a) => isBrowserApp(a));
}

function youtubeFullscreen(p: Piece): PieceResult {
  const fromPrevious = isYoutubeStep(p.prev);
  const s = step(p, "youtube.request_fullscreen", {}, { needsTarget: "youtube", fromPrevious, detail: "Browsers usually allow this only after a click on the PC itself; theater mode always works." });
  const apps = p.ctx.apps ?? [];
  const alternatives = browserApps(p.ctx).map((a) => choice(`Make ${a.display_name} full screen instead`, `make ${appPhrase(a, apps)} full screen`));
  return steps([s], [...alternatives.slice(0, 2), choice("Theater mode on", "theater mode on")]);
}

function youtubeExitFullscreen(p: Piece): PieceResult {
  const apps = p.ctx.apps ?? [];
  const browsers = browserApps(p.ctx).map((a) => choice(`Leave full screen in ${a.display_name}`, `exit full screen in ${appPhrase(a, apps)}`));
  return clarify("DoMe can't leave the video's own full screen yet: press Esc on the PC, or use the Touchpad. For an app's full screen, say “exit full screen in Edge”.", browsers.slice(0, 2), { link: "touchpad" });
}

const viewRules: Rule = (p) => {
  const t = p.text;
  let m = new RegExp(`^(?:(?:turn|switch|put) )?(?<a>on |off )?${DET}${THEATER}(?: (?<b>on|off))?$`).exec(t);
  if (m) return theater(p, (m.groups?.a ?? m.groups?.b ?? "on").trim() === "on");
  m = new RegExp(`^(?<verb>enable|disable|exit|leave|enter|use|close|quit|start|stop|activate|deactivate|turn on|turn off|switch on|switch off) ${DET}${THEATER}$`).exec(t);
  if (m) return theater(p, !/^(?:disable|exit|leave|close|quit|stop|deactivate|turn off|switch off)$/.test(m.groups!.verb!));
  if (new RegExp(`^(?:(?:go|switch|change|put it|put youtube|put the video) (?:in|into|to)|make it|watch in|watch it in) ${DET}${THEATER}$`).test(t)) return theater(p, true);

  // YouTube full screen (an app's full screen is an app rule: "full screen notepad")
  if (new RegExp(`^(?:(?:go|switch to|turn on|enter|watch in|watch it in|play in|put (?:it|this|the video|youtube) (?:in|into|to)) )?${FS}(?: on| mode)?$|^${FS} (?:youtube|the video|video|the youtube video)$|^(?:youtube|the video|video) ${FS}$|^make (?:the video|video|youtube|the youtube video|the youtube player) ${FS}$`).test(t)) return youtubeFullscreen(p);
  m = new RegExp(`^(?:make|put|set|switch) (?<it>it|this|that) (?:in |into |to )?${FS}(?: mode)?$|^${FS} (?<it2>it|this|that)$`).exec(t);
  if (m) return p.prev?.appId ? null : youtubeFullscreen(p); // after an app step, "make it full screen" is that app (app rules)
  if (new RegExp(`^(?:(?:exit|leave|quit|close|end|escape|turn off|get out of|cancel|stop) ${FS}(?: mode)?|${FS} off|un ?${FS}|exit ${FS} on youtube)$`).test(t)) return youtubeExitFullscreen(p);

  // captions and speed: not in protocol 1.2, said honestly
  if (new RegExp(`^(?:(?:turn|switch|put) (?:on|off) |show |hide |enable |disable |toggle |(?:turn|switch) )?${DET}(?:youtube )?(?:closed )?(?:captions?|subtitles?|cc|subs)(?: (?:on|off))?$`).test(t)) {
    return handoff("DoMe can't turn captions on or off yet. Press C on the PC, or tap the CC button in the player with the Touchpad.");
  }
  if (/^(?:(?:set|change|make|put) )?(?:the |my )?(?:playback |video |youtube )?speed(?: .+)?$|^(?:play|watch) (?:it |this |the video )?(?:at|in) .+ speed$|^(?:play |watch )?(?:at )?\d+(?:\.\d+)? ?x(?: speed)?$|^(?:speed up|slow down)(?: the video| youtube| it| playback)?$|^(?:normal|double|half) speed$|^(?:faster|slower)$/.test(t)) {
    return handoff("DoMe can't change the playback speed yet. Use the Touchpad: the player's gear button → Playback speed.");
  }
  return null;
};

// --- YouTube search and results ------------------------------------------------------------------------------------------

const MAX_QUERY = 100;

/**
 * A search for the words in capture group `group`, exactly as typed. null when the words around the query are not a
 * plain command (INJECTION): the query itself may say anything. `openEnded` = the query runs to the end of the piece.
 */
function searchStep(p: Piece, m: RegExpExecArray, group: string, openEnded: boolean): PlanStep | PieceResult | null {
  const at = m.indices?.groups?.[group];
  if (!at || INJECTION.test(`${p.text.slice(0, at[0])} ${p.text.slice(at[1])}`)) return null;
  // "search lofi on youtube and theater mode": "on youtube" ends a query, so the words after it are not part of it
  if (openEnded && / (?:on|in|from) youtube(?: |$)/.test(p.text.slice(at[0], at[1]))) return null;
  const q = unquote(typedGroup(p, m, group));
  if (q === "") return clarify("What should I search YouTube for? For example “search YouTube for lofi beats”.");
  if ([...q].length > MAX_QUERY) return clarify(`That search is too long. Searches from DoMe can be up to ${MAX_QUERY} characters.`);
  const s = step(p, "youtube.search", { query: q }, { needsTarget: "youtube_browser" });
  if (openEnded) OPEN_ENDED.add(s);
  return s;
}

const asSteps = (s: PlanStep | PieceResult | null): PieceResult | null => (s === null ? null : "action" in s ? steps([s]) : s);

function openResult(p: Piece, index: number): PieceResult {
  if (index < 1 || index > 10) return clarify("DoMe can open search results 1 to 10.");
  if (p.prev?.action === "youtube.search") {
    return steps([step(p, "youtube.open_result", { index }, { needsTarget: "search_results", fromPrevious: true, detail: `The ${ordinalWord(index)} video the search finds` })]);
  }
  const results = p.ctx.lastSearch?.results;
  if (!results) return clarify("Search YouTube first, for example “search YouTube for lofi beats”, then say “play the second video”.", [choice("Search YouTube for lofi beats", "search youtube for lofi beats")]);
  if (results.length === 0) return clarify("The last search found no videos. Search again with other words.");
  const hit = results.find((r) => r.index === index);
  if (!hit) return clarify(`The last search found only ${results.length} video${results.length === 1 ? "" : "s"}.`, resultChoices(results));
  return steps([step(p, "youtube.open_result", { index, video_id: hit.video_id }, { needsTarget: "search_results", detail: `“${hit.title || "Untitled video"}”` })]);
}

/** Chips for the last search's results: the title as the label (plain text), "play the Nth video" as the text. */
function resultChoices(results: ReadonlyArray<{ index: number; title: string }>): Choice[] {
  return results.slice(0, 5).map((r) => choice(`${r.index}. ${r.title || "Untitled video"}`, `play the ${ordinalWord(r.index)} video`));
}

/** "Which one?" for a pick DoMe cannot place ("play the last one", "that movie"): the results, or how to get some. */
function whichResult(p: Piece): PieceResult {
  const results = p.ctx.lastSearch?.results;
  if (results && results.length > 0) return clarify("Which one? Tap a result, or say “play the first video”.", resultChoices(results));
  return clarify("Search YouTube first, for example “search YouTube for lofi beats”, then say “play the second video”.", [choice("Search YouTube for lofi beats", "search youtube for lofi beats")]);
}

const ORDINAL_WORD = String.raw`(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|top|\d{1,2}(?:st|nd|rd|th))`;
const RESULT_NOUN = String.raw`(?:one|video|result|link|clip|movie|song|search result|thing|option)`;
const PICK = String.raw`(?:play|open|watch|click(?: on)?|tap(?: on)?|select|choose|pick|start|put on|go to|show(?: me)?)`;
/** What "play …" may name that is no search: a position or a pronoun ("play the second", "play that one"). */
const NOT_A_QUERY = new RegExp(`^(?:the |this |that )?(?:${ORDINAL_WORD}|last|next|previous|other|it|this|that|one|same)(?: one)?$`);

const searchRules: Rule = (p) => {
  const t = p.text;
  if (/^(?:search for|search|look up|lookup|look for|find)(?: on| in)? youtube(?: for)?$|^search(?: for)?$|^youtube search(?: for)?$/.test(t)) return clarify("What should I search YouTube for? For example “search YouTube for lofi beats”.");
  // results of the last search (or of the search one step earlier); with a verb the noun may be left out ("play the 2nd")
  let m = new RegExp(`^(?:${PICK} )?(?:the )?(?<ord>${ORDINAL_WORD}) ${RESULT_NOUN}(?: (?:on youtube|in the (?:list|results)|from the (?:list|results)))?$|^(?:${PICK} )?(?:the )?(?:video|result|number|search result|option|#) ?(?:number )?(?<num>[a-z0-9]+)$|^${PICK} (?:the )?(?<ord2>${ORDINAL_WORD})$`).exec(t);
  if (m) {
    const n = parseOrdinal(m.groups?.ord ?? m.groups?.num ?? m.groups?.ord2 ?? "");
    if (n !== null) return openResult(p, n);
  }
  if (p.prev?.action === "youtube.search" && /^(?:open|watch) (?:it|that|this)$/.test(t)) return openResult(p, 1);
  // "play the last one": DoMe does not guess which position "last" is
  if (new RegExp(`^(?:${PICK} )?(?:the )?last (?:${RESULT_NOUN})$|^${PICK} the last$`).test(t)) return whichResult(p);
  // "click on that movie": DoMe cannot see the screen
  if (new RegExp(`^(?:click|tap|press|select|choose|open|play|watch|pick|start|put on)(?: on)? (?:that|this|those|the) (?:movie|film|one|show|episode|thing|button|link|item|clip|result|series|game|icon|picture|photo|image|video)$`).test(t) && !/^(?:play|start|watch) (?:the|this) video$/.test(t)) {
    const results = p.ctx.lastSearch?.results;
    if (results && results.length > 0) return clarify("Which one? Tap a result, or say “play the first video”.", resultChoices(results));
    const hint = p.ctx.playback?.youtube.available ? " On YouTube you can also say “search YouTube for …” and then “play the first video”." : "";
    return handoff(`${THAT_ONE_HANDOFF} Use the Touchpad to point and tap.${hint}`);
  }
  // search
  m = /^(?:search for|search|look up|lookup|look for|find)(?: on| in)? youtube(?: for)? (?<q>.+)$|^youtube search(?: for)? (?<q2>.+)$/d.exec(t);
  if (m) return asSteps(searchStep(p, m, m.groups?.q !== undefined ? "q" : "q2", true));
  m = /^(?:search for|search|look up|lookup|look for|find) (?<q>.+) (?:on|in) youtube$/d.exec(t);
  if (m) return asSteps(searchStep(p, m, "q", false));
  // "play despacito on youtube" = search, then open the first result
  m = /^(?:play|watch|put on|show me|find and play|search and play) (?<q>.+) (?:on|from|in) youtube$/d.exec(t);
  if (m) {
    if (/^(?:it|this|that|the video|this video|video)$/.test(m.groups!.q!)) return playbackResult(p, "play", "youtube");
    if (NOT_A_QUERY.test(m.groups!.q!)) return whichResult(p);
    const s = searchStep(p, m, "q", false);
    if (s === null || !("action" in s)) return s;
    const open = step({ ...p, prev: s }, "youtube.open_result", { index: 1 }, { needsTarget: "search_results", fromPrevious: true, detail: "The first video the search finds" });
    return steps([s, open]);
  }
  m = /^(?:google|bing)(?: for)? (?<q>.+)$|^search (?:google|the web|the internet|online) for (?<q2>.+)$/d.exec(t);
  if (m) {
    const at = m.indices?.groups?.[m.groups?.q !== undefined ? "q" : "q2"];
    if (!at || INJECTION.test(p.text.slice(0, at[0]))) return null;
    const q = unquote(typedGroup(p, m, m.groups?.q !== undefined ? "q" : "q2"));
    return clarify("DoMe can only search YouTube.", [choice(`Search YouTube for “${q}”`, `search youtube for ${q}`)]);
  }
  return null;
};

/** "youtube next", "youtube mute": one control word after "youtube" is that control, never a search for the word. */
const YOUTUBE_WORD: Readonly<Record<string, string>> = {
  next: "next video",
  skip: "next video",
  previous: "previous video",
  back: "previous video",
  mute: "mute youtube",
  unmute: "unmute youtube",
  louder: "make youtube louder",
  quieter: "make youtube quieter",
  softer: "make youtube quieter",
  up: "turn youtube up",
  down: "turn youtube down",
};

/** The end of the answer to "play {words}": a question on the Type page; the Assistant speaks a statement instead (butlerSpeech.ts). */
export const ONLY_SEARCH_PLAYS = "DoMe only plays videos by searching YouTube.";

/** Last-resort YouTube phrases, after every other rule: "youtube lofi", "search lofi", "play lofi". */
const searchFallbackRules: Rule = (p) => {
  const word = /^youtube (\S+)$/.exec(p.text)?.[1];
  if (word && word in YOUTUBE_WORD) return parsePiece(reworded(p, YOUTUBE_WORD[word]!));
  let m = /^youtube (?<q>.+)$|^search(?: for)? (?<q2>.+)$/d.exec(p.text);
  if (m) return asSteps(searchStep(p, m, m.groups?.q !== undefined ? "q" : "q2", true));
  m = /^(?:play|watch|put on) (?<q>.+)$/d.exec(p.text);
  if (m) {
    // "play the second", "play that one": a position or a pronoun is never searched for
    if (NOT_A_QUERY.test(m.groups!.q!)) return whichResult(p);
    const q = unquote(typedGroup(p, m, "q"));
    return clarify(`Play “${q}” on YouTube? ${ONLY_SEARCH_PLAYS}`, [choice(`Play “${q}” on YouTube`, `play ${q} on youtube`)]);
  }
  return null;
};

// --- PC: connection, status, lock, power ---------------------------------------------------------------------------------

const DEVICE = String.raw`${DET}(?:pc|computer|laptop)`;

const pcRules: Rule = (p) => {
  const t = p.text;
  if (/^(?:ping|ping (?:the |my )?(?:pc|computer)|check (?:the )?connection|are you there|is (?:the |my )?(?:pc|computer) (?:online|connected))$/.test(t)) return steps([step(p, "system.ping", {})]);
  if (/^(?:status|pc status|refresh(?: the)? status|is (?:the |my )?(?:pc|computer) ok(?:ay)?|how is (?:the |my )?(?:pc|computer))$/.test(t)) return steps([step(p, "system.get_status", {})]);
  if (/^(?:what(?:'s| is) (?:the |my )?(?:pc |computer )?volume(?: at)?|(?:current|pc) volume level|how loud is it)$/.test(t)) return steps([step(p, "windows.get_volume", {})]);
  if (/^(?:(?:list|show(?: me)?) (?:the |my )?(?:approved )?apps|(?:what|which) apps can i (?:open|use))$/.test(t)) return steps([step(p, "app.list", {})]);
  if (new RegExp(`^(?:lock|lock (?:it|up|it up)|lock ${DET}(?:pc|computer|laptop|windows|screen|workstation|session))$`).test(t)) return steps([step(p, "windows.lock", {})]);
  if (new RegExp(`^(?:sleep|go to sleep|sleep mode|put (?:it|${DEVICE}) to sleep|sleep ${DEVICE}|suspend(?: ${DEVICE})?|put ${DEVICE} in(?:to)? sleep mode)$`).test(t)) return steps([step(p, "power.sleep", {})]);
  if (new RegExp(`^hibernate(?: ${DEVICE})?$`).test(t)) return clarify("DoMe can put the PC to sleep, not hibernate it.", [choice("Put the PC to sleep", "put my computer to sleep")]);
  // "shut down in 5 minutes": DoMe has no timer, and the words after the verb are no app
  const timed = new RegExp(`^(?<verb>shut ?down|turn off|power (?:off|down)|switch off|restart|reboot|sleep|go to sleep)(?: ${DEVICE})? (?:in|after|at|tonight|later)(?: .+)?$`).exec(t);
  if (timed) {
    const verb = timed.groups!.verb!;
    const [what, phrase] = /restart|reboot/.test(verb) ? ["a restart", "restart my pc"] : /sleep/.test(verb) ? ["sleep", "put my computer to sleep"] : ["a shutdown", "shut down my pc"];
    return clarify(`DoMe can't schedule ${what}. Send “${phrase}” when you're ready.`, [choice(phrase.charAt(0).toUpperCase() + phrase.slice(1), phrase)], SAFETY);
  }
  if (new RegExp(`^(?:shut ?down|shut ${DEVICE} down|shut ?down ${DEVICE}|turn ${DEVICE} off|turn off ${DEVICE}|power (?:off|down)(?: ${DEVICE})?|switch off ${DEVICE}|switch ${DEVICE} off)$`).test(t)) return steps([step(p, "power.shutdown", {})]);
  if (new RegExp(`^(?:restart|reboot)(?: ${DEVICE})?$`).test(t)) return steps([step(p, "power.restart", {})]);
  if (/^(?:cancel|never mind|cancel (?:the )?(?:shutdown|shut down|restart|reboot|sleep|countdown|power ?(?:action|request|off))|(?:abort|stop)(?: the)? (?:shutdown|shut down|restart|reboot|sleep|countdown)|don't (?:shut down|shutdown|restart|sleep))$/.test(t)) {
    return steps([step(p, "power.cancel", {})]);
  }
  return null;
};

// --- pointer / keyboard: honest handoff --------------------------------------------------------------------------------

/** The honest answers to "click …" and "type …"; the Assistant speaks its own words for them (butlerSpeech.ts). */
export const POINTER_HANDOFF = "DoMe can't see your screen, so typed commands don't click or scroll. Use the Touchpad to point and tap.";
export const KEYBOARD_HANDOFF = "Typed commands never type text or press keys on the PC. Use the keyboard on the Touchpad page.";
/** Start of the answer to "click on that movie" when there are no search results to pick from. */
export const THAT_ONE_HANDOFF = "DoMe can't see your screen, so it can't tell which one “that” is.";

const handoffRules: Rule = (p) => {
  const t = p.text;
  if (/^(?:(?:double|right|left) )?click(?: .*)?$|^(?:double )?tap(?: on)? .+$|^(?:scroll|swipe)(?: .*)?$|^move (?:the )?(?:mouse|cursor|pointer)(?: .*)?$|^drag(?: .*)?$/.test(t)) {
    return handoff(POINTER_HANDOFF);
  }
  if (/^(?:type|write|press|hit) .+$/.test(t)) return handoff(KEYBOARD_HANDOFF);
  return null;
};

// --- apps ----------------------------------------------------------------------------------------------------------------

export type AppVerb = "launch" | "focus" | "minimize" | "maximize" | "restore" | "fullscreen_on" | "fullscreen_off" | "close";

/** Sentence forms per verb; `app` is whatever stands where the app name goes (resolved afterwards). */
const APP_FORMS: ReadonlyArray<readonly [RegExp, AppVerb | "launch_fullscreen" | "launch_maximized"]> = [
  [/^(?:open|launch|start|run) (?<app>.+?) (?:in )?(?:full ?screen|full-screen)$/d, "launch_fullscreen"],
  [/^(?:open|launch|start|run) (?<app>.+?) maximi[sz]ed$/d, "launch_maximized"],
  [/^(?:open up|start up|boot up|fire up|open|launch|start|run|load) (?<app>.+)$/d, "launch"],
  [/^(?:switch to|switch over to|switch back to|go to|go back to|change to|jump to|return to|back to|focus(?: on)?|show(?: me)?|bring up|bring back|activate|pull up) (?<app>.+)$/d, "focus"],
  [/^(?:bring|put|move|get) (?<app>.+?) (?:to the front|to front|in front|forward|up front|to the top|on top|to the foreground|into focus|into view|up|back)$/d, "focus"],
  [/^(?<app>.+?) to the front$/d, "focus"],
  [/^make (?<app>.+?) (?:active|the active window)$/d, "focus"],
  [/^(?:minimi[sz]e|hide|shrink|collapse) (?<app>.+)$/d, "minimize"],
  [/^(?:put|send|move) (?<app>.+?) (?:away|down|to the taskbar|in the taskbar)$/d, "minimize"],
  [/^(?:maximi[sz]e|enlarge|expand|max) (?<app>.+)$/d, "maximize"],
  [/^make (?<app>.+?) (?:bigger|big|larger|huge|full size|maximi[sz]ed|max|as big as possible)$/d, "maximize"],
  // leaving full screen comes before closing, so "exit full screen X" is never "close an app called full screen X"
  [/^(?:exit|leave|quit|end|close|turn off|disable|get out of|escape|cancel|stop|switch off) (?:full ?screen|full-screen)(?: mode)? (?:(?:in|on|for|of) )?(?<app>.+)$/d, "fullscreen_off"],
  [/^(?:take|bring|get) (?<app>.+?) out of (?:full ?screen|full-screen)$/d, "fullscreen_off"],
  [/^(?:exit|leave) (?<app>.+?) (?:full ?screen|full-screen)$/d, "fullscreen_off"],
  [/^(?<app>.+?) (?:exit|leave|quit|end|close|turn off|stop) (?:full ?screen|full-screen)(?: mode)?$/d, "fullscreen_off"],
  [/^(?<app>.+?) (?:full ?screen off|out of full ?screen|not full ?screen)$/d, "fullscreen_off"],
  [/^un ?full ?screen (?<app>.+)$/d, "fullscreen_off"],
  [/^(?:full ?screen|full-screen) (?<app>.+)$/d, "fullscreen_on"],
  [/^make (?<app>.+?) (?:full ?screen|full-screen)$/d, "fullscreen_on"],
  [/^(?:put|set|switch|turn) (?<app>.+?) (?:in|into|to) (?:full ?screen|full-screen)(?: mode)?$/d, "fullscreen_on"],
  [/^(?:go|enter|turn on|enable|switch to) (?:full ?screen|full-screen) (?:in|on|for|with) (?<app>.+)$/d, "fullscreen_on"],
  [/^(?<app>.+?) (?:in )?(?:full ?screen|full-screen)(?: mode)?$/d, "fullscreen_on"],
  [/^(?:restore|unmaximi[sz]e|un maximi[sz]e|unminimi[sz]e|un minimi[sz]e) (?<app>.+)$/d, "restore"],
  [/^make (?<app>.+?) (?:normal|normal size|smaller|regular size|windowed|small|not maximi[sz]ed)$/d, "restore"],
  [/^(?:restore|return|bring|put|set) (?<app>.+?) (?:to|back to) (?:normal|normal size|its normal size|regular size)$/d, "restore"],
  [/^shut (?<app>.+?) down$/d, "close"],
  [/^(?:close down|shut down|close|quit|exit|kill|end|terminate) (?<app>.+)$/d, "close"],
];

/** "verb {app}" phrases for chips; each parses back to the same verb. */
const VERB_PHRASE: Record<AppVerb, string> = {
  launch: "open {}",
  focus: "bring {} to the front",
  minimize: "minimize {}",
  maximize: "maximize {}",
  restore: "restore {}",
  fullscreen_on: "full screen {}",
  fullscreen_off: "exit full screen in {}",
  close: "close {}",
};

/** "maximize ZStream": a command for one approved app, in words that parse back to that verb and that app. */
export function appCommandText(verb: AppVerb, app: IntentApp, apps: readonly IntentApp[]): string {
  return VERB_PHRASE[verb].replace("{}", appPhrase(app, apps));
}

/**
 * How help text and the Type page's examples name an app: a running approved app (else the first one) in words that
 * parse back to it, so an example reads like the customer's own command ("Full screen ZStream"). "Notepad" only
 * while the PC's list is unknown or empty.
 */
export function exampleAppPhrase(apps: readonly IntentApp[] | null | undefined): string {
  const list = apps ?? [];
  const app = list.find((a) => a.running === true) ?? list[0];
  return app ? appPhrase(app, list) : "Notepad";
}

/** What to say after "open X and …" when the app is not running yet. */
const AFTER_LAUNCH: Partial<Record<AppVerb, string>> = { maximize: "maximize it", restore: "restore it", fullscreen_on: "make it full screen", minimize: "minimize it", fullscreen_off: "exit full screen in it" };
const ACTIVE_VERBS: ReadonlySet<AppVerb> = new Set(["focus", "minimize", "maximize", "restore", "fullscreen_on", "fullscreen_off", "close"]);
/**
 * Words that stand for something other than an app ("pause the music", "lock the screen"). An approved app with
 * exactly that name (or id, or a known alias) still wins: "open Spotify" opens it when Spotify is approved.
 */
const RESERVED_APP_WORDS = /^(?:pc|computer|laptop|volume|sound|audio|music|spotify|media|youtube player|screen|everything|all|full ?screen|theater mode|theatre mode)$/;
/** Reserved words that are also an app's name: with an app verb and no such app approved, "not an approved app". */
const RESERVED_APP_BRANDS = /^spotify$/;
/** An app slot that is no name at all: a preposition or a number first ("in 5 minutes"), "all …", a flag or a path. */
const NOT_A_NAME = /^(?:in|on|at|to|for|with|from|by|after|before|into|onto|of|off|out|over|up|down|all|every|everything|each|both)\b|^\d+(?: |$)|(?:^| )[-/\\]/;
const MANY_APPS = /^(?:all|every|everything|each|both)\b/;

function appSteps(p: Piece, verb: AppVerb, app: IntentApp): PieceResult {
  const target = { app_id: app.app_id };
  const apps = p.ctx.apps ?? [];
  let warning: string | undefined;
  const choices: Choice[] = [];
  const startedJustBefore = p.prev?.action === "app.launch" && p.prev.appId === app.app_id;
  // Running with no window (Edge's Startup boost, a player closed to the tray): window commands have nothing to act on.
  const windowless = app.running === true && app.windows !== undefined && app.windows.length === 0;
  if (ACTIVE_VERBS.has(verb) && (app.running === false || windowless) && !startedJustBefore) {
    warning = windowless ? `${app.display_name} is running without a window on the PC. Open it first to show its window.` : `${app.display_name} was not running when DoMe last checked. If it still isn't, the PC will say so.`;
    const phrase = appPhrase(app, apps);
    const after = AFTER_LAUNCH[verb];
    if (after) choices.push(choice(`Open ${app.display_name} first`, `open ${phrase} and ${after}`));
    else if (verb === "focus") choices.push(choice(`Open ${app.display_name}`, `open ${phrase}`));
  }
  const opts = { target, appId: app.app_id, ...(warning ? { warning } : {}) };
  switch (verb) {
    case "launch":
      return steps([step(p, "app.launch", { app_id: app.app_id })]);
    case "focus":
      return steps([step(p, "app.focus", {}, opts)], choices);
    case "minimize":
      return steps([step(p, "app.minimize", {}, opts)], choices);
    case "maximize":
      return steps([step(p, "app.maximize", {}, opts)], choices);
    case "restore":
      return steps([step(p, "app.restore", {}, opts)], choices);
    case "fullscreen_on":
      return steps([step(p, "app.set_fullscreen", { enabled: true }, opts)], choices);
    case "fullscreen_off":
      return steps([step(p, "app.set_fullscreen", { enabled: false }, opts)], choices);
    case "close":
      return steps([step(p, "app.close", {}, opts)], choices);
  }
}

/** Chips for "close it" with nothing before it: the running approved apps, the one in front first. */
function pickAppChoices(ctx: IntentContext, verb: AppVerb): Choice[] {
  const apps = ctx.apps ?? [];
  const running = apps.filter((a) => a.running !== false);
  const ordered = [...running.filter((a) => a.windows?.some((w) => w.foreground)), ...running.filter((a) => !a.windows?.some((w) => w.foreground))];
  return ordered.slice(0, 4).map((a) => {
    const text = VERB_PHRASE[verb].replace("{}", appPhrase(a, apps));
    return choice(text.charAt(0).toUpperCase() + text.slice(1), text);
  });
}

/** "minimise YouTube" means the browser YouTube plays in, when that browser is an approved app. */
function youtubeAsApp(p: Piece, verb: AppVerb): PieceResult {
  const apps = p.ctx.apps ?? [];
  const browsers = browserApps(p.ctx);
  if (verb === "launch") return clarify("DoMe opens YouTube by searching it. For example “search YouTube for lofi beats”, or “play lofi beats on YouTube”.", browsers.slice(0, 2).map((a) => choice(`Open ${a.display_name}`, `open ${appPhrase(a, apps)}`)));
  if (verb === "fullscreen_on") return youtubeFullscreen(p);
  if (verb === "fullscreen_off") return youtubeExitFullscreen(p);
  if (verb === "close") return clarify("DoMe can't close a single tab. Closing the browser closes all of its tabs.", browsers.slice(0, 2).map((a) => choice(`Close ${a.display_name}`, `close ${appPhrase(a, apps)}`)));
  if (browsers.length === 1) return appSteps(p, verb, browsers[0]!);
  if (browsers.length > 1) return clarify("Which browser is YouTube in?", browsers.slice(0, 4).map((a) => choice(VERB_PHRASE[verb].replace("{}", a.display_name), VERB_PHRASE[verb].replace("{}", appPhrase(a, apps)))), APP);
  return clarify("To control the browser's window from here, approve it on the PC first: DoMe tray → Add an app…", [], APP);
}

type AppResolution = { kind: "app"; app: IntentApp } | { kind: "result"; result: PieceResult; strength: number };

/** Start of the "Did you mean …?" suggestion (the Assistant speaks it as a statement, butlerSpeech.ts). */
export const DID_YOU_MEAN = "Did you mean ";

const notApproved = (name: string): PieceResult => clarify(`“${name}” is not an approved app on this PC. Add it on the PC: DoMe tray → Add an app…`, [], { missingApp: name, topic: "app" });

function resolveAppSlot(p: Piece, verb: AppVerb, raw: string): AppResolution | null {
  // "the window", "the app" and "the video" are tested before the article goes: they are not names
  const full = unquote(raw).trim().toLowerCase();
  const name = unquote(raw).replace(/^(?:the|my|a|an) /i, "").trim();
  const lower = appNameKey(name);
  if (IT.test(full) || IT.test(lower)) {
    const last = p.ctx.lastAppId;
    const id = p.prev?.appId ?? (last && p.ctx.apps?.some((a) => a.app_id === last) ? last : null);
    if (id) return { kind: "app", app: p.ctx.apps?.find((a) => a.app_id === id) ?? { app_id: id, display_name: id } };
    // the example names one of the customer's own apps: the first chip (the app in front first), else any approved one
    const chips = pickAppChoices(p.ctx, verb);
    const fallback = VERB_PHRASE[verb].replace("{}", exampleAppPhrase(p.ctx.apps));
    const example = chips[0]?.label ?? fallback.charAt(0).toUpperCase() + fallback.slice(1);
    return { kind: "result", result: clarify(`Which app? Say “${example}”, for example.`, chips, APP), strength: 2 };
  }
  if (YT_OBJECT.test(full) || YT_OBJECT.test(lower)) return { kind: "result", result: youtubeAsApp(p, verb), strength: 3 };
  if (MANY_APPS.test(lower)) return { kind: "result", result: clarify("DoMe acts on one app at a time. Which one?", pickAppChoices(p.ctx, verb), APP), strength: 1 };
  if (!APP_NAME.test(lower) || NOT_A_NAME.test(lower)) return null;
  if (RESERVED_APP_WORDS.test(lower)) {
    const own = p.ctx.apps ? strongMatch(lower, p.ctx.apps) : null;
    if (own) return { kind: "app", app: own };
    // "open Spotify" names an app even when it is not approved: say so, as for any other name, never "unknown"
    return RESERVED_APP_BRANDS.test(lower) ? { kind: "result", result: notApproved(name), strength: 0 } : null;
  }
  if (!p.ctx.apps) return { kind: "result", result: clarify("I can't see the PC's approved apps yet. They load as soon as the PC is online.", [], { appsUnknown: true, missingApp: name, topic: "app" }), strength: 0 };
  const apps = p.ctx.apps;
  const found = matchApp(lower, apps);
  const chipsFor = (list: readonly IntentApp[]) => list.map((a) => choice(VERB_PHRASE[verb].replace("{}", a.display_name), VERB_PHRASE[verb].replace("{}", appPhrase(a, apps))));
  if (found.kind === "match") return { kind: "app", app: found.app };
  if (found.kind === "ambiguous") return { kind: "result", result: clarify(`Which one: ${found.apps.map((a) => a.display_name).join(" or ")}?`, chipsFor(found.apps), APP), strength: 2 };
  if (found.kind === "suggest") return { kind: "result", result: clarify(`${DID_YOU_MEAN}${found.apps.map((a) => a.display_name).join(" or ")}?`, chipsFor(found.apps), APP), strength: 1 };
  return { kind: "result", result: notApproved(name), strength: 0 };
}

const appRules: Rule = (p) => {
  let fallback: { result: PieceResult; strength: number } | null = null;
  for (const [re, form] of APP_FORMS) {
    const m = re.exec(p.text);
    if (!m) continue;
    const verb: AppVerb = form === "launch_fullscreen" || form === "launch_maximized" ? "launch" : form;
    const resolved = resolveAppSlot(p, verb, shown(p, m, "app"));
    if (!resolved) continue;
    if (resolved.kind === "result") {
      if (!fallback || resolved.strength > fallback.strength) fallback = resolved;
      continue;
    }
    if (form === "launch_fullscreen" || form === "launch_maximized") {
      const launch = step(p, "app.launch", { app_id: resolved.app.app_id });
      const next = appSteps({ ...p, prev: launch }, form === "launch_fullscreen" ? "fullscreen_on" : "maximize", resolved.app);
      return next.kind === "steps" ? steps([launch, ...next.steps]) : next;
    }
    return appSteps(p, verb, resolved.app);
  }
  return fallback?.result ?? null;
};

// --- help ------------------------------------------------------------------------------------------------------------------

/** The Type page's answer to "help" (the Assistant shows and speaks its own words for it, butlerSpeech.ts). */
export const HELP_MESSAGE = "Here are some things you can type. You can also join up to three with “and” or “then”.";

const helpRules: Rule = (p) => {
  if (!/^(?:help|what can (?:you|i) (?:do|say|type)|commands|examples|what can dome do)$/.test(p.text)) return null;
  return clarify(HELP_MESSAGE, [
    choice("Make the volume 100%", "make the volume 100%"),
    choice("Search YouTube for lofi beats", "search youtube for lofi beats"),
    choice("Play the second video", "play the second video"),
    choice("Skip ahead 30 seconds", "skip ahead 30 seconds"),
    choice("Lock my computer", "lock my computer"),
  ]);
};

/**
 * After an app step, a bare window phrase is about that app: "open ZStream and full screen", "… and maximise".
 * With no app before it, "full screen" stays YouTube's (view rules) and "maximise" alone is not a command.
 */
const AFTER_APP: ReadonlyArray<readonly [RegExp, string]> = [
  [new RegExp(`^(?:(?:go|switch to|turn on|enter|make it|put it in|go to) )?${FS}(?: mode)?(?: on)?$`), "make it full screen"],
  [/^(?:maximi[sz]e|max|enlarge)(?: it)?$/, "maximize it"],
  [/^(?:minimi[sz]e|hide)(?: it)?$/, "minimize it"],
  [/^(?:restore|unmaximi[sz]e|un maximi[sz]e)(?: it)?$/, "restore it"],
  [new RegExp(`^(?:(?:exit|leave|quit|end|escape|turn off|get out of|cancel|stop) ${FS}(?: mode)?|${FS} off|un ?${FS})$`), "exit full screen in it"],
  [/^(?:bring (?:it )?to the front|bring it forward|focus(?: on)? it|switch to it)$/, "bring it to the front"],
];

const afterAppRules: Rule = (p) => {
  if (!p.prev?.appId) return null;
  for (const [re, text] of AFTER_APP) if (re.test(p.text)) return appRules(reworded(p, text));
  return null;
};

/** Rule families in precedence order: specific YouTube/volume/PC phrases before the generic app and search forms. */
const RULES: readonly Rule[] = [helpRules, afterAppRules, pcRules, searchRules, playbackRules, seekRules, muteRules, volumeRules, viewRules, handoffRules, appRules, searchFallbackRules];
/** The only rules a part with INJECTION text may reach: those whose free text is a search query (checked there). */
const QUERY_RULES: readonly Rule[] = [searchRules, searchFallbackRules];

/** After "open notepad and …", a bare app name repeats the verb: "open notepad and chrome". */
const CARRY_VERB: Partial<Record<ActionName, string>> = { "app.launch": "open", "app.minimize": "minimize", "app.maximize": "maximize", "app.restore": "restore" };

function runRules(p: Piece, rules: readonly Rule[]): PieceResult | null {
  for (const rule of rules) {
    let r: PieceResult | null;
    try {
      r = rule(p);
    } catch {
      // A value the contract refuses (validateParams/validateTarget) is not a command; never a crash on the page.
      r = null;
    }
    if (r) return r;
  }
  return null;
}

const PICK_RESULT_FIRST = "Pick a result first: a search shows a list of videos, not a player. For example “play the first video and make it full screen”.";

function parsePiece(p: Piece): PieceResult | null {
  if (p.text.length > MAX_STEP_CHARS) return null;
  // "turn the volume up on my pc", "open notepad on the computer": the device is implied (but "search YouTube for
  // videos on my pc" searches for all of it)
  const device = / (?:on|in) (?:the |my )?(?:pc|computer|laptop)$/.exec(p.text);
  if (device) {
    const r = parsePiece({ ...p, text: p.text.slice(0, device.index), display: p.display.slice(0, device.index) });
    if (r && !(r.kind === "steps" && r.steps.some((s) => s.action === "youtube.search"))) return r;
  }
  const flagged = INJECTION.test(p.text);
  let r = runRules(p, flagged ? QUERY_RULES : RULES);
  const verb = p.prev ? CARRY_VERB[p.prev.action] : undefined;
  if (!r && !flagged && verb && APP_NAME.test(p.text)) {
    const carried = appRules(reworded(p, `${verb} ${p.text}`, `${verb} ${p.display}`));
    if (carried?.kind === "steps") r = carried;
  }
  // A search shows a list of videos, not a player: a player step right after it would reach the results page.
  if (r?.kind === "steps" && p.prev?.action === "youtube.search" && r.steps[0]?.needsTarget === "youtube") {
    return clarify(PICK_RESULT_FIRST, [choice(`Play the first video, then ${p.display}`, `play the first video and ${p.display}`)], SAFETY);
  }
  return r;
}

// --- compound commands ---------------------------------------------------------------------------------------------------

/** Joining words between steps. A split is only kept where the words after it are a command of their own. */
const JOINER = /\s*,\s*(?:and then |then |and |after that |and also )?|\s+(?:and then|then|and|after that|and also|afterwards)\s+/g;
/** Joiners that always start a new step. A bare "and" or comma may also be part of a search query ("pop and lock"). */
const EXPLICIT_JOINER = /\bthen\b|\bafter that\b|\bafterwards\b/;
const MAX_JOINERS = 6;

/** A place the text may split: [start, end) of the joining words, and whether they say "then". */
type Cut = [start: number, end: number, explicit: boolean];

/** Joiner positions outside double quotes ("search youtube for "pause and play"" never splits). */
function joiners(text: string): Cut[] {
  const out: Cut[] = [];
  for (const m of text.matchAll(JOINER)) {
    const before = text.slice(0, m.index);
    if ((before.match(/"/g) ?? []).length % 2 === 1) continue;
    if (m.index === 0 || m.index + m[0].length >= text.length) continue;
    out.push([m.index, m.index + m[0].length, EXPLICIT_JOINER.test(m[0])]);
    if (out.length === MAX_JOINERS) break;
  }
  return out;
}

interface Segment {
  start: number;
  end: number;
  result: PieceResult | null;
}

/**
 * Parses each part of one text once per previous step. A part's meaning depends on the step before it only through
 * that step's action and app ("maximise it", "play the first video" after a search), so that pair is the key.
 */
type PieceMemo = Map<string, PieceResult | null>;

interface Source {
  text: string;
  display: string;
  typed(a: number, b: number): string;
}

function memoPiece(memo: PieceMemo, src: Source, ctx: IntentContext, start: number, end: number, prev: PlanStep | null): PieceResult | null {
  const key = `${start}:${end}:${prev ? `${prev.action}|${prev.appId ?? ""}` : ""}`;
  if (!memo.has(key)) memo.set(key, parsePiece({ text: src.text.slice(start, end), display: src.display.slice(start, end), typed: (a, b) => src.typed(start + a, start + b), ctx, prev }));
  return memo.get(key) ?? null;
}

function parseSegments(memo: PieceMemo, src: Source, ctx: IntentContext, bounds: Array<[number, number]>): Segment[] {
  const out: Segment[] = [];
  let prev: PlanStep | null = null;
  for (const [start, end] of bounds) {
    const result = memoPiece(memo, src, ctx, start, end, prev);
    out.push({ start, end, result });
    prev = result?.kind === "steps" ? (result.steps[result.steps.length - 1] ?? null) : null;
    if (!result) break;
  }
  return out;
}

function boundsFor(length: number, cuts: readonly Cut[], mask: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let start = 0;
  cuts.forEach(([a, b], i) => {
    if (mask & (1 << i)) {
      out.push([start, a]);
      start = b;
    }
  });
  out.push([start, length]);
  return out;
}

/** Chip text for a whole compound command with one part replaced. */
function withPart(display: string, seg: Segment, text: string): string {
  return `${display.slice(0, seg.start)}${text}${display.slice(seg.end)}`;
}

const RESULT_TAB_ACTIONS: ReadonlySet<string> = new Set([...registry.actions().values()].filter((s) => s.resultName === "youtube_state_result" || s.resultName === "youtube_search_result").map((s) => s.name));

function stepKey(s: PlanStep): string {
  return `${s.action}|${s.needsTarget ?? ""}|${JSON.stringify(s.target)}|${s.action === "app.launch" ? String(s.params.app_id) : ""}`;
}

const lastStep = (seg: Segment): PlanStep | null => (seg.result?.kind === "steps" ? (seg.result.steps.at(-1) ?? null) : null);
const firstStep = (seg: Segment): PlanStep | null => (seg.result?.kind === "steps" ? (seg.result.steps[0] ?? null) : null);

/**
 * A split at a bare "and" or comma right after an open-ended search query cuts the query in two ("search YouTube
 * for pop and lock" is one search, never a search and a lock). Only picking a result may follow that way ("… and
 * play the first one"); anything else needs "then".
 */
function cutsQuery(segments: readonly Segment[], used: readonly Cut[]): boolean {
  return used.some((cut, k) => {
    const before = lastStep(segments[k]!);
    return !cut[2] && !!before && OPEN_ENDED.has(before) && firstStep(segments[k + 1]!)?.action !== "youtube.open_result";
  });
}

/** An open-ended search query that swallowed a "then" ("search for cats then mute" → the query "cats then mute"). */
function swallowsThen(segments: readonly Segment[], unused: readonly Cut[]): boolean {
  return segments.some((seg) => {
    const last = lastStep(seg);
    return !!last && OPEN_ENDED.has(last) && unused.some(([a, , explicit]) => explicit && a > seg.start && a < seg.end);
  });
}

/**
 * Opening a result plays a video; one already playing in another tab would keep playing under it. DoMe pauses that
 * one first, as a step of its own that the preview lists (unless the command pauses YouTube before it anyway). It
 * goes before the search a result comes from, so the steps that use a previous step's tab stay next to each other.
 */
function pauseOtherVideo(list: PlanStep[], ctx: IntentContext): PlanStep[] {
  const i = list.findIndex((s) => s.action === "youtube.open_result");
  if (i < 0) return list;
  const open = list[i]!;
  const other = open.fromPrevious ? ctx.playingElsewhere?.afterSearch : ctx.playingElsewhere?.lastResults;
  if (!other || list.slice(0, i).some((s) => s.action === "youtube.set_paused" && s.params.paused === true)) return list;
  const at = open.fromPrevious && list[i - 1]?.action === "youtube.search" ? i - 1 : i;
  const what = other.title ? `“${other.title}”` : "A video";
  const text = "pause the video playing in the other tab";
  const pause = step({ text, display: text, typed: () => text, ctx, prev: null }, "youtube.set_paused", { paused: true }, { needsTarget: "playing_elsewhere", detail: `${what} is playing in another tab; paused first so two videos don't play at once` });
  return [...list.slice(0, at), { ...pause, added: true }, ...list.slice(at)];
}

/**
 * Parse typed text into a plan of up to three steps, a clarification, a handoff to the Touchpad, or unknown.
 * Pure and deterministic: the same text and context always give the same answer.
 */
export function parseCommand(raw: unknown, ctx: IntentContext = {}): CommandParse {
  if (typeof raw !== "string") return UNKNOWN;
  const src = normalizeParts(raw);
  const { text, display } = src;
  if (!text || text.length > MAX_COMMAND_CHARS) return UNKNOWN;

  const cuts = joiners(text);
  const memo: PieceMemo = new Map();
  let best: { segments: Segment[]; allSteps: boolean; swallows: boolean } | null = null;
  for (let mask = 0; mask < 1 << cuts.length; mask++) {
    const segments = parseSegments(memo, src, ctx, boundsFor(text.length, cuts, mask));
    if (segments.length !== popcount(mask) + 1 || segments.some((s) => !s.result)) continue;
    const used = cuts.filter((_, i) => mask & (1 << i));
    if (cutsQuery(segments, used)) continue;
    const swallows = swallowsThen(segments, cuts.filter((_, i) => !(mask & (1 << i))));
    const allSteps = segments.every((s) => s.result?.kind === "steps");
    // Prefer a search that keeps to its own words, then parts that all run over parts that ask something, then more
    // parts over fewer: "search for cats then mute" asks what to mute rather than searching for "cats then mute".
    const better = !best || (swallows !== best.swallows ? !swallows : allSteps !== best.allSteps ? allSteps : segments.length > best.segments.length);
    if (better) best = { segments, allSteps, swallows };
  }

  if (!best) return INJECTION.test(text) ? { kind: "unknown", message: INJECTION_MESSAGE } : unknownWithParts(memo, src, ctx, cuts);

  const { segments } = best;
  const multi = segments.length > 1;
  for (const seg of segments) {
    const r = seg.result!;
    if (r.kind === "steps") continue;
    const choices = multi ? r.choices.map((c) => ({ label: c.label, text: withPart(display, seg, c.text) })) : r.choices;
    return { ...r, choices };
  }

  const planSteps = segments.flatMap((s) => (s.result?.kind === "steps" ? s.result.steps : []));
  const choices = segments.flatMap((seg) => (seg.result?.kind === "steps" ? seg.result.choices.map((c) => (multi ? { label: c.label, text: withPart(display, seg, c.text) } : c)) : []));
  return assemblePlan(planSteps, choices, ctx);
}

/**
 * Fixed parts of the plan-level and suggestion messages, exported so the Assistant can speak its own fixed lines for
 * them (butlerSpeech.ts): the on-screen messages quote the owner's words, the spoken ones never do.
 */
export const TOO_MANY_STEPS = `DoMe runs up to ${MAX_PLAN_STEPS} steps at a time. Send the rest as a separate command.`;
export const CONFIRM_ALONE = " needs your confirmation on the phone, so send it on its own.";
export const SAME_THING = " control the same thing. Send one of them.";
export const UNDERSTOOD_PART = "I understood ";

/**
 * The plan-level rules every plan obeys, however its steps were found (typed words or the assistant's steps): at
 * most three steps, a step the PC asks to confirm only on its own, no two steps controlling the same thing, a YouTube
 * step after one that reported a tab acting on that tab, and the video playing elsewhere paused before a result opens.
 */
export function assemblePlan(planSteps: PlanStep[], choices: Choice[], ctx: IntentContext): CommandParse {
  if (planSteps.length > MAX_PLAN_STEPS) return { kind: "clarify", message: TOO_MANY_STEPS, choices: [], link: null, topic: "safety" };
  if (planSteps.length > 1) {
    const confirmed = planSteps.find((s) => s.confirmation);
    if (confirmed) return { kind: "clarify", message: `“${confirmed.text}”${CONFIRM_ALONE}`, choices: expressible(confirmed.action) ? [choice(confirmed.text, confirmed.text)] : [], link: null, topic: "safety" };
    const seen = new Map<string, PlanStep>();
    for (const s of planSteps) {
      const twin = seen.get(stepKey(s));
      if (twin) return { kind: "clarify", message: `“${twin.text}” and “${s.text}”${SAME_THING}`, choices: [], link: null, topic: "safety" };
      seen.set(stepKey(s), s);
    }
  }
  // A YouTube step right after another one acts on the tab that step reported (an opened result's tab).
  const linked = planSteps.map((s, i) => (i > 0 && s.needsTarget === "youtube" && !s.fromPrevious && RESULT_TAB_ACTIONS.has(planSteps[i - 1]!.action) ? { ...s, fromPrevious: true } : s));
  return { kind: "plan", steps: pauseOtherVideo(linked, ctx), choices };
}

function popcount(n: number): number {
  let c = 0;
  for (let v = n; v; v &= v - 1) c++;
  return c;
}

/** Nothing parsed: name the first part that was not understood when other parts were. */
function unknownWithParts(memo: PieceMemo, src: Source, ctx: IntentContext, cuts: readonly Cut[]): CommandParse {
  if (cuts.length === 0) return UNKNOWN;
  const bounds = boundsFor(src.text.length, cuts, (1 << cuts.length) - 1);
  const parts: Array<{ shown: string; ok: boolean }> = [];
  let prev: PlanStep | null = null;
  for (const [start, end] of bounds) {
    const r = memoPiece(memo, src, ctx, start, end, prev);
    parts.push({ shown: src.display.slice(start, end), ok: !!r });
    prev = r?.kind === "steps" ? (r.steps[r.steps.length - 1] ?? null) : null;
  }
  const missing = parts.find((x) => !x.ok);
  const understood = parts.filter((x) => x.ok).map((x) => `“${x.shown}”`);
  if (!missing || understood.length === 0) return UNKNOWN;
  return { kind: "unknown", message: `${UNDERSTOOD_PART}${understood.join(" and ")} but not “${missing.shown}”.` };
}

// --- the assistant's steps -------------------------------------------------------------------------------------------

/** Actions the grammar has no words for: their `commandText` is readable, but never offered as a chip to tap. */
const NO_COMMAND_WORDS: ReadonlySet<string> = new Set(["media.get_sessions", "youtube.get_state", "youtube.list_tabs"]);

/** True when `commandText` of this action parses back to it (so a chip may carry it). */
export function expressible(action: string): boolean {
  return !NO_COMMAND_WORDS.has(action);
}

/** Seconds as a clock time the grammar reads back: 90 → "1:30", 3725 → "1:02:05". */
function clock(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** A search query as words that parse back to exactly that query (quoted, so "and", "then" or "please" stay in it). */
function queryWords(query: string): string {
  return query.includes('"') || [...query].length > MAX_QUERY - 1 ? query : `"${query}"`;
}

/**
 * A canonical command for a step, in words `parseCommand` reads back as the same action and params (for the same
 * approved apps and, for a result, the same search). It is the `text` of steps the assistant proposed, so chips and
 * messages built from `step.text` work as for typed commands. Never spoken: butlerSpeech.ts has its own phrases.
 */
export function commandText(s: PlanStep, apps: readonly IntentApp[] | null | undefined): string {
  const p = s.params;
  const list = apps ?? [];
  const id = s.appId ?? (typeof s.target?.app_id === "string" ? s.target.app_id : typeof p.app_id === "string" ? p.app_id : null);
  const app = id ? list.find((a) => a.app_id === id) : undefined;
  const name = app ? appPhrase(app, list) : (id ?? "it");
  const n = (v: JsonValue | undefined) => (typeof v === "number" ? v : 0);
  switch (s.action) {
    case "app.launch":
      return `open ${name}`;
    case "app.focus":
      return `bring ${name} to the front`;
    case "app.minimize":
      return `minimize ${name}`;
    case "app.maximize":
      return `maximize ${name}`;
    case "app.restore":
      return `restore ${name}`;
    case "app.set_fullscreen":
      return p.enabled ? `full screen ${name}` : `exit full screen in ${name}`;
    case "app.close":
      return `close ${name}`;
    case "app.list":
      return "list my apps";
    case "media.get_sessions":
      return "list the media players";
    case "media.next":
      return "next track";
    case "media.previous":
      return "previous track";
    case "media.set_paused":
      return p.paused ? "pause the media player" : "play the media player";
    case "power.cancel":
      return "cancel the countdown";
    case "power.restart":
      return "restart my pc";
    case "power.shutdown":
      return "shut down my pc";
    case "power.sleep":
      return "put my computer to sleep";
    case "system.get_status":
      return "refresh the status";
    case "system.ping":
      return "ping my pc";
    case "windows.get_volume":
      return "what is the pc volume";
    case "windows.lock":
      return "lock my computer";
    case "windows.set_muted":
      return p.muted ? "mute the pc" : "unmute the pc";
    case "windows.set_volume":
      return `set the pc volume to ${n(p.value)}`;
    case "youtube.get_state":
      return "read the youtube player";
    case "youtube.list_tabs":
      return "list the youtube tabs";
    case "youtube.next":
      return "next video";
    case "youtube.previous":
      return "previous video";
    case "youtube.open_result":
      return `play the ${ordinalWord(n(p.index))} video`;
    case "youtube.request_fullscreen":
      return "make the video full screen";
    case "youtube.search":
      return `search youtube for ${queryWords(typeof p.query === "string" ? p.query : "")}`;
    case "youtube.seek_relative":
      return n(p.seconds) < 0 ? `go back ${-n(p.seconds)} seconds` : `skip ahead ${n(p.seconds)} seconds`;
    case "youtube.seek_to":
      return n(p.position_seconds) === 0 ? "start over" : `jump to ${clock(n(p.position_seconds))}`;
    case "youtube.set_muted":
      return p.muted ? "mute youtube" : "unmute youtube";
    case "youtube.set_paused":
      return p.paused ? "pause youtube" : "play youtube";
    case "youtube.set_theater":
      return p.enabled ? "theater mode on" : "theater mode off";
    case "youtube.set_volume":
      return `set youtube volume to ${n(p.value)}`;
    case "input.session_start":
    case "input.session_stop":
      return "use the touchpad";
  }
}

/** A step as POST /v1/assistant/turn returns it (rest.schema.json `assistant_step`): untrusted until checked here. */
export interface AssistantStepInput {
  action: string;
  params: Record<string, JsonValue>;
  app?: string;
}

/** The answer to any step DoMe will not take from the assistant (unknown, human-only, wrong params or fields). */
export const NOT_FROM_HERE = "That isn't something DoMe can do from here.";

const APP_VERBS: Partial<Record<ActionName, AppVerb>> = { "app.launch": "launch", "app.focus": "focus", "app.minimize": "minimize", "app.maximize": "maximize", "app.restore": "restore", "app.close": "close" };
/** Parameters the assistant never sets: the phone fills `video_id` from its own results; power keeps its 10 s countdown. */
const WITHHELD: Partial<Record<ActionName, string>> = { "youtube.open_result": "video_id", "power.sleep": "countdown_seconds", "power.restart": "countdown_seconds", "power.shutdown": "countdown_seconds" };

const isAppAction = (name: ActionName) => name === "app.launch" || registry.get(name).targetName === "window_target";

/** One assistant step as plan steps (or why not), built by the grammar's own functions with `prev` before it. */
function assistantPiece(raw: AssistantStepInput, ctx: IntentContext, prev: PlanStep | null): PieceResult {
  const refuse = clarify(NOT_FROM_HERE, [], SAFETY);
  if (typeof raw !== "object" || raw === null || typeof raw.action !== "string" || !registry.has(raw.action)) return refuse;
  const name: ActionName = raw.action;
  if (!registry.get(name).aiEligible) return refuse;
  const params = raw.params;
  if (typeof params !== "object" || params === null || Array.isArray(params)) return refuse;
  const withheld = WITHHELD[name];
  if (withheld && withheld in params) return refuse;
  const appAction = isAppAction(name);
  if (appAction !== (raw.app !== undefined) || (appAction && typeof raw.app !== "string")) return refuse;
  if (name === "app.launch" && Object.keys(params).length > 0) return refuse; // the phone adds app_id from its own match
  let validated: Record<string, unknown> = {};
  try {
    if (name !== "app.launch") validated = registry.validateParams(name, params);
  } catch {
    return refuse;
  }
  const p: Piece = { text: "", display: "", typed: () => "", ctx, prev };
  if (appAction) {
    const spoken = raw.app!;
    const verb: AppVerb = name === "app.set_fullscreen" ? (validated.enabled ? "fullscreen_on" : "fullscreen_off") : APP_VERBS[name]!;
    const resolved = resolveAppSlot(p, verb, spoken);
    if (!resolved) return notApproved(spoken.trim());
    return resolved.kind === "result" ? resolved.result : appSteps(p, verb, resolved.app);
  }
  if (name === "youtube.open_result") return openResult(p, Number(validated.index));
  if (name === "youtube.request_fullscreen") return youtubeFullscreen(p);
  const target = registry.get(name).targetName;
  const needsTarget: TargetSource | undefined = name === "youtube.search" ? "youtube_browser" : target === "youtube_target" ? "youtube" : target === "media_session_target" ? "media" : undefined;
  return steps([step(p, name, params, needsTarget ? { needsTarget } : {})]);
}

/**
 * Steps the assistant proposed (docs/design/assistant.md §4.4) as the same plan the grammar would build: every
 * action checked against the registry here again (AI-eligible, params, no fields it may not set), app names resolved
 * against the PC's approved apps on this phone ("it" = the app of the step before, else `ctx.lastAppId`), targets
 * chosen like typed steps, then the same plan rules (`assemblePlan`). Anything DoMe will not run becomes a
 * clarification: never a partial plan, never a guess.
 */
export function planFromAssistant(input: readonly AssistantStepInput[], ctx: IntentContext): CommandParse {
  if (input.length === 0) return clarify(NOT_FROM_HERE, [], SAFETY) as CommandParse;
  if (input.length > MAX_PLAN_STEPS) return { kind: "clarify", message: TOO_MANY_STEPS, choices: [], link: null, topic: "safety" };
  const built: PlanStep[] = [];
  const choices: Choice[] = [];
  for (const raw of input) {
    const prev = built.at(-1) ?? null;
    let r: PieceResult;
    try {
      r = assistantPiece(raw, ctx, prev);
    } catch {
      // A value the contract refuses (validateParams/validateTarget) is not a step; never a crash.
      r = clarify(NOT_FROM_HERE, [], SAFETY);
    }
    if (r.kind !== "steps") return r as CommandParse;
    const made = r.steps.map((s) => ({ ...s, text: commandText(s, ctx.apps) }));
    // A search shows a list of videos, not a player: a player step right after it would reach the results page.
    if (prev?.action === "youtube.search" && made[0]?.needsTarget === "youtube") {
      const chip = expressible(made[0].action) ? [choice(`Play the first video, then ${made[0].text}`, `${prev.text} and play the first video and ${made[0].text}`)] : [];
      return clarify(PICK_RESULT_FIRST, chip, SAFETY) as CommandParse;
    }
    built.push(...made);
    choices.push(...r.choices);
  }
  return assemblePlan(built, choices, ctx);
}

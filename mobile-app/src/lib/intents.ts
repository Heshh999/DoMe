/**
 * Deterministic text commands (spec §13). No language model is involved: the text is normalised and
 * matched against an ordered rule table; anything that does not match exactly one rule is `unknown`
 * or a clarification. Media titles and other remote text never reach this parser; it only sees what
 * the customer typed, and even that can never become a path, argument or shell text — the output is
 * a registry action name plus validated parameters.
 */
import { registry, type ActionName, type JsonValue } from "@dome/protocol";

export interface IntentContext {
  /** Approved apps from the PC's `app.list` (untrusted display names, matched case-insensitively). */
  apps?: ReadonlyArray<{ app_id: string; display_name: string }> | null;
  /** Last known Windows volume (for "volume up/down"). */
  pcVolume?: number | null;
  /** Last known YouTube player volume for the selected tab. */
  youtubeVolume?: number | null;
}

export type TargetKind = "youtube" | "media";

export type Intent =
  | {
      kind: "action";
      action: ActionName;
      params: Record<string, JsonValue>;
      /** Window targets are fixed here; YouTube/media targets are chosen from live state at send time. */
      target: Record<string, JsonValue> | null;
      needsTarget: TargetKind | null;
      /** Short sentence used for the preview: "Pause YouTube". */
      summary: string;
      /** The PC will issue a confirmation challenge before running this. */
      confirmation: boolean;
    }
  | { kind: "clarify"; question: string }
  | { kind: "unknown" };

const UNITS: Record<string, number> = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19,
};
const TENS: Record<string, number> = { twenty: 20, thirty: 30, forty: 40, fourty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };

/** "35", "thirty five", "thirty-five", "a hundred" → 0–100 (or any digits); null when not a number. */
export function parseNumberWords(text: string): number | null {
  const t = text.trim().toLowerCase().replace(/-/g, " ").replace(/\s+/g, " ");
  if (t === "") return null;
  if (/^\d+$/.test(t)) return Number(t);
  if (t === "a hundred" || t === "one hundred" || t === "hundred") return 100;
  const words = t.split(" ");
  if (words.length === 1) {
    const w = words[0]!;
    if (w in UNITS) return UNITS[w]!;
    if (w in TENS) return TENS[w]!;
    return null;
  }
  if (words.length === 2) {
    const [a, b] = words as [string, string];
    if (a in TENS && b in UNITS && UNITS[b]! < 10) return TENS[a]! + UNITS[b]!;
  }
  return null;
}

const NUM = String.raw`(\d{1,3}|(?:[a-z]+(?:[ -][a-z]+)?))`;

export function normalizeText(text: string): string {
  let t = text
    .toLowerCase()
    .replace(/[‘’“”]/g, "'")
    .replace(/%/g, " percent")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[.!?,;:]+$/g, "")
    .trim();
  // politeness / wake words that do not change meaning (applied twice so "…, please?" fully unwraps)
  for (let i = 0; i < 2; i++) {
    t = t.replace(/^(?:(?:hey|ok|okay)\s+)?(?:dome[,]?\s+)?(?:please\s+)?(?:can you|could you|would you|will you)?\s*/, "").trim();
    t = t.replace(/,?\s+(?:please|now|for me|thanks|thank you)$/g, "").trim();
    t = t.replace(/^(?:please\s+)/, "").trim();
    t = t.replace(/[.!?,;:]+$/g, "").trim();
  }
  return t;
}

const INJECTION = /(ignore|instruction|disregard|override|system prompt|https?:|www\.|<|>|\{|\}|`|\$\(|;|&&|\|\|)/i;
// No `g` flag: a global regex used with `.test()` keeps `lastIndex` between calls, so the second
// segment would be scanned from where the first match ended and the multi-verb guard would never fire.
const VERB = /\b(pause|play|resume|skip|next|previous|back|rewind|forward|open|launch|start|close|quit|lock|sleep|shut|shutdown|restart|reboot|mute|unmute|set|turn|put|go|seek|focus|minimi[sz]e|hide|show|switch|cancel|stop|volume|louder|quieter|wake|ping|check)\b/;

function action(name: ActionName, params: Record<string, JsonValue>, summary: string, opts: { target?: Record<string, JsonValue> | null; needsTarget?: TargetKind | null } = {}): Intent {
  const spec = registry.get(name);
  const validated = registry.validateParams(name, params) as Record<string, JsonValue>;
  return { kind: "action", action: name, params: validated, target: opts.target ?? null, needsTarget: opts.needsTarget ?? null, summary, confirmation: spec.confirmation === "challenge" };
}

const clarify = (question: string): Intent => ({ kind: "clarify", question });
const UNKNOWN: Intent = { kind: "unknown" };

function seconds(amount: string, unit: string): number | null {
  const n = parseNumberWords(amount);
  if (n === null || n <= 0) return null;
  const s = /^min/.test(unit) ? n * 60 : n;
  return s > 600 ? null : s;
}

function findApp(name: string, ctx: IntentContext): { app_id: string; display_name: string } | "unknown-list" | null {
  if (!ctx.apps) return "unknown-list";
  const needle = name.trim().toLowerCase();
  if (!needle) return null;
  return ctx.apps.find((a) => a.display_name.toLowerCase() === needle || a.app_id.toLowerCase() === needle) ?? null;
}

const APP_WORDS = String.raw`([a-z0-9][a-z0-9 ._-]{0,40})`;

export function parseIntent(rawText: string, ctx: IntentContext = {}): Intent {
  if (typeof rawText !== "string") return UNKNOWN;
  const text = normalizeText(rawText);
  if (!text || text.length > 120) return UNKNOWN;
  if (INJECTION.test(text)) return UNKNOWN;
  // Two verb phrases joined ("pause youtube and shut down") never match a single rule.
  const segments = text.split(/\b(?:and|then|after that)\b|,/);
  if (segments.length > 1 && segments.filter((s) => VERB.test(s) && !/^\s*$/.test(s)).length > 1) return UNKNOWN;

  let m: RegExpExecArray | null;

  // --- connection ---------------------------------------------------------------------------------
  if (/^(?:ping|ping (?:the |my )?(?:pc|computer)|check (?:the )?connection|are you there|is (?:the |my )?(?:pc|computer) (?:online|connected))$/.test(text)) {
    return action("system.ping", {}, "Check the connection to the PC");
  }

  // --- YouTube play/pause ---------------------------------------------------------------------------
  if (/^(?:pause|pause (?:the )?(?:video|youtube)|pause youtube|stop(?: the)? video|stop youtube)$/.test(text)) {
    return action("youtube.set_paused", { paused: true }, "Pause YouTube", { needsTarget: "youtube" });
  }
  if (/^(?:play|resume|unpause|continue|play (?:the )?(?:video|youtube)|resume (?:the )?(?:video|youtube)|play youtube)$/.test(text)) {
    return action("youtube.set_paused", { paused: false }, "Play YouTube", { needsTarget: "youtube" });
  }
  // --- Windows media sessions -----------------------------------------------------------------------
  if (/^(?:pause|stop) (?:the |my )?(?:music|media|song|player|spotify)$/.test(text)) {
    return action("media.set_paused", { paused: true }, "Pause the media player", { needsTarget: "media" });
  }
  if (/^(?:play|resume|unpause) (?:the |my )?(?:music|media|song|player|spotify)$/.test(text)) {
    return action("media.set_paused", { paused: false }, "Play the media player", { needsTarget: "media" });
  }
  if (/^(?:next|skip) (?:track|song)$/.test(text)) return action("media.next", {}, "Next track", { needsTarget: "media" });
  if (/^(?:previous|last|prev) (?:track|song)$/.test(text) || text === "go back a track") return action("media.previous", {}, "Previous track", { needsTarget: "media" });

  // --- YouTube next/previous ------------------------------------------------------------------------
  if (/^(?:skip|next|skip this|skip this video|skip (?:the )?video|next video|skip it|skip this one|play (?:the )?next video|next one)$/.test(text)) {
    return action("youtube.next", {}, "Skip to the next video", { needsTarget: "youtube" });
  }
  if (/^(?:previous|previous video|last video|go back a video|back a video|go to (?:the )?previous video|play (?:the )?previous video)$/.test(text)) {
    return action("youtube.previous", {}, "Go to the previous video", { needsTarget: "youtube" });
  }

  // --- seek -------------------------------------------------------------------------------------------
  m = new RegExp(String.raw`^(?:go back|back|rewind|skip back|jump back)(?: by)?(?: ${NUM} (seconds?|secs?|minutes?|mins?))?$`).exec(text);
  if (m) {
    if (!m[1]) return action("youtube.seek_relative", { seconds: -10 }, "Go back 10 seconds", { needsTarget: "youtube" });
    const s = seconds(m[1], m[2] ?? "seconds");
    if (s === null) return clarify("How far back? Up to 10 minutes, for example “go back 30 seconds”.");
    return action("youtube.seek_relative", { seconds: -s }, `Go back ${s} seconds`, { needsTarget: "youtube" });
  }
  m = new RegExp(String.raw`^(?:skip ahead|skip forward|go forward|forward|fast forward|jump ahead|jump forward|skip)(?: by)?(?: ${NUM} (seconds?|secs?|minutes?|mins?))?$`).exec(text);
  if (m) {
    if (!m[1]) return action("youtube.seek_relative", { seconds: 10 }, "Skip ahead 10 seconds", { needsTarget: "youtube" });
    const s = seconds(m[1], m[2] ?? "seconds");
    if (s === null) return clarify("How far ahead? Up to 10 minutes, for example “skip ahead 30 seconds”.");
    return action("youtube.seek_relative", { seconds: s }, `Skip ahead ${s} seconds`, { needsTarget: "youtube" });
  }

  // --- volume: relative -------------------------------------------------------------------------------
  m = /^(?:turn (?:the |my )?(youtube|video|player|pc|computer)? ?(?:volume )?(up|down)|(youtube|video|player|pc|computer)? ?volume (up|down)|louder|quieter|softer)$/.exec(text);
  if (m) {
    const scope = m[1] ?? m[3] ?? "";
    const dir = m[2] ?? m[4] ?? (text === "louder" ? "up" : "down");
    const delta = dir === "up" ? 10 : -10;
    if (/^(youtube|video|player)$/.test(scope)) {
      if (ctx.youtubeVolume === null || ctx.youtubeVolume === undefined) return clarify("I don't know the current YouTube volume yet. Try “set YouTube volume to 40”.");
      const v = Math.min(100, Math.max(0, ctx.youtubeVolume + delta));
      return action("youtube.set_volume", { value: v }, `Set YouTube volume to ${v}%`, { needsTarget: "youtube" });
    }
    if (ctx.pcVolume === null || ctx.pcVolume === undefined) return clarify("I don't know the current PC volume yet. Try “set volume to 40”.");
    const v = Math.min(100, Math.max(0, ctx.pcVolume + delta));
    return action("windows.set_volume", { value: v }, `Set PC volume to ${v}%`);
  }

  // --- volume: absolute -------------------------------------------------------------------------------
  m = new RegExp(String.raw`^(?:set|change|put|turn) (?:the |my )?(youtube|video|player|pc|computer|windows|system|speaker)? ?volume to ${NUM}(?: percent)?$`).exec(text);
  if (!m) m = new RegExp(String.raw`^(youtube|video|player|pc|computer|windows|system|speaker)? ?volume ${NUM}(?: percent)?$`).exec(text);
  if (m) {
    const n = parseNumberWords(m[2]!);
    if (n === null || n < 0 || n > 100) return clarify("Volume must be between 0 and 100 percent.");
    if (m[1] && /^(youtube|video|player)$/.test(m[1])) return action("youtube.set_volume", { value: n }, `Set YouTube volume to ${n}%`, { needsTarget: "youtube" });
    return action("windows.set_volume", { value: n }, `Set PC volume to ${n}%`);
  }
  // --- mute ---------------------------------------------------------------------------------------------
  m = /^(un)?mute(?: (?:the |my )?(youtube|video|player|pc|computer|windows|system|speakers?|sound))?$/.exec(text);
  if (m) {
    const muted = !m[1];
    if (!m[2]) return clarify(`${muted ? "Mute" : "Unmute"} YouTube or the PC? Say “${muted ? "mute" : "unmute"} YouTube” or “${muted ? "mute" : "unmute"} the PC”.`);
    if (/^(youtube|video|player)$/.test(m[2])) return action("youtube.set_muted", { muted }, muted ? "Mute YouTube" : "Unmute YouTube", { needsTarget: "youtube" });
    return action("windows.set_muted", { muted }, muted ? "Mute the PC" : "Unmute the PC");
  }

  // --- theater / fullscreen -------------------------------------------------------------------------
  m = /^(?:(?:turn |switch )?theat(?:er|re) mode (on|off)|theat(?:er|re) (on|off)|(enable|disable) theat(?:er|re)(?: mode)?)$/.exec(text);
  if (m) {
    const on = (m[1] ?? m[2] ?? (m[3] === "enable" ? "on" : "off")) === "on";
    return action("youtube.set_theater", { enabled: on }, on ? "Turn theater mode on" : "Turn theater mode off", { needsTarget: "youtube" });
  }
  if (/^(?:fullscreen|full screen|go fullscreen|make it fullscreen)$/.test(text)) {
    return action("youtube.request_fullscreen", {}, "Ask the browser for fullscreen", { needsTarget: "youtube" });
  }

  // --- lock ---------------------------------------------------------------------------------------------
  if (/^(?:lock|lock (?:the |my )?(?:pc|computer|windows|screen|workstation))$/.test(text)) return action("windows.lock", {}, "Lock Windows");

  // --- power (confirmation-required; never pre-confirmed) -------------------------------------------------
  if (/^(?:sleep|go to sleep|put (?:the |my )?(?:pc|computer) to sleep|sleep (?:the |my )?(?:pc|computer)|suspend(?: (?:the |my )?(?:pc|computer))?)$/.test(text)) {
    return action("power.sleep", {}, "Put the PC to sleep (you will confirm first)");
  }
  if (/^(?:shut ?down|shut (?:the |my )?(?:pc|computer) down|shut ?down (?:the |my )?(?:pc|computer)|turn (?:the |my )?(?:pc|computer) off|turn off (?:the |my )?(?:pc|computer)|power off(?: (?:the |my )?(?:pc|computer))?)$/.test(text)) {
    return action("power.shutdown", {}, "Shut down the PC (you will confirm first)");
  }
  if (/^(?:restart|reboot|restart (?:the |my )?(?:pc|computer)|reboot (?:the |my )?(?:pc|computer))$/.test(text)) {
    return action("power.restart", {}, "Restart the PC (you will confirm first)");
  }
  if (/^(?:cancel|cancel (?:the )?(?:shutdown|shut down|restart|sleep|countdown|power ?(?:action|request)))$/.test(text)) {
    return action("power.cancel", {}, "Cancel the pending power countdown");
  }

  // --- apps -------------------------------------------------------------------------------------------------
  if (/^close (?:that|it|this|the window|this window|that window|the app|this app)$/.test(text)) return clarify("Which app should I close? Say “close Notepad”, for example.");
  m = new RegExp(String.raw`^(open|launch|start|run|focus|focus on|switch to|show|bring up|minimi[sz]e|hide|close|quit) ${APP_WORDS}$`).exec(text);
  if (m) {
    const verb = m[1]!;
    const found = findApp(m[2]!, ctx);
    if (found === "unknown-list") return clarify("I can't see the PC's approved apps yet. Wait for the PC to connect, then try again.");
    if (!found) return clarify(`“${m[2]!.trim()}” is not an approved app on this PC. Approve it on the PC first (DoMe tray → Approved apps).`);
    const target = { app_id: found.app_id };
    if (/^(open|launch|start|run)$/.test(verb)) return action("app.launch", { app_id: found.app_id }, `Open ${found.display_name}`);
    if (/^(focus|focus on|switch to|show|bring up)$/.test(verb)) return action("app.focus", {}, `Bring ${found.display_name} to the front`, { target });
    if (/^(minimi[sz]e|hide)$/.test(verb)) return action("app.minimize", {}, `Minimise ${found.display_name}`, { target });
    return action("app.close", {}, `Close ${found.display_name} (you will confirm first)`, { target });
  }

  return UNKNOWN;
}

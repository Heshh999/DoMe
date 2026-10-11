/**
 * Words → values for typed commands (spec §13): text normalisation, numbers, volume levels, durations, positions and
 * ordinals. Pure functions with no knowledge of actions; `intents.ts` decides what the values mean. Everything here
 * is deterministic: the same text always gives the same value, and anything not recognised is `null` (never a guess).
 */

const UNITS: Record<string, number> = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19,
};
const TENS: Record<string, number> = { twenty: 20, thirty: 30, forty: 40, fourty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };

/** "35", "thirty five", "thirty-five", "a hundred" → the number; null when not a number. */
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

/** A number that may be "a"/"an" (one) or a decimal ("1.5"); used for durations. */
function parseCount(text: string): number | null {
  const t = text.trim();
  if (t === "a" || t === "an") return 1;
  if (/^\d+(?:\.\d+)?$/.test(t)) return Number(t);
  return parseNumberWords(t);
}

/**
 * A volume level: 0–100, a number word, or an extreme ("max", "full", "half", "a quarter", "all the way down").
 * `range` = a real number outside 0–100; `word` = not a level at all (so the caller can say "I didn't catch it").
 */
export function parseLevel(text: string): { value: number } | { invalid: "range" | "word" } {
  const t = text.trim().replace(/ percent$/, "").trim();
  if (/^(?:the )?(?:max|maximum|full|highest|loudest)$|^all the way up$|^as loud as possible$/.test(t)) return { value: 100 };
  if (/^(?:the )?(?:min|minimum|lowest|zero|nothing)$|^all the way down$/.test(t)) return { value: 0 };
  if (/^(?:half|halfway|half way)$/.test(t)) return { value: 50 };
  if (/^(?:a |one )?quarter$/.test(t)) return { value: 25 };
  if (/^three quarters?$/.test(t)) return { value: 75 };
  const n = parseNumberWords(t);
  if (n === null) return /^-?\d/.test(t) ? { invalid: "range" } : { invalid: "word" };
  return n >= 0 && n <= 100 ? { value: n } : { invalid: "range" };
}

const SECONDS = String.raw`(?:seconds?|secs?|s)`;
const MINUTES = String.raw`(?:minutes?|mins?|m)`;
const HOURS = String.raw`(?:hours?|hrs?|h)`;
/**
 * A count and the gap before its unit: digits may touch the unit ("30s"), words need a space, so a word count never
 * swallows part of the unit ("ten second" + "s"). Two capture groups: digits, or words.
 */
const COUNT = String.raw`(?:(\d+(?:\.\d+)?) ?|(an?|[a-z]+(?:[ -][a-z]+)?) )`;

/** "h:mm:ss" or "m:ss" → seconds; null when it is not a clock time. */
function clockTime(t: string): number | null {
  const m = /^(?:(\d{1,2}):)?(\d{1,3}):(\d{2})$/.exec(t);
  if (!m) return null;
  const s = Number(m[3]);
  if (s > 59) return null;
  return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + s;
}

/**
 * A length of time in seconds: "30 seconds", "30s", "a minute", "2 mins", "half a minute", "a minute and a half",
 * "1 minute 30 seconds", "1:30". Null when it is not a duration (callers enforce their own maximum).
 */
export function parseDuration(text: string): number | null {
  const t = text.trim();
  const clock = clockTime(t);
  if (clock !== null) return clock;
  if (/^half an? minute$|^a half minute$/.test(t)) return 30;
  if (/^half an? hour$/.test(t)) return 1800;
  let m = new RegExp(String.raw`^(?:an?|one) (?:minute and a half|and a half minutes?)$`).exec(t);
  if (m) return 90;
  m = new RegExp(String.raw`^${COUNT}${MINUTES}(?: and)? ${COUNT}${SECONDS}$`).exec(t);
  if (m) {
    const a = parseCount(m[1] ?? m[2]!);
    const b = parseCount(m[3] ?? m[4]!);
    return a === null || b === null ? null : Math.round(a * 60 + b);
  }
  m = new RegExp(String.raw`^${COUNT}(${SECONDS}|${MINUTES}|${HOURS})$`).exec(t);
  if (m) {
    const n = parseCount(m[1] ?? m[2]!);
    if (n === null) return null;
    const unit = m[3]!;
    const factor = new RegExp(`^${HOURS}$`).test(unit) ? 3600 : new RegExp(`^${MINUTES}$`).test(unit) ? 60 : 1;
    return Math.round(n * factor);
  }
  return null;
}

/** A position in a video for "go to …": a clock time, "minute 3", "the 2 minute mark", "90 seconds", "the start". */
export function parsePosition(text: string): number | null {
  const t = text.trim().replace(/^the /, "");
  if (/^(?:start|beginning|very beginning|top)$/.test(t)) return 0;
  const clock = clockTime(t);
  if (clock !== null) return clock;
  let m = /^minute (\S+(?: \S+)?)$/.exec(t);
  if (m) {
    const n = parseCount(m[1]!);
    return n === null ? null : Math.round(n * 60);
  }
  m = /^(.+?)(?: minute| min)(?: mark)?$/.exec(t);
  if (m && !/ (?:seconds?|secs?)$/.test(t)) {
    const n = parseCount(m[1]!);
    if (n !== null) return Math.round(n * 60);
  }
  return parseDuration(t.replace(/ mark$/, ""));
}

const ORDINALS: Record<string, number> = {
  first: 1, "1st": 1, top: 1, one: 1,
  second: 2, "2nd": 2, two: 2,
  third: 3, "3rd": 3, three: 3,
  fourth: 4, "4th": 4, four: 4,
  fifth: 5, "5th": 5, five: 5,
  sixth: 6, "6th": 6, six: 6,
  seventh: 7, "7th": 7, seven: 7,
  eighth: 8, "8th": 8, eight: 8,
  ninth: 9, "9th": 9, nine: 9,
  tenth: 10, "10th": 10, ten: 10,
};

/** "second", "2nd", "two", "2" → 2. Larger numbers are returned as they are (the caller checks the range). */
export function parseOrdinal(text: string): number | null {
  const t = text.trim();
  if (t in ORDINALS) return ORDINALS[t]!;
  if (/^\d{1,2}(?:st|nd|rd|th)?$/.test(t)) return Number(t.replace(/\D/g, ""));
  return null;
}

/** English ordinal for copy and chip text: 1 → "first", 12 → "12th". */
export function ordinalWord(n: number): string {
  const words = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"];
  return words[n - 1] ?? `${n}th`;
}

/**
 * Text plus, for every boundary between its characters (0..length), where that boundary lies in the source text:
 * `from` when a slice starts there, `to` when a slice ends there. Text a rewrite inserted maps to the whole source
 * span it replaced, so slicing back always yields what the customer typed.
 */
interface Tracked {
  s: string;
  from: number[];
  to: number[];
}

function tracked(s: string): Tracked {
  const at = Array.from({ length: s.length + 1 }, (_, i) => i);
  return { s, from: at, to: [...at] };
}

/** `text.replace(re, by)` for a regex, keeping the source positions of everything it did not touch. */
function rewrite(t: Tracked, re: RegExp, by: (match: string, ...groups: Array<string | undefined>) => string): Tracked {
  const all = re.global ? re : new RegExp(re.source, `${re.flags}g`);
  let s = "";
  const from: number[] = [];
  const to: number[] = [];
  /** Text was deleted right before the next boundary: a slice ending there stops before it. */
  let endBefore: number | null = null;
  const boundary = (fromAt: number, toAt: number) => {
    from.push(fromAt);
    to.push(endBefore ?? toAt);
    endBefore = null;
  };
  const keep = (a: number, b: number) => {
    for (let i = a; i < b; i++) {
      boundary(t.from[i]!, t.to[i]!);
      s += t.s[i];
    }
  };
  let last = 0;
  for (const m of t.s.matchAll(all)) {
    const a = m.index;
    const b = a + m[0].length;
    keep(last, a);
    const out = by(m[0], ...m.slice(1));
    if (out.length === 0) endBefore ??= t.to[a]!;
    for (let k = 0; k < out.length; k++) {
      boundary(t.from[a]!, k === 0 ? t.to[a]! : t.to[b]!);
      s += out[k];
    }
    last = b;
  }
  keep(last, t.s.length);
  boundary(t.from[t.s.length]!, t.to[t.s.length]!);
  return { s, from, to };
}

const drop = (t: Tracked, re: RegExp) => rewrite(t, re, () => "");
const trimmed = (t: Tracked) => drop(t, /^\s+|\s+$/g);

/** Trailing courtesies that are never part of a command. */
const ALWAYS_POLITE_END = /,?\s+(?:please|pls)$/i;
/** Trailing words that could also end a search ("don't stop me now", "thank you"): dropped only after a comma there. */
const SOFT_POLITE_END = /,?\s+(?:now|right now|for me|thanks|thank you|thx)$/i;
/** A text that ends in an open-ended search query: a search verb (or "youtube …" but not "youtube next"). */
const ENDS_IN_QUERY = /(?:^|\s)(?:search|look ?up|look for|find|google|bing)\s|^youtube\s(?!(?:next|skip|previous|back|mute|unmute|louder|quieter|softer|up|down|pause|play|resume|stop)$)/i;

export interface NormalizedText {
  /** Lowercase normalised text, for matching. */
  text: string;
  /** The same text with the customer's capitalisation (same length as `text`), for app names quoted back. */
  display: string;
  /**
   * What the customer typed for `text.slice(a, b)`, before the "%"/"pct"/"'s" rewrites (control characters as spaces,
   * curly quotes straight, whitespace runs as one space). Search queries are taken from here, so they are sent as typed.
   */
  typed(a: number, b: number): string;
}

/**
 * Lowercase text for matching (`text`) and the same text with the customer's capitalisation (`display`). Both have
 * the same length so a match on one can be sliced from the other; `typed` slices the source itself.
 * - control characters become spaces, curly quotes become straight ones, "%", "per cent" and "pct" become " percent";
 * - "my PC's volume" becomes "my pc volume" (only for pc/computer/laptop/youtube/video/player);
 * - polite openers ("hey DoMe, could you please", "I'd like to", "let's") and endings (", please", "now", "thanks")
 *   are removed, twice, so "…, please?" fully unwraps. "now", "for me" and "thanks" stay when the text ends in a
 *   search query (unless a comma sets them apart), so "search YouTube for don't stop me now" keeps its last word.
 *   Hedges such as "maybe" or "later" are kept, so a rule never matches inside a longer sentence.
 */
export function normalizeParts(raw: string): NormalizedText {
  const source = withoutControlChars(raw).replace(/[‘’‚‛]/g, "'").replace(/[“”„‟]/g, '"');
  let d = tracked(source);
  d = rewrite(d, /%/g, () => " percent");
  d = rewrite(d, /\bper ?cent\b|\bpct\b/gi, () => "percent");
  d = rewrite(d, /\b(pc|computer|laptop|youtube|video|player)'s\b/gi, (_, word) => word ?? "");
  d = rewrite(d, /\s+/g, () => " ");
  d = trimmed(drop(trimmed(d), /[.!?,;:]+$/g));
  for (let i = 0; i < 2; i++) {
    d = trimmed(drop(d, /^(?:(?:hey|hi|hello|ok|okay|yo)[,!]?\s+)?(?:dome[,!:]?\s+)?/i));
    d = trimmed(drop(d, /^(?:please\s+)?(?:(?:can|could|would|will) you\s+)?(?:please\s+)?(?:just\s+)?/i));
    d = trimmed(drop(d, /^(?:i want to|i'd like to|i would like to|i wanna|i need to|let's|lets|go ahead and|i want you to|i'd like you to|i would like you to|i need you to)\s+/i));
    d = trimmed(drop(d, ALWAYS_POLITE_END));
    const soft = SOFT_POLITE_END.exec(d.s);
    const rest = soft ? d.s.slice(0, soft.index) : "";
    if (soft && (soft[0].startsWith(",") || !ENDS_IN_QUERY.test(rest) || /\byoutube$/i.test(rest))) d = trimmed(drop(d, SOFT_POLITE_END));
    d = trimmed(drop(d, /[.!?,;:]+$/g));
  }
  const final = d;
  const text = final.s.toLowerCase();
  // toLowerCase can change the length of a few characters (e.g. "İ"); then the display copy is the lowercase text.
  return {
    text,
    display: text.length === final.s.length ? final.s : text,
    typed: (a, b) => source.slice(final.from[a] ?? 0, final.to[b] ?? source.length).replace(/\s+/g, " ").trim(),
  };
}

/** C0/C1 control characters become spaces (the contract refuses them in a search query; nothing types them). */
function withoutControlChars(text: string): string {
  let out = "";
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0;
    out += c < 0x20 || (c >= 0x7f && c <= 0x9f) ? " " : ch;
  }
  return out;
}

/** Lowercase normalised text (see `normalizeParts`). */
export function normalizeText(text: string): string {
  return normalizeParts(text).text;
}

/** Text without one pair of surrounding straight quotes: `"tom and jerry"` → `tom and jerry`. */
export function unquote(text: string): string {
  const t = text.trim();
  if (t.length > 2 && ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'")))) return t.slice(1, -1).trim();
  return t;
}

/**
 * Matching a typed app name against the PC's approved apps (`app.list`; display names are untrusted text and are
 * only ever compared and shown, never interpreted). Deterministic and conservative, in tiers:
 * 1. the exact display name or app id ("Notepad", "msedge");
 * 2. the same after normalising (".exe", "-", "_" and "." ignored) or through a common alias ("edge" → "msedge",
 *    "vs code" → "code", "the browser" → the one approved browser);
 * 3. every typed word starts a word of the name ("chrome" → "Google Chrome", "visual code" → "Visual Studio Code").
 * Only a single hit in tiers 1–3 acts; two hits ask which one. A substring or a near-miss spelling (tiers 4–5:
 * "pad", "notpad") only ever suggests ("Did you mean Notepad?"), never runs anything.
 */

export interface MatchableApp {
  app_id: string;
  display_name: string;
}

/**
 * What may stand where an app name goes in a typed command (lowercase, NFKC: see `appNameKey`): letters and digits of
 * any script ("Météo", "微信") with spaces and ._+&'()-, never a path, flag or shell text.
 */
export const APP_NAME = /^[\p{L}\p{N}][\p{L}\p{M}\p{N} ._+&'()-]{0,63}$/u;

/** A typed or displayed app name in the form names are compared in: NFKC (one form for "é"), lowercase, trimmed. */
export function appNameKey(name: string): string {
  return name.normalize("NFKC").toLowerCase().trim();
}

export type AppMatch<A extends MatchableApp> = { kind: "match"; app: A } | { kind: "ambiguous"; apps: A[] } | { kind: "suggest"; apps: A[] } | { kind: "none" };

/** Common names for Windows programs that are often approved under their exe name or full product name. */
const ALIASES: ReadonlyArray<readonly string[]> = [
  ["edge", "msedge", "microsoft edge", "ms edge"],
  ["chrome", "google chrome"],
  ["firefox", "mozilla firefox"],
  ["brave", "brave browser"],
  ["vs code", "vscode", "code", "visual studio code"],
  ["word", "winword", "microsoft word", "ms word"],
  ["excel", "microsoft excel", "ms excel"],
  ["powerpoint", "powerpnt", "microsoft powerpoint"],
  ["outlook", "microsoft outlook"],
  ["teams", "ms teams", "microsoft teams"],
  ["file explorer", "explorer", "windows explorer", "files"],
  ["calculator", "calc"],
  ["obs", "obs64", "obs studio"],
  ["vlc", "vlc media player"],
  ["task manager", "taskmgr"],
  ["terminal", "windows terminal", "wt"],
  ["command prompt", "cmd"],
  ["paint", "mspaint"],
  ["notepad++", "notepad plus plus"],
];

/** Names that mean "the web browser" and the browsers they can stand for. */
const BROWSER_WORDS: ReadonlySet<string> = new Set(["browser", "web browser", "internet", "the internet"]);
const BROWSERS: Readonly<Record<"edge" | "chrome" | "other", readonly string[]>> = {
  edge: ["edge", "msedge", "microsoft edge", "ms edge"],
  chrome: ["chrome", "google chrome"],
  other: ["firefox", "mozilla firefox", "brave", "brave browser", "opera", "vivaldi"],
};

/** Lowercase, without ".exe", with "-", "_" and "." as spaces: "MS-Teams.exe" → "ms teams". */
export function normalizeAppName(name: string): string {
  return appNameKey(name)
    .replace(/\.exe$/, "")
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** A typed reference to an app without the words around it: "the Notepad window" → "notepad". */
function cleanNeedle(name: string): string {
  return normalizeAppName(name.replace(/^(?:the|my|a|an) /i, "").replace(/ (?:app|application|program|window)$/i, ""));
}

function names(app: MatchableApp): string[] {
  return [normalizeAppName(app.display_name), normalizeAppName(app.app_id)];
}

function tokens(text: string): string[] {
  return text.split(" ").filter(Boolean);
}

interface Forms {
  /** The app's own names plus every alias of an alias group one of them belongs to. */
  forms: string[];
  tokens: string[][];
  compact: string[];
}

/** Forms per app object, computed once (the parser matches the same list many times while the customer types). */
const formsCache = new WeakMap<MatchableApp, Forms>();

function formsOf(app: MatchableApp): Forms {
  let f = formsCache.get(app);
  if (!f) {
    const own = names(app);
    const out = new Set(own);
    for (const group of ALIASES) if (group.some((g) => own.includes(g))) for (const g of group) out.add(g);
    const forms = [...out];
    f = { forms, tokens: forms.map(tokens), compact: forms.map((x) => x.replace(/ /g, "")) };
    formsCache.set(app, f);
  }
  return f;
}

/** Optimal-string-alignment distance (Damerau–Levenshtein with adjacent swaps), for "did you mean" only. */
export function editDistance(a: string, b: string): number {
  const d: number[][] = Array.from({ length: a.length + 1 }, (_, i) => Array.from({ length: b.length + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0)));
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(d[i - 1]![j]! + 1, d[i]![j - 1]! + 1, d[i - 1]![j - 1]! + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, d[i - 2]![j - 2]! + 1);
      d[i]![j] = v;
    }
  }
  return d[a.length]![b.length]!;
}

/** True when the app is one of the browsers (optionally of one family: the browser YouTube plays in). */
export function isBrowserApp(app: MatchableApp, family?: "chrome" | "edge" | "unknown" | null): boolean {
  const own = names(app);
  const lists = family === "edge" ? [BROWSERS.edge] : family === "chrome" ? [BROWSERS.chrome] : [BROWSERS.edge, BROWSERS.chrome, BROWSERS.other];
  return lists.some((list) => list.some((n) => own.includes(n)));
}

function result<A extends MatchableApp>(hits: A[], kind: "match" | "suggest"): AppMatch<A> | null {
  if (hits.length === 0) return null;
  if (kind === "suggest") return { kind: "suggest", apps: hits.slice(0, 3) };
  return hits.length === 1 ? { kind: "match", app: hits[0]! } : { kind: "ambiguous", apps: hits.slice(0, 4) };
}

/**
 * The one approved app a name means exactly: its display name or app id, or an alias of either (tiers 1–2). Used
 * where the word could also mean something else ("Spotify" the media player, "Music"): only an exact name wins.
 */
export function strongMatch<A extends MatchableApp>(name: string, apps: readonly A[]): A | null {
  const raw = appNameKey(name);
  const needle = cleanNeedle(name);
  for (const test of [(a: A) => appNameKey(a.display_name) === raw || appNameKey(a.app_id) === raw, (a: A) => formsOf(a).forms.includes(needle)]) {
    const hits = apps.filter(test);
    if (hits.length === 1) return hits[0]!;
    if (hits.length > 1) return null;
  }
  return null;
}

/** Find the approved app a typed name refers to (see the module comment for the tiers). */
export function matchApp<A extends MatchableApp>(name: string, apps: readonly A[]): AppMatch<A> {
  const raw = appNameKey(name);
  const needle = cleanNeedle(name);
  if (!needle) return { kind: "none" };
  if (BROWSER_WORDS.has(needle)) return result(apps.filter((a) => isBrowserApp(a)), "match") ?? { kind: "none" };
  const needleTokens = tokens(needle);
  const compact = needle.replace(/ /g, "");
  const limit = needle.length >= 7 ? 2 : needle.length >= 4 ? 1 : 0;
  const near = (other: string) => Math.abs(other.length - needle.length) <= limit && editDistance(needle, other) <= limit;
  const tiers: Array<[(a: A) => boolean, "match" | "suggest"]> = [
    [(a) => appNameKey(a.display_name) === raw || appNameKey(a.app_id) === raw, "match"],
    [(a) => formsOf(a).forms.includes(needle), "match"],
    [(a) => compact.length >= 3 && needleTokens.every((t) => formsOf(a).tokens.some((ft) => ft.some((x) => x.startsWith(t)))), "match"],
    [(a) => compact.length >= 3 && formsOf(a).compact.some((f) => f.includes(compact)), "suggest"],
    [(a) => limit > 0 && formsOf(a).forms.some((f, i) => near(f) || formsOf(a).tokens[i]!.some((t) => t.length >= 4 && near(t))), "suggest"],
  ];
  for (const [test, kind] of tiers) {
    const hit = result(apps.filter(test), kind);
    if (hit) return hit;
  }
  return { kind: "none" };
}

/**
 * Words that, typed after a verb, name exactly this app among `apps` (its display name when that is unambiguous,
 * else its app id). Used for chip text, so tapping a chip always fills a command that parses to this app.
 */
export function appPhrase<A extends MatchableApp>(app: A, apps: readonly A[]): string {
  const byName = matchApp(app.display_name, apps);
  if (byName.kind === "match" && byName.app.app_id === app.app_id && APP_NAME.test(appNameKey(app.display_name))) return app.display_name.trim();
  return app.app_id;
}

/**
 * Light / dark / follow-the-device theme for the website and the app (one origin, one choice).
 *
 * - Nothing stored → the device decides (`prefers-color-scheme`); styles.css holds both palettes.
 * - "light" / "dark" stored under `dome.theme` → `<html data-theme>` forces that palette.
 *
 * The attribute is set before the first paint by `public/theme-init.js`, a tiny classic script loaded
 * from index.html's <head> (the CSP allows `script-src 'self'` only, so an inline script is not an
 * option). That file repeats the few lines of `applyTheme` below; `test/theme.test.ts` keeps the two
 * in step (storage key, values, colours). A storage that throws (private mode, blocked site data)
 * simply means "follow the device".
 */
export type ThemeChoice = "system" | "light" | "dark";

export const THEME_KEY = "dome.theme";
/** The browser UI colour (`<meta name="theme-color">`) for each palette: the page background. */
export const THEME_COLOR = { light: "#f6f8fc", dark: "#001441" } as const;

export function readThemeChoice(storage: Pick<Storage, "getItem"> | null = safeStorage()): ThemeChoice {
  try {
    const v = storage?.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

/** Whether the device prefers dark (false where matchMedia is unavailable). */
export function systemPrefersDark(): boolean {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

/** The palette actually showing for a choice. */
export function resolvedTheme(choice: ThemeChoice, prefersDark = systemPrefersDark()): "light" | "dark" {
  return choice === "system" ? (prefersDark ? "dark" : "light") : choice;
}

/** Puts a choice on the page: `data-theme` on <html> and the browser UI colour. */
export function applyTheme(choice: ThemeChoice, doc: Document = document): void {
  const root = doc.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  for (const meta of Array.from(doc.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'))) {
    // the two <meta> carry a media query each; a forced theme gives both the same colour
    const media = meta.getAttribute("media") ?? "";
    const forMedia = media.includes("dark") ? THEME_COLOR.dark : THEME_COLOR.light;
    meta.setAttribute("content", choice === "system" ? forMedia : THEME_COLOR[choice]);
  }
}

/** Remembers and applies a choice ("system" forgets the stored one). */
export function setThemeChoice(choice: ThemeChoice, storage: Pick<Storage, "setItem" | "removeItem"> | null = safeStorage()): void {
  try {
    if (choice === "system") storage?.removeItem(THEME_KEY);
    else storage?.setItem(THEME_KEY, choice);
  } catch {
    /* storage unavailable: the choice holds for this page only */
  }
  applyTheme(choice);
  for (const listener of listeners) listener(choice);
}

const listeners = new Set<(choice: ThemeChoice) => void>();

/** Called with every new choice made on this page (several switches stay in step). */
export function onThemeChoice(listener: (choice: ThemeChoice) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

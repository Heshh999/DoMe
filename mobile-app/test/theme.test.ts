/**
 * Light / dark / follow-the-device: the stored choice, the <html data-theme> attribute and the browser UI colour,
 * and the pre-paint script (public/theme-init.js, loaded from index.html's <head> because the CSP allows no inline
 * script) applying a saved choice exactly as src/lib/theme.ts does.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { applyTheme, readThemeChoice, resolvedTheme, setThemeChoice, THEME_COLOR, THEME_KEY } from "../src/lib/theme.ts";

const root = resolve(__dirname, "..");
const initScript = readFileSync(resolve(root, "public/theme-init.js"), "utf8");
const indexHtml = readFileSync(resolve(root, "index.html"), "utf8");
const css = readFileSync(resolve(root, "src/styles.css"), "utf8");

function metas() {
  document.head.innerHTML = '<meta name="theme-color" content="#001441" media="(prefers-color-scheme: dark)"><meta name="theme-color" content="#f6f8fc" media="(prefers-color-scheme: light)">';
  return Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'));
}

function runInitScript() {
  // the same file the browser runs before the first paint
  new Function(initScript)();
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.head.innerHTML = "";
});

describe("theme choice", () => {
  it("follows the device until Light or Dark is chosen; anything else stored means the device", () => {
    expect(readThemeChoice()).toBe("system");
    localStorage.setItem(THEME_KEY, "dark");
    expect(readThemeChoice()).toBe("dark");
    localStorage.setItem(THEME_KEY, "purple");
    expect(readThemeChoice()).toBe("system");
    expect(readThemeChoice({ getItem: () => { throw new Error("blocked"); } })).toBe("system");
  });

  it("resolves Auto with the device preference", () => {
    expect(resolvedTheme("system", true)).toBe("dark");
    expect(resolvedTheme("system", false)).toBe("light");
    expect(resolvedTheme("light", true)).toBe("light");
  });

  it("a choice is remembered and put on <html> with the browser colour; Auto forgets it and restores both colours", () => {
    const [dark, light] = metas();
    setThemeChoice("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect([dark!.content, light!.content]).toEqual([THEME_COLOR.dark, THEME_COLOR.dark]);
    setThemeChoice("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect([dark!.content, light!.content]).toEqual([THEME_COLOR.light, THEME_COLOR.light]);
    setThemeChoice("system");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect([dark!.content, light!.content]).toEqual([THEME_COLOR.dark, THEME_COLOR.light]);
  });

  it("a storage that refuses writes still applies the choice to this page", () => {
    setThemeChoice("dark", { setItem: () => { throw new Error("quota"); }, removeItem: () => undefined });
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});

describe("no flash of the wrong theme", () => {
  it("index.html loads the pre-paint script from <head>, as an external classic script, before the app module", () => {
    const head = indexHtml.slice(0, indexHtml.indexOf("</head>"));
    expect(head).toMatch(/<script src="\/theme-init\.js"><\/script>/);
    expect(indexHtml).not.toMatch(/<script>(?!<\/script>)/); // no inline script (CSP script-src 'self')
    expect(indexHtml.indexOf("/theme-init.js")).toBeLessThan(indexHtml.indexOf("/src/main.tsx"));
  });

  it("the pre-paint script applies a saved choice the way applyTheme does, and does nothing without one", () => {
    const [dark, light] = metas();
    runInitScript();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect([dark!.content, light!.content]).toEqual([THEME_COLOR.dark, THEME_COLOR.light]);
    for (const choice of ["dark", "light"] as const) {
      localStorage.setItem(THEME_KEY, choice);
      runInitScript();
      const fromScript = { attr: document.documentElement.getAttribute("data-theme"), colors: [dark!.content, light!.content] };
      applyTheme(choice);
      expect(fromScript).toEqual({ attr: document.documentElement.getAttribute("data-theme"), colors: [dark!.content, light!.content] });
    }
    localStorage.setItem(THEME_KEY, "<script>");
    document.documentElement.removeAttribute("data-theme");
    runInitScript();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("styles.css gives the forced dark theme the same palette as the device's dark preference", () => {
    const block = (selector: string) => {
      const at = css.indexOf(selector);
      return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at)).replace(/\s+/g, " ").trim();
    };
    const auto = block(':root:not([data-theme="light"]) {');
    const forced = block('.dome-dark {\n  color-scheme: dark;');
    expect(auto.length).toBeGreaterThan(200);
    expect(forced).toBe(auto);
  });
});

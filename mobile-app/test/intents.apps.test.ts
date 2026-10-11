/**
 * Fuzzy app matching for typed commands (src/lib/appMatch.ts): exact names and ids, normalised names and common
 * aliases act; whole-word prefixes act when unique; substrings and near-miss spellings only ever suggest.
 */
import { describe, expect, it } from "vitest";

import { APP_NAME, appNameKey, appPhrase, editDistance, isBrowserApp, matchApp, normalizeAppName, strongMatch } from "../src/lib/appMatch.ts";

const APPS = [
  { app_id: "msedge", display_name: "msedge" },
  { app_id: "chrome", display_name: "Google Chrome" },
  { app_id: "code", display_name: "Visual Studio Code" },
  { app_id: "notepad", display_name: "Notepad" },
  { app_id: "notepadpp", display_name: "Notepad++" },
  { app_id: "ms-teams", display_name: "ms-teams" },
  { app_id: "obs64", display_name: "obs64" },
];

const ids = (r: ReturnType<typeof matchApp>) => (r.kind === "match" ? [r.app.app_id] : r.kind === "none" ? [] : r.apps.map((a) => a.app_id));

describe("matchApp", () => {
  it.each<[string, string]>([
    ["Notepad", "notepad"],
    ["NOTEPAD", "notepad"],
    ["msedge", "msedge"],
    ["msedge.exe", "msedge"],
    ["edge", "msedge"],
    ["microsoft edge", "msedge"],
    ["chrome", "chrome"],
    ["google chrome", "chrome"],
    ["vs code", "code"],
    ["vscode", "code"],
    ["visual studio code", "code"],
    ["visual code", "code"],
    ["visual", "code"],
    ["teams", "ms-teams"],
    ["microsoft teams", "ms-teams"],
    ["obs", "obs64"],
    ["the notepad window", "notepad"],
    ["notepad app", "notepad"],
  ])("%j → %s", (name, id) => {
    expect(matchApp(name, APPS)).toMatchObject({ kind: "match", app: { app_id: id } });
  });

  it("an exact name wins over a longer name it starts", () => {
    expect(ids(matchApp("notepad", APPS))).toEqual(["notepad"]);
  });
  it("two prefix matches are ambiguous (never a pick), aliases included", () => {
    const r = matchApp("note", APPS);
    expect(r.kind).toBe("ambiguous");
    expect(ids(r)).toEqual(["notepad", "notepadpp"]);
    expect(ids(matchApp("studio", APPS))).toEqual(["code", "obs64"]); // Visual Studio Code, OBS Studio
  });
  it("a substring or a near-miss spelling only suggests", () => {
    expect(matchApp("pad", APPS)).toMatchObject({ kind: "suggest" });
    expect(ids(matchApp("notpad", APPS))).toContain("notepad");
    expect(matchApp("notpad", APPS).kind).toBe("suggest");
    expect(matchApp("crome", APPS)).toMatchObject({ kind: "suggest", apps: [{ app_id: "chrome" }] });
  });
  it("nothing close is none, and very short names never prefix-match", () => {
    expect(matchApp("powershell", APPS)).toEqual({ kind: "none" });
    expect(matchApp("n", APPS)).toEqual({ kind: "none" });
    expect(matchApp("", APPS)).toEqual({ kind: "none" });
  });
  it("'the browser' is the one approved browser, or a question when there are several", () => {
    expect(matchApp("the browser", APPS.filter((a) => a.app_id !== "chrome"))).toMatchObject({ kind: "match", app: { app_id: "msedge" } });
    expect(ids(matchApp("browser", APPS))).toEqual(["msedge", "chrome"]);
    expect(matchApp("browser", APPS).kind).toBe("ambiguous");
    expect(matchApp("browser", [{ app_id: "notepad", display_name: "Notepad" }])).toEqual({ kind: "none" });
  });
  it("display names are only compared, never interpreted", () => {
    const hostile = [{ app_id: "evil", display_name: "<img src=x onerror=alert(1)>" }];
    expect(matchApp("evil", hostile)).toMatchObject({ kind: "match", app: { display_name: "<img src=x onerror=alert(1)>" } });
    expect(matchApp("img", hostile)).toMatchObject({ kind: "suggest" });
    expect(appPhrase(hostile[0]!, hostile)).toBe("evil"); // a chip never carries the markup as command text
  });
});

describe("helpers", () => {
  it("normalizeAppName", () => {
    expect(normalizeAppName("MS-Teams.exe")).toBe("ms teams");
    expect(normalizeAppName("  Visual_Studio.Code ")).toBe("visual studio code");
  });
  it("editDistance counts an adjacent swap as one edit", () => {
    expect(editDistance("notepad", "notepad")).toBe(0);
    expect(editDistance("notpad", "notepad")).toBe(1);
    expect(editDistance("ntoepad", "notepad")).toBe(1);
    expect(editDistance("chrome", "crome")).toBe(1);
  });
  it("isBrowserApp, optionally for one browser family", () => {
    expect(isBrowserApp({ app_id: "msedge", display_name: "msedge" })).toBe(true);
    expect(isBrowserApp({ app_id: "msedge", display_name: "msedge" }, "chrome")).toBe(false);
    expect(isBrowserApp({ app_id: "chrome", display_name: "Google Chrome" }, "chrome")).toBe(true);
    expect(isBrowserApp({ app_id: "notepad", display_name: "Notepad" })).toBe(false);
  });
  it("appPhrase names the app unambiguously: its display name when that matches only it, else its id", () => {
    expect(appPhrase(APPS[1]!, APPS)).toBe("Google Chrome");
    const twins = [
      { app_id: "chrome", display_name: "Chrome" },
      { app_id: "chrome-beta", display_name: "Chrome" },
    ];
    expect(appPhrase(twins[1]!, twins)).toBe("chrome-beta");
  });
  it("names in any script can be typed (compared after NFKC); paths, flags and shell text still cannot", () => {
    const meteo = [{ app_id: "meteo", display_name: "Météo" }];
    expect(APP_NAME.test(appNameKey("Météo"))).toBe(true);
    expect(matchApp("météo", meteo)).toMatchObject({ kind: "match", app: { app_id: "meteo" } });
    expect(matchApp("me\u0301te\u0301o", meteo)).toMatchObject({ kind: "match", app: { app_id: "meteo" } }); // decomposed é
    expect(appPhrase(meteo[0]!, meteo)).toBe("Météo");
    for (const bad of ["c:\\windows\\cmd.exe", "notepad; rm", "app$(x)", "/usr/bin/x", "a|b"]) expect(APP_NAME.test(bad), bad).toBe(false);
  });
  it("strongMatch: only an exact name, id or alias (for words that also mean something else)", () => {
    const apps = [{ app_id: "spotify", display_name: "Spotify" }, { app_id: "musicbee", display_name: "MusicBee" }];
    expect(strongMatch("spotify", apps)?.app_id).toBe("spotify");
    expect(strongMatch("music", apps)).toBeNull(); // a prefix of MusicBee is not enough
    expect(strongMatch("edge", [{ app_id: "msedge", display_name: "msedge" }])?.app_id).toBe("msedge"); // alias
  });
});

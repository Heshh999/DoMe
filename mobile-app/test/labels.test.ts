/**
 * Copy for the protocol 1.2 actions and errors: what the confirmation/outcome line says, the next steps per error
 * code, the help category, and the plain-language result summaries. Labels are rendered from action, params and
 * target only (never PC text); a search query is quoted back shortened and only as typed.
 */
import { describe, expect, it } from "vitest";

import { registry } from "@dome/protocol";

import type { CommandRecord } from "../src/lib/commands.ts";
import { actionTitle, describeAction, errorMessage, QUERY_LABEL_MAX, recoverySteps, supportCategoryFor } from "../src/lib/labels.ts";
import { resultSummary } from "../src/lib/outcome.ts";

const PC = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:00.000Z";
const NEW_ERRORS = ["WINDOW_STATE_UNCHANGED", "FULLSCREEN_UNSUPPORTED", "APP_ELEVATED", "SEARCH_RESULT_UNAVAILABLE", "EXTENSION_UPDATE_REQUIRED"];

function succeeded(action: string, result: Record<string, unknown>, params: CommandRecord["params"] = {}): CommandRecord {
  return { commandId: "c", pcId: PC, action, params, target: null, protocolVersion: "1.2", createdAt: 1, expiresAt: TS, state: "succeeded", ackAt: null, confirmation: null, terminal: { origin: "agent", state: "succeeded", at: TS, durationMs: 5, result, resultInvalid: false, error: null, warning: null }, noAnswer: false, source: "button" };
}

describe("labels for the 1.2 actions", () => {
  it("every action in the registry has a title of its own", () => {
    for (const [name] of registry.actions()) expect(actionTitle(name), name).not.toBe("Unknown action");
    expect(actionTitle("app.maximize")).toBe("Maximise app");
    expect(actionTitle("youtube.search")).toBe("Search YouTube");
  });

  it("window actions name the app from the PC's list (as text) and say what full screen does", () => {
    const ctx = { appNames: { edge: "Microsoft Edge" } };
    expect(describeAction("app.maximize", {}, { app_id: "edge" }, ctx)).toBe("Maximise Microsoft Edge");
    expect(describeAction("app.restore", {}, { app_id: "edge", window_id: "w1" }, ctx)).toBe("Show Microsoft Edge at normal size");
    expect(describeAction("app.set_fullscreen", { enabled: true }, { app_id: "edge" }, ctx)).toBe("Make Microsoft Edge full screen (F11)");
    expect(describeAction("app.set_fullscreen", { enabled: false }, { app_id: "edge" }, ctx)).toBe("Leave full screen in Microsoft Edge");
    expect(describeAction("app.maximize", {}, { app_id: "vlc" })).toBe("Maximise vlc"); // unknown name: the id, never PC text
  });

  it("search quotes the query back shortened; opening a result names its position", () => {
    expect(describeAction("youtube.search", { query: "lofi beats" }, { browser_instance_id: "bi_test0001" })).toBe("Search YouTube for “lofi beats”");
    const long = "ü".repeat(QUERY_LABEL_MAX + 20);
    const label = describeAction("youtube.search", { query: long }, null);
    expect([...label.replace(/^Search YouTube for “|”$/g, "")]).toHaveLength(QUERY_LABEL_MAX);
    expect(label.endsWith("…”")).toBe(true);
    expect(describeAction("youtube.search", { query: "x".repeat(QUERY_LABEL_MAX) }, null)).toBe(`Search YouTube for “${"x".repeat(QUERY_LABEL_MAX)}”`);
    expect(describeAction("youtube.open_result", { index: 2, video_id: "abc" }, { browser_instance_id: "bi_test0001", tab_id: 1, tab_token: "A".repeat(22) })).toBe("Open search result 2");
  });
});

describe("recovery steps and help categories", () => {
  it("every new 1.2 error code, and APP_NOT_RUNNING, has specific next steps", () => {
    for (const code of [...NEW_ERRORS, "APP_NOT_RUNNING"]) expect(recoverySteps(code).length, code).toBeGreaterThan(0);
  });

  it("points to the right place: Open from Apps, Maximise instead, the extension update, the tray's Add an app…", () => {
    expect(recoverySteps("APP_NOT_RUNNING").join(" ")).toMatch(/Open it first from Apps/);
    expect(recoverySteps("FULLSCREEN_UNSUPPORTED")[0]).toBe("Try Maximise instead.");
    expect(recoverySteps("EXTENSION_UPDATE_REQUIRED").join(" ")).toMatch(/Update it/);
    expect(recoverySteps("APP_NOT_APPROVED").join(" ")).toMatch(/DoMe tray menu and choose Add an app…/);
    expect(recoverySteps("APP_NOT_APPROVED").join(" ")).not.toMatch(/command|CLI/i);
    expect(recoverySteps("ACTIVATION_REQUIRED").join(" ")).toMatch(/double-click the video with the touchpad/);
    expect(recoverySteps("SEARCH_RESULT_UNAVAILABLE").join(" ")).toMatch(/Search again/);
  });

  it("ACTIVATION_REQUIRED for Play says the browser blocked starting the video, not fullscreen", () => {
    const play = { action: "youtube.set_paused", origin: "agent" as const };
    expect(errorMessage({ code: "ACTIVATION_REQUIRED", message: "x" }, play)).toBe("The browser on the PC blocked starting the video without a click there.");
    expect(errorMessage({ code: "ACTIVATION_REQUIRED" }, { action: "youtube.request_fullscreen", origin: "agent" })).toMatch(/[Ff]ullscreen/);
  });

  it("FULLSCREEN_UNSUPPORTED shows the PC's own explanation (how to allow full screen there); Try Maximise instead stays the next step", () => {
    const pcSays = "DoMe presses F11 only in apps where it means full screen. Nothing was pressed. To allow it for ZStream, open the DoMe tray menu on the PC → Approved apps…";
    const fromAgent = { action: "app.set_fullscreen", origin: "agent" as const, target: { app_id: "zstream-app" } };
    expect(errorMessage({ code: "FULLSCREEN_UNSUPPORTED", message: pcSays }, fromAgent)).toBe(pcSays);
    // shown as one line of plain text: control and bidi/format characters become spaces
    expect(errorMessage({ code: "FULLSCREEN_UNSUPPORTED", message: "No F11 here.\n\u202eAllow it\u0007 on the PC." }, fromAgent)).toBe("No F11 here. Allow it on the PC.");
    // no message from the PC, or not the PC speaking: the shared copy
    const generic = "That app did not switch to full screen with F11. Not every app supports it; try Maximise instead.";
    expect(errorMessage({ code: "FULLSCREEN_UNSUPPORTED", message: "  \n " }, fromAgent)).toBe(generic);
    expect(errorMessage({ code: "FULLSCREEN_UNSUPPORTED" }, fromAgent)).toBe(generic);
    expect(errorMessage({ code: "FULLSCREEN_UNSUPPORTED", message: pcSays }, { ...fromAgent, origin: "relay" })).toBe(generic);
    expect(errorMessage({ code: "FULLSCREEN_UNSUPPORTED", message: pcSays })).toBe(generic);
    // WINDOW_STATE_UNCHANGED also carries the PC's specific reason; other codes keep the shared copy
    expect(errorMessage({ code: "WINDOW_STATE_UNCHANGED", message: "agent text" }, fromAgent)).toBe("agent text");
    expect(errorMessage({ code: "APP_ELEVATED", message: "agent text" }, fromAgent)).not.toBe("agent text");
    expect(recoverySteps("FULLSCREEN_UNSUPPORTED", fromAgent)[0]).toBe("Try Maximise instead.");
  });

  it("window errors go to Apps help, search and extension errors to media help", () => {
    expect(supportCategoryFor("WINDOW_STATE_UNCHANGED")).toBe("apps");
    expect(supportCategoryFor("FULLSCREEN_UNSUPPORTED")).toBe("apps");
    expect(supportCategoryFor("APP_ELEVATED")).toBe("apps");
    expect(supportCategoryFor("APP_NOT_RUNNING")).toBe("apps");
    expect(supportCategoryFor("SEARCH_RESULT_UNAVAILABLE")).toBe("media");
    expect(supportCategoryFor("EXTENSION_UPDATE_REQUIRED")).toBe("media");
  });
});

describe("result summaries", () => {
  it("window actions report the state the PC read back, and an unfocused window honestly", () => {
    const base = { app_id: "edge", window_id: "w1", foreground: true };
    expect(resultSummary(succeeded("app.maximize", { ...base, state: "maximized", fullscreen: false }))).toBe("The window is maximised.");
    expect(resultSummary(succeeded("app.restore", { ...base, state: "normal", fullscreen: false }))).toBe("The window is at normal size.");
    expect(resultSummary(succeeded("app.set_fullscreen", { ...base, state: "maximized", fullscreen: true }, { enabled: true }))).toBe("The window is in full screen.");
    expect(resultSummary(succeeded("app.maximize", { ...base, state: "maximized", fullscreen: false, foreground: false }))).toBe("The window is maximised. Windows did not bring it to the front.");
  });

  it("search counts the videos; opening one notes a paused start", () => {
    const tab = { browser_instance_id: "bi_test0001", tab_id: 1, script_attached: true, context: "other", ad_showing: false, is_live: false, in_playlist: false };
    expect(resultSummary(succeeded("youtube.search", { tab, results: [{ index: 1, video_id: "a", title: "A" }, { index: 2, video_id: "b", title: "B" }] }))).toBe("Found 2 videos. Tap one to play it.");
    expect(resultSummary(succeeded("youtube.search", { tab, results: [{ index: 1, video_id: "a", title: "A" }] }))).toBe("Found 1 video. Tap one to play it.");
    expect(resultSummary(succeeded("youtube.search", { tab, results: [] }))).toMatch(/no video results/);
    expect(resultSummary(succeeded("youtube.open_result", { tab: { ...tab, context: "watch", paused: false } }))).toBe("Opened the video.");
    // Play from the phone is refused by the same autoplay rule; a click on the PC (or with the Touchpad) is not
    expect(resultSummary(succeeded("youtube.open_result", { tab: { ...tab, context: "watch", paused: true } }))).toBe("Opened the video. If it does not start, press Play on the PC, or click the video with the Touchpad.");
  });
});

describe("copy that depends on the action", () => {
  it("TARGET_GONE for an app's window command: the app runs without a window, and Open shows it (not 'refresh the tab')", () => {
    const ctx = { action: "app.maximize", target: { app_id: "msedge" } };
    expect(errorMessage({ code: "TARGET_GONE" }, ctx)).toBe("The app has no open window on the PC.");
    expect(recoverySteps("TARGET_GONE", ctx).join(" ")).toMatch(/running without a window on the PC.*Open it from Apps to show its window/);
    // one window picked from the list that has gone, and a YouTube tab that has gone, keep the general copy
    const window = { action: "app.maximize", target: { app_id: "msedge", window_id: "w1" } };
    expect(errorMessage({ code: "TARGET_GONE" }, window)).toBe("That tab or window is no longer there.");
    expect(recoverySteps("TARGET_GONE", { action: "youtube.next" })).toEqual(["The tab or window changed on the PC. Refresh and pick the current one."]);
  });
  it("a search the PC could not confirm points at the browser on the PC, not at the network", () => {
    const ctx = { action: "youtube.search", origin: "agent" as const };
    expect(errorMessage({ code: "OUTCOME_UNKNOWN" }, ctx)).toBe("YouTube did not finish loading the results on the PC, so DoMe can't tell what it shows.");
    expect(recoverySteps("OUTCOME_UNKNOWN", ctx)).toEqual(["Look at the browser on the PC: if YouTube asks you to accept cookies or to sign in, do that once there.", "Then search again."]);
    expect(errorMessage({ code: "OUTCOME_UNKNOWN" }, { action: "youtube.open_result", origin: "agent" })).toMatch(/did not finish opening the video/);
    // the relay losing the PC is still a lost connection
    expect(errorMessage({ code: "OUTCOME_UNKNOWN" }, { action: "youtube.search", origin: "relay" })).toMatch(/connection was lost/);
  });
  it("UNSUPPORTED_CONTEXT names pages without a player, and opening a result from a used list says to search again", () => {
    expect(recoverySteps("UNSUPPORTED_CONTEXT")[0]).toBe("This page on the PC has no video player (for example YouTube search results), or an ad, a live stream or a Short is showing.");
    expect(errorMessage({ code: "UNSUPPORTED_CONTEXT" }, { action: "youtube.open_result" })).toBe("That tab on the PC is not showing the search results any more.");
    expect(recoverySteps("UNSUPPORTED_CONTEXT", { action: "youtube.open_result" }).join(" ")).toMatch(/Search again/);
    expect(recoverySteps("ACTIVATION_REQUIRED").join(" ")).toMatch(/press Play on the PC, or click the video with the touchpad/);
  });
});

/**
 * The §16 state table as copy (docs/design/screen-view.md §16): every row maps to one status with its exact words, one
 * next action and an error code for the help links; the allowance wording reuses lib/billing.ts; the Remote tile's
 * entry line (§2.2 step 1).
 */
import { describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import type { PcKeyPin } from "../src/lib/controllerKey.ts";
import { EMPTY_INPUT_STATE } from "../src/lib/input.ts";
import { EMPTY_SCREEN_STATE, type ScreenSessionState } from "../src/lib/screen.ts";
import { allowanceLow, allowanceText, COPY, entryLine, exhaustedText, inputNotice, remainingSeconds, screenStatus, stoppedText, type ScreenStatusInput } from "../src/lib/screenStatus.ts";
import { CONTROLLER, PC, TS } from "./helpers/harness.ts";

const NOW = Date.parse("2026-10-21T15:00:00.000Z");
const PIN: PcKeyPin = { pc_id: PC, pc_kid: "K".repeat(43), pc_public_jwk: { kty: "EC", crv: "P-256", x: "x", y: "y" }, pinned_at: TS };
const GRANT: rest.Grant = { id: "g1", controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "pointer", "screen"], created_at: TS };
const VIEW_ONLY: rest.Grant = { ...GRANT, capabilities: ["status", "screen"] };
const NO_SCREEN: rest.Grant = { ...GRANT, capabilities: ["status", "pointer"] };

function input(state: Partial<ScreenSessionState>, extra: Partial<ScreenStatusInput> = {}): ScreenStatusInput {
  return { state: { ...EMPTY_SCREEN_STATE, pcId: PC, ...state }, switchOn: "on", grant: GRANT, pin: PIN, pcName: "Office PC", pcOnline: true, remoteEnabled: true, relayUp: true, now: NOW, timeZone: "America/New_York", ...extra };
}

describe("screenStatus: before a session", () => {
  it("the operator switch: off says it is not switched on yet (operator-wide, never 'for this account'); unknown is a checking state", () => {
    expect(screenStatus(input({}, { switchOn: "unknown" }))).toMatchObject({ kind: "checking", title: "Checking…", text: null, action: "none" });
    expect(screenStatus(input({}, { switchOn: "off" }))).toMatchObject({ kind: "not_built", text: COPY.notBuilt, action: "none" });
    expect(COPY.notBuilt).toBe("Screen view isn't switched on yet.");
    expect(COPY.notBuilt).not.toContain("account");
  });
  it("no screen grant → how to allow it on the PC; no pin → pair again (SCREEN_PC_UNVERIFIED)", () => {
    expect(screenStatus(input({}, { grant: NO_SCREEN }))).toMatchObject({ kind: "no_grant", text: COPY.noGrant, code: "GRANT_MISSING", action: "none" });
    expect(screenStatus(input({}, { grant: null }))).toMatchObject({ kind: "no_grant" });
    expect(screenStatus(input({}, { pin: null }))).toMatchObject({ kind: "unpinned", text: COPY.unpinned, code: "SCREEN_PC_UNVERIFIED", action: "pair" });
    expect(COPY.unpinned).toContain("remove this phone, then pair");
  });
  it("remote control off, PC offline, relay down, loading, another viewer, ready", () => {
    expect(screenStatus(input({}, { remoteEnabled: false }))).toMatchObject({ kind: "remote_off", text: COPY.remoteOff, code: "PC_REMOTE_DISABLED" });
    expect(screenStatus(input({}, { pcOnline: false }))).toMatchObject({ kind: "pc_offline", title: "Office PC is not online" });
    expect(screenStatus(input({}, { relayUp: false }))).toMatchObject({ kind: "relay_down" });
    expect(screenStatus(input({}, { pin: undefined }))).toMatchObject({ kind: "checking" });
    expect(screenStatus(input({ otherViewer: true }))).toMatchObject({ kind: "other_viewer", title: COPY.owned, action: "takeover" });
    const ready = screenStatus(input({}));
    expect(ready).toMatchObject({ kind: "ready", title: "Show Office PC's screen", text: COPY.explainer, action: "start" });
    expect(COPY.explainer).toContain("The AI never sees it");
    expect(screenStatus(input({ resume: "manual" }))).toMatchObject({ kind: "resume", action: "resume" });
  });
});

describe("screenStatus: during a session", () => {
  it("connecting, the relay hint, consent waiting with its countdown, paused on the secure desktop, live, frozen, clicks paused", () => {
    expect(screenStatus(input({ phase: "ice" }))).toMatchObject({ kind: "connecting", title: COPY.connecting });
    expect(screenStatus(input({ phase: "starting" }))).toMatchObject({ kind: "connecting" });
    expect(screenStatus(input({ phase: "connecting", tryingRelay: true }))).toMatchObject({ kind: "relay", title: COPY.tryingRelay });
    // DTLS is up and no frame yet (device round 2): the copy names the stage that is being waited for
    expect(screenStatus(input({ phase: "connecting", verified: true, transportConnected: true }))).toMatchObject({ kind: "waiting_picture", title: COPY.waitingPicture, code: null, action: "none" });
    expect(COPY.waitingPicture).toBe("Connected. Waiting for the picture…");
    expect(screenStatus(input({ phase: "connecting", verified: true, transportConnected: false })).title).toBe(COPY.connecting);
    expect(screenStatus(input({ phase: "starting", consentWaiting: true, consentDeadlineAt: NOW + 30_000 })).title).toBe("Waiting for someone at the PC to allow this… (30)");
    expect(screenStatus(input({ phase: "starting", consentWaiting: true, consentDeadlineAt: NOW + 7_400 })).title).toBe("Waiting for someone at the PC to allow this… (8)");
    // a Windows prompt: on Free, what the relay really does (up to two minutes per window are not counted)
    expect(screenStatus(input({ phase: "paused", pauseReason: "secure_desktop", allowance: { unlimited: false, remainingSeconds: 300, resetsAt: null, at: NOW } }))).toMatchObject({ kind: "paused", title: COPY.secureDesktop, text: COPY.secureDesktopFree });
    expect(COPY.secureDesktopFree).toContain("Up to two minutes");
    expect(screenStatus(input({ phase: "paused", pauseReason: "secure_desktop", allowance: { unlimited: true, remainingSeconds: null, resetsAt: null, at: NOW } }))).toMatchObject({ title: COPY.secureDesktop, text: null });
    // a missing bar (a screen changed): never the Windows text, and no claim about the clock
    const bar = screenStatus(input({ phase: "paused", pauseReason: "indicator", allowance: { unlimited: false, remainingSeconds: 300, resetsAt: null, at: NOW } }));
    expect(bar).toMatchObject({ kind: "paused_indicator", title: COPY.indicatorPaused, text: null, tone: "warning" });
    expect(bar.title).not.toContain("Windows");
    for (const copy of Object.values(COPY)) expect(copy).not.toContain("The clock stops");
    expect(screenStatus(input({ phase: "live", verified: true }))).toMatchObject({ kind: "live", title: COPY.encrypted, tone: "success" });
    expect(screenStatus(input({ phase: "live", verified: true, stalled: true }))).toMatchObject({ kind: "frozen", title: COPY.frozen });
    expect(screenStatus(input({ phase: "live", verified: true, relayDown: true }))).toMatchObject({ kind: "live_relay_down", text: COPY.clicksPaused });
  });

  it("the input-stream notices over a live picture: paused on the PC, an administrator window, a reserved tap, a stale generation", () => {
    expect(inputNotice({ ...EMPTY_INPUT_STATE, problem: { code: "INPUT_PAUSED_ON_PC" } }, null)).toEqual({ text: COPY.pausedOnPc, code: "INPUT_PAUSED_ON_PC" });
    expect(inputNotice({ ...EMPTY_INPUT_STATE, problem: { code: "INPUT_RESTRICTED" } }, null)?.text).toBe(COPY.adminWindow);
    expect(inputNotice(EMPTY_INPUT_STATE, { restricted: true, reason: "elevated" })?.text).toBe(COPY.adminWindow);
    expect(inputNotice({ ...EMPTY_INPUT_STATE, problem: { code: "INPUT_TARGET_RESERVED" } }, null)?.text).toMatch(/DoMe's own window/);
    expect(inputNotice({ ...EMPTY_INPUT_STATE, problem: { code: "SCREEN_GEOMETRY_CHANGED" } }, null)?.text).toBe("The PC's screen layout changed. Tap again.");
    expect(inputNotice(EMPTY_INPUT_STATE, { restricted: false, reason: null })).toBeNull();
    expect(screenStatus(input({ phase: "live", verified: true }, { grant: VIEW_ONLY })).kind).toBe("live"); // view-only is the page's own line, the status stays live
  });
});

describe("screenStatus: after a session", () => {
  const ended = (endReason: ScreenSessionState["endReason"], extra: Partial<ScreenSessionState> = {}) => screenStatus(input({ phase: "ended", endReason, liveSeconds: 192, ...extra }));
  it("every end reason has its own words and next action", () => {
    expect(ended("stopped")).toMatchObject({ kind: "stopped", title: "Stopped · 3 min 12 s", action: "start" });
    expect(ended("stopped_on_pc")).toMatchObject({ kind: "stopped_on_pc", text: COPY.stoppedOnPc, action: "none" });
    expect(COPY.stoppedOnPc).not.toMatch(/minute/); // the cooldown is 10 min, 1 min or off, per grant: no length is promised
    expect(ended("takeover")).toMatchObject({ kind: "takeover", title: COPY.takeover, action: "takeover", code: "SCREEN_SESSION_OWNED" });
    expect(ended("allowance_exhausted", { allowance: { unlimited: false, remainingSeconds: 0, resetsAt: "2026-10-21T20:12:00.000Z", at: NOW } })).toMatchObject({ kind: "exhausted", text: "You've used Free Screen view for now. Refreshes today at 4:12 pm. The touchpad and remote still work.", code: "SCREEN_ALLOWANCE_EXHAUSTED", action: "none" });
    expect(ended("session_locked")).toMatchObject({ kind: "locked", text: COPY.locked, action: "retry" });
    expect(ended("secure_desktop")).toMatchObject({ kind: "secure_desktop_ended", text: COPY.secureDesktopEnded });
    expect(ended("capture_failed")).toMatchObject({ kind: "crashed", title: COPY.crashed, action: "retry", code: "SCREEN_CAPTURE_UNAVAILABLE" });
    expect(ended("connection_failed")).toMatchObject({ kind: "no_path", text: COPY.noPath, action: "retry" });
    expect(ended("allowance_unconfirmed")).toMatchObject({ kind: "relay_lost_pc", title: COPY.relayLostPc });
    expect(ended("controller_offline")).toMatchObject({ kind: "controller_offline", title: COPY.controllerOffline });
    expect(ended("viewer_gone")).toMatchObject({ kind: "viewer_gone", text: COPY.viewerGone });
    expect(ended("max_duration")).toMatchObject({ kind: "max_duration", text: COPY.maxDuration });
    expect(ended("grant_removed")).toMatchObject({ kind: "grant_removed", action: "none" });
    expect(ended("controller_revoked")).toMatchObject({ kind: "revoked", action: "none" });
    expect(ended("plan_disabled")).toMatchObject({ kind: "disabled", text: COPY.disabled });
    expect(ended("remote_disabled")).toMatchObject({ kind: "remote_off", text: COPY.remoteOff });
    expect(ended("agent_restart")).toMatchObject({ kind: "agent_restart", text: COPY.agentRestart });
    expect(ended("replaced")).toMatchObject({ kind: "replaced" });
    expect(ended("indicator")).toMatchObject({ kind: "ended" });
  });

  it("every start refusal has its own words: pinning, identity change, incompatible agent, kill switch, consent, allowance, owned, no path, capture, browser", () => {
    const failed = (code: string, extra: Partial<ScreenSessionState> = {}, detail?: Record<string, unknown>) => screenStatus(input({ phase: "failed", problem: { code, ...(detail ? { detail } : {}) }, ...extra }));
    expect(failed("SCREEN_PC_UNVERIFIED")).toMatchObject({ kind: "unpinned", action: "pair" });
    expect(failed("SCREEN_ANSWER_INVALID", {}, { reason: "kid is not the pinned PC key" })).toMatchObject({ kind: "identity_changed", title: COPY.identityChanged, tone: "danger", action: "pair" });
    expect(failed("SCREEN_ANSWER_INVALID", {}, { reason: "offer_digest does not match the offer we sent" })).toMatchObject({ kind: "answer_invalid", title: COPY.answerInvalid, action: "none" });
    expect(failed("PROTOCOL_INCOMPATIBLE")).toMatchObject({ kind: "update_pc", text: COPY.updatePc });
    expect(failed("SCREEN_VIEW_DISABLED")).toMatchObject({ kind: "disabled", text: COPY.disabled });
    expect(failed("SCREEN_CONSENT_DECLINED", { sentAt: NOW - 30_000, ackAt: NOW - 29_000 })).toMatchObject({ kind: "declined", title: COPY.declined, action: "none" });
    expect(failed("SCREEN_CONSENT_DECLINED", { sentAt: NOW - 500, ackAt: null })).toMatchObject({ kind: "declined_backoff", title: COPY.declinedBackoff });
    expect(failed("SCREEN_CONSENT_TIMEOUT")).toMatchObject({ kind: "consent_timeout", title: COPY.timeout, action: "retry" });
    expect(failed("SCREEN_ALLOWANCE_EXHAUSTED", { allowance: { unlimited: false, remainingSeconds: 0, resetsAt: "2026-10-22T13:00:00.000Z", at: NOW } }).text).toBe("You've used Free Screen view for now. Refreshes tomorrow at 9 am. The touchpad and remote still work.");
    expect(failed("SCREEN_ALLOWANCE_EXHAUSTED").text).toBe("You've used Free Screen view for now. The touchpad and remote still work.");
    expect(failed("SCREEN_SESSION_OWNED")).toMatchObject({ kind: "owned", title: COPY.owned, action: "takeover" });
    expect(failed("SCREEN_CONNECTION_FAILED")).toMatchObject({ kind: "no_path", text: COPY.noPath, action: "retry" });
    expect(failed("SCREEN_CONNECTION_FAILED", { turn: "disabled" }).text).toBe(`${COPY.noPath} ${COPY.turnDisabled}`);
    // the first-frame watchdog gave up: the connection worked, so the network's copy would be wrong
    expect(failed("SCREEN_CONNECTION_FAILED", {}, { cause: "no_picture" })).toMatchObject({ kind: "no_picture", title: COPY.noPicture, text: null, code: "SCREEN_CONNECTION_FAILED", action: "retry" });
    expect(failed("SCREEN_CONNECTION_FAILED", {}, { cause: "stall" })).toMatchObject({ kind: "no_path" });
    expect(failed("SCREEN_CAPTURE_UNAVAILABLE")).toMatchObject({ kind: "capture_unavailable", text: COPY.captureUnavailable });
    expect(failed("PC_REMOTE_DISABLED")).toMatchObject({ kind: "remote_off" });
    expect(failed("PC_SESSION_LOCKED")).toMatchObject({ kind: "locked" });
    expect(failed("GRANT_MISSING")).toMatchObject({ kind: "no_grant" });
    expect(failed("SCREEN_UNSUPPORTED_BROWSER")).toMatchObject({ kind: "unsupported" });
    expect(failed("NO_ANSWER")).toMatchObject({ kind: "failed", title: "The PC did not answer in time. Nothing is known to have run.", action: "retry" });
    // the copy never names a browser or phone maker
    for (const text of Object.values(COPY)) expect(text).not.toMatch(/safari|apple|iphone|chrome|android|google|cloudflare/i);
  });
});

describe("allowance wording and the entry line", () => {
  it("counts down from the PC's figure while live, turns low under a minute, says Unlimited, and reuses the refresh wording", () => {
    const a = { unlimited: false, remainingSeconds: 420, resetsAt: "2026-10-21T20:12:00.000Z", at: NOW };
    expect(remainingSeconds(a, NOW + 60_000, true)).toBe(360);
    expect(remainingSeconds(a, NOW + 60_000, false)).toBe(420);
    expect(remainingSeconds(a, NOW + 900_000, true)).toBe(0);
    expect(allowanceText(a, NOW, true)).toBe("About 7 min left");
    expect(allowanceText({ ...a, remainingSeconds: 75 }, NOW, true)).toBe("About 1 min left");
    expect(allowanceText({ ...a, remainingSeconds: 40 }, NOW, true)).toBe("About 40 s left");
    expect(allowanceText({ unlimited: true, remainingSeconds: null, resetsAt: null, at: NOW }, NOW, true)).toBe("Unlimited");
    expect(allowanceText(null, NOW, true)).toBeNull();
    expect(allowanceLow(a, NOW, true)).toBe(false);
    expect(allowanceLow({ ...a, remainingSeconds: 59 }, NOW, true)).toBe(true);
    expect(exhaustedText("2026-10-21T20:12:00.000Z", new Date(NOW), "America/New_York")).toBe("You've used Free Screen view for now. Refreshes today at 4:12 pm. The touchpad and remote still work.");
    expect(stoppedText(45)).toBe("Stopped · 45 s");
    expect(stoppedText(null)).toBe("Stopped · 0 s");
  });

  it("the Remote tile line: allow first, pair again, update the PC, resets at, ready with minutes", () => {
    const base = { grant: GRANT, pin: PIN, lastProblem: null, allowance: null, now: NOW, timeZone: "America/New_York" };
    expect(entryLine({ ...base, grant: NO_SCREEN })).toBe("Allow Screen view on your PC first");
    expect(entryLine({ ...base, grant: null })).toBe("Allow Screen view on your PC first");
    expect(entryLine({ ...base, pin: null })).toBe("Pair again so your phone can check it is your PC");
    expect(entryLine({ ...base, lastProblem: "PROTOCOL_INCOMPATIBLE" })).toBe("Update DoMe on your PC");
    expect(entryLine({ ...base, allowance: { unlimited: false, remainingSeconds: 0, resetsAt: "2026-10-21T20:12:00.000Z", at: NOW } })).toBe("Free Screen view refreshes today at 4:12 pm");
    expect(entryLine({ ...base, allowance: { unlimited: false, remainingSeconds: 600, resetsAt: null, at: NOW } })).toBe("Ready · 10 min left in this window");
    expect(entryLine({ ...base, allowance: { unlimited: false, remainingSeconds: 45, resetsAt: null, at: NOW } })).toBe("Ready · 45 s left in this window");
    expect(entryLine({ ...base, allowance: { unlimited: true, remainingSeconds: null, resetsAt: null, at: NOW } })).toBe("Ready · unlimited");
    expect(entryLine(base)).toBe("Ready · Free: 10 min per 5-hour window");
    expect(entryLine({ ...base, grant: undefined, pin: undefined })).toBe("Ready · Free: 10 min per 5-hour window");
  });
});


describe("screenStatus: review round (2026-10-22)", () => {
  it("a PC whose DoMe build has no Screen view says so, never 'your display isn't supported'", () => {
    const st = screenStatus(input({ phase: "failed", problem: { code: "ACTION_UNAVAILABLE", message: "This version of DoMe on your PC doesn't include Screen view yet.", detail: { reason: "screen_view_not_included" } } }));
    expect(st).toMatchObject({ kind: "not_included", text: COPY.notIncluded, code: "ACTION_UNAVAILABLE", action: "none" });
    expect(st.text).not.toContain("display");
    // any other ACTION_UNAVAILABLE keeps the generic wording and a retry
    expect(screenStatus(input({ phase: "failed", problem: { code: "ACTION_UNAVAILABLE" } }))).toMatchObject({ kind: "failed", action: "retry" });
  });
  it("on Free the consent wait says it counts toward the minutes if the PC allows it; unlimited plans say nothing about minutes", () => {
    const free = screenStatus(input({ phase: "starting", consentWaiting: true, consentDeadlineAt: NOW + 30_000, allowance: { unlimited: false, remainingSeconds: 540, resetsAt: null, at: NOW } }));
    expect(free.text).toContain(COPY.consentFreeNote);
    const pro = screenStatus(input({ phase: "starting", consentWaiting: true, consentDeadlineAt: NOW + 30_000, allowance: { unlimited: true, remainingSeconds: null, resetsAt: null, at: NOW } }));
    expect(pro.text).not.toContain("minutes");
  });
});

describe("screenStatus: review of device round 2 (2026-10-27)", () => {
  it("the VP8 badge names no cause: VP8 is also the fallback after H.264 stalled on the PC or never decoded on the phone", () => {
    expect(COPY.basicPicture).toBe("Basic picture mode");
    expect(COPY.basicPicture).not.toMatch(/lacks|encoder/);
  });
});

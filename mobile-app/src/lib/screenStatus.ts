/**
 * The Screen view page's states and their exact copy (docs/design/screen-view.md §16): one state at a time, one next
 * action, no brand names, nothing claimed that is not known. Pure over the session state and the stores, so every row
 * of the §16 table has a unit test and the page only renders what comes out. Time wording reuses `lib/billing.ts`
 * (`refreshText`: "Refreshes today at 4:12 pm"), so nothing on the phone invents a second way of saying when.
 */
import type { rest } from "@dome/protocol";

import type { Tone } from "../components/ui.tsx";
import { FREE_SCREEN_VIEW_MINUTES, WINDOW_HOURS } from "../content/plans.ts";
import { lowerFirst, refreshText } from "./billing.ts";
import type { PcKeyPin } from "./controllerKey.ts";
import type { InputSessionState } from "./input.ts";
import { errorMessage, SCREEN_UNSUPPORTED_BROWSER } from "./labels.ts";
import { BACKOFF_REFUSAL_MS, screenEndCode, type ScreenAllowance, type ScreenSessionState } from "./screen.ts";

export type ScreenAction = "start" | "takeover" | "retry" | "resume" | "pair" | "none";

export interface ScreenStatus {
  kind: string;
  title: string;
  text: string | null;
  tone: Tone;
  /** Error code for FailureLinks / recovery steps; null when nothing failed. */
  code: string | null;
  action: ScreenAction;
}

export interface ScreenStatusInput {
  state: ScreenSessionState;
  /** The operator switch as the server states it (GET /healthz `screen_view`); "unknown" until it answers. */
  switchOn: "on" | "off" | "unknown";
  /** This phone's grant on the PC (undefined = not loaded yet, null = none). */
  grant: rest.Grant | null | undefined;
  /** The PC key pinned at pairing (undefined = not read yet, null = none). */
  pin: PcKeyPin | null | undefined;
  pcName: string | null;
  pcOnline: boolean;
  remoteEnabled: boolean | null;
  /** The relay socket is open (clicks reach the PC). */
  relayUp: boolean;
  now: number;
  timeZone?: string;
}

// ---- the exact copy (§16) ---------------------------------------------------------------------------------------------

export const COPY = {
  notBuilt: "Screen view isn't switched on yet.",
  notIncluded: "This version of DoMe on your PC doesn't include Screen view yet. The touchpad and remote still work.",
  noGrant: "Allow Screen view on your PC: DoMe tray icon › Paired phones › this phone › Allow Screen view.",
  unpinned: "Pair this phone with your PC again so it can check it is really your PC. On the PC: DoMe tray › Paired phones › remove this phone, then pair.",
  identityChanged: "This PC's identity changed. Pair again.",
  answerInvalid: "Couldn't confirm this is your PC. Nothing was shown.",
  updatePc: "Update DoMe on your PC to use Screen view.",
  remoteOff: "Remote control is switched off on the PC. Turn it on from the DoMe tray icon.",
  disabled: "Screen view is switched off for now. The touchpad and remote still work.",
  consentWaiting: "Waiting for someone at the PC to allow this…",
  consentFreeNote: "On Free, this wait counts toward your minutes if the PC allows it.",
  declined: "Not allowed at the PC.",
  declinedBackoff: "Not allowed at the PC. Try again in a few minutes.",
  timeout: "No one answered at the PC.",
  owned: "Another phone is viewing this PC.",
  connecting: "Connecting to your PC's screen…",
  tryingRelay: "Trying the relay server…",
  tapToPlay: "Tap to show the picture.",
  noPath: "Couldn't reach your PC's picture from this network. The touchpad and remote still work.",
  turnDisabled: "Screen view could only try a direct connection right now. It works on most home networks.",
  encrypted: "Encrypted between your PC and this phone",
  viaRelay: "Via relay server",
  dataSaver: "Data saver",
  viewOnly: "View only. Allow the touchpad on your PC to tap.",
  adminWindow: "That window runs as administrator. You can see it but not click it from the phone.",
  pausedOnPc: "Waiting for someone at the PC.",
  frozen: "Picture paused — reconnecting…",
  locked: "Your PC is locked. Unlock it to start again.",
  secureDesktop: "Windows is asking for permission on the PC. Someone at the PC must answer.",
  secureDesktopFree: `Up to two minutes of Windows permission prompts in each ${WINDOW_HOURS}-hour window don't count toward your Free minutes.`,
  indicatorPaused: "A screen on the PC changed. The picture comes back when DoMe's bar is on every screen.",
  protectedContent: "Some protected videos and windows show as black.",
  basicPicture: "Basic picture mode (this PC lacks the video encoder)",
  captureUnavailable: "Your PC can't share its screen right now. Another app may be capturing it, or this display isn't supported yet.",
  crashed: "Your PC's screen sharing stopped unexpectedly.",
  stoppedOnPc: "Screen view was stopped on the PC. Starting it again may need someone at the PC to allow it.",
  takeover: "Another phone took over Screen view.",
  relayLostPc: "Lost contact with DoMe's service. Screen view ended.",
  controllerOffline: "Screen view ended because this phone lost contact with DoMe's service.",
  clicksPaused: "Clicks paused: reconnecting",
  resume: "Screen view stopped while the app was in the background.",
  viewerGone: "The PC stopped hearing from this phone, so Screen view ended.",
  maxDuration: "Screen view stopped after 4 hours. Start again to keep watching.",
  secureDesktopEnded: "Windows kept asking for permission on the PC for two minutes, so Screen view ended.",
  grantRemoved: "Screen view was turned off for this phone on the PC.",
  revoked: "This phone's access to the PC was revoked.",
  agentRestart: "DoMe on the PC restarted, so Screen view ended.",
  explainer: "Your PC's picture goes straight to this phone, encrypted between the two. Nothing is recorded or stored. The AI never sees it. Uses about 0.7 GB per hour at full quality.",
  unsupported: errorMessage({ code: SCREEN_UNSUPPORTED_BROWSER }),
} as const;

export const SCREEN_BETA_LABEL = "Screen view (beta)";
/** `ACTION_UNAVAILABLE{reason}` from a PC whose DoMe build has no media stack (every build until the screen extra). */
export const SCREEN_VIEW_NOT_INCLUDED = "screen_view_not_included";

// ---- allowance wording ----------------------------------------------------------------------------------------------

/** Seconds left as the phone counts them: the PC's last figure minus the time since, never below 0 (display only). */
export function remainingSeconds(a: ScreenAllowance | null, now: number, counting: boolean): number | null {
  if (!a || a.unlimited || a.remainingSeconds === null) return null;
  if (!counting) return Math.max(0, a.remainingSeconds);
  return Math.max(0, a.remainingSeconds - Math.floor((now - a.at) / 1000));
}

/** "About 7 min left", "About 40 s left", "Unlimited", or null while nothing is known. */
export function allowanceText(a: ScreenAllowance | null, now: number, counting: boolean): string | null {
  if (!a) return null;
  if (a.unlimited) return "Unlimited";
  const s = remainingSeconds(a, now, counting);
  if (s === null) return null;
  if (s >= 90) return `About ${Math.round(s / 60)} min left`;
  if (s >= 60) return "About 1 min left";
  return `About ${s} s left`;
}

/** Below a minute the bar turns amber; at 0 the PC's `ended{allowance_exhausted}` is what ends the session. */
export function allowanceLow(a: ScreenAllowance | null, now: number, counting: boolean): boolean {
  const s = remainingSeconds(a, now, counting);
  return s !== null && s < 60;
}

/** "You've used Free Screen view for now. Refreshes today at 4:12 pm. The touchpad and remote still work." */
export function exhaustedText(resetsAt: string | null, now: Date, timeZone?: string): string {
  const when = resetsAt ? ` ${refreshText(resetsAt, now, timeZone)}.` : "";
  return `You've used Free Screen view for now.${when} The touchpad and remote still work.`;
}

/** "Stopped · 3 min 12 s". */
export function stoppedText(liveSeconds: number | null): string {
  const s = Math.max(0, liveSeconds ?? 0);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `Stopped · ${m} min ${r} s` : `Stopped · ${r} s`;
}

/** The Remote tile and Home card line (§2.2 step 1): one state, one line. */
export function entryLine(input: { grant: rest.Grant | null | undefined; pin: PcKeyPin | null | undefined; lastProblem: string | null; allowance: ScreenAllowance | null; now: number; timeZone?: string }): string {
  if (input.grant === null || (input.grant !== undefined && !input.grant.capabilities.includes("screen"))) return "Allow Screen view on your PC first";
  if (input.pin === null) return "Pair again so your phone can check it is your PC";
  if (input.lastProblem === "PROTOCOL_INCOMPATIBLE") return "Update DoMe on your PC";
  const left = remainingSeconds(input.allowance, input.now, false);
  if (left === 0 && input.allowance?.resetsAt) return `Free Screen view ${lowerFirst(refreshText(input.allowance.resetsAt, new Date(input.now), input.timeZone))}`;
  if (input.allowance?.unlimited) return "Ready · unlimited";
  if (left !== null) return `Ready · ${left >= 60 ? `${Math.round(left / 60)} min` : `${left} s`} left in this window`;
  return `Ready · Free: ${FREE_SCREEN_VIEW_MINUTES} min per ${WINDOW_HOURS}-hour window`;
}

// ---- the state table -------------------------------------------------------------------------------------------------

function status(kind: string, title: string, text: string | null, tone: Tone, code: string | null, action: ScreenAction): ScreenStatus {
  return { kind, title, text, tone, code, action };
}

/** Which §16 row applies right now. The page shows exactly one of these above or instead of the picture. */
export function screenStatus(input: ScreenStatusInput): ScreenStatus {
  const { state, grant, pin } = input;
  const nowDate = new Date(input.now);
  if (input.switchOn === "unknown") return status("checking", "Checking…", null, "neutral", null, "none");
  if (input.switchOn !== "on") return status("not_built", "Screen view", COPY.notBuilt, "neutral", null, "none");
  if (state.phase === "idle" || state.phase === "ended" || state.phase === "failed") {
    if (grant === null || (grant !== undefined && !grant.capabilities.includes("screen"))) return status("no_grant", "Allow Screen view on your PC", COPY.noGrant, "warning", "GRANT_MISSING", "none");
    if (pin === null) return status("unpinned", "Pair again", COPY.unpinned, "warning", "SCREEN_PC_UNVERIFIED", "pair");
  }
  switch (state.phase) {
    case "ice":
    case "offering":
    case "starting":
      if (state.consentWaiting && state.consentDeadlineAt !== null) {
        const left = Math.max(0, Math.ceil((state.consentDeadlineAt - input.now) / 1000));
        const free = state.allowance !== null && !state.allowance.unlimited;
        return status("consent", `${COPY.consentWaiting} (${left})`, `The PC shows a window naming this phone; someone there must choose Allow.${free ? ` ${COPY.consentFreeNote}` : ""}`, "info", null, "none");
      }
      return status("connecting", COPY.connecting, null, "info", null, "none");
    case "connecting":
      return status(state.tryingRelay ? "relay" : "connecting", state.tryingRelay ? COPY.tryingRelay : COPY.connecting, null, "info", null, "none");
    case "paused":
      // Only a Windows permission prompt is ever discounted, and only up to two minutes per window: a missing bar
      // (a screen changed) makes no claim about the clock at all
      if (state.pauseReason === "indicator") return status("paused_indicator", COPY.indicatorPaused, null, "warning", null, "none");
      return status("paused", COPY.secureDesktop, state.allowance && !state.allowance.unlimited ? COPY.secureDesktopFree : null, "warning", null, "none");
    case "live":
      if (state.stalled) return status("frozen", COPY.frozen, null, "warning", null, "none");
      if (state.relayDown) return status("live_relay_down", COPY.encrypted, COPY.clicksPaused, "warning", null, "none");
      return status("live", COPY.encrypted, null, "success", null, "none");
    case "ended":
      return endedStatus(state, nowDate, input.timeZone);
    case "failed":
      return failedStatus(state, nowDate, input.timeZone);
    case "idle":
    default:
      break;
  }
  if (input.remoteEnabled === false) return status("remote_off", "Remote control is off", COPY.remoteOff, "warning", "PC_REMOTE_DISABLED", "none");
  if (!input.pcOnline) return status("pc_offline", `${input.pcName ?? "The PC"} is not online`, "Screen view needs the PC connected to DoMe.", "warning", "PC_OFFLINE", "none");
  if (!input.relayUp) return status("relay_down", "Connecting to DoMe…", "Screen view needs this phone connected to DoMe.", "warning", "PC_RECONNECTING", "none");
  if (state.resume === "manual") return status("resume", COPY.resume, null, "info", null, "resume");
  if (pin === undefined || grant === undefined) return status("checking", "Checking…", null, "neutral", null, "none");
  if (state.otherViewer) return status("other_viewer", COPY.owned, "You can take over; the other phone's view ends.", "info", "SCREEN_SESSION_OWNED", "takeover");
  return status("ready", `Show ${input.pcName ?? "the PC"}'s screen`, COPY.explainer, "neutral", null, "start");
}

function endedStatus(state: ScreenSessionState, now: Date, timeZone?: string): ScreenStatus {
  const code = screenEndCode(state.endReason);
  switch (state.endReason) {
    case "stopped":
      return status("stopped", stoppedText(state.liveSeconds), null, "neutral", null, "start");
    case "stopped_on_pc":
      return status("stopped_on_pc", "Stopped on the PC", COPY.stoppedOnPc, "warning", null, "none");
    case "takeover":
      return status("takeover", COPY.takeover, "You can take it back; the other phone's view ends.", "info", code, "takeover");
    case "allowance_exhausted":
      return status("exhausted", "Free Screen view used up", exhaustedText(state.allowance?.resetsAt ?? null, now, timeZone), "warning", code, "none");
    case "session_locked":
      return status("locked", "PC locked", COPY.locked, "warning", code, "retry");
    case "secure_desktop":
      return status("secure_desktop_ended", "Screen view ended", COPY.secureDesktopEnded, "warning", code, "retry");
    case "capture_failed":
      return status("crashed", COPY.crashed, null, "danger", code, "retry");
    case "connection_failed":
      return status("no_path", "Couldn't connect", COPY.noPath, "warning", code, "retry");
    case "allowance_unconfirmed":
      return status("relay_lost_pc", COPY.relayLostPc, null, "warning", code, "retry");
    case "controller_offline":
      return status("controller_offline", COPY.controllerOffline, null, "warning", code, "retry");
    case "viewer_gone":
      return status("viewer_gone", "Screen view ended", COPY.viewerGone, "warning", code, "retry");
    case "max_duration":
      return status("max_duration", "Screen view ended", COPY.maxDuration, "info", null, "retry");
    case "grant_removed":
      return status("grant_removed", "Screen view turned off for this phone", COPY.grantRemoved, "warning", code, "none");
    case "controller_revoked":
      return status("revoked", "Access revoked", COPY.revoked, "danger", code, "none");
    case "plan_disabled":
      return status("disabled", "Screen view is off", COPY.disabled, "warning", code, "none");
    case "remote_disabled":
      return status("remote_off", "Remote control is off", COPY.remoteOff, "warning", code, "none");
    case "agent_restart":
      return status("agent_restart", "Screen view ended", COPY.agentRestart, "warning", code, "retry");
    case "replaced":
      return status("replaced", "Screen view restarted", null, "neutral", null, "start");
    default:
      return status("ended", "Screen view ended", null, "neutral", code, "start");
  }
}

function failedStatus(state: ScreenSessionState, now: Date, timeZone?: string): ScreenStatus {
  const p = state.problem;
  const code = p?.code ?? "INTERNAL";
  const reason = typeof p?.detail?.reason === "string" ? p.detail.reason : null;
  switch (code) {
    case "SCREEN_PC_UNVERIFIED":
      return status("unpinned", "Pair again", COPY.unpinned, "warning", code, "pair");
    case "SCREEN_ANSWER_INVALID":
      return reason === "kid is not the pinned PC key" ? status("identity_changed", COPY.identityChanged, "Nothing was shown. This is a hard stop: a key this phone did not pin can never show a picture.", "danger", code, "pair") : status("answer_invalid", COPY.answerInvalid, null, "danger", code, "none");
    case "PROTOCOL_INCOMPATIBLE":
      return status("update_pc", "Update needed", COPY.updatePc, "warning", code, "none");
    case "SCREEN_VIEW_DISABLED":
      return status("disabled", "Screen view is off", COPY.disabled, "warning", code, "none");
    case "SCREEN_CONSENT_DECLINED": {
      const quick = state.sentAt !== null && state.ackAt === null && now.getTime() - state.sentAt < BACKOFF_REFUSAL_MS;
      return status(quick ? "declined_backoff" : "declined", quick ? COPY.declinedBackoff : COPY.declined, null, "warning", code, "none");
    }
    case "SCREEN_CONSENT_TIMEOUT":
      return status("consent_timeout", COPY.timeout, null, "warning", code, "retry");
    case "SCREEN_ALLOWANCE_EXHAUSTED":
      return status("exhausted", "Free Screen view used up", exhaustedText(state.allowance?.resetsAt ?? null, now, timeZone), "warning", code, "none");
    case "SCREEN_SESSION_OWNED":
      return status("owned", COPY.owned, "You can take over; the other phone's view ends.", "info", code, "takeover");
    case "SCREEN_CONNECTION_FAILED":
      return status("no_path", "Couldn't connect", state.turn === "disabled" ? `${COPY.noPath} ${COPY.turnDisabled}` : COPY.noPath, "warning", code, "retry");
    case "ACTION_UNAVAILABLE":
      if (p?.detail?.reason === SCREEN_VIEW_NOT_INCLUDED) return status("not_included", "Screen view isn't in DoMe on your PC yet", COPY.notIncluded, "warning", code, "none");
      return status("failed", errorMessage(p, { action: "screen.session_start" }), null, "warning", code, "retry");
    case "SCREEN_CAPTURE_UNAVAILABLE":
      return status("capture_unavailable", "Your PC can't share its screen right now", COPY.captureUnavailable, "warning", code, "retry");
    case "PC_REMOTE_DISABLED":
      return status("remote_off", "Remote control is off", COPY.remoteOff, "warning", code, "none");
    case "PC_SESSION_LOCKED":
      return status("locked", "PC locked", COPY.locked, "warning", code, "retry");
    case "GRANT_MISSING":
      return status("no_grant", "Allow Screen view on your PC", COPY.noGrant, "warning", code, "none");
    case SCREEN_UNSUPPORTED_BROWSER:
      return status("unsupported", "Not available in this browser", COPY.unsupported, "warning", code, "none");
    default:
      return status("failed", errorMessage(p, { action: "screen.session_start" }), null, "warning", code, "retry");
  }
}

/** The input-session notice shown over a live picture (§16 rows that come from the signed input stream). */
export function inputNotice(input: InputSessionState, restricted: ScreenSessionState["restricted"]): { text: string; code: string } | null {
  const code = input.problem?.code ?? null;
  if (code === "INPUT_PAUSED_ON_PC") return { text: COPY.pausedOnPc, code };
  if (code === "INPUT_RESTRICTED" || (restricted?.restricted && restricted.reason === "elevated")) return { text: COPY.adminWindow, code: code ?? "INPUT_RESTRICTED" };
  if (code === "INPUT_TARGET_RESERVED") return { text: errorMessage({ code }), code };
  if (code === "SCREEN_GEOMETRY_CHANGED" || code === "SCREEN_SESSION_REQUIRED") return { text: errorMessage({ code }), code };
  return null;
}

/**
 * Customer-facing copy derived from the action registry and error table. The confirmation modal's
 * primary line comes from `describeAction` (never from text the PC sent), so a tampered or
 * mislabelled challenge cannot describe a different action than the one that will run.
 */
import { ERRORS, registry, type ActionName } from "@dome/protocol";
import type { relayFrames } from "@dome/protocol";

export interface LabelContext {
  /** app_id → display name from the PC's approved-app list (untrusted text, rendered as text). */
  appNames?: Record<string, string>;
}

const TITLES: Record<ActionName, string> = {
  "system.ping": "Connection check",
  "system.get_status": "Refresh status",
  "youtube.list_tabs": "List YouTube tabs",
  "youtube.get_state": "Read player state",
  "youtube.set_paused": "Play/pause YouTube",
  "youtube.next": "Next video",
  "youtube.previous": "Previous video",
  "youtube.seek_relative": "Seek",
  "youtube.seek_to": "Seek to position",
  "youtube.set_muted": "Mute YouTube",
  "youtube.set_volume": "YouTube volume",
  "youtube.set_theater": "Theater mode",
  "youtube.request_fullscreen": "Fullscreen",
  "youtube.search": "Search YouTube",
  "youtube.open_result": "Open search result",
  "media.get_sessions": "List media players",
  "media.set_paused": "Play/pause media",
  "media.next": "Next track",
  "media.previous": "Previous track",
  "windows.get_volume": "Read PC volume",
  "windows.set_volume": "PC volume",
  "windows.set_muted": "Mute PC",
  "windows.lock": "Lock Windows",
  "app.list": "List apps",
  "app.launch": "Open app",
  "app.focus": "Bring app to front",
  "app.minimize": "Minimize app",
  "app.maximize": "Maximize app",
  "app.restore": "Restore window",
  "app.set_fullscreen": "App full screen",
  "app.close": "Close app",
  "power.sleep": "Sleep",
  "power.restart": "Restart",
  "power.shutdown": "Shut down",
  "power.cancel": "Cancel countdown",
  "input.session_start": "Start touchpad/keyboard",
  "input.session_stop": "Stop touchpad/keyboard",
  "screen.session_start": "Start Screen view",
  "screen.session_stop": "Stop Screen view",
};

export function actionTitle(action: string): string {
  return registry.has(action) ? TITLES[action] : "Unknown action";
}

function appName(id: unknown, ctx?: LabelContext): string {
  if (typeof id !== "string") return "an app";
  return ctx?.appNames?.[id] ?? id;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Longest search text quoted back in a label; the rest is elided (the query is never logged or stored). */
export const QUERY_LABEL_MAX = 60;

function quoted(v: unknown): string {
  const text = typeof v === "string" ? v : "";
  const chars = [...text];
  return `“${chars.length > QUERY_LABEL_MAX ? `${chars.slice(0, QUERY_LABEL_MAX - 1).join("")}…` : text}”`;
}

/** One sentence saying exactly what the PC will do. Params/target must already be validated. */
export function describeAction(action: string, params: Record<string, unknown>, target: Record<string, unknown> | null, ctx?: LabelContext): string {
  if (!registry.has(action)) return "Unknown action";
  const name: ActionName = action;
  switch (name) {
    case "system.ping":
      return "Check the connection to the PC";
    case "system.get_status":
      return "Refresh the PC's status";
    case "youtube.list_tabs":
      return "List the YouTube tabs on the PC";
    case "youtube.get_state":
      return "Read the YouTube player state";
    case "youtube.set_paused":
      return params.paused ? "Pause YouTube" : "Play YouTube";
    case "youtube.next":
      return "Skip to the next video";
    case "youtube.previous":
      return "Go to the previous video";
    case "youtube.seek_relative": {
      const s = num(params.seconds) ?? 0;
      return s < 0 ? `Go back ${Math.abs(s)} seconds` : `Skip ahead ${s} seconds`;
    }
    case "youtube.seek_to":
      return `Jump to ${Math.round(num(params.position_seconds) ?? 0)} seconds`;
    case "youtube.set_muted":
      return params.muted ? "Mute YouTube" : "Unmute YouTube";
    case "youtube.set_volume":
      return `Set YouTube volume to ${num(params.value) ?? "?"}%`;
    case "youtube.set_theater":
      return params.enabled ? "Turn theater mode on" : "Turn theater mode off";
    case "youtube.request_fullscreen":
      return "Ask the browser for fullscreen";
    case "youtube.search":
      return `Search YouTube for ${quoted(params.query)}`;
    case "youtube.open_result":
      return `Open search result ${num(params.index) ?? "?"}`;
    case "media.get_sessions":
      return "List media players on the PC";
    case "media.set_paused":
      return params.paused ? "Pause the media player" : "Play the media player";
    case "media.next":
      return "Next track";
    case "media.previous":
      return "Previous track";
    case "windows.get_volume":
      return "Read the PC volume";
    case "windows.set_volume":
      return `Set PC volume to ${num(params.value) ?? "?"}%`;
    case "windows.set_muted":
      return params.muted ? "Mute the PC" : "Unmute the PC";
    case "windows.lock":
      return "Lock Windows";
    case "app.list":
      return "List approved apps";
    case "app.launch":
      return `Open ${appName(params.app_id, ctx)}`;
    case "app.focus":
      return `Bring ${appName(target?.app_id, ctx)} to the front`;
    case "app.minimize":
      return `Minimize ${appName(target?.app_id, ctx)}`;
    case "app.maximize":
      return `Maximize ${appName(target?.app_id, ctx)}`;
    case "app.restore":
      return `Show ${appName(target?.app_id, ctx)} at normal size`;
    case "app.set_fullscreen":
      return params.enabled ? `Make ${appName(target?.app_id, ctx)} full screen (F11)` : `Leave full screen in ${appName(target?.app_id, ctx)}`;
    case "app.close":
      return `Close ${appName(target?.app_id, ctx)}`;
    case "power.sleep":
      return `Put the PC to sleep${countdown(params)}`;
    case "power.restart":
      return `Restart the PC${countdown(params)}`;
    case "power.shutdown":
      return `Shut down the PC${countdown(params)}`;
    case "power.cancel":
      return "Cancel the pending power countdown";
    case "input.session_start":
      return params.takeover ? "Take over the touchpad and keyboard on the PC" : "Start the touchpad and keyboard on the PC";
    case "input.session_stop":
      return "Stop the touchpad and keyboard";
    case "screen.session_start":
      return params.takeover ? "Take over Screen view of the PC" : "Show the PC's screen on this phone";
    case "screen.session_stop":
      return "Stop Screen view";
  }
}

function countdown(params: Record<string, unknown>): string {
  const c = num(params.countdown_seconds);
  if (c === null) return " after a 10-second countdown";
  return c === 0 ? " immediately" : ` after a ${c}-second countdown`;
}

export function capabilityLabel(cap: string): string {
  const labels: Record<string, string> = {
    status: "See status",
    media: "Control YouTube and media",
    volume: "Change PC volume",
    apps: "Open and manage approved apps",
    lock: "Lock Windows",
    power: "Sleep, restart, shut down (always confirmed)",
    pointer: "Touchpad: move the cursor, click, scroll, drag",
    keyboard: "Keyboard: type text, press keys and shortcuts",
    screen: "Screen view: see this PC's screens live on the phone",
    screen_audio: "PC sound during Screen view (coming in a later update)",
  };
  return labels[cap] ?? cap;
}

/** True for the two manual-input capabilities whose reach is every app of the unlocked session. */
export function isInputCapability(cap: string): boolean {
  return cap === "pointer" || cap === "keyboard";
}

/** True for the two Screen view capabilities (protocol 1.4), granted only in a DoMe window on the PC. */
export function isScreenCapability(cap: string): boolean {
  return cap === "screen" || cap === "screen_audio";
}

/** Fixed explanation of the Screen view permission (docs/design/screen-view.md §2.1), shown wherever the grant appears. */
export const SCREEN_SCOPE_EXPLANATION = "Screen view lets this phone see everything on the PC's screens while a Screen view is on: every app, notifications and messages that pop up, and anything revealed there. It does not let the phone click or type; that is Touchpad and Keyboard. The PC shows a 'Screen view on' bar on every screen whenever someone is watching. Nothing is recorded, and the AI never receives it. Only the PC owner can allow it, in a DoMe window on the PC.";

/** Codes the phone raises itself for Screen view (not in errors.json, like NETWORK and NO_ANSWER). */
export const SCREEN_UNSUPPORTED_BROWSER = "SCREEN_UNSUPPORTED_BROWSER";

/** Fixed, spec-mandated explanation of the manual-input permission (spec §10A D). */
export const INPUT_SCOPE_EXPLANATION = "Touchpad and keyboard input reaches every app of the unlocked Windows session, not only the approved apps — exactly like a mouse and keyboard plugged into the PC. The approved-app list limits structured app actions; it is not a sandbox around manual input. The PC owner grants and removes these permissions on the PC itself (DoMe tray → Paired phones).";

/** Which support category a help link should preselect for an error code. */
export function supportCategoryFor(code: string | null | undefined): "connection" | "pairing" | "media" | "input" | "apps" | "power" | "install" | "billing" | "account" | "other" {
  if (!code) return "other";
  if (code.startsWith("INPUT_")) return "input";
  if (code === "SCREEN_PC_UNVERIFIED" || code === "SCREEN_ANSWER_INVALID") return "pairing";
  if (code.startsWith("SCREEN_")) return "connection";
  if (code.startsWith("PAIRING_") || code === "CONTROLLER_REVOKED" || code === "UNKNOWN_KEY" || code === "GRANT_MISSING" || code === "SIGNATURE_INVALID") return "pairing";
  if (code.startsWith("PC_") || code === "NETWORK" || code === "OUTCOME_UNKNOWN" || code === "COMMAND_EXPIRED" || code === "PROTOCOL_INCOMPATIBLE" || code === "RATE_LIMITED" || code === "QUEUE_FULL") return "connection";
  if (code.startsWith("EXTENSION_") || code.startsWith("TARGET_") || code.startsWith("TAB_") || code.startsWith("SEARCH_") || code === "BROWSER_NOT_RUNNING" || code === "UNSUPPORTED_CONTEXT" || code === "ACTIVATION_REQUIRED") return "media";
  if (code.startsWith("APP_") || code.startsWith("WINDOW_") || code.startsWith("FULLSCREEN_") || code === "CLOSE_REFUSED" || code === "FOCUS_DENIED") return "apps";
  if (code.startsWith("POWER_") || code.startsWith("CONFIRMATION_")) return "power";
  if (code.startsWith("ENTITLEMENT_") || code.startsWith("BILLING_") || code.startsWith("TOPUP_") || code.startsWith("PLAN_SELECTION_") || code === "ALREADY_SUBSCRIBED" || code === "CHECKOUT_PROCESSING" || code === "PLAN_NOT_AVAILABLE" || code.endsWith("_PLAN_DISABLED") || code === "DEVICE_LIMIT_REACHED") return "billing";
  if (code === "UNAUTHENTICATED" || code === "FORBIDDEN") return "account";
  return "other";
}

export function connectionLabel(conn: relayFrames.PcConnectionState | "unknown"): string {
  switch (conn) {
    case "online":
      return "Online";
    case "reconnecting":
      return "Reconnecting";
    case "offline":
      return "Offline";
    default:
      return "Unknown";
  }
}

export function lifecycleLabel(state: relayFrames.LifecycleState | "sent", origin?: "agent" | "relay"): string {
  switch (state) {
    case "sent":
    case "created":
      return "Sent";
    case "accepted":
      return "PC received";
    case "awaiting_confirmation":
      return "Waiting for your confirmation";
    case "executing":
      return "Running on the PC";
    case "succeeded":
      return "Done";
    case "failed":
      return origin === "relay" ? "Not delivered" : "Failed";
    case "expired":
      return "Expired";
    case "canceled":
      return "Cancelled";
    case "outcome_unknown":
      return "Outcome unknown";
  }
}

/** The command an error belongs to, when the same code means something more specific for that action. */
export interface ErrorContext {
  action?: string | undefined;
  /** Who reported the terminal state: an agent-reported OUTCOME_UNKNOWN means the PC answered, unsure of the result. */
  origin?: "agent" | "relay" | undefined;
  target?: Record<string, unknown> | null | undefined;
}

const isSearch = (ctx?: ErrorContext) => ctx?.action === "youtube.search" || ctx?.action === "youtube.open_result";
/** A window action on an app (not one window picked from its list) that found no window: the app runs without one. */
const appWithoutWindow = (code: string, ctx?: ErrorContext) => code === "TARGET_GONE" && !!ctx?.action?.startsWith("app.") && !ctx.target?.window_id;

/**
 * Codes whose explanation is the PC's own: the agent knows why it pressed nothing in that app and how to allow full
 * screen for it on the PC, which the phone cannot know. Only an answer the agent reported (never the relay's) is
 * shown, as plain text (≤ 512 characters by the schema), with control and format characters turned into spaces.
 */
const PC_EXPLAINS: ReadonlySet<string> = new Set(["FULLSCREEN_UNSUPPORTED", "WINDOW_STATE_UNCHANGED"]);

/** The agent's own message for a code in PC_EXPLAINS, cleaned for display; null when there is none to show. */
function pcExplanation(error: { code: string; message?: string | undefined }, ctx?: ErrorContext): string | null {
  if (!PC_EXPLAINS.has(error.code) || ctx?.origin !== "agent" || typeof error.message !== "string") return null;
  const text = error.message.replace(/[\p{Cc}\p{Cf}]+/gu, " ").replace(/\s+/g, " ").trim();
  return text === "" ? null : text;
}

export function errorMessage(error: { code: string; message?: string } | null | undefined, ctx?: ErrorContext): string {
  if (!error) return "Something went wrong.";
  if (appWithoutWindow(error.code, ctx)) return "The app has no open window on the PC.";
  const fromPc = pcExplanation(error, ctx);
  if (fromPc) return fromPc;
  if (error.code === "OUTCOME_UNKNOWN" && ctx?.origin === "agent" && isSearch(ctx)) {
    return ctx?.action === "youtube.search" ? "YouTube did not finish loading the results on the PC, so DoMe can't tell what it shows." : "YouTube did not finish opening the video on the PC, so DoMe can't tell whether it is playing.";
  }
  if (error.code === "UNSUPPORTED_CONTEXT" && ctx?.action === "youtube.open_result") return "That tab on the PC is not showing the search results any more.";
  if (error.code === "ACTIVATION_REQUIRED" && ctx?.action === "youtube.set_paused") return "The browser on the PC blocked starting the video without a click there.";
  if (error.code === "PROTOCOL_INCOMPATIBLE" && isScreenAction(ctx)) return "Update DoMe on your PC to use Screen view.";
  const known = ERRORS[error.code];
  if (known) return known.user_message;
  if (error.code === "NETWORK") return error.message ?? "You appear to be offline.";
  if (error.code === "UNAUTHENTICATED") return "Your session has ended. Sign in again.";
  if (error.code === "NOT_FOUND") return "That item no longer exists.";
  if (error.code === "FORBIDDEN") return "This request was not allowed.";
  if (error.code === "LINK_EXPIRED") return "The link code expired or was already used. Start again on the PC.";
  if (error.code === "LINK_DENIED") return "Linking this PC was declined.";
  if (error.code === "NO_ANSWER") return "The PC did not answer in time. Nothing is known to have run.";
  if (error.code === SCREEN_UNSUPPORTED_BROWSER) return "This browser can't show your PC's picture. Open DoMe in your phone's browser or from the home-screen icon.";
  return "Something went wrong. Try again.";
}

/** The two Screen view actions: an incompatible PC gets the design's own line (§16), not the generic update message. */
const isScreenAction = (ctx?: ErrorContext) => !!ctx?.action?.startsWith("screen.");

/** Specific next steps per error code (spec §11), more specific where the action tells more; pages add their own. */
export function recoverySteps(code: string, ctx?: ErrorContext): string[] {
  if (appWithoutWindow(code, ctx)) return ["The app is running without a window on the PC (for example only in the system tray, or Edge's Startup boost).", "Open it from Apps to show its window, then try again."];
  if (code === "OUTCOME_UNKNOWN" && ctx?.origin === "agent" && isSearch(ctx)) return ["Look at the browser on the PC: if YouTube asks you to accept cookies or to sign in, do that once there.", ctx?.action === "youtube.search" ? "Then search again." : "Then search again and pick the video."];
  if (code === "UNSUPPORTED_CONTEXT" && ctx?.action === "youtube.open_result") return ["A video was opened from this list, so that tab plays it now.", "Search again, then pick from the new list."];
  switch (code) {
    case "PC_OFFLINE":
    case "PC_RECONNECTING":
      return ["Check that the PC is awake and connected to the internet.", "Open the DoMe tray icon on the PC and check that it shows Connected.", "Nothing was sent; try again once the PC shows Online here."];
    case "PC_REMOTE_DISABLED":
      return ["On the PC, open the DoMe tray menu and switch remote control back on.", "Remote commands can never turn it back on by themselves."];
    case "PC_SESSION_LOCKED":
    case "PC_SESSION_LOCKED_MEDIA_ONLY":
      return ["Unlock Windows on the PC to use this control.", "Media controls while locked can be allowed in the DoMe tray settings on the PC."];
    case "EXTENSION_NOT_INSTALLED":
      return ["Install the DoMe extension in Chrome or Edge on the PC.", "Open a YouTube video, then try again."];
    case "EXTENSION_DISCONNECTED":
      return ["Open Chrome or Edge on the PC.", "Check that the DoMe extension is enabled.", "Reload the YouTube tab if the extension icon shows it is not connected."];
    case "EXTENSION_UPDATE_REQUIRED":
      return ["The DoMe browser extension on the PC is older than this control.", "Update it (or reload it from the browser's Extensions page after updating DoMe on the PC), then try again.", "The other YouTube controls keep working meanwhile."];
    case "BROWSER_NOT_RUNNING":
      return ["Open Chrome or Edge on the PC and play a YouTube video."];
    case "TAB_NOT_CONTROLLABLE":
      return ["Reload the YouTube tab on the PC so the DoMe extension can attach to it."];
    case "TARGET_REQUIRED":
    case "TARGET_AMBIGUOUS":
      return ["Pick the tab or player you want to control from the list, then try again."];
    case "TARGET_GONE":
    case "TARGET_CHANGED":
      return ["The tab or window changed on the PC. Refresh and pick the current one."];
    case "PAIRING_CODE_INVALID":
      return ["Pairing codes last 5 minutes and work once.", "Ask the PC for a new code (DoMe tray → Pair a phone) and try again."];
    case "PAIRING_DECLINED":
      return ["Pairing was declined on the PC. Start again from the PC if that was a mistake."];
    case "CONTROLLER_REVOKED":
    case "UNKNOWN_KEY":
    case "SIGNATURE_INVALID":
    case "GRANT_MISSING":
      return ["This phone is no longer trusted by that PC.", "Pair again: on the PC choose Pair a phone, then scan the code here under Devices."];
    case "UNSUPPORTED_CONTEXT":
      return ["This page on the PC has no video player (for example YouTube search results), or an ad, a live stream or a Short is showing.", "Open a normal video, or wait for the ad to finish."];
    case "ACTIVATION_REQUIRED":
      return ["Browsers only allow full screen, and sometimes starting a video, after a click on the PC itself.", "For full screen: use Theater mode instead, press F on the PC, or double-click the video with the touchpad.", "For playback: press Play on the PC, or click the video with the touchpad."];
    case "SEARCH_RESULT_UNAVAILABLE":
      return ["The YouTube results page changed on the PC, so that result is not there any more.", "Search again, then pick from the new list."];
    case "ACTION_UNAVAILABLE":
    case "UNKNOWN_ACTION":
    case "PLATFORM_UNSUPPORTED":
      return ["This control is not available on that PC right now.", "Updating the DoMe agent on the PC may add it."];
    case "ENTITLEMENT_PENDING":
      return ["Your payment is being confirmed. Pro features unlock automatically once it is verified.", "No action is needed; check Billing in a few minutes."];
    case "BILLING_PAST_DUE":
      return ["Open Billing and choose Manage subscription to update your card.", "Free features keep working in the meantime."];
    case "ALREADY_SUBSCRIBED":
      return ["You already have a subscription.", "Open Billing and choose Manage subscription to change the plan or the card."];
    case "CHECKOUT_PROCESSING":
      return ["A payment page is being prepared, or your payment is being confirmed. Nothing more was charged.", "Wait a minute, then open Billing again; it updates by itself once the payment is verified."];
    case "PLAN_NOT_AVAILABLE":
      return ["Pro Plus adds only AI requests, and the AI butler is not switched on yet, so it is not sold yet. Nothing was charged.", "Choose Pro instead, or come back once the AI butler is on."];
    case "TOPUP_NOT_ALLOWED":
      return ["Top-ups are for DoMe Pro and Pro Plus while the subscription is active.", "Nothing was charged."];
    case "TOPUP_BALANCE_LIMIT":
      return ["Your account already holds the most top-up requests it can.", "Use some first; nothing was charged."];
    case "PLAN_SELECTION_CLOSED":
      return ["The time to choose here has passed; the most recently used devices stayed enabled.", "Enable and disable devices under Devices instead."];
    case "PLAN_SELECTION_INVALID":
      return ["Pick no more PCs and phones than your plan allows, from your own devices.", "Then save again."];
    case "BILLING_NOT_AVAILABLE":
      return ["Payments aren't available on this DoMe service yet. Nothing was charged.", "Everything you use today stays free."];
    case "BILLING_PROVIDER_ERROR":
      return ["Our payment provider didn't answer. Nothing was charged.", "Try again in a moment."];
    case "PC_PLAN_DISABLED":
    case "CONTROLLER_PLAN_DISABLED":
    case "DEVICE_LIMIT_REACHED":
      return ["Your plan limits how many PCs and phones are enabled.", "Choose which devices stay enabled under Devices, or upgrade to DoMe Pro when available."];
    case "OUTCOME_UNKNOWN":
      return ["The PC's connection was lost before it reported a result, so this may or may not have run.", "Check the PC's current state here before sending it again; the PC reports what happened once it reconnects."];
    case "COMMAND_EXPIRED":
      return ["The command did not reach the PC in time. Nothing ran.", "Try again when the PC shows Online."];
    case "CONFIRMATION_EXPIRED":
      return ["Confirmations expire after 60 seconds.", "Send the action again and confirm within a minute."];
    case "CLOSE_REFUSED":
      return ["The app stayed open, probably asking about unsaved work.", "Finish on the PC, or close it there."];
    case "FOCUS_DENIED":
      return ["Windows did not allow the app to come to the front.", "The app is running; click it on the PC if needed."];
    case "APP_NOT_APPROVED":
      return ["Approve the app on the PC first: open the DoMe tray menu and choose Add an app…", "It then appears under Apps here by itself."];
    case "APP_NOT_RUNNING":
      return ["Open it first from Apps, then try again.", "Maximize, Restore and Full screen never start an app by themselves."];
    case "FULLSCREEN_UNSUPPORTED":
      return ["Try Maximize instead.", "Not every app has an F11 full screen, and DoMe never maximizes in its place without asking."];
    case "WINDOW_STATE_UNCHANGED":
      return ["Some windows have a fixed size and cannot be maximized or resized.", "If the app is busy or not responding, wait for it on the PC and try again."];
    case "APP_ELEVATED":
      return ["That app runs as administrator, and Windows does not let other programs control its windows.", "Use it on the PC itself, or start it without administrator rights."];
    case "RATE_LIMITED":
    case "QUEUE_FULL":
      return ["Wait a moment and try again."];
    case "NETWORK":
      return ["Check your phone's internet connection.", "DoMe needs the internet to reach your PC; there is no local-only mode yet."];
    case "UNAUTHENTICATED":
      return ["Sign in again to continue.", "Your paired PCs stay paired; signing in does not require pairing again."];
    case "PROTOCOL_INCOMPATIBLE":
      return ["Update the DoMe agent on the PC and reload this app so both speak the same version."];
    case "INPUT_NOT_PERMITTED":
      return ["On the PC, open the DoMe tray menu → Paired phones and allow Touchpad and/or Keyboard for this phone.", "Existing pairings do not get these permissions automatically; only the PC owner can add them, on the PC.", "Then open the touchpad here again."];
    case "INPUT_SESSION_OWNED":
      return ["Another phone is using the touchpad on this PC right now.", "Choose Take over to end its session (its held buttons are released first), or wait until it stops."];
    case "INPUT_SESSION_REQUIRED":
    case "INPUT_SESSION_EXPIRED":
      return ["Tap Start on the touchpad to connect again.", "Nothing you did while disconnected is sent later."];
    case "INPUT_SUSPENDED":
      return ["The connection fell behind, so pending input was discarded rather than played back late.", "Tap Start to begin a fresh session."];
    case "INPUT_STALE":
    case "INPUT_SEQUENCE_INVALID":
      return ["Some input arrived too late or out of order and was dropped; nothing was replayed.", "Keep going — if it repeats, check the connection under Health."];
    case "INPUT_RESTRICTED":
      return ["Windows is showing a protected screen (lock screen, sign-in or an administrator prompt), where remote input is not allowed by design.", "Finish that screen on the PC itself, then continue."];
    case "INPUT_INJECTION_FAILED":
      return ["Windows did not accept that input. An elevated (administrator) window in front can cause this.", "Click a normal window on the PC and try again."];
    case "INPUT_TARGET_CHANGED":
      return ["The window in front of the PC changed, so remaining keys were not pressed into the wrong window.", "Check what is in front on the PC, click where you want to type, then continue."];
    case "NO_ANSWER":
      return ["The PC did not answer in time.", "Check the PC shows Online under Health, then try again."];
    // Screen view (protocol 1.4, docs/design/screen-view.md §16)
    case "SCREEN_PC_UNVERIFIED":
      return ["Pair this phone with your PC again so it can check it is really your PC.", "On the PC: DoMe tray › Paired phones › remove this phone, then pair."];
    case "SCREEN_ANSWER_INVALID":
      return ["Nothing was shown: the answer did not come from the PC key this phone trusts.", "If the PC was set up again, pair this phone with it again. Otherwise get help: this should not happen."];
    case "SCREEN_SESSION_OWNED":
      return ["Another phone is viewing this PC right now.", "Choose Take over to end its view, or wait until it stops."];
    case "SCREEN_SESSION_REQUIRED":
      return ["Taps on the picture need a live Screen view on this PC.", "Show the screen again, then tap."];
    case "SCREEN_GEOMETRY_CHANGED":
      return ["The PC's screen layout changed, so that tap was not sent.", "Wait for the picture to settle, then tap again."];
    case "SCREEN_ALLOWANCE_EXHAUSTED":
      return ["Free Screen view is 10 minutes per 5-hour window; the app shows when it refreshes.", "The touchpad and remote still work meanwhile."];
    case "SCREEN_OFFER_INVALID":
      return ["Try again.", "If it repeats, reload the app; the connection request it built was not accepted by the PC."];
    case "SCREEN_CAPTURE_UNAVAILABLE":
      return ["Close other apps that may be capturing or recording the PC's screen.", "Then try again. Some displays are not supported yet."];
    case "SCREEN_ENCODER_UNAVAILABLE":
      return ["This PC has no video encoder DoMe can use.", "Update the graphics driver on the PC, or get help."];
    case "SCREEN_CONNECTION_FAILED":
      return ["Your phone and PC could not reach each other on this network.", "Try again, or try another network. The touchpad and remote still work."];
    case "SCREEN_CONSENT_DECLINED":
      return ["Someone at the PC did not allow this view.", "Ask them, or allow it on the PC yourself; try again in a few minutes."];
    case "SCREEN_CONSENT_TIMEOUT":
      return ["No one answered at the PC within 30 seconds.", "Someone at the PC must choose Allow when the window appears."];
    case "SCREEN_VIEW_DISABLED":
      return ["Screen view is switched off for now.", "The touchpad and remote still work."];
    case "INPUT_PAUSED_ON_PC":
      return ["A DoMe window is open on the PC, so phone taps are paused until it closes.", "Someone at the PC must answer it."];
    case "INPUT_TARGET_RESERVED":
      return ["That tap would have landed on DoMe's own 'Screen view on' bar, so it was not sent.", "Tap elsewhere."];
    case SCREEN_UNSUPPORTED_BROWSER:
      return ["Open DoMe in your phone's own browser, or from the home-screen icon, and try again."];
    default:
      return [];
  }
}

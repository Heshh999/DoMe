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
  "app.minimize": "Minimise app",
  "app.close": "Close app",
  "power.sleep": "Sleep",
  "power.restart": "Restart",
  "power.shutdown": "Shut down",
  "power.cancel": "Cancel countdown",
  "input.session_start": "Start touchpad/keyboard",
  "input.session_stop": "Stop touchpad/keyboard",
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
      return `Minimise ${appName(target?.app_id, ctx)}`;
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
  };
  return labels[cap] ?? cap;
}

/** True for the two manual-input capabilities whose reach is every app of the unlocked session. */
export function isInputCapability(cap: string): boolean {
  return cap === "pointer" || cap === "keyboard";
}

/** Fixed, spec-mandated explanation of the manual-input permission (spec §10A D). */
export const INPUT_SCOPE_EXPLANATION = "Touchpad and keyboard input reaches every app of the unlocked Windows session, not only the approved apps — exactly like a mouse and keyboard plugged into the PC. The approved-app list limits structured app actions; it is not a sandbox around manual input. The PC owner grants and removes these permissions on the PC itself (DoMe tray → Paired phones).";

/** Which support category a help link should preselect for an error code. */
export function supportCategoryFor(code: string | null | undefined): "connection" | "pairing" | "media" | "input" | "apps" | "power" | "install" | "billing" | "account" | "other" {
  if (!code) return "other";
  if (code.startsWith("INPUT_")) return "input";
  if (code.startsWith("PAIRING_") || code === "CONTROLLER_REVOKED" || code === "UNKNOWN_KEY" || code === "GRANT_MISSING" || code === "SIGNATURE_INVALID") return "pairing";
  if (code.startsWith("PC_") || code === "NETWORK" || code === "OUTCOME_UNKNOWN" || code === "COMMAND_EXPIRED" || code === "PROTOCOL_INCOMPATIBLE" || code === "RATE_LIMITED" || code === "QUEUE_FULL") return "connection";
  if (code.startsWith("EXTENSION_") || code.startsWith("TARGET_") || code.startsWith("TAB_") || code === "BROWSER_NOT_RUNNING" || code === "UNSUPPORTED_CONTEXT" || code === "ACTIVATION_REQUIRED") return "media";
  if (code.startsWith("APP_") || code === "CLOSE_REFUSED" || code === "FOCUS_DENIED") return "apps";
  if (code.startsWith("POWER_") || code.startsWith("CONFIRMATION_")) return "power";
  if (code.startsWith("ENTITLEMENT_") || code.startsWith("BILLING_") || code.endsWith("_PLAN_DISABLED") || code === "DEVICE_LIMIT_REACHED") return "billing";
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

export function errorMessage(error: { code: string; message?: string } | null | undefined): string {
  if (!error) return "Something went wrong.";
  const known = ERRORS[error.code];
  if (known) return known.user_message;
  if (error.code === "NETWORK") return error.message ?? "You appear to be offline.";
  if (error.code === "UNAUTHENTICATED") return "Your session has ended. Sign in again.";
  if (error.code === "NOT_FOUND") return "That item no longer exists.";
  if (error.code === "FORBIDDEN") return "This request was not allowed.";
  if (error.code === "LINK_EXPIRED") return "The link code expired or was already used. Start again on the PC.";
  if (error.code === "LINK_DENIED") return "Linking this PC was declined.";
  if (error.code === "NO_ANSWER") return "The PC did not answer in time. Nothing is known to have run.";
  return "Something went wrong. Try again.";
}

/** Specific next steps per error code (spec §11); page-specific steps are added by the pages. */
export function recoverySteps(code: string): string[] {
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
      return ["This control is not available for the current page on the PC (for example an ad, a live stream or Shorts).", "Wait for the ad to finish or open a normal video."];
    case "ACTIVATION_REQUIRED":
      return ["Browsers only allow fullscreen from a click on the PC itself.", "Use Theater mode instead, or press F on the PC."];
    case "ACTION_UNAVAILABLE":
    case "UNKNOWN_ACTION":
    case "PLATFORM_UNSUPPORTED":
      return ["This control is not available on that PC right now.", "Updating the DoMe agent on the PC may add it."];
    case "ENTITLEMENT_PENDING":
      return ["Your payment is being confirmed. Pro features unlock automatically once it is verified.", "No action is needed; check Billing in a few minutes."];
    case "BILLING_PAST_DUE":
      return ["Update the payment method from Billing to keep DoMe Pro.", "Free features keep working in the meantime."];
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
      return ["Approve the app on the PC first (DoMe tray → Approved apps)."];
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
    default:
      return [];
  }
}

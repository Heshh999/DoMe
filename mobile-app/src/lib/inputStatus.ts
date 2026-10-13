/**
 * Pill copy for the manual-input session (Touchpad page). "Live" means Windows accepted the latest input (input_ack),
 * never that an app reacted; the accepted-event count is a detail (the pill's title), not the label.
 */
import type { Tone } from "../components/ui.tsx";
import type { ForegroundApp, InputSessionState } from "./input.ts";

export function sessionStatus(s: InputSessionState, now: number): { label: string; tone: Tone; pulse: boolean; detail?: string } {
  switch (s.phase) {
    case "live": {
      const ackAge = s.lastAck ? now - s.lastAck.receivedAt : null;
      if (s.lastAck && ackAge !== null && ackAge < 4000) return { label: "Live", tone: "success", pulse: false, detail: `Windows accepted ${s.lastAck.acceptedEvents} input event${s.lastAck.acceptedEvents === 1 ? "" : "s"}` };
      return { label: s.batchesSent > 0 ? "Live · waiting for the PC" : "Connected", tone: "info", pulse: true };
    }
    case "starting":
      return { label: "Connecting…", tone: "info", pulse: true };
    case "suspended":
      return { label: "Paused", tone: "warning", pulse: false };
    case "ended":
      return { label: "Ended", tone: "warning", pulse: false };
    case "failed":
      return { label: "Not connected", tone: "danger", pulse: false };
    default:
      return { label: "Not connected", tone: "neutral", pulse: false };
  }
}


/** "Chrome — title" / "notepad": what the PC says is in front; field-level focus is never claimed. */
export function foregroundLabel(app: ForegroundApp | null): string {
  const { name, title } = foregroundParts(app);
  return title ? `${name} — ${title}` : name;
}

/** The app in front and its window title, separately (the app name stays whole; the title is what truncates). */
export function foregroundParts(app: ForegroundApp | null): { name: string; title: string | null } {
  if (!app) return { name: "Foreground window unknown", title: null };
  const browser = app.browser === "chrome" ? "Chrome" : app.browser === "edge" ? "Edge" : app.browser === "other" ? "Browser" : null;
  return { name: browser ?? app.process_name.replace(/\.exe$/i, ""), title: app.window_title || null };
}


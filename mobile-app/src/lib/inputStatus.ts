/** Pill copy for the manual-input session (Touchpad page). The live label is Windows acceptance from input_ack, never an app effect. */
import type { Tone } from "../components/ui.tsx";
import type { ForegroundApp, InputSessionState } from "./input.ts";

export function sessionStatus(s: InputSessionState, now: number): { label: string; tone: Tone; pulse: boolean } {
  switch (s.phase) {
    case "live": {
      const ackAge = s.lastAck ? now - s.lastAck.receivedAt : null;
      if (s.lastAck && ackAge !== null && ackAge < 4000) return { label: `Live · Windows accepted ${s.lastAck.acceptedEvents}`, tone: "success", pulse: false };
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
  if (!app) return "Foreground window unknown";
  const browser = app.browser === "chrome" ? "Chrome" : app.browser === "edge" ? "Edge" : app.browser === "other" ? "Browser" : null;
  const name = browser ?? app.process_name.replace(/\.exe$/i, "");
  return app.window_title ? `${name} — ${app.window_title}` : name;
}


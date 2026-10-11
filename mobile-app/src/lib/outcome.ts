/** Tone and plain-language summary for a command record (used by CommandOutcome and tests). */
import type { CommandRecord } from "./commands.ts";
import type { Tone } from "../components/ui.tsx";

export function outcomeTone(record: CommandRecord): Tone {
  if (record.terminal) {
    switch (record.terminal.state) {
      case "succeeded":
        return "success";
      case "canceled":
        return record.terminal.error?.code === "COMMAND_SUPERSEDED" ? "neutral" : "warning";
      case "outcome_unknown":
      case "expired":
        return "warning";
      default:
        return "danger";
    }
  }
  if (record.noAnswer) return "warning";
  return "info";
}

export function resultSummary(record: CommandRecord): string | null {
  const r = record.terminal?.result;
  if (!r || record.terminal?.state !== "succeeded") return null;
  switch (record.action) {
    case "windows.set_volume":
    case "windows.set_muted":
    case "windows.get_volume":
      return `PC volume is now ${String(r.value)}%${r.muted ? " (muted)" : ""}.`;
    case "youtube.next":
    case "youtube.previous":
      return r.previous_video_id ? "The player moved to a different video." : "Done.";
    case "youtube.set_paused": {
      const tab = r.tab as { paused?: boolean } | undefined;
      return tab?.paused === undefined ? "Done." : tab.paused ? "YouTube is paused." : "YouTube is playing.";
    }
    case "youtube.search": {
      const n = Array.isArray(r.results) ? r.results.length : 0;
      return n === 0 ? "YouTube showed no video results for that search." : `Found ${n} video${n === 1 ? "" : "s"}. Tap one to play it.`;
    }
    case "youtube.open_result": {
      const tab = r.tab as { paused?: boolean } | undefined;
      // Play from the phone is a script call the browser's autoplay rule may refuse too; a click on the PC never is.
      return tab?.paused ? "Opened the video. If it does not start, press Play on the PC, or click the video with the Touchpad." : "Opened the video.";
    }
    case "youtube.set_volume": {
      const tab = r.tab as { volume?: number } | undefined;
      return tab?.volume === undefined ? "Done." : `YouTube volume is now ${tab.volume}%.`;
    }
    case "windows.lock":
      return "Windows accepted the lock request.";
    case "app.launch":
      return r.launched ? "The app was started." : r.running ? "The app was already running." : "Done.";
    case "app.focus":
      return r.focused ? "The app is in front." : "The app is running but Windows did not bring it to the front.";
    case "app.minimize":
      return r.minimized ? "The window was minimised." : "Done.";
    case "app.maximize":
    case "app.restore":
    case "app.set_fullscreen":
      return windowStateSummary(r);
    case "app.close":
      return r.closed ? "The app closed." : "The app stayed open.";
    case "power.sleep":
    case "power.restart":
    case "power.shutdown":
      return typeof r.countdown_seconds === "number" && r.countdown_seconds > 0 ? `Windows accepted the request; it runs in ${r.countdown_seconds} s unless cancelled.` : "Windows accepted the request.";
    case "power.cancel":
      return r.canceled ? "The countdown was cancelled." : "There was no countdown to cancel.";
    case "system.ping":
      return `The PC answered in ${record.terminal?.durationMs ?? 0} ms.`;
    default:
      return "Done.";
  }
}

/** app_window_state_result read back by the PC: what the window looks like now, and whether it came to the front. */
function windowStateSummary(r: Record<string, unknown>): string {
  const shape = r.fullscreen === true ? "in full screen" : r.state === "maximized" ? "maximised" : r.state === "minimized" ? "minimised" : "at normal size";
  return `The window is ${shape}.${r.foreground === false && r.state !== "minimized" ? " Windows did not bring it to the front." : ""}`;
}

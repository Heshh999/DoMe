/**
 * What the popup says for each connection state, in plain words for the PC owner: a short title that
 * says whether it works and what to do, and a hint with the next step. The technical reason (the
 * browser's or the agent's own message) is shown separately as the detail line.
 */
import type { ConnectionStateName, StatusReport } from "../shared/messages.ts";

export interface StatusCopy {
  title: string;
  hint: string;
  /** Indicator dot class: "connected", "warn", "error" or "" (neutral). */
  cls: string;
}

const RETRY = "then press Retry connection below";
const SELF_RETRY = "The extension also tries again by itself, every few seconds at first and then every 30 seconds.";

const COPY: Record<ConnectionStateName, StatusCopy> = {
  connected: { title: "Connected", hint: "Your phone can control YouTube in this browser.", cls: "connected" },
  connecting: { title: "Connecting...", hint: "Looking for DoMe on this PC. This takes a few seconds.", cls: "" },
  disconnected: { title: "Not connected - start DoMe on this PC", hint: `If DoMe is not running, start it (its icon appears next to the clock). If it is running, press Retry connection below. ${SELF_RETRY}`, cls: "warn" },
  agent_not_running: { title: "Not connected - start DoMe on this PC", hint: `DoMe is not running. Start DoMe (its icon appears next to the clock), ${RETRY}. ${SELF_RETRY}`, cls: "warn" },
  host_missing: { title: "Not connected - set up DoMe on this PC", hint: `DoMe is not set up for this browser yet. Run DoMe's setup on this PC (it registers this browser), ${RETRY}.`, cls: "error" },
  host_forbidden: { title: "Not connected - DoMe does not know this extension", hint: `DoMe on this PC accepts only the extension it was set up with. Run DoMe's setup on this PC again so it registers the Extension id shown below, ${RETRY}.`, cls: "error" },
  incompatible: { title: "Not connected - update needed", hint: `This extension and DoMe on this PC are different versions. Update whichever is older, ${RETRY}.`, cls: "error" },
  error: { title: "Not connected - something went wrong", hint: "Press Retry connection below. If this keeps happening, close the browser completely and open it again.", cls: "error" },
};

export function describeStatus(status: StatusReport): StatusCopy {
  const copy = COPY[status.connection.state] ?? COPY.error;
  if (status.connection.state === "connected" && status.attached_tabs === 0) {
    return { ...copy, hint: "Open a YouTube video in this browser to control it from your phone." };
  }
  return copy;
}

/** The technical reason, kept for support but labelled so it does not read as the main message. */
export function describeDetail(status: StatusReport): string {
  return status.connection.message ? `Details: ${status.connection.message}` : "";
}

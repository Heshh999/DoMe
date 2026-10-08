/** Truthful connection summary for a PC from REST inventory + live relay state. */
import type { rest } from "@dome/protocol";

import type { Tone } from "../components/ui.tsx";
import type { LivePc } from "../store/live.ts";
import { relativeTime } from "./format.ts";

export function connectionOf(pc: rest.Pc | undefined, live: LivePc | undefined): { label: string; tone: Tone; pulse: boolean; detail: string } {
  const conn = live?.connection && live.connection !== "unknown" ? live.connection : (pc?.connection ?? "offline");
  const lastSeen = live?.lastSeen ?? pc?.last_seen ?? null;
  if (live?.refused === "GRANT_MISSING") return { label: "Not paired with this phone", tone: "warning", pulse: false, detail: "Pair this phone with the PC to control it." };
  if (pc && !pc.enabled) return { label: "Disabled on your plan", tone: "neutral", pulse: false, detail: "Enable it under Devices (plan limits apply)." };
  switch (conn) {
    case "online":
      return live?.stale ? { label: "Online · refreshing", tone: "info", pulse: true, detail: "Waiting for the PC’s current state." } : { label: "Online", tone: "success", pulse: false, detail: live?.state?.remote_enabled === false ? "Remote control is switched off on the PC." : "Connected through DoMe." };
    case "reconnecting":
      return { label: "Reconnecting", tone: "warning", pulse: true, detail: `Last seen ${relativeTime(lastSeen)}.` };
    default:
      return { label: "Offline", tone: "neutral", pulse: false, detail: `Last seen ${relativeTime(lastSeen)}. Commands are not stored for later.` };
  }
}


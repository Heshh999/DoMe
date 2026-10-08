/**
 * What the dashboard may truthfully say about a power request after the PC disconnected.
 *
 * `pc_status.last_power_request` is written by the relay when it FORWARDS a power command, i.e. while
 * the command is still `created`: before the PC checked the signature, before the confirmation
 * challenge and before any OS call. On its own it is therefore evidence of a request, never of
 * acceptance. "Accepted by Windows" is only claimed when this phone's own command record for that
 * action carries an `executing` ack or an agent `succeeded` result (a relay `outcome_unknown` is only
 * emitted after an executing ack was seen — version.json rules.in_flight — so it counts too).
 */
import type { CommandRecord } from "./commands.ts";
import { relativeTime } from "./format.ts";

export type PowerEvidence = "accepted" | "requested";

/** A record is matched to the relay's note when it is for the same PC and action and close in time. */
const MATCH_WINDOW_MS = 10 * 60_000;

const VERB: Record<string, string> = {
  "power.sleep": "sleep",
  "power.restart": "restart",
  "power.shutdown": "shutdown",
  "power.cancel": "countdown cancel",
};

export function powerVerb(action: string): string {
  return VERB[action] ?? action.replace(/^power\./, "").replace(/_/g, " ");
}

export function powerRequestEvidence(commands: readonly CommandRecord[], pcId: string, request: { action: string; at: string }): PowerEvidence {
  const at = Date.parse(request.at);
  for (const c of commands) {
    if (c.pcId !== pcId || c.action !== request.action) continue;
    if (Number.isFinite(at) && Math.abs(c.createdAt - at) > MATCH_WINDOW_MS) continue;
    if (c.state === "executing") return "accepted";
    if (c.terminal?.origin === "agent" && c.terminal.state === "succeeded") return "accepted";
    if (c.terminal?.origin === "relay" && c.terminal.state === "outcome_unknown") return "accepted";
  }
  return "requested";
}

export function powerRequestNotice(request: { action: string; at: string }, evidence: PowerEvidence, now: Date = new Date()): string {
  const verb = powerVerb(request.action);
  const when = relativeTime(request.at, now);
  if (evidence === "accepted") return `Windows accepted the ${verb} request ${when}; the PC then disconnected. DoMe cannot confirm whether it completed.`;
  return `A ${verb} was requested ${when}. The PC has since disconnected; DoMe cannot tell whether it ran.`;
}

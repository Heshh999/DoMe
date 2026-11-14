/** Support-ticket categories (rest.schema.json support_ticket_request.category) with customer labels. */
import { ProtocolError, type rest } from "@dome/protocol";

import { youtubeComing } from "../content/windowsAppStatus.ts";
import { getWindowsRelease, type WindowsRelease } from "../content/windowsRelease.ts";
import { ApiError } from "./api.ts";
import { supportCategoryFor } from "./labels.ts";

/**
 * The Support page's YouTube topic: "coming" while no offered release has the browser add-on (content/windowsAppStatus.ts),
 * the extension steps once one has.
 */
export function youtubeTopic(release: WindowsRelease | null = getWindowsRelease()): { title: string; steps: string[] } {
  const coming = youtubeComing(release);
  if (!coming) return { title: "YouTube controls say the extension is not connected", steps: ["Open Chrome or Edge on the PC and check the DoMe extension is installed and enabled.", "Reload the YouTube tab so the extension attaches to it.", "Restart the browser if the extension icon still shows it is disconnected."] };
  return { title: "YouTube control in Chrome or Edge (coming)", steps: [coming, "Windows media works now: Play/Pause, next, previous and volume work on whatever Windows is playing."] };
}

export const CATEGORIES: Array<{ value: rest.SupportTicketRequest["category"]; label: string }> = [
  { value: "connection", label: "Connection / PC offline" },
  { value: "pairing", label: "Pairing / phone access" },
  { value: "media", label: "YouTube / media controls" },
  { value: "input", label: "Touchpad / keyboard" },
  { value: "apps", label: "Apps" },
  { value: "power", label: "Lock / sleep / shutdown" },
  { value: "install", label: "Install / update on Windows" },
  { value: "billing", label: "Plan / billing" },
  { value: "account", label: "Account / sign-in" },
  { value: "other", label: "Something else" },
];


/** `/support?category=…&code=…` for a failure: the form preselects the category and carries the code. */
export function supportLink(code: string | null | undefined): string {
  const params = new URLSearchParams();
  params.set("category", supportCategoryFor(code));
  if (code && /^[A-Z_]+$/.test(code)) params.set("code", code);
  return `/support?${params.toString()}`;
}


/**
 * What a failed ticket POST says about receipt (spec §11A honesty, both directions):
 *  - "not_sent": the request definitely did not create a ticket — it never left the phone (no CSRF
 *    token, request body failed the contract) or the server answered with a definite 4xx rejection;
 *  - "unconfirmed": the request may have been stored — the connection failed or timed out (the POST
 *    may have left before it dropped), the server answered 5xx (possibly after committing), or a 2xx
 *    answer failed `support_ticket_response` validation. The customer checks "Your requests" before
 *    retrying, because the request schema carries no idempotency key (CONTRACT_ISSUES.md).
 */
export type SubmissionFailure = "not_sent" | "unconfirmed";

export function submissionFailure(e: unknown): SubmissionFailure {
  if (e instanceof ApiError) {
    if (e.status === 0) return e.code === "NETWORK" ? "unconfirmed" : "not_sent";
    if (e.status >= 400 && e.status < 500) return "not_sent";
    return "unconfirmed";
  }
  if (e instanceof ProtocolError) return "not_sent";
  return "unconfirmed";
}

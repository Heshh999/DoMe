/** Support-ticket categories (rest.schema.json support_ticket_request.category) with customer labels. */
import type { rest } from "@dome/protocol";

import { supportCategoryFor } from "./labels.ts";

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


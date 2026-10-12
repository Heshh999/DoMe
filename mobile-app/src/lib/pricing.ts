/**
 * The pricing page's comparison rows for Free, Pro and Pro Plus: device counts, routines and layouts come
 * from the central plan contract (plans.json); Screen view, the AI butler allowance and the top-up from
 * the website's plan content (src/content/plans.ts). Rows are grouped the way the table shows them.
 */
import { PLANS } from "@dome/protocol";

import { FREE_SCREEN_VIEW_MINUTES, FREE_TASTE_REQUESTS, planById, SCREEN_VIEW_STATUS, TOP_UP, usd, WINDOW_HOURS } from "../content/plans.ts";

/** Cell values with a fixed meaning: the page draws a check mark or a dash for them. */
export const INCLUDED = "Included";
export const NOT_INCLUDED = "—";
export const AT_PAID_LAUNCH = "At paid launch";

export type Row = { label: string; free: string; pro: string; proPlus: string };
export type RowGroup = { title: string; status?: string; rows: Row[] };

const same = (label: string, value: string): Row => ({ label, free: value, pro: value, proPlus: value });

export function pricingRows(): Row[] {
  return pricingGroups().flatMap((g) => g.rows);
}

export function pricingGroups(): RowGroup[] {
  const free = PLANS.plans.free;
  const pro = PLANS.plans.pro;
  const proPlus = planById("pro_plus");
  const proContent = planById("pro");
  const yes = INCLUDED;
  const no = NOT_INCLUDED;
  return [
    {
      title: "Remote control",
      rows: [
        same("YouTube controls, Windows media, system volume", yes),
        same("Approved app launch, focus, minimise, close", yes),
        same("Lock and confirmed power controls", yes),
        same("Touchpad, keyboard, typed commands and dictation", yes),
        same("Secure access away from home through the DoMe service", yes),
        same("Everyday commands DoMe understands by itself", "Unlimited, never counted"),
        same("Fair-use limit on manual controls", `${free.manual_command_rate_limit.per_minute} per minute`),
      ],
    },
    {
      title: "Devices",
      rows: [
        { label: "Linked PCs that can be controlled", free: String(free.max_enabled_pcs), pro: `Up to ${pro.max_enabled_pcs}`, proPlus: `Up to ${proPlus.pcs}` },
        { label: "Paired phones per account", free: `Up to ${free.max_controllers}`, pro: `Up to ${pro.max_controllers}`, proPlus: `Up to ${proPlus.phones}` },
      ],
    },
    {
      title: "Screen view",
      status: SCREEN_VIEW_STATUS,
      rows: [{ label: "Your PC’s screen live on your phone, tap what you see", free: `${FREE_SCREEN_VIEW_MINUTES} min per ${WINDOW_HOURS}-hour window`, pro: "Unlimited", proPlus: "Unlimited" }],
    },
    {
      title: "AI butler",
      rows: [
        { label: `AI requests per ${WINDOW_HOURS}-hour window`, free: `${FREE_TASTE_REQUESTS} to try, once`, pro: String(proContent.ai.perWindow?.requests ?? ""), proPlus: String(proPlus.ai.perWindow?.requests ?? "") },
        { label: "AI requests per week", free: no, pro: `Up to ${proContent.ai.perWeek?.requests ?? ""}`, proPlus: `Up to ${proPlus.ai.perWeek?.requests ?? ""}` },
        { label: `Top-up: ${TOP_UP.requests} requests for ${usd(TOP_UP.priceCents)}, only when you choose`, free: no, pro: yes, proPlus: yes },
      ],
    },
    {
      title: "At paid launch",
      rows: [
        { label: "Custom remote layouts and named profiles", free: free.custom_layouts ? yes : no, pro: pro.custom_layouts ? AT_PAID_LAUNCH : no, proPlus: pro.custom_layouts ? AT_PAID_LAUNCH : no },
        { label: `Saved routines (up to ${pro.routine_max_steps} steps)`, free: free.routines ? yes : no, pro: pro.routines ? AT_PAID_LAUNCH : no, proPlus: pro.routines ? AT_PAID_LAUNCH : no },
      ],
    },
    {
      title: "Security",
      rows: [same("Permissions, revocation, security activity", yes), same("Emergency stop on the PC", yes)],
    },
  ];
}

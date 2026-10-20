/**
 * What the server says about buying and about the AI butler, for the public pages (pricing, landing, FAQ): GET
 * /v1/plans once per page (public, no session). `null` until it answers; a failed answer keeps `null`, so every page
 * keeps its static, honest labels ("Coming at paid launch", AI "Not switched on yet"). Pro Plus differs from Pro only
 * in AI requests, so it is sold only while the assistant is switched on (the server refuses it otherwise).
 */
import { useEffect, useState } from "react";

import type { rest } from "@dome/protocol";

import { api } from "./api.ts";

export interface BillingAvailability {
  /** Checkout exists (Stripe values are set on this server). */
  selling: boolean;
  mode: rest.BillingMode;
  /** The AI assistant can be used on this server (DOME_AI_ENABLED). */
  aiEnabled: boolean;
}

export function availabilityFrom(p: rest.PlansResponse): BillingAvailability {
  return { selling: p.billing_enabled && p.billing_mode !== "off", mode: p.billing_mode, aiEnabled: p.ai_enabled };
}

/** Whether a paid plan can be bought now. */
export function sellsPlan(availability: BillingAvailability | null, plan: "pro" | "pro_plus"): boolean {
  if (!availability?.selling) return false;
  return plan === "pro" || availability.aiEnabled;
}

/** Whether the AI butler can be used now; unknown (no answer yet, or a failed one) counts as no. */
export const aiSwitchedOn = (availability: BillingAvailability | null): boolean => availability?.aiEnabled === true;

/** The landing page's closing-card sentence: what the server sells now (GET /v1/plans); until it answers, "at paid launch". */
export function paidPlansLine(availability: BillingAvailability | null): string {
  if (!availability?.selling) return "Both arrive at paid launch.";
  if (sellsPlan(availability, "pro_plus")) return "Subscribe to either from Billing in the app.";
  return "Pro can be bought from Billing in the app; Pro Plus goes on sale once the AI butler is switched on.";
}

export function useBillingAvailability(): BillingAvailability | null {
  const [availability, setAvailability] = useState<BillingAvailability | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .plans()
      .then((p) => {
        if (!cancelled) setAvailability(availabilityFrom(p));
      })
      .catch(() => {
        /* the page keeps its static labels */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return availability;
}

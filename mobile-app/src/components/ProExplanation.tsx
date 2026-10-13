/**
 * The only upgrade copy in the app (spec §12 "Upgrade experience"): shown when the customer
 * deliberately opened a Pro-only surface, names the specific benefit they selected, is dismissible,
 * and never appears on a Free flow (media, touchpad, typing, pairing, reconnects, health, support).
 * It also never implies that subscribing fixes a connectivity or compatibility problem. The price and the
 * "coming at paid launch" status come from the website's plan content (src/content/plans.ts), so the app
 * and the pricing page agree.
 */
import { useState } from "react";
import { Link } from "react-router";

import { PLANS } from "@dome/protocol";

import { COMING_AT_PAID_LAUNCH, planById, usd } from "../content/plans.ts";
import { Button, Notice } from "./ui.tsx";

export function ProExplanation({ benefit, children }: { benefit: string; children?: React.ReactNode }) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  const pro = PLANS.plans.pro;
  const price = planById("pro").price;
  const priceLine = price ? ` (${usd(price.monthlyCents)}\u00a0/\u00a0month or ${usd(price.annualCents)}\u00a0/\u00a0year)` : "";
  return (
    <div data-testid="pro-explanation">
      <Notice tone="info" title={`${benefit} is part of ${pro.display_name}`}>
        {children}
        <p>
          There is nothing to buy yet: {pro.display_name}
          {priceLine} and {planById("pro_plus").displayName} are {COMING_AT_PAID_LAUNCH.toLowerCase()}. Everything you use today — media controls, touchpad and keyboard, diagnostics, recovery and support — stays free. See <Link to="/app/billing" className="text-accent font-semibold underline">Billing</Link> for your plan.
        </p>
        <Button size="md" variant="ghost" onClick={() => setDismissed(true)}>
          Dismiss
        </Button>
      </Notice>
    </div>
  );
}

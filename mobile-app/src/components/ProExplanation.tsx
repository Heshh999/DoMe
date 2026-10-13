/**
 * The only upgrade copy in the app (spec §12 "Upgrade experience"): shown when the customer
 * deliberately opened a Pro-only surface, names the specific benefit they selected, is dismissible
 * (remembered on this phone), and never appears on a Free flow (media, touchpad, typing, pairing,
 * reconnects, health, support). It also never implies that subscribing fixes a connectivity or
 * compatibility problem. The status comes from the website's plan content (src/content/plans.ts),
 * so the app and the pricing page agree. Two lines and two links: the page's own content comes first.
 */
import { useState } from "react";
import { Link } from "react-router";

import { COMING_AT_PAID_LAUNCH, planById, usd } from "../content/plans.ts";
import { Button, Notice } from "./ui.tsx";

const KEY = "dome.proExplanation.dismissed";

function dismissedList(): string[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function ProExplanation({ benefit }: { benefit: string }) {
  const [dismissed, setDismissed] = useState(() => dismissedList().includes(benefit));
  if (dismissed) return null;
  const pro = planById("pro");
  const proPlus = planById("pro_plus");
  const dismiss = () => {
    setDismissed(true);
    try {
      localStorage.setItem(KEY, JSON.stringify([...new Set([...dismissedList(), benefit])]));
    } catch {
      /* not remembered on this phone: it shows again next time */
    }
  };
  return (
    <div data-testid="pro-explanation">
      <Notice tone="info" title={`Part of ${pro.name} and ${proPlus.name}: ${benefit}`}>
        <p>
          {COMING_AT_PAID_LAUNCH} ({pro.name} from {usd(pro.price!.monthlyCents)}&nbsp;/&nbsp;month); there is nothing to buy yet. Everything you use today stays free.
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
          <Link to="/pricing" className="tap inline-flex items-center font-semibold text-accent">
            What’s in Pro
          </Link>
          <Link to="/app/billing" className="tap inline-flex items-center font-semibold text-accent">
            Billing
          </Link>
          <Button size="sm" variant="ghost" className="-mr-2 ml-auto" onClick={dismiss}>
            Dismiss
          </Button>
        </div>
      </Notice>
    </div>
  );
}

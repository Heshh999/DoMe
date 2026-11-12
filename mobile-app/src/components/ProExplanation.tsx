/**
 * The only upgrade copy in the app (spec §12 "Upgrade experience"): shown when the customer
 * deliberately opened a Pro-only surface, names the specific benefit they selected, is dismissible
 * (remembered on this phone), and never appears on a Free flow (media, touchpad, typing, pairing,
 * reconnects, health, support). It also never implies that subscribing fixes a connectivity or
 * compatibility problem. The status comes from the website's plan content (src/content/plans.ts),
 * so the app and the pricing page agree. Two lines and two links: the page's own content comes first.
 *
 * What it says about buying follows the server, with the signal the pricing page and the FAQ use (GET /v1/plans,
 * lib/useBillingAvailability.ts; the Billing page reads the same server setting from GET /v1/account/billing): until
 * the server answers, when the answer fails, or while it sells nothing, Pro is "Coming at paid launch" and there is
 * nothing to buy; while it sells Pro, Pro is on sale in Billing. Every Pro-only surface today is a preview of a
 * feature that is not built (routines, custom layouts: plans.ts LAYOUTS_STATUS), so the on-sale line also says that
 * buying Pro does not add it yet. On a paid plan it says nothing about buying.
 */
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { COMING_AT_PAID_LAUNCH, planById, usd } from "../content/plans.ts";
import { sellsPlan, useBillingAvailability } from "../lib/useBillingAvailability.ts";
import { useSessionStore } from "../store/session.ts";
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
  const dismiss = () => {
    setDismissed(true);
    try {
      localStorage.setItem(KEY, JSON.stringify([...new Set([...dismissedList(), benefit])]));
    } catch {
      /* not remembered on this phone: it shows again next time */
    }
  };
  return <Explanation benefit={benefit} onDismiss={dismiss} />;
}

/** The notice itself: asks the server what it sells only while the explanation is showing. */
function Explanation({ benefit, onDismiss }: { benefit: string; onDismiss: () => void }) {
  const availability = useBillingAvailability();
  const plan = useSessionStore((s) => s.session?.plan ?? "free");
  const pro = planById("pro");
  const proPlus = planById("pro_plus");
  const price = <>{usd(pro.price!.monthlyCents)}&nbsp;/&nbsp;month</>;
  let line: ReactNode;
  if (plan !== "free") {
    line = <>Your plan includes this once it arrives; it is not in the app yet.</>;
  } else if (sellsPlan(availability, "pro")) {
    line = (
      <>
        {pro.name} is on sale in Billing from {price}. This is not in the app yet, so buying {pro.name} does not add it now.{availability?.mode === "test" ? " Test mode: no real charges." : ""} Everything you use today stays free.
      </>
    );
  } else {
    line = (
      <>
        {COMING_AT_PAID_LAUNCH} ({pro.name} from {price}); there is nothing to buy yet. Everything you use today stays free.
      </>
    );
  }
  return (
    <div data-testid="pro-explanation">
      <Notice tone="info" title={`Part of ${pro.name} and ${proPlus.name}: ${benefit}`}>
        <p>{line}</p>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
          <Link to="/pricing" className="tap inline-flex items-center font-semibold text-accent">
            What’s in Pro
          </Link>
          <Link to="/app/billing" className="tap inline-flex items-center font-semibold text-accent">
            Billing
          </Link>
          <Button size="sm" variant="ghost" className="-mr-2 ml-auto" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
      </Notice>
    </div>
  );
}

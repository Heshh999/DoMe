/**
 * What a customer reads before paying for a subscription (docs/legal; the Terms' "Billing and automatic renewal" and
 * "Cancelling"): the plan renews automatically at the price shown until cancelled, how to cancel, and that subscribing
 * means agreeing to the Terms and the Privacy notice, with links to both. Under every "Continue to payment" button on
 * the Billing page (one interval) and under the paid plans on the pricing page (both intervals). The prices come from
 * the caller's plan data; the words from plans.ts.
 *
 * The renewal sentence must stand out (state automatic-renewal laws ask for "clear and conspicuous" terms): it is set
 * at text-sm in the normal text colour, larger and darker than the caller's small muted text around it, with
 * "Renews automatically" in bold. The cancel and Terms lines keep the caller's style.
 */
import { Link } from "react-router";

import { HOW_TO_CANCEL, renewalLine, RENEWS_AUTOMATICALLY, TAX_INCLUDED, type PlanPrice, type RenewalInterval } from "./plans.ts";

const LINK = "font-semibold text-accent underline decoration-accent/35 underline-offset-[3px] hover:decoration-current";
/** The renewal sentence's own style, whatever the caller's className says. */
export const RENEWAL_CLASS = "text-sm leading-snug text-text";

export function SubscriptionTerms({ price, interval, className = "", testId }: { price: PlanPrice; interval: RenewalInterval; className?: string; testId?: string }) {
  const rest = renewalLine(price, interval).slice(RENEWS_AUTOMATICALLY.length);
  return (
    <div className={`space-y-1 ${className}`} data-testid={testId}>
      <p className={RENEWAL_CLASS} data-testid={testId ? `${testId}-renewal` : undefined}>
        <strong className="font-semibold">{RENEWS_AUTOMATICALLY}</strong>
        {rest} {TAX_INCLUDED}
      </p>
      <p>{HOW_TO_CANCEL}</p>
      <p>
        By subscribing you agree to the{" "}
        <Link to="/terms" className={LINK}>
          Terms
        </Link>{" "}
        and the{" "}
        <Link to="/privacy" className={LINK}>
          Privacy notice
        </Link>
        .
      </p>
    </div>
  );
}

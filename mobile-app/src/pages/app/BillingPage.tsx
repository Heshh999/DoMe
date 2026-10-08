/**
 * Billing status without checkout (Phase C adds Stripe Checkout/Portal). Everything shown comes
 * from the backend's session document; "pending" is only displayed when the backend says so.
 */
import { PLANS } from "@dome/protocol";

import { Card, Notice, Pill, Steps, type Tone } from "../../components/ui.tsx";
import { money } from "../../lib/format.ts";
import { recoverySteps } from "../../lib/labels.ts";
import { useSessionStore } from "../../store/session.ts";

const STATE_COPY: Record<string, { label: string; tone: Tone; text: string }> = {
  free: { label: "Free", tone: "neutral", text: "You are on DoMe Free. Everything you use today stays free; no card is needed." },
  active: { label: "Active", tone: "success", text: "Your DoMe Pro subscription is active." },
  pending: { label: "Pending activation", tone: "warning", text: "Your payment is being confirmed. Pro features unlock automatically once the payment is verified." },
  past_due: { label: "Payment problem", tone: "danger", text: "Your last payment did not go through. Free features keep working." },
  canceling: { label: "Ends at period end", tone: "warning", text: "Your subscription is set to end at the close of the current period. Pro features stay until then; your settings are kept afterwards." },
  expired: { label: "Expired", tone: "neutral", text: "Your Pro subscription ended. You are on DoMe Free; your PCs, phones and settings were kept." },
};

export function BillingPage() {
  const session = useSessionStore((s) => s.session);
  if (!session) return null;
  const plan = PLANS.plans[session.plan];
  const state = session.entitlement_state ?? (session.plan === "pro" ? "active" : "free");
  const copy = STATE_COPY[state] ?? STATE_COPY.free!;
  const pricing = PLANS.pricing_defaults;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Billing</h1>
      <Card>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-text-faint uppercase tracking-wider">Current plan</p>
            <p className="text-xl font-bold">{plan.display_name}</p>
          </div>
          <Pill tone={copy.tone}>{copy.label}</Pill>
        </div>
        <p className="text-sm text-text-muted mt-2">{copy.text}</p>
        {state === "past_due" ? (
          <div className="mt-2 text-sm text-text-muted">
            <Steps steps={recoverySteps("BILLING_PAST_DUE")} />
          </div>
        ) : null}
      </Card>
      <Card>
        <h2 className="font-semibold mb-2">Your limits</h2>
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-text-muted">Enabled PCs</dt>
          <dd className="font-medium text-right">{session.limits.max_enabled_pcs}</dd>
          <dt className="text-text-muted">Paired phones</dt>
          <dd className="font-medium text-right">{session.limits.max_controllers}</dd>
          <dt className="text-text-muted">Routines</dt>
          <dd className="font-medium text-right">{session.limits.routines ? "Included" : "Not included"}</dd>
          <dt className="text-text-muted">Custom layouts</dt>
          <dd className="font-medium text-right">{session.limits.custom_layouts ? "Included" : "Not included"}</dd>
        </dl>
      </Card>
      <Notice tone="info" title="Upgrading is not available yet">
        <p>
          DoMe Pro is planned at {money(pricing.monthly_cents, pricing.currency)} per month or {money(pricing.annual_cents, pricing.currency)} per year (planned pricing, not final). Checkout, invoices and the payment portal arrive with the paid launch; nothing can be charged from this app today.
        </p>
      </Notice>
      <p className="text-xs text-text-faint">Security controls — revoking phones, unlinking PCs, emergency stop on the PC — are included on every plan and are never paywalled.</p>
    </div>
  );
}

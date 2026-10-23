/**
 * Billing (docs/BILLING.md; protocol 1.3). Everything shown about the customer's own plan comes from the server:
 * GET /v1/account/billing (the subscription, what can be bought, the top-up) and GET /v1/session (the plan and its
 * limits). Buying happens on Stripe's hosted pages, reached through POST /v1/billing/checkout, /topup and /portal;
 * returning from them unlocks nothing — the `?checkout=` / `?topup=` query is informational, the page polls the server
 * until it says the plan changed, and says "pending" until then. Without Stripe keys the server reports
 * `billing_enabled: false` and this page stays today's honest page: the plan, its limits, and a notice that upgrading is
 * not available yet, with the prices of the website's plan content (src/content/plans.ts).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import type { rest } from "@dome/protocol";

import { loadAssistantStatus } from "../../app/voice.ts";
import { Button, Card, CheckRow, IconWell, KeyValue, Notice, PageHeader, Pill, Segmented, Spinner, Steps } from "../../components/ui.tsx";
import { AI_OFF_STATUS, aiSummary, COMING_AT_PAID_LAUNCH, planById, usd, type PlanContent } from "../../content/plans.ts";
import { api, ApiError } from "../../lib/api.ts";
import { allowanceSummary, billingErrorText, billingStateCopy, browserNavigation, bucketLevel, isCheckoutUrl, longDate, providerErrorReason, providerTestModeLine, refreshText, topupLabel, type AllowanceBucket, type AssistantAllowance, type BillingStatusResponse, type BucketLevel, type ProviderErrorReason } from "../../lib/billing.ts";
import { relativeTime } from "../../lib/format.ts";
import { recoverySteps } from "../../lib/labels.ts";
import { useAssistantStore } from "../../store/assistant.ts";
import { useSessionStore } from "../../store/session.ts";

/** How often, and for how long, the page asks the server after a return from Stripe (bounded; cleared on unmount). */
export const RETURN_POLL_MS = 3000;
export const RETURN_POLL_LIMIT = 40;

export const STILL_CONFIRMING = "Still confirming. This page updates when the payment is verified; nothing is unlocked before that. If it takes longer than a few minutes, contact support (reference your email).";
export const STRIPE_NOTE = "You'll finish on Stripe's secure page. Card details never touch DoMe. Prices in US dollars, tax included.";
export const TEST_MODE = "Test mode: no real charges";
export const NOT_SWITCHED_ON = AI_OFF_STATUS;
/** Under the offers while the server sells Pro but not Pro Plus (`checkout_plans` without it: the assistant is off). */
export const PRO_PLUS_NOT_SOLD = "Pro Plus is not on sale yet: its extra AI requests are its only difference from Pro, and the AI butler is not switched on yet.";

type ReturnOutcome = "polling" | "cancel" | "confirmed" | "timeout";
interface Returned {
  kind: "checkout" | "topup";
  outcome: ReturnOutcome;
}

/** `?checkout=return|cancel` or `?topup=return|cancel` from Stripe's return URLs; anything else is no return at all. */
function readReturn(params: URLSearchParams): Returned | null {
  const checkout = params.get("checkout");
  const topup = params.get("topup");
  if (!checkout && !topup) return null;
  return { kind: checkout ? "checkout" : "topup", outcome: (checkout ?? topup) === "cancel" ? "cancel" : "polling" };
}

/** "$9.99 / month or $99.99 / year", written the way the website's pricing page writes it; each price stays on one line. */
function priceLine(plan: PlanContent): string {
  return plan.price ? `${usd(plan.price.monthlyCents)}\u00a0/\u00a0month or ${usd(plan.price.annualCents)}\u00a0/\u00a0year` : usd(0);
}

const BAR: Record<BucketLevel, string> = { ok: "bg-success", warn: "bg-warning", exhausted: "bg-danger" };
const BAR_TEXT: Record<BucketLevel, string> = { ok: "text-text-muted", warn: "text-warning", exhausted: "text-danger" };

/** One window's progress: used of limit, amber from 80 %, red when full, with its refresh time while it is open. */
function AllowanceBar({ label, bucket, now }: { label: string; bucket: AllowanceBucket; now: Date }) {
  const level = bucket.limit > 0 ? bucketLevel(bucket.used, bucket.limit) : "ok";
  const percent = bucket.limit > 0 ? Math.min(100, Math.round((bucket.used / bucket.limit) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium">{label}</span>
        <span className={`tabular-nums ${BAR_TEXT[level]}`}>
          {bucket.used} of {bucket.limit}
        </span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={bucket.limit} aria-valuenow={bucket.used} className="h-2 overflow-hidden rounded-full bg-bg-sunken">
        <div className={`h-full rounded-full ${BAR[level]}`} style={{ width: `${percent}%` }} />
      </div>
      <p className="text-xs text-text-muted">{bucket.ends_at ? refreshText(bucket.ends_at, now) : "Starts with your next request"}</p>
    </div>
  );
}

/** The notices for a return from Stripe: never a claim the plan changed before the server said so. */
function ReturnNotice({ returned, status, allowance }: { returned: Returned; status: BillingStatusResponse | null; allowance: AssistantAllowance | null }) {
  const what = returned.kind === "checkout" ? "Checkout" : "Top-up";
  switch (returned.outcome) {
    case "cancel":
      return <Notice tone="info">{what} cancelled. Nothing was charged.</Notice>;
    case "polling":
      return (
        <Notice tone="warning" title={returned.kind === "checkout" ? "Pending activation" : "Top-up pending — confirming the payment"}>
          <p>We are confirming your payment with Stripe. This page updates by itself; nothing is unlocked before the payment is verified.</p>
          <Spinner small label="Confirming" />
        </Notice>
      );
    case "timeout":
      return (
        <Notice tone="warning" title={returned.kind === "checkout" ? "Pending activation" : "Top-up pending"}>
          <p>{STILL_CONFIRMING}</p>
        </Notice>
      );
    case "confirmed":
      return (
        <Notice tone="success" title="Payment verified">
          <p>{returned.kind === "checkout" ? `${planById(status?.plan ?? "free").displayName} is active on your account.` : `Top-up added: ${allowance?.topup.balance ?? status?.topup.balance_requests ?? 0} top-up requests available.`}</p>
        </Notice>
      );
  }
}

/** After a downgrade: which PCs and phones stay enabled, within the plan's limits, until the deadline. */
function SelectionCard({ deadline, onSaved }: { deadline: string; onSaved: () => void }) {
  const [selection, setSelection] = useState<rest.PlanSelectionResponse | null>(null);
  const [pcs, setPcs] = useState<Set<string>>(new Set());
  const [phones, setPhones] = useState<Set<string>>(new Set());
  const [error, setError] = useState<{ code: string; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .planSelection()
      .then((s) => {
        if (cancelled) return;
        setSelection(s);
        setPcs(new Set(s.pcs.filter((p) => p.selected).map((p) => p.id)));
        setPhones(new Set(s.controllers.filter((c) => c.selected).map((c) => c.id)));
      })
      .catch((e: unknown) => {
        if (!cancelled) setError({ code: e instanceof ApiError ? e.code : "INTERNAL", text: billingErrorText(e) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (set: Set<string>, update: (next: Set<string>) => void, id: string, checked: boolean) => {
    const next = new Set(set);
    if (checked) next.add(id);
    else next.delete(id);
    update(next);
    setSaved(false);
  };

  const save = async () => {
    setError(null);
    setSaving(true);
    try {
      const s = await api.putPlanSelection({ pc_ids: [...pcs] as rest.PlanSelectionRequest["pc_ids"], controller_ids: [...phones] as rest.PlanSelectionRequest["controller_ids"] });
      setSelection(s);
      setSaved(true);
      onSaved();
    } catch (e) {
      setError({ code: e instanceof ApiError ? e.code : "INTERNAL", text: billingErrorText(e) });
    } finally {
      setSaving(false);
    }
  };

  const pcLimit = selection?.limits.max_enabled_pcs ?? 0;
  const phoneLimit = selection?.limits.max_controllers ?? 0;
  return (
    <Card>
      <h2 className="font-semibold">Choose which devices stay enabled</h2>
      <p className="mt-1 text-sm text-text-muted">
        Your plan now allows {pcLimit} {pcLimit === 1 ? "PC" : "PCs"} and {phoneLimit} phones. Choose by {longDate(deadline)}; otherwise the most recently used stay enabled. Nothing is deleted.
      </p>
      {!selection && !error ? <Spinner small label="Loading devices" /> : null}
      {selection ? (
        <div className="mt-3 space-y-4">
          <div className="space-y-2">
            <p className="text-caption font-semibold uppercase tracking-[0.12em] text-text-faint">
              PCs · {pcs.size} of {pcLimit}
            </p>
            {selection.pcs.map((pc) => (
              <CheckRow key={pc.id} label={pc.name} hint={`Last seen ${relativeTime(pc.last_seen)}`} checked={pcs.has(pc.id)} disabled={!pcs.has(pc.id) && pcs.size >= pcLimit} onChange={(v) => toggle(pcs, setPcs, pc.id, v)} />
            ))}
          </div>
          <div className="space-y-2">
            <p className="text-caption font-semibold uppercase tracking-[0.12em] text-text-faint">
              Phones · {phones.size} of {phoneLimit}
            </p>
            {selection.controllers.map((c) => (
              <CheckRow key={c.id} label={c.display_name} hint={`Last seen ${relativeTime(c.last_seen)}`} checked={phones.has(c.id)} disabled={!phones.has(c.id) && phones.size >= phoneLimit} onChange={(v) => toggle(phones, setPhones, c.id, v)} />
            ))}
          </div>
          <Button variant="primary" busy={saving} disabled={saving || !selection.open} onClick={() => void save()}>
            Save
          </Button>
          {saved ? <p className="text-sm font-medium text-success">Saved. The PCs you chose are enabled now.</p> : null}
        </div>
      ) : null}
      {error ? (
        <Notice tone="danger">
          <p>{error.text}</p>
          {error.code === "PLAN_SELECTION_CLOSED" ? (
            <Link to="/app/devices" className="tap inline-flex items-center gap-1 font-semibold text-accent">
              Open Devices
            </Link>
          ) : null}
        </Notice>
      ) : null}
    </Card>
  );
}

export function BillingPage() {
  const session = useSessionStore((s) => s.session);
  const assistantStatus = useAssistantStore((s) => s.status);
  const allowance = useAssistantStore((s) => s.allowance);
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState<BillingStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // The query string says only where the customer came back from: read once when the page opens, then dropped from
  // the URL (below), and the server is asked what is true.
  const [returned, setReturned] = useState<Returned | null>(() => readReturn(params));
  const [interval, setInterval_] = useState<rest.BillingInterval>("monthly");
  const [busy, setBusy] = useState<string | null>(null);
  // a failed purchase or portal request: the customer's text, and the payment provider's reason for the test-mode line
  const [actionError, setActionError] = useState<{ text: string; reason: ProviderErrorReason | null } | null>(null);
  const now = new Date();

  const load = useCallback(
    (): Promise<BillingStatusResponse | null> =>
      api
        .billing()
        .then((s) => {
          setStatus(s);
          setLoadError(null);
          setLoading(false);
          return s;
        })
        .catch((e: unknown) => {
          setLoadError(billingErrorText(e));
          setLoading(false);
          return null;
        }),
    [],
  );

  useEffect(() => {
    void load();
    void loadAssistantStatus();
  }, [load]);

  useEffect(() => {
    if (params.has("checkout") || params.has("topup")) setParams(new URLSearchParams(), { replace: true });
  }, [params, setParams]);

  const kind = returned?.kind;
  const outcome = returned?.outcome;
  const baseline = useRef<number | null>(null);
  useEffect(() => {
    if (!kind || outcome !== "polling") return;
    let polls = 0;
    let stopped = false;
    const finish = (result: ReturnOutcome) => {
      stopped = true;
      setReturned({ kind, outcome: result });
    };
    const tick = async () => {
      if (stopped) return;
      polls += 1;
      if (kind === "checkout") {
        await useSessionStore.getState().load();
        const s = await load();
        if (stopped) return;
        if (s && (s.entitlement_state === "active" || s.entitlement_state === "past_due")) return finish("confirmed");
      } else {
        try {
          const st = await api.assistantStatus();
          useAssistantStore.getState().setStatus(st);
          const balance = st.allowance.topup.balance;
          if (baseline.current === null) baseline.current = balance;
          else if (balance > baseline.current) {
            void load();
            return finish("confirmed");
          }
        } catch {
          /* the next poll tries again */
        }
        if (stopped) return;
      }
      if (polls >= RETURN_POLL_LIMIT) finish("timeout");
    };
    const id = setInterval(() => void tick(), RETURN_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [kind, outcome, load]);

  if (!session) return null;

  const plan = status?.plan ?? session.plan;
  const content = planById(plan);
  const copy = billingStateCopy(status ?? { plan: session.plan, entitlement_state: session.entitlement_state ?? (session.plan === "free" ? "free" : "active"), subscription: null });
  const enabled = !!status?.billing_enabled;
  const mode: rest.BillingMode = enabled && status ? status.billing_mode : "off";
  const testMode = mode === "test";
  // test mode only: the payment provider's reason for whoever runs this DoMe service (never in live mode)
  const testLine = actionError ? providerTestModeLine(actionError.reason, mode) : null;
  const sub = status?.subscription ?? null;
  const pro = planById("pro");
  const proPlus = planById("pro_plus");
  const aiOff = assistantStatus?.unavailable_reason === "not_configured";
  // the server lists what can be bought now: Pro Plus is absent while the assistant is off (its only difference)
  const offered: readonly rest.PaidPlanId[] = status?.checkout_plans ?? [];
  const showCheckout = enabled && plan === "free" && !!status?.checkout_available && offered.length > 0;
  const showAi = enabled && !!status && (allowance?.kind === "windows" || status.topup.allowed || status.topup.balance_requests > 0);

  const leave = (url: string) => {
    if (!isCheckoutUrl(url)) throw new ApiError(0, "MALFORMED_MESSAGE", "The server's answer was not understood.", false);
    browserNavigation.assign(url);
  };

  const failed = (e: unknown) => {
    setActionError({ text: billingErrorText(e), reason: providerErrorReason(e) });
    setBusy(null);
  };

  const startCheckout = async (paid: rest.PaidPlanId) => {
    setActionError(null);
    setBusy(`checkout:${paid}`);
    try {
      const r = await api.billingCheckout({ plan: paid, interval });
      leave(r.checkout_url);
    } catch (e) {
      failed(e);
      if (e instanceof ApiError && (e.code === "ALREADY_SUBSCRIBED" || e.code === "CHECKOUT_PROCESSING" || e.code === "PLAN_NOT_AVAILABLE")) void load();
    }
  };

  const openPortal = async () => {
    setActionError(null);
    setBusy("portal");
    try {
      const r = await api.billingPortal();
      leave(r.portal_url);
    } catch (e) {
      failed(e);
    }
  };

  const buyTopup = async () => {
    setActionError(null);
    setBusy("topup");
    try {
      const r = await api.billingTopup();
      leave(r.checkout_url);
    } catch (e) {
      failed(e);
    }
  };

  /** A plan a Free customer can buy: its price for the chosen interval, its allowances, one button. */
  const planOffer = (offer: PlanContent) => {
    const id = offer.id as rest.PaidPlanId;
    const price = status?.pricing.plans[id];
    const monthly = price ? price.monthly_cents : offer.price?.monthlyCents ?? 0;
    const annual = price ? price.annual_cents : offer.price?.annualCents ?? 0;
    return (
      <Card key={offer.id} as="article" aria-labelledby={`offer-${offer.id}`}>
        <div className="flex items-baseline justify-between gap-3">
          <h3 id={`offer-${offer.id}`} className="text-lg font-semibold">
            {offer.displayName}
          </h3>
          <p className="text-right">
            <span className="text-xl font-bold tabular-nums">{usd(interval === "monthly" ? monthly : annual)}</span>
            <span className="text-sm text-text-muted">{interval === "monthly" ? "\u00a0/\u00a0month" : "\u00a0/\u00a0year"}</span>
          </p>
        </div>
        <p className="mt-1 text-sm text-text-muted">{offer.tagline}</p>
        <ul className="mt-3 space-y-1 text-sm">
          <li>
            Up to {offer.pcs} PCs and {offer.phones} phones
          </li>
          <li>
            AI butler: {aiSummary(offer.ai)}
            {aiOff ? (
              <>
                {" "}
                <Pill tone="neutral" dot={false}>
                  {NOT_SWITCHED_ON}
                </Pill>
              </>
            ) : null}
          </li>
        </ul>
        <Button className="mt-4" variant="primary" full busy={busy === `checkout:${id}`} disabled={busy !== null} onClick={() => void startCheckout(id)}>
          Continue to payment
        </Button>
      </Card>
    );
  };

  return (
    <div className="space-y-4">
      <PageHeader title="Billing" />
      {returned ? <ReturnNotice returned={returned} status={status} allowance={allowance} /> : null}
      <Card>
        <div className="flex items-center gap-3.5">
          <IconWell icon="card" size={44} radius={14} />
          <div className="min-w-0 flex-1">
            <p className="text-caption font-semibold uppercase tracking-[0.12em] text-text-faint">Current plan</p>
            <p className="text-[1.375rem] font-bold leading-tight tracking-[-0.02em]">{content.displayName}</p>
          </div>
          <Pill tone={copy.tone}>{copy.label}</Pill>
        </div>
        {testMode ? (
          <Pill tone="warning" className="mt-3">
            {TEST_MODE}
          </Pill>
        ) : null}
        <p className="mt-3 text-sm text-text-muted">{copy.text}</p>
        {sub && sub.current_period_end && (sub.state === "active" || sub.state === "trialing" || sub.state === "cancel_scheduled") ? (
          <p className="mt-2 text-sm text-text-muted" data-testid="period-end">
            {sub.cancel_at_period_end || sub.state === "cancel_scheduled" ? "Ends" : "Renews"} {longDate(sub.current_period_end)}
            {sub.interval ? ` · billed ${sub.interval === "annual" ? "yearly" : "monthly"}` : ""}
          </p>
        ) : null}
        {sub && sub.state === "past_due" && sub.grace_until ? (
          <p className="mt-2 text-sm text-danger" data-testid="grace-until">
            Paid features stay until {longDate(sub.grace_until)}.
          </p>
        ) : null}
        {copy.label === "Payment problem" ? (
          <div className="mt-2 text-sm text-text-muted">
            <Steps steps={recoverySteps("BILLING_PAST_DUE")} />
          </div>
        ) : null}
        {enabled && status?.portal_available ? (
          <div className="mt-4 space-y-2">
            <Button variant="secondary" full busy={busy === "portal"} disabled={busy !== null} onClick={() => void openPortal()}>
              Manage subscription
            </Button>
            <p className="text-xs text-text-faint">Change plan, switch between monthly and yearly, update your card or cancel, on Stripe's secure page.</p>
          </div>
        ) : null}
        {loading ? <Spinner small label="Checking billing" /> : null}
      </Card>
      {actionError ? (
        <Notice tone="danger">
          <p>{actionError.text}</p>
          {testLine ? (
            <p className="text-xs" data-testid="provider-test-line">
              {testLine}
            </p>
          ) : null}
        </Notice>
      ) : null}
      <Card>
        <h2 className="font-semibold">Your limits</h2>
        <KeyValue
          className="mt-1"
          rows={[
            { label: "Enabled PCs", value: String(session.limits.max_enabled_pcs) },
            { label: "Paired phones", value: String(session.limits.max_controllers) },
            { label: "Routines", value: session.limits.routines ? "Included" : "Not included" },
            { label: "Custom layouts", value: session.limits.custom_layouts ? "Included" : "Not included" },
          ]}
        />
      </Card>
      {status?.selection?.open ? <SelectionCard deadline={status.selection.deadline} onSaved={() => void useSessionStore.getState().load()} /> : null}
      {status?.selection && !status.selection.open ? <p className="px-1 text-sm text-text-muted">The default (most recently used) selection applies; change it under Devices.</p> : null}
      {showCheckout ? (
        <section aria-labelledby="choose-plan" className="space-y-3">
          <div className="flex items-center justify-between gap-3 px-1">
            <h2 id="choose-plan" className="font-semibold">
              Choose a plan
            </h2>
            {testMode ? <Pill tone="warning">{TEST_MODE}</Pill> : null}
          </div>
          <Segmented<rest.BillingInterval>
            label="Billing interval"
            value={interval}
            onChange={setInterval_}
            options={[
              { value: "monthly", label: "Monthly" },
              { value: "annual", label: "Yearly" },
            ]}
          />
          {offered.includes("pro") ? planOffer(pro) : null}
          {offered.includes("pro_plus") ? planOffer(proPlus) : null}
          {offered.includes("pro_plus") ? null : (
            <p className="px-1 text-sm text-text-muted" data-testid="pro-plus-not-sold">
              {PRO_PLUS_NOT_SOLD}
            </p>
          )}
          <p className="px-1 text-xs text-text-faint">{STRIPE_NOTE}</p>
        </section>
      ) : null}
      {showAi && status ? (
        <Card>
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-semibold">AI requests</h2>
            {aiOff ? (
              <Pill tone="neutral" dot={false}>
                {NOT_SWITCHED_ON}
              </Pill>
            ) : null}
          </div>
          {allowance ? <p className="mt-1 text-sm text-text-muted">{allowanceSummary(allowance, now)}</p> : null}
          {allowance?.kind === "windows" && allowance.window && allowance.week ? (
            <div className="mt-3 space-y-3">
              <AllowanceBar label="This 5-hour window" bucket={allowance.window} now={now} />
              <AllowanceBar label="This week" bucket={allowance.week} now={now} />
            </div>
          ) : null}
          {status.topup.balance_requests > 0 ? (
            <p className="mt-3 text-sm" data-testid="topup-balance">
              {plan === "free" ? `Your ${status.topup.balance_requests} top-up requests are kept and usable again on Pro or Pro Plus.` : `${status.topup.balance_requests} top-up requests available (used after your window or week runs out).`}
            </p>
          ) : null}
          {status.topup.allowed ? (
            <div className="mt-4 space-y-2">
              {testMode ? <Pill tone="warning">{TEST_MODE}</Pill> : null}
              <Button variant="secondary" full busy={busy === "topup"} disabled={busy !== null} onClick={() => void buyTopup()}>
                {topupLabel(status.topup)}
              </Button>
              <p className="text-xs text-text-faint">You choose when; nothing is added or charged automatically. {STRIPE_NOTE}</p>
            </div>
          ) : null}
        </Card>
      ) : null}
      {!enabled && !loading ? (
        <>
          {loadError ? (
            <Notice tone="neutral">
              <p>Billing details could not be loaded right now. {loadError}</p>
            </Notice>
          ) : null}
          <Notice tone="info" title="Upgrading is not available yet">
            <p>
              {pro.name} ({priceLine(pro)}) and {proPlus.name} ({priceLine(proPlus)}) are {COMING_AT_PAID_LAUNCH.toLowerCase()}. Nothing can be bought or charged from this app today.
            </p>
            <Link to="/pricing" className="tap mt-1 inline-flex items-center gap-1 font-semibold text-accent">
              Compare plans
            </Link>
          </Notice>
        </>
      ) : null}
      <p className="px-1 text-xs text-text-faint">Security controls — revoking phones, unlinking PCs, emergency stop on the PC — are included on every plan and are never paywalled.</p>
    </div>
  );
}

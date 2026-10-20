/**
 * Pricing: Free (available now), Pro and Pro Plus (coming at paid launch) from src/content/plans.ts, the
 * plain-words rules of the AI butler allowance, a left-aligned comparison table (a stacked list on phones)
 * and "Good to know" as quiet prose. Honest by design: the paid plans carry a status line, never a button
 * that looks tappable, until the server says checkout exists (GET /v1/plans `billing_enabled`, asked once,
 * failures ignored); then they say "Available now" with a link to sign in and subscribe from Billing, and
 * "Test mode" while Stripe runs in test mode. Screen view, custom layouts and routines are marked "Coming" wherever
 * they appear. Every AI allowance says "Not switched on yet" unless the server says the assistant can be used
 * (`ai_enabled`), and Pro Plus, whose only difference from Pro is AI requests, is not offered while it cannot.
 */
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router";

import { PLANS } from "@dome/protocol";

import { Icon } from "../../components/icons.tsx";
import { AI_OFF_STATUS, AI_RULES, AVAILABLE_NOW, PLAN_CONTENT, SCREEN_VIEW_STATUS, usd, type PlanContent } from "../../content/plans.ts";
import { INCLUDED, NOT_INCLUDED, pricingGroups, type Row, type RowGroup } from "../../lib/pricing.ts";
import { aiSwitchedOn, sellsPlan, useBillingAvailability, type BillingAvailability } from "../../lib/useBillingAvailability.ts";
import { PublicIcon, type PublicIconName } from "./publicIcons.tsx";

/** Shown on Pro Plus while checkout exists but the assistant is off: its AI requests are its only difference. */
export const PRO_PLUS_NEEDS_AI = "On sale once the AI butler is switched on";

const RULES: Array<{ icon: PublicIconName; title: string; text: string }> = [
  { icon: "chat", title: "One request is one thing you ask the AI", text: AI_RULES.request },
  { icon: "hand", title: "Everyday commands are free and never count", text: AI_RULES.everyday },
  { icon: "timer", title: "Two windows, refreshed on a clear schedule", text: AI_RULES.windows },
  { icon: "wallet", title: "A top-up only when you ask for one", text: AI_RULES.topUp },
];

/** "Available now" on Free; "Coming" on Screen view. Never a button. */
function Chip({ children, tone = "accent" }: { children: ReactNode; tone?: "accent" | "live" | "quiet" }) {
  return (
    <span className={`dome-chip ${tone === "live" ? "dome-chip-live" : tone === "quiet" ? "dome-chip-quiet" : ""}`}>
      {tone === "live" ? <span aria-hidden="true" className="dome-chip-dot" /> : null}
      {children}
    </span>
  );
}

/** A comparison cell: "Included" is a check mark (the word stays for screen readers), "—" a dash. */
function Cell({ value }: { value: string }) {
  if (value === INCLUDED) {
    return (
      <span className="inline-flex items-center text-success">
        <Icon name="check" size={20} strokeWidth={2.25} />
        <span className="sr-only">Included</span>
      </span>
    );
  }
  if (value === NOT_INCLUDED) {
    return (
      <span className="text-text-faint">
        <span aria-hidden="true">—</span>
        <span className="sr-only">Not included</span>
      </span>
    );
  }
  return <span className="text-[13px] leading-snug sm:text-[15px]">{value}</span>;
}

/** A phone-sized value: "Up to 5" → "5", "10 min per 5-hour window" → "10 min / 5 h", "10, once (does not renew)" → "10, once". */
function phoneValue(v: string): string {
  return v
    .replace(/^Up to /, "")
    .replace(/ min per (\d+)-hour window$/, " min per\u00a0$1\u00a0h")
    .replace(/ \(does not renew\)$/, "");
}

const PLAN_NAMES = ["Free", "Pro", "Pro Plus"] as const;
const isSame = (r: Row) => r.free === r.pro && r.pro === r.proPlus;

/** Phone labels keep "5-hour" together (a non-breaking hyphen), so a label never breaks as "5-" / "hour". */
const keepTogether = (label: string) => label.replace(/(\d)-hour/g, "$1\u2011hour");

/** One feature on a phone: its label, then the three plans' values under the sticky Free · Pro · Pro Plus header. */
function PhoneRow({ label, row }: { label: ReactNode; row?: Row }) {
  const text = typeof label === "string" ? keepTogether(label) : label;
  if (row && !isSame(row)) {
    return (
      <li className="dome-compare-row">
        <p className="text-[15px] leading-snug text-text-muted">{text}</p>
        {([row.free, row.pro, row.proPlus] as const).map((v, i) => (
          <span key={PLAN_NAMES[i]} className="dome-compare-value">
            <span className="sr-only">{PLAN_NAMES[i]}: </span>
            <Cell value={phoneValue(v)} />
          </span>
        ))}
      </li>
    );
  }
  // the same on every plan: the label gets the width, one value on the right says so
  const value = row && row.free !== INCLUDED ? row.free : null;
  return (
    <li className="dome-compare-row-all">
      <p className="text-[15px] leading-snug text-text-muted">{text}</p>
      <span className="flex flex-col items-end text-right">
        {value ? (
          <span className="text-[13px] font-medium leading-snug text-text">{value}</span>
        ) : (
          <span className="inline-flex items-center text-success">
            <Icon name="check" size={20} strokeWidth={2.25} />
          </span>
        )}
        <span className="text-[12px] text-text-muted">Every plan</span>
      </span>
    </li>
  );
}

/** A group on phones: the rows every plan includes become one line (the group's summary), the rest one row each. */
function PhoneGroup({ group }: { group: RowGroup }) {
  const included = group.rows.filter((r) => isSame(r) && r.free === INCLUDED);
  const rest = group.rows.filter((r) => !(isSame(r) && r.free === INCLUDED));
  return (
    <section aria-label={group.title} className="pt-6 first:pt-4">
      <h3 className="flex items-center gap-2 text-caption font-semibold uppercase tracking-[0.12em] text-text-faint">
        {group.title}
        {group.status ? (
          <span className="normal-case tracking-normal">
            <Chip tone="quiet">{group.status}</Chip>
          </span>
        ) : null}
      </h3>
      <ul className="dome-hairlines mt-1">
        {included.length > 0 ? <PhoneRow label={group.summary ?? included.map((r) => r.label).join("; ")} /> : null}
        {rest.map((r) => (
          <PhoneRow key={r.label} label={r.label} row={r} />
        ))}
      </ul>
    </section>
  );
}

function PlanCard({ plan, checkout }: { plan: PlanContent; checkout: BillingAvailability | null }) {
  const available = plan.status.kind === "available";
  // a paid plan the server sells now: the same "Available now" chip as Free, and a link into the app to subscribe
  const purchasable = !available && plan.id !== "free" && sellsPlan(checkout, plan.id);
  const aiOn = aiSwitchedOn(checkout);
  const statusLine = !available && !purchasable && checkout?.selling ? PRO_PLUS_NEEDS_AI : plan.status.label;
  return (
    <section aria-labelledby={`plan-${plan.id}`} className={`dome-card relative flex flex-col p-6 lg:p-7 ${available ? "ring-1 ring-primary/35 shadow-[var(--dome-shadow-raised)]" : ""}`}>
      <div className="flex items-center justify-between gap-3">
        <h2 id={`plan-${plan.id}`} className="text-title font-semibold">
          {plan.name}
        </h2>
        {available || purchasable ? (
          <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
            <Chip tone="live">{available ? plan.status.label : AVAILABLE_NOW}</Chip>
            {purchasable && checkout?.mode === "test" ? <Chip tone="quiet">Test mode</Chip> : null}
          </span>
        ) : (
          <Chip tone="quiet">Paid plan</Chip>
        )}
      </div>
      <p className="mt-5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-display text-[2.75rem] font-bold leading-none tracking-[-0.04em] md:text-[2.5rem] lg:text-5xl">{plan.price ? usd(plan.price.monthlyCents) : usd(0)}</span>
        {plan.price ? <span className="whitespace-nowrap text-[15px] text-text-muted">/ month</span> : null}
      </p>
      <p className="mt-2 text-sm text-text-muted">{plan.price ? `or ${usd(plan.price.annualCents)} / year` : "No credit card. No ads."}</p>
      <p className="mt-4 text-[15px] text-text-muted">{plan.tagline}</p>
      <ul className="mt-6 space-y-3 border-t border-border pt-6 text-[15px]">
        {plan.highlights.map((h) => (
          <li key={h.text} className="flex gap-3">
            <span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-success/12 text-success">
              <Icon name="check" size={12} strokeWidth={2.75} />
            </span>
            <span>
              {h.text}
              {h.status ?? (h.ai && !aiOn ? AI_OFF_STATUS : null) ? (
                <>
                  {" "}
                  <Chip tone="quiet">{h.status ?? AI_OFF_STATUS}</Chip>
                </>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-auto pt-8">
        {available ? (
          <Link to="/app" className="tap dome-primary dome-press inline-flex min-h-[52px] w-full items-center justify-center rounded-full font-semibold">
            Start free
          </Link>
        ) : purchasable ? (
          <Link to="/app/billing" data-testid={`subscribe-${plan.id}`} className="tap dome-primary dome-press inline-flex min-h-[52px] w-full items-center justify-center rounded-full font-semibold">
            Sign in to subscribe
          </Link>
        ) : (
          <p className="dome-status" data-testid={`status-${plan.id}`}>
            <PublicIcon name="clock" size={18} />
            {statusLine}
          </p>
        )}
      </div>
    </section>
  );
}

export function PricingPage() {
  const checkout = useBillingAvailability();
  const aiOn = aiSwitchedOn(checkout);
  const groups = pricingGroups({ aiOn });
  const free = PLANS.plans.free;
  const names = ["Free", "Pro", "Pro Plus"] as const;
  const selling = !!checkout?.selling;
  return (
    <div className="mx-auto max-w-6xl px-5 lg:px-8">
      <header className="animate-rise max-w-3xl pb-10 pt-14 sm:pb-14 sm:pt-20">
        <p className="dome-eyebrow">Pricing</p>
        <h1 className="mt-4 text-h1 font-bold">Free for everyday control.</h1>
        <p className="mt-5 max-w-2xl text-lg text-text-muted sm:text-xl">The remote is free: no credit card, no ads. Pro and Pro Plus add more PCs and a real AI butler allowance{selling ? "" : " at paid launch"}, and unlimited Screen view once it ships.</p>
      </header>

      <div className="grid gap-4 md:grid-cols-3 lg:gap-5">
        {PLAN_CONTENT.map((plan) => (
          <PlanCard key={plan.id} plan={plan} checkout={checkout} />
        ))}
      </div>
      <p className="mt-5 text-sm text-text-faint">{selling ? `Prices in US dollars, tax included. Subscribe from Billing in the app; payment runs on Stripe's secure page.${checkout?.mode === "test" ? " Test mode: no real charges." : ""}` : "Prices in US dollars, tax included. Pro and Pro Plus arrive at paid launch; nothing can be bought in this version."}</p>

      <section aria-labelledby="ai-rules" className="py-[var(--dome-section)]">
        <div className="grid gap-10 lg:grid-cols-[1fr_1.55fr] lg:gap-16">
          <div>
            <p className="dome-eyebrow">AI butler</p>
            <h2 id="ai-rules" className="mt-3 text-h2 font-bold">
              How AI requests work.
            </h2>
            <p className="mt-4 max-w-md text-lg text-text-muted">The butler is optional and off until you turn it on. {AI_RULES.taste}</p>
            {aiOn ? null : (
              <p className="mt-3 inline-flex flex-wrap items-center gap-2 text-[15px] text-text-muted" data-testid="ai-off">
                <Chip tone="quiet">{AI_OFF_STATUS}</Chip>
                <span>The AI butler is not switched on for DoMe accounts yet; these allowances describe the plans.</span>
              </p>
            )}
            <p className="mt-4 max-w-md text-[15px] text-text-muted">{AI_RULES.privacy}</p>
          </div>
          <ul className="grid gap-4 sm:grid-cols-2">
            {RULES.map((r) => (
              <li key={r.title} className="dome-card flex gap-4 p-5 sm:block sm:p-6">
                <span className="dome-well">
                  <PublicIcon name={r.icon} size={22} />
                </span>
                <div className="min-w-0">
                  <h3 className="text-[17px] font-semibold tracking-[-0.01em] sm:mt-5">{r.title}</h3>
                  <p className="mt-1.5 text-[15px] text-text-muted">{r.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section aria-labelledby="compare" className="pb-[var(--dome-section)]">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h2 id="compare" className="text-h2 font-bold">
            Compare plans
          </h2>
          <p className="inline-flex flex-wrap items-center gap-2 text-sm text-text-faint">
            <Chip tone="quiet">{SCREEN_VIEW_STATUS}</Chip>
            <span>{selling ? "Screen view is in development." : "Screen view · Pro and Pro Plus arrive at paid launch."}</span>
          </p>
        </div>

        {/* Tablets and up: a real table, left-aligned. */}
        <table className="dome-compare mt-8 hidden sm:table">
          <caption className="sr-only">Feature comparison between DoMe Free, Pro and Pro Plus</caption>
          <thead>
            <tr>
              <th scope="col" className="w-[46%]">
                Feature
              </th>
              {names.map((n) => (
                <th key={n} scope="col" className="dome-compare-plan w-[18%]">
                  {n}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <Fragment key={g.title}>
                <tr className="dome-compare-group">
                  <th scope="colgroup" colSpan={4}>
                    <span className="inline-flex items-center gap-2">
                      {g.title}
                      {g.status ? (
                        <span className="normal-case tracking-normal">
                          <Chip tone="quiet">{g.status}</Chip>
                        </span>
                      ) : null}
                    </span>
                  </th>
                </tr>
                {g.rows.map((r) => (
                  <tr key={r.label}>
                    <th scope="row">{r.label}</th>
                    <td>
                      <Cell value={r.free} />
                    </td>
                    <td>
                      <Cell value={r.pro} />
                    </td>
                    <td>
                      <Cell value={r.proPlus} />
                    </td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>

        {/* Phones: one sticky Free · Pro · Pro Plus header and one compact row per feature; what every plan includes is
            a single line per group. (The table above is display:none here, so nothing is read twice.) */}
        <div className="dome-compare-list mt-4 sm:hidden">
          <div className="dome-compare-head" aria-hidden="true">
            <span />
            {names.map((n) => (
              <span key={n}>{n}</span>
            ))}
          </div>
          {groups.map((g) => (
            <PhoneGroup key={g.title} group={g} />
          ))}
        </div>
      </section>

      <section aria-labelledby="good-to-know" className="grid gap-6 pb-[var(--dome-section)] lg:grid-cols-12">
        <h2 id="good-to-know" className="text-title font-semibold lg:col-span-4">
          Good to know
        </h2>
        <ul className="dome-hairlines text-[15px] text-text-muted lg:col-span-8">
          <li className="pb-4">{selling ? "You see the exact price on Stripe’s checkout page before confirming anything; nothing is charged until you do." : "Prices are planned, not final, and may change before Pro and Pro Plus can be purchased. Nothing can be bought in this version."}</li>
          <li className="py-4">{AI_RULES.billing} You can cancel at any time; access continues until the end of the paid period and your settings are kept.</li>
          <li className="py-4">
            If your Pro or Pro Plus plan ends, you keep every PC, phone and setting and choose which {free.max_enabled_pcs} PC and {free.max_controllers} phones stay enabled (within {PLANS.downgrade_policy.selection_window_days} days; otherwise the most recently used are kept). Revocation and emergency stop always work.
          </li>
          <li className="py-4">The AI butler is optional and off until you turn it on. {AI_RULES.privacy}</li>
          <li className="py-4">Screen view is in development. The allowances above describe the plans; the feature is not in the app yet, and nothing is recorded when it arrives.</li>
          <li className="pt-4">An account is one person’s account, not a shared household login. Guest access is a later feature.</li>
        </ul>
        <p className="text-[15px] text-text-muted lg:col-span-8 lg:col-start-5">
          More questions?{" "}
          <Link to="/faq" className="font-semibold text-accent underline-offset-4 hover:underline">
            Read the FAQ
          </Link>
        </p>
      </section>
    </div>
  );
}


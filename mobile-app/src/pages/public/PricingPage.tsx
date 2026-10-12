/** Free/Pro comparison generated from plans.json; prices are shown as planned, not live. */
import type { ReactNode } from "react";
import { Link } from "react-router";

import { PLANS } from "@dome/protocol";

import { Icon } from "../../components/icons.tsx";
import { money } from "../../lib/format.ts";
import { pricingRows } from "../../lib/pricing.ts";

const INCLUDED = "Included";
const NOT_INCLUDED = "—";

/** A comparison cell: "Included" becomes a check mark (the word stays for screen readers), "—" a dash. */
function Cell({ value }: { value: string }) {
  if (value === INCLUDED) {
    return (
      <span className="inline-flex items-center justify-center text-success">
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
  return <span className="text-[13px] font-medium leading-tight sm:text-sm">{value}</span>;
}

function Feature({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-success/12 text-success">
        <Icon name="check" size={12} strokeWidth={2.75} />
      </span>
      <span>{children}</span>
    </li>
  );
}

export function PricingPage() {
  const rows = pricingRows();
  const p = PLANS.pricing_defaults;
  const free = PLANS.plans.free;
  const pro = PLANS.plans.pro;
  return (
    <div className="mx-auto max-w-6xl px-5 lg:px-8">
      <header className="animate-rise mx-auto max-w-2xl pb-12 pt-14 text-center sm:pt-20 lg:pb-16">
        <p className="dome-eyebrow">Pricing</p>
        <h1 className="mt-4 text-h1 font-bold">Free at heart.</h1>
        <p className="mt-5 text-lg text-text-muted sm:text-xl">The core remote is free for as long as you like. Pro is for people who want more PCs and convenience features.</p>
      </header>

      <div className="mx-auto grid max-w-4xl gap-5 md:grid-cols-2">
        <section aria-labelledby="plan-free" className="dome-card flex flex-col p-7 lg:p-9">
          <h2 id="plan-free" className="text-lg font-semibold">
            {free.display_name}
          </h2>
          <p className="mt-5 flex items-baseline gap-2">
            <span className="font-display text-5xl font-bold tracking-[-0.04em]">{money(0, p.currency).replace(/[.,]00$/, "")}</span>
            <span className="text-text-muted">forever</span>
          </p>
          <p className="mt-3 text-[15px] text-text-muted">No credit card. No ads. No daily paywall on manual controls.</p>
          <ul className="mt-7 space-y-3 text-[15px]">
            <Feature>
              {free.max_enabled_pcs} PC and up to {free.max_controllers} phones
            </Feature>
            <Feature>YouTube, Windows media and volume</Feature>
            <Feature>Approved apps, lock and confirmed power</Feature>
            <Feature>Touchpad, keyboard and typed commands</Feature>
            <Feature>Secure access away from home</Feature>
          </ul>
          <div className="mt-auto pt-8">
            <Link to="/app" className="tap dome-primary dome-press inline-flex w-full items-center justify-center rounded-full font-semibold min-h-[52px]">
              Start free
            </Link>
          </div>
        </section>

        <section aria-labelledby="plan-pro" className="dome-dark relative flex flex-col overflow-hidden rounded-card bg-navy p-7 shadow-[0_40px_80px_-40px_rgb(0_20_65/0.6)] ring-1 ring-white/10 lg:p-9">
          <div aria-hidden="true" className="absolute inset-0 bg-[radial-gradient(30rem_16rem_at_100%_0%,rgb(1_66_251/0.55),transparent_70%)]" />
          <div className="relative flex flex-1 flex-col">
            <div className="flex items-center justify-between gap-3">
              <h2 id="plan-pro" className="text-lg font-semibold">
                {pro.display_name}
              </h2>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/10 px-2.5 py-1 text-xs font-semibold">
                <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-coral" />
                Planned
              </span>
            </div>
            <p className="mt-5 flex items-baseline gap-2">
              <span className="font-display text-5xl font-bold tracking-[-0.04em]">{money(p.monthly_cents, p.currency)}</span>
              <span className="text-text-muted">/ month</span>
            </p>
            <p className="mt-3 text-[15px] text-text-muted">
              or {money(p.annual_cents, p.currency)} / year. <strong className="text-text">Planned pricing</strong>: not yet available to buy.
            </p>
            <ul className="mt-7 space-y-3 text-[15px]">
              <Feature>Everything in Free</Feature>
              <Feature>
                Up to {pro.max_enabled_pcs} PCs and {pro.max_controllers} phones
              </Feature>
              <Feature>Custom remote layouts, at paid launch</Feature>
              <Feature>Saved routines of up to {pro.routine_max_steps} steps, at paid launch</Feature>
            </ul>
            <div className="mt-auto pt-8">
              <button type="button" disabled className="w-full cursor-not-allowed rounded-full border border-white/15 py-3.5 font-semibold text-text-muted min-h-[52px]">
                Coming at paid launch
              </button>
            </div>
          </div>
        </section>
      </div>

      <section aria-labelledby="compare" className="mx-auto max-w-4xl py-[var(--dome-section)]">
        <h2 id="compare" className="text-center text-h2 font-bold">
          Compare plans
        </h2>
        <div className="dome-card mt-10 overflow-hidden p-0">
          <table className="w-full text-[15px]">
            <caption className="sr-only">Feature comparison between DoMe Free and DoMe Pro</caption>
            <thead>
              <tr className="border-b border-border">
                <th scope="col" className="px-4 py-3.5 text-left text-caption font-semibold uppercase tracking-[0.12em] text-text-faint sm:px-6">
                  Feature
                </th>
                <th scope="col" className="w-[23%] px-2 py-3.5 text-center font-semibold sm:w-[18%] sm:px-4">
                  Free
                </th>
                <th scope="col" className="w-[23%] px-2 py-3.5 text-center font-semibold sm:w-[18%] sm:px-4">
                  Pro
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label} className="border-t border-border first:border-t-0">
                  <th scope="row" className="px-4 py-3.5 text-left text-[14px] font-normal text-text-muted sm:px-6 sm:text-[15px]">
                    {r.label}
                  </th>
                  <td className="px-2 py-3.5 text-center sm:px-4">
                    <Cell value={r.free} />
                  </td>
                  <td className="px-2 py-3.5 text-center sm:px-4">
                    <Cell value={r.pro} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="good-to-know" className="mx-auto max-w-4xl pb-[var(--dome-section)]">
        <h2 id="good-to-know" className="text-title font-semibold">
          Good to know
        </h2>
        <ul className="mt-5 grid gap-4 text-[15px] text-text-muted sm:grid-cols-2">
          <li className="dome-card p-5">Prices are design assumptions for a launch experiment and may change before Pro can be purchased. Nothing can be bought in this version.</li>
          <li className="dome-card p-5">
            If Pro ends, you keep every PC, phone and setting and choose which {free.max_enabled_pcs} PC and {free.max_controllers} phones stay enabled (within {PLANS.downgrade_policy.selection_window_days} days; otherwise the most recently used are kept). Revocation and emergency stop always work.
          </li>
          <li className="dome-card p-5">Optional AI interpretation and voice transcription are not part of either plan today; they would be announced separately with their own costs.</li>
          <li className="dome-card p-5">An account is one person’s account, not a shared household login. Guest access is a later feature.</li>
        </ul>
        <p className="mt-8 text-center text-[15px] text-text-muted">
          More questions?{" "}
          <Link to="/faq" className="font-semibold text-accent underline-offset-4 hover:underline">
            Read the FAQ
          </Link>
        </p>
      </section>
    </div>
  );
}

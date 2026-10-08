/** Free/Pro comparison generated from plans.json; prices are shown as planned, not live. */
import { Link } from "react-router";

import { PLANS } from "@dome/protocol";

import { money } from "../../lib/format.ts";
import { pricingRows } from "../../lib/pricing.ts";

export function PricingPage() {
  const rows = pricingRows();
  const p = PLANS.pricing_defaults;
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Pricing</h1>
        <p className="text-text-muted mt-2">The core remote is free for as long as you like. Pro is for people who want more PCs and convenience features.</p>
      </header>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-card border border-border p-5">
          <h2 className="text-xl font-bold">{PLANS.plans.free.display_name}</h2>
          <p className="text-3xl font-bold mt-2">{money(0, p.currency)}</p>
          <p className="text-sm text-text-muted">No credit card. No ads. No daily paywall on manual controls.</p>
          <Link to="/app" className="tap mt-4 inline-flex w-full items-center justify-center rounded-control bg-accent text-on-accent font-semibold">
            Start free
          </Link>
        </div>
        <div className="rounded-card border border-border p-5">
          <h2 className="text-xl font-bold">{PLANS.plans.pro.display_name}</h2>
          <p className="text-3xl font-bold mt-2">
            {money(p.monthly_cents, p.currency)}
            <span className="text-base font-medium text-text-muted"> / month</span>
          </p>
          <p className="text-sm text-text-muted">
            or {money(p.annual_cents, p.currency)} / year. <strong>Planned pricing</strong> — not yet available to buy.
          </p>
          <button type="button" disabled className="mt-4 w-full rounded-control border border-border py-3 font-semibold text-text-faint cursor-not-allowed">
            Coming at paid launch
          </button>
        </div>
      </div>
      <div className="overflow-x-auto rounded-card border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">Feature comparison between DoMe Free and DoMe Pro</caption>
          <thead className="bg-bg-elevated">
            <tr>
              <th scope="col" className="text-left p-3 font-semibold">
                Feature
              </th>
              <th scope="col" className="text-left p-3 font-semibold">
                Free
              </th>
              <th scope="col" className="text-left p-3 font-semibold">
                Pro
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-t border-border">
                <th scope="row" className="text-left p-3 font-normal">
                  {r.label}
                </th>
                <td className="p-3">{r.free}</td>
                <td className="p-3">{r.pro}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <section className="text-sm text-text-muted space-y-2">
        <h2 className="font-semibold text-text">Good to know</h2>
        <ul className="list-disc pl-5 space-y-1">
          <li>Prices are design assumptions for a launch experiment and may change before Pro can be purchased. Nothing can be bought in this version.</li>
          <li>If Pro ends, you keep every PC, phone and setting and choose which {PLANS.plans.free.max_enabled_pcs} PC and {PLANS.plans.free.max_controllers} phones stay enabled (within {PLANS.downgrade_policy.selection_window_days} days; otherwise the most recently used are kept). Revocation and emergency stop always work.</li>
          <li>Optional AI interpretation and voice transcription are not part of either plan today; they would be announced separately with their own costs.</li>
          <li>An account is one person’s account, not a shared household login. Guest access is a later feature.</li>
        </ul>
      </section>
    </div>
  );
}

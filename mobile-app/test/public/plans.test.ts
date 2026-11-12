/**
 * The website's plan content (src/content/plans.ts) states the owner's decisions exactly: prices, device
 * counts, Screen view and AI butler allowances, the top-up, the status labels, and the plain-words rules.
 */
import { describe, expect, it } from "vitest";

import { PLANS } from "@dome/protocol";

import { aboutTime, AGREE_TO_TERMS, AI_OFF_STATUS, AI_RULES, aiSummary, AVAILABLE_NOW, COMING_AT_PAID_LAUNCH, FREE_SCREEN_VIEW_MINUTES, FREE_TASTE_REQUESTS, HOW_TO_CANCEL, PAID_SCREEN_VIEW, PLAN_CONTENT, planById, LAYOUTS_STATUS, POINT_AND_TALK_STATUS, renewalLine, SCREEN_VIEW_FAIR_USE, SCREEN_VIEW_STATUS, TAX_INCLUDED, TOP_UP, topUpTerms, usd, WARN_AT_PERCENT, WEEK_DAYS, WINDOW_HOURS } from "../../src/content/plans.ts";
import { COMING, INCLUDED, NOT_INCLUDED, pricingGroups, pricingRows } from "../../src/lib/pricing.ts";

describe("plan content", () => {
  it("lists Free, Pro and Pro Plus in that order with the decided prices", () => {
    expect(PLAN_CONTENT.map((p) => p.name)).toEqual(["Free", "Pro", "Pro Plus"]);
    expect(planById("free").price).toBeNull();
    expect(planById("pro").price).toEqual({ monthlyCents: 999, annualCents: 9999 });
    expect(planById("pro_plus").price).toEqual({ monthlyCents: 2499, annualCents: 24999 });
  });

  it("formats US dollars without a pointless .00: $0, $9.99, $99.99, $24.99, $249.99, $2.99", () => {
    expect(usd(0)).toBe("$0");
    expect(usd(999)).toBe("$9.99");
    expect(usd(9999)).toBe("$99.99");
    expect(usd(2499)).toBe("$24.99");
    expect(usd(24999)).toBe("$249.99");
    expect(usd(TOP_UP.priceCents)).toBe("$2.99");
    expect(usd(1000)).toBe("$10");
    expect(usd(105)).toBe("$1.05");
  });

  it("only Free is available; Pro and Pro Plus are coming at paid launch", () => {
    expect(planById("free").status).toEqual({ kind: "available", label: AVAILABLE_NOW });
    expect(planById("pro").status).toEqual({ kind: "coming", label: COMING_AT_PAID_LAUNCH });
    expect(planById("pro_plus").status).toEqual({ kind: "coming", label: COMING_AT_PAID_LAUNCH });
    expect(AVAILABLE_NOW).toBe("Available now");
    expect(COMING_AT_PAID_LAUNCH).toBe("Coming at paid launch");
  });

  it("device counts follow the plan contract: 1 PC and 2 phones on Free, 5 and 5 on Pro and Pro Plus", () => {
    expect(planById("free").pcs).toBe(PLANS.plans.free.max_enabled_pcs);
    expect(planById("free").phones).toBe(PLANS.plans.free.max_controllers);
    expect([planById("free").pcs, planById("free").phones]).toEqual([1, 2]);
    expect([planById("pro").pcs, planById("pro").phones]).toEqual([5, 5]);
    expect([planById("pro_plus").pcs, planById("pro_plus").phones]).toEqual([5, 5]);
  });

  it("Screen view is coming: 10 minutes per 5-hour window on Free, no Free time limit but fair-use limits on the paid plans", () => {
    expect(SCREEN_VIEW_STATUS).toBe("Coming");
    expect(POINT_AND_TALK_STATUS).toBe("Planned");
    expect(FREE_SCREEN_VIEW_MINUTES).toBe(10);
    expect(WINDOW_HOURS).toBe(5);
    expect(planById("free").screenView).toBe("10 minutes per 5-hour window");
    expect(planById("pro").screenView).toBe("Without the Free plan’s time limit (fair-use limits apply)");
    expect(planById("pro_plus").screenView).toBe(PAID_SCREEN_VIEW);
    // the relay keeps per-account limits on every plan: no plan's Screen view is "unlimited"
    for (const plan of PLAN_CONTENT) {
      expect(plan.screenView).not.toMatch(/unlimited/i);
      expect(plan.tagline).not.toMatch(/unlimited/i);
      expect(plan.highlights.find((h) => h.text.startsWith("Screen view"))!.text).not.toMatch(/unlimited/i);
    }
    // the card says "Coming" next to Screen view on every plan
    for (const plan of PLAN_CONTENT) expect(plan.highlights.find((h) => h.text.startsWith("Screen view"))?.status).toBe("Coming");
    // every AI allowance is marked, so the page can say "Not switched on yet" beside it while AI is off
    for (const plan of PLAN_CONTENT) expect(plan.highlights.filter((h) => h.ai).map((h) => h.text)).toEqual(plan.highlights.filter((h) => /AI /.test(h.text)).map((h) => h.text));
    for (const plan of PLAN_CONTENT.filter((p) => p.price)) {
      const layouts = plan.highlights.find((h) => h.text.startsWith("Custom remote layouts"))!;
      expect(layouts.status).toBe(LAYOUTS_STATUS);
      expect(layouts.text).not.toMatch(/paid launch/);
    }
  });

  it("AI butler allowances: a one-time taste of 10 on Free; 30 per 5 h / 120 per week on Pro; 90 / 300 on Pro Plus", () => {
    expect(FREE_TASTE_REQUESTS).toBe(10);
    expect(planById("free").ai).toEqual({ tasteRequests: 10 });
    expect(planById("pro").ai).toEqual({ perWindow: { requests: 30, minutes: 15 }, perWeek: { requests: 120, minutes: 60 } });
    expect(planById("pro_plus").ai).toEqual({ perWindow: { requests: 90, minutes: 45 }, perWeek: { requests: 300, minutes: 150 } });
    expect(aiSummary(planById("pro").ai)).toBe("30 requests (about 15 minutes) per 5-hour window, up to 120 (about 1 hour) a week");
    expect(aiSummary(planById("pro_plus").ai)).toBe("90 requests (about 45 minutes) per 5-hour window, up to 300 (about 2.5 hours) a week");
    expect(aboutTime(120)).toBe("about 2 hours");
  });

  it("the top-up is 100 requests for $2.99 on the paid plans only, never automatic", () => {
    expect(TOP_UP).toEqual({ requests: 100, priceCents: 299 });
    expect(planById("free").topUp).toBe(false);
    expect(planById("pro").topUp).toBe(true);
    expect(planById("pro_plus").topUp).toBe(true);
    expect(AI_RULES.topUp).toMatch(/100 AI requests for \$2\.99/);
    expect(AI_RULES.topUp).toMatch(/nothing is ever added or charged automatically/i);
  });

  it("explains the request, the free everyday commands, the windows with exact refresh time and the 80 % warning", () => {
    expect(AI_RULES.request).toMatch(/cannot work out on its own/);
    expect(AI_RULES.request).toMatch(/roughly 30 seconds/);
    expect(AI_RULES.everyday).toMatch(/“pause”, “volume 40”, “open Spotify”/);
    expect(AI_RULES.everyday).toMatch(/are free and never count against your AI allowance, on every plan, even after you reach an AI limit/);
    // a fair-use limit applies to every command (the comparison's "Fair-use limit on manual controls"): never "unlimited"
    expect(AI_RULES.everyday).not.toMatch(/unlimited/i);
    expect(planById("free").highlights.find((h) => h.text.startsWith("Everyday commands"))!.text).toBe("Everyday commands without AI: free, never counted");
    for (const plan of PLAN_CONTENT) for (const h of plan.highlights) expect(h.text).not.toMatch(/unlimited/i);
    expect(WEEK_DAYS).toBe(7);
    expect(WARN_AT_PERCENT).toBe(80);
    expect(AI_RULES.windows).toMatch(/5-hour window starts with your first AI request/);
    expect(AI_RULES.windows).toMatch(/refreshes 7 days after the week’s first use/);
    expect(AI_RULES.windows).toMatch(/Refreshes Saturday at 2 pm/);
    expect(AI_RULES.windows).toMatch(/When paid plans launch, the app will show the exact refresh time/);
    expect(AI_RULES.windows).toMatch(/warn you at 80%/);
    expect(AI_RULES.request).not.toMatch(/before anything runs/);
    expect(AI_RULES.actions).toMatch(/run right away/);
    expect(AI_RULES.actions).toMatch(/Ask before every action/);
    expect(AI_RULES.taste).toMatch(/one-time taste of 10 AI requests/);
    expect(AI_RULES.privacy).toMatch(/never sees your screen: no screenshots/);
    expect(AI_RULES.billing).toMatch(/US dollars and are the full amount you pay \(any tax is included\)/);
    expect(AI_RULES.billing).toMatch(/Stripe/);
  });
});

describe("plan content follows the contract (protocol 1.3)", () => {
  it("prices, the top-up and the AI figures equal plans.json's pricing_defaults and plan fields", () => {
    const pricing = PLANS.pricing_defaults;
    expect(pricing.currency).toBe("USD");
    expect(planById("pro").price).toEqual({ monthlyCents: pricing.plans.pro.monthly_cents, annualCents: pricing.plans.pro.annual_cents });
    expect(planById("pro_plus").price).toEqual({ monthlyCents: pricing.plans.pro_plus.monthly_cents, annualCents: pricing.plans.pro_plus.annual_cents });
    expect(TOP_UP).toEqual({ requests: pricing.topup.requests, priceCents: pricing.topup.price_cents });
    const plans = PLANS.plans;
    expect(FREE_TASTE_REQUESTS).toBe(plans.free.ai_taste_requests);
    expect(planById("pro").ai.perWindow?.requests).toBe(plans.pro.ai_window.requests);
    expect(planById("pro").ai.perWeek?.requests).toBe(plans.pro.ai_week.requests);
    expect(planById("pro_plus").ai.perWindow?.requests).toBe(plans.pro_plus.ai_window.requests);
    expect(planById("pro_plus").ai.perWeek?.requests).toBe(plans.pro_plus.ai_week.requests);
    expect([planById("pro_plus").pcs, planById("pro_plus").phones]).toEqual([plans.pro_plus.max_enabled_pcs, plans.pro_plus.max_controllers]);
    expect(planById("pro_plus").displayName).toBe(plans.pro_plus.display_name);
    expect(planById("pro").topUp).toBe(plans.pro.ai_topup_allowed);
    expect(planById("free").topUp).toBe(plans.free.ai_topup_allowed);
    expect(WINDOW_HOURS).toBe(plans.pro.ai_window.hours);
    expect(WEEK_DAYS).toBe(plans.pro.ai_week.days);
    // The owner's published figures (docs/PRODUCT_AND_PLANS.md): a change to plans.json must be a deliberate one.
    expect([pricing.plans.pro.monthly_cents, pricing.plans.pro.annual_cents, pricing.plans.pro_plus.monthly_cents, pricing.plans.pro_plus.annual_cents]).toEqual([999, 9999, 2499, 24999]);
    expect([pricing.topup.requests, pricing.topup.price_cents]).toEqual([100, 299]);
  });
});

describe("pricing rows", () => {
  it("has three plan columns per row and keeps the contract's layouts, routines and rate limit", () => {
    const rows = pricingRows();
    expect(rows.length).toBeGreaterThan(10);
    for (const r of rows) expect(Object.keys(r).sort()).toEqual(["free", "label", "pro", "proPlus"]);
    const layouts = rows.find((r) => r.label.startsWith("Custom remote layouts"))!;
    // not built yet: "Coming", never "at paid launch" (paid plans can go on sale before they exist)
    expect(layouts).toMatchObject({ free: NOT_INCLUDED, pro: COMING, proPlus: COMING });
    expect(COMING).toBe("Coming");
    expect(pricingGroups().find((g) => g.rows.some((r) => r.label === layouts.label))).toMatchObject({ title: "Layouts and routines", status: LAYOUTS_STATUS });
    expect(rows.some((r) => /paid launch/i.test(`${r.label} ${r.pro} ${r.proPlus}`))).toBe(false);
    const routines = rows.find((r) => r.label.startsWith("Saved routines"))!;
    expect(routines.label).toContain(`up to ${PLANS.plans.pro.routine_max_steps} steps`);
    const rate = rows.find((r) => r.label.startsWith("Fair-use limit"))!;
    expect(rate.free).toBe(`${PLANS.plans.free.manual_command_rate_limit.per_minute} per minute`);
  });

  it("states the AI and Screen view allowances per plan, with Screen view's group marked Coming", () => {
    const groups = pricingGroups();
    const screen = groups.find((g) => g.title === "Screen view")!;
    expect(screen.status).toBe("Coming");
    // the paid cells are the plan content's wording: no Free time limit, but fair-use limits on the relay, never "Unlimited"
    expect(screen.rows[0]).toMatchObject({ free: "10 min per 5-hour window", pro: PAID_SCREEN_VIEW, proPlus: PAID_SCREEN_VIEW });
    expect([screen.rows[0]!.pro, screen.rows[0]!.proPlus]).toEqual([planById("pro").screenView, planById("pro_plus").screenView]);
    expect(`${screen.rows[0]!.pro} ${screen.rows[0]!.proPlus}`).not.toMatch(/unlimited/i);
    const ai = groups.find((g) => g.title === "AI butler")!;
    // Free's one-time taste has its own row, so it never reads as "10 per window"
    expect(ai.rows.map((r) => [r.free, r.pro, r.proPlus])).toEqual([
      ["10, once (does not renew)", NOT_INCLUDED, NOT_INCLUDED],
      [NOT_INCLUDED, "30", "90"],
      [NOT_INCLUDED, "Up to 120", "Up to 300"],
      [NOT_INCLUDED, INCLUDED, INCLUDED],
    ]);
    expect(ai.rows[0]!.label).toBe("One-time taste to try the butler");
    // AI is off unless the server says otherwise: the group says so; with the assistant on, no status
    expect(ai.status).toBe(AI_OFF_STATUS);
    expect(pricingGroups({ aiOn: true }).find((g) => g.title === "AI butler")!.status).toBeUndefined();
    expect(ai.rows[3]!.label).toBe("Top-up: 100 requests for $2.99, only when you choose");
    const everyday = groups[0]!.rows.find((r) => r.label.startsWith("Everyday commands"))!;
    expect([everyday.free, everyday.pro, everyday.proPlus]).toEqual(["Never counted against AI limits", "Never counted against AI limits", "Never counted against AI limits"]);
    // the per-minute fair-use limit sits beside it, so no cell calls everyday commands "unlimited"
    expect(groups[0]!.rows.find((r) => r.label.startsWith("Fair-use limit"))).toBeDefined();
    for (const r of pricingRows()) expect(`${r.label} ${r.free} ${r.pro} ${r.proPlus}`).not.toMatch(/unlimited/i);
  });
});

describe("before payment (docs/legal: renewal, cancellation, Terms; the top-up)", () => {
  it("says the plan renews automatically at the price it is given, for each interval and for both", () => {
    const pro = planById("pro").price!;
    expect(renewalLine(pro, "monthly")).toBe("Renews automatically every month at $9.99 until you cancel.");
    expect(renewalLine(pro, "annual")).toBe("Renews automatically every year at $99.99 until you cancel.");
    expect(renewalLine(pro, "both")).toBe("Renews automatically every month at $9.99, or every year at $99.99 if you choose yearly billing, until you cancel.");
    // the amounts are the caller's plan data, never words in this file: another price, another sentence
    const other = { monthlyCents: 1234, annualCents: 12000 };
    expect(renewalLine(other, "monthly")).toBe("Renews automatically every month at $12.34 until you cancel.");
    expect(renewalLine(other, "annual")).toBe("Renews automatically every year at $120 until you cancel.");
    expect(renewalLine(other, "both")).toContain("every month at $12.34, or every year at $120");
  });

  it("says how to cancel, that the price includes any tax, and what subscribing agrees to", () => {
    // docs/BILLING.md §2: the Stripe prices are tax-inclusive
    expect(TAX_INCLUDED).toBe("Price includes any sales tax.");
    expect(HOW_TO_CANCEL).toBe("To cancel, open Billing and choose Manage subscription. Paid features stay until the end of the period you paid for.");
    expect(AGREE_TO_TERMS).toBe("By subscribing you agree to the Terms and the Privacy notice.");
  });

  it("describes the top-up as a one-time purchase and says what happens to unused requests, as cloud-api does today", () => {
    expect(topUpTerms(TOP_UP.requests)).toBe(
      "A one-time purchase, not a subscription: it adds 100 AI requests to your account. Unused top-up requests do not expire. They are used after your 5-hour window or week runs out, and they are kept if you move to Free, to use again on Pro or Pro Plus.",
    );
    expect(topUpTerms(250)).toContain("it adds 250 AI requests");
    expect(topUpTerms(TOP_UP.requests)).not.toMatch(/automatic|renew/i);
  });

  it("explains paid Screen view's fair-use limits with the Terms' example", () => {
    expect(PAID_SCREEN_VIEW).toBe("Without the Free plan’s time limit (fair-use limits apply)");
    expect(SCREEN_VIEW_FAIR_USE).toBe("Pro and Pro Plus will not have Free’s time limit, but fair-use limits apply, for example a monthly limit on relay connections per account.");
  });
});

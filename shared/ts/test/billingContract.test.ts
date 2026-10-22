import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ERRORS, PLANS, PROTOCOL_VERSION, ProtocolError, schemas } from "../src/index.ts";
import type { rest } from "../src/generated/index.ts";

// Protocol 1.3 billing bodies (docs/BILLING.md; shared/protocol/README.md "Protocol 1.3"): the same accept/refuse decisions as
// shared/python/tests/test_billing_contract.py, plus the shared allowance fixture's arithmetic.

const here = dirname(fileURLToPath(import.meta.url));
interface WindowCase {
  name: string;
  now?: string;
  open_window?: null;
  open_week?: null;
  limits?: { window?: number; week?: number };
  used?: { window?: number; week?: number };
  topup_balance?: number;
  topup_allowed?: boolean;
  taste?: { limit: number; used: number };
  resets_at?: string;
  window_ends_at?: string;
  week_ends_at?: string;
  expect: { source?: string | null; exhausted?: string | null; resets_at?: string | null; warn?: boolean; window_ends_at?: string; week_ends_at?: string; display_new_york?: string };
}
const FIXTURE = JSON.parse(readFileSync(resolve(here, "../../protocol/fixtures/ai-window-cases.json"), "utf8")) as {
  window_hours: number;
  week_days: number;
  warn_at_percent: number;
  cases: WindowCase[];
};

const TS = "2026-10-24T13:00:00.000Z";
const UUID = "44444444-4444-4444-8444-444444444444";
const UUID2 = "55555555-5555-4555-8555-555555555555";
const PRICING: rest.Pricing = {
  currency: "USD",
  plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } },
  topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 },
};
const CHECKOUT_RESPONSE: rest.BillingCheckoutResponse = { kind: "subscription", checkout_url: "https://checkout.stripe.com/c/pay/cs_test_a1", session_id: "cs_test_a1", expires_at: TS };
const SUBSCRIPTION = { state: "active", plan: "pro", interval: "monthly", current_period_end: TS, cancel_at_period_end: false, grace_until: null, checkout_expires_at: null } as const;
const STATUS: rest.BillingStatusResponse = {
  billing_enabled: true,
  billing_mode: "test",
  plan: "pro",
  entitlement_state: "active",
  subscription: SUBSCRIPTION,
  checkout_available: false,
  checkout_plans: [],
  portal_available: true,
  pricing: PRICING,
  topup: { allowed: true, balance_requests: 0, requests: 100, price_cents: 299 },
  selection: null,
};
const SELECTION: rest.PlanSelectionResponse = {
  open: true,
  deadline: TS,
  plan: "free",
  limits: { max_enabled_pcs: 1, max_controllers: 2 },
  pcs: [
    { id: UUID, name: "Desk PC", selected: true, last_seen: TS },
    { id: UUID2, name: "Laptop", selected: false, last_seen: null },
  ],
  controllers: [{ id: UUID, display_name: "Phone", selected: true, last_seen: null }],
};

function ok(name: string, value: unknown): void {
  schemas.validateRest(name, value);
}

function bad(name: string, value: unknown): void {
  let code = "OK";
  try {
    schemas.validateRest(name, value);
  } catch (e) {
    if (!(e instanceof ProtocolError)) throw e;
    code = e.code;
  }
  expect(code).toBe("MALFORMED_MESSAGE");
}

describe("billing REST bodies (protocol 1.3)", () => {
  it("accepts the examples and refuses unknown fields", () => {
    const examples: Array<[string, unknown]> = [
      ["plans_response", { plans: {}, pricing: PRICING, billing_enabled: false, billing_mode: "off", ai_enabled: false }],
      ["pricing", PRICING],
      ["billing_checkout_request", { plan: "pro", interval: "monthly" }],
      ["billing_checkout_response", CHECKOUT_RESPONSE],
      ["billing_portal_response", { portal_url: "https://billing.stripe.com/p/session/x" }],
      ["billing_webhook_response", { received: true, duplicate: true, status: "duplicate" }],
      ["billing_status_response", STATUS],
      ["plan_selection_request", { pc_ids: [UUID], controller_ids: [UUID, UUID2] }],
      ["plan_selection_response", SELECTION],
    ];
    for (const [name, value] of examples) {
      ok(name, value);
      bad(name, { ...(value as object), extra: 1 });
    }
    expect(PROTOCOL_VERSION).toBe("1.4"); // 1.3 added the billing shapes; 1.4 (Screen view) is current
  });

  it("pins plan ids, intervals and modes", () => {
    for (const plan of ["free", "pro", "pro_plus"]) ok("plan_id", plan);
    bad("plan_id", "enterprise");
    bad("paid_plan_id", "free");
    bad("billing_interval", "weekly");
    bad("billing_mode", "sandbox");
    bad("billing_checkout_request", { plan: "free", interval: "monthly" });
    bad("billing_checkout_request", { plan: "pro", interval: "monthly", price_id: "price_x" });
  });

  it("keeps the pricing object in the contract's shape and equal to plans.json", () => {
    bad("plans_response", { plans: {}, pricing: { currency: "USD", monthly_cents: 999, annual_cents: 9999 }, billing_enabled: false, billing_mode: "off", ai_enabled: false });
    ok("plans_response", { plans: {}, pricing: PRICING, billing_enabled: true, billing_mode: "test", ai_enabled: true });
    bad("plans_response", { plans: {}, pricing: PRICING, billing_enabled: true, billing_mode: "test" }); // ai_enabled is required
    bad("pricing", { ...PRICING, currency: "usd" });
    bad("pricing", { ...PRICING, plans: { ...PRICING.plans, free: { monthly_cents: 0, annual_cents: 0 } } });
    bad("pricing", { ...PRICING, topup: { ...PRICING.topup, requests: 0 } });
    expect(PLANS.pricing_defaults.plans).toEqual(PRICING.plans);
    expect(PLANS.pricing_defaults.topup).toEqual(PRICING.topup);
  });

  it("bounds the hosted-page URLs and the webhook statuses", () => {
    bad("billing_checkout_response", { ...CHECKOUT_RESPONSE, kind: "payment" });
    bad("billing_checkout_response", { ...CHECKOUT_RESPONSE, checkout_url: "http://checkout.stripe.com/x" });
    bad("billing_checkout_response", { ...CHECKOUT_RESPONSE, checkout_url: "https://" + "x".repeat(2048) });
    bad("billing_portal_response", { portal_url: "javascript:alert(1)" });
    for (const status of ["processed", "ignored", "failed", "duplicate"]) ok("billing_webhook_response", { received: true, duplicate: false, status });
    bad("billing_webhook_response", { received: true, duplicate: false, status: "queued" });
  });

  it("describes every subscription and entitlement state and never a provider id", () => {
    for (const state of ["checkout_pending", "incomplete", "trialing", "active", "cancel_scheduled", "past_due", "unpaid", "canceled"]) {
      ok("billing_status_response", { ...STATUS, subscription: { ...SUBSCRIPTION, state } });
    }
    bad("subscription_state", "disputed");
    for (const ent of ["free", "active", "pending", "past_due", "canceling", "expired"]) ok("billing_status_response", { ...STATUS, entitlement_state: ent });
    bad("billing_status_response", { ...STATUS, entitlement_state: "paused" });
    ok("billing_status_response", { ...STATUS, plan: "free", subscription: null, checkout_available: true, portal_available: false, billing_enabled: false, billing_mode: "off" });
    ok("billing_status_response", { ...STATUS, plan: "free", subscription: null, checkout_available: true, checkout_plans: ["pro"] }); // AI off: no Pro Plus
    bad("billing_status_response", { ...STATUS, checkout_plans: ["free"] });
    bad("billing_status_response", { ...STATUS, checkout_plans: ["pro", "pro"] });
    ok("billing_status_response", { ...STATUS, selection: { open: true, deadline: TS } });
    bad("billing_status_response", { ...STATUS, selection: { open: true } });
    bad("billing_status_response", { ...STATUS, subscription: { ...SUBSCRIPTION, plan: "free" } });
    bad("billing_status_response", { ...STATUS, subscription: { ...SUBSCRIPTION, customer_id: "cus_x" } });
    bad("billing_status_response", { ...STATUS, topup: { ...STATUS.topup, balance_requests: -1 } });
  });

  it("bounds the plan selection bodies", () => {
    ok("plan_selection_request", { pc_ids: [], controller_ids: [] });
    bad("plan_selection_request", { pc_ids: [UUID, UUID], controller_ids: [] });
    bad("plan_selection_request", { pc_ids: Array(17).fill(UUID), controller_ids: [] });
    bad("plan_selection_request", { pc_ids: ["nope"], controller_ids: [] });
    ok("plan_selection_response", { ...SELECTION, open: false, deadline: null, plan: "pro" });
    bad("plan_selection_response", { ...SELECTION, pcs: [{ ...SELECTION.pcs[0], enabled: true }] });
    bad("plan_selection_response", { ...SELECTION, pcs: [{ ...SELECTION.pcs[0], name: "x".repeat(65) }] });
  });

  it("has the billing error codes and the reworded allowance message", () => {
    const expected: Record<string, boolean> = {
      BILLING_NOT_AVAILABLE: false,
      BILLING_PROVIDER_ERROR: true,
      BILLING_WEBHOOK_INVALID: false,
      ALREADY_SUBSCRIBED: false,
      CHECKOUT_PROCESSING: true,
      PLAN_NOT_AVAILABLE: false,
      BILLING_NO_CUSTOMER: false,
      TOPUP_NOT_ALLOWED: false,
      TOPUP_BALANCE_LIMIT: false,
      PLAN_SELECTION_CLOSED: false,
      PLAN_SELECTION_INVALID: false,
    };
    for (const [code, retryable] of Object.entries(expected)) {
      expect(ERRORS[code as keyof typeof ERRORS]?.retryable).toBe(retryable);
    }
    expect(ERRORS.AI_ALLOWANCE_EXHAUSTED?.user_message).not.toContain("month");
  });
});

/** Reference arithmetic of rules.ai_allowance: window before week; a full bucket is paid by a top-up when allowed. */
function exhaustedReason(c: WindowCase): [string | null, string | null] {
  if (c.taste) return c.taste.used >= c.taste.limit ? [null, "taste"] : ["taste", null];
  const full = (["window", "week"] as const).filter((bucket) => {
    const limit = c.limits?.[bucket];
    return limit !== undefined && (c.used?.[bucket] ?? 0) >= limit;
  });
  if (full.length === 0) return ["window", null];
  if (c.topup_allowed && (c.topup_balance ?? 0) > 0) return ["topup", null];
  // both full: the reason is the bucket that clears later, so the reset time named is when a request works again
  if (full.length === 2 && c.window_ends_at && c.week_ends_at && Date.parse(c.window_ends_at) > Date.parse(c.week_ends_at)) return [null, "window"];
  return [null, full[full.length - 1]!];
}

describe("ai-window-cases fixture", () => {
  it("matches the plans' window and week lengths", () => {
    for (const plan of Object.values(PLANS.plans)) {
      expect(plan.ai_window.hours).toBe(FIXTURE.window_hours);
      expect(plan.ai_week.days).toBe(FIXTURE.week_days);
    }
  });
  for (const c of FIXTURE.cases) {
    it(c.name, () => {
      if (c.open_window === null && c.open_week === null && c.now) {
        const now = Date.parse(c.now);
        expect(Date.parse(c.expect.window_ends_at!)).toBe(now + FIXTURE.window_hours * 3_600_000);
        expect(Date.parse(c.expect.week_ends_at!)).toBe(now + FIXTURE.week_days * 86_400_000);
      }
      if (c.taste || (c.limits && c.used && c.expect.warn === undefined)) {
        const [source, exhausted] = exhaustedReason(c);
        expect(exhausted).toBe(c.expect.exhausted ?? null);
        if (c.expect.source !== undefined) expect(source).toBe(c.expect.source);
        if (exhausted === "window") expect(c.expect.resets_at).toBe(c.window_ends_at);
        if (exhausted === "week") expect(c.expect.resets_at).toBe(c.week_ends_at);
        if (exhausted === "taste") expect(c.expect.resets_at).toBeNull();
      }
      if (c.expect.warn !== undefined) {
        const used = c.used!.window!;
        const limit = c.limits!.window!;
        expect(used * 100 >= limit * FIXTURE.warn_at_percent).toBe(c.expect.warn);
      }
    });
  }
});

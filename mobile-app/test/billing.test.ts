/**
 * lib/billing.ts: the refresh wording pinned by the shared fixture (fixtures/ai-window-cases.json, in America/New_York),
 * the 80 % level, the Settings line, which limit stopped a turn, the plan-state copy, and the error text for billing codes.
 */
import { describe, expect, it } from "vitest";

import { ERRORS, type rest } from "@dome/protocol";

import fixture from "../../shared/protocol/fixtures/ai-window-cases.json";
import { WARN_AT_PERCENT } from "../src/content/plans.ts";
import { ApiError } from "../src/lib/api.ts";
import { allowanceSummary, allowanceWarns, billingErrorText, billingStateCopy, bucketLevel, isCheckoutUrl, limitExplanation, longDate, nextRefresh, refreshText, requestsLeft, topupLabel, warningText } from "../src/lib/billing.ts";

const NY = "America/New_York";
const lowerFirstWord = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

const WINDOWS: rest.AssistantAllowance = {
  kind: "windows",
  taste: null,
  window: { limit: 30, used: 2, starts_at: "2026-10-24T13:00:00Z", ends_at: "2026-10-24T18:00:00Z" },
  week: { limit: 120, used: 7, starts_at: "2026-10-24T13:00:00Z", ends_at: "2026-10-31T13:00:00Z" },
  topup: { balance: 0, allowed: true },
  exhausted: null,
  resets_at: null,
  warn_at_percent: 80,
};
const TASTE: rest.AssistantAllowance = { kind: "taste", taste: { limit: 10, used: 3 }, window: null, week: null, topup: { balance: 0, allowed: false }, exhausted: null, resets_at: null, warn_at_percent: 80 };
const NONE: rest.AssistantAllowance = { ...TASTE, kind: "none", taste: null };

interface Case {
  name: string;
  now?: string;
  limits?: { window?: number; week?: number };
  used?: { window?: number; week?: number };
  resets_at?: string;
  taste?: { limit: number; used: number };
  expect: { display_new_york?: string; warn?: boolean; resets_at?: string | null; exhausted?: string | null };
}

describe("refreshText (the shared fixture's wording, formatted in New York)", () => {
  const cases = (fixture.cases as Case[]).filter((c) => c.expect.display_new_york !== undefined);
  it.each(cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const resets = c.taste ? null : (c.resets_at ?? c.expect.resets_at ?? null);
    expect(refreshText(resets, new Date(c.now ?? "2026-10-20T10:00:00Z"), NY)).toBe(c.expect.display_new_york);
  });

  it("writes the viewer's own zone when none is given, keeps minutes only when non-zero, and never invents a date", () => {
    expect(refreshText("2026-10-24T18:00:00Z", new Date("2026-10-24T15:00:00Z"), "UTC")).toBe("Refreshes today at 6 pm");
    expect(refreshText("2026-10-24T18:30:00Z", new Date("2026-10-24T15:00:00Z"), "UTC")).toBe("Refreshes today at 6:30 pm");
    expect(refreshText("2026-10-25T00:05:00Z", new Date("2026-10-24T15:00:00Z"), "UTC")).toBe("Refreshes tomorrow at 12:05 am");
    expect(refreshText("2026-10-30T09:00:00Z", new Date("2026-10-24T15:00:00Z"), "UTC")).toBe("Refreshes Friday at 9 am");
    expect(refreshText("2026-10-31T09:00:00Z", new Date("2026-10-24T15:00:00Z"), "UTC")).toBe("Refreshes 31 October at 9 am");
    expect(refreshText(null, new Date())).toBe("Does not renew");
    expect(refreshText("not a time", new Date())).toBe("Refreshes later");
    expect(refreshText("2026-10-24T18:00:00Z", new Date("2026-10-24T15:00:00Z"))).toMatch(/^Refreshes today at \d{1,2}(:\d{2})? [ap]m$/);
    expect(longDate("2026-11-20T12:00:00Z", "UTC")).toBe("20 November 2026");
    expect(longDate(null)).toBe("—");
  });
});

describe("bucketLevel and the fixture's warning cases", () => {
  it("warns from 80 %, not at 79 %, and is exhausted at the limit", () => {
    expect(WARN_AT_PERCENT).toBe(fixture.warn_at_percent);
    expect(bucketLevel(23, 30)).toBe("ok"); // 76 %
    expect(bucketLevel(24, 30)).toBe("warn"); // 80 %
    expect(bucketLevel(71, 90)).toBe("ok"); // 78.9 %
    expect(bucketLevel(72, 90)).toBe("warn"); // 80 %
    expect(bucketLevel(30, 30)).toBe("exhausted");
    expect(bucketLevel(0, 0)).toBe("exhausted");
  });

  it.each((fixture.cases as Case[]).filter((c) => c.expect.warn !== undefined).map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const a: rest.AssistantAllowance = { ...WINDOWS, window: { ...WINDOWS.window!, limit: c.limits!.window!, used: c.used!.window! } };
    expect(allowanceWarns(a)).toBe(c.expect.warn);
    expect(bucketLevel(c.used!.window!, c.limits!.window!) === "warn").toBe(c.expect.warn);
  });

  it("the taste never warns; a full week warns even with room in the window", () => {
    expect(allowanceWarns({ ...TASTE, taste: { limit: 10, used: 9 } })).toBe(false);
    expect(allowanceWarns({ ...WINDOWS, week: { ...WINDOWS.week!, used: 100 } })).toBe(true);
    expect(warningText({ ...WINDOWS, week: { ...WINDOWS.week!, used: 100 } }, new Date("2026-10-24T15:00:00Z"), NY)).toBe("You've used 100 of 120 AI requests this week. Refreshes 31 October at 9 am.");
    expect(warningText({ ...WINDOWS, window: { ...WINDOWS.window!, used: 24, ends_at: "2026-10-24T22:30:00Z" } }, new Date("2026-10-24T15:00:00Z"), NY)).toBe("You've used 24 of 30 AI requests in this 5-hour window. Refreshes today at 6:30 pm.");
    expect(warningText(WINDOWS)).toBeNull();
    expect(warningText(TASTE)).toBeNull();
  });
});

describe("allowanceSummary (the Settings line)", () => {
  const now = new Date("2026-10-20T10:00:00Z");
  it("Free: the one-time taste, with 'none left' when it is used up", () => {
    expect(allowanceSummary(TASTE)).toBe("3 of 10 AI requests to try used");
    expect(allowanceSummary({ ...TASTE, taste: { limit: 10, used: 10 }, exhausted: "taste" })).toBe("10 of 10 AI requests to try used · none left");
  });

  it("paid plans: both windows, the earliest refresh, or 'starts with your next request' while none is open", () => {
    expect(allowanceSummary(WINDOWS, now, NY)).toBe("2 of 30 used in this 5-hour window · 7 of 120 this week · refreshes Saturday at 2 pm");
    expect(nextRefresh(WINDOWS)).toBe("2026-10-24T18:00:00Z");
    const closed: rest.AssistantAllowance = { ...WINDOWS, window: { limit: 30, used: 0, starts_at: null, ends_at: null }, week: { limit: 120, used: 0, starts_at: null, ends_at: null } };
    expect(allowanceSummary(closed, now, NY)).toBe("0 of 30 used in this 5-hour window · 0 of 120 this week · starts with your next request");
    expect(nextRefresh(closed)).toBeNull();
  });

  it("names the later end while buckets are full (the shared fixture's exhausted cases), never the window's earlier one", () => {
    const exhausted = (fixture.cases as (Case & { window_ends_at?: string; week_ends_at?: string; topup_balance?: number })[]).filter(
      (c) => c.limits?.window !== undefined && c.limits.week !== undefined && c.used && c.window_ends_at && c.week_ends_at && c.expect.exhausted && !c.topup_balance,
    );
    expect(exhausted.map((c) => c.name)).toEqual(expect.arrayContaining(["window and week both full", "both full, the week ends before the window"]));
    for (const c of exhausted) {
      const bucket = (limit: number, used: number, ends: string) => ({ limit, used, starts_at: "2026-10-24T13:00:00Z", ends_at: ends });
      const a: rest.AssistantAllowance = { ...WINDOWS, window: bucket(c.limits!.window!, c.used!.window ?? 0, c.window_ends_at!), week: bucket(c.limits!.week!, c.used!.week ?? 0, c.week_ends_at!) };
      expect(nextRefresh(a), c.name).toBe(c.expect.resets_at); // computed from the buckets alone
      expect(nextRefresh({ ...a, exhausted: "week", resets_at: "2026-11-01T00:00:00Z" })).toBe("2026-11-01T00:00:00Z"); // the server's answer wins
      expect(allowanceSummary(a, new Date(c.now!), NY), c.name).toContain(lowerFirstWord(c.expect.display_new_york!));
    }
  });

  it("a top-up balance is added at the end; a plan without AI has no line", () => {
    expect(allowanceSummary({ ...WINDOWS, topup: { balance: 100, allowed: true } }, now, NY)).toBe("2 of 30 used in this 5-hour window · 7 of 120 this week · refreshes Saturday at 2 pm · 100 top-up requests available");
    expect(allowanceSummary({ ...TASTE, topup: { balance: 100, allowed: false } })).toBe("3 of 10 AI requests to try used · 100 top-up requests available");
    expect(allowanceSummary(NONE)).toBe("");
  });

  it("requestsLeft is the smaller room of the two windows plus a spendable top-up; the taste's remainder on Free", () => {
    expect(requestsLeft(WINDOWS)).toBe(28);
    expect(requestsLeft({ ...WINDOWS, week: { ...WINDOWS.week!, used: 115 } })).toBe(5);
    expect(requestsLeft({ ...WINDOWS, topup: { balance: 100, allowed: true } })).toBe(128);
    expect(requestsLeft({ ...WINDOWS, topup: { balance: 100, allowed: false } })).toBe(28);
    expect(requestsLeft(TASTE)).toBe(7);
    expect(requestsLeft(NONE)).toBeNull();
  });
});

describe("limitExplanation (which limit stopped a turn)", () => {
  const now = new Date("2026-10-24T15:00:00Z");
  it("names the taste, the window, the week, the cost cap or the failures, then when it refreshes", () => {
    expect(limitExplanation("taste", { ...TASTE, taste: { limit: 10, used: 10 }, exhausted: "taste" })).toBe("You've used your one-time taste of 10 AI requests. It does not renew.");
    const window: rest.AssistantAllowance = { ...WINDOWS, window: { ...WINDOWS.window!, used: 30 }, exhausted: "window", resets_at: "2026-10-24T18:00:00Z" };
    expect(limitExplanation("window", window, { now, timeZone: NY })).toBe("You've used this 5-hour window's 30 AI requests. Refreshes today at 2 pm.");
    const week: rest.AssistantAllowance = { ...WINDOWS, week: { ...WINDOWS.week!, used: 120 }, exhausted: "week", resets_at: "2026-10-31T13:00:00Z" };
    expect(limitExplanation("week", week, { now: new Date("2026-10-30T20:30:00Z"), timeZone: NY })).toBe("You've used this week's 120 AI requests. Refreshes tomorrow at 9 am.");
    expect(limitExplanation("cost_cap", { ...WINDOWS, exhausted: "cost_cap", resets_at: "2026-10-31T13:00:00Z" }, { now, timeZone: NY })).toBe("This week's AI requests needed more processing than your plan includes. Refreshes 31 October at 9 am.");
    expect(limitExplanation("failures", { ...WINDOWS, exhausted: "failures", resets_at: "2026-10-31T13:00:00Z" }, { now, timeZone: NY })).toBe("Too many AI requests failed this week, so the assistant is paused to protect your allowance. Refreshes 31 October at 9 am.");
  });

  it("the error's own resets_at wins over the allowance's; nothing known gives no refresh and no number", () => {
    expect(limitExplanation("window", { ...WINDOWS, resets_at: "2026-10-24T18:00:00Z" }, { resetsAt: "2026-10-24T22:30:00Z", now, timeZone: NY })).toBe("You've used this 5-hour window's 30 AI requests. Refreshes today at 6:30 pm.");
    expect(limitExplanation("window", null)).toBe("You've used this 5-hour window's AI requests.");
    expect(limitExplanation("week", null)).toBe("You've used this week's AI requests.");
    expect(limitExplanation(undefined, WINDOWS)).toBe("");
    expect(limitExplanation("requests", WINDOWS)).toBe("");
  });

  it("no wording says 'month' any more", () => {
    for (const reason of ["taste", "window", "week", "cost_cap", "failures"]) expect(limitExplanation(reason, WINDOWS)).not.toMatch(/month/i);
  });
});

describe("billingStateCopy", () => {
  const base = { plan: "pro" as const, entitlement_state: "active" as const };
  const sub = (over: Partial<NonNullable<rest.BillingStatusResponse["subscription"]>>): rest.BillingStatusResponse["subscription"] => ({ state: "active", plan: "pro", interval: "monthly", current_period_end: "2026-11-20T12:00:00Z", cancel_at_period_end: false, grace_until: null, checkout_expires_at: null, ...over });

  it("covers every subscription state with the plan's name and the dates the server gave", () => {
    expect(billingStateCopy({ ...base, subscription: sub({}) })).toMatchObject({ label: "Active", tone: "success", text: "Your DoMe Pro subscription is active." });
    expect(billingStateCopy({ ...base, subscription: sub({ state: "trialing", plan: "pro_plus" }) })).toMatchObject({ label: "Active", text: "Your DoMe Pro Plus subscription is active." });
    const canceling = billingStateCopy({ ...base, subscription: sub({ state: "cancel_scheduled", cancel_at_period_end: true }) }, "UTC");
    expect(canceling).toMatchObject({ label: "Ends at period end", tone: "warning" });
    expect(canceling.text).toBe("DoMe Pro stays on until 20 November 2026. Then your account returns to Free: 1 PC and 2 phones stay enabled, your layouts and routines are kept but cannot run, and you can re-subscribe at any time.");
    // Stripe's `active` with cancel_at_period_end set reads the same way
    expect(billingStateCopy({ ...base, subscription: sub({ cancel_at_period_end: true }) }, "UTC").text).toBe(canceling.text);
    const pastDue = billingStateCopy({ ...base, subscription: sub({ state: "past_due", grace_until: "2026-11-27T12:00:00Z" }) }, "UTC");
    expect(pastDue).toMatchObject({ label: "Payment problem", tone: "danger" });
    expect(pastDue.text).toBe("Your last payment did not go through. Update your card by 27 November 2026 to keep DoMe Pro. Free features keep working.");
    for (const state of ["checkout_pending", "incomplete"] as const) {
      expect(billingStateCopy({ ...base, subscription: sub({ state }) })).toMatchObject({ label: "Pending activation", tone: "warning", text: "Pending activation — we are confirming your payment. Nothing is unlocked until it is verified." });
    }
    expect(billingStateCopy({ plan: "free", entitlement_state: "expired", subscription: sub({ state: "unpaid" }) }).text).toMatch(/^Your DoMe Pro subscription ended because the payment could not be collected\. You are on DoMe Free; your PCs, phones and settings were kept\.$/);
    expect(billingStateCopy({ plan: "free", entitlement_state: "expired", subscription: sub({ state: "canceled" }) })).toMatchObject({ label: "Expired", tone: "neutral", text: "Your paid subscription ended. You are on DoMe Free; your PCs, phones and settings were kept." });
  });

  it("falls back to the six entitlement states without a subscription row", () => {
    expect(billingStateCopy({ plan: "free", entitlement_state: "free", subscription: null })).toMatchObject({ label: "Free", tone: "neutral" });
    expect(billingStateCopy({ plan: "pro", entitlement_state: "active", subscription: null })).toMatchObject({ label: "Active", text: "Your DoMe Pro subscription is active." });
    expect(billingStateCopy({ plan: "free", entitlement_state: "pending", subscription: null })).toMatchObject({ label: "Pending activation" });
    expect(billingStateCopy({ plan: "pro", entitlement_state: "past_due", subscription: null })).toMatchObject({ label: "Payment problem", text: "Your last payment did not go through. Free features keep working." });
    expect(billingStateCopy({ plan: "pro", entitlement_state: "canceling", subscription: null })).toMatchObject({ label: "Ends at period end" });
    expect(billingStateCopy({ plan: "free", entitlement_state: "expired", subscription: null })).toMatchObject({ label: "Expired" });
  });
});

describe("billing errors and checkout URLs", () => {
  it("shows the contract's message for every billing code, the generic one for the rest", () => {
    for (const code of ["BILLING_NOT_AVAILABLE", "BILLING_PROVIDER_ERROR", "ALREADY_SUBSCRIBED", "CHECKOUT_PROCESSING", "PLAN_NOT_AVAILABLE", "BILLING_NO_CUSTOMER", "TOPUP_NOT_ALLOWED", "TOPUP_BALANCE_LIMIT", "PLAN_SELECTION_CLOSED", "PLAN_SELECTION_INVALID", "RATE_LIMITED"]) {
      expect(billingErrorText(new ApiError(400, code, "server text", false))).toBe(ERRORS[code]!.user_message);
    }
    expect(billingErrorText(new ApiError(400, "BILLING_NOT_AVAILABLE", "x", false))).toBe("Payments aren't available yet. Nothing was charged.");
    expect(billingErrorText(new ApiError(0, "NETWORK", "offline", true))).toBe("You appear to be offline.");
    expect(billingErrorText(new Error("boom"))).toBe(ERRORS.INTERNAL!.user_message);
    expect(billingErrorText(null)).toBe(ERRORS.INTERNAL!.user_message);
  });

  it("navigates only to an https URL the server gave, and labels the top-up from the server's figures", () => {
    expect(isCheckoutUrl("https://checkout.stripe.com/c/pay/cs_test_a1b2")).toBe(true);
    expect(isCheckoutUrl("http://checkout.stripe.com/c/pay/cs_test_a1b2")).toBe(false);
    expect(isCheckoutUrl("javascript:alert(1)")).toBe(false);
    expect(isCheckoutUrl("https://x.test/a b")).toBe(false);
    expect(isCheckoutUrl(`https://x.test/${"a".repeat(2048)}`)).toBe(false);
    expect(topupLabel({ requests: 100, price_cents: 299 })).toBe("Add 100 AI requests for $2.99");
  });
});

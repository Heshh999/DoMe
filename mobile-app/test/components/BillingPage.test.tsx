/**
 * The in-app Billing page (docs/BILLING.md; protocol 1.3). Everything about the customer's own plan comes from
 * GET /v1/account/billing and GET /v1/session; purchases are Stripe-hosted pages the server names; returning from one
 * unlocks nothing until the server says so. Without Stripe keys the page is today's honest page: plan, limits, the
 * website's prices, and no way to buy anything.
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ERRORS, type rest } from "@dome/protocol";

import { COMING_AT_PAID_LAUNCH, planById, usd } from "../../src/content/plans.ts";
import { configureApi } from "../../src/lib/api.ts";
import { browserNavigation } from "../../src/lib/billing.ts";
import { BillingPage, NOT_SWITCHED_ON, PRO_PLUS_NOT_SOLD, RETURN_POLL_MS, STILL_CONFIRMING, TEST_MODE } from "../../src/pages/app/BillingPage.tsx";
import { useAssistantStore } from "../../src/store/assistant.ts";
import { useSessionStore } from "../../src/store/session.ts";
import { jsonResponse, PC, SESSION, TS } from "../helpers/harness.ts";

const PRO_LIMITS = { max_enabled_pcs: 5, max_controllers: 5, routines: true, custom_layouts: true };
const PRICING: rest.Pricing = { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } };
const OFF: rest.BillingStatusResponse = { billing_enabled: false, billing_mode: "off", plan: "free", entitlement_state: "free", subscription: null, checkout_available: false, checkout_plans: [], portal_available: false, pricing: PRICING, topup: { allowed: false, balance_requests: 0, requests: 100, price_cents: 299 }, selection: null };
const FREE_ON: rest.BillingStatusResponse = { ...OFF, billing_enabled: true, billing_mode: "test", checkout_available: true, checkout_plans: ["pro", "pro_plus"] };
const SUB: NonNullable<rest.BillingStatusResponse["subscription"]> = { state: "active", plan: "pro", interval: "monthly", current_period_end: "2026-11-20T12:00:00.000Z", cancel_at_period_end: false, grace_until: null, checkout_expires_at: null };
const PRO_ON: rest.BillingStatusResponse = { ...FREE_ON, plan: "pro", entitlement_state: "active", subscription: SUB, checkout_available: false, checkout_plans: [], portal_available: true, topup: { allowed: true, balance_requests: 0, requests: 100, price_cents: 299 } };
const WINDOWS: rest.AssistantAllowance = { kind: "windows", taste: null, window: { limit: 30, used: 24, starts_at: TS, ends_at: "2099-01-01T12:00:00.000Z" }, week: { limit: 120, used: 30, starts_at: TS, ends_at: "2099-01-05T12:00:00.000Z" }, topup: { balance: 0, allowed: true }, exhausted: null, resets_at: null, warn_at_percent: 80 };
const TASTE: rest.AssistantAllowance = { kind: "taste", taste: { limit: 10, used: 3 }, window: null, week: null, topup: { balance: 0, allowed: false }, exhausted: null, resets_at: null, warn_at_percent: 80 };
const AI_ON: rest.AssistantStatusResponse = { available: true, unavailable_reason: null, provider: "fake", turn_deadline_seconds: 20, consent_version: "2026-10-11", consent: null, allowance: TASTE };
const AI_OFF: rest.AssistantStatusResponse = { ...AI_ON, available: false, unavailable_reason: "not_configured", provider: "none" };
const PHONE_A = "77777777-7777-4777-8777-777777777777";
const PHONE_B = "88888888-8888-4888-8888-888888888888";
const PHONE_C = "99999999-9999-4999-8999-999999999999";
const OTHER = "55555555-5555-4555-8555-555555555555";
const CHECKOUT_URL = "https://checkout.stripe.test/c/pay/cs_test_a1b2c3";
const PORTAL_URL = "https://billing.stripe.test/p/session/test_x1y2";

interface Call {
  method: string;
  url: string;
  body: unknown;
  csrf: string | undefined;
}

let calls: Call[];
let billing: rest.BillingStatusResponse;
let assistant: rest.AssistantStatusResponse;
let session: rest.SessionResponse;
let selection: rest.PlanSelectionResponse;
let respond: Partial<Record<string, () => Response>>;
let assign: ReturnType<typeof vi.spyOn>;

function Location() {
  const loc = useLocation();
  return <p data-testid="location">{loc.pathname + loc.search}</p>;
}

function renderBilling(path = "/app/billing") {
  useSessionStore.setState({ status: "signed_in", session, error: null });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/app/billing"
          element={
            <>
              <BillingPage />
              <Location />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  calls = [];
  billing = OFF;
  assistant = AI_ON;
  session = SESSION;
  selection = { open: true, deadline: "2026-11-03T12:00:00.000Z", plan: "free", limits: { max_enabled_pcs: 1, max_controllers: 2 }, pcs: [{ id: PC, name: "Office PC", selected: true, last_seen: TS }, { id: OTHER, name: "Den PC", selected: false, last_seen: null }], controllers: [{ id: PHONE_A, display_name: "Ann's iPhone", selected: true, last_seen: TS }, { id: PHONE_B, display_name: "Kitchen iPad", selected: true, last_seen: TS }, { id: PHONE_C, display_name: "Old phone", selected: false, last_seen: null }] };
  respond = {};
  useAssistantStore.setState({ status: null, statusError: null, allowance: null, aiBlocked: null, items: [] });
  assign = vi.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
  configureApi({
    csrfToken: SESSION.csrf_token,
    accountId: SESSION.account.id,
    fetchImpl: async (url, init) => {
      const method = init.method ?? "GET";
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      calls.push({ method, url: path, body: init.body ? JSON.parse(init.body as string) : undefined, csrf: (init.headers as Record<string, string>)["X-DoMe-CSRF"] });
      const custom = respond[`${method} ${path}`];
      if (custom) return custom();
      if (path === "/v1/account/billing") return jsonResponse(200, billing);
      if (path === "/v1/assistant") return jsonResponse(200, assistant);
      if (path === "/v1/session") return jsonResponse(200, session);
      if (path === "/v1/account/plan-selection" && method === "GET") return jsonResponse(200, selection);
      if (path === "/v1/billing/checkout") return jsonResponse(200, { kind: "subscription", checkout_url: CHECKOUT_URL, session_id: "cs_test_a1b2c3", expires_at: "2026-10-21T12:00:00.000Z" });
      if (path === "/v1/billing/portal") return jsonResponse(200, { portal_url: PORTAL_URL });
      if (path === "/v1/billing/topup") return jsonResponse(200, { kind: "topup", checkout_url: CHECKOUT_URL, session_id: "cs_test_topup1", expires_at: "2026-10-21T12:00:00.000Z" });
      return jsonResponse(500, { error: { code: "INTERNAL", message: "unexpected", retryable: false } });
    },
  });
});

afterEach(() => {
  vi.useRealTimers();
  assign.mockRestore();
  configureApi({ csrfToken: null, accountId: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
});

const error = (status: number, code: string) => () => jsonResponse(status, { error: { code, message: "server text", retryable: false } });
const billingLoaded = () => waitFor(() => expect(calls.some((c) => c.url === "/v1/account/billing")).toBe(true));

describe("BillingPage", () => {
  it("billing off shows today's honest page and no buttons (plan and limits from the session document, the website's prices)", async () => {
    const { container } = renderBilling();
    expect(screen.getByText("DoMe Free")).toBeInTheDocument();
    expect(await screen.findByText("Upgrading is not available yet")).toBeInTheDocument();
    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(container.textContent).toMatch(/You are on DoMe Free/);
    const limits = screen.getByRole("heading", { name: "Your limits" }).closest("div") as HTMLElement;
    expect(limits).toHaveTextContent(/Enabled PCs\s*1/);
    expect(limits).toHaveTextContent(/Paired phones\s*2/);
    expect(limits).toHaveTextContent(/Routines\s*Not included/);
    expect(limits).toHaveTextContent(/Custom layouts\s*Not included/);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getAllByRole("link").map((l) => l.getAttribute("href"))).toEqual(["/pricing"]);
    expect(container.textContent).not.toMatch(/upgrade now|subscribe|buy now|checkout now|Continue to payment|Test mode/i);
    const notice = screen.getByText("Upgrading is not available yet").closest('[role="status"]') as HTMLElement;
    expect(notice).toHaveTextContent("Pro ($9.99 / month or $99.99 / year) and Pro Plus ($24.99 / month or $249.99 / year) are coming at paid launch");
    expect(notice).toHaveTextContent(/Nothing can be bought or charged from this app today/);
    expect(within(notice).getByRole("link", { name: "Compare plans" })).toHaveAttribute("href", "/pricing");
    const pro = planById("pro").price!;
    expect(notice).toHaveTextContent(`${usd(pro.monthlyCents)} / month or ${usd(pro.annualCents)} / year`);
    expect(notice).toHaveTextContent(COMING_AT_PAID_LAUNCH.toLowerCase());
    expect(container.textContent).toMatch(/never paywalled/);
    // nothing but reads happened
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });

  it("an older server without the billing endpoint still gets today's honest page, with a quiet note", async () => {
    respond["GET /v1/account/billing"] = error(404, "NOT_FOUND");
    const { container } = renderBilling();
    expect(await screen.findByText("Upgrading is not available yet")).toBeInTheDocument();
    expect(container.textContent).toMatch(/Billing details could not be loaded right now/);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("without a billing status (an older server) the plan state comes from the session document: active Pro, pending, past due with recovery steps, canceling, expired", async () => {
    respond["GET /v1/account/billing"] = error(404, "NOT_FOUND");
    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    const { unmount } = renderBilling();
    expect(await screen.findByText("DoMe Pro")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your limits" }).closest("div")).toHaveTextContent(/Enabled PCs\s*5/);
    unmount();

    session = { ...SESSION, plan: "pro", entitlement_state: "pending", limits: PRO_LIMITS };
    const pending = renderBilling();
    expect(await screen.findByText("Pending activation")).toBeInTheDocument();
    expect(pending.container.textContent).toMatch(/Nothing is unlocked until it is verified/);
    pending.unmount();

    session = { ...SESSION, plan: "pro", entitlement_state: "past_due", limits: PRO_LIMITS };
    const pastDue = renderBilling();
    expect(await screen.findByText("Payment problem")).toBeInTheDocument();
    expect(pastDue.container.textContent).toMatch(/Free features keep working/);
    expect(screen.getByRole("list")).toHaveTextContent("Open Billing and choose Manage subscription to update your card.");
    pastDue.unmount();

    session = { ...SESSION, plan: "pro", entitlement_state: "canceling", limits: PRO_LIMITS };
    const canceling = renderBilling();
    expect(await screen.findByText("Ends at period end")).toBeInTheDocument();
    canceling.unmount();

    session = { ...SESSION, plan: "free", entitlement_state: "expired" };
    renderBilling();
    expect(await screen.findByText("Expired")).toBeInTheDocument();
    expect(screen.getByText(/your PCs, phones and settings were kept/)).toBeInTheDocument();
  });

  it("test mode is labelled (on the plan card and above the checkout buttons)", async () => {
    billing = FREE_ON;
    renderBilling();
    expect((await screen.findAllByText(TEST_MODE)).length).toBeGreaterThanOrEqual(2);
    billing = { ...FREE_ON, billing_mode: "live" };
    const live = renderBilling();
    await waitFor(() => expect(within(live.container).getAllByRole("button", { name: "Continue to payment" })).toHaveLength(2));
    expect(within(live.container).queryByText(TEST_MODE)).toBeNull();
  });

  it("offers only what the server sells: no Pro Plus while the assistant is off, with the reason; Pro is still offered", async () => {
    billing = { ...FREE_ON, checkout_plans: ["pro"] };
    assistant = AI_OFF;
    renderBilling();
    const pro = (await screen.findByRole("article", { name: "DoMe Pro" })) as HTMLElement;
    expect(within(pro).getByRole("button", { name: "Continue to payment" })).toBeEnabled();
    expect(screen.queryByRole("article", { name: "DoMe Pro Plus" })).toBeNull();
    expect(screen.getByTestId("pro-plus-not-sold")).toHaveTextContent(PRO_PLUS_NOT_SOLD);
    expect(screen.getAllByRole("button", { name: "Continue to payment" })).toHaveLength(1);
  });

  it("a refused Pro Plus or a page still being confirmed reads the status again and says why, never opening a second page", async () => {
    billing = FREE_ON;
    const user = userEvent.setup();
    for (const [code, text] of [
      ["PLAN_NOT_AVAILABLE", /can't be bought yet/],
      ["CHECKOUT_PROCESSING", /being confirmed/],
    ] as const) {
      respond["POST /v1/billing/checkout"] = error(code === "PLAN_NOT_AVAILABLE" ? 403 : 409, code);
      const before = calls.filter((c) => c.url === "/v1/account/billing").length;
      const view = renderBilling();
      const plus = (await within(view.container).findByRole("article", { name: "DoMe Pro Plus" })) as HTMLElement;
      await user.click(within(plus).getByRole("button", { name: "Continue to payment" }));
      expect(await within(view.container).findByText(text)).toBeInTheDocument();
      await waitFor(() => expect(calls.filter((c) => c.url === "/v1/account/billing").length).toBeGreaterThanOrEqual(before + 2));
      expect(assign).not.toHaveBeenCalled();
      view.unmount();
    }
  });

  it("free with checkout shows four purchase paths and redirects to the checkout_url; the AI line says when AI is not switched on", async () => {
    billing = FREE_ON;
    assistant = AI_OFF;
    const user = userEvent.setup();
    renderBilling();
    const pro = (await screen.findByRole("article", { name: "DoMe Pro" })) as HTMLElement;
    const proPlus = screen.getByRole("article", { name: "DoMe Pro Plus" });
    expect(pro).toHaveTextContent("$9.99 / month");
    expect(proPlus).toHaveTextContent("$24.99 / month");
    expect(pro).toHaveTextContent("30 requests (about 15 minutes) per 5-hour window");
    expect(within(pro).getByText(NOT_SWITCHED_ON)).toBeInTheDocument();
    expect(within(proPlus).getByText(NOT_SWITCHED_ON)).toBeInTheDocument();
    expect(screen.getByText(/You'll finish on Stripe's secure page\. Card details never touch DoMe\./)).toBeInTheDocument();
    // the founder's prices are tax-inclusive: the amount shown is the amount charged
    expect(screen.getByText(/Prices in US dollars, tax included\./)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/before tax/);
    expect(screen.queryByText("Upgrading is not available yet")).toBeNull();

    await user.click(screen.getByRole("tab", { name: "Yearly" }));
    expect(pro).toHaveTextContent("$99.99 / year");
    expect(proPlus).toHaveTextContent("$249.99 / year");
    await user.click(within(proPlus).getByRole("button", { name: "Continue to payment" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(CHECKOUT_URL));
    const checkout = calls.find((c) => c.url === "/v1/billing/checkout")!;
    expect(checkout.method).toBe("POST");
    expect(checkout.body).toEqual({ plan: "pro_plus", interval: "annual" });
    expect(checkout.csrf).toBe(SESSION.csrf_token);
    // all four paths stay disabled while the browser leaves for Stripe
    for (const b of screen.getAllByRole("button", { name: "Continue to payment" })) expect(b).toBeDisabled();

    assign.mockClear();
    const monthly = renderBilling();
    await waitFor(() => expect(within(monthly.container).getAllByRole("button", { name: "Continue to payment" })).toHaveLength(2));
    await user.click(within(within(monthly.container).getByRole("article", { name: "DoMe Pro" })).getByRole("button", { name: "Continue to payment" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(CHECKOUT_URL));
    expect(calls.filter((c) => c.url === "/v1/billing/checkout").at(-1)!.body).toEqual({ plan: "pro", interval: "monthly" });
    // the plan shown never changed: the server was not asked to say anything else
    expect(within(monthly.container).getByText("DoMe Free")).toBeInTheDocument();
  });

  it("a checkout_url that is not https is never navigated to", async () => {
    billing = FREE_ON;
    respond["POST /v1/billing/checkout"] = () => jsonResponse(200, { kind: "subscription", checkout_url: "https://checkout.stripe.test/ok", session_id: "cs_1", expires_at: "2026-10-21T12:00:00.000Z" });
    const user = userEvent.setup();
    renderBilling();
    const pro = (await screen.findByRole("article", { name: "DoMe Pro" })) as HTMLElement;
    // the contract refuses a non-https URL before the page sees it: the schema pins ^https://
    respond["POST /v1/billing/checkout"] = () => jsonResponse(200, { kind: "subscription", checkout_url: "http://evil.test/", session_id: "cs_1", expires_at: "2026-10-21T12:00:00.000Z" });
    await user.click(within(pro).getByRole("button", { name: "Continue to payment" }));
    expect(await screen.findByText(ERRORS.MALFORMED_MESSAGE!.user_message)).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("errors render the contract's messages and never claim a purchase", async () => {
    billing = FREE_ON;
    const user = userEvent.setup();
    for (const [code, status, text] of [
      ["BILLING_NOT_AVAILABLE", 503, "Payments aren't available yet. Nothing was charged."],
      ["RATE_LIMITED", 429, "Too many requests. Wait a moment and try again."],
      ["BILLING_PROVIDER_ERROR", 502, "Our payment provider didn't answer. Nothing was charged. Try again in a moment."],
    ] as const) {
      respond["POST /v1/billing/checkout"] = error(status, code);
      const r = renderBilling();
      const pro = (await within(r.container).findByRole("article", { name: "DoMe Pro" })) as HTMLElement;
      await user.click(within(pro).getByRole("button", { name: "Continue to payment" }));
      expect(await within(r.container).findByText(text)).toBeInTheDocument();
      expect(within(r.container).getByText("DoMe Free")).toBeInTheDocument();
      expect(within(r.container).getByText("Free")).toBeInTheDocument();
      expect(assign).not.toHaveBeenCalled();
      r.unmount();
    }
    // ALREADY_SUBSCRIBED: the status is read again and the page follows what the server now says
    respond["POST /v1/billing/checkout"] = () => {
      billing = PRO_ON;
      return error(409, "ALREADY_SUBSCRIBED")();
    };
    renderBilling();
    const pro = (await screen.findByRole("article", { name: "DoMe Pro" })) as HTMLElement;
    await user.click(within(pro).getByRole("button", { name: "Continue to payment" }));
    expect(await screen.findByText("You already have a subscription. Manage it from Billing.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Manage subscription" })).toBeInTheDocument();
  });

  it("return URL shows pending and polls the session, never unlocking by itself", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    billing = { ...FREE_ON, entitlement_state: "pending", checkout_available: false, subscription: { ...SUB, state: "checkout_pending", current_period_end: null, checkout_expires_at: "2026-10-21T12:00:00.000Z" } };
    renderBilling("/app/billing?checkout=return&session_id=cs_test_a1b2c3");
    expect(await screen.findByText("Pending activation", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText(/nothing is unlocked before the payment is verified/)).toBeInTheDocument();
    // the query is informational only and is dropped from the URL
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/app/billing"));
    expect(screen.getByTestId("location").textContent).not.toContain("session_id");
    expect(screen.getByText("DoMe Free")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue to payment" })).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS + 10);
    });
    expect(calls.filter((c) => c.url === "/v1/session")).toHaveLength(1);
    expect(calls.filter((c) => c.url === "/v1/account/billing")).toHaveLength(2);
    // still Free until the server says otherwise
    expect(screen.getByText("DoMe Free")).toBeInTheDocument();
    expect(screen.getAllByText("Pending activation").length).toBeGreaterThan(0);

    // the webhook landed: the next poll sees Pro active and the page says so
    billing = PRO_ON;
    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS + 10);
    });
    expect(await screen.findByText("Payment verified")).toBeInTheDocument();
    expect(screen.getByText("DoMe Pro is active on your account.")).toBeInTheDocument();
    expect(screen.getByText("DoMe Pro")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    const polls = calls.filter((c) => c.url === "/v1/session").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS * 3);
    });
    expect(calls.filter((c) => c.url === "/v1/session")).toHaveLength(polls); // polling stopped
  });

  it("after two minutes of pending the page says it is still confirming and stops polling; unmounting clears the timer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    billing = { ...FREE_ON, entitlement_state: "pending", checkout_available: false };
    const { unmount } = renderBilling("/app/billing?checkout=return");
    expect(await screen.findByText("Pending activation", { selector: "p" })).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS * 40 + 50);
    });
    expect(await screen.findByText(STILL_CONFIRMING)).toBeInTheDocument();
    const polls = calls.filter((c) => c.url === "/v1/session").length;
    expect(polls).toBe(40);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS * 3);
    });
    expect(calls.filter((c) => c.url === "/v1/session")).toHaveLength(polls);
    expect(screen.getByText("DoMe Free")).toBeInTheDocument();

    const second = renderBilling("/app/billing?checkout=return");
    await within(second.container).findByText("Pending activation", { selector: "p" });
    const before = calls.length;
    unmount();
    second.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS * 3);
    });
    expect(calls.length).toBe(before);
  });

  it("cancel return says nothing was charged", async () => {
    billing = FREE_ON;
    renderBilling("/app/billing?checkout=cancel");
    expect(await screen.findByText("Checkout cancelled. Nothing was charged.")).toBeInTheDocument();
    expect(await screen.findAllByRole("button", { name: "Continue to payment" })).toHaveLength(2);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/app\/billing$/));
  });

  it("paid shows renew/end/grace copy and the portal button", async () => {
    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    billing = PRO_ON;
    assistant = { ...AI_ON, allowance: WINDOWS };
    const user = userEvent.setup();
    const active = renderBilling();
    expect(await screen.findByRole("button", { name: "Manage subscription" })).toBeInTheDocument();
    expect(screen.getByText("Your DoMe Pro subscription is active.")).toBeInTheDocument();
    expect(screen.getByTestId("period-end")).toHaveTextContent(/^Renews 20 November 2026 · billed monthly$/);
    expect(screen.queryByRole("button", { name: "Continue to payment" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Manage subscription" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(PORTAL_URL));
    const portal = calls.find((c) => c.url === "/v1/billing/portal")!;
    expect(portal.method).toBe("POST");
    expect(portal.body).toEqual({});
    expect(portal.csrf).toBe(SESSION.csrf_token);
    active.unmount();

    billing = { ...PRO_ON, entitlement_state: "canceling", subscription: { ...SUB, state: "cancel_scheduled", cancel_at_period_end: true, interval: "annual" } };
    const ending = renderBilling();
    expect(await screen.findByText("Ends at period end")).toBeInTheDocument();
    expect(screen.getByTestId("period-end")).toHaveTextContent(/^Ends 20 November 2026 · billed yearly$/);
    expect(ending.container.textContent).toContain("DoMe Pro stays on until 20 November 2026. Then your account returns to Free: 1 PC and 2 phones stay enabled");
    ending.unmount();

    billing = { ...PRO_ON, entitlement_state: "past_due", subscription: { ...SUB, state: "past_due", grace_until: "2026-11-27T12:00:00.000Z" } };
    const pastDue = renderBilling();
    expect(await screen.findByText("Payment problem")).toBeInTheDocument();
    expect(pastDue.container.textContent).toContain("Update your card by 27 November 2026 to keep DoMe Pro.");
    expect(screen.getByTestId("grace-until")).toHaveTextContent("Paid features stay until 27 November 2026.");
    expect(screen.getByRole("list")).toHaveTextContent("Manage subscription");
    pastDue.unmount();

    billing = { ...PRO_ON, portal_available: false };
    respond["POST /v1/billing/portal"] = error(404, "BILLING_NO_CUSTOMER");
    renderBilling();
    await billingLoaded();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Manage subscription" })).toBeNull());
  });

  it("the portal's 404 renders the contract's message", async () => {
    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    billing = PRO_ON;
    respond["POST /v1/billing/portal"] = error(404, "BILLING_NO_CUSTOMER");
    const user = userEvent.setup();
    renderBilling();
    await user.click(await screen.findByRole("button", { name: "Manage subscription" }));
    expect(await screen.findByText("There is no subscription to manage yet.")).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("the AI card: the allowance line, progress bars that warn from 80 %, and the top-up button only when the server allows it", async () => {
    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    billing = PRO_ON;
    assistant = { ...AI_ON, allowance: WINDOWS };
    const user = userEvent.setup();
    renderBilling();
    expect(await screen.findByRole("heading", { name: "AI requests" })).toBeInTheDocument();
    expect(screen.getByText(/^24 of 30 used in this 5-hour window · 30 of 120 this week · refreshes /)).toBeInTheDocument();
    const window = screen.getByRole("progressbar", { name: "This 5-hour window" });
    expect(window).toHaveAttribute("aria-valuenow", "24");
    expect(window).toHaveAttribute("aria-valuemax", "30");
    expect(window.firstElementChild).toHaveClass("bg-warning");
    const week = screen.getByRole("progressbar", { name: "This week" });
    expect(week.firstElementChild).toHaveClass("bg-success");
    expect(week.parentElement).toHaveTextContent(/Refreshes /);
    const topup = screen.getByRole("button", { name: "Add 100 AI requests for $2.99" });
    expect(screen.getByText(/You choose when; nothing is added or charged automatically\./)).toBeInTheDocument();
    await user.click(topup);
    await waitFor(() => expect(assign).toHaveBeenCalledWith(CHECKOUT_URL));
    const call = calls.find((c) => c.url === "/v1/billing/topup")!;
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({});
    expect(call.csrf).toBe(SESSION.csrf_token);
  });

  it("no top-up button when the server says not allowed; a kept balance on Free is explained; TOPUP errors render the contract's message", async () => {
    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    billing = { ...PRO_ON, topup: { ...PRO_ON.topup, allowed: false } };
    assistant = { ...AI_ON, allowance: { ...WINDOWS, topup: { balance: 0, allowed: false } } };
    const notAllowed = renderBilling();
    expect(await screen.findByRole("heading", { name: "AI requests" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add 100 AI requests/ })).toBeNull();
    notAllowed.unmount();

    session = SESSION;
    billing = { ...FREE_ON, topup: { allowed: false, balance_requests: 40, requests: 100, price_cents: 299 } };
    assistant = { ...AI_ON, allowance: { ...TASTE, topup: { balance: 40, allowed: false } } };
    const kept = renderBilling();
    expect(await screen.findByTestId("topup-balance")).toHaveTextContent("Your 40 top-up requests are kept and usable again on Pro or Pro Plus.");
    expect(screen.queryByRole("button", { name: /Add 100 AI requests/ })).toBeNull();
    kept.unmount();

    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    billing = { ...PRO_ON, topup: { ...PRO_ON.topup, balance_requests: 1000 } };
    assistant = { ...AI_ON, allowance: { ...WINDOWS, topup: { balance: 1000, allowed: true } } };
    respond["POST /v1/billing/topup"] = error(409, "TOPUP_BALANCE_LIMIT");
    const user = userEvent.setup();
    renderBilling();
    expect(await screen.findByTestId("topup-balance")).toHaveTextContent("1000 top-up requests available (used after your window or week runs out).");
    await user.click(screen.getByRole("button", { name: "Add 100 AI requests for $2.99" }));
    expect(await screen.findByText("You already have the most top-up requests an account can hold. Use some first.")).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("top-up button only when allowed; purchase redirects; after return polls the allowance until the balance grew", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    session = { ...SESSION, plan: "pro", entitlement_state: "active", limits: PRO_LIMITS };
    billing = PRO_ON;
    assistant = { ...AI_ON, allowance: WINDOWS };
    renderBilling("/app/billing?topup=return&session_id=cs_test_topup1");
    expect(await screen.findByText("Top-up pending — confirming the payment")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/app\/billing$/));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS + 10);
    });
    // the first poll fixes the baseline; the balance has not moved yet
    expect(screen.getByText("Top-up pending — confirming the payment")).toBeInTheDocument();
    assistant = { ...AI_ON, allowance: { ...WINDOWS, topup: { balance: 100, allowed: true } } };
    billing = { ...PRO_ON, topup: { ...PRO_ON.topup, balance_requests: 100 } };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS + 10);
    });
    expect(await screen.findByText("Top-up added: 100 top-up requests available.")).toBeInTheDocument();
    expect(await screen.findByText(/· 100 top-up requests available$/)).toBeInTheDocument();
    const polls = calls.filter((c) => c.url === "/v1/assistant").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RETURN_POLL_MS * 3);
    });
    expect(calls.filter((c) => c.url === "/v1/assistant")).toHaveLength(polls);
  });

  it("downgrade selection enforces limits and saves; closed window points to Devices", async () => {
    billing = { ...FREE_ON, checkout_available: true, selection: { open: true, deadline: "2026-11-03T12:00:00.000Z" } };
    const user = userEvent.setup();
    let saved: unknown;
    respond["PUT /v1/account/plan-selection"] = () => {
      saved = calls.at(-1)!.body;
      return jsonResponse(200, { ...selection, pcs: selection.pcs.map((p) => ({ ...p, selected: p.id === OTHER })) });
    };
    renderBilling();
    expect(await screen.findByRole("heading", { name: "Choose which devices stay enabled" })).toBeInTheDocument();
    const office = await screen.findByRole("checkbox", { name: /Office PC/ });
    expect(screen.getByText(/Your plan now allows 1 PC and 2 phones\. Choose by 3 November 2026/)).toBeInTheDocument();
    expect(screen.getByText(/^PCs · 1 of 1$/)).toBeInTheDocument();
    expect(screen.getByText(/^Phones · 2 of 2$/)).toBeInTheDocument();
    const den = screen.getByRole("checkbox", { name: /Den PC/ });
    expect(office).toBeChecked();
    expect(den).toBeDisabled(); // 1 of 1 PCs chosen
    expect(screen.getByRole("checkbox", { name: /Old phone/ })).toBeDisabled(); // 2 of 2 phones chosen
    await user.click(office);
    expect(den).toBeEnabled();
    await user.click(den);
    expect(office).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/^Saved\./)).toBeInTheDocument();
    expect(saved).toEqual({ pc_ids: [OTHER], controller_ids: [PHONE_A, PHONE_B] });
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/v1/account/plan-selection");
    expect(put.csrf).toBe(SESSION.csrf_token);
    // the session is read again so the limits card follows the server
    expect(calls.filter((c) => c.url === "/v1/session").length).toBeGreaterThan(0);
  });

  it("PLAN_SELECTION_CLOSED and _INVALID render the contract's messages; after the window one line points to Devices", async () => {
    billing = { ...FREE_ON, selection: { open: true, deadline: "2026-11-03T12:00:00.000Z" } };
    respond["PUT /v1/account/plan-selection"] = error(409, "PLAN_SELECTION_CLOSED");
    const user = userEvent.setup();
    const closed = renderBilling();
    await user.click(await screen.findByRole("button", { name: "Save" }));
    expect(await screen.findByText("The time to choose here has passed. Enable and disable devices under Devices instead.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Devices" })).toHaveAttribute("href", "/app/devices");
    closed.unmount();

    respond["PUT /v1/account/plan-selection"] = error(400, "PLAN_SELECTION_INVALID");
    const invalid = renderBilling();
    await user.click(await screen.findByRole("button", { name: "Save" }));
    expect(await screen.findByText("That choice doesn't fit your plan's limits or names a device that isn't yours.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Devices" })).toBeNull();
    invalid.unmount();

    billing = { ...FREE_ON, selection: { open: false, deadline: "2026-10-01T12:00:00.000Z" } };
    renderBilling();
    expect(await screen.findByText("The default (most recently used) selection applies; change it under Devices.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Choose which devices stay enabled" })).toBeNull();
  });

  it("renders nothing without a session", () => {
    useSessionStore.setState({ status: "signed_out", session: null, error: null });
    const { container } = render(
      <MemoryRouter>
        <BillingPage />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

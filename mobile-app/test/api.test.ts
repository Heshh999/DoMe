import { afterEach, describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import { api, ApiError, apiRequest, configureApi, loginUrl } from "../src/lib/api.ts";

const UUID = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:00.000Z";
const session = { account: { id: UUID, email: "a@example.test", display_name: "A", created_at: TS }, csrf_token: "C".repeat(40), plan: "free", limits: { max_enabled_pcs: 1, max_controllers: 2, routines: false, custom_layouts: false }, protocol_version: "1.1" };

function respond(status: number, body: unknown) {
  return new Response(body === undefined ? null : typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => configureApi({ csrfToken: null, accountId: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null }));

describe("REST client", () => {
  it("validates responses against rest.schema.json and rejects anything else", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    configureApi({
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return respond(200, session);
      },
    });
    const s = await api.session();
    expect(s.csrf_token).toBe("C".repeat(40));
    expect(calls[0]!.url).toBe("/v1/session");
    expect(calls[0]!.init.credentials).toBe("include");
    expect(calls[0]!.init.cache).toBe("no-store");

    configureApi({ fetchImpl: async () => respond(200, { ...session, plan: "gold" }) });
    await expect(api.session()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    configureApi({ fetchImpl: async () => respond(200, { ...session, extra: 1 }) });
    await expect(api.session()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    configureApi({ fetchImpl: async () => respond(200, '{"account":1,"account":2}') });
    await expect(api.session()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
  });

  it("state-changing requests need the CSRF token and carry it as a header; the request body is validated too", async () => {
    await expect(api.logout()).rejects.toMatchObject({ code: "UNAUTHENTICATED" });
    const calls: Array<{ url: string; init: RequestInit }> = [];
    configureApi({
      csrfToken: "tok",
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return respond(200, { id: UUID, name: "Office", enabled: true, connection: "online", last_seen: null, created_at: TS, platform: "windows" });
      },
    });
    await api.patchPc(UUID, { name: "Office" });
    expect((calls[0]!.init.headers as Record<string, string>)["X-DoMe-CSRF"]).toBe("tok");
    expect(calls[0]!.init.method).toBe("PATCH");
    await expect(api.patchPc(UUID, { name: "" } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    expect(calls).toHaveLength(1);
  });

  it("maps error bodies to ApiError and triggers onUnauthenticated on 401", async () => {
    let unauth = 0;
    configureApi({ fetchImpl: async () => respond(401, { error: { code: "UNAUTHENTICATED", message: "Sign in", retryable: false } }), onUnauthenticated: () => void unauth++ });
    await expect(api.pcs()).rejects.toMatchObject({ status: 401, code: "UNAUTHENTICATED" });
    expect(unauth).toBe(1);
    configureApi({ fetchImpl: async () => respond(400, { error: { code: "PAIRING_CODE_INVALID", message: "nope", retryable: false } }) });
    await expect(api.pairingStatus(UUID)).rejects.toMatchObject({ status: 400, code: "PAIRING_CODE_INVALID", retryable: false });
    configureApi({ fetchImpl: async () => respond(502, "<html>bad gateway</html>") });
    await expect(api.pcs()).rejects.toMatchObject({ status: 502, code: "INTERNAL", retryable: true });
    configureApi({
      fetchImpl: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const e = await api.pcs().catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).code).toBe("NETWORK");
  });

  it("a FORBIDDEN from a stale CSRF token (signed in again elsewhere) refreshes the token and retries once, for the same account only", async () => {
    const forbidden = () => respond(403, { error: { code: "FORBIDDEN", message: "Missing or invalid CSRF token", retryable: false } });
    const pc = { id: UUID, name: "Office", enabled: true, connection: "online", last_seen: null, created_at: TS, platform: "windows" };
    const fresh = "F".repeat(40);
    let calls: Array<{ url: string; method: string; csrf: string | undefined }> = [];
    const server = (current: { token: string; account: string }) => async (url: string, init: RequestInit) => {
      const csrf = (init.headers as Record<string, string>)["X-DoMe-CSRF"];
      calls.push({ url, method: init.method ?? "GET", csrf });
      if (url.endsWith("/v1/session")) return respond(200, { ...session, csrf_token: current.token, account: { ...session.account, id: current.account } });
      return csrf === current.token ? respond(200, pc) : forbidden();
    };

    // same account, new token: one session fetch, one retry with the fresh token
    configureApi({ csrfToken: "stale-token", accountId: UUID, fetchImpl: server({ token: fresh, account: UUID }) });
    await expect(api.patchPc(UUID, { name: "Office" })).resolves.toMatchObject({ id: UUID });
    expect(calls.map((c) => `${c.method} ${c.url.replace(/^.*\/v1/, "/v1")} ${c.csrf ?? "-"}`)).toEqual([
      `PATCH /v1/pcs/${UUID} stale-token`,
      "GET /v1/session -",
      `PATCH /v1/pcs/${UUID} ${fresh}`,
    ]);
    calls = [];
    await api.patchPc(UUID, { name: "Office" }); // the fresh token is kept for later requests
    expect(calls).toHaveLength(1);

    // signed in as ANOTHER account elsewhere: never acts for it, the refusal stands
    calls = [];
    const other = "44444444-4444-4444-8444-444444444444";
    configureApi({ csrfToken: "stale-token", accountId: UUID, fetchImpl: server({ token: fresh, account: other }) });
    await expect(api.patchPc(UUID, { name: "Office" })).rejects.toMatchObject({ status: 403, code: "FORBIDDEN" });
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);

    // refused again with the fresh token (e.g. a wrong origin): exactly one retry, then the error
    calls = [];
    configureApi({ csrfToken: "stale-token", accountId: UUID, fetchImpl: async (url, init) => {
      calls.push({ url, method: init.method ?? "GET", csrf: undefined });
      return url.endsWith("/v1/session") ? respond(200, { ...session, csrf_token: fresh }) : forbidden();
    } });
    await expect(api.patchPc(UUID, { name: "Office" })).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2);

    // GET requests are never retried this way
    calls = [];
    configureApi({ fetchImpl: async (url, init) => {
      calls.push({ url, method: init.method ?? "GET", csrf: undefined });
      return forbidden();
    } });
    await expect(api.pcs()).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(calls).toHaveLength(1);
  });

  it("204 responses return undefined and path segments are encoded", async () => {
    const calls: string[] = [];
    configureApi({
      csrfToken: "tok",
      fetchImpl: async (url) => {
        calls.push(url);
        return new Response(null, { status: 204 });
      },
    });
    await expect(apiRequest<void>("DELETE", `/v1/pcs/${encodeURIComponent("a/b c")}`, { responseBody: null })).resolves.toBeUndefined();
    expect(calls[0]).toBe("/v1/pcs/a%2Fb%20c");
  });

  it("loginUrl never forwards a fragment (the pairing deep link carries the code there)", () => {
    const url = loginUrl("/app/devices/pair#code=ABCDE-FGHJK-MNPQR-STVWX");
    expect(url).toBe("/v1/auth/login?return_to=%2Fapp%2Fdevices%2Fpair");
    expect(url).not.toMatch(/code=/i);
    expect(url).not.toMatch(/ABCDE|%23|#/);
    expect(loginUrl("/app/remote?x=1#frag")).toBe("/v1/auth/login?return_to=%2Fapp%2Fremote%3Fx%3D1");
    expect(loginUrl("#code=ABCDE-FGHJK-MNPQR-STVWX")).toBe("/v1/auth/login?return_to=%2Fapp");
  });

  it("loginUrl only accepts same-origin relative return paths", () => {
    expect(loginUrl("/app/remote")).toBe("/v1/auth/login?return_to=%2Fapp%2Fremote");
    expect(loginUrl("//evil.example")).toBe("/v1/auth/login?return_to=%2Fapp");
    expect(loginUrl("https://evil.example")).toBe("/v1/auth/login?return_to=%2Fapp");
  });
});

describe("assistant endpoints (docs/design/assistant.md §1.2)", () => {
  const ALLOWANCE = { kind: "windows", taste: null, window: { limit: 300, used: 5, starts_at: TS, ends_at: TS }, week: { limit: 1200, used: 5, starts_at: TS, ends_at: TS }, topup: { balance: 0, allowed: true }, exhausted: null, resets_at: null, warn_at_percent: 80 };
  const STATUS = { available: true, unavailable_reason: null, provider: "fake", turn_deadline_seconds: 20, consent_version: "2026-10-11", consent: null, allowance: ALLOWANCE };
  const TURN: rest.AssistantTurnRequest = { turn_id: UUID, source: "speech", text: "it's a little quiet in here", recent: [], context: { persona: { name: "DoMe" }, pc_connection: "online", playback: { youtube: "none", media: "none" } } };
  const REPLY = { turn_id: UUID, mode: "act", reply: "", choices: [], handoff: "none", steps: [{ action: "windows.set_volume", params: { value: 50 } }], charged: true, charged_from: "window", allowance: { ...ALLOWANCE, window: { ...ALLOWANCE.window, used: 6 } } };

  function server(status: number, body: unknown) {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    configureApi({
      csrfToken: "tok",
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return status === 204 ? new Response(null, { status }) : respond(status, body);
      },
    });
    return calls;
  }

  it("GET /v1/assistant validates the status", async () => {
    const calls = server(200, STATUS);
    await expect(api.assistantStatus()).resolves.toMatchObject({ available: true, consent_version: "2026-10-11" });
    expect(calls[0]!.url).toBe("/v1/assistant");
    server(200, { ...STATUS, provider: "openai" });
    await expect(api.assistantStatus()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
  });

  it("POST /v1/assistant/consent sends the pinned version with CSRF; anything else is refused before sending", async () => {
    const calls = server(200, { ...STATUS, consent: { version: "2026-10-11", given_at: TS } });
    await api.assistantConsent({ version: "2026-10-11", accept: true });
    expect(calls[0]!.init.method).toBe("POST");
    expect((calls[0]!.init.headers as Record<string, string>)["X-DoMe-CSRF"]).toBe("tok");
    expect(JSON.parse(calls[0]!.init.body as string)).toEqual({ version: "2026-10-11", accept: true });
    await expect(api.assistantConsent({ version: "2026-01-01", accept: true } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    await expect(api.assistantConsent({ version: "2026-10-11", accept: false } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    expect(calls).toHaveLength(1);
  });

  it("DELETE /v1/assistant/consent withdraws with CSRF", async () => {
    const calls = server(204, null);
    await expect(api.assistantWithdraw()).resolves.toBeUndefined();
    expect(calls[0]!.init.method).toBe("DELETE");
    expect((calls[0]!.init.headers as Record<string, string>)["X-DoMe-CSRF"]).toBe("tok");
  });

  it("POST /v1/assistant/turn validates the request and the response, and passes the abort signal", async () => {
    const calls = server(200, REPLY);
    const abort = new AbortController();
    await expect(api.assistantTurn(TURN, abort.signal)).resolves.toMatchObject({ mode: "act", charged: true });
    expect(calls[0]!.init.signal).toBe(abort.signal);
    expect(JSON.parse(calls[0]!.init.body as string)).toEqual(TURN);
    // an extra field (a title, say) never leaves the phone
    await expect(api.assistantTurn({ ...TURN, context: { ...TURN.context, title: "x" } } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    await expect(api.assistantTurn({ ...TURN, text: "a\u202eb" })).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    expect(calls).toHaveLength(1);
    server(200, { ...REPLY, steps: [REPLY.steps[0], REPLY.steps[0], REPLY.steps[0], REPLY.steps[0]] });
    await expect(api.assistantTurn(TURN)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    server(200, { ...REPLY, reply: "bad\u202e" });
    await expect(api.assistantTurn(TURN)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    // protocol 1.3: charged_from is required, and the allowance names its windows, taste, top-up and what is exhausted
    const withoutSource: Record<string, unknown> = { ...REPLY };
    delete withoutSource.charged_from;
    server(200, withoutSource);
    await expect(api.assistantTurn(TURN)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    server(200, { ...REPLY, charged: false, charged_from: null, allowance: { ...ALLOWANCE, exhausted: "window", resets_at: TS } });
    await expect(api.assistantTurn(TURN)).resolves.toMatchObject({ charged: false, charged_from: null });
    server(200, { ...REPLY, allowance: { ...ALLOWANCE, exhausted: "requests" } });
    await expect(api.assistantTurn(TURN)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    server(200, { ...REPLY, allowance: { limit: 300, used: 6, period_start: TS, period_end: TS, exhausted: null } });
    await expect(api.assistantTurn(TURN)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
  });

  it.each([
    [429, "AI_ALLOWANCE_EXHAUSTED", { resets_at: "2026-11-01T00:00:00Z", reason: "window" }],
    [429, "AI_ALLOWANCE_EXHAUSTED", { resets_at: null, reason: "taste" }],
    [502, "AI_UNAVAILABLE", { reason: "timeout" }],
    [503, "AI_SPEND_LIMIT_REACHED", undefined],
    [409, "AI_BUSY", undefined],
    [422, "AI_DECLINED", undefined],
    [403, "AI_CONSENT_REQUIRED", undefined],
  ])("an error body %i %s keeps its code and detail", async (status, code, detail) => {
    server(status, { error: { code, message: "m", retryable: false, ...(detail ? { detail } : {}) } });
    const e = await api.assistantTurn(TURN).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ status, code, detail });
  });

  it("an aborted turn rejects with the AbortError (Cancel), not a network error", async () => {
    configureApi({
      csrfToken: "tok",
      fetchImpl: (_url, init) => new Promise((_r, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))),
    });
    const abort = new AbortController();
    const p = api.assistantTurn(TURN, abort.signal);
    abort.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("billing endpoints (docs/BILLING.md; protocol 1.3)", () => {
  const PRICING = { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } };
  const STATUS = { billing_enabled: true, billing_mode: "test", plan: "pro", entitlement_state: "active", subscription: { state: "active", plan: "pro", interval: "monthly", current_period_end: TS, cancel_at_period_end: false, grace_until: null, checkout_expires_at: null }, checkout_available: false, checkout_plans: [], portal_available: true, pricing: PRICING, topup: { allowed: true, balance_requests: 0, requests: 100, price_cents: 299 }, selection: null };
  const CHECKOUT = { kind: "subscription", checkout_url: "https://checkout.stripe.test/c/pay/cs_test_1", session_id: "cs_test_1", expires_at: TS };

  it("GET /v1/account/billing validates the status body; pro_plus is a plan", async () => {
    configureApi({ fetchImpl: async () => respond(200, STATUS) });
    expect((await api.billing()).portal_available).toBe(true);
    configureApi({ fetchImpl: async () => respond(200, { ...STATUS, plan: "pro_plus", subscription: { ...STATUS.subscription, plan: "pro_plus", state: "cancel_scheduled", cancel_at_period_end: true } }) });
    expect((await api.billing()).plan).toBe("pro_plus");
    configureApi({ fetchImpl: async () => respond(200, { ...STATUS, plan: "gold" }) });
    await expect(api.billing()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    configureApi({ fetchImpl: async () => respond(200, { ...STATUS, subscription: { ...STATUS.subscription, state: "paused" } }) });
    await expect(api.billing()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    configureApi({ fetchImpl: async () => respond(200, { ...STATUS, extra: true }) });
    await expect(api.billing()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
  });

  it("POST /v1/billing/checkout validates the request before sending and the hosted-page answer after; the URL must be https", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    configureApi({
      csrfToken: "tok",
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return respond(200, CHECKOUT);
      },
    });
    const r = await api.billingCheckout({ plan: "pro_plus", interval: "annual" });
    expect(r.checkout_url).toBe(CHECKOUT.checkout_url);
    expect(calls[0]!.url).toBe("/v1/billing/checkout");
    expect(calls[0]!.init.method).toBe("POST");
    expect((calls[0]!.init.headers as Record<string, string>)["X-DoMe-CSRF"]).toBe("tok");
    expect(JSON.parse(calls[0]!.init.body as string)).toEqual({ plan: "pro_plus", interval: "annual" });
    await expect(api.billingCheckout({ plan: "free", interval: "monthly" } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    await expect(api.billingCheckout({ plan: "pro", interval: "weekly" } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    expect(calls).toHaveLength(1);
    configureApi({ csrfToken: "tok", fetchImpl: async () => respond(200, { ...CHECKOUT, checkout_url: "http://checkout.stripe.test/c/pay/cs_test_1" }) });
    await expect(api.billingCheckout({ plan: "pro", interval: "monthly" })).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    configureApi({ csrfToken: "tok", fetchImpl: async () => respond(503, { error: { code: "BILLING_NOT_AVAILABLE", message: "Payments aren't available yet. Nothing was charged.", retryable: false } }) });
    await expect(api.billingCheckout({ plan: "pro", interval: "monthly" })).rejects.toMatchObject({ status: 503, code: "BILLING_NOT_AVAILABLE" });
  });

  it("POST /v1/billing/portal and /topup send an empty JSON object with CSRF and validate their answers", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    configureApi({
      csrfToken: "tok",
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return url.endsWith("/portal") ? respond(200, { portal_url: "https://billing.stripe.test/p/session/x" }) : respond(200, { ...CHECKOUT, kind: "topup" });
      },
    });
    expect((await api.billingPortal()).portal_url).toBe("https://billing.stripe.test/p/session/x");
    expect((await api.billingTopup()).kind).toBe("topup");
    for (const c of calls) {
      expect(c.init.method).toBe("POST");
      expect(c.init.body).toBe("{}");
      expect((c.init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
      expect((c.init.headers as Record<string, string>)["X-DoMe-CSRF"]).toBe("tok");
    }
    expect(calls.map((c) => c.url)).toEqual(["/v1/billing/portal", "/v1/billing/topup"]);
    configureApi({ csrfToken: "tok", fetchImpl: async () => respond(200, { portal_url: "http://billing.stripe.test/p" }) });
    await expect(api.billingPortal()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    configureApi({ csrfToken: null });
    await expect(api.billingTopup()).rejects.toMatchObject({ code: "UNAUTHENTICATED" });
  });

  it("GET and PUT /v1/account/plan-selection validate both ways; PUT needs CSRF", async () => {
    const SELECTION = { open: true, deadline: TS, plan: "free", limits: { max_enabled_pcs: 1, max_controllers: 2 }, pcs: [{ id: UUID, name: "Office PC", selected: true, last_seen: TS }], controllers: [{ id: UUID, display_name: "Phone", selected: true, last_seen: null }] };
    const calls: Array<{ url: string; init: RequestInit }> = [];
    configureApi({
      csrfToken: "tok",
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return respond(200, SELECTION);
      },
    });
    expect((await api.planSelection()).pcs[0]!.name).toBe("Office PC");
    const put = await api.putPlanSelection({ pc_ids: [UUID], controller_ids: [UUID] });
    expect(put.open).toBe(true);
    expect(calls[1]!.init.method).toBe("PUT");
    expect((calls[1]!.init.headers as Record<string, string>)["X-DoMe-CSRF"]).toBe("tok");
    await expect(api.putPlanSelection({ pc_ids: ["not-a-uuid"], controller_ids: [] } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    await expect(api.putPlanSelection({ pc_ids: [UUID, UUID], controller_ids: [] } as never)).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
    expect(calls).toHaveLength(2);
    configureApi({ csrfToken: "tok", fetchImpl: async () => respond(200, { ...SELECTION, plan: "gold" }) });
    await expect(api.planSelection()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
  });

  it("GET /v1/plans carries the pricing shape and the billing mode", async () => {
    configureApi({ fetchImpl: async () => respond(200, { plans: {}, pricing: PRICING, billing_enabled: false, billing_mode: "off", ai_enabled: false }) });
    const p = await api.plans();
    expect(p.billing_mode).toBe("off");
    expect(p.pricing.topup.price_cents).toBe(299);
    configureApi({ fetchImpl: async () => respond(200, { plans: {}, pricing: { currency: "USD", monthly_cents: 599, annual_cents: 4999 }, billing_enabled: false }) });
    await expect(api.plans()).rejects.toMatchObject({ code: "MALFORMED_MESSAGE" });
  });
});

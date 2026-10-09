import { afterEach, describe, expect, it } from "vitest";

import { api, ApiError, apiRequest, configureApi, loginUrl } from "../src/lib/api.ts";

const UUID = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:00.000Z";
const session = { account: { id: UUID, email: "a@example.test", display_name: "A", created_at: TS }, csrf_token: "C".repeat(40), plan: "free", limits: { max_enabled_pcs: 1, max_controllers: 2, routines: false, custom_layouts: false }, protocol_version: "1.1" };

function respond(status: number, body: unknown) {
  return new Response(body === undefined ? null : typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null }));

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

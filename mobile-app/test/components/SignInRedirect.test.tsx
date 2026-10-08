/**
 * The pairing QR deep link `/pair#code=…` must never leak the code: not into cloud-api's `return_to`
 * (query string, oidc_flows table), not into the address bar during the sign-in round trip, and not
 * into any storage. This exercises the first-run path (camera app opens the link before the phone
 * has ever signed in on this browser).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RequireSession } from "../../src/app/AppShell.tsx";
import { PAIR_PATH, setNavigationForTests, signInRedirect, type Navigation } from "../../src/app/navigation.ts";
import { loginUrl } from "../../src/lib/api.ts";
import { takeScanAgainHint } from "../../src/lib/pairing.ts";
import { useSessionStore } from "../../src/store/session.ts";

const CODE = "ABCDE-FGHJK-MNPQR-STVWX";

function fakeNavigation() {
  const calls: Array<{ kind: "assign" | "replaceState"; url: string }> = [];
  const nav: Navigation = {
    assign: (url) => calls.push({ kind: "assign", url }),
    replaceState: (url) => calls.push({ kind: "replaceState", url }),
  };
  return { nav, calls };
}

beforeEach(() => {
  useSessionStore.setState({ status: "signed_out", session: null, error: null });
});
afterEach(() => setNavigationForTests(null));

describe("signInRedirect", () => {
  it("drops the fragment and adds the non-secret scan_again marker on the pairing path", () => {
    const r = signInRedirect({ pathname: PAIR_PATH, search: "", hash: `#code=${CODE}` });
    expect(r.returnTo).toBe(`${PAIR_PATH}?scan_again=1`);
    expect(r.scrubTo).toBe(`${PAIR_PATH}?scan_again=1`);
    expect(JSON.stringify(r)).not.toMatch(/code|ABCDE/);
  });
  it("keeps path + query elsewhere, scrubbing any fragment", () => {
    expect(signInRedirect({ pathname: "/app/remote", search: "?tab=1", hash: "" })).toEqual({ returnTo: "/app/remote?tab=1", scrubTo: null });
    expect(signInRedirect({ pathname: "/app/remote", search: "", hash: "#section" })).toEqual({ returnTo: "/app/remote", scrubTo: "/app/remote" });
    expect(signInRedirect({ pathname: "/link", search: "?user_code=ABCD-EFGH", hash: "" }).returnTo).toBe("/link?user_code=ABCD-EFGH");
  });
});

describe("RequireSession when signed out on the pairing deep link", () => {
  it("scrubs the fragment from the address bar and sends a return_to without the code", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    render(
      <MemoryRouter initialEntries={[`${PAIR_PATH}#code=${CODE}`]}>
        <Routes>
          <Route
            path={PAIR_PATH}
            element={
              <RequireSession>
                <div>pairing page</div>
              </RequireSession>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(calls.some((c) => c.kind === "assign")).toBe(true));
    expect(screen.queryByText("pairing page")).toBeNull();
    const assign = calls.find((c) => c.kind === "assign")!;
    const scrub = calls.find((c) => c.kind === "replaceState")!;
    expect(calls.indexOf(scrub)).toBeLessThan(calls.indexOf(assign));
    expect(scrub.url).toBe(`${PAIR_PATH}?scan_again=1`);
    expect(assign.url).toBe(loginUrl(`${PAIR_PATH}?scan_again=1`));
    expect(assign.url).not.toMatch(/code=/i);
    expect(assign.url).not.toMatch(/ABCDE|FGHJK|%23/);
    expect(decodeURIComponent(assign.url)).not.toContain("#");
    expect(screen.getByRole("status", { name: "Taking you to sign in" })).toBeInTheDocument();
  });

  it("does not touch the address bar on an ordinary page without a fragment", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    render(
      <MemoryRouter initialEntries={["/app/remote"]}>
        <Routes>
          <Route
            path="/app/remote"
            element={
              <RequireSession>
                <div>remote</div>
              </RequireSession>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ kind: "assign", url: loginUrl("/app/remote") });
  });
});

describe("RequireSession during a sign-out the customer started", () => {
  it("renders a neutral 'Signing out' screen and never starts the sign-in redirect (that flow navigates once itself)", async () => {
    useSessionStore.setState({ status: "signing_out", session: null, error: null });
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    render(
      <MemoryRouter initialEntries={["/app/settings"]}>
        <RequireSession>
          <div>settings</div>
        </RequireSession>
      </MemoryRouter>,
    );
    expect(screen.getByRole("status", { name: "Signing out" })).toBeInTheDocument();
    expect(screen.queryByText("settings")).toBeNull();
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toHaveLength(0);
    // a late server 401 during the sign-out does not change that
    useSessionStore.getState().clear();
    expect(useSessionStore.getState().status).toBe("signing_out");
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toHaveLength(0);
  });
});

describe("takeScanAgainHint", () => {
  it("reads the marker once and removes it from the address bar, leaving a code fragment for takeCodeFromLocation", () => {
    const replaced: string[] = [];
    const history = { replaceState: (_d: unknown, _u: string, url?: string | URL | null) => replaced.push(String(url)) };
    expect(takeScanAgainHint({ search: "?scan_again=1", hash: "" }, history, PAIR_PATH)).toBe(true);
    expect(replaced).toEqual([PAIR_PATH]);
    expect(takeScanAgainHint({ search: "?scan_again=1&x=2", hash: `#code=${CODE}` }, history, PAIR_PATH)).toBe(true);
    expect(replaced[1]).toBe(`${PAIR_PATH}?x=2#code=${CODE}`);
    expect(takeScanAgainHint({ search: "", hash: "" }, history, PAIR_PATH)).toBe(false);
    expect(replaced).toHaveLength(2);
  });
});

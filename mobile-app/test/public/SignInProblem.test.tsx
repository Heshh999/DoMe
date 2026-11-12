/**
 * A sign-in refused because the email address is not confirmed yet lands on the home page with
 * `?sign_in=email_unverified` (cloud-api DECISIONS #118). The page says so in plain words, exactly as written for the
 * founder, removes the marker from the address bar, and never shows a reason code it does not know. After that text: the
 * founder's way out for a link that never came (Forgot password? on the provider's login page also confirms the
 * address) and a "Sign in again" button that starts sign-in like the header's Sign in (cloud-api makes that sign-in show
 * the login page, DECISIONS #119). When a support contact is configured it adds one line after those (the support
 * page's form needs a sign-in this person cannot complete, so the line goes to the contact itself).
 *
 * A sign-in that could not be finished and only needs a fresh start (it took too long, its answer was opened twice, or
 * it was cancelled at the provider) lands with `?sign_in=try_again` (cloud-api DECISIONS #120): the "Sign in again"
 * notice, with the same button and neither the Forgot-password paragraph nor the support line.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { rest } from "@dome/protocol";

import { api, loginUrl } from "../../src/lib/api.ts";
import { CARRY_MS, SIGN_IN_PROBLEMS, carriedSignInProblem, carrySignInProblem, signInProblem, supportContact, withoutSignInProblem } from "../../src/lib/signInProblem.ts";
import { LandingPage } from "../../src/pages/public/LandingPage.tsx";

const TITLE = "Confirm your email first";
const TEXT = "We sent a link to your inbox when you signed up. Open it, then sign in again. Can't find it? Check your spam folder.";
const WAY_OUT = "No email? Tap Sign in, then Forgot password?. Setting a new password confirms your email too.";
const SIGN_IN_AGAIN = "Sign in again";
// The notice's full text: the founder's title and text, the way out, the button.
const FULL = TITLE + TEXT + WAY_OUT + SIGN_IN_AGAIN;
const SUPPORT_LINE = "Still nothing? Contact support and we'll send a new link.";
const CONTACT = "mailto:support@example.test";
// The second reason: a sign-in that did not finish. Its whole notice is the title, the text and the button.
const AGAIN_TITLE = "Sign in again";
const AGAIN_TEXT = "That sign-in didn't finish. It may have taken too long. Tap Sign in again to start over.";
const AGAIN_FULL = AGAIN_TITLE + AGAIN_TEXT + SIGN_IN_AGAIN;
const PLANS: rest.PlansResponse = { plans: {}, pricing: { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } }, billing_enabled: false, billing_mode: "off", ai_enabled: false };

let plans: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  plans = vi.spyOn(api, "plans").mockResolvedValue(PLANS);
});
afterEach(() => {
  plans.mockRestore();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

function Address() {
  const loc = useLocation();
  return <output data-testid="address">{loc.pathname + loc.search}</output>;
}

function renderHome(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path="/"
          element={
            <>
              <LandingPage />
              <Address />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("signInProblem", () => {
  it("knows email_unverified and try_again and nothing else", () => {
    expect(signInProblem(new URLSearchParams("sign_in=email_unverified"))).toBe("email_unverified");
    expect(signInProblem(new URLSearchParams("sign_in=try_again"))).toBe("try_again");
    for (const q of ["", "sign_in=", "sign_in=toString", "sign_in=EMAIL_UNVERIFIED", "other=email_unverified", "sign_in=TRY_AGAIN", "sign_in=try_again ", "sign_in=expired", "other=try_again"]) {
      expect(signInProblem(new URLSearchParams(q))).toBeNull();
    }
    expect(Object.keys(SIGN_IN_PROBLEMS).sort()).toEqual(["email_unverified", "try_again"]);
    // each reason says which parts its notice has: the founder's words, exactly
    expect(SIGN_IN_PROBLEMS.email_unverified).toEqual({
      title: TITLE,
      text: TEXT,
      wayOut: { lead: "No email? Tap Sign in, then ", strong: "Forgot password?", rest: ". Setting a new password confirms your email too." },
      support: { lead: "Still nothing? ", link: "Contact support", rest: " and we'll send a new link." },
    });
    expect(SIGN_IN_PROBLEMS.try_again).toEqual({ title: AGAIN_TITLE, text: AGAIN_TEXT, wayOut: null, support: null });
  });

  it("removes only the marker", () => {
    expect(withoutSignInProblem(new URLSearchParams("sign_in=email_unverified&ref=mail")).toString()).toBe("ref=mail");
    expect(withoutSignInProblem(new URLSearchParams("ref=mail&sign_in=try_again")).toString()).toBe("ref=mail");
  });
});

describe("home page after a refused sign-in", () => {
  it("shows exactly the title and the text, then clears the marker from the address bar", async () => {
    renderHome("/?sign_in=email_unverified");
    const notice = screen.getByRole("alert");
    expect(notice).toHaveTextContent(TITLE);
    expect(screen.getByText(TITLE).tagName).toBe("P");
    expect(screen.getByText(TEXT)).toBeInTheDocument();
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(FULL);
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
    // still shown after the marker is gone
    expect(screen.getByText(TEXT)).toBeInTheDocument();
    // the notice sits above the hero
    const hero = screen.getByRole("heading", { level: 1 });
    expect(notice.compareDocumentPosition(hero) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps any other part of the address", async () => {
    renderHome("/?sign_in=email_unverified&ref=mail");
    expect(screen.getByText(TITLE)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/\?ref=mail$/));
  });

  it("after the text: the way out in the founder's words, with Forgot password? in bold", () => {
    renderHome("/?sign_in=email_unverified");
    const wayOut = screen.getByText((_, element) => element?.tagName === "P" && element.textContent === WAY_OUT);
    expect(wayOut.querySelector("strong")?.textContent).toBe("Forgot password?");
    expect(screen.getByText(TEXT).compareDocumentPosition(wayOut) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a Sign in again button starts sign-in like the header's Sign in, returning to the app", () => {
    renderHome("/?sign_in=email_unverified");
    const button = screen.getByRole("link", { name: SIGN_IN_AGAIN });
    expect(button).toHaveAttribute("href", loginUrl("/app"));
    expect(button.getAttribute("href")).toMatch(/\/v1\/auth\/login\?return_to=%2Fapp$/);
    expect(screen.getByTestId("sign-in-problem")).toContainElement(button);
  });

  it("without a support contact: the founder's text only, and no link to the support page (its form needs a sign-in)", () => {
    vi.stubEnv("VITE_DOME_SUPPORT_URL", "");
    expect(supportContact()).toBeNull();
    renderHome("/?sign_in=email_unverified");
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(FULL);
    expect(screen.queryByRole("link", { name: "Contact support" })).toBeNull();
  });

  it("with a support contact: one more line after the founder's unchanged text, linking to that contact", () => {
    vi.stubEnv("VITE_DOME_SUPPORT_URL", CONTACT);
    renderHome("/?sign_in=email_unverified");
    expect(screen.getByText(TEXT)).toBeInTheDocument();
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(FULL + SUPPORT_LINE);
    const link = screen.getByRole("link", { name: "Contact support" });
    expect(link).toHaveAttribute("href", CONTACT);
    expect(link).not.toHaveAttribute("href", "/support");
  });

  it("shows nothing on an ordinary visit", () => {
    renderHome("/");
    expect(screen.queryByTestId("sign-in-problem")).toBeNull();
    expect(screen.queryByText(TITLE)).toBeNull();
  });

  it("shows nothing for a reason it does not know, and never the code itself", async () => {
    renderHome("/?sign_in=something_else");
    expect(screen.queryByTestId("sign-in-problem")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
    expect(document.body.textContent).not.toContain("something_else");
    expect(carriedSignInProblem()).toBeNull();
  });

  it("keeps the notice when the app reloads itself right after the refusal (the marker is already gone)", async () => {
    const first = renderHome("/?sign_in=email_unverified");
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
    first.unmount();
    renderHome("/");
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(FULL);
  });

  it("does not carry it past a minute, nor anything but a known reason", () => {
    carrySignInProblem("email_unverified", Date.now() - CARRY_MS - 1000);
    renderHome("/");
    expect(screen.queryByTestId("sign-in-problem")).toBeNull();
    sessionStorage.setItem("dome.sign_in_problem", JSON.stringify({ problem: "something_else", at: Date.now() }));
    expect(carriedSignInProblem()).toBeNull();
    sessionStorage.setItem("dome.sign_in_problem", "not json");
    expect(carriedSignInProblem()).toBeNull();
  });

  it("still shows the notice and clears the address when storage is off", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage off");
    });
    renderHome("/?sign_in=email_unverified");
    expect(screen.getByText(TITLE)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
  });
});

describe("home page after a sign-in that did not finish", () => {
  it("shows exactly the title, the text and the Sign in again button, then clears the marker from the address bar", async () => {
    renderHome("/?sign_in=try_again");
    const notice = screen.getByRole("alert");
    const shown = screen.getByTestId("sign-in-problem");
    expect(shown.textContent).toBe(AGAIN_FULL);
    expect(shown.textContent).toBe("Sign in againThat sign-in didn't finish. It may have taken too long. Tap Sign in again to start over.Sign in again");
    // the title, then the text, then the button: three parts and nothing else
    const [title, text] = Array.from(notice.querySelectorAll("p"));
    expect(notice.querySelectorAll("p")).toHaveLength(2);
    expect(title?.textContent).toBe(AGAIN_TITLE);
    expect(text?.textContent).toBe(AGAIN_TEXT);
    const button = screen.getByRole("link", { name: SIGN_IN_AGAIN });
    expect(text && text.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(shown).toContainElement(button);
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
    // still shown after the marker is gone, above the hero, and the reason code itself is never shown
    expect(screen.getByText(AGAIN_TEXT)).toBeInTheDocument();
    const hero = screen.getByRole("heading", { level: 1 });
    expect(notice.compareDocumentPosition(hero) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.body.textContent).not.toContain("try_again");
  });

  it("its button is the email notice's: the same link, starting sign-in and returning to the app", () => {
    const again = renderHome("/?sign_in=try_again");
    const button = screen.getByRole("link", { name: SIGN_IN_AGAIN });
    expect(button).toHaveAttribute("href", loginUrl("/app"));
    expect(button.getAttribute("href")).toMatch(/\/v1\/auth\/login\?return_to=%2Fapp$/);
    const markup = button.outerHTML;
    again.unmount();
    sessionStorage.clear();
    renderHome("/?sign_in=email_unverified");
    expect(screen.getByRole("link", { name: SIGN_IN_AGAIN }).outerHTML).toBe(markup);
  });

  it("has no Forgot-password paragraph and no support line, with or without a support contact", () => {
    for (const contact of ["", CONTACT]) {
      vi.stubEnv("VITE_DOME_SUPPORT_URL", contact);
      const view = renderHome("/?sign_in=try_again");
      const shown = screen.getByTestId("sign-in-problem");
      expect(shown.textContent).toBe(AGAIN_FULL);
      expect(shown.textContent).not.toContain("Forgot password?");
      expect(shown.textContent).not.toContain("No email?");
      expect(shown.textContent).not.toContain("Still nothing?");
      expect(shown.querySelector("strong")).toBeNull();
      expect(screen.queryByRole("link", { name: "Contact support" })).toBeNull();
      expect(shown.querySelectorAll("a")).toHaveLength(1);
      view.unmount();
      sessionStorage.clear();
    }
    // the same contact does show on the email notice: the line is that reason's own part
    vi.stubEnv("VITE_DOME_SUPPORT_URL", CONTACT);
    renderHome("/?sign_in=email_unverified");
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(FULL + SUPPORT_LINE);
  });

  it("keeps any other part of the address", async () => {
    renderHome("/?ref=mail&sign_in=try_again");
    expect(screen.getByText(AGAIN_TEXT)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/\?ref=mail$/));
  });

  it("keeps the notice when the app reloads itself right after (the marker is already gone), for a minute", async () => {
    const first = renderHome("/?sign_in=try_again");
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
    expect(carriedSignInProblem()).toBe("try_again");
    first.unmount();
    const second = renderHome("/");
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(AGAIN_FULL);
    second.unmount();
    sessionStorage.clear();
    carrySignInProblem("try_again", Date.now() - CARRY_MS - 1000);
    expect(carriedSignInProblem()).toBeNull();
    renderHome("/");
    expect(screen.queryByTestId("sign-in-problem")).toBeNull();
  });

  it("the reason in the address wins over a carried one, and replaces it", async () => {
    carrySignInProblem("email_unverified");
    renderHome("/?sign_in=try_again");
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(AGAIN_FULL);
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
    expect(carriedSignInProblem()).toBe("try_again");
  });

  it("still shows the notice and clears the address when storage is off", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage off");
    });
    renderHome("/?sign_in=try_again");
    expect(screen.getByTestId("sign-in-problem").textContent).toBe(AGAIN_FULL);
    await waitFor(() => expect(screen.getByTestId("address")).toHaveTextContent(/^\/$/));
  });
});

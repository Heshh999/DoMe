/**
 * The home page's film section and the content the rollout brief asked for around it: the honest concept line,
 * the link to /demo, Screen view marked "Coming", and a closing teaser that names Free, Pro and Pro Plus and links to
 * the pricing page as "Compare plans".
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import type { rest } from "@dome/protocol";

import { CONCEPT_NOTE } from "../../src/components/film/timeline.ts";
import { api, ApiError } from "../../src/lib/api.ts";
import { paidPlansLine } from "../../src/lib/useBillingAvailability.ts";
import { LandingPage } from "../../src/pages/public/LandingPage.tsx";

const PLANS: rest.PlansResponse = { plans: {}, pricing: { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } }, billing_enabled: false, billing_mode: "off", ai_enabled: false };
let plans: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  plans = vi.spyOn(api, "plans").mockResolvedValue(PLANS);
});
afterEach(() => plans.mockRestore());

function renderHome() {
  return render(
    <MemoryRouter>
      <LandingPage />
    </MemoryRouter>,
  );
}

describe("LandingPage", () => {
  it("has the film right after the hero, with the concept line and a link to /demo", () => {
    renderHome();
    const sections = Array.from(document.querySelectorAll("main section, section"));
    const hero = sections.findIndex((s) => within(s as HTMLElement).queryByRole("heading", { level: 1 }));
    const see = sections.findIndex((s) => s.getAttribute("aria-labelledby") === "see");
    expect(hero).toBeGreaterThanOrEqual(0);
    expect(see).toBe(hero + 1);
    const section = sections[see] as HTMLElement;
    expect(within(section).getByRole("heading", { level: 2 })).toHaveTextContent(/with DoMe/);
    expect(within(section).getByRole("group", { name: "DoMe demo film" })).toBeInTheDocument();
    expect(within(section).getByText(CONCEPT_NOTE)).toBeInTheDocument();
    expect(within(section).getByRole("link", { name: /Open the full demo/ })).toHaveAttribute("href", "/demo");
  });

  it("keeps the hero's existing copy, caveat and the described product picture", () => {
    renderHome();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent(/Your PC,/);
    expect(h1).toHaveTextContent(/in your hand\./);
    expect(screen.getByText(/DoMe is in development\. The Windows app is not published yet/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /The DoMe app on an iPhone/ })).toBeInTheDocument();
    // the secondary hero button never promises a download that does not exist yet
    expect(screen.getByRole("link", { name: /Windows app · coming soon/ })).toHaveAttribute("href", "/download");
  });

  it("lists Screen view in What you can do as Coming, never as available", () => {
    renderHome();
    const item = screen.getByRole("heading", { level: 3, name: /Screen view/ });
    expect(item).toHaveTextContent("Coming");
    expect(item.closest("li")).toHaveTextContent(/In development/);
    expect(within(item.closest("li") as HTMLElement).getByRole("link", { name: /See the preview/ })).toHaveAttribute("href", "/demo");
  });

  it("closes with Free, Pro and Pro Plus, their prices, and the paid-launch status", () => {
    renderHome();
    const closing = screen.getByRole("heading", { level: 2, name: /Free today\. Pro and Pro Plus/ });
    const card = closing.closest("section") as HTMLElement;
    expect(card).toHaveTextContent("$9.99");
    expect(card).toHaveTextContent("$24.99");
    expect(card).toHaveTextContent(/1 PC and 2 phones/);
    expect(card).toHaveTextContent(/5 PCs and 5 phones/);
    expect(card).toHaveTextContent(/arrive at paid launch/);
    expect(card).toHaveTextContent(/Pro Plus/);
    expect(within(card).getByRole("link", { name: /^Compare plans$/ })).toHaveAttribute("href", "/pricing");
  });

  it("the closing card follows the server once it sells, and never sells Pro Plus while the AI butler is off", async () => {
    expect(paidPlansLine(null)).toBe("Both arrive at paid launch.");
    expect(paidPlansLine({ selling: false, mode: "off", aiEnabled: true })).toBe("Both arrive at paid launch.");
    expect(paidPlansLine({ selling: true, mode: "live", aiEnabled: true })).toBe("Subscribe to either from Billing in the app.");
    expect(paidPlansLine({ selling: true, mode: "live", aiEnabled: false })).toMatch(/Pro Plus goes on sale once the AI butler is switched on/);
    plans.mockResolvedValue({ ...PLANS, billing_enabled: true, billing_mode: "live", ai_enabled: true });
    renderHome();
    const card = screen.getByRole("heading", { level: 2, name: /Free today\. Pro and Pro Plus/ }).closest("section") as HTMLElement;
    await waitFor(() => expect(card).toHaveTextContent("Subscribe to either from Billing in the app."));
    expect(card).not.toHaveTextContent(/arrive at paid launch/);
    plans.mockRejectedValue(new ApiError(0, "NETWORK", "offline", true));
  });
});


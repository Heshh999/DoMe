/**
 * The pricing page shows the three plans honestly: Free "Available now" with a real "Start free" link; Pro and
 * Pro Plus with their prices and a status line (not a button) "Coming at paid launch"; "$0" never "$0.00";
 * Screen view marked Coming wherever it appears; the plain-words AI rules; the comparison table.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { rest } from "@dome/protocol";

import { api, ApiError } from "../../src/lib/api.ts";
import { PricingPage, PRO_PLUS_NEEDS_AI } from "../../src/pages/public/PricingPage.tsx";

const PLANS_OFF: rest.PlansResponse = { plans: {}, pricing: { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } }, billing_enabled: false, billing_mode: "off", ai_enabled: false };

let plans: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  // the public page asks GET /v1/plans once; by default the server has no Stripe keys
  plans = vi.spyOn(api, "plans").mockResolvedValue(PLANS_OFF);
});

afterEach(() => {
  plans.mockRestore();
});

function renderPricing() {
  return render(
    <MemoryRouter initialEntries={["/pricing"]}>
      <PricingPage />
    </MemoryRouter>,
  );
}

const plansAsked = () => waitFor(() => expect(plans).toHaveBeenCalledTimes(1));

describe("PricingPage", () => {
  it("shows Free as available with $0 and a Start free link, and the paid plans with prices and a non-button status line", () => {
    const { container } = renderPricing();
    const free = screen.getByRole("region", { name: "Free" });
    expect(within(free).getByText("Available now")).toBeInTheDocument();
    expect(within(free).getByText("$0")).toBeInTheDocument();
    expect(container.textContent).not.toContain("$0.00");
    expect(within(free).getByRole("link", { name: "Start free" })).toHaveAttribute("href", "/app");

    const pro = screen.getByRole("region", { name: "Pro" });
    expect(within(pro).getByText("$9.99")).toBeInTheDocument();
    expect(within(pro).getByText("or $99.99 / year")).toBeInTheDocument();
    const proPlus = screen.getByRole("region", { name: "Pro Plus" });
    expect(within(proPlus).getByText("$24.99")).toBeInTheDocument();
    expect(within(proPlus).getByText("or $249.99 / year")).toBeInTheDocument();

    // the status is a line of text, never something that looks or acts like a button
    expect(screen.queryByRole("button", { name: /Coming at paid launch/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Coming at paid launch/ })).toBeNull();
    expect(screen.getByTestId("status-pro").tagName).toBe("P");
    expect(screen.getByTestId("status-pro")).toHaveTextContent("Coming at paid launch");
    expect(screen.getByTestId("status-pro_plus")).toHaveTextContent("Coming at paid launch");
    expect(within(pro).queryByRole("button")).toBeNull();
    expect(within(proPlus).queryByRole("button")).toBeNull();
  });

  it("keeps the static labels when GET /v1/plans fails or says billing is off", async () => {
    plans.mockRejectedValue(new ApiError(0, "NETWORK", "offline", true));
    const failed = renderPricing();
    await plansAsked();
    expect(within(failed.container).getByTestId("status-pro")).toHaveTextContent("Coming at paid launch");
    expect(within(failed.container).queryByRole("link", { name: "Sign in to subscribe" })).toBeNull();
    expect(failed.container.textContent).toContain("nothing can be bought in this version");
    failed.unmount();

    plans.mockResolvedValue(PLANS_OFF);
    const off = renderPricing();
    await waitFor(() => expect(plans).toHaveBeenCalledTimes(2));
    expect(within(off.container).getByTestId("status-pro_plus")).toHaveTextContent("Coming at paid launch");
    expect(within(off.container).queryByRole("link", { name: "Sign in to subscribe" })).toBeNull();
    expect(off.container.textContent).not.toContain("Test mode");
  });

  it("when the server sells, the paid cards say Available now with a link to subscribe from Billing, and Test mode in Stripe's test mode", async () => {
    plans.mockResolvedValue({ ...PLANS_OFF, billing_enabled: true, billing_mode: "test", ai_enabled: true });
    const { container } = renderPricing();
    const pro = screen.getByRole("region", { name: "Pro" });
    expect(await within(pro).findByRole("link", { name: "Sign in to subscribe" })).toHaveAttribute("href", "/app/billing");
    expect(within(pro).getByText("Available now")).toBeInTheDocument();
    expect(within(pro).getByText("Test mode")).toBeInTheDocument();
    const proPlus = screen.getByRole("region", { name: "Pro Plus" });
    expect(within(proPlus).getByRole("link", { name: "Sign in to subscribe" })).toHaveAttribute("href", "/app/billing");
    expect(within(proPlus).getByText("Available now")).toBeInTheDocument();
    expect(screen.queryByTestId("status-pro")).toBeNull();
    expect(screen.queryByText("Coming at paid launch")).toBeNull();
    // Free's link and chip are unchanged; nothing on the page looks like an in-page purchase button
    expect(within(screen.getByRole("region", { name: "Free" })).getByRole("link", { name: "Start free" })).toHaveAttribute("href", "/app");
    expect(screen.queryByRole("button")).toBeNull();
    expect(container.textContent).not.toContain("nothing can be bought in this version");
    expect(container.textContent).not.toContain("Prices are planned, not final");
    expect(container.textContent).toContain("Test mode: no real charges.");
    expect(container.textContent).toContain("Screen view is in development.");
    // Screen view stays "Coming" whatever billing says
    expect(within(pro).getByText(/^Screen view:/).closest("li")).toHaveTextContent("Coming");

    plans.mockResolvedValue({ ...PLANS_OFF, billing_enabled: true, billing_mode: "live", ai_enabled: true });
    const live = renderPricing();
    expect(await within(live.container).findAllByRole("link", { name: "Sign in to subscribe" })).toHaveLength(2);
    expect(within(live.container).queryByText("Test mode")).toBeNull();
  });

  it("while selling, never sells what is not built or switched on: layouts and routines say Coming, AI allowances say Not switched on yet, and Pro Plus waits for the AI butler", async () => {
    plans.mockResolvedValue({ ...PLANS_OFF, billing_enabled: true, billing_mode: "live", ai_enabled: false });
    const { container } = renderPricing();
    const pro = screen.getByRole("region", { name: "Pro" });
    expect(await within(pro).findByRole("link", { name: "Sign in to subscribe" })).toHaveAttribute("href", "/app/billing");
    const proPlus = screen.getByRole("region", { name: "Pro Plus" });
    // Pro Plus differs from Pro only in AI requests: not offered while the assistant is off
    expect(within(proPlus).queryByRole("link", { name: "Sign in to subscribe" })).toBeNull();
    expect(within(proPlus).getByTestId("status-pro_plus")).toHaveTextContent(PRO_PLUS_NEEDS_AI);
    expect(within(proPlus).queryByText("Available now")).toBeNull();
    for (const name of ["Free", "Pro", "Pro Plus"]) {
      const card = screen.getByRole("region", { name });
      expect(within(card).getByText(/^AI butler:/).closest("li"), name).toHaveTextContent("Not switched on yet");
    }
    for (const card of [pro, proPlus]) {
      const layouts = within(card).getByText(/^Custom remote layouts/).closest("li")!;
      expect(layouts).toHaveTextContent("Coming");
      expect(layouts.textContent).not.toMatch(/paid launch/);
      expect(within(card).getByText(/^Optional top-up/).closest("li")).toHaveTextContent("Not switched on yet");
    }
    const table = screen.getByRole("table", { name: "Feature comparison between DoMe Free, Pro and Pro Plus" });
    expect(within(table).getByRole("columnheader", { name: /AI butler/ })).toHaveTextContent("Not switched on yet");
    expect(within(table).getByRole("columnheader", { name: /Layouts and routines/ })).toHaveTextContent("Coming");
    expect(within(table).getByRole("row", { name: /Custom remote layouts/ })).toHaveTextContent("Coming");
    expect(within(table).queryByText(/At paid launch/i)).toBeNull();
    expect(screen.getByTestId("ai-off")).toHaveTextContent("not switched on for DoMe accounts yet");
    expect(container.textContent).not.toContain("nothing can be bought in this version");

    // with the assistant on, the AI lines carry no status and Pro Plus is sold too
    plans.mockResolvedValue({ ...PLANS_OFF, billing_enabled: true, billing_mode: "live", ai_enabled: true });
    const on = renderPricing();
    expect(await within(on.container).findAllByRole("link", { name: "Sign in to subscribe" })).toHaveLength(2);
    expect(within(on.container).queryByText("Not switched on yet")).toBeNull();
    expect(within(on.container).queryByTestId("ai-off")).toBeNull();
  });

  it("says Not switched on yet beside Free's AI taste before the server answers and when it says AI is off", async () => {
    renderPricing();
    const free = screen.getByRole("region", { name: "Free" });
    expect(within(free).getByText(/^AI butler: a one-time taste/).closest("li")).toHaveTextContent("Not switched on yet");
    await plansAsked();
    expect(within(free).getByText(/^AI butler: a one-time taste/).closest("li")).toHaveTextContent("Not switched on yet");
  });

  it("states the allowances, the everyday-commands rule, the windows and the top-up in plain words", () => {
    const { container } = renderPricing();
    const text = container.textContent ?? "";
    expect(text).toContain("AI butler: a one-time taste of 10 requests");
    expect(text).toContain("30 requests (about 15 minutes) per 5-hour window, up to 120 (about 1 hour) a week");
    expect(text).toContain("90 requests (about 45 minutes) per 5-hour window, up to 300 (about 2.5 hours) a week");
    expect(text).toContain("are free, unlimited and never count, on every plan, even after you reach an AI limit");
    expect(text).toContain("Refreshes Saturday at 2 pm");
    expect(text).toContain("the app will show the exact refresh time");
    expect(text).toContain("warn you at 80%");
    // no promise of "forever" (not in the brief): Free is "$0, no credit card"
    expect(text).not.toMatch(/forever/i);
    // nothing claims every AI action is shown before it runs
    expect(text).not.toMatch(/always shown to you before anything runs/);
    expect(text).toContain("100 extra AI requests for $2.99, only when you choose");
    expect(text).toContain("nothing is ever added or charged automatically");
    expect(text).toContain("Prices in US dollars, tax included");
    expect(text).not.toMatch(/before tax/);
    expect(text).toContain("nothing can be bought in this version");
    expect(text).toContain("The AI never sees your screen: no screenshots, no pictures");
  });

  it("marks Screen view as Coming on every card and in the comparison, and keeps the Pro-ending guarantee", () => {
    const { container } = renderPricing();
    for (const name of ["Free", "Pro", "Pro Plus"]) {
      const card = screen.getByRole("region", { name });
      const item = within(card).getByText(/^Screen view:/).closest("li")!;
      expect(item).toHaveTextContent("Coming");
    }
    const table = screen.getByRole("table", { name: "Feature comparison between DoMe Free, Pro and Pro Plus" });
    expect(within(table).getByRole("columnheader", { name: "Pro Plus" })).toBeInTheDocument();
    const screenRow = within(table).getByRole("row", { name: /Your PC’s screen live on your phone/ });
    expect(screenRow).toHaveTextContent("10 min per 5-hour window");
    expect(screenRow).toHaveTextContent("Unlimited");
    expect(within(table).getByRole("columnheader", { name: /Screen view/ })).toHaveTextContent("Coming");
    expect(container.textContent).toContain("If your Pro or Pro Plus plan ends, you keep every PC, phone and setting and choose which 1 PC and 2 phones stay enabled (within 14 days");
    expect(screen.getByRole("link", { name: "Read the FAQ" })).toHaveAttribute("href", "/faq");
  });
});

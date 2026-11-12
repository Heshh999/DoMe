/**
 * The renewal lines under the paid plans take their prices from the plan data (plans.json's `pricing_defaults`), never
 * from words in the code: with other prices in the contract, the pricing page's cards and their renewal lines follow.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RENEWAL_CLASS, SubscriptionTerms } from "../../src/content/SubscriptionTerms.tsx";
import { HOW_TO_CANCEL } from "../../src/content/plans.ts";
import { api, ApiError } from "../../src/lib/api.ts";
import { PricingPage } from "../../src/pages/public/PricingPage.tsx";

vi.mock("@dome/protocol", async (importOriginal) => {
  const real = await importOriginal<typeof import("@dome/protocol")>();
  const pricing = real.PLANS.pricing_defaults;
  return { ...real, PLANS: { ...real.PLANS, pricing_defaults: { ...pricing, plans: { pro: { monthly_cents: 1234, annual_cents: 12300 }, pro_plus: { monthly_cents: 3456, annual_cents: 34567 } } } } };
});

let plans: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  plans = vi.spyOn(api, "plans").mockRejectedValue(new ApiError(0, "NETWORK", "offline", true));
});
afterEach(() => plans.mockRestore());

describe("renewal lines follow the plan data", () => {
  it("shows the changed prices on the cards and in the renewal lines under them", () => {
    render(
      <MemoryRouter initialEntries={["/pricing"]}>
        <PricingPage />
      </MemoryRouter>,
    );
    const pro = screen.getByRole("region", { name: "Pro" });
    expect(within(pro).getByText("$12.34")).toBeInTheDocument();
    expect(within(pro).getByTestId("terms-pro")).toHaveTextContent("Renews automatically every month at $12.34, or every year at $123 if you choose yearly billing, until you cancel.");
    const proPlus = screen.getByRole("region", { name: "Pro Plus" });
    expect(within(proPlus).getByText("$34.56")).toBeInTheDocument();
    expect(within(proPlus).getByTestId("terms-pro_plus")).toHaveTextContent("Renews automatically every month at $34.56, or every year at $345.67 if you choose yearly billing, until you cancel.");
    // nothing of the contract's usual prices is left anywhere on the page
    expect(document.body.textContent).not.toMatch(/\$9\.99|\$99\.99|\$24\.99|\$249\.99/);
  });
});

describe("the renewal sentence stands out from the small print around it", () => {
  it("is larger and in the normal text colour, with Renews automatically in bold, whatever the caller's muted style", () => {
    render(
      <MemoryRouter>
        <SubscriptionTerms className="text-xs text-text-muted" price={{ monthlyCents: 1234, annualCents: 12300 }} interval="monthly" testId="terms-x" />
      </MemoryRouter>,
    );
    const renewal = screen.getByTestId("terms-x-renewal");
    expect(renewal).toHaveTextContent("Renews automatically every month at $12.34 until you cancel.");
    expect(renewal.className).toBe(RENEWAL_CLASS);
    expect(RENEWAL_CLASS.split(" ")).toEqual(expect.arrayContaining(["text-sm", "text-text"]));
    expect(RENEWAL_CLASS).not.toMatch(/text-xs|text-\[1[0-3]px\]|text-text-muted|text-text-faint/);
    const lead = within(renewal).getByText("Renews automatically");
    expect(lead.tagName).toBe("STRONG");
    // the cancel line keeps the caller's style
    expect(screen.getByText(HOW_TO_CANCEL).className).toBe("");
  });

  it("on the pricing page too, under each paid plan", () => {
    render(
      <MemoryRouter initialEntries={["/pricing"]}>
        <PricingPage />
      </MemoryRouter>,
    );
    for (const id of ["pro", "pro_plus"]) {
      const renewal = screen.getByTestId(`terms-${id}-renewal`);
      expect(renewal.className).toBe(RENEWAL_CLASS);
      expect(within(renewal).getByText("Renews automatically").tagName).toBe("STRONG");
    }
  });
});

/**
 * The advertising review's rule for Screen view (docs/legal): the paid plans have no Free-style time limit, but the relay
 * keeps per-account limits (cloud-api screen/turn.py), so no rendered page may call Screen view "unlimited". This scans
 * the rendered Pricing and FAQ pages (and the Landing and Demo pages, which show the plans too), before the server
 * answers and while it sells: no block that mentions Screen view may say it. Everyday commands are not called
 * "unlimited" either (a fair-use limit of 120 per minute applies to every command, plans.json
 * `manual_command_rate_limit`): they are never counted against the AI allowance. The features that are not built are
 * never offered as available, and the film shows none of them (no mini view, PC sound or TV-style remote).
 */
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { rest } from "@dome/protocol";

import { api, ApiError } from "../../src/lib/api.ts";
import { DemoPage } from "../../src/pages/public/DemoPage.tsx";
import { FaqPage } from "../../src/pages/public/FaqPage.tsx";
import { LandingPage } from "../../src/pages/public/LandingPage.tsx";
import { PricingPage } from "../../src/pages/public/PricingPage.tsx";

const PRICING: rest.Pricing = { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } };
const SELLING: rest.PlansResponse = { plans: {}, pricing: PRICING, billing_enabled: true, billing_mode: "live", ai_enabled: true };

let plans: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  plans = vi.spyOn(api, "plans").mockRejectedValue(new ApiError(0, "NETWORK", "offline", true));
});
afterEach(() => {
  plans.mockRestore();
  delete window.__domeFilm;
});

/** The smallest blocks a reader takes in one go: a paragraph, a list item, a table row, a heading, a question. */
function blocks(root: HTMLElement): string[] {
  return Array.from(root.querySelectorAll("p, li, tr, h1, h2, h3, summary, caption")).map((el) => (el.textContent ?? "").replace(/\s+/g, " ").trim());
}

const SCREEN_VIEW = /Screen view|PC’s screen/i;

function expectNoUnlimitedScreenView(root: HTMLElement, page: string) {
  const all = blocks(root);
  expect(all.some((t) => SCREEN_VIEW.test(t)), `${page} mentions Screen view`).toBe(true);
  for (const t of all) {
    if (SCREEN_VIEW.test(t)) expect(t, page).not.toMatch(/unlimited/i);
    // nor the everyday commands: they are never counted against the AI allowance, but a fair-use limit applies
    expect(t, page).not.toMatch(/unlimited/i);
  }
  const text = root.textContent ?? "";
  expect(text, page).not.toMatch(/unlimited Screen view|Screen view: unlimited|Pro: unlimited|Pro Plus unlimited/i);
  // not built: never described as something Screen view does today or as a plan benefit
  expect(text, page).not.toMatch(/mini view while you use another app/i);
  // the film's beats: nothing Screen view's first version lacks
  expect(text, page).not.toMatch(/keeps playing in a corner|arrow keys|TV remote|Hear your PC|PC audio/i);
}

function renderAt(url: string, path: string, element: React.ReactElement) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("no page calls Screen view unlimited", () => {
  for (const [state, answer] of [
    ["before the server answers (or when it fails)", null],
    ["while the server sells, with the AI butler on", SELLING],
  ] as const) {
    it(`Pricing and FAQ, ${state}`, async () => {
      if (answer) plans.mockResolvedValue(answer);
      const pricing = renderAt("/pricing", "/pricing", <PricingPage />);
      if (answer) await vi.waitFor(() => expect(pricing.getAllByRole("link", { name: "Sign in to subscribe" })).toHaveLength(2));
      expectNoUnlimitedScreenView(pricing.container, "Pricing");
      // the comparison and the cards say what the paid plans have instead
      expect(pricing.container.textContent).toContain("Without the Free plan’s time limit (fair-use limits apply)");
      pricing.unmount();

      const faq = renderAt("/faq", "/faq", <FaqPage />);
      if (answer) await vi.waitFor(() => expect(faq.container.textContent).toContain("Yes: Pro and Pro Plus can be bought from Billing"));
      expectNoUnlimitedScreenView(faq.container, "FAQ");
      expect(faq.container.textContent).toContain("fair-use limits apply");
      expect(faq.container.textContent).toContain("Hearing your PC’s sound on your phone and a TV-style remote are coming later.");
    });
  }

  it("Landing and Demo (the film's Screen view scene and plan cards included)", () => {
    const landing = renderAt("/", "/", <LandingPage />);
    expectNoUnlimitedScreenView(landing.container, "Landing");
    landing.unmount();
    for (const scene of [3, 5]) {
      const demo = renderAt(`/demo?scene=${scene}&paused=1`, "/demo", <DemoPage />);
      expectNoUnlimitedScreenView(demo.container, `Demo scene ${scene}`);
      demo.unmount();
      delete window.__domeFilm;
    }
  });
});

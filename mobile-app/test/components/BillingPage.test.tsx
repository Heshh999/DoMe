/**
 * The in-app Billing page: everything about the customer's own plan comes from the session document; the
 * coming paid plans and their prices come from the website's plan content (src/content/plans.ts), so the app
 * and the pricing page agree: Pro $9.99 / month or $99.99 / year, Pro Plus $24.99 / month or $249.99 / year,
 * both coming at paid launch. Nothing on the page offers or implies a purchase.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { rest } from "@dome/protocol";

import { COMING_AT_PAID_LAUNCH, planById, usd } from "../../src/content/plans.ts";
import { BillingPage } from "../../src/pages/app/BillingPage.tsx";
import { useSessionStore } from "../../src/store/session.ts";
import { SESSION } from "../helpers/harness.ts";

function renderBilling(overrides: Partial<rest.SessionResponse> = {}) {
  useSessionStore.setState({ status: "signed_in", session: { ...SESSION, ...overrides }, error: null });
  return render(
    <MemoryRouter initialEntries={["/app/billing"]}>
      <BillingPage />
    </MemoryRouter>,
  );
}

const PRO_LIMITS = { max_enabled_pcs: 5, max_controllers: 5, routines: true, custom_layouts: true };

describe("BillingPage", () => {
  it("shows a Free customer their plan and limits from the session document, and no way to buy anything", () => {
    const { container } = renderBilling();
    expect(screen.getByText("DoMe Free")).toBeInTheDocument();
    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(container.textContent).toMatch(/You are on DoMe Free/);
    const limits = screen.getByRole("heading", { name: "Your limits" }).closest("div") as HTMLElement;
    expect(limits).toHaveTextContent(/Enabled PCs\s*1/);
    expect(limits).toHaveTextContent(/Paired phones\s*2/);
    expect(limits).toHaveTextContent(/Routines\s*Not included/);
    expect(limits).toHaveTextContent(/Custom layouts\s*Not included/);
    expect(screen.queryByRole("button")).toBeNull();
    // the only link compares the plans on the website; nothing here buys anything
    expect(screen.getAllByRole("link").map((l) => l.getAttribute("href"))).toEqual(["/pricing"]);
    expect(container.textContent).not.toMatch(/upgrade now|subscribe|buy now|checkout now/i);
  });

  it("states the coming plans exactly as the website does: Pro $9.99 / $99.99, Pro Plus $24.99 / $249.99, coming at paid launch, nothing can be bought", () => {
    const { container } = renderBilling();
    const notice = screen.getByText("Upgrading is not available yet").closest('[role="status"]') as HTMLElement;
    expect(notice).toHaveTextContent("Pro ($9.99 / month or $99.99 / year) and Pro Plus ($24.99 / month or $249.99 / year) are coming at paid launch");
    expect(notice).toHaveTextContent(/Nothing can be bought or charged from this app today/);
    expect(within(notice).getByRole("link", { name: "Compare plans" })).toHaveAttribute("href", "/pricing");
    // the figures are the plan content's, so a price change on the website reaches this page too
    const pro = planById("pro").price!;
    const proPlus = planById("pro_plus").price!;
    expect(notice).toHaveTextContent(`${usd(pro.monthlyCents)} / month or ${usd(pro.annualCents)} / year`);
    expect(notice).toHaveTextContent(`${usd(proPlus.monthlyCents)} / month or ${usd(proPlus.annualCents)} / year`);
    expect(notice).toHaveTextContent(COMING_AT_PAID_LAUNCH.toLowerCase());
    // the old planned price and its hedge are gone
    expect(container.textContent).not.toMatch(/\$5\.99|\$49\.99|planned pricing|not final/);
    expect(container.textContent).toMatch(/never paywalled/);
  });

  it("takes the plan state from the session document: active Pro, pending, past due with recovery steps, canceling, expired", () => {
    const { unmount } = renderBilling({ plan: "pro", entitlement_state: "active", limits: PRO_LIMITS });
    expect(screen.getByText("DoMe Pro")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your limits" }).closest("div")).toHaveTextContent(/Enabled PCs\s*5/);
    unmount();

    const pending = renderBilling({ plan: "pro", entitlement_state: "pending", limits: PRO_LIMITS });
    expect(screen.getByText("Pending activation")).toBeInTheDocument();
    expect(pending.container.textContent).toMatch(/payment is being confirmed/);
    pending.unmount();

    const pastDue = renderBilling({ plan: "pro", entitlement_state: "past_due", limits: PRO_LIMITS });
    expect(screen.getByText("Payment problem")).toBeInTheDocument();
    expect(pastDue.container.textContent).toMatch(/Free features keep working/);
    expect(screen.getByRole("list")).toBeInTheDocument(); // the recovery steps
    pastDue.unmount();

    const canceling = renderBilling({ plan: "pro", entitlement_state: "canceling", limits: PRO_LIMITS });
    expect(screen.getByText("Ends at period end")).toBeInTheDocument();
    canceling.unmount();

    renderBilling({ plan: "free", entitlement_state: "expired" });
    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText(/your PCs, phones and settings were kept/)).toBeInTheDocument();
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

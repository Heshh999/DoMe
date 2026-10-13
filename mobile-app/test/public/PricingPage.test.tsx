/**
 * The pricing page shows the three plans honestly: Free "Available now" with a real "Start free" link; Pro and
 * Pro Plus with their prices and a status line (not a button) "Coming at paid launch"; "$0" never "$0.00";
 * Screen view marked Coming wherever it appears; the plain-words AI rules; the comparison table.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { PricingPage } from "../../src/pages/public/PricingPage.tsx";

function renderPricing() {
  return render(
    <MemoryRouter initialEntries={["/pricing"]}>
      <PricingPage />
    </MemoryRouter>,
  );
}

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

  it("states the allowances, the everyday-commands rule, the windows and the top-up in plain words", () => {
    const { container } = renderPricing();
    const text = container.textContent ?? "";
    expect(text).toContain("AI butler: a one-time taste of 10 requests");
    expect(text).toContain("30 requests (about 15 minutes) per 5-hour window, up to 120 (about 1 hour) a week");
    expect(text).toContain("90 requests (about 45 minutes) per 5-hour window, up to 300 (about 2.5 hours) a week");
    expect(text).toContain("are free, unlimited and never count, on every plan, even after you reach an AI limit");
    expect(text).toContain("Refreshes Saturday at 2 pm");
    expect(text).toContain("warns you at 80 %");
    expect(text).toContain("100 extra AI requests for $2.99, only when you choose");
    expect(text).toContain("nothing is ever added or charged automatically");
    expect(text).toContain("Prices in US dollars, before tax");
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
    expect(container.textContent).toContain("If Pro ends, you keep every PC, phone and setting and choose which 1 PC and 2 phones stay enabled (within 14 days");
    expect(screen.getByRole("link", { name: "Read the FAQ" })).toHaveAttribute("href", "/faq");
  });
});

/**
 * The home page's film section and the content the rollout brief asked for around it: the honest concept line,
 * the link to /demo, Screen view marked "Coming", and a closing teaser that names Free, Pro and Pro Plus and links to
 * the pricing page as "Compare plans".
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router";

import { CONCEPT_NOTE } from "../../src/components/film/timeline.ts";
import { LandingPage } from "../../src/pages/public/LandingPage.tsx";

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
    expect(screen.getByRole("link", { name: /Get DoMe for Windows/ })).toHaveAttribute("href", "/download");
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
});

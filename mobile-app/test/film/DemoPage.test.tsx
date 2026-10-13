/**
 * /demo: the public page around the film, its query flags, and the route itself.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import { CONCEPT_NOTE, SCENES } from "../../src/components/film/timeline.ts";
import { DemoPage } from "../../src/pages/public/DemoPage.tsx";

afterEach(() => {
  delete window.__domeFilm;
});

function at(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/demo" element={<DemoPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DemoPage", () => {
  it("shows the film with an introduction, the honest concept line, the plans and their links", () => {
    at("/demo");
    expect(screen.getByRole("heading", { level: 1, name: "See DoMe in action." })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "DoMe demo film" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Film position" })).toBeInTheDocument();
    expect(screen.getAllByText(CONCEPT_NOTE).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("heading", { level: 2, name: "What you saw, by plan." })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Compare plans/ })).toHaveAttribute("href", "/pricing");
    expect(screen.getByRole("link", { name: "Open the app" })).toHaveAttribute("href", "/app");
    // the plan cards: Free available, the paid ones coming, prices from the brief
    expect(screen.getByText("Available now")).toBeInTheDocument();
    expect(screen.getAllByText("Coming at paid launch")).toHaveLength(2);
    expect(screen.getByText("$9.99")).toBeInTheDocument();
    expect(screen.getByText("$24.99")).toBeInTheDocument();
    // Screen view carries its "Coming" status on every plan card
    for (const name of ["Free", "Pro", "Pro Plus"]) {
      const card = screen.getByRole("heading", { level: 3, name }).closest("li") as HTMLElement;
      expect(card.querySelector("li:nth-child(3)")).toHaveTextContent(/^Screen view: .*Coming$/);
    }
    expect(screen.getByText(/Nothing is recorded; the AI never sees your screen/)).toBeInTheDocument();
    expect(screen.getByText(/the Windows app is not published yet/)).toBeInTheDocument();
  });

  it("?scene=N&paused=1 renders that scene's representative still, not playing", () => {
    at("/demo?scene=4&paused=1");
    expect(Number(document.querySelector("[data-film-time]")?.getAttribute("data-film-time"))).toBe(SCENES[3]!.still);
    expect(screen.getByRole("status")).toHaveTextContent("Point-and-talk");
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });

  it("?t=<ms>&paused=1 renders any moment and ?layout=vertical restacks the stage", () => {
    at("/demo?t=12345&paused=1&layout=vertical");
    expect(Number(document.querySelector("[data-film-time]")?.getAttribute("data-film-time"))).toBe(12_345);
    expect(screen.getByRole("group", { name: "DoMe demo film" })).toHaveAttribute("data-layout", "vertical");
  });

  it("?export=1 is chrome-less: only the film, no controls, no page copy, from t = 0", () => {
    at("/demo?export=1");
    expect(screen.getByTestId("film-export")).toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(Number(document.querySelector("[data-film-time]")?.getAttribute("data-film-time"))).toBe(0);
    expect(window.__domeFilm?.total).toBeGreaterThan(0);
  });
});

describe("the /demo route", () => {
  it("is registered on the public site, next to the other public pages", async () => {
    const { router } = await import("../../src/app/router.tsx");
    const publicRoutes = router.routes[0]!.children!.map((r) => r.path);
    expect(publicRoutes).toContain("/demo");
    expect(publicRoutes).toContain("/");
    expect(publicRoutes).toContain("/pricing");
  });
});

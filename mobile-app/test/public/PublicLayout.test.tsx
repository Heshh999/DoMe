/**
 * The website chrome: the header links (with Demo → /demo), the phone menu that closes on navigation, the
 * icon-only theme switch with tooltips, the footer's lockup and the Windows app's status: the committed release
 * (DoMe for Windows 0.1.0) as a free beta, and "not published yet" for the placeholder before it.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PublicLayout } from "../../src/app/PublicLayout.tsx";
import { ThemeSwitch } from "../../src/components/ThemeSwitch.tsx";
import { FOOTER, NAV } from "../../src/content/nav.ts";
import { THEME_KEY } from "../../src/lib/theme.ts";
import { committedRelease } from "../helpers/committedRelease.ts";

// the real committed release, or the placeholder the site carried before it while `release.placeholder` is set
const release = vi.hoisted(() => ({ placeholder: false }));
vi.mock("../../src/content/windows-release.json", async (importOriginal) => (await import("../helpers/committedRelease.ts")).releaseOrPlaceholder(importOriginal, release));

function site(initial = "/faq") {
  const router = createMemoryRouter(
    [
      {
        element: <PublicLayout />,
        children: [
          { path: "/", element: <p>home page</p> },
          { path: "/demo", element: <p>demo page</p> },
          { path: "/pricing", element: <p>pricing page</p> },
          { path: "/faq", element: <p>faq page</p> },
        ],
      },
    ],
    { initialEntries: [initial] },
  );
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  release.placeholder = false;
});

describe("PublicLayout", () => {
  it("links Demo, Pricing, FAQ, Download and Support in the header, Demo first and pointing at /demo", () => {
    site();
    const nav = screen.getByRole("navigation", { name: "Site" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["Demo", "Pricing", "FAQ", "Download", "Support"]);
    expect(within(nav).getByRole("link", { name: "Demo" })).toHaveAttribute("href", "/demo");
    expect(within(nav).getByRole("link", { name: "FAQ" })).toHaveAttribute("aria-current", "page");
    expect(NAV.find((n) => n.label === "Demo")?.to).toBe("/demo");
    expect(FOOTER.flatMap((g) => g.links).some((l) => l.to === "/demo")).toBe(true);
    expect(within(screen.getByRole("banner")).getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/app");
  });

  it("opens the phone menu with the same links and closes it when one is followed", async () => {
    site();
    const button = screen.getByRole("button", { name: "Menu" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("navigation", { name: "Site menu" })).toBeNull();
    await userEvent.click(button);
    const menu = screen.getByRole("navigation", { name: "Site menu" });
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
    expect(within(menu).getByRole("link", { name: "Demo" })).toHaveAttribute("href", "/demo");
    // the menu carries the appearance switch with text labels
    expect(screen.getAllByRole("radiogroup", { name: "Appearance" }).length).toBeGreaterThanOrEqual(2);
    await userEvent.click(within(menu).getByRole("link", { name: "Demo" }));
    expect(screen.getByText("demo page")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Site menu" })).toBeNull();
    expect(screen.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the full lockup, every page and the committed release's status in the footer", () => {
    const r = committedRelease();
    const { container } = site();
    for (const group of FOOTER) {
      const nav = screen.getByRole("navigation", { name: group.title });
      for (const l of group.links) expect(within(nav).getByRole("link", { name: l.label })).toHaveAttribute("href", l.to);
    }
    expect(screen.getByText(`Free beta: DoMe for Windows ${r.version} has not been tested on a Windows 10 or 11 PC yet.`)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/not published yet/);
    expect(screen.getByText("DoMe Remote · for Windows PCs and iPhone")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "DoMe Remote home" }).length).toBe(2);
  });

  it("before a release, the footer says the Windows app is not published yet", () => {
    release.placeholder = true;
    site();
    expect(screen.getByText("In development: the Windows app is not published yet.")).toBeInTheDocument();
    expect(screen.queryByText(/free beta/i)).toBeNull();
  });
});

describe("ThemeSwitch (website header)", () => {
  it("names every option for assistive tech and tooltips, and says what Auto does", () => {
    render(<ThemeSwitch labels="lg" />);
    const group = screen.getByRole("radiogroup", { name: "Appearance" });
    const auto = within(group).getByRole("radio", { name: "Auto" });
    expect(auto).toHaveAttribute("title", "Auto: follows your device");
    expect(within(group).getByRole("radio", { name: "Light" })).toHaveAttribute("title", "Light: always the light look");
    expect(within(group).getByRole("radio", { name: "Dark" })).toHaveAttribute("title", "Dark: always the dark look");
    // the words are in the DOM for the wide-screen look (CSS hides them below lg)
    expect(auto.querySelector("span")?.textContent).toBe("Auto");
    expect(auto.querySelector("svg")).not.toBeNull();
  });

  it("still applies and remembers a choice from the header switch", async () => {
    render(<ThemeSwitch />);
    await userEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  });

  it("names each page in the document title, so tabs and history tell pages apart", async () => {
    site("/pricing");
    expect(document.title).toBe("Pricing · DoMe Remote");
    await userEvent.click(within(screen.getByRole("navigation", { name: "Site" })).getByRole("link", { name: "FAQ" }));
    expect(document.title).toBe("FAQ · DoMe Remote");
    await userEvent.click(screen.getAllByRole("link", { name: "DoMe Remote home" })[0]!);
    expect(document.title).toBe("DoMe Remote");
  });

  it("offers Skip to content as the first stop, to a focusable main landmark", () => {
    site("/faq");
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveAttribute("href", "#main");
    expect(document.querySelector(".dome-public a")).toBe(skip);
    expect(screen.getByRole("main")).toHaveAttribute("tabindex", "-1");
  });

  it("points aria-controls at the menu only while it exists, and Escape closes it back to the Menu button", async () => {
    site("/faq");
    const button = screen.getByRole("button", { name: "Menu" });
    expect(button).not.toHaveAttribute("aria-controls");
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-controls", "site-menu");
    expect(document.getElementById("site-menu")).not.toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(document.getElementById("site-menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Menu" })).toHaveFocus();
  });
});

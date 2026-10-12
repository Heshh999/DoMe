/**
 * The website chrome: the header links (with Demo → /demo), the phone menu that closes on navigation, the
 * icon-only theme switch with tooltips, the footer's lockup and the development caveat.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PublicLayout } from "../../src/app/PublicLayout.tsx";
import { ThemeSwitch } from "../../src/components/ThemeSwitch.tsx";
import { FOOTER, NAV } from "../../src/content/nav.ts";
import { THEME_KEY } from "../../src/lib/theme.ts";

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
    expect(screen.getByRole("link", { name: "Open app" })).toHaveAttribute("href", "/app");
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

  it("shows the full lockup, every page and the development caveat in the footer", () => {
    site();
    for (const group of FOOTER) {
      const nav = screen.getByRole("navigation", { name: group.title });
      for (const l of group.links) expect(within(nav).getByRole("link", { name: l.label })).toHaveAttribute("href", l.to);
    }
    expect(screen.getByText("In development: the Windows app is not published yet.")).toBeInTheDocument();
    expect(screen.getByText("DoMe Remote · for Windows PCs and iPhone")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "DoMe Remote home" }).length).toBe(2);
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
});

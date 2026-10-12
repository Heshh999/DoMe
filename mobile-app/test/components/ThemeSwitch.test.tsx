/** The Light / Auto / Dark switch on the website and in More: a radio group that applies and remembers the choice. */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ThemeSwitch } from "../../src/components/ThemeSwitch.tsx";
import { THEME_KEY } from "../../src/lib/theme.ts";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("ThemeSwitch", () => {
  it("starts on Auto, applies and remembers Dark and Light, and Auto follows the device again", async () => {
    render(<ThemeSwitch />);
    const group = screen.getByRole("radiogroup", { name: "Appearance" });
    expect(within(group).getByRole("radio", { name: "Auto" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(group).getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(within(group).getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(group).getByRole("radio", { name: "Light" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    await userEvent.click(within(group).getByRole("radio", { name: "Auto" }));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("shows the saved choice, keeps two switches on one page in step, and moves with the arrow keys", async () => {
    localStorage.setItem(THEME_KEY, "light");
    render(
      <>
        <ThemeSwitch />
        <ThemeSwitch labels />
      </>,
    );
    const [first, second] = screen.getAllByRole("radiogroup", { name: "Appearance" });
    expect(within(second!).getByRole("radio", { name: "Light" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(first!).getByRole("radio", { name: "Dark" }));
    expect(within(second!).getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    // roving focus: only the checked option is in the tab order; arrows change the choice
    expect(within(second!).getByRole("radio", { name: "Dark" })).toHaveAttribute("tabindex", "0");
    within(second!).getByRole("radio", { name: "Dark" }).focus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
    expect(within(second!).getByRole("radio", { name: "Auto" })).toHaveFocus();
  });
});

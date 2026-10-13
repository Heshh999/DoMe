/**
 * The FAQ keeps every honest answer and adds the AI butler, limits, Screen view, screen/speech privacy, plans,
 * top-ups and cancelling. The screen answers say "no screenshots" and the speech answer says "never records".
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { FAQ_GROUPS } from "../../src/content/faq.tsx";
import { FaqPage } from "../../src/pages/public/FaqPage.tsx";

const questions = FAQ_GROUPS.flatMap((g) => g.entries.map((e) => e.q));

describe("FAQ", () => {
  it("asks the questions the plans and the AI butler raise, and keeps the long-standing honest ones", () => {
    for (const q of [
      "What is the AI butler?",
      "What counts as an AI request?",
      "What happens when I reach a limit?",
      "What is Screen view, and when is it coming?",
      "Does the AI see my screen?",
      "Is my speech stored?",
      "What do Pro and Pro Plus include?",
      "What is a top-up?",
      "How do I cancel?",
      "Is this end-to-end encrypted?",
      "Can someone with my password control my PC?",
      "What if I clear my browser data or reinstall?",
      "What does the Windows app see or change?",
      "Why can’t fullscreen be turned on from my phone?",
    ]) {
      expect(questions).toContain(q);
    }
    expect(FAQ_GROUPS.find((g) => g.title === "Screen view")?.status).toBe("Coming");
  });

  it("renders every question as a disclosure; the screen answers say no screenshots and the speech answer says nothing is recorded", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/faq"]}>
        <FaqPage />
      </MemoryRouter>,
    );
    const details = Array.from(container.querySelectorAll("details"));
    expect(details.length).toBe(questions.length);
    const answer = (q: string) => details.find((d) => d.querySelector("summary")?.textContent?.includes(q))!.textContent ?? "";

    expect(answer("Does the AI see my screen?")).toMatch(/Never\. DoMe takes no screenshots/);
    expect(answer("Does the AI see my screen?")).toMatch(/planned, not built/);
    expect(answer("Does the AI see my screen?")).toMatch(/names of on-screen elements only/);
    expect(answer("Is anything recorded in Screen view?")).toMatch(/not recorded or stored anywhere/);
    expect(answer("Is my speech stored?")).toMatch(/never records, uploads or keeps audio/);
    expect(answer("Is my speech stored?")).toMatch(/does not store what you say/);
    expect(answer("What happens when I reach a limit?")).toMatch(/every everyday command/);
    expect(answer("What happens when I reach a limit?")).toMatch(/Refreshes Saturday at 2 pm/);
    expect(answer("What counts as an AI request?")).toMatch(/free, unlimited and never count/);
    expect(answer("What is Screen view, and when is it coming?")).toMatch(/10 minutes per 5-hour window; Pro and Pro Plus will have unlimited/);
    expect(answer("What do Pro and Pro Plus include?")).toMatch(/\$9\.99 a month or \$99\.99 a year/);
    expect(answer("What do Pro and Pro Plus include?")).toMatch(/\$24\.99 a month or \$249\.99 a year/);
    expect(answer("What is a top-up?")).toMatch(/100 AI requests for \$2\.99/);
    expect(answer("Can I buy Pro today?")).toMatch(/Not yet/);
    expect(answer("Is this end-to-end encrypted?")).toMatch(/encrypted\?Not yet\. Connections are encrypted in transit/);

    // nothing on the page claims the AI sees the screen, that anything is recorded, or that Screen view exists today
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/the AI (can )?sees? your screen(?! *:)/i);
    expect(text).toContain("Screen view");
    expect(screen.getByRole("navigation", { name: "Topics" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "/privacy");
  });
});

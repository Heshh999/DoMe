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
      "Why can’t YouTube’s own full-screen button be pressed from my phone?",
      "Can I buy Pro or Pro Plus today?",
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
    expect(answer("Can I buy Pro or Pro Plus today?")).toMatch(/Not yet/);
    // the butler's answer says exactly what runs at once, what waits for Run and what asks for confirmation
    expect(answer("What is the AI butler?")).toMatch(/Simple actions such as pause or volume run right away/);
    expect(answer("What is the AI butler?")).toMatch(/wait for your tap on Run/);
    expect(answer("What is the AI butler?")).not.toMatch(/before anything runs/);
    // the microphone: tap to talk, not hold, and Apple may process the words
    expect(answer("Is my speech stored?")).toMatch(/only after you tap the microphone/);
    expect(answer("Is my speech stored?")).toMatch(/at most 15 seconds/);
    expect(answer("Is my speech stored?")).toMatch(/Apple’s servers/);
    expect(answer("Is my speech stored?")).not.toMatch(/hold/);
    // the Windows app's "only" list names manual input too
    expect(answer("What does the Windows app see or change?")).toMatch(/touchpad and keyboard input/);
    expect(answer("What does the Windows app see or change?")).not.toMatch(/registry/);
    // the app's 80% warning is future behaviour, not today's
    expect(answer("What is a top-up?")).toMatch(/When paid plans launch, the app will warn you at 80%/);
    expect(answer("Is this end-to-end encrypted?")).toMatch(/encrypted\?Not yet\. Connections are encrypted in transit/);

    // nothing on the page claims the AI sees the screen, that anything is recorded, or that Screen view exists today
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/the AI (can )?sees? your screen(?! *:)/i);
    expect(text).toContain("Screen view");
    expect(screen.getByRole("navigation", { name: "Topics" })).toBeInTheDocument();
    const privacy = screen.getAllByRole("link", { name: "Privacy" });
    expect(privacy.length).toBeGreaterThanOrEqual(2);
    for (const l of privacy) expect(l).toHaveAttribute("href", "/privacy");
  });
});

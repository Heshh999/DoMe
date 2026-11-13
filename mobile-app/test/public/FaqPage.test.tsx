/**
 * The FAQ keeps every honest answer and adds the AI butler, limits, Screen view, screen/speech privacy, plans,
 * top-ups and cancelling. The screen answers say "no screenshots" and the speech answer says "never records". The
 * YouTube answer follows the release the Download page offers: the committed DoMe for Windows 0.1.0 has no browser add-on.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { rest } from "@dome/protocol";

import { FAQ_GROUPS, faqGroups } from "../../src/content/faq.tsx";
import { api, ApiError } from "../../src/lib/api.ts";
import { youtubeNotAvailable } from "../../src/content/windowsAppStatus.ts";
import { getWindowsRelease } from "../../src/content/windowsRelease.ts";
import type { BillingAvailability } from "../../src/lib/useBillingAvailability.ts";
import { FaqPage } from "../../src/pages/public/FaqPage.tsx";
import { committedRelease } from "../helpers/committedRelease.ts";

let plans: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  // the page asks GET /v1/plans once; by default it fails, so the static answers stay
  plans = vi.spyOn(api, "plans").mockRejectedValue(new ApiError(0, "NETWORK", "offline", true));
});
afterEach(() => plans.mockRestore());

/** One answer's text for a given server answer, rendered outside the page. */
function answerFor(availability: BillingAvailability | null, q: string): string {
  const entry = faqGroups(availability)
    .flatMap((g) => g.entries)
    .find((e) => e.q === q)!;
  return typeof entry.a === "string" ? entry.a : renderToStaticMarkup(<MemoryRouter>{entry.a}</MemoryRouter>).replace(/<[^>]+>/g, "");
}

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
    expect(answer("What counts as an AI request?")).toMatch(/free and never count against your AI allowance/);
    expect(answer("What is Screen view, and when is it coming?")).toMatch(/10 minutes per 5-hour window\. Pro and Pro Plus will not have Free’s time limit, but fair-use limits apply/);
    expect(answer("What is Screen view, and when is it coming?")).not.toMatch(/unlimited/i);
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

  it("states prices as tax included everywhere, never before tax", () => {
    for (const availability of [null, { selling: true, mode: "live", aiEnabled: true } as const]) {
      const text = faqGroups(availability)
        .flatMap((g) => g.entries)
        .map((e) => (typeof e.a === "string" ? e.a : renderToStaticMarkup(<MemoryRouter>{e.a}</MemoryRouter>)))
        .join(" ");
      expect(text).not.toMatch(/before tax/);
    }
    expect(answerFor(null, "What do Pro and Pro Plus include?")).toMatch(/in US dollars, tax included \(the price shown is the amount charged\)/);
    expect(answerFor(null, "What do Pro and Pro Plus include?")).not.toMatch(/at paid launch\. The difference/);
    expect(answerFor(null, "What do Pro and Pro Plus include?")).toMatch(/custom remote layouts and saved routines are coming later: none of them is in the app yet/);
  });

  it("follows the server: static wording until it answers, how to subscribe once it sells, and Pro Plus only with the AI butler on", async () => {
    const off = null;
    const sellingAiOff: BillingAvailability = { selling: true, mode: "live", aiEnabled: false };
    const sellingAiOn: BillingAvailability = { selling: true, mode: "live", aiEnabled: true };
    const buy = "Can I buy Pro or Pro Plus today?";
    expect(answerFor(off, buy)).toMatch(/^Not yet\. Only Free exists today/);
    // the AI butler is off in production: the taste is not promised as usable today
    expect(answerFor(off, buy)).toMatch(/once the AI butler is switched on for DoMe accounts \(it is not yet\)/);
    expect(answerFor(sellingAiOff, buy)).toMatch(/^Yes: Pro can be bought from Billing/);
    expect(answerFor(sellingAiOff, buy)).toMatch(/Pro Plus goes on sale once the AI butler is switched on/);
    expect(answerFor(sellingAiOn, buy)).toMatch(/^Yes: Pro and Pro Plus can be bought from Billing/);
    expect(answerFor(sellingAiOn, buy)).toMatch(/so you can try the butler first/);
    for (const a of [sellingAiOff, sellingAiOn]) {
      expect(answerFor(a, buy)).not.toMatch(/Not yet|nothing can be bought/);
      expect(answerFor(a, "What do Pro and Pro Plus include?")).toMatch(/Subscribe from Billing in the DoMe web app/);
      expect(answerFor(a, "How do I cancel?")).not.toMatch(/once paid plans launch/);
    }
    expect(answerFor(off, "How do I cancel?")).toMatch(/once paid plans launch/);

    // the page itself asks the server and switches its answers
    const sold: rest.PlansResponse = { plans: {}, pricing: { currency: "USD", plans: { pro: { monthly_cents: 999, annual_cents: 9999 }, pro_plus: { monthly_cents: 2499, annual_cents: 24999 } }, topup: { requests: 100, price_cents: 299, max_balance_requests: 1000 } }, billing_enabled: true, billing_mode: "live", ai_enabled: false };
    plans.mockResolvedValue(sold);
    const { container } = render(
      <MemoryRouter initialEntries={["/faq"]}>
        <FaqPage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(container.textContent).toContain("Yes: Pro can be bought from Billing"));
    expect(container.textContent).not.toContain("nothing can be bought in this version");
  });

  it("answers the YouTube question from the offered release: not in DoMe for Windows 0.1.0, which has no browser add-on", () => {
    const q = "Can DoMe control YouTube when the browser is in the background?";
    const youtubeAnswer = (youtube: string | null) => faqGroups(null, youtube).flatMap((g) => g.entries).find((e) => e.q === q)!.a as string;
    const r = committedRelease();
    expect(r.browserAddon).toBe(false);
    const committed = `Not yet. It needs the DoMe extension for Chrome or Edge, which is not in the Chrome or Edge stores yet, so DoMe for Windows ${r.version} does not include it. When the extension is available, it will control the player in the chosen tab directly, so another window can have focus, and it will only ask for access to YouTube pages.`;
    expect(answerFor(null, q)).toBe(committed);
    // the rendered page says the same
    const { container } = render(
      <MemoryRouter initialEntries={["/faq"]}>
        <FaqPage />
      </MemoryRouter>,
    );
    const details = Array.from(container.querySelectorAll("details")).find((d) => d.querySelector("summary")?.textContent?.includes(q))!;
    expect(details.textContent).toContain(committed);
    expect(details.textContent).not.toMatch(/Yes\./);
    // before a release: not yet, without a version
    expect(youtubeAnswer(youtubeNotAvailable(null))).toMatch(/^Not yet\. It needs the DoMe extension for Chrome or Edge, which is not in the Chrome or Edge stores yet\. When/);
    // a release with the browser add-on: yes
    const withAddon = { ...getWindowsRelease()!, version: "0.2.0", browserAddon: true };
    expect(youtubeNotAvailable(withAddon)).toBeNull();
    expect(youtubeAnswer(youtubeNotAvailable(withAddon))).toMatch(/^Yes\. The DoMe browser extension in Chrome or Edge on the PC controls the player/);
  });
});


/**
 * The restyled reading pages keep their meaning: the legal drafts carry the DRAFT note and their sections,
 * the download button stays disabled with the honest copy, the 404 keeps both ways out.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { DownloadPage } from "../../src/pages/public/DownloadPage.tsx";
import { NotFoundPage } from "../../src/pages/public/NotFoundPage.tsx";
import { PrivacyPage } from "../../src/pages/public/PrivacyPage.tsx";
import { ReleaseNotesPage } from "../../src/pages/public/ReleaseNotesPage.tsx";
import { TermsPage } from "../../src/pages/public/TermsPage.tsx";

function page(el: React.ReactElement) {
  return render(<MemoryRouter>{el}</MemoryRouter>);
}

describe("public reading pages", () => {
  it("Privacy keeps the DRAFT note and every section of the draft", () => {
    const { container } = page(<PrivacyPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Privacy notice" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/DRAFT — prepared for founder review/);
    const headings = Array.from(container.querySelectorAll("h2")).map((h) => h.textContent);
    expect(headings).toEqual([
      "Who we are",
      "What DoMe is",
      "What we collect and why",
      "Data that passes through the DoMe service",
      "On your phone",
      "On your PC",
      "The AI butler (optional)",
      "Screen view (beta, optional)",
      "What we never collect",
      "Cookies",
      "Who receives data (sub-processors)",
      "How long we keep data",
      "Deleting your account",
      "Your choices",
      "Security",
      "Children",
      "Your privacy rights in US states",
      "Changes to this notice",
      "Contact",
      "Open items for the founder and counsel",
    ]);
    const text = container.textContent ?? "";
    expect(text).toContain("Never sent to Anthropic: audio, your screen");
    // the connection relay provider is named as a sub-processor item, with what it sees and what it never sees
    expect(text).toContain("connection addresses, timing and byte counts; never the picture");
    expect(text).toContain("It never passes through the DoMe service");
    expect(text).toContain("DoMe is not end-to-end encrypted and we do not claim otherwise");
    // the only two cookies, and every sub-processor by name
    expect(text).toContain("dome_session");
    expect(text).toContain("dome_reprompt");
    for (const vendor of ["Fly.io", "Auth0", "Resend", "Stripe", "Cloudflare", "Anthropic"]) expect(text).toContain(vendor);
    // no account-deletion promise the app cannot keep yet
    expect(text).toContain("The app does not have a button to delete your account yet.");
    // what the code does: Screen view's temporary ports, the cookie set once at sign-in, linking codes kept after deletion
    expect(text).toContain("During a Screen view session it opens temporary network ports");
    expect(text).toContain("Your browser keeps it for 30 days from sign-in.");
    expect(text).toContain("PC linking codes are also kept without the link to your account");
    // business facts stay placeholders for the founder; legal judgement stays marked for counsel
    expect(text).toContain("[FOUNDER: legal name of the business]");
    expect(text).toContain("[COUNSEL:");
    expect(screen.getByRole("link", { name: "Terms of service" })).toHaveAttribute("href", "/terms");
    expect(screen.getByRole("link", { name: "support form" })).toHaveAttribute("href", "/support");
  });

  it("Terms keeps the DRAFT note and every section of the draft", () => {
    const { container } = page(<TermsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Terms of service" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/Not yet legally reviewed/);
    const headings = Array.from(container.querySelectorAll("h2")).map((h) => h.textContent);
    expect(headings).toEqual([
      "About these terms",
      "The service",
      "Who may use DoMe",
      "Accounts",
      "Acceptable use",
      "The Windows program and browser extension",
      "Free, Pro and Pro Plus",
      "Billing and automatic renewal",
      "Cancelling",
      "Refunds",
      "Price changes",
      "Screen view (beta)",
      "The AI butler",
      "Availability and limits",
      "Suspension and ending your account",
      "Disclaimer and liability",
      "Governing law",
      "Disputes",
      "Changes to these terms",
      "Contact",
      "Open items for the founder and counsel",
    ]);
    const text = container.textContent ?? "";
    expect(text).toContain("Prices shown before paid launch are planned, not binding.");
    expect(text).toContain("Your subscription renews automatically");
    expect(text).toContain("The AI can make mistakes.");
    // no promise of published abuse limits that no customer page shows
    expect(text).toContain("Technical limits protect the service");
    expect(text).not.toContain("published abuse limits");
    // the licence wording the Windows installer's licence terms also carry (pc-agent/packaging/installer/licence-terms.txt)
    expect(text).toContain("you may modify DoMe for your own use and reverse engineer it to debug such modifications");
    // the same placeholders as the licence terms and third-party notices
    for (const placeholder of ["[FOUNDER: legal name of the business]", "[FOUNDER: year]", "[FOUNDER: governing law (New Jersey?)]"]) expect(text).toContain(placeholder);
    expect(text).toContain("[COUNSEL:");
    expect(screen.getByRole("link", { name: "Privacy notice" })).toHaveAttribute("href", "/privacy");
    expect(screen.getByRole("link", { name: "Pricing page" })).toHaveAttribute("href", "/pricing");
    expect(screen.getByRole("link", { name: "support form" })).toHaveAttribute("href", "/support");
  });

  it("Download stays honest: a disabled button, the status copy, the requirements and the iPhone steps", () => {
    const { container } = page(<DownloadPage />);
    const button = screen.getByTestId("download-button");
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleName("Download for Windows 10/11 — not yet published");
    expect(button).toHaveTextContent("Windows app: not yet published");
    expect(button).toHaveAttribute("aria-describedby", "download-status");
    expect(container.querySelector("#download-status")).toHaveTextContent(/Release status: not yet published\./);
    expect(container.textContent).toMatch(/never to disable antivirus or other protection/);
    expect(screen.getByRole("link", { name: "release history and known issues" })).toHaveAttribute("href", "/release-notes");
    expect(screen.getByRole("heading", { name: "What the app will need" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Install DoMe on your iPhone" })).toBeInTheDocument();
    expect(container.textContent).toContain("Tap Share, then “Add to Home Screen”, then Add.");
  });

  it("Release notes show the pre-release status, known issues with workarounds and each release's verification", () => {
    const { container } = page(<ReleaseNotesPage />);
    expect(container.textContent).toMatch(/pre-release/);
    expect(screen.getByRole("heading", { name: "Known issues" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Releases" })).toBeInTheDocument();
    expect(container.textContent).toMatch(/Real iPhone Safari/);
    expect(container.querySelectorAll("details").length).toBeGreaterThan(0);
    expect(container.textContent).toContain("Workaround");
  });

  it("404 offers the website and the app", () => {
    page(<NotFoundPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Website" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Open the app/ })).toHaveAttribute("href", "/app");
  });
});

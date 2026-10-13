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
    expect(headings).toEqual(["What DoMe is", "Data we hold for your account", "Data that passes through the DoMe service", "On your phone", "The AI butler (optional)", "On your PC", "Your choices", "Open items for founder review"]);
    expect(container.textContent).toContain("Never sent to Anthropic: audio, your screen");
    expect(container.textContent).toContain("DoMe is not end-to-end encrypted and we do not claim otherwise");
  });

  it("Terms keeps the DRAFT note and every section of the draft", () => {
    const { container } = page(<TermsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Terms of service" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/Not yet legally reviewed/);
    const headings = Array.from(container.querySelectorAll("h2")).map((h) => h.textContent);
    expect(headings).toEqual(["The service", "Accounts", "Acceptable use", "Free, Pro and Pro Plus", "Availability and limits", "Disclaimer and liability", "Changes and termination", "Open items for founder review"]);
    expect(container.textContent).toContain("Prices shown before paid launch are planned, not binding.");
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

/**
 * The restyled reading pages keep their meaning: the legal drafts carry the DRAFT note and their sections,
 * the download button stays disabled with the honest copy until a release exists and offers the committed release
 * (DoMe for Windows 0.1.0) with its caveats once it does, the release notes describe that release, the 404 keeps
 * both ways out.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { committedRelease } from "../helpers/committedRelease.ts";

// The Download page reads src/content/windows-release.json: the real, committed release, unless a test asks for the
// placeholder the site carried before the first release, so both states stay tested.
const release = vi.hoisted(() => ({ placeholder: false }));
vi.mock("../../src/content/windows-release.json", async (importOriginal) => (await import("../helpers/committedRelease.ts")).releaseOrPlaceholder(importOriginal, release));

import { DownloadPage } from "../../src/pages/public/DownloadPage.tsx";
import { NotFoundPage } from "../../src/pages/public/NotFoundPage.tsx";
import { PrivacyPage } from "../../src/pages/public/PrivacyPage.tsx";
import { ReleaseNotesPage } from "../../src/pages/public/ReleaseNotesPage.tsx";
import { TermsPage } from "../../src/pages/public/TermsPage.tsx";

function page(el: React.ReactElement) {
  return render(<MemoryRouter>{el}</MemoryRouter>);
}

afterEach(() => {
  release.placeholder = false;
});

/** Every sentence that mentions switching protection off is a promise never to ask for it; nothing says "Run anyway". */
function expectNoOfferToDisableProtection(text: string) {
  const sentences = text.split(/(?<=[.!?])\s+/);
  for (const sentence of sentences.filter((x) => /\b(disable|turn off|switch off)\b/i.test(x))) expect(sentence).toMatch(/\bnever\b/i);
  expect(text).not.toMatch(/run anyway/i);
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

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
    // account deletion as the app does it: self-serve, the 30-day erase, and the Auth0 sign-in it does not delete
    expect(text).not.toContain("The app does not have a button to delete your account yet.");
    expect(text).toContain("You can delete your account yourself in the app: Settings, then Delete account.");
    expect(text).toContain("Thirty days later (or once Stripe has confirmed the cancellation, if that takes longer) the account is erased");
    expect(text).toContain("Your sign-in at Auth0 (your email address and password) is separate and is not deleted by DoMe.");
    // what the code does: Screen view's temporary ports, the cookie set once at sign-in, linking codes kept after deletion
    expect(text).toContain("During a Screen view session it opens temporary network ports");
    expect(text).toContain("Your browser keeps it for 30 days from sign-in.");
    expect(text).toContain("PC linking codes are also kept without the link to your account");
    // the published program does not check for updates (pc-agent/dome_agent/update_check.py is not started): no daily check today
    expect(text).toContain("DoMe for Windows 0.1.0 does not check for newer versions yet");
    expect(text).toContain("A later version will check GitHub about once a day");
    expect(text).not.toContain("About once a day the program checks GitHub");
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
    // deleting the account is in the app now, not a support request
    expect(text).toContain("in the app under Settings, Delete account");
    expect(text).not.toContain("There is no delete button in the app yet");
    expect(text).not.toContain("published abuse limits");
    // the licence wording the Windows installer's licence terms also carry (pc-agent/packaging/installer/licence-terms.txt)
    expect(text).toContain("you may modify DoMe for your own use and reverse engineer it to debug such modifications");
    // the published program does not check for updates yet; a later version will
    expect(text).toContain("DoMe for Windows 0.1.0 does not check for new versions yet");
    expect(text).not.toContain("About once a day the program checks for a new version.");
    // the same placeholders as the licence terms and third-party notices
    for (const placeholder of ["[FOUNDER: legal name of the business]", "[FOUNDER: year]", "[FOUNDER: governing law (New Jersey?)]"]) expect(text).toContain(placeholder);
    expect(text).toContain("[COUNSEL:");
    expect(screen.getByRole("link", { name: "Privacy notice" })).toHaveAttribute("href", "/privacy");
    expect(screen.getByRole("link", { name: "Pricing page" })).toHaveAttribute("href", "/pricing");
    expect(screen.getByRole("link", { name: "support form" })).toHaveAttribute("href", "/support");
  });

  it("Download stays honest before a release: a disabled button, the status copy, the requirements and the iPhone steps", () => {
    release.placeholder = true;
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

  it("Download offers the committed release: an enabled link to exactly its installer, its version, SHA-256 and publisher, and what this version leaves out", () => {
    const r = committedRelease();
    const { container } = page(<DownloadPage />);
    const button = screen.getByTestId("download-button");
    // a real link, not the disabled status line, to exactly the release's installer on DoMe's GitHub releases
    expect(button.tagName).toBe("A");
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute("aria-disabled");
    const installer = `https://github.com/Heshh999/DoMe/releases/download/v${r.version}/DoMe-Setup-${r.version}.exe`;
    expect(r.url).toBe(installer);
    expect(button).toHaveAttribute("href", installer);
    expect(button).toHaveAttribute("download");
    expect(button).toHaveAccessibleName(new RegExp(`^Download DoMe-Setup-${escapeRe(r.version)}\\.exe \\(\\d+ MB\\)$`));
    expect(container.querySelectorAll('a[href$=".exe"], a[href$=".msi"]')).toHaveLength(1);
    // the version, the SHA-256 and the publisher Windows shows
    const status = container.querySelector("#download-status");
    expect(status).toHaveTextContent(`Release status: ${r.version}, published `);
    expect(status).toHaveTextContent(`Signed for Windows by ${r.publisher} through Azure Artifact Signing.`);
    expect(status).toHaveTextContent(`SHA-256 ${r.sha256}`);
    expect(container.textContent).toContain(`Get-FileHash .\\DoMe-Setup-${r.version}.exe -Algorithm SHA256`);
    const text = container.textContent ?? "";
    // SmartScreen: check the publisher; protection stays on
    expect(text).toContain(`The dialog names the publisher: continue only if it says ${r.publisher}. We never ask you to turn off antivirus or other protection.`);
    expect(text.match(/antivirus/g)).toHaveLength(1);
    expectNoOfferToDisableProtection(text);
    // what this version leaves out: YouTube control in Chrome or Edge, and Screen view is not switched on for accounts
    expect(r.browserAddon).toBe(false);
    expect(text).toContain("This version does not include YouTube control in Chrome or Edge; everything else in DoMe is included.");
    expect(screen.getByRole("heading", { name: "Chrome or Edge, for YouTube control (not in this version)" })).toBeInTheDocument();
    expect(screen.getByTestId("screen-view-note")).toHaveTextContent("The program includes Screen view (beta), but it is not switched on for DoMe accounts yet, so it cannot be used.");
    // what was tested, and what was not
    expect(screen.getByTestId("tested-note")).toHaveTextContent(/tested so far only on GitHub’s Windows Server test machine \(an administrator account with UAC off\)/);
    expect(screen.getByTestId("tested-note")).toHaveTextContent(/not yet been tested on a Windows 10 or 11 PC or by a standard user/);
    expect(text).not.toMatch(/not yet published/);
    expect(screen.getByRole("link", { name: "release page on GitHub" })).toHaveAttribute("href", r.releasePage);
    expect(screen.getByRole("link", { name: "release history and known issues" })).toHaveAttribute("href", "/release-notes");
    expect(screen.getByRole("heading", { name: "What the app needs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Install DoMe on your iPhone" })).toBeInTheDocument();
  });

  it("Release notes show the beta status, the committed release with what it leaves out and what was tested, and known issues with workarounds", () => {
    const r = committedRelease();
    const { container } = page(<ReleaseNotesPage />);
    const text = container.textContent ?? "";
    expect(text).toContain("Beta");
    expect(text).toContain(`DoMe for Windows ${r.version} is published as a free beta. This release has not been verified on a Windows 10 or 11 PC or on an iPhone yet.`);
    expect(screen.getByRole("heading", { name: "Known issues" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Releases" })).toBeInTheDocument();
    expect(text).toMatch(/Real iPhone Safari/);
    expect(container.querySelectorAll("details").length).toBeGreaterThan(0);
    expect(text).toContain("Workaround");

    // the newest release is the one the Download page offers, on the day its version information was written
    const newest = container.querySelector<HTMLElement>(".dome-timeline > li article");
    expect(newest).not.toBeNull();
    expect(within(newest!).getByRole("heading", { level: 3 })).toHaveTextContent(`DoMe for Windows ${r.version}`);
    expect(newest).toHaveTextContent(r.publishedAt.slice(0, 10));
    expect(newest).toHaveTextContent("published — free beta");
    expect(newest).toHaveTextContent(`signed for Windows by ${r.publisher} through Azure Artifact Signing`);
    expect(newest).toHaveTextContent("Not in this version: YouTube control in Chrome or Edge.");
    expect(newest).toHaveTextContent("Screen view (beta) is in the program, but it is not switched on for DoMe accounts yet, so it cannot be used.");
    expect(newest).toHaveTextContent("Free: nothing can be bought yet. The licence terms Setup shows are still a draft");
    expect(newest).toHaveTextContent("Integration-tested on GitHub’s Windows Server test machine (an administrator account with UAC off): install, upgrade and uninstall of the signed Setup");
    expect(newest).toHaveTextContent("Install, upgrade and uninstall on a Windows 10 or 11 PC, and by a standard (non-administrator) user.");
    expect(newest).toHaveTextContent("Windows 11 PCs with Smart App Control on.");
    expect(newest).toHaveTextContent("Screen view on any device.");
    // the older builds stay in the history as what they were, named so that none reads as the published version
    expect(text).toContain("development build — not published");
    expect(text).toContain("internal build — not published");
    const releases = within(screen.getByRole("region", { name: "Releases" }))
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(releases).toEqual([`DoMe for Windows ${r.version}`, "Development build (protocol 1.1)", "Internal build (protocol 1.0)"]);
    expect(text).toContain("YouTube control through the browser extension in a background tab (not in the published DoMe for Windows 0.1.0)");

    // known issues: the release's own first, then the ones that still apply; "no download yet" is gone
    const issues = within(screen.getByRole("region", { name: "Known issues" }))
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(issues).toEqual([
      "Windows may warn about DoMe while this version is new",
      "Not yet verified with Smart App Control",
      "No YouTube control in Chrome or Edge in this version",
      "Screen view is not switched on yet",
      "Linking the PC to your account takes one command",
      "DoMe for Windows does not check for updates yet",
      "Double tap on the touchpad relies on Windows’ double-click timing",
      "Live typing pauses after some edits",
      "The phone cannot see which field has focus on the PC",
      "Lock screen, sign-in and administrator prompts cannot be controlled",
      "Freshness is re-evaluated every 5 seconds",
    ]);
    expect(text).not.toContain("No official Windows download yet");
    expect(text).toContain(`Check that the publisher the dialog names is ${r.publisher} before you continue. DoMe never asks you to turn off antivirus or other protection.`);
    expect(text).toContain("If Smart App Control blocks DoMe, do not turn Smart App Control off");
    expectNoOfferToDisableProtection(text);
  });

  it("404 offers the website and the app", () => {
    page(<NotFoundPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Website" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Open the app/ })).toHaveAttribute("href", "/app");
  });
});

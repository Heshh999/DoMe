/**
 * The Download page offers a real download only for a valid signed release in src/content/windows-release.json
 * (mocked here): the placeholder and anything that breaks a rule keep the disabled "not yet published" state;
 * a published release shows its exact GitHub address, size, publisher, SHA-256, the SmartScreen and Smart App Control caveats,
 * that Screen view is not switched on for accounts, what it was tested on and, when it has no browser add-on, says so (also
 * in what the app needs). Nothing ever tells people to turn protection off. The committed release (DoMe for Windows 0.1.0)
 * is rendered from the real committed file (through a pass-through mock that can switch to the placeholder,
 * test/helpers/committedRelease.ts) in test/public/pages.test.tsx and test/components/UpgradeAndHealth.test.tsx.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const content = vi.hoisted(() => ({ value: { status: "not-published" } as Record<string, unknown> }));

vi.mock("../../src/content/windows-release.json", () => ({
  get default() {
    return content.value;
  },
}));

import { DownloadPage, SCREEN_VIEW_NOTE, SMART_APP_CONTROL_NOTE, TESTED_NOTE } from "../../src/pages/public/DownloadPage.tsx";

const SHA = "3f".repeat(32);
const PUBLISHED = {
  browser_addon: false,
  product: "DoMe for Windows",
  published_at: "2026-11-02T14:03:11Z",
  publisher: "Jane Example",
  release_page: "https://github.com/Heshh999/DoMe/releases/tag/v0.2.0",
  schema: 1,
  sha256: SHA,
  size: 41_234_567,
  url: "https://github.com/Heshh999/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe",
  version: "0.2.0",
};

function page() {
  return render(
    <MemoryRouter>
      <DownloadPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  content.value = { status: "not-published" };
});

function expectNotPublished(container: HTMLElement) {
  const button = screen.getByTestId("download-button");
  expect(button.tagName).toBe("BUTTON");
  expect(button).toBeDisabled();
  expect(button).toHaveAccessibleName("Download for Windows 10/11 — not yet published");
  expect(container.querySelector("#download-status")).toHaveTextContent(/Release status: not yet published\./);
  expect(container.querySelector('a[href*="github.com"]')).toBeNull();
  // nothing about a published version: no Screen view or test caveats, and the requirements are still ahead
  expect(screen.queryByTestId("screen-view-note")).toBeNull();
  expect(screen.queryByTestId("tested-note")).toBeNull();
  expect(screen.getByRole("heading", { name: "What the app will need" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Chrome or Edge, for YouTube control" })).toBeInTheDocument();
}

describe("Download page", () => {
  it("the placeholder keeps the disabled, not-yet-published state", () => {
    const { container } = page();
    expectNotPublished(container);
  });

  it("a published release is a real download with its address, size, publisher and SHA-256", () => {
    content.value = { ...PUBLISHED };
    const { container } = page();
    const link = screen.getByRole("link", { name: "Download DoMe-Setup-0.2.0.exe (41 MB)" });
    expect(link).toHaveAttribute("href", PUBLISHED.url);
    expect(link).toHaveAttribute("download");
    expect(link).toHaveAttribute("aria-describedby", "download-status");
    expect(link).toHaveAttribute("data-testid", "download-button");
    const status = container.querySelector("#download-status");
    expect(status).toHaveTextContent("Release status: 0.2.0, published 2 November 2026.");
    expect(status).toHaveTextContent("Signed for Windows by Jane Example through Azure Artifact Signing.");
    expect(status).toHaveTextContent(`SHA-256 ${SHA}`);
    expect(container.textContent).toContain("Get-FileHash .\\DoMe-Setup-0.2.0.exe -Algorithm SHA256");
    expect(container.textContent).toContain("compare the result with the SHA-256 above");
    expect(container.textContent).toContain(
      "While a version is new, Windows may show “Windows protected your PC”. The dialog names the publisher: continue only if it says Jane Example. We never ask you to turn off antivirus or other protection.",
    );
    // never an unverified guarantee: the Setup's own wording, not "everything else works"
    expect(container.textContent).toContain("This version does not include YouTube control in Chrome or Edge; everything else in DoMe is included.");
    expect(container.textContent).not.toContain("everything else works");
    // Screen view is in the program but switched off for accounts on the server: the page says so
    expect(screen.getByTestId("screen-view-note")).toHaveTextContent(SCREEN_VIEW_NOTE);
    expect(SCREEN_VIEW_NOTE).toMatch(/not switched on for DoMe accounts yet, so it cannot be used/);
    // what was tested, and what was not
    expect(screen.getByTestId("tested-note")).toHaveTextContent(TESTED_NOTE);
    expect(TESTED_NOTE).toMatch(/only on GitHub’s Windows Server test machine \(an administrator account with UAC off\): install, upgrade, uninstall and the signatures/);
    expect(TESTED_NOTE).toMatch(/not yet been tested on a Windows 10 or 11 PC or by a standard user/);
    // the requirements say the browser part is not in this version, instead of asking for an extension that cannot be had
    expect(screen.getByRole("heading", { name: "What the app needs" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "What the app will need" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Chrome or Edge, for YouTube control (not in this version)" })).toBeInTheDocument();
    expect(container.textContent).toContain("YouTube control in Chrome or Edge is not part of this version: it needs the DoMe extension, which is not in the Chrome or Edge stores yet.");
    expect(container.textContent).not.toContain("with the DoMe extension if you want YouTube control");
    // signing is not a Smart App Control promise (docs/SECURITY.md T12): the page says it is not verified
    expect(screen.getByTestId("smart-app-control")).toHaveTextContent(SMART_APP_CONTROL_NOTE);
    expect(SMART_APP_CONTROL_NOTE).toMatch(/^Not yet verified on Windows 11 PCs with Smart App Control on; if it blocks DoMe, do not turn it off/);
    expect(screen.getByRole("link", { name: "release page on GitHub" })).toHaveAttribute("href", PUBLISHED.release_page);
    expect(screen.getByRole("link", { name: "release history and known issues" })).toHaveAttribute("href", "/release-notes");
    expect(screen.getByRole("heading", { name: "Install DoMe on your iPhone" })).toBeInTheDocument();
    expect(screen.queryByText(/not yet published/)).toBeNull();
  });

  it("says nothing about missing YouTube control when the release includes the add-on", () => {
    content.value = { ...PUBLISHED, browser_addon: true };
    const { container } = page();
    expect(screen.getByRole("link", { name: /Download DoMe-Setup-0\.2\.0\.exe/ })).toBeInTheDocument();
    expect(container.textContent).not.toContain("does not include YouTube control");
    expect(container.textContent).not.toContain("not part of this version");
    expect(screen.getByRole("heading", { name: "Chrome or Edge, for YouTube control" })).toBeInTheDocument();
    // Screen view's switch is on the server, not in the release: still not switched on
    expect(screen.getByTestId("screen-view-note")).toHaveTextContent(SCREEN_VIEW_NOTE);
  });

  it.each([
    ["a download address off GitHub", { url: "https://evil.example/DoMe-Setup-0.2.0.exe" }],
    ["another repository", { url: "https://github.com/someone/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe" }],
    ["a bad SHA-256", { sha256: "XYZ" }],
    ["no publisher", { publisher: "" }],
    ["an unknown schema", { schema: 2 }],
    ["an extra field", { note: "download now" }],
  ])("treats a release with %s as not published", (_label, change) => {
    content.value = { ...PUBLISHED, ...change };
    const { container } = page();
    expectNotPublished(container);
  });

  it("never tells anyone to disable protection, in either state", () => {
    for (const value of [{ status: "not-published" }, { ...PUBLISHED }]) {
      content.value = value;
      const { container, unmount } = page();
      const sentences = (container.textContent ?? "").split(/(?<=[.!?])\s+/);
      // every sentence that mentions switching something off is a promise not to ask for it
      for (const sentence of sentences.filter((x) => /\b(disable|turn off|switch off)\b/i.test(x))) expect(sentence).toMatch(/\bnever\b/i);
      expect(sentences.join(" ")).not.toMatch(/run anyway/i);
      unmount();
    }
  });
});

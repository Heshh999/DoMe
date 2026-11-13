/**
 * What the website says about the Windows app outside the Download page (src/content/windowsAppStatus.ts): "not
 * published yet" only while there is no release, the release's version as a free beta once there is one, and YouTube
 * control promised only by a release with the browser add-on. The pages that show these lines are tested with the
 * committed release and the placeholder in test/film/LandingPage.test.tsx, test/film/DemoPage.test.tsx,
 * test/public/PublicLayout.test.tsx and test/public/FaqPage.test.tsx.
 */
import { describe, expect, it } from "vitest";

import { windowsAppStatus, youtubeNotAvailable } from "../../src/content/windowsAppStatus.ts";
import type { WindowsRelease } from "../../src/content/windowsRelease.ts";

const RELEASE: WindowsRelease = {
  version: "0.2.0",
  url: "https://github.com/Heshh999/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe",
  sha256: "3f".repeat(32),
  size: 40_000_000,
  publishedAt: "2026-12-01T10:00:00Z",
  releasePage: "https://github.com/Heshh999/DoMe/releases/tag/v0.2.0",
  publisher: "Justin Gaskins",
  browserAddon: false,
};

describe("the Windows app's status on the website", () => {
  it("before a release: not published yet, everywhere", () => {
    const s = windowsAppStatus(null);
    expect(s.heroButton).toBe("Windows app · coming soon");
    for (const line of [s.heroCaveat, s.footer, s.demo]) expect(line).toMatch(/not published yet/);
    expect(Object.values(s).join(" ")).not.toMatch(/beta/);
    expect(youtubeNotAvailable(null)).toBe("It needs the DoMe extension for Chrome or Edge, which is not in the Chrome or Edge stores yet.");
  });

  it("with a release: its version as a free beta, never 'not published' or 'coming soon'", () => {
    const s = windowsAppStatus(RELEASE);
    expect(s.heroButton).toBe("Windows app · free beta");
    expect(s.heroCaveat).toBe("DoMe for Windows 0.2.0 is a free beta, not yet tested on a Windows 10 or 11 PC: see Download for what it includes.");
    expect(s.footer).toBe("Free beta: DoMe for Windows 0.2.0 has not been tested on a Windows 10 or 11 PC yet.");
    expect(s.demo).toBe("DoMe for Windows 0.2.0 is a free beta without YouTube control in Chrome or Edge: see Download for what it includes.");
    expect(Object.values(s).join(" ")).not.toMatch(/not published|coming soon/i);
    expect(youtubeNotAvailable(RELEASE)).toBe("It needs the DoMe extension for Chrome or Edge, which is not in the Chrome or Edge stores yet, so DoMe for Windows 0.2.0 does not include it.");
  });

  it("a release with the browser add-on: YouTube control is no longer called missing", () => {
    const withAddon = { ...RELEASE, browserAddon: true };
    expect(youtubeNotAvailable(withAddon)).toBeNull();
    expect(windowsAppStatus(withAddon).demo).toBe("DoMe for Windows 0.2.0 is a free beta: see Download for what it includes.");
  });
});

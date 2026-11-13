/**
 * What the website says about the Windows app outside the Download page: the home page's hero button and the line
 * under it, the footer of every public page, the demo page's closing line, the home page's YouTube card and the FAQ's
 * YouTube answer. All of it follows the release the Download page offers (windowsRelease.ts), so no page can say
 * "not published yet" next to a live download, or promise YouTube control that the published version leaves out.
 *
 * Every release so far is a free beta (the founder's decision of 2026-11-13: free, nothing can be bought, not yet
 * tested on a Windows 10 or 11 PC). Change the wording here when a release stops being one.
 */
import { getWindowsRelease, type WindowsRelease } from "./windowsRelease.ts";

export interface WindowsAppStatus {
  /** The home page's secondary hero button, which links to /download. */
  heroButton: string;
  /** The small line under the hero buttons. */
  heroCaveat: string;
  /** The footer of every public page. */
  footer: string;
  /** The demo page's closing line, after the scenes. */
  demo: string;
}

/** The status lines for `release`; by default the committed one, read at call time. */
export function windowsAppStatus(release: WindowsRelease | null = getWindowsRelease()): WindowsAppStatus {
  if (!release) {
    return {
      heroButton: "Windows app · coming soon",
      heroCaveat: "DoMe is in development. The Windows app is not published yet: see Download for its status.",
      footer: "In development: the Windows app is not published yet.",
      demo: "DoMe is in development: the Windows app is not published yet.",
    };
  }
  const name = `DoMe for Windows ${release.version}`;
  return {
    heroButton: "Windows app · free beta",
    heroCaveat: `${name} is a free beta, not yet tested on a Windows 10 or 11 PC: see Download for what it includes.`,
    footer: `Free beta: ${name} has not been tested on a Windows 10 or 11 PC yet.`,
    demo: `${name} is a free beta${release.browserAddon ? "" : " without YouTube control in Chrome or Edge"}: see Download for what it includes.`,
  };
}

/**
 * YouTube control needs the DoMe browser extension, which only a release with the browser add-on registers; the
 * extension is not in the Chrome or Edge stores yet, so no release has it so far. `null` means it is available.
 */
export function youtubeNotAvailable(release: WindowsRelease | null = getWindowsRelease()): string | null {
  if (release?.browserAddon) return null;
  const store = "It needs the DoMe extension for Chrome or Edge, which is not in the Chrome or Edge stores yet";
  return release ? `${store}, so DoMe for Windows ${release.version} does not include it.` : `${store}.`;
}

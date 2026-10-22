/**
 * Document titles: every page names itself ("Pricing · DoMe Remote", "Remote · DoMe Remote"), so screen readers,
 * the browser history and tab switchers can tell pages apart (WCAG 2.4.2). Setting document.title is CSSOM-free
 * and needs no inline script, so the CSP is unaffected.
 */
import { useEffect } from "react";

import { FOOTER, NAV } from "../content/nav.ts";

export const SITE_NAME = "DoMe Remote";

/** "Page · DoMe Remote", or just the site name when there is no page name. */
export function titled(page: string | null | undefined): string {
  return page ? `${page} · ${SITE_NAME}` : SITE_NAME;
}

/** The public page's title from the header and footer links ("/" is the site name alone). */
export function publicTitle(pathname: string): string {
  if (pathname === "/") return SITE_NAME;
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return titled([...NAV, ...FOOTER.flatMap((g) => g.links)].find((l) => l.to === path)?.label);
}

/** The app's page names by route (the tab or page title), for "Remote · DoMe Remote". */
const APP_PAGES: Record<string, string> = {
  "/app": "Home",
  "/app/remote": "Remote",
  "/app/touchpad": "Touchpad",
  "/app/screen": "Screen view",
  "/app/assistant": "Ask",
  "/app/command": "Type a command",
  "/app/apps": "Apps",
  "/app/devices": "Devices",
  "/app/devices/pair": "Pair this phone",
  "/app/health": "Connection health",
  "/app/settings": "Settings & help",
  "/app/billing": "Billing",
  "/app/routines": "Routines",
  "/app/layouts": "Custom remotes",
  "/app/more": "More",
};

export function appTitle(pathname: string): string {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return titled(APP_PAGES[path]);
}

/** Sets document.title while mounted and whenever it changes. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}

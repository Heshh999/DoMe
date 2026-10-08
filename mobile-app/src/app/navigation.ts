/**
 * The one place the PWA touches `window.location` / `window.history` for the sign-in redirect.
 * Kept behind a tiny seam so the signed-out deep-link path can be exercised in jsdom (which cannot
 * navigate). Nothing here is selectable at runtime: the default always drives the real browser.
 */
export interface Navigation {
  /** Full-page navigation (leaves the app). */
  assign(url: string): void;
  /** Rewrite the address bar in place without a navigation (used to drop a secret fragment). */
  replaceState(url: string): void;
}

const browserNavigation: Navigation = {
  assign: (url) => window.location.assign(url),
  replaceState: (url) => window.history.replaceState(null, "", url),
};

let current: Navigation = browserNavigation;

export function navigation(): Navigation {
  return current;
}

/** Tests only. */
export function setNavigationForTests(nav: Navigation | null): void {
  current = nav ?? browserNavigation;
}

export const PAIR_PATH = "/app/devices/pair";
/** Non-secret marker: the pairing page shows "scan the code again" after a sign-in round trip. */
export const SCAN_AGAIN_PARAM = "scan_again";

export interface SignInRedirect {
  /** What cloud-api gets as `return_to`: path + query only, never a fragment. */
  returnTo: string;
  /** When set, the address bar is rewritten to this (fragment dropped) before leaving the app. */
  scrubTo: string | null;
}

/**
 * Where to come back to after sign-in. The URL fragment is never included: on the pairing deep link
 * it carries the pairing code, which the backend must never see (version.json rules.pairing_secret),
 * and it is not kept anywhere on the phone either — the customer scans the code again after signing
 * in. The fragment is also removed from the address bar so it cannot travel as a referrer.
 */
export function signInRedirect(loc: { pathname: string; search: string; hash: string }): SignInRedirect {
  const base = loc.pathname + loc.search;
  if (!loc.hash || loc.hash === "#") return { returnTo: base, scrubTo: null };
  if (loc.pathname === PAIR_PATH) {
    const params = new URLSearchParams(loc.search);
    params.set(SCAN_AGAIN_PARAM, "1");
    const withHint = `${PAIR_PATH}?${params.toString()}`;
    return { returnTo: withHint, scrubTo: withHint };
  }
  return { returnTo: base, scrubTo: base };
}

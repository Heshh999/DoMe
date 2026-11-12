/**
 * Why cloud-api sent the browser back without signing it in. The sign-in callback redirects to the public home page
 * with `?sign_in=<reason>` (cloud-api `routes/auth.py`, DECISIONS #118, #120 and #121), never to `/app`: the app shell
 * sends a signed-out browser straight back to the identity provider, which remembers the person and would answer the
 * same way again, in a loop. The home page shows the matching notice once and removes the marker from the address bar.
 * Unknown reasons show nothing: the customer never sees a code.
 *
 * `email_unverified`: the provider has not confirmed the address yet (DECISIONS #118).
 * `try_again`: the sign-in could not be finished and starting again is the whole remedy: it took more than ten
 * minutes (the "Forgot password?" detour often does), its answer was opened a second time (Back, reload), or it was
 * cancelled or failed at the provider (DECISIONS #120).
 * `account_deleted`: the identity belongs to a deleted DoMe account (DECISIONS #121). The account is erased 30 days
 * after it was deleted; after that, the same sign-in starts a new, empty account.
 *
 * Also here: the home page's "Your account was deleted." after this app deleted the account (`carryAccountDeleted`).
 */
import type { rest } from "@dome/protocol";

export const SIGN_IN_PARAM = "sign_in";

/** rest.schema.json `sign_in_problem`: the values cloud-api puts in `?sign_in=`. */
export type SignInProblem = rest.SignInProblem;

/**
 * One notice per reason, and each reason says which parts its notice has. Every notice has the title, the text and
 * then the "Sign in again" button (`SIGN_IN_AGAIN`); `wayOut` and `support` are null where a notice has no such part.
 */
export interface SignInNoticeCopy {
  title: string;
  text: string;
  /** A paragraph between the text and the button, `lead` + `strong` (in bold) + `rest`; null: none. */
  wayOut: { lead: string; strong: string; rest: string } | null;
  /** A last line after the button, shown only when a support contact is configured; null: never shown. */
  support: { lead: string; link: string; rest: string } | null;
}

export const SIGN_IN_PROBLEMS: Record<SignInProblem, SignInNoticeCopy> = {
  email_unverified: {
    title: "Confirm your email first",
    text: "We sent a link to your inbox when you signed up. Open it, then sign in again. Can't find it? Check your spam folder.",
    // The way out when the link never came or no longer works (Auth0 sends it once, at sign-up, and it expires;
    // DECISIONS #118): setting a new password through "Forgot password?" on Auth0's login page also confirms the
    // address (founder-run on staging, 2026-11-12). The sign-in the button starts shows Auth0's login page even
    // though Auth0 still remembers the person, because cloud-api set a marker for it with the refusal (DECISIONS #119).
    wayOut: { lead: "No email? Tap Sign in, then ", strong: "Forgot password?", rest: ". Setting a new password confirms your email too." },
    // Never the support page: its form needs a sign-in, which this person cannot complete. The founder sends a new
    // link from Auth0 (docs/LAUNCH_SETUP.md §16, which also says what happened when that was tried on staging).
    support: { lead: "Still nothing? ", link: "Contact support", rest: " and we'll send a new link." },
  },
  try_again: {
    title: "Sign in again",
    text: "That sign-in didn't finish. It may have taken too long. Tap Sign in again to start over.",
    // Nothing is wrong with the email or the password, so no "Forgot password?" paragraph, and nothing for support
    // to send: the button is the whole remedy.
    wayOut: null,
    support: null,
  },
  account_deleted: {
    title: "This account was deleted",
    text: "It will be erased for good 30 days after it was deleted. To use DoMe again before then, sign up with a different email address.",
    // A new password changes nothing here, so no "Forgot password?" paragraph. The support line goes to the contact
    // itself, as for email_unverified: the support page's form needs a sign-in this person cannot complete.
    wayOut: null,
    support: { lead: "Questions? ", link: "Contact support", rest: "." },
  },
};

/** The button every notice has, after its text and its way out. It starts sign-in like the header's Sign in. */
export const SIGN_IN_AGAIN = "Sign in again";

/**
 * How a notice's "Sign in again" starts sign-in (`loginUrl`'s options). After `account_deleted` it asks the provider
 * for its login page (`prompt=login`): the provider still remembers the deleted identity and cloud-api sets no
 * re-prompt marker for it, so a plain sign-in would land on the same notice at once, while the login page is where the
 * person can use a different email address. The other reasons start sign-in exactly like the header's Sign in.
 */
export function signInAgainOptions(problem: SignInProblem): { prompt?: "login" } {
  return problem === "account_deleted" ? { prompt: "login" } : {};
}

/** The support contact (`VITE_DOME_SUPPORT_URL`, mailto: or https://, checked by the Deploy tool), or null. */
export function supportContact(): string | null {
  const url = import.meta.env.VITE_DOME_SUPPORT_URL;
  return typeof url === "string" && url.trim() !== "" ? url.trim() : null;
}

const REASONS = Object.keys(SIGN_IN_PROBLEMS) as SignInProblem[];

export function signInProblem(params: URLSearchParams): SignInProblem | null {
  const reason = params.get(SIGN_IN_PARAM);
  return REASONS.find((r) => r === reason) ?? null;
}

/**
 * The notice survives one reload in the minute after the refusal. The refused callback is a full page load, which can
 * find a newer version of the app; nothing is busy on the home page, so the app reloads at once (DECISIONS 92), after
 * the marker has already left the address bar. The reason is kept in this tab's sessionStorage for CARRY_MS and is
 * only ever one of the known reason codes. Storage that is off or full just means the notice is not carried.
 */
const CARRY_KEY = "dome.sign_in_problem";
export const CARRY_MS = 60_000;

export function carrySignInProblem(problem: SignInProblem, now: number = Date.now()): void {
  try {
    sessionStorage.setItem(CARRY_KEY, JSON.stringify({ problem, at: now }));
  } catch {
    /* not carried */
  }
}

/** The reason carried from a refusal less than CARRY_MS ago in this tab, or null. Reading does not clear it. */
export function carriedSignInProblem(now: number = Date.now()): SignInProblem | null {
  try {
    const raw = sessionStorage.getItem(CARRY_KEY);
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const { problem, at } = value as { problem?: unknown; at?: unknown };
    if (typeof at !== "number" || now < at || now - at > CARRY_MS) return null;
    return REASONS.find((r) => r === problem) ?? null;
  } catch {
    return null;
  }
}

/** The query without the marker (other parameters kept), for `setSearchParams(…, { replace: true })`. */
export function withoutSignInProblem(params: URLSearchParams): URLSearchParams {
  const rest = new URLSearchParams(params);
  rest.delete(SIGN_IN_PARAM);
  return rest;
}

/**
 * What the home page says after this app deleted the account (Settings → Delete account). It is not a reason in the
 * address, so no link can make the home page say it: the app keeps it in this tab's sessionStorage right before it
 * leaves for the home page, and the home page shows it for CARRY_MS, like a carried reason (an update can reload the
 * page at once). Only a time is stored. Storage that is off or full just means the line is not shown.
 */
export const ACCOUNT_DELETED_HERE = "Your account was deleted.";
const DELETED_KEY = "dome.account_deleted";

export function carryAccountDeleted(now: number = Date.now()): void {
  try {
    sessionStorage.setItem(DELETED_KEY, JSON.stringify({ at: now }));
  } catch {
    /* not carried */
  }
}

/** True when this tab deleted the account less than CARRY_MS ago. Reading does not clear it. */
export function carriedAccountDeleted(now: number = Date.now()): boolean {
  try {
    const raw = sessionStorage.getItem(DELETED_KEY);
    if (raw === null) return false;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return false;
    const { at } = value as { at?: unknown };
    return typeof at === "number" && now >= at && now - at <= CARRY_MS;
  } catch {
    return false;
  }
}

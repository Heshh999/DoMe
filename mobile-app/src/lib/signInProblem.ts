/**
 * Why cloud-api sent the browser back without signing it in. The sign-in callback redirects to the public home page
 * with `?sign_in=<reason>` (cloud-api `routes/auth.py`, DECISIONS #118 and #120), never to `/app`: the app shell sends
 * a signed-out browser straight back to the identity provider, which remembers the person and would answer the same
 * way again, in a loop. The home page shows the matching notice once and removes the marker from the address bar.
 * Unknown reasons show nothing: the customer never sees a code.
 *
 * `email_unverified`: the provider has not confirmed the address yet (DECISIONS #118).
 * `try_again`: the sign-in could not be finished and starting again is the whole remedy: it took more than ten
 * minutes (the "Forgot password?" detour often does), its answer was opened a second time (Back, reload), or it was
 * cancelled or failed at the provider (DECISIONS #120).
 */
export const SIGN_IN_PARAM = "sign_in";

export type SignInProblem = "email_unverified" | "try_again";

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
};

/** The button every notice has, after its text and its way out. It starts sign-in like the header's Sign in. */
export const SIGN_IN_AGAIN = "Sign in again";

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

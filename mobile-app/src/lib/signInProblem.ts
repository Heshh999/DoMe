/**
 * Why cloud-api sent the browser back without signing it in. The sign-in callback redirects to the public home page
 * with `?sign_in=<reason>` (cloud-api `routes/auth.py`, DECISIONS #118), never to `/app`: the app shell sends a
 * signed-out browser straight back to the identity provider, which remembers the person and would answer the same
 * way again, in a loop. The home page shows the matching notice once and removes the marker from the address bar.
 * Unknown reasons show nothing: the customer never sees a code.
 */
export const SIGN_IN_PARAM = "sign_in";

export type SignInProblem = "email_unverified";

export const SIGN_IN_PROBLEMS: Record<SignInProblem, { title: string; text: string }> = {
  email_unverified: {
    title: "Confirm your email first",
    text: "We sent a link to your inbox when you signed up. Open it, then sign in again. Can't find it? Check your spam folder.",
  },
};

/**
 * The way out when the link never came or no longer works (Auth0 sends it once, at sign-up, and it expires; DECISIONS
 * #118): setting a new password through "Forgot password?" on Auth0's login page also confirms the address
 * (founder-run on staging, 2026-11-12). The founder's paragraph after the text, `lead` + `strong` (in bold) + `rest`,
 * then a "Sign in again" button. That sign-in shows Auth0's login page even though Auth0 still remembers the person,
 * because cloud-api set a marker for it with the refusal (DECISIONS #119).
 */
export const SIGN_IN_WAY_OUT: Record<SignInProblem, { lead: string; strong: string; rest: string }> = {
  email_unverified: { lead: "No email? Tap Sign in, then ", strong: "Forgot password?", rest: ". Setting a new password confirms your email too." },
};

export const SIGN_IN_AGAIN = "Sign in again";

/**
 * After the way out, a last line shown only when a support contact is configured. Never the support page: its form
 * needs a sign-in, which this person cannot complete. The founder sends a new link from Auth0
 * (docs/LAUNCH_SETUP.md §16, which also says what happened when that was tried on staging).
 */
export const SIGN_IN_SUPPORT: Record<SignInProblem, { lead: string; link: string; rest: string }> = {
  email_unverified: { lead: "Still nothing? ", link: "Contact support", rest: " and we'll send a new link." },
};

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

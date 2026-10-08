/**
 * Web session (cookie-backed; the PWA never sees a bearer token). `load()` fetches `GET /v1/session`,
 * installs the CSRF token into the API client and records the plan/limits the backend reports.
 */
import { create } from "zustand";

import type { rest } from "@dome/protocol";

import { api, ApiError, configureApi } from "../lib/api.ts";
import { errorSummary, log } from "../lib/log.ts";

/**
 * `signed_out` is the server's verdict (401 / relay 4008): the shell redirects to sign-in at once.
 * `signing_out` is the customer's own Sign out / Forget this installation: the shell shows a plain
 * "signing out" screen and leaves the single navigation to the page that started it, so the
 * sign-in redirect can never race the local clean-up (key deletion) or bounce the customer straight
 * back to the identity provider.
 */
export type SessionStatus = "unknown" | "loading" | "signed_in" | "signed_out" | "signing_out" | "error";

export interface SessionState {
  status: SessionStatus;
  session: rest.SessionResponse | null;
  error: ApiError | null;
  load(): Promise<rest.SessionResponse | null>;
  /**
   * The server said 401: drop everything account-specific held here. While a manual sign-out is in
   * progress (`signing_out`) the status is left alone — that flow owns the navigation.
   */
  clear(): void;
  /** The customer pressed Sign out / Forget: no automatic sign-in redirect from here on. */
  beginSignOut(): void;
}

export const useSessionStore = create<SessionState>()((set, get) => ({
  status: "unknown",
  session: null,
  error: null,
  async load() {
    if (get().status !== "signed_in") set({ status: "loading", error: null });
    try {
      const session = await api.session();
      configureApi({ csrfToken: session.csrf_token });
      set({ status: "signed_in", session, error: null });
      return session;
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Could not load your session.", true);
      if (err.status === 401) {
        configureApi({ csrfToken: null });
        set({ status: "signed_out", session: null, error: null });
      } else {
        log.warn("session.load_failed", errorSummary(err));
        set({ status: "error", error: err });
      }
      return null;
    }
  },
  clear() {
    configureApi({ csrfToken: null });
    if (get().status === "signing_out") {
      set({ session: null, error: null });
      return;
    }
    set({ status: "signed_out", session: null, error: null });
  },
  beginSignOut() {
    set({ status: "signing_out", session: null, error: null });
  },
}));

export function accountId(): string | null {
  return useSessionStore.getState().session?.account.id ?? null;
}

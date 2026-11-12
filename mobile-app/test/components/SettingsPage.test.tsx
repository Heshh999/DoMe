/**
 * Settings: Sign out and "Forget this installation" must never race the shell's automatic sign-in
 * redirect. Each flow navigates exactly once, to "/", after the runtime has finished — and for
 * Forget, after the signing key is gone from IndexedDB. The connection check stays available while
 * the state is stale or remote control is off, and is withheld only when the socket is down or the
 * relay says the PC is not online. The diagnostics download carries the Screen view client's own facts (device round 2)
 * and, with the line under it, the app build (DECISIONS 92).
 *
 * Delete account (cloud-api DECISIONS #121) is the last item, on its own. Its sheet says what happens, enables the red
 * button only for the typed phrase, and sends exactly that phrase with the CSRF token. REAUTH_REQUIRED leads to a
 * sign-in that asks for the login page and comes back to the sheet; a 204 makes the phone forget the account like
 * Forget (no logout request: the server ended the session) and lands on the home page's "Your account was deleted.";
 * any other answer leaves everything as it was and says why in plain words.
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ERRORS } from "@dome/protocol";

import { RequireSession } from "../../src/app/AppShell.tsx";
import { setNavigationForTests, type Navigation } from "../../src/app/navigation.ts";
import { DELETE_ACCOUNT_RETURN } from "../../src/components/DeleteAccountSheet.tsx";
import { api, configureApi, loginUrl } from "../../src/lib/api.ts";
import { getExistingKeyPair, getOrCreateKeyPair, getStoredControllerId, setStoredControllerId } from "../../src/lib/controllerKey.ts";
import { APP_VERSION, BUILD_COMMIT, buildLabel } from "../../src/lib/diagnostics.ts";
import { emptyTimeline, type ScreenClientFacts } from "../../src/lib/screenStats.ts";
import { carriedAccountDeleted } from "../../src/lib/signInProblem.ts";
import { SettingsPage } from "../../src/pages/app/SettingsPage.tsx";
import { LandingPage } from "../../src/pages/public/LandingPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { STATE_FRESH_MS, useLiveStore } from "../../src/store/live.ts";
import { useSessionStore } from "../../src/store/session.ts";
import { CONTROLLER, emptyResponse, freshStorage, installDialogPolyfill, jsonResponse, makeRuntime, OFFICE_PC, PC, SESSION, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

interface NavCall {
  kind: "assign" | "replaceState";
  url: string;
  /** Session status at the moment the navigation was requested. */
  status: string;
  /** Key record read issued at that moment (IndexedDB orders it after any committed delete). */
  key: Promise<CryptoKeyPair | null>;
}

function fakeNavigation() {
  const calls: NavCall[] = [];
  const nav: Navigation = {
    assign: (url) => calls.push({ kind: "assign", url, status: useSessionStore.getState().status, key: getExistingKeyPair() }),
    replaceState: (url) => calls.push({ kind: "replaceState", url, status: useSessionStore.getState().status, key: getExistingKeyPair() }),
  };
  return { nav, calls };
}

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={["/app/settings"]}>
      <RequireSession>
        <SettingsPage />
      </RequireSession>
    </MemoryRouter>,
  );
}

let h: RuntimeHarness;
let requests: string[];

beforeEach(async () => {
  freshStorage();
  signedIn();
  requests = [];
  h = makeRuntime();
  configureApi({
    fetchImpl: async (url, init) => {
      requests.push(`${init.method ?? "GET"} ${url}`);
      if (url.endsWith("/v1/auth/logout")) return emptyResponse(204);
      return jsonResponse(500, { error: { code: "INTERNAL", message: "unexpected", retryable: false } });
    },
  });
  await getOrCreateKeyPair();
  await setStoredControllerId(CONTROLLER);
  await h.connect();
});

afterEach(() => {
  h.teardown();
  setNavigationForTests(null);
});

describe("Sign out", () => {
  it("navigates exactly once to '/', never to the sign-in URL, and keeps the installation key", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    // let any effect that might want to redirect run
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]!.kind).toBe("assign");
    expect(calls[0]!.url).toBe("/");
    expect(calls[0]!.status).toBe("signing_out");
    expect(requests).toContain("POST /v1/auth/logout");
    expect(useSessionStore.getState().status).toBe("signing_out");
    expect(useSessionStore.getState().session).toBeNull();
    expect(h.rt.relay.status).toBe("closed");
    expect(await getExistingKeyPair()).not.toBeNull(); // sign-out keeps the key (DECISIONS 7)
    expect(await getStoredControllerId()).toBeNull(); // but forgets account state
    // the shell shows a neutral screen, not the "Taking you to sign in" redirect
    expect(screen.getByRole("status", { name: "Signing out" })).toBeInTheDocument();
  });

  it("a 401 from the logout call does not turn the manual sign-out into a sign-in redirect", async () => {
    // only the logout call answers 401 (the page's own reads on opening, such as the assistant status, do not)
    configureApi({ fetchImpl: async (url) => (url.endsWith("/v1/auth/logout") ? jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "gone", retryable: false } }) : jsonResponse(500, { error: { code: "INTERNAL", message: "unexpected", retryable: false } })) });
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(calls.map((c) => c.url)).toEqual(["/"]);
    expect(useSessionStore.getState().status).toBe("signing_out");
  });
});

describe("Forget this installation", () => {
  it("deletes the key record before any navigation is requested, then navigates once to '/'", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    expect(await getExistingKeyPair()).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Forget this installation…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete key and sign out" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe("/");
    expect(calls[0]!.url).not.toMatch(/auth\/login/);
    // the read issued at navigation time already sees no key: the delete had committed
    expect(await calls[0]!.key).toBeNull();
    expect(await getExistingKeyPair()).toBeNull();
    expect(await getStoredControllerId()).toBeNull();
    expect(requests).toContain("POST /v1/auth/logout");
    expect(h.rt.relay.status).toBe("closed");
  });

  it("'Keep' disarms without doing anything", async () => {
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    renderSettings();
    await userEvent.click(screen.getByRole("button", { name: "Forget this installation…" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.getByRole("button", { name: "Forget this installation…" })).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    expect(await getExistingKeyPair()).not.toBeNull();
    expect(useSessionStore.getState().status).toBe("signed_in");
  });
});

describe("Delete account", () => {
  const PHRASE = "delete my account";
  const FACTS = [
    "Your paid plan, if you have one, is cancelled at once. There is no automatic refund.",
    "Any unused AI top-up requests go with the account.",
    "Every phone and PC is disconnected. The DoMe program on your PC will ask to be linked again.",
    "Your settings are deleted and the AI butler is turned off.",
    "DoMe keeps a record of your payments, without your name or email. Our payment provider keeps its own records, including your name and email.",
    "After 30 days, your account is erased for good.",
    "Your sign-in (the email and password you use to sign in) is kept by our sign-in provider. If you want it removed too, contact support before you delete your account.",
  ];
  const REAUTH = { error: { code: "REAUTH_REQUIRED", message: ERRORS.REAUTH_REQUIRED!.user_message, retryable: false } };
  let deletes: Array<{ url: string; init: RequestInit }>;

  /** The server: DELETE /v1/account answers with `answer`; logout as before; anything else 500. */
  function serve(answer: () => Response | Promise<Response>) {
    deletes = [];
    configureApi({
      fetchImpl: async (url, init) => {
        requests.push(`${init.method ?? "GET"} ${url}`);
        if (url.endsWith("/v1/account") && init.method === "DELETE") {
          deletes.push({ url, init });
          return answer();
        }
        if (url.endsWith("/v1/auth/logout")) return emptyResponse(204);
        return jsonResponse(500, { error: { code: "INTERNAL", message: "unexpected", retryable: false } });
      },
    });
  }

  function Address() {
    const loc = useLocation();
    return <output data-testid="address">{loc.pathname + loc.search}</output>;
  }

  function renderAt(entry: string) {
    return render(
      <MemoryRouter initialEntries={[entry]}>
        <RequireSession>
          <SettingsPage />
        </RequireSession>
        <Address />
      </MemoryRouter>,
    );
  }

  async function openSheet() {
    await userEvent.click(screen.getByRole("button", { name: "Delete account…" }));
    return screen.getByRole("dialog", { name: "Delete your account?" });
  }

  /** Everything the phone holds for the account is still there: nothing was cleared and nothing navigated. */
  async function nothingChanged(calls: NavCall[]) {
    expect(calls).toHaveLength(0);
    expect(useSessionStore.getState().status).toBe("signed_in");
    expect(useSessionStore.getState().session).toEqual(SESSION);
    expect(await getExistingKeyPair()).not.toBeNull();
    expect(await getStoredControllerId()).toBe(CONTROLLER);
    expect(h.rt.relay.status).toBe("open");
    expect(requests).not.toContain("POST /v1/auth/logout");
    expect(carriedAccountDeleted()).toBe(false);
  }

  beforeEach(() => installDialogPolyfill());
  afterEach(() => sessionStorage.clear());

  it("is the last item of Settings, below Sign out and Forget, and opens a sheet that says plainly what happens", async () => {
    serve(() => emptyResponse(204));
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    const { container } = renderAt("/app/settings");
    const title = screen.getByRole("heading", { level: 2, name: "Delete account" });
    const forget = screen.getByRole("heading", { level: 2, name: "Forget this installation" });
    const signOut = screen.getByRole("heading", { level: 2, name: "Sign out" });
    for (const everyday of [signOut, forget]) expect(everyday.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const sections = container.querySelectorAll("section");
    expect(sections[sections.length - 1]).toContainElement(title);
    expect(sections[sections.length - 1]).not.toContainElement(forget);

    const sheet = await openSheet();
    expect(screen.getByTestId("address")).toHaveTextContent("/app/settings?delete_account=1");
    const facts = within(sheet).getByTestId("delete-account-facts");
    // the account it deletes, by name, and every consequence in plain words
    expect(facts).toHaveTextContent(`This deletes the DoMe account ${SESSION.account.email}. You can’t undo it.`);
    const items = Array.from(facts.querySelectorAll("li")).map((li) => li.textContent);
    expect(items).toEqual(FACTS);
    expect(within(sheet).getByRole("link", { name: "contact support" })).toHaveAttribute("href", "/support");
    expect(within(sheet).getByRole("button", { name: "Delete account" })).toBeDisabled();
    expect(deletes).toHaveLength(0);

    // Cancel closes it and sends nothing
    await userEvent.click(within(sheet).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("address")).toHaveTextContent(/^\/app\/settings$/);
    expect(deletes).toHaveLength(0);
    await nothingChanged(calls);
  });

  it("with a support contact configured, 'contact support' goes to it (no sign-in needed after the deletion)", async () => {
    vi.stubEnv("VITE_DOME_SUPPORT_URL", "mailto:support@example.test");
    serve(() => emptyResponse(204));
    renderAt("/app/settings");
    const sheet = await openSheet();
    expect(within(sheet).getByRole("link", { name: "contact support" })).toHaveAttribute("href", "mailto:support@example.test");
    vi.unstubAllEnvs();
  });

  it("enables the red button only for the typed phrase: trimmed and in any case, never anything else", async () => {
    serve(() => emptyResponse(204));
    renderAt("/app/settings");
    const sheet = await openSheet();
    const input = within(sheet).getByLabelText(`Type “${PHRASE}” to confirm`);
    const button = within(sheet).getByRole("button", { name: "Delete account" });
    for (const wrong of ["delete", "delete my", "delete  my account", "delete my account please", "delete my acount"]) {
      await userEvent.clear(input);
      await userEvent.type(input, wrong);
      expect(button, wrong).toBeDisabled();
    }
    for (const right of [PHRASE, "Delete my account", "  DELETE MY ACCOUNT  "]) {
      await userEvent.clear(input);
      await userEvent.type(input, right);
      expect(button, right).toBeEnabled();
    }
    expect(deletes).toHaveLength(0);
  });

  it("sends DELETE /v1/account once, with the session's CSRF token and exactly the phrase as JSON", async () => {
    serve(() => emptyResponse(204));
    const { nav } = fakeNavigation();
    setNavigationForTests(nav);
    renderAt("/app/settings");
    const sheet = await openSheet();
    // iOS capitalises the first letter; the phrase sent is still exactly the lower-case one
    await userEvent.type(within(sheet).getByLabelText(`Type “${PHRASE}” to confirm`), " Delete my account ");
    await userEvent.click(within(sheet).getByRole("button", { name: "Delete account" }));
    await waitFor(() => expect(deletes).toHaveLength(1));
    const { url, init } = deletes[0]!;
    expect(url).toBe("/v1/account");
    expect(init.method).toBe("DELETE");
    expect(init.credentials).toBe("include");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-DoMe-CSRF"]).toBe(SESSION.csrf_token);
    expect(headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"confirm":"delete my account"}');
  });

  it("REAUTH_REQUIRED: explains, and its Sign in again asks for the login page and comes back to this sheet; nothing else changes", async () => {
    serve(() => jsonResponse(403, REAUTH));
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    const first = renderAt("/app/settings");
    const sheet = await openSheet();
    await userEvent.type(within(sheet).getByLabelText(`Type “${PHRASE}” to confirm`), PHRASE);
    await userEvent.click(within(sheet).getByRole("button", { name: "Delete account" }));
    const reauth = await within(sheet).findByTestId("delete-account-reauth");
    expect(reauth).toHaveTextContent("For your safety, sign in again to confirm.");
    const link = within(reauth).getByRole("link", { name: "Sign in again" });
    expect(DELETE_ACCOUNT_RETURN).toBe("/app/settings?delete_account=1");
    expect(link).toHaveAttribute("href", loginUrl(DELETE_ACCOUNT_RETURN, { prompt: "login" }));
    expect(link).toHaveAttribute("href", "/v1/auth/login?return_to=%2Fapp%2Fsettings%3Fdelete_account%3D1&prompt=login");
    // the red button waits for the new sign-in; the request was sent once and not retried
    expect(within(sheet).getByRole("button", { name: "Delete account" })).toBeDisabled();
    expect(deletes).toHaveLength(1);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    await nothingChanged(calls);

    // back from the sign-in (a fresh session): the same sheet is open on arrival, the phrase typed again, and it works
    first.unmount();
    serve(() => emptyResponse(204));
    renderAt(DELETE_ACCOUNT_RETURN);
    const again = screen.getByRole("dialog", { name: "Delete your account?" });
    const input = within(again).getByLabelText(`Type “${PHRASE}” to confirm`);
    expect(input).toHaveValue("");
    await userEvent.type(input, PHRASE);
    await userEvent.click(within(again).getByRole("button", { name: "Delete account" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(calls.map((c) => c.url)).toEqual(["/"]);
    expect(deletes).toHaveLength(1);
  });

  it("on success forgets everything of the account on this phone, sends no logout, and lands once on the home page's notice", async () => {
    serve(() => emptyResponse(204));
    const { nav, calls } = fakeNavigation();
    setNavigationForTests(nav);
    useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
    renderAt("/app/settings");
    const sheet = await openSheet();
    await userEvent.type(within(sheet).getByLabelText(`Type “${PHRASE}” to confirm`), PHRASE);
    await userEvent.click(within(sheet).getByRole("button", { name: "Delete account" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    // one navigation, to the home page, never to sign-in, after the clean-up finished
    expect(calls).toHaveLength(1);
    expect(calls[0]!.kind).toBe("assign");
    expect(calls[0]!.url).toBe("/");
    expect(calls[0]!.status).toBe("signing_out");
    expect(await calls[0]!.key).toBeNull();
    // the same clean-up as Forget this installation: key, account state, stores, socket, CSRF token
    expect(await getExistingKeyPair()).toBeNull();
    expect(await getStoredControllerId()).toBeNull();
    expect(useSessionStore.getState().session).toBeNull();
    expect(useDevicesStore.getState().pcs).toEqual([]);
    expect(useLiveStore.getState().controllerId).toBeNull();
    expect(h.rt.relay.status).toBe("closed");
    await expect(api.logout()).rejects.toMatchObject({ code: "UNAUTHENTICATED" }); // no CSRF token left to send
    // the server already ended the session: no logout request
    expect(requests.filter((r) => r.includes("/v1/auth/logout"))).toEqual([]);
    expect(requests.filter((r) => r.startsWith("DELETE "))).toEqual(["DELETE /v1/account"]);
    // the home page says so, once it loads
    expect(carriedAccountDeleted()).toBe(true);
    render(
      <MemoryRouter initialEntries={["/"]}>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("account-deleted").textContent).toBe("Your account was deleted.");
  });

  it("a server error or a lost connection leaves everything as it was and says so in plain words", async () => {
    const answers: Array<{ answer: () => Response | Promise<Response>; message: string }> = [
      { answer: () => jsonResponse(500, { error: { code: "INTERNAL", message: "boom", retryable: true } }), message: ERRORS.INTERNAL!.user_message },
      {
        answer: () => {
          throw new TypeError("Failed to fetch");
        },
        message: "You appear to be offline, or DoMe could not be reached.",
      },
    ];
    for (const { answer, message } of answers) {
      serve(answer);
      const { nav, calls } = fakeNavigation();
      setNavigationForTests(nav);
      const view = renderAt("/app/settings");
      const sheet = await openSheet();
      const input = within(sheet).getByLabelText(`Type “${PHRASE}” to confirm`);
      await userEvent.type(input, PHRASE);
      const button = within(sheet).getByRole("button", { name: "Delete account" });
      await userEvent.click(button);
      expect(await within(sheet).findByText(message)).toBeInTheDocument();
      expect(within(sheet).queryByTestId("delete-account-reauth")).toBeNull();
      // the sheet stays, with what was typed, and can be tried again
      expect(screen.getByRole("dialog", { name: "Delete your account?" })).toBe(sheet);
      expect(input).toHaveValue(PHRASE);
      expect(button).toBeEnabled();
      expect(deletes).toHaveLength(1);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 30));
      });
      await nothingChanged(calls);
      view.unmount();
    }
  });
});

describe("Connection check gating", () => {
  const freshState = () => {
    const live = useLiveStore.getState();
    live.setRelay("open");
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, volume: { value: 42, muted: false } } });
  };
  beforeEach(() => {
    useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
  });

  it("stays enabled while the state is stale ('Online · refreshing' for over 75 s)", () => {
    freshState();
    const received = useLiveStore.getState().pcs[PC]!.stateReceivedAt!;
    useLiveStore.getState().tick(received + STATE_FRESH_MS + 1);
    renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeEnabled();
  });

  it("stays enabled when remote control is switched off on the PC (the PC answers honestly)", () => {
    const live = useLiveStore.getState();
    live.setRelay("open");
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: false, session_locked: false, extension_connected: false } });
    renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeEnabled();
  });

  it("is disabled when the relay reports the PC offline, and when the socket itself is not open", () => {
    const live = useLiveStore.getState();
    live.setRelay("open");
    live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "offline", last_seen: TS });
    const first = renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeDisabled();
    first.unmount();
    freshState();
    useLiveStore.getState().setRelay("reconnecting");
    renderSettings();
    expect(screen.getByRole("button", { name: "Check connection to Office PC" })).toBeDisabled();
  });
});

describe("Settings: diagnostics", () => {
  it("the download carries the Screen view client's facts (last stats, the <video> facts, the timeline, DTLS, the codec fallback) from the runtime, not from React state", async () => {
    const facts: ScreenClientFacts = {
      stats: { at: 1, inbound: null, codec: { mime: "video/H264", pt: 98, fmtp: "profile-level-id=42e01f;packetization-mode=1" }, pair: null, dtls: "connected", ice: null, dc_sent: 4, dc_received: 1 },
      media: null,
      timeline: { ...emptyTimeline(), answer_verified: 900, track: 905, dtls_connected: 1300 },
      channel: { open: false, hb_sent: 4, received: 1, dropped: 0 },
      transport_connected: true,
      codec_fallback: { from: "h264", to: "vp8", reason: "not_decoded" },
    };
    vi.spyOn(h.rt.screen, "facts", "get").mockReturnValue(facts);
    const blobs: Blob[] = [];
    const createObjectURL = vi.fn((b: Blob) => {
      blobs.push(b);
      return "blob:diagnostics";
    });
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => undefined });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    renderSettings();
    await userEvent.click(await screen.findByRole("button", { name: "Download diagnostics" }));
    expect(click).toHaveBeenCalledTimes(1);
    const report = JSON.parse(await blobs[0]!.text()) as { screen: Record<string, unknown> };
    expect(report.screen).toMatchObject({ phase: "idle", stats: facts.stats, media: null, timeline: facts.timeline, transport_connected: true, codec_fallback: { from: "h264", to: "vp8", reason: "not_decoded" } });
    vi.restoreAllMocks();
  });

  it("the download and the line under it name the app build (DECISIONS 92): the commit, or none for a local build", async () => {
    const blobs: Blob[] = [];
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: (b: Blob) => {
        blobs.push(b);
        return "blob:diagnostics";
      },
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => undefined });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    renderSettings();
    await userEvent.click(await screen.findByRole("button", { name: "Download diagnostics" }));
    const report = JSON.parse(await blobs[0]!.text()) as { app: Record<string, unknown> };
    expect(report.app).toMatchObject({ version: APP_VERSION, build_commit: BUILD_COMMIT === "" ? null : BUILD_COMMIT });
    expect(screen.getByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").startsWith(`DoMe app ${buildLabel()}`))).toBeInTheDocument();
    expect(buildLabel()).toMatch(/^\d+\.\d+\.\d+ · build ([0-9a-f]{12}|dev)$/);
    vi.restoreAllMocks();
  });
});

/**
 * Support form (spec §11A): category preselected from the help link, POST body validated against the
 * contract, 201 → reference shown; definite 4xx rejection → "Not sent"; a failure where the server may
 * have stored the ticket (network drop, 5xx, unreadable 2xx) → "Not confirmed" with a refresh of
 * "Your requests" before any retry; copyable redacted summary, never a claim of receipt either way;
 * the account's tickets listed.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { configureApi } from "../../src/lib/api.ts";
import { clearLogs, log } from "../../src/lib/log.ts";
import { SupportPage } from "../../src/pages/public/SupportPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { useSessionStore } from "../../src/store/session.ts";
import { jsonResponse, OFFICE_PC, PC, signedIn, TS } from "../helpers/harness.ts";

const TICKET = { ticket_id: "77777777-7777-4777-8777-777777777777", reference: "DM-7K3M9P2Q", status: "received", category: "input", error_code: "INPUT_NOT_PERMITTED", created_at: TS, updated_at: TS };

let requests: Array<{ url: string; init: RequestInit }>;

beforeEach(() => {
  signedIn();
  clearLogs();
  requests = [];
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
  useLiveStore.getState().onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
});

afterEach(() => {
  configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
});

function mock(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  configureApi({
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      return handler(url, init);
    },
  });
}

function renderSupport(query = "?category=input&code=INPUT_NOT_PERMITTED") {
  return render(
    <MemoryRouter initialEntries={[`/support${query}`]}>
      <SupportPage />
    </MemoryRouter>,
  );
}

describe("SupportPage", () => {
  it("preselects the category and code from the help link, posts a contract-valid body with reviewed diagnostics, and shows the reference on 201", async () => {
    log.info("api.error", { method: "GET", path: "/v1/pcs", status: 503, code: "PC_OFFLINE", pairing_code: "SECRET-CODE" });
    mock((url, init) => {
      if (url.endsWith("/v1/support/tickets") && init.method === "GET") return jsonResponse(200, { tickets: [] });
      if (url.endsWith("/v1/support/tickets") && init.method === "POST") return jsonResponse(201, { ...TICKET, response_expectation: "We answer within two working days." });
      return jsonResponse(404, { error: { code: "NOT_FOUND", message: "no", retryable: false } });
    });
    renderSupport();
    expect(screen.getByLabelText("What is it about?")).toHaveValue("input");
    expect(screen.getByText(/Error you were looking at/)).toHaveTextContent("INPUT_NOT_PERMITTED");
    await userEvent.type(screen.getByLabelText("What happened?"), "The touchpad says not permitted even after pairing.");
    await userEvent.click(screen.getByRole("button", { name: "Attach redacted diagnostics…" }));
    const preview = screen.getByTestId("diagnostics-preview");
    expect(preview.textContent).toContain("PC_OFFLINE"); // codes stay
    expect(preview.textContent).not.toContain("SECRET-CODE"); // pairing material redacted
    expect(preview.textContent).not.toContain("a@example.test"); // no e-mail in the bundle
    await userEvent.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(screen.getByText(/Received — reference DM-7K3M9P2Q/)).toBeInTheDocument());
    expect(screen.getByText("We answer within two working days.")).toBeInTheDocument();
    const post = requests.find((r) => r.init.method === "POST")!;
    const body = JSON.parse(post.init.body as string) as Record<string, unknown>;
    expect(body.category).toBe("input");
    expect(body.error_code).toBe("INPUT_NOT_PERMITTED");
    expect(body.message).toBe("The touchpad says not permitted even after pairing.");
    expect(typeof body.diagnostics).toBe("string");
    expect((body.diagnostics as string).length).toBeLessThanOrEqual(32768);
    expect(body.diagnostics as string).not.toContain("SECRET-CODE");
    expect((post.init.headers as Record<string, string>)["X-DoMe-CSRF"]).toBeDefined();
  });

  it("a definite 4xx rejection says Not sent, offers a copyable redacted summary and never claims receipt", async () => {
    mock((url, init) => {
      if (init.method === "GET") return jsonResponse(200, { tickets: [TICKET] });
      return jsonResponse(429, { error: { code: "RATE_LIMITED", message: "slow down", retryable: true } });
    });
    renderSupport("?category=connection");
    expect(screen.getByLabelText("What is it about?")).toHaveValue("connection");
    await userEvent.type(screen.getByLabelText("What happened?"), "PC shows offline although it is on.");
    await userEvent.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(screen.getByText("Not sent")).toBeInTheDocument());
    expect(screen.getByText(/Support has not received this request/)).toBeInTheDocument();
    expect(screen.queryByText(/Received/)).toBeNull();
    expect(screen.queryByText(/could not confirm/)).toBeNull();
    const summary = screen.getByTestId("unsent-summary") as HTMLTextAreaElement;
    expect(summary.value).toContain("not sent");
    expect(summary.value).toContain("Category: connection");
    expect(summary.value).toContain("PC shows offline although it is on.");
    expect(screen.getByRole("button", { name: "Copy summary" })).toBeInTheDocument();
    // the account's existing tickets are listed
    expect(screen.getByText("DM-7K3M9P2Q")).toBeInTheDocument();
  });

  it("a 5xx answer is reported as unconfirmed (the ticket may exist), never as Not sent; Refresh your requests re-reads the list before any retry", async () => {
    let stored = false;
    mock((url, init) => {
      if (init.method === "GET") return jsonResponse(200, { tickets: stored ? [TICKET] : [] });
      stored = true; // committed, then the answer failed
      return jsonResponse(503, { error: { code: "INTERNAL", message: "down", retryable: true } });
    });
    renderSupport("?category=connection");
    await userEvent.type(screen.getByLabelText("What happened?"), "PC shows offline although it is on.");
    await userEvent.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(screen.getByText("Not confirmed")).toBeInTheDocument());
    expect(screen.getByText(/could not confirm whether support received this request/)).toBeInTheDocument();
    expect(screen.queryByText("Not sent")).toBeNull();
    expect(screen.queryByText(/Support has not received/)).toBeNull();
    expect(screen.queryByText(/Received —/)).toBeNull();
    expect((screen.getByTestId("unsent-summary") as HTMLTextAreaElement).value).toContain("delivery not confirmed");
    const getsBefore = requests.filter((r) => r.init.method === "GET").length;
    await userEvent.click(screen.getByRole("button", { name: "Refresh your requests" }));
    await waitFor(() => expect(screen.getByText("DM-7K3M9P2Q")).toBeInTheDocument());
    expect(requests.filter((r) => r.init.method === "GET").length).toBe(getsBefore + 1);
    expect(requests.filter((r) => r.init.method === "POST")).toHaveLength(1); // nothing resent on its own
  });

  it("a 201 whose body fails support_ticket_response validation is unconfirmed, not a receipt and not Not sent", async () => {
    mock((url, init) => {
      if (init.method === "GET") return jsonResponse(200, { tickets: [] });
      return jsonResponse(201, { reference: 42 });
    });
    renderSupport();
    await userEvent.type(screen.getByLabelText("What happened?"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(screen.getByText("Not confirmed")).toBeInTheDocument());
    expect(screen.queryByText(/Received —/)).toBeNull();
    expect(screen.queryByText("Not sent")).toBeNull();
  });

  it("a network failure is unconfirmed too (the POST may have left before the drop) and an unknown category falls back to 'other'", async () => {
    mock((url, init) => {
      if (init.method === "GET") return jsonResponse(200, { tickets: [] });
      throw new TypeError("Failed to fetch");
    });
    renderSupport("?category=bogus&code=not-a-code");
    expect(screen.getByLabelText("What is it about?")).toHaveValue("other");
    expect(screen.queryByText(/Error you were looking at/)).toBeNull();
    await userEvent.type(screen.getByLabelText("What happened?"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(screen.getByText("Not confirmed")).toBeInTheDocument());
    expect(screen.getByText(/offline, or DoMe could not be reached/)).toBeInTheDocument();
    expect(screen.queryByText(/Support has not received/)).toBeNull();
  });

  it("signed out: self-help stays, the form asks to sign in and sends nothing", () => {
    useSessionStore.setState({ status: "signed_out", session: null, error: null });
    mock(() => jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "x", retryable: false } }));
    renderSupport();
    expect(screen.getByText("Sign in to continue")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send request" })).toBeNull();
    expect(screen.getByText("The PC shows Offline")).toBeInTheDocument();
    expect(requests.filter((r) => r.init.method === "POST")).toHaveLength(0);
  });
});

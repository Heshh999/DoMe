/**
 * Pairing page: the 20-symbol code never leaves the phone — the claim carries only its SHA-256
 * handle, the public JWK, a display name and the requested capabilities; the 6-digit verification
 * code matches the shared helper; approval polling is slow (2 s), reconnects the relay exactly once
 * on `approved`, and maps a 404 to PAIRING_CODE_INVALID. The deep-link fragment is scrubbed on
 * arrival even under StrictMode's double-invoked initialisers.
 */
import { StrictMode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureApi } from "../../src/lib/api.ts";
import { getControllerIdentity, getStoredControllerId } from "../../src/lib/controllerKey.ts";
import { ALL_CAPABILITIES, normalizePairingCode, pairingCodeHandle, pairingVerificationCode } from "../../src/lib/pairing.ts";
import { PairPage } from "../../src/pages/app/PairPage.tsx";
import { CONTROLLER, freshStorage, jsonResponse, makeRuntime, PC, signedIn, type RuntimeHarness } from "../helpers/harness.ts";

const CODE = "abcde-fghjk-mnpqr-stvwx"; // typed in lower case: normalisation is the page's job
const NORMALIZED = normalizePairingCode(CODE);
const PAIRING_ID = "66666666-6666-4666-8666-666666666666";
const EXPIRES = new Date(Date.now() + 5 * 60_000).toISOString();
const PATH = "/app/devices/pair";

interface Captured {
  url: string;
  method: string;
  body: string | null;
}

let h: RuntimeHarness;
let captured: Captured[];
let statusResponse: () => Response;

function renderPair(strict = false) {
  const tree = (
    <MemoryRouter initialEntries={[PATH]}>
      <Routes>
        <Route path={PATH} element={<PairPage />} />
      </Routes>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

async function typeCodeAndClaim() {
  await userEvent.type(screen.getByLabelText("Pairing code from the PC"), CODE);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(screen.getByText("Almost there")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Request pairing" }));
  await waitFor(() => expect(screen.getByTestId("verification-code")).toBeInTheDocument(), { timeout: 4000 });
}

beforeEach(async () => {
  freshStorage();
  signedIn();
  h = makeRuntime();
  await h.connect();
  captured = [];
  statusResponse = () => jsonResponse(200, { pairing_id: PAIRING_ID, state: "claimed", pc_id: PC, pc_name: "Office PC", pc_online: true, expires_at: EXPIRES });
  configureApi({
    fetchImpl: async (url, init) => {
      captured.push({ url, method: init.method ?? "GET", body: typeof init.body === "string" ? init.body : null });
      if (url.endsWith("/v1/pairing/claim")) return jsonResponse(202, { pairing_id: PAIRING_ID, state: "claimed", pc_id: PC, pc_name: "Office PC", pc_online: true, expires_at: EXPIRES });
      if (url.includes(`/v1/pairing/${PAIRING_ID}`)) return statusResponse();
      if (url.endsWith("/v1/pcs")) return jsonResponse(200, { pcs: [] });
      if (url.endsWith("/v1/controllers")) return jsonResponse(200, { controllers: [] });
      return jsonResponse(500, { error: { code: "INTERNAL", message: `unexpected ${url}`, retryable: false } });
    },
  });
});

afterEach(() => {
  h.teardown();
  window.history.replaceState(null, "", "/");
});

describe("PairPage", () => {
  it("claims with code_hash + public_jwk only (never the code), shows the shared verification code, polls slowly and reconnects the relay once on approval", async () => {
    const reconnect = vi.spyOn(h.rt.relay, "reconnect").mockImplementation(() => undefined);
    renderPair();
    expect(screen.getByRole("tab", { name: "Type the code" })).toHaveAttribute("aria-selected", "true"); // jsdom has no camera
    await typeCodeAndClaim();

    const claim = captured.find((c) => c.url.endsWith("/v1/pairing/claim"))!;
    expect(claim.method).toBe("POST");
    const body = JSON.parse(claim.body!) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["code_hash", "display_name", "public_jwk", "requested_capabilities"]);
    expect(body.code_hash).toBe(await pairingCodeHandle(NORMALIZED));
    const identity = await getControllerIdentity();
    expect(body.public_jwk).toEqual(identity.jwk);
    expect(body.display_name).toBe("This phone");
    expect(body.requested_capabilities).toEqual([...ALL_CAPABILITIES]);
    // the secret itself appears nowhere in what left the phone
    for (const c of captured) {
      expect(`${c.url} ${c.body ?? ""}`).not.toMatch(/abcde|ABCDE|stvwx|STVWX/);
      expect(`${c.url} ${c.body ?? ""}`).not.toContain(NORMALIZED);
    }

    const expected = await pairingVerificationCode(NORMALIZED, PAIRING_ID, PC, identity.kid);
    expect(screen.getByTestId("verification-code")).toHaveTextContent(expected);
    expect(screen.getByText(/Waiting for approval on the PC/)).toBeInTheDocument();

    // polling is deliberately slow: no status request within the first second
    await new Promise((r) => setTimeout(r, 1000));
    expect(captured.filter((c) => c.url.includes(`/v1/pairing/${PAIRING_ID}`))).toHaveLength(0);

    statusResponse = () => jsonResponse(200, { pairing_id: PAIRING_ID, state: "approved", pc_id: PC, pc_name: "Office PC", expires_at: EXPIRES, controller_id: CONTROLLER, granted_capabilities: ["status", "media"] });
    await waitFor(() => expect(screen.getByText("Paired")).toBeInTheDocument(), { timeout: 5000 });
    expect(screen.getByText(/This phone can now control Office PC/)).toBeInTheDocument();
    expect(reconnect).toHaveBeenCalledTimes(1);
    expect(await getStoredControllerId()).toBe(CONTROLLER);
    const polls = captured.filter((c) => c.url.includes(`/v1/pairing/${PAIRING_ID}`));
    expect(polls.length).toBeGreaterThanOrEqual(1);
    expect(polls.every((c) => c.method === "GET")).toBe(true);
    // approval stops polling
    const after = polls.length;
    await new Promise((r) => setTimeout(r, 2300));
    expect(captured.filter((c) => c.url.includes(`/v1/pairing/${PAIRING_ID}`))).toHaveLength(after);
  }, 15_000);

  it("a 404 while waiting is shown as an invalid/expired code with its recovery steps, and nothing reconnects", async () => {
    const reconnect = vi.spyOn(h.rt.relay, "reconnect").mockImplementation(() => undefined);
    renderPair();
    await typeCodeAndClaim();
    statusResponse = () => jsonResponse(404, { error: { code: "NOT_FOUND", message: "no such pairing", retryable: false } });
    await waitFor(() => expect(screen.getByText("Pairing did not complete")).toBeInTheDocument(), { timeout: 5000 });
    expect(screen.getByText(/Pairing codes last 5 minutes and work once/)).toBeInTheDocument();
    expect(reconnect).not.toHaveBeenCalled();
    expect(await getStoredControllerId()).toBe(CONTROLLER); // unchanged from hello_ack: nothing was written by the failed pairing
    // failure stops polling
    const polls = captured.filter((c) => c.url.includes(`/v1/pairing/${PAIRING_ID}`)).length;
    await new Promise((r) => setTimeout(r, 2300));
    expect(captured.filter((c) => c.url.includes(`/v1/pairing/${PAIRING_ID}`))).toHaveLength(polls);
  }, 10_000);

  it("rejects text that is not a pairing code without sending anything", async () => {
    renderPair();
    await userEvent.type(screen.getByLabelText("Pairing code from the PC"), "this is not a pairing code!!");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/not a DoMe pairing code/);
    expect(captured).toHaveLength(0);
  });

  it("deep link: the #code fragment is consumed once and scrubbed from the address bar, also under StrictMode", async () => {
    window.history.replaceState(null, "", `${PATH}#code=${CODE.toUpperCase()}`);
    renderPair(true);
    expect(screen.getByText("Almost there")).toBeInTheDocument(); // code accepted straight into the details step
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe(PATH);
    expect(window.location.href).not.toMatch(/code=/i);
    // the page is at the details step with the code held in memory only; the claim still works
    await userEvent.click(screen.getByRole("button", { name: "Request pairing" }));
    await waitFor(() => expect(screen.getByTestId("verification-code")).toBeInTheDocument(), { timeout: 4000 });
    const claim = captured.find((c) => c.url.endsWith("/v1/pairing/claim"))!;
    expect((JSON.parse(claim.body!) as { code_hash: string }).code_hash).toBe(await pairingCodeHandle(NORMALIZED));
  });
});

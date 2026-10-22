/**
 * Pairing page: the 20-symbol code never leaves the phone — the claim carries only its SHA-256
 * handle, the public JWK, a display name and the requested capabilities; the 6-digit verification
 * code matches the shared helper; approval polling is slow (2 s), reconnects the relay exactly once
 * on `approved`, and maps a 404 to PAIRING_CODE_INVALID. The deep-link fragment is scrubbed on
 * arrival even under StrictMode's double-invoked initialisers.
 *
 * PC key pinning (protocol 1.4, docs/design/screen-view.md §4.1): the code is held through `waiting` and is gone after
 * done, failed, expired, declined, cancel and unmount; a valid `pc_key.mac` pins the PC key, an invalid one pins
 * nothing and says so, an older PC that sent no key pins nothing.
 */
import { StrictMode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pcKeyMac, type rest } from "@dome/protocol";

import { configureApi } from "../../src/lib/api.ts";
import { getControllerIdentity, getStoredControllerId, writePin } from "../../src/lib/controllerKey.ts";
import { clearLogs, recentLogs } from "../../src/lib/log.ts";
import { ALL_CAPABILITIES, normalizePairingCode, pairingCodeHandle, pairingVerificationCode } from "../../src/lib/pairing.ts";
import { getPin, heldPairingCodes } from "../../src/lib/pcKeyPins.ts";
import { PairPage } from "../../src/pages/app/PairPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useScreenStore } from "../../src/store/screen.ts";
import { FakePcKey } from "../helpers/fakePeer.ts";
import { CONTROLLER, freshStorage, jsonResponse, makeRuntime, OFFICE_PC, OTHER_PC, PC, signedIn, type RuntimeHarness } from "../helpers/harness.ts";

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
let pcsListed: unknown[];
let claimResponse: (() => Response) | null;

function renderPair(strict = false) {
  const tree = (
    <MemoryRouter initialEntries={[PATH]}>
      <Routes>
        <Route path={PATH} element={<PairPage />} />
        <Route path="/app/devices" element={<p>Devices page</p>} />
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
  pcsListed = [];
  claimResponse = null;
  statusResponse = () => jsonResponse(200, { pairing_id: PAIRING_ID, state: "claimed", pc_id: PC, pc_name: "Office PC", pc_online: true, expires_at: EXPIRES });
  configureApi({
    fetchImpl: async (url, init) => {
      captured.push({ url, method: init.method ?? "GET", body: typeof init.body === "string" ? init.body : null });
      if (url.endsWith("/v1/pairing/claim") && claimResponse) return claimResponse();
      if (url.endsWith("/v1/pairing/claim")) return jsonResponse(202, { pairing_id: PAIRING_ID, state: "claimed", pc_id: PC, pc_name: "Office PC", pc_online: true, expires_at: EXPIRES });
      if (url.includes(`/v1/pairing/${PAIRING_ID}`)) return statusResponse();
      if (url.endsWith("/v1/pcs")) return jsonResponse(200, { pcs: pcsListed });
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
  it("selects the PC it just paired with, even when another PC was selected", async () => {
    vi.spyOn(h.rt.relay, "reconnect").mockImplementation(() => undefined);
    const other = { ...OFFICE_PC, id: OTHER_PC, name: "Living room PC" };
    pcsListed = [other, { ...OFFICE_PC, connection: "offline" }];
    await useDevicesStore.getState().refresh();
    expect(useDevicesStore.getState().selectedPcId).toBe(OTHER_PC); // the online one wins by default

    renderPair();
    await typeCodeAndClaim();
    statusResponse = () => jsonResponse(200, { pairing_id: PAIRING_ID, state: "approved", pc_id: PC, pc_name: "Office PC", expires_at: EXPIRES, controller_id: CONTROLLER, granted_capabilities: ["status", "media"] });
    await waitFor(() => expect(screen.getByText("Paired")).toBeInTheDocument(), { timeout: 5000 });
    await waitFor(() => expect(useDevicesStore.getState().selectedPcId).toBe(PC));
    await waitFor(() => expect(useDevicesStore.getState().pcs).toHaveLength(2));
    expect(useDevicesStore.getState().selectedPcId).toBe(PC); // the refresh after pairing keeps it
  });
  it("a device limit refusal offers Manage devices next to Start again", async () => {
    claimResponse = () => jsonResponse(403, { error: { code: "DEVICE_LIMIT_REACHED", message: "limit", retryable: false } });
    renderPair();
    await userEvent.type(screen.getByLabelText("Pairing code from the PC"), CODE);
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Request pairing" }));
    await waitFor(() => expect(screen.getByText("Pairing did not complete")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Start again" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Manage devices" }));
    expect(screen.getByText("Devices page")).toBeInTheDocument();
  });
});

describe("PairPage: PC key pinning (protocol 1.4)", () => {
  function approvedWith(pcKey?: rest.PairingStatusResponse["pc_key"]) {
    return () => jsonResponse(200, { pairing_id: PAIRING_ID, state: "approved", pc_id: PC, pc_name: "Office PC", expires_at: EXPIRES, controller_id: CONTROLLER, granted_capabilities: ["status", "media"], ...(pcKey ? { pc_key: pcKey } : {}) });
  }
  const polls = () => captured.filter((c) => c.url.includes(`/v1/pairing/${PAIRING_ID}`)).length;

  beforeEach(() => {
    vi.spyOn(h.rt.relay, "reconnect").mockImplementation(() => undefined);
    useScreenStore.setState({ switch: "on", switchChecked: true });
    clearLogs();
  });

  it("holds the code through `waiting` (also across claimed polls), pins the PC key when its MAC verifies, then drops the code", async () => {
    expect(heldPairingCodes()).toBe(0);
    renderPair();
    await typeCodeAndClaim();
    expect(heldPairingCodes()).toBe(1);
    await waitFor(() => expect(polls()).toBeGreaterThanOrEqual(1), { timeout: 4000 });
    expect(heldPairingCodes()).toBe(1); // a `claimed` answer keeps waiting, and keeps the code

    const identity = await getControllerIdentity();
    const pcKey = await FakePcKey.create();
    statusResponse = approvedWith({ public_jwk: pcKey.jwk, mac: await pcKeyMac(NORMALIZED, PAIRING_ID, PC, identity.kid, pcKey.kid) });
    await waitFor(() => expect(screen.getByText("Paired")).toBeInTheDocument(), { timeout: 6000 });
    expect(await getPin(PC)).toMatchObject({ pc_id: PC, pc_kid: pcKey.kid, pc_public_jwk: pcKey.jwk });
    expect(heldPairingCodes()).toBe(0);
    expect(screen.getByTestId("pin-ok")).toHaveTextContent(/Screen view can check every picture really comes from this PC/);
    // the code still never left the phone and never reached the log
    for (const c of captured) expect(`${c.url} ${c.body ?? ""}`).not.toContain(NORMALIZED);
    expect(JSON.stringify(recentLogs())).not.toContain(NORMALIZED);
  }, 15_000);

  it("a MAC that does not verify pairs but pins nothing (an earlier pin for the PC is removed), says so, and drops the code", async () => {
    const earlier = await FakePcKey.create();
    await writePin(earlier.pin(PC));
    renderPair();
    await typeCodeAndClaim();
    const substitute = await FakePcKey.create();
    const identity = await getControllerIdentity();
    // the relay serves a key of its own with the MAC the PC computed over the real one
    statusResponse = approvedWith({ public_jwk: substitute.jwk, mac: await pcKeyMac(NORMALIZED, PAIRING_ID, PC, identity.kid, earlier.kid) });
    await waitFor(() => expect(screen.getByText("Paired")).toBeInTheDocument(), { timeout: 6000 });
    expect(await getPin(PC)).toBeNull();
    expect(screen.getByTestId("pin-failed")).toHaveTextContent(/will not show this PC’s screen/);
    expect(heldPairingCodes()).toBe(0);
  }, 15_000);

  it("an older PC that sent no key pins nothing and asks for a PC update while Screen view is on; the code is dropped", async () => {
    renderPair();
    await typeCodeAndClaim();
    statusResponse = approvedWith();
    await waitFor(() => expect(screen.getByText("Paired")).toBeInTheDocument(), { timeout: 6000 });
    expect(await getPin(PC)).toBeNull();
    expect(screen.getByTestId("pin-missing")).toHaveTextContent(/update DoMe on the PC, then pair this phone again/);
    expect(heldPairingCodes()).toBe(0);
  }, 15_000);

  it("the code is dropped when the pairing is declined, when it expires, when it is cancelled and when the page unmounts", async () => {
    for (const state of ["declined", "expired"] as const) {
      const view = renderPair();
      await typeCodeAndClaim();
      expect(heldPairingCodes()).toBe(1);
      statusResponse = () => jsonResponse(200, { pairing_id: PAIRING_ID, state, pc_id: PC, pc_name: "Office PC", expires_at: EXPIRES });
      await waitFor(() => expect(screen.getByText("Pairing did not complete")).toBeInTheDocument(), { timeout: 6000 });
      expect(heldPairingCodes()).toBe(0);
      view.unmount();
      statusResponse = () => jsonResponse(200, { pairing_id: PAIRING_ID, state: "claimed", pc_id: PC, pc_name: "Office PC", pc_online: true, expires_at: EXPIRES });
    }
    const cancelled = renderPair();
    await typeCodeAndClaim();
    expect(heldPairingCodes()).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(heldPairingCodes()).toBe(0);
    cancelled.unmount();

    const left = renderPair();
    await typeCodeAndClaim();
    expect(heldPairingCodes()).toBe(1);
    left.unmount(); // the customer navigated away while the PC had not answered
    expect(heldPairingCodes()).toBe(0);
    expect(await getPin(PC)).toBeNull();
  }, 30_000);
});

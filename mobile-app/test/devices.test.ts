/**
 * The devices store (design screen-view-2a §4.9, D28): which protocol MINORs a PC's agent announced
 * (`pc.protocol_versions`, [] when unknown) and whether it speaks one; a successful `/v1/pcs` answer this app version
 * does not understand (a field from a newer deploy: MALFORMED_MESSAGE with a 2xx status) keeps the list on screen,
 * still takes the controllers from their own answer, says a newer DoMe is ready instead of an error, and asks for the
 * newer app; any other failure (an HTML 404 from a proxy included) is the error as before.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureApi } from "../src/lib/api.ts";
import { useDevicesStore } from "../src/store/devices.ts";
import { CONTROLLER, emptyResponse, jsonResponse, OFFICE_PC, PC, signedIn, TS } from "./helpers/harness.ts";

const pwa = vi.hoisted(() => ({ requestAppUpdate: vi.fn() }));
vi.mock("../src/pwa.ts", () => pwa);

const CONTROLLER_ROW = { id: CONTROLLER, kid: "k".repeat(43), display_name: "iPhone", status: "active", created_at: TS, last_seen: TS };

function serve(pcsBody: () => Response, controllers: () => Response = () => jsonResponse(200, { controllers: [] })): void {
  configureApi({ fetchImpl: async (url) => (String(url).endsWith("/v1/pcs") ? pcsBody() : String(url).endsWith("/v1/controllers") ? controllers() : emptyResponse(404)) });
}

beforeEach(() => {
  signedIn();
  pwa.requestAppUpdate.mockClear();
});

afterEach(() => {
  configureApi({ fetchImpl: (i, init) => fetch(i, init) });
});

describe("devices store: protocol versions", () => {
  it("reads pc.protocol_versions, [] when the field is absent or the PC unknown; pcSpeaks follows the compatibility rule", async () => {
    serve(() => jsonResponse(200, { pcs: [{ ...OFFICE_PC, protocol_versions: ["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"] }] }));
    await useDevicesStore.getState().refresh();
    const s = useDevicesStore.getState();
    expect(s.pcProtocolVersions(PC)).toEqual(["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"]);
    expect(s.pcSpeaks(PC, "1.5")).toBe(true);
    expect(s.pcSpeaks(PC, "1.4")).toBe(true);
    expect(s.pcSpeaks(PC, "1.6")).toBe(false);
    expect(s.pcProtocolVersions("55555555-5555-4555-8555-555555555555")).toEqual([]);
    serve(() => jsonResponse(200, { pcs: [OFFICE_PC] }));
    await useDevicesStore.getState().refresh();
    expect(useDevicesStore.getState().pcProtocolVersions(PC)).toEqual([]);
    expect(useDevicesStore.getState().pcSpeaks(PC, "1.5")).toBe(false);
    // a copy: changing it changes nothing in the store
    useDevicesStore.getState().pcProtocolVersions(PC).push("9.9");
    expect(useDevicesStore.getState().pcProtocolVersions(PC)).toEqual([]);
  });
});

describe("devices store: a newer deploy than this app", () => {
  it("MALFORMED_MESSAGE from /v1/pcs keeps the last list, sets updateReady instead of an error, and asks for the newer app", async () => {
    serve(() => jsonResponse(200, { pcs: [OFFICE_PC] }));
    await useDevicesStore.getState().refresh();
    expect(useDevicesStore.getState().pcs).toHaveLength(1);
    // the server now sends a field this bundle's schema does not know
    serve(() => jsonResponse(200, { pcs: [{ ...OFFICE_PC, a_field_from_the_future: true }] }));
    await useDevicesStore.getState().refresh();
    const s = useDevicesStore.getState();
    expect(s.updateReady).toBe(true);
    expect(s.error).toBeNull();
    expect(s.loading).toBe(false);
    expect(s.pcs).toEqual([OFFICE_PC]);
    expect(pwa.requestAppUpdate).toHaveBeenCalledTimes(1);
    useDevicesStore.getState().reset();
    expect(useDevicesStore.getState().updateReady).toBe(false);
  });

  it("on a cold load the controllers still come from their own answer (the Devices page names this phone from them)", async () => {
    useDevicesStore.getState().reset();
    serve(
      () => jsonResponse(200, { pcs: [{ ...OFFICE_PC, a_field_from_the_future: true }] }),
      () => jsonResponse(200, { controllers: [CONTROLLER_ROW] }),
    );
    await useDevicesStore.getState().refresh();
    const s = useDevicesStore.getState();
    expect(s.updateReady).toBe(true);
    expect(s.controllers).toEqual([CONTROLLER_ROW]);
    expect(s.loaded).toBe(false);
    expect(s.error).toBeNull();
    useDevicesStore.getState().reset();
  });

  it("a refusal whose body is not DoMe's (an HTML 404 from a proxy) is an error, never the update notice", async () => {
    useDevicesStore.getState().reset();
    serve(() => new Response("<html><body>Not Found</body></html>", { status: 404, headers: { "Content-Type": "text/html" } }));
    await useDevicesStore.getState().refresh();
    const s = useDevicesStore.getState();
    expect(s.error?.code).toBe("MALFORMED_MESSAGE");
    expect(s.error?.status).toBe(404);
    expect(s.updateReady).toBe(false);
    expect(pwa.requestAppUpdate).not.toHaveBeenCalled();
    useDevicesStore.getState().reset();
  });

  it("any other failure is the error as before, with no update notice", async () => {
    serve(() => jsonResponse(503, { error: { code: "INTERNAL", message: "down", retryable: true } }));
    await useDevicesStore.getState().refresh();
    const s = useDevicesStore.getState();
    expect(s.error?.code).toBe("INTERNAL");
    expect(s.updateReady).toBe(false);
    expect(pwa.requestAppUpdate).not.toHaveBeenCalled();
  });
});

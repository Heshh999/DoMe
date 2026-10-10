/**
 * Runtime wiring: a REST 401 mid-session signs the user out and closes the socket; a socket drop
 * marks pending confirmations as connection-lost (and an `open` clears it); the store clock ticks so
 * time-based freshness is re-evaluated without a frame.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Runtime, setRuntimeForTests } from "../src/app/runtime.ts";
import { api, configureApi } from "../src/lib/api.ts";
import { getExistingKeyPair, getOrCreateKeyPair, getStoredControllerId, resetDbHandleForTests, setStoredControllerId } from "../src/lib/controllerKey.ts";
import type { WebSocketLike } from "../src/lib/relay.ts";
import { useDevicesStore } from "../src/store/devices.ts";
import { pendingConfirmation, useLiveStore } from "../src/store/live.ts";
import { useSessionStore } from "../src/store/session.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const CONTROLLER = "22222222-2222-4222-8222-222222222222";
const PC = "33333333-3333-4333-8333-333333333333";
const KID = "SuoPiWQtA7FeESmVk-Yk4r0ucbmt81cCcsFjGdkxXoU";
const TS = "2026-10-08T12:00:00.000Z";
const session = { account: { id: ACCOUNT, email: "a@example.test", display_name: "A", created_at: TS }, csrf_token: "C".repeat(40), plan: "free" as const, limits: { max_enabled_pcs: 1, max_controllers: 2, routines: false, custom_layouts: false }, protocol_version: "1.1" };

class FakeSocket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  closed: number | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  send(data: string) {
    this.sent.push(data);
  }
  close(code?: number) {
    this.closed = code ?? 1005;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  receive(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  serverClose(code: number) {
    this.readyState = 3;
    this.onclose?.({ code, reason: "" });
  }
}

const noTimers = {
  setTimeout: (_fn: () => void, _ms: number): unknown => ({}),
  clearTimeout: (_h: unknown): void => undefined,
  setInterval: (_fn: () => void, _ms: number): unknown => ({}),
  clearInterval: (_h: unknown): void => undefined,
};

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function flush() {
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
}

let rt: Runtime | null = null;
const sockets: FakeSocket[] = [];

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  resetDbHandleForTests();
  sockets.length = 0;
  useLiveStore.getState().reset();
  useDevicesStore.getState().reset();
  useSessionStore.setState({ status: "signed_in", session, error: null });
  configureApi({ csrfToken: session.csrf_token });
  rt = new Runtime({
    relay: {
      kid: async () => KID,
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s;
      },
      timers: noTimers,
    },
    clockIntervalMs: 20,
  });
  setRuntimeForTests(rt);
});

afterEach(() => {
  rt?.stop();
  setRuntimeForTests(null);
  configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
});

const helloAck = { type: "hello_ack", protocol_version: "1.1", server_time: TS, connection_id: "44444444-4444-4444-8444-444444444444", controller_id: CONTROLLER };

async function connected(): Promise<FakeSocket> {
  rt!.start();
  // The first socket opens only after the hello proof is signed with a freshly generated key; under
  // full-suite load that can take more than a few macrotasks.
  for (let i = 0; i < 50 && sockets.length === 0; i++) await flush();
  const s = sockets[0]!;
  s.open();
  s.receive(helloAck);
  expect(rt!.relay.isOpen).toBe(true);
  return s;
}

describe("Runtime", () => {
  it("a 401 from any REST call mid-session flips the session to signed_out and closes the socket", async () => {
    await connected();
    configureApi({ fetchImpl: async () => respond(401, { error: { code: "UNAUTHENTICATED", message: "Sign in", retryable: false } }) });
    await expect(api.pcs()).rejects.toMatchObject({ status: 401 });
    expect(useSessionStore.getState().status).toBe("signed_out");
    expect(useSessionStore.getState().session).toBeNull();
    expect(rt!.relay.status).toBe("closed");
    expect(useLiveStore.getState().relayStatus).toBe("closed");
    // a state-changing call now fails locally: the CSRF token is gone with the session
    await expect(api.logout()).rejects.toMatchObject({ code: "UNAUTHENTICATED" });
  });

  it("other REST errors do not sign the user out", async () => {
    await connected();
    configureApi({ fetchImpl: async () => respond(500, { error: { code: "INTERNAL", message: "x", retryable: true } }) });
    await expect(api.pcs()).rejects.toMatchObject({ status: 500 });
    expect(useSessionStore.getState().status).toBe("signed_in");
    expect(rt!.relay.isOpen).toBe(true);
  });

  it("a socket drop marks the pending confirmation connection-lost; reopening clears it; a later result still applies", async () => {
    const s = await connected();
    const rec = await rt!.send({ pcId: PC, action: "app.close", target: { app_id: "notepad" } });
    s.receive({ type: "confirmation_required", command_id: rec.commandId, challenge_text: fixture.digests.challenge_text });
    let pending = pendingConfirmation(useLiveStore.getState().commands)!;
    expect(pending.commandId).toBe(rec.commandId);
    expect(pending.confirmation!.connectionLost).toBe(false);

    s.serverClose(1006);
    expect(useLiveStore.getState().relayStatus).toBe("reconnecting");
    pending = pendingConfirmation(useLiveStore.getState().commands)!;
    expect(pending.confirmation!.connectionLost).toBe(true);
    // answering over a dead socket fails without pretending
    await expect(rt!.respond(rec.commandId, "decline")).rejects.toThrow(/PC_RECONNECTING/);
    expect(pending.confirmation!.decision).toBeNull();

    // the local escape hatch sends nothing and hides the modal
    const sentBefore = s.sent.length;
    rt!.dismissConfirmation(rec.commandId);
    expect(s.sent.length).toBe(sentBefore);
    expect(pendingConfirmation(useLiveStore.getState().commands)).toBeNull();
    expect(useLiveStore.getState().commands.find((c) => c.commandId === rec.commandId)!.confirmation!.decision).toBe("dismissed");

    // the socket comes back and the relay's deadline result still lands on the record
    rt!.relay.nudge();
    await flush();
    const s2 = sockets[1]!;
    s2.open();
    s2.receive(helloAck);
    s2.receive({ type: "result", command_id: rec.commandId, origin: "relay", state: "failed", at: TS, duration_ms: 1000, error: { code: "COMMAND_EXPIRED", message: "expired", retryable: false } });
    const after = useLiveStore.getState().commands.find((c) => c.commandId === rec.commandId)!;
    expect(after.terminal?.state).toBe("failed");
    expect(after.terminal?.error?.code).toBe("COMMAND_EXPIRED");
  });

  it("reopening the socket clears connection-lost on a still pending confirmation", async () => {
    const s = await connected();
    const rec = await rt!.send({ pcId: PC, action: "app.close", target: { app_id: "notepad" } });
    s.receive({ type: "confirmation_required", command_id: rec.commandId, challenge_text: fixture.digests.challenge_text });
    s.serverClose(1006);
    expect(pendingConfirmation(useLiveStore.getState().commands)!.confirmation!.connectionLost).toBe(true);
    rt!.relay.nudge();
    await flush();
    sockets[1]!.open();
    sockets[1]!.receive(helloAck);
    expect(pendingConfirmation(useLiveStore.getState().commands)!.confirmation!.connectionLost).toBe(false);
  });

  it("signOut() enters signing_out before anything else, closes the socket, keeps the key and clears account state", async () => {
    await getOrCreateKeyPair();
    await setStoredControllerId(CONTROLLER);
    const s = await connected();
    const statuses: string[] = [];
    const unsub = useSessionStore.subscribe((st) => statuses.push(st.status));
    configureApi({ fetchImpl: async () => new Response(null, { status: 204 }) });
    await rt!.signOut();
    unsub();
    expect(statuses[0]).toBe("signing_out");
    expect(statuses).not.toContain("signed_out");
    expect(useSessionStore.getState().status).toBe("signing_out");
    expect(s.closed).toBe(1000);
    expect(rt!.relay.status).toBe("closed");
    expect(await getExistingKeyPair()).not.toBeNull();
    expect(await getStoredControllerId()).toBeNull();
    expect(useLiveStore.getState().commands).toEqual([]);
  });

  it("signOut({ forgetInstallation: true }) deletes the key and only resolves once the deletion is committed", async () => {
    await getOrCreateKeyPair();
    await connected();
    configureApi({ fetchImpl: async () => new Response(null, { status: 204 }) });
    await rt!.signOut({ forgetInstallation: true });
    expect(await getExistingKeyPair()).toBeNull();
    expect(await getStoredControllerId()).toBeNull();
    expect(useSessionStore.getState().status).toBe("signing_out");
  });

  it("a 401 or a 4008 close during a manual sign-out leaves the status at signing_out (no automatic redirect)", async () => {
    const s = await connected();
    configureApi({ fetchImpl: async () => respond(401, { error: { code: "UNAUTHENTICATED", message: "Sign in", retryable: false } }) });
    const done = rt!.signOut();
    s.serverClose(4008);
    await done;
    expect(useSessionStore.getState().status).toBe("signing_out");
    expect(useSessionStore.getState().session).toBeNull();
  });

  it("a pc_status re-reads the grants already loaded for that PC (permission changed on the PC)", async () => {
    const grant = { id: "55555555-5555-4555-8555-555555555555", controller_id: CONTROLLER, pc_id: PC, capabilities: ["status", "media", "pointer"], created_at: TS };
    const fetched: string[] = [];
    configureApi({
      csrfToken: session.csrf_token,
      fetchImpl: async (input) => {
        fetched.push(String(input));
        return respond(200, { grants: [grant] });
      },
    });
    useDevicesStore.setState({ grantsByPc: { [PC]: [{ ...grant, capabilities: ["status", "media"] }] } });
    const s = await connected();
    s.receive({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
    await flush();
    expect(fetched.filter((u) => u.endsWith(`/v1/pcs/${PC}/grants`))).toHaveLength(1);
    expect(useDevicesStore.getState().grantsByPc[PC]![0]!.capabilities).toContain("pointer");

    // a PC whose grants were never shown is not fetched
    const other = "66666666-6666-4666-8666-666666666666";
    s.receive({ type: "pc_status", pc_id: other, connection: "online", last_seen: TS });
    await flush();
    expect(fetched.some((u) => u.includes(other))).toBe(false);
  });

  it("advances the live store clock while started and stops it on stop()", async () => {
    const before = useLiveStore.getState().now;
    useLiveStore.setState({ now: before - 60_000 });
    rt!.start();
    await new Promise((r) => setTimeout(r, 80));
    expect(useLiveStore.getState().now).toBeGreaterThanOrEqual(before);
    rt!.stop();
    const frozen = useLiveStore.getState().now;
    useLiveStore.setState({ now: frozen - 60_000 });
    await new Promise((r) => setTimeout(r, 80));
    expect(useLiveStore.getState().now).toBe(frozen - 60_000);
  });
});

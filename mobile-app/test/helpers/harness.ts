/**
 * Shared fixtures for component tests that need the real Runtime over a fake socket: a fresh
 * fake-indexeddb per test, a signed-in session store, a FakeSocket factory with inert timers, and the
 * jsdom `<dialog>` polyfill the Sheet needs. Test-only: nothing here is imported by src/.
 */
import { IDBFactory } from "fake-indexeddb";

import type { rest } from "@dome/protocol";

import { Runtime, setRuntimeForTests } from "../../src/app/runtime.ts";
import { configureApi } from "../../src/lib/api.ts";
import { resetDbHandleForTests } from "../../src/lib/controllerKey.ts";
import type { WebSocketLike } from "../../src/lib/relay.ts";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { useSessionStore } from "../../src/store/session.ts";

export const ACCOUNT = "11111111-1111-4111-8111-111111111111";
export const CONTROLLER = "22222222-2222-4222-8222-222222222222";
export const PC = "33333333-3333-4333-8333-333333333333";
export const OTHER_PC = "55555555-5555-4555-8555-555555555555";
export const TS = "2026-10-08T12:00:00.000Z";

export const SESSION: rest.SessionResponse = {
  account: { id: ACCOUNT, email: "a@example.test", display_name: "A", created_at: TS },
  csrf_token: "C".repeat(40),
  plan: "free",
  limits: { max_enabled_pcs: 1, max_controllers: 2, routines: false, custom_layouts: false },
  protocol_version: "1.0",
};

export const OFFICE_PC: rest.Pc = { id: PC, name: "Office PC", enabled: true, connection: "online", last_seen: TS, created_at: TS, platform: "windows" };

export const HELLO_ACK = { type: "hello_ack", protocol_version: "1.0", server_time: TS, connection_id: "44444444-4444-4444-8444-444444444444", controller_id: CONTROLLER };

export class FakeSocket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  closed: { code: number | undefined; reason: string | undefined } | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  send(data: string) {
    this.sent.push(data);
  }
  close(code?: number, reason?: string) {
    this.closed = { code, reason };
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
  frames(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

export const noTimers = {
  setTimeout: (_fn: () => void, _ms: number): unknown => ({}),
  clearTimeout: (_h: unknown): void => undefined,
  setInterval: (_fn: () => void, _ms: number): unknown => ({}),
  clearInterval: (_h: unknown): void => undefined,
};

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function emptyResponse(status = 204): Response {
  return new Response(null, { status });
}

export async function flush(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 0));
}

/** jsdom has no <dialog>.showModal; the Sheet calls it on open. */
export function installDialogPolyfill(): void {
  if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute("open");
    };
  }
}

/** A brand-new IndexedDB (no key, no state) for this test. */
export function freshStorage(): void {
  globalThis.indexedDB = new IDBFactory();
  resetDbHandleForTests();
}

export function signedIn(): void {
  useLiveStore.getState().reset();
  useDevicesStore.getState().reset();
  useSessionStore.setState({ status: "signed_in", session: SESSION, error: null });
  configureApi({ csrfToken: SESSION.csrf_token });
}

export interface RuntimeHarness {
  rt: Runtime;
  sockets: FakeSocket[];
  /** start() the runtime and complete hello/hello_ack on the first socket. */
  connect(): Promise<FakeSocket>;
  teardown(): void;
}

/**
 * A Runtime whose relay uses fake sockets and inert timers. The controller key is the real one from
 * (fake) IndexedDB so signatures and pairing verification codes are genuine. The store clock interval
 * is long so it never re-renders a component mid-assertion.
 */
export function makeRuntime(): RuntimeHarness {
  const sockets: FakeSocket[] = [];
  const rt = new Runtime({
    relay: {
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s;
      },
      timers: noTimers,
    },
    clockIntervalMs: 600_000,
  });
  setRuntimeForTests(rt);
  return {
    rt,
    sockets,
    async connect() {
      rt.start();
      await flush();
      const s = sockets[0];
      if (!s) throw new Error("no socket was opened");
      s.open();
      s.receive(HELLO_ACK);
      if (!rt.relay.isOpen) throw new Error("relay did not open");
      return s;
    },
    teardown() {
      rt.stop();
      setRuntimeForTests(null);
      configureApi({ csrfToken: null, fetchImpl: (i, init) => fetch(i, init), onUnauthenticated: null });
    },
  };
}

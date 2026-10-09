import { describe, expect, it } from "vitest";

import type { relayFrames } from "@dome/protocol";

import { NUDGE_DEADLINE_MS, RelayClient, relayUrl, type RelayClientOptions, type RelayStatus, type WebSocketLike } from "../src/lib/relay.ts";

const KID = "SuoPiWQtA7FeESmVk-Yk4r0ucbmt81cCcsFjGdkxXoU";
const CONTROLLER = "22222222-2222-4222-8222-222222222222";
const PC = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:00.000Z";

class FakeSocket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  closed: { code: number | undefined; reason: string | undefined } | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(readonly url: string) {}
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
    this.onmessage?.({ data: typeof frame === "string" ? frame : JSON.stringify(frame) });
  }
  serverClose(code: number) {
    this.readyState = 3;
    this.onclose?.({ code, reason: "" });
  }
  frames(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

function fakeTimers() {
  let now = 0;
  const queue: Array<{ at: number; fn: () => void; id: number; every?: number }> = [];
  let seq = 0;
  const timers = {
    setTimeout(fn: () => void, ms: number): unknown {
      const id = ++seq;
      queue.push({ at: now + ms, fn, id });
      return id;
    },
    clearTimeout(h: unknown) {
      const i = queue.findIndex((q) => q.id === h);
      if (i >= 0) queue.splice(i, 1);
    },
    setInterval(fn: () => void, ms: number): unknown {
      const id = ++seq;
      queue.push({ at: now + ms, fn, id, every: ms });
      return id;
    },
    clearInterval(h: unknown) {
      const i = queue.findIndex((q) => q.id === h);
      if (i >= 0) queue.splice(i, 1);
    },
  };
  const advance = async (ms: number) => {
    const target = now + ms;
    for (;;) {
      queue.sort((a, b) => a.at - b.at);
      const n = queue[0];
      if (!n || n.at > target) break;
      now = n.at;
      if (n.every) n.at = now + n.every;
      else queue.shift();
      n.fn();
      await Promise.resolve();
    }
    now = target;
  };
  return { timers, advance };
}

async function setup(extra: Partial<RelayClientOptions> = {}) {
  const sockets: FakeSocket[] = [];
  const ft = fakeTimers();
  const statuses: RelayStatus[] = [];
  const frames: relayFrames.RelayToController[] = [];
  const client = new RelayClient({
    url: "ws://test/ws/controller",
    kid: async () => KID,
    componentVersion: "0.1.0-test",
    socketFactory: (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s;
    },
    timers: ft.timers,
    random: () => 0.5,
    backoffBaseMs: 100,
    backoffMaxMs: 1000,
    ...extra,
  });
  client.on("status", (s) => statuses.push(s));
  client.on("frame", (f) => frames.push(f));
  const flush = async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  };
  return { client, sockets, ft, statuses, frames, flush };
}

const helloAck = (controllerId?: string) => ({ type: "hello_ack", protocol_version: "1.0", server_time: TS, connection_id: "44444444-4444-4444-8444-444444444444", ...(controllerId ? { controller_id: controllerId } : {}) });

describe("RelayClient", () => {
  it("relayUrl derives wss from the page origin or the configured API origin", () => {
    expect(relayUrl("", { protocol: "https:", host: "dome.example" })).toBe("wss://dome.example/ws/controller");
    expect(relayUrl("", { protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/ws/controller");
    expect(relayUrl("https://api.dome.example", { protocol: "https:", host: "x" })).toBe("wss://api.dome.example/ws/controller");
  });

  it("sends hello{kid} first, binds the controller from hello_ack and re-subscribes", async () => {
    const { client, sockets, statuses, flush } = await setup();
    client.setSubscriptions([PC]);
    client.connect();
    await flush();
    const s = sockets[0]!;
    s.open();
    const hello = s.frames()[0]!;
    expect(hello).toMatchObject({ type: "hello", component: "controller", kid: KID, protocol_versions: ["1.1"], registry_version: "1.1" });
    expect(client.isOpen).toBe(false);
    s.receive(helloAck(CONTROLLER));
    expect(client.isOpen).toBe(true);
    expect(client.controllerId).toBe(CONTROLLER);
    expect(s.frames()[1]).toEqual({ type: "subscribe", pc_ids: [PC] });
    expect(statuses).toEqual(["connecting", "open"]);
    client.setSubscriptions([PC, PC]);
    expect(s.frames()[2]).toEqual({ type: "subscribe", pc_ids: [PC] });
  });

  it("attaches the signed hello proof when a signer is configured, and omits it when the signer has nothing to sign with", async () => {
    const proof = { v: 1, alg: "ES256", kid: KID, payload: "{}", sig: "A".repeat(86) } as const;
    let calls = 0;
    const { sockets, flush } = await setup({
      helloProof: async (kid) => {
        calls++;
        expect(kid).toBe(KID);
        return calls === 1 ? proof : null;
      },
    }).then((h) => {
      h.client.connect();
      return h;
    });
    await flush();
    sockets[0]!.open();
    expect(sockets[0]!.frames()[0]).toMatchObject({ type: "hello", kid: KID, proof });
    // a second socket (reconnect) asks the signer again; null → bare kid, the relay leaves it unbound
    sockets[0]!.close(1006);
    await flush();
    const { client: c2, sockets: s2, flush: f2 } = await setup({ helloProof: async () => null });
    c2.connect();
    await f2();
    s2[0]!.open();
    expect(s2[0]!.frames()[0]).not.toHaveProperty("proof");
    expect(calls).toBeGreaterThanOrEqual(1);
  });

  it("an unpaired kid gets hello_ack without controller_id: open but not bound, no subscribe sent", async () => {
    const { client, sockets, flush } = await setup();
    client.setSubscriptions([PC]);
    client.connect();
    await flush();
    sockets[0]!.open();
    sockets[0]!.receive(helloAck());
    expect(client.isOpen).toBe(true);
    expect(client.controllerId).toBeNull();
    expect(sockets[0]!.frames()).toHaveLength(1);
  });

  it("drops invalid, unknown and oversized inbound frames; answers ping; forwards valid frames", async () => {
    const { client, sockets, frames, flush } = await setup();
    client.connect();
    await flush();
    const s = sockets[0]!;
    s.open();
    s.receive(helloAck(CONTROLLER));
    s.receive("not json");
    s.receive({ type: "unknown_type" });
    s.receive({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS, extra: 1 });
    s.receive({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [{ browser_instance_id: "b", tab_id: 1, script_attached: true, context: "watch", ad_showing: false, is_live: false, in_playlist: false, title: "<img src=x onerror=alert(1)>" }] } });
    s.receive({ type: "ping", t: TS });
    s.receive(`{"type":"pc_status","pc_id":"${PC}","connection":"online","last_seen":null,"pad":"${"x".repeat(70_000)}"}`);
    expect(frames.map((f) => f.type)).toEqual(["hello_ack", "state"]);
    expect(s.frames().some((f) => f.type === "pong" && f.t === TS)).toBe(true);
    expect(client.isOpen).toBe(true);
  });

  it("validates outbound frames and refuses to send when not open", async () => {
    const { client, sockets, flush } = await setup();
    expect(() => client.send({ type: "ping" })).toThrow(/PC_RECONNECTING/);
    client.connect();
    await flush();
    sockets[0]!.open();
    sockets[0]!.receive(helloAck(CONTROLLER));
    expect(() => client.send({ type: "subscribe", pc_ids: [] } as unknown as relayFrames.ControllerToRelay)).toThrow(/MALFORMED_MESSAGE/);
    expect(() => client.send({ type: "nope" } as unknown as relayFrames.ControllerToRelay)).toThrow(/MALFORMED_MESSAGE/);
  });

  it("reconnects with backoff after a drop, resets the attempt counter on success and treats the resume nudge as an immediate reconnect", async () => {
    const { client, sockets, ft, statuses, flush } = await setup();
    client.connect();
    await flush();
    sockets[0]!.open();
    sockets[0]!.receive(helloAck(CONTROLLER));
    sockets[0]!.serverClose(1006);
    expect(client.isOpen).toBe(false);
    expect(statuses[statuses.length - 1]).toBe("reconnecting");
    expect(sockets).toHaveLength(1);
    await ft.advance(99);
    expect(sockets).toHaveLength(1);
    await ft.advance(2);
    await flush();
    expect(sockets).toHaveLength(2); // 100 ms * jitter(1.0)
    sockets[1]!.serverClose(1006);
    await ft.advance(150);
    await flush();
    expect(sockets).toHaveLength(2); // second delay is 200 ms
    await ft.advance(60);
    await flush();
    expect(sockets).toHaveLength(3);
    // visibility/online nudge while waiting → reconnect now
    sockets[2]!.serverClose(1006);
    client.nudge();
    await flush();
    expect(sockets).toHaveLength(4);
    sockets[3]!.open();
    sockets[3]!.receive(helloAck(CONTROLLER));
    expect(client.isOpen).toBe(true);
    // nudge while open asks for state and pings; it does not reconnect by itself
    client.nudge();
    expect(sockets[3]!.frames().some((f) => f.type === "ping")).toBe(true);
    expect(sockets).toHaveLength(4);
  });

  it("a resume nudge on an open socket re-sends subscribe (relay replies with pc_status + cached state) and pings with t", async () => {
    const { client, sockets, flush, ft } = await setup();
    client.setSubscriptions([PC]);
    client.connect();
    await flush();
    const s = sockets[0]!;
    s.open();
    s.receive(helloAck(CONTROLLER));
    const before = s.frames().length;
    client.nudge();
    const after = s.frames().slice(before);
    expect(after[0]).toEqual({ type: "subscribe", pc_ids: [PC] });
    expect(after[1]).toMatchObject({ type: "ping" });
    expect(typeof (after[1] as { t?: unknown }).t).toBe("string");
    // the relay answers → socket alive, nothing else happens
    s.receive({ type: "pong", t: (after[1] as { t: string }).t });
    await ft.advance(NUDGE_DEADLINE_MS + 1);
    await flush();
    expect(sockets).toHaveLength(1);
    expect(client.isOpen).toBe(true);
  });

  it("a nudge with no inbound frame within the deadline tears the dead socket down and reconnects right away", async () => {
    const { client, sockets, flush, ft, statuses } = await setup();
    client.setSubscriptions([PC]);
    client.connect();
    await flush();
    const s = sockets[0]!;
    s.open();
    s.receive(helloAck(CONTROLLER));
    client.nudge();
    await ft.advance(NUDGE_DEADLINE_MS - 1);
    await flush();
    expect(sockets).toHaveLength(1);
    await ft.advance(2);
    await flush();
    expect(s.closed?.code).toBe(1000);
    expect(sockets).toHaveLength(2);
    expect(statuses[statuses.length - 1]).toBe("reconnecting");
    expect(client.isOpen).toBe(false);
    sockets[1]!.open();
    sockets[1]!.receive(helloAck(CONTROLLER));
    expect(client.isOpen).toBe(true);
    expect(sockets[1]!.frames()[1]).toEqual({ type: "subscribe", pc_ids: [PC] });
  });

  it("a nudge on an unbound open socket (no controller yet) only pings", async () => {
    const { client, sockets, flush } = await setup();
    client.setSubscriptions([PC]);
    client.connect();
    await flush();
    sockets[0]!.open();
    sockets[0]!.receive(helloAck());
    client.nudge();
    expect(sockets[0]!.frames().map((f) => f.type)).toEqual(["hello", "ping"]);
  });

  it("hello timeout and idle timeout tear the socket down and reconnect", async () => {
    const { client, sockets, ft, flush } = await setup();
    client.connect();
    await flush();
    sockets[0]!.open();
    await ft.advance(10_001);
    await flush();
    expect(sockets[0]!.closed?.code).toBe(1000);
    await ft.advance(200);
    await flush();
    expect(sockets.length).toBeGreaterThanOrEqual(2);
  });

  it("close code 4003 and `revoked` frames stop reconnecting and report revoked; 4008 reports unauthenticated", async () => {
    const a = await setup();
    a.client.connect();
    await a.flush();
    a.sockets[0]!.open();
    a.sockets[0]!.receive(helloAck(CONTROLLER));
    a.sockets[0]!.receive({ type: "revoked", reason: "controller_revoked" });
    expect(a.client.status).toBe("revoked");
    await a.ft.advance(5000);
    expect(a.sockets).toHaveLength(1);

    const b = await setup();
    b.client.connect();
    await b.flush();
    b.sockets[0]!.open();
    b.sockets[0]!.serverClose(4003);
    expect(b.client.status).toBe("revoked");

    const c = await setup();
    c.client.connect();
    await c.flush();
    c.sockets[0]!.open();
    c.sockets[0]!.serverClose(4008);
    expect(c.client.status).toBe("unauthenticated");
    await c.ft.advance(5000);
    expect(c.sockets).toHaveLength(1);
  });

  it("PROTOCOL_INCOMPATIBLE error frames stop the client with status incompatible", async () => {
    const { client, sockets, flush } = await setup();
    client.connect();
    await flush();
    sockets[0]!.open();
    sockets[0]!.receive({ type: "error", error: { code: "PROTOCOL_INCOMPATIBLE", message: "x", retryable: false, detail: { peer: ["1.0"], supported: ["2.0"] } } });
    expect(client.status).toBe("incompatible");
  });

  it("close() ends everything and reconnect() rebinds on a fresh socket", async () => {
    const { client, sockets, flush, ft } = await setup();
    client.connect();
    await flush();
    sockets[0]!.open();
    sockets[0]!.receive(helloAck(CONTROLLER));
    client.reconnect();
    await flush();
    expect(sockets[0]!.closed?.code).toBe(1000);
    expect(sockets).toHaveLength(2);
    expect(client.controllerId).toBeNull();
    sockets[1]!.open();
    sockets[1]!.receive(helloAck(CONTROLLER));
    expect(client.isOpen).toBe(true);
    client.close();
    expect(client.status).toBe("closed");
    expect(sockets[1]!.closed?.code).toBe(1000);
    await ft.advance(10_000);
    expect(sockets).toHaveLength(2);
  });
});

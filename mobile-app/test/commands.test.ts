import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { generateControllerKeyPair, schemas, type relayFrames, verifyAndParseCommand, exportPublicJwk, kidFromJwk } from "@dome/protocol";

import { CommandService, isTerminal, type CommandRecord } from "../src/lib/commands.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const CONTROLLER = "22222222-2222-4222-8222-222222222222";
const PC = "33333333-3333-4333-8333-333333333333";
const TS = "2026-10-08T12:00:01.000Z";

function fakeTimers() {
  let now = 0;
  const queue: Array<{ at: number; fn: () => void; id: number }> = [];
  let seq = 0;
  return {
    timers: {
      setTimeout(fn: () => void, ms: number): unknown {
        const id = ++seq;
        queue.push({ at: now + ms, fn, id });
        return id;
      },
      clearTimeout(h: unknown) {
        const i = queue.findIndex((q) => q.id === h);
        if (i >= 0) queue.splice(i, 1);
      },
    },
    advance(ms: number) {
      const target = now + ms;
      for (;;) {
        queue.sort((a, b) => a.at - b.at);
        const n = queue[0];
        if (!n || n.at > target) break;
        now = n.at;
        queue.shift();
        n.fn();
      }
      now = target;
    },
    pending: () => queue.length,
  };
}

async function harness(opts: { identity?: boolean; socketOpen?: boolean } = {}) {
  const keyPair = await generateControllerKeyPair();
  const jwk = await exportPublicJwk(keyPair.publicKey);
  const kid = await kidFromJwk(jwk);
  const sent: relayFrames.ControllerToRelay[] = [];
  const changes: CommandRecord[] = [];
  const ft = fakeTimers();
  const service = new CommandService({
    send(frame) {
      if (opts.socketOpen === false) throw new Error("PC_RECONNECTING: not connected");
      schemas.validateFrame("controller_to_relay", frame);
      sent.push(frame);
    },
    identity: () => (opts.identity === false ? null : { accountId: ACCOUNT, controllerId: CONTROLLER }),
    keyPair: async () => keyPair,
    onChange: (r) => changes.push(r),
    timers: ft.timers,
    now: () => new Date("2026-10-08T12:00:00.000Z"),
  });
  return { service, sent, changes, ft, keyPair, jwk, kid };
}

describe("CommandService lifecycle", () => {
  let h: Awaited<ReturnType<typeof harness>>;
  beforeEach(async () => {
    h = await harness();
  });

  it("builds, signs and sends a command frame the relay/PC can verify; state is created (shown as Sent)", async () => {
    const rec = await h.service.send({ pcId: PC, action: "windows.set_volume", params: { value: 35 } });
    expect(rec.state).toBe("created");
    expect(rec.terminal).toBeNull();
    expect(h.sent).toHaveLength(1);
    const frame = h.sent[0] as relayFrames.ControllerCommand;
    expect(frame.type).toBe("command");
    expect(frame.pc_id).toBe(PC);
    expect(frame.envelope.kid).toBe(h.kid);
    const verified = await verifyAndParseCommand(frame.envelope, (k) => (k === h.kid ? { controllerId: CONTROLLER, accountId: ACCOUNT, jwk: h.jwk } : null), new Date("2026-10-08T12:00:00.500Z"));
    expect(verified.payload.command_id).toBe(rec.commandId);
    expect(verified.params).toEqual({ value: 35 });
    expect(verified.payload.expires_at).toBe("2026-10-08T12:00:30.000Z"); // 30 s default lifetime
  });

  it("disruptive actions get the 90 s lifetime and never carry a confirm flag", async () => {
    const rec = await h.service.send({ pcId: PC, action: "power.sleep" });
    expect(rec.expiresAt).toBe("2026-10-08T12:01:30.000Z");
    expect(JSON.stringify(rec.params)).not.toMatch(/confirm/);
  });

  it("refuses to build invalid params/targets and records nothing", async () => {
    await expect(h.service.send({ pcId: PC, action: "windows.set_volume", params: { value: 135 } })).rejects.toThrow(/INVALID_PARAMETERS/);
    await expect(h.service.send({ pcId: PC, action: "youtube.next" })).rejects.toThrow(/TARGET_REQUIRED/);
    await expect(h.service.send({ pcId: PC, action: "app.launch", params: { app_id: "C:\\evil.exe" } })).rejects.toThrow(/INVALID_PARAMETERS/);
    expect(h.sent).toHaveLength(0);
    expect(h.service.list()).toHaveLength(0);
  });

  it("without a bound controller or an open socket nothing is sent", async () => {
    const noId = await harness({ identity: false });
    await expect(noId.service.send({ pcId: PC, action: "system.ping" })).rejects.toThrow(/UNKNOWN_KEY/);
    const closed = await harness({ socketOpen: false });
    await expect(closed.service.send({ pcId: PC, action: "system.ping" })).rejects.toThrow(/PC_RECONNECTING/);
    expect(closed.service.list()).toHaveLength(0);
  });

  it("ack frames move the state forward; only a result frame is terminal and carries the validated result", async () => {
    const rec = await h.service.send({ pcId: PC, action: "windows.set_volume", params: { value: 35 } });
    expect(h.service.handleFrame({ type: "ack", command_id: rec.commandId, state: "accepted", at: TS })).toBe(true);
    expect(h.service.get(rec.commandId)!.state).toBe("accepted");
    h.service.handleFrame({ type: "ack", command_id: rec.commandId, state: "executing", at: TS });
    expect(h.service.get(rec.commandId)!.state).toBe("executing");
    expect(h.service.get(rec.commandId)!.terminal).toBeNull();
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 120, result: { value: 35, muted: false } });
    const done = h.service.get(rec.commandId)!;
    expect(done.state).toBe("succeeded");
    expect(done.terminal).toMatchObject({ origin: "agent", state: "succeeded", durationMs: 120, result: { value: 35, muted: false }, resultInvalid: false });
    expect(isTerminal(done.state)).toBe(true);
    // a late ack after terminal is ignored
    h.service.handleFrame({ type: "ack", command_id: rec.commandId, state: "executing", at: TS });
    expect(h.service.get(rec.commandId)!.state).toBe("succeeded");
  });

  it("an invalid success result is flagged instead of rendered", async () => {
    const rec = await h.service.send({ pcId: PC, action: "windows.set_volume", params: { value: 35 } });
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 1, result: { value: 500, muted: false } });
    expect(h.service.get(rec.commandId)!.terminal).toMatchObject({ result: null, resultInvalid: true });
  });

  it("relay-origin failures are terminal with their error; a frame for an unknown command is not claimed", async () => {
    const rec = await h.service.send({ pcId: PC, action: "system.ping" });
    expect(h.service.handleFrame({ type: "result", command_id: "00000000-0000-4000-8000-000000000000", origin: "relay", state: "failed", at: TS, duration_ms: 0, error: { code: "PC_OFFLINE", message: "x", retryable: true } })).toBe(false);
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "relay", state: "failed", at: TS, duration_ms: 0, error: { code: "PC_OFFLINE", message: "The PC is not connected", retryable: true } });
    expect(h.service.get(rec.commandId)!.terminal).toMatchObject({ origin: "relay", state: "failed", error: { code: "PC_OFFLINE" } });
  });

  it("no answer by the deadline is flagged as noAnswer, never as failed; the eventual result clears it", async () => {
    const rec = await h.service.send({ pcId: PC, action: "youtube.set_paused", params: { paused: true }, target: { browser_instance_id: "bi_test0001", tab_id: 7, tab_token: "dGFiLXRva2VuLTAwMDAwMD" } });
    h.ft.advance(30_000 + 8_000 + 10_000 - 1);
    expect(h.service.get(rec.commandId)!.noAnswer).toBe(false);
    h.ft.advance(2);
    const r = h.service.get(rec.commandId)!;
    expect(r.noAnswer).toBe(true);
    expect(r.terminal).toBeNull();
    expect(r.state).toBe("created");
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "relay", state: "failed", at: TS, duration_ms: 0, error: { code: "COMMAND_EXPIRED", message: "x", retryable: true } });
    expect(h.service.get(rec.commandId)!.noAnswer).toBe(false);
    expect(h.ft.pending()).toBe(0);
  });

  it("post-terminal results are dropped, except an agent correction of a relay outcome_unknown", async () => {
    const rec = await h.service.send({ pcId: PC, action: "youtube.next", target: { browser_instance_id: "bi_test0001", tab_id: 7, tab_token: "dGFiLXRva2VuLTAwMDAwMD" } });
    h.service.handleFrame({ type: "ack", command_id: rec.commandId, state: "executing", at: TS });
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "relay", state: "outcome_unknown", at: TS, duration_ms: 0, error: { code: "OUTCOME_UNKNOWN", message: "x", retryable: false } });
    expect(h.service.get(rec.commandId)!.terminal!.state).toBe("outcome_unknown");
    const tab = { browser_instance_id: "bi_test0001", tab_id: 7, script_attached: true, context: "watch", ad_showing: false, is_live: false, in_playlist: false, video_id: "abc" };
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 900, result: { tab, previous_video_id: "xyz" } });
    expect(h.service.get(rec.commandId)!.terminal).toMatchObject({ origin: "agent", state: "succeeded" });
    // any further result is ignored
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "agent", state: "failed", at: TS, duration_ms: 1, error: { code: "OS_ERROR", message: "x", retryable: false } });
    expect(h.service.get(rec.commandId)!.terminal!.state).toBe("succeeded");
  });

  it("cancel sends a cancel frame for an in-flight command only", async () => {
    const rec = await h.service.send({ pcId: PC, action: "power.sleep" });
    h.service.cancel(rec.commandId);
    expect(h.sent[1]).toEqual({ type: "cancel", pc_id: PC, command_id: rec.commandId });
    h.service.handleFrame({ type: "result", command_id: rec.commandId, origin: "agent", state: "canceled", at: TS, duration_ms: 1, error: { code: "POWER_CANCELED", message: "x", retryable: false } });
    h.service.cancel(rec.commandId);
    expect(h.sent).toHaveLength(2);
  });
});

describe("confirmation transaction through the service", () => {
  it("confirmation_required → awaiting_confirmation → signed approve/decline frame", async () => {
    const h = await harness();
    // Build the command with the ids the fixture challenge names, so the challenge binds to it.
    const challenge = JSON.parse(fixture.digests.challenge_text) as { command_id: string; pc_id: string; controller_id: string };
    const keyed = new CommandService({
      send: (f) => h.sent.push(f),
      identity: () => ({ accountId: ACCOUNT, controllerId: challenge.controller_id }),
      keyPair: async () => h.keyPair,
      onChange: () => undefined,
      timers: h.ft.timers,
      now: () => new Date("2026-10-08T12:00:00.000Z"),
    });
    const rec = await keyed.send({ pcId: challenge.pc_id, action: "app.close", target: { app_id: "notepad" } });
    // the PC answers with a challenge for *its* command id; ours differs → binding problem, Decline only
    expect(keyed.handleFrame({ type: "confirmation_required", command_id: rec.commandId, challenge_text: fixture.digests.challenge_text })).toBe(true);
    const r1 = keyed.get(rec.commandId)!;
    expect(r1.state).toBe("awaiting_confirmation");
    expect(r1.confirmation!.bindingProblem).toMatch(/different command/);
    await expect(keyed.respondToChallenge(rec.commandId, "approve")).rejects.toThrow(/CONFIRMATION_INVALID/);
    await keyed.respondToChallenge(rec.commandId, "decline");
    const declineFrame = h.sent[h.sent.length - 1] as relayFrames.ControllerConfirmation;
    expect(declineFrame.type).toBe("confirmation");

    // Now a challenge that matches exactly what we sent
    const rec2 = await keyed.send({ pcId: challenge.pc_id, action: "app.close", target: { app_id: "notepad" } });
    const text = fixture.digests.challenge_text.replace(challenge.command_id, rec2.commandId);
    keyed.handleFrame({ type: "confirmation_required", command_id: rec2.commandId, challenge_text: text });
    const r2 = keyed.get(rec2.commandId)!;
    expect(r2.confirmation!.bindingProblem).toBeNull();
    expect(r2.confirmation!.parsed.raw).toBe(text);
    await keyed.respondToChallenge(rec2.commandId, "approve");
    expect(keyed.get(rec2.commandId)!.confirmation!.decision).toBe("approve");
    const frame = h.sent[h.sent.length - 1] as relayFrames.ControllerConfirmation;
    const verified = await verifyAndParseCommand; // type anchor only
    expect(typeof verified).toBe("function");
    expect(frame.envelope.kid).toBe(h.kid);
    expect(frame.pc_id).toBe(challenge.pc_id);
    // decision cannot be sent twice after the command finished
    keyed.handleFrame({ type: "result", command_id: rec2.commandId, origin: "agent", state: "succeeded", at: TS, duration_ms: 50, result: { app_id: "notepad", closed: true } });
    await expect(keyed.respondToChallenge(rec2.commandId, "approve")).rejects.toThrow(/CONFIRMATION_EXPIRED/);
  });

  it("an unparseable challenge is ignored (no Approve ever offered)", async () => {
    const h = await harness();
    const rec = await h.service.send({ pcId: PC, action: "power.shutdown" });
    expect(h.service.handleFrame({ type: "confirmation_required", command_id: rec.commandId, challenge_text: '{"evil":true}' })).toBe(true);
    expect(h.service.get(rec.commandId)!.confirmation).toBeNull();
    expect(h.service.get(rec.commandId)!.state).toBe("created");
  });
});

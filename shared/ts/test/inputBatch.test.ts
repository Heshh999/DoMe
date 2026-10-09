import { describe, expect, it } from "vitest";

import { buildInputBatchPayload, exportPublicJwk, generateControllerKeyPair, inputEventCapabilities, kidFromJwk, ProtocolError, schemas, signInputBatch, verifyEnvelope, type InputEvent } from "../src/index.ts";

const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const CONTROLLER = "22222222-2222-4222-8222-222222222222";
const PC = "33333333-3333-4333-8333-333333333333";
const SESSION = "AAAAAAAAAAAAAAAAAAAAAA";

describe("input batch", () => {
  it("builds a schema-valid batch with a 5 s window, signs it, and maps events to capabilities", async () => {
    const pair = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(pair.publicKey);
    const kid = await kidFromJwk(jwk);
    const events: InputEvent[] = [
      { type: "pointer_move", dx: 3, dy: -4 },
      { type: "pointer_button", button: "right", action: "click" },
      { type: "text", text: "Hi 👋" },
      { type: "key", key: "enter" },
    ];
    const now = new Date("2026-10-09T09:00:00.000Z");
    const payload = buildInputBatchPayload({ accountId: ACCOUNT, controllerId: CONTROLLER, targetPcId: PC, inputSessionId: SESSION, seq: 3, events, now });
    expect(payload).toMatchObject({ type: "input_batch", protocol_version: "1.1", seq: 3, issued_at: "2026-10-09T09:00:00.000Z", expires_at: "2026-10-09T09:00:05.000Z" });
    schemas.validateInputBatchPayload(payload);
    const envelope = await signInputBatch(pair.privateKey, pair.publicKey, payload);
    const verified = await verifyEnvelope(envelope, (k) => (k === kid ? jwk : null));
    expect(verified.payload).toEqual(payload);
    expect([...inputEventCapabilities(events)].sort()).toEqual(["keyboard", "pointer"]);
    expect([...inputEventCapabilities([])]).toEqual([]);
    schemas.validateFrame("controller_to_relay", { type: "input_batch", pc_id: PC, envelope });
  });

  it("refuses out-of-bound events", async () => {
    const pair = await generateControllerKeyPair();
    const bad = buildInputBatchPayload({ accountId: ACCOUNT, controllerId: CONTROLLER, targetPcId: PC, inputSessionId: SESSION, seq: 1, events: [{ type: "pointer_move", dx: 99999, dy: 0 }] });
    await expect(signInputBatch(pair.privateKey, pair.publicKey, bad)).rejects.toBeInstanceOf(ProtocolError);
    expect(() => schemas.validateInputBatchPayload({ ...bad, events: Array.from({ length: 65 }, () => ({ type: "pointer_move", dx: 1, dy: 1 })) })).toThrow(ProtocolError);
  });
});

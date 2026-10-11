/**
 * rules.command_version_stamping: every signed payload carries the lowest protocol version that defines it, so a phone
 * update never breaks an older PC, and verifiers refuse a stamp below the action's `since`.
 */
import { describe, expect, it } from "vitest";

import {
  INPUT_BATCH_PROTOCOL_VERSION,
  PROTOCOL_VERSION,
  ProtocolError,
  buildCommandPayload,
  buildConfirmationPayload,
  buildInputBatchPayload,
  dumpsCompact,
  exportPublicJwk,
  generateControllerKeyPair,
  kidFromJwk,
  protocolCompatible,
  registry,
  signCommand,
  signPayload,
  verifyAndParseCommand,
  type CommandPayload,
  type KeyRecord,
} from "../src/index.ts";

const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const CONTROLLER = "22222222-2222-4222-8222-222222222222";
const PC = "33333333-3333-4333-8333-333333333333";
const base = { accountId: ACCOUNT, controllerId: CONTROLLER, targetPcId: PC };
const TAB = { browser_instance_id: "abcdefgh", tab_id: 5, tab_token: "AAAAAAAAAAAAAAAAAAAAAA" };

async function controller() {
  const kp = await generateControllerKeyPair();
  const jwk = await exportPublicJwk(kp.publicKey);
  const kid = await kidFromJwk(jwk);
  const record: KeyRecord = { controllerId: CONTROLLER, accountId: ACCOUNT, jwk };
  return { kp, resolve: (k: string) => (k === kid ? record : null) };
}

async function rejection(p: Promise<unknown>): Promise<ProtocolError> {
  try {
    await p;
  } catch (e) {
    if (e instanceof ProtocolError) return e;
    throw e;
  }
  throw new Error("accepted");
}

describe("command version stamping", () => {
  it("stamps each command with its action's since, never the library's newest version", () => {
    expect(PROTOCOL_VERSION).toBe("1.2");
    expect(buildCommandPayload({ ...base, action: "app.list" }).protocol_version).toBe("1.0");
    expect(buildCommandPayload({ ...base, action: "youtube.set_paused", params: { paused: true }, target: TAB }).protocol_version).toBe("1.0");
    expect(buildCommandPayload({ ...base, action: "input.session_start" }).protocol_version).toBe("1.1");
    expect(buildCommandPayload({ ...base, action: "app.maximize", target: { app_id: "notepad" } }).protocol_version).toBe("1.2");
    expect(buildCommandPayload({ ...base, action: "youtube.search", params: { query: "lofi" }, target: { browser_instance_id: "abcdefgh" } }).protocol_version).toBe("1.2");
    // a 1.1 PC accepts every action it knows (the MINOR rule on the agent side), and refuses only the new ones
    for (const [name, spec] of registry.actions()) {
      expect([name, protocolCompatible(spec.since, ["1.1"])]).toEqual([name, spec.since !== "1.2"]);
    }
  });

  it("stamps input batches with 1.1 and confirmations with the confirmed command's version", async () => {
    expect(INPUT_BATCH_PROTOCOL_VERSION).toBe(registry.get("input.session_start").since);
    expect(buildInputBatchPayload({ ...base, inputSessionId: "AAAAAAAAAAAAAAAAAAAAAA", seq: 1, events: [] }).protocol_version).toBe("1.1");
    const confirm = (commandProtocolVersion: string) =>
      buildConfirmationPayload({ commandId: "6f1c2d3e-4a5b-4c6d-8e7f-901234567890", challengeId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", challengeText: "{}", controllerId: CONTROLLER, targetPcId: PC, decision: "approve", commandProtocolVersion });
    const command = buildCommandPayload({ ...base, action: "power.sleep" });
    expect((await confirm(command.protocol_version)).protocol_version).toBe("1.0");
    expect((await confirm("1.2")).protocol_version).toBe("1.2");
    for (const bad of ["1.3", "2.0", "x", ""]) expect((await rejection(confirm(bad))).code).toBe("PROTOCOL_INCOMPATIBLE");
  });

  it("verifies stamps from since up to its own version and refuses anything below since", async () => {
    const { kp, resolve } = await controller();
    const sign = async (payload: CommandPayload) => signPayload(kp.privateKey, kp.publicKey, dumpsCompact(payload));
    const maximize = buildCommandPayload({ ...base, action: "app.maximize", target: { app_id: "notepad" } });
    expect((await verifyAndParseCommand(await signCommand(kp.privateKey, kp.publicKey, maximize), resolve)).spec.name).toBe("app.maximize");
    for (const stamp of ["1.1", "1.0"]) {
      const err = await rejection(verifyAndParseCommand(await sign({ ...maximize, protocol_version: stamp }), resolve));
      expect([err.code, err.detail]).toEqual(["PROTOCOL_INCOMPATIBLE", { peer: stamp, required: "1.2" }]);
    }
    expect((await rejection(verifyAndParseCommand(await sign({ ...maximize, protocol_version: "1.3" }), resolve))).code).toBe("PROTOCOL_INCOMPATIBLE");
    const ping = buildCommandPayload({ ...base, action: "system.ping" });
    for (const stamp of ["1.0", "1.1", "1.2"]) {
      expect((await verifyAndParseCommand(await sign({ ...ping, protocol_version: stamp }), resolve)).spec.name).toBe("system.ping");
    }
    const start = buildCommandPayload({ ...base, action: "input.session_start" });
    expect((await rejection(verifyAndParseCommand(await sign({ ...start, protocol_version: "1.0" }), resolve))).code).toBe("PROTOCOL_INCOMPATIBLE");
  });
});

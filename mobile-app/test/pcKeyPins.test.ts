/**
 * PC key pinning (protocol 1.4 rules.pc_key_pinning): a valid MAC pins the PC's key; a wrong MAC, a wrong kid, a missing
 * `pc_key` or a lost code leaves the PC unpinned (and removes any earlier pin for it); the cross-language fixture
 * vector verifies; "Forget this installation" deletes the pins with the key; a pin is never updated from anything but
 * a verified pairing.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { pcKeyMac, type rest } from "@dome/protocol";

import { deleteInstallationKey, getExistingKeyPair, getOrCreateKeyPair, listPins, readPin, writePin } from "../src/lib/controllerKey.ts";
import { clearLogs, recentLogs } from "../src/lib/log.ts";
import { allPins, getPin, pinMatches, removePin, verifyAndPin } from "../src/lib/pcKeyPins.ts";
import { FakePcKey } from "./helpers/fakePeer.ts";
import { freshStorage, PC, TS } from "./helpers/harness.ts";

const PAIRING_ID = "66666666-6666-4666-8666-666666666666";
const CONTROLLER_KID = "SuoPiWQtA7FeESmVk-Yk4r0ucbmt81cCcsFjGdkxXoU";
const CODE = "7Q3K9-M2XVB-5HTC8-D4RNW";

function approved(pcKey: rest.PairingStatusResponse["pc_key"] | undefined, pcId = PC): rest.PairingStatusResponse {
  return { pairing_id: PAIRING_ID, state: "approved", pc_id: pcId, pc_name: "Office PC", expires_at: TS, controller_id: "22222222-2222-4222-8222-222222222222", granted_capabilities: ["status"], ...(pcKey ? { pc_key: pcKey } : {}) };
}

beforeEach(() => {
  freshStorage();
  clearLogs();
});

describe("verifyAndPin", () => {
  it("pins the PC key when the MAC verifies with the code the phone still holds, and never logs the code", async () => {
    const pc = await FakePcKey.create();
    const mac = await pcKeyMac(CODE, PAIRING_ID, PC, CONTROLLER_KID, pc.kid);
    const outcome = await verifyAndPin({ code: CODE, status: approved({ public_jwk: pc.jwk, mac }), controllerKid: CONTROLLER_KID, now: new Date(TS) });
    expect(outcome).toEqual({ pinned: true, pin: { pc_id: PC, pc_kid: pc.kid, pc_public_jwk: pc.jwk, pinned_at: TS } });
    expect(await getPin(PC)).toEqual({ pc_id: PC, pc_kid: pc.kid, pc_public_jwk: pc.jwk, pinned_at: TS });
    expect(pinMatches(await getPin(PC), pc.kid)).toBe(true);
    expect(pinMatches(await getPin(PC), "A".repeat(43))).toBe(false);
    expect(JSON.stringify(recentLogs())).not.toMatch(/7Q3K9|D4RNW|7Q3K9M2XVB5HTC8D4RNW/);
  });

  it("a wrong MAC, a MAC for another PC key, a missing pc_key or a lost code leaves the PC unpinned, and an earlier pin for it is removed", async () => {
    const pc = await FakePcKey.create();
    const other = await FakePcKey.create();
    await writePin({ pc_id: PC, pc_kid: other.kid, pc_public_jwk: other.jwk, pinned_at: TS }); // a pin from an earlier pairing of this PC
    const mac = await pcKeyMac(CODE, PAIRING_ID, PC, CONTROLLER_KID, pc.kid);
    expect(await verifyAndPin({ code: CODE, status: approved({ public_jwk: other.jwk, mac }), controllerKid: CONTROLLER_KID })).toEqual({ pinned: false, reason: "mac_invalid" });
    expect(await getPin(PC)).toBeNull(); // the relay served a key the MAC does not cover: nothing for this PC is trusted now
    expect(await verifyAndPin({ code: CODE, status: approved({ public_jwk: pc.jwk, mac: "B".repeat(43) }), controllerKid: CONTROLLER_KID })).toEqual({ pinned: false, reason: "mac_invalid" });
    expect(await verifyAndPin({ code: CODE, status: approved({ public_jwk: pc.jwk, mac }), controllerKid: "A".repeat(43) })).toEqual({ pinned: false, reason: "mac_invalid" });
    expect(await verifyAndPin({ code: "ABCDE-FGHJK-MNPQR-STVWX", status: approved({ public_jwk: pc.jwk, mac }), controllerKid: CONTROLLER_KID })).toEqual({ pinned: false, reason: "mac_invalid" });
    expect(await verifyAndPin({ code: CODE, status: approved(undefined), controllerKid: CONTROLLER_KID })).toEqual({ pinned: false, reason: "no_pc_key" }); // an agent older than 1.4
    expect(await verifyAndPin({ code: null, status: approved({ public_jwk: pc.jwk, mac }), controllerKid: CONTROLLER_KID })).toEqual({ pinned: false, reason: "no_code" });
    expect(await verifyAndPin({ code: CODE, status: approved({ public_jwk: { kty: "EC", crv: "P-256", x: "not base64url!", y: "" }, mac }), controllerKid: CONTROLLER_KID })).toEqual({ pinned: false, reason: "jwk_invalid" });
    expect(await getPin(PC)).toBeNull();
    expect(await allPins()).toEqual([]);
  });

  it("verifies the cross-language fixture vector (shared/protocol/fixtures/es256-typescript.json → screen.pc_key_mac)", async () => {
    const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { kid: string; screen: { pc_public_jwk: rest.PairingStatusResponse["pc_key"] extends infer T ? (T extends { public_jwk: infer J } ? J : never) : never; pc_kid: string; pc_id: string; pc_key_mac: string } };
    const status: rest.PairingStatusResponse = { ...approved({ public_jwk: fixture.screen.pc_public_jwk, mac: fixture.screen.pc_key_mac }, fixture.screen.pc_id), pairing_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" };
    const outcome = await verifyAndPin({ code: CODE, status, controllerKid: fixture.kid });
    expect(outcome.pinned).toBe(true);
    expect((await getPin(fixture.screen.pc_id))?.pc_kid).toBe(fixture.screen.pc_kid);
  });

  it("a re-pairing of the same PC replaces its pin; removePin forgets one PC; Forget this installation deletes every pin with the key", async () => {
    const first = await FakePcKey.create();
    const second = await FakePcKey.create();
    await verifyAndPin({ code: CODE, status: approved({ public_jwk: first.jwk, mac: await pcKeyMac(CODE, PAIRING_ID, PC, CONTROLLER_KID, first.kid) }), controllerKid: CONTROLLER_KID });
    await verifyAndPin({ code: CODE, status: approved({ public_jwk: second.jwk, mac: await pcKeyMac(CODE, PAIRING_ID, PC, CONTROLLER_KID, second.kid) }), controllerKid: CONTROLLER_KID });
    expect((await readPin(PC))?.pc_kid).toBe(second.kid);
    const otherPc = "55555555-5555-4555-8555-555555555555";
    await verifyAndPin({ code: CODE, status: approved({ public_jwk: first.jwk, mac: await pcKeyMac(CODE, PAIRING_ID, otherPc, CONTROLLER_KID, first.kid) }, otherPc), controllerKid: CONTROLLER_KID });
    expect((await listPins()).map((p) => p.pc_id).sort()).toEqual([PC, otherPc].sort());
    await removePin(otherPc);
    expect(await getPin(otherPc)).toBeNull();
    await getOrCreateKeyPair();
    await deleteInstallationKey();
    expect(await listPins()).toEqual([]);
    expect(await getExistingKeyPair()).toBeNull();
  });
});

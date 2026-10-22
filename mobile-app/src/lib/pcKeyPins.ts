/**
 * PC key pinning (protocol 1.4, version.json rules.pc_key_pinning; docs/design/screen-view.md §4.1).
 *
 * At pairing the PC still holds the pairing code and sends `pairing_decision.pc_key_mac`, an HMAC over its own
 * identity key keyed by that code. Once the pairing is approved, `GET /v1/pairing/{id}` returns `pc_key{public_jwk,
 * mac}`. This module verifies the MAC with the code the phone still holds (constant time, WebCrypto HMAC verify) and
 * only then stores `{pc_id, pc_kid, pc_public_jwk, pinned_at}` next to the controller key. The pin is what the Screen
 * view page checks every PC-signed answer against: no pin, no picture; a mismatching key is a hard stop. A pin is
 * never written from a relay frame, only from a verified pairing, and a re-pairing of the same PC replaces it.
 *
 * The code never leaves `verifyAndPin`'s arguments: it is not logged (log.ts redacts `pairing_code` anyway), not
 * stored and not derived into anything but the HMAC check.
 */
import { assertEcPublicJwk, kidFromJwk, ProtocolError, verifyPcKeyMac, type rest } from "@dome/protocol";

import { deletePin, listPins, readPin, writePin, type PcKeyPin } from "./controllerKey.ts";
import { errorSummary, log } from "./log.ts";

export type { PcKeyPin };

/** Why a pairing ended without a pin (every reason is final for that pairing: the Screen tile says "pair again"). */
export type UnpinnedReason = "no_code" | "no_pc_key" | "jwk_invalid" | "mac_invalid" | "storage";

export type PinOutcome = { pinned: true; pin: PcKeyPin } | { pinned: false; reason: UnpinnedReason };

export interface VerifyAndPinInput {
  /** The normalised pairing code the page kept through the `waiting` step; null when it was already dropped. */
  code: string | null;
  /** The `approved` status body of the poll. */
  status: rest.PairingStatusResponse;
  /** This phone's controller kid (the MAC covers it). */
  controllerKid: string;
  now?: Date;
}

/**
 * Verify `pc_key.mac` and write the pin. Never throws for a verification failure: the outcome says why, and the caller
 * shows the honest state ("pair again" on the Screen tile). Only a storage failure after a VALID MAC is reported as
 * `storage`; an invalid MAC never touches storage, and any pin a previous pairing left for this PC is removed, because
 * the relay just proved it cannot be trusted for the key it now serves.
 */
export async function verifyAndPin(input: VerifyAndPinInput): Promise<PinOutcome> {
  const { status } = input;
  if (!input.code) return { pinned: false, reason: "no_code" };
  if (!status.pc_key) return { pinned: false, reason: "no_pc_key" };
  let pcKid: string;
  try {
    assertEcPublicJwk(status.pc_key.public_jwk);
    pcKid = await kidFromJwk(status.pc_key.public_jwk);
  } catch (e) {
    log.warn("pc_key_pin.jwk_invalid", errorSummary(e));
    return { pinned: false, reason: "jwk_invalid" };
  }
  let ok = false;
  try {
    ok = await verifyPcKeyMac(input.code, status.pairing_id, status.pc_id, input.controllerKid, pcKid, status.pc_key.mac);
  } catch (e) {
    log.warn("pc_key_pin.verify_failed", errorSummary(e));
    ok = false;
  }
  if (!ok) {
    log.warn("pc_key_pin.mac_invalid", { pc_id: status.pc_id });
    try {
      await deletePin(status.pc_id);
    } catch {
      /* nothing to remove, or storage unavailable: either way nothing is pinned */
    }
    return { pinned: false, reason: "mac_invalid" };
  }
  const pin: PcKeyPin = { pc_id: status.pc_id, pc_kid: pcKid, pc_public_jwk: { kty: "EC", crv: "P-256", x: status.pc_key.public_jwk.x, y: status.pc_key.public_jwk.y }, pinned_at: (input.now ?? new Date()).toISOString() };
  try {
    await writePin(pin);
  } catch (e) {
    log.warn("pc_key_pin.store_failed", errorSummary(e));
    return { pinned: false, reason: "storage" };
  }
  log.info("pc_key_pin.written", { pc_id: pin.pc_id });
  return { pinned: true, pin };
}

/** The pin for `pcId`, or null (unpinned: the phone left before approval, or the PC's agent predates 1.4). */
export async function getPin(pcId: string): Promise<PcKeyPin | null> {
  try {
    return await readPin(pcId);
  } catch (e) {
    log.warn("pc_key_pin.read_failed", errorSummary(e));
    return null;
  }
}

export async function allPins(): Promise<PcKeyPin[]> {
  try {
    return await listPins();
  } catch {
    return [];
  }
}

/** Devices → unlink / forget a PC: its pin goes with it. */
export async function removePin(pcId: string): Promise<void> {
  try {
    await deletePin(pcId);
  } catch (e) {
    log.warn("pc_key_pin.delete_failed", errorSummary(e));
  }
}

/** True when `kid` is the pinned key for `pcId` (the hard-stop check before any answer is trusted). */
export function pinMatches(pin: PcKeyPin | null, kid: string): boolean {
  return pin !== null && pin.pc_kid === kid;
}

// ---- the pairing code between the claim and the approval (§4.1, WP1.7) ------------------------------------------------

let heldCodes = 0;

/**
 * The normalised pairing code, held by the pairing page from the claim until the status poll returns `approved` with
 * `pc_key` (the MAC is verified with it, then it is dropped), or until the pairing fails, expires, is declined or
 * cancelled, or the page unmounts. It is the only thing that can tell a PC key the PC vouched for from one the relay
 * made up, so it is kept exactly that long and no longer: never in the URL, storage, logs or any other object.
 *
 * "Zeroing" is dropping the one reference: JavaScript strings are immutable and cannot be overwritten in place, so the
 * text stays in memory until the garbage collector reclaims it (KNOWN_ISSUES). `heldPairingCodes()` counts the holders
 * that still hold a code, which is how the page test proves the code is gone after done / failed / expired / unmount.
 */
export class HeldPairingCode {
  private code: string | null;

  constructor(code: string) {
    this.code = code;
    heldCodes += 1;
  }

  get value(): string | null {
    return this.code;
  }

  get held(): boolean {
    return this.code !== null;
  }

  clear(): void {
    if (this.code === null) return;
    this.code = null;
    heldCodes -= 1;
  }
}

/** How many pairing codes are still held anywhere in this app (0 except while a pairing waits for approval). */
export function heldPairingCodes(): number {
  return heldCodes;
}

export { ProtocolError };

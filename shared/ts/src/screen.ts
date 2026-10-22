/**
 * Screen view (protocol 1.4): the PC-signed answer, the strict SDP helper and the data-channel vocabulary.
 *
 * The phone's offer travels inside the signed `screen.session_start` command (an ordinary command built with
 * `buildCommandPayload`). The PC answers with `screen_answer_payload` signed by its identity key; the phone verifies it
 * with `verifyScreenAnswer` against the PC key pinned at pairing (rules.pc_key_pinning). Nothing here touches media:
 * SDP text is validated, hashed and compared, never logged or stored by this module. Byte-identical twin of
 * shared/python/dome_protocol/screen.py, covered by the cross-language fixtures.
 */
import { sha256B64url } from "./digest.ts";
import { ProtocolError } from "./errors.ts";
import { kidFromJwk, type EcPublicJwk } from "./keys.ts";
import { LIMITS, PROTOCOL_VERSION, schemas, type ScreenDcDirection } from "./registry.ts";
import { signPayload, verifyEnvelope, type Envelope } from "./signing.ts";
import { dumpsCompact, loadsStrict, utf8ByteLength, type JsonValue } from "./strictJson.ts";
import { checkCommandWindow, formatRfc3339 } from "./time.ts";
import { protocolCompatible } from "./versions.ts";

export const SCREEN_ANSWER_TYPE = "screen_answer";
export const SCREEN_DATA_CHANNEL_LABEL = "dome-screen-v1";
/** actions.json screen.session_start.params.offer_sdp and screen_answer_payload.answer_sdp share this pattern. */
export const SCREEN_SDP_PATTERN = /^[\x20-\x7E\r\n]*$/;
const FINGERPRINT_RE = /^a=fingerprint:sha-256[ \t]+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){31})[ \t]*$/;

/**
 * The strict SDP shape both devices demand before parsing anything (rules.screen_signaling (4)): non-empty, at most
 * screen_sdp_max_bytes bytes, printable ASCII plus CRLF only, every line break a real CRLF pair. A bare LF, a bare CR, a tab,
 * a NUL or a non-ASCII character fails. The agent's m-line and fingerprint rules are its own `sdp_check`.
 */
export function screenSdpAcceptable(text: unknown, maxBytes: number = LIMITS.screen_sdp_max_bytes): boolean {
  if (typeof text !== "string" || text.length === 0) return false;
  if (utf8ByteLength(text) > maxBytes) return false;
  if (!SCREEN_SDP_PATTERN.test(text)) return false;
  const count = (needle: string): number => text.split(needle).length - 1;
  const crlf = count("\r\n");
  return crlf === count("\r") && crlf === count("\n");
}

/** Every `a=fingerprint:sha-256` value in the SDP, upper-cased, in order (the phone requires them all equal). */
export function sdpFingerprints(sdp: string): string[] {
  const out: string[] = [];
  for (const line of sdp.split("\r\n")) {
    const m = FINGERPRINT_RE.exec(line);
    if (m?.[1]) out.push(m[1].toUpperCase());
  }
  return out;
}

/** base64url SHA-256 of the exact offer bytes, as the answer names them. */
export async function offerDigest(offerSdp: string): Promise<string> {
  return sha256B64url(offerSdp);
}

export interface ScreenAnswerPayload {
  type: "screen_answer";
  protocol_version: string;
  account_id: string;
  pc_id: string;
  controller_id: string;
  command_id: string;
  screen_session_id: string;
  offer_digest: string;
  answer_sdp: string;
  issued_at: string;
  expires_at: string;
}

export interface BuildScreenAnswerInput {
  accountId: string;
  pcId: string;
  controllerId: string;
  commandId: string;
  screenSessionId: string;
  offerSdp: string;
  answerSdp: string;
  now?: Date;
}

/**
 * The payload the PC signs with its identity key to answer one `screen.session_start` (rules.screen_signaling (5)). The PC
 * is Python; this builder exists for the cross-language fixtures and for tests of the phone's verifier. Lifetime =
 * screen_answer_lifetime_seconds.
 */
export async function buildScreenAnswerPayload(input: BuildScreenAnswerInput): Promise<ScreenAnswerPayload> {
  if (!screenSdpAcceptable(input.answerSdp)) throw new ProtocolError("MALFORMED_MESSAGE", "answer_sdp is not an acceptable SDP");
  const now = input.now ?? new Date();
  return {
    type: SCREEN_ANSWER_TYPE,
    protocol_version: PROTOCOL_VERSION,
    account_id: input.accountId,
    pc_id: input.pcId,
    controller_id: input.controllerId,
    command_id: input.commandId,
    screen_session_id: input.screenSessionId,
    offer_digest: await offerDigest(input.offerSdp),
    answer_sdp: input.answerSdp,
    issued_at: formatRfc3339(now),
    expires_at: formatRfc3339(new Date(now.getTime() + LIMITS.screen_answer_lifetime_seconds * 1000)),
  };
}

export async function signScreenAnswer(privateKey: CryptoKey, publicKey: CryptoKey, payload: ScreenAnswerPayload): Promise<Envelope> {
  schemas.validateScreenAnswerPayload(payload);
  return signPayload(privateKey, publicKey, dumpsCompact(payload as unknown as JsonValue));
}

export interface ScreenAnswerExpectation {
  /** The PC key pinned at pairing for this pc_id (rules.pc_key_pinning). Without a pin there is nothing to verify against. */
  pinnedJwk: EcPublicJwk;
  accountId: string;
  controllerId: string;
  pcId: string;
  commandId: string;
  /** The exact offer_sdp string the phone signed into screen.session_start. */
  offerSdp: string;
  now?: Date;
}

export interface VerifiedScreenAnswer {
  envelope: Envelope;
  payload: ScreenAnswerPayload;
  pcKid: string;
  /** The single DTLS fingerprint of the answer, upper-cased. */
  fingerprint: string;
}

function invalid(reason: string, cause?: string): ProtocolError {
  return new ProtocolError("SCREEN_ANSWER_INVALID", `screen answer rejected: ${reason}`, false, cause ? { reason, cause } : { reason });
}

/**
 * The phone's acceptance test for a `screen_answer` frame (rules.screen_signaling (6)). Accepts only if the envelope's kid
 * is the pinned PC key, the signature verifies with it, the payload names this account, controller, PC and command,
 * offer_digest matches the offer the phone sent, the window is fresh (lifetime <= screen_answer_lifetime_seconds, skew
 * max_clock_skew_seconds) and every a=fingerprint:sha-256 in the answer is identical. Everything else is
 * SCREEN_ANSWER_INVALID (detail.reason names the check, never the SDP), except a stamp this library does not speak, which is
 * PROTOCOL_INCOMPATIBLE so the phone can say "update". There is no "continue anyway".
 */
export async function verifyScreenAnswer(raw: unknown, expected: ScreenAnswerExpectation): Promise<VerifiedScreenAnswer> {
  let pinnedKid: string;
  try {
    pinnedKid = await kidFromJwk(expected.pinnedJwk);
  } catch (e) {
    throw invalid("pinned key unusable", e instanceof ProtocolError ? e.code : undefined);
  }
  let envelope: Envelope;
  let payload: Record<string, JsonValue>;
  try {
    ({ envelope, payload } = await verifyEnvelope(raw, (kid) => (kid === pinnedKid ? expected.pinnedJwk : null), {
      maxPayloadBytes: LIMITS.max_payload_bytes,
      maxDepth: LIMITS.max_json_depth,
    }));
  } catch (e) {
    if (!(e instanceof ProtocolError)) throw e;
    // UNKNOWN_KEY here means the answer was signed by a key other than the pinned one: the hard stop.
    throw invalid(e.code === "UNKNOWN_KEY" ? "kid is not the pinned PC key" : "envelope or signature", e.code);
  }
  const stamp = payload.protocol_version;
  if (typeof stamp === "string" && !protocolCompatible(stamp, [PROTOCOL_VERSION])) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version", false, { peer: stamp, supported: [PROTOCOL_VERSION] });
  }
  try {
    schemas.validateScreenAnswerPayload(payload);
  } catch (e) {
    throw invalid("payload shape", e instanceof ProtocolError ? e.code : undefined);
  }
  const answer = payload as unknown as ScreenAnswerPayload;
  const ours: Array<[keyof ScreenAnswerPayload, string]> = [
    ["account_id", expected.accountId],
    ["controller_id", expected.controllerId],
    ["pc_id", expected.pcId],
    ["command_id", expected.commandId],
  ];
  for (const [field, value] of ours) if (answer[field] !== value) throw invalid(`${field} is not ours`);
  if (answer.offer_digest !== (await offerDigest(expected.offerSdp))) throw invalid("offer_digest does not match the offer we sent");
  try {
    checkCommandWindow(answer.issued_at, answer.expires_at, {
      ...(expected.now ? { now: expected.now } : {}),
      maxLifetimeSeconds: LIMITS.screen_answer_lifetime_seconds + LIMITS.max_clock_skew_seconds,
      maxSkewSeconds: LIMITS.max_clock_skew_seconds,
    });
  } catch (e) {
    throw invalid("window", e instanceof ProtocolError ? e.code : undefined);
  }
  if (!screenSdpAcceptable(answer.answer_sdp)) throw invalid("answer_sdp is not an acceptable SDP");
  const prints = sdpFingerprints(answer.answer_sdp);
  if (prints.length === 0 || prints.some((fp) => fp !== prints[0])) throw invalid("fingerprints missing or not identical");
  return { envelope, payload: answer, pcKid: pinnedKid, fingerprint: prints[0]! };
}

export type ScreenDcPhoneToPc = { type: "hb" } | { type: "keyframe" } | { type: "bye" } | { type: "audio"; on: boolean };
export type ScreenDcPcToPhone =
  | { type: "geometry"; gen: number; surface_index: number; width: number; height: number; rotation: 0 | 90 | 180 | 270; reserved: Array<{ x: number; y: number; w: number; h: number }> }
  | { type: "state"; state: "sending" | "paused"; reason?: "secure_desktop" | "indicator"; encoder?: "h264" | "vp8"; data_saver?: boolean }
  | { type: "allowance"; unlimited: boolean; remaining_seconds: number | null; resets_at: string | null }
  | { type: "restricted"; restricted: boolean; reason?: "elevated" | "secure_desktop" | "locked" | "permission_ui" };
export type ScreenDcMessage = ScreenDcPhoneToPc | ScreenDcPcToPhone;

/**
 * Strict-parse one `dome-screen-v1` data-channel message and validate it for the receiving side (rules.screen_content): at
 * most screen_dc_message_max_bytes, a JSON object, one of the closed vocabulary for `direction` (`pc_to_phone` on the
 * phone). Anything else throws and the receiver drops it; no message here can inject input or run anything.
 */
export function parseScreenDcMessage(text: string, direction: "pc_to_phone"): ScreenDcPcToPhone;
export function parseScreenDcMessage(text: string, direction: "phone_to_pc"): ScreenDcPhoneToPc;
export function parseScreenDcMessage(text: string, direction: ScreenDcDirection): ScreenDcMessage {
  if (typeof text !== "string") throw new ProtocolError("MALFORMED_MESSAGE", "data-channel message must be text");
  const parsed = loadsStrict(text, { maxBytes: LIMITS.screen_dc_message_max_bytes, maxDepth: LIMITS.max_json_depth, requireObject: true });
  schemas.validateScreenDcMessage(direction, parsed);
  return parsed as unknown as ScreenDcMessage;
}

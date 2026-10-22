/**
 * Building and verifying command / confirmation payloads.
 *
 * The phone *builds and signs*; the relay and PC *verify*. Both halves live here so the
 * TypeScript and Python implementations can be checked against the same fixtures. Verification
 * takes a *key record* resolver that binds the signing key to its controller, so a controller can
 * never sign a payload naming another controller's id (and thereby another grant).
 */
import { challengeDigest, commandDigest, randomNonce, randomUuid } from "./digest.ts";
import { ProtocolError } from "./errors.ts";
import type { EcPublicJwk } from "./keys.ts";
import { LIMITS, PROTOCOL_VERSION, registry, schemas, type ActionSpec } from "./registry.ts";
import { signPayload, verifyEnvelope, type Envelope } from "./signing.ts";
import { dumpsCompact, type JsonValue } from "./strictJson.ts";
import { checkCommandWindow, formatRfc3339 } from "./time.ts";
import { INPUT_BATCH_PROTOCOL_VERSION, inputBatchProtocolVersion, parseVersion, protocolCompatible, versionBelow } from "./versions.ts";

export interface KeyRecord {
  controllerId: string;
  accountId: string;
  jwk: EcPublicJwk;
  capabilities?: readonly string[];
}

export type KeyResolver = (kid: string) => KeyRecord | null | undefined | Promise<KeyRecord | null | undefined>;

export interface CommandPayload {
  type: "command";
  protocol_version: string;
  command_id: string;
  account_id: string;
  controller_id: string;
  target_pc_id: string;
  action: string;
  params: Record<string, JsonValue>;
  target: Record<string, JsonValue> | null;
  issued_at: string;
  expires_at: string;
  nonce: string;
}

export interface ConfirmationPayload {
  type: "confirmation";
  protocol_version: string;
  command_id: string;
  challenge_id: string;
  challenge_digest: string;
  controller_id: string;
  target_pc_id: string;
  decision: "approve" | "decline";
  issued_at: string;
  expires_at: string;
  nonce: string;
}

export interface BuildCommandInput {
  accountId: string;
  controllerId: string;
  targetPcId: string;
  action: string;
  params?: Record<string, JsonValue>;
  target?: Record<string, JsonValue> | null;
  lifetimeSeconds?: number;
  now?: Date;
  commandId?: string;
}

/** The lowest protocol_version a command for `action` may carry: the action's `since` (throws UNKNOWN_ACTION). */
export function minVersion(action: string): string {
  return registry.get(action).since;
}

/**
 * Build a command payload, validating params/target locally first so bad input never gets signed. The payload is
 * stamped with the action's `since`, not this library's newest version, so a PC whose agent predates this phone keeps
 * accepting every action it knows (rules.command_version_stamping).
 */
export function buildCommandPayload(input: BuildCommandInput): CommandPayload {
  const spec = registry.get(input.action);
  const params = registry.validateParams(input.action, input.params ?? {}) as Record<string, JsonValue>;
  const target = registry.validateTarget(input.action, input.target ?? null) as Record<string, JsonValue> | null;
  // Disruptive actions wait for a 60 s confirmation; give them a longer default so the two
  // windows cannot cross (version.json rules.command_window_and_confirmation).
  const defaultLifetime = spec.confirmation === "challenge" ? LIMITS.default_confirmed_command_lifetime_seconds : LIMITS.default_command_lifetime_seconds;
  const lifetime = input.lifetimeSeconds ?? defaultLifetime;
  if (!Number.isInteger(lifetime) || lifetime <= 0 || lifetime > LIMITS.max_command_lifetime_seconds) {
    throw new ProtocolError("INVALID_PARAMETERS", "invalid command lifetime");
  }
  const now = input.now ?? new Date();
  return {
    type: "command",
    protocol_version: spec.since,
    command_id: input.commandId ?? randomUuid(),
    account_id: input.accountId,
    controller_id: input.controllerId,
    target_pc_id: input.targetPcId,
    action: spec.name,
    params,
    target,
    issued_at: formatRfc3339(now),
    expires_at: formatRfc3339(new Date(now.getTime() + lifetime * 1000)),
    nonce: randomNonce(),
  };
}

export async function signCommand(privateKey: CryptoKey, publicKey: CryptoKey, payload: CommandPayload): Promise<Envelope> {
  schemas.validateCommandPayload(payload);
  return signPayload(privateKey, publicKey, dumpsCompact(payload));
}

export interface BuildConfirmationInput {
  commandId: string;
  challengeId: string;
  /** The challenge_text string exactly as received in confirmation_required. */
  challengeText: string;
  controllerId: string;
  targetPcId: string;
  decision: "approve" | "decline";
  /** protocol_version of the command being confirmed: the confirmation carries the same stamp (rules.command_version_stamping). */
  commandProtocolVersion: string;
  now?: Date;
}

export async function buildConfirmationPayload(input: BuildConfirmationInput): Promise<ConfirmationPayload> {
  if (parseVersion(input.commandProtocolVersion) === null || !protocolCompatible(input.commandProtocolVersion, [PROTOCOL_VERSION])) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", "the confirmed command's protocol version is not supported");
  }
  const now = input.now ?? new Date();
  return {
    type: "confirmation",
    protocol_version: input.commandProtocolVersion,
    command_id: input.commandId,
    challenge_id: input.challengeId,
    challenge_digest: await challengeDigest(input.challengeText),
    controller_id: input.controllerId,
    target_pc_id: input.targetPcId,
    decision: input.decision,
    issued_at: formatRfc3339(now),
    expires_at: formatRfc3339(new Date(now.getTime() + LIMITS.confirmation_challenge_lifetime_seconds * 1000)),
    nonce: randomNonce(),
  };
}

export async function signConfirmation(privateKey: CryptoKey, publicKey: CryptoKey, payload: ConfirmationPayload): Promise<Envelope> {
  schemas.validateConfirmationPayload(payload);
  return signPayload(privateKey, publicKey, dumpsCompact(payload));
}

export interface HelloProofPayload {
  type: "hello_proof";
  protocol_version: string;
  kid: string;
  account_id: string;
  issued_at: string;
  expires_at: string;
  nonce: string;
}

export interface BuildHelloProofInput {
  kid: string;
  accountId: string;
  now?: Date;
}

/**
 * The payload a controller signs to prove it holds its key when opening the relay socket
 * (`rules.controller_socket_identity`): a bare kid is public to the whole account, so the relay binds
 * the socket to the paired controller only after verifying this. Lifetime = 60 s, single-use nonce.
 */
export function buildHelloProofPayload(input: BuildHelloProofInput): HelloProofPayload {
  const now = input.now ?? new Date();
  return {
    type: "hello_proof",
    protocol_version: PROTOCOL_VERSION,
    kid: input.kid,
    account_id: input.accountId,
    issued_at: formatRfc3339(now),
    expires_at: formatRfc3339(new Date(now.getTime() + LIMITS.confirmation_challenge_lifetime_seconds * 1000)),
    nonce: randomNonce(),
  };
}

export async function signHelloProof(privateKey: CryptoKey, publicKey: CryptoKey, payload: HelloProofPayload): Promise<Envelope> {
  schemas.validateHelloProofPayload(payload);
  return signPayload(privateKey, publicKey, dumpsCompact(payload));
}

export type InputEvent =
  | { type: "pointer_move"; dx: number; dy: number }
  | { type: "pointer_button"; button: "left" | "right" | "middle"; action: "down" | "up" | "click" | "double_click" }
  | { type: "pointer_scroll"; dx: number; dy: number }
  /** Since 1.4 (Screen view): absolute position normalised to 0..65535 over the streamed surface at geometry generation `gen`. */
  | { type: "pointer_move_to"; x: number; y: number; gen: number }
  /** Since 1.4 (Screen view): wheel motion in Windows wheel units, 120 = one notch, |dx|,|dy| <= input_wheel_max. */
  | { type: "pointer_wheel"; dx: number; dy: number }
  | { type: "text"; text: string }
  | { type: "key"; key: "enter" | "tab" | "escape" | "backspace" | "delete" | "space" | "arrow_up" | "arrow_down" | "arrow_left" | "arrow_right" | "home" | "end" | "page_up" | "page_down" | "f11" }
  | { type: "shortcut"; name: "ctrl_a" | "ctrl_c" | "ctrl_v" | "ctrl_z" | "ctrl_l" };

const POINTER_EVENT_TYPES = new Set(["pointer_move", "pointer_button", "pointer_scroll", "pointer_move_to", "pointer_wheel"]);

export interface InputBatchPayload {
  type: "input_batch";
  protocol_version: string;
  account_id: string;
  controller_id: string;
  target_pc_id: string;
  input_session_id: string;
  seq: number;
  issued_at: string;
  expires_at: string;
  events: InputEvent[];
}

export interface BuildInputBatchInput {
  accountId: string;
  controllerId: string;
  targetPcId: string;
  inputSessionId: string;
  seq: number;
  events: InputEvent[];
  now?: Date;
}

/** The capabilities a grant must hold to send these events (pointer_* → pointer, pointer_move_to and pointer_wheel included; text/key/shortcut → keyboard). */
export function inputEventCapabilities(events: readonly InputEvent[]): Set<"pointer" | "keyboard"> {
  const needed = new Set<"pointer" | "keyboard">();
  for (const ev of events) needed.add(POINTER_EVENT_TYPES.has(ev.type) ? "pointer" : "keyboard");
  return needed;
}

/**
 * One batch of the manual-input stream (rules.input_sessions). The phone builds and signs it with the
 * controller key; the relay and the PC verify it. Lifetime = input_batch_lifetime_seconds (5 s); an empty
 * events array renews the session lease. Stamped with the lowest version that defines every event in it ("1.1" for the
 * touchpad events, "1.4" once a Screen view event is present), so older PCs keep accepting plain batches.
 */
export function buildInputBatchPayload(input: BuildInputBatchInput): InputBatchPayload {
  const now = input.now ?? new Date();
  return {
    type: "input_batch",
    protocol_version: inputBatchProtocolVersion(input.events),
    account_id: input.accountId,
    controller_id: input.controllerId,
    target_pc_id: input.targetPcId,
    input_session_id: input.inputSessionId,
    seq: input.seq,
    issued_at: formatRfc3339(now),
    expires_at: formatRfc3339(new Date(now.getTime() + LIMITS.input_batch_lifetime_seconds * 1000)),
    events: input.events,
  };
}

export async function signInputBatch(privateKey: CryptoKey, publicKey: CryptoKey, payload: InputBatchPayload): Promise<Envelope> {
  schemas.validateInputBatchPayload(payload);
  return signPayload(privateKey, publicKey, dumpsCompact(payload as unknown as JsonValue));
}

export interface VerifiedCommand {
  envelope: Envelope;
  payload: CommandPayload;
  key: KeyRecord;
  spec: ActionSpec;
  params: Record<string, unknown>;
  target: Record<string, unknown> | null;
  digest: string;
}

export interface VerifiedConfirmation {
  envelope: Envelope;
  payload: ConfirmationPayload;
  key: KeyRecord;
  approved: boolean;
}

async function verifyWithRecord(raw: unknown, resolveKey: KeyResolver): Promise<{ envelope: Envelope; payload: Record<string, JsonValue>; key: KeyRecord }> {
  let record: KeyRecord | null | undefined;
  const { envelope, payload } = await verifyEnvelope(
    raw,
    async (kid) => {
      record = await resolveKey(kid);
      return record?.jwk ?? null;
    },
    { maxPayloadBytes: LIMITS.max_payload_bytes, maxDepth: LIMITS.max_json_depth },
  );
  if (!record) throw new ProtocolError("UNKNOWN_KEY", "no paired key for kid");
  return { envelope, payload, key: record };
}

/**
 * The version checks that run BEFORE schema validation (rules.command_version_stamping, 1.4 check order). A payload from a
 * newer MINOR may carry fields or events this library does not know; validating its schema first would turn that into
 * MALFORMED_MESSAGE. So the stamp is read first: an unsupported version is PROTOCOL_INCOMPATIBLE {peer, supported}, a stamp
 * below `since` is PROTOCOL_INCOMPATIBLE {peer, required}. A stamp that is not a MAJOR.MINOR string falls through to the
 * schema (MALFORMED_MESSAGE).
 */
function checkStamp(payload: Record<string, JsonValue>, since: string | null): void {
  const stamp = payload.protocol_version;
  if (parseVersion(stamp) === null || typeof stamp !== "string") return;
  if (!protocolCompatible(stamp, [PROTOCOL_VERSION])) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version", false, { peer: stamp, supported: [PROTOCOL_VERSION] });
  }
  if (since !== null && versionBelow(stamp, since)) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", `stamped protocol version is below ${since}, which introduced it`, false, { peer: stamp, required: since });
  }
}

function bind(payload: Record<string, JsonValue>, key: KeyRecord): void {
  if (payload.controller_id !== key.controllerId) throw new ProtocolError("CONTROLLER_MISMATCH", "payload controller_id does not belong to the signing key");
  if ("account_id" in payload && payload.account_id !== key.accountId) throw new ProtocolError("ACCOUNT_MISMATCH", "payload account_id does not belong to the signing key");
}

export async function verifyAndParseCommand(raw: unknown, resolveKey: KeyResolver, now?: Date): Promise<VerifiedCommand> {
  const { envelope, payload, key } = await verifyWithRecord(raw, resolveKey);
  // Version first (compatibility and the action's `since`, when the action is one we know), schema second.
  const action = payload.action;
  checkStamp(payload, typeof action === "string" && registry.has(action) ? registry.get(action).since : null);
  schemas.validateCommandPayload(payload);
  const cmd = payload as unknown as CommandPayload;
  bind(payload, key);
  checkCommandWindow(cmd.issued_at, cmd.expires_at, {
    ...(now ? { now } : {}),
    maxLifetimeSeconds: LIMITS.max_command_lifetime_seconds,
    maxSkewSeconds: LIMITS.max_clock_skew_seconds,
  });
  const spec = registry.get(cmd.action);
  if (versionBelow(cmd.protocol_version, spec.since)) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", `stamped protocol version is below ${spec.since}, which introduced it`, false, { peer: cmd.protocol_version, required: spec.since });
  }
  const params = registry.validateParams(spec.name, cmd.params);
  const target = registry.validateTarget(spec.name, cmd.target);
  return { envelope, payload: cmd, key, spec, params, target, digest: await commandDigest(envelope.payload) };
}

export async function verifyAndParseConfirmation(raw: unknown, resolveKey: KeyResolver, now?: Date): Promise<VerifiedConfirmation> {
  const { envelope, payload, key } = await verifyWithRecord(raw, resolveKey);
  checkStamp(payload, null);
  schemas.validateConfirmationPayload(payload);
  const conf = payload as unknown as ConfirmationPayload;
  bind(payload, key);
  checkCommandWindow(conf.issued_at, conf.expires_at, {
    ...(now ? { now } : {}),
    maxLifetimeSeconds: LIMITS.confirmation_challenge_lifetime_seconds + LIMITS.max_clock_skew_seconds,
    maxSkewSeconds: LIMITS.max_clock_skew_seconds,
  });
  return { envelope, payload: conf, key, approved: conf.decision === "approve" };
}

export interface VerifiedInputBatch {
  envelope: Envelope;
  payload: InputBatchPayload;
  key: KeyRecord;
  /** The lowest version that defines every event in the batch (what the stamp had to be at least). */
  requiredProtocolVersion: string;
  requiredCapabilities: Set<"pointer" | "keyboard">;
}

/**
 * The input-batch half of what `verify_and_parse_input_batch` does in Python, in the same order: signature over the exact
 * bytes, protocol compatibility and the 1.1 floor BEFORE the schema (a batch from a newer MINOR with unknown events is
 * PROTOCOL_INCOMPATIBLE, not MALFORMED_MESSAGE), schema, the per-event floor (a 1.4 event stamped 1.1 is
 * PROTOCOL_INCOMPATIBLE with required 1.4), binding and the 5 s window. Session ownership, sequence and age are the caller's.
 */
export async function verifyAndParseInputBatch(raw: unknown, resolveKey: KeyResolver, now?: Date): Promise<VerifiedInputBatch> {
  const { envelope, payload, key } = await verifyWithRecord(raw, resolveKey);
  checkStamp(payload, INPUT_BATCH_PROTOCOL_VERSION);
  schemas.validateInputBatchPayload(payload);
  const batch = payload as unknown as InputBatchPayload;
  const required = inputBatchProtocolVersion(batch.events);
  if (versionBelow(batch.protocol_version, required)) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", `stamped protocol version is below ${required}, which introduced it`, false, { peer: batch.protocol_version, required });
  }
  bind(payload, key);
  checkCommandWindow(batch.issued_at, batch.expires_at, {
    ...(now ? { now } : {}),
    maxLifetimeSeconds: LIMITS.input_batch_lifetime_seconds + LIMITS.max_clock_skew_seconds,
    maxSkewSeconds: LIMITS.max_clock_skew_seconds,
  });
  return { envelope, payload: batch, key, requiredProtocolVersion: required, requiredCapabilities: inputEventCapabilities(batch.events) };
}

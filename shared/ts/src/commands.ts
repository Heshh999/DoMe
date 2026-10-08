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
import { LIMITS, PROTOCOL_VERSION, protocolCompatible, registry, schemas, type ActionSpec } from "./registry.ts";
import { signPayload, verifyEnvelope, type Envelope } from "./signing.ts";
import { dumpsCompact, type JsonValue } from "./strictJson.ts";
import { checkCommandWindow, formatRfc3339 } from "./time.ts";

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

/** Build a command payload, validating params/target locally first so bad input never gets signed. */
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
    protocol_version: PROTOCOL_VERSION,
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
  now?: Date;
}

export async function buildConfirmationPayload(input: BuildConfirmationInput): Promise<ConfirmationPayload> {
  const now = input.now ?? new Date();
  return {
    type: "confirmation",
    protocol_version: PROTOCOL_VERSION,
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

function bind(payload: Record<string, JsonValue>, key: KeyRecord): void {
  if (payload.controller_id !== key.controllerId) throw new ProtocolError("CONTROLLER_MISMATCH", "payload controller_id does not belong to the signing key");
  if ("account_id" in payload && payload.account_id !== key.accountId) throw new ProtocolError("ACCOUNT_MISMATCH", "payload account_id does not belong to the signing key");
}

export async function verifyAndParseCommand(raw: unknown, resolveKey: KeyResolver, now?: Date): Promise<VerifiedCommand> {
  const { envelope, payload, key } = await verifyWithRecord(raw, resolveKey);
  schemas.validateCommandPayload(payload);
  const cmd = payload as unknown as CommandPayload;
  if (!protocolCompatible(cmd.protocol_version, [PROTOCOL_VERSION])) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version", false, { peer: cmd.protocol_version, supported: [PROTOCOL_VERSION] });
  }
  bind(payload, key);
  checkCommandWindow(cmd.issued_at, cmd.expires_at, {
    ...(now ? { now } : {}),
    maxLifetimeSeconds: LIMITS.max_command_lifetime_seconds,
    maxSkewSeconds: LIMITS.max_clock_skew_seconds,
  });
  const spec = registry.get(cmd.action);
  const params = registry.validateParams(spec.name, cmd.params);
  const target = registry.validateTarget(spec.name, cmd.target);
  return { envelope, payload: cmd, key, spec, params, target, digest: await commandDigest(envelope.payload) };
}

export async function verifyAndParseConfirmation(raw: unknown, resolveKey: KeyResolver, now?: Date): Promise<VerifiedConfirmation> {
  const { envelope, payload, key } = await verifyWithRecord(raw, resolveKey);
  schemas.validateConfirmationPayload(payload);
  const conf = payload as unknown as ConfirmationPayload;
  if (!protocolCompatible(conf.protocol_version, [PROTOCOL_VERSION])) throw new ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version");
  bind(payload, key);
  checkCommandWindow(conf.issued_at, conf.expires_at, {
    ...(now ? { now } : {}),
    maxLifetimeSeconds: LIMITS.confirmation_challenge_lifetime_seconds + LIMITS.max_clock_skew_seconds,
    maxSkewSeconds: LIMITS.max_clock_skew_seconds,
  });
  return { envelope, payload: conf, key, approved: conf.decision === "approve" };
}

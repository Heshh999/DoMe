/**
 * Building and verifying command / confirmation payloads.
 *
 * The phone *builds and signs*; the relay and PC *verify*. Both halves live here so the
 * TypeScript and Python implementations can be checked against the same fixtures.
 */
import { randomNonce, randomUuid, challengeDigest } from "./digest.ts";
import { ProtocolError } from "./errors.ts";
import { LIMITS, PROTOCOL_VERSION, registry, schemas, protocolCompatible, type ActionSpec } from "./registry.ts";
import { signPayload, verifyEnvelope, type Envelope, type JwkResolver } from "./signing.ts";
import { dumpsCompact, type JsonValue } from "./strictJson.ts";
import { checkCommandWindow, formatRfc3339 } from "./time.ts";

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
  const lifetime = input.lifetimeSeconds ?? LIMITS.default_command_lifetime_seconds;
  if (lifetime <= 0 || lifetime > LIMITS.max_command_lifetime_seconds) throw new ProtocolError("INVALID_PARAMETERS", "invalid command lifetime");
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
  challengeText: string; // the raw challenge JSON text exactly as received
  controllerId: string;
  targetPcId: string;
  decision: "approve" | "decline";
  now?: Date;
}

export async function buildConfirmationPayload(input: BuildConfirmationInput): Promise<ConfirmationPayload> {
  return {
    type: "confirmation",
    protocol_version: PROTOCOL_VERSION,
    command_id: input.commandId,
    challenge_id: input.challengeId,
    challenge_digest: await challengeDigest(input.challengeText),
    controller_id: input.controllerId,
    target_pc_id: input.targetPcId,
    decision: input.decision,
    issued_at: formatRfc3339(input.now ?? new Date()),
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
  spec: ActionSpec;
  params: Record<string, unknown>;
  target: Record<string, unknown> | null;
}

export async function verifyAndParseCommand(raw: unknown, resolveJwk: JwkResolver, now?: Date): Promise<VerifiedCommand> {
  const { envelope, payload } = await verifyEnvelope(raw, resolveJwk, {
    maxPayloadBytes: LIMITS.max_payload_bytes,
    maxDepth: LIMITS.max_json_depth,
  });
  schemas.validateCommandPayload(payload);
  const cmd = payload as unknown as CommandPayload;
  if (!protocolCompatible(cmd.protocol_version, [PROTOCOL_VERSION])) {
    throw new ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version", false, { peer: cmd.protocol_version, supported: [PROTOCOL_VERSION] });
  }
  checkCommandWindow(cmd.issued_at, cmd.expires_at, {
    ...(now ? { now } : {}),
    maxLifetimeSeconds: LIMITS.max_command_lifetime_seconds,
    maxSkewSeconds: LIMITS.max_clock_skew_seconds,
  });
  const spec = registry.get(cmd.action);
  const params = registry.validateParams(spec.name, cmd.params);
  const target = registry.validateTarget(spec.name, cmd.target);
  return { envelope, payload: cmd, spec, params, target };
}

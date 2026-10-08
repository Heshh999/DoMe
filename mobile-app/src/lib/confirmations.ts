/**
 * Confirmation transactions (ADR-0001 D7). The challenge arrives as an opaque string; it is parsed
 * once for display (strictly), hashed verbatim into the signed confirmation, and bound to the
 * command we actually sent. The primary line shown to the customer is rendered from `action`,
 * `params` and `target` with the app's own labels; `display.detail` is untrusted secondary text.
 */
import { buildConfirmationPayload, schemas, signConfirmation, type JsonValue, type relayFrames } from "@dome/protocol";

import { describeAction, type LabelContext } from "./labels.ts";

export type Challenge = relayFrames.Challenge;

export interface ParsedChallenge {
  /** The exact string received; hashed verbatim into `challenge_digest`. */
  raw: string;
  challenge: Challenge;
}

export function parseChallenge(challengeText: string): ParsedChallenge {
  const parsed = schemas.validateChallengeText(challengeText) as unknown as Challenge;
  return { raw: challengeText, challenge: parsed };
}

export interface CommandBinding {
  commandId: string;
  pcId: string;
  action: string;
  params: Record<string, unknown>;
  target: Record<string, unknown> | null;
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  const o = value as Record<string, unknown>;
  return `{${Object.keys(o)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${stableJson(o[k])}`)
    .join(",")}}`;
}

/**
 * A challenge is only shown with an Approve button when it describes exactly the command this
 * phone sent: same command, PC, controller, action, parameters and target. Returns null when bound,
 * otherwise a short reason (the UI then offers Decline only).
 */
export function checkChallengeBinding(challenge: Challenge, record: CommandBinding, controllerId: string | null): string | null {
  if (challenge.command_id !== record.commandId) return "The confirmation is for a different command.";
  if (challenge.pc_id !== record.pcId) return "The confirmation names a different PC.";
  if (!controllerId || challenge.controller_id !== controllerId) return "The confirmation is not addressed to this phone.";
  if (challenge.action !== record.action) return "The confirmation describes a different action.";
  if (stableJson(challenge.params) !== stableJson(record.params)) return "The confirmation's settings differ from what you sent.";
  if (stableJson(challenge.target ?? null) !== stableJson(record.target ?? null)) return "The confirmation targets something else.";
  return null;
}

export interface ChallengeDescription {
  /** Rendered from action/params/target with the app's own labels — never from PC-supplied text. */
  primary: string;
  /** PC name from the device inventory matched by pc_id; falls back to a neutral label, never to the challenge's own pc_name. */
  pcName: string;
  /** Untrusted secondary text from the PC (window title etc.), rendered as plain text. */
  detail: string;
  expiresAt: string;
}

export function describeChallenge(challenge: Challenge, pcName: string | null, ctx?: LabelContext): ChallengeDescription {
  return {
    primary: describeAction(challenge.action, challenge.params as Record<string, unknown>, (challenge.target as Record<string, unknown> | null) ?? null, ctx),
    pcName: pcName ?? "your PC",
    detail: typeof challenge.display?.detail === "string" ? challenge.display.detail : "",
    expiresAt: challenge.expires_at,
  };
}

export async function buildConfirmationEnvelope(input: {
  parsed: ParsedChallenge;
  controllerId: string;
  decision: "approve" | "decline";
  keyPair: CryptoKeyPair;
  now?: Date;
}): Promise<relayFrames.ControllerConfirmation> {
  const { challenge, raw } = input.parsed;
  const payload = await buildConfirmationPayload({
    commandId: challenge.command_id,
    challengeId: challenge.challenge_id,
    challengeText: raw,
    controllerId: input.controllerId,
    targetPcId: challenge.pc_id,
    decision: input.decision,
    ...(input.now ? { now: input.now } : {}),
  });
  const envelope = await signConfirmation(input.keyPair.privateKey, input.keyPair.publicKey, payload);
  return { type: "confirmation", pc_id: challenge.pc_id, envelope };
}

export type { JsonValue };

/**
 * API-identical replacement for `@dome/protocol`'s `src/registry.ts` for the browser bundle.
 *
 * Why this exists: the shared module compiles its Ajv validators with `new Function` at import time.
 * cloud-api serves the PWA under `Content-Security-Policy: script-src 'self'` (no `unsafe-eval`), so
 * that module cannot run in production. `vite.config.ts` (plugin `domeProtocolNoEval`) resolves the
 * shared library's internal `./registry.ts` import to this file, which exposes the same exports
 * backed by validators precompiled at build time (`pnpm gen:validators`) from the SAME frozen schema
 * files. Everything else in `@dome/protocol` (strict JSON, keys, signing, digests, command building)
 * is used unchanged. `test/protocol-parity.test.ts` checks this facade against the real module.
 */
import actionsJson from "../../../shared/protocol/actions.json";
import errorsJson from "../../../shared/protocol/errors.json";
import plansJson from "../../../shared/protocol/plans.json";
import versionJson from "../../../shared/protocol/version.json";
import { ProtocolError } from "../../../shared/ts/src/errors.ts";
import { loadsStrict } from "../../../shared/ts/src/strictJson.ts";
import * as generated from "./generated/validators.js";
import type { Validator } from "./generated/validators.js";

export type Risk = "low" | "moderate" | "disruptive";
export type Confirmation = "none" | "challenge";
export type Capability = keyof typeof actionsJson.capabilities;
export type ActionName = keyof typeof actionsJson.actions;
export type ErrorCode = keyof typeof errorsJson.errors;
export type PlanId = keyof typeof plansJson.plans;

export interface ActionSpec {
  name: string;
  summary: string;
  paramsSchema: Record<string, unknown>;
  targetName: string | null;
  resultName: string;
  capability: Capability;
  risk: Risk;
  confirmation: Confirmation;
  timeoutMs: number;
  availability: readonly string[];
  verification: string;
  coalesce: string | null;
  idempotent: boolean;
  routineAllowed: boolean;
  /** Capabilities that also permit the action (rules: `capability` OR any of these). */
  alternateCapabilities: readonly Capability[];
  /** false = human-only: never offered to an AI tool catalogue or a saved routine (rules.ai_eligibility). */
  aiEligible: boolean;
}

/** True when a grant holding `capabilities` may run `spec`. */
export function capabilitySatisfied(spec: Pick<ActionSpec, "capability" | "alternateCapabilities">, capabilities: Iterable<string>): boolean {
  const held = new Set(capabilities);
  return held.has(spec.capability) || spec.alternateCapabilities.some((c) => held.has(c));
}

export const PROTOCOL_VERSION: string = versionJson.protocol_version;
export const REGISTRY_VERSION: string = actionsJson.registry_version;
export const LIMITS = versionJson.limits;
export const PLANS = plansJson;
export const ERRORS = errorsJson.errors as Record<string, { retryable: boolean; user_message: string }>;
export const CAPABILITIES = actionsJson.capabilities as Record<string, string>;
export const URI_FORMAT = /^(?:https?|wss?):\/\/[^\s/?#]+[^\s]*$/;

const validators = generated as unknown as Record<string, Validator | undefined>;

function validatorFor(group: string, name: string): Validator {
  const key = name === "" ? group : `${group}__${name.replace(/[^A-Za-z0-9_]/g, "_")}`;
  const v = validators[key];
  if (!v) throw new ProtocolError("INTERNAL", `no precompiled validator for ${group}/${name}`);
  return v;
}

function throwValidation(v: Validator, code: string, prefix = ""): never {
  const err = v.errors?.[0];
  const path = err?.instancePath?.replace(/^\//, "") || "<root>";
  throw new ProtocolError(code, `${prefix}${path}: ${err?.message ?? "invalid"}`.slice(0, 300));
}

const actionSpecs = new Map<string, ActionSpec>();
for (const [name, raw] of Object.entries(actionsJson.actions)) {
  const r = raw as Record<string, unknown>;
  const spec: ActionSpec = {
    name,
    summary: String(r.summary),
    paramsSchema: r.params as Record<string, unknown>,
    targetName: (r.target as string | null) ?? null,
    resultName: String(r.result),
    capability: r.capability as Capability,
    risk: r.risk as Risk,
    confirmation: r.confirmation as Confirmation,
    timeoutMs: Number(r.timeout_ms),
    availability: (r.availability as string[]) ?? [],
    verification: String(r.verification),
    coalesce: (r.coalesce as string | undefined) ?? null,
    idempotent: Boolean(r.idempotent),
    routineAllowed: Boolean(r.routine_allowed),
    alternateCapabilities: ((r.alternate_capabilities as string[] | undefined) ?? []) as Capability[],
    aiEligible: r.ai_eligible === undefined ? true : Boolean(r.ai_eligible),
  };
  if (spec.routineAllowed && !spec.aiEligible) throw new Error(`human-only action ${name} cannot be routine-allowed`);
  if (spec.risk === "disruptive" && spec.confirmation !== "challenge") throw new Error(`disruptive action ${name} must require a challenge`);
  validatorFor("params", name); // fail fast if the generated code is stale
  actionSpecs.set(name, spec);
}

export const registry = {
  actions(): ReadonlyMap<string, ActionSpec> {
    return actionSpecs;
  },
  get(name: string): ActionSpec {
    const spec = actionSpecs.get(name);
    if (!spec) throw new ProtocolError("UNKNOWN_ACTION", `unknown action ${JSON.stringify(name)}`);
    return spec;
  },
  has(name: string): name is ActionName {
    return actionSpecs.has(name);
  },
  validateParams(action: string, params: unknown): Record<string, unknown> {
    const spec = registry.get(action);
    if (typeof params !== "object" || params === null || Array.isArray(params)) throw new ProtocolError("INVALID_PARAMETERS", "params must be an object");
    const v = validatorFor("params", action);
    if (!v(params)) throwValidation(v, "INVALID_PARAMETERS");
    const out: Record<string, unknown> = { ...(params as Record<string, unknown>) };
    const props = (spec.paramsSchema.properties ?? {}) as Record<string, { default?: unknown }>;
    for (const [k, p] of Object.entries(props)) if (!(k in out) && p.default !== undefined) out[k] = p.default;
    return out;
  },
  validateTarget(action: string, target: unknown): Record<string, unknown> | null {
    const spec = registry.get(action);
    if (spec.targetName === null) {
      if (target !== null && target !== undefined) throw new ProtocolError("INVALID_PARAMETERS", "this action does not take a target");
      return null;
    }
    if (typeof target !== "object" || target === null || Array.isArray(target)) throw new ProtocolError("TARGET_REQUIRED", "this action requires a target");
    const v = validatorFor("target", spec.targetName);
    if (!v(target)) throwValidation(v, "INVALID_PARAMETERS", "target: ");
    return { ...(target as Record<string, unknown>) };
  },
  validateResult(action: string, result: unknown): Record<string, unknown> {
    const spec = registry.get(action);
    if (typeof result !== "object" || result === null || Array.isArray(result)) throw new ProtocolError("MALFORMED_MESSAGE", "result must be an object");
    const v = validatorFor("results", spec.resultName);
    if (!v(result)) throwValidation(v, "MALFORMED_MESSAGE", "result: ");
    return { ...(result as Record<string, unknown>) };
  },
  errorDefaults(code: string): { retryable: boolean; user_message: string } {
    return ERRORS[code] ?? ERRORS.INTERNAL!;
  },
  plan(planId: PlanId) {
    return PLANS.plans[planId];
  },
};

export type FrameDirection = "controller_to_relay" | "relay_to_controller" | "agent_to_relay" | "relay_to_agent";
export type BridgeDirection = "extension_to_agent" | "agent_to_extension";

export const schemas = {
  validateEnvelopeShape(value: unknown): void {
    const v = validatorFor("envelope", "");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateCommandPayload(value: unknown): void {
    const v = validatorFor("command", "");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateConfirmationPayload(value: unknown): void {
    const v = validatorFor("confirmation", "");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  /** relay-frames.schema.json#/$defs/hello_proof — the payload the phone signs to bind its socket. */
  validateHelloProofPayload(value: unknown): void {
    const v = validatorFor("relay_frames", "hello_proof");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  /** relay-frames.schema.json#/$defs/input_batch_payload — the signed payload of an input_batch frame. */
  validateInputBatchPayload(value: unknown): void {
    const v = validatorFor("relay_frames", "input_batch_payload");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateFrame(direction: FrameDirection, value: unknown): void {
    const v = validatorFor("relay_frames", direction);
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateBridgeFrame(_direction: BridgeDirection, _value: unknown): void {
    // The phone never speaks the native-messaging bridge protocol; no validator is bundled for it.
    throw new ProtocolError("INTERNAL", "bridge frames are not validated in the PWA build");
  },
  /** Strict-parse a copy of challenge_text and validate it against #/$defs/challenge. Keep the original string for hashing. */
  validateChallengeText(challengeText: string): Record<string, unknown> {
    const parsed = loadsStrict(challengeText, { maxBytes: LIMITS.max_challenge_text_bytes, requireObject: true });
    const v = validatorFor("relay_frames", "challenge");
    if (!v(parsed)) throwValidation(v, "MALFORMED_MESSAGE", "challenge: ");
    return parsed as Record<string, unknown>;
  },
  validateRest(bodyName: string, value: unknown): void {
    const v = validatorFor("rest", bodyName);
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE", `${bodyName}: `);
  },
  validateEntitlementClaims(_claims: unknown): void {
    // Entitlement assertions are verified by the PC, never by the phone; no validator is bundled.
    throw new ProtocolError("INTERNAL", "entitlement claims are not validated in the PWA build");
  },
  validateDef(doc: "relay-frames" | "bridge" | "command" | "results" | "rest", def: string, value: unknown): void {
    const v = validatorFor(doc.replace("-", "_"), def);
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
};

export function protocolCompatible(peerVersion: string, supported: readonly string[]): boolean {
  if (typeof peerVersion !== "string") return false;
  const m = /^([0-9]+)\.([0-9]+)$/.exec(peerVersion);
  if (!m) return false;
  const major = Number(m[1]);
  const minor = Number(m[2]);
  let best = -1;
  for (const v of supported) {
    const s = /^(\d+)\.(\d+)$/.exec(v);
    if (s && Number(s[1]) === major) best = Math.max(best, Number(s[2]));
  }
  return best >= 0 && minor <= best;
}

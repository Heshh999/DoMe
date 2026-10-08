/**
 * Action registry and frame validators, compiled from the JSON contract in shared/protocol.
 */
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";

import actionsJson from "../../protocol/actions.json" with { type: "json" };
import errorsJson from "../../protocol/errors.json" with { type: "json" };
import plansJson from "../../protocol/plans.json" with { type: "json" };
import versionJson from "../../protocol/version.json" with { type: "json" };
import envelopeSchema from "../../protocol/schemas/envelope.schema.json" with { type: "json" };
import commandSchema from "../../protocol/schemas/command.schema.json" with { type: "json" };
import confirmationSchema from "../../protocol/schemas/confirmation.schema.json" with { type: "json" };
import relayFramesSchema from "../../protocol/schemas/relay-frames.schema.json" with { type: "json" };
import bridgeSchema from "../../protocol/schemas/bridge.schema.json" with { type: "json" };

import { ProtocolError } from "./errors.ts";

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
  capability: Capability;
  risk: Risk;
  confirmation: Confirmation;
  timeoutMs: number;
  availability: readonly string[];
  verification: string;
  coalesce: string | null;
  idempotent: boolean;
  routineAllowed: boolean;
}

export const PROTOCOL_VERSION: string = versionJson.protocol_version;
export const REGISTRY_VERSION: string = actionsJson.registry_version;
export const LIMITS = versionJson.limits;
export const PLANS = plansJson;
export const ERRORS = errorsJson.errors as Record<string, { retryable: boolean; user_message: string }>;
export const CAPABILITIES = actionsJson.capabilities as Record<string, string>;

const BASE = "https://dome.app/schemas/";

const ajv = new Ajv2020({ strict: true, allErrors: false, allowUnionTypes: true });
for (const doc of [envelopeSchema, commandSchema, confirmationSchema, relayFramesSchema, bridgeSchema]) {
  ajv.addSchema(doc as object);
}

const refValidators = new Map<string, ValidateFunction>();
function refValidator(doc: string, pointer: string): ValidateFunction {
  const key = `${doc}#${pointer}`;
  let v = refValidators.get(key);
  if (!v) {
    v = ajv.compile({ $ref: `${BASE}${doc}.schema.json#${pointer}` });
    refValidators.set(key, v);
  }
  return v;
}

function throwValidation(v: ValidateFunction, code: string, prefix = ""): never {
  const err = v.errors?.[0];
  const path = err?.instancePath?.replace(/^\//, "").replace(/\//g, "/") || "<root>";
  throw new ProtocolError(code, `${prefix}${path}: ${err?.message ?? "invalid"}`.slice(0, 300));
}

const actionSpecs = new Map<string, ActionSpec>();
const paramValidators = new Map<string, ValidateFunction>();
const targetValidators = new Map<string, ValidateFunction>();

for (const [name, schema] of Object.entries(actionsJson.target_schemas)) {
  targetValidators.set(name, ajv.compile(schema as object));
}
for (const [name, raw] of Object.entries(actionsJson.actions)) {
  const r = raw as Record<string, unknown>;
  const spec: ActionSpec = {
    name,
    summary: String(r.summary),
    paramsSchema: r.params as Record<string, unknown>,
    targetName: (r.target as string | null) ?? null,
    capability: r.capability as Capability,
    risk: r.risk as Risk,
    confirmation: r.confirmation as Confirmation,
    timeoutMs: Number(r.timeout_ms),
    availability: (r.availability as string[]) ?? [],
    verification: String(r.verification),
    coalesce: (r.coalesce as string | undefined) ?? null,
    idempotent: Boolean(r.idempotent),
    routineAllowed: Boolean(r.routine_allowed),
  };
  if (spec.risk === "disruptive" && spec.confirmation !== "challenge") throw new Error(`disruptive action ${name} must require a challenge`);
  actionSpecs.set(name, spec);
  paramValidators.set(name, ajv.compile(spec.paramsSchema));
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
    const v = paramValidators.get(action)!;
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
    const v = targetValidators.get(spec.targetName)!;
    if (!v(target)) throwValidation(v, "INVALID_PARAMETERS", "target: ");
    return { ...(target as Record<string, unknown>) };
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
    const v = refValidator("envelope", "");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateCommandPayload(value: unknown): void {
    const v = refValidator("command", "");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateConfirmationPayload(value: unknown): void {
    const v = refValidator("confirmation", "");
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateFrame(direction: FrameDirection, value: unknown): void {
    const v = refValidator("relay-frames", `/$defs/${direction}`);
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateBridgeFrame(direction: BridgeDirection, value: unknown): void {
    const v = refValidator("bridge", `/$defs/${direction}`);
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
  validateDef(doc: "relay-frames" | "bridge" | "command", def: string, value: unknown): void {
    const v = refValidator(doc, `/$defs/${def}`);
    if (!v(value)) throwValidation(v, "MALFORMED_MESSAGE");
  },
};

export function protocolCompatible(peerVersion: string, supported: readonly string[]): boolean {
  const m = /^(\d+)\.(\d+)$/.exec(peerVersion);
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

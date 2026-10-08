/**
 * Build-time validator generation (no runtime `eval`).
 *
 * The PWA is served by cloud-api under `Content-Security-Policy: script-src 'self'` (no
 * `unsafe-eval`). `@dome/protocol`'s `registry.ts` compiles its Ajv validators with `new Function`
 * when the module is imported, which that CSP refuses. The PWA therefore precompiles every
 * validator it needs from the SAME frozen schema files in `shared/protocol/` with Ajv's standalone
 * code generator, and `vite.config.ts` resolves the shared library's internal `./registry.ts`
 * import to `src/protocol/registry.ts`, an API-identical facade over this generated code.
 * `test/protocol-parity.test.ts` fails when this output is stale or disagrees with the real module.
 *
 *   pnpm gen:validators
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import { _ } from "ajv/dist/compile/codegen/index.js";
import standaloneCode from "ajv/dist/standalone/index.js";

const here = dirname(fileURLToPath(import.meta.url));
export const PROTOCOL_DIR = resolve(here, "../../shared/protocol");
export const SCHEMA_DIR = resolve(PROTOCOL_DIR, "schemas");
export const OUTPUT_JS = resolve(here, "../src/protocol/generated/validators.js");
export const OUTPUT_DTS = resolve(here, "../src/protocol/generated/validators.d.ts");

const SCHEMA_DOCS = ["envelope", "command", "confirmation", "relay-frames", "bridge", "results", "entitlement", "rest"] as const;
const BASE = "https://dome.app/schemas/";
const ACTIONS_BASE = "https://dome.app/actions/";
/** relay-frames $defs the phone validates directly (the two directions it speaks plus the challenge and its parts). */
const RELAY_FRAME_DEFS = new Set(["relay_to_controller", "controller_to_relay", "challenge", "error", "pc_state", "youtube_tab", "media_session", "pc_connection_state", "lifecycle_state"]);

interface JsonObject {
  [k: string]: unknown;
}

function readJson(path: string): JsonObject {
  return JSON.parse(readFileSync(path, "utf8")) as JsonObject;
}

/** Export name for a `$defs` entry or action: `rest__session_response`, `params__youtube_set_paused`. */
export function exportName(group: string, name: string): string {
  return `${group}__${name.replace(/[^A-Za-z0-9_]/g, "_")}`;
}

export function buildExports(): Record<string, string> {
  const out: Record<string, string> = {
    envelope: `${BASE}envelope.schema.json`,
    command: `${BASE}command.schema.json`,
    confirmation: `${BASE}confirmation.schema.json`,
  };
  for (const doc of ["relay-frames", "results", "rest"] as const) {
    const schema = readJson(resolve(SCHEMA_DIR, `${doc}.schema.json`));
    const defs = schema.$defs as Record<string, unknown>;
    for (const name of Object.keys(defs)) {
      if (doc === "relay-frames" && !RELAY_FRAME_DEFS.has(name)) continue; // agent-side frames are never seen by the phone
      out[exportName(doc.replace("-", "_"), name)] = `${BASE}${doc}.schema.json#/$defs/${name}`;
    }
  }
  const actions = readJson(resolve(PROTOCOL_DIR, "actions.json"));
  for (const name of Object.keys(actions.actions as Record<string, unknown>)) {
    out[exportName("params", name)] = `${ACTIONS_BASE}params/${name}`;
  }
  for (const name of Object.keys(actions.target_schemas as Record<string, unknown>)) {
    out[exportName("target", name)] = `${ACTIONS_BASE}targets/${name}`;
  }
  return out;
}

export function generateValidatorsSource(): string {
  const ajv = new Ajv2020({
    strict: true,
    allErrors: false,
    allowUnionTypes: true,
    formats: { uri: /^(?:https?|wss?):\/\/[^\s/?#]+[^\s]*$/ },
    code: { source: true, esm: true, lines: false, formats: _`__formats` },
  });
  for (const name of SCHEMA_DOCS) {
    ajv.addSchema(readJson(resolve(SCHEMA_DIR, `${name}.schema.json`)));
  }
  const actions = readJson(resolve(PROTOCOL_DIR, "actions.json"));
  for (const [name, raw] of Object.entries(actions.actions as Record<string, JsonObject>)) {
    ajv.addSchema({ $id: `${ACTIONS_BASE}params/${name}`, ...(raw.params as JsonObject) });
  }
  for (const [name, raw] of Object.entries(actions.target_schemas as Record<string, JsonObject>)) {
    ajv.addSchema({ $id: `${ACTIONS_BASE}targets/${name}`, ...raw });
  }
  const exportsMap = buildExports();
  for (const ref of Object.values(exportsMap)) {
    if (!ajv.getSchema(ref)) throw new Error(`schema ref not found: ${ref}`);
  }
  let code = standaloneCode(ajv, exportsMap);
  // Ajv emits CommonJS requires for its runtime helpers even in ESM mode; turn them into imports.
  let n = 0;
  code = code.replace(/const (\w+) = require\("(ajv\/dist\/runtime\/[\w/-]+)"\)\.default;/g, (_m, name: string, mod: string) => {
    n += 1;
    return `import __rt${n} from "${mod}.js";\nconst ${name} = __rt${n}.default ?? __rt${n};`;
  });
  if (/require\(/.test(code)) throw new Error("unexpected require() left in generated code");
  if (/new Function|\beval\(/.test(code)) throw new Error("generated code must not contain eval");
  const header = [
    "/* eslint-disable */",
    "// AUTO-GENERATED by scripts/gen-validators.ts from shared/protocol — do not edit.",
    "// Plain JavaScript validators (Ajv standalone); no code generation at runtime (CSP script-src 'self').",
    'import __formats from "../formats.js";',
    "",
  ].join("\n");
  return header + code.replace(/^"use strict";\n?/, "") + "\n";
}

export function generateDtsSource(): string {
  const names = Object.keys(buildExports()).sort();
  return [
    "// AUTO-GENERATED by scripts/gen-validators.ts — do not edit.",
    "export interface ValidationError {",
    "  instancePath: string;",
    "  schemaPath: string;",
    "  keyword: string;",
    "  params: Record<string, unknown>;",
    "  message?: string;",
    "}",
    "export interface Validator {",
    "  (data: unknown): boolean;",
    "  errors?: ValidationError[] | null;",
    "}",
    ...names.map((n) => `export const ${n}: Validator;`),
    "",
  ].join("\n");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  writeFileSync(OUTPUT_JS, generateValidatorsSource());
  writeFileSync(OUTPUT_DTS, generateDtsSource());
  console.log(`wrote ${OUTPUT_JS} (${Object.keys(buildExports()).length} validators)`);
}

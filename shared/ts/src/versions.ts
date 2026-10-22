/**
 * Protocol version arithmetic: the MINOR compatibility rule, bridge negotiation and per-action stamping.
 *
 * Kept free of Ajv and the registry so the PWA's no-eval registry facade (mobile-app/src/protocol/registry.ts) does not
 * have to mirror it: everything here is used unchanged in every bundle.
 */

/**
 * rules.command_version_stamping: input batches were introduced in 1.1 and are stamped with it unless they carry an event a
 * later MINOR added (see `inputBatchProtocolVersion`).
 */
export const INPUT_BATCH_PROTOCOL_VERSION = "1.1";

/** Screen view (protocol 1.4) added three input events; a batch that carries one needs a 1.4 peer. */
export const SCREEN_INPUT_PROTOCOL_VERSION = "1.4";
const SCREEN_INPUT_EVENT_TYPES = new Set(["pointer_move_to", "pointer_wheel"]);
const SCREEN_INPUT_KEYS = new Set(["f11"]);

/**
 * The protocol version that defined this `input_event`: 1.1 for the touchpad events, 1.4 for the Screen view additions
 * `pointer_move_to`, `pointer_wheel` and `key: f11`. Unknown shapes answer the base version; schema validation rejects them.
 */
export function inputEventSince(event: { type?: unknown; key?: unknown }): string {
  const kind = typeof event.type === "string" ? event.type : "";
  if (SCREEN_INPUT_EVENT_TYPES.has(kind) || (kind === "key" && typeof event.key === "string" && SCREEN_INPUT_KEYS.has(event.key))) {
    return SCREEN_INPUT_PROTOCOL_VERSION;
  }
  return INPUT_BATCH_PROTOCOL_VERSION;
}

/**
 * rules.command_version_stamping: the lowest protocol version that defines every event in the batch. The phone stamps its
 * batches with it so a 1.1 PC keeps accepting plain touchpad batches; verifiers refuse a batch stamped below what its
 * events need (PROTOCOL_INCOMPATIBLE, `required` = this value).
 */
export function inputBatchProtocolVersion(events: readonly { type?: unknown; key?: unknown }[]): string {
  let best = INPUT_BATCH_PROTOCOL_VERSION;
  for (const ev of events) {
    const since = inputEventSince(ev);
    if (versionBelow(best, since)) best = since;
  }
  return best;
}

/** `[major, minor]` for a strict `MAJOR.MINOR` string (ASCII digits only), else null. */
export function parseVersion(version: unknown): [number, number] | null {
  if (typeof version !== "string") return null;
  const m = /^([0-9]+)\.([0-9]+)$/.exec(version);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

export function protocolCompatible(peerVersion: string, supported: readonly string[]): boolean {
  const peer = parseVersion(peerVersion);
  if (!peer) return false;
  const [major, minor] = peer;
  let best = -1;
  for (const v of supported) {
    const s = parseVersion(v);
    if (s && s[0] === major) best = Math.max(best, s[1]);
  }
  return best >= 0 && minor <= best;
}

/** True when `version` is below `minimum` (both valid version strings; callers validate the payload schema first). */
export function versionBelow(version: string, minimum: string): boolean {
  const v = parseVersion(version);
  const m = parseVersion(minimum);
  if (!v || !m) throw new Error(`not a protocol version: ${JSON.stringify(version)} / ${JSON.stringify(minimum)}`);
  return v[0] < m[0] || (v[0] === m[0] && v[1] < m[1]);
}

/**
 * The highest version both sides speak (rules.bridge_negotiation): within the highest MAJOR both lists name, the lower
 * of the two sides' highest MINORs (a side that supports a MINOR also accepts every lower one). null when no MAJOR is
 * shared; invalid strings are ignored.
 */
export function negotiateVersion(peerVersions: readonly string[], supported: readonly string[]): string | null {
  const bestMinorByMajor = (versions: readonly string[]): Map<number, number> => {
    const out = new Map<number, number>();
    for (const v of versions) {
      const p = parseVersion(v);
      if (p) out.set(p[0], Math.max(out.get(p[0]) ?? -1, p[1]));
    }
    return out;
  };
  const peer = bestMinorByMajor(peerVersions);
  const ours = bestMinorByMajor(supported);
  const common = [...peer.keys()].filter((major) => ours.has(major));
  if (common.length === 0) return null;
  const major = Math.max(...common);
  return `${major}.${Math.min(peer.get(major)!, ours.get(major)!)}`;
}

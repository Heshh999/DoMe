/**
 * Protocol version arithmetic: the MINOR compatibility rule, bridge negotiation and per-action stamping.
 *
 * Kept free of Ajv and the registry so the PWA's no-eval registry facade (mobile-app/src/protocol/registry.ts) does not
 * have to mirror it: everything here is used unchanged in every bundle.
 */

/** rules.command_version_stamping: input batches were introduced in 1.1 and are stamped with it. */
export const INPUT_BATCH_PROTOCOL_VERSION = "1.1";

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

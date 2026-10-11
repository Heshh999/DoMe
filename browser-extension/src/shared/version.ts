/**
 * Protocol versions this extension speaks and the contract's compatibility rule
 * (version.json → compatibility_rule). Mirrors `protocolCompatible` in @dome/protocol, which the
 * service worker cannot import (see CONTRACT_ISSUES.md); test/frames.test.ts checks parity.
 *
 * Every MINOR is listed, not only the newest: the agent accepts the hello only when a version is on
 * both its list and this one (`negotiate_protocol_version` in pc-agent dome_agent/bridge/server.py),
 * and its ack names the highest such version; otherwise it refuses with PROTOCOL_INCOMPATIBLE. 1.1
 * added manual input (touchpad/keyboard) and left bridge.schema.json unchanged, so this extension
 * speaks it as-is. An ack with a higher MINOR is still refused until that MINOR is added here, and the
 * agent's list must name it too.
 */
export const SUPPORTED_PROTOCOL_VERSIONS: readonly string[] = ["1.0", "1.1"];

export function protocolCompatible(peerVersion: unknown, supported: readonly string[] = SUPPORTED_PROTOCOL_VERSIONS): boolean {
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

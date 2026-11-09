/**
 * The offer compaction (DECISIONS 91; docs/design/screen-view.md §4.2). On 2026-11-09 the staging relay began handing
 * the phone TURN servers (three URL variants: udp, tcp, tls) and from that moment every start failed with
 * SCREEN_OFFER_INVALID, on Wi-Fi and on mobile data alike: Safari gathers relay candidates for every URL variant on
 * every interface (Wi-Fi and cellular, IPv4 and IPv6) plus ICE-TCP host candidates, and with the default bundle policy
 * it repeats every candidate in all three media sections, so the offer went past
 * shared/protocol/version.json limits.screen_sdp_max_bytes (12288) and the phone's own pre-send check refused it. The
 * peer connection is now created with bundlePolicy "max-bundle" (lib/screen.ts BUNDLE_POLICY) and, once gathering is
 * complete, `compactOffer` removes the candidates that cannot help and trims to the limit, in this order:
 *   1. every a=candidate line in a media section after the first (`droppedBundled`): the sections are bundled, so the
 *      PC's ICE agent only ever uses the first section's candidates;
 *   2. every TCP candidate, transport `tcp` or a `tcptype` token (`droppedTcp`): the PC's aiortc 1.15 / aioice 0.10
 *      gathers UDP only (aioice/ice.py `gather_candidates`: `transport="udp"` for every host, srflx and relay
 *      candidate) and `Candidate.can_pair_with` (aioice/candidate.py, "self.transport.lower() ==
 *      other.transport.lower()") pairs only candidates of the same transport, so a TCP candidate of ours never forms
 *      a pair there;
 *   3. only while the offer is still above `limit`: the remaining candidate with the lowest priority (the priority
 *      field of a=candidate; among equals the later line), one at a time, as long as at least one candidate of every
 *      kind AND address family present (host, srflx, prflx, relay; IPv4, IPv6, an mDNS name) stays (`droppedLow`):
 *      aioice pairs a remote candidate only with a local one of the same IP version (aioice/candidate.py
 *      `Candidate.can_pair_with`, "a.version == b.version"), and libwebrtc ranks an IPv6 candidate above the IPv4
 *      one of the same kind on the same interface, so a guard by kind alone would leave a PC without a global IPv6
 *      address nothing to pair with. When nothing more may go, the offer is returned above the limit and the caller
 *      fails the start as "too long".
 * `a=end-of-candidates` stays, an a=candidate line this parser does not understand stays, and every other line stays
 * byte for byte and in order (the DTLS fingerprint, ice-ufrag and ice-pwd, the m-lines, the codecs), so the string is
 * still this peer's own description: the phone applied the full one to its own RTCPeerConnection, and the remote side
 * only needs a subset of our candidates. Pure and silent: the caller logs sizes and counts only (never a candidate, an
 * address or a credential; rules.screen_content).
 */
import { utf8ByteLength } from "@dome/protocol";

export interface CompactOfferResult {
  sdp: string;
  bytesBefore: number;
  bytesAfter: number;
  /** a=candidate lines understood, before and after. */
  candidatesBefore: number;
  candidatesAfter: number;
  /** TCP candidates of the first section (a TCP line of a later section counts as bundled). */
  droppedTcp: number;
  /** Lowest-priority candidates dropped to reach the limit. */
  droppedLow: number;
  /** Candidates of the media sections after the first. */
  droppedBundled: number;
}

const CANDIDATE_PREFIX = "a=candidate:";

interface Candidate {
  /** Index of the line in the split SDP. */
  at: number;
  kind: string;
  /** The address family of the connection address: "v6", "v4" or "mdns" (an obfuscated `….local` host name). */
  family: string;
  priority: number;
  tcp: boolean;
}

/** `a=candidate:<foundation> <component> <transport> <priority> <address> <port> typ <type> [raddr A rport P] [tcptype T] …` */
function parseCandidate(line: string): Pick<Candidate, "kind" | "family" | "priority" | "tcp"> | null {
  const fields = line.slice(CANDIDATE_PREFIX.length).split(" ");
  if (fields.length < 8 || fields[6] !== "typ") return null;
  const priority = Number(fields[3]);
  const kind = fields[7];
  if (!Number.isFinite(priority) || !kind) return null;
  const transport = fields[2]?.toLowerCase();
  const address = fields[4] ?? "";
  const family = address.includes(":") ? "v6" : address.toLowerCase().endsWith(".local") ? "mdns" : "v4";
  return { kind, family, priority, tcp: transport === "tcp" || fields.includes("tcptype") };
}

export function compactOffer(sdp: string, limit: number): CompactOfferResult {
  const lines = sdp.split("\r\n");
  const bytesBefore = utf8ByteLength(sdp);
  const dropped = new Set<number>();
  const kept: Candidate[] = [];
  let section = -1;
  let candidatesBefore = 0;
  let droppedBundled = 0;
  let droppedTcp = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (line.startsWith("m=")) {
      section += 1;
      continue;
    }
    if (!line.startsWith(CANDIDATE_PREFIX)) continue;
    const c = parseCandidate(line);
    if (!c) continue;
    candidatesBefore += 1;
    if (section >= 1) {
      dropped.add(i);
      droppedBundled += 1;
    } else if (c.tcp) {
      dropped.add(i);
      droppedTcp += 1;
    } else {
      kept.push({ ...c, at: i });
    }
  }
  // every line but the last (empty) one is followed by a CRLF pair, and a candidate line is never the last
  const lineBytes = (i: number): number => utf8ByteLength(lines[i]!) + 2;
  let bytes = bytesBefore;
  for (const i of dropped) bytes -= lineBytes(i);
  let droppedLow = 0;
  const group = (c: Candidate): string => `${c.kind}/${c.family}`;
  while (bytes > limit) {
    const perGroup = new Map<string, number>();
    for (const c of kept) perGroup.set(group(c), (perGroup.get(group(c)) ?? 0) + 1);
    let lowest = -1;
    for (let k = 0; k < kept.length; k++) {
      const c = kept[k]!;
      if ((perGroup.get(group(c)) ?? 0) < 2) continue; // the last one of its kind and address family stays
      if (lowest < 0 || c.priority <= kept[lowest]!.priority) lowest = k;
    }
    if (lowest < 0) break;
    const [c] = kept.splice(lowest, 1);
    dropped.add(c!.at);
    bytes -= lineBytes(c!.at);
    droppedLow += 1;
  }
  const out = dropped.size === 0 ? sdp : lines.filter((_, i) => !dropped.has(i)).join("\r\n");
  return { sdp: out, bytesBefore, bytesAfter: utf8ByteLength(out), candidatesBefore, candidatesAfter: kept.length, droppedTcp, droppedLow, droppedBundled };
}

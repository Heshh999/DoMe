/**
 * Synthetic offers shaped like Safari's for the compaction tests (lib/sdpCompact.ts; DECISIONS 91). Placeholder
 * addresses only (RFC 5737 / RFC 3849 ranges), a placeholder fingerprint, ICE fragments and ports. Test-only.
 *
 * `safariLikeOffer()` is what the founder's phone built on 2026-11-09 once the relay handed it TURN servers: three
 * media sections (video, audio, application), each repeating the same candidates (the default "balanced" bundle
 * policy): per section 4 host candidates of which 2 ICE-TCP, 4 server-reflexive (Wi-Fi and cellular, IPv4 and IPv6)
 * and 9 relay candidates (udp, tcp and tls URL variants on three interfaces; a relay candidate reached through TURN
 * over TCP or TLS is still a `udp` transport candidate with a lower priority, as libwebrtc writes it). Well over
 * screen_sdp_max_bytes.
 */
const FP = "40:41:42:43:44:45:46:47:48:49:4A:4B:4C:4D:4E:4F:50:51:52:53:54:55:56:57:58:59:5A:5B:5C:5D:5E:5F";
const ICE = ["a=ice-ufrag:ph0n", "a=ice-pwd:phonephonephonephonephon", "a=ice-options:trickle", `a=fingerprint:sha-256 ${FP}`, "a=setup:actpass"];

const VIDEO_PTS = "96 97 98 99 100 101 102 103 35 36 37 38 39 40 41 42 43 44 45 46 104 105 106 107 108 109 110 111 112 113 114 115";
const VIDEO = [
  "a=extmap:1 urn:ietf:params:rtp-hdrext:toffset",
  "a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
  "a=extmap:3 urn:3gpp:video-orientation",
  "a=extmap:4 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
  "a=extmap:5 http://www.webrtc.org/experiments/rtp-hdrext/playout-delay",
  "a=extmap:6 http://www.webrtc.org/experiments/rtp-hdrext/video-content-type",
  "a=extmap:7 http://www.webrtc.org/experiments/rtp-hdrext/video-timing",
  "a=extmap:8 http://www.webrtc.org/experiments/rtp-hdrext/color-space",
  "a=extmap:9 urn:ietf:params:rtp-hdrext:sdes:mid",
  "a=extmap:10 urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id",
  "a=extmap:11 urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id",
  "a=recvonly",
  "a=rtcp-mux",
  "a=rtcp-rsize",
  ...codec(96, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640c1f", 97),
  ...codec(98, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=0;profile-level-id=640c1f", 99),
  ...codec(100, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f", 101),
  ...codec(102, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=0;profile-level-id=42e01f", 103),
  ...codec(35, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=4d001f", 36),
  ...codec(37, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=0;profile-level-id=4d001f", 38),
  ...codec(39, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=64001f", 40),
  ...codec(41, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=0;profile-level-id=64001f", 42),
  ...codec(43, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42001f", 44),
  ...codec(45, "H264/90000", "level-asymmetry-allowed=1;packetization-mode=0;profile-level-id=42001f", 46),
  ...codec(104, "VP8/90000", null, 105),
  ...codec(106, "VP9/90000", "profile-id=0", 107),
  ...codec(108, "VP9/90000", "profile-id=2", 109),
  ...codec(110, "AV1/90000", "level-idx=5;profile=0;tier=0", 111),
  "a=rtpmap:112 red/90000",
  "a=rtpmap:113 rtx/90000",
  "a=fmtp:113 apt=112",
  "a=rtpmap:114 ulpfec/90000",
  "a=rtpmap:115 flexfec-03/90000",
  "a=rtcp-fb:115 goog-remb",
  "a=rtcp-fb:115 transport-cc",
  "a=fmtp:115 repair-window=10000000",
];
const AUDIO_PTS = "111 63 9 0 8 13 110 126";
const AUDIO = [
  "a=extmap:14 urn:ietf:params:rtp-hdrext:ssrc-audio-level",
  "a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
  "a=extmap:4 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
  "a=extmap:9 urn:ietf:params:rtp-hdrext:sdes:mid",
  "a=recvonly",
  "a=rtcp-mux",
  "a=rtcp-rsize",
  "a=rtpmap:111 opus/48000/2",
  "a=rtcp-fb:111 transport-cc",
  "a=fmtp:111 minptime=10;useinbandfec=1",
  "a=rtpmap:63 red/48000/2",
  "a=fmtp:63 111/111",
  "a=rtpmap:9 G722/8000",
  "a=rtpmap:0 PCMU/8000",
  "a=rtpmap:8 PCMA/8000",
  "a=rtpmap:13 CN/8000",
  "a=rtpmap:110 telephone-event/48000",
  "a=rtpmap:126 telephone-event/8000",
];
const APPLICATION = ["a=sctp-port:5000", "a=max-message-size:262144"];

function codec(pt: number, name: string, fmtp: string | null, rtx: number): string[] {
  return [`a=rtpmap:${pt} ${name}`, `a=rtcp-fb:${pt} goog-remb`, `a=rtcp-fb:${pt} transport-cc`, `a=rtcp-fb:${pt} ccm fir`, `a=rtcp-fb:${pt} nack`, `a=rtcp-fb:${pt} nack pli`, ...(fmtp ? [`a=fmtp:${pt} ${fmtp}`] : []), `a=rtpmap:${rtx} rtx/90000`, `a=fmtp:${rtx} apt=${pt}`];
}

/** One interface as Safari sees it: its host address, what the STUN server saw and the relay address TURN gave. */
interface Iface {
  id: number;
  host: string;
  srflx: string;
  relay: string;
  cost: number;
}
const WIFI4: Iface = { id: 1, host: "192.0.2.10", srflx: "198.51.100.20", relay: "203.0.113.30", cost: 10 };
const WIFI6: Iface = { id: 2, host: "2001:db8:1::10", srflx: "2001:db8:1::10", relay: "203.0.113.31", cost: 10 };
const CELL4: Iface = { id: 3, host: "192.0.2.11", srflx: "198.51.100.21", relay: "203.0.113.32", cost: 900 };
const CELL6: Iface = { id: 4, host: "2001:db8:2::10", srflx: "2001:db8:2::10", relay: "203.0.113.33", cost: 900 };

/** libwebrtc's priorities: type preference host 126 / srflx 100 / relay 0 and, within relay, udp 2 / tcp 1 / tls 0. */
export const PRIORITY = { hostUdp: 2122260223, hostTcp: 1518280447, srflx: 1686052607, relayUdp: 41885439, relayTcp: 25108223, relayTls: 8331263 } as const;

export interface CandidateCounts {
  /** UDP host candidates, then ICE-TCP host candidates (tcptype active, port 9). */
  hostUdp: number;
  hostTcp: number;
  srflx: number;
  /** Relay candidates: three URL variants (udp, tcp, tls) per interface, interfaces first. */
  relay: number;
}
export const SAFARI_COUNTS: CandidateCounts = { hostUdp: 2, hostTcp: 2, srflx: 4, relay: 9 };

const tail = (iface: Iface) => `generation 0 network-id ${iface.id} network-cost ${iface.cost}`;

/** The candidate lines of one media section (`section` makes the ports differ per section as libwebrtc's do). */
export function candidateLines(counts: CandidateCounts, section: number): string[] {
  const ifaces = [WIFI4, WIFI6, CELL4, CELL6];
  const port = (base: number, n: number) => base + section * 7 + n;
  const out: string[] = [];
  let f = 1000 + section;
  for (let n = 0; n < counts.hostUdp; n++) {
    const i = ifaces[n % 4]!;
    out.push(`a=candidate:${f++} 1 udp ${PRIORITY.hostUdp - n} ${i.host} ${port(50000, n)} typ host ${tail(i)}`);
  }
  for (let n = 0; n < counts.hostTcp; n++) {
    const i = ifaces[n % 4]!;
    out.push(`a=candidate:${f++} 1 tcp ${PRIORITY.hostTcp - n} ${i.host} 9 typ host tcptype active ${tail(i)}`);
  }
  for (let n = 0; n < counts.srflx; n++) {
    const i = ifaces[n % 4]!;
    out.push(`a=candidate:${f++} 1 udp ${PRIORITY.srflx - n} ${i.srflx} ${port(51000, n)} typ srflx raddr ${i.host} rport ${port(50000, n)} ${tail(i)}`);
  }
  const relayPriorities = [PRIORITY.relayUdp, PRIORITY.relayTcp, PRIORITY.relayTls];
  for (let n = 0; n < counts.relay; n++) {
    const i = ifaces[Math.floor(n / 3) % 4]!;
    out.push(`a=candidate:${f++} 1 udp ${relayPriorities[n % 3]! - Math.floor(n / 3)} ${i.relay} ${port(60000, n)} typ relay raddr ${i.srflx} rport ${port(51000, n)} ${tail(i)}`);
  }
  return out;
}

export interface SafariOfferOptions {
  /** Candidates per media section (every section repeats them, as the balanced bundle policy does). */
  counts?: CandidateCounts;
  /** 1.5: the cursor channel is in the same application section (no extra section), so nothing changes here. */
  endOfCandidates?: boolean;
  /** Fixture padding of the first section's non-candidate lines, in whole lines of about 30 bytes. */
  extraLines?: number;
}

/** A three-section receive-only offer shaped like Safari's, with the candidates repeated in every section. */
export function safariLikeOffer(o: SafariOfferOptions = {}): string {
  const counts = o.counts ?? SAFARI_COUNTS;
  const end = o.endOfCandidates ? ["a=end-of-candidates"] : [];
  const pad = Array.from({ length: o.extraLines ?? 0 }, (_, i) => `a=rtcp-fb:96 nack pli ${i}`);
  const section = (m: string, mid: number, body: string[]) => [m, "c=IN IP4 0.0.0.0", "a=rtcp:9 IN IP4 0.0.0.0", ...candidateLines(counts, mid), ...end, ...ICE, `a=mid:${mid}`, ...body];
  return [
    "v=0",
    "o=- 4611731400430051337 2 IN IP4 127.0.0.1",
    "s=-",
    "t=0 0",
    "a=group:BUNDLE 0 1 2",
    "a=extmap-allow-mixed",
    "a=msid-semantic: WMS",
    ...section(`m=video 9 UDP/TLS/RTP/SAVPF ${VIDEO_PTS}`, 0, [...VIDEO, ...pad]),
    ...section(`m=audio 9 UDP/TLS/RTP/SAVPF ${AUDIO_PTS}`, 1, AUDIO),
    ...section("m=application 9 UDP/DTLS/SCTP webrtc-datachannel", 2, APPLICATION),
    "",
  ].join("\r\n");
}

/** The candidate lines of each media section of `sdp`, in order (section 0 first). */
export function candidatesPerSection(sdp: string): string[][] {
  const out: string[][] = [];
  for (const line of sdp.split("\r\n")) {
    if (line.startsWith("m=")) out.push([]);
    else if (line.startsWith("a=candidate:")) out[out.length - 1]?.push(line);
  }
  return out;
}

/** Every line that is not a candidate, in order: what compaction must leave byte for byte. */
export function nonCandidateLines(sdp: string): string[] {
  return sdp.split("\r\n").filter((l) => !l.startsWith("a=candidate:"));
}

/** Strings from the synthetic offer that must never reach a log or a diagnostics bundle. */
export const OFFER_SECRETS = ["192.0.2.10", "198.51.100.20", "203.0.113.30", "2001:db8", "ph0n", "phonephone", "a=candidate", FP.slice(0, 23)];

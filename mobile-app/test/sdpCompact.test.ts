/**
 * `compactOffer` (lib/sdpCompact.ts; DECISIONS 91) on a synthetic offer shaped like Safari's once the relay handed it
 * TURN servers (2026-11-09): three media sections each with 4 host (2 of them ICE-TCP), 4 server-reflexive and 9 relay
 * candidates, over screen_sdp_max_bytes. Compacted: under the limit, candidates in the first section only, no TCP, at
 * least one of each kind and address family, every other line byte for byte and in order; the lowest priorities go
 * last and only while still too long; an offer already under the limit loses only what can never help; no
 * candidates, nothing changes.
 */
import { describe, expect, it } from "vitest";

import { LIMITS, screenSdpAcceptable } from "@dome/protocol";

import { compactOffer } from "../src/lib/sdpCompact.ts";
import { OFFER_SDP } from "./helpers/fakePeer.ts";
import { candidateLines, candidatesPerSection, nonCandidateLines, PRIORITY, SAFARI_COUNTS, safariLikeOffer } from "./helpers/sdpOffers.ts";

const LIMIT = LIMITS.screen_sdp_max_bytes;
const bytes = (s: string): number => new TextEncoder().encode(s).length;
const kindOf = (line: string): string => / typ (\w+)/.exec(line)![1]!;
const priorityOf = (line: string): number => Number(line.split(" ")[3]);
const isTcp = (line: string): boolean => / tcp /i.test(line) || / tcptype /.test(line);
const familyOf = (line: string): string => (line.split(" ")[4]!.includes(":") ? "v6" : "v4");

describe("compactOffer: Safari's offer with TURN servers", () => {
  it("3 sections x (4 host incl. 2 tcp, 4 srflx, 9 relay) is over the limit; compacted it is under, candidates in section 0 only, no tcp, every kind kept, everything else byte for byte", () => {
    const offer = safariLikeOffer({ endOfCandidates: true });
    expect(bytes(offer)).toBeGreaterThan(LIMIT);
    expect(screenSdpAcceptable(offer)).toBe(false); // what the phone's pre-send check refused on 2026-11-09
    const r = compactOffer(offer, LIMIT);
    expect(r.bytesBefore).toBe(bytes(offer));
    expect(r.bytesAfter).toBe(bytes(r.sdp));
    expect(r.bytesAfter).toBeLessThanOrEqual(LIMIT);
    expect(screenSdpAcceptable(r.sdp)).toBe(true);
    expect(r).toMatchObject({ candidatesBefore: 51, candidatesAfter: 15, droppedBundled: 34, droppedTcp: 2, droppedLow: 0 });
    const sections = candidatesPerSection(r.sdp);
    expect(sections.map((s) => s.length)).toEqual([15, 0, 0]);
    expect(sections[0]!.some(isTcp)).toBe(false);
    expect(sections[0]!.map(kindOf)).toEqual([...Array<string>(2).fill("host"), ...Array<string>(4).fill("srflx"), ...Array<string>(9).fill("relay")]);
    // exactly the first section's UDP lines, in their order; a relay reached through TURN over TCP or TLS is a udp candidate and stays
    expect(sections[0]).toEqual(candidateLines(SAFARI_COUNTS, 0).filter((l) => !isTcp(l)));
    expect(nonCandidateLines(r.sdp)).toEqual(nonCandidateLines(offer));
    expect(r.sdp.split("\r\n").filter((l) => l === "a=end-of-candidates")).toHaveLength(3);
    expect(r.sdp.endsWith("\r\n")).toBe(true);
    expect(r.sdp).toContain("a=fingerprint:sha-256 40:41:42:43");
    expect(r.sdp).toContain("a=ice-pwd:phonephonephonephonephon");
  });

  it("the lowest priorities go last, one at a time and only while still above the limit; the last candidate of a kind never goes", () => {
    const offer = safariLikeOffer();
    const full = compactOffer(offer, LIMIT);
    expect(full.droppedLow).toBe(0);
    // one byte short of the compact size: exactly one more candidate goes, the lowest priority (the cellular TLS relay)
    const one = compactOffer(offer, full.bytesAfter - 1);
    expect(one).toMatchObject({ candidatesAfter: 14, droppedLow: 1, droppedBundled: 34, droppedTcp: 2 });
    expect(one.bytesAfter).toBeLessThanOrEqual(full.bytesAfter - 1);
    const kept = candidatesPerSection(one.sdp)[0]!.map(priorityOf);
    const before = candidatesPerSection(full.sdp)[0]!.map(priorityOf);
    expect(kept).toEqual(before.filter((p) => p !== Math.min(...before)));
    expect(Math.min(...before)).toBe(PRIORITY.relayTls - 2);
    // a limit nothing can reach: one of each kind and address family stays (the highest priority of each): the Wi-Fi
    // IPv4 and IPv6 host and srflx, and one relay (every relay address the TURN server gave is IPv4); the result is
    // honest about it
    const floor = compactOffer(offer, 0);
    expect(floor).toMatchObject({ candidatesAfter: 5, droppedLow: 10, droppedBundled: 34, droppedTcp: 2 });
    expect(floor.bytesAfter).toBeGreaterThan(0);
    expect(floor.bytesAfter).toBe(bytes(floor.sdp));
    const last = candidatesPerSection(floor.sdp);
    expect(last.map((s) => s.length)).toEqual([5, 0, 0]);
    expect(last[0]!.map(kindOf)).toEqual(["host", "host", "srflx", "srflx", "relay"]);
    expect(last[0]!.map(priorityOf)).toEqual([PRIORITY.hostUdp, PRIORITY.hostUdp - 1, PRIORITY.srflx, PRIORITY.srflx - 1, PRIORITY.relayUdp]);
    expect(last[0]!.map(familyOf)).toEqual(["v4", "v6", "v4", "v6", "v4"]);
    expect(nonCandidateLines(floor.sdp)).toEqual(nonCandidateLines(offer));
  });

  it("the last-resort trim keeps one candidate of each kind AND address family: libwebrtc ranks IPv6 above IPv4 within a kind, and the PC's aioice pairs only candidates of the same IP version, so an IPv4-only PC must still find one", () => {
    // libwebrtc's priorities on a dual-stack phone on Wi-Fi + cellular: within a kind, Wi-Fi IPv6 > Wi-Fi IPv4 >
    // cellular IPv6 > cellular IPv4 (address precedence 40 vs 30 inside the local preference, then the adapter);
    // a relay from a dual-stack TURN server, IPv6 above IPv4 too
    const hostWifi6 = "a=candidate:1 1 udp 2122262783 2001:db8:1::10 50000 typ host generation 0 network-id 2 network-cost 10";
    const hostWifi4 = "a=candidate:2 1 udp 2122260223 192.0.2.10 50001 typ host generation 0 network-id 1 network-cost 10";
    const hostCell6 = "a=candidate:3 1 udp 2122197247 2001:db8:2::10 50002 typ host generation 0 network-id 4 network-cost 900";
    const hostCell4 = "a=candidate:4 1 udp 2122194687 192.0.2.11 50003 typ host generation 0 network-id 3 network-cost 900";
    const srflxWifi6 = "a=candidate:5 1 udp 1686055167 2001:db8:1::10 51000 typ srflx raddr 2001:db8:1::10 rport 50000 generation 0 network-id 2 network-cost 10";
    const srflxWifi4 = "a=candidate:6 1 udp 1686052607 198.51.100.20 51001 typ srflx raddr 192.0.2.10 rport 50001 generation 0 network-id 1 network-cost 10";
    const srflxCell6 = "a=candidate:7 1 udp 1685989631 2001:db8:2::10 51002 typ srflx raddr 2001:db8:2::10 rport 50002 generation 0 network-id 4 network-cost 900";
    const srflxCell4 = "a=candidate:8 1 udp 1685987071 198.51.100.21 51003 typ srflx raddr 192.0.2.11 rport 50003 generation 0 network-id 3 network-cost 900";
    const relay6 = "a=candidate:9 1 udp 41887999 2001:db8:3::30 60000 typ relay raddr 2001:db8:1::10 rport 51000 generation 0 network-id 2 network-cost 10";
    const relay4 = "a=candidate:10 1 udp 41885439 203.0.113.30 60001 typ relay raddr 198.51.100.20 rport 51001 generation 0 network-id 1 network-cost 10";
    const all = [hostWifi6, hostWifi4, hostCell6, hostCell4, srflxWifi6, srflxWifi4, srflxCell6, srflxCell4, relay6, relay4];
    const sdp = OFFER_SDP.replace("a=mid:0\r\n", `${all.join("\r\n")}\r\na=mid:0\r\n`);
    expect(compactOffer(sdp, LIMIT)).toMatchObject({ sdp, candidatesBefore: 10, candidatesAfter: 10, droppedLow: 0 }); // under the limit: nothing goes
    // one byte short: the lowest priority that is not the last of its kind and family goes, the cellular IPv4 srflx
    // (the two relays rank lowest of all and each is the last of its family)
    const one = compactOffer(sdp, bytes(sdp) - 1);
    expect(one).toMatchObject({ candidatesAfter: 9, droppedLow: 1, droppedBundled: 0, droppedTcp: 0 });
    expect(candidatesPerSection(one.sdp)[0]).toEqual(all.filter((l) => l !== srflxCell4));
    // the floor: the highest priority of each kind and family, so an IPv4-only PC and an IPv6-only one both find a pair
    const floor = compactOffer(sdp, 0);
    expect(floor).toMatchObject({ candidatesBefore: 10, candidatesAfter: 6, droppedLow: 4, droppedBundled: 0, droppedTcp: 0 });
    expect(candidatesPerSection(floor.sdp)[0]).toEqual([hostWifi6, hostWifi4, srflxWifi6, srflxWifi4, relay6, relay4]);
    expect(nonCandidateLines(floor.sdp)).toEqual(nonCandidateLines(sdp));
    // an mDNS host name (Safari with address obfuscation) is a family of its own: it stays beside the IPv4 host
    const mdns = "a=candidate:11 1 udp 2122260222 1b2c3d4e-0000-4000-8000-000000000001.local 50004 typ host generation 0 network-id 1 network-cost 10";
    const withMdns = compactOffer(OFFER_SDP.replace("a=mid:0\r\n", `${hostWifi4}\r\n${mdns}\r\na=mid:0\r\n`), 0);
    expect(withMdns).toMatchObject({ candidatesAfter: 2, droppedLow: 0 });
  });

  it("an offer already under the limit loses only the bundled sections' and the TCP candidates", () => {
    const offer = safariLikeOffer({ counts: { hostUdp: 1, hostTcp: 1, srflx: 1, relay: 1 } });
    expect(bytes(offer)).toBeLessThan(LIMIT);
    const r = compactOffer(offer, LIMIT);
    expect(r).toMatchObject({ candidatesBefore: 12, candidatesAfter: 3, droppedBundled: 8, droppedTcp: 1, droppedLow: 0 });
    let section = -1;
    const expected = offer.split("\r\n").filter((l) => {
      if (l.startsWith("m=")) section += 1;
      if (!l.startsWith("a=candidate:")) return true;
      return section === 0 && !isTcp(l);
    });
    expect(r.sdp).toBe(expected.join("\r\n"));
    expect(r.bytesAfter).toBe(bytes(r.sdp));
  });

  it("an offer without candidates comes back as it is", () => {
    expect(compactOffer(OFFER_SDP, LIMIT)).toEqual({ sdp: OFFER_SDP, bytesBefore: bytes(OFFER_SDP), bytesAfter: bytes(OFFER_SDP), candidatesBefore: 0, candidatesAfter: 0, droppedTcp: 0, droppedLow: 0, droppedBundled: 0 });
  });

  it("a candidate line it does not understand stays and is not counted; TCP in any case, or a tcptype token, counts as TCP", () => {
    const odd = "a=candidate:1 1 udp 1 192.0.2.1 1 nottyp host";
    const upper = "a=candidate:2 1 TCP 5 192.0.2.1 9 typ host tcptype active";
    const token = "a=candidate:3 1 udp 5 192.0.2.1 9 typ host tcptype passive";
    const sdp = OFFER_SDP.replace("a=mid:0\r\n", `${odd}\r\n${upper}\r\n${token}\r\na=mid:0\r\n`);
    const r = compactOffer(sdp, LIMIT);
    expect(r.sdp).toBe(sdp.replace(`${upper}\r\n${token}\r\n`, ""));
    expect(r).toMatchObject({ candidatesBefore: 2, candidatesAfter: 0, droppedTcp: 2, droppedLow: 0, droppedBundled: 0 });
  });
});

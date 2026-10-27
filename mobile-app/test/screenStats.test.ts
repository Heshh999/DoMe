/**
 * lib/screenStats.ts (device round 2, 2026-10-27): the `getStats()` whitelist over a Safari-shaped report that carries
 * candidate addresses, ports, ICE fragments, a STUN name, the PC's certificate and the stream's SPS — none of which may
 * survive; the fmtp reduction; the `<video>` probe over a fake element; the log summaries pass `log.ts` redaction
 * untouched; the readout's gate lines say "yes"/"no" in pipeline order.
 */
import { describe, expect, it } from "vitest";

import { redact } from "../src/lib/log.ts";
import { EMPTY_SCREEN_STATE } from "../src/lib/screen.ts";
import { emptyTimeline, fmtpSummary, mediaSummary, playOutcome, probeVideo, readoutLines, reduceStats, statsSummary, type FrameCounters, type ScreenClientFacts } from "../src/lib/screenStats.ts";
import { safariLikeStats, STATS_SECRETS } from "./helpers/fakePeer.ts";

const report = (entries: Array<Record<string, unknown>>) => new Map(entries.map((e, i) => [e && typeof e === "object" && typeof e.id === "string" ? e.id : `x${i}`, e]));

describe("reduceStats: the whitelist", () => {
  it("reads the video inbound-rtp, its codec, the selected pair's candidate TYPES, the transport and the data channel; no address, port, fragment, server, certificate or SPS survives", () => {
    const s = reduceStats(report(safariLikeStats()), 5_000);
    expect(s).toEqual({
      at: 5_000,
      inbound: { packets_received: 400, bytes_received: 420_000, packets_lost: 2, jitter_ms: 3.1, frames_received: 30, frames_decoded: 28, key_frames_decoded: 1, frames_dropped: 0, fps: 30, width: 1280, height: 720, pli: 1, fir: 0, nack: 3, decoder: "VideoToolbox", decode_ms_total: 420, last_packet_age_ms: 40 },
      codec: { mime: "video/H264", pt: 98, fmtp: "profile-level-id=42e01f;packetization-mode=1" },
      pair: { state: "succeeded", rtt_ms: 12, bytes_received: 48_000, local_type: "srflx", remote_type: "host", proto: "udp" },
      dtls: "connected",
      ice: "connected",
      dc_sent: 12,
      dc_received: 3,
    });
    const text = JSON.stringify(s);
    for (const secret of STATS_SECRETS) expect(text, secret).not.toContain(secret);
  });

  it("an empty report, unknown entries and odd values give nulls and placeholders, never the raw strings", () => {
    expect(reduceStats(report([]), 1)).toEqual({ at: 1, inbound: null, codec: null, pair: null, dtls: null, ice: null, dc_sent: null, dc_received: null });
    const odd = reduceStats(
      report([
        { id: "T", type: "transport", dtlsState: "connected; 192.0.2.1", selectedCandidatePairId: "P" },
        { id: "P", type: "candidate-pair", state: "SUCCEEDED", localCandidateId: "L", remoteCandidateId: "R", currentRoundTripTime: "fast" },
        { id: "L", type: "local-candidate", candidateType: "srflx 192.0.2.1", protocol: "quic" },
        { id: "R", type: "remote-candidate", candidateType: "relay" },
        { id: "V", type: "inbound-rtp", mediaType: "video", packetsReceived: Number.NaN, codecId: "C", decoderImplementation: "<script>192.0.2.1:5000</script>" },
        { id: "C", type: "codec", mimeType: "video/H264;evil", payloadType: "98", sdpFmtpLine: "profile-level-id=zz;packetization-mode=7" },
        { id: "M", type: "media-source", trackIdentifier: "secret" },
        null,
        "junk",
      ] as unknown as Array<Record<string, unknown>>),
      2,
    );
    expect(odd.inbound).toMatchObject({ packets_received: null, decoder: "?" });
    expect(odd.codec).toEqual({ mime: "?", pt: null, fmtp: "-" });
    expect(odd.pair).toEqual({ state: "?", rtt_ms: null, bytes_received: null, local_type: "?", remote_type: "relay", proto: "?" });
    expect(odd.dtls).toBe("?");
    expect(odd.ice).toBeNull();
    expect(JSON.stringify(odd)).not.toContain("192.0.2.1");
  });

  it("without the transport's selected pair id: the nominated, succeeded pair (else one flagged selected); the busiest video entry wins", () => {
    const s = reduceStats(
      report([
        { id: "P1", type: "candidate-pair", state: "failed", nominated: true, localCandidateId: "L", remoteCandidateId: "R" },
        { id: "P2", type: "candidate-pair", state: "succeeded", nominated: true, localCandidateId: "L", remoteCandidateId: "R", currentRoundTripTime: 0.05 },
        { id: "L", type: "local-candidate", candidateType: "relay", protocol: "TCP", address: "203.0.113.5" },
        { id: "R", type: "remote-candidate", candidateType: "prflx", address: "203.0.113.6" },
        { id: "V1", type: "inbound-rtp", kind: "video", packetsReceived: 3 },
        { id: "V2", type: "inbound-rtp", kind: "video", packetsReceived: 30 },
      ]),
      3,
    );
    expect(s.pair).toMatchObject({ state: "succeeded", rtt_ms: 50, local_type: "relay", remote_type: "prflx", proto: "tcp" });
    expect(s.inbound?.packets_received).toBe(30);
    expect(s.inbound?.decoder).toBe("-");
    expect(JSON.stringify(s)).not.toContain("203.0.113");
    const firefox = reduceStats(report([{ id: "P", type: "candidate-pair", selected: true, state: "succeeded" }]), 4);
    expect(firefox.pair?.state).toBe("succeeded");
  });

  it("fmtpSummary keeps profile-level-id and packetization-mode only, in a fixed order", () => {
    expect(fmtpSummary("level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42E01F;sprop-parameter-sets=Z0LAH9oB,aM4G4g==")).toBe("profile-level-id=42e01f;packetization-mode=1");
    expect(fmtpSummary("profile-level-id=640c1f")).toBe("profile-level-id=640c1f");
    expect(fmtpSummary("")).toBe("-");
    expect(fmtpSummary(undefined)).toBe("-");
    expect(fmtpSummary("x-google-start-bitrate=1000")).toBe("-");
  });

  it("the summaries a log line carries pass log.ts redaction untouched (no key it would blank)", () => {
    const s = statsSummary(reduceStats(report(safariLikeStats()), 1));
    expect(redact(s)).toEqual(s);
    expect(s).toMatchObject({ rtp_packets: 400, frames_decoded: 28, size: "1280x720", codec: "video/H264", fmtp: "profile-level-id=42e01f;packetization-mode=1", local_type: "srflx", remote_type: "host", proto: "udp", rtt_ms: 12, last_rtp_age_ms: 40 });
    expect(statsSummary(null)).toEqual({ stats: false });
    const m = mediaSummary(probeVideo(fakeVideo(), fakeMedia(), counters(), 1));
    expect(redact(m)).toEqual(m);
    expect(mediaSummary(null)).toEqual({ video: false });
  });
});

function fakeVideo(over: Record<string, unknown> = {}) {
  return { readyState: 4, networkState: 2, paused: false, ended: false, videoWidth: 1280, videoHeight: 720, currentTime: 12.345, error: null, getVideoPlaybackQuality: () => ({ totalVideoFrames: 300, droppedVideoFrames: 2 }), ...over };
}
function fakeMedia(over: Record<string, unknown> = {}) {
  return { active: true, getVideoTracks: () => [{ readyState: "live", muted: false, enabled: true }], ...over };
}
function counters(over: Partial<FrameCounters> = {}): FrameCounters {
  return { play: "resolved", rvfc: true, ticks: 290, firstTickMs: 140, ...over };
}

describe("probeVideo: what the <video> says about itself", () => {
  it("ready state, size, time, playback quality, the play() outcome, frame ticks and the stream's and track's flags", () => {
    expect(probeVideo(fakeVideo(), fakeMedia(), counters(), 7)).toEqual({ at: 7, ready_state: 4, network_state: 2, paused: false, ended: false, width: 1280, height: 720, current_time: 12.3, error: null, total_frames: 300, dropped_frames: 2, play: "resolved", rvfc: true, frame_ticks: 290, first_tick_ms: 140, stream_active: true, track_state: "live", track_muted: false, track_enabled: true });
  });

  it("no element, no stream, an element error, a muted track, a throwing quality call: each reported as such", () => {
    expect(probeVideo(null, null, counters({ play: "none", ticks: 0, firstTickMs: null, rvfc: false }), 1)).toMatchObject({ ready_state: null, paused: null, play: "none", frame_ticks: 0, rvfc: false, stream_active: null, track_muted: null });
    const broken = probeVideo(
      fakeVideo({
        readyState: 0,
        paused: true,
        error: { code: 3 },
        getVideoPlaybackQuality: () => {
          throw new Error("no");
        },
      }),
      fakeMedia({ active: false, getVideoTracks: () => [{ readyState: "ended", muted: true, enabled: true }] }),
      counters({ play: "rejected:NotAllowedError", ticks: 0 }),
      2,
    );
    expect(broken).toMatchObject({ ready_state: 0, paused: true, error: 3, total_frames: null, dropped_frames: null, play: "rejected:NotAllowedError", stream_active: false, track_state: "ended", track_muted: true });
    expect(probeVideo(fakeVideo(), fakeMedia({ getVideoTracks: () => [] }), counters(), 3)).toMatchObject({ track_state: null, track_muted: null, track_enabled: null });
  });

  it("playOutcome names a DOMException by its name, never its message", () => {
    expect(playOutcome("rejected", new DOMException("The request is not allowed by the user agent 192.0.2.1", "NotAllowedError"))).toBe("rejected:NotAllowedError");
    expect(playOutcome("threw", new TypeError("x"))).toBe("threw:TypeError");
    expect(playOutcome("rejected", "a string")).toBe("rejected:Error");
    expect(playOutcome("rejected", { name: "Not Allowed <b>" })).toBe("rejected:Error");
  });
});

describe("readoutLines: the gate checklist, then the numbers", () => {
  const facts = (over: Partial<ScreenClientFacts> = {}): ScreenClientFacts => ({ stats: null, media: null, timeline: emptyTimeline(), channel: { open: false, hb_sent: 0, received: 0, dropped: 0 }, transport_connected: false, codec_fallback: null, ...over });

  it("with nothing known (no stream yet) every gate says no or '-', in pipeline order", () => {
    const lines = readoutLines({ ...EMPTY_SCREEN_STATE, phase: "connecting", verified: true }, facts());
    expect(lines.slice(0, 13)).toEqual(["verified: yes", "track seen: no", "stream set: no", "DTLS: no", "data channel: no, hb 0, in 0", "first RTP: no", "track unmuted: -", "key frame decoded: -", "frames decoded: -", "readyState>=2: -", "play(): -", "rVFC ticks: -", "phase: connecting"]);
    expect(lines.join("\n")).not.toMatch(/✓|✗|true|false/);
  });

  it("a live picture: every gate yes, with the stage times and the numbers behind them", () => {
    const timeline = { ...emptyTimeline(), answer_verified: 900, track: 905, dtls_connected: 1300, dc_open: 1350, first_rtp: 1400, first_decoded: 1500, first_frame: 1520, live: 1520 };
    const stats = reduceStats(report(safariLikeStats()), 1);
    const media = probeVideo(fakeVideo(), fakeMedia(), counters(), 1);
    const lines = readoutLines({ ...EMPTY_SCREEN_STATE, phase: "live", verified: true, stream: { id: "s" } as unknown as MediaStream, encoder: "h264", path: "direct" }, facts({ stats, media, timeline, channel: { open: true, hb_sent: 12, received: 4, dropped: 0 }, codec_fallback: { from: "h264", to: "vp8", reason: "not_decoded" } }));
    expect(lines).toEqual(
      expect.arrayContaining([
        "verified: yes",
        "track seen: yes +905 ms",
        "stream set: yes",
        "DTLS: yes +1300 ms",
        "data channel: open, hb 12, in 4",
        "first RTP: yes +1400 ms",
        "track unmuted: yes",
        "key frame decoded: yes (1)",
        "frames decoded: 28",
        "readyState>=2: yes (4)",
        "play(): resolved",
        "rVFC ticks: 290",
        "phase: live",
        "rtp pkts 400 bytes 420000 lost 2 jitter 3.1 ms age 40 ms",
        "frames recv 30 dec 28 key 1 drop 0 fps 30 1280x720",
        "codec video/H264 pt 98 profile-level-id=42e01f;packetization-mode=1 decoder VideoToolbox",
        "pli 1 fir 0 nack 3",
        "pair succeeded srflx/host udp rtt 12 ms dtls connected",
        "phase live retries 0 stalled no encoder h264 path direct fallback vp8",
      ]),
    );
    for (const secret of STATS_SECRETS) expect(lines.join("\n"), secret).not.toContain(secret);
  });
});

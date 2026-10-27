/**
 * What the phone can say about a Screen view session it cannot see (device round 2, 2026-10-27: the PC said "live" for
 * 217 s while the phone showed "Connecting…" and nothing on the phone could tell which stage failed). Three kinds of
 * facts, all metadata, never a pixel and never an address:
 *   - `reduceStats`: the browser's `getStats()` report passed through a WHITELIST. Candidate stats carry addresses,
 *     ports, related addresses, TURN server names and ICE fragments, certificate stats carry the DTLS fingerprint; none
 *     of those fields is ever read, and nothing raw is spread or kept. Strings are cut to closed shapes (a candidate
 *     type, a protocol, a state word, a codec mime, `profile-level-id` and `packetization-mode` only from the fmtp line).
 *   - `probeVideo`: what the `<video>` element reports about itself (ready state, size, play() outcome, frame-callback
 *     ticks) and the stream's and track's flags.
 *   - the session timeline: ms since `start_sent` at which each stage was first seen.
 * The keys are chosen to pass `log.ts` REDACT_KEY untouched (`fmtp`, `local_type`; never `url`, `candidate*` or
 * `*_text`), so a summary can be logged as it is. `readoutLines` is the test-build readout's text (ScreenReadout).
 */
import type { ScreenSessionState } from "./screen.ts";

// ---- the timeline ----------------------------------------------------------------------------------------------------

export const TIMELINE_MARKS = ["answer_verified", "track", "dtls_connected", "dc_open", "first_rtp", "first_decoded", "first_frame", "live"] as const;
export type TimelineMark = (typeof TIMELINE_MARKS)[number];
/** ms since `screen.start_sent` at which each stage was first reached in this attempt; null = not (yet). */
export type ScreenTimeline = Record<TimelineMark, number | null>;

export function emptyTimeline(): ScreenTimeline {
  return { answer_verified: null, track: null, dtls_connected: null, dc_open: null, first_rtp: null, first_decoded: null, first_frame: null, live: null };
}

// ---- getStats, whitelisted -------------------------------------------------------------------------------------------

/** The receive side of the video RTP stream (inbound-rtp, kind video). Seconds are converted to ms. */
export interface ScreenInboundStats {
  packets_received: number | null;
  bytes_received: number | null;
  packets_lost: number | null;
  jitter_ms: number | null;
  frames_received: number | null;
  frames_decoded: number | null;
  key_frames_decoded: number | null;
  frames_dropped: number | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  pli: number | null;
  fir: number | null;
  nack: number | null;
  /** `decoderImplementation`, "-" when the browser does not say. */
  decoder: string;
  decode_ms_total: number | null;
  /** How long before this report the last RTP packet arrived (same clock as the report). */
  last_packet_age_ms: number | null;
}

export interface ScreenCodecStats {
  mime: string;
  pt: number | null;
  /** Only `profile-level-id` and `packetization-mode` from the fmtp line, or "-". */
  fmtp: string;
}

/** The selected ICE candidate pair: its state and round trip, and only the TYPES and protocol of its two ends. */
export interface ScreenPairStats {
  state: string;
  rtt_ms: number | null;
  bytes_received: number | null;
  local_type: string;
  remote_type: string;
  proto: string;
}

export interface ScreenStatsSnapshot {
  /** Wall-clock ms when the report was read. */
  at: number;
  inbound: ScreenInboundStats | null;
  codec: ScreenCodecStats | null;
  pair: ScreenPairStats | null;
  dtls: string | null;
  ice: string | null;
  dc_sent: number | null;
  dc_received: number | null;
}

/** RTCStatsReport and a Map both have this. */
export interface StatsReportLike {
  forEach(callback: (value: unknown) => void): void;
}

type Entry = Record<string, unknown>;

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function ms(seconds: unknown, digits = 1): number | null {
  const s = num(seconds);
  if (s === null) return null;
  const f = 10 ** digits;
  return Math.round(s * 1000 * f) / f;
}
/** A lower-case state word ("succeeded", "in-progress", "connected"), else "?". */
function word(v: unknown): string {
  const s = str(v);
  return s !== null && /^[a-z][a-z-]{0,23}$/.test(s) ? s : "?";
}
const CANDIDATE_TYPES = new Set(["host", "srflx", "prflx", "relay"]);
function candidateType(v: unknown): string {
  const s = str(v);
  return s !== null && CANDIDATE_TYPES.has(s) ? s : "?";
}
function proto(v: unknown): string {
  const s = str(v)?.toLowerCase() ?? null;
  return s === "udp" || s === "tcp" ? s : "?";
}
/** "video/H264", "video/VP8": a mime of letters, digits and dashes, else "?". */
function mime(v: unknown): string {
  const s = str(v);
  return s !== null && /^[a-z]+\/[A-Za-z0-9.-]{1,24}$/.test(s) ? s : "?";
}
/** A decoder name such as "VideoToolbox" or "libvpx (fallback)": short and plain, else "?"; absent = "-". */
function decoderName(v: unknown): string {
  const s = str(v);
  if (s === null || s === "") return "-";
  return /^[A-Za-z0-9 _.(),-]{1,40}$/.test(s) ? s : "?";
}

/**
 * `profile-level-id` (6 hex) and `packetization-mode` (0-2) only, in that order; every other fmtp parameter (such as
 * `sprop-parameter-sets`, the stream's own SPS and PPS) is left out.
 */
export function fmtpSummary(line: unknown): string {
  const s = str(line);
  if (s === null) return "-";
  let profile: string | null = null;
  let mode: string | null = null;
  for (const part of s.split(";")) {
    const [rawKey, rawValue] = part.split("=", 2);
    const key = rawKey?.trim().toLowerCase();
    const value = rawValue?.trim() ?? "";
    if (key === "profile-level-id" && /^[0-9a-fA-F]{6}$/.test(value)) profile = value.toLowerCase();
    if (key === "packetization-mode" && /^[0-2]$/.test(value)) mode = value;
  }
  const out = [profile !== null ? `profile-level-id=${profile}` : null, mode !== null ? `packetization-mode=${mode}` : null].filter((x) => x !== null);
  return out.length > 0 ? out.join(";") : "-";
}

function isVideoInbound(e: Entry): boolean {
  return e.type === "inbound-rtp" && (e.kind === "video" || e.mediaType === "video");
}

/**
 * The whitelisted reduction of a `getStats()` report. Reads named fields of five entry types (inbound-rtp video, its
 * codec, the selected candidate pair and the TYPES of its two candidates, the transport, the data channel) and builds
 * a new object from them; every other entry and field is ignored.
 */
export function reduceStats(report: StatsReportLike, at: number): ScreenStatsSnapshot {
  const byId = new Map<string, Entry>();
  const all: Entry[] = [];
  report.forEach((value) => {
    if (!value || typeof value !== "object") return;
    const e = value as Entry;
    all.push(e);
    if (typeof e.id === "string") byId.set(e.id, e);
  });

  // inbound video: the entry that received the most packets (one per SSRC; RTX is not a separate entry)
  let rtp: Entry | null = null;
  for (const e of all) if (isVideoInbound(e) && (rtp === null || (num(e.packetsReceived) ?? 0) > (num(rtp.packetsReceived) ?? 0))) rtp = e;
  let inbound: ScreenInboundStats | null = null;
  let codec: ScreenCodecStats | null = null;
  if (rtp) {
    const lastAt = num(rtp.lastPacketReceivedTimestamp);
    const reportAt = num(rtp.timestamp);
    inbound = {
      packets_received: num(rtp.packetsReceived),
      bytes_received: num(rtp.bytesReceived),
      packets_lost: num(rtp.packetsLost),
      jitter_ms: ms(rtp.jitter),
      frames_received: num(rtp.framesReceived),
      frames_decoded: num(rtp.framesDecoded),
      key_frames_decoded: num(rtp.keyFramesDecoded),
      frames_dropped: num(rtp.framesDropped),
      fps: num(rtp.framesPerSecond),
      width: num(rtp.frameWidth),
      height: num(rtp.frameHeight),
      pli: num(rtp.pliCount),
      fir: num(rtp.firCount),
      nack: num(rtp.nackCount),
      decoder: decoderName(rtp.decoderImplementation),
      decode_ms_total: ms(rtp.totalDecodeTime, 0),
      last_packet_age_ms: lastAt !== null && reportAt !== null ? Math.max(0, Math.round(reportAt - lastAt)) : null,
    };
    const c = typeof rtp.codecId === "string" ? byId.get(rtp.codecId) : undefined;
    if (c && c.type === "codec") codec = { mime: mime(c.mimeType), pt: num(c.payloadType), fmtp: fmtpSummary(c.sdpFmtpLine) };
  }

  // the transport, and the pair it selected (else the nominated, succeeded pair; else one flagged `selected`)
  let transport: Entry | null = null;
  for (const e of all) if (e.type === "transport" && (transport === null || typeof e.selectedCandidatePairId === "string")) transport = e;
  let pairEntry: Entry | null = null;
  if (transport && typeof transport.selectedCandidatePairId === "string") pairEntry = byId.get(transport.selectedCandidatePairId) ?? null;
  if (!pairEntry) pairEntry = all.find((e) => e.type === "candidate-pair" && e.nominated === true && e.state === "succeeded") ?? all.find((e) => e.type === "candidate-pair" && e.selected === true) ?? null;
  let pair: ScreenPairStats | null = null;
  if (pairEntry && pairEntry.type === "candidate-pair") {
    const local = typeof pairEntry.localCandidateId === "string" ? byId.get(pairEntry.localCandidateId) : undefined;
    const remote = typeof pairEntry.remoteCandidateId === "string" ? byId.get(pairEntry.remoteCandidateId) : undefined;
    pair = {
      state: word(pairEntry.state),
      rtt_ms: ms(pairEntry.currentRoundTripTime, 0),
      bytes_received: num(pairEntry.bytesReceived),
      local_type: candidateType(local?.candidateType),
      remote_type: candidateType(remote?.candidateType),
      proto: proto(local?.protocol),
    };
  }

  let dcSent: number | null = null;
  let dcReceived: number | null = null;
  for (const e of all) {
    if (e.type !== "data-channel") continue;
    dcSent = (dcSent ?? 0) + (num(e.messagesSent) ?? 0);
    dcReceived = (dcReceived ?? 0) + (num(e.messagesReceived) ?? 0);
  }

  return {
    at,
    inbound,
    codec,
    pair,
    dtls: transport ? word(transport.dtlsState) : null,
    ice: transport && transport.iceState !== undefined ? word(transport.iceState) : null,
    dc_sent: dcSent,
    dc_received: dcReceived,
  };
}

/** The flat, log-safe summary used by `screen.no_picture` (no nested objects, keys REDACT_KEY leaves alone). */
export function statsSummary(s: ScreenStatsSnapshot | null): Record<string, string | number | boolean | null> {
  if (!s) return { stats: false };
  const i = s.inbound;
  return {
    rtp_packets: i?.packets_received ?? null,
    rtp_bytes: i?.bytes_received ?? null,
    lost: i?.packets_lost ?? null,
    last_rtp_age_ms: i?.last_packet_age_ms ?? null,
    frames_received: i?.frames_received ?? null,
    frames_decoded: i?.frames_decoded ?? null,
    key_frames_decoded: i?.key_frames_decoded ?? null,
    frames_dropped: i?.frames_dropped ?? null,
    size: i && i.width !== null && i.height !== null ? `${i.width}x${i.height}` : null,
    pli: i?.pli ?? null,
    nack: i?.nack ?? null,
    decoder: i?.decoder ?? null,
    codec: s.codec?.mime ?? null,
    fmtp: s.codec?.fmtp ?? null,
    pair: s.pair?.state ?? null,
    local_type: s.pair?.local_type ?? null,
    remote_type: s.pair?.remote_type ?? null,
    proto: s.pair?.proto ?? null,
    rtt_ms: s.pair?.rtt_ms ?? null,
    dtls: s.dtls,
    dc_sent: s.dc_sent,
    dc_received: s.dc_received,
  };
}

// ---- the <video> element, metadata only --------------------------------------------------------------------------------

export interface ScreenMediaFacts {
  at: number;
  ready_state: number | null;
  network_state: number | null;
  paused: boolean | null;
  ended: boolean | null;
  width: number | null;
  height: number | null;
  current_time: number | null;
  /** `MediaError.code`, or null. */
  error: number | null;
  total_frames: number | null;
  dropped_frames: number | null;
  /** The last `play()`: none, pending, resolved, rejected:<DOMException name> or threw:<name>. */
  play: string;
  /** The browser has `requestVideoFrameCallback` (else `timeupdate` stands in for it). */
  rvfc: boolean;
  /** Frame callbacks (or timeupdate events) since the stream was attached. */
  frame_ticks: number;
  /** ms from attaching the stream to the first tick. */
  first_tick_ms: number | null;
  stream_active: boolean | null;
  track_state: string | null;
  track_muted: boolean | null;
  track_enabled: boolean | null;
}

/** The structural subset of HTMLVideoElement the probe reads (a fake in tests). */
export interface VideoLike {
  readyState?: number;
  networkState?: number;
  paused?: boolean;
  ended?: boolean;
  videoWidth?: number;
  videoHeight?: number;
  currentTime?: number;
  error?: { code?: number } | null;
  getVideoPlaybackQuality?: () => { totalVideoFrames?: number; droppedVideoFrames?: number };
}

/** The structural subset of MediaStream the probe reads: flags only. */
export interface MediaLike {
  active?: boolean;
  getVideoTracks?: () => Array<{ readyState?: string; muted?: boolean; enabled?: boolean }>;
}

export interface FrameCounters {
  play: string;
  rvfc: boolean;
  ticks: number;
  firstTickMs: number | null;
}

/** "rejected:NotAllowedError": a DOMException's name, else the error kind; never its message. */
export function playOutcome(kind: "rejected" | "threw", e: unknown): string {
  const name = e && typeof e === "object" && typeof (e as { name?: unknown }).name === "string" ? (e as { name: string }).name : "Error";
  return `${kind}:${/^[A-Za-z]{1,40}$/.test(name) ? name : "Error"}`;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

/** What the `<video>` and the stream say about themselves right now (no pixel is read; rules.screen_content). */
export function probeVideo(video: VideoLike | null, media: MediaLike | null, counters: FrameCounters, at: number): ScreenMediaFacts {
  let total: number | null = null;
  let dropped: number | null = null;
  try {
    const q = video?.getVideoPlaybackQuality?.();
    total = num(q?.totalVideoFrames);
    dropped = num(q?.droppedVideoFrames);
  } catch {
    /* not supported */
  }
  let track: { readyState?: string; muted?: boolean; enabled?: boolean } | undefined;
  try {
    track = media?.getVideoTracks?.()[0];
  } catch {
    track = undefined;
  }
  const t = num(video?.currentTime);
  return {
    at,
    ready_state: num(video?.readyState),
    network_state: num(video?.networkState),
    paused: bool(video?.paused),
    ended: bool(video?.ended),
    width: num(video?.videoWidth),
    height: num(video?.videoHeight),
    current_time: t === null ? null : Math.round(t * 10) / 10,
    error: num(video?.error?.code),
    total_frames: total,
    dropped_frames: dropped,
    play: counters.play,
    rvfc: counters.rvfc,
    frame_ticks: counters.ticks,
    first_tick_ms: counters.firstTickMs,
    stream_active: media ? bool(media.active) : null,
    track_state: track && typeof track.readyState === "string" ? word(track.readyState) : null,
    track_muted: track ? bool(track.muted) : null,
    track_enabled: track ? bool(track.enabled) : null,
  };
}

/** The flat, log-safe summary of the `<video>` facts for `screen.no_picture`. */
export function mediaSummary(m: ScreenMediaFacts | null): Record<string, string | number | boolean | null> {
  if (!m) return { video: false };
  return { ready: m.ready_state, paused: m.paused, video_size: m.width !== null && m.height !== null ? `${m.width}x${m.height}` : null, play: m.play, frame_ticks: m.frame_ticks, track_muted: m.track_muted, stream_active: m.stream_active };
}

// ---- the client's facts, and the test-build readout ------------------------------------------------------------------

export interface ScreenChannelFacts {
  open: boolean;
  hb_sent: number;
  received: number;
  dropped: number;
}

export interface CodecFallback {
  from: "h264";
  to: "vp8";
  reason: "not_decoded";
}

export interface ScreenClientFacts {
  stats: ScreenStatsSnapshot | null;
  media: ScreenMediaFacts | null;
  timeline: ScreenTimeline;
  channel: ScreenChannelFacts;
  /** DTLS came up in the current (or last) attempt. */
  transport_connected: boolean;
  codec_fallback: CodecFallback | null;
}

const yesNo = (v: boolean | null | undefined): string => (v === true ? "yes" : v === false ? "no" : "-");
const num0 = (v: number | null | undefined): string => (v === null || v === undefined ? "-" : String(v));
const mark = (v: number | null): string => (v === null ? "no" : `yes +${v} ms`);
/** "yes (4)" / "no (1)" for a count or level against a threshold; "-" when unknown. */
const atLeast = (v: number | null | undefined, min: number): string => (v === null || v === undefined ? "-" : `${yesNo(v >= min)} (${v})`);
const size = (w: number | null | undefined, h: number | null | undefined): string => (w === null || w === undefined || h === null || h === undefined ? "-" : `${w}x${h}`);

/** The readout's lines: a gate checklist in pipeline order (the first "no" is the stage that failed), then the numbers. */
export function readoutLines(state: Pick<ScreenSessionState, "phase" | "verified" | "stream" | "retries" | "stalled" | "encoder" | "path">, f: ScreenClientFacts): string[] {
  const i = f.stats?.inbound ?? null;
  const m = f.media;
  const c = f.stats?.codec ?? null;
  const p = f.stats?.pair ?? null;
  const unmuted = m?.track_muted === null || m?.track_muted === undefined ? null : !m.track_muted;
  const firstRtp = f.timeline.first_rtp !== null ? mark(f.timeline.first_rtp) : (i?.packets_received ?? 0) > 0 ? "yes" : "no";
  return [
    `verified: ${yesNo(state.verified)}`,
    `track seen: ${mark(f.timeline.track)}`,
    `stream set: ${yesNo(state.stream !== null)}`,
    `DTLS: ${mark(f.timeline.dtls_connected)}`,
    `data channel: ${f.channel.open ? "open" : "no"}, hb ${f.channel.hb_sent}, in ${f.channel.received}`,
    `first RTP: ${firstRtp}`,
    `track unmuted: ${yesNo(unmuted)}`,
    `key frame decoded: ${atLeast(i?.key_frames_decoded, 1)}`,
    `frames decoded: ${num0(i?.frames_decoded)}`,
    `readyState>=2: ${atLeast(m?.ready_state, 2)}`,
    `play(): ${m?.play ?? "-"}`,
    `rVFC ticks: ${m ? `${m.frame_ticks}${m.rvfc ? "" : " (timeupdate)"}` : "-"}`,
    `phase: ${state.phase}`,
    `rtp pkts ${num0(i?.packets_received)} bytes ${num0(i?.bytes_received)} lost ${num0(i?.packets_lost)} jitter ${num0(i?.jitter_ms)} ms age ${num0(i?.last_packet_age_ms)} ms`,
    `frames recv ${num0(i?.frames_received)} dec ${num0(i?.frames_decoded)} key ${num0(i?.key_frames_decoded)} drop ${num0(i?.frames_dropped)} fps ${num0(i?.fps)} ${size(i?.width, i?.height)}`,
    `codec ${c?.mime ?? "-"} pt ${num0(c?.pt)} ${c?.fmtp ?? "-"} decoder ${i?.decoder ?? "-"}`,
    `pli ${num0(i?.pli)} fir ${num0(i?.fir)} nack ${num0(i?.nack)}`,
    `pair ${p?.state ?? "-"} ${p ? `${p.local_type}/${p.remote_type}` : "-"} ${p?.proto ?? "-"} rtt ${num0(p?.rtt_ms)} ms dtls ${f.stats?.dtls ?? "-"}`,
    `video ${size(m?.width, m?.height)} paused ${yesNo(m?.paused)} t ${num0(m?.current_time)} err ${num0(m?.error)} quality ${num0(m?.total_frames)}/${num0(m?.dropped_frames)} track ${m?.track_state ?? "-"} enabled ${yesNo(m?.track_enabled)} active ${yesNo(m?.stream_active)}`,
    `phase ${state.phase} retries ${state.retries} stalled ${yesNo(state.stalled)} encoder ${state.encoder ?? "-"} path ${state.path ?? "-"}${f.codec_fallback ? ` fallback ${f.codec_fallback.to}` : ""}`,
  ];
}

/** The release channels whose builds show the on-stage readout (deploy/, testkit/, ci.yml name them). */
export const READOUT_CHANNELS: readonly string[] = ["dev", "test", "ci", "staging"];

/**
 * The on-stage readout exists only in the development, test-kit, CI and staging builds (the founder tests staging):
 * an allow-list, so a plain `vite build`, an unset channel or a channel added later shows nothing to customers. Read
 * at call time, so a test can switch the channel.
 */
export function screenReadoutEnabled(): boolean {
  const channel = import.meta.env.VITE_DOME_RELEASE_CHANNEL;
  return typeof channel === "string" && READOUT_CHANNELS.includes(channel);
}

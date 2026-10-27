/**
 * Test doubles for the Screen view transport: a fake RTCPeerConnection and RTCDataChannel that implement exactly the
 * subset `lib/screen.ts` uses, and a PC identity key that signs answers the way the agent does. Test-only.
 *
 * The fake keeps the browsers' event order (webrtc-pc; WebKit PeerConnectionBackend::setRemoteDescriptionSucceeded):
 * `track` fires INSIDE `setRemoteDescription`, before its promise resolves; DTLS and the data channel come later
 * (`connect()`). Device round 2 (2026-10-27) showed what an out-of-order fake hides: the phone dropped the only track.
 */
import { buildScreenAnswerPayload, exportPublicJwk, generateControllerKeyPair, kidFromJwk, signScreenAnswer, type EcPublicJwk, type Envelope } from "@dome/protocol";

import type { PcKeyPin } from "../../src/lib/controllerKey.ts";
import type { DataChannelLike, PeerConnectionLike, PeerFactory, TrackEventLike, TransceiverLike } from "../../src/lib/screen.ts";

const FP = "40:41:42:43:44:45:46:47:48:49:4A:4B:4C:4D:4E:4F:50:51:52:53:54:55:56:57:58:59:5A:5B:5C:5D:5E:5F";
/** A receive-only offer the strict check accepts (CRLF, one fingerprint, video/audio/application). Placeholder addresses only. */
export const OFFER_SDP = ["v=0", "o=- 4611731400430051337 2 IN IP4 0.0.0.0", "s=-", "t=0 0", "a=group:BUNDLE 0 1 2", "m=video 9 UDP/TLS/RTP/SAVPF 96 97", "c=IN IP4 0.0.0.0", "a=ice-ufrag:ph0n", "a=ice-pwd:phonephonephonephonephon", `a=fingerprint:sha-256 ${FP}`, "a=setup:actpass", "a=mid:0", "a=recvonly", "a=rtcp-mux", "a=rtpmap:96 H264/90000", "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f", "a=rtpmap:97 VP8/90000", "m=audio 9 UDP/TLS/RTP/SAVPF 111", "c=IN IP4 0.0.0.0", "a=mid:1", "a=recvonly", "a=rtcp-mux", "a=rtpmap:111 opus/48000/2", "m=application 9 UDP/DTLS/SCTP webrtc-datachannel", "c=IN IP4 0.0.0.0", "a=mid:2", "a=sctp-port:5000", ""].join("\r\n");

const PC_FP = "80:81:82:83:84:85:86:87:88:89:8A:8B:8C:8D:8E:8F:90:91:92:93:94:95:96:97:98:99:9A:9B:9C:9D:9E:9F";
/** The PC's send-only answer with one DTLS fingerprint repeated per m-line (as aiortc writes it). */
export const ANSWER_SDP = ["v=0", "o=- 3900000000000000002 2 IN IP4 0.0.0.0", "s=-", "t=0 0", "a=group:BUNDLE 0 1 2", "m=video 9 UDP/TLS/RTP/SAVPF 97", "c=IN IP4 0.0.0.0", "a=ice-ufrag:pcpc", "a=ice-pwd:pcpcpcpcpcpcpcpcpcpcpcpc", `a=fingerprint:sha-256 ${PC_FP}`, "a=setup:active", "a=mid:0", "a=sendonly", "a=rtcp-mux", "a=rtpmap:97 VP8/90000", "m=audio 9 UDP/TLS/RTP/SAVPF 111", "c=IN IP4 0.0.0.0", `a=fingerprint:sha-256 ${PC_FP}`, "a=mid:1", "a=sendonly", "a=rtcp-mux", "a=rtpmap:111 opus/48000/2", "m=application 9 UDP/DTLS/SCTP webrtc-datachannel", "c=IN IP4 0.0.0.0", `a=fingerprint:sha-256 ${PC_FP}`, "a=mid:2", "a=sctp-port:5000", ""].join("\r\n");

/** A sentinel that must never appear in logs or diagnostics: the fingerprint of the PC's answer. */
export const SDP_SENTINEL = "80:81:82:83:84:85:86:87";

export class FakeDataChannel implements DataChannelLike {
  readyState: DataChannelLike["readyState"] = "connecting";
  readonly sent: string[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  constructor(
    readonly label: string,
    readonly init: { ordered: boolean },
  ) {}
  send(data: string): void {
    if (this.readyState !== "open") throw new Error("channel not open");
    this.sent.push(data);
  }
  close(): void {
    this.readyState = "closed";
    this.onclose?.({});
  }
  /** Test: the SCTP channel came up. */
  open(): void {
    this.readyState = "open";
    this.onopen?.({});
  }
  /** Test: the PC sent a message (an object is serialised; a string arrives verbatim, e.g. malformed JSON). */
  receive(message: unknown): void {
    this.onmessage?.({ data: typeof message === "string" ? message : JSON.stringify(message) });
  }
  messages(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

export class FakePeerConnection implements PeerConnectionLike {
  readonly transceivers: Array<{ kind: string; direction: string; codecs: RTCRtpCodec[] | null }> = [];
  readonly channels: FakeDataChannel[] = [];
  localDescription: { sdp: string } | null = null;
  readonly remoteDescriptions: Array<{ type: string; sdp: string }> = [];
  iceGatheringState: PeerConnectionLike["iceGatheringState"] = "new";
  connectionState = "new";
  iceConnectionState = "new";
  closed = false;
  /** Test knobs. */
  offerSdp = OFFER_SDP;
  gatherOnSetLocal = true;
  /** The answer's video track fires inside setRemoteDescription (false: a test fires it later with `fireTrack`). */
  trackOnAnswer = true;
  /** The streams the video track event carries ([] = a sender without `a=msid`). */
  trackStreams: MediaStream[] = [{ id: "fake-stream" } as unknown as MediaStream];
  /** What `getStats()` reports (entries keyed by their `id`, as RTCStatsReport is). */
  stats: Array<Record<string, unknown>> = [];
  getStatsCalls = 0;
  onicegatheringstatechange: ((ev: unknown) => void) | null = null;
  onconnectionstatechange: ((ev: unknown) => void) | null = null;
  oniceconnectionstatechange: ((ev: unknown) => void) | null = null;
  ontrack: ((ev: TrackEventLike) => void) | null = null;
  constructor(readonly config: { iceServers: RTCIceServer[] }) {}
  addTransceiver(kind: "video" | "audio", init: { direction: "recvonly" }): TransceiverLike {
    const entry = { kind, direction: init.direction, codecs: null as RTCRtpCodec[] | null };
    this.transceivers.push(entry);
    return {
      setCodecPreferences: (codecs) => {
        entry.codecs = codecs;
      },
    };
  }
  createDataChannel(label: string, init: { ordered: boolean }): DataChannelLike {
    const ch = new FakeDataChannel(label, init);
    this.channels.push(ch);
    return ch;
  }
  async createOffer(): Promise<{ type: string; sdp: string }> {
    return { type: "offer", sdp: this.offerSdp };
  }
  async setLocalDescription(d: { type?: string; sdp?: string }): Promise<void> {
    this.localDescription = { sdp: d.sdp ?? this.offerSdp };
    if (this.gatherOnSetLocal) this.completeGathering();
  }
  /** Browser order: the track event is dispatched before this promise resolves. */
  async setRemoteDescription(d: { type: "answer"; sdp: string }): Promise<void> {
    this.remoteDescriptions.push(d);
    if (this.trackOnAnswer) this.fireTrack();
  }
  async getStats(): Promise<Map<string, Record<string, unknown>>> {
    this.getStatsCalls += 1;
    return new Map(this.stats.map((e, i) => [typeof e.id === "string" ? e.id : `s${i}`, e]));
  }
  /** Test: a track event (the answer's video by default). */
  fireTrack(streams: readonly MediaStream[] = this.trackStreams, kind = "video"): void {
    this.ontrack?.({ streams, track: { kind } });
  }
  close(): void {
    this.closed = true;
    this.connectionState = "closed";
  }
  /** Test: every candidate gathered (vanilla ICE). */
  completeGathering(): void {
    this.iceGatheringState = "complete";
    this.onicegatheringstatechange?.({});
  }
  /** Test: DTLS connected with the certificate the PC signed, and the data channel opens (the track came with the answer). */
  connect(): void {
    this.connectionState = "connected";
    this.iceConnectionState = "connected";
    this.onconnectionstatechange?.({});
    this.channels[0]?.open();
  }
  /** Test: the transport failed (network change, no path). */
  fail(): void {
    this.connectionState = "failed";
    this.iceConnectionState = "failed";
    this.onconnectionstatechange?.({});
  }
  get channel(): FakeDataChannel {
    const ch = this.channels[0];
    if (!ch) throw new Error("no data channel was created");
    return ch;
  }
}

/** `knobs` apply to every peer created after they are set (e.g. an offer the strict check refuses). */
export function fakePeerFactory(): { factory: PeerFactory; peers: FakePeerConnection[]; knobs: { offerSdp: string; gatherOnSetLocal: boolean; stats: Array<Record<string, unknown>> } } {
  const peers: FakePeerConnection[] = [];
  const knobs = { offerSdp: OFFER_SDP, gatherOnSetLocal: true, stats: [] as Array<Record<string, unknown>> };
  return {
    peers,
    knobs,
    factory: (config) => {
      const pc = new FakePeerConnection(config);
      pc.offerSdp = knobs.offerSdp;
      pc.gatherOnSetLocal = knobs.gatherOnSetLocal;
      pc.stats = knobs.stats;
      peers.push(pc);
      return pc;
    },
  };
}

/** A PC identity key for tests: signs answers the way the agent does, and gives the pin the phone would hold for it. */
export class FakePcKey {
  private constructor(
    readonly pair: CryptoKeyPair,
    readonly jwk: EcPublicJwk,
    readonly kid: string,
  ) {}
  static async create(): Promise<FakePcKey> {
    const pair = await generateControllerKeyPair();
    const jwk = await exportPublicJwk(pair.publicKey);
    return new FakePcKey(pair, jwk, await kidFromJwk(jwk));
  }
  pin(pcId: string): PcKeyPin {
    return { pc_id: pcId, pc_kid: this.kid, pc_public_jwk: this.jwk, pinned_at: "2026-10-21T12:00:00.000Z" };
  }
  async signAnswer(input: { accountId: string; pcId: string; controllerId: string; commandId: string; screenSessionId: string; offerSdp: string; answerSdp?: string; now?: Date; tamper?: (payload: Record<string, unknown>) => void }): Promise<Envelope> {
    const payload = await buildScreenAnswerPayload({ accountId: input.accountId, pcId: input.pcId, controllerId: input.controllerId, commandId: input.commandId, screenSessionId: input.screenSessionId, offerSdp: input.offerSdp, answerSdp: input.answerSdp ?? ANSWER_SDP, ...(input.now ? { now: input.now } : {}) });
    const raw = payload as unknown as Record<string, unknown>;
    input.tamper?.(raw);
    return signScreenAnswer(this.pair.privateKey, this.pair.publicKey, raw as unknown as typeof payload);
  }
}

/** Addresses, ports, ICE fragments, a TURN/STUN name, the certificate and the SPS in this report: none may survive the whitelist. */
export const STATS_SECRETS = ["192.0.2.44", "54321", "10.0.0.7", "61000", "198.51.100.9", "50000", "stun.example.net", "ufragph0n", "ufragpcpc", SDP_SENTINEL, "SECRETCERTBYTES", "sprop", "Z0LAH9oB", "aiortc-track-uuid", "wifi"];

/**
 * A `getStats()` report shaped like Safari's (libwebrtc's field names): the selected pair through the transport, both
 * candidates WITH their addresses, the PC's certificate WITH its fingerprint, the H.264 codec with sprop-parameter-sets
 * in its fmtp line, the video and audio inbound-rtp entries and the data channel.
 */
export function safariLikeStats(o: { packets?: number; received?: number; decoded?: number; keyDecoded?: number; mime?: string; fmtp?: string } = {}): Array<Record<string, unknown>> {
  return [
    { id: "T01", type: "transport", timestamp: 1000, dtlsState: "connected", iceState: "connected", selectedCandidatePairId: "CPab", localCertificateId: "CFlocal", remoteCertificateId: "CFremote", tlsVersion: "FEFD", srtpCipher: "AES_CM_128_HMAC_SHA1_80", bytesReceived: 50_000 },
    { id: "CPab", type: "candidate-pair", timestamp: 1000, transportId: "T01", localCandidateId: "ILx", remoteCandidateId: "IRy", state: "succeeded", nominated: true, currentRoundTripTime: 0.012, bytesReceived: 48_000 },
    { id: "CPzz", type: "candidate-pair", timestamp: 1000, transportId: "T01", localCandidateId: "ILx", remoteCandidateId: "IRz", state: "failed", nominated: false },
    { id: "ILx", type: "local-candidate", transportId: "T01", address: "192.0.2.44", port: 54321, protocol: "udp", candidateType: "srflx", relatedAddress: "10.0.0.7", relatedPort: 61000, usernameFragment: "ufragph0n", url: "stun:stun.example.net:3478", networkType: "wifi" },
    { id: "IRy", type: "remote-candidate", transportId: "T01", address: "198.51.100.9", port: 50000, protocol: "udp", candidateType: "host", usernameFragment: "ufragpcpc" },
    { id: "CFremote", type: "certificate", fingerprint: PC_FP, fingerprintAlgorithm: "sha-256", base64Certificate: "MIIBSECRETCERTBYTES" },
    { id: "CIT01_98", type: "codec", transportId: "T01", payloadType: 98, mimeType: o.mime ?? "video/H264", clockRate: 90000, sdpFmtpLine: o.fmtp ?? "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42E01F;sprop-parameter-sets=Z0LAH9oBQBbsBEAAAAMAQAAADyPGDKg=,aM4G4g==" },
    { id: "IT01V123", type: "inbound-rtp", kind: "video", timestamp: 1000, ssrc: 123, codecId: "CIT01_98", transportId: "T01", trackIdentifier: "aiortc-track-uuid", mid: "0", packetsReceived: o.packets ?? 400, bytesReceived: 420_000, packetsLost: 2, jitter: 0.0031, framesReceived: o.received ?? 30, framesDecoded: o.decoded ?? 28, keyFramesDecoded: o.keyDecoded ?? 1, framesDropped: 0, framesPerSecond: 30, frameWidth: 1280, frameHeight: 720, pliCount: 1, firCount: 0, nackCount: 3, decoderImplementation: "VideoToolbox", totalDecodeTime: 0.42, lastPacketReceivedTimestamp: 960 },
    { id: "IT01A456", type: "inbound-rtp", kind: "audio", timestamp: 1000, packetsReceived: 0 },
    { id: "D1", type: "data-channel", label: "dome-screen-v1", dataChannelIdentifier: 1, state: "open", messagesSent: 12, bytesSent: 144, messagesReceived: 3, bytesReceived: 300 },
    { id: "P", type: "peer-connection", dataChannelsOpened: 1 },
  ];
}

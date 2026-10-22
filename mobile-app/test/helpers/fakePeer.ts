/**
 * Test doubles for the Screen view transport: a fake RTCPeerConnection and RTCDataChannel that implement exactly the
 * subset `lib/screen.ts` uses, and a PC identity key that signs answers the way the agent does. Test-only.
 */
import { buildScreenAnswerPayload, exportPublicJwk, generateControllerKeyPair, kidFromJwk, signScreenAnswer, type EcPublicJwk, type Envelope } from "@dome/protocol";

import type { PcKeyPin } from "../../src/lib/controllerKey.ts";
import type { DataChannelLike, PeerConnectionLike, PeerFactory, TransceiverLike } from "../../src/lib/screen.ts";

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
  onicegatheringstatechange: ((ev: unknown) => void) | null = null;
  onconnectionstatechange: ((ev: unknown) => void) | null = null;
  oniceconnectionstatechange: ((ev: unknown) => void) | null = null;
  ontrack: ((ev: { streams: readonly MediaStream[]; track: { kind: string } }) => void) | null = null;
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
  async setRemoteDescription(d: { type: "answer"; sdp: string }): Promise<void> {
    this.remoteDescriptions.push(d);
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
  /** Test: DTLS connected with the certificate the PC signed; the video track and the channel come up. */
  connect(stream: MediaStream = { id: "fake-stream" } as unknown as MediaStream): void {
    this.connectionState = "connected";
    this.iceConnectionState = "connected";
    this.onconnectionstatechange?.({});
    this.ontrack?.({ streams: [stream], track: { kind: "video" } });
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
export function fakePeerFactory(): { factory: PeerFactory; peers: FakePeerConnection[]; knobs: { offerSdp: string; gatherOnSetLocal: boolean } } {
  const peers: FakePeerConnection[] = [];
  const knobs = { offerSdp: OFFER_SDP, gatherOnSetLocal: true };
  return {
    peers,
    knobs,
    factory: (config) => {
      const pc = new FakePeerConnection(config);
      pc.offerSdp = knobs.offerSdp;
      pc.gatherOnSetLocal = knobs.gatherOnSetLocal;
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

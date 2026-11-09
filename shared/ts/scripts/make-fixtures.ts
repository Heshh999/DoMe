/**
 * Generate shared/protocol/fixtures/es256-typescript.json with the TypeScript signer so the
 * Python verifier can check it (and vice versa). Uses the committed TEST keys only (controller and PC).
 * Since protocol 1.4 the file also carries the Screen view vectors: a PC-signed screen answer the phone must accept,
 * two it must refuse, one it must call PROTOCOL_INCOMPATIBLE, and the pairing pc_key_mac (rules.pc_key_pinning).
 * Since protocol 1.5 it also writes shared/protocol/fixtures/screen-dc-cases.json, the data-channel messages both libraries
 * must accept or refuse, byte for byte as shared/python/scripts/make_fixtures.py writes it.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { crc32, deflateSync } from "node:zlib";

import { challengeDigest, commandDigest, pairingCodeHandle, pairingVerificationCode, pcKeyMac } from "../src/digest.ts";
import { ECDSA_P256, exportPublicJwk, kidFromJwk } from "../src/keys.ts";
import { PROTOCOL_VERSION } from "../src/registry.ts";
import { buildScreenAnswerPayload, offerDigest, sdpFingerprints } from "../src/screen.ts";
import { signPayload } from "../src/signing.ts";
import { dumpsCompact } from "../src/strictJson.ts";
import { parseVersion } from "../src/versions.ts";

const here = dirname(fileURLToPath(import.meta.url));
const fixtureDir = resolve(here, "../../protocol/fixtures");
const pem = readFileSync(resolve(fixtureDir, "test-controller-key.pem"), "utf8");
const der = Buffer.from(pem.replace(/-----[A-Z ]+-----/g, "").replace(/\s+/g, ""), "base64");

async function loadTestKey(file: string): Promise<{ privateKey: CryptoKey; publicKey: CryptoKey }> {
  const pemText = readFileSync(resolve(fixtureDir, file), "utf8");
  const bytes = Buffer.from(pemText.replace(/-----[A-Z ]+-----/g, "").replace(/\s+/g, ""), "base64");
  const privateKey = await crypto.subtle.importKey("pkcs8", bytes, ECDSA_P256, false, ["sign"]);
  const extractable = await crypto.subtle.importKey("pkcs8", bytes, ECDSA_P256, true, ["sign"]);
  const fullJwk = (await crypto.subtle.exportKey("jwk", extractable)) as JsonWebKey;
  const publicJwk: JsonWebKey = { kty: fullJwk.kty!, crv: fullJwk.crv!, x: fullJwk.x!, y: fullJwk.y!, ext: true };
  const publicKey = await crypto.subtle.importKey("jwk", publicJwk, ECDSA_P256, true, ["verify"]);
  return { privateKey, publicKey };
}

const { privateKey, publicKey } = await loadTestKey("test-controller-key.pem");
const jwk = await exportPublicJwk(publicKey);
const kid = await kidFromJwk(jwk);
const pcKeys = await loadTestKey("test-pc-key.pem");
const pcJwk = await exportPublicJwk(pcKeys.publicKey);
const pcKid = await kidFromJwk(pcJwk);

const PAIRING_CODE = "7Q3K9-M2XVB-5HTC8-D4RNW";
const PAIRING_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const PC_ID = "33333333-3333-4333-8333-333333333333";
const ACCOUNT_ID = "11111111-1111-4111-8111-111111111111";
const CONTROLLER_ID = "22222222-2222-4222-8222-222222222222";
const SCREEN_COMMAND_ID = "9b1c2d3e-4a5b-4c6d-8e7f-901234567890";
const SCREEN_SESSION_ID = "U2NyZWVuU2Vzc2lvbjAyVF";
const SCREEN_NOW = new Date("2026-10-21T12:05:00.000Z");

// Minimal but shaped like what the devices exchange (test data only; placeholder addresses). The PC answers with VP8 here
// so both fixture files together cover both codecs.
const hex = (from: number): string => Array.from({ length: 32 }, (_, i) => (from + i).toString(16).padStart(2, "0").toUpperCase()).join(":");
const OFFER_SDP =
  [
    "v=0",
    "o=- 4611731400430051337 2 IN IP4 0.0.0.0",
    "s=-",
    "t=0 0",
    "a=group:BUNDLE 0 1 2",
    "a=msid-semantic: WMS",
    "m=video 9 UDP/TLS/RTP/SAVPF 96 97",
    "c=IN IP4 0.0.0.0",
    "a=ice-ufrag:ph0n",
    "a=ice-pwd:phonephonephonephonephon",
    `a=fingerprint:sha-256 ${hex(0x40)}`,
    "a=setup:actpass",
    "a=mid:0",
    "a=recvonly",
    "a=rtcp-mux",
    "a=rtpmap:96 H264/90000",
    "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
    "a=rtpmap:97 VP8/90000",
    "m=audio 9 UDP/TLS/RTP/SAVPF 111",
    "c=IN IP4 0.0.0.0",
    "a=mid:1",
    "a=recvonly",
    "a=rtcp-mux",
    "a=rtpmap:111 opus/48000/2",
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel",
    "c=IN IP4 0.0.0.0",
    "a=mid:2",
    "a=sctp-port:5000",
    "",
  ].join("\r\n");
const PC_FINGERPRINT = hex(0x80);
const ANSWER_SDP =
  [
    "v=0",
    "o=- 3900000000000000002 2 IN IP4 0.0.0.0",
    "s=-",
    "t=0 0",
    "a=group:BUNDLE 0 1 2",
    "a=msid-semantic:WMS *",
    "m=video 9 UDP/TLS/RTP/SAVPF 97",
    "c=IN IP4 0.0.0.0",
    "a=ice-ufrag:pcpc",
    "a=ice-pwd:pcpcpcpcpcpcpcpcpcpcpcpc",
    `a=fingerprint:sha-256 ${PC_FINGERPRINT}`,
    "a=setup:active",
    "a=mid:0",
    "a=sendonly",
    "a=rtcp-mux",
    "a=rtpmap:97 VP8/90000",
    "m=audio 9 UDP/TLS/RTP/SAVPF 111",
    "c=IN IP4 0.0.0.0",
    `a=fingerprint:sha-256 ${PC_FINGERPRINT}`,
    "a=mid:1",
    "a=sendonly",
    "a=rtcp-mux",
    "a=rtpmap:111 opus/48000/2",
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel",
    "c=IN IP4 0.0.0.0",
    `a=fingerprint:sha-256 ${PC_FINGERPRINT}`,
    "a=mid:2",
    "a=sctp-port:5000",
    "",
  ].join("\r\n");

const payloads = [
  '{"type":"command","protocol_version":"1.0","command_id":"8f1c2d3e-4a5b-4c6d-8e7f-901234567890","account_id":"11111111-1111-4111-8111-111111111111","controller_id":"22222222-2222-4222-8222-222222222222","target_pc_id":"33333333-3333-4333-8333-333333333333","action":"youtube.set_paused","params":{"paused":true},"target":{"browser_instance_id":"bi_test0001","tab_id":7,"tab_token":"dGFiLXRva2VuLTAyQUJDRE"},"issued_at":"2026-10-08T12:00:00.000Z","expires_at":"2026-10-08T12:00:30.000Z","nonce":"AgICAgICAgICAgICAgICAg"}',
  '{"type":"confirmation","protocol_version":"1.0","command_id":"8f1c2d3e-4a5b-4c6d-8e7f-901234567890","challenge_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","challenge_digest":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","controller_id":"22222222-2222-4222-8222-222222222222","target_pc_id":"33333333-3333-4333-8333-333333333333","decision":"approve","issued_at":"2026-10-08T12:00:05.000Z","expires_at":"2026-10-08T12:01:05.000Z","nonce":"AwMDAwMDAwMDAwMDAwMDAw"}',
  '{"emoji":"🎬","text":"Ünïcödé — ok"}',
];

const cases: unknown[] = [];
for (const payload of payloads) {
  const envelope = await signPayload(privateKey, publicKey, payload);
  cases.push({ payload, envelope, expect: "valid", command_digest: await commandDigest(payload) });
}
const base = (cases[0] as { envelope: Record<string, unknown> }).envelope;
const tamperedPayload = (base.payload as string).replace('"paused":true', '"paused":false');
cases.push({ payload: tamperedPayload, envelope: { ...base, payload: tamperedPayload }, expect: "SIGNATURE_INVALID" });
cases.push({ payload: base.payload, envelope: { ...base, v: 2 }, expect: "PROTOCOL_INCOMPATIBLE" });
cases.push({ payload: base.payload, envelope: { ...base, kid: "A".repeat(43) }, expect: "UNKNOWN_KEY" });

const challengeText =
  '{"challenge_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","command_id":"8f1c2d3e-4a5b-4c6d-8e7f-901234567890","controller_id":"22222222-2222-4222-8222-222222222222","pc_id":"33333333-3333-4333-8333-333333333333","action":"app.close","params":{},"target":{"app_id":"notepad"},"target_state_digest":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","issued_at":"2026-10-08T12:00:00.000Z","expires_at":"2026-10-08T12:01:00.000Z","display":{"pc_name":"Wohnzimmer-PC","action_label":"Close Notepad","detail":"Untitled — Notepad / 📝"}}';
// ----- protocol 1.4: the PC-signed screen answer and the pairing pc_key_mac --------------------------------------
const [major, minor] = parseVersion(PROTOCOL_VERSION)!;
const newerStamp = `${major}.${minor + 1}`; // one MINOR above this library's version: the phone must refuse it
const answer = await buildScreenAnswerPayload({
  accountId: ACCOUNT_ID,
  pcId: PC_ID,
  controllerId: CONTROLLER_ID,
  commandId: SCREEN_COMMAND_ID,
  screenSessionId: SCREEN_SESSION_ID,
  offerSdp: OFFER_SDP,
  answerSdp: ANSWER_SDP,
  protocolVersion: PROTOCOL_VERSION,
  now: SCREEN_NOW,
});
const answerText = dumpsCompact(answer);
const good = await signPayload(pcKeys.privateKey, pcKeys.publicKey, answerText);
const tampered = { ...good, payload: answerText.replace("a=setup:active", "a=setup:passive") };
const byControllerKey = await signPayload(privateKey, publicKey, answerText); // right payload, wrong (unpinned) signer
const newer = await signPayload(pcKeys.privateKey, pcKeys.publicKey, dumpsCompact({ ...answer, protocol_version: newerStamp }));
const screen = {
  $comment: "Protocol 1.4+. The newer-MINOR case is stamped one MINOR above this library's version. The phone verifies these with the PC key pinned at pairing (pc_public_jwk) at `now`.",
  pc_public_jwk: pcJwk,
  pc_kid: pcKid,
  account_id: ACCOUNT_ID,
  controller_id: CONTROLLER_ID,
  pc_id: PC_ID,
  command_id: SCREEN_COMMAND_ID,
  screen_session_id: SCREEN_SESSION_ID,
  offer_sdp: OFFER_SDP,
  offer_digest: await offerDigest(OFFER_SDP),
  answer_sdp: ANSWER_SDP,
  fingerprint: sdpFingerprints(ANSWER_SDP)[0],
  now: new Date(SCREEN_NOW.getTime() + 1000).toISOString(),
  cases: [
    { name: "PC-signed answer", envelope: good, expect: "valid" },
    { name: "answer_sdp altered after signing", envelope: tampered, expect: "SCREEN_ANSWER_INVALID", cause: "SIGNATURE_INVALID" },
    { name: "signed by a key other than the pinned PC key", envelope: byControllerKey, expect: "SCREEN_ANSWER_INVALID", cause: "UNKNOWN_KEY" },
    { name: "stamped with a MINOR this library does not speak", envelope: newer, expect: "PROTOCOL_INCOMPATIBLE" },
  ],
  pc_key_mac: await pcKeyMac(PAIRING_CODE, PAIRING_ID, PC_ID, kid, pcKid),
};

const out = {
  $comment: "Generated by shared/ts/scripts/make-fixtures.ts. TEST KEYS ONLY.",
  public_jwk: jwk,
  kid,
  cases,
  digests: {
    challenge_text: challengeText,
    challenge_digest: await challengeDigest(challengeText),
    pairing_code: PAIRING_CODE,
    pairing_code_handle: await pairingCodeHandle(PAIRING_CODE),
    pairing_id: PAIRING_ID,
    pc_id: PC_ID,
    pairing_verification_code: await pairingVerificationCode(PAIRING_CODE, PAIRING_ID, PC_ID, kid),
  },
  screen,
};
writeFileSync(resolve(fixtureDir, "es256-typescript.json"), JSON.stringify(out, null, 2) + "\n");
console.log("wrote es256-typescript.json kid", kid);

// ----- protocol 1.5: the data-channel cases (screen-dc-cases.json) ----------------------------------------------------
// A real 1x1 RGBA PNG (signature, IHDR, IDAT with a stored deflate block, IEND), the same bytes the Python generator builds
// with the standard library; both check them against this literal.
const CURSOR_PNG_1X1_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAEElEQVR4AQEFAPr/AAAAAP8BBAEAeX5eMQAAAABJRU5ErkJggg==";
// The largest custom bitmap the schema allows: 8192 base64 characters = 6144 PNG bytes (the 1x1 image plus a tEXt chunk).
const CURSOR_PNG_MAX_BYTES = 6144;

function pngChunk(kind: string, data: Buffer): Buffer {
  const head = Buffer.alloc(4);
  head.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(kind, "latin1"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body) >>> 0);
  return Buffer.concat([head, body, crc]);
}

/** A 1x1 RGBA PNG (one opaque black pixel); with `totalBytes` a valid tEXt chunk pads it to exactly that size. */
function cursorPng(totalBytes?: number): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(1, 0); // width
  ihdr.writeUInt32BE(1, 4); // height
  ihdr.set([8, 6, 0, 0, 0], 8); // 8-bit, colour type 6 (RGBA), deflate, no filter set, no interlace
  const idat = deflateSync(Buffer.from([0, 0, 0, 0, 255]), { level: 0 }); // filter byte 0, then R G B A
  const head = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), pngChunk("IHDR", ihdr)]);
  const tail = Buffer.concat([pngChunk("IDAT", idat), pngChunk("IEND", Buffer.alloc(0))]);
  if (totalBytes === undefined) return Buffer.concat([head, tail]);
  const keyword = Buffer.from("Comment\0", "latin1");
  const pad = totalBytes - head.length - tail.length - 12 - keyword.length;
  return Buffer.concat([head, pngChunk("tEXt", Buffer.concat([keyword, Buffer.alloc(pad, "x")])), tail]);
}

/** The same JSON object with spaces before its closing brace so it is exactly `totalBytes` UTF-8 bytes. */
function padded(text: string, totalBytes: number): string {
  if (!text.endsWith("}") || !/^[\x00-\x7f]*$/.test(text)) throw new Error("padded() takes an ASCII JSON object");
  const out = text.slice(0, -1) + " ".repeat(totalBytes - text.length) + "}";
  if (Buffer.byteLength(out, "utf8") !== totalBytes) throw new Error("bad padding");
  return out;
}

const small = cursorPng().toString("base64");
if (small !== CURSOR_PNG_1X1_B64) throw new Error(`the 1x1 PNG differs from the shared literal: ${small}`);
const large = cursorPng(CURSOR_PNG_MAX_BYTES).toString("base64");
if (large.length !== 8192) throw new Error("the large PNG must be 8192 base64 characters");
const custom = (png: string): string => `{"type":"cursor_shape","kind":"custom","width":1,"height":1,"hotspot_x":0,"hotspot_y":0,"png":"${png}"}`;
const cursorPos = '{"type":"cursor_pos","gen":1,"x":32768,"y":16384,"visible":true}';
const P2P = "phone_to_pc";
const P2C = "pc_to_phone";
const dcCase = (name: string, direction: string, text: string) => ({ name, direction, text });
const screenDcCases = {
  $comment:
    "Protocol 1.5. Data-channel messages both libraries must accept or refuse with parse_screen_dc_message / " +
    "parseScreenDcMessage (rules.screen_content, rules.screen_cursor); every refusal is MALFORMED_MESSAGE. " +
    "`direction` is the receiving side's vocabulary, `text` the exact message string (padding is spaces before " +
    "the closing brace). Written byte for byte by shared/python/scripts/make_fixtures.py and " +
    "shared/ts/scripts/make-fixtures.ts; the PNGs are test images built there (1x1 RGBA, the large one padded " +
    "with a tEXt chunk).",
  accepted: [
    dcCase("cursor_pos", P2C, cursorPos),
    dcCase("named cursor_shape ibeam", P2C, '{"type":"cursor_shape","kind":"ibeam"}'),
    dcCase("custom cursor_shape with a real 1x1 RGBA PNG", P2C, custom(small)),
    dcCase("state sending with encoder h264 and cursor overlay", P2C, '{"type":"state","state":"sending","encoder":"h264","cursor":"overlay"}'),
    dcCase("custom cursor_shape of 9000 bytes (8192-character png padded with a tEXt chunk)", P2C, padded(custom(large), 9000)),
  ],
  rejected: [
    dcCase("custom cursor_shape without png", P2C, '{"type":"cursor_shape","kind":"custom","width":1,"height":1,"hotspot_x":0,"hotspot_y":0}'),
    dcCase("custom cursor_shape whose png contains '-' (base64url, not standard base64)", P2C, custom(small.slice(0, 10) + "-" + small.slice(11))),
    dcCase("custom cursor_shape with a 33-character png (not a valid base64 length)", P2C, custom("A".repeat(32) + "=")),
    dcCase("custom cursor_shape with width 65", P2C, custom(small).replace('"width":1', '"width":65')),
    dcCase("custom cursor_shape with a visible key", P2C, custom(small).slice(0, -1) + ',"visible":true}'),
    dcCase("state of 1025 bytes", P2C, padded('{"type":"state","state":"sending"}', 1025)),
    dcCase("custom cursor_shape of 12289 bytes", P2C, padded(custom(large), 12289)),
    dcCase("cursor_pos without visible", P2C, '{"type":"cursor_pos","gen":1,"x":32768,"y":16384}'),
    dcCase("hb of 1025 bytes", P2P, padded('{"type":"hb"}', 1025)),
    dcCase("cursor_pos sent by the phone", P2P, cursorPos),
  ],
};
writeFileSync(resolve(fixtureDir, "screen-dc-cases.json"), JSON.stringify(screenDcCases, null, 2) + "\n");
console.log("wrote screen-dc-cases.json");

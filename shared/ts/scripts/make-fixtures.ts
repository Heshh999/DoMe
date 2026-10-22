/**
 * Generate shared/protocol/fixtures/es256-typescript.json with the TypeScript signer so the
 * Python verifier can check it (and vice versa). Uses the committed TEST keys only (controller and PC).
 * Since protocol 1.4 the file also carries the Screen view vectors: a PC-signed screen answer the phone must accept,
 * two it must refuse, one it must call PROTOCOL_INCOMPATIBLE, and the pairing pc_key_mac (rules.pc_key_pinning).
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { challengeDigest, commandDigest, pairingCodeHandle, pairingVerificationCode, pcKeyMac } from "../src/digest.ts";
import { ECDSA_P256, exportPublicJwk, kidFromJwk } from "../src/keys.ts";
import { buildScreenAnswerPayload, offerDigest, sdpFingerprints } from "../src/screen.ts";
import { signPayload } from "../src/signing.ts";
import { dumpsCompact } from "../src/strictJson.ts";

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
const answer = await buildScreenAnswerPayload({
  accountId: ACCOUNT_ID,
  pcId: PC_ID,
  controllerId: CONTROLLER_ID,
  commandId: SCREEN_COMMAND_ID,
  screenSessionId: SCREEN_SESSION_ID,
  offerSdp: OFFER_SDP,
  answerSdp: ANSWER_SDP,
  now: SCREEN_NOW,
});
const answerText = dumpsCompact(answer);
const good = await signPayload(pcKeys.privateKey, pcKeys.publicKey, answerText);
const tampered = { ...good, payload: answerText.replace("a=setup:active", "a=setup:passive") };
const byControllerKey = await signPayload(privateKey, publicKey, answerText); // right payload, wrong (unpinned) signer
const newer = await signPayload(pcKeys.privateKey, pcKeys.publicKey, dumpsCompact({ ...answer, protocol_version: "1.5" }));
const screen = {
  $comment: "Protocol 1.4. The phone verifies these with the PC key pinned at pairing (pc_public_jwk) at `now`.",
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

# ADR-0002 — Screen view media transport

Status: proposed (2026-10-16), to be accepted when the Phase 0 gate in `docs/design/screen-view.md` §18
has been run and its numbers recorded in §"Gate results" below. Reversible decisions are marked
**[reversible]**. Design brief: `docs/design/screen-view.md`.

## Context

Screen view (roadmap #52) shows the PC's screen live on the phone with tap-to-click, two-finger scroll,
pinch zoom, a floating mini view, PC sound with mute and a TV-style remote; Free gets 10 minutes per 5-hour
window, Pro and Pro Plus unlimited. The website and FAQ already promise it as "Coming". Nothing is recorded
and the AI never receives it.

What exists today, and constrains the choice:

- The relay is one FastAPI/asyncio process on one shared-cpu-1x 512 MB Fly machine in `iad` with a shared
  IPv4 and only an `[http_service]` (`deploy/fly.toml`). It carries **text JSON frames only**
  (`ws.receive_text()` → strict parse → schema), capped at 64 KiB per frame and 16 KiB per signed payload
  (`version.json limits`). It is redeployed by restarting it. ADR-0001 D10 states that the relay can read
  every payload it routes.
- Production costs ≈ $45/month and staging ≈ $4 of a ≈ $50 budget (`docs/COST_MODEL.md` §2A); Fly egress is
  $0.02/GB; a dedicated IPv4 ($2/month) is never allocated.
- The security model is fixed: the phone signs every command and input batch with its own P-256 key; the PC
  verifies against local grants approved on the PC; the server relays but is not trusted to authorize;
  capabilities are granted only on the PC. The PC never listens on the internet (ADR-0001 D1).
- The product is closed-source and commercial, packaged with PyInstaller for Windows 10/11 x64, run as the
  normal user, and must work in iOS Safari and the installed home-screen PWA.
- A phone-side `RTCPeerConnection`, hardware H.264 decode and Opus are native in iOS Safari and in the
  home-screen app on iOS 17, 18 and 26 [research]. WebCodecs has no audio decoder before iOS 26 and
  WebTransport needs Safari 26.4 [research].

## Options considered

Latency figures are estimates, not measured; costs use 1 GB per streamed hour at the cap (0.74 GB/h at
1.5 Mbps plus audio) and the scenarios of the design brief §14 (1,000 MAU, 30 % adopt, Base 4 h and High
10 h per user per month, 40 % of hours relayed where a relay applies).

| | (a) WebRTC peer to peer, aiortc on the PC, TURN for the phone only (**chosen**) | (b) Video through the existing Fly WebSocket relay | (c) WebTransport + WebCodecs through Fly | (d) WebRTC with self-hosted coturn on Fly |
| --- | --- | --- | --- | --- |
| Latency | Direct path; same Wi-Fi ≈ 60–120 ms glass-to-glass; a relayed session detours via the nearest Cloudflare site; UDP with NACK/RTX and PLI | Every byte PC → `iad` → phone over TCP; +70–200 ms outside US-East; one lost packet stalls everything behind it | QUIC avoids the TCP stall; same `iad` detour | As (a) for direct; relayed detour via `iad` only |
| iPhone support | Native `RTCPeerConnection`, hardware H.264, Opus, `<video>`; Safari tab and home-screen app | WebCodecs: video from iOS 16.4, `AudioDecoder` only from iOS 26; canvas rendering; no PiP path | Safari 26.4+ only; home-screen support unconfirmed; same decoder limits | As (a) |
| Fly cost at 1,000 MAU | **$0 media**; signaling ≈ $0; input batches during viewing ≤ $0.001/h (≈ $1–3/month) | 100 % of bytes at $0.02/GB: ≈ $18 Base, ≈ $45 High, plus a performance-CPU machine | As (b) plus a dedicated IPv4 ($2) and a QUIC server | ≈ $6/month fixed (machine + IPv4) plus $0.02/GB on relayed bytes |
| Relay provider cost | Cloudflare Realtime TURN: $0.05/GB on Cloudflare-to-client data, 1,000 GB/month free, STUN free [research, verify]: **$0** at 1,000 MAU Base and High (240 / 600 GB) | — | — | — |
| Relay CPU | Negligible: a few KB of signaling per session | Video competes on the one asyncio loop with the touchpad path that just passed its device test; binary frames, back-pressure and a bigger machine needed | As (b) plus QUIC | TURN is a second process or app; abuse monitoring |
| Privacy from DoMe's servers | DTLS-SRTP between the devices; fingerprints inside phone-signed and PC-signed SDP; the relay and TURN see ciphertext, addresses and timing | The relay sees the screen in plain form unless an app-level cipher is added on top of WebCodecs | Same as (b) | As (a) |
| Deploys | A relay restart does not cut a stream (the stream survives up to 60 s without the relay, then ends by the dead-man ticket) | Every deploy cuts every stream | Same as (b) | A coturn deploy cuts relayed streams |
| Licensing | aiortc and its dependencies are BSD/Apache/MIT; **but** aiortc requires PyAV, whose stock Windows wheels bundle GPL x264/x265 and are not shippable: DoMe must build its own LGPL-2.1 FFmpeg + PyAV (no x264/x265/OpenH264, no `--enable-version3`); H.264 comes from Windows Media Foundation (OS/driver encoder, counsel pending) with VP8 (BSD) as the fallback; Opus (BSD) | The PC still needs an encoder, so the same build obligation; plus a WebCodecs decode path on the phone | Same as (b) | As (a); coturn is BSD |
| New moving parts | aiortc in an isolated worker process; one Cloudflare API call per session; the FFmpeg/PyAV CI build; PC key pinning at pairing | Binary relay frames, back-pressure, a bigger machine, a canvas renderer | The most: QUIC server, WebCodecs renderer, dedicated IPv4 | As (a) plus TURN operations |
| Blocked networks | A PC on a network that blocks all outbound UDP cannot connect (honest error; PC-side TURN over TLS 443 is Phase 3) | Works wherever the app loads (its one strength) | Needs UDP 443; no fallback except (b) | As (a), with a self-run relay |

Variants of (a) that were considered and rejected for Phase 1:

- **TURN credentials for the PC too.** aiortc supports one TURN server and has no ICE restart
  **[inspected]**; one relayed side is enough for ICE, the PC's outbound packets open its own NAT state and
  TURN permissions are per IP. Phone-only TURN removes credentials, delivery and revocation from the agent.
  Kept as the Phase 3 answer to UDP-blocked PCs.
- **Trickle ICE and a separate signed signal stream.** aiortc gathers all candidates before answering, and
  WebKit's gathering costs about 1 s; vanilla ICE inside one signed command removes a frame type, sequencing
  and replay handling.
- **Accepting that the phone cannot authenticate the PC's answer.** A malicious relay could then show a fake
  screen while reading typed text in transit (T21): a phishing path that steers the owner's taps. PC key
  pinning at pairing, through a MAC keyed by the 100-bit pairing code the server never holds, is required
  in v1 with no "continue anyway".
- **Server-signed JWS allowance tickets.** They add no protection over the TLS-authenticated relay socket,
  which the agent already trusts for plan state; plain `granted_until` tickets the PC must ask for are used.
- **Vendor SDK encoders (NVENC/AMF/QSV) and Windows.Graphics.Capture as the primary source.** `h264_mf
  hw_encoding=1` already reaches the vendor hardware; WGC-first needs either CPU copies or an FFmpeg
  version PyAV ≤ 17 is not verified to build against. Desktop Duplication is primary, WGC the fallback.

## Decision

1. **Media travels directly between the PC and the phone over WebRTC** (DTLS-SRTP), produced by aiortc in
   a separate send-only `DoMe.exe screen-worker` process and consumed by the browser's `RTCPeerConnection`
   into a `<video>` element. The Fly relay carries only small signed signaling frames and never a pixel or
   audio sample. **[reversible: the worker hides aiortc behind a `MediaTransport` seam; a native stack can
   replace it without touching signaling]**
2. **Cloudflare Realtime TURN is the relay fallback, minted by cloud-api for the phone only**, with a TTL
   bound to the Free allowance. **TURN spending is bounded server-side without trusting either device:**
   every credential is tied to one controller (one outstanding at a time; a new mint revokes the previous)
   and is revoked when no `screen.session_start` follows within 60 s, when the start is refused, when the
   session ends, and on controller revocation, grant narrowing, logout, account-session revocation or plan
   change; relayed bytes are reconciled hourly from Cloudflare's own usage figures (`customIdentifier =
   account_id`), with a server-side provisional bound (every ticketed second counted as relayed at the cap)
   until they arrive; a per-account daily relayed-GB cap on Free and a per-account monthly mint cap refuse
   further minting; and the graduated monthly guard (Data saver past 700 GB, STUN-only for Free past 900 GB)
   runs on those figures, never on agent-reported bytes. The PC uses STUN only. **[reversible: self-hosted
   coturn on Fly is the documented fallback if Cloudflare's terms change; PC-side TURN over TLS 443 is
   Phase 3]**
3. **Signaling is authenticated end to end with the keys DoMe already has.** The phone's offer, with its
   DTLS fingerprint, travels inside the signed `screen.session_start` command and is verified by the PC
   against its local grant. The PC's answer is signed by its identity key and verified by the phone against
   a key pinned at pairing (`pc_key_mac`, HMAC keyed by the pairing code). Capture starts only after DTLS
   connects to the signed certificate. No renegotiation: every transport change is a new signed session.
4. **Codecs and the capture pipeline:** H.264 within level 3.1 from Windows Media Foundation (`h264_mf`,
   hardware first), sent through aiortc's pre-encoded path with SPS and PPS prepended to every IDR (aiortc's
   packer only splits on start codes, and whether a vendor MFT repeats parameter sets varies); VP8 (libvpx)
   as the fallback; Opus for sound, fed interleaved s16 (aiortc's encoder asserts it; WASAPI delivers
   float). Never AAC, x264, x265 or OpenH264. Capture is Desktop Duplication through FFmpeg's `lavfi`
   device; because that device emits `wrapped_avframe` packets, the build enables **exactly one decoder,
   `wrapped_avframe`** (LGPL; it unwraps the local device's frames and parses no bitstream, never network
   input), and the licence gate asserts the decoder set is exactly that; PyAV's `Graph` API (no decoder) is
   the fallback path. Keyframes on PLI come from a hook on aiortc's `RTCRtpSender._send_keyframe` (the
   pre-encoded path never surfaces PLI otherwise), with aiortc pinned by hash, a guard test and a periodic
   IDR fallback. DoMe builds its own LGPL-2.1 FFmpeg and PyAV, ships the LGPL notice and a source offer,
   keeps the DLLs replaceable, and fails the build through an automated licence gate on the wheel and on
   the frozen folder; the stock GPL-bundled PyAV wheel is used only in Linux CI tests and never ships. H.264
   through the OS encoder is **pending counsel**; VP8-only is the fallback decision.
5. **Free metering is a server ledger** (rolling 5-hour window per account) enforced by the PC through
   60-second `granted_until` tickets it must ask for, which double as a dead-man switch so revocation reaches
   a PC the relay cannot within 60 s, and **charged on the relay's own clock**: the charge is the wall time
   the relay measured from forwarding the start to the end frame or the last ticket, the agent's
   `live_seconds` never reduces it, `connection_failed` is free only when the relay saw no `started` frame
   within 20 s (at most three times per window), and agent-reported secure-desktop pauses are discounted
   only when the relay saw the pair and at most 120 s per session, so pauses may count as minutes. A patched
   agent can therefore take unlimited minutes only on a direct path, which costs DoMe nothing; on the relayed
   path it is charged like everyone else. The relay also ends a session whose owning controller has been
   signed out, revoked or absent for 60 s (`controller_offline`), so a stolen phone's stream does not outlive
   its account session. TURN credentials expire with the allowance and are revoked per Decision 2.
6. **Phased delivery with a go/no-go gate.** Phase 0 proves the pipeline on devices against the thresholds
   below; Phase 1 ships picture, taps, scroll, zoom, indicator, pinning and metering as a beta behind
   `DOME_SCREEN_VIEW_ENABLED`; Phase 2 adds sound, the in-app mini view, the TV remote and a second monitor;
   Phase 3 builds only what device data asks for.

### Gate results (to be filled in before this ADR is accepted)

| Criterion | Threshold | Result | Device, OS, build, date |
| --- | --- | --- | --- |
| Plays in Safari and the home-screen app on iOS 18 and 26 | yes | not yet verified on a device | |
| First frame after "Show screen" | < 4 s | not yet verified on a device | |
| Same-Wi-Fi glass-to-glass | < 250 ms | not yet verified on a device | |
| PC CPU at 720p30 with hardware encoding (worker + agent) | < 10 % of the total | not yet verified on a device | |
| Works relayed through Cloudflare TURN (forced `relay` policy) | yes | not yet verified on a device | |
| Same-Wi-Fi direct via mDNS `.local` candidates from a standard-user process | informational | not yet verified on a device | |
| Windows firewall prompt for a standard user; ICE works after Cancel | informational | not yet verified on a device | |

## Consequences

- **Amendments to ADR-0001.** D1's "the PC never listens on the internet" becomes: during a live Screen view
  session only, the worker binds ephemeral UDP sockets that answer only that session's ICE checks and
  complete DTLS only with the signed certificate; they close with the session. D10 moves "screen viewing"
  into the product; "end-to-end payload encryption from the relay" stays absent for commands, typing and PC
  state, and `docs/SECURITY.md` §1/§8 describe media confidentiality precisely and for media only. No
  product-wide "end-to-end encrypted" claim is made anywhere.
- **A contract MINOR (1.3)** with two capabilities, two actions, signaling and lifecycle frames, three input
  events, PC key pinning in pairing, new limits, rules and errors (design brief §11). Cloud deploys before
  agents; a 1.3 phone against a 1.2 PC gets `PROTOCOL_INCOMPATIBLE` ("Update DoMe on your PC") **from the
  relay, which refuses `screen.*` commands and 1.3-stamped input batches before forwarding them**: the shipped
  1.2 agent validates a batch's schema before its version stamp and would answer `MALFORMED_MESSAGE`, which
  no new contract can change; the 1.3 shared libraries check the stamp first so later MINORs behave as
  `PROTOCOL.md` §18.2 promises. Existing pairings must re-pair once to pin the PC's key, which requires the
  pairing page to keep the code until approval (today it drops it right after the claim).
- **Hosting cost stays inside the budget**: ≈ $0 added at 100 MAU, ≈ $1–3 at 1,000 MAU (input-batch egress
  during viewing, not media). The stress case (every Free user at the cap, 40 % relayed) costs $0 at
  Cloudflare with the guard but ≈ $14 of Fly egress; at that scale paid plans must exist.
- **A new vendor and sub-processor** (Cloudflare Realtime TURN) sees addresses, timing and byte counts of
  relayed sessions; the privacy page says so. Its pricing was read from search excerpts and must be
  confirmed on the vendor page, including whether a payment method is required, before the founder's setup
  step.
- **A recurring build cost**: the LGPL FFmpeg/PyAV wheel must be rebuilt on every FFmpeg, PyAV or aiortc
  bump, pinned by hash and guarded by the licence gate. The installer grows by an estimated 10–20 MB.
- **Code signing becomes a release blocker** for Screen view beyond the founder: an unsigned app that
  captures the screen, injects input and (Phase 2) records sound is very likely to be flagged by SmartScreen
  and antivirus.
- **The mini view promise changes.** System picture-in-picture is broken in iOS home-screen apps and
  unreliable for live streams; DoMe ships an in-app mini view and the website wording must change (founder
  decision, design brief §22).
- **What can only be verified on devices** is listed in the design brief §18 and in the gate table above;
  `docs/ACCEPTANCE.md` gains scenarios 26–30 and records evidence honestly. Everything else is unit- or
  integration-tested in CI with a fake screen source and real aiortc peers on loopback.
- **Known limits accepted for Phase 1:** a PC on a network that blocks all outbound UDP cannot connect
  (honest error); a patched agent on the customer's own PC can take unlimited **direct-path** minutes, which
  cost DoMe nothing (relayed minutes are charged on the relay's clock and bounded by the TURN controls); a
  Free user whose PC shows a UAC prompt for longer than two minutes is charged for the excess; the relay
  still sees tap coordinates and typed text during viewing (T21) until the Phase 3 "private input" item; the
  relayed share (40 %) is an assumption until Phase 0 measures mDNS and NAT behaviour; the Cloudflare revoke
  endpoint and Analytics API fields are read from documentation and must be confirmed before Phase 1, with
  the provisional server-side bound carrying the guard until then.

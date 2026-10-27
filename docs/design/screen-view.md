# Design: Screen view (roadmap #52, protocol 1.4)

**Status 2026-10-21: Phase 1 (WP1.1–WP1.9, the "Screen view beta") is being built as protocol 1.4.** Billing took MINOR 1.3 on 2026-10-20, so every "1.3" this brief used for Screen view's contract version now reads 1.4; the shipped agent still announces up to 1.2 and the shipped shared libraries are 1.3, so the compatibility arguments below (the relay shields pre-1.4 agents from 1.4-stamped batches) are unchanged. The contract is in `shared/protocol` and `docs/PROTOCOL.md` §20; where this brief and the contract disagree, the contract wins.

Status: design brief for the component builders, written 2026-10-16 from four research reports, three
competing designs and the judge's verdict (the secure design as the base, with the lean design's scope
discipline and the quality design's UX grafted in). Nothing in it is built. The transport decision is
`docs/adr/0002-screen-view-transport.md`. When the contract MINOR lands, the normative sources become
`shared/protocol` (`version.json → rules.screen_sessions`, `rules.screen_signaling`,
`rules.pc_key_pinning`, `rules.screen_metering`, `rules.screen_content`, `rules.local_permission_ui`;
`actions.json` capabilities `screen` / `screen_audio` and actions `screen.session_start` /
`screen.session_stop`; `relay-frames.schema.json` defs listed in §11). Where this brief and the contract
then disagree, the contract wins and the builder records the gap in its `CONTRACT_ISSUES.md`.

Evidence tags, used throughout:

- **[repo]** read in this repository on 2026-10-16; **[inspected]** read by a researcher in the actual
  wheel or source (aiortc 1.15.0, aioice, PyAV Windows wheels, FFmpeg `mfenc.c`/`ddagrab`);
  **[research]** taken from the research reports and not re-checked here (vendor prices among them are
  search excerpts and must be confirmed on the vendor page); **[estimate]** an assumption, not measured.
- Planned evidence per work package (§19): **unit-tested** (CI, fakes), **integration-tested** (CI, real
  PostgreSQL / real aiortc peers on loopback), **not yet verified on a device** (needs the founder's
  Windows PC and iPhone; recorded in `docs/ACCEPTANCE.md` only for what passes).
- Nothing below is Windows-device-tested or iPhone-tested today. The capture pipeline, the encoder, the
  Windows indicator bar, iOS playback and NAT behaviour can only be verified on devices.

Why the founder cannot find it on the phone: Screen view does not exist in the product. The website and
FAQ mark it "Coming" (`SCREEN_VIEW_STATUS` in `mobile-app/src/content/plans.ts:21`, `DECISIONS.md` 62–63,
`docs/HANDOFF.md` §7 item 2) **[repo]**. The touchpad and keyboard that just passed the real-device test
are a different feature. WP-0 (§19) puts a "Screen view: Coming" card on the Remote page now.

## 0. Decisions on one page

| Topic | Decision |
| --- | --- |
| Transport | WebRTC between the PC and the phone: aiortc in a separate `DoMe.exe screen-worker` process, Safari's own `RTCPeerConnection` and `<video>` on the phone. Media is DTLS-SRTP and never passes through Fly. Signaling rides the existing relay as signed JSON frames. ADR-0002. |
| Relay fallback | Cloudflare Realtime TURN, **minted for the phone only** (UDP 3478, TCP, TLS 443). Every credential is tied to one controller (one outstanding at a time) and revoked when no session starts within 60 s, when the start is refused, and when the session ends; relayed bytes are reconciled from the provider's own usage figures, never from agent reports (§13). The PC uses STUN only, so it holds no TURN credential and aiortc's single-TURN-server limit never applies. PC-side TURN over TLS 443 is a Phase 3 item for UDP-blocked PCs. |
| End-to-end authentication | The phone signs the offer (its DTLS fingerprint inside the signed bytes) with its controller key; the PC verifies against its **local** grant. The PC signs the answer with its existing identity key (`pc_key.bin`); the phone verifies against a PC key **pinned at pairing** through a new MAC keyed by the pairing code the server never holds. No pin, no picture: there is no "continue anyway". |
| Capture and encoding | Our own LGPL-2.1 FFmpeg + PyAV build (no x264/x265/OpenH264, no `--enable-version3`, no vendor SDK encoders, `--disable-network`, exactly one decoder, `wrapped_avframe`, which the `lavfi` capture device needs for its own local frames). Desktop Duplication (`ddagrab → scale_d3d11 → hwdownload`) is primary, Windows.Graphics.Capture the fallback. H.264 from Windows Media Foundation (`h264_mf hw_encoding=1`) within level 3.1; VP8 (libvpx) as the fallback. Sound (Phase 2): WASAPI loopback → Opus. |
| Capabilities | `screen` (picture) and `screen_audio` (PC sound, only with `screen`), granted only in a DoMe window on the PC, never by the phone, the server or a bare CLI flag. Taps need `pointer`; the TV remote needs `keyboard`. |
| PC indicator | A "Screen view on" bar on **every** monitor (excluded from capture), a tray badge, start/end notifications and a local history. The bars are created and confirmed **before** the worker starts and capture pauses whenever one is missing (fail-closed, §7). No setting hides it. Stop on the bar, in the tray, by hotkey, by "Disable remote control", by un-granting, by locking the PC, by quitting DoMe. |
| Session lifecycle | One viewer per PC. Capture starts only after DTLS connects to the certificate the phone signed. An 8 s viewer lease (2 s heartbeats over the authenticated data channel), 60 s allowance tickets the PC must **ask** for (dead-man switch), a 4 h maximum, lock ends, UAC pauses. |
| Free minutes | A server ledger (rolling 5-hour window per account, 600 s on Free) enforced by the PC through plain `granted_until` tickets and **charged on the relay's own clock** (agent-reported seconds never reduce the charge, §10); TURN credentials expire with the allowance. A patched agent gains only direct-path minutes, which cost DoMe $0; on the relayed path it is charged like everyone else and bounded by the TURN controls. |
| Input | Tap-to-click stays on the signed `input_batch` path with three additive events: `pointer_move_to` (absolute, normalised over the streamed surface, with a geometry generation), `pointer_wheel` (1/120-notch units for smooth two-finger scroll) and `key: f11`. Batches containing them are stamped 1.4. |
| Mini view, TV remote | In-app mini view everywhere (Phase 2). System picture-in-picture only as a Safari-tab experiment after a device test (Phase 3): it is broken in home-screen apps [research]. The TV-style remote is a separate page that works without the picture and is not metered. The website wording for the mini view must change (§22). |
| Contract | MINOR **1.4** (§11). No `pc_state` change in Phase 1. |
| Hosting cost | ≈ $0/month added at 100 and 1,000 MAU in the base and high scenarios (§14). No new Fly machine, no dedicated IPv4, no media through the relay. |
| Ships | Phase 0 spike with a go/no-go gate → Phase 1 "Screen view beta" (picture, taps, scroll, zoom, indicator, pinning, metering) → Phase 2 (sound, mini view, TV remote, second monitor) → Phase 3 only where device data asks. |

## 1. Goals and non-goals

Goals, in the order the security model ranks them:

- **G1** No DoMe server and no TURN server can see or alter the picture or sound.
- **G2** The phone shows a picture only after it has verified that the answer was signed by the PC key
  pinned at pairing; the PC captures only after the DTLS handshake with the certificate the phone signed.
- **G3** Capture starts only when all of these hold: a phone-signed start command, a local `screen`
  grant, remote control on, the Windows session unlocked, a valid allowance ticket, local consent where the
  owner asked for it, a DTLS connection to the signed certificate.
- **G4** Whenever pixels are captured, a DoMe indicator is visible on every monitor and in the tray, and
  no phone or server can hide it.
- **G5** Every emergency stop ends capture within about 1 s; a lost relay ends it within 60 s; a vanished
  phone within 8 s; nothing runs longer than 4 h without a fresh signed start.
- **G6** No frame or audio sample is written to disk, logged, included in diagnostics, sent to cloud-api,
  or sent to an AI provider. Each clause has a structural test (§17.2).
- **G7** The phone cannot change the Free allowance; the PC can only cheat itself out of minutes that cost
  DoMe nothing: the ledger is charged on the relay's clock, never on what the PC reports. TURN spending
  is bounded server-side without trusting either device: every credential is tied to one controller and
  revoked when its session does not start or ends, relayed bytes are reconciled from the provider's own
  usage figures, and per-account daily and monthly caps refuse further minting (§13).
- **G8** The PC's network exposure is limited to the ICE/UDP ports of a live session, which answer only
  that session's ICE checks and complete DTLS only with the signed certificate.
- The website's promises, delivered honestly and in stages: live picture, tap what you see, two-finger
  scroll, pinch to zoom, a floating mini view, PC sound with mute, a TV-style remote; Free 10 minutes per
  5-hour window, Pro and Pro Plus unlimited.

Non-goals, stated in the product where a customer could expect otherwise:

- Protection from a compromised PC or phone OS, or from a malicious PWA deploy or agent update (T34).
- Preventing the iPhone's own screenshots or screen recording of what the phone shows.
- End-to-end encryption of commands, typing and PC state: they stay readable by the trusted relay as
  today (`SECURITY.md` §1) until the Phase 3 "private input" item.
- System picture-in-picture over other apps from the home-screen app; a native wrapper, which is the only
  full fix, is out of scope.
- Capturing the lock screen, UAC prompts or any secure desktop. DoMe never tries.
- Not built unless data asks for it: an SFU or media server, video over the WebSocket relay, WebCodecs or
  WebTransport, trickle ICE, renegotiation, PC-side TURN, server-signed allowance tokens, region-crop zoom,
  a warm pre-spawned worker, signed input over the data channel (§19 Phase 3).

## 2. User flows

### 2.1 PC: one-time grant (the owner at the desk)

1. Update DoMe (an agent that announces protocol 1.4).
2. Tray › **Paired phones** › *Maya's iPhone* › **Allow Screen view…** opens a DoMe window. There is no
   silent checkbox and no CLI-only path on Windows: `DoMe.exe grant <id> --screen` opens this same window
   and waits (headless widening of `screen` works only on the fake platform, for tests).
   - Scope text (`SCREEN_SCOPE_EXPLANATION`, next to `INPUT_SCOPE_EXPLANATION` in `pairing.py`):
     "Screen view lets this phone see everything on this PC's screens while a Screen view is on: every
     app, notifications and messages that pop up, and anything you reveal, such as a password you show. It
     does not let the phone click or type; that is Touchpad and Keyboard. DoMe shows a 'Screen view on'
     bar on every screen and a red dot on the tray icon whenever someone is watching, and you can stop it
     there. Nothing is recorded, and the AI never receives it."
   - Scam line: "Only allow phones that are yours. DoMe will never call you or ask you to allow Screen
     view."
   - Options: ☐ Also allow PC sound (`screen_audio`), with "PC sound is everything the PC plays, including
     calls." · When this phone starts Screen view: (•) Start right away (the bar still shows) ( ) Ask on
     this PC each time · After someone at this PC presses Stop, ask before the next start for: [10 minutes ▾
     | 1 minute | off] (default 10 minutes; see §6.3).
   - Buttons: **Allow** (enabled after 5 s; a one-time cost on a rare action) and Cancel.
3. The pairing approval window lists "Screen view" and "PC sound" unticked and greyed with "add later from
   Paired phones". They are never granted at pairing, so pairing stays one decision. Existing grants never
   gain them (`rules.grant_update`).

### 2.2 Phone, every session

1. **Entry.** The Remote page's "Screen" tile and a Home card show one state: "Allow Screen view on your PC
   first" · "Pair again so your phone can check it is your PC" (pairings made before 1.4, §4.1) · "Update
   DoMe on your PC" (`PROTOCOL_INCOMPATIBLE`) · "Ready · 10 min left in this window" · "Free Screen view
   resets at 4:12 pm".
2. **Starting.** The owner taps **Show screen**. That tap is the user gesture iOS requires for `play()`
   (and covers Low Power Mode, where muted autoplay can be refused [research]). The phone then: sends
   `screen_ice_request` on its bound controller socket and receives ICE servers plus the allowance; builds
   a receive-only offer with `setCodecPreferences` (H.264 packetization-mode 1, VP8, RTX; Opus); waits for
   ICE gathering to complete or 3 s; signs `screen.session_start{offer_sdp, surface, audio, takeover}`;
   verifies the PC-signed answer against the pinned key; connects; shows the first frame muted. It also
   starts an input session (existing code) if `pointer` is granted.
3. **Live view.** Full-viewport, landscape when rotated (manifest `orientation: "any"`, §16), `env(safe-
   area-inset-*)`, `object-fit: contain`. A thin top strip: PC name · "Encrypted between your PC and this
   phone" (shown only after the answer verified) · Free time left · Touchpad-mode toggle · Keyboard ·
   Sound (Phase 2) · Screens (Phase 2) · Stop. A screen wake lock is requested where supported.
4. **Interaction.** Tap to click; press-and-hold for the precision loupe; two fingers to scroll; pinch to
   zoom (local only, nothing sent); one-finger drag pans when zoomed; Touchpad mode swaps in today's
   relative gesture machine for drags and precision. Without `pointer`: "View only. Allow the touchpad on
   your PC to tap."
5. **Leaving and returning.** Another DoMe page → the in-app mini view (Phase 2; Phase 1 stops the
   session). The app going to the background or the phone locking → the phone sends `bye`, closes the
   connection and stops input (the existing `runtime.ts` "hidden" pattern **[repo]**); the PC ends the
   session within 8 s; no minutes count while away. On return with the Screen page still in front, the
   phone starts **one** fresh session automatically, except after `stopped_on_pc`, a consent refusal, an
   exhausted allowance or a removed grant, and never while ask-mode is on (then a **Resume** button).
6. **Stopping.** Stop sends `bye` on the data channel and the signed `screen.session_stop`, closes the
   connection and shows "Stopped · 3 min 12 s".

### 2.3 PC during a session

- At start: notification "*Maya's iPhone* is viewing this screen. Stop: tray › Stop Screen view, or
  Ctrl+Alt+Shift+S"; a topmost bar on every monitor, excluded from capture: "● Screen view · Maya's iPhone ·
  [Stop ▾]" with a speaker glyph while PC sound is on; a red dot on the tray icon; the tooltip and the
  status window name the viewer. The bar is visible from the moment the worker starts until it has exited.
- Stop ▾ offers "Stop" and "Stop and turn off Screen view for this phone" (removes `screen` and
  `screen_audio` through `grant_update`).
- At end: notification "Screen view ended (3 min 12 s)". Status window › **Screen view history** lists the
  last 20 sessions (phone, start, duration, end reason; metadata only; clearable).
- With ask-each-time, or during the post-Stop cooldown (which applies to **every** phone, §6.3): a window
  "*Maya's iPhone* wants to see this screen" with **Deny**, **Allow** (enabled after 1 s), **Block this
  phone for 1 hour** and **Turn off Screen view for this phone** (sends `grant_update` without `screen` /
  `screen_audio`); no answer within 30 s refuses. At most one consent window opens per minute per PC, and
  after a Deny or a timeout further starts from any phone are refused without a window for an escalating
  interval (§6.3), so nobody can keep a dialog in front of the person at the PC. While it is open, DoMe
  injects no phone input (§6.4).

## 3. Transport (summary; ADR-0002 is authoritative)

```
iPhone (Safari tab or home-screen app)                         Windows PC (DoMe.exe, standard user)
  RTCPeerConnection (recvonly video+audio, data channel)        agent ── pipe ── DoMe.exe screen-worker
        │ signed offer (controller key) ──► Fly relay ──► agent verifies vs LOCAL grant, spawns worker
        │ ◄── PC-signed answer (pc_key) ◄── Fly relay ◄── agent signs the worker's answer
        │
        └═══ DTLS-SRTP media + DTLS data channel ═══════════════════════════════► worker (send-only)
             direct (same Wi-Fi / NAT) or via Cloudflare TURN on the PHONE side (ciphertext only)
  input_batch (tap = pointer_move_to + click) ──► Fly relay ──► agent (unchanged signed path)
```

- Why not the Fly relay: it is one asyncio process on one shared-cpu-1x 512 MB machine with text-only
  JSON frames capped at 64 KiB (`deploy/fly.toml`, `version.json limits`) **[repo]**; every byte would be
  $0.02/GB egress (`COST_MODEL.md` §2) and the relay would see the screen in plain form (ADR-0001 D10).
- Why phone-only TURN: ICE needs one relayed side; the PC's outbound packets to the phone's relayed address
  open its own NAT state; TURN permissions are per IP, so a PC behind a port-changing NAT still matches its
  server-reflexive address [research]. The PC therefore needs no TURN credential, no credential delivery and
  no revocation path. The cost is a PC on a network that blocks **all** outbound UDP, which gets an honest
  error (§16) until Phase 3 adds PC-side TURN over TLS 443.
- Same-Wi-Fi: WebKit publishes mDNS `.local` host candidates instead of raw LAN addresses (WebKit 189503)
  [research]; aioice ships `mdns.py` and resolves `.local` remote candidates **[inspected]**, so a direct
  LAN path may work without router hairpin or TURN. Whether a standard-user Windows process can join the
  multicast group and get through the firewall is **not yet verified on a device** and is Phase 0 item 6.
- Changes to "the PC never listens" (ADR-0001 D1, `SECURITY.md` §1): during a live session only, the
  worker's ICE agent binds ephemeral UDP sockets that answer only STUN checks carrying the session's ICE
  credentials (which travel inside the signed offer and answer) and complete DTLS only with the signed
  certificate. They close with the session. The Windows Defender Firewall prompt for a standard user is
  **not yet verified**; the design must work when the owner presses Cancel (outbound-initiated ICE and TURN
  still work).

## 4. Signaling, authenticated end to end with the keys DoMe already has

### 4.1 PC key pinning at pairing (new in 1.4; closes the fake-screen gap)

Today the PC's identity key (`identity.py`, DPAPI-protected `pc_key.bin`) reaches only the server, in
`agent_link_start_request.pc_public_jwk`; the pairing HMAC covers `pairing_id|pc_id|controller_kid` only
(`SECURITY.md` §3 step 6, ADR-0001 D5) **[repo]**. A malicious relay could therefore substitute the PC's
answer and show the phone a **fake** screen; because the relay also reads typed text in transit (T21),
that is a credential-phishing path and it steers the owner's taps. Pinning closes it in v1.

1. **Approval on the PC.** The PC still holds the pairing `code` in memory (`PairingSession.code`
   **[repo]**). It computes `pc_key_mac = base64url(HMAC-SHA256(key = code, msg = "dome-pair-pc-key-v1|"
   + pairing_id + "|" + pc_id + "|" + controller_kid + "|" + pc_kid))`, all 32 bytes, untruncated, and
   sends it in `pairing_decision`.
2. **The relay** stores the MAC on the pairing row. Once `approved`, `GET /v1/pairing/{id}` returns
   `pc_key{public_jwk, mac}` with the JWK from the server's PC row.
3. **The phone** computes `pc_kid = kid_from_jwk(public_jwk)`, verifies the MAC with WebCrypto HMAC
   `verify` (constant time), and stores `{pc_id, pc_kid, pc_public_jwk, pinned_at}` in IndexedDB next to
   the controller key. **This is a change to `PairPage.tsx`, not current behaviour** **[repo]**: today
   the page drops the code right after `pairingClaim` ("The code has done its job; it is not kept anywhere
   from here on", `PairPage.tsx:111`) and its `waiting` step carries only `{status, verification}`
   (`Step` type, line 24), so a builder who keeps that flow would compute no MAC and every pairing would end
   unpinned. WP1.7 therefore changes the `waiting` step to carry the normalised code (or an HMAC
   `CryptoKey` imported from it, non-extractable) until the status poll returns `approved` with `pc_key`,
   verifies the MAC, writes the pin and then zeroes the code; the "never kept anywhere" guarantee is kept
   by clearing it on `done`, `failed`, `expired` and on page unmount, and it still never reaches the URL,
   storage or logs. The relay side must keep `pairing_sessions` rows readable after approval long enough
   for the poll to fetch `pc_key`: today `GET /v1/pairing/{id}` answers 404 only for `open` rows and no
   sweeper deletes approved rows (`routes/pairing.py::status`, `pcs.py` expires only `open`/`claimed` on PC
   deletion) **[repo]**; WP1.2 records "approved rows stay readable for at least 5 minutes" as a tested
   invariant so a later retention change cannot break pinning.
4. **Why the server cannot cheat.** It knows only `SHA-256("dome-pair-handle-v1|" + code)`; the code is 20
   Crockford symbols, 100 bits (`version.json pairing_code_symbols: 20`) **[repo]**, so a MAC for a
   substitute key cannot be ground out. A 6-digit comparison shown to the user would be grindable and is
   deliberately not used here.
5. **Failures.** The phone left the page before approval, or the agent is older than 1.4 → no pin → the
   Screen tile says "Pair again so your phone can check it is your PC". A PC re-linked with `--new-key` →
   pin mismatch → hard stop "This PC's identity changed. Pair again." **Existing pairings (today the
   founder's iPhone) re-pair once**: a camera-scanned verification page would open Safari, whose IndexedDB
   is separate from the home-screen app that holds the controller key [judge; confirm on device], so
   re-pairing is the honest path.

### 4.2 Session start: the offer, signed by the phone's controller key

1. **ICE servers and allowance.** `screen_ice_request{pc_id}` on the **bound** controller socket (bound by
   `hello_proof`, so the request is tied to the paired key, not merely to an account session). The relay
   checks: the socket announced 1.4; a live grant with `screen` exists for this controller on this PC; the
   PC is online and past its first snapshot; the allowance is not exhausted (no reservation yet); rate
   limits (6/min per controller, 60/h per account); the per-account TURN caps (§13); the operator kill
   switch. It mints the phone's TURN credential (§13) **bound to this controller**: at most one
   outstanding credential per controller, so minting a new one first revokes the previous, and the
   credential is revoked again if no `screen.session_start` from this controller arrives within
   `screen_start_command_lifetime_seconds` (60 s). It answers `screen_ice_servers{pc_id, ice_servers,
   expires_at, allowance{unlimited, remaining_seconds, window_resets_at}, turn:
   available|unavailable|disabled}`.
2. **The offer.** `RTCPeerConnection({iceServers})`; `addTransceiver('video', {direction:'recvonly'})`;
   `addTransceiver('audio', {direction:'recvonly'})` always, because adding audio later pauses an unmuted
   video on iOS [research]; `createDataChannel('dome-screen-v1', {ordered: true})`;
   `setCodecPreferences`; `setLocalDescription`; wait for gathering complete or 3 s. **Vanilla ICE**: no
   candidate travels separately (aiortc gathers everything before answering anyway **[inspected]**), so
   there is no second signed stream.
3. **The signed command** `screen.session_start{offer_sdp, surface, audio, takeover}` uses the existing
   ES256 envelope (ADR-0001 D6). The phone's `a=fingerprint:sha-256` is inside the signed bytes. The
   controller gives this command a **60 s lifetime** (`limits.screen_start_command_lifetime_seconds`, under
   `max_command_lifetime_seconds: 300`), because a consent wait of 30 s must fit; `timeout_ms` is 45000 and
   the command window is checked once at receipt (`rules.command_window_and_confirmation`).
4. **The relay** applies the existing `route_command` checks (signature, kid = socket kid, binding, grant
   coverage, rate limits, PC online, `supports_screen` on the agent socket), reserves the first 60 s of
   allowance (§10), and forwards the envelope **verbatim** with `relay.screen{granted_until|null,
   unlimited, stun_urls}`. The `commands` row stores only the digest, as today **[repo]**.
5. **The agent** re-verifies against its **local** grant (`authz`), then checks in order: remote control
   on; session unlocked and not on the secure desktop; `relay.screen` present and valid; cooldown and
   ask-mode consent; the strict SDP check: ≤ 12 KiB, printable ASCII plus CRLF only, exactly one
   `a=fingerprint:sha-256` value, m-lines exactly `video recvonly`, `audio recvonly` and one `application`
   section, H.264 or VP8 offered. Anything else is `SCREEN_OFFER_INVALID`. Only then does it spawn the
   worker. The journal records only the result frame, which holds no SDP and no address **[repo:
   `journal.frame_json`]**.

### 4.3 The answer, signed by the PC's identity key

The worker produces a send-only answer with a fresh per-session DTLS certificate. The **agent** signs:

```
screen_answer_payload {
  type: "screen_answer", protocol_version: "1.4",
  account_id, pc_id, controller_id,
  command_id,            // the screen.session_start it answers
  screen_session_id,     // 22 chars, agent-issued
  offer_digest,          // base64url SHA-256 of the exact offer_sdp bytes
  answer_sdp,            // ≤ 12 KiB
  issued_at, expires_at  // ≤ screen_answer_lifetime_seconds (60)
}
envelope { v: 1, alg: "ES256", kid: pc_kid, payload, sig }
```

- **Routing.** The agent sends `screen_answer{envelope, ref_controller_id}`. The relay checks that the
  controller belongs to the PC's account and holds a live `screen` grant, strips the reference, and
  delivers `screen_answer{pc_id, envelope}` only to that controller's 1.4 sockets, preferring the one that
  issued the command (`ConnectionManager.send_to_controller(controller_id, preferred_conn_id, …)`,
  `manager.py:370` **[repo]**). Never to subscribers: SDP contains addresses. No `commands` row, no log
  content.
- **The answer is not in the command result**, because results are journaled with `frame_json` in the
  agent's SQLite **[repo: `store.py`]** and SDP must never be persisted. `screen_session_result` carries
  ids, geometry, flags and the allowance only; the phone waits for both frames, in either order.
- **The phone accepts the answer only if all hold:** `envelope.kid` equals the pinned `pc_kid` for that
  `pc_id`; the signature verifies with the pinned JWK; account, controller, PC and `command_id` are its
  own; `offer_digest` matches the offer it sent; the window is fresh within ±5 s skew; every
  `a=fingerprint:sha-256` in the SDP is identical. Only then `setRemoteDescription({type: "answer", sdp:
  payload.answer_sdp})` with exactly the signed string.

### 4.4 Why this is end to end, and what the relay can still do

- The phone authenticates the PC: WebRTC checks the remote certificate against the fingerprint in the
  remote SDP, and the phone uses only the PC-signed SDP. The PC authenticates the phone: aiortc 1.15.0
  fails the DTLS handshake on a fingerprint mismatch (`rtcdtlstransport.py:446-449`) **[inspected]**, and
  the PC uses only the phone-signed SDP. Neither DTLS private key leaves its device (WebKit does not expose
  `RTCCertificate` keys; aiortc keeps its key in the worker's memory). A relay that rewrites either SDP
  breaks a signature; one that forwards both unchanged sees fingerprints and ICE passwords, which are not
  enough to join the DTLS session.
- The data channel is authenticated by the same DTLS session, so heartbeats and `bye` come from the phone
  that signed the offer and need no per-message signature.
- Replays fail: a replayed start is answered with the journaled result and starts nothing
  (`rules.duplicate_command`); a replayed answer fails the `command_id`/`offer_digest` checks.
- **No renegotiation, ever.** Any transport change (network change, a different monitor, recovery) is a
  new signed session. aiortc has no ICE restart **[inspected]** and this keeps the rule simple.
- What the relay can still do: drop or delay frames, refuse allowance, see that a session happened and
  how long, see both devices' addresses in passing SDP (never logged), and, in v1, see the coordinates of
  taps and typed text in input batches as it does today (T21). The Phase 3 "private input" item moves input
  onto the data channel.
- Hygiene: `offer_sdp`, `answer_sdp`, `sdp`, `candidate`, `ice_servers`, `credential`, `username` join
  `REDACT_EXACT` in `cloud-api/dome_api/logging.py` and the agent's `logsetup.py`; the phone's `log.ts`
  drops the same keys and diagnostics never include the session. Key-based redaction is not enough on the
  PC: aioice logs candidate pairs with both peers' `ip:port` at **INFO** (`ice.py::__log_info` →
  `logger.info`: `Check <pair> …`, `Remote candidate "…" resolved to <addr>`, `Discovered peer reflexive
  candidate …`) **[inspected]**, the agent's default `LOG_LEVEL` is INFO (`settings.py:106`) and
  `redact_text_line` masks only bearer tokens and 40+ character tokens **[repo]**, so without more the PC's
  LAN addresses, the phone's public address and the TURN relay address would reach the rotating log, the
  crash log and the support diagnostics bundle (`diagnostics.py` tails 200 raw lines). Therefore: (1) the
  worker, and any module that imports aiortc, aioice or pylibsrtp, pins `aioice`, `aioice.ice`,
  `aioice.turn`, `aioice.stun`, `aiortc`, `aiortc.rtcdtlstransport`, `aiortc.rtcicetransport` and
  `aiortc.rtcpeerconnection` to WARNING regardless of `LOG_LEVEL`, mirroring cloud-api's
  `pin_sdk_loggers` **[repo: `logging.py:332`]**; (2) `redact_text_line`, the structured `redact_value`
  and `crashguard.redact` gain an IPv4 / IPv6 (with optional `:port`) masking regex, so a library line that
  slips through at WARNING still carries no address; (3) the worker's stderr is not a file: it is inherited
  by the agent, which reads it line by line through the same redacting pipeline into its own log at WARNING,
  and the worker's crash log is written only through `crashguard` (masked). Loopback addresses are masked
  too, which lets the sentinel test in §17.2 assert that `127.0.0.1:<port>` and `::1` never appear.

## 5. Capture, encoding and audio, with licensing verdicts

### 5.1 The worker process

- `DoMe.exe screen-worker` is an argv dispatch inside the existing PyInstaller onedir, started by the agent
  with the **same user token, never elevated**, an anonymous pipe (length-prefixed JSON, no listening
  socket) and a **Job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`** (pywin32 `win32job`, no admin
  needed): if DoMe.exe exits or crashes, Windows kills the worker, so it can never capture without its
  parent. It also exits on pipe EOF. It is spawned per session; a warm worker is Phase 3, only if the spike
  measures spawn plus import above ≈ 1.5 s.
- It contains aiortc, our PyAV/FFmpeg build and the capture and audio code; it imports none of
  `dome_agent.api`, `relay_client`, `assistant`; it writes no file except a crash log (through
  `crashguard`, masked, including addresses per §4.4) that never holds frame or audio data; its stderr goes
  to the agent's redacting log pipeline, never to a file of its own; it pins the aiortc/aioice loggers to
  WARNING (§4.4); it is **send-only**: incoming media sections are rejected and no decoder runs on network
  input (the single decoder in the build, `wrapped_avframe`, only unwraps the local capture device's own
  frames, §5.2). `aiortc.contrib` (which contains `MediaRecorder`) is excluded from the frozen build.
- A GPU-driver or Media Foundation crash therefore cannot take down the tray, commands or the touchpad
  that "works great"; `crashguard` covers the worker and the agent reports `capture_failed`.
- **Capture starts only after `connectionState == "connected"`**, i.e. ICE and the DTLS handshake with
  the signed certificate. No frame exists for an unauthenticated peer.

### 5.2 Capture

- **Primary: DXGI Desktop Duplication through FFmpeg `ddagrab`**, opened by PyAV as an `lavfi` graph:
  `ddagrab=output_idx=N:framerate=30:draw_mouse=1:dup_frames=1, scale_d3d11=width=W:height=H:format=nv12,
  hwdownload, format=nv12`. GPU colour conversion and scaling, no per-pixel Python. **Revised 2026-10-27 (device
  round 2):** the graph shipped with `scale_d3d11=w=W:h=H`, option names `vf_scale_d3d11.c` (n8.1.3) does not have
  (`w`/`h` are `scale`'s), so Desktop Duplication failed on every PC and the Windows.Graphics.Capture fallback ran;
  the worker's self-test and the licence gate now parse the graph's options without a desktop (pc-agent/DECISIONS.md
  97). Desktop Duplication is relied on only once it has given a frame: a first open still waiting for a desktop
  change after 1.5 s, or a graph that fails before its first frame, switches the session to Windows.Graphics.Capture,
  whose first frame comes at once (pc-agent/DECISIONS.md 104). **Revised 2026-10-27
  (device round 1):** `dup_frames=0` was the design, but in FFmpeg n8.1.3 `ddagrab`'s `request_frame` then loops
  until the desktop changes, so a `lavfi` read *blocks* on a still screen instead of returning `EAGAIN`, and
  `av.open` itself waits for a real frame (`probe_output_format`); the build ships `dup_frames=1` and the worker
  drops re-sent frames by a CRC-32 signature of the visible bytes, keeping the 1 fps floor time-driven
  (pc-agent/DECISIONS.md 94, KNOWN_ISSUES 32). `output_idx` is the index among the **default adapter's**
  outputs, not DoMe's monitor index: the worker maps the monitor to a DXGI output by HMONITOR and, when the
  monitor hangs off another adapter, goes straight to the Windows.Graphics.Capture fallback (DECISIONS 93).
  `ddagrab` sets per-monitor-v2 DPI itself **[inspected]**. **The
  `lavfi` device needs one decoder**: libavdevice's `lavfi_read_header()` sets the video stream's codec to
  `AV_CODEC_ID_WRAPPED_AVFRAME` and `lavfi_read_packet()` wraps the `AVFrame` struct itself in the packet
  (`libavdevice/lavfi.c`, FFmpeg master, **[inspected]**), so PyAV's `container.decode()` must run the
  `wrapped_avframe` decoder to turn those packets into frames. The build therefore enables exactly that one
  decoder (`--enable-decoder=wrapped_avframe`, LGPL, no parser, it only unwraps frames the local device
  produced and never touches network input; §5.5, T32). That PyAV drives this graph is **not yet verified**
  and is Phase 0 item 1; the fallback is PyAV's `av.filter.Graph` API (`ddagrab` added as a source filter,
  `buffersink` pulled directly, no decoder involved), then CPU `hwdownload` + `swscale` at 15 fps. The choice
  is recorded in ADR-0002 Decision 4.
- **Variable frame rate with a floor:** only changed frames are sent, but the worker keeps the **last raw
  NV12 frame** and **re-encodes** it at ≥ 1 fps while the screen is still (re-sent duplicate frames are dropped
  by signature before encoding), so the phone's stall detector stays meaningful. Encoded packets are never re-sent: feeding a P-frame
  to the decoder twice applies its motion and residual twice and corrupts the picture until the next IDR.
  Re-encoding a still frame yields a near-empty skip P-frame. WP1.4 asserts on the fake source that two
  consecutive still-frame packets are distinct encoder outputs, not byte-identical copies.
- **Fallback: Windows.Graphics.Capture** (Windows 10 1903+) through `windows-capture` (MIT, Rust abi3
  `.pyd`) when DDA reports `UNSUPPORTED` (hybrid-GPU laptops, which are common) or
  `NOT_CURRENTLY_AVAILABLE` (the 4-duplication limit) [research]. On Windows 10 its yellow border cannot
  be turned off [research]; it is an extra indicator, not DoMe's. FFmpeg's own `gfxcapture` filter needs
  FFmpeg 8.1+, which is **unverified** against the FFmpeg that PyAV ≤ 17 (aiortc's `av<18` pin) builds
  against; it is not the plan.
- **Rebuild instead of fail:** `ddagrab` errors on access-lost or a desktop switch and does not recreate
  itself **[inspected]**; the worker rebuilds the graph on `EVENT_SYSTEM_DESKTOPSWITCH`, unlock, mode or
  monitor change, bumps `gen`, sends `geometry` and forces an IDR. After 3 failed rebuilds:
  `capture_failed`.
- **Never read:** the secure desktop, lock screen and UAC prompt (a normal-user process gets
  `E_ACCESSDENIED` [research]). Protected content (`WDA_MONITOR` windows, DRM video) shows black; DRM
  audio is not delivered by loopback [research]; the help text says so.
- **Geometry:** each `gen` carries `{surface_index, gen, width, height, rotation, reserved[]}` where
  `reserved` holds the normalised rectangles of DoMe's own bars (§8). Rotated, HDR and mixed-DPI monitors
  are **not yet verified**.

### 5.3 Video encoding

- Output size fits **H.264 level 3.1** (≤ 3,600 macroblocks per frame, ≤ 108,000 per second), which Safari
  offers as `42e01f` [research]: 16:9 → 1280×720; 16:10 → 1152×720; 4:3 → 960×720; 21:9 → 1280×536. Long
  edge ≤ 1280, short edge ≤ 720, even dimensions [estimate of the rule; verify what Safari accepts]. The
  encoder follows the `profile-level-id` the phone negotiated (WebKit 259987) [research].
- Encoder order, probed once per worker start: (1) `h264_mf` with `hw_encoding=1`,
  `scenario=display_remoting`, `rate_control=ld_vbr` or `cbr`, `max_b_frames=0`, the low-delay flag
  (`mfenc.c` options **[inspected]**): this already reaches NVIDIA, Intel and AMD hardware through Media
  Foundation, so **no vendor SDK encoders** are built; (2) `h264_mf` software (missing on Windows N without
  the Media Feature Pack [research]); (3) libvpx VP8 at 720p ≤ 15 fps through aiortc's own encoder
  ("Basic picture mode" on the phone). A PC where H.264 failed records it locally and offers VP8 first next
  time.
- Packets go through aiortc's **pre-encoded path** (`encoder.pack(av.Packet)`, `rtcrtpsender.py:311-323`
  **[inspected]**), so aiortc's hard-coded `libx264` (`codecs/h264.py:270` **[inspected]**) is never used.
- **Parameter sets in band.** aiortc's `H264Encoder.pack()` only splits the packet on Annex B start codes
  and packetizes (`codecs/h264.py::_split_bitstream`, `_packetize` **[inspected]**); nothing inserts SPS/PPS.
  FFmpeg's `mfenc.c` exposes them as `extradata` (`MF_MT_MPEG_SEQUENCE_HEADER`) and copies MFT sample bytes
  unchanged, and whether a given hardware MFT repeats SPS/PPS on every IDR varies by vendor. A Safari decoder
  that misses the new parameter sets after an encoder reopen (new resolution → new SPS, bitrate step) or a
  PLI-triggered IDR shows nothing until the stall detector re-sessions. Worker requirement: **every IDR
  access unit handed to `pack()` begins with SPS and PPS NALs**; if the encoder output lacks them, the worker
  prepends `codec_context.extradata` (Annex B) first. WP1.4 asserts on the fake source that NAL types 7 and
  8 precede every type-5 NAL; "SPS/PPS repetition per vendor MFT" is on the Phase 0 device checklist item 1.
- IDR on join, on PLI/FIR (rate-limited to 2/s) and on geometry change; no periodic IDR unless the device
  round shows stalls (then 20 s). **How the worker learns of a PLI:** with the pre-encoded path aiortc
  1.15.0 never surfaces PLI/FIR to the application. `_handle_rtcp_packet` turns them into
  `self._send_keyframe()`, which sets the name-mangled `__force_keyframe`; `_next_encoded_frame` reads and
  clears that flag only in its `isinstance(data, Frame)` branch, while the `else` branch calls
  `self.__encoder.pack(data)` with no keyframe argument and never clears it (`rtcrtpsender.py:277-281,
  311-323, 351-355` **[inspected]**); there is no public attribute, event or callback. The design therefore
  **wraps the sender's `_send_keyframe`**: after `addTrack`, the worker replaces
  `sender._send_keyframe` on that instance with a function that sets an `asyncio.Event` the capture loop
  awaits (the original is not called; the flag would otherwise stay set forever on this path). aiortc is
  pinned `==1.15.0` by hash in the frozen build, a unit test fails if `RTCRtpSender._send_keyframe` or the
  PLI dispatch to it disappears, and `--self-test` checks the hook at start-up; if the hook is unavailable
  at runtime the worker falls back to a periodic IDR every 2 s for that session and reports `pli_hook:
  missing` in its state frame. The alternative of reading `sender._RTCRtpSender__force_keyframe` from the
  packet loop was rejected as a second private dependency with the same fragility and no event semantics.
  `tests/test_e2e_screen.py` sends a PLI from the aiortc "phone" and asserts the next packet from the fake
  source is a keyframe within 500 ms.
- Bitrate: fixed caps, **1.5 Mbps direct and 1.0 Mbps relayed** (the path is known after ICE selection),
  "Data saver" 0.5 Mbps when the TURN guard says so (§13). `h264_mf` cannot change bitrate mid-stream through
  FFmpeg [research, inferred], so the worker steps down by reopening the encoder (≈ 100 ms [estimate]) with
  an IDR when the receiver-report loss fraction stays above 10 % for 5 s: 1.5 → 0.8 → 0.5 Mbps. Receiver
  reports come from `RTCRtpSender.getStats()` (`RTCRemoteInboundRtpStreamStats`) **[inspected]**, not from
  patching aiortc internals. No REMB wiring in Phase 1.
- Phase 2 and 3 quality items, behind device data: a cursor overlay (`draw_mouse=0`, shape and position on
  the data channel, drawn crisp on the phone), a sharpening refresh frame after 300 ms still, and the
  native-resolution region crop for zoom (§8).
- **Implementation notes (device round 2, 2026-10-27; pc-agent/DECISIONS.md 98 to 103).** On the founder's PC the
  first encoded packet came 5.5 to 9.2 s after the first captured frame: the synchronous software transform held
  frames and the still screen fed it at the 1 fps floor. Now: `h264_mf` is opened with the low-delay flag (which is
  what makes `mfenc.c` send `CODECAPI_AVLowLatencyMode`) and `max_b_frames = 0` on the context; after a new picture
  or a forced IDR the worker re-encodes the held frame at the capture cadence until that picture's packet comes out
  (the **drain pump**, at most 3 s), so the latency is about N / fps for an encoder that holds N frames whatever the
  transform does with the flag, and a due IDR on a still screen is encoded at once; no IDR within 5 s of the first
  frame, or an encoder error, is reported once (`encoder_stalled`) and the next start asks for VP8 first for 24 hours
  (the fallback this section already promised; across sessions, because 1.4 has no renegotiation). The probe runs at
  the real output size and logs FFmpeg's reason per skipped encoder; the bitstream is hardened (in-band SPS/PPS
  preferred over extradata, AVCC recognised and converted). RTP payloads are capped at 1200 bytes (aiortc's
  `PACKET_MAX`, 1300). The worker logs what the media path did (the transform's name, open time, SPS
  profile/level bytes, time to first keyframe, a counters line every 5 s, the transport's candidate types and address
  family, never an address) and the agent keeps the counters in the diagnostics bundle. Still open: the hardware
  transform (it needs NV12 input), matching `profile-level-id` to the real SPS, and the CBR budget on a still screen.

### 5.4 Audio (Phase 2)

- Opened only when `screen_audio` is granted **and** the phone turned sound on: WASAPI shared-mode loopback
  of the default render endpoint through our own `comtypes` `IAudioClient`/`IAudioCaptureClient` code
  (comtypes and pycaw are already shipped and proven on the founder's PC, HANDOFF #36 **[repo]**; no new
  dependency). **Sample-format conversion is mandatory:** aiortc 1.15.0's `OpusEncoder.encode()` asserts
  `frame.format.name == "s16"` and a `mono`/`stereo` layout before its own `AudioResampler` runs
  (`codecs/opus.py:52-56` **[inspected]**), while WASAPI shared-mode loopback delivers the endpoint's mix
  format, which is 32-bit float on practically every Windows 10/11 PC; a float32 frame would raise an
  `AssertionError` in the worker's audio task. `audio_loopback.py` therefore converts the mix format to
  interleaved s16 stereo at 48 kHz (its own `av.AudioResampler(format="s16", layout="stereo", rate=48000)`,
  or numpy) before building each 960-sample (20 ms) `av.AudioFrame` for aiortc's Opus encoder (libopus) at
  64 kbps stereo, FEC on. WP2.1 feeds a float32 fake capture client through the real
  `aiortc.codecs.opus.OpusEncoder` in a unit test.
- Loopback delivers nothing while the PC is silent, so the worker generates timed silence [research]; it
  polls the default device every 2 s and reopens on change. Audio and video timestamps come from one
  QPC-based clock.
- The audio transceiver is always negotiated; when sound is off or not granted it sends nothing. The phone
  starts muted, unmutes only inside a `click` handler, and sets `navigator.audioSession.type = "playback"`
  where supported [research]. Whether muting the PC's endpoint also silences loopback is **not yet
  verified** (research Q5); a "Quiet PC while I listen" option appears only if it does not.
- Process loopback (exclude DoMe's own sounds, Windows 11 build 20348+) is Phase 3. PyAudioWPatch
  (Apache-2.0) is the fallback if the comtypes code proves fragile; it is not planned.

### 5.5 Licensing verdicts (closed-source commercial product)

| Component | Licence | Verdict | Source |
| --- | --- | --- | --- |
| Stock PyAV Windows wheels (`av` 17.x/19.x) | Bundle `libx264-165.dll`, `libx265.dll` (GPL-2.0+), imported directly by `avcodec`; `avutil` reports "LGPL version 3" only because pyav-ffmpeg patches `configure`; OpenH264 built from source (no Cisco patent cover) | **Not shippable** | **[inspected]**; [pyav-ffmpeg patch](https://raw.githubusercontent.com/PyAV-Org/pyav-ffmpeg/main/patches/ffmpeg.patch) |
| DoMe's own FFmpeg build: LGPL-2.1+ shared DLLs; `--disable-gpl --disable-nonfree`, **no** `--enable-version3`, `--disable-network`, `--disable-everything` then enable indev `lavfi`; filters `ddagrab, scale_d3d11, hwdownload, format, scale, aresample` plus the synthetic sources `testsrc, color` (for `--self-test` and CI, never used in a session), and `buffer(sink), abuffer(sink)`, which FFmpeg always builds and configure cannot select by name (n8.1.3 answers "did not match anything"; the licence gate checks them in the built library instead); encoders `h264_mf, libvpx_vp8, libopus`; **exactly one decoder, `wrapped_avframe`** (needed by the `lavfi` device, §5.2; it unwraps local frames and parses no bitstream); `--enable-d3d11va --enable-mediafoundation --enable-libvpx --enable-libopus`; no other decoders, no demuxers, no protocols, no openh264, no vendor SDKs | LGPL-2.1+ | **Compatible** with obligations: ship the LGPL text and a written source-and-build-script offer (`THIRD_PARTY_NOTICES.txt`, installer licence page), keep the DLLs replaceable (onedir does), and the EULA must not forbid reverse engineering for debugging such replacements | FFmpeg licence page; LGPL §6 |
| PyAV built against it (the version aiortc accepts, `av>=14,<18`) | BSD-3 | Compatible. The custom wheel is `win_amd64` only; the **stock GPL-bundled PyAV wheel from PyPI is used only in Linux CI unit tests and never ships** (`pyproject.toml` resolves `av` to the custom wheel with `sys_platform == 'win32'` and to PyPI elsewhere, §15; the licence gate runs on the Windows wheel and on `dist/DoMe`) | PyPI metadata [research] |
| aiortc 1.15.0, aioice, pylibsrtp (+ libsrtp) | BSD-3 | Compatible; `aiortc.contrib` excluded from the build | [research] |
| cryptography, pyOpenSSL (OpenSSL 3), google-crc32c | Apache-2.0 | Compatible (notice); `cryptography` already ships via joserfc **[repo]** | [research] |
| pyee, ifaddr, dnspython | MIT / ISC | Compatible | [research] |
| windows-capture 2.x (fallback only) | MIT | Compatible | [research] |
| libvpx VP8 | BSD-3 + Google patent grant | Compatible, royalty-free | [research] |
| libopus | BSD-3 + royalty-free patent licences | Compatible | [opus-codec.org/license](https://opus-codec.org/license/) |
| H.264 encoding through Windows Media Foundation or the GPU driver (DoMe ships no H.264 code) | OS/driver-provided | **Acceptable pending counsel**: Via LA's AVC terms are $0 for the first 100,000 units a year and $0.20 after; Microsoft's 2010 statement that apps calling its codec are covered was informal and disputed, and there is no current statement. If counsel says no, ship VP8 only (more PC CPU, more phone battery) | [Via LA fees](https://www.via-la.com/licensing-2/avc-h-264/avc-h-264-license-fees); [Redmond 2010](https://redmondmag.com/articles/2010/05/03/microsoft-h264-support-not-about-royalties.aspx) [research] |
| H.264 decode on the iPhone | Apple's | No exposure for DoMe | — |
| AAC; x264 / x265; OpenH264 built from source or Cisco's binary | — | **Not used** (Cisco's binary needs a separate user download and toggle; not for v1) | [research] |
| Cloudflare Realtime TURN | Service terms | New sub-processor: the privacy page must say it sees addresses, timing and byte counts, never plaintext | §13 |

**Automated licence gate (CI, unit-tested)**: `pc-agent/packaging/license_gate.py` runs on the built
wheel **and** on the frozen `dist/DoMe` folder and fails the build if any DLL matches
`x264|x265|openh264|fdk|nonfree`, if any DLL import table references them, if `av.codec`'s FFmpeg
configuration string contains `--enable-gpl`, `--enable-version3` or `--enable-libx264`, if the set of
available decoders is anything other than exactly `{wrapped_avframe}`, or if `avcodec`'s `license()`
string is not "LGPL version 2.1 or later". The gate is tested against a planted `libx264.dll` in a
synthetic folder and against a configuration string that enables a second decoder.

## 6. Capabilities and how they are granted on the PC

### 6.1 Contract

- `screen`: "See this PC's screens live during a Screen view session. Human-directed; shows every app,
  notification and window of the unlocked Windows session, not only approved apps. Never recorded, never
  given to an AI."
- `screen_audio`: "Hear the PC's sound during Screen view. Only together with `screen`." The agent refuses
  `screen_audio` without `screen` (`INVALID_PARAMETERS` on the grant change).
- Both join every closed capability enum (`actions.json capabilities`, the four enums in
  `relay-frames.schema.json`: `grants_snapshot`, `pairing_request`, `pairing_decision`,
  `agent_grant_update`, and `rest.schema.json`). `screen.*` actions are `ai_eligible: false`,
  `routine_allowed: false`; `rules.ai_eligibility` names screen and audio media, signaling and the data
  channel. Existing grants never gain them; `grants_snapshot` only intersects.
- Combinations: `screen` alone → view only; `+ pointer` → tap, scroll, drag; `+ keyboard` → type and the
  D-pad; `+ screen_audio` → sound. The TV remote needs `keyboard` (plus `media`, `volume`, `apps` for those
  buttons) and **not** `screen` while the picture is off.

### 6.2 Granting

- Only in the DoMe grant window (§2.1), opened from the tray's per-phone menu (`tray.py::_phone_menu`,
  `labels` in `_open_approval_window` **[repo]**). `pairing.py` generalises `INPUT_CAPABILITIES` to
  `LOCAL_ONLY_CAPABILITIES = ("pointer", "keyboard", "screen", "screen_audio")`;
  `Agent.update_grant_capabilities` accepts them; the agent sends `grant_update` with the exact list and
  the relay replaces the grant row as today (`rules.grant_update`) **[repo]**.
- Narrowing is immediate everywhere: tray untick, CLI `--remove-screen`, "Stop and turn off…" on the bar,
  revoke. Each ends a live session (`grant_removed` / `controller_revoked`).

### 6.3 Ask mode and the post-Stop cooldown

- Ask mode is a per-grant local setting (SQLite, never sent to the server). The cooldown is a **per-PC**
  timestamp set when someone at the PC presses Stop, and it applies to **every** controller: a Safari tab and
  the home-screen app are two controller installations with two keys (§4.1 step 5), so a per-controller
  cooldown would be bypassed by opening the other installation with `takeover: true`. The history row
  records which phone was stopped. The cooldown's length is the longest value among the PC's `screen`
  grants' settings (default `screen_pc_stop_cooldown_default_seconds` = 600; 60; or off, per grant; a phone
  whose own setting is "off" is still asked during another phone's cooldown). The secure default is on
  because a Stop at the desk is the clearest signal the person at the PC did not want to be watched; the
  setting exists because a one-person household pays a UX cost for it.
- During the cooldown, or with ask mode on, a start shows the consent window; Deny →
  `SCREEN_CONSENT_DECLINED`; 30 s without an answer → `SCREEN_CONSENT_TIMEOUT`. **Nobody can keep a dialog
  in front of the person at the PC:** at most one consent window opens per minute per PC, and after a Deny
  or a timeout further starts from **every** controller are refused with `SCREEN_CONSENT_DECLINED` and no
  window for an escalating interval (1, 2, 5, then 10 minutes per consecutive refusal), reset by an Allow or
  by the cooldown ending. A start refused this way is answered at once (the phone shows "Not allowed at the
  PC"), and `screen.session_start` keeps its 6/min relay limit on top. The consent window also offers
  **Block this phone for 1 hour** (a per-controller block, refused with `SCREEN_CONSENT_DECLINED`, no window)
  and **Turn off Screen view for this phone** (`grant_update` without `screen` / `screen_audio`). Declined,
  timed-out and blocked starts are written to the local history and reported as the security event
  `screen_consent_declined{reason: denied|timeout|backoff|blocked}` (no names). Turning ask mode off or
  shortening the cooldown counts as widening and needs the DoMe window. Unit tests (WP1.3): a second
  controller's start during another phone's cooldown is asked; the third refused start within a minute opens
  no window and is refused at once.

### 6.4 DoMe never injects input into its own permission UI (new `rules.local_permission_ui`)

- While a DoMe permission window is open (pairing approval, grant window, consent window, Stop-menu
  confirmation), the input dispatcher drops every pointer button and keyboard event from every phone,
  counts them, and sends `INPUT_PAUSED_ON_PC` at most once per second; the phone shows "Waiting for someone
  at the PC".
- Before injecting a button press the agent compares `GetCursorPos` with the registered rectangles of its
  bars and windows; a press that would land on DoMe UI is dropped (`INPUT_TARGET_RESERVED`). The bar is
  `WS_EX_NOACTIVATE`, so keyboard focus never lands on it.
- Why: a phone cannot click its own Allow, and an accidental tap cannot trigger a Stop or the cooldown.

### 6.5 Honest limit, for `SECURITY.md`

A phone that already holds `keyboard` can open a command prompt and run programs as the user, so a
determined holder could capture the screen without DoMe. §6.4 stops casual and blind self-granting through
DoMe; it is not a boundary against that holder. The separate `screen` grant still matters: it protects
phones that hold only `screen` (watch-only family members), it makes viewing a deliberate decision made at
the PC, and it guarantees the indicator for everything DoMe does. The keyboard scope text gains one
sentence: "A phone with Keyboard can also run programs on this PC, as you could."

## 7. Session lifecycle and the PC indicator

`pc-agent/dome_agent/screen/session.py::ScreenSessionManager` copies `InputSessionManager`'s structure
**[repo: `input_session.py`]**: one live session per PC, a 22-character id, a watchdog independent of the
relay socket, a fixed end order. End order: retire the id (late data-channel messages and `pointer_move_to`
are dropped) → tell the worker `stop` → wait ≤ 2 s → `TerminateProcess` → close the bars only once the
process has exited → emit `screen_session{ended, reason, live_seconds}` → notify → write the history row →
revoke the phone's TURN credential (relay, best effort).

| Phase | Mechanism |
| --- | --- |
| **Start** | §4.2–4.3. `screen_session_result{screen_session_id, surfaces[{index, width, height, primary}], surface_index, gen, audio, lease_seconds, allowance{unlimited, remaining_seconds|null, resets_at|null}, consent}`; `screen_session{started, path: direct|relayed}` after DTLS and the first frame. No names, no SDP, no addresses. |
| **Viewer lease** | The phone sends `hb` on the data channel every 2 s (`screen_heartbeat_seconds`); no heartbeat for **8 s** (`screen_lease_seconds`) ends the session `viewer_gone`. The heartbeat is DTLS-authenticated, independent of the relay and in no server's path. Receiver reports from `getStats()` are a secondary liveness signal (used by the Phase 3 PiP experiment, where page JavaScript is throttled). |
| **Allowance ticket** (dead-man switch) | About 20 s before `granted_until` the agent sends `screen_usage{screen_session_id, live_seconds, relayed, relayed_bytes}`; the relay answers `screen_allowance{screen_session_id, granted_until|null, unlimited, final, reason?, resets_at?}` in 60 s chunks (§10). The agent's figures are informational (dashboards, the pause discount): the ledger is charged on the **relay's clock** (§10). When `granted_until` passes without a new ticket the agent ends the session: `allowance_exhausted` if the relay said `final` with no other reason, `controller_offline` if `reason: controller_offline`, otherwise `allowance_unconfirmed`. **Because the PC must ask, a dead session never consumes allowance**, and a PC cut off from the relay stops within 60 s on every plan, so an account-side revocation takes effect even if the PC never hears it. |
| **Relay socket lost (PC)** | The stream continues within the current ticket (≤ 60 s) while the agent reconnects. After reconnecting, `grants_snapshot` is applied **first** (a revocation ends the session), then tickets resume. |
| **Relay socket lost (phone)** | Media continues through a short blip; input pauses ("Clicks paused: reconnecting") and the phone restarts its input session when the socket returns. **The owner must stay present:** when the owning controller has had no bound socket for 60 s, or its sockets were closed by `revoked`, by logout or by account-session revocation (`DELETE /v1/account/sessions/{id}`), the relay answers the next `screen_usage` with `screen_allowance{final: true, reason: controller_offline}` and, if a ticket is outstanding, sends that frame at once, so the PC ends within one ticket (`controller_offline`). Without this, media being independent of the relay socket would let a signed-out (stolen) phone keep a live Screen view for up to 4 h on its data-channel heartbeat alone, and `SECURITY.md` T4's "logout / session revocation cuts the phone off" **[repo: `SECURITY.md:68`]** would be false for Screen view; WP1.9 amends T4 to "signing a phone out also ends its Screen view within about 60 s". The 60 s grace keeps Wi-Fi blips harmless. Integration test: "logout of the owning controller ends the stream within one ticket". |
| **Stop from the phone** | `bye` on the data channel and the signed `screen.session_stop`, whichever arrives first; then `pc.close()`. |
| **Stop at the PC** | Bar Stop, tray "Stop Screen view", the hotkey **Ctrl+Alt+Shift+S** (`RegisterHotKey`, registered only while a session is live; if it fails the bar says so; **not yet verified**). All end `stopped_on_pc` and start the cooldown. The phone never restarts by itself after this. |
| **Takeover** | A second phone with `screen` gets `SCREEN_SESSION_OWNED` unless `takeover: true`; its own ask mode, the PC-wide post-Stop cooldown and the consent back-off (§6.3) apply, so a takeover never skips a consent window that the stopped phone would have faced. The old session ends `takeover`; the bars and a notification name the new viewer; the old phone shows "Another phone took over Screen view". A same-phone restart replaces its own session (`replaced`). |
| **Expiry** | Free: §10. Every plan: `screen_session_max_seconds` = 14400 (`max_duration`); the phone asks "Still watching?" at 3 h 55 min and Continue starts a fresh signed session. Free also gets "Still watching?" after 5 idle minutes (no touches) to protect its minutes. |
| **Lock / secure desktop** | Win+L or a locked session ends it at once (`session_locked`), as input does. A UAC or other secure desktop **pauses** it: the worker stops capture, sends `state{paused, secure_desktop}`, the phone shows "Windows is asking for permission on the PC. Someone at the PC must answer", the agent emits `paused`/`resumed` frames, and after 120 s paused the session ends (`secure_desktop`). On Free the relay discounts a pause only when it saw the `paused`→`resumed` pair itself and only up to 120 s per session (§10): the PC's word alone never buys minutes. |
| **Emergency stops** | "Disable remote control" ends `remote_disabled` **before** `input.end` returns (`agent.py::set_remote_enabled`) and refuses new starts; revoke → `controller_revoked`; un-grant → `grant_removed`; plan change → `plan_disabled`; quitting DoMe kills the worker through the Job object and the next start records `agent_restart` in history; a worker crash → `capture_failed`; no DTLS within `screen_connect_timeout_seconds` = 20 → `connection_failed`, charged 0 minutes only when the relay itself saw no `started` frame and at most 20 s passed (§10). All of these work offline; no server is involved. |
| **PC indicator** | Visible from before the worker starts until the worker has exited, and **fail-closed**: the agent first enumerates monitors, creates a bar on every one of them and the tray badge, and confirms each window exists (`winfo_exists`, a non-zero HWND); only then does it spawn the worker. If enumeration returns nothing, a `Toplevel` cannot be created, `AgentUI` has no display or the UI thread does not confirm within 3 s, the start is answered `SCREEN_CAPTURE_UNAVAILABLE{detail: indicator}` and nothing is captured. One borderless, topmost Tk `Toplevel` per monitor (the tray already uses tkinter windows **[repo: `ui.py`]**), `WS_EX_NOACTIVATE`, with `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` (Windows 10 2004+); on older builds `WDA_MONITOR` and the bar shows black in the stream; if both affinity calls fail the bar simply appears in the picture, which is harmless (the bar's existence, not its exclusion, is the guarantee). On `WM_DISPLAYCHANGE` the agent re-enumerates and the worker **pauses capture** (`state{paused, indicator}`) until a bar exists on every monitor again; if that takes more than 10 s the session ends `capture_failed`. Liveness runs both ways: the agent sends a tick every 3 s over the pipe and the worker **stops capture and exits if ticks stop for 10 s**, so a frozen UI thread cannot leave an unattended stream behind bars that no longer repaint; the Job object covers agent exit. Exclusive full-screen games can hide topmost windows; the tray badge and the hotkey remain. **Not yet verified on a device.** Tray: a new `_COLOURS["screen_view"]` badge, tooltip "DoMe — Screen view on (*phone*)", a "Stop Screen view" item next to "Stop manual input" **[repo: `tray.py`]**; `StatusView` gains `screen_session: {phone_name, since, audio} | None` and the history list. Unit tests (WP1.3/WP1.5): a fake UI that refuses to create a window → `SCREEN_CAPTURE_UNAVAILABLE{indicator}` and no worker spawn; ticks stopped → `FakeScreenWorker` sees `exit` within 10 s. |
| **Recovery** | `recover_after_restart` emits `ended/agent_restart` for any session in `screen_session.json`, which holds the id and owner only. |

Phone lifecycle: `bye` and close on `visibilitychange` to hidden; stall detection: `requestVideoFrameCallback`
(or `framesDecoded` from `getStats`) silent for 2 s while the PC's `state` says `sending`, or black video
after an app switch (WebKit 231365) [research] → recreate the session, at most twice, then the failure
state; Wi-Fi ↔ cellular (`iceConnectionState` failed) → the same. A screen wake lock is held while viewing
(home-screen app from iOS 18.4) [research]. **Revised 2026-10-27 (device round 2; mobile-app DECISIONS 80 to 82):**
the video track arrives inside `setRemoteDescription` and is kept until the answer verified (dropping it was why the
founder's phone never left "Connecting"); after DTLS a **first-frame watchdog** asks for a keyframe at 3 s and 8 s
and recreates the session at 15 s (counted with the stall restarts), held while the PC is paused and when the
picture only waits for the owner's tap; when H.264 arrived but never decoded the fresh session offers VP8 only.
`getStats()` is read through a whitelist into the phone's diagnostics, and staging builds show it on the stage.

No `pc_state` change in Phase 1: other phones learn of ownership from `SCREEN_SESSION_OWNED` and from the
`screen_session` lifecycle frames, which the relay broadcasts to the PC's 1.4 subscribers (skipping older
sockets, as it does for 1.0 and `input_session`). This avoids the per-version `pc_state` stripping problem
(`PROTOCOL.md` §16 #17; `manager.py:42 PC_STATE_1_1_FIELDS` **[repo]**); generalising that tuple into a
per-version map is the right fix when a `pc_state.screen_session` field is wanted (Phase 2 Health page).

## 8. Tap to click, scroll, pinch to zoom: the absolute pointer primitive

### 8.1 New input events (contract 1.4, `input_event`)

- `pointer_move_to{x: 0..65535, y: 0..65535, gen: 1..2147483647}`: normalised over the **currently
  streamed surface** at geometry generation `gen`. Not PC pixels: the ±4096 limit and negative
  virtual-desktop origins rule pixels out. Capability `pointer`. Requires a live screen session **owned by
  the batch's controller** (`SCREEN_SESSION_REQUIRED`) and `gen == current` (`SCREEN_GEOMETRY_CHANGED`);
  failing batches are dropped and counted in `input_ack.dropped_events` with one `error` frame per code per
  second; the input session continues (`rules.input_sessions` pattern **[repo]**). A blind absolute pointer
  without the picture is refused. **Delivery of these two rejections needs a relay change:** today
  `agent_ws.py::_on_agent_error` forwards an agent `error` frame to the controller only when its code starts
  with `INPUT_` and `ref_controller_id` is set, and logs everything else (`agent_ws.py:526`) **[repo]**, so
  `SCREEN_SESSION_REQUIRED` and `SCREEN_GEOMETRY_CHANGED` would never reach the phone and the §16 copy for
  them could never be shown. §12 extends that prefix filter to `INPUT_` **and** `SCREEN_` (same
  `ref_controller_id` / owner checks, same `send_input_frame_to_controller` delivery, which already filters
  by socket version, `manager.py:412` **[repo]**); the codes keep their names because the condition is about
  the screen session, not the input grant. Integration test: "a `SCREEN_*` rejection frame reaches the
  issuing controller's 1.4 socket and no subscriber".
- `pointer_wheel{dx, dy}`: integers in Windows wheel units, 120 = one notch, ±1200 per event, for smooth
  two-finger scrolling (today's `pointer_scroll` is whole notches **[repo]**). Capability `pointer`.
- `key` enum gains `f11` (full screen in apps that are not approved; approved apps already have
  `app.set_fullscreen`). Capability `keyboard`.
- **Version stamping** (`rules.command_version_stamping`, amended): a batch carries the lowest protocol
  version that defines every event in it: 1.1 for today's events, **1.4** if it contains `pointer_move_to`,
  `pointer_wheel` or `f11`. **The relay refuses such a batch before forwarding it to an agent that did not
  announce 1.4**, with `error PROTOCOL_INCOMPATIBLE detail{peer: "agent", required: "1.4"}` on the
  controller socket, mirroring today's `supports_input` check (`router.py::route_input_batch:395`
  **[repo]**), because the shipped 1.2 agent cannot give that answer itself: the 1.2 shared library runs
  `schemas.validate_input_batch_payload(payload)` **before** `protocol_compatible(...)` /
  `_require_since(...)` (`commands.py::verify_and_parse_input_batch:361-382` **[repo]**), so an unknown
  event fails the closed `input_event` oneOf first and raises `MALFORMED_MESSAGE` (`schemas.py:51`), which
  `agent.py::_on_input_batch` sends back as `error{MALFORMED_MESSAGE}` (`agent.py:521-531`) **[repo]**, and
  no 1.4 contract can change code that is already installed. The relay therefore keeps each agent socket's
  announced versions (`AgentConn.protocol_versions` from `hello`, with `supports_screen` derived from it)
  and compares the verified batch's stamp against them. In the 1.4 libraries the compatibility and `since`
  checks move **ahead** of schema validation (Python `verify_and_parse_input_batch`; the TS library has no
  input-batch verifier **[repo]**, its command verifier gets the same ordering), so a future MINOR really
  does yield `PROTOCOL_INCOMPATIBLE` from older peers; `PROTOCOL.md` §20 records that 1.2 agents answer
  `MALFORMED_MESSAGE` and that the relay shields them. `INPUT_BATCH_PROTOCOL_VERSION` becomes a function in
  Python and TS; `input_event_capabilities` and the relay's coverage check learn the new events. The §18.7
  matrix gains "1.4 phone, 1.2 agent, batch with `pointer_move_to` → relay `PROTOCOL_INCOMPATIBLE`, the
  agent never sees it", tested in `tests/test_e2e_versions.py` with `tests/older_agent.py` **[repo]**.

### 8.2 Agent mapping

- `InputAdapter.move_to(surface_index, gen, x, y)` in `platform/protocol.py` and
  `platform/windows/input.py`: resolve the generation (the last 4 are kept) to the monitor rectangle in
  **physical** pixels, then to virtual-desktop coordinates; call `SetCursorPos` **inside
  `per_monitor_dpi()`** (`window_control.py:459` **[repo]**: the agent is DPI-unaware and capture works in
  physical pixels) with `px = mon.left + round(x · (mon.width − 1) / 65535)`, same for `y`; verify with
  `GetCursorPos`. `SetCursorPos` gives exact pixels with no pointer acceleration. Clicks then use the
  existing `pointer_button` path; UIPI and elevated windows reuse `INPUT_RESTRICTED` / `APP_ELEVATED`.
  `FakeInput` records the mapped pixels. **Not yet verified** on mixed-DPI, rotated or HDR monitors.
- `pointer_wheel` → `SendInput` `MOUSEEVENTF_WHEEL`/`HWHEEL` with the raw delta.
- Typing after a tap keeps T17's foreground re-capture "at the phone's last click" **[repo]**.
- `docs/INPUT_CONTROL.md` line 151 ("the phone never maps its own dimensions onto PC pixels") is amended
  for Direct mode: the phone maps onto a normalised surface; the PC maps onto pixels.

### 8.3 Phone gestures (two pure modules, unit-tested in jsdom like `gestures.ts`)

`src/lib/screenMap.ts`: content scale `k = min(boxW/vw, boxH/vh)`, letterbox offsets `offX = (boxW −
vw·k)/2` (same for Y); zoom `s ∈ [1, 4]` and pan `(tx, ty)` as a CSS transform with `transform-origin: 0
0`; inverse `px = (clientX − left − tx)/s`, `u = (px − offX)/(vw·k)`; taps in the black bars and in the
PC's `reserved` rectangles are **dropped**, never clamped; output integers 0–65535 plus the `gen` the phone
**saw**; `vw`/`vh` from `videoWidth/Height`, and the `<video>` `resize` event marks the geometry stale until
the matching `geometry` message arrives (taps blocked ≤ 300 ms).

`src/lib/directGestures.ts` (`DirectGestureMachine`, keeping "a second finger never clicks"):

| Gesture | Result |
| --- | --- |
| Tap | `[pointer_move_to, pointer_button click]`, sent immediately with a local ripple. A second tap within 500 ms and 12 CSS px reuses the first position so Windows forms the double-click itself; no tap delay. |
| Two-finger tap | Right click at the midpoint (the existing touchpad rule). |
| Press and hold 350 ms | **Precision loupe**: a 2.5× magnified circle above the finger with a crosshair; slide to aim; lift to left-click at the crosshair. |
| Two fingers moving | Spread ratio changes > 10 % before the midpoint moves 8 px → **pinch**: local zoom, nothing sent. Otherwise **scroll**: `[pointer_move_to(midpoint), pointer_wheel(dx, dy)]` at 1/120-notch resolution, using the existing direction preference. |
| One-finger drag | Pans when zoomed; nothing at 1×. |
| Drags (select, move windows) | **Touchpad mode** toggle swaps in the existing relative `GestureMachine` over the same video (Phase 1); direct-mode long-press drag is Phase 2. |

Overlay: reuse the device-proven `TouchpadSurface.tsx` pattern **[repo]** (`touch-action: none`, non-passive
`touchmove` `preventDefault`, pointer capture, cancel on visibility or orientation change) above `<video
playsinline muted>` with `pointer-events: none`, no `controls`, `-webkit-touch-callout: none`. The signed
input pipeline (`lib/input.ts`) is reused unchanged except for the new events and "last position wins"
coalescing for `pointer_move_to` (`coalesceMotion` keeps summing only relative moves **[repo]**).

Honest precision limits: a 1920-pixel desktop shown about 850 pt wide needs the loupe, zoom or Touchpad
mode; zoomed text is blurry until the Phase 3 region crop; a dialog that appears between the frame shown
and the click (100–300 ms) takes the click, as it would for a person at the desk.

### 8.4 Sharp zoom (Phase 3, behind device data)

When the zoom is ≥ 2× and still for 250 ms the phone sends `roi{gen_seen, rect}` on the data channel; the
worker re-crops at native resolution (`ddagrab` `offset`/`video_size` or a `scale_d3d11` crop), keeps the
encoded size, forces an IDR and announces `geometry{gen+1, …}`; the phone switches its mapping when the
`<video>` `resize` event shows the new frame. Because taps carry the `gen` the phone saw, a mis-attributed
frame cannot misclick by more than the overlap of two generations.

## 9. Mini view and the TV-style remote

**In-app mini view (Phase 2).** The same `<video>` and its `RTCPeerConnection` move through a React portal
into an `AppShell`-level draggable corner tile driven by a small store, so the picture is never re-attached
or re-decoded while the owner uses Remote, Type, Apps or the assistant. Heartbeats continue (the page is
visible) and minutes count; a line under it says "Free minutes count while the mini view is open". Tapping
it returns to full size; × stops. The input session still stops when the Screen page is left, as today.

**System picture-in-picture: not in Phases 1–2.** It is broken in home-screen apps (WebKit 303885, open),
live-stream PiP on iPhone is unreliable (WebKit 262479 vs 268732), automatic PiP is not offered for inline
web video, and iOS 26 opens every home-screen site as an app [research]. Phase 3 experiment: a PiP button
only in a Safari tab, only when `document.pictureInPictureEnabled` is true and the page is not standalone
(`display-mode: standalone` / `navigator.standalone`), only after a device test passes, and with
`getStats()` receiver reports as the liveness signal because page JavaScript is throttled in PiP. The
visibility handler must then not send `bye` while `document.pictureInPictureElement` is set. UI copy never
names a browser or phone maker: "Picture-in-picture over other apps works when DoMe is open in your
browser, not from the home-screen icon."

**Website consequence (founder decision, §22):** "a floating mini view while you use another app" is not
deliverable in the home-screen app. Proposed: "a floating mini view while you use the rest of DoMe, and
picture-in-picture over other apps where your phone allows it (in the browser)". `plans.ts`, `faq.tsx` and
the film chip change together with their tests (DECISIONS 63).

**TV-style remote (Phase 2).** A separate page that works **without** Screen view and is not metered;
opening it from Screen view ends the stream (no capture runs while the owner watches the PC's own screen or
TV). D-pad and OK → `key` arrows and `enter`; Back → `escape`; Play/Pause → `space` or `media.set_paused`
when a media session exists; Next/Previous → `media.*`; Volume and Mute → `windows.set_volume` /
`set_muted`; Full screen → `app.set_fullscreen` for approved apps, else `key: f11`; "Picture on" → a new
Screen view session. Needs `keyboard` (with T17's target-change check), `media`, `volume`, `apps`. No new
frames.

## 10. Free minutes: 10 per 5-hour window

| Layer | Role |
| --- | --- |
| **cloud-api ledger** (authority) | A rolling 5-hour window per **account**, starting at first use (the semantics #51 specifies for AI; `HANDOFF.md` #51 **[repo]**); Free allows 600 s per window; PostgreSQL, so it survives deploys. |
| **Relay** | Refuses `screen_ice_request` and `screen.session_start` when nothing is left (`result{origin: relay, failed, SCREEN_ALLOWANCE_EXHAUSTED, detail{resets_at}}`, so the PC never sees the command). Reserves 60 s chunks; **settles on its own clock** (wall seconds it measured between forwarding the start and the end), never on numbers the PC reports. |
| **Agent** | Enforces tickets: no capture without a valid `granted_until`; ends on `final`; reports `live_seconds` and pauses, which the relay uses only as a bounded discount and for dashboards. |
| **TURN** | Phone credentials carry TTL = remaining allowance + 60 s (Free) or 14400 s (unlimited); bound to one controller and revoked when no session starts, when the start is refused and at the end of the session (§13). |
| **Phone** | Display only: "6 min left · resets 6:12 pm" (counted locally from the `screen_ice_servers` allowance). It cannot influence anything; the PC's `ended{allowance_exhausted}` is authoritative. |

Ledger mechanics, copying the conditional-`UPDATE` reserve/settle/sweep pattern of `assistant/metering.py`
**[repo]**: Alembic `0004_usage_windows` adds `usage_windows(account_id, meter, window_start, window_end,
used_seconds, reserved_seconds, PRIMARY KEY(account_id, meter))` (`meter = 'screen_view'`; generic so #51
adds `ai_requests`) and `screen_reservations(id, account_id, pc_id, controller_id, command_id,
screen_session_id null, forwarded_at, started_at null, reserved_until, state)`. **Reserve** `min(60, limit −
used − reserved)` at start and at each `screen_usage` renewal (resetting the window when `window_end ≤
now`; nothing left → no ticket, `final: true`). **Settle on the relay's own clock, never on the agent's
figures.** The charge for a session is the wall time the relay measured from the moment it forwarded
`screen.session_start` (`forwarded_at`; or from the first `started` frame it saw, whichever the operator
configures, default `forwarded_at`) to the `ended` frame or, if none arrives, to the end of the last ticket;
rounded up to 10 s and capped at the total reserved. `live_seconds` from the agent **never reduces** that
charge. `connection_failed` settles to 0 only when the relay saw **no** `started` frame **and** at most
`screen_connect_timeout_seconds` (20 s) elapsed since `forwarded_at`, and at most **3** such zero-charged
attempts are accepted per window per account (the fourth is charged its measured seconds); otherwise it is
charged like any other end. Agent-reported **pauses** (`paused`/`resumed` frames for the secure desktop)
are honoured only as a bounded discount: at most `screen_secure_desktop_pause_max_seconds` (120 s) per
session and only for pauses the relay itself saw as a `paused`→`resumed` pair on its clock; everything else
counts as minutes, which the Free copy states ("minutes may count while the PC shows a permission prompt").
The unused part of the last chunk is refunded **once per session**, never more than the relay-measured
unused remainder, and only for sessions the relay remembers (after a relay restart no refund is given; the
user loses at most 59 s, documented). A **sweeper** charges an unreported reservation in full (at most 60 s
over-charge when an agent vanishes). Setup time (1–4 s [estimate]) counts. Why: a settlement based on
`live_seconds` would let a patched agent stream 59 s, report `ended{connection_failed, live_seconds: 0}`,
get the chunk refunded and repeat, keeping the window at zero while the relay kept minting TURN credentials
for a legitimately relayed path (≈ 0.5 GB/h against the free tier, then $0.05/GB). Rate limits:
`screen_ice_request` 6/min per controller and 60/h per account; `screen.session_start` 6/min per controller.
The integration test "ledger: 10 concurrent sessions never exceed 600 s" includes a **lying agent** that
reports 0 seconds and `connection_failed` on every end and still exhausts its window.

Tamper resistance: a **modified phone** gains nothing (the gate is at the relay and the PC). A **modified
agent** on the customer's own unsigned PC could ignore tickets on a **direct** path; that costs DoMe $0 and
is a revenue risk limited to people who patch a Python app (and a signed binary once T12's updater exists),
recorded as a business risk. A server-signed JWS ticket would not stop it and adds no protection over the
TLS-authenticated relay socket, which the agent already trusts for plan state (`rules.plan_state`); tickets
are therefore plain `granted_until`. **Relayed** use is bounded server-side without trusting the agent: the
ledger is charged on the relay's clock, every TURN credential is bound to a controller and revoked when its
session does not start or ends, relayed bytes are reconciled from Cloudflare's own figures, and per-account
daily and monthly caps stop minting (§13). Many Free accounts each need their own linked PC
(`max_enabled_pcs: 1` **[repo]**).

Before and after #51: everyone is Free today, **the founder included**; no production override (staging may
set `DOME_SCREEN_FREE_SECONDS_OVERRIDE`, refused when `DOME_ENV=production`). When #51 lands, `plans.json`
gains `screen_view_seconds_per_window` (free 600; pro and pro_plus `null` = unlimited) and
`screen_view_window_hours: 5`; `plans.py::catalog()` maps them explicitly **[repo]**; Pro sessions receive
`unlimited: true` tickets still in 60 s chunks (same dead-man latency), and nothing changes on the agent or
phone. If #51 lands first, Phase 1 uses its window ledger instead of adding the table.

## 11. Contract changes: protocol 1.4 (additive MINOR)

Done the way 1.1 and 1.2 were (`shared/protocol/README.md`, `PROTOCOL.md` §17–§18 **[repo]**).

- **`version.json`:** `protocol_version: "1.4"`. New limits: `screen_sdp_max_bytes: 12288`,
  `screen_start_command_lifetime_seconds: 60`, `screen_answer_lifetime_seconds: 60`,
  `screen_heartbeat_seconds: 2`, `screen_lease_seconds: 8`, `screen_connect_timeout_seconds: 20`,
  `screen_allowance_chunk_seconds: 60`, `screen_allowance_renew_before_seconds: 20`,
  `screen_session_max_seconds: 14400`, `screen_consent_timeout_seconds: 30`,
  `screen_pc_stop_cooldown_default_seconds: 600`, `screen_secure_desktop_pause_max_seconds: 120`,
  `screen_dc_message_max_bytes: 1024`, `screen_geometry_generations_kept: 4`, `input_wheel_max: 1200`.
  New rules: `screen_sessions` (one per PC; capture only after DTLS; lease; tickets; end order; the relay
  socket's loss does not end a session; lock ends, secure desktop pauses; 4 h maximum),
  `screen_signaling` (offer inside the signed command; PC-signed answer in a frame, never in a result;
  vanilla ICE; strict SDP check; no renegotiation), `pc_key_pinning`, `screen_metering`,
  `screen_content` (nothing recorded; no AI; no SDP, address or credential in logs, journals, results,
  diagnostics or storage), `local_permission_ui`. Amended: `grant_update` (screen and screen_audio, never
  implicit), `ai_eligibility`, `command_version_stamping` (§8.1), `pairing_secret` (the PC key MAC),
  `input_sessions` (`pointer_move_to` only with a live screen session of the same controller).
- **`actions.json`** (`registry_version` 1.4): capabilities `screen`, `screen_audio`;
  `screen.session_start` (`since: "1.4"`, params `{offer_sdp: string, maxLength 12288, pattern
  ^[\x20-\x7E\r\n]*$; surface: "primary" | 0–7, default "primary"; audio: boolean, default true; takeover:
  boolean, default false}`, result `screen_session_result`, capability `screen`, risk `moderate`,
  confirmation `none`, `timeout_ms` 45000, availability `windows`, `session_unlocked`, `ai_eligible:
  false`, `routine_allowed: false`); `screen.session_stop` (`{screen_session_id}`, result
  `screen_session_stop_result`, idempotent).
- **`relay-frames.schema.json`:** the four capability enums; new frames `controller_screen_ice_request`,
  `relay_screen_ice_servers`, `agent_screen_answer` (envelope + `ref_controller_id`),
  `relay_screen_answer`, `agent_screen_session` (events `started|paused|resumed|ended`; reasons `stopped,
  stopped_on_pc, replaced, viewer_gone, takeover, controller_revoked, controller_offline, grant_removed,
  plan_disabled, remote_disabled, session_locked, secure_desktop, allowance_exhausted,
  allowance_unconfirmed, max_duration, capture_failed, connection_failed, agent_restart`; `path`,
  `live_seconds`, `at`) and its relay-to-controller form, `agent_screen_usage`, `relay_screen_allowance`
  (with optional `reason: controller_offline | allowance_exhausted | plan_disabled`); optional `screen` in
  `relay_to_agent_command.relay`; new defs `screen_answer_payload` and `screen_dc_message` (phone → PC:
  `hb`, `keyframe`, `bye`, `audio{on}`; PC → phone: `geometry`, `state`, `allowance`, `restricted`;
  Phase 2/3 add `cursor_shape`, `cursor_pos`, `roi`; no data-channel message can inject input or run
  anything); `input_event` additions `pointer_move_to`, `pointer_wheel`, `key: f11`;
  `pairing_decision.pc_key_mac`; both direction oneOf lists. No `pc_state` change.
- **`results.schema.json`:** `screen_session_result`, `screen_session_stop_result{stopped, live_seconds}`.
  Neither contains SDP, titles or addresses.
- **`rest.schema.json`:** the capability enum; `pairing_status_response.pc_key{public_jwk, mac}`.
- **`errors.json`** (each with a plain `user_message`, no brand names): `SCREEN_SESSION_OWNED`,
  `SCREEN_SESSION_REQUIRED`, `SCREEN_ALLOWANCE_EXHAUSTED` (detail `resets_at`), `SCREEN_OFFER_INVALID`,
  `SCREEN_CAPTURE_UNAVAILABLE` (detail `busy|unsupported_display|driver|remote_session`),
  `SCREEN_ENCODER_UNAVAILABLE`, `SCREEN_CONNECTION_FAILED`, `SCREEN_CONSENT_DECLINED`,
  `SCREEN_CONSENT_TIMEOUT`, `SCREEN_GEOMETRY_CHANGED`, `SCREEN_VIEW_DISABLED` (operator kill switch),
  `INPUT_PAUSED_ON_PC`, `INPUT_TARGET_RESERVED`; phone-side only: `SCREEN_PC_UNVERIFIED`,
  `SCREEN_ANSWER_INVALID`.
- **`plans.json`:** `screen_view_seconds_per_window` (free 600, pro null), `screen_view_window_hours: 5`,
  `screen_ice_rate_limit`. Read by cloud-api only; no entitlement-claim change.
- **Shared libraries:** Python `dome_protocol`: `build/verify_screen_answer_payload` (PC-signed),
  `pc_key_mac`, `screen_dc_message` parsing, the stamping function, `input_event_capabilities`, an SDP
  pattern helper, and the **check order change** in `verify_and_parse_input_batch` and the command verifier
  (protocol compatibility and `since` before schema validation, §8.1); TS `commands.ts` / `versions.ts`
  mirrors (same order in the command verifier); `pnpm gen:types`; signed fixtures regenerated
  with `make_fixtures.py` / `make-fixtures.ts` (a PC-signed answer vector and a `pc_key_mac` vector);
  `strict-json-cases.json` gains SDP cases (bare `\n`, NUL, non-ASCII, over-length).
- **Peers:** agent `SUPPORTED_PROTOCOL_VERSIONS` adds `"1.4"` (`__init__.py:12` **[repo]**); relay
  `supports_screen = protocol_compatible("1.4", …)` next to `supports_input` (`controller_ws.py:99`,
  `agent_ws.py:83` **[repo]**); the PWA announces 1.4 and adds the new defs to the `RELAY_FRAME_DEFS`
  allow-list in `scripts/gen-validators.ts` (and mirrors any new library API in the no-eval facade, caught
  by `protocol-parity.test.ts`); the browser extension only regenerates validators.
- **Deploy order:** cloud (relay and PWA) first, then the agent (`agent_ws._versions_ok` refuses an agent
  newer than the relay **[repo]**). A 1.4 phone against a 1.2 PC gets `PROTOCOL_INCOMPATIBLE` **from the
  relay**, for `screen.*` commands and for input batches stamped 1.4 alike (§8.1): "Update DoMe on your PC
  to use Screen view." `PROTOCOL.md` §20 says why the relay, not the 1.2 agent, gives that answer for
  batches.
- The §6 proposals in `HANDOFF.md` (Ed25519 algorithm name, `controller_id` in `pairing_request`, proof of
  possession at link start) may ride with this MINOR, since pinning makes the PC key matter; they are not
  required by it.

## 12. cloud-api changes

| Area | Change |
| --- | --- |
| `relay/router.py` | `route_screen_ice_request` (§4.2 checks, rate limits, per-account TURN caps, mint bound to the controller with revocation of its previous credential, `screen_ice_servers`); a pre-forward hook for `screen.session_start`: `supports_screen` on the agent socket (else `PROTOCOL_INCOMPATIBLE`), reserve the first chunk (else the relay `failed` result), attach `relay.screen`, remember `pending_screen_starts[command_id] = (controller_id, connection_id, forwarded_at)` with a 60 s TTL for answer routing and the relay-clock charge; `route_input_batch` gains a **stamp gate**: a batch whose `protocol_version` is not compatible with the target agent's announced versions is refused with `PROTOCOL_INCOMPATIBLE detail{peer: "agent", required}` before forwarding, next to the existing `supports_input` check (§8.1). |
| `relay/agent_ws.py` | `screen_answer` (schema, size, ownership, strip `ref_controller_id`, deliver to the issuing socket, else the controller's other 1.4 sockets); `screen_session` (track the owner in `AgentConn.screen_sessions` as `input_sessions` does **[repo: `manager.py:122`]**, settle on the relay clock and refund once on `ended`, write security events, forward to the owner and broadcast to 1.4 subscribers); `screen_usage` (reserve the next chunk, answer `screen_allowance`; `final, reason: controller_offline` when the owner has had no bound socket for 60 s or its sockets were closed by `revoked`, logout or session revocation, §7); a per-socket budget of 2 `screen_usage` per minute per session (`KNOWN_ISSUES.md` #1 has no general agent frame budget **[repo]**); `_on_agent_error`'s prefix filter extended from `INPUT_` to `INPUT_` **and** `SCREEN_` so `SCREEN_SESSION_REQUIRED` / `SCREEN_GEOMETRY_CHANGED` reach the issuing controller (§8.1). |
| `relay/manager.py` | `AgentConn.protocol_versions` (from `hello`), `supports_screen`, `screen_sessions`; `ControllerConn.supports_screen`; `controller_last_bound_at` for the 60 s owner-presence rule; on a controller socket close by `revoked`, logout or session revocation, an immediate `screen_allowance{final, controller_offline}` to the PC of any session that controller owns, plus revocation of its TURN credential. |
| `routes/pairing.py` | Store `pc_key_mac`; return `pc_key` once approved; never before; approved rows stay readable for at least 5 minutes after approval (tested invariant, §4.1). |
| `routes/account.py`, `routes/controllers.py` | Logout, session revocation and controller deletion call the same hook: end owned screen sessions (`controller_offline` / `controller_revoked`) and revoke the controller's outstanding TURN credential. |
| `screen/metering.py` + `db/alembic/versions/0004_usage_windows.py` | §10 ledger: reserve, renew, settle on the relay clock (`forwarded_at`, `started_at`), bounded pause discount, zero-charge attempts counter, refund-once, sweeper. |
| `screen/turn.py` | Provider interface with `cloudflare`, `none` (STUN only) and `fake` implementations; httpx with a 3 s timeout; `POST https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers` (Bearer token, `{"ttl": N, "customIdentifier": account_id}`) [research]; `POST .../credentials/{username}/revoke` [research, **verify**] on every path in §13 (previous credential at mint, no start within 60 s, start refused, `ended`, controller revocation, grant narrowing, logout, plan change); `turn_credentials(controller_id PK, username, account_id, minted_at, expires_at, screen_session_id null, state)` so revocation needs no agent cooperation; never logs the response; the hourly **usage reconciliation** job (GraphQL Analytics API, `callsTurnUsageAdaptiveGroups`, `egressBytes` grouped by `customIdentifier` and `username` [research, **verify**]) feeding the per-account daily cap and the monthly 700/900 GB guard (§13); circuit breaker → STUN only. |
| `settings.py`, `.env.example` | `DOME_SCREEN_VIEW_ENABLED` (kill switch; default off in production until the launch gate), `DOME_TURN_PROVIDER` (`cloudflare|none`), `DOME_TURN_KEY_ID`, `DOME_TURN_API_TOKEN` (`SecretStr`), `DOME_TURN_ANALYTICS_TOKEN` (`SecretStr`, read-only analytics scope, for reconciliation), `DOME_SCREEN_TURN_SOFT_CAP_GB` (700), `DOME_SCREEN_TURN_HARD_CAP_GB` (900), `DOME_SCREEN_TURN_FREE_DAILY_GB` (2), `DOME_SCREEN_TURN_MINTS_PER_ACCOUNT_MONTH` (600), `DOME_SCREEN_TURN_TTL_UNLIMITED_SECONDS` (14400), `DOME_SCREEN_STUN_URLS` (default `stun:stun.cloudflare.com:3478`), `DOME_SCREEN_FREE_SECONDS_OVERRIDE` (refused in production). |
| `logging.py` | Redaction keys (§4.4); one structured line per session end with only `path`, a duration bucket and the end reason. |
| Security events | `screen_session_started`, `screen_session_ended` (reason and relay-measured seconds only), `screen_allowance_exhausted`, `screen_consent_declined{reason}` (reported by the agent, §6.3), `turn_credential_revoked{reason}`, `turn_cap_reached{cap: daily|monthly_mints|soft|hard}`, `grant_updated` with the new capabilities; visible at `GET /v1/account/security-events` **[repo]**. |
| `/healthz` | `turn: ok | unconfigured | error`, `turn_month_gb` (reconciled and provisional) and `turn_reconciled_at`, no secret in the output. |
| Deploy | `.github/workflows/deploy.yml` and `deploy/launch/dome_deploy.py` copy the two TURN secrets from the GitHub Environment into Fly like the other secrets; `docs/LAUNCH_SETUP.md` gains the step. |
| CSP | Expected unchanged (`connect-src 'self'`): `srcObject` is not a URL load and WebRTC is not governed by `connect-src` in current engines [research]. **To verify on WebKit**; `security/headers.py` changes only if the device test fails. |

No new process, machine or UDP service on Fly. Signaling is a few KB per session.

## 13. TURN credential minting and the spending guard

- Provider: Cloudflare Realtime TURN, $0.05/GB billed only on Cloudflare-to-client data, **1,000 GB a
  month free** shared with their SFU, STUN free [research: [TURN FAQ](https://developers.cloudflare.com/realtime/turn/faq/),
  [pricing](https://developers.cloudflare.com/realtime/sfu/pricing); vendor page via search excerpt, **verify**,
  including whether a payment method is required and the maximum TTL]. Minted by cloud-api for the **phone
  only**, inside `screen_ice_request`, only for a bound controller with a live `screen` grant.
- TTL: Free `remaining_seconds + 60`, capped at 3600; unlimited plans 14400 (the session maximum). Whether
  an expired credential ends a running allocation, and when, is **not yet verified**; TURN re-authenticates
  on allocation refresh (≈ 10 min) and permission refresh (5 min) [research], which bounds it.
- **Every credential is tied to one controller, and revoked without the agent's help.** The relay keeps
  `turn_credentials(controller_id, username, account_id, minted_at, expires_at, screen_session_id, state)`:
  at most one outstanding `username` per controller, and minting a new one first revokes the previous
  (`POST .../credentials/{username}/revoke` [research, **verify**]). The relay revokes when no
  `screen.session_start` from that controller arrives within `screen_start_command_lifetime_seconds`
  (60 s); on any start refusal (`SCREEN_CONSENT_DECLINED`, `SCREEN_CONSENT_TIMEOUT`, `SCREEN_OFFER_INVALID`,
  `SCREEN_ALLOWANCE_EXHAUSTED`, `PROTOCOL_INCOMPATIBLE`, any relay-side refusal); on `ended`; when the
  session is ended by the sweeper; on controller revocation, grant narrowing that removes `screen`, logout
  or account-session revocation of the controller, and plan change. Each revocation writes the security
  event `turn_credential_revoked{reason}`. A credential minted without a session therefore lives at most
  60 s, not its TTL.
- The PC receives `stun_urls` only (`stun.cloudflare.com:3478`, free, no key). With no key configured
  (development, staging without a key) the phone also gets STUN only and `turn: unavailable`; same-Wi-Fi
  and simple NATs still connect.
- **Graduated guard, fed by Cloudflare's figures, never by the agent.** `relayed_bytes` in `screen_usage`
  is informational only (a non-DoMe TURN client or a patched agent reports nothing). The relay passes
  `customIdentifier = account_id` when it mints and runs an **hourly reconciliation** against the GraphQL
  Analytics API (`callsTurnUsageAdaptiveGroups`, `egressBytes` grouped by `customIdentifier` and `username`
  [research, **verify** field names and freshness on the vendor page]), writing per-account daily and
  per-month totals. Until the hour's figures arrive the guard uses a **provisional upper bound computed
  server-side**: every ticketed second of every session is counted as relayed at the relayed cap (0.5 GB/h),
  plus the full TTL at cap for any credential with no session; the thresholds below use the larger of the
  provisional and the reconciled figure. Past 700 GB in the month, relayed Free sessions get "Data saver"
  (0.5 Mbps) through `relay.screen.relayed_max_kbps`; past 900 GB, Free sessions get STUN only for the rest
  of the month and the phone says so (§16). Pro sessions are unaffected (none exist yet). The founder's kill
  switch `DOME_SCREEN_VIEW_ENABLED=false` refuses every start with `SCREEN_VIEW_DISABLED`; `DOME_TURN_PROVIDER=none`
  keeps Screen view working where a direct path exists.
- **Per-account caps** (both refuse `screen_ice_request` with `turn: disabled` and revoke the account's
  outstanding credentials; `turn_cap_reached` security event): a **daily relayed-GB cap on Free**,
  `DOME_SCREEN_TURN_FREE_DAILY_GB` = 2 [estimate: ≈ 5 × the 0.4 GB a Free account can legitimately relay in
  a day at 10 min per 5 h], and a **monthly mint cap per account**, `DOME_SCREEN_TURN_MINTS_PER_ACCOUNT_MONTH`
  = 600 [estimate: ≈ 4 × the ≈ 150 legitimate Free starts a month, with room for retries]. The caps are
  checked against the provisional bound as well, so they bite within the hour even before reconciliation.
- Abuse, stated honestly: a signed-in user could reuse the phone credential as a generic UDP relay until it
  is revoked or expires; TTL limits time, not bytes. Without the controls above, re-minting every 10 minutes
  would have given a perpetual relay at any bandwidth, invisible to an agent-fed guard, and one account at
  100 Mbps would have spent the whole month's hosting budget in about a day past the free tier. What now
  bounds it: minting only for a bound controller with a live `screen` grant; one outstanding credential per
  controller; revocation within 60 s when no session follows and at every end; the daily GB and monthly
  mint caps; the Cloudflare-fed monthly guard with the provisional bound; the rate limits. Worst case per
  Free account per day is therefore ≈ 2 GB ≈ $0.10 past the free tier. The founder's monthly dashboard check
  (§20) is a backstop, not the control. Finer limits (relayed-hours per account) are Phase 3.
- Privacy: Cloudflare sees the PC's and the phone's addresses, timing and byte counts of relayed sessions
  (and STUN requests for every session), never plaintext. It goes on the privacy page as a sub-processor.

## 14. Cost

Inputs: Fly egress $0.02/GB (`COST_MODEL.md` §2/§2A **[repo]**); Cloudflare TURN as above [research,
verify]; production ≈ $45/month and staging ≈ $4 of the ≈ $50 budget, so headroom is ≈ $1–5 (§2A **[repo]**).

Data per streamed hour (video cap + 64 kbps Opus + ≈ 5 % overhead [estimate]): direct at cap 1.5 Mbps →
≈ 1.64 Mbps → **0.74 GB/h**; relayed at cap 1.0 Mbps → **0.50 GB/h**; Data saver 0.5 Mbps → ≈ 0.27 GB/h; a
still desktop (VFR) ≈ 0.1–0.3 GB/h [estimate]; video playing on the PC runs at the cap.

| Cost per streamed hour | DoMe pays |
| --- | --- |
| Direct (P2P) media | **$0** |
| Relayed, inside the free 1,000 GB | **$0** |
| Relayed, beyond the free tier | ≤ **$0.025/h** (0.50 GB × $0.05); ≤ $0.014/h in Data saver |
| Signaling on Fly (≈ 15 KB per start + usage frames) | ≈ $0.000001/h |
| **Input batches during Screen view** (COST_MODEL §7A: 0.75 KB/s idle keepalive … 13 KB/s typical active → 2.7–48 MB/h) | **$0.00005–0.001/h** |
| Database | 1 row per account; one UPDATE per minute per live Free session |

Monthly (pessimistic on purpose: 30 % of MAU use Screen view; Base 4 h/user/month, High 10 h; **40 % of
hours relayed**, because Safari hides raw LAN addresses and the PC has no TURN; Free allows at most ≈ 24 h
per month = 10 min × 4.8 windows/day × 30) [estimate]:

| MAU | Scenario | Streamed h | Relayed h | TURN GB | TURN $ | Fly $ (signaling + input) | **Added total** |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 100 | Base | 120 | 48 | 24 | $0 | ≤ $0.12 | **≈ $0** |
| 100 | High | 300 | 120 | 60 | $0 | ≤ $0.30 | **≈ $0** |
| 1,000 | Base | 1,200 | 480 | 240 | $0 | ≤ $1.20 | **≈ $1** |
| 1,000 | High | 3,000 | 1,200 | 600 | $0 | ≤ $3 | **≈ $3** (input egress is the whole remaining headroom) |
| 1,000 | Stress: 60 % adopt, every user at the Free maximum, 40 % relayed | 14,400 | 5,760 | 2,880 (1,440 at Data saver) | $94 without the guard; **$0 with it** (Free sessions go STUN-only past 900 GB; ≈ 2/3 of relayed hours refused late in the month) | ≤ $14 | **≈ $14** — beyond the budget: at this scale paid plans must exist and the caps can rise |

Reading: the free tier covers ≈ 2,000 relayed hours, or ≈ 5,000 streamed hours a month at 40 % relayed; the
hosting budget stays ≈ $49 at 100 and 1,000 MAU in Base and High. Abuse is bounded independently of these
assumptions: with the §13 controls a Free account can relay at most ≈ 2 GB a day (≈ $0.10 past the free
tier), and the monthly guard stops Free relaying altogether at 900 GB, so no single account or small group
can spend the budget. If mDNS direct paths work (§3) the
relayed share falls and the Cloudflare figures with it; the input-egress line does not depend on the path.
For comparison, video through the Fly relay would cost ≈ $0.015/h at the cap: ≈ $18/month at 1,000 MAU Base
and ≈ $45 at High, **plus** a larger CPU machine; coturn on Fly ≈ $6/month fixed plus $0.02/GB and a
dedicated IPv4. Both break the budget (ADR-0002).

Non-hosting costs this feature makes urgent (HANDOFF item 4 **[repo]**): Authenticode signing (Azure
Trusted Signing ≈ $9.99/month, `COST_MODEL.md` §2 **[repo]**), because an unsigned app that captures the
screen and injects input is very likely to be flagged by SmartScreen and antivirus [research]; a one-off
counsel review of H.264 through OS encoders and of the LGPL obligations (cost unknown).

## 15. pc-agent modules

| Module | Contents |
| --- | --- |
| `dome_agent/screen/session.py` | `ScreenSessionManager`, `EndReason`, lease, tickets, pause, takeover, cooldown, ask mode, end order, recovery (§7). |
| `dome_agent/screen/supervisor.py` | Spawns `DoMe.exe screen-worker` only after `indicator.py` confirmed a bar on every monitor (§7), the Job object, the pipe protocol, liveness ticks (3 s agent → worker; the worker exits after 10 s without one), the worker's stderr read through the redacting log pipeline (§4.4), `TerminateProcess`. |
| `dome_agent/screen/sdp_check.py` | The strict offer check (§4.2 step 5). |
| `dome_agent/screen/signing.py` | `screen_answer_payload` built and signed with `identity.py`'s key. |
| `dome_agent/screen/indicator.py` | Bars on every monitor (`WDA_EXCLUDEFROMCAPTURE`, `WS_EX_NOACTIVATE`), the hotkey, reserved rectangles for §6.4. |
| `dome_agent/screen/consent.py` | Grant window and consent window through `AgentUI` (new `show_screen_grant`, `show_screen_consent`); the permission-UI-open flag the input dispatcher reads. |
| `dome_agent/screen/history.py` | The last 20 sessions, metadata only, in SQLite. |
| `dome_agent/screen/worker/{__main__,main,rtc,capture_dda,capture_wgc,encode,audio_loopback,fake}.py` | aiortc send-only peer, pre-encoded track, PLI → IDR through the `_send_keyframe` hook (§5.3), SPS/PPS prepended to every IDR (§5.3), 1 fps floor by re-encoding the last raw frame (§5.2), path detection, bitrate steps, data-channel vocabulary, aiortc/aioice loggers pinned to WARNING (§4.4), exit on missing liveness ticks (§7), s16 conversion for audio (§5.4), `--self-test` (encoder list, LGPL string, exactly one decoder, a decoded frame from a `lavfi` `testsrc` graph, the PLI hook present), `--list-encoders`. |
| `dome_agent/actions/screen.py` | `screen.session_start` / `screen.session_stop` handlers; strips everything but ids and geometry from results. |
| `dome_agent/platform/protocol.py`, `platform/windows/input.py`, `platform/windows/screen.py` | `InputAdapter.move_to`, `wheel`; `ScreenCaptureAdapter`, `AudioCaptureAdapter`; monitor enumeration in physical pixels. |
| `dome_agent/testing/fake_platform.py` | `FakeScreen` (synthetic NAL packets or VP8 frames, settable geometry, capture loss, lock), `FakeAudio`, `FakeInput.move_to`, `FakeScreenWorker`. |
| `dome_agent/pairing.py`, `agent.py`, `cli.py`, `tray.py`, `ui.py`, `logsetup.py`, `crashguard.py`, `diagnostics.py` | `LOCAL_ONLY_CAPABILITIES`, `SCREEN_SCOPE_EXPLANATION`, `pc_key_mac`, grant plumbing, `grant --screen` opens the window, tray badge and menu, `StatusView.screen_session`, redaction keys plus the IPv4/IPv6(:port) masking in `redact_text_line`, `redact_value` and `crashguard.redact` (§4.4), `pin_media_loggers()`, emergency-stop ordering. |
| `packaging/dome-agent.spec`, `packaging/license_gate.py`, `packaging/THIRD_PARTY_NOTICES.txt`, `packaging/installer/DoMe.iss`, `.github/workflows/{ffmpeg-lgpl,windows-installer}.yml` | aiortc (`==1.15.0`, hash-pinned) and deps, the custom `av` wheel in `pyproject.toml` `[tool.uv.sources]` as `av = [{ url = "<release-asset>.whl", marker = "sys_platform == 'win32'" }]` with PyPI as the fallback for other platforms (so `uv sync --locked` keeps working on the `ubuntu-24.04` CI jobs, which share one `uv.lock` with `windows-latest` **[repo: `ci.yml:76-163`, `windows-installer.yml:80`]**; the stock GPL PyAV wheel is then used only in Linux unit tests and never ships, §5.5), `aiortc.contrib` excluded, the LGPL notice and source offer, the gate on `dist/`, `screen-worker --self-test`. |

## 16. mobile-app screens, states and copy

Modules: `lib/screen.ts` (`ScreenSessionClient` with an injected `RTCPeerConnection` factory, as
`RelayClient` takes `socketFactory` **[repo]**: signaling, answer verification, heartbeats, visibility,
stall detection, restart limits), `lib/screenMap.ts`, `lib/directGestures.ts`, `lib/pcKeyPins.ts`
(IndexedDB pins), `lib/input.ts` (new events, 1.4 stamping, last-wins coalescing), `pages/app/ScreenPage.tsx`,
`components/ScreenSurface.tsx`, `components/ScreenLoupe.tsx`, `pages/app/PairPage.tsx` (pinning),
`pages/app/RemotePage.tsx` (the WP-0 card, then the Screen tile), `components/MiniView.tsx` and
`pages/app/TvRemotePage.tsx` (Phase 2), `scripts/gen-validators.ts`, `vite.config.ts` (`orientation:
"any"`, currently `"portrait"` at line 58 **[repo]**; iOS reportedly ignores it, but if honoured it would
forbid landscape [research]). The five-tab structure (DECISIONS 57) is unchanged: Screen opens from the
Remote page and Home.

Exact copy (no brand names; one next action per state; every row has a component test with the injected
factory):

| State | Detection | Phone shows |
| --- | --- | --- |
| Not built yet (WP-0) | `SCREEN_VIEW_STATUS` | Card "Screen view: Coming. See your PC's screen on your phone and tap what you see. In development; not in the app yet." |
| No `screen` grant | grants list / relay refusal | "Allow Screen view on your PC: DoMe tray icon › Paired phones › this phone › Allow Screen view." |
| PC key not pinned | pin store | "Pair this phone with your PC again so it can check it is really your PC. On the PC: DoMe tray › Paired phones › remove this phone, then pair." |
| Pinned key mismatch | answer `kid` | "This PC's identity changed. Pair again." (hard stop) |
| Answer signature invalid | §4.3 checks | "Couldn't confirm this is your PC. Nothing was shown." |
| PC agent older than 1.4 | `PROTOCOL_INCOMPATIBLE` | "Update DoMe on your PC to use Screen view." |
| Remote control off | state | "Remote control is switched off on the PC. Turn it on from the DoMe tray icon." |
| Operator kill switch | `SCREEN_VIEW_DISABLED` | "Screen view is switched off for now. The touchpad and remote still work." |
| Ask mode / cooldown | `consent: pending` | "Waiting for someone at the PC to allow this… (30)" |
| Declined / timed out / backing off | `SCREEN_CONSENT_DECLINED` / `_TIMEOUT` | "Not allowed at the PC." / "No one answered at the PC." / after a refusal: "Not allowed at the PC. Try again in a few minutes." (no automatic retry) |
| Free allowance used | `SCREEN_ALLOWANCE_EXHAUSTED` / `ended{allowance_exhausted}` | "You've used Free Screen view for now. It resets at 4:12 pm. The touchpad and remote still work." (no buy button) |
| Another phone viewing | `SCREEN_SESSION_OWNED` | "Another phone is viewing this PC." [Take over] |
| Connecting | timer | "Connecting to your PC's screen…" then "Trying the relay server…" |
| `play()` rejected (Low Power Mode) | promise rejection | "Tap to show the picture." |
| No path | 20 s with no DTLS | "Couldn't reach your PC's picture from this network. The touchpad and remote still work." [Try again] |
| Relay guard hard cap | `turn: disabled` | "Screen view could only try a direct connection right now. It works on most home networks." |
| Live | first frame | top strip "Encrypted between your PC and this phone" · "About 7 min left" |
| Data saver / relayed | `path`, `relayed_max_kbps` | badge "Via relay server" / "Data saver" |
| Weak connection | step-down | "Weak connection: lowering quality" |
| View-only grant | no `pointer` | "View only. Allow the touchpad on your PC to tap." |
| Tap on an admin window | `INPUT_RESTRICTED` (existing) | "That window runs as administrator. You can see it but not click it from the phone." |
| Permission window open on the PC | `INPUT_PAUSED_ON_PC` | "Waiting for someone at the PC." |
| Frozen or black picture | rVFC silent 2 s / ICE failed | "Picture paused — reconnecting…" then one automatic fresh session (twice at most) |
| PC locked | `session_locked` | "Your PC is locked. Unlock it to start again." |
| UAC / secure desktop | `state{paused}` | "Windows is asking for permission on the PC. Someone at the PC must answer." (clock stopped) |
| Protected content | — | help text and first-use hint: "Some protected videos and windows show as black." |
| Basic picture mode | `encoder: vp8` | Health note "Basic picture mode" (VP8 is also the fallback after H.264 stalled on the PC or never decoded on the phone, so the note names no cause; revised 2026-10-27) |
| Capture unavailable | `SCREEN_CAPTURE_UNAVAILABLE` | "Your PC can't share its screen right now. Another app may be capturing it, or this display isn't supported yet." |
| Worker crash | `capture_failed` | "Your PC's screen sharing stopped unexpectedly." [Start again] |
| Stopped at the PC | `stopped_on_pc` | "Screen view was stopped on the PC. For 10 minutes someone at the PC must allow it again." (no automatic restart; the same copy on any other phone that tries during the cooldown) |
| Takeover | `takeover` | "Another phone took over Screen view." |
| Relay contact lost (PC side) | `allowance_unconfirmed` | "Lost contact with DoMe's service. Screen view ended." |
| This phone signed out or offline for a minute | `controller_offline` (seen on the PC; the phone's socket is already closed) | PC notification "Screen view ended: the phone is no longer signed in or lost contact". The phone, if it still shows the page: "Screen view ended because this phone lost contact with DoMe's service." |
| UAC prompt on the PC (Free) | `state{paused}` | "Windows is asking for permission on the PC. Minutes may still count after two minutes." |
| Relay contact lost (phone side) | socket closed | picture continues; "Clicks paused: reconnecting" |
| Phone backgrounded | `visibilitychange` | on return: "Resume" or one automatic fresh session (§2.2 step 5) |
| Still watching? | 5 idle min (Free) / 3 h 55 min (all) | "Still watching? Screen view will stop in 60 seconds." [Keep watching] |
| Sound (Phase 2) | muted start | "Tap for sound" → mute toggle; "PC sound is off for this phone. Allow it in the DoMe tray." |
| Mini view (Phase 2) | — | "Free minutes count while the mini view is open." |

Product labels: "Screen view (beta)" in the app during Phase 1; the website keeps `SCREEN_VIEW_STATUS =
"Coming"` until the launch gate (§19), then per-sub-feature labels ("PC sound, mini view and TV remote:
Coming") until Phase 2 ships (founder decision, §22). The first-run explainer: "Your PC's picture goes
straight to this phone, encrypted between the two. Nothing is recorded or stored. The AI never sees it.
Uses about 0.7 GB per hour at full quality."

## 17. Security analysis

### 17.1 Threats and mitigations (new `SECURITY.md` rows)

| # | Threat | Control | Planned evidence |
| --- | --- | --- | --- |
| T24 | Relay, TURN or a network attacker reads the screen or sound | DTLS-SRTP; the phone's fingerprint inside the phone-signed offer; the PC's fingerprint inside the PC-signed answer; aiortc's fingerprint check **[inspected]**; capture only after DTLS | integration-tested: `tests/test_e2e_screen.py` tampered fingerprint or swapped answer → no media and a refusal |
| T25 | Relay shows a **fake** screen to steer taps or harvest typing | PC key pinned at pairing with the code-keyed MAC; hard stop on mismatch; no fallback | unit-tested (phone §4.3 refusals; MAC vectors) |
| T26 | Covert viewing (family PC, intimate-partner surveillance, a stolen phone) | PC-only grant window; bars on every monitor plus tray and notifications with no hide option, created and confirmed **before** capture (fail-closed, §7); local history; account security events; **PC-wide** post-Stop cooldown (a second installation of the same phone cannot skip it); consent back-off after Deny/timeout and one consent window per minute per PC (no consent fatigue); "Block this phone for 1 hour" and "Turn off Screen view for this phone" on the consent window; ask mode; 60 s dead-man tickets so revocation reaches a PC the relay cannot; a signed-out or revoked phone's session ends within one ticket (`controller_offline`) | unit-tested (session manager, UI model: second controller during cooldown asked, third refused start within a minute opens no window, fake UI refusing a window starts nothing); integration-tested (logout ends the stream within one ticket); bars **not yet verified on a device** |
| T27 | A phone with input grants itself `screen` | `rules.local_permission_ui`; widening only through the GUI; the honest limit (§6.5) | unit-tested |
| T28 | Tech-support scam coaching the owner | Scam line, 5 s hold, bars, history | unit-tested (UI model) |
| T29 | Runaway or forgotten stream; revocation while the relay is down | 8 s lease, 60 s pull-based tickets, 4 h maximum, lock ends, Job object kill-on-close | unit-tested; e2e; Job object **not yet verified on a device** |
| T30 | Metering fraud and TURN cost abuse | Server ledger charged on the relay's clock (agent figures never reduce the charge; bounded pause discount; at most 3 zero-charged `connection_failed` per window); PC tickets; TURN credentials bound to one controller and revoked when no session starts, on refusal and at every end; Cloudflare-fed usage reconciliation with a server-side provisional bound; per-account daily GB and monthly mint caps; the graduated monthly guard; rate limits; kill switch | integration-tested (ledger concurrency: 10 sessions never exceed 600 s, including a lying agent that reports 0 seconds and still exhausts its window; a credential minted without a start is revoked within 60 s); unit-tested (TURN stub: bind, revoke on every §13 path, caps, reconciliation parsing) |
| T31 | Recording or AI leakage | §17.2 structural guarantees | §17.2 tests |
| T32 | Media parser attack surface on the PC | Send-only worker; incoming media rejected; FFmpeg with no demuxers or protocols and no decoders except `wrapped_avframe` for the local `lavfi` device (it unwraps frames, parses no bitstream); strict SDP check; isolated process | unit-tested (SDP checker, build configure gate asserting exactly one decoder) |
| T33 | Network exposure during sessions | ICE ports only while live; STUN integrity from signed credentials; DTLS only to the signed certificate | e2e; firewall **not yet verified on a device** |
| T34 | Malicious PWA deploy or agent update (residual) | Branch protection with required review (SECURITY §9) and Authenticode signing with an updater (T12) become launch gates | process controls, not a code test |

Wording for `SECURITY.md` §1/§8 and the FAQ "Is this end-to-end encrypted?", only after WP5.1 passes: "The
picture and sound are encrypted between your PC and your phone; DoMe's servers set up the connection but
cannot see them. Commands, typing and PC status are still not end-to-end encrypted. Your phone's own
screenshots or screen recording can capture what it shows; DoMe cannot prevent that. Some protected videos
and windows appear black." Never a product-wide "end-to-end encrypted" claim. ADR-0001 D1 ("the PC never
listens") and D10 ("screen viewing" not in V1) are amended by ADR-0002.

### 17.2 "Nothing recorded" and "the AI never sees it", enforced in the code with a test each

| Guarantee | Enforcement | Test |
| --- | --- | --- |
| The phone never reads pixels | Screen modules use only `<video>.srcObject`; no `drawImage`, `getImageData`, `toDataURL`, `toBlob`, `captureStream`, `MediaRecorder`, `ImageCapture`, `VideoFrame`; `requestVideoFrameCallback` (metadata only) is allowed | `mobile-app/test/no-pixel-access.test.ts` scans `src/` for the identifiers outside an empty allow-list |
| The phone never logs or sends media | `log.ts` drops `sdp`, `candidate`, `ice_servers`, `credential`; diagnostics exclude the session | log and diagnostics tests with a sentinel SDP |
| The worker cannot record or exfiltrate | No file writes except the crash log; no imports of `api`, `relay_client`, `assistant`; `aiortc.contrib` absent | import-graph test of `dome_agent.screen.worker`; a fake-platform run in a temporary profile asserts no unexpected file; the PyInstaller smoke test asserts `aiortc.contrib` is absent |
| The agent stores no content | History rows hold phone, times, reason, seconds; results hold no SDP or address; aiortc/aioice loggers pinned to WARNING and addresses masked in free text (§4.4) | sentinel test as `test_text_content_never_appears_in_logs_or_persisted_state` **[repo]**: SDP, candidate and TURN credential searched in logs, journal, SQLite, diagnostics and state frames; extended with a loopback session at `LOG_LEVEL=DEBUG` asserting the peer's `127.0.0.1:<port>` and `::1` never appear in the log file, the crash log or the diagnostics bundle |
| cloud-api never receives media | No route accepts `image/*`, `video/*`, `audio/*`; frames are text JSON ≤ 64 KiB | route-table test |
| The AI never receives it | `assistant_turn_request` / `assistant_context` stay closed schemas with no media field; `screen.*` are `ai_eligible: false` | `test_catalogue_is_exactly_the_ai_eligible_actions_sorted` extended; a schema test that no assistant property can carry more than 4 KiB of base64 |
| Servers see only ciphertext | §4 | e2e tampered fingerprint or answer → no media |

## 18. Test plan

**Unit (CI, Linux and `windows-latest`):** contract fixtures and cross-language vectors (PC-signed answer,
`pc_key_mac`, stamping, SDP pattern cases; a 1.4-stamped batch against a 1.2 verifier yields
`PROTOCOL_INCOMPATIBLE`, not `MALFORMED_MESSAGE`, in the 1.4 library); `ScreenSessionManager` on the fake
platform with one test per end reason, every emergency stop within budget, cooldown forces ask, **a second
controller's start during another phone's cooldown is asked**, **the third refused start within a minute
opens no window and is refused at once**, the 1-hour block, ticket expiry stops a PC cut off from the relay,
`controller_offline` ends within one ticket, snapshot-after-reconnect revocation before tickets resume,
stale `gen` dropped, owner mismatch dropped, `move_to` without a session refused, `local_permission_ui`
drops injected events; **indicator fail-closed** (a fake UI that refuses to create a window →
`SCREEN_CAPTURE_UNAVAILABLE{indicator}` and no worker spawn; a monitor set change pauses capture until bars
exist; liveness ticks stopped → the fake worker receives `exit` within 10 s); SDP checker table tests;
data-channel vocabulary (unknown messages refused); import-graph and no-file-write tests; the worker exits
on pipe EOF and on missing ticks; worker media tests on the fake source (**two consecutive still-frame
packets are distinct encoder outputs**, **NAL types 7 and 8 precede every type-5 NAL**, the `_send_keyframe`
hook exists on `RTCRtpSender` and a PLI sets the worker's keyframe event, a **float32 fake capture client
passes through the real `aiortc.codecs.opus.OpusEncoder`**); logger pinning and the IP-masking regex
(`redact_text_line`, `redact_value`, `crashguard.redact` on IPv4, IPv6, `[::1]:port`); `move_to` mapping
maths on layouts with negative origins and mixed DPI against a scripted user32 double; the licence gate on
synthetic folders and on a configuration string with a second decoder; phone `screenMap` (letterbox drop,
zoom and pan inverse, rounding), `DirectGestureMachine` (pinch vs scroll, loupe, second finger never clicks,
last-wins coalescing), `ScreenSessionClient` (every §4.3 refusal, `bye` on hidden, no media before
verification, stall → one retry, `stopped_on_pc` → no restart), pin store (valid MAC pins; wrong MAC, wrong
kid, missing MAC or lost code leaves unpinned; key change → hard stop), **`PairPage`: the code is retained
through `waiting` and absent after `done`, `failed`, `expired` and unmount**, every §16 state as a component
test, the no-pixel-access scan; TURN client against an HTTP stub (TTL from the allowance, one outstanding
credential per controller, revoke on every §13 path, daily and monthly caps, reconciliation parsing, the
provisional bound, guard trips at 700 and 900 GB, token never in logs or errors).

**Integration (CI):** relay routing against real PostgreSQL and dev-idp, modelled on
`test_input_routing.py` / `test_input_hardening.py` **[repo]** (grant required; a 1.2 socket refused; the
answer reaches only the issuing socket, never subscribers; a foreign `ref_controller_id` refused; oversize
SDP; **a `SCREEN_*` rejection frame reaches the issuing controller's 1.4 socket and no subscriber**; **a 1.4
phone's batch with `pointer_move_to` against a 1.2 agent is refused by the relay with
`PROTOCOL_INCOMPATIBLE` and the agent never sees it** (`tests/test_e2e_versions.py` with
`tests/older_agent.py` **[repo]**); no `commands` content and no SDP in logs); the ledger (10 concurrent
sessions never exceed 600 s, **including a lying agent that reports `live_seconds: 0` and `connection_failed`
on every end and still exhausts its window**; window roll-over; `connection_failed` costs 0 only when the
relay saw no `started` frame within 20 s and at most 3 times per window; pauses discounted at most 120 s;
refund once and never more than the relay-measured remainder; sweeper charges stale reservations; the
override refused in production); TURN (a credential minted without a start is revoked within 60 s; a second
mint revokes the first; **logout of the owning controller ends the stream within one ticket** and revokes
its credential); pairing (`pc_key_mac` stored, returned only once approved, still readable 5 minutes after
approval, invisible to another account); `tests/test_e2e_screen.py`: the real agent on the fake platform
with a **fake screen source** (synthetic frames) and a **Python aiortc peer acting as the phone** signing
with the fixture key over loopback host candidates: frames arrive; a tampered offer fingerprint or a swapped
answer means no media and a refusal; lease expiry ends the session; takeover; revocation and "Disable remote
control" end it; **the "phone" sends a PLI and the next packet from the fake source is a keyframe within
500 ms**; taps arrive as `move_to` at the right normalised position; the ledger refuses at the limit; no
SDP, address or credential in database rows, journal or logs, and no `127.0.0.1:<port>` in the agent's
log, crash log or diagnostics bundle. The frozen worker's `--self-test` runs in `windows-installer.yml`
(imports, encoder list, LGPL string, exactly one decoder, a decoded `testsrc` frame through the `lavfi`
device, the PLI hook); capture is reported, not required, on the GPU-less runner [research].

**Real-device checklist (the founder's Windows 11 PC and iPhone, plus a borrowed Intel-graphics laptop and
an AMD PC where possible; iOS 18.x and 26.x, each in a Safari tab and the home-screen app):**

1. PyAV drives `ddagrab → scale_d3d11 → hwdownload` through the `lavfi` device with the `wrapped_avframe`
   decoder; `h264_mf hw_encoding=1` on NVIDIA, Intel and AMD; **whether each vendor's MFT repeats SPS/PPS on
   every IDR** (the worker prepends `extradata` when it does not, §5.3); time to first frame; PC CPU and RAM;
   the WGC fallback on a hybrid-GPU laptop.
2. Safari accepts the stream (profile and level 3.1, Annex B through aiortc's packetiser); first frame;
   keyframe on PLI; VP8 fallback playback; Low Power Mode; the silent switch (Phase 2).
3. Glass-to-glass latency on the same Wi-Fi and over cellular (a PC test page with a millisecond counter
   filmed together with the phone at 240 fps); tap-to-photon.
4. NAT outcomes: same Wi-Fi (direct via mDNS vs hairpin vs relayed), home PC ↔ cellular phone, hotel-style
   Wi-Fi forcing TURN over TLS 443; how often `path = relayed`; the Windows firewall prompt for a standard
   user on Private and Public networks, and whether ICE works after Cancel.
5. Lease and tickets: background 5 s / 30 s, lock, an incoming call → the PC ends within 8 s and the Free
   clock stops; the PC's relay socket cut → the stream ends within 60 s.
6. mDNS `.local` candidate resolution from the standard-user worker (multicast join, firewall).
7. Indicator: bars excluded from the capture on each capture path; bars over an exclusive full-screen
   game; tray badge; notifications; every Stop path; the hotkey; the Job object killing the worker when
   DoMe.exe is ended in Task Manager.
8. Lock, UAC pause and resume, resolution change, monitor unplug, rotated display, HDR, mixed DPI, an
   elevated-window tap, a `WDA_MONITOR` window and a DRM video showing black.
9. Tap precision at 1×, 2× and 4× zoom and with the loupe on a 1080p and a 1440p desktop; letterbox taps
   dropped; pinch vs scroll; smooth `pointer_wheel` scrolling.
10. Pinning: re-pair the founder's iPhone; a `--new-key` re-link triggers the hard stop.
11. Landscape with `orientation: "any"`; wake lock; a 30-minute battery and heat check; iOS 26.0.x
    home-screen media regressions [research].
12. Phase 2: WASAPI loopback (silence, device change, does endpoint mute silence loopback?), unmute in a
    tap, `audioSession`, audio after a phone call; the in-app mini view; the TV remote.
13. The frozen build on a clean Windows 10 22H2 standard-user account: antivirus and SmartScreen behaviour,
    unsigned vs signed; Windows N behaviour.

**Phase 0 go/no-go gate** (recorded in ADR-0002 before Phase 1 starts):

| Criterion | Threshold |
| --- | --- |
| Plays in Safari and the home-screen app on iOS 18 and 26 | yes |
| First frame after "Show screen" | < 4 s |
| Same-Wi-Fi glass-to-glass | < 250 ms |
| PC CPU at 720p30 with hardware encoding (worker + agent) | < 10 % of the total |
| Works relayed through Cloudflare TURN (`iceTransportPolicy: "relay"` forced) | yes |
| Same-Wi-Fi direct via mDNS from a standard-user process | informational; decides the relayed-share assumption |

No-go fallbacks: `h264_mf` fails broadly → VP8 at 15 fps; PyAV cannot drive `ddagrab` → the `Graph` API or
CPU scaling; iOS playback fails → stop and reconsider the ADR.

## 19. Phased work packages

Sizes are rough engineer-day estimates [estimate]. Phases 1–4 of the build run in parallel per component
once WP1.1 lands; the integration round joins them.

**WP-0 (now, ≈ 0.5 day; mobile-app):** a "Screen view: Coming" card on the Remote page from
`SCREEN_VIEW_STATUS`, with a component test asserting the label and no claim of availability. Unit-tested. **Status 2026-10-20: built; unit-tested (jsdom). Card component `ScreenViewComingCard`; not iPhone-tested.**

**Phase 0: spikes and the gate (≈ 6–9 days; nothing ships)**

| WP | Component | Contents | Tests / acceptance |
| --- | --- | --- | --- |
| WP0.1 | pc-agent packaging | `.github/workflows/ffmpeg-lgpl.yml` (manual `workflow_dispatch` on `windows-latest`): FFmpeg with the §5.5 flags (including `--enable-decoder=wrapped_avframe` and the `testsrc`/`color` test sources) and PyAV (the version aiortc 1.15.0 accepts), keeping delvewheel's `av.libs` layout so the contrib `hook-av.py` works [research]; wheel published as a release asset pinned by SHA-256; `pyproject.toml` `[tool.uv.sources]` entry `av = [{ url = "<release-asset>.whl", marker = "sys_platform == 'win32'" }]` with PyPI as the fallback elsewhere, `uv.lock` regenerated; `license_gate.py` | gate fails on a planted `libx264.dll` and on a second decoder; `import av`; encoders list `h264_mf`, `libvpx`, `libopus`; decoders list is exactly `wrapped_avframe`; `ddagrab` listed; **the frozen worker pulls ≥ 1 decoded frame from a `lavfi` graph (`testsrc` or `color` → `format=nv12`)**; aiortc imports; **`uv sync --locked --extra dev` succeeds on `ubuntu-24.04` and `windows-latest` from the same lock** → **unit-tested** (CI)  **Status 2026-10-20: `ffmpeg-lgpl.yml` and `packaging/licence_gate.py` written (the gate is named `licence_gate.py`; §15 says `license_gate.py`); gate logic unit-tested on Linux; the workflow has not been run yet; the `pyproject.toml` source entry, the lock and the release asset are not done.** |
| WP0.2 | pc-agent (dev only) | `DoMe.exe screen-probe`: captures 5 s into memory with each source and encoder, prints fps, CPU, encode latency and failures; writes no images; serves the stream to a dev-only staging page through aiortc and a staging Cloudflare TURN key | the §18 gate → **not yet verified on a device** until the founder runs it  **Status 2026-10-20: skeleton only (`dome_agent/screen/probe.py`: argument parsing, result table, §18 gate evaluation from JSON, a fake backend); unit-tested; no capture or encode code exists; not yet verified on a device.** |
| WP0.3 | owner | Counsel on H.264 (§5.5); Cloudflare pricing and payment-method check on the vendor page | written go/no-go |

**Phase 1: "Screen view beta" (≈ 22–30 days)**

| WP | Component | Contents | Tests / acceptance |
| --- | --- | --- | --- |
| WP1.1 (≈ 3 d) | shared/protocol | §11 in full; `PROTOCOL.md` §20; fixtures; Python and TS libraries; generated types and validators | registry/schema tests, `test_shared_cases`, stamping (`pointer_move_to` below 1.4 → `PROTOCOL_INCOMPATIBLE`), cross-language answer and MAC vectors → **unit-tested** |
| WP1.2 (≈ 5 d) | cloud-api | §12: routing (with the input-batch stamp gate), start hook, answer and session frames, `screen_usage` / `screen_allowance` (with `controller_offline`), ledger + migration 0004 settled on the relay clock, TURN client with controller-bound credentials, revocation on every §13 path, per-account caps, **hourly Cloudflare usage reconciliation and the provisional bound** (moved here from Phase 3), the `_on_agent_error` prefix filter, pairing `pc_key_mac` and the approved-row retention invariant, the logout/revocation hook, redaction, security events, settings, deploy secrets, `/healthz` | §18 integration list against real PostgreSQL (including the lying agent, the logout test, the stamp-gate test, the `SCREEN_*` rejection test and the credential-revocation tests) → **integration-tested** |
| WP1.3 (≈ 6 d) | pc-agent core | `screen/session.py` (PC-wide cooldown, consent back-off, one window per minute, the 1-hour block), `supervisor.py` (bars confirmed before spawn, two-way liveness, redacted stderr), `sdp_check.py`, `signing.py`, `consent.py`, `history.py`, `actions/screen.py`, grants (`pairing.py`, `agent.py`, CLI, tray), `pc_key_mac` in `pairing_decision`, `InputAdapter.move_to` / `wheel` with `gen` and owner checks, `local_permission_ui`, emergency-stop ordering, redaction (IP masking, logger pinning) | `tests/test_screen_session.py` modelled on `test_input_session.py` with `FakeScreenWorker` (including the §18 cooldown, back-off, fail-closed indicator and liveness tests); `test_link_pairing.py` (MAC vector; `screen` never granted at pairing, even with `--yes`); mapping maths; the sentinel test with a loopback peer → **unit-tested** |
| WP1.4 (≈ 5 d) | pc-agent worker | `screen/worker/*`: pipe protocol, Job object, aiortc send-only (STUN only), strict offer use, pre-encoded H.264 track with SPS/PPS before every IDR, VP8 fallback, PLI → IDR through the `_send_keyframe` hook, 1 fps floor by re-encoding the held raw frame, path detection, bitrate steps from `getStats()`, data-channel vocabulary, no capture before `connected`, exit on missing ticks, logger pinning, `--self-test` | Linux unit tests with the `fake` source (synthetic frames → an aiortc receiver; distinct still-frame packets; NAL 7/8 before every NAL 5; PLI hook; the stock PyAV wheel from PyPI, tests only); import-graph and no-file-write tests → **unit-tested**; Windows capture and encoding → **not yet verified on a device** |
| WP1.5 (≈ 2 d) | pc-agent UI | Grant window (5 s hold, scam line, options), consent window (Deny, Allow, Block 1 hour, Turn off for this phone), bars on every monitor created and confirmed before capture, tray badge and "Stop Screen view", hotkey, notifications, history view, `StatusView.screen_session` | fake-UI tests of the window model, of `local_permission_ui` and of a UI that cannot create a window → **unit-tested**; tkinter windows and display affinity → **not yet verified on a device** |
| WP1.6 (≈ 2 d) | packaging | `dome-agent.spec` (aiortc and deps, custom `av`, `screen-worker` subcommand, `aiortc.contrib` excluded), `THIRD_PARTY_NOTICES.txt` + installer licence page + EULA clause, `windows-installer.yml` steps (gate on `dist/`, `screen-worker --self-test`) | `windows-installer.yml` smoke → **integration-tested** on the runner |
| WP1.7 (≈ 6 d) | mobile-app | `lib/screen.ts`, `screenMap.ts`, `directGestures.ts`, `pcKeyPins.ts`, `input.ts` changes, `ScreenPage` with every §16 state, the loupe, Touchpad-mode toggle, **`PairPage` pinning: the `waiting` step keeps the normalised code (or a derived non-extractable HMAC key) until the poll returns `approved` with `pc_key`, verifies the MAC, writes the pin, then zeroes the code; cleared on `done`, `failed`, `expired` and unmount (§4.1)**, Remote tile and Home card, manifest `orientation: "any"`, wake lock, labels, validators allow-list | the §18 unit list, the `PairPage` component test "code is retained through `waiting` and absent after `done`/`failed`/`expired`/unmount", `no-pixel-access.test.ts`, `protocol-parity.test.ts` → **unit-tested**; playback and gestures → **not yet verified on a device** |
| WP1.8 (≈ 3 d) | integration | `tests/test_e2e_screen.py` (§18, including the PLI test and the loopback-address sentinel); `tests/test_e2e_versions.py` row for the 1.4 batch against `older_agent.py`; `ControllerSim` gains screen frames; a `conftest.py` aiortc "phone" | → **integration-tested** |
| WP1.9 (≈ 2 d) | docs and website | ADR-0002 numbers; ADR-0001 D1/D10 amendment note; `SECURITY.md` T24–T34 and §1/§8 wording, **and T4 amended: signing a phone out or revoking its account session also ends its Screen view within about 60 s (`controller_offline`)**; `PROTOCOL.md` §20 (including "1.2 agents answer `MALFORMED_MESSAGE` to a 1.4-stamped batch; the relay refuses such batches before forwarding") and the §18.7 matrix row; `ARCHITECTURE.md` §3.3; `COST_MODEL.md` §7B (§14); `INPUT_CONTROL.md` line 151; the privacy page (sub-processor); `HANDOFF.md`, `PROGRESS.md`, `ACCEPTANCE.md` scenarios 26–30 with device rows; FAQ, `plans.ts` and film copy per §22 | existing website tests updated → **unit-tested** |
| WP1.10 | device round | §18 checklist items 1–11 and 13 with the founder; results in `docs/ACCEPTANCE.md` with Windows build, iOS version and Safari/home-screen mode | **verified on a device** only for what passes |

**Launch gate** before `SCREEN_VIEW_STATUS` changes from "Coming" (DECISIONS 63): device evidence on at
least one PC per GPU vendor and on iOS 18 and 26 in both modes; Authenticode signing and the updater (T12);
branch protection with required review (SECURITY §9); counsel's answer (or VP8 only);
`DOME_SCREEN_VIEW_ENABLED` on in production. Until then Phase 1 is reachable only by the founder's account
on staging and, behind the switch, on production.

**Phase 2: sound, mini view, TV remote, second monitor (≈ 8–12 days)**

| WP | Contents | Tests |
| --- | --- | --- |
| WP2.1 pc-agent | `audio_loopback.py` (mix format → interleaved s16 stereo 48 kHz before each 960-sample frame, §5.4) → Opus; `screen_audio` grant plumbing; the speaker glyph; `audio{on}` message | silence generation with a fake capture client, and a **float32 fake capture client through the real `aiortc.codecs.opus.OpusEncoder`** → unit-tested; loopback → device |
| WP2.2 pc-agent | "Next screen": a new session with `surface: N`; cursor overlay (`draw_mouse=0`, shape and position messages) behind device data | unit-tested; device |
| WP2.3 mobile-app | In-app mini view (portal + store), Sound button and `audioSession`, `TvRemotePage`, "Still watching?", Data saver badge, direct-mode long-press drag | unit-tested lifecycle and key mapping; device |
| WP2.4 docs | Labels flip for the delivered parts; mini-view wording per the founder's decision | website tests |

**Phase 3: only where data asks**

| Item | Trigger |
| --- | --- |
| Sharp zoom (region crop, `roi`) | users zoom often; text complaints |
| 1080p "sharp text" (level 4.0 negotiation) on direct paths | complaints about small text |
| PC-side TURN over TLS 443 | support tickets from UDP-blocked PCs |
| Safari-tab PiP experiment with `getStats()` liveness | device test passes |
| Private input: signed `input_batch` envelopes over the data channel, verified identically by the agent, accepted only while the PC's relay socket is up and past its first snapshot | typed-text privacy, or ≥ 50 ms p50 tap-to-photon gain outside US-East |
| Finer per-account TURN limits (relayed hours, per-controller GB) beyond the Phase 1 daily GB and monthly mint caps; raising the caps for paid plans | TURN usage passes 50 % of the free tier, or paid plans launch |
| Warm worker | spawn plus import above ≈ 1.5 s |
| `pc_state.screen_session` with per-version stripping (`PC_STATE_1_1_FIELDS` → a per-version map) | the Health page wants it |
| Process loopback (exclude DoMe's own sounds); worker at low integrity; optional Face ID before Screen view | later |
| Move `media.py` from archived winsdk to `winrt-*` | dependency hygiene [research] |

## 20. Owner (non-technical founder) setup steps and costs

| Step | What to do | Time | Cost |
| --- | --- | --- | --- |
| 1. Relay server key | In the Cloudflare account already used for DNS (`COST_MODEL.md` §2A): Dashboard › Realtime › TURN Server › Create, twice: `domeremote-staging` and `domeremote-production`. Also create one **read-only Analytics API token** (the engineer names the exact permission) so the service can read its own TURN usage every hour. In GitHub › Settings › Environments add `DOME_TURN_KEY_ID` (variable), `DOME_TURN_API_TOKEN` and `DOME_TURN_ANALYTICS_TOKEN` (secrets) to each environment, then press Deploy as usual. Check on screen whether Cloudflare asks for a payment method and turn on its usage notifications. | ≈ 20 min | **$0** up to 1,000 GB/month relayed, then $0.05/GB [research, **verify** on the vendor page] |
| 2. Monthly check (backstop) | The service limits relay use by itself (per-account daily and monthly caps, automatic reconciliation with Cloudflare's figures, §13). Once a month, compare the Cloudflare dashboard's TURN usage with the `/healthz` `turn_month_gb` figure; if they disagree by more than 10 % or usage nears 1,000 GB, tell the engineer, or set `DOME_TURN_PROVIDER=none` (Screen view then works only where a direct connection exists, with an honest message). | 2 min/month | $0 |
| 3. Code signing | Already planned (HANDOFF item 4); now required before anyone but you installs a Screen view build. | per the existing plan | ≈ $9.99/month (Azure Trusted Signing, `COST_MODEL.md` §2; price to verify) |
| 4. Legal | Two questions for counsel: (a) is relying on the Windows/GPU H.264 encoder acceptable, and must DoMe join the AVC pool (free under 100,000 units a year)? (b) are the LGPL notice, source offer and EULA clause adequate? If (a) is no, Screen view ships with VP8 only. | one meeting | unknown, one-off |
| 5. Device rounds | Run the §18 checklist with the engineer's script (≈ 2–3 h per phase); borrow an Intel-graphics laptop and an AMD PC if possible. Re-pair your iPhone once after the 1.4 update so it pins the PC's key. | 2–3 h/phase | $0 |
| 6. Decisions | §22 | — | — |

Not needed: no new Fly machine, no dedicated IPv4, no new server, no Apple developer account. Added hosting
cost at launch scale: ≈ $0–3/month (§14).

## 21. Open questions

1. Does PyAV drive the `ddagrab → scale_d3d11 → hwdownload` graph and `h264_mf hw_encoding=1` reliably on
   NVIDIA, Intel and AMD, and is zero-copy D3D11 input to the encoder reachable? (Phase 0.)
2. Does mDNS `.local` candidate resolution work from a standard-user Windows process, and how does the
   Windows Firewall prompt behave for a standard user on Private and Public networks? (Phase 0.) This
   decides the relayed-share assumption in §14.
3. Does an expired Cloudflare TURN credential end a running allocation, what is the maximum TTL, is a
   payment method required, does the revoke endpoint behave as documented (and does it end a running
   allocation, not only refuse new ones), and do the Analytics API's TURN usage fields (`customIdentifier`,
   `username`, `egressBytes`) and their freshness match what §13's hourly reconciliation assumes? Until
   confirmed, the provisional server-side bound carries the guard.
4. Does Safari accept H.264 above the level it offered, and does it send REMB? (Decides Phase 3 quality
   items.)
5. Should ask mode be the default for phones that hold only `screen` (watch-only family members)? The
   proposal's default is "start right away" with the mandatory indicator.
6. Should the Phase 3 "private input" item move earlier so typed text is end-to-end protected as soon as
   Screen view ships?
7. Does Windows Media Foundation honour the negotiated constrained-baseline profile, and does muting the
   PC's endpoint also silence loopback capture (Phase 2)?
8. CSP on WebKit: is `srcObject` playback and WebRTC traffic really untouched by `connect-src 'self'`?

## 22. Decisions for the founder

1. **Mini view wording.** Approve changing "floating mini view while you use another app" to "a floating
   mini view while you use the rest of DoMe, and picture-in-picture over other apps where your phone allows
   it (in the browser)". Recommended now: the current copy promises something iOS does not allow for
   home-screen apps [research].
2. **Staged labels.** "Screen view (beta)" in the app during Phase 1; `SCREEN_VIEW_STATUS` stays "Coming"
   until the launch gate; then "Coming" on PC sound, mini view and TV remote until Phase 2 ships.
3. **Free limit binds you too.** No production override; staging may use a short limit for testing.
4. **Two capabilities** (`screen`, `screen_audio`), both PC-granted, rather than one with a PC-wide sound
   switch.
5. **Re-pair existing phones once** so they pin the PC's key; no insecure fallback.
6. **Post-Stop cooldown** on by default (10 minutes), adjustable per phone in the grant window.
7. **H.264 via the OS encoder** pending counsel, with VP8-only as the zero-patent-risk fallback.
8. **Cloudflare as a sub-processor** on the privacy page; code signing and branch protection as launch
   gates.

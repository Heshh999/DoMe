# pc-agent — the DoMe Windows user agent

`dome_agent` runs in the logged-in user's Windows session, opens **one outbound WebSocket** to the
managed relay and executes locally authorised actions: YouTube through the browser extension
(Native Messaging bridge), Windows media sessions, system volume, approved applications, lock,
explicitly confirmed power actions and — protocol 1.1 — the Free **manual touchpad/keyboard** stream
(`SendInput`). It never listens on the internet, never accepts a path, argument or shell text over the
wire, and honours a local **Disable remote control** switch that no remote frame can undo. One agent
runs per Windows user session, and at most one per Windows account (they share one state directory and
PC identity; see "Single instance").

Evidence tags used below: **unit-tested** (pytest on Linux with the fake platform/relay/extension),
**integration-tested** (full agent ↔ fake relay ↔ fake adapters path), **not yet verified** (needs a
Windows device or an iPhone; nothing in this component has been Windows-device-tested or iPhone-tested).

Design: `docs/design/pc-agent.md`. Contract: `shared/protocol/` (frozen; verified through the
`dome-protocol` library for every trust-boundary input). Decisions taken while building:
`DECISIONS.md`. Contract friction found while building: `CONTRACT_ISSUES.md`.

## Layout

```
dome_agent/
  cli.py           dome-agent run|link|unlink|pair|pair-approve|pair-decline|status|enable|disable|
                   approve-app|remove-app|revoke|grant|stop-input|stop-screen|screen-history|
                   install-native-host|uninstall-native-host|repair|reconnect|diagnostics|quit|start-at-login|
                   version · screen-worker (INTERNAL: the Screen view media process the agent starts)
  crashguard.py    frozen entry points: any exception → logs/crash.log + exit 70, never a modal error box
  agent.py         orchestrator: frame dispatch, snapshot application, mismatch handling, local controls,
                   input_batch verification, grant_update, every input-session end trigger
  input_session.py manual-input session manager: one session per PC, lease watchdog, seq/age/capability
                   checks, ordered dispatch with motion coalescing, backpressure, holds + crash recovery, acks;
                   1.4: pointer_move_to (absolute, needs the owner's connected screen session at a known gen),
                   pointer_wheel, key f11, and rules.local_permission_ui (no presses while a DoMe window is open
                   or onto DoMe's own bars)
  screen/          Screen view (protocol 1.4): session.py (ScreenSessionManager: start checks in the design's
                   order, lease, allowance tickets, pause/resume, takeover, end order, recovery), supervisor.py
                   (the worker process: pipe, Job object, ticks, redacted stderr), sdp_check.py (strict offer
                   check), signing.py (PC-signed answer), consent.py (consent gate, permission-UI state, window
                   models), indicator.py (bars confirmed before capture, reserved rectangles), history.py,
                   preflight.py (the Desktop Duplication probe before the bars: raw HRESULT → honest detail),
                   probe.py (Phase 0 probe), worker/ (the separate send-only aiortc process: main, rtc, track,
                   encode, fake sources; capture_dda / capture_wgc written for Windows, not yet verified)
  single_instance.py one agent per user session: named mutex (Windows) / flock, second-launch `show`, repair
  authz.py         authorization in the design's exact order (KeyRecord resolver = local store ∩ snapshot)
  queue.py         serialized executor: depth 16, coalescing groups, timeouts, lifecycle frames, never-retry
  confirmations.py challenge issue / atomic consume (challenge_text, pinned kid, target-state digest)
  relay_client.py  hello → hello_ack → snapshot-before-commands, backoff+jitter, 25 s pings, 4001/4003/4008
  api.py link.py   REST client (schema-validated bodies) and the device-link flow
  entitlement.py   EdDSA entitlement assertions (joserfc + JWKS), 80 % refresh, 72 h grace on network/5xx only
  pairing.py       local code + QR, kid check, HMAC verification code, approve/decline
  store.py         SQLite (WAL): settings, grants, bounded journal, challenges, approved apps, pending power
  identity.py secrets.py  ES256 PC key + credential (DPAPI on Windows, 0600 files elsewhere with a warning)
  approved_apps.py local .exe approval with path validation and executable hash pinning
  state.py         pc_state aggregation → state frames (after snapshot, on change debounced, every 30 s)
  actions/         one handler per registry action; results validated with registry.validate_result
  platform/        protocol.py (interfaces incl. InputAdapter/ForegroundApp) · windows/* (real APIs incl.
                   input.py = SendInput, instance.py = mutex, dxgi.py = adapters/outputs + the DuplicateOutput
                   probe, displayconfig.py = HDR state) · unsupported.py (other OSes)
  bridge/          host.py (native-messaging host), ipc.py (named pipe / unix socket), server.py (agent side)
  control.py       local CLI ⇄ agent channel (same identity rule as the bridge IPC)
  tray.py ui.py    pystray tray + tkinter pairing windows; 1.4: the Screen view grant and consent windows, the
                   "Screen view on" bars (one per monitor), the red badge, the Ctrl+Alt+Shift+S hotkey, the
                   history window; NullUI for headless runs (no bars → Screen view fails closed)
  diagnostics.py   redacted bundle, with a hardware inventory (monitors; on Windows DXGI adapters/outputs, remote
                   session, build, GPU preference, HDR state; each item on its own, no handles, no pixels)
  testing/         fake_platform.py, fake_relay.py (fake API + relay), fake_extension.py, fake_ui.py,
                   fake_screen_worker.py — TEST DOUBLES ONLY
packaging/         dome-agent.spec (ONE PyInstaller folder: DoMe.exe, dome-native-host.exe, _internal),
                   agent_entry.py / native_host_entry.py, installer/ (DoMe.iss = Inno Setup per-user
                   installer, build_installer.py, smoke_test.py; built and tested by
                   .github/workflows/windows-installer.yml)
tests/             pytest suite (runs on Linux)
```

## Development on Linux/macOS

```bash
cd pc-agent
uv venv --python 3.12 && uv sync --extra dev
uv run pytest -q -p no:cacheprovider   # ~3.5 minutes; fake relay/API/extension/platform, real signatures
uv run mypy dome_agent
uv run ruff check . && uv run ruff format --check .
```

Last full run on Linux (2026-10-09, after the input review fixes): **256 passed, 1 skipped** (the
state-directory permission probe cannot fail as root), mypy strict clean (58 files), ruff check and
format clean. The review-fix round added 12 tests (phone clock 2 s behind/ahead, relay-leg stall,
`AgeEstimator` unit, keyboard batches queued across a target change, the 2 s grace continuation, an
elevated window with a stale/unknown cached foreground, click on a held button, a dispatch outliving the
bounded wait, a shortcut whose recovery release failed, the Windows shortcut recovery with a scripted
`_send` double, the same-account other-session refusal) and changed four expectations on purpose: the
`input.session_start` result no longer carries `window_title`; typing sent right after a target change
stays blocked until a click; the secure-desktop test drives the fake's new `secure_desktop` flag instead
of "restricted + no foreground"; `--yes` no longer grants pointer/keyboard. Earlier 1.1 baseline: 244
passed, 1 skipped. New with protocol 1.1: `tests/test_input_session.py`
(40 tests incl. the review fixes: lifecycle, ordering/coalescing, replay/stale/forged batches, lease and keepalives, holds and
every end trigger, target change, ack rate, backpressure, crash recovery, grant_update delivery, content
hygiene, clock skew), `tests/test_windows_input_layout.py` (9), `tests/test_single_instance.py` (8), pairing/CLI
capability prompts (2), power confirmation copy (1), registry handler coverage for the two input actions.
Expectations changed on purpose: `test_bridge.py::test_incompatible_extension_is_refused` now expects
`supported == ["1.0", "1.1", "1.2"]` (the agent announces every MINOR it speaks: `SUPPORTED_PROTOCOL_VERSIONS`,
DECISIONS.md #38; see CONTRACT_ISSUES.md #15);
`tests/helpers.py::ALL_CAPS` includes `pointer` and `keyboard`.

Run the agent against a local cloud-api (see `cloud-api/README.md`) with the **fake platform**
(explicit opt-in; the installer never sets it; state frames then report `platform: "development"`):

```bash
export DOME_AGENT_API_URL=http://127.0.0.1:8000
export DOME_AGENT_STATE_DIR=/tmp/dome-dev          # default: ~/.local/state/dome
export DOME_AGENT_PLATFORM=fake                     # Windows-only adapters replaced by in-memory doubles
export DOME_AGENT_HEADLESS=1                        # no tray / tkinter
.venv/bin/dome-agent link --no-browser              # prints the user code + approval URL; approve in the browser
.venv/bin/dome-agent run                            # connects to the relay URL returned at link time
# in another shell:
.venv/bin/dome-agent status
.venv/bin/dome-agent pair                           # shows QR + code, waits for the phone, asks to approve
.venv/bin/dome-agent pair --print-code --no-wait    # scripts/tests: code on stdout, then `pair-approve <id>`
.venv/bin/dome-agent disable                        # emergency stop (local flag; remote frames cannot undo)
```

Without `DOME_AGENT_PLATFORM=fake` the agent still starts on Linux: YouTube control works through the
browser bridge; every Windows-only action answers `PLATFORM_UNSUPPORTED`.

The browser bridge endpoint is a Unix socket `<state dir>/bridge.sock` (0600, peer uid checked). A
test extension can drive it with `dome_agent.testing.fake_extension.FakeExtension`.

Environment variables: `DOME_AGENT_STATE_DIR`, `DOME_AGENT_API_URL`, `DOME_AGENT_RELAY_URL` (override
the URLs stored at link time), `DOME_AGENT_LOG_LEVEL`, `DOME_AGENT_HEADLESS`, `DOME_AGENT_PLATFORM`
(`fake` only), `DOME_AGENT_DEV_EXTENSION_ID` (adds a development extension id to the native-host
manifest). Nothing secret is configured through the environment.

## Manual touchpad and keyboard input (protocol 1.1, spec §10A)

Pointer and keyboard events travel in signed `input_batch` frames that bypass the command queue
(`rules.input_sessions`); only `input.session_start` / `input.session_stop` are commands.

* **Verification per batch** (`Agent._on_input_batch`, integration-tested): signature with the LOCAL
  grant's key record (`verify_and_parse_input_batch`, same resolver as commands), `target_pc_id`,
  snapshot received, local `remote_enabled`, plan state, live non-revoked grant. Then the manager
  (`InputSessionManager.handle_batch`): the id must be the live session owned by this controller
  (`INPUT_SESSION_REQUIRED` / `INPUT_SESSION_EXPIRED` for a retired id), `seq` strictly increasing
  (`INPUT_SEQUENCE_INVALID`), age ≤ `input_age_budget_ms` = 1000 (`INPUT_STALE`), event types covered by
  the session's pointer/keyboard flags = the grant's effective capabilities (`INPUT_NOT_PERMITTED`).
  Every valid batch renews the 3 s lease; an empty batch is a keepalive. Rejections drop the batch, are
  counted in `dropped_events` and emitted as `error` frames (≤ 1 per code per second); they never end
  the session. No journal row, no security event, no content in any log line.
* **Age is judged against the phone's own clock** (unit- and integration-tested with a phone clock 2 s
  behind and 2 s ahead): `issued_at` is stamped by the phone, so the agent keeps a per-session windowed
  minimum of `receipt − issued_at` (seeded by the `input.session_start` command) and measures each
  batch's delay over the fastest recent one; the relay's `received_at` bounds the relay → agent leg the
  same way. A constant skew neither makes every batch stale nor hides a stall (DECISIONS.md #39).
* **What reaches the phone — honest limitation.** The contract's `error_frame` names no session or
  controller, and the current relay (cloud-api) only logs agent `error` frames; it forwards nothing. In
  the integrated system the batch-rejection REASONS (`INPUT_STALE`, `INPUT_SEQUENCE_INVALID`,
  `INPUT_NOT_PERMITTED`, `INPUT_SUSPENDED`, `INPUT_RESTRICTED`, `INPUT_INJECTION_FAILED`,
  `INPUT_TARGET_CHANGED`) therefore do **not** reach the phone. What does reach it: the growing
  `dropped_events` count in `input_ack`, `input_session{suspended|ended, reason}`, and `pc_state`
  (`input_restricted`, `foreground_app` — the agent triggers a state frame when either changes). The
  pc-agent tests read the error frames from the fake relay, which proves only that the agent emits them
  (CONTRACT_ISSUES.md #11 proposes the routing reference; not integration-tested on the real relay).
* **Dispatch** (unit/integration-tested): one worker, in order; adjacent `pointer_move`s summed, nothing
  merged across a button/scroll/text/key/shortcut; if the dispatch lag exceeds the age budget the backlog
  is discarded and the session is **suspended** (`input_session{suspended, backpressure}`, holds
  released, `INPUT_SUSPENDED` for further batches; a fresh `input.session_start` is required).
* **Target rule** (integration-tested): before each text/key/shortcut the foreground window identity
  (hwnd + pid) is compared with the one captured at session start or at the phone's last click; a change
  BLOCKS keyboard input of the session (`INPUT_TARGET_CHANGED` once; every later text/key/shortcut is
  dropped and counted, including batches the phone had already sent before it could know). The block
  clears on a user-directed click/press from the phone (re-capturing the target), on a fresh
  `input.session_start`, or for a batch issued ≥ 2 s after the change (by then the phone has the new
  `foreground_app` and has paused live typing; this is how a keyboard-only phone continues). Pointer
  events keep working throughout.
* **Restricted input** (integration-tested with the fake adapter): only a secure desktop
  (`secure_desktop_active()`: the input desktop is not `Default` or cannot be opened) or a locked session
  ends a session. An elevated or unknown-integrity window in front keeps it, sets
  `pc_state.input_restricted`, and keyboard events are refused with `INPUT_RESTRICTED` (Windows UIPI would
  drop them silently, so they are never acked as accepted); pointer events still run so the customer can
  click elsewhere.
* **Acks** (integration-tested): `input_ack` at most 4/s with `last_seq`, cumulative accepted/dropped
  counts and the current holds; Windows acceptance only, never an observed application effect.
* **End triggers** (integration-tested, every one): `input.session_stop`, lease expiry (watchdog task,
  independent of the relay), takeover, snapshot/local revocation, grant narrowing (`grant_removed` when
  pointer and keyboard are both gone; a narrowing of one flag keeps the session and releases a drag),
  Windows lock and secure desktop (polled every second), remote disable, relay disconnect / 4001
  supersession (`controller_disconnected`), agent stop (`agent_restart`). Order: retire the id → wait
  (≤ 2 s) for the in-flight dispatch → release exactly the buttons/keys this session injected → emit
  `input_session{ended, reason, holds_released}`. If the adapter call outlives the wait, whatever it
  still pressed is released the moment it returns (integration-tested with a slow fake adapter). A
  `click`/`double_click` on a held button clears that hold.
* **Crash recovery** (integration-tested): held buttons/keys are mirrored to `<state dir>/input_holds.json`
  (ids and names only, never content); the next start-up releases them, deletes the file and sends
  `input_session{ended, agent_restart}` after the first snapshot. Old session ids are rejected; nothing is
  replayed from disk.
* **State** (integration-tested): `pc_state.foreground_app` (refreshed at most every 2 s while a session is
  live, otherwise on request), `pc_state.input_session`, `pc_state.input_restricted`.
* **Content hygiene** (unit-tested by capturing the log file, stdout, security events, status, the
  diagnostics bundle, state frames, the SQLite file and the recovery file for a sentinel): text payloads
  exist only in memory and in the adapter call.
* **Windows adapter** `platform/windows/input.py` — `SendInput` with exact 32/64-bit `INPUT` layouts
  (unit-tested on Linux via fixed-width ctypes), relative `MOUSEEVENTF_MOVE` (no clamping to one display),
  `WHEEL`/`HWHEEL` × `WHEEL_DELTA`, `KEYEVENTF_UNICODE` per UTF-16 code unit with surrogate pairs kept
  in one call (unit-tested), VK codes + extended-key flags, CTRL shortcuts with the modifier always
  released (unit-tested sequence; after a partial insertion both the letter and CTRL get a key-up, and
  if that recovery fails too the adapter raises `InputHoldError` so the session keeps both tracked for
  the end-of-session release and crash recovery — unit-tested with a scripted `_send` double on Linux,
  not device-tested), `GetForegroundWindow` →
  process name via psutil, chrome/msedge detection, elevation via token integrity level (unknown when
  the process cannot be opened). A short `SendInput` count → `INPUT_INJECTION_FAILED`; access denied
  while the input desktop is not `Default` → `INPUT_RESTRICTED`; UIPI is never claimed from the return
  value alone. **Not yet verified on a Windows device** — see the checklist below. Off Windows the
  `unsupported` adapter answers `PLATFORM_UNSUPPORTED` and `input.session_start` refuses up front.

## Permissions and their scope (spec §10A-D)

`pointer` and `keyboard` are independent, Free, initially absent from every existing grant. Only the PC
owner adds them, locally: pairing approval (tray checkboxes / CLI prompts, off by default, with the
explanation below), the tray menu *Paired phones ▸ phone ▸ Allow touchpad / Allow keyboard*, or
`dome-agent grant <controller_id> --pointer --keyboard` (`--remove-*` to withdraw). `pair --yes` /
`pair-approve --yes` answer only the general approval: touchpad/keyboard are granted without a prompt
only when named explicitly (`--pointer` / `--keyboard`, or `--capabilities`), never implied by `--yes`
(unit-tested). The `input.session_start` result omits `foreground_app.window_title` because results are
journaled durably; the title reaches the phone only in memory-only `pc_state` frames (integration-tested). The agent then sends
`grant_update{controller_id, kid, capabilities}`; the relay replaces the list and re-pushes
`grants_snapshot`; effective capabilities stay local ∩ snapshot. A change made offline is journaled in
`pending_grant_updates` (by kid) and re-sent after the next snapshot, like local revocations
(integration-tested, including the offline re-send). Removing both input capabilities ends a live
session (`grant_removed`). The copy shown everywhere (`pairing.INPUT_SCOPE_EXPLANATION`): manual input
reaches every app of the unlocked Windows session, not only approved apps; the approved-app list
restricts structured app actions and is not a sandbox around a real mouse and keyboard.

Authorization uses `ActionSpec.satisfied_by` (capability or an alternate; `input.session_start` is
permitted by pointer **or** keyboard) and the `GRANT_MISSING` message names every accepted capability
(unit-tested). `ai_eligible: false` actions and the batch primitives are never offered to a model or a
routine (the agent has no AI path; the registry check is unit-tested).

## Screen view (protocol 1.4, Phase 1 beta; `docs/design/screen-view.md`, ADR-0002)

The phone sees the PC's picture over WebRTC between the two devices; the relay carries signed signaling only
and never any media. The agent side is `dome_agent/screen/`; the media stack is the optional extra `screen`
(`uv sync --extra dev` installs it; `aiortc==1.15.0`, `av>=14,<18`). **Evidence: everything below is
unit-tested on Linux with the fake platform, a fake UI, an in-process fake worker and real aiortc peers on
loopback (the fake frame sources `lavfi testsrc` / synthetic frames and the development `libx264`); Windows
capture, encoding, the bars, the hotkey and the Job object are not yet verified on a device
(KNOWN_ISSUES.md #19–#33). An end the worker reports (`bye`, `disconnected`, a late `error`, its exit) is also
tested through the REAL `ProcessWorkerLauncher` with a scripted worker process (DECISIONS #75), and the
cross-component suite runs the real agent and worker against a Python aiortc phone (`tests/test_e2e_screen.py`,
integration-tested on Linux). A build without the media stack refuses every start with
`ACTION_UNAVAILABLE{reason: screen_view_not_included}` (KNOWN_ISSUES #33).**

* **Start checks, in the design's order** (`ScreenSessionManager.start_session`): remote control on →
  session unlocked and not on the secure desktop → the relay's first allowance ticket (`relay.screen` on the
  forwarded `screen.session_start`, kept by command id; missing → `ACTION_UNAVAILABLE`) → the strict offer
  check (`sdp_check.check_offer`: ≤ 12 KiB, printable ASCII + CRLF, `v=0`, exactly one `sha-256` fingerprint
  value, `setup`, ICE credentials, exactly `video recvonly` + `audio recvonly` + one SCTP `application`
  section, H.264 or VP8; anything else `SCREEN_OFFER_INVALID{reason}`) → ownership
  (`SCREEN_SESSION_OWNED` unless `takeover`) → consent (below) → monitors → the Desktop Duplication pre-flight
  (`screen/preflight.py`: a DXGI `DuplicateOutput1` probe on the monitor's output, released at once, on its own thread,
  bounded to 500 ms; a PC without a D3D11 device is refused `driver`, a failing duplication — `busy` /
  `unsupported_display` / `remote_session` from the raw HRESULT — is handed to the worker, which skips Desktop
  Duplication for Windows.Graphics.Capture, else the start goes on as before) → the old session ends (`takeover` /
  `replaced`) → **a bar on every monitor, confirmed** (fail closed:
  `SCREEN_CAPTURE_UNAVAILABLE{indicator}`) → only then the worker is spawned. The worker's send-only answer is
  signed with the PC's identity key (`signing.sign_screen_answer`, verified against the shared fixture
  vector) and leaves in a `screen_answer` frame, never in the result or the journal.
* **The worker** (`DoMe.exe screen-worker`, `screen/worker/`): a separate send-only aiortc process on an
  anonymous pipe, Job object kill-on-close on Windows, exits on pipe EOF, on `stop` and after 10 s without an
  agent tick (ticks are sent only while the UI thread that owns the bars still answers). STUN only (no TURN
  credential ever reaches the PC). Capture starts only after `connectionState == "connected"` (DTLS to the
  certificate the phone signed): the capture source is constructed at `start` but opened only by the track after
  that, on every kind (`ddagrab`, its WGC fallback, a rebuild's new source). `ddagrab` runs with `dup_frames=1`
  (a `dup_frames=0` read blocks on a still desktop) and its `output_idx` is the monitor's index among the DEFAULT
  adapter's outputs, found through DXGI at open time (a monitor on another adapter goes straight to WGC); the source
  drops re-sent frames by a CRC-32 of their visible bytes, opens on a helper thread (the open waits for a real frame)
  and classifies failures from PyAV's class/errno and FFmpeg's captured lines. Pre-encoded H.264 track with
  SPS/PPS before every IDR, 1 fps floor by re-encoding the held raw frame (never re-sending a packet), PLI →
  IDR through the `RTCRtpSender._send_keyframe` hook, at most two requested IDRs a second (periodic IDR
  fallback), path detection from the
  nominated pair, bitrate steps 1.5 → 0.8 → 0.5 Mbps from `getStats()`, VP8 through aiortc's libvpx as
  "Basic picture mode". Data channel `dome-screen-v1`: inbound `hb` / `keyframe` / `bye` / `audio` and
  outbound `geometry` / `state` / `allowance` / `restricted` only, both directions validated against the
  contract; anything else is dropped and counted. `--self-test` and `--list-encoders` print JSON.
* **Lifecycle** (`rules.screen_sessions`): 8 s viewer lease renewed by `hb`; allowance tickets the agent
  ASKS for (`screen_usage` about 20 s before `granted_until`, the dead-man switch: no ticket →
  `allowance_exhausted` / `controller_offline` / `plan_disabled` / `allowance_unconfirmed`, so a PC cut off
  from the relay stops within one ticket and a signed-out phone within about 60 s); 4 h maximum; lock ends
  (`session_locked`); a UAC / secure desktop pauses and ends after 120 s; a missing bar (or a bar off its
  monitor, or a UI thread whose check has not run for 3 s) pauses and ends after 10 s; pause causes are a set, so
  capture resumes only once every cause is gone (the monitors are enumerated afresh every second, so a monitor plugged in without a bar counts too; bars
  are recreated for the new set, and a viewed monitor that changed size makes the worker rebuild its capture at a
  new geometry generation; the viewed monitor unplugged ends the session `capture_failed`); no DTLS within 20 s
  is `connection_failed`. The start answers within its own 45 s command timeout, and a start abandoned half-way
  (timeout, revocation) takes its bars, worker and consent window down. End order: retire the id → `stop` to the worker, ≤ 2 s,
  terminate → close the bars only once the worker has exited → `screen_session{ended}` → notification →
  history row; an end the worker asks for runs on a task of its own, never on the supervisor's pipe reader.
  Every emergency stop works offline: *Disable remote control* retires the screen `remote_disabled` (and
  withdraws a waiting consent window) BEFORE the input session is touched, releases held input while the worker
  exits, and returns once both have ended; revocation and snapshot narrowing end it `controller_revoked` /
  `grant_removed` / `plan_disabled`, quitting DoMe ends it `agent_restart` (and a crashed agent's session is
  recorded as such at the next start from `screen_session.json`, which holds ids only).
* **Grants only in a DoMe window** (`rules.local_permission_ui`): `screen` and `screen_audio` are never granted
  at pairing (greyed, "add later from Paired phones"), never by a bare flag on Windows and never by the phone
  or the server. Tray › Paired phones › *phone* › **Allow Screen view…** (or `DoMe.exe grant <id> --screen`,
  which opens the same window and waits) shows `pairing.SCREEN_SCOPE_EXPLANATION`, the scam line, the PC sound
  option, start right away / ask each time, and the post-Stop cooldown (10 min / 1 min / off); Allow unlocks
  after 5 s; the PC sound option says "(coming in a later update)". Narrowing is immediate (`--remove-screen`,
  "Turn off Screen view", the bar's "Stop and turn off") and ends a live session `grant_removed`; the bar's "Stop
  and turn off" and the tray's "Turn off Screen view" while that phone watches also start the PC-wide cooldown.
* **Consent and the PC-wide cooldown** (`screen/consent.py`): with ask mode, or during the cooldown that a Stop
  at the PC starts for EVERY phone, a window "*phone* wants to see this screen" offers Deny, Allow (after 1 s),
  Block this phone for 1 hour and Turn off Screen view for this phone; 30 s without an answer is
  `SCREEN_CONSENT_TIMEOUT`. At most one consent window per minute per PC; after a Deny or timeout further
  starts from any phone are refused without a window for 1, 2, 5, then 10 minutes. Refusals are written to the
  local history and reported as the security event `screen_consent_declined{reason}`.
* **DoMe never injects input into its own permission UI** (`PermissionUiState`): while a pairing approval,
  grant window or consent window is open every button and keyboard event from every phone is dropped
  (`INPUT_PAUSED_ON_PC`, one frame per second); a press whose cursor sits on a bar or DoMe window is dropped
  (`INPUT_TARGET_RESERVED`).
* **Tap to click** (design §8): `pointer_move_to{x, y, gen}` is mapped onto the streamed monitor in PHYSICAL
  pixels (`map_to_pixels`, last position wins, never summed) and injected with `SetCursorPos` inside per-monitor
  DPI awareness; it needs the batch's controller to own the connected screen session at the CURRENT geometry
  generation (`SCREEN_SESSION_REQUIRED` / `SCREEN_GEOMETRY_CHANGED` drop the batch, the input session continues;
  while a new generation is being announced every tap waits for it).
  `pointer_wheel` sends raw wheel units; `key: f11` is a plain key.
* **Content hygiene** (`rules.screen_content`): SDP exists in memory and on the private pipe only; results,
  frames, the journal, the history, the recovery file and the diagnostics bundle carry ids, geometry, flags,
  reasons and seconds. `offer_sdp`, `answer_sdp`, `sdp`, `candidate`, `ice_servers`, `username` are sensitive
  keys; free text (log lines, the worker's stderr, `crash.log`, the diagnostics tail) has IPv4/IPv6(:port)
  addresses and SDP attribute lines masked; the aiortc / aioice loggers are pinned to WARNING whatever
  `LOG_LEVEL` says (an address ending a sentence, "could not reach 192.168.1.5.", is masked too). The sentinel
  tests run a real loopback session at `LOG_LEVEL=DEBUG` and assert that
  `127.0.0.1:<port>`, the fingerprints and the ICE password reach no sink.
* **PC key pinning** (`rules.pc_key_pinning`): the pairing approve carries `pc_key_mac`, the HMAC over the PC's
  identity key keyed by the pairing code the server never holds (shared fixture vector), so the phone can
  verify the PC-signed answer later; no pin, no picture.
* **Operator switch**: the relay enables Screen view with `DOME_SCREEN_VIEW_ENABLED` (default off) and refuses
  starts and tickets itself; the agent needs no setting. The PC owner's switches are the grant window, the
  bar, the tray, the hotkey, `DoMe.exe stop-screen` and *Disable remote control*.
* **Device checklist (Phase 0 / §18 round, nothing here is device-verified)**: the bars on every monitor at 100 %,
  125 %, 150 % and mixed scaling, with a secondary monitor left of the primary (each bar on its monitor, taps under
  it refused); a still desktop for 10 s keeps ≥ 1 fps with no rebuild, and a start on a still desktop shows a picture
  within a second (else the first frame needs a cursor move: KNOWN_ISSUES #32); one UAC prompt pauses rather than
  ends; the WGC fallback's first frame is the primary's size; a second capture app (OBS display capture) or a Remote
  Desktop session makes the pre-flight hand the start to WGC ("capture pre-flight found Desktop Duplication failing"
  with the HRESULT, then "Desktop Duplication skipped: the pre-flight answered ..." and `source=wgc` in the log); on a
  hybrid-GPU laptop the external monitor streams through WGC with "Desktop Duplication skipped: ... not of the default
  adapter" in the log;
  the diagnostics bundle's `hardware` section lists the monitors, adapters and outputs; Ctrl+Alt+Shift+S; the Job
  object killing the worker when DoMe.exe is ended in Task Manager; the Windows firewall prompt on first use.
  Device round 2 additions (DECISIONS #97 to #104, KNOWN_ISSUES #39): `source=dda` in "screen capture source running"
  (the scaler options fixed), or "gave no first frame within 1.5 s" in the fallback warning on a still desktop (then
  `source=wgc` for that session); one "screen encoder candidate unavailable" line per skipped encoder with FFmpeg's
  reason; "screen encoder opened" (`mft=`, `low_delay=True`, `open_ms=`, `sps=`, `unused=` empty); "screen transport"
  (candidate types, `ip=v4|v6`, `packet_max=1200`); "screen first keyframe" with `ms` under 1000 on a still screen
  (`form=`, `nals=`, `frames_in=`); a "screen media" line every 5 s with `rr=True` and `rtp_packets` growing; a click
  on a still screen visible within a second; and, if H.264 stalls, "offers VP8 first" then a VP8 picture after the
  phone's restart. After Stop the bundle keeps `status.screen.last_session_media`.

## Single instance per Windows user session (spec §10)

`dome-agent run` takes an instance lock before anything else: the named mutex
`Local\DoMe.Agent.<session id>` on Windows (`platform/windows/instance.py`, **not yet verified**), an
`flock`ed `<state dir>/agent.lock` elsewhere (unit-tested), plus an advisory `agent.pid` file. A second
launch asks the running instance over the authenticated control channel to show its tray/status window
(op `show`), prints "already running (pid N)" and exits 0 (integration-tested). If the lock is held but
the control channel does not answer, it prints "another DoMe instance is running but not responding
(pid N)" with the `dome-agent repair` hint and exits 1 (unit-tested). `dome-agent status` reports a
stale control endpoint, a stale pid file, a permission problem on the state directory and an
other-session conflict distinctly (`single_instance.inspect`). **One agent per Windows account's state
directory**: the mutex is per logon session, but `%LOCALAPPDATA%\DoMe` (identity, PC credential,
grants, `state.sqlite3`) is per account, so a second agent of the same account in another session (RDS,
a second or reconnected session) would reuse the same PC identity and supersede the first at the relay.
`dome-agent run` therefore refuses with "DoMe already runs for this Windows account in session N" when
`agent.pid` names a live agent in another session (unit-tested with injected session ids; the real
`ProcessIdToSessionId` path is **not yet verified**). Nothing ever terminates another process.

## Repair

`dome-agent repair [--host-path …]` re-writes and re-registers the native-messaging manifest, removes a
stale control/bridge endpoint and a stale pid file, checks that the state directory is writable, and
reports — never touches — `identity.json`, the DPAPI/0600 secrets, the grants and the approved apps
(integration-tested with a running agent: it is left running, pairing and grants are unchanged). A
new controller approval still needs the local approval flow.

## Windows install (summary)

The installer does steps 1, 3 (when an extension id exists) and the Start at login opt-in; its user
guide is `docs/WINDOWS_INSTALL.md` "The installer".

1. Build on Windows: `uv sync --locked --extra build`, then
   `uv run --no-sync python packaging\installer\build_installer.py` (PyInstaller on
   `packaging\dome-agent.spec` → `dist\DoMe\` with both programs, then Inno Setup 6 →
   `dist\installer\DoMe-Setup-<version>.exe`, `SHA256SUMS`, `BUILD-INFO.txt`). `--pyinstaller-only`
   stops after the folder (also on Linux); `--release` builds a Setup without the development options
   `/ApiUrl=` and `/ExtensionId=` (it refuses while `PRODUCTION_EXTENSION_IDS` is empty; DECISIONS 57). CI: `.github/workflows/windows-installer.yml` builds it and runs
   `packaging/installer/smoke_test.py full` (destructive: it refuses outside GitHub Actions without
   `--allow-this-pc`); `smoke_test.py bridge --app-dir dist/DoMe …` checks a built folder anywhere.
2. First run: `DoMe.exe link` opens the system browser for the account sign-in (device-code flow;
   the agent never sees the password). The customer names the PC and enables remote control. With no
   `--api-url` and no `DOME_AGENT_API_URL`, an installed `DoMe.exe` uses the address Setup wrote to
   `dome-server.json` next to it.
3. `DoMe.exe install-native-host` writes `%LOCALAPPDATA%\DoMe\com.dome.agent.json` and registers it
   under `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent` and the Edge equivalent
   (per user, no elevation). `allowed_origins` = the production extension id baked into
   `dome_agent/bridge/manifest.py::PRODUCTION_EXTENSION_IDS` (empty in source until the store listing
   fixes the key) plus `DOME_AGENT_DEV_EXTENSION_ID` when set; with neither, the origins of the manifest
   already registered here are kept (an upgrade or `repair` keeps a development extension).
4. `DoMe.exe` (= `run`) shows the tray icon (second launch: the running instance's window comes up).
   Menu: status · Disable/Enable remote control · Stop manual input · Paired phones ▸ (Allow touchpad /
   Allow keyboard per phone) · Pair a phone… · Add an app… · Approved apps… (list, re-approve, remove,
   *Full screen with F11* per app) · Start at login (HKCU Run key) · Reconnect · Diagnostics… · Quit.
5. Pair: tray → Pair a phone… shows a QR + 20-symbol code (5 min). The phone scans it; the PC shows the
   phone's name, the 6-digit verification code and the requested permissions; touchpad and keyboard are
   separate, unticked checkboxes with the scope explanation; the customer approves **on the PC**.
6. Approve apps for remote launch/focus/close: tray → Add an app… (or
   `DoMe.exe approve-app chrome "C:\Program Files\Google\Chrome\Application\chrome.exe"`).
   Only absolute, existing `.exe` paths outside temp folders are accepted; the SHA-256 is pinned and a
   changed binary must be re-approved. **Full screen with F11**: the phone's Full screen presses F11, which
   DoMe does on its own only in apps known to use F11 that way (browsers, File Explorer, VS Code, Windows
   Terminal, Notepad++, Sublime Text). For any other app (for example ZStream) the PC owner turns it on per
   app: tray → Approved apps… → select the app → tick *Full screen with F11* (or
   `DoMe.exe app-option zstream --fullscreen-f11 on`, or `approve-app … --fullscreen-f11`). Until then the
   phone is told "Full screen is not turned on for ZStream. On the PC: DoMe tray > Approved apps > ZStream >
   Full screen with F11. Or use Maximise." Leave it off where F11 means something else (Excel adds a chart
   sheet). Re-approving an app keeps its choice.
7. Secrets (`pc_key.bin`, `pc_credential.bin`) are DPAPI-protected (current user) under
   `%LOCALAPPDATA%\DoMe\secrets`. Logs: `%LOCALAPPDATA%\DoMe\logs\agent.log` (redacted JSON lines).
   Uninstall: Settings → Apps → DoMe (the uninstaller quits DoMe, removes the program, the host keys and
   manifest and start at login, and keeps `%LOCALAPPDATA%\DoMe` unless told otherwise). From source:
   `DoMe.exe quit`, `DoMe.exe uninstall-native-host`, `DoMe.exe start-at-login off`, delete
   `%LOCALAPPDATA%\DoMe`.

## Windows verification checklist (not runnable here — manual)

Evidence tag for everything below: **not yet verified on a Windows device**. Unit/integration
tests on Linux cover the logic around each adapter with `fake_platform`.

| Area | Manual step |
| --- | --- |
| DPAPI | After `link`, `secrets\pc_credential.bin` is not readable as text; copying the state dir to another user/machine makes `status` report "not linked". |
| Volume (pycaw) | `windows.set_volume 35` from the phone → Windows mixer shows 35; result `{"value":35,"muted":false}`. |
| Media (winsdk GSMTC) | Play Spotify; `media.get_sessions` lists it with `controls`; `media.set_paused` pauses; `media.next` on a session without `next` → `ACTION_UNAVAILABLE`. |
| Lock | `windows.lock` locks the session; while locked `app.launch` → `PC_SESSION_LOCKED`, media allowed only with "allow media while locked". |
| Apps (pywin32) | approve Notepad; launch/focus/minimize; `app.close` with unsaved text → `CLOSE_REFUSED` (dialog on PC), never force-killed; focus from a non-foreground process → `FOCUS_DENIED` where Windows refuses. |
| Window actions | Approve Edge, Notepad and Excel. Edge: Full screen on, then off (F11 full screen and back); Full screen, Minimise from the phone, Full screen again → Edge comes back in full screen (not toggled out of it); then Restore → normal size, not full screen. Notepad and Excel: Full screen → `FULLSCREEN_UNSUPPORTED` with "Full screen is not turned on for Notepad. On the PC: DoMe tray > Approved apps > Notepad > Full screen with F11. Or use Maximise.", nothing pressed (Excel gains no chart sheet). After a YouTube search in Edge, the phone's Apps page lists the Edge window as "Microsoft Edge" and the keyboard panel shows "Edge" without the page title. |
| Bring to front | Notepad open behind Edge, DoMe idle in the tray: Bring to front from the phone → Notepad comes to the front and takes the keyboard, with no taskbar button flashing instead. Leave the PC untouched for 1 minute (no mouse or keyboard input) and repeat: still in front, no flash. |
| Maximise / Restore / Minimise | On Notepad: Maximise → maximised; Restore → normal size; Minimise → minimised; Restore → normal size again. Maximise, then Minimise (the window was maximised when minimised), then Restore → normal size, not maximised. Each result's `state` matches what the screen shows. |
| F11 full screen and display scale | Edge Full screen on and off at 100 % and at 125 % display scale (Settings ▸ Display ▸ Scale), and with "Automatically hide the taskbar" on: the result says `fullscreen: true` only while Edge really covers the whole screen; a maximised Notepad under the auto-hidden taskbar reports `maximized`, `fullscreen: false`. |
| Administrator app | Approve Task Manager (`C:\Windows\System32\Taskmgr.exe`), open it, then Maximise / Restore / Full screen from the phone → `APP_ELEVATED` ("That app runs as administrator…"); the window does not change and nothing is pressed. |
| Other virtual desktop | Open a second Notepad window on another virtual desktop (Win+Tab ▸ New desktop), go back to the first desktop: the Apps page lists only the window on the current desktop, and Bring to front / Maximise act on it, never on the hidden one. |
| Ctrl held | While Ctrl is held down (on the PC keyboard, or a Ctrl the phone's keyboard left held after a failed shortcut, shown in `input_ack.held_keys`), Full screen from the phone → `ACTION_UNAVAILABLE` ("A key … is held down" / "Release the keys held from the phone first."), and F11 is not pressed: Edge stays as it was. Release Ctrl and Full screen works. |
| Old browser extension | Keep an extension build from before protocol 1.2 loaded in Edge with this agent: Search YouTube from the phone → `EXTENSION_UPDATE_REQUIRED` ("Update the DoMe browser extension on the PC…"), nothing is opened; play/pause still work. Reload the current extension and the search works. |
| Full screen with F11 (per app) | Approve ZStream (`C:\zstream\zstream-app.exe`) with Add an app…: the dialog shows *Full screen with F11* unticked (it is pre-ticked, and cannot be unticked, only for Edge, Chrome and the other apps on the built-in list). Type "full screen zstream" → refused with "Full screen is not turned on for ZStream. On the PC: DoMe tray > Approved apps > ZStream > Full screen with F11. Or use Maximise.", nothing pressed. Tick the box in Approved apps… (the "Full screen" column shows F11) → "full screen zstream" makes ZStream full screen with one F11; "exit full screen zstream" leaves it. Untick → refused again. `DoMe.exe app-option zstream --fullscreen-f11 on` does the same while DoMe runs or not. |
| Power | `power.sleep` with countdown 10 → tray/phone show the countdown; `power.cancel` cancels while the countdown runs; a cancel that arrives while the OS call is being issued is refused (`canceled: false`, `ACTION_UNAVAILABLE`) and the power command reports what Windows did; after an accepted restart/shutdown `power.cancel` calls `AbortSystemShutdownW` and reports `canceled: true` only when Windows aborted (with `dwTimeout=0` expect `false`; see KNOWN_ISSUES.md). restart/shutdown use `InitiateSystemShutdownExW(bForceAppsClosed=FALSE)`. Test on a disposable machine. |
| Native host | `install-native-host`; Chrome `chrome://extensions` → DoMe extension connects; `youtube.next` works while another app has focus; two Chrome profiles show two `browser_instances`. |
| IPC identity | A process running as another local user cannot connect to `\\.\pipe\DoMe.Agent.*` (DACL) and a same-user process from another session is closed silently with a `bridge_ipc_identity_mismatch` security event. |
| Start at login | Tray toggle writes `HKCU\...\Run\DoMe`; agent starts after sign-in. |
| Tray | Icon colour: green connected, amber reconnecting, red disabled/superseded, grey offline; Disable remote control makes the phone fail with `PC_REMOTE_DISABLED`. |
| Relay URL error | Set `DOME_AGENT_RELAY_URL=not-a-url` and start the agent: tray notification "DoMe cannot connect", `dome-agent status` prints `CONFIGURATION ERROR: The relay URL is invalid...`, `secrets\pc_credential.bin` is unchanged and no re-link is requested; unset the variable and the agent connects again without `link`. |
| Offline revocation | Disconnect the network, `dome-agent revoke <controller_id>` (agent running or not), reconnect: the relay receives exactly one `revoke_controller` for that controller after the next `grants_snapshot`, `status` shows `pending_revocations: []`, and the account's device list no longer shows the phone as active. |
| Packaging | `windows-installer.yml` is green (it builds the folder and smoke-tests install, the tray process, upgrades and uninstalls on a Windows Server runner with UAC off). On a Windows 10/11 PC as a standard user: double-click `DoMe-Setup` (keyboard only works), no elevation prompt; right-click → Run as administrator installs nothing and says why; Start menu → DoMe shows the tray icon and its menu, and the menu's **Quit DoMe** ends it; `DoMe.exe status` works from a console with redirected output; `dome-native-host.exe` is console-less; Settings → Apps → DoMe uninstalls and asks about the settings. |
| SendInput on a real desktop | Grant pointer+keyboard to a phone; from the touchpad: one-finger motion moves the cursor relative to its position, tap = left click, double tap = double click (Windows double-click time), two-finger tap = right click, two-finger move scrolls (dy>0 = content up), Drag holds the left button until End Drag; `input_ack.held_buttons` shows `["left"]` during the drag. Type `héllo 😀`, Enter, Backspace, arrows, Ctrl+A/C/V/Z into Notepad and the Chrome address bar (Ctrl+L): surrogate pairs arrive intact, CTRL is never left down (check with a physical modifier indicator / `GetAsyncKeyState`). |
| Multi-monitor / mixed DPI | Two monitors, one at 150 % and one positioned left/above the primary: the cursor crosses every edge following the real topology; motion is never clamped to one display; "Enhance pointer precision" acceleration applies like a physical mouse (document the observed ratio). |
| UAC / lock refusal | Open a UAC prompt or lock Windows while a session is live: the session ends `secure_desktop` / `session_locked` within ~1 s and the held drag is released. Click Task Manager / an elevated terminal from the phone: the session stays live, `pc_state.input_restricted` is true, typing is refused `INPUT_RESTRICTED` (no ack as accepted), and clicking a normal window clears it; `foreground_app.elevated` is true where `OpenProcess` succeeds, absent otherwise. |
| Clock skew | Set the iPhone clock 3 s behind / ahead of the PC (Settings ▸ General ▸ Date & Time, manual): the touchpad keeps working; pull the network for 3 s mid-motion: the motion is not played back afterwards. |
| Tray grants | Paired phones ▸ phone ▸ Allow touchpad: the explanation notification appears, the relay receives `grant_update`, the phone's Devices page shows `pointer`; untick → the live session ends `grant_removed` once both are off; a drag ends when only pointer is removed. |
| Second launch | Start `DoMe.exe` twice: the second prints "already running (pid N)" and the first instance's window comes up; signed in to a second session of the SAME account (RDS), `DoMe.exe run` refuses with "already runs for this Windows account in session N" and `dome-agent status` reports the conflict; another Windows account runs its own agent normally. |
| Repair | Delete the HKCU native-messaging key, run `DoMe.exe repair`: the key is re-created, identity/credential/grants/approved apps unchanged, the running agent untouched; with the agent hung (suspended in Process Explorer) `DoMe.exe run` prints "running but not responding (pid N)" and `repair` refuses to kill it. |
| Crash recovery | Hold a drag from the phone, kill `DoMe.exe` from Task Manager, start it again: the left button is released at start-up (`input_holds.json` gone) and the phone receives `input_session{ended, agent_restart}`. |
| Power confirmation copy | Sleep/Restart/Shutdown from the phone: the confirmation sheet shows the agent's detail text stating that remote access can be interrupted or end and that there is no remote wake in V1. |

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
                   approve-app|remove-app|revoke|grant|stop-input|install-native-host|uninstall-native-host|
                   repair|reconnect|diagnostics
  agent.py         orchestrator: frame dispatch, snapshot application, mismatch handling, local controls,
                   input_batch verification, grant_update, every input-session end trigger
  input_session.py manual-input session manager: one session per PC, lease watchdog, seq/age/capability
                   checks, ordered dispatch with motion coalescing, backpressure, holds + crash recovery, acks
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
                   input.py = SendInput, instance.py = mutex) · unsupported.py (other OSes)
  bridge/          host.py (native-messaging host), ipc.py (named pipe / unix socket), server.py (agent side)
  control.py       local CLI ⇄ agent channel (same identity rule as the bridge IPC)
  tray.py ui.py    pystray tray + tkinter pairing windows; NullUI for headless runs
  diagnostics.py   redacted bundle
  testing/         fake_platform.py, fake_relay.py (fake API + relay), fake_extension.py — TEST DOUBLES ONLY
packaging/         PyInstaller specs for DoMe.exe (tray agent) and dome-native-host.exe
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

Last full run on Linux (2026-10-09): **244 passed, 1 skipped** (the state-directory permission probe
cannot fail as root), mypy strict clean, ruff clean. New with protocol 1.1: `tests/test_input_session.py`
(30 tests: lifecycle, ordering/coalescing, replay/stale/forged batches, lease and keepalives, holds and
every end trigger, target change, ack rate, backpressure, crash recovery, grant_update delivery, content
hygiene), `tests/test_windows_input_layout.py` (8), `tests/test_single_instance.py` (7), pairing/CLI
capability prompts (2), power confirmation copy (1), registry handler coverage for the two input actions.
Expectations changed on purpose: `test_bridge.py::test_incompatible_extension_is_refused` now expects
`supported == ["1.0", "1.1"]` (the agent announces every MINOR it speaks; see CONTRACT_ISSUES.md #15);
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

1. Build on Windows: `uv sync --extra dev --extra build`, then
   `.venv\Scripts\pyinstaller packaging\dome-agent.spec` and
   `.venv\Scripts\pyinstaller packaging\dome-native-host.spec`. Place `dome-native-host.exe` next to
   `DoMe.exe`. (PyInstaller is not run here; see the verification checklist.)
2. First run: `DoMe.exe link` opens the system browser for the account sign-in (device-code flow;
   the agent never sees the password). The customer names the PC and enables remote control.
3. `DoMe.exe install-native-host` writes `%LOCALAPPDATA%\DoMe\com.dome.agent.json` and registers it
   under `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent` and the Edge equivalent
   (per user, no elevation). `allowed_origins` = the production extension id baked into
   `dome_agent/bridge/manifest.py::PRODUCTION_EXTENSION_IDS` (empty in source until the store listing
   fixes the key) plus `DOME_AGENT_DEV_EXTENSION_ID` when set.
4. `DoMe.exe` (= `run`) shows the tray icon (second launch: the running instance's window comes up).
   Menu: status · Disable/Enable remote control · Stop manual input · Paired phones ▸ (Allow touchpad /
   Allow keyboard per phone) · Pair a phone… · Approved apps… · Start at login (HKCU Run key) ·
   Reconnect · Diagnostics… · Quit.
5. Pair: tray → Pair a phone… shows a QR + 20-symbol code (5 min). The phone scans it; the PC shows the
   phone's name, the 6-digit verification code and the requested permissions; touchpad and keyboard are
   separate, unticked checkboxes with the scope explanation; the customer approves **on the PC**.
6. Approve apps for remote launch/focus/close: `DoMe.exe approve-app chrome "C:\Program Files\Google\Chrome\Application\chrome.exe"`.
   Only absolute, existing `.exe` paths outside temp folders are accepted; the SHA-256 is pinned and a
   changed binary must be re-approved.
7. Secrets (`pc_key.bin`, `pc_credential.bin`) are DPAPI-protected (current user) under
   `%LOCALAPPDATA%\DoMe\secrets`. Logs: `%LOCALAPPDATA%\DoMe\logs\agent.log` (redacted JSON lines).
   Uninstall: `DoMe.exe uninstall-native-host`, untick Start at login, delete `%LOCALAPPDATA%\DoMe`.

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
| Power | `power.sleep` with countdown 10 → tray/phone show the countdown; `power.cancel` cancels while the countdown runs; a cancel that arrives while the OS call is being issued is refused (`canceled: false`, `ACTION_UNAVAILABLE`) and the power command reports what Windows did; after an accepted restart/shutdown `power.cancel` calls `AbortSystemShutdownW` and reports `canceled: true` only when Windows aborted (with `dwTimeout=0` expect `false`; see KNOWN_ISSUES.md). restart/shutdown use `InitiateSystemShutdownExW(bForceAppsClosed=FALSE)`. Test on a disposable machine. |
| Native host | `install-native-host`; Chrome `chrome://extensions` → DoMe extension connects; `youtube.next` works while another app has focus; two Chrome profiles show two `browser_instances`. |
| IPC identity | A process running as another local user cannot connect to `\\.\pipe\DoMe.Agent.*` (DACL) and a same-user process from another session is closed silently with a `bridge_ipc_identity_mismatch` security event. |
| Start at login | Tray toggle writes `HKCU\...\Run\DoMe`; agent starts after sign-in. |
| Tray | Icon colour: green connected, amber reconnecting, red disabled/superseded, grey offline; Disable remote control makes the phone fail with `PC_REMOTE_DISABLED`. |
| Relay URL error | Set `DOME_AGENT_RELAY_URL=not-a-url` and start the agent: tray notification "DoMe cannot connect", `dome-agent status` prints `CONFIGURATION ERROR: The relay URL is invalid...`, `secrets\pc_credential.bin` is unchanged and no re-link is requested; unset the variable and the agent connects again without `link`. |
| Offline revocation | Disconnect the network, `dome-agent revoke <controller_id>` (agent running or not), reconnect: the relay receives exactly one `revoke_controller` for that controller after the next `grants_snapshot`, `status` shows `pending_revocations: []`, and the account's device list no longer shows the phone as active. |
| Packaging | Both PyInstaller specs build; `DoMe.exe status` works from a console; `dome-native-host.exe` is console-less. |
| SendInput on a real desktop | Grant pointer+keyboard to a phone; from the touchpad: one-finger motion moves the cursor relative to its position, tap = left click, double tap = double click (Windows double-click time), two-finger tap = right click, two-finger move scrolls (dy>0 = content up), Drag holds the left button until End Drag; `input_ack.held_buttons` shows `["left"]` during the drag. Type `héllo 😀`, Enter, Backspace, arrows, Ctrl+A/C/V/Z into Notepad and the Chrome address bar (Ctrl+L): surrogate pairs arrive intact, CTRL is never left down (check with a physical modifier indicator / `GetAsyncKeyState`). |
| Multi-monitor / mixed DPI | Two monitors, one at 150 % and one positioned left/above the primary: the cursor crosses every edge following the real topology; motion is never clamped to one display; "Enhance pointer precision" acceleration applies like a physical mouse (document the observed ratio). |
| UAC / lock refusal | Open a UAC prompt or lock Windows while a session is live: the session ends `secure_desktop` / `session_locked` within ~1 s and the held drag is released. Click Task Manager / an elevated terminal from the phone: the session stays live, `pc_state.input_restricted` is true, typing is refused `INPUT_RESTRICTED` (no ack as accepted), and clicking a normal window clears it; `foreground_app.elevated` is true where `OpenProcess` succeeds, absent otherwise. |
| Clock skew | Set the iPhone clock 3 s behind / ahead of the PC (Settings ▸ General ▸ Date & Time, manual): the touchpad keeps working; pull the network for 3 s mid-motion: the motion is not played back afterwards. |
| Tray grants | Paired phones ▸ phone ▸ Allow touchpad: the explanation notification appears, the relay receives `grant_update`, the phone's Devices page shows `pointer`; untick → the live session ends `grant_removed` once both are off; a drag ends when only pointer is removed. |
| Second launch | Start `DoMe.exe` twice: the second prints "already running (pid N)" and the first instance's window comes up; signed in to a second session of the SAME account (RDS), `DoMe.exe run` refuses with "already runs for this Windows account in session N" and `dome-agent status` reports the conflict; another Windows account runs its own agent normally. |
| Repair | Delete the HKCU native-messaging key, run `DoMe.exe repair`: the key is re-created, identity/credential/grants/approved apps unchanged, the running agent untouched; with the agent hung (suspended in Process Explorer) `DoMe.exe run` prints "running but not responding (pid N)" and `repair` refuses to kill it. |
| Crash recovery | Hold a drag from the phone, kill `DoMe.exe` from Task Manager, start it again: the left button is released at start-up (`input_holds.json` gone) and the phone receives `input_session{ended, agent_restart}`. |
| Power confirmation copy | Sleep/Restart/Shutdown from the phone: the confirmation sheet shows the agent's detail text stating that remote access can be interrupted or end and that there is no remote wake in V1. |

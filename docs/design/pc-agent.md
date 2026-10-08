# pc-agent design

Package `dome_agent` (Python 3.12, asyncio, Pydantic v2, `websockets`, `httpx`, `structlog`,
SQLite via stdlib `sqlite3` in a thread executor; Windows adapters use `pywin32`, `pycaw`,
`comtypes`, `winsdk`, `psutil`; tray via `pystray` + `Pillow`; dialogs via `tkinter`). Runs in the
logged-in user's interactive session with no elevation. Depends on `dome-protocol`
(`../shared/python`) for every verification step.

## Non-Windows behaviour

The agent starts on Linux/macOS for development and CI. Every Windows-only adapter is replaced by
an `UnsupportedPlatform` adapter that fails with `PLATFORM_UNSUPPORTED`. The YouTube path works on
every platform because it goes through the browser bridge. For integration tests only, a
visibly isolated fake adapter set lives in `dome_agent/testing/fake_platform.py` and is selected
solely by `DOME_AGENT_PLATFORM=fake`; the agent logs a loud warning and reports
`platform: "development"` in its state frames when it is active. The installer never sets it.

## Modules

```
dome_agent/
  cli.py          dome-agent [run|link|pair|status|enable|disable|approve-app|install-native-host|diagnostics]
  settings.py     DOME_AGENT_* env + state dir (%LOCALAPPDATA%\DoMe or ~/.local/state/dome)
  identity.py     PC ES256 key + pc_id/account_id + pc_credential; secrets.py wraps DPAPI
  link.py         device-link flow: start → open browser → poll → store credential
  store.py        SQLite schema + bounded journal + grants + challenges + approved apps + settings
  authz.py        local authorization decisions (see below)
  queue.py        per-PC serialized executor with coalescing, timeouts, lifecycle emission
  confirmations.py challenge issue / atomic consume
  relay_client.py WS client: hello, snapshot-before-commands, reconnect with backoff+jitter, pings
  pairing.py      request code, show QR/code, approve controller locally
  state.py        PC state aggregation → `state` frames (debounced, on change + every 30 s)
  actions/        registry of handlers: youtube.py media.py volume.py windows.py apps.py power.py system.py
  platform/       protocol.py (interfaces), windows/*.py (real), unsupported.py
  bridge/         host.py (native messaging host entry), ipc.py (named pipe / unix socket), server.py (agent side)
  tray.py         pystray menu; headless when DOME_AGENT_HEADLESS=1
  diagnostics.py  redacted bundle (last 200 log lines, state, versions; no tokens/titles)
  testing/        fake_platform.py, fake_relay.py (test doubles, never imported by production paths)
```

## Local state (SQLite, `state.sqlite3`, WAL)

- `settings(key pk, value)` — `remote_enabled` (default **false** until the user enables it during
  linking), `media_while_locked` (default false), `start_at_login`, `pc_name`.
- `grants(controller_id pk, kid unique, public_jwk, capabilities, display_name, granted_at, revoked_at, snapshot_id)`
- `journal(command_id pk, digest, action, state, result_json, error_code, created_at, finished_at)`
  bounded: prune to the newest 5,000 rows after each insert; `outcome_unknown` written for any
  row found in `executing` at start-up.
- `challenges(challenge_id pk, command_id, digest, challenge_text, expires_at, consumed_at)`
- `approved_apps(app_id pk, display_name, exe_path, exe_sha256, added_at)` — paths validated
  (absolute, exists, `.exe`, not under %TEMP%), never editable remotely.
- `pending_power(command_id, action, fires_at)` — at most one row.

Private key PEM and `pc_credential` are stored in files protected with DPAPI
(`CryptProtectData`, current user scope) on Windows; elsewhere plain files with mode 0600 and a
logged warning.

## Authorization (`authz.py`), applied to every inbound command in this order

1. Frame validated (`relay_to_agent`), envelope verified and parsed via
   `verify_and_parse_command` with a resolver that returns a JWK **only** for a grant that is not
   revoked (→ `UNKNOWN_KEY` / `CONTROLLER_REVOKED` when the kid is known but revoked).
2. `payload.account_id == identity.account_id` else `ACCOUNT_MISMATCH`; `payload.target_pc_id ==
   identity.pc_id` else `TARGET_PC_MISMATCH`; `payload.controller_id == grant.controller_id` else `GRANT_MISSING`.
3. `remote_enabled` else `PC_REMOTE_DISABLED` (checked again right before execution).
4. `spec.capability in grant.capabilities` else `GRANT_MISSING`.
5. Journal: same `command_id` + same digest → re-send the stored `result` (or current `ack` if
   still running); same id + different digest → `COMMAND_ID_REUSED`.
6. Availability conditions: `windows`, `extension_connected`, `session_unlocked`,
   `session_media_allowed` → `ACTION_UNAVAILABLE` / `EXTENSION_DISCONNECTED` / `PC_SESSION_LOCKED`.
7. Target resolution (YouTube: browser instance + tab must currently exist → `TARGET_GONE`;
   `expected_video_id` mismatch → `TARGET_CHANGED` before acting; apps: `app_id` approved → else `APP_NOT_APPROVED`).
8. Disruptive actions: issue a challenge (below) and answer `confirmation_required`; the command
   is journaled as `awaiting_confirmation` and is **not** queued for execution.
9. Entitlement gates (Pro-only actions — none in v1.0 registry; routines later): verify the
   latest assertion, with the 72 h grace rule.
Then `ack{accepted}` is sent and the command is queued.

## Execution (`queue.py`)

One asyncio worker per PC (one per process). Queue depth 16. Coalescing: when a command with a
`coalesce` key is enqueued and an earlier **queued, not yet executing** command has the same key,
the earlier one finishes as `canceled` with warning "superseded by a newer value". Each command:
`ack{executing}` → handler with `asyncio.wait_for(timeout_ms)` → `result`. Timeout → `failed`
with the handler's best-known error, or `outcome_unknown` for non-idempotent actions whose OS
call was already issued. Before the OS call the journal row is set to `executing` (durable) so a
crash yields `outcome_unknown` at restart. Non-idempotent actions are never retried by the agent.

## Confirmation transaction (`confirmations.py`)

Challenge = `dumps_compact({challenge_id, command_id, controller_id, pc_id, action, params,
target, target_state_digest, expires_at, display})`; the exact text is stored and sent inside
`confirmation_required`. `target_state_digest` = SHA-256 of the current observable target state
(window title + handle for `app.close`; pending-power state for `power.*`). On a signed
`confirmation`: verify with the same controller key; `challenge_id` exists, unconsumed,
unexpired, `command_id` and `controller_id` match, `challenge_digest == challenge_digest(stored text)`;
re-compute `target_state_digest` and compare (→ `TARGET_CHANGED`); then in ONE SQLite transaction
mark consumed and move the command to `accepted`; a `decline` → `canceled` with
`CONFIRMATION_DECLINED`. Expired → `CONFIRMATION_EXPIRED`, command `expired`.

## Relay client

`wss://…/ws/agent` with `Authorization: Bearer <access token>` (refreshed via
`POST /v1/agent/token` when a 401 occurs). Sequence: `hello` (component `agent`, versions,
registry) → `hello_ack` → wait for `grants_snapshot`, apply it atomically (controllers absent from
the snapshot are marked revoked; in-flight commands from revoked controllers are canceled) →
only then process commands. Reconnect: exponential backoff 1 s → 60 s with full jitter; ping
every 25 s; a stale queue is never replayed — all queued commands are failed with `PC_OFFLINE`
on disconnect and the controller is told when the socket returns? No: they are failed
immediately in the journal; the relay independently reports to the controller.
`state` frames are sent after connect, on change (debounced 500 ms), and every 30 s.

## Actions

| Namespace | Adapter | Notes |
| --- | --- | --- |
| `youtube.*` | `bridge/server.py` → extension op | Maps action → `bridge_request.op`; response → result/error; `observe_video_transition` is performed by the extension and reported in the response |
| `media.*` | `platform/windows/media.py` (`winsdk` GlobalSystemMediaTransportControlsSessionManager) | Session id = `SourceAppUserModelId` + index; only advertised controls are offered |
| `windows.get/set_volume`, `set_muted` | `platform/windows/volume.py` (`pycaw` `IAudioEndpointVolume`) | read-back verification |
| `windows.lock` | `LockWorkStation` | `os_accepted` |
| `app.*` | `platform/windows/apps.py` (`subprocess.Popen([exe_path])` **no shell, no args**, `pywin32` `EnumWindows`/`SetForegroundWindow`/`ShowWindow`/`PostMessage WM_CLOSE`) | `FOCUS_DENIED` when `GetForegroundWindow` is not ours after the call; `CLOSE_REFUSED` when the window survives the timeout; never `TerminateProcess` |
| `power.*` | `platform/windows/power.py` (`SetSuspendState`, `InitiateSystemShutdownExW` with `bForceAppsClosed=False` / `ExitWindowsEx` without `EWX_FORCE`) | countdown task; `power.cancel` → `AbortSystemShutdown`; `pending_power_action` in state |
| `system.*` | `state.py` | |

The agent **never** accepts a path, argument, script or shell text over the wire.

## Browser bridge

`dome-native-host` (separate console-less entry point built by PyInstaller) speaks Chrome Native
Messaging on stdio (4-byte little-endian length + UTF-8 JSON, max 64 KiB) and connects to the agent
over a per-user IPC endpoint: Windows named pipe `\\.\pipe\DoMe.Agent.<sha256(user SID + session id)[:16]>`
created with a DACL granting access only to the current user; Unix domain socket
`<state dir>/bridge.sock` mode 0600 elsewhere. The host validates every frame against the
bridge schema in both directions and forwards verbatim. Manifest registration
(`install-native-host`): `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent` and
`HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.dome.agent` → manifest JSON with
`allowed_origins` = production extension id and, only when `DOME_AGENT_DEV_EXTENSION_ID` is set, a
development id. The agent keeps `browser_instances[browser_instance_id] = {browser, profile_label,
tabs}` from `bridge_hello`/`bridge_event` and drops an instance when its host disconnects.

## Pairing on the PC

`pair` → `POST /v1/pairing/start` → show code + QR (tray window via tkinter, or console) with
"expires in 5:00". On `pairing_request`: compute `pairing_verification_code(pairing_id, pc_id,
kid)` locally, show `display_name` + code + requested capabilities, Approve/Decline. Approve
stores the grant (so the agent accepts commands even before the next snapshot) and sends
`pairing_decision{approve, kid, granted_capabilities}`.

## Tray

Menu: status line (Connected / Reconnecting / Offline / Remote control OFF), **Disable remote
control** (toggle; local flag, remote frames cannot change it), Pair a phone, Approved apps…,
Start at login (toggle, HKCU Run key), Diagnostics…, Quit. The icon colour reflects state.

## Tests (pytest; run on Linux)

authz order and every rejection code; journal duplicate/reuse; expiry; coalescing; crash →
`outcome_unknown`; confirmation happy path, digest mismatch, expired, declined, target changed;
grants snapshot revocation cancels in-flight; relay client reconnect/backoff against
`testing/fake_relay.py`; bridge framing (length prefix, oversize, schema rejection); action
handler coverage (every registry action has a handler); approved-app validation rejects
non-absolute, non-exe, temp-dir paths and anything resembling arguments; `PLATFORM_UNSUPPORTED`
on Linux for Windows-only actions; end-to-end with fake platform + fake relay: signed command →
executed → result.

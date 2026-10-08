# pc-agent — the DoMe Windows user agent

`dome_agent` runs in the logged-in user's Windows session, opens **one outbound WebSocket** to the
managed relay and executes locally authorised actions: YouTube through the browser extension
(Native Messaging bridge), Windows media sessions, system volume, approved applications, lock and
explicitly confirmed power actions. It never listens on the internet, never accepts a path, argument
or shell text over the wire, and honours a local **Disable remote control** switch that no remote
frame can undo.

Design: `docs/design/pc-agent.md`. Contract: `shared/protocol/` (frozen; verified through the
`dome-protocol` library for every trust-boundary input). Decisions taken while building:
`DECISIONS.md`. Contract friction found while building: `CONTRACT_ISSUES.md`.

## Layout

```
dome_agent/
  cli.py           dome-agent run|link|unlink|pair|pair-approve|pair-decline|status|enable|disable|
                   approve-app|remove-app|revoke|install-native-host|uninstall-native-host|reconnect|diagnostics
  agent.py         orchestrator: frame dispatch, snapshot application, mismatch handling, local controls
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
  platform/        protocol.py (interfaces) · windows/* (real APIs) · unsupported.py (other OSes)
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
.venv/bin/pytest -q            # ~2 minutes; fake relay/API/extension/platform, real signatures
.venv/bin/ruff check dome_agent tests
.venv/bin/mypy
```

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
4. `DoMe.exe` (= `run`) shows the tray icon. Menu: status · Disable/Enable remote control · Pair a
   phone… · Approved apps… · Start at login (HKCU Run key) · Reconnect · Diagnostics… · Quit.
5. Pair: tray → Pair a phone… shows a QR + 20-symbol code (5 min). The phone scans it; the PC shows the
   phone's name, the 6-digit verification code and the requested permissions; the customer approves
   **on the PC**.
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
| Power | `power.sleep` with countdown 10 → tray/phone show the countdown; `power.cancel` cancels; restart/shutdown use `InitiateSystemShutdownExW(bForceAppsClosed=FALSE)`. Test on a disposable machine. |
| Native host | `install-native-host`; Chrome `chrome://extensions` → DoMe extension connects; `youtube.next` works while another app has focus; two Chrome profiles show two `browser_instances`. |
| IPC identity | A process running as another local user cannot connect to `\\.\pipe\DoMe.Agent.*` (DACL) and a same-user process from another session is closed silently with a `bridge_ipc_identity_mismatch` security event. |
| Start at login | Tray toggle writes `HKCU\...\Run\DoMe`; agent starts after sign-in. |
| Tray | Icon colour: green connected, amber reconnecting, red disabled/superseded, grey offline; Disable remote control makes the phone fail with `PC_REMOTE_DISABLED`. |
| Packaging | Both PyInstaller specs build; `DoMe.exe status` works from a console; `dome-native-host.exe` is console-less. |

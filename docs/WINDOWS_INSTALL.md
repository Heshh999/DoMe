# DoMe on Windows — install, run, uninstall

Status: **pre-release engineering build. Nothing in this document has been Windows-device-tested.**
Every Windows API path in `pc-agent/dome_agent/platform/windows/` is implemented against the real
Windows APIs and unit-tested on Linux behind an explicit fake platform (`DOME_AGENT_PLATFORM=fake`,
194 tests at the last recorded run), but the agent has never been started on a Windows machine, the
PyInstaller specs have never been built, and there is no installer package yet. This document
describes what the agent does and the install, run and uninstall steps **as the code implements
them today**; the last section lists what is still blocked. Evidence tags follow
`docs/spec/MASTER_PROMPT.md` §17.

Related: `pc-agent/README.md` (developer view and the manual verification checklist),
`docs/IPHONE_SETUP.md` (the phone side), `docs/TROUBLESHOOTING.md`, `docs/ARCHITECTURE.md`.

## 1. What the DoMe agent is and is not

The agent (`dome_agent`, packaged as `DoMe.exe`) runs **in the signed-in user's Windows session** with
that user's ordinary rights. It:

- opens **one outbound WebSocket** to the DoMe service (`wss://…/ws/agent`) and reconnects with
  backoff; it never listens on the internet and never accepts inbound connections from the network;
- shows a **tray icon** (green connected, amber reconnecting, red disabled or superseded, grey offline)
  with the menu *status · Disable/Enable remote control · Pair a phone… · Approved apps… · Start at
  login · Reconnect · Diagnostics… · Quit DoMe*;
- executes only the 30 actions of the shared registry (`shared/protocol/actions.json`): YouTube
  control through the browser extension, Windows media sessions, system volume and mute, approved
  application launch/focus/minimise/close, lock, and explicitly confirmed sleep/restart/shutdown;
- verifies every command's ES256 signature against a key **you approved on this PC during pairing**,
  checks the grant, the replay journal, the target and (for disruptive actions) a signed confirmation
  before doing anything — the service cannot make the PC run something the PC has not authorised;
- keeps a local **Disable remote control** switch that no remote message can undo.

It does **not**: open ports, run shell commands, scripts or arbitrary executables, accept paths or
arguments from the phone, bypass UAC or the lock screen, run as a service in Session 0, update itself,
or send anything to anyone other than the DoMe service (and, locally, the browser extension).

Boundary against the local user: the agent runs as you. Whoever can edit `%LOCALAPPDATA%\DoMe\
state.sqlite3` can edit the approved-app list; DoMe does not defend the PC against the user who is
logged in (`pc-agent/DECISIONS.md` #5).

## 2. Supported Windows versions (assumption)

Design assumption, **not yet verified on any device**:

| Requirement | Why |
| --- | --- |
| Windows 10 or Windows 11, 64-bit, an interactive user session | Lock-state detection uses `WTSQuerySessionInformationW(WTSSessionInfoEx)` (`SessionFlags`, Windows 8 and later) with an `OpenInputDesktop` fallback; media sessions use the Windows Runtime `GlobalSystemMediaTransportControls` API (Windows 10 1809 and later via `winsdk`). |
| Python 3.12 when running from source | `pc-agent/pyproject.toml` pins `>=3.12,<3.13` for pywin32 / pycaw / comtypes / winsdk compatibility. The packaged `DoMe.exe` would bundle its own interpreter. |
| Google Chrome 116 or later, or Chromium-based Microsoft Edge, for YouTube control | The extension manifest sets `minimum_chrome_version: 116` and uses the MV3 service worker that Chrome keeps alive while a native port is open. Firefox and Safari are not supported. |
| No administrator rights | Everything is per user: `%LOCALAPPDATA%`, `HKCU` registry keys, DPAPI current-user scope. |

Windows Home/Pro editions, Windows on ARM, multiple simultaneous user sessions, Remote Desktop
sessions and domain-managed PCs with policy restrictions have not been considered beyond the code's
generic handling (`POWER_DENIED` for policy refusals, `FOCUS_DENIED` when Windows refuses foreground
changes). Treat all of this as the checklist for the first device pass, not as a support statement.

## 3. What gets installed where

| Item | Location | Created by |
| --- | --- | --- |
| State directory | `%LOCALAPPDATA%\DoMe` (override: `DOME_AGENT_STATE_DIR` or `--state-dir`) | first command |
| Local database | `%LOCALAPPDATA%\DoMe\state.sqlite3` (settings, locally approved phones, bounded command journal, confirmation challenges, approved apps, pending power action, pending revocations, local security events) | first command |
| PC identity key and account credential | `%LOCALAPPDATA%\DoMe\secrets\pc_key.bin` (P-256 private key) and `pc_credential.bin` — each **DPAPI-protected (current user)** via `CryptProtectData`; decryptable only by the same Windows user on the same machine | `DoMe.exe link` |
| Logs | `%LOCALAPPDATA%\DoMe\logs\agent.log`, redacted JSON lines, 2 MB × 3 rotations | `run` |
| Diagnostics bundles | `%LOCALAPPDATA%\DoMe\diagnostics\dome-diagnostics-<timestamp>.json` | tray *Diagnostics…* or `DoMe.exe diagnostics` |
| Native-messaging manifest | `%LOCALAPPDATA%\DoMe\com.dome.agent.json` | `install-native-host` |
| Native-host registration | `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent` and `HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.dome.agent` (default value = manifest path) | `install-native-host` |
| Start at login | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DoMe` = `"<path>\DoMe.exe" run` | tray *Start at login* (opt-in; off by default) |
| Local IPC endpoints | Named pipes `\\.\pipe\DoMe.Agent.<hash>` (browser bridge) and `\\.\pipe\DoMe.Control.<hash>` (CLI ⇄ agent), DACL restricted to the current user and checked for the same session | `run` |

No `HKLM` keys, no services, no scheduled tasks, no Program Files writes are made by the agent itself.

## 4. Install — as implemented today

There is **no installer**. The steps below are what exists: build the two executables from source on a
Windows machine, then run the commands the agent provides. (On Linux/macOS, see §8.)

### 4.1 Build `DoMe.exe` and `dome-native-host.exe` (not yet run anywhere)

Prerequisites on the build PC: Python 3.12, [`uv`](https://docs.astral.sh/uv/), the repository.

```bat
cd pc-agent
uv venv --python 3.12
uv sync --extra dev --extra build
.venv\Scripts\pyinstaller packaging\dome-agent.spec
.venv\Scripts\pyinstaller packaging\dome-native-host.spec
```

Expected outputs: `dist\DoMe\DoMe.exe` (one-folder build, `console=False`, tray application; `DoMe.exe`
without arguments means `run`) and `dist\dome-native-host.exe` (single file, `console=False` so Chrome
never flashes a console window). **Copy `dome-native-host.exe` next to `DoMe.exe`**: `install-native-host`
looks for it there (`dome_agent/tray.py::host_executable_path`). The shared protocol contract is
bundled into both as `dome_protocol/_contract`. Evidence: **not yet verified** (the specs exist;
PyInstaller has never been executed in this repository's environment).

Alternative without PyInstaller (developer PC): `uv sync --extra dev` and use
`.venv\Scripts\dome-agent.exe` / `.venv\Scripts\dome-native-host.exe` in place of the two executables.
`install-native-host` finds the native host in the same `Scripts` folder.

### 4.2 Link the PC to your DoMe account

```bat
DoMe.exe link
```

What happens (ADR-0001 D4, device-authorization shape; `dome_agent/link.py`):

1. The agent generates its P-256 identity key (DPAPI-protected) if it has none and calls
   `POST /v1/agent-link/start`.
2. It prints a short **user code** and opens the system browser at `https://<dome>/link?user_code=…`.
   `--no-browser` prints the link instead of opening it. The agent never sees your password; sign-in
   happens in the browser with the account's identity provider.
3. In the browser, signed in to your account, you see what is asking, **name the PC** and keep
   **Enable remote control** ticked, then approve (or deny).
4. The agent polls (`interval` from the server, +5 s on `slow_down`) until approved, denied or expired
   (codes last 10 minutes: `device_link_code_lifetime_seconds`). On approval it receives an opaque
   PC credential exactly once and stores it DPAPI-protected. The console prints
   `Linked as '<name>' (pc_id …). Remote control is enabled locally.`

If your plan's enabled-PC limit is already reached (Free: 1) the PC is linked but **not enabled**;
the agent says so and the phone's Devices page lets you choose which PC stays enabled.

`DoMe.exe unlink` forgets the link and credential locally (local phone approvals are kept for
inspection; pair again after re-linking). Unlinking from the phone (Devices → *Unlink…*) revokes the
credential and every grant on the service side; the agent then discards its credential and asks to be
linked again.

### 4.3 Register the browser bridge (native-messaging host)

```bat
set DOME_AGENT_DEV_EXTENSION_ID=<32-letter extension id from chrome://extensions>
DoMe.exe install-native-host
```

This writes `%LOCALAPPDATA%\DoMe\com.dome.agent.json`:

```json
{ "name": "com.dome.agent", "description": "DoMe browser bridge (YouTube control for the DoMe agent)",
  "path": "C:\\…\\dome-native-host.exe", "type": "stdio",
  "allowed_origins": ["chrome-extension://<id>/"] }
```

and sets the two `HKCU` registry keys listed in §3. `allowed_origins` is the production extension id
baked into the build (`dome_agent/bridge/manifest.py::PRODUCTION_EXTENSION_IDS`, **empty in source**
until a Chrome Web Store listing fixes the key) plus the development id from
`DOME_AGENT_DEV_EXTENSION_ID`. With neither, the command refuses with
`no extension id available …`. The agent never downloads this list.

The extension itself is loaded unpacked for now (`browser-extension/README.md` → "Load unpacked"):
`pnpm build` on any machine, copy `browser-extension/dist` to the PC, `chrome://extensions` →
Developer mode → Load unpacked. Unpacked ids are derived from the folder path, so keep `dist` where
it is or pin a `"key"` in the manifest. Evidence: **not yet verified** in a real browser.

`DoMe.exe uninstall-native-host` deletes the two registry keys (the manifest file is removed with the
state directory).

### 4.4 Run the agent

```bat
DoMe.exe
```

(or `DoMe.exe run`). The tray icon appears; the agent fetches a PC access token with its credential,
connects to the relay URL stored at link time, sends `hello`, waits for `hello_ack` and the
`grants_snapshot`, then starts accepting commands. If it is started before `link`, it waits and polls
`identity.json` every 3 s so you can link from another console. `DoMe.exe status` prints the link,
connection, extension, entitlement and paired-phone summary (`--json` for machines); it works with or
without the agent running.

Environment variables (none are secrets): `DOME_AGENT_API_URL` / `DOME_AGENT_RELAY_URL` override the
URLs stored at link time; `DOME_AGENT_STATE_DIR`; `DOME_AGENT_LOG_LEVEL`; `DOME_AGENT_HEADLESS=1` (no
tray); `DOME_AGENT_DEV_EXTENSION_ID`. `DOME_AGENT_PLATFORM=fake` selects the test double and is never
set by anything that ships.

### 4.5 Pair a phone

Tray → **Pair a phone…** (or `DoMe.exe pair` in a console with the agent running). The PC shows a QR code
and a 20-symbol code (`XXXXX-XXXXX-XXXXX-XXXXX`, valid 5 minutes, single use). The DoMe service only ever
receives a SHA-256 handle of the code. On the phone, follow `docs/IPHONE_SETUP.md`. The PC then shows
the phone's name (untrusted text), the permissions it asked for and a **6-digit verification code**;
compare it with the phone's screen and approve **on the PC**. Only then is the phone's key stored
locally and the grant created on the service.

### 4.6 Approve applications for remote launch/focus/close

```bat
DoMe.exe approve-app chrome "C:\Program Files\Google\Chrome\Application\chrome.exe" --name "Chrome"
DoMe.exe remove-app chrome
```

Rules (`dome_agent/approved_apps.py`, unit-tested): `app_id` is a short lowercase identifier; the path
must be absolute, existing, end in `.exe`, not be a directory, not sit in a temp folder, and contain
nothing argument-like. The SHA-256 of the executable is pinned; after the application updates,
`app.launch` answers `APP_NOT_APPROVED` (security event `approved_app_hash_changed`) until you approve it
again. The phone only ever sends the `app_id`. The tray's *Approved apps…* window lists and removes
approvals.

### 4.7 Start at login (opt-in)

Tray → **Start at login** (checked = on). Writes `HKCU\…\Run\DoMe` with `"<DoMe.exe>" run`; unticking
deletes the value. Nothing is written by default. Evidence: **not yet verified** (registry code only).

### 4.8 Emergency stop

Tray → **Disable remote control** or `DoMe.exe disable` (works even when the agent is not running: it
writes the local flag to `state.sqlite3`). Every command then fails with `PC_REMOTE_DISABLED`, an armed power countdown is cancelled, a
confirmation that arrives afterwards is refused, and no frame from the service can re-enable it.
`DoMe.exe enable` or the tray item turns it back on. `DoMe.exe revoke <controller_id>` revokes one phone
locally and tells the service (immediately, or on the next connect if offline).

## 5. Uninstall — as implemented today

There is no uninstaller; the steps mirror what the install created:

1. Tray → **Quit DoMe** (or close the headless process). Any armed power countdown is cancelled on exit.
2. Optionally first revoke the phones from the phone (Devices → Revoke) or unlink the PC (Devices →
   *Unlink…*) so the service stops listing it. Without this the service keeps the PC row until you
   unlink it from any signed-in device; the credential on this PC is useless once the folder is gone.
3. `DoMe.exe uninstall-native-host` — removes the two `HKCU` native-messaging keys.
4. Tray → untick **Start at login** (or delete `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DoMe`).
5. Delete `%LOCALAPPDATA%\DoMe` — this removes the SQLite store (local phone approvals, journal,
   approved apps), the DPAPI-protected key and credential, logs, diagnostics and the manifest file.
6. Remove the DoMe extension from `chrome://extensions` / `edge://extensions` and delete the
   `DoMe.exe` folder.

Spec §15 asks for installer-driven cleanup of all of the above; that packaging work is still open (§9).

## 6. Day-to-day behaviour worth knowing

- **Lock screen**: while the session is locked, actions that need the desktop (`app.*`, and by default
  all media actions) answer `PC_SESSION_LOCKED`. A local `media_while_locked` setting exists in the
  store and switches media actions to allowed (`PC_SESSION_LOCKED_MEDIA_ONLY` for everything else), but
  **no tray item or CLI command sets it yet** — see `docs/TROUBLESHOOTING.md` §10.
- **Power actions**: sleep/restart/shutdown require a signed confirmation on the phone, then run a
  cancellable countdown in the agent (`power.cancel` from the phone, the tray, or local disable). The OS
  call is `SetSuspendState` for sleep and `InitiateSystemShutdownExW(bForceAppsClosed=FALSE, dwTimeout=0)`
  for restart/shutdown, so Windows itself prompts about unsaved work and nothing is force-closed. Once
  the OS call has been issued a cancel is honestly reported as `canceled: false`
  (`pc-agent/KNOWN_ISSUES.md` #1).
- **Close app** asks the window to close normally and reports `CLOSE_REFUSED` if it stays open
  (unsaved-work dialog); it never kills the process.
- **Clock**: commands carry `issued_at`/`expires_at`; the PC tolerates 5 s of skew. A badly wrong PC
  clock produces `CLOCK_SKEW` or `COMMAND_EXPIRED` results — fix the time settings on the PC.
- **Superseded**: if a second agent instance connects for the same PC the first is closed with code
  4001 and stops reconnecting until tray → **Reconnect**.
- **Re-link required**: when the service revokes the credential (unlink from the phone) or names a
  different PC, the tray reports it and `DoMe.exe status` prints `RE-LINK REQUIRED`; run `DoMe.exe link`.

## 7. Verifying the install (manual, not yet done)

`pc-agent/README.md` → "Windows verification checklist" is the exact list to work through on a real
machine (DPAPI, volume, media sessions, lock, apps, power on a disposable machine, native host with
two Chrome profiles, IPC identity across users and sessions, start at login, tray colours, relay URL
error, offline revocation, packaging). `browser-extension/README.md` → "Manual verification checklist"
covers the browser side. Record results in `docs/ACCEPTANCE.md` with the tag **Windows-device-tested**.

## 8. Development run on Linux (and macOS)

The agent starts on Linux without any Windows library (the Windows adapters are import-gated behind
`sys_platform == 'win32'`). Two modes exist:

| Mode | How | What works |
| --- | --- | --- |
| Real platform, non-Windows | `uv sync --extra dev`, `dome-agent run` | Link, relay connection, pairing, YouTube actions through the bridge (Unix socket `<state dir>/bridge.sock`, 0600, peer uid checked); every Windows-only action answers `PLATFORM_UNSUPPORTED`; secrets are plain files with mode 0600 and a logged warning |
| Fake platform | add `DOME_AGENT_PLATFORM=fake` (and usually `DOME_AGENT_HEADLESS=1`) | All actions against in-memory doubles that record calls and perform nothing (`dome_agent/testing/fake_platform.py`); state frames report `platform: "development"`. This is what the unit and integration suites use. |

Typical loop against a local service (see `cloud-api/README.md` and the root `Makefile`):

```bash
cd pc-agent && uv venv --python 3.12 && uv sync --extra dev
export DOME_AGENT_API_URL=http://127.0.0.1:8000 DOME_AGENT_STATE_DIR=/tmp/dome-dev
export DOME_AGENT_PLATFORM=fake DOME_AGENT_HEADLESS=1
.venv/bin/dome-agent link --no-browser      # approve the printed user code in the PWA (http://localhost:5173/link?…)
.venv/bin/dome-agent run                    # terminal 1
.venv/bin/dome-agent status                 # terminal 2
.venv/bin/dome-agent pair                   # QR + code; or `pair --print-code --no-wait` then `pair-approve <id>`
.venv/bin/pytest -q                         # 194 tests at the last recorded run (fake platform, fake relay, fake extension)
```

`install-native-host` on Linux writes the manifest and reports that registry registration was skipped;
the native host can be exercised with `dome_agent.testing.fake_extension.FakeExtension` or the process
test `tests/test_processes.py::test_native_host_process_forwards_frames` (**unit-tested**).

## 9. What remains blocked

| Item | Status | What unblocks it |
| --- | --- | --- |
| Building `DoMe.exe` / `dome-native-host.exe` | PyInstaller specs written, never run | A Windows build machine; fix whatever the first build reports |
| Installer package (MSI/MSIX/Inno) with uninstall cleanup | Not started; manual steps only (§4–5) | Choose a packaging tool; it must register the native host, offer the start-at-login opt-in, and remove `HKCU` keys, credentials and pairing state on uninstall (spec §15) |
| **Code signing** (Authenticode) | Not possible here: needs the founder's publisher certificate/account | Without it SmartScreen will warn on an unsigned `DoMe.exe`; **never instruct customers to disable antivirus or SmartScreen** — ship signed builds instead |
| Update mechanism with a signed manifest, downgrade rejection, staged rollout | Not implemented (spec §15); the agent has no updater | A maintained update framework plus the signing key; version negotiation on `hello` already exists so mixed versions fail clearly (`PROTOCOL_INCOMPATIBLE`) |
| Production extension id | `PRODUCTION_EXTENSION_IDS` is empty; only `DOME_AGENT_DEV_EXTENSION_ID` works | Chrome Web Store listing (founder account), pin its `"key"` in `browser-extension/public/manifest.json`, bake the id into the release build |
| Public download page | `mobile-app` download links are disabled with "coming soon" | A signed build to link to |
| Windows device evidence | None | The checklist in §7 |
| `media_while_locked` switch | Setting exists, no UI | A tray or CLI toggle (small change) |

None of these affect the security model: an unsigned development build has the same authorisation
path as a signed one; signing protects the download, not the protocol.

# DoMe on Windows — install, run, uninstall

Status: **pre-release engineering build. No formal Windows-device-tested pass is recorded.** Every
Windows API path in `pc-agent/dome_agent/platform/windows/` is implemented against the real Windows
APIs and unit-tested on Linux behind an explicit fake platform (`DOME_AGENT_PLATFORM=fake`); the owner
has run the agent from source on his Windows 11 PC through the test kit (`docs/HANDOFF.md` §4). A
per-user installer, `DoMe-Setup-<version>.exe`, now exists (next section). A GitHub Actions workflow
(`windows-installer.yml`) is set up to build it on a Windows Server runner and smoke-test install,
upgrade and uninstall there; its first run was green on 2026-10-14 (run 38488799434: the Windows unit tests, the
build and all 157 smoke-test checks passed). Installer evidence: **integration-tested on the windows-latest
CI runner (Windows Server, administrator, UAC off); not Windows-device-tested** (no Windows 10/11 PC and
no standard user yet). A signed public release does not exist yet; the pipeline that will produce one
(`.github/workflows/release.yml`, see "How a release is made" below) is written and **not yet run**
(no signing account, no update key yet). This document describes what the agent
does and the install, run and uninstall steps **as the code implements them today**; the last section
lists what is still blocked. Evidence tags follow `docs/spec/MASTER_PROMPT.md` §17.

Related: `pc-agent/README.md` (developer view and the manual verification checklist),
`docs/IPHONE_SETUP.md` (the phone side), `docs/TROUBLESHOOTING.md`, `docs/ARCHITECTURE.md`.

## The installer (DoMe-Setup.exe) — start here

This is the normal way to put DoMe on a Windows PC. It needs no administrator rights and installs DoMe
only for the Windows account that runs it.

**Getting it.** There are two kinds of DoMe-Setup:

**(A) The public release, when it exists** (*not yet verified*: none has been made). It is signed for
Windows, and its download is listed on **domeremote.com/download** and on the release page on GitHub
(`github.com/Heshh999/DoMe/releases`). Both show the file's SHA-256 and the publisher name Windows will
show. To check the file, run `Get-FileHash .\DoMe-Setup-<version>.exe -Algorithm SHA256` in PowerShell and
compare the result with the SHA-256 on the page. Right-click the file → **Properties** → **Digital
Signatures** names the publisher. While a version is new, Windows SmartScreen may still show "Windows
protected your PC" even though the file is signed: its reputation builds up over weeks of installs.
The dialog names the publisher; continue only if it is the one on the download page. A release built
without the browser add-on (there is no Chrome or Edge store listing yet) says so on the page and in
Setup: YouTube control in Chrome or Edge is then not part of that version; everything else is.

**(B) Test builds for the team.** Once the team sends the PC code to GitHub, GitHub builds an unsigned
development installer and tests it on a Windows machine. The first build and test passed on 2026-10-14
(a GitHub test machine, not a home PC). To download one:

1. Open the repository on GitHub (signed in), click **Actions**, then **Windows installer** on the left.
2. Above the list, set **Branch** to the branch the team names (`main` once the work is merged) and
   **Event** to **push**. Only use a run from that branch and event: anyone on GitHub can open a pull
   request, and a run started by someone else's pull request is not DoMe's code.
3. Open the newest run with a green tick and, under **Artifacts**, download `DoMe-Setup-<version>+<code>`.
   Downloads are deleted after 30 days; if the **Artifacts** section is empty, ask the team to run the
   workflow again.
4. Unzip it. You get `DoMe-Setup-<version>.exe`, `SHA256SUMS` and `BUILD-INFO.txt`. `BUILD-INFO.txt`
   says which build this is: its `commit:` line must be a commit on that same branch (the team can check
   it for you).

To check that the file arrived undamaged, run this in PowerShell and compare the result with the line in
`SHA256SUMS`:

```powershell
Get-FileHash .\DoMe-Setup-0.1.0.exe -Algorithm SHA256
```

**Installing.** Double-click `DoMe-Setup-<version>.exe`. Do **not** choose "Run as administrator":
DoMe belongs to the account you use every day and must never run with administrator rights. If you do,
Setup installs nothing and asks you to start it again normally. (On a PC where Windows' administrator
prompts are switched off, it asks instead whether this is the right account.) Setup:

- puts DoMe in `%LOCALAPPDATA%\Programs\DoMe` and adds **DoMe** to the Start menu;
- offers **Start DoMe when I sign in to Windows** (unticked; tick it if you want DoMe running all the
  time);
- tells DoMe which server to use: `https://domeremote.com`, written to `dome-server.json` in that folder;
- connects the YouTube browser add-on when it can. Until the add-on is in the Chrome and Edge stores it
  cannot, and Setup shows a page that says so; everything else is installed;
- offers **Start DoMe now** at the end. DoMe then sits in the notification area, next to the clock. If
  you do not see its icon there, open **DoMe** from the Start menu.

**The blue "Windows protected your PC" warning is expected for test builds.** They are not signed.
For your own test build from GitHub you can click **More info**, then **Run anyway**. Customers must
never be asked to do this: public downloads are signed releases only, and for those the only advice is
to check that the dialog names the right publisher.

**If Windows says "Smart App Control blocked" DoMe or DoMe-Setup**, do not turn Smart App Control off:
once it is off, Windows cannot switch it back on without resetting the PC. Keep using the test kit
(`testkit/README.md`) on that PC. Signing does not settle this: a signed DoMe may still be blocked by Smart
App Control, because only `DoMe.exe`, the browser helper, Setup and the uninstaller are signed, not the
libraries bundled with them (`docs/SECURITY.md` T12). Whether a signed release runs on a Windows 11 PC with
Smart App Control on is **not yet verified**: the release smoke test runs on a Windows Server runner, which
has no Smart App Control.

**Linking.** domeremote.com is live (2026-10-16), so the installed DoMe links to your account. Open
Windows PowerShell (not as administrator) and run
`& "$env:LOCALAPPDATA\Programs\DoMe\DoMe.exe" link | Out-Host`. It shows a code and opens the
domeremote.com **Link a PC** page: check the code, name the PC, keep **Enable remote control** ticked and
click **Link this PC**. A running DoMe picks the link up by itself. If it says "This PC is already
linked", the PC still holds an older link, for example from the test kit, which shares the same settings
folder: quit DoMe from its icon, run the same command with `unlink` instead of `link`, then `link` again
and start DoMe from the Start menu. A "Link this PC" item in DoMe's menu will follow. First done on a
real PC against production on 2026-10-16 (`docs/ACCEPTANCE.md` Scenario 16).

**Updating.** DoMe does not check for updates yet. The update-check code exists
(`pc-agent/dome_agent/update_check.py`, **unit-tested** only: it accepts only version information signed
with DoMe's pinned update key, and never downloads or installs anything by itself), but it is not
connected to DoMe's menu or started by DoMe, and no update key is pinned yet. To update, run a newer
`DoMe-Setup` over the old one. It closes DoMe first (the same as **Quit
DoMe**) and keeps your link, paired phones and settings. If DoMe does not close, Setup stops without
changing anything and asks you to quit DoMe from its icon, then run Setup again. Setup refuses to
install an older **version number** over a newer one; uninstall first if you really want to go back.
All test builds today have the same version number (0.1.0), so any of them can replace any other,
older or newer: `BUILD-INFO.txt` (and the Setup file's **Properties → Details → Product version**)
tells you which build you have.

**Uninstalling.** Settings → Apps → DoMe → Uninstall. It closes DoMe, removes the program, the Start
menu entry, the browser registration and Start at login, and asks whether to also remove DoMe's
settings from this PC (the link to your account, the phones you allowed, the approved apps, the logs).
**No** (the default) keeps them: if you install DoMe again, this PC is still linked and your phones are
still allowed. **Yes** removes them. YouTube control is not kept either way: testers add
`/ExtensionId=<id>` again when they reinstall (see "Options for testers"). Uninstalling never removes
the PC from your DoMe account: if you answered **Yes**, or you will not use DoMe on this PC again,
remove it in the DoMe app on your phone (Devices). If you added the browser add-on, remove it in
`chrome://extensions` or `edge://extensions`.

**The test kit and the installer on the same PC.** Both use the same settings folder
(`%LOCALAPPDATA%\DoMe`), the same browser registration and the same "one DoMe at a time" rule. Quit one
before starting the other. Answering **Yes** to the uninstaller's question also deletes the test kit's
link. Uninstalling also removes the browser registration the test kit set up: run the test kit's
`2 Start DoMe on this PC.cmd` again afterwards. The installer sets no environment variable, so it never
changes which server the test kit uses.

**Options for testers.** Type them after the file name in PowerShell
(`.\DoMe-Setup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`). `/ApiUrl=` and `/ExtensionId=`
work only in the development builds that GitHub makes today; a release build
(`build_installer.py --release`) ignores them, always uses `https://domeremote.com`, and registers only
the production add-on (a release built with `--without-browser-addon` registers none).

| Option | What it does |
| --- | --- |
| `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` | install with no windows |
| `/MERGETASKS=startatlogin` | tick "Start DoMe when I sign in" |
| `/LOG=C:\path\setup.log` | write Setup's log there |
| `/ApiUrl=https://staging.domeremote.com` | use another DoMe server (https only; later development builds keep it when they upgrade; a release build replaces it with `https://domeremote.com`) |
| `/ExtensionId=<32 letters a-p>` | connect a development browser add-on (the id the test kit prints) |

The uninstaller, `%LOCALAPPDATA%\Programs\DoMe\unins000.exe`, takes `/VERYSILENT /SUPPRESSMSGBOXES`
and `/REMOVEDATA` (also delete the settings).

**What the installer does not do yet**: the test builds are not signed (Authenticode), and no signed
release has been made yet (the release pipeline is written, **not yet run**); DoMe does not check for
updates (see "Updating"); the YouTube add-on is not in the stores, so there is no production add-on id
(a release can only be built with `--without-browser-addon`, and then honestly says that YouTube
control is not included); DoMe's menu has no "Link this PC" item; test builds of the same version are not
ordered (see "Updating"); it has been tested only on GitHub's Windows Server machine (an administrator
account with UAC off), not yet on any Windows 10 or 11 PC, nor by a standard user; that test checks the tray only as a process (DoMe.exe keeps running, a second launch is refused,
`DoMe.exe quit` ends it): the icon, its menu and **Quit DoMe** from the menu are not verified (the
runner may have no visible notification area); and Inno Setup's makers ask commercial users to buy a
licence, which DoMe needs before it is sold. Evidence: **integration-tested on the windows-latest CI runner (administrator, UAC off)** —
first green `windows-installer.yml` run on 2026-10-14 (run 38488799434, 157 checks); **not Windows-device-tested**.

How it is built (developers): `pc-agent/packaging/installer/build_installer.py` runs PyInstaller on
`pc-agent/packaging/dome-agent.spec` (one folder with `DoMe.exe`, `dome-native-host.exe` and
`_internal\`) and compiles `pc-agent/packaging/installer/DoMe.iss` with Inno Setup 6;
`pc-agent/packaging/installer/smoke_test.py` is the test the workflow runs. Every DoMe.exe command
Setup runs gets 60 seconds, and Setup never stops DoMe itself: it asks it to quit
(`DoMe.exe quit`). Decisions: `pc-agent/DECISIONS.md` 51–56.

**How a release is made (developers)** (*not yet verified*: `release.yml` has never run). The founder's
one-time setup is `docs/LAUNCH_SETUP.md` §17. `.github/workflows/release.yml` starts from a pushed tag
`vX.Y.Z` or from **Run workflow** (version, and **publish** on or off) and runs these jobs:

1. **check** (no secrets): the version equals `pc-agent/pyproject.toml` and `dome_agent.__version__`;
   the commit is on the default branch; no release `vX.Y.Z` exists yet; an update key is pinned in
   `UPDATE_PUBLIC_KEYS`; whether a production add-on id exists.
2. **tests** (Windows, no secrets): the Windows test list, as `windows-installer.yml`.
3. **build** (Windows, no secrets): PyInstaller only (`build_installer.py --pyinstaller-only`); the folder
   becomes a 7-day workflow artifact.
4. **sign** (environment `release`: the founder approves the run): refuses at once when a variable or the
   update key is missing; installs Inno Setup and Microsoft's `Microsoft.ArtifactSigning.Client` (pinned
   SHA-256, checked to be signed by Microsoft) before it signs in to Azure with the run's OIDC token;
   `build_installer.py --release --skip-pyinstaller --sign-artifact-signing … --publisher …` compiles
   Setup, and Inno's `SignTool` signs `DoMe.exe`, `dome-native-host.exe`, Setup and the uninstaller with
   Azure Artifact Signing (RFC 3161 timestamp from `http://timestamp.acs.microsoft.com`); the script then
   runs `signtool verify /pa /v` on Setup and both programs and writes `SHA256SUMS`, `BUILD-INFO.txt`,
   `latest.json` and `RELEASE-NOTES.md`; the job signs out of Azure, then one step that alone holds the
   update key runs `packaging/release/release_files.py sign` to write `latest.json.sig`.
5. **smoke** (Windows, no secrets): installs the **signed** Setup with `smoke_test.py full --release`,
   which checks the Authenticode signatures of Setup and the installed `DoMe.exe`,
   `dome-native-host.exe` and `unins000.exe` (Valid, the publisher's name, timestamped), that
   `/ApiUrl=` and `/ExtensionId=` are ignored, and the install, upgrade and uninstall phases.
6. **publish** (only for a tag, or with **publish** on): `release_files.py verify` checks the signature,
   the fields, `SHA256SUMS` and that `BUILD-INFO.txt` says "signed with Azure Artifact Signing"; then a
   **draft** release with the six files, a comparison of GitHub's own SHA-256 digest of every uploaded file
   with the verified ones, and only then the release is published as **latest**.

`--release` rules (`build_installer.py`): never without a signing option (no unsigned release exists);
`--publisher` is required (the certificate's name, as Windows shows it); while
`PRODUCTION_EXTENSION_IDS` is empty, `--without-browser-addon` is required (Setup is compiled with
`/DNoAddon`: it registers no browser bridge, still removes a development registration left by a test
install, and shows "YouTube control in Chrome or Edge is not part of this version of DoMe"); once an id
exists, that flag is refused.

`latest.json` (schema 1, keys sorted, two-space indent, one trailing newline) holds `version`, `url`
(exactly `https://github.com/Heshh999/DoMe/releases/download/vX.Y.Z/DoMe-Setup-X.Y.Z.exe`), `sha256`,
`size`, `published_at` (when the build wrote the file, just before signing; the release appears minutes
later), `release_page`, `publisher`, `browser_addon`, `product` and `schema`. `latest.json.sig` is an
Ed25519 signature over its exact bytes, base64 plus a newline. The public half of the update key is
pinned twice: `UPDATE_PUBLIC_KEYS` in `pc-agent/dome_agent/update_check.py` and
`WINDOWS_RELEASE_PUBLIC_KEYS` in `mobile-app/src/content/windowsRelease.ts` (a pc-agent test checks they
are equal). The key itself is made with `packaging/release/make_update_key.py` on the founder's PC and
lives only in the GitHub Environment secret `DOME_UPDATE_SIGNING_KEY`.

**Showing a release on domeremote.com.** The site cannot ask GitHub (the server allows the page to
connect only to its own origin), so after a release the engineer copies the release's `latest.json` to
`mobile-app/src/content/windows-release.json` and `latest.json.sig` to
`mobile-app/src/content/windows-release.sig`, byte for byte. `mobile-app/test/public/windowsRelease.test.ts`
verifies that signature against the pinned key, so a hand-typed or stale file fails CI. After CI passes,
the founder presses **Deploy** (production); only then does domeremote.com/download show the button. Until
then the GitHub release page is where the release is.

## 1. What the DoMe agent is and is not

The agent (`dome_agent`, packaged as `DoMe.exe`) runs **in the signed-in user's Windows session** with
that user's ordinary rights. It:

- opens **one outbound WebSocket** to the DoMe service (`wss://…/ws/agent`) and reconnects with
  backoff; it never listens on the internet and never accepts inbound connections from the network;
- shows a **tray icon** (green connected, amber reconnecting, red disabled or superseded, grey offline)
  with the menu *status · Disable/Enable remote control · Stop manual input · Paired phones ▸ · Pair a
  phone… · Approved apps… · Start at login · Reconnect · Diagnostics… · Quit DoMe*;
- executes only the 32 actions of the shared registry (`shared/protocol/actions.json`): YouTube
  control through the browser extension, Windows media sessions, system volume and mute, approved
  application launch/focus/minimise/close, lock, explicitly confirmed sleep/restart/shutdown, and
  starting/stopping a manual-input session;
- injects **touchpad and keyboard input** (`SendInput`) from phones the PC owner explicitly allowed
  (§4.9), through a signed, bounded input stream that is never queued or replayed
  (`docs/INPUT_CONTROL.md`);
- runs **once per Windows user session** and at most once per Windows account (§4.10);
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
| Program folder | `%LOCALAPPDATA%\Programs\DoMe`: `DoMe.exe`, `dome-native-host.exe`, `_internal\` (Python and the libraries), `THIRD_PARTY_NOTICES.txt` and `licenses\` (the third-party licences, §4.13), `unins000.exe`/`.dat`, `dome-server.json` (the server address `DoMe.exe link` falls back to) | Setup |
| Start menu entry and uninstall entry | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\DoMe.lnk`; `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{FD238776-147F-4CCF-B2CA-4AC5F30A07FF}_is1` (Settings → Apps) | Setup |
| State directory | `%LOCALAPPDATA%\DoMe` (override: `DOME_AGENT_STATE_DIR` or `--state-dir`) | first command |
| Local database | `%LOCALAPPDATA%\DoMe\state.sqlite3` (settings, locally approved phones, bounded command journal, confirmation challenges, approved apps, pending power action, pending revocations, local security events) | first command |
| PC identity key and account credential | `%LOCALAPPDATA%\DoMe\secrets\pc_key.bin` (P-256 private key) and `pc_credential.bin` — each **DPAPI-protected (current user)** via `CryptProtectData`; decryptable only by the same Windows user on the same machine | `DoMe.exe link` |
| Logs | `%LOCALAPPDATA%\DoMe\logs\agent.log`, redacted JSON lines, 2 MB × 3 rotations; `native-host.log`; `crash.log` only after an unexpected error in the installed programs (command line and traceback, tokens masked but not reviewed: read it before sharing any of it; exit code 70) | `run`, the native host |
| Diagnostics bundles | `%LOCALAPPDATA%\DoMe\diagnostics\dome-diagnostics-<timestamp>.json` | tray *Diagnostics…* or `DoMe.exe diagnostics` |
| Native-messaging manifest | `%LOCALAPPDATA%\DoMe\com.dome.agent.json` | `install-native-host` |
| Native-host registration | `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent` and `HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.dome.agent` (default value = manifest path) | `install-native-host` |
| Start at login | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DoMe` = `"<path>\DoMe.exe" run` | tray *Start at login* (opt-in; off by default) |
| Instance lock and pid file | Named mutex `Local\DoMe.Agent.<session id>` (Windows) or `%LOCALAPPDATA%\DoMe\agent.lock` (`flock`, other systems), plus `agent.pid` (`{pid, session, started_at}`) | `run` |
| Input recovery file | `%LOCALAPPDATA%\DoMe\input_holds.json` — only while the phone holds a button or key: session id, phone id and the held button/key **names**, never typed text; deleted when released | `run` |
| Local IPC endpoints | Named pipes `\\.\pipe\DoMe.Agent.<hash>` (browser bridge) and `\\.\pipe\DoMe.Control.<hash>` (CLI ⇄ agent), DACL restricted to the current user and checked for the same session | `run` |

No `HKLM` keys, no services, no scheduled tasks, no Program Files writes are made by the agent or by
Setup, and Setup sets no environment variable.

## 4. Install — the manual steps

The installer (previous section) does §4.1, §4.3 (when an extension id is available) and §4.7 for you.
The steps below are the manual way, for source installs and the test kit. (On Linux/macOS, see §8.)

### 4.1 Build `DoMe.exe` and `dome-native-host.exe`

Prerequisites on the build PC: Python 3.12, [`uv`](https://docs.astral.sh/uv/), the repository, and
Inno Setup 6 for the installer.

```bat
cd pc-agent
uv sync --locked --extra build
uv run --no-sync python packaging\installer\build_installer.py --pyinstaller-only
```

Output: one folder, `dist\DoMe\`, holding `DoMe.exe` (`console=False`, tray application; `DoMe.exe`
without arguments means `run`), `dome-native-host.exe` (`console=False`, so the browser never flashes a
console window) and the `_internal\` runtime they share; `install-native-host` finds the host next to
`DoMe.exe` (`dome_agent/tray.py::host_executable_path`), so nothing needs copying. The protocol contract
is bundled as `dome_protocol/_contract` (without the test fixtures and their test key; the script checks
that). Without `--pyinstaller-only` the same script also compiles the installer into
`dist\installer\`. Evidence: the folder was built and run on Linux (`DoMe version`, the native host,
a `bridge_hello` round trip, `DoMe quit`); the Windows build: **integration-tested on the windows-latest CI runner** (first green
`windows-installer.yml` run, 2026-10-14, run 38488799434).

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
`DOME_AGENT_DEV_EXTENSION_ID`. With neither, it keeps the extension of the manifest already registered
on this PC (Setup's `/ExtensionId=`, or the test kit), so upgrades and `repair` keep a development
extension working; with none of these, the command refuses with `no extension id available …`. The
agent never downloads this list.

The extension itself is loaded unpacked for now (`browser-extension/README.md` → "Load unpacked"):
`pnpm build` on any machine, copy `browser-extension/dist` to the PC, `chrome://extensions` →
Developer mode → Load unpacked. Unpacked ids are derived from the folder path, so keep `dist` where
it is or pin a `"key"` in the manifest. Evidence: **not yet verified** in a real browser.

`DoMe.exe uninstall-native-host` deletes the two registry keys (the manifest file is removed with the
state directory, or by the uninstaller).

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
URLs stored at link time (so the installer does not set them: `DoMe.exe link` falls back to
`dome-server.json` next to the installed `DoMe.exe` instead); `DOME_AGENT_STATE_DIR`; `DOME_AGENT_LOG_LEVEL`; `DOME_AGENT_HEADLESS=1` (no
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
deletes the value. Nothing is written by default. `DoMe.exe start-at-login on|off|status` does the same
from a console (Setup uses it for its opt-in option; the uninstaller turns it off). Evidence: **not yet
verified** on a PC (unit-tested with a stand-in for the registry).

### 4.8 Emergency stop

Tray → **Disable remote control** or `DoMe.exe disable` (works even when the agent is not running: it
writes the local flag to `state.sqlite3`). Every command then fails with `PC_REMOTE_DISABLED`, an armed power countdown is cancelled, a
confirmation that arrives afterwards is refused, and no frame from the service can re-enable it.
`DoMe.exe enable` or the tray item turns it back on. `DoMe.exe revoke <controller_id>` revokes one phone
locally and tells the service (immediately, or on the next connect if offline).

### 4.9 Allow touchpad and keyboard for a phone (on the PC)

Touchpad (`pointer`) and keyboard (`keyboard`) are two separate permissions per phone. They are **off**
for every phone until you turn them on here, including phones paired before this version; a phone
cannot grant them to itself, and the DoMe service cannot either (`rules.grant_update`).

**What they allow** — the PC shows this text before you grant them
(`dome_agent/pairing.py::INPUT_SCOPE_EXPLANATION`):

> Touchpad (pointer) and Keyboard let this phone move the mouse, click, scroll and type into whatever
> is in front on this PC, in every app of the unlocked Windows session, not only the approved apps. The
> approved-app list restricts structured app actions; it is not a sandbox around a real mouse and
> keyboard. Only the phone you approve can use them, only while you keep remote control on, and you can
> switch them off here at any time.

Grant them only to phones you trust as much as someone sitting at this PC. Ways to grant:

1. **While pairing**: the approval window lists the requested permissions and adds two unticked
   checkboxes, *Allow touchpad / mouse (pointer)* and *Allow keyboard*. In the console, `DoMe.exe pair`
   asks "Allow touchpad / mouse (pointer) for this phone? [y/N]" and "Allow keyboard (typing, keys,
   shortcuts) for this phone? [y/N]" separately. `--yes` never grants them; only `--pointer` /
   `--keyboard` do.
2. **For a phone that is already paired**: tray → **Paired phones ▸ ‹phone name› (‹id›…) ▸ Allow
   touchpad** / **Allow keyboard** (checkable; ticking shows the explanation as a notification;
   **What this allows…** shows it again). A phone paired a moment ago appears after the next status
   update (`pc-agent/KNOWN_ISSUES.md` #9).
3. **Console**:

   ```bat
   DoMe.exe status                                   REM lists paired phones and their controller ids
   DoMe.exe grant <controller_id> --pointer --keyboard
   DoMe.exe grant <controller_id> --remove-keyboard
   ```

   The command prints the explanation, then "Permissions for <id>: …". With the agent not running it
   changes the local store and says "The agent is not running, so the DoMe service has NOT been told
   yet: it sends grant_update when it connects." (suffix "(service update pending)").

The agent tells the service (`grant_update`), the phone's Devices page then shows "Touchpad / keyboard:
Allowed: touchpad and keyboard". Removing both ends a live session and releases anything the phone
held; removing only the touchpad releases a drag. To stop the current session without changing
permissions: tray → **Stop manual input** or `DoMe.exe stop-input` ("Manual input stopped; released N
held button(s)/key(s)."). **Disable remote control** (§4.8) stops all input too. Evidence: grant,
removal and offline re-send are **integration-tested** on Linux; the tray and console flows on Windows
are **not yet verified**.

Windows itself refuses remote input on the lock screen, the sign-in screen, UAC prompts and windows
running as administrator. DoMe does not work around that and does not need to run as administrator.

### 4.10 One DoMe per Windows session

Starting `DoMe.exe` again while it runs does not start a second agent: the new process asks the running
one to show its window, prints "DoMe is already running in this session (pid N); its window was brought
up. Nothing else started." and exits. If the running agent does not answer, it prints "Another DoMe
instance is running but not responding (pid N)…" and suggests `dome-agent repair`. A second Windows
session of the **same** Windows account (Remote Desktop, a second sign-in) is refused with "DoMe already
runs for this Windows account in session N …", because both sessions would share one PC identity;
another Windows account runs its own agent normally. DoMe never terminates another process.
`DoMe.exe status` reports a stale endpoint, a stale pid file, a permission problem on
`%LOCALAPPDATA%\DoMe` and an other-session conflict on an `INSTANCE:` line. Messages and steps:
`docs/TROUBLESHOOTING.md` §20. Evidence: **unit-/integration-tested** on Linux (`flock`); the Windows
named mutex and session-id detection are **not yet verified**.

### 4.11 Repair

```bat
DoMe.exe repair
DoMe.exe repair --host-path "C:\…\dome-native-host.exe"
```

Re-writes and re-registers the browser native-messaging host, removes a stale local endpoint and a stale
pid file, and checks that `%LOCALAPPDATA%\DoMe` is writable. It leaves a running agent running, refuses
to touch one that does not answer, and never touches `identity.json`, the DPAPI-protected key and
credential, the paired phones and their permissions, or the approved apps; it ends with "Repair
finished. Pairing, grants and approved apps were preserved; nothing was terminated." A new phone still
needs the local approval. Evidence: **integration-tested** on Linux with a running agent; registry
re-registration on Windows **not yet verified**.

### 4.12 Quit from a console or a script

```bat
DoMe.exe quit
DoMe.exe quit --timeout 30
```

The tray's **Quit DoMe** from the command line: it asks the DoMe of this Windows session to quit, waits
until its process has ended (20 s by default) and prints "DoMe has quit." (exit code 0), "DoMe is not
running in this session." (0), or that it did not answer or did not quit (1). It never terminates a
process. Setup and the uninstaller run it before they replace or remove files. Evidence:
**integration-tested** on Linux (a real agent process stops cleanly); Windows: in the installer smoke
test, **not yet verified**.

### 4.13 Screen view (beta, not yet verified)

Screen view (protocol 1.4, `docs/design/screen-view.md`) shows this PC's screen live on a phone, with tap
to click, two-finger scroll and pinch to zoom. **Nothing of it has been verified on a device**: capture,
encoding, the "Screen view on" bars, the hotkey and iPhone playback were built and unit-/integration-tested
on Linux with a synthetic picture only. It is off unless the DoMe service has it switched on
(`DOME_SCREEN_VIEW_ENABLED`, off by default), and it needs both a build that contains it and a phone you
allow here, on this PC.

**Is it in my build?** `BUILD-INFO.txt` next to the installer says `screen view: included (beta, not yet
verified on a device)` or `screen view: not included in this build`. Every build so far says *not
included*: Screen view needs DoMe's own video library (an LGPL build of FFmpeg, made by
`.github/workflows/ffmpeg-lgpl.yml`, which has not run yet). The stock video library from the Python
package index contains GPL code DoMe may not ship, so the build leaves Screen view out instead of using it;
`pc-agent/packaging/licence_gate.py` checks every build for that, and a release build fails if it finds
anything it does not know. Once the LGPL library is published and pinned in `pc-agent/pyproject.toml`
for Windows, the same workflow builds Screen view in and also runs `DoMe.exe screen-worker --self-test` on
the build machine (video encoders, exactly one decoder, a test picture, the keyframe hook). A build
without Screen view still answers that command and says the video parts are missing.

**Allowing a phone (on the PC only).** A phone can never give itself Screen view, and neither can the
DoMe service or a pairing (`--yes` included):
- tray → **Paired phones ▸ ‹phone› ▸ Allow Screen view…** opens DoMe's window, which explains what it
  allows, warns "Only allow phones that are yours. DoMe will never call you or ask you to allow Screen
  view.", and enables **Allow** after 5 seconds; or
- `DoMe.exe grant <controller_id> --screen` opens the same window and waits for your answer there.
  `--remove-screen` turns it off at once (and ends a live picture).

**While a phone is watching**, a "Screen view on" bar is shown on every monitor (DoMe keeps it out of
the picture the phone sees; no setting hides it), the tray shows it, and Windows shows a notification at
the start and the end. Stop it with the bar, tray → **Stop Screen view**, **Ctrl+Alt+Shift+S**,
`DoMe.exe stop-screen`, **Disable remote control**, by locking the PC or by quitting DoMe. After you stop
it at the PC, phones are asked before they can start again (10 minutes by default). Tray → **Screen view
history…** or `DoMe.exe screen-history` lists the last 20 sessions and refused starts (phone, time,
duration, reason; never a picture). Nothing is recorded and the AI assistant never sees the screen. The
lock screen, UAC prompts and windows protected by their app (some videos) are never shown.

**Licences.** Setup now shows DoMe's licence terms (a draft, `pc-agent/packaging/installer/licence-terms.txt`)
and installs `THIRD_PARTY_NOTICES.txt` and a `licenses` folder next to `DoMe.exe`: every library in DoMe,
its licence text, and for the LGPL parts (the tray library pystray, and FFmpeg in builds with Screen view)
how to replace them and how to get their source code. The terms and the notices are drafts for the
founder and counsel (`docs/design/screen-view.md` §20 step 4).

## 5. Uninstall — the manual steps

The uninstaller (Settings → Apps → DoMe) does all of this for an installed DoMe (see "The installer");
it keeps `%LOCALAPPDATA%\DoMe` unless you answer Yes or pass `/REMOVEDATA` (`pc-agent/DECISIONS.md` 55).
For a source or test-kit install, the steps mirror what the install created:

1. Tray → **Quit DoMe** (or close the headless process). Any armed power countdown is cancelled on exit.
2. Optionally first revoke the phones from the phone (Devices → Revoke) or unlink the PC (Devices →
   *Unlink…*) so the service stops listing it. Without this the service keeps the PC row until you
   unlink it from any signed-in device; the credential on this PC is useless once the folder is gone.
3. `DoMe.exe uninstall-native-host` — removes the two `HKCU` native-messaging keys.
4. Tray → untick **Start at login** (or delete `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DoMe`).
5. Delete `%LOCALAPPDATA%\DoMe` — this removes the SQLite store (local phone approvals and their
   touchpad/keyboard permissions, journal, approved apps), the DPAPI-protected key and credential, logs,
   diagnostics, the pid file, any input recovery file and the manifest file.
6. Remove the DoMe extension from `chrome://extensions` / `edge://extensions` and delete the
   `DoMe.exe` folder.

Spec §15 asks for installer-driven cleanup of all of the above: the uninstaller does steps 1, 3, 4 and
(when you answer Yes) 5; steps 2 and 6 stay with you, because the agent cannot unlink itself on the
service and Setup cannot remove a browser add-on.

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
- **Power confirmations** on the phone state that sleep, restart or shutdown can interrupt or end
  remote access and that DoMe cannot wake or turn the PC on again remotely (no remote wake in V1); the
  agent's countdown detail says the same (`dome_agent/authz.py::power_confirmation_detail`). After a
  restart, DoMe reconnects only if Start at login is on and Windows has signed you in.
- **Manual input** ends by itself when the phone goes quiet for 3 s, when Windows locks or shows a
  secure desktop (lock, sign-in, UAC), when remote control is disabled, when the phone is revoked or its
  permissions are removed, and when DoMe quits; each time the agent releases the buttons and keys that
  phone was holding (never your physical keyboard's). After a crash, the next start releases them from
  `input_holds.json`. With an elevated window in front, typing from the phone is refused and clicks may
  be dropped by Windows (`docs/INPUT_CONTROL.md` §7–§8).
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
error, offline revocation, packaging, and for protocol 1.1: SendInput on a real desktop, multi-monitor /
mixed DPI, UAC / lock refusal, clock skew, tray grants, second launch, repair, crash recovery and the
power confirmation copy). `browser-extension/README.md` → "Manual verification checklist"
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
.venv/bin/pytest -q                         # 256 passed, 1 skipped at the last recorded full run (fake platform, fake relay, fake extension)
```

`install-native-host` on Linux writes the manifest and reports that registry registration was skipped;
the native host can be exercised with `dome_agent.testing.fake_extension.FakeExtension` or the process
test `tests/test_processes.py::test_native_host_process_forwards_frames` (**unit-tested**).

## 9. What remains blocked

| Item | Status | What unblocks it |
| --- | --- | --- |
| Building `DoMe.exe` / `dome-native-host.exe` | One PyInstaller folder (`dome-agent.spec`); built and run on Linux; built on Windows by `windows-installer.yml` | The first green `windows-installer.yml` run; fix whatever it reports |
| Installer with uninstall cleanup | Inno Setup per-user installer (`pc-agent/packaging/installer/DoMe.iss`): native host, start-at-login opt-in, upgrade that quits DoMe first, downgrade refusal, uninstall cleanup; smoke-tested by CI, **awaiting its first run**; never run on a PC by a standard user | The first green run, then the founder installs it on his Windows 11 PC |
| Linking from the tray | `DoMe.exe link` works only from a console; the tray says "Not linked — run DoMe link" | A tray "Link this PC…" item (small change), and domeremote.com live |
| **Code signing** (Authenticode) | Designed and written: `release.yml` signs `DoMe.exe`, `dome-native-host.exe`, Setup and the uninstaller with Azure Artifact Signing (`build_installer.py --sign-artifact-signing`, through Inno's `SignTool`) behind the protected environment `release`, verifies every signature after signing and again after installing. **Not yet verified**: it has never run; the option rules and the commands are **unit-tested** | The founder's Artifact Signing account, identity validation and the GitHub setup (`docs/LAUNCH_SETUP.md` §17), then a first run with publish off. SmartScreen may still warn for new versions; **never instruct customers to disable antivirus or SmartScreen** |
| Update mechanism | An update **check** exists in code (`dome_agent/update_check.py`): Ed25519-signed `latest.json` against a pinned key, exact GitHub download address, rollback guard, at most three redirects on GitHub hosts, a download kept only when size and SHA-256 match; it never installs anything by itself. **Unit-tested** only; it is **not connected** to DoMe's menu or start-up, and no key is pinned, so DoMe does not check for updates yet. No automatic update, no staged rollout | Connect it to the tray ("Check for updates…") and the agent's start-up (`tray.py`, `agent.py`), pin the founder's update key, then a first signed release to check against |
| Public download page | domeremote.com/download shows "not yet published" until `mobile-app/src/content/windows-release.json` holds a signed release's `latest.json` (its signature is checked by a vitest); then it shows the download, its SHA-256 and the publisher (**unit-tested** with a mocked release; **not yet verified** with a real one) | A published signed release, its two files copied into the site, CI, then **Deploy** |
| Inno Setup licence | Free for non-commercial use; its makers ask commercial users to buy a licence | Buy one before the paid launch |
| Production extension id | `PRODUCTION_EXTENSION_IDS` is empty; only `DOME_AGENT_DEV_EXTENSION_ID` works. A release can still ship, built with `--without-browser-addon`: it registers no browser bridge and says on the download page, in the release notes and in Setup that YouTube control in Chrome or Edge is not included in that version (**not yet verified**: no release has been made) | Chrome Web Store listing (founder account), pin its `"key"` in `browser-extension/public/manifest.json`, bake the id into the release build |
| Windows device evidence | None | The checklist in §7 |
| Touchpad/keyboard on a real desktop (`SendInput`, multi-monitor, UAC/elevation, lock) | Implemented, unit-/integration-tested with a fake adapter only | The protocol 1.1 rows of the checklist in §7; results go to `docs/INPUT_CONTROL.md` §10–§11 and `docs/ACCEPTANCE.md` |
| Single-instance mutex and same-account session detection on Windows | Implemented, not run on Windows | Checklist rows *Second launch* and *Repair* |
| `media_while_locked` switch | Setting exists, no UI | A tray or CLI toggle (small change) |
| Screen view in the Windows build (§4.13) | Every build leaves it out: DoMe's LGPL FFmpeg + PyAV wheel (`ffmpeg-lgpl.yml`) has never been built. The licence gate on `dist/DoMe` and the worker's `--self-test` steps are in `windows-installer.yml` and `release.yml` (**unit-tested** on their text; a Linux PyInstaller build of the spec was checked by hand) and **not yet run** on Windows. The list of DLLs the gate knows was written without a Windows build: an unknown one is a warning in development builds and stops a release | Run `ffmpeg-lgpl.yml`, publish its wheel as a release asset, pin it in `pc-agent/pyproject.toml` under `[tool.uv.sources]` with `marker = "sys_platform == 'win32'"` and lock; then the first `windows-installer.yml` run with Screen view, then the device round (`docs/ACCEPTANCE.md` Scenario 26) |
| Licence terms and third-party notices | Drafts (`pc-agent/packaging/installer/licence-terms.txt`, `pc-agent/packaging/THIRD_PARTY_NOTICES.txt`); the written source offer points to the Support page | Founder and counsel review (business name, liability, governing law, the LGPL obligations) |

None of these affect the security model: an unsigned development build has the same authorisation
path as a signed one; signing protects the download, not the protocol.

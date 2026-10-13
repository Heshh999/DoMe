# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for DoMe on Windows: ONE folder, dist\DoMe\, holding both programs and the runtime
# they share:
#   DoMe.exe              the tray agent and its command line (`DoMe.exe` alone means `run`)
#   dome-native-host.exe  the Chrome/Edge native-messaging host (started by the browser)
#   _internal\            Python, the libraries, the protocol contract (dome_protocol/_contract), the tray icon
# The installer (packaging/installer/DoMe.iss) ships this folder as it is. A shared folder instead of a
# one-file host: the browser starts the host every few seconds while DoMe is not running, and a one-file
# build would unpack itself to %TEMP% on every start; install-native-host finds the host next to DoMe.exe.
# Both are windowed (console=False). Their entry scripts (agent_entry.py, native_host_entry.py) catch
# every exception (dome_agent.crashguard: logs\crash.log, exit code 70), so the bootloader's modal error
# box never appears for a Python error; Setup runs DoMe.exe commands and must never wait on such a box.
# Build (Windows, from pc-agent):
#   uv sync --locked --extra build
#   uv run --no-sync pyinstaller --noconfirm --clean packaging\dome-agent.spec
# or packaging\installer\build_installer.py, which also checks the folder and compiles the installer.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # pc-agent/
CONTRACT = ROOT.parent / "shared" / "protocol"
# Only what dome_protocol reads at run time (registry.py, schemas.py): never fixtures/ (it holds a test key).
CONTRACT_DATAS = [(str(CONTRACT / name), "dome_protocol/_contract") for name in ("actions.json", "errors.json", "plans.json", "version.json")]
CONTRACT_DATAS.append((str(CONTRACT / "schemas"), "dome_protocol/_contract/schemas"))
ICON = ROOT.parent / "brand" / "exports" / "windows" / "dome.ico"
if not ICON.is_file():
    raise SystemExit(f"brand icon missing: {ICON}")

agent_hidden = ["dome_agent.actions.system", "dome_agent.actions.youtube", "dome_agent.actions.media", "dome_agent.actions.volume",
                "dome_agent.actions.windows", "dome_agent.actions.apps", "dome_agent.actions.power",
                "dome_agent.platform.windows.apps", "dome_agent.platform.windows.media", "dome_agent.platform.windows.nativehost",
                "dome_agent.platform.windows.power", "dome_agent.platform.windows.session", "dome_agent.platform.windows.startup",
                "dome_agent.platform.windows.volume", "pystray._win32", "PIL.ImageTk", "tkinter", "win32crypt", "win32pipe", "win32file",
                "win32security", "win32process", "win32api", "win32gui", "win32con", "ntsecuritycon", "comtypes", "pycaw.pycaw"]
agent_hidden += collect_submodules("winsdk.windows.media.control")
agent_hidden += collect_submodules("winsdk.windows.foundation")

agent = Analysis(
    [str(ROOT / "packaging" / "agent_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[*CONTRACT_DATAS, (str(ROOT / "dome_agent" / "assets"), "dome_agent/assets")],
    hiddenimports=agent_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["dome_agent.testing"],
    noarchive=False,
)
host = Analysis(
    [str(ROOT / "packaging" / "native_host_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=list(CONTRACT_DATAS),
    hiddenimports=["win32pipe", "win32file", "win32security", "win32process", "win32api", "pywintypes"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["dome_agent.testing", "dome_agent.tray", "pystray", "PIL", "tkinter", "winsdk", "pycaw", "comtypes", "psutil",
              "websockets", "httpx"],
    noarchive=False,
)
agent_exe = EXE(
    PYZ(agent.pure),
    agent.scripts,
    [],
    exclude_binaries=True,
    name="DoMe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # tray application; CLI subcommands print when their output is redirected (Setup, CI)
    icon=str(ICON),
)
host_exe = EXE(
    PYZ(host.pure),
    host.scripts,
    [],
    exclude_binaries=True,
    name="dome-native-host",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # the browser must not flash a console window; stdio is the native-messaging pipe
    icon=str(ICON),
)
coll = COLLECT(agent_exe, agent.binaries, agent.datas, host_exe, host.binaries, host.datas, strip=False, upx=False, name="DoMe")

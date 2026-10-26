# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for DoMe on Windows: ONE folder, dist\DoMe\, holding both programs and the runtime
# they share:
#   DoMe.exe              the tray agent and its command line (`DoMe.exe` alone means `run`); also the
#                         Screen view worker, `DoMe.exe screen-worker` (an argv dispatch in cli.main, started
#                         only by the running agent, design §5.1), so the worker needs no program of its own
#   dome-native-host.exe  the Chrome/Edge native-messaging host (started by the browser)
#   _internal\            Python, the libraries, the protocol contract (dome_protocol/_contract), the tray icon
# The installer (packaging/installer/DoMe.iss) ships this folder as it is, plus THIRD_PARTY_NOTICES.txt and
# licenses\ that build_installer.py writes next to the programs after PyInstaller. A shared folder instead of a
# one-file host: the browser starts the host every few seconds while DoMe is not running, and a one-file
# build would unpack itself to %TEMP% on every start; install-native-host finds the host next to DoMe.exe.
# Both are windowed (console=False). Their entry scripts (agent_entry.py, native_host_entry.py) catch
# every exception (dome_agent.crashguard: logs\crash.log, exit code 70), so the bootloader's modal error
# box never appears for a Python error; Setup runs DoMe.exe commands and must never wait on such a box.
#
# Screen view (docs/design/screen-view.md §5, §15; WP1.6) is built in only when the build environment has the
# `screen` extra (aiortc, av). windows-installer.yml and release.yml install it only when pyproject.toml pins
# av on Windows to DoMe's own LGPL wheel (`licence_gate.py shipping-av`), so the stock PyPI av wheel, which
# bundles GPL x264/x265, never reaches a Windows build; without the extra the folder has no aiortc and no av,
# and `DoMe.exe screen-worker --self-test` reports them missing. Either way, after the build, the licence gate
# checks dist\DoMe (`licence_gate.py collect --folder` + `check --frozen`) and the workflows run the worker's
# self-test. Three collection rules:
#   * aiortc.contrib (MediaRecorder, MediaPlayer, signaling helpers) is never bundled (design §5.1, §17.2);
#   * aiortc.rtcrtpsender also goes in as its .py source: the worker's PLI hook check reads that method's
#     source with inspect.getsource (dome_agent/screen/worker/rtc.py), which finds nothing in a frozen PYZ;
#   * pystray (LGPL-3.0) goes in as plain .py files under _internal\pystray, so a user can replace it, as
#     THIRD_PARTY_NOTICES.txt says;
#   * the Windows.Graphics.Capture fallback takes windows-capture's native module ALONE, as a binary at
#     _internal\windows_capture\windows_capture.pyd, where capture_wgc.load_native finds it: the package's Python
#     wrapper imports cv2 and numpy, which pyproject.toml excludes (OpenCV's wheel carries its own FFmpeg), so the
#     wrapper is never analysed and cv2/numpy are excluded outright.
# build_installer.py checks all four in the built folder; the worker's --self-test loads the native module.
#
# Build (Windows, from pc-agent):
#   uv sync --locked --extra build [--extra screen]
#   uv run --no-sync pyinstaller --noconfirm --clean packaging\dome-agent.spec
# or packaging\installer\build_installer.py, which also writes the notices, checks the folder and compiles the
# installer.
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # pc-agent/
CONTRACT = ROOT.parent / "shared" / "protocol"
# Only what dome_protocol reads at run time (registry.py, schemas.py): never fixtures/ (it holds a test key).
CONTRACT_DATAS = [(str(CONTRACT / name), "dome_protocol/_contract") for name in ("actions.json", "errors.json", "plans.json", "version.json")]
CONTRACT_DATAS.append((str(CONTRACT / "schemas"), "dome_protocol/_contract/schemas"))
ICON = ROOT.parent / "brand" / "exports" / "windows" / "dome.ico"
if not ICON.is_file():
    raise SystemExit(f"brand icon missing: {ICON}")

SCREEN_VIEW = all(importlib.util.find_spec(name) is not None for name in ("aiortc", "av"))
print(f"dome-agent.spec: Screen view {'built in' if SCREEN_VIEW else 'left out (no aiortc/av in this environment)'}")

agent_hidden = ["dome_agent.actions.system", "dome_agent.actions.youtube", "dome_agent.actions.media", "dome_agent.actions.volume",
                "dome_agent.actions.windows", "dome_agent.actions.apps", "dome_agent.actions.power",
                "dome_agent.platform.windows.apps", "dome_agent.platform.windows.media", "dome_agent.platform.windows.nativehost",
                "dome_agent.platform.windows.power", "dome_agent.platform.windows.session", "dome_agent.platform.windows.startup",
                "dome_agent.platform.windows.volume", "pystray._win32", "PIL.ImageTk", "tkinter", "win32crypt", "win32pipe", "win32file",
                "win32security", "win32process", "win32api", "win32gui", "win32con", "ntsecuritycon", "comtypes", "pycaw.pycaw"]
agent_hidden += collect_submodules("winsdk.windows.media.control")
agent_hidden += collect_submodules("winsdk.windows.foundation")
# The Screen view modules of the agent itself are in every build (the worker's `screen-worker` dispatch must
# answer even without aiortc); the worker imports aiortc and av lazily, so they are only analysed when present.
agent_hidden += ["dome_agent.actions.screen", "dome_agent.platform.windows.screen", "dome_agent.screen.supervisor",
                 "dome_agent.screen.worker.main", "dome_agent.screen.worker.pipe", "dome_agent.screen.worker.encode",
                 "dome_agent.screen.worker.fake", "win32job"]
agent_binaries = []
agent_collection_mode = {"pystray": "py"}
if SCREEN_VIEW:
    agent_hidden += ["dome_agent.screen.worker.rtc", "dome_agent.screen.worker.track", "dome_agent.screen.worker.capture_dda",
                     "dome_agent.screen.worker.capture_wgc"]
    agent_hidden += collect_submodules("aiortc", filter=lambda name: not name.startswith("aiortc.contrib"))
    # google-crc32c (an aiortc dependency) loads its DLL from google_crc32c\extra-dll on Windows.
    agent_binaries += collect_dynamic_libs("google_crc32c")
    agent_collection_mode["aiortc.rtcrtpsender"] = "pyz+py"
    # windows-capture (Windows only, MIT): its native module alone (find_spec locates the package without importing
    # its wrapper). A Windows build with Screen view and without it would fail every Desktop Duplication fallback.
    wgc_package = importlib.util.find_spec("windows_capture")
    wgc_folders = list(wgc_package.submodule_search_locations or []) if wgc_package is not None else []
    wgc_native = [path for folder in wgc_folders for path in sorted(Path(folder).glob("windows_capture*.pyd"))]
    if sys.platform == "win32" and not wgc_native:
        raise SystemExit("dome-agent.spec: windows-capture's native module is missing (uv sync --locked --extra screen)")
    agent_binaries += [(str(path), "windows_capture") for path in wgc_native]

agent = Analysis(
    [str(ROOT / "packaging" / "agent_entry.py")],
    pathex=[str(ROOT)],
    binaries=agent_binaries,
    datas=[*CONTRACT_DATAS, (str(ROOT / "dome_agent" / "assets"), "dome_agent/assets")],
    hiddenimports=agent_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["dome_agent.testing", "aiortc.contrib", "cv2", "numpy"],
    noarchive=False,
    module_collection_mode=agent_collection_mode,
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
              "websockets", "httpx", "dome_agent.screen", "aiortc", "aioice", "av", "pylibsrtp", "google_crc32c"],
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

# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for dome-native-host.exe (Chrome/Edge Native Messaging host). Build on Windows:
#   .venv\Scripts\pyinstaller packaging\dome-native-host.spec
# Must be console=False so Chrome does not flash a console window; stdio stays available for the
# native-messaging stream. The installer places it next to DoMe.exe (install-native-host looks there).
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
CONTRACT = ROOT.parent / "shared" / "protocol"

a = Analysis(
    [str(ROOT / "packaging" / "native_host_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(CONTRACT), "dome_protocol/_contract")],
    hiddenimports=["win32pipe", "win32file", "win32security", "win32process", "win32api", "pywintypes"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["dome_agent.testing", "dome_agent.tray", "pystray", "PIL", "tkinter", "winsdk", "pycaw", "comtypes", "psutil", "websockets", "httpx"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="dome-native-host",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

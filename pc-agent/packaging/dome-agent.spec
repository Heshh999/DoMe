# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the DoMe tray agent (Windows). Build on a Windows machine:
#   uv sync --extra build && .venv\Scripts\pyinstaller packaging\dome-agent.spec
# The shared protocol contract is bundled as dome_protocol/_contract (dome_protocol looks there when frozen).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
CONTRACT = ROOT.parent / "shared" / "protocol"

hidden = ["dome_agent.actions.system", "dome_agent.actions.youtube", "dome_agent.actions.media", "dome_agent.actions.volume",
          "dome_agent.actions.windows", "dome_agent.actions.apps", "dome_agent.actions.power",
          "dome_agent.platform.windows.apps", "dome_agent.platform.windows.media", "dome_agent.platform.windows.nativehost",
          "dome_agent.platform.windows.power", "dome_agent.platform.windows.session", "dome_agent.platform.windows.startup",
          "dome_agent.platform.windows.volume", "pystray._win32", "PIL.ImageTk", "tkinter", "win32crypt", "win32pipe", "win32file",
          "win32security", "win32process", "win32api", "win32gui", "win32con", "win32process", "ntsecuritycon", "comtypes", "pycaw.pycaw"]
hidden += collect_submodules("winsdk.windows.media.control")
hidden += collect_submodules("winsdk.windows.foundation")

a = Analysis(
    [str(ROOT / "packaging" / "agent_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(CONTRACT), "dome_protocol/_contract")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["dome_agent.testing"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DoMe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # tray application; `DoMe.exe status` etc. still work from a console via stdout redirection
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DoMe")

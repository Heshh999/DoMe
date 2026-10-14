"""Smoke test of the DoMe installer and of the frozen programs.

    python smoke_test.py bridge --app-dir DIR --version X.Y.Z --log-dir LOGS [--allow-this-pc]
    python smoke_test.py full --setup DoMe-Setup-X.Y.Z.exe --version X.Y.Z --log-dir LOGS [--allow-this-pc]

``bridge`` (Windows or Linux) runs the PyInstaller folder: ``DoMe version``, the native host without an
agent (one ``AGENT_NOT_RUNNING`` frame, exit 1), ``DoMe run --headless`` with a temporary state folder, a
native-messaging ``bridge_hello`` round trip through ``dome-native-host``, and ``DoMe quit``.

``full`` (Windows only) is DESTRUCTIVE: it installs and uninstalls DoMe for the current Windows user,
starts and quits DoMe there, and deletes ``%LOCALAPPDATA%\\DoMe`` (the link, the paired phones and the
test kit's data). It refuses to run outside GitHub Actions unless ``--allow-this-pc`` is given. Phases:
a silent install with start at login and a development extension id; the frozen command line; the
``bridge`` checks against the installed folder; the tray (``DoMe.exe`` alone) and a second launch; an
upgrade over the running tray; an upgrade while DoMe will not quit (Setup must stop and change nothing);
a refused downgrade; a refused ``/ApiUrl=http://``; an uninstall over the running tray that keeps the
data; an install with a staging address and no extension id, an upgrade that keeps that address; an
uninstall with ``/REMOVEDATA``; and an install plus uninstall that must leave no empty state folder.
After every Setup or uninstaller run no process may be left running from the install folder (the process
list itself must be readable and hold this script, or the test fails); after every phase no DoMe program
may have written ``logs\\crash.log``; and the DoMe.exe commands Setup and the uninstaller run must show
their expected exit codes in the logs, because Setup's own fallbacks (deleting the registry keys itself)
would otherwise hide a broken ``uninstall-native-host`` or ``start-at-login off``. The tray check covers
the process only (it keeps running, a second launch is refused, ``DoMe.exe quit`` ends it); the icon, its
menu and the menu's Quit DoMe are not exercised (the runner may have no visible notification area).

Both modes run DoMe.exe and Setup with every ``DOME_AGENT_*`` variable removed, never kill anything but
the processes they started themselves, and write ``smoke-test.log`` plus every Setup, uninstaller and
DoMe log into ``--log-dir``. Exit code 0 when every check passed, 1 otherwise. Standard library only
(plus ``pefile``, when importable, to check the icons inside the programs).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

REPO = Path(__file__).resolve().parents[3]
BRAND_ICON = REPO / "brand" / "exports" / "windows" / "dome.ico"
APP_ID = "{FD238776-147F-4CCF-B2CA-4AC5F30A07FF}"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1"
CHROME_KEY = r"Software\Google\Chrome\NativeMessagingHosts\com.dome.agent"
EDGE_KEY = r"Software\Microsoft\Edge\NativeMessagingHosts\com.dome.agent"
# Parents that `install-native-host` creates when missing (winreg.CreateKeyEx); the uninstaller removes them
# again when they are empty.
HOST_PARENT_KEYS = (r"Software\Google\Chrome\NativeMessagingHosts", r"Software\Microsoft\Edge\NativeMessagingHosts")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
TEST_ID = "abcdefghijklmnopabcdefghijklmnop"
TEST_ORIGIN = f"chrome-extension://{TEST_ID}/"
DEFAULT_API = "https://domeremote.com"
STAGING_API = "https://staging.domeremote.com"
HELLO = {
    "type": "bridge_hello",
    "browser_instance_id": "bi_smoke0001",
    "browser": "edge",
    "extension_version": "0.2.0",
    "protocol_versions": ["1.0", "1.1", "1.2"],
}


class SmokeError(Exception):
    pass


class Report:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        self._file = (log_dir / "smoke-test.log").open("a", encoding="utf-8")
        self.passed = 0

    def line(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp} {text}", flush=True)
        self._file.write(f"{stamp} {text}\n")
        self._file.flush()

    def phase(self, title: str) -> None:
        self.line(f"=== {title}")

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            self.line(f"PASS {name}")
            return
        self.line(f"FAIL {name}" + (f": {detail}" if detail else ""))
        raise SmokeError(name)

    def note(self, text: str) -> None:
        self.line(f"NOTE {text}")

    def close(self) -> None:
        self._file.close()


def exe(app: Path, stem: str) -> Path:
    return app / (f"{stem}.exe" if os.name == "nt" else stem)


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("DOME_AGENT_")}
    env.update(extra)
    return env


def frame(obj: dict[str, Any]) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def parse_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 4:
        raise ValueError(f"no frame (got {len(data)} bytes)")
    (length,) = struct.unpack("<I", data[:4])
    parsed = json.loads(data[4 : 4 + length].decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("frame is not a JSON object")
    return parsed


def run_cli(
    program: Path, args: list[str], env: dict[str, str], cwd: Path | None = None, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    """A DoMe.exe command with its output captured (DoMe.exe is a windowed program: without redirected
    output it would print nothing)."""
    return subprocess.run(
        [str(program), *args],
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )


def status_json(program: Path, env: dict[str, str], cwd: Path | None = None) -> dict[str, Any]:
    out = run_cli(program, ["status", "--json"], env, cwd)
    if out.returncode != 0:
        raise SmokeError(f"status --json exited {out.returncode}: {out.stderr.strip()}")
    parsed = json.loads(out.stdout)
    if not isinstance(parsed, dict):
        raise SmokeError("status --json is not an object")
    return parsed


def wait_until(predicate: Callable[[], bool], timeout: float, poll: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def stop_own(proc: subprocess.Popen[Any] | None, report: Report, what: str) -> None:
    """End a process this script started, if it is still running (that is a failure in itself)."""
    if proc is not None and proc.poll() is None:
        report.line(f"FAIL {what} was still running at the end; this script stops its own process now")
        proc.kill()
        proc.wait(timeout=30)


def read_reply(stream: IO[bytes], timeout: float) -> bytes:
    """One length-prefixed frame from the native host's stdout, read on a thread so a silent host cannot
    hang the test."""
    result: queue.Queue[bytes] = queue.Queue()

    def reader() -> None:
        header = stream.read(4)
        body = stream.read(struct.unpack("<I", header)[0]) if len(header) == 4 else b""
        result.put(header + body)

    threading.Thread(target=reader, daemon=True).start()
    try:
        return result.get(timeout=timeout)
    except queue.Empty:
        raise SmokeError(f"no reply from the native host within {timeout:g} s") from None


# ----- bridge mode ----------------------------------------------------------------------------------------------


def bridge(app: Path, version: str, report: Report, allow_this_pc: bool) -> None:
    dome = exe(app, "DoMe")
    host = exe(app, "dome-native-host")
    # A short relative state folder (Unix socket paths are limited to ~104 bytes on Linux).
    workdir = Path(tempfile.mkdtemp(prefix="dome-smoke-"))
    env = clean_env(DOME_AGENT_STATE_DIR="state")
    report.phase(f"bridge: {app}")
    if status_json(dome, env, workdir).get("agent_running"):
        # On Windows the control pipe belongs to the user session, not to a state folder.
        if not allow_this_pc:
            raise SmokeError("a DoMe agent already runs in this Windows session; quit it or pass --allow-this-pc")
        report.note("a DoMe agent already runs in this session (--allow-this-pc)")
    out = run_cli(dome, ["version"], env, workdir)
    expected = re.compile(rf"dome-agent {re.escape(version)} \(protocol \d+\.\d+, registry \d+\.\d+\)")
    report.check("DoMe version", out.returncode == 0 and bool(expected.fullmatch(out.stdout.strip())), out.stdout)

    alone = subprocess.run([str(host), TEST_ORIGIN], env=env, cwd=workdir, input=b"", capture_output=True, timeout=60)
    first = parse_frame(alone.stdout) if len(alone.stdout) >= 4 else {}
    report.check(
        "native host without an agent: AGENT_NOT_RUNNING, exit 1",
        alone.returncode == 1
        and first.get("type") == "bridge_error"
        and first.get("error", {}).get("code") == "AGENT_NOT_RUNNING",
        f"rc={alone.returncode} frame={first}",
    )

    agent = subprocess.Popen(
        [str(dome), "run", "--headless"],
        env=env,
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    hosted: subprocess.Popen[bytes] | None = None
    try:
        running = wait_until(lambda: bool(status_json(dome, env, workdir).get("agent_running")), 90)
        report.check("DoMe run --headless answers status", running)
        status = status_json(dome, env, workdir)
        report.check(
            "status: version and not linked",
            status.get("agent_version") == version and status.get("identity", {}).get("linked") is False,
            json.dumps(status)[:500],
        )
        hosted = subprocess.Popen(
            [str(host), TEST_ORIGIN], env=env, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        assert hosted.stdin is not None and hosted.stdout is not None
        hosted.stdin.write(frame(HELLO))
        hosted.stdin.flush()
        ack = parse_frame(read_reply(hosted.stdout, 30))
        report.check(
            "bridge_hello answered by bridge_hello_ack",
            ack.get("type") == "bridge_hello_ack"
            and ack.get("protocol_version") in HELLO["protocol_versions"]
            and ack.get("agent_version") == version,
            json.dumps(ack),
        )
        hosted.stdin.close()
        report.check("native host exits 0 when the browser closes it", hosted.wait(timeout=30) == 0)
        out = run_cli(dome, ["quit", "--timeout", "30"], env, workdir)
        report.check("DoMe quit", out.returncode == 0, out.stdout + out.stderr)
        report.check("the agent exited 0", agent.wait(timeout=30) == 0)
        report.check("status: not running", status_json(dome, env, workdir).get("agent_running") is False)
        crash = workdir / "state" / "logs" / "crash.log"
        report.check(
            "bridge: no crash.log",
            not crash.exists(),
            crash.read_text(errors="replace")[-2000:] if crash.exists() else "",
        )
    finally:
        stop_own(hosted, report, "the native host")
        stop_own(agent, report, "DoMe run --headless")
        logs = workdir / "state" / "logs"
        if logs.is_dir():
            target = report.log_dir / "bridge-state-logs"
            target.mkdir(parents=True, exist_ok=True)
            for log in logs.glob("*.log*"):
                shutil.copy2(log, target / log.name)
        shutil.rmtree(workdir, ignore_errors=True)


# ----- Windows helpers (full mode) -------------------------------------------------------------------------------


def reg_value(key: str, name: str | None) -> str | None:
    """A HKCU string value (``name=None``: the default value); None when the key or value is missing."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name or "")
    except FileNotFoundError:
        return None
    return str(value)


def reg_has_value(key: str, name: str) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return False
    return True


def reg_key_exists(key: str) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, key))
    except FileNotFoundError:
        return False
    return True


def reg_set(key: str, name: str, value: str | bytes) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
        if isinstance(value, bytes):
            winreg.SetValueEx(handle, name, 0, winreg.REG_BINARY, value)
        else:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],  # noqa: S607 - on every Windows 10/11
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )


def processes() -> list[dict[str, Any]]:
    """Every process on the runner. Raises SmokeError when the list cannot be trusted (PowerShell failed, or
    the list does not even hold this script), so a check built on it can never pass by seeing nothing."""
    out = powershell(
        "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    detail = f"exit code {out.returncode}; stderr: {out.stderr.strip()[:500]}"
    if out.returncode != 0:
        raise SmokeError(f"the process list could not be read ({detail})")
    try:
        parsed = json.loads(out.stdout.strip() or "[]")
    except ValueError as exc:
        raise SmokeError(f"the process list is not JSON ({exc}; {detail})") from None
    found = [parsed] if isinstance(parsed, dict) else list(parsed) if isinstance(parsed, list) else []
    if not any(isinstance(p, dict) and p.get("ProcessId") == os.getpid() for p in found):
        raise SmokeError(
            f"the process list ({len(found)} entries) does not hold this script (pid {os.getpid()}); {detail}"
        )
    return [p for p in found if isinstance(p, dict)]


def under(path: str | None, folder: Path) -> bool:
    return bool(path) and os.path.normcase(str(path)).startswith(os.path.normcase(str(folder)) + os.sep)


def shortcut_target(lnk: Path) -> str:
    escaped = str(lnk).replace("'", "''")
    return powershell(f"(New-Object -ComObject WScript.Shell).CreateShortcut('{escaped}').TargetPath").stdout.strip()


def agent_mutex_name() -> str:
    """pc-agent/dome_agent/single_instance.py: ``Local\\DoMe.Agent.<session id>``."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    session = ctypes.c_ulong(0)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
        return f"Local\\DoMe.Agent.{session.value}"
    return "Local\\DoMe.Agent.unknown"


@contextmanager
def hold_agent_mutex() -> Iterator[None]:
    """Pretend that a DoMe agent runs in this session but never answers (the lock without the control
    channel): ``DoMe.exe quit`` then fails, and Setup must stop without changing anything."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, agent_mutex_name())
    if not handle or ctypes.get_last_error() == 183:
        raise SmokeError("could not create the agent mutex (is a DoMe agent running?)")
    try:
        yield
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def ico_images(path: Path) -> list[bytes]:
    data = path.read_bytes()
    _reserved, _kind, count = struct.unpack_from("<HHH", data, 0)
    images = []
    for i in range(count):
        size, offset = struct.unpack_from("<II", data, 6 + 16 * i + 8)
        images.append(data[offset : offset + size])
    return images


def pe_icon_resources(path: Path) -> set[bytes] | None:
    """The RT_ICON resources of a program, or None when pefile is not available. The file is read into
    memory first, so no handle stays open on it (Setup's Restart Manager would see one)."""
    try:
        import pefile
    except ImportError:
        return None
    pe = pefile.PE(data=path.read_bytes(), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    found: set[bytes] = set()
    resources = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    for entry in resources.entries if resources is not None else []:
        if entry.id != 3:  # RT_ICON
            continue
        for icon in entry.directory.entries:
            for lang in icon.directory.entries:
                found.add(bytes(pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)))
    pe.close()
    return found


# ----- full mode --------------------------------------------------------------------------------------------------


class Machine:
    """The current Windows user's DoMe paths, and running Setup / the uninstaller."""

    def __init__(self, setup: Path, version: str, report: Report) -> None:
        local = Path(os.environ["LOCALAPPDATA"])
        self.app = local / "Programs" / "DoMe"
        self.state = local / "DoMe"
        self.lnk = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "DoMe.lnk"
        self.setup = setup
        self.version = version
        self.report = report
        self.env = clean_env()
        self.own: list[subprocess.Popen[Any]] = []
        self.parents_before: dict[str, bool] = {}

    @property
    def dome(self) -> Path:
        return self.app / "DoMe.exe"

    @property
    def marker(self) -> Path:
        """A file this test puts into the installed runtime folder: Setup deletes ``{app}\\_internal`` before
        it copies a new version, so the marker shows whether Setup changed the installation. (File times
        cannot: Setup gives every file the time stored in the installer.)"""
        return self.app / "_internal" / "smoke-test-marker.txt"

    def own_live_pids(self) -> set[int]:
        return {p.pid for p in self.own if p.poll() is None}

    def stray_processes(self) -> list[str]:
        mine = self.own_live_pids()
        return [
            f"{p.get('ProcessId')} {p.get('ExecutablePath')}"
            for p in processes()
            if under(p.get("ExecutablePath"), self.app) and p.get("ProcessId") not in mine
        ]

    def run_setup(self, name: str, *args: str) -> tuple[int, str]:
        log = self.report.log_dir / f"{name}.log"
        cmd = [str(self.setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/LOG={log}", *args]
        self.report.line("+ " + " ".join(cmd))
        # No pipes: Setup's children (DoMe.exe) could inherit them and keep this call waiting.
        rc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300
        ).returncode
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        self.report.line(f"Setup exit code {rc}")
        time.sleep(2)
        stray = self.stray_processes()
        self.report.check(f"{name}: no process left running from the install folder", not stray, "; ".join(stray))
        return rc, text

    def run_uninstall(self, name: str, *args: str) -> tuple[int, str]:
        log = self.report.log_dir / f"{name}.log"
        cmd = [str(self.app / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}", *args]
        self.report.line("+ " + " ".join(cmd))
        rc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300
        ).returncode
        self.report.line(f"uninstaller exit code {rc}")
        if rc == 0:
            # the uninstaller's copy in %TEMP% may still be removing files when the exit code comes back
            gone = wait_until(lambda: not self.app.exists(), 120)
            self.report.check(f"{name}: the install folder disappears", gone, str(list(self.app.rglob("*"))[:20]))
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        stray = self.stray_processes()
        self.report.check(f"{name}: no process left running from the install folder", not stray, "; ".join(stray))
        return rc, text

    def start_tray(self, label: str) -> subprocess.Popen[bytes]:
        """``DoMe.exe`` alone: the tray, with the default state folder (what the Start menu starts)."""
        proc = subprocess.Popen(
            [str(self.dome)],
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.own.append(proc)
        running = wait_until(lambda: bool(status_json(self.dome, self.env).get("agent_running")), 90)
        self.report.check(
            f"{label}: the tray is running (pid {proc.pid})",
            running and proc.poll() is None,
            f"exit code {proc.poll()}",
        )
        return proc

    def server_address(self) -> str:
        path = self.app / "dome-server.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return "<missing>"
        return str(data.get("api_url")) if isinstance(data, dict) else "<invalid>"

    def check_installed(self, host: bool, run: bool, api: str) -> None:
        r = self.report
        for rel in (
            "DoMe.exe",
            "dome-native-host.exe",
            "unins000.exe",
            r"_internal\dome_protocol\_contract\actions.json",
        ):
            r.check(f"installed: {rel}", (self.app / rel).exists())
        r.check("installed: no test fixtures", not (self.app / r"_internal\dome_protocol\_contract\fixtures").exists())
        r.check(
            "Start menu shortcut points at DoMe.exe",
            os.path.normcase(shortcut_target(self.lnk)) == os.path.normcase(str(self.dome)),
            shortcut_target(self.lnk),
        )
        r.check(
            "uninstall entry",
            reg_value(UNINSTALL_KEY, "DisplayVersion") == self.version
            and reg_value(UNINSTALL_KEY, "DisplayName") == "DoMe",
            f"{reg_value(UNINSTALL_KEY, 'DisplayVersion')} {reg_value(UNINSTALL_KEY, 'DisplayName')}",
        )
        r.check(f"server address {api}", self.server_address() == api, self.server_address())
        for name in (
            "DOME_AGENT_API_URL",
            "DOME_AGENT_PLATFORM",
            "DOME_AGENT_DEV_EXTENSION_ID",
            "DOME_AGENT_STATE_DIR",
        ):
            r.check(f"no {name} in the user environment", reg_value("Environment", name) is None)
        manifest_path = self.state / "com.dome.agent.json"
        if host:
            for key in (CHROME_KEY, EDGE_KEY):
                value = reg_value(key, None)
                r.check(
                    f"HKCU\\{key} -> the manifest",
                    value is not None and os.path.normcase(value) == os.path.normcase(str(manifest_path)),
                    str(value),
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            r.check(
                "manifest: name, type, path, origins",
                manifest.get("name") == "com.dome.agent"
                and manifest.get("type") == "stdio"
                and os.path.normcase(str(manifest.get("path")))
                == os.path.normcase(str(self.app / "dome-native-host.exe"))
                and Path(str(manifest.get("path"))).exists()
                and manifest.get("allowed_origins") == [TEST_ORIGIN],
                json.dumps(manifest),
            )
        else:
            r.check("browser bridge not registered", not reg_key_exists(CHROME_KEY) and not reg_key_exists(EDGE_KEY))
        run_value = reg_value(RUN_KEY, "DoMe")
        if run:
            r.check(
                "start at login",
                run_value is not None and os.path.normcase(run_value) == os.path.normcase(f'"{self.dome}" run'),
                str(run_value),
            )
        else:
            r.check("no start at login", run_value is None, str(run_value))

    def check_icons(self) -> None:
        images = ico_images(BRAND_ICON)
        for name in ("DoMe.exe", "dome-native-host.exe"):
            resources = pe_icon_resources(self.app / name)
            if resources is None:
                self.report.note("pefile is not installed: the icon check is skipped")
                return
            missing = sum(1 for image in images if image not in resources)
            self.report.check(f"{name} carries the {len(images)} brand icon images", missing == 0, f"{missing} missing")

    def check_removed(self) -> None:
        r = self.report
        r.check("removed: install folder", not self.app.exists())
        r.check("removed: uninstall entry", not reg_key_exists(UNINSTALL_KEY))
        r.check("removed: browser bridge keys", not reg_key_exists(CHROME_KEY) and not reg_key_exists(EDGE_KEY))
        r.check(
            "removed: start at login", not reg_has_value(RUN_KEY, "DoMe") and not reg_has_value(APPROVED_KEY, "DoMe")
        )
        r.check("removed: Start menu shortcut", not self.lnk.exists())
        r.check("removed: native host manifest", not (self.state / "com.dome.agent.json").exists())
        for key in HOST_PARENT_KEYS:
            if not self.parents_before.get(key, True):
                r.check(f"removed: the empty HKCU\\{key} that the install created", not reg_key_exists(key))

    def check_no_crash(self, label: str) -> None:
        """No DoMe program crashed so far: Setup's fallbacks (deleting registry keys itself) would hide a
        crashing ``uninstall-native-host`` or ``start-at-login off`` everywhere else."""
        crash = self.state / "logs" / "crash.log"
        detail = crash.read_text(encoding="utf-8", errors="replace")[-2000:] if crash.exists() else ""
        self.report.check(f"{label}: no crash.log", not crash.exists(), detail)

    def check_uninstall_commands(self, name: str, log: str) -> None:
        """The uninstaller's own DoMe.exe commands worked (not only its registry fallbacks)."""
        for command in ("uninstall-native-host", "start-at-login off"):
            self.report.check(
                f"{name}: DoMe.exe {command} exited with code 0",
                f"DoMe.exe {command} exited with code 0" in log,
            )

    def wer_reports(self, pid: int) -> list[str]:
        return [
            str(p.get("CommandLine"))
            for p in processes()
            if str(p.get("Name", "")).lower() == "werfault.exe" and f"-p {pid}" in str(p.get("CommandLine"))
        ]


def full(setup: Path, version: str, report: Report) -> None:
    if sys.platform != "win32":
        raise SmokeError("full mode runs on Windows only")
    m = Machine(setup, version, report)
    try:
        _full_phases(m, report)
    finally:
        for proc in m.own:
            stop_own(proc, report, f"process {proc.pid}")


def _full_phases(m: Machine, report: Report) -> None:
    version = m.version
    report.phase("0 preflight: nothing of DoMe on this runner")
    report.check("no install folder", not m.app.exists())
    report.check("no uninstall entry", not reg_key_exists(UNINSTALL_KEY))
    report.check("no browser bridge keys", not reg_key_exists(CHROME_KEY) and not reg_key_exists(EDGE_KEY))
    report.check("no start at login", not reg_has_value(RUN_KEY, "DoMe"))
    report.check("no state folder", not m.state.exists())
    m.parents_before = {key: reg_key_exists(key) for key in HOST_PARENT_KEYS}
    for key, existed in m.parents_before.items():
        if existed:
            report.note(f"HKCU\\{key} exists before the test: the empty-parent check is skipped for it")
    explorer = any(str(p.get("Name", "")).lower() == "explorer.exe" for p in processes())
    report.note(
        f"notification area (explorer.exe running): {'yes' if explorer else 'NO - the tray icon cannot be seen here'}"
    )

    report.phase("1 install: start at login, development extension id")
    rc, log = m.run_setup("1-install", "/MERGETASKS=startatlogin", f"/ExtensionId={TEST_ID}")
    # The runner has UAC off, so Setup's token is not "elevated through UAC" and Setup may run DoMe.exe;
    # if this ever fails, Setup refused to install (DoMe.iss ElevatedThroughUac) and nothing below can pass.
    elevation = re.search(r"Administrator: \d; elevation type: \d+; elevated through UAC: \d", log)
    report.check(
        "Setup does not count as elevated through UAC here",
        elevation is not None and elevation.group(0).endswith(": 0"),
        elevation.group(0) if elevation else "no elevation line in the Setup log",
    )
    report.check("Setup exit code 0", rc == 0, str(rc))
    m.check_installed(host=True, run=True, api=DEFAULT_API)
    m.check_icons()
    m.check_no_crash("1")

    report.phase("2 the installed command line")
    out = run_cli(m.dome, ["version"], m.env)
    report.check(
        "DoMe.exe version", out.returncode == 0 and out.stdout.startswith(f"dome-agent {version} "), out.stdout
    )
    out = run_cli(m.dome, ["start-at-login", "status"], m.env)
    report.check("DoMe.exe start-at-login status: on", out.returncode == 0 and out.stdout.strip() == "on", out.stdout)
    status = status_json(m.dome, m.env)
    report.check(
        "DoMe.exe status --json",
        status.get("agent_running") is False
        and status.get("agent_version") == version
        and status.get("identity", {}).get("linked") is False,
        json.dumps(status)[:500],
    )
    report.check("state.sqlite3 created", (m.state / "state.sqlite3").exists())
    m.check_no_crash("2")

    bridge(m.app, version, report, allow_this_pc=False)

    report.phase("4 the tray (DoMe.exe alone) and a second launch")
    tray = m.start_tray("tray")
    out = run_cli(m.dome, [], m.env)
    report.check(
        "second launch: exit 0, 'already running'",
        out.returncode == 0 and "already running" in out.stdout,
        out.stdout + out.stderr,
    )
    m.check_no_crash("4")

    report.phase("5 upgrade over the running tray")
    m.marker.write_text("installed by phase 1", encoding="ascii")
    rc, log = m.run_setup("5-upgrade")
    report.check("Setup exit code 0", rc == 0, str(rc))
    report.check(
        "Setup quit DoMe first",
        "Running DoMe.exe quit" in log and "DoMe.exe quit --timeout 20 exited with code 0" in log,
    )
    try:
        tray_rc: int | None = tray.wait(timeout=60)
    except subprocess.TimeoutExpired:
        tray_rc = None
    report.check("the tray quit cleanly (exit 0)", tray_rc == 0, str(tray_rc))
    report.check("no Windows error report for the tray", not m.wer_reports(tray.pid))
    report.check("the old runtime folder was replaced", not m.marker.exists())
    m.check_installed(host=True, run=True, api=DEFAULT_API)
    report.check("state.sqlite3 kept", (m.state / "state.sqlite3").exists())
    m.check_no_crash("5")

    report.phase("6 upgrade while DoMe does not quit: Setup must change nothing")
    m.marker.write_text("installed by phase 5", encoding="ascii")
    with hold_agent_mutex():
        rc, log = m.run_setup("6-not-quitting")
    report.check("Setup stops (exit code 7)", rc == 7, str(rc))
    report.check("the log says DoMe is still running", "DoMe is still running in this session" in log)
    report.check("the installation is unchanged", m.marker.exists() and m.dome.exists())
    # The keys alone prove nothing: if `uninstall-native-host` had failed they would never have gone away.
    report.check(
        "Setup unregistered the browser bridge first (DoMe.exe uninstall-native-host exited with code 0)",
        "DoMe.exe uninstall-native-host exited with code 0" in log,
    )
    again = log.find("registering the browser bridge of the installed DoMe again")
    report.check(
        "Setup registered the browser bridge again (then DoMe.exe install-native-host exited with code 0)",
        again >= 0 and log.find("DoMe.exe install-native-host exited with code 0", again) > again,
    )
    report.check("browser bridge registered again", reg_key_exists(CHROME_KEY) and reg_key_exists(EDGE_KEY))
    m.check_no_crash("6")

    report.phase("7 downgrade refused")
    reg_set(UNINSTALL_KEY, "DisplayVersion", "99.0.0")
    try:
        rc, log = m.run_setup("7-downgrade")
    finally:
        reg_set(UNINSTALL_KEY, "DisplayVersion", version)
    report.check("Setup refuses", rc != 0 and "Refusing to downgrade" in log, str(rc))
    report.check("the installation is unchanged", m.marker.exists())
    m.check_no_crash("7")

    report.phase("8 /ApiUrl=http:// refused")
    rc, log = m.run_setup("8-insecure-url", "/ApiUrl=http://insecure.example.test")
    report.check("Setup refuses", rc != 0 and "Refused /ApiUrl" in log, str(rc))
    report.check("server address unchanged", m.server_address() == DEFAULT_API, m.server_address())
    m.check_no_crash("8")

    report.phase("9 uninstall over the running tray, keeping the data")
    tray = m.start_tray("tray before uninstall")
    reg_set(APPROVED_KEY, "DoMe", bytes([3]) + bytes(11))  # what Task Manager writes when it disables an entry
    rc, log = m.run_uninstall("9-uninstall")
    report.check("uninstaller exit code 0", rc == 0, str(rc))
    report.check("the uninstaller quit DoMe", "DoMe.exe quit --timeout 20 exited with code 0" in log)
    m.check_uninstall_commands("9-uninstall", log)
    try:
        tray_rc = tray.wait(timeout=60)
    except subprocess.TimeoutExpired:
        tray_rc = None
    report.check("the tray quit cleanly (exit 0)", tray_rc == 0, str(tray_rc))
    report.check("no Windows error report for the tray", not m.wer_reports(tray.pid))
    m.check_removed()
    report.check("data kept: state.sqlite3", (m.state / "state.sqlite3").exists())
    m.check_no_crash("9")

    report.phase("10 install with a staging address and no extension id")
    rc, log = m.run_setup("10-staging", f"/ApiUrl={STAGING_API}/")
    report.check("Setup exit code 0", rc == 0, str(rc))
    m.check_installed(host=False, run=False, api=STAGING_API)
    report.check("the log says the bridge was skipped", "Browser bridge not registered" in log)
    # skipped for the intended reason, not because DoMe.exe crashed (70), hung (-2) or could not start (-1)
    report.check(
        "DoMe.exe install-native-host exited with code 1", "DoMe.exe install-native-host exited with code 1" in log
    )
    report.check(
        "DoMe.exe said why: no extension id available",
        re.search(r"DoMe\.exe: .*no extension id available", log) is not None,
    )
    report.check("data kept: state.sqlite3", (m.state / "state.sqlite3").exists())
    m.check_no_crash("10")

    report.phase("11 an upgrade keeps the staging address")
    rc, _log = m.run_setup("11-keeps-address")
    report.check("Setup exit code 0", rc == 0, str(rc))
    report.check("server address kept", m.server_address() == STAGING_API, m.server_address())
    m.check_no_crash("11")

    report.phase("12 uninstall with /REMOVEDATA")
    m.check_no_crash("before 12")  # /REMOVEDATA deletes the logs folder
    rc, log = m.run_uninstall("12-removedata", "/REMOVEDATA")
    report.check("uninstaller exit code 0", rc == 0, str(rc))
    m.check_uninstall_commands("12-removedata", log)
    m.check_removed()
    report.check("state folder removed", not m.state.exists())

    report.phase("13 install and uninstall without ever starting DoMe: no empty state folder")
    rc, _log = m.run_setup("13-install")
    report.check("Setup exit code 0", rc == 0, str(rc))
    rc, log = m.run_uninstall("13-uninstall")
    report.check("uninstaller exit code 0", rc == 0, str(rc))
    m.check_uninstall_commands("13-uninstall", log)
    m.check_removed()
    report.check(
        "no state folder left behind",
        not m.state.exists(),
        str(list(m.state.rglob("*"))[:20]) if m.state.exists() else "",
    )


def copy_logs(report: Report) -> None:
    """DoMe's own logs (agent.log, native-host.log, crash.log) next to Setup's /LOG files."""
    local = os.environ.get("LOCALAPPDATA")
    if local and Path(local, "DoMe", "logs").is_dir():
        target = report.log_dir / "dome-logs"
        target.mkdir(parents=True, exist_ok=True)
        for log in Path(local, "DoMe", "logs").glob("*.log*"):
            shutil.copy2(log, target / log.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test of the DoMe installer and frozen programs")
    sub = parser.add_subparsers(dest="mode", required=True)
    b = sub.add_parser("bridge", help="run the PyInstaller folder: version, native messaging, quit")
    b.add_argument("--app-dir", type=Path, required=True)
    f = sub.add_parser("full", help="DESTRUCTIVE: install, check, upgrade and uninstall DoMe for this user")
    f.add_argument("--setup", type=Path, required=True)
    for p in (b, f):
        p.add_argument("--version", required=True)
        p.add_argument("--log-dir", type=Path, required=True)
        p.add_argument("--allow-this-pc", action="store_true", help="run even outside GitHub Actions")
    args = parser.parse_args(argv)

    report = Report(args.log_dir)
    try:
        if args.mode == "bridge":
            bridge(args.app_dir.resolve(), args.version, report, args.allow_this_pc)
        else:
            if os.environ.get("GITHUB_ACTIONS") != "true" and not args.allow_this_pc:
                report.line(
                    "REFUSED: full mode installs and uninstalls DoMe for this Windows user and deletes "
                    "%LOCALAPPDATA%\\DoMe (your link, paired phones and the test kit's data). It runs on GitHub "
                    "Actions; pass --allow-this-pc to run it here anyway."
                )
                return 1
            full(args.setup.resolve(), args.version, report)
    except SmokeError as exc:
        report.line(f"SMOKE TEST FAILED at: {exc}")
        if args.mode == "full":
            copy_logs(report)
        report.close()
        return 1
    except Exception as exc:  # noqa: BLE001 - report every unexpected error as a failure, with the logs
        report.line(f"SMOKE TEST ERROR: {exc.__class__.__name__}: {exc}")
        if args.mode == "full":
            copy_logs(report)
        report.close()
        raise
    report.line(f"ALL {report.passed} CHECKS PASSED")
    if args.mode == "full":
        copy_logs(report)
    report.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Local approval of applications (spec §10, design → ``approved_apps``).

An approval maps a stable ``app_id`` to a validated executable identity. The entry is created only
on the PC (CLI or tray). Remote commands carry ``app_id`` and nothing else; the agent never accepts
a path, an argument, a script or shell text over the wire, so the only place a path enters the
system is :func:`validate_executable_path` below.

Validation rules (all enforced before anything is stored):

* the path is absolute, exists, is a regular file and ends in ``.exe`` (case-insensitive);
* it is not inside a temporary directory (``%TEMP%``, ``%TMP%``, ``tempfile.gettempdir()``,
  the Windows ``Temp`` folders);
* it contains none of the characters an argument or shell string would (quotes, ``&|<>^%;``, and
  whitespace followed by ``-`` or ``/``), so ``C:\\app.exe --flag`` can never be approved;
* the SHA-256 of the file is recorded; :meth:`ApprovedApps.resolve_for_launch` refuses to launch an
  executable whose hash changed since approval (the user re-approves after an update);
* an ``app_id`` keeps the program it was approved for: approving the same file again (after an update)
  is fine, but pointing a taken ``app_id`` at another file is refused (:class:`AppIdTakenError`) unless the
  caller asks to replace it (the tray's *Find it again…*, ``dome-agent approve-app --replace``).

Every refusal carries a ``code`` (:class:`ApprovalError`). The rest of the module holds the pure helpers
behind the tray's *Approved apps* window (:mod:`dome_agent.tray`): the ``app_id`` and name suggested for a
picked program, plain-words explanations of each refusal, warnings for programs that would be approved but
not work from the phone (Microsoft Store apps, updaters, launcher stubs) and each approval's current status.
Windows paths are compared with :mod:`ntpath`, so these behave the same in the Linux tests as on the PC.

*Full screen with F11* (``fullscreen_f11``) is the PC owner's per-app choice, stored with the approval and
changed only on the PC (the tray, ``dome-agent app-option``): F11 is pressed for full screen in an app on the
built-in F11 list (:func:`f11_built_in`) or in one the owner turned it on for, never in any other app, because
elsewhere F11 can mean something else (Excel: a new chart sheet). Re-approving keeps the choice.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import re
import sys
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dome_protocol import ProtocolError

from .logsetup import get_logger
from .platform.protocol import f11_toggles_fullscreen
from .store import ApprovedAppRow, Store

log = get_logger(__name__)

APP_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
APP_ID_MAX = 64
DISPLAY_NAME_MAX = 64
_FORBIDDEN_CHARS = set("\"'&|<>^%;`$*?")
_ARGUMENT_RE = re.compile(r"\s[-/]")  # whitespace followed by an option marker
_HASH_CHUNK = 1024 * 1024
_ID_JUNK = re.compile(r"[^a-z0-9_]+")
_NAME_SEPARATOR = re.compile(r"\s+[:\u2013\u2014-]\s+")  # "Notepad++ : a free (GNU) source code editor"
_GENERIC_NAMES = frozenset(
    {"app", "application", "bootstrapper", "electron", "installer", "launcher", "setup", "stub", "update", "updater"}
)
_VERSION_BLOCKS = ("040904B0", "040904E4", "04090000")  # en-US Unicode / Windows-1252 / neutral: fallbacks
# System32 programs that Windows 10/11 hand over to a Microsoft Store app: the window then belongs to another
# program, so the phone can open them but not find their window to focus, maximise or close it.
_STORE_HANDOFF = frozenset({"calc.exe", "mspaint.exe", "notepad.exe", "snippingtool.exe"})


class ApprovalError(ValueError):
    """The path cannot be approved; the message is safe to show to the local user.

    ``code`` names the rule that refused it, so the tray can explain it in plain words
    (:func:`explain_approval_error`); ``str(exc)`` is unchanged for the CLI and the control channel."""

    def __init__(self, message: str, *, code: str = "INVALID") -> None:
        super().__init__(message)
        self.code = code


class AppIdTakenError(ApprovalError):
    """Approving would silently point an ``app_id`` that is already approved at a different program."""

    def __init__(self, row: ApprovedAppRow) -> None:
        super().__init__(
            f"'{row.app_id}' is already approved for {row.exe_path}; remove it first or replace it explicitly "
            "(dome-agent approve-app --replace)",
            code="APP_ID_TAKEN",
        )
        self.row = row


@dataclass(frozen=True, slots=True)
class ValidatedExecutable:
    path: str
    sha256: str


def default_temp_dirs() -> tuple[Path, ...]:
    candidates: list[Path] = []
    for var in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(var)
        if value:
            candidates.append(Path(value))
    candidates.append(Path(tempfile.gettempdir()))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Temp")
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        candidates.append(Path(windir) / "Temp")
    out: list[Path] = []
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            resolved = c
        if resolved not in out:
            out.append(resolved)
    return tuple(out)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_app_id(app_id: str) -> str:
    if not isinstance(app_id, str) or not APP_ID_RE.match(app_id):
        raise ApprovalError(
            "app_id must be 1-64 characters: lowercase letters, digits, '_' or '-', starting with a letter or digit",
            code="BAD_APP_ID",
        )
    return app_id


def validate_executable_path(raw: str, *, temp_dirs: Iterable[Path] | None = None) -> ValidatedExecutable:
    if not isinstance(raw, str) or not raw.strip():
        raise ApprovalError("an executable path is required", code="NO_PATH")
    if any(ord(ch) < 32 for ch in raw):  # newlines, tabs and other control characters
        raise ApprovalError("the path may not contain control characters", code="CONTROL_CHARS")
    text = raw.strip(" ")
    if any(ch in _FORBIDDEN_CHARS for ch in text) or _ARGUMENT_RE.search(text):
        raise ApprovalError(
            "the path may not contain arguments, quotes or shell characters; approve the executable file only",
            code="SHELL_CHARS",
        )
    path = Path(text)
    if not path.is_absolute():
        raise ApprovalError("the executable path must be absolute", code="NOT_ABSOLUTE")
    if path.suffix.lower() != ".exe":
        raise ApprovalError("only .exe files can be approved", code="NOT_EXE")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ApprovalError("the executable does not exist", code="NOT_FOUND") from exc
    if not resolved.is_file():
        raise ApprovalError("the executable path is not a file", code="NOT_FILE")
    if resolved.suffix.lower() != ".exe":
        raise ApprovalError("only .exe files can be approved", code="NOT_EXE")
    dirs = tuple(temp_dirs) if temp_dirs is not None else default_temp_dirs()
    for temp_dir in dirs:
        if _is_under(resolved, temp_dir):
            raise ApprovalError("executables inside temporary folders cannot be approved", code="TEMP_DIR")
    try:
        digest = sha256_file(resolved)
    except OSError as exc:
        raise ApprovalError("the executable could not be read", code="UNREADABLE") from exc
    return ValidatedExecutable(path=str(resolved), sha256=digest)


class ApprovedApps:
    """Store-backed approval list used by the CLI/tray (writes) and the app handlers (reads)."""

    def __init__(self, store: Store, *, temp_dirs: Iterable[Path] | None = None) -> None:
        self._store = store
        self._temp_dirs = tuple(temp_dirs) if temp_dirs is not None else None
        # The tray and the control channel approve from worker threads: the "is this id taken" check and
        # the write happen together.
        self._lock = threading.Lock()

    def approve(
        self,
        app_id: str,
        exe_path: str,
        display_name: str | None = None,
        *,
        replace: bool = False,
        fullscreen_f11: bool | None = None,
    ) -> ApprovedAppRow:
        """Validate, hash and store. Hashes the whole file, so callers on the event loop or the Tk thread run
        it in a worker thread. Approving the file an ``app_id`` already has (after an update) refreshes it;
        another file under a taken ``app_id`` raises :class:`AppIdTakenError` unless ``replace`` is set.
        ``fullscreen_f11`` None keeps a re-approved app's *Full screen with F11* choice (off for a new one)."""
        app_id = validate_app_id(app_id)
        exe = validate_executable_path(exe_path, temp_dirs=self._temp_dirs)
        name = clean_display_name(display_name or Path(exe.path).stem) or app_id
        with self._lock:
            existing = self._store.get_approved_app(app_id)
            if existing is not None and not replace and not same_program(existing.exe_path, exe.path):
                raise AppIdTakenError(existing)
            row = self._store.add_approved_app(app_id, name, exe.path, exe.sha256, fullscreen_f11=fullscreen_f11)
        log.info(
            "application approved locally", app_id=app_id, again=existing is not None, fullscreen_f11=row.fullscreen_f11
        )
        return row

    def set_fullscreen_f11(self, app_id: str, enabled: bool) -> ApprovedAppRow | None:
        """Turn *Full screen with F11* on or off for an approved app; None when ``app_id`` is not approved."""
        row = self._store.set_approved_app_fullscreen_f11(validate_app_id(app_id), bool(enabled))
        if row is not None:
            log.info("full screen with F11 changed locally", app_id=app_id, enabled=row.fullscreen_f11)
        return row

    def remove(self, app_id: str) -> bool:
        removed = self._store.remove_approved_app(validate_app_id(app_id))
        if removed:
            log.info("application approval removed", app_id=app_id)
        return removed

    def list(self) -> list[ApprovedAppRow]:
        return self._store.list_approved_apps()

    def get(self, app_id: str) -> ApprovedAppRow | None:
        if not APP_ID_RE.match(app_id):
            return None
        return self._store.get_approved_app(app_id)

    def statuses(self) -> dict[str, str]:
        """``{app_id: status}`` (:func:`app_status`) for the tray's list. Hashes every approved file: run it in
        a worker thread."""
        return {row.app_id: app_status(row) for row in self.list()}

    def resolve_for_launch(self, app_id: str) -> ApprovedAppRow:
        """Return the approval only if the executable on disk still matches the approved identity."""
        row = self.get(app_id)
        if row is None:
            raise ProtocolError(
                "APP_NOT_APPROVED", "That app is not approved for remote control. Approve it on the PC first."
            )
        path = Path(row.exe_path)
        if not path.is_file():
            raise ProtocolError(
                "APP_NOT_APPROVED", "The approved executable no longer exists. Approve it again on the PC."
            )
        try:
            current = sha256_file(path)
        except OSError as exc:
            raise ProtocolError("APP_LAUNCH_FAILED", "The approved executable could not be read.") from exc
        if current != row.exe_sha256:
            self._store.add_security_event("approved_app_hash_changed", app_id=app_id)
            raise ProtocolError(
                "APP_NOT_APPROVED",
                "The application changed since it was approved (an update?). Approve it again on the PC.",
            )
        return row


def app_status(row: ApprovedAppRow, *, hasher: Callable[[Path], str] = sha256_file) -> str:
    """``ok`` | ``changed`` (the file's hash differs: an update; re-approve) | ``missing`` | ``unreadable``.
    The same identity check as :meth:`ApprovedApps.resolve_for_launch`, without recording a security event."""
    path = Path(row.exe_path)
    if not path.is_file():
        return "missing"
    try:
        return "ok" if hasher(path) == row.exe_sha256 else "changed"
    except OSError:
        return "unreadable"


# ----- the tray's Add an app… helpers (pure; no tkinter) ----------------------------------------------------------
def _path_key(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def _under(path: str, folder: str) -> bool:
    return _path_key(path).startswith(_path_key(folder).rstrip("\\") + "\\")


def _env(env: Mapping[str, str], name: str) -> str:
    # os.environ on Windows ignores case, a copied dict (or a test's) does not
    return env.get(name) or env.get(name.upper()) or ""


def same_program(a: str, b: str) -> bool:
    """Two Windows paths name the same file (case and ``/`` versus ``\\`` ignored)."""
    return _path_key(a) == _path_key(b)


def f11_built_in(exe_path: str) -> bool:
    """The program is on the built-in F11 list (browsers, File Explorer, VS Code, ...): DoMe presses F11 for full
    screen in it whatever its *Full screen with F11* choice says."""
    return f11_toggles_fullscreen(ntpath.basename(exe_path))


def find_approved_by_path(rows: Iterable[ApprovedAppRow], exe_path: str) -> ApprovedAppRow | None:
    return next((r for r in rows if same_program(r.exe_path, exe_path)), None)


def normalise_picked_path(picked: object, *, windows: bool = sys.platform == "win32") -> str:
    """The file dialog's answer as a path: "" when cancelled (Tk returns "" or an empty tuple), and Tk's
    ``C:/Program Files/…`` forward slashes turned back into ``C:\\Program Files\\…`` on Windows, before the
    duplicate check and validation."""
    if not isinstance(picked, str) or not picked.strip():
        return ""
    return ntpath.normpath(picked) if windows else os.path.normpath(picked)


def suggest_app_id(name: str, existing: Iterable[str]) -> str:
    """A valid (:data:`APP_ID_RE`), unused ``app_id`` for a program, from its file name: ``msedge``; ``code-2``
    then ``code-3`` when ``code`` is taken. Accents are folded, ``+`` becomes ``p`` (``notepadpp``), anything
    else outside ``a-z0-9_`` becomes ``-``; a name with nothing usable gives ``app``."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    base = _ID_JUNK.sub("-", folded.replace("+", "p")).strip("-_") or "app"
    taken = set(existing)
    n = 1
    while True:
        suffix = f"-{n}" if n > 1 else ""
        candidate = base[: APP_ID_MAX - len(suffix)].rstrip("-_") + suffix
        if candidate not in taken:
            return candidate
        n += 1


def clean_display_name(text: str) -> str:
    """What the phone shows and matches: control and format characters (bidi overrides too) dropped,
    whitespace collapsed to single spaces, at most 64 characters."""
    kept = "".join(
        " " if ch.isspace() else ch for ch in text if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    return " ".join(kept.split())[:DISPLAY_NAME_MAX].strip()


def version_strings(exe_path: str) -> dict[str, str]:
    """``FileDescription`` and ``ProductName`` from a Windows program's version resource (what Explorer shows as
    the file description); {} on other platforms or when the file has none. Reads the resource only."""
    if sys.platform != "win32":
        return {}
    import win32api

    query: Any = win32api.GetFileVersionInfo  # the stub types only the "\\" root block's return value
    try:
        pairs = query(exe_path, "\\VarFileInfo\\Translation") or []
    except Exception:  # noqa: BLE001 - pywintypes.error: the file has no version resource
        pairs = []
    blocks = [f"{int(lang):04X}{int(codepage):04X}" for lang, codepage in pairs] + list(_VERSION_BLOCKS)
    out: dict[str, str] = {}
    for key in ("FileDescription", "ProductName"):
        for block in blocks:
            try:
                value = query(exe_path, f"\\StringFileInfo\\{block}\\{key}")
            except Exception:  # noqa: BLE001, S112 - no such string in this language block: try the next one
                continue
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
                break
    return out


def suggest_display_name(exe_path: str, strings: Mapping[str, str] | None = None) -> str:
    """The name suggested for a picked program: its FileDescription (``Microsoft Edge``), else its ProductName,
    else the file name (``spotify.exe`` → ``Spotify``). Generic values (``Electron``, ``Update``) and file names
    are skipped, and a long description is cut at `` - `` or `` : `` (``Notepad++ : a free…`` → ``Notepad++``).
    The phone matches the name exactly, so a short one matters."""
    if strings is None:
        strings = version_strings(exe_path)
    for key in ("FileDescription", "ProductName"):
        candidate = clean_display_name(strings.get(key) or "")
        if len(candidate) > 40:
            candidate = clean_display_name(_NAME_SEPARATOR.split(candidate, maxsplit=1)[0])
        folded = candidate.casefold()
        if candidate and len(candidate) <= 40 and folded not in _GENERIC_NAMES and not folded.endswith(".exe"):
            return candidate
    stem = clean_display_name(ntpath.splitext(ntpath.basename(exe_path))[0])
    if stem.islower():
        stem = stem[:1].upper() + stem[1:]
    return stem or "App"


@dataclass(frozen=True, slots=True)
class PickWarning:
    """Why a picked program would be approved but not work from the phone. ``blocking``: it cannot work at
    all, so it is not added; otherwise the owner may add it anyway."""

    text: str
    blocking: bool


def pick_warning(
    exe_path: str, env: Mapping[str, str], *, listdir: Callable[[str], Sequence[str]] = os.listdir
) -> PickWarning | None:
    """Checked before validation for programs that pass it but never work from the phone (pass ``os.environ``
    itself on Windows: it ignores case)."""
    parts = _path_key(exe_path).split("\\")
    name = parts[-1]
    folder = ntpath.dirname(ntpath.normpath(exe_path))
    windir = _env(env, "WINDIR") or _env(env, "SystemRoot") or "C:\\Windows"
    if "windowsapps" in parts[:-1]:  # %ProgramFiles%\WindowsApps, or a per-user app alias
        return PickWarning(
            "This is a Microsoft Store app. DoMe can't add Store apps yet: Windows doesn't let other programs "
            "start them from their program file.",
            blocking=True,
        )
    if _under(exe_path, ntpath.join(windir, "Installer")):
        return PickWarning(
            "That file is in Windows' installer cache (Microsoft Office shortcuts point there), not the program "
            "itself. Choose the program in its install folder, for example "
            "Program Files\\Microsoft Office\\root\\Office16.",
            blocking=True,
        )
    if name in ("chrome_proxy.exe", "msedge_proxy.exe"):
        return PickWarning(
            "That program only opens web apps inside the browser. Add the browser itself (chrome.exe or "
            "msedge.exe in the same folder) instead.",
            blocking=True,
        )
    if name == "update.exe":
        try:
            siblings = listdir(folder)
        except OSError:
            siblings = []
        if any(s.lower().startswith("app-") for s in siblings):
            app = ntpath.basename(folder) or "the app"
            return PickWarning(
                f"That is {app}'s updater, not the app. Open the newest \u201capp-\u2026\u201d folder next to it "
                f"and choose {app}'s own program there. After {app} updates itself, select it in this list and "
                "click \u201cFind it again\u2026\u201d.",
                blocking=True,
            )
    if name in _STORE_HANDOFF and any(_under(exe_path, ntpath.join(windir, d)) for d in ("System32", "SysWOW64")):
        return PickWarning(
            "Windows may hand this program over to its Microsoft Store version. Your phone can open it, but may "
            "not be able to bring it to the front, maximise it or close it.",
            blocking=False,
        )
    return None


def explain_approval_error(exc: ApprovalError, exe_path: str = "") -> str:
    """Plain words for the tray; ``str(exc)`` stays the terse rule for the CLI."""
    if isinstance(exc, AppIdTakenError):
        return (
            f"Another app in the list already uses the id \u201c{exc.row.app_id}\u201d "
            f"({exc.row.display_name}). Nothing was changed; try again."
        )
    if exc.code == "SHELL_CHARS":
        found = [
            "an apostrophe (')" if ch == "'" else f"\u201c{ch}\u201d" for ch in sorted(_FORBIDDEN_CHARS & set(exe_path))
        ]
        if _ARGUMENT_RE.search(exe_path):
            found.append("a space followed by \u201c-\u201d or \u201c/\u201d")
        return (
            "DoMe can't add this program: its folder or file name contains "
            + (", ".join(found) or "a character DoMe does not accept")
            + ". DoMe refuses those so that nothing can be slipped in alongside a program. Choose a copy installed "
            "somewhere else (for example under Program Files), or rename that folder."
        )
    return {
        "NO_PATH": "No program was chosen.",
        "NOT_ABSOLUTE": "No program was chosen.",
        "CONTROL_CHARS": "That file name contains characters DoMe can't accept.",
        "NOT_EXE": "Only programs (.exe files) can be added. Documents, shortcuts to websites and Microsoft Store "
        "apps can't.",
        "NOT_FOUND": "That program could not be found. It may have been moved or uninstalled.",
        "NOT_FILE": "That is a folder, not a program. Open it and choose the program inside.",
        "TEMP_DIR": "Programs in a temporary folder (for example an installer that is still unpacking) can't be "
        "added. Install the program first, then add it from where it was installed.",
        "UNREADABLE": "Windows did not let DoMe read that program file. Try again, or choose another program.",
        "BAD_APP_ID": "That app id is not valid. Nothing was changed.",
    }.get(exc.code, f"DoMe could not add that program ({exc}).")


def programs_folder(env: Mapping[str, str], *, isdir: Callable[[str], bool] = os.path.isdir) -> str:
    """Where the file dialog starts: ``%ProgramFiles%``, or "" (the dialog's own default) without one."""
    folder = _env(env, "ProgramFiles")
    return folder if folder and isdir(folder) else ""


def nearest_existing_dir(path: str, *, isdir: Callable[[str], bool] = os.path.isdir) -> str:
    """For *Find it again…*: the closest folder of a program's old location that still exists ("" if none)."""
    current = ntpath.dirname(path)
    while current and not isdir(current):
        parent = ntpath.dirname(current)
        if parent == current:
            return ""
        current = parent
    return current

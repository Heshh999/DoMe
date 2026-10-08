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
  executable whose hash changed since approval (the user re-approves after an update).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from dome_protocol import ProtocolError

from .logsetup import get_logger
from .store import ApprovedAppRow, Store

log = get_logger(__name__)

APP_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
_FORBIDDEN_CHARS = set('"\'&|<>^%;`$*?')
_ARGUMENT_RE = re.compile(r"\s[-/]")  # whitespace followed by an option marker
_HASH_CHUNK = 1024 * 1024


class ApprovalError(ValueError):
    """The path cannot be approved; the message is safe to show to the local user."""


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
        raise ApprovalError("app_id must be 1-64 characters: lowercase letters, digits, '_' or '-', starting with a letter or digit")
    return app_id


def validate_executable_path(raw: str, *, temp_dirs: Iterable[Path] | None = None) -> ValidatedExecutable:
    if not isinstance(raw, str) or not raw.strip():
        raise ApprovalError("an executable path is required")
    text = raw.strip()
    if any(ch in _FORBIDDEN_CHARS for ch in text) or _ARGUMENT_RE.search(text) or "\n" in text or "\r" in text or "\t" in text:
        raise ApprovalError("the path may not contain arguments, quotes or shell characters; approve the executable file only")
    path = Path(text)
    if not path.is_absolute():
        raise ApprovalError("the executable path must be absolute")
    if path.suffix.lower() != ".exe":
        raise ApprovalError("only .exe files can be approved")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ApprovalError("the executable does not exist") from exc
    if not resolved.is_file():
        raise ApprovalError("the executable path is not a file")
    if resolved.suffix.lower() != ".exe":
        raise ApprovalError("only .exe files can be approved")
    dirs = tuple(temp_dirs) if temp_dirs is not None else default_temp_dirs()
    for temp_dir in dirs:
        if _is_under(resolved, temp_dir):
            raise ApprovalError("executables inside temporary folders cannot be approved")
    try:
        digest = sha256_file(resolved)
    except OSError as exc:
        raise ApprovalError("the executable could not be read") from exc
    return ValidatedExecutable(path=str(resolved), sha256=digest)


class ApprovedApps:
    """Store-backed approval list used by the CLI/tray (writes) and the app handlers (reads)."""

    def __init__(self, store: Store, *, temp_dirs: Iterable[Path] | None = None) -> None:
        self._store = store
        self._temp_dirs = tuple(temp_dirs) if temp_dirs is not None else None

    def approve(self, app_id: str, exe_path: str, display_name: str | None = None) -> ApprovedAppRow:
        app_id = validate_app_id(app_id)
        exe = validate_executable_path(exe_path, temp_dirs=self._temp_dirs)
        name = (display_name or Path(exe.path).stem).strip()[:64] or app_id
        row = self._store.add_approved_app(app_id, name, exe.path, exe.sha256)
        log.info("application approved locally", app_id=app_id)
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

    def resolve_for_launch(self, app_id: str) -> ApprovedAppRow:
        """Return the approval only if the executable on disk still matches the approved identity."""
        row = self.get(app_id)
        if row is None:
            raise ProtocolError("APP_NOT_APPROVED", "That app is not approved for remote control. Approve it on the PC first.")
        path = Path(row.exe_path)
        if not path.is_file():
            raise ProtocolError("APP_NOT_APPROVED", "The approved executable no longer exists. Approve it again on the PC.")
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

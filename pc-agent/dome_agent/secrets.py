"""Secret-at-rest protection.

Windows: ``CryptProtectData`` / ``CryptUnprotectData`` (DPAPI, current-user scope) via
``win32crypt``. The blob can only be decrypted by the same Windows user on the same machine.
Elsewhere: the bytes are stored as-is in a file with mode 0600 and a logged warning. Both paths
write atomically (temp file + ``os.replace``) and never log the content.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from .logsetup import get_logger

log = get_logger(__name__)

_DPAPI_DESCRIPTION = "DoMe agent secret"
_WARNED = False


class SecretStoreError(Exception):
    pass


def protection_label() -> str:
    return "dpapi-current-user" if sys.platform == "win32" else "file-mode-0600"


def protect(data: bytes) -> bytes:
    if sys.platform == "win32":
        import win32crypt

        try:
            blob: bytes = win32crypt.CryptProtectData(data, _DPAPI_DESCRIPTION, None, None, None, 0)
        except Exception as exc:  # pywintypes.error
            raise SecretStoreError(f"DPAPI protect failed: {exc}") from exc
        return blob
    _warn_once()
    return bytes(data)


def unprotect(blob: bytes) -> bytes:
    if sys.platform == "win32":
        import win32crypt

        try:
            _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        except Exception as exc:
            raise SecretStoreError(f"DPAPI unprotect failed: {exc}") from exc
        return bytes(data)
    return bytes(blob)


def _warn_once() -> None:
    global _WARNED
    if not _WARNED:
        _WARNED = True
        log.warning(
            "secrets are stored without OS-level encryption on this platform (file mode 0600 only); "
            "Windows builds use DPAPI",
            platform=sys.platform,
        )


def write_secret_file(path: Path, data: bytes) -> None:
    """Atomically write ``protect(data)`` to ``path`` with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = protect(data)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        if sys.platform != "win32":
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def read_secret_file(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if sys.platform != "win32":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            log.warning("secret file has permissive mode; tightening to 0600", file=path.name, mode=oct(mode))
            os.chmod(path, 0o600)
    return unprotect(path.read_bytes())


def delete_secret_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass

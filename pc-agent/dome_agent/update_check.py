"""Update check: is a newer signed DoMe for Windows published on GitHub?

What is checked:

* ``latest.json`` (the release's version information) must carry a detached Ed25519 signature
  (``latest.json.sig``, base64 over the exact file bytes) made with a key whose public half is pinned
  below in :data:`UPDATE_PUBLIC_KEYS`. The signature is checked before the file is even parsed.
* Its fields must be exactly what a DoMe release writes (``build_installer.write_manifest``): an
  X.Y.Z version, a 64-hex SHA-256, the size, and the download address
  ``https://github.com/Heshh999/DoMe/releases/download/vX.Y.Z/DoMe-Setup-X.Y.Z.exe``, nothing else.
* A verified version lower than the highest one this PC has already seen is refused (rollback guard).
* :meth:`UpdateChecker.download_installer` hashes the installer while it downloads and keeps it only
  when size and SHA-256 match the signed information.

What is NOT checked here: the installer's Authenticode signature; Windows checks it when the user runs
the file. DoMe never downloads or installs anything by itself: the daily check only fetches the two
small files from GitHub (sending nothing but a ``User-Agent``), and a download happens only when the
user asks for it in the tray; the user starts the downloaded Setup.

Every request is HTTPS to ``github.com``, following at most three redirects, each only to
``github.com`` or a ``*.githubusercontent.com`` host (where GitHub serves release files from).

:data:`UPDATE_PUBLIC_KEYS` holds the founder's update key, made on 2026-10-30 (docs/LAUNCH_SETUP.md section 17
step 5); a build without a pinned key answers "not set up in this build" and never fetches anything.
``mobile-app/src/content/windowsRelease.ts`` pins the same keys (a test checks both).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__
from .logsetup import get_logger
from .settings import Settings

log = get_logger(__name__)

REPO = "Heshh999/DoMe"
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/latest.json"
SIGNATURE_URL = MANIFEST_URL + ".sig"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
ALLOWED_HOSTS: tuple[str, ...] = ("github.com",)
# GitHub answers release downloads with a redirect to its file servers under this domain.
ALLOWED_HOST_SUFFIX = ".githubusercontent.com"
# Raw Ed25519 public keys, 64 hex characters each: the founder's update key, made on 2026-10-30 on the
# founder's PC (docs/LAUNCH_SETUP.md section 17 step 5); its private half lives only in the GitHub secret
# DOME_UPDATE_SIGNING_KEY of the `release` environment. A rotation adds the new key here first.
UPDATE_PUBLIC_KEYS: tuple[str, ...] = ("6945ea3e568ea103ab96cd3567e12494e0995bd639f0b872e53580c3537a5974",)
MAX_MANIFEST_BYTES = 16 * 1024
MAX_INSTALLER_BYTES = 300_000_000
MAX_REDIRECTS = 3
HTTP_TIMEOUT = 15.0
CHECK_INTERVAL = 24 * 3600.0
MIN_INTERVAL = 20 * 3600.0
FIRST_CHECK_DELAY = 10 * 60.0
STATE_FILENAME = "update-check.json"
DOWNLOADS_DIRNAME = "downloads"
MANIFEST_SCHEMA = 1

UP_TO_DATE_MESSAGE = "DoMe {current} is up to date."
NEWER_MESSAGE = (
    "DoMe {new} is available (you have {current}). DoMe does not update itself: open the download page, "
    "or let DoMe download the installer and check it before you run it."
)
INVALID_MESSAGE = "The update information could not be verified, so it was ignored. DoMe will check again tomorrow."
OFFLINE_MESSAGE = "Could not reach the update server. DoMe will check again tomorrow."
NOT_CONFIGURED_MESSAGE = "Update checks are not set up in this build."
NOTIFY_MESSAGE = "DoMe {new} is available. Open the menu, Check for updates…"

_VERSION_RE = re.compile(r"\A\d{1,4}\.\d{1,4}\.\d{1,4}\Z")
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_KEY_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_TIME_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_RELEASE_PAGE_RE = re.compile(rf"\Ahttps://github\.com/{re.escape(REPO)}/releases/[A-Za-z0-9._/-]{{1,100}}\Z")
_MANIFEST_KEYS = frozenset(
    (
        "browser_addon",
        "product",
        "published_at",
        "publisher",
        "release_page",
        "schema",
        "sha256",
        "size",
        "url",
        "version",
    )
)

Status = Literal["newer", "up_to_date", "invalid", "offline", "not_configured"]


class ManifestError(Exception):
    """The update information or a download was refused. ``reason`` is plain English and never quotes
    the refused content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FetchError(Exception):
    """A request failed. ``refused`` is True when DoMe refused it (a redirect off GitHub, plain http, a
    body larger than allowed), False for network trouble."""

    def __init__(self, reason: str, *, refused: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.refused = refused


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: int
    body: bytes


Fetch = Callable[[str], FetchResult]
Progress = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    url: str
    sha256: str
    size: int
    published_at: str
    release_page: str
    publisher: str
    browser_addon: bool


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: Status
    message: str
    release: ReleaseInfo | None = None
    # Why a check ended as it did, for the log (plain English, never the refused content).
    reason: str = ""


class Outcome(StrEnum):
    newer = "newer"
    up_to_date = "up_to_date"
    older = "older"


def parse_version(version: str) -> tuple[int, int, int]:
    if not _VERSION_RE.match(version):
        raise ValueError(f"not an X.Y.Z version: {version!r}")
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def compare_versions(current: str, candidate: str) -> int:
    """-1, 0 or 1 as ``candidate`` is lower than, equal to or higher than ``current``."""
    a, b = parse_version(current), parse_version(candidate)
    return (b > a) - (b < a)


def classify(current: str, candidate: str) -> Outcome:
    order = compare_versions(current, candidate)
    return Outcome.newer if order > 0 else Outcome.up_to_date if order == 0 else Outcome.older


def expected_url(version: str) -> str:
    return f"{DOWNLOAD_PREFIX}v{version}/DoMe-Setup-{version}.exe"


def _verify_signature(manifest: bytes, signature: bytes, public_keys: tuple[str, ...]) -> None:
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ManifestError("the update information carries no readable signature") from None
    if len(raw) != 64:
        raise ManifestError("the update information carries no readable signature")
    for hex_key in public_keys:
        if not _KEY_RE.match(hex_key):
            continue
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key)).verify(raw, manifest)
        except (InvalidSignature, ValueError):
            continue
        return
    raise ManifestError("the signature of the update information does not match DoMe's key")


def verify_and_parse(
    manifest: bytes, signature: bytes, *, public_keys: tuple[str, ...] = UPDATE_PUBLIC_KEYS
) -> ReleaseInfo:
    """The release a signed ``latest.json`` describes. The signature is checked first, over the exact
    bytes; then every field. Raises :class:`ManifestError`."""
    if not public_keys:
        raise ManifestError("update checks are not set up in this build")
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise ManifestError("the update information is larger than allowed")
    _verify_signature(manifest, signature, public_keys)
    try:
        data = json.loads(manifest.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ManifestError("the update information is not readable") from None
    if not isinstance(data, dict):
        raise ManifestError("the update information is not readable")
    if data.get("schema") != MANIFEST_SCHEMA or isinstance(data.get("schema"), bool):
        raise ManifestError("the update information has a format this version of DoMe does not know")
    if set(data) != _MANIFEST_KEYS:
        raise ManifestError("the update information does not have the expected fields")
    version, sha256, url, size = data["version"], data["sha256"], data["url"], data["size"]
    published_at, release_page, publisher = data["published_at"], data["release_page"], data["publisher"]
    if data["product"] != "DoMe for Windows":
        raise ManifestError("the update information is not about DoMe for Windows")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ManifestError("the update information has no valid version")
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise ManifestError("the update information has no valid SHA-256")
    if url != expected_url(version):
        raise ManifestError("the download address is not DoMe's GitHub release address")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_INSTALLER_BYTES:
        raise ManifestError("the update information has no valid file size")
    if not isinstance(published_at, str) or not _TIME_RE.match(published_at):
        raise ManifestError("the update information has no valid date")
    try:
        datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ManifestError("the update information has no valid date") from None
    if not isinstance(release_page, str) or not _RELEASE_PAGE_RE.match(release_page):
        raise ManifestError("the release page is not on DoMe's GitHub releases")
    if not isinstance(publisher, str) or not publisher.strip() or len(publisher) > 200:
        raise ManifestError("the update information names no publisher")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in publisher):
        raise ManifestError("the update information names no publisher")
    if not isinstance(data["browser_addon"], bool):
        raise ManifestError("the update information is not readable")
    return ReleaseInfo(
        version=version,
        url=url,
        sha256=sha256,
        size=size,
        published_at=published_at,
        release_page=release_page,
        publisher=publisher,
        browser_addon=data["browser_addon"],
    )


# ----- HTTP ---------------------------------------------------------------------------------------------------


def allowed_url(url: str) -> bool:
    """HTTPS to github.com or a *.githubusercontent.com host, on the default port, without user info."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
        and (host in ALLOWED_HOSTS or (host.endswith(ALLOWED_HOST_SUFFIX) and len(host) > len(ALLOWED_HOST_SUFFIX)))
    )


def _client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": f"DoMe-agent/{__version__}"},
        transport=transport,
    )


@contextmanager
def _open(client: httpx.Client, url: str) -> Iterator[httpx.Response]:
    """GET ``url``, following at most :data:`MAX_REDIRECTS` redirects, each checked with
    :func:`allowed_url` before anything is requested from it."""
    for _ in range(MAX_REDIRECTS + 1):
        if not allowed_url(url):
            raise FetchError("an address outside GitHub (or not https) was refused", refused=True)
        with client.stream("GET", url) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            yield response
            return
    raise FetchError("too many redirects", refused=True)


def _read_capped(response: httpx.Response, cap: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise FetchError("the answer is larger than allowed", refused=True)
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > cap:
            raise FetchError("the answer is larger than allowed", refused=True)
    return bytes(body)


def http_fetch(
    url: str, *, transport: httpx.BaseTransport | None = None, max_bytes: int = MAX_MANIFEST_BYTES
) -> FetchResult:
    """One small file from GitHub. Raises :class:`FetchError`."""
    try:
        with _client(transport) as client, _open(client, url) as response:
            if response.status_code != 200:
                return FetchResult(response.status_code, b"")
            return FetchResult(200, _read_capped(response, max_bytes))
    except httpx.HTTPError as exc:
        raise FetchError(f"{exc.__class__.__name__} talking to GitHub", refused=False) from None


# ----- the checker --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _State:
    last_checked_at: float | None = None
    last_status: str = ""
    highest_seen_version: str = ""
    notified_version: str = ""


class UpdateChecker:
    """Checks GitHub for a newer signed release; downloads and checks its installer only when asked."""

    def __init__(
        self,
        settings: Settings,
        *,
        current_version: str = __version__,
        fetch: Fetch | None = None,
        clock: Callable[[], float] = time.time,
        state_path: Path | None = None,
        public_keys: tuple[str, ...] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._current = current_version
        self._transport = transport
        self._fetch: Fetch = fetch or (lambda url: http_fetch(url, transport=transport))
        self._clock = clock
        self._state_path = state_path or settings.state_dir / STATE_FILENAME
        self._public_keys = UPDATE_PUBLIC_KEYS if public_keys is None else public_keys
        self._sleep = sleep

    @property
    def configured(self) -> bool:
        return bool(self._public_keys)

    @property
    def downloads_dir(self) -> Path:
        return self._settings.state_dir / DOWNLOADS_DIRNAME

    # ----- state file ------------------------------------------------------------------------------------
    def _load_state(self) -> _State:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _State()
        if not isinstance(data, dict):
            return _State()
        state = _State()
        checked = data.get("last_checked_at")
        if isinstance(checked, int | float) and not isinstance(checked, bool):
            state.last_checked_at = float(checked)
        for name in ("last_status", "highest_seen_version", "notified_version"):
            value = data.get(name)
            if isinstance(value, str) and (name == "last_status" or not value or _VERSION_RE.match(value)):
                setattr(state, name, value)
        return state

    def _save_state(self, state: _State) -> None:
        payload = {
            "last_checked_at": state.last_checked_at,
            "last_status": state.last_status,
            "highest_seen_version": state.highest_seen_version,
            "notified_version": state.notified_version,
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_name(self._state_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self._state_path)

    def last_checked_at(self) -> float | None:
        return self._load_state().last_checked_at

    # ----- one check ---------------------------------------------------------------------------------------
    def check(self, reason: Literal["manual", "daily"]) -> CheckResult:
        """Fetch and verify ``latest.json``; never raises for network or content problems."""
        if not self._public_keys:
            return CheckResult("not_configured", NOT_CONFIGURED_MESSAGE, reason="no update key is pinned in this build")
        state = self._load_state()
        result = self._check(state)
        state.last_checked_at = self._clock()
        state.last_status = result.status
        try:
            self._save_state(state)
        except OSError as exc:
            log.info("update_check_state_not_saved", error=exc.__class__.__name__)
        log.info("update_check", trigger=reason, status=result.status, reason=result.reason)
        return result

    def _check(self, state: _State) -> CheckResult:
        try:
            manifest = self._fetch(MANIFEST_URL)
            signature = self._fetch(SIGNATURE_URL) if manifest.status == 200 else FetchResult(0, b"")
        except FetchError as exc:
            if exc.refused:
                return CheckResult("invalid", INVALID_MESSAGE, reason=exc.reason)
            return CheckResult("offline", OFFLINE_MESSAGE, reason=exc.reason)
        if manifest.status != 200 or signature.status != 200:
            status = manifest.status if manifest.status != 200 else signature.status
            return CheckResult("offline", OFFLINE_MESSAGE, reason=f"GitHub answered HTTP {status}")
        try:
            release = verify_and_parse(manifest.body, signature.body, public_keys=self._public_keys)
        except ManifestError as exc:
            return CheckResult("invalid", INVALID_MESSAGE, reason=exc.reason)
        highest = state.highest_seen_version
        if highest and compare_versions(highest, release.version) < 0:
            return CheckResult(
                "invalid",
                INVALID_MESSAGE,
                reason=f"the signed version is older than a version this PC already saw ({highest})",
            )
        state.highest_seen_version = release.version
        if classify(self._current, release.version) is Outcome.newer:
            message = NEWER_MESSAGE.format(new=release.version, current=self._current)
            return CheckResult("newer", message, release, reason="a newer signed version exists")
        return CheckResult(
            "up_to_date", UP_TO_DATE_MESSAGE.format(current=self._current), release, reason="no newer signed version"
        )

    # ----- the daily check ---------------------------------------------------------------------------------
    async def daily_loop(self, on_newer: Callable[[CheckResult], None]) -> None:
        """Check about once a day (never twice within :data:`MIN_INTERVAL`, also across restarts) and call
        ``on_newer`` once per newer version. Runs until cancelled."""
        await self._sleep(FIRST_CHECK_DELAY)
        while True:
            last = self.last_checked_at()
            if last is None or self._clock() - last >= MIN_INTERVAL:
                try:
                    result = await asyncio.to_thread(self.check, "daily")
                except Exception as exc:  # noqa: BLE001 - the loop must survive anything; the reason is logged
                    log.info("update_check_failed", error=exc.__class__.__name__)
                else:
                    self._maybe_notify(result, on_newer)
            await self._sleep(CHECK_INTERVAL * secrets.SystemRandom().uniform(0.9, 1.1))

    def _maybe_notify(self, result: CheckResult, on_newer: Callable[[CheckResult], None]) -> None:
        if result.status != "newer" or result.release is None:
            return
        state = self._load_state()
        if state.notified_version == result.release.version:
            return
        state.notified_version = result.release.version
        try:
            self._save_state(state)
        except OSError as exc:
            log.info("update_check_state_not_saved", error=exc.__class__.__name__)
        on_newer(result)

    # ----- the download (only when the user asks) ---------------------------------------------------------
    def download_installer(self, release: ReleaseInfo, *, progress: Progress | None = None) -> Path:
        """Download ``release``'s Setup into ``<state dir>/downloads`` and keep it only when its size and
        SHA-256 match the signed information. Never runs it. Raises :class:`ManifestError`."""
        if release.url != expected_url(release.version) or not 0 < release.size <= MAX_INSTALLER_BYTES:
            raise ManifestError("the download address is not DoMe's GitHub release address")
        folder = self.downloads_dir
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / f"DoMe-Setup-{release.version}.exe.part"
        final = folder / f"DoMe-Setup-{release.version}.exe"
        digest = hashlib.sha256()
        total = 0
        try:
            with _client(self._transport) as client, _open(client, release.url) as response:
                if response.status_code != 200:
                    raise ManifestError(
                        f"the installer could not be downloaded (GitHub answered HTTP {response.status_code})"
                    )
                with part.open("wb") as fh:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > release.size:
                            raise ManifestError(
                                "the download is larger than the signed version information says; it was deleted"
                            )
                        digest.update(chunk)
                        fh.write(chunk)
                        if progress is not None:
                            progress(total, release.size)
        except ManifestError:
            part.unlink(missing_ok=True)
            raise
        except FetchError as exc:
            part.unlink(missing_ok=True)
            raise ManifestError(f"the installer could not be downloaded ({exc.reason})") from None
        except (httpx.HTTPError, OSError) as exc:
            part.unlink(missing_ok=True)
            raise ManifestError(f"the installer could not be downloaded ({exc.__class__.__name__})") from None
        if total != release.size or digest.hexdigest() != release.sha256:
            part.unlink(missing_ok=True)
            raise ManifestError("the downloaded file does not match the signed version information; it was deleted")
        os.replace(part, final)
        return final

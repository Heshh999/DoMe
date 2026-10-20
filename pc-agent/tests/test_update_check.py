"""The update check (dome_agent/update_check.py): only a manifest signed with a pinned Ed25519 key, with
DoMe's exact GitHub download address, counts; a tampered, unsigned, foreign or rolled-back one is refused
with the honest message; requests stay on GitHub; a downloaded installer is kept only when its size and
SHA-256 match. Keys are generated in memory here: no key material exists in the repository."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dome_agent import update_check as uc
from dome_agent.settings import Settings
from dome_agent.update_check import (
    FetchError,
    FetchResult,
    ManifestError,
    Outcome,
    ReleaseInfo,
    UpdateChecker,
    allowed_url,
    classify,
    compare_versions,
    http_fetch,
    verify_and_parse,
)

WINDOWS_RELEASE_TS = Path(__file__).resolve().parents[2] / "mobile-app" / "src" / "content" / "windowsRelease.ts"
INSTALLER = b"MZ" + bytes(range(256)) * 40


def public_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def manifest_obj(version: str = "0.2.0", body: bytes = INSTALLER, **overrides: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "browser_addon": False,
        "product": "DoMe for Windows",
        "published_at": "2026-11-02T14:03:11Z",
        "publisher": "Jane Example",
        "release_page": f"https://github.com/Heshh999/DoMe/releases/tag/v{version}",
        "schema": 1,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
        "url": f"https://github.com/Heshh999/DoMe/releases/download/v{version}/DoMe-Setup-{version}.exe",
        "version": version,
    }
    obj.update(overrides)
    return obj


def encode(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sign(key: Ed25519PrivateKey, data: bytes) -> bytes:
    return base64.b64encode(key.sign(data)) + b"\n"


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def keys(key: Ed25519PrivateKey) -> tuple[str, ...]:
    return (public_hex(key),)


class FakeGitHub:
    """Answers MANIFEST_URL and SIGNATURE_URL; records every request."""

    def __init__(self, manifest: bytes, signature: bytes, status: int = 200) -> None:
        self.manifest, self.signature, self.status = manifest, signature, status
        self.requests: list[str] = []
        self.error: FetchError | None = None

    def __call__(self, url: str) -> FetchResult:
        self.requests.append(url)
        if self.error is not None:
            raise self.error
        if url == uc.MANIFEST_URL:
            return FetchResult(self.status, self.manifest if self.status == 200 else b"")
        if url == uc.SIGNATURE_URL:
            return FetchResult(self.status, self.signature if self.status == 200 else b"")
        raise AssertionError(f"unexpected request {url}")


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def checker(
    settings: Settings, github: FakeGitHub, keys: tuple[str, ...], *, current: str = "0.1.0", clock: Clock | None = None
) -> UpdateChecker:
    return UpdateChecker(settings, current_version=current, fetch=github, clock=clock or Clock(), public_keys=keys)


# ----- verify_and_parse -------------------------------------------------------------------------------------


def test_a_signed_manifest_is_parsed(key: Ed25519PrivateKey, keys: tuple[str, ...]) -> None:
    data = encode(manifest_obj())
    release = verify_and_parse(data, sign(key, data), public_keys=keys)
    assert release == ReleaseInfo(
        version="0.2.0",
        url="https://github.com/Heshh999/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe",
        sha256=hashlib.sha256(INSTALLER).hexdigest(),
        size=len(INSTALLER),
        published_at="2026-11-02T14:03:11Z",
        release_page="https://github.com/Heshh999/DoMe/releases/tag/v0.2.0",
        publisher="Jane Example",
        browser_addon=False,
    )


def test_one_flipped_byte_breaks_the_signature(key: Ed25519PrivateKey, keys: tuple[str, ...]) -> None:
    data = encode(manifest_obj())
    signature = sign(key, data)
    tampered = data.replace(b'"size": ', b'"size":  ')
    assert tampered != data
    with pytest.raises(ManifestError, match="does not match DoMe's key"):
        verify_and_parse(tampered, signature, public_keys=keys)
    flipped = bytearray(data)
    flipped[10] ^= 0x01
    with pytest.raises(ManifestError):
        verify_and_parse(bytes(flipped), signature, public_keys=keys)


def test_another_key_and_no_key_are_refused(key: Ed25519PrivateKey) -> None:
    data = encode(manifest_obj())
    other = (public_hex(Ed25519PrivateKey.generate()),)
    with pytest.raises(ManifestError, match="does not match"):
        verify_and_parse(data, sign(key, data), public_keys=other)
    with pytest.raises(ManifestError, match="not set up in this build"):
        verify_and_parse(data, sign(key, data), public_keys=())


@pytest.mark.parametrize("signature", [b"", b"not base64!!", base64.b64encode(b"short"), b"\x00\xff"])
def test_missing_or_garbled_signatures_are_refused(signature: bytes, keys: tuple[str, ...]) -> None:
    with pytest.raises(ManifestError, match="no readable signature"):
        verify_and_parse(encode(manifest_obj()), signature, public_keys=keys)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"url": "https://evil.example/DoMe-Setup-0.2.0.exe"}, "download address"),
        ({"url": "https://github.com/Heshh999/DoMe/releases/download/v0.2.0/Other.exe"}, "download address"),
        ({"url": "https://github.com/someone/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe"}, "download address"),
        ({"url": "http://github.com/Heshh999/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe"}, "download address"),
        ({"sha256": "A" * 64}, "SHA-256"),
        ({"sha256": "ab" * 31}, "SHA-256"),
        ({"schema": 2}, "format"),
        ({"schema": True}, "format"),
        ({"version": "0.2"}, "version"),
        ({"size": 0}, "file size"),
        ({"size": uc.MAX_INSTALLER_BYTES + 1}, "file size"),
        ({"size": True}, "file size"),
        ({"published_at": "yesterday"}, "date"),
        ({"published_at": "2026-13-40T99:00:00Z"}, "date"),
        ({"release_page": "https://evil.example/releases/tag/v0.2.0"}, "release page"),
        ({"publisher": ""}, "publisher"),
        ({"publisher": "x" * 201}, "publisher"),
        ({"publisher": "Jane\nExample"}, "publisher"),
        ({"browser_addon": "no"}, "readable"),
        ({"product": "Something else"}, "not about DoMe"),
        ({"extra": 1}, "expected fields"),
    ],
)
def test_signed_but_wrong_fields_are_refused(
    key: Ed25519PrivateKey, keys: tuple[str, ...], overrides: dict[str, Any], reason: str
) -> None:
    data = encode(manifest_obj(**overrides))
    with pytest.raises(ManifestError, match=reason):
        verify_and_parse(data, sign(key, data), public_keys=keys)


def test_a_signed_non_object_and_an_oversize_manifest_are_refused(
    key: Ed25519PrivateKey, keys: tuple[str, ...]
) -> None:
    data = b"[1, 2, 3]\n"
    with pytest.raises(ManifestError, match="not readable"):
        verify_and_parse(data, sign(key, data), public_keys=keys)
    big = encode(manifest_obj()) + b" " * uc.MAX_MANIFEST_BYTES
    with pytest.raises(ManifestError, match="larger than allowed"):
        verify_and_parse(big, sign(key, big), public_keys=keys)


def test_manifest_error_reasons_never_quote_the_content(key: Ed25519PrivateKey, keys: tuple[str, ...]) -> None:
    data = encode(manifest_obj(url="https://evil.example/payload.exe"))
    with pytest.raises(ManifestError) as caught:
        verify_and_parse(data, sign(key, data), public_keys=keys)
    assert "evil.example" not in caught.value.reason


def test_versions_compare_numerically() -> None:
    assert compare_versions("0.9.0", "0.10.0") == 1
    assert compare_versions("1.0.0", "1.0.0") == 0
    assert compare_versions("1.0.1", "1.0.0") == -1
    assert classify("0.1.0", "0.2.0") is Outcome.newer
    assert classify("0.2.0", "0.2.0") is Outcome.up_to_date
    assert classify("0.3.0", "0.2.0") is Outcome.older
    with pytest.raises(ValueError):
        compare_versions("1.0", "1.0.0")


# ----- UpdateChecker.check ----------------------------------------------------------------------------------


def test_newer_release(settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]) -> None:
    data = encode(manifest_obj("0.2.0"))
    github = FakeGitHub(data, sign(key, data))
    result = checker(settings, github, keys).check("manual")
    assert result.status == "newer" and result.release is not None and result.release.version == "0.2.0"
    assert result.message == (
        "DoMe 0.2.0 is available (you have 0.1.0). DoMe does not update itself: open the download page, "
        "or let DoMe download the installer and check it before you run it."
    )
    assert github.requests == [uc.MANIFEST_URL, uc.SIGNATURE_URL]


def test_equal_or_older_release_is_up_to_date(
    settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]
) -> None:
    data = encode(manifest_obj("0.2.0"))
    github = FakeGitHub(data, sign(key, data))
    result = checker(settings, github, keys, current="0.2.0").check("manual")
    assert (result.status, result.message) == ("up_to_date", "DoMe 0.2.0 is up to date.")
    result = checker(settings, github, keys, current="0.3.0").check("manual")
    assert result.status == "up_to_date"


def test_tampered_manifest_is_invalid_with_the_honest_message(
    settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]
) -> None:
    data = encode(manifest_obj("0.2.0"))
    signature = sign(key, data)
    tampered = data.replace(b"0.2.0", b"0.9.0")
    result = checker(settings, FakeGitHub(tampered, signature), keys).check("manual")
    assert result.status == "invalid" and result.release is None
    assert result.message == (
        "The update information could not be verified, so it was ignored. DoMe will check again tomorrow."
    )


@pytest.mark.parametrize("signature", [b"", b"garbage", base64.b64encode(bytes(64))])
def test_missing_or_garbled_sig_file_is_invalid(settings: Settings, keys: tuple[str, ...], signature: bytes) -> None:
    result = checker(settings, FakeGitHub(encode(manifest_obj()), signature), keys).check("daily")
    assert result.status == "invalid" and result.release is None


def test_no_pinned_key_is_not_configured_and_fetches_nothing(settings: Settings) -> None:
    github = FakeGitHub(b"", b"")
    result = checker(settings, github, ()).check("manual")
    assert (result.status, result.message) == ("not_configured", "Update checks are not set up in this build.")
    assert github.requests == []
    assert uc.UPDATE_PUBLIC_KEYS == () or all(re.fullmatch(r"[0-9a-f]{64}", k) for k in uc.UPDATE_PUBLIC_KEYS)


def test_network_trouble_and_http_errors_are_offline(settings: Settings, keys: tuple[str, ...]) -> None:
    github = FakeGitHub(b"", b"", status=404)
    result = checker(settings, github, keys).check("daily")
    assert (result.status, result.message) == (
        "offline",
        "Could not reach the update server. DoMe will check again tomorrow.",
    )
    github.error = FetchError("ConnectError talking to GitHub", refused=False)
    assert checker(settings, github, keys).check("daily").status == "offline"
    github.error = FetchError("an address outside GitHub (or not https) was refused", refused=True)
    assert checker(settings, github, keys).check("daily").status == "invalid"


def test_rollback_is_refused_and_the_highest_version_stays(
    settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]
) -> None:
    new = encode(manifest_obj("0.3.0"))
    assert checker(settings, FakeGitHub(new, sign(key, new)), keys).check("daily").status == "newer"
    old = encode(manifest_obj("0.2.0"))
    result = checker(settings, FakeGitHub(old, sign(key, old)), keys).check("daily")
    assert result.status == "invalid" and result.release is None
    assert "older than a version this PC already saw" in result.reason
    state = json.loads((settings.state_dir / "update-check.json").read_text(encoding="utf-8"))
    assert state["highest_seen_version"] == "0.3.0"


def test_highest_seen_only_advances_on_a_verified_manifest(
    settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]
) -> None:
    good = encode(manifest_obj("0.2.0"))
    checker(settings, FakeGitHub(good, sign(key, good)), keys).check("daily")
    forged = encode(manifest_obj("9.0.0"))
    other = Ed25519PrivateKey.generate()
    assert checker(settings, FakeGitHub(forged, sign(other, forged)), keys).check("daily").status == "invalid"
    state = json.loads((settings.state_dir / "update-check.json").read_text(encoding="utf-8"))
    assert state["highest_seen_version"] == "0.2.0"
    assert state["last_status"] == "invalid"


def test_state_file_round_trip_and_corrupt_state(
    settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]
) -> None:
    data = encode(manifest_obj("0.2.0"))
    clock = Clock(1_900_000_000.0)
    c = checker(settings, FakeGitHub(data, sign(key, data)), keys, clock=clock)
    c.check("manual")
    assert c.last_checked_at() == 1_900_000_000.0
    state = json.loads((settings.state_dir / "update-check.json").read_text(encoding="utf-8"))
    assert state == {
        "highest_seen_version": "0.2.0",
        "last_checked_at": 1_900_000_000.0,
        "last_status": "newer",
        "notified_version": "",
    }
    (settings.state_dir / "update-check.json").write_text("{not json", encoding="utf-8")
    assert c.last_checked_at() is None
    assert c.check("manual").status == "newer"


# ----- HTTP: GitHub only, https only, small answers --------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://github.com/Heshh999/DoMe/releases/latest/download/latest.json", True),
        ("https://objects.githubusercontent.com/github-production-release-asset/x", True),
        ("https://release-assets.githubusercontent.com/x", True),
        ("http://github.com/x", False),
        ("https://github.com.evil.example/x", False),
        ("https://evilgithubusercontent.com/x", False),
        ("https://.githubusercontent.com/x", False),
        ("https://user@github.com/x", False),
        ("https://github.com:8443/x", False),
        ("https://github.com:notaport/x", False),
        ("ftp://github.com/x", False),
    ],
)
def test_allowed_urls(url: str, ok: bool) -> None:
    assert allowed_url(url) is ok


def transport(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.MockTransport(handler)


def test_redirects_to_github_file_servers_are_followed() -> None:
    seen: list[str] = []
    routes = {
        uc.MANIFEST_URL: httpx.Response(
            302, headers={"location": "https://objects.githubusercontent.com/a/latest.json"}
        ),
        "https://objects.githubusercontent.com/a/latest.json": httpx.Response(200, content=b"{}"),
    }
    result = http_fetch(uc.MANIFEST_URL, transport=transport(routes, seen))
    assert result == FetchResult(200, b"{}")
    assert seen == [uc.MANIFEST_URL, "https://objects.githubusercontent.com/a/latest.json"]
    assert all(r.startswith("https://") for r in seen)


@pytest.mark.parametrize("target", ["https://evil.example/latest.json", "http://objects.githubusercontent.com/a"])
def test_a_redirect_off_github_or_to_http_is_refused_before_any_request(target: str) -> None:
    seen: list[str] = []
    routes = {uc.MANIFEST_URL: httpx.Response(302, headers={"location": target})}
    with pytest.raises(FetchError) as caught:
        http_fetch(uc.MANIFEST_URL, transport=transport(routes, seen))
    assert caught.value.refused
    assert seen == [uc.MANIFEST_URL]


def test_too_many_redirects_are_refused() -> None:
    seen: list[str] = []
    loop = {uc.MANIFEST_URL: httpx.Response(302, headers={"location": uc.MANIFEST_URL})}
    with pytest.raises(FetchError, match="too many redirects"):
        http_fetch(uc.MANIFEST_URL, transport=transport(loop, seen))
    assert len(seen) == uc.MAX_REDIRECTS + 1


def test_an_oversize_answer_is_refused() -> None:
    seen: list[str] = []
    routes = {uc.MANIFEST_URL: httpx.Response(200, content=b"x" * (uc.MAX_MANIFEST_BYTES + 1))}
    with pytest.raises(FetchError, match="larger than allowed") as caught:
        http_fetch(uc.MANIFEST_URL, transport=transport(routes, seen))
    assert caught.value.refused


def test_the_checker_sends_only_a_user_agent(settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]) -> None:
    data = encode(manifest_obj())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=data if request.url.path.endswith(".json") else sign(key, data))

    c = UpdateChecker(settings, public_keys=keys, transport=httpx.MockTransport(handler), current_version="0.1.0")
    assert c.check("manual").status == "newer"
    assert [r.method for r in requests] == ["GET", "GET"]
    for request in requests:
        assert request.headers["user-agent"].startswith("DoMe-agent/")
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert request.content == b""


# ----- download_installer -----------------------------------------------------------------------------------


def release_for(body: bytes, version: str = "0.2.0", **overrides: Any) -> ReleaseInfo:
    obj = manifest_obj(version, body)
    obj.update(overrides)
    return ReleaseInfo(
        version=obj["version"],
        url=obj["url"],
        sha256=obj["sha256"],
        size=obj["size"],
        published_at=obj["published_at"],
        release_page=obj["release_page"],
        publisher=obj["publisher"],
        browser_addon=obj["browser_addon"],
    )


def serving(body: bytes) -> httpx.MockTransport:
    asset = "https://release-assets.githubusercontent.com/github-production-release-asset/1/DoMe-Setup-0.2.0.exe"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://github.com/"):
            return httpx.Response(302, headers={"location": asset})
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def test_a_matching_download_is_kept_and_never_run(settings: Settings) -> None:
    progress: list[tuple[int, int]] = []
    c = UpdateChecker(settings, public_keys=("00" * 32,), transport=serving(INSTALLER))
    path = c.download_installer(release_for(INSTALLER), progress=lambda done, total: progress.append((done, total)))
    assert path == settings.state_dir / "downloads" / "DoMe-Setup-0.2.0.exe"
    assert path.read_bytes() == INSTALLER
    assert progress and progress[-1] == (len(INSTALLER), len(INSTALLER))
    assert not (settings.state_dir / "downloads" / "DoMe-Setup-0.2.0.exe.part").exists()


def test_a_mismatching_download_is_deleted(settings: Settings) -> None:
    other = INSTALLER[:-1] + b"\x00"
    c = UpdateChecker(settings, public_keys=("00" * 32,), transport=serving(other))
    with pytest.raises(ManifestError, match="does not match the signed version information; it was deleted"):
        c.download_installer(release_for(INSTALLER))
    assert list((settings.state_dir / "downloads").iterdir()) == []


def test_a_download_larger_than_signed_is_aborted(settings: Settings) -> None:
    c = UpdateChecker(settings, public_keys=("00" * 32,), transport=serving(INSTALLER + b"more"))
    with pytest.raises(ManifestError, match="larger than the signed version information"):
        c.download_installer(release_for(INSTALLER))
    assert list((settings.state_dir / "downloads").iterdir()) == []


def test_a_download_address_off_github_is_refused(settings: Settings) -> None:
    c = UpdateChecker(settings, public_keys=("00" * 32,), transport=serving(INSTALLER))
    with pytest.raises(ManifestError, match="download address"):
        c.download_installer(release_for(INSTALLER, url="https://evil.example/DoMe-Setup-0.2.0.exe"))
    with pytest.raises(ManifestError, match="download address"):
        c.download_installer(release_for(INSTALLER, size=uc.MAX_INSTALLER_BYTES + 1))


def test_a_download_redirected_off_github_is_refused(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/x.exe"})

    c = UpdateChecker(settings, public_keys=("00" * 32,), transport=httpx.MockTransport(handler))
    with pytest.raises(ManifestError, match="could not be downloaded"):
        c.download_installer(release_for(INSTALLER))
    assert list((settings.state_dir / "downloads").iterdir()) == []


# ----- the daily loop ---------------------------------------------------------------------------------------


class StopLoopError(Exception):
    pass


def test_daily_loop_skips_recent_checks_and_notifies_once_per_version(
    settings: Settings, key: Ed25519PrivateKey, keys: tuple[str, ...]
) -> None:
    data = encode(manifest_obj("0.2.0"))
    github = FakeGitHub(data, sign(key, data))
    clock = Clock()
    sleeps: list[float] = []
    notified: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds
        if len(sleeps) > 4:
            raise StopLoopError

    c = UpdateChecker(settings, current_version="0.1.0", fetch=github, clock=clock, public_keys=keys, sleep=fake_sleep)
    # checked 1 hour ago: the first round must skip
    (settings.state_dir / "update-check.json").write_text(
        json.dumps({"last_checked_at": clock.now + uc.FIRST_CHECK_DELAY - 3600}), encoding="utf-8"
    )
    with pytest.raises(StopLoopError):
        asyncio.run(c.daily_loop(lambda result: notified.append(result.release.version if result.release else "")))
    assert sleeps[0] == uc.FIRST_CHECK_DELAY
    assert all(0.9 * uc.CHECK_INTERVAL <= s <= 1.1 * uc.CHECK_INTERVAL for s in sleeps[1:])
    # rounds 2, 3 and 4 checked (each about a day apart); the first did not
    assert github.requests.count(uc.MANIFEST_URL) == 3
    assert notified == ["0.2.0"]
    state = json.loads((settings.state_dir / "update-check.json").read_text(encoding="utf-8"))
    assert state["notified_version"] == "0.2.0"


def test_daily_loop_survives_a_failing_check(settings: Settings, keys: tuple[str, ...]) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise StopLoopError

    def broken(url: str) -> FetchResult:
        raise RuntimeError("boom")

    c = UpdateChecker(settings, fetch=broken, public_keys=keys, sleep=fake_sleep)
    with pytest.raises(StopLoopError):
        asyncio.run(c.daily_loop(lambda result: None))
    assert len(sleeps) == 3


# ----- the website pins the same keys -----------------------------------------------------------------------

_KEYS_ARRAY = re.compile(r"export\s+const\s+WINDOWS_RELEASE_PUBLIC_KEYS\b[^=]*=\s*\[(?P<items>[^\]]*)\]")


def website_keys(source: str) -> tuple[str, ...]:
    match = _KEYS_ARRAY.search(source)
    assert match is not None, "no `export const WINDOWS_RELEASE_PUBLIC_KEYS ... = [...]` array literal"
    items = [item.strip() for item in match.group("items").split(",") if item.strip()]
    found: list[str] = []
    for item in items:
        literal = re.fullmatch(r"""(["'])(?P<key>[0-9a-f]{64})\1""", item)
        assert literal is not None, f"unexpected entry {item!r}: expected a quoted 64-hex key"
        found.append(literal.group("key"))
    return tuple(found)


def test_the_website_parser_reads_the_array_literal() -> None:
    k = "ab" * 32
    assert website_keys(f'export const WINDOWS_RELEASE_PUBLIC_KEYS: readonly string[] = ["{k}"];') == (k,)
    assert website_keys("export const WINDOWS_RELEASE_PUBLIC_KEYS: readonly string[] = [];") == ()
    with pytest.raises(AssertionError):
        website_keys('export const WINDOWS_RELEASE_PUBLIC_KEYS = ["nothex"];')


def test_the_agent_and_the_website_pin_the_same_update_keys() -> None:
    assert WINDOWS_RELEASE_TS.is_file(), f"{WINDOWS_RELEASE_TS} is missing: these tests need the whole repository"
    assert website_keys(WINDOWS_RELEASE_TS.read_text(encoding="utf-8")) == uc.UPDATE_PUBLIC_KEYS

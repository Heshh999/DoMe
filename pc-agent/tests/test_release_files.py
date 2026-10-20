"""pc-agent/packaging/release/release_files.py and make_update_key.py: latest.json is signed only with a key
whose public half is pinned, the signature covers the exact bytes, and ``verify`` (what the release
workflow's publish job runs) refuses a tampered manifest, another key, no pinned key, a SHA256SUMS that
names another file, and a BUILD-INFO.txt that is not signed. Keys are generated in memory or in tmp_path:
no key material exists in the repository."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
INSTALLER = b"MZ" + b"\x01" * 2048


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rf = load("dome_release_files", PACKAGING / "release" / "release_files.py")
mk = load("dome_make_update_key", PACKAGING / "release" / "make_update_key.py")


def pem(key: Ed25519PrivateKey) -> str:
    raw = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return raw.decode("ascii")


def public_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def release_dir(tmp_path: Path) -> Path:
    """latest.json, SHA256SUMS, BUILD-INFO.txt and the Setup as build_installer.py writes them for 0.2.0."""
    digest = hashlib.sha256(INSTALLER).hexdigest()
    manifest = {
        "browser_addon": False,
        "product": "DoMe for Windows",
        "published_at": "2026-11-02T14:03:11Z",
        "publisher": "Jane Example",
        "release_page": "https://github.com/Heshh999/DoMe/releases/tag/v0.2.0",
        "schema": 1,
        "sha256": digest,
        "size": len(INSTALLER),
        "url": "https://github.com/Heshh999/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe",
        "version": "0.2.0",
    }
    (tmp_path / "latest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (tmp_path / "DoMe-Setup-0.2.0.exe").write_bytes(INSTALLER)
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  DoMe-Setup-0.2.0.exe\n", encoding="ascii")
    (tmp_path / "BUILD-INFO.txt").write_text(
        "DoMe-Setup-0.2.0.exe\nversion: 0.2.0\nbuild: 0.2.0+abcdef0 (build 7)\ncommit: abcdef0\n"
        f"source: push\nsha256: {digest}\nkind: release build without the browser add-on\n"
        'signature: Azure Artifact Signing, publisher "Jane Example", timestamped\n'
        "browser add-on: not included in this version (no store listing yet)\n",
        encoding="utf-8",
    )
    return tmp_path


def sign_cli(release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv(rf.KEY_ENV, pem(key))
    return int(
        rf.main(["sign", "--manifest", str(release_dir / "latest.json"), "--out", str(release_dir / "latest.json.sig")])
    )


def verify_cli(release_dir: Path, *extra: str) -> int:
    args = ["verify", "--manifest", str(release_dir / "latest.json"), "--sig", str(release_dir / "latest.json.sig")]
    return int(rf.main([*args, *extra]))


def test_sign_and_verify_round_trip(
    release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rf, "pinned_public_keys", lambda: (public_hex(key),))
    assert sign_cli(release_dir, key, monkeypatch) == 0
    sig = (release_dir / "latest.json.sig").read_bytes()
    assert sig.endswith(b"\n") and re.fullmatch(rb"[A-Za-z0-9+/]{86}==\n", sig)
    extra = ["--sha256sums", str(release_dir / "SHA256SUMS"), "--build-info", str(release_dir / "BUILD-INFO.txt")]
    assert verify_cli(release_dir, *extra, "--setup", str(release_dir / "DoMe-Setup-0.2.0.exe")) == 0
    out = capsys.readouterr().out
    assert "verified: DoMe 0.2.0" in out and 'publisher "Jane Example"' in out and "installer bytes checked" in out
    assert "PRIVATE" not in out and pem(key).splitlines()[1] not in out


def test_a_tampered_manifest_fails(
    release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rf, "pinned_public_keys", lambda: (public_hex(key),))
    assert sign_cli(release_dir, key, monkeypatch) == 0
    data = bytearray((release_dir / "latest.json").read_bytes())
    data[40] ^= 0x01
    (release_dir / "latest.json").write_bytes(bytes(data))
    assert verify_cli(release_dir) == 1
    assert "release_files:" in capsys.readouterr().err


def test_another_key_fails(release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch) -> None:
    other = Ed25519PrivateKey.generate()
    monkeypatch.setattr(rf, "pinned_public_keys", lambda: (public_hex(other),))
    (release_dir / "latest.json.sig").write_bytes(
        rf.base64.b64encode(key.sign((release_dir / "latest.json").read_bytes())) + b"\n"
    )
    assert verify_cli(release_dir) == 1


def test_no_pinned_key_fails(
    release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rf, "pinned_public_keys", lambda: ())
    (release_dir / "latest.json.sig").write_bytes(b"AAAA\n")
    assert verify_cli(release_dir) == 1
    assert "no update key is pinned" in capsys.readouterr().err


def test_sign_refuses_a_key_that_is_not_pinned(
    release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rf, "pinned_public_keys", lambda: (public_hex(Ed25519PrivateKey.generate()),))
    assert sign_cli(release_dir, key, monkeypatch) == 1
    assert "pin it first" in capsys.readouterr().err
    assert not (release_dir / "latest.json.sig").exists()


def test_sign_needs_the_key_in_the_environment(
    release_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(rf.KEY_ENV, raising=False)
    assert rf.main(["sign", "--manifest", str(release_dir / "latest.json"), "--out", str(release_dir / "x.sig")]) == 1
    assert "DOME_UPDATE_SIGNING_KEY is not set" in capsys.readouterr().err
    monkeypatch.setenv(rf.KEY_ENV, "not a key")
    assert rf.main(["sign", "--manifest", str(release_dir / "latest.json"), "--out", str(release_dir / "x.sig")]) == 1
    err = capsys.readouterr().err
    assert "not an unencrypted PEM private key" in err and "not a key" not in err


def test_wrong_sha256sums_or_unsigned_build_info_fail(
    release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rf, "pinned_public_keys", lambda: (public_hex(key),))
    assert sign_cli(release_dir, key, monkeypatch) == 0
    sums, info = release_dir / "SHA256SUMS", release_dir / "BUILD-INFO.txt"
    good_sums, good_info = sums.read_text(encoding="ascii"), info.read_text(encoding="utf-8")

    sums.write_text("0" * 64 + "  DoMe-Setup-0.2.0.exe\n", encoding="ascii")
    assert verify_cli(release_dir, "--sha256sums", str(sums)) == 1
    assert "SHA256SUMS does not list" in capsys.readouterr().err
    sums.write_text(good_sums.replace("DoMe-Setup-0.2.0.exe", "Other.exe"), encoding="ascii")
    assert verify_cli(release_dir, "--sha256sums", str(sums)) == 1

    info.write_text(
        good_info.replace(
            'signature: Azure Artifact Signing, publisher "Jane Example", timestamped',
            "signature: NOT signed: Windows SmartScreen will warn (development build)",
        ),
        encoding="utf-8",
    )
    assert verify_cli(release_dir, "--build-info", str(info)) == 1
    assert "does not say the build was signed" in capsys.readouterr().err
    info.write_text(good_info.replace('"Jane Example"', '"Someone Else"'), encoding="utf-8")
    assert verify_cli(release_dir, "--build-info", str(info)) == 1
    info.write_text(good_info.replace("version: 0.2.0", "version: 0.3.0"), encoding="utf-8")
    assert verify_cli(release_dir, "--build-info", str(info)) == 1


def test_the_pinned_key_reader_reads_the_agent_source() -> None:
    keys = rf.pinned_public_keys()
    from dome_agent.update_check import UPDATE_PUBLIC_KEYS

    assert keys == UPDATE_PUBLIC_KEYS


def test_the_pinned_key_reader_parses_a_literal(tmp_path: Path) -> None:
    module = tmp_path / "update_check.py"
    module.write_text("X = 1\nUPDATE_PUBLIC_KEYS: tuple[str, ...] = ('" + "ab" * 32 + "',)\n", encoding="utf-8")
    assert rf.pinned_public_keys(module) == ("ab" * 32,)
    module.write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(rf.ReleaseFileError):
        rf.pinned_public_keys(module)


# ----- make_update_key.py -----------------------------------------------------------------------------------


def test_make_update_key_writes_a_pem_and_prints_only_the_public_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert mk.main(["--out", str(tmp_path / "keys")]) == 0
    out = capsys.readouterr().out
    path = tmp_path / "keys" / "dome-update-key.pem"
    data = path.read_bytes()
    assert data.startswith(b"-----BEGIN PRIVATE KEY-----")
    key = rf.load_signing_key(data)
    hex_lines = [line for line in out.splitlines() if re.fullmatch(r"[0-9a-f]{64}", line)]
    assert hex_lines == [rf.public_hex(key)]
    assert "PRIVATE KEY" not in out
    for line in data.decode("ascii").splitlines()[1:-1]:
        assert line not in out
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0


def test_make_update_key_refuses_to_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert mk.main(["--out", str(tmp_path)]) == 0
    before = (tmp_path / "dome-update-key.pem").read_bytes()
    capsys.readouterr()
    assert mk.main(["--out", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "dome-update-key.pem").read_bytes() == before


def test_the_installer_itself_must_be_the_file_latest_json_describes(
    release_dir: Path, key: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mix-up of artifacts, or a wrong write order in build_installer.finish, would otherwise be published as
    verified: SHA256SUMS and BUILD-INFO.txt only quote latest.json's hash, they never hash the Setup."""
    monkeypatch.setattr(rf, "pinned_public_keys", lambda: (public_hex(key),))
    assert sign_cli(release_dir, key, monkeypatch) == 0
    setup = release_dir / "DoMe-Setup-0.2.0.exe"
    assert verify_cli(release_dir, "--setup", str(setup)) == 0
    capsys.readouterr()

    tampered = bytearray(INSTALLER)
    tampered[len(tampered) // 2] ^= 0x01  # same size, one byte changed
    setup.write_bytes(bytes(tampered))
    assert verify_cli(release_dir, "--setup", str(setup)) == 1
    assert "SHA-256 is not the one in latest.json" in capsys.readouterr().err

    setup.write_bytes(INSTALLER + b"\0")
    assert verify_cli(release_dir, "--setup", str(setup)) == 1
    assert "bytes, latest.json says" in capsys.readouterr().err

    renamed = release_dir / "DoMe-Setup-0.1.9.exe"
    renamed.write_bytes(INSTALLER)
    assert verify_cli(release_dir, "--setup", str(renamed)) == 1
    assert "latest.json describes DoMe-Setup-0.2.0.exe" in capsys.readouterr().err

    assert verify_cli(release_dir, "--setup", str(release_dir / "missing.exe")) == 1

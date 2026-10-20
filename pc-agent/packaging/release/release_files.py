"""Sign and check a release's version information (``latest.json``).

Usage (from pc-agent, after ``uv sync --locked``)::

    uv run --no-sync python -I packaging/release/release_files.py sign --manifest latest.json --out latest.json.sig
    uv run --no-sync python -I packaging/release/release_files.py verify --manifest latest.json \
        --sig latest.json.sig [--sha256sums SHA256SUMS] [--build-info BUILD-INFO.txt] [--setup DoMe-Setup-X.Y.Z.exe]

``sign`` reads the update signing key (PEM, PKCS#8, Ed25519) from the environment variable
``DOME_UPDATE_SIGNING_KEY`` only, never from the command line. It refuses when the key's public half is
not pinned in ``UPDATE_PUBLIC_KEYS`` of ``dome_agent/update_check.py`` (read from the source file, so the
pin that ships in the agent is the one checked), signs the exact bytes of ``latest.json`` and writes the
signature as base64 plus a newline, then verifies its own output. It never prints key material.

``verify`` is what the release workflow's publish job runs before anything is uploaded: the signature
against the pinned keys (it fails when none is pinned), the same field rules as the agent's update
check (``dome_agent.update_check.verify_and_parse``), and, when given, that ``SHA256SUMS`` names the same
file and hash, that ``BUILD-INFO.txt`` says the build was signed with Azure Artifact Signing, and that the
installer itself (``--setup``) is the file ``latest.json`` describes: its name is the download address's
last part and its size and SHA-256 (hashed while it streams) equal the signed values. Without ``--setup``
nothing proves the uploaded Setup is the one every agent and the website will check against.

Exit code 0 when everything passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dome_agent.update_check import ManifestError, ReleaseInfo, verify_and_parse

PC_AGENT = Path(__file__).resolve().parents[2]
UPDATE_CHECK_MODULE = PC_AGENT / "dome_agent" / "update_check.py"
KEY_ENV = "DOME_UPDATE_SIGNING_KEY"
SIGNED_LINE = "signature: Azure Artifact Signing"


class ReleaseFileError(Exception):
    pass


def pinned_public_keys(path: Path = UPDATE_CHECK_MODULE) -> tuple[str, ...]:
    """``UPDATE_PUBLIC_KEYS`` as written in ``dome_agent/update_check.py`` (read, not imported)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id == "UPDATE_PUBLIC_KEYS" and value is not None:
            return tuple(str(v) for v in ast.literal_eval(value))
    raise ReleaseFileError(f"UPDATE_PUBLIC_KEYS not found in {path}")


def load_signing_key(pem: bytes) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError):
        raise ReleaseFileError(f"{KEY_ENV} is not an unencrypted PEM private key") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ReleaseFileError(f"{KEY_ENV} is not an Ed25519 key")
    return key


def public_hex(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw.hex()


def sign_manifest(manifest: bytes, key: Ed25519PrivateKey, pinned: tuple[str, ...]) -> bytes:
    """The ``latest.json.sig`` content for ``manifest``; refuses a key that is not pinned."""
    if public_hex(key) not in pinned:
        raise ReleaseFileError(
            "the pinned public key does not match the signing key; pin it first "
            "(UPDATE_PUBLIC_KEYS in dome_agent/update_check.py and WINDOWS_RELEASE_PUBLIC_KEYS in "
            "mobile-app/src/content/windowsRelease.ts)"
        )
    signature = base64.b64encode(key.sign(manifest)) + b"\n"
    verify_and_parse(manifest, signature, public_keys=pinned)
    return signature


def check_sha256sums(release: ReleaseInfo, text: str) -> None:
    name = release.url.rsplit("/", 1)[-1]
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if [release.sha256, name] not in lines:
        raise ReleaseFileError(f"SHA256SUMS does not list {name} with the SHA-256 in latest.json")


def check_setup(release: ReleaseInfo, setup: Path) -> None:
    """The installer is exactly the file the signed ``latest.json`` names: same file name, size and SHA-256."""
    name = release.url.rsplit("/", 1)[-1]
    if setup.name != name:
        raise ReleaseFileError(f"the Setup is named {setup.name}, latest.json describes {name}")
    size = setup.stat().st_size
    if size != release.size:
        raise ReleaseFileError(f"{name} is {size} bytes, latest.json says {release.size}")
    digest = hashlib.sha256()
    with setup.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != release.sha256:
        raise ReleaseFileError(f"{name}'s SHA-256 is not the one in latest.json")


def check_build_info(release: ReleaseInfo, text: str) -> None:
    lines = text.splitlines()
    if not any(line.startswith(SIGNED_LINE) for line in lines):
        raise ReleaseFileError("BUILD-INFO.txt does not say the build was signed with Azure Artifact Signing")
    if f'publisher "{release.publisher}"' not in text:
        raise ReleaseFileError("BUILD-INFO.txt names another publisher than latest.json")
    if f"sha256: {release.sha256}" not in lines or f"version: {release.version}" not in lines:
        raise ReleaseFileError("BUILD-INFO.txt describes another file or version than latest.json")


def cmd_sign(args: argparse.Namespace) -> int:
    pem = os.environ.get(KEY_ENV, "")
    if not pem.strip():
        raise ReleaseFileError(f"{KEY_ENV} is not set (it is the release environment's secret)")
    key = load_signing_key(pem.encode("utf-8"))
    manifest = Path(args.manifest).read_bytes()
    signature = sign_manifest(manifest, key, pinned_public_keys())
    Path(args.out).write_bytes(signature)
    print(f"signed {args.manifest} with the pinned key {public_hex(key)[:16]}...; wrote {args.out}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    pinned = pinned_public_keys()
    if not pinned:
        raise ReleaseFileError("no update key is pinned in dome_agent/update_check.py (UPDATE_PUBLIC_KEYS)")
    release = verify_and_parse(Path(args.manifest).read_bytes(), Path(args.sig).read_bytes(), public_keys=pinned)
    if args.sha256sums:
        check_sha256sums(release, Path(args.sha256sums).read_text(encoding="utf-8"))
    if args.build_info:
        check_build_info(release, Path(args.build_info).read_text(encoding="utf-8"))
    if args.setup:
        check_setup(release, Path(args.setup))
    print(
        f"verified: DoMe {release.version}, {release.url.rsplit('/', 1)[-1]}, sha256 {release.sha256}, "
        f'publisher "{release.publisher}", browser add-on {"yes" if release.browser_addon else "no"}'
        + (", installer bytes checked" if args.setup else "")
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sign and check a DoMe release's latest.json")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sign", help=f"sign latest.json with the key in {KEY_ENV}")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_sign)
    v = sub.add_parser("verify", help="check latest.json, its signature and the files it describes")
    v.add_argument("--manifest", required=True)
    v.add_argument("--sig", required=True)
    v.add_argument("--sha256sums", default=None)
    v.add_argument("--build-info", default=None)
    v.add_argument("--setup", default=None, help="the DoMe-Setup-X.Y.Z.exe latest.json describes (hashed here)")
    v.set_defaults(fn=cmd_verify)
    args = parser.parse_args(argv)
    try:
        return int(args.fn(args))
    except (ReleaseFileError, ManifestError, OSError) as exc:
        reason = exc.reason if isinstance(exc, ManifestError) else str(exc)
        print(f"release_files: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Make the update signing key: the key that signs every release's version information (latest.json).

For the founder, or an engineer on a trusted machine (never inside an AI session, never on a shared PC)::

    uv run python packaging/release/make_update_key.py --out <folder>

Writes ``<folder>/dome-update-key.pem`` (an Ed25519 private key, PKCS#8 PEM, created readable by its owner
only where the system supports that) and prints ONLY the public key, 64 hex characters, which is not
secret. Then: paste the whole .pem file into the GitHub Environment secret ``DOME_UPDATE_SIGNING_KEY``
(environment ``release``), delete the file, and send the printed public key to the engineer, who pins it
in ``dome_agent/update_check.py`` and ``mobile-app/src/content/windowsRelease.ts``
(docs/LAUNCH_SETUP.md section 17). Refuses to overwrite an existing file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FILENAME = "dome-update-key.pem"


def make_key(folder: Path) -> tuple[Path, str]:
    """Write a new key into ``folder`` and return its path and the public key as hex."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FILENAME
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    # O_EXCL: never overwrite a key (an existing one may already be in use). 0o600 where POSIX permissions exist;
    # on Windows the file inherits the folder's permissions (your own user folder).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return path, public


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make DoMe's update signing key (prints only the public key)")
    parser.add_argument("--out", type=Path, required=True, help="the folder for dome-update-key.pem")
    args = parser.parse_args(argv)
    try:
        path, public = make_key(args.out)
    except FileExistsError:
        print(
            f"make_update_key: {args.out / FILENAME} already exists; nothing was changed. "
            "Use another folder, or delete the old file if it was never used.",
            file=sys.stderr,
        )
        return 1
    print(f"Wrote the private key to {path}")
    print(
        "Paste the whole .pem file into the GitHub secret DOME_UPDATE_SIGNING_KEY (environment release), then "
        "delete the file. Send the public key below to the engineer (this line is not secret):"
    )
    print(public)
    return 0


if __name__ == "__main__":
    sys.exit(main())

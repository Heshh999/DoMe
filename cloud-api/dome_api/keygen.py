"""Generate the EdDSA (Ed25519) entitlement signing key for development.

Usage: ``uv run dome-api-gen-entitlement-key [path]`` — default path is
``DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH`` or ``./.dome-dev-state/entitlement-ed25519.pem``.
Refuses to overwrite an existing file unless ``--force`` is given. The file is created 0600.
Production keys should come from the deployment's secret store instead (see README).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from joserfc.jwk import OKPKey

DEFAULT_PATH = Path("./.dome-dev-state/entitlement-ed25519.pem")


def generate_pem() -> bytes:
    key = OKPKey.generate_key("Ed25519")
    pem = key.as_pem(private=True)
    return pem if isinstance(pem, bytes) else pem.encode("ascii")


def write_key(path: Path, *, force: bool = False) -> Path:
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists (use --force to replace it)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(generate_pem())
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the DoMe entitlement signing key (Ed25519, PKCS8 PEM).")
    parser.add_argument(
        "path", nargs="?", default=os.environ.get("DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH") or str(DEFAULT_PATH)
    )
    parser.add_argument("--force", action="store_true", help="replace an existing key file")
    args = parser.parse_args(argv)
    try:
        out = write_key(Path(args.path), force=args.force)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"wrote {out} (Ed25519 private key, mode 0600). Keep it out of version control.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

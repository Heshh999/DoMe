"""PC identity: ES256 key pair, link record (pc_id / account_id) and the opaque PC credential.

Files under ``<state dir>/secrets``:

* ``pc_key.bin``        — DPAPI/0600-protected PKCS8 PEM of the PC's P-256 private key
* ``pc_credential.bin`` — DPAPI/0600-protected opaque credential issued at link time

``<state dir>/identity.json`` holds the non-secret link record. The pc_id/account_id are fixed
at link time (``rules.agent_identity``); a different id reported by the relay is a hard stop.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from dome_protocol import (
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    private_key_from_pem,
    private_key_to_pem,
)
from pydantic import BaseModel, ConfigDict, Field

from . import secrets as secret_files
from .logsetup import get_logger

log = get_logger(__name__)

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class LinkRecord(BaseModel):
    """Non-secret link information written exactly once by the device-link flow."""

    model_config = ConfigDict(extra="forbid")

    pc_id: str = Field(pattern=UUID_RE)
    account_id: str = Field(pattern=UUID_RE)
    pc_name: str = Field(min_length=1, max_length=64)
    relay_url: str = Field(min_length=1, max_length=512)
    api_url: str = Field(min_length=1, max_length=512)
    enabled: bool = True
    linked_at: str


class Identity:
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._secrets_dir = state_dir / "secrets"
        self._key_path = self._secrets_dir / "pc_key.bin"
        self._credential_path = self._secrets_dir / "pc_credential.bin"
        self._link_path = state_dir / "identity.json"
        self._key: ec.EllipticCurvePrivateKey | None = None
        self._link: LinkRecord | None = None
        self._load_link()

    # ----- key -------------------------------------------------------------------------------
    def ensure_key(self) -> ec.EllipticCurvePrivateKey:
        if self._key is not None:
            return self._key
        pem = secret_files.read_secret_file(self._key_path)
        if pem is None:
            key = generate_private_key()
            secret_files.write_secret_file(self._key_path, private_key_to_pem(key))
            log.info("generated new PC identity key", protection=secret_files.protection_label())
        else:
            key = private_key_from_pem(pem)
        self._key = key
        return key

    @property
    def public_jwk(self) -> dict[str, str]:
        return jwk_from_public_key(self.ensure_key().public_key())

    @property
    def kid(self) -> str:
        return kid_from_jwk(self.public_jwk)

    # ----- link record -----------------------------------------------------------------------
    def _load_link(self) -> None:
        if not self._link_path.exists():
            self._link = None
            return
        try:
            self._link = LinkRecord.model_validate_json(self._link_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("identity.json is unreadable; treating the PC as not linked", error=str(exc))
            self._link = None

    @property
    def link(self) -> LinkRecord | None:
        return self._link

    @property
    def is_linked(self) -> bool:
        return self._link is not None and self.read_credential() is not None

    @property
    def pc_id(self) -> str | None:
        return self._link.pc_id if self._link else None

    @property
    def account_id(self) -> str | None:
        return self._link.account_id if self._link else None

    @property
    def pc_name(self) -> str:
        return self._link.pc_name if self._link else "This PC"

    def store_link(self, record: LinkRecord, pc_credential: str) -> None:
        """Write the credential first (secret), then the public link record (atomic replace)."""
        secret_files.write_secret_file(self._credential_path, pc_credential.encode("ascii"))
        data = record.model_dump_json(indent=2).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(self._state_dir))
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._link_path)
        self._link = record
        log.info("PC linked", pc_id=record.pc_id, account_id=record.account_id)

    def read_credential(self) -> str | None:
        raw = secret_files.read_secret_file(self._credential_path)
        if raw is None:
            return None
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            log.error("pc credential file is corrupt")
            return None

    def discard_credential(self) -> None:
        """Called on ``revoked``: the cloud side is gone; local grants stay for inspection."""
        secret_files.delete_secret_file(self._credential_path)
        log.warning("PC credential discarded; re-link required")

    def unlink(self) -> None:
        self.discard_credential()
        try:
            self._link_path.unlink()
        except FileNotFoundError:
            pass
        self._link = None

    def status_summary(self) -> dict[str, object]:
        return {
            "linked": self.is_linked,
            "pc_id": self.pc_id,
            "account_id": self.account_id,
            "pc_name": self.pc_name if self._link else None,
            "kid": self.kid if self._key_path.exists() else None,
            "key_protection": secret_files.protection_label(),
        }

    @staticmethod
    def write_json_safely(path: Path, payload: dict[str, object]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

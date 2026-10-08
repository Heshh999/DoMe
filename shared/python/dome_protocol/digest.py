from __future__ import annotations

import hashlib
from typing import Any

from .keys import b64url_encode
from .strict_json import dumps_compact


def sha256_b64url(data: bytes) -> str:
    return b64url_encode(hashlib.sha256(data).digest())


def challenge_digest(challenge_text: str) -> str:
    """Digest of the challenge exactly as serialised by the PC (the relay forwards the text)."""
    return sha256_b64url(challenge_text.encode("utf-8"))


def command_digest(parsed_command: dict[str, Any]) -> str:
    """Digest of the execution-relevant fields of a verified command payload.

    Used by the durable journal: a later command with the same ``command_id`` is only treated as
    a duplicate (and answered with the previous result) when this digest matches exactly.
    """
    fields = {
        k: parsed_command.get(k)
        for k in ("command_id", "account_id", "controller_id", "target_pc_id", "action", "params", "target", "expires_at")
    }
    return sha256_b64url(_canonical(fields).encode("utf-8"))


def _canonical(value: Any) -> str:
    """Sorted-key compact JSON for digests we compute ourselves (never used for verification)."""
    import json

    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False, allow_nan=False)


def pairing_verification_code(pairing_id: str, pc_id: str, kid: str) -> str:
    """6-digit code shown on both devices during pairing.

    Both sides compute it independently from the pairing id, the PC id and the controller key id
    they each believe is being paired; a relay that substituted a different key would produce a
    different code on the phone and the PC.
    """
    material = f"dome-pair-v1|{pairing_id}|{pc_id}|{kid}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    number = int.from_bytes(digest[:8], "big") % 1_000_000
    return f"{number:06d}"


__all__ = ["sha256_b64url", "challenge_digest", "command_digest", "pairing_verification_code", "dumps_compact"]

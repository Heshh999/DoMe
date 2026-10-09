"""Verified-command helpers shared by the relay (routing checks) and the agent (authorization).

Both call ``verify_and_parse_command`` / ``verify_and_parse_confirmation`` with a *key record*
resolver. The record binds the signing key to the controller it belongs to, so a controller can
never sign a payload that names another controller's id (and thereby another grant).
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .digest import command_digest
from .errors import ProtocolError
from .keys import b64url_encode
from .registry import ActionSpec, Registry, load_registry
from .schemas import Schemas, load_schemas
from .signing import Envelope, verify_envelope
from .timeutil import check_command_window, format_rfc3339, now_utc

_VERSION_RE = re.compile(r"\A([0-9]+)\.([0-9]+)\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class KeyRecord:
    """A currently paired, non-revoked controller key as the verifier knows it."""

    controller_id: str
    account_id: str
    jwk: dict[str, str]
    capabilities: tuple[str, ...] = ()


KeyResolver = Callable[[str], KeyRecord | None]


@dataclass(frozen=True, slots=True)
class VerifiedCommand:
    envelope: Envelope
    payload: dict[str, Any]
    key: KeyRecord
    spec: ActionSpec
    params: dict[str, Any]
    target: dict[str, Any] | None
    digest: str

    @property
    def command_id(self) -> str:
        return self.payload["command_id"]

    @property
    def controller_id(self) -> str:
        return self.payload["controller_id"]

    @property
    def account_id(self) -> str:
        return self.payload["account_id"]

    @property
    def target_pc_id(self) -> str:
        return self.payload["target_pc_id"]


@dataclass(frozen=True, slots=True)
class VerifiedConfirmation:
    envelope: Envelope
    payload: dict[str, Any]
    key: KeyRecord

    @property
    def command_id(self) -> str:
        return self.payload["command_id"]

    @property
    def challenge_id(self) -> str:
        return self.payload["challenge_id"]

    @property
    def approved(self) -> bool:
        return self.payload["decision"] == "approve"


def protocol_compatible(peer_version: str, supported: tuple[str, ...]) -> bool:
    if not isinstance(peer_version, str):
        return False
    m = _VERSION_RE.match(peer_version)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    best_minor = -1
    for v in supported:
        sm = _VERSION_RE.match(v)
        if sm and int(sm.group(1)) == major:
            best_minor = max(best_minor, int(sm.group(2)))
    return best_minor >= 0 and minor <= best_minor


def _verify(raw_envelope: Any, resolve_key: KeyResolver, registry: Registry) -> tuple[Envelope, dict[str, Any], KeyRecord]:
    records: dict[str, KeyRecord] = {}

    def resolve_jwk(kid: str) -> dict[str, str] | None:
        rec = resolve_key(kid)
        if rec is None:
            return None
        records[kid] = rec
        return rec.jwk

    envelope, payload = verify_envelope(
        raw_envelope,
        resolve_jwk,
        max_payload_bytes=registry.limits["max_payload_bytes"],
        max_depth=registry.limits["max_json_depth"],
    )
    return envelope, payload, records[envelope.kid]


def _bind(payload: dict[str, Any], key: KeyRecord) -> None:
    if payload.get("controller_id") != key.controller_id:
        raise ProtocolError("CONTROLLER_MISMATCH", "payload controller_id does not belong to the signing key")
    if "account_id" in payload and payload["account_id"] != key.account_id:
        raise ProtocolError("ACCOUNT_MISMATCH", "payload account_id does not belong to the signing key")


def verify_and_parse_command(
    raw_envelope: Any,
    resolve_key: KeyResolver,
    *,
    now: datetime | None = None,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> VerifiedCommand:
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    envelope, payload, key = _verify(raw_envelope, resolve_key, registry)
    schemas.validate_command_payload(payload)
    if not protocol_compatible(payload["protocol_version"], (registry.protocol_version,)):
        raise ProtocolError(
            "PROTOCOL_INCOMPATIBLE",
            "unsupported protocol version",
            detail={"peer": payload["protocol_version"], "supported": [registry.protocol_version]},
        )
    _bind(payload, key)
    check_command_window(
        payload["issued_at"],
        payload["expires_at"],
        now=now,
        max_lifetime_seconds=registry.limits["max_command_lifetime_seconds"],
        max_skew_seconds=registry.limits["max_clock_skew_seconds"],
    )
    spec = registry.get(payload["action"])
    params = registry.validate_params(spec.name, payload["params"])
    target = registry.validate_target(spec.name, payload["target"])
    return VerifiedCommand(
        envelope=envelope,
        payload=payload,
        key=key,
        spec=spec,
        params=params,
        target=target,
        digest=command_digest(envelope.payload),
    )


def verify_and_parse_confirmation(
    raw_envelope: Any,
    resolve_key: KeyResolver,
    *,
    now: datetime | None = None,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> VerifiedConfirmation:
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    envelope, payload, key = _verify(raw_envelope, resolve_key, registry)
    schemas.validate_confirmation_payload(payload)
    if not protocol_compatible(payload["protocol_version"], (registry.protocol_version,)):
        raise ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version")
    _bind(payload, key)
    check_command_window(
        payload["issued_at"],
        payload["expires_at"],
        now=now,
        max_lifetime_seconds=registry.limits["confirmation_challenge_lifetime_seconds"] + registry.limits["max_clock_skew_seconds"],
        max_skew_seconds=registry.limits["max_clock_skew_seconds"],
    )
    return VerifiedConfirmation(envelope=envelope, payload=payload, key=key)


@dataclass(frozen=True, slots=True)
class VerifiedHelloProof:
    """A controller hello proof whose signature, binding and window all checked out."""

    envelope: Envelope
    payload: dict[str, Any]
    key: KeyRecord

    @property
    def nonce(self) -> str:
        return str(self.payload["nonce"])


def build_hello_proof_payload(
    *, kid: str, account_id: str, now: datetime | None = None, registry: Registry | None = None
) -> dict[str, Any]:
    """The payload a controller signs to prove possession of its key when opening a relay socket
    (``rules.controller_socket_identity``). Lifetime = confirmation_challenge_lifetime_seconds."""
    registry = registry or load_registry()
    issued = now or now_utc()
    return {
        "type": "hello_proof",
        "protocol_version": registry.protocol_version,
        "kid": kid,
        "account_id": account_id,
        "issued_at": format_rfc3339(issued),
        "expires_at": format_rfc3339(issued + timedelta(seconds=registry.limits["confirmation_challenge_lifetime_seconds"])),
        "nonce": b64url_encode(secrets.token_bytes(16)),
    }


def verify_hello_proof(
    raw_envelope: Any,
    resolve_key: KeyResolver,
    *,
    expected_kid: str,
    expected_account_id: str,
    now: datetime | None = None,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> VerifiedHelloProof:
    """Verify ``hello.proof`` before binding a controller socket: signature by the key the relay
    stored for (account, kid), payload/envelope/hello kids all equal, account bound to the session,
    normal time window. Nonce single-use is the caller's (relay's) job."""
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    envelope, payload, key = _verify(raw_envelope, resolve_key, registry)
    schemas.validate_hello_proof_payload(payload)
    if not protocol_compatible(payload["protocol_version"], (registry.protocol_version,)):
        raise ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version")
    if envelope.kid != expected_kid or payload["kid"] != expected_kid:
        raise ProtocolError("UNKNOWN_KEY", "hello proof is not for this socket's kid")
    if payload["account_id"] != expected_account_id or key.account_id != expected_account_id:
        raise ProtocolError("ACCOUNT_MISMATCH", "hello proof account_id does not belong to this session")
    skew = registry.limits["max_clock_skew_seconds"]
    check_command_window(
        payload["issued_at"],
        payload["expires_at"],
        now=now,
        max_lifetime_seconds=registry.limits["confirmation_challenge_lifetime_seconds"] + skew,
        max_skew_seconds=skew,
    )
    return VerifiedHelloProof(envelope=envelope, payload=payload, key=key)

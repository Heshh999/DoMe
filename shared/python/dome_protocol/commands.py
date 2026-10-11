"""Verified-command helpers shared by the relay (routing checks) and the agent (authorization).

Both call ``verify_and_parse_command`` / ``verify_and_parse_confirmation`` with a *key record*
resolver. The record binds the signing key to the controller it belongs to, so a controller can
never sign a payload that names another controller's id (and thereby another grant).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .digest import command_digest
from .errors import ProtocolError
from .keys import b64url_encode
from .registry import ActionSpec, Registry, load_registry
from .schemas import Schemas, load_schemas
from .signing import Envelope, verify_envelope
from .timeutil import check_command_window, format_rfc3339, now_utc, parse_rfc3339
from .versions import (
    INPUT_BATCH_PROTOCOL_VERSION,
    negotiate_version,
    protocol_compatible,
    version_below,
)

# The version helpers live in .versions (the registry needs them while loading) and are re-exported here.
__all__ = [
    "INPUT_BATCH_PROTOCOL_VERSION",
    "KEYBOARD_EVENT_TYPES",
    "POINTER_EVENT_TYPES",
    "KeyRecord",
    "KeyResolver",
    "VerifiedCommand",
    "VerifiedConfirmation",
    "VerifiedHelloProof",
    "VerifiedInputBatch",
    "build_hello_proof_payload",
    "build_input_batch_payload",
    "input_event_capabilities",
    "negotiate_version",
    "protocol_compatible",
    "verify_and_parse_command",
    "verify_and_parse_confirmation",
    "verify_and_parse_input_batch",
    "verify_hello_proof",
]


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


def _require_since(payload_version: str, since: str) -> None:
    """rules.command_version_stamping: a payload stamped below the version that defines it is refused."""
    if version_below(payload_version, since):
        raise ProtocolError(
            "PROTOCOL_INCOMPATIBLE",
            f"stamped protocol version is below {since}, which introduced it",
            detail={"peer": payload_version, "required": since},
        )


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
    _require_since(payload["protocol_version"], spec.since)
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


POINTER_EVENT_TYPES = frozenset({"pointer_move", "pointer_button", "pointer_scroll"})
KEYBOARD_EVENT_TYPES = frozenset({"text", "key", "shortcut"})


def input_event_capabilities(events: Iterable[dict[str, Any]]) -> set[str]:
    """The capabilities a grant must hold to send these events (rules.input_sessions)."""
    needed: set[str] = set()
    for ev in events:
        kind = ev.get("type")
        if kind in POINTER_EVENT_TYPES:
            needed.add("pointer")
        elif kind in KEYBOARD_EVENT_TYPES:
            needed.add("keyboard")
        else:
            raise ProtocolError("MALFORMED_MESSAGE", f"unknown input event type {kind!r}")
    return needed


@dataclass(frozen=True, slots=True)
class VerifiedInputBatch:
    envelope: Envelope
    payload: dict[str, Any]
    key: KeyRecord

    @property
    def input_session_id(self) -> str:
        return str(self.payload["input_session_id"])

    @property
    def seq(self) -> int:
        return int(self.payload["seq"])

    @property
    def target_pc_id(self) -> str:
        return str(self.payload["target_pc_id"])

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self.payload["events"])

    @property
    def required_capabilities(self) -> set[str]:
        return input_event_capabilities(self.payload["events"])

    def age_seconds(self, now: datetime | None = None) -> float:
        """Seconds since the controller issued the batch (the agent's input-age budget check)."""
        return ((now or now_utc()) - parse_rfc3339(self.payload["issued_at"])).total_seconds()


def build_input_batch_payload(
    *,
    account_id: str,
    controller_id: str,
    target_pc_id: str,
    input_session_id: str,
    seq: int,
    events: list[dict[str, Any]],
    now: datetime | None = None,
    registry: Registry | None = None,
) -> dict[str, Any]:
    registry = registry or load_registry()
    issued = now or now_utc()
    return {
        "type": "input_batch",
        # rules.command_version_stamping: the version that defined input batches, so older PCs accept it
        "protocol_version": INPUT_BATCH_PROTOCOL_VERSION,
        "account_id": account_id,
        "controller_id": controller_id,
        "target_pc_id": target_pc_id,
        "input_session_id": input_session_id,
        "seq": seq,
        "issued_at": format_rfc3339(issued),
        "expires_at": format_rfc3339(issued + timedelta(seconds=registry.limits["input_batch_lifetime_seconds"])),
        "events": events,
    }


def verify_and_parse_input_batch(
    raw_envelope: Any,
    resolve_key: KeyResolver,
    *,
    now: datetime | None = None,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> VerifiedInputBatch:
    """Relay and agent both call this: signature over the exact bytes, schema, protocol compatibility,
    controller/account binding to the key record, and the batch window (lifetime <= input_batch_lifetime_seconds).
    Session ownership, sequence, age budget and capability coverage are the caller's checks."""
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    envelope, payload, key = _verify(raw_envelope, resolve_key, registry)
    schemas.validate_input_batch_payload(payload)
    if not protocol_compatible(payload["protocol_version"], (registry.protocol_version,)):
        raise ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version")
    _require_since(payload["protocol_version"], INPUT_BATCH_PROTOCOL_VERSION)
    _bind(payload, key)
    skew = registry.limits["max_clock_skew_seconds"]
    check_command_window(
        payload["issued_at"],
        payload["expires_at"],
        now=now,
        max_lifetime_seconds=registry.limits["input_batch_lifetime_seconds"] + skew,
        max_skew_seconds=skew,
    )
    return VerifiedInputBatch(envelope=envelope, payload=payload, key=key)

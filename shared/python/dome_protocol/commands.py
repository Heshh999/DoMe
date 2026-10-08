"""Verified-command helpers shared by the relay (routing checks) and the agent (authorization).

The relay and the agent both call ``verify_and_parse_command``; the agent then applies its
local policy (grants, availability, journal, confirmation) on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .digest import command_digest
from .errors import ProtocolError
from .registry import ActionSpec, Registry, load_registry
from .schemas import Schemas, load_schemas
from .signing import Envelope, verify_envelope
from .timeutil import check_command_window


@dataclass(frozen=True, slots=True)
class VerifiedCommand:
    envelope: Envelope
    payload: dict[str, Any]
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


def protocol_compatible(peer_version: str, supported: tuple[str, ...]) -> bool:
    try:
        major, minor = (int(x) for x in peer_version.split("."))
    except ValueError:
        return False
    best_minor = -1
    for v in supported:
        smaj, smin = (int(x) for x in v.split("."))
        if smaj == major:
            best_minor = max(best_minor, smin)
    return best_minor >= 0 and minor <= best_minor


def verify_and_parse_command(
    raw_envelope: Any,
    resolve_jwk: Callable[[str], dict[str, str] | None],
    *,
    now: datetime | None = None,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> VerifiedCommand:
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    envelope, payload = verify_envelope(
        raw_envelope,
        resolve_jwk,
        max_payload_bytes=registry.limits["max_payload_bytes"],
        max_depth=registry.limits["max_json_depth"],
    )
    schemas.validate_command_payload(payload)
    if not protocol_compatible(payload["protocol_version"], (registry.protocol_version,)):
        raise ProtocolError(
            "PROTOCOL_INCOMPATIBLE",
            "unsupported protocol version",
            detail={"peer": payload["protocol_version"], "supported": [registry.protocol_version]},
        )
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
    return VerifiedCommand(envelope=envelope, payload=payload, spec=spec, params=params, target=target, digest=command_digest(payload))


def verify_and_parse_confirmation(
    raw_envelope: Any,
    resolve_jwk: Callable[[str], dict[str, str] | None],
    *,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> tuple[Envelope, dict[str, Any]]:
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    envelope, payload = verify_envelope(
        raw_envelope,
        resolve_jwk,
        max_payload_bytes=registry.limits["max_payload_bytes"],
        max_depth=registry.limits["max_json_depth"],
    )
    schemas.validate_confirmation_payload(payload)
    if not protocol_compatible(payload["protocol_version"], (registry.protocol_version,)):
        raise ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported protocol version")
    return envelope, payload

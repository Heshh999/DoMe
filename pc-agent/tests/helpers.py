"""Test helpers: a Python-signed controller, linked identities and frame builders."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from dome_protocol import (
    challenge_digest,
    dumps_compact,
    format_rfc3339,
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    now_utc,
    sign_payload,
)
from dome_protocol.keys import b64url_encode

from dome_agent.identity import Identity, LinkRecord
from dome_agent.store import Store

ALL_CAPS = ("status", "media", "volume", "apps", "lock", "power")


def nonce() -> str:
    import secrets

    return b64url_encode(secrets.token_bytes(16))


class Controller:
    """A paired phone as the tests see it: a P-256 key, kid, controller_id and signing helpers."""

    def __init__(self, account_id: str, pc_id: str, *, display_name: str = "Test phone", capabilities: tuple[str, ...] = ALL_CAPS) -> None:
        self.key = generate_private_key()
        self.jwk = jwk_from_public_key(self.key.public_key())
        self.kid = kid_from_jwk(self.jwk)
        self.controller_id = str(uuid.uuid4())
        self.account_id = account_id
        self.pc_id = pc_id
        self.display_name = display_name
        self.capabilities = capabilities

    # ----- store / snapshot --
    def grant_locally(self, store: Store, snapshot_id: str | None = None, capabilities: tuple[str, ...] | None = None) -> None:
        store.add_grant(
            controller_id=self.controller_id,
            kid=self.kid,
            public_jwk=self.jwk,
            capabilities=capabilities or self.capabilities,
            display_name=self.display_name,
            snapshot_id=snapshot_id,
        )

    def snapshot_entry(self, *, capabilities: tuple[str, ...] | None = None, status: str = "active") -> dict[str, Any]:
        return {
            "controller_id": self.controller_id,
            "kid": self.kid,
            "capabilities": list(capabilities or self.capabilities),
            "display_name": self.display_name,
            "status": status,
        }

    # ----- payloads --
    def command_payload(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
        *,
        command_id: str | None = None,
        lifetime: int = 30,
        issued_at: Any = None,
        origin: dict[str, Any] | None = None,
        target_pc_id: str | None = None,
        account_id: str | None = None,
        controller_id: str | None = None,
    ) -> dict[str, Any]:
        issued = issued_at or now_utc()
        payload: dict[str, Any] = {
            "type": "command",
            "protocol_version": "1.0",
            "command_id": command_id or str(uuid.uuid4()),
            "account_id": account_id or self.account_id,
            "controller_id": controller_id or self.controller_id,
            "target_pc_id": target_pc_id or self.pc_id,
            "action": action,
            "params": params or {},
            "target": target,
        }
        if origin is not None:
            payload["origin"] = origin
        payload["issued_at"] = format_rfc3339(issued)
        payload["expires_at"] = format_rfc3339(issued + timedelta(seconds=lifetime))
        payload["nonce"] = nonce()
        return payload

    def sign(self, payload: dict[str, Any], *, key: Any = None) -> dict[str, Any]:
        text = dumps_compact(payload)
        env = sign_payload(key or self.key, text)
        return env.to_dict()

    def sign_text(self, text: str) -> dict[str, Any]:
        return sign_payload(self.key, text).to_dict()

    def command(self, action: str, params: dict[str, Any] | None = None, target: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        """Signed envelope for a command."""
        return self.sign(self.command_payload(action, params, target, **kw))

    def confirmation(self, command_id: str, challenge_text: str, *, decision: str = "approve", challenge_id: str | None = None, digest: str | None = None, lifetime: int = 60, key: Any = None) -> dict[str, Any]:
        from dome_protocol import loads_strict

        challenge = loads_strict(challenge_text)
        now = now_utc()
        payload = {
            "type": "confirmation",
            "protocol_version": "1.0",
            "command_id": command_id,
            "challenge_id": challenge_id or challenge["challenge_id"],
            "challenge_digest": digest or challenge_digest(challenge_text),
            "controller_id": self.controller_id,
            "target_pc_id": self.pc_id,
            "decision": decision,
            "issued_at": format_rfc3339(now),
            "expires_at": format_rfc3339(now + timedelta(seconds=lifetime)),
            "nonce": nonce(),
        }
        return self.sign(payload, key=key)


def relay_command_frame(envelope: dict[str, Any], connection_id: str | None = None) -> dict[str, Any]:
    return {"type": "command", "envelope": envelope, "relay": {"received_at": format_rfc3339(now_utc()), "connection_id": connection_id or str(uuid.uuid4())}}


def relay_confirmation_frame(envelope: dict[str, Any], connection_id: str | None = None) -> dict[str, Any]:
    return {"type": "confirmation", "envelope": envelope, "relay": {"received_at": format_rfc3339(now_utc()), "connection_id": connection_id or str(uuid.uuid4())}}


def link_identity(state_dir: Path, pc_id: str, account_id: str, *, credential: str, api_url: str = "http://127.0.0.1:1", relay_url: str = "ws://127.0.0.1:1/ws/agent", pc_name: str = "Test PC") -> Identity:
    identity = Identity(state_dir)
    identity.ensure_key()
    identity.store_link(
        LinkRecord(pc_id=pc_id, account_id=account_id, pc_name=pc_name, relay_url=relay_url, api_url=api_url, enabled=True, linked_at=format_rfc3339(now_utc())),
        credential,
    )
    return identity


def payload_of(envelope: dict[str, Any]) -> dict[str, Any]:
    from dome_protocol import loads_strict

    return loads_strict(envelope["payload"])

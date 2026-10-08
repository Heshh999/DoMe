"""Pairing on the PC (ADR-0001 D5, design → "Pairing on the PC").

The PC generates a 20-symbol Crockford code locally and registers only ``code_hash`` with the backend.
The code itself lives in this process's memory for the session lifetime (5 min) and is shown to the
local user as text + QR (``<app origin>/pair#code=…``, fragment → never in a Referer). When the relay
forwards a ``pairing_request`` the agent (1) ignores it unless ``code_hash`` matches the live session,
(2) recomputes ``kid_from_jwk(public_jwk)`` and refuses a mismatch, (3) computes the 6-digit
verification code ``HMAC(code, pairing_id|pc_id|kid)`` and shows it with the untrusted display name
and requested scopes. Only an explicit local Approve stores the grant and sends ``pairing_decision``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from dome_protocol import (
    ProtocolError,
    format_pairing_code,
    generate_pairing_code,
    kid_from_jwk,
    now_utc,
    pairing_code_handle,
    pairing_verification_code,
    parse_rfc3339,
)

from .frames import pairing_decision_frame
from .logsetup import get_logger
from .ui import AgentUI, PairingApproval, PairingDisplay

if TYPE_CHECKING:
    from .api import ApiClient
    from .identity import Identity
    from .relay_client import TokenManager
    from .store import Store

log = get_logger(__name__)

Sender = Callable[[dict[str, Any]], Awaitable[bool]]
CAPABILITIES = ("status", "media", "volume", "apps", "lock", "power")


@dataclass(slots=True)
class PendingPairing:
    pairing_id: str
    kid: str
    public_jwk: dict[str, str]
    display_name: str
    requested_capabilities: tuple[str, ...]
    verification_code: str
    expires_at: str

    def approval(self) -> PairingApproval:
        return PairingApproval(
            self.pairing_id,
            self.display_name,
            self.verification_code,
            self.requested_capabilities,
            self.kid,
            self.expires_at,
        )


@dataclass(slots=True)
class PairingSession:
    code: str  # SECRET: memory only
    code_hash: str
    pairing_id: str
    expires_at: str
    qr_url: str
    requests: dict[str, PendingPairing] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return parse_rfc3339(self.expires_at) < now_utc()

    def display(self) -> PairingDisplay:
        return PairingDisplay(self.pairing_id, format_pairing_code(self.code), self.qr_url, self.expires_at)

    def public_view(self) -> dict[str, Any]:
        """Status without the code (for `status` / diagnostics)."""
        return {
            "pairing_id": self.pairing_id,
            "expires_at": self.expires_at,
            "pending_requests": [
                {
                    "pairing_id": r.pairing_id,
                    "display_name": r.display_name,
                    "requested_capabilities": list(r.requested_capabilities),
                    "expires_at": r.expires_at,
                }
                for r in self.requests.values()
            ],
        }


def app_origin(api_url: str) -> str:
    parts = urlsplit(api_url)
    return f"{parts.scheme}://{parts.netloc}"


class PairingManager:
    def __init__(
        self, store: Store, identity: Identity, api: ApiClient, tokens: TokenManager, send: Sender, ui: AgentUI
    ) -> None:
        self._store = store
        self._identity = identity
        self._api = api
        self._tokens = tokens
        self._send = send
        self._ui = ui
        self._session: PairingSession | None = None

    @property
    def session(self) -> PairingSession | None:
        if self._session is not None and self._session.expired:
            log.info("pairing session expired", pairing_id=self._session.pairing_id)
            self._session = None
        return self._session

    # ----- start ---------------------------------------------------------------------------------------------
    async def start(self) -> PairingSession:
        if self._identity.pc_id is None:
            raise ProtocolError("PC_OFFLINE", "This PC is not linked yet. Run `dome-agent link` first.")
        code = generate_pairing_code()
        code_hash = pairing_code_handle(code)
        token = await self._tokens.get()
        pairing_id, expires_at = await self._api.pairing_start(token, code_hash)
        qr_url = f"{app_origin(self._api.base_url)}/pair#code={format_pairing_code(code)}"
        self._session = PairingSession(
            code=code, code_hash=code_hash, pairing_id=pairing_id, expires_at=expires_at, qr_url=qr_url
        )
        log.info("pairing session started", pairing_id=pairing_id, expires_at=expires_at)
        self._ui.show_pairing_code(self._session.display())
        return self._session

    def cancel(self) -> None:
        self._session = None

    # ----- inbound request -------------------------------------------------------------------------------------
    async def handle_request(self, frame: dict[str, Any]) -> PendingPairing | None:
        session = self.session
        if session is None or frame["code_hash"] != session.code_hash:
            self._store.add_security_event("pairing_request_unmatched", pairing_id=frame["pairing_id"])
            log.warning("pairing_request ignored: no matching live pairing session", pairing_id=frame["pairing_id"])
            return None
        try:
            computed_kid = kid_from_jwk(frame["public_jwk"])
        except ProtocolError as exc:
            self._store.add_security_event("pairing_request_bad_jwk", pairing_id=frame["pairing_id"], code=exc.code)
            await self._send(pairing_decision_frame(frame["pairing_id"], "decline", frame["kid"], []))
            return None
        if computed_kid != frame["kid"]:
            self._store.add_security_event("pairing_request_kid_mismatch", pairing_id=frame["pairing_id"])
            log.error("pairing_request kid does not match its public key; declined", pairing_id=frame["pairing_id"])
            await self._send(pairing_decision_frame(frame["pairing_id"], "decline", frame["kid"], []))
            return None
        pc_id = self._identity.pc_id
        assert pc_id is not None
        verification = pairing_verification_code(session.code, frame["pairing_id"], pc_id, computed_kid)
        expires_at = frame["expires_at"]
        if parse_rfc3339(expires_at) > parse_rfc3339(session.expires_at) + timedelta(seconds=5):
            expires_at = session.expires_at  # the request can never outlive the local code
        pending = PendingPairing(
            pairing_id=frame["pairing_id"],
            kid=computed_kid,
            public_jwk=dict(frame["public_jwk"]),
            display_name=str(frame["controller_display_name"])[:64],
            requested_capabilities=tuple(c for c in frame["requested_capabilities"] if c in CAPABILITIES),
            verification_code=verification,
            expires_at=expires_at,
        )
        session.requests[pending.pairing_id] = pending
        log.info("pairing request received; waiting for local approval", pairing_id=pending.pairing_id)
        self._ui.show_pairing_request(pending.approval())
        return pending

    def pending(self, pairing_id: str) -> PendingPairing | None:
        session = self.session
        if session is None:
            return None
        pending = session.requests.get(pairing_id)
        if pending is not None and parse_rfc3339(pending.expires_at) < now_utc():
            del session.requests[pairing_id]
            return None
        return pending

    def pending_requests(self) -> list[PendingPairing]:
        session = self.session
        if session is None:
            return []
        return [p for p in list(session.requests.values()) if self.pending(p.pairing_id) is not None]

    # ----- local decision ---------------------------------------------------------------------------------------
    async def approve(self, pairing_id: str, granted: list[str] | None = None) -> PendingPairing:
        pending = self.pending(pairing_id)
        if pending is None:
            raise ProtocolError("PAIRING_CODE_INVALID", "No pending pairing request with that id (expired?)")
        caps = tuple(
            c
            for c in (granted if granted is not None else pending.requested_capabilities)
            if c in pending.requested_capabilities
        )
        if not caps:
            raise ProtocolError(
                "INVALID_PARAMETERS", "At least one requested capability must be granted; decline instead"
            )
        self._store.add_grant(
            controller_id=None,
            kid=pending.kid,
            public_jwk=pending.public_jwk,
            capabilities=caps,
            display_name=pending.display_name,
            snapshot_id=self._store.current_snapshot_id(),
        )
        self._store.add_security_event("controller_paired_locally", pairing_id=pairing_id, capabilities=list(caps))
        sent = await self._send(pairing_decision_frame(pairing_id, "approve", pending.kid, list(caps)))
        if not sent:
            log.warning(
                "pairing_decision could not be sent (offline); the relay will not create the controller until it is"
            )
        session = self.session
        if session is not None:
            session.requests.pop(pairing_id, None)
        self._ui.pairing_finished(pairing_id, "approve")
        self._session = None  # one approval per code: the code is single-use by design
        return pending

    async def decline(self, pairing_id: str) -> None:
        pending = self.pending(pairing_id)
        if pending is None:
            raise ProtocolError("PAIRING_CODE_INVALID", "No pending pairing request with that id (expired?)")
        await self._send(pairing_decision_frame(pairing_id, "decline", pending.kid, []))
        self._store.add_security_event("pairing_declined_locally", pairing_id=pairing_id)
        session = self.session
        if session is not None:
            session.requests.pop(pairing_id, None)
        self._ui.pairing_finished(pairing_id, "decline")

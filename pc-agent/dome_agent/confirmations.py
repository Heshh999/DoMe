"""Confirmation transactions for disruptive actions (ADR-0001 D7, design → "Confirmation transaction").

Issue: the challenge object is serialised ONCE with ``dumps_compact`` into ``challenge_text``; the
exact string, its digest, the command's envelope ``kid`` and the ``target_state_digest`` are stored.
Consume: the signed ``confirmation`` is verified with the same resolver, the envelope ``kid`` must
equal the pinned kid, the challenge must exist / be unconsumed / unexpired and belong to the same
command and controller, ``challenge_digest`` must equal the digest of the stored text, the target
state digest must still match, and then — in ONE SQLite transaction — the challenge is consumed and
the command moves to ``accepted``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from dome_protocol import (
    ProtocolError,
    challenge_digest,
    dumps_compact,
    format_rfc3339,
    load_schemas,
    now_utc,
    parse_rfc3339,
    sha256_b64url,
)
from dome_protocol.commands import VerifiedCommand, VerifiedConfirmation

from .logsetup import get_logger
from .store import ChallengeRow, Store

if TYPE_CHECKING:
    from .actions.context import AgentServices

log = get_logger(__name__)

CHALLENGE_LIFETIME_SECONDS = 60
MAX_SKEW_SECONDS = 5


@dataclass(slots=True)
class PendingConfirmation:
    command: VerifiedCommand
    challenge_id: str
    challenge_text: str


@dataclass(frozen=True, slots=True)
class ConfirmationOutcome:
    """What the agent should do with the command after a confirmation frame."""

    command_id: str
    kind: str  # "approved" | "declined" | "rejected"
    pending: PendingConfirmation | None
    error: ProtocolError | None = None
    result_state: str = "failed"  # for declined/rejected: canceled | expired | failed


class ConfirmationError(ProtocolError):
    """A rejected confirmation. ``terminated`` is True when the challenge was consumed and the
    command must now end with ``result{result_state}``; False when nothing matched (the pending
    command, if any, is left untouched and only an ``error`` frame is sent)."""

    def __init__(self, code: str, message: str, result_state: str = "failed", *, terminated: bool = False) -> None:
        super().__init__(code, message, retryable=(code == "CONFIRMATION_EXPIRED"))
        self.result_state = result_state
        self.terminated = terminated


async def compute_target_state_digest(services: AgentServices, vc: VerifiedCommand) -> str:
    """Digest of the observable target state that approval is bound to; re-computed before executing."""
    action = vc.spec.name
    if action == "app.close":
        assert vc.target is not None
        snapshot: dict[str, Any] = {"app_id": vc.target["app_id"], "window_id": vc.target.get("window_id")}
        app = services.store.get_approved_app(vc.target["app_id"])
        if app is not None:
            try:
                pids = await asyncio.to_thread(services.platform.apps.running_pids, app.exe_path)
                windows = await asyncio.to_thread(services.platform.apps.list_windows, pids)
            except ProtocolError:
                windows = []
            wanted = vc.target.get("window_id")
            selected = [w for w in windows if wanted is None or w.window_id == wanted]
            snapshot["windows"] = [{"window_id": w.window_id, "title": w.title, "pid": w.pid} for w in selected]
        return sha256_b64url(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if action.startswith("power."):
        pending = services.store.get_pending_power()
        snapshot = {
            "pending": None if pending is None else {"action": pending.action, "command_id": pending.command_id}
        }
        return sha256_b64url(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return sha256_b64url(b"{}")


class ConfirmationManager:
    def __init__(
        self,
        store: Store,
        *,
        pc_name: Callable[[], str],
        clock: Callable[[], datetime] = now_utc,
        lifetime_seconds: int = CHALLENGE_LIFETIME_SECONDS,
    ) -> None:
        self._store = store
        self._pc_name = pc_name
        self._clock = clock
        self._lifetime = lifetime_seconds
        self._pending: dict[str, PendingConfirmation] = {}  # command_id → pending

    # ----- issue --------------------------------------------------------------------------------------
    def issue(self, vc: VerifiedCommand, target_state_digest: str, detail: str) -> PendingConfirmation:
        now = self._clock()
        challenge_id = str(uuid.uuid4())
        expires_text = format_rfc3339(now + timedelta(seconds=self._lifetime))
        challenge: dict[str, Any] = {
            "challenge_id": challenge_id,
            "command_id": vc.command_id,
            "controller_id": vc.controller_id,
            "pc_id": vc.target_pc_id,
            "action": vc.spec.name,
            "params": vc.params,
            "target": vc.target,
            "target_state_digest": target_state_digest,
            "issued_at": format_rfc3339(now),
            "expires_at": expires_text,
            "display": {
                "pc_name": self._pc_name()[:64],
                "action_label": action_label(vc.spec.name)[:80],
                "detail": detail[:200],
            },
        }
        text = dumps_compact(challenge)
        load_schemas().validate_challenge_text(text)  # the relay and the phone will apply the same check
        self._store.insert_challenge(
            ChallengeRow(
                challenge_id=challenge_id,
                command_id=vc.command_id,
                controller_id=vc.controller_id,
                kid=vc.envelope.kid,
                challenge_text=text,
                digest=challenge_digest(text),
                target_state_digest=target_state_digest,
                expires_at=expires_text,
                consumed_at=None,
            )
        )
        pending = PendingConfirmation(command=vc, challenge_id=challenge_id, challenge_text=text)
        self._pending[vc.command_id] = pending
        return pending

    def pending_for(self, command_id: str) -> PendingConfirmation | None:
        return self._pending.get(command_id)

    def drop(self, command_id: str) -> PendingConfirmation | None:
        return self._pending.pop(command_id, None)

    def pending_command_ids(self) -> list[str]:
        return list(self._pending)

    def expire_stale(self) -> list[PendingConfirmation]:
        """Pending confirmations whose challenge expired; their rows are consumed and returned."""
        expired: list[PendingConfirmation] = []
        now = self._clock()
        for command_id, pending in list(self._pending.items()):
            row = self._store.get_challenge(pending.challenge_id)
            if row is None or row.consumed_at is not None:
                self._pending.pop(command_id, None)
                continue
            if parse_rfc3339(row.expires_at) < now - timedelta(seconds=MAX_SKEW_SECONDS):
                if self._store.consume_challenge(
                    row.challenge_id, new_command_state="expired", error_code="CONFIRMATION_EXPIRED"
                ):
                    expired.append(pending)
                self._pending.pop(command_id, None)
        return expired

    # ----- consume ------------------------------------------------------------------------------------
    async def consume(
        self, vconf: VerifiedConfirmation, *, recompute_target_digest: Callable[[VerifiedCommand], Any]
    ) -> PendingConfirmation:
        """Validate and atomically consume. Raises :class:`ConfirmationError` when the command must
        terminate (declined / expired / invalid / target changed); the challenge is consumed in every
        case except an unknown or already-consumed challenge."""
        payload = vconf.payload
        command_id = payload["command_id"]
        row = self._store.get_challenge(payload["challenge_id"])
        pending = self._pending.get(command_id)
        if row is None or row.command_id != command_id or pending is None or pending.challenge_id != row.challenge_id:
            raise ConfirmationError("CONFIRMATION_INVALID", "No pending confirmation matches this challenge")
        if row.consumed_at is not None:
            raise ConfirmationError("CONFIRMATION_INVALID", "This confirmation was already used")
        if vconf.envelope.kid != row.kid or payload["controller_id"] != row.controller_id:
            self._store.consume_challenge(
                row.challenge_id, new_command_state="failed", error_code="CONFIRMATION_INVALID"
            )
            self._pending.pop(command_id, None)
            raise ConfirmationError(
                "CONFIRMATION_INVALID", "The confirmation was signed by a different controller", terminated=True
            )
        if payload["challenge_digest"] != row.digest:
            self._store.consume_challenge(
                row.challenge_id, new_command_state="failed", error_code="CONFIRMATION_INVALID"
            )
            self._pending.pop(command_id, None)
            raise ConfirmationError(
                "CONFIRMATION_INVALID", "The confirmation does not match the challenge shown", terminated=True
            )
        if parse_rfc3339(row.expires_at) < self._clock() - timedelta(seconds=MAX_SKEW_SECONDS):
            self._store.consume_challenge(
                row.challenge_id, new_command_state="expired", error_code="CONFIRMATION_EXPIRED"
            )
            self._pending.pop(command_id, None)
            raise ConfirmationError(
                "CONFIRMATION_EXPIRED", "The confirmation timed out", result_state="expired", terminated=True
            )
        if not vconf.approved:
            self._store.consume_challenge(
                row.challenge_id, new_command_state="canceled", error_code="CONFIRMATION_DECLINED"
            )
            self._pending.pop(command_id, None)
            raise ConfirmationError(
                "CONFIRMATION_DECLINED", "You declined this action", result_state="canceled", terminated=True
            )
        current_digest = await recompute_target_digest(pending.command)
        if current_digest != row.target_state_digest:
            self._store.consume_challenge(row.challenge_id, new_command_state="failed", error_code="TARGET_CHANGED")
            self._pending.pop(command_id, None)
            raise ConfirmationError(
                "TARGET_CHANGED", "The target changed since you were asked to confirm", terminated=True
            )
        if not self._store.consume_challenge(row.challenge_id, new_command_state="accepted"):
            self._pending.pop(command_id, None)
            raise ConfirmationError("CONFIRMATION_INVALID", "This confirmation was already used")
        self._pending.pop(command_id, None)
        return pending


def action_label(action: str) -> str:
    return {
        "app.close": "Close application",
        "power.sleep": "Sleep",
        "power.restart": "Restart",
        "power.shutdown": "Shut down",
    }.get(action, action)

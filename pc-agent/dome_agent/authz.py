"""Local authorization of inbound commands — applied to EVERY command in the design's order.

 1. envelope verified + payload parsed once (``verify_and_parse_command``) with a KeyRecord resolver
    that only answers for a locally approved, non-revoked controller present in the latest
    ``grants_snapshot`` (→ UNKNOWN_KEY / CONTROLLER_REVOKED; identity binding → CONTROLLER_MISMATCH /
    ACCOUNT_MISMATCH by the shared library)
 2. ``target_pc_id`` must be this PC (→ TARGET_PC_MISMATCH). Mismatches are not journaled, are
    logged as local security events and counted for ``rules.mismatch_handling``
 3. local ``remote_enabled`` (→ PC_REMOTE_DISABLED), snapshot ``pc_enabled`` (→ PC_PLAN_DISABLED),
    controller status (→ CONTROLLER_PLAN_DISABLED)
 4. the action's capability — or one of its ``alternate_capabilities`` — ∈ local ∩ snapshot (→ GRANT_MISSING)
    Steps 3-4 are :meth:`Authorizer.recheck_grant` and are re-applied when a confirmation arrives,
    right before execution and whenever a new ``grants_snapshot`` lands.
 5. journal: identical duplicate → re-emit; same id, different bytes → COMMAND_ID_REUSED; new → row
 6. availability conditions
 7. target resolution
 8. routine steps: routine_allowed + Pro entitlement (→ ENTITLEMENT_REQUIRED)
 9. disruptive actions: issue a challenge → confirmation_required (journaled awaiting_confirmation)
Only then is the command accepted for execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from dome_protocol import KeyRecord, ProtocolError, load_registry, load_schemas, now_utc, verify_and_parse_command
from dome_protocol.commands import VerifiedCommand

from .actions.youtube import resolve_youtube_target
from .confirmations import PendingConfirmation, compute_target_state_digest
from .frames import ack_frame, confirmation_required_frame
from .logsetup import get_logger

if TYPE_CHECKING:
    from .actions.context import AgentServices
    from .confirmations import ConfirmationManager
    from .entitlement import EntitlementManager

log = get_logger(__name__)

MISMATCH_CODES = frozenset({"ACCOUNT_MISMATCH", "TARGET_PC_MISMATCH"})
DecisionKind = Literal["rejected", "duplicate", "confirm", "execute"]


@dataclass(slots=True)
class Decision:
    kind: DecisionKind
    command: VerifiedCommand | None = None
    error: ProtocolError | None = None
    command_id: str | None = None  # best-effort id for rejections (None when the payload never verified)
    journaled: bool = False  # whether the journal row exists (rejections after step 5 are recorded there)
    mismatch: bool = False  # counts toward rules.mismatch_handling
    frames: list[dict[str, Any]] = field(default_factory=list)  # frames to (re-)send
    pending: PendingConfirmation | None = None


class Authorizer:
    def __init__(
        self, services: AgentServices, confirmations: ConfirmationManager, entitlement: EntitlementManager
    ) -> None:
        self._s = services
        self._confirmations = confirmations
        self._entitlement = entitlement
        self._registry = load_registry()
        self._schemas = load_schemas()

    # ----- key resolution (step 1) ------------------------------------------------------------------
    def resolve_key(self, kid: str) -> KeyRecord | None:
        row = self._s.store.get_grant_by_kid(kid)
        if row is None:
            return None
        if row.revoked:
            raise ProtocolError(
                "CONTROLLER_REVOKED", "Access for this phone was revoked. Pair again from the PC to restore it."
            )
        account_id = self._s.identity.account_id
        if account_id is None:
            return None
        return KeyRecord(
            controller_id=row.controller_id,
            account_id=account_id,
            jwk=row.public_jwk,
            capabilities=row.effective_capabilities(self._s.store.current_snapshot_id()),
        )

    # ----- the decision --------------------------------------------------------------------------------
    async def authorize(self, envelope: Any, *, snapshot_received: bool, now: datetime | None = None) -> Decision:
        now = now or now_utc()
        # step 1
        try:
            vc = verify_and_parse_command(
                envelope, self.resolve_key, now=now, registry=self._registry, schemas=self._schemas
            )
        except ProtocolError as exc:
            command_id = _peek_command_id(envelope)
            if exc.code in MISMATCH_CODES:
                self._s.store.add_security_event("command_identity_mismatch", code=exc.code)
                return Decision("rejected", error=exc, command_id=command_id, mismatch=True)
            if exc.code in ("UNKNOWN_KEY", "SIGNATURE_INVALID", "CONTROLLER_MISMATCH", "CONTROLLER_REVOKED"):
                self._s.store.add_security_event("command_rejected", code=exc.code)
            return Decision("rejected", error=exc, command_id=command_id)
        # step 2
        if vc.target_pc_id != self._s.identity.pc_id:
            self._s.store.add_security_event(
                "command_identity_mismatch", code="TARGET_PC_MISMATCH", controller_id=vc.controller_id
            )
            return Decision(
                "rejected", command=vc, command_id=vc.command_id, mismatch=True, error=self._err("TARGET_PC_MISMATCH")
            )
        if not snapshot_received:
            return Decision("rejected", command=vc, command_id=vc.command_id, error=self._err("PC_RECONNECTING"))
        # steps 3 + 4 (re-applied by recheck_grant before every later state transition)
        store = self._s.store
        blocker = self.recheck_grant(vc)
        if blocker is not None:
            return self._reject(vc, blocker)
        # step 5
        existing = store.journal_get(vc.command_id)
        if existing is not None:
            if existing.digest != vc.digest:
                store.add_security_event("command_id_reused", controller_id=vc.controller_id)
                return self._reject(vc, "COMMAND_ID_REUSED")
            return Decision(
                "duplicate",
                command=vc,
                command_id=vc.command_id,
                journaled=True,
                frames=self._replay_frames(existing.state, existing.frame, vc),
            )
        if not store.journal_insert(
            command_id=vc.command_id, digest=vc.digest, action=vc.spec.name, controller_id=vc.controller_id
        ):
            return self._reject(vc, "COMMAND_ID_REUSED")  # lost a race with an identical id
        # step 6
        unavailable = await self.check_availability(vc)
        if unavailable is not None:
            return self._reject(vc, unavailable, journaled=True)
        # step 7
        try:
            await self.resolve_target(vc)
        except ProtocolError as exc:
            return self._reject(vc, exc, journaled=True)
        # step 8 (routine steps)
        origin = vc.payload.get("origin")
        if isinstance(origin, dict) and origin.get("kind") == "routine":
            if not vc.spec.routine_allowed:
                return self._reject(
                    vc, "ENTITLEMENT_REQUIRED", "This action cannot run as a routine step.", journaled=True
                )
            if not self._entitlement.routines_allowed():
                return self._reject(vc, "ENTITLEMENT_REQUIRED", journaled=True)
        # step 9
        if vc.spec.requires_confirmation:
            digest = await compute_target_state_digest(self._s, vc)
            pending = self._confirmations.issue(vc, digest, await self._confirmation_detail(vc))
            ack = ack_frame(vc.command_id, "awaiting_confirmation")
            store.journal_set_state(vc.command_id, "awaiting_confirmation", frame=ack)
            return Decision(
                "confirm",
                command=vc,
                command_id=vc.command_id,
                journaled=True,
                pending=pending,
                frames=[ack, confirmation_required_frame(vc.command_id, pending.challenge_text)],
            )
        return Decision("execute", command=vc, command_id=vc.command_id, journaled=True)

    # ----- helpers ----------------------------------------------------------------------------------------
    def recheck_grant(self, vc: VerifiedCommand) -> ProtocolError | None:
        """Authorization steps 3 + 4 against the CURRENT local state and snapshot.

        Applied at receipt, again when a confirmation arrives (the command may have waited up to 60 s),
        again by the executor right before the handler runs, and to every pending command when a new
        ``grants_snapshot`` lands — so a capability removed from the phone, a plan change or a local
        *Disable remote control* takes effect before the side effect, not only before acceptance
        (``rules.grants_snapshot``: effective capabilities = local ∩ snapshot, at all times)."""
        store = self._s.store
        if not store.remote_enabled:
            return self._err("PC_REMOTE_DISABLED")
        if not store.snapshot_pc_enabled():
            return self._err("PC_PLAN_DISABLED")
        grant = store.get_grant(vc.controller_id)
        if grant is None or grant.revoked:
            return self._err("CONTROLLER_REVOKED")
        if grant.snapshot_status != "active":
            return self._err("CONTROLLER_PLAN_DISABLED")
        if not vc.spec.satisfied_by(grant.effective_capabilities(store.current_snapshot_id())):
            accepted = " or ".join(f"'{c}'" for c in vc.spec.accepted_capabilities)
            return self._err("GRANT_MISSING", f"This phone does not have the {accepted} permission on this PC.")
        return None

    async def precheck(self, vc: VerifiedCommand) -> ProtocolError | None:
        """Executor precheck right before the handler runs: grant/plan state, then availability."""
        blocker = self.recheck_grant(vc)
        if blocker is not None:
            return blocker
        return await self.check_availability(vc)

    async def check_availability(self, vc: VerifiedCommand) -> ProtocolError | None:
        """Availability conditions (step 6; also re-applied by :meth:`precheck` right before running)."""
        if not self._s.store.remote_enabled:
            return self._err("PC_REMOTE_DISABLED")
        locked: bool | None = None
        for condition in vc.spec.availability:
            if condition == "windows":
                if not self._s.platform.supports_windows_actions:
                    return self._err("PLATFORM_UNSUPPORTED")
            elif condition == "extension_connected":
                if not self._s.bridge.connected:
                    return self._err("EXTENSION_DISCONNECTED")
            elif condition == "session_unlocked":
                if locked is None:
                    locked = await self._s.state.session_locked()
                if locked:
                    return self._err(
                        "PC_SESSION_LOCKED_MEDIA_ONLY" if self._s.store.media_while_locked else "PC_SESSION_LOCKED"
                    )
            elif condition == "session_media_allowed":
                if locked is None:
                    locked = await self._s.state.session_locked()
                if locked and not self._s.store.media_while_locked:
                    return self._err("PC_SESSION_LOCKED")
            else:  # unknown condition in the registry: refuse rather than guess
                return self._err("ACTION_UNAVAILABLE", f"unknown availability condition {condition}")
        return None

    async def resolve_target(self, vc: VerifiedCommand) -> None:
        spec = vc.spec
        if spec.target_name == "youtube_target":
            assert vc.target is not None
            resolve_youtube_target(self._s.bridge, vc.target)
        elif spec.target_name == "window_target":
            assert vc.target is not None
            if self._s.apps.get(str(vc.target["app_id"])) is None:
                raise self._err("APP_NOT_APPROVED")
        elif spec.name == "app.launch":
            if self._s.apps.get(str(vc.params["app_id"])) is None:
                raise self._err("APP_NOT_APPROVED")
        # media_session_target: existence is verified by the handler against the live session list

    async def _confirmation_detail(self, vc: VerifiedCommand) -> str:
        if vc.spec.name == "app.close" and vc.target is not None:
            app = self._s.apps.get(str(vc.target["app_id"]))
            return f"Close {app.display_name if app else vc.target['app_id']}"[:200]
        if vc.spec.name.startswith("power."):
            seconds = int(vc.params.get("countdown_seconds", 10))
            return power_confirmation_detail(vc.spec.name, seconds)
        return ""

    def _replay_frames(self, state: str, frame: dict[str, Any] | None, vc: VerifiedCommand) -> list[dict[str, Any]]:
        if state in ("succeeded", "failed", "expired", "canceled", "outcome_unknown"):
            return [frame] if frame else []
        if state == "awaiting_confirmation":
            row = self._s.store.get_open_challenge_for_command(vc.command_id)
            frames = [ack_frame(vc.command_id, "awaiting_confirmation")]
            if row is not None:
                frames.append(confirmation_required_frame(vc.command_id, row.challenge_text))
            return frames
        return [ack_frame(vc.command_id, "executing" if state == "executing" else "accepted")]

    def _err(self, code: str, message: str | None = None) -> ProtocolError:
        return self._registry.make_error(code, message)

    def _reject(
        self, vc: VerifiedCommand, error: str | ProtocolError, message: str | None = None, *, journaled: bool = False
    ) -> Decision:
        err = error if isinstance(error, ProtocolError) else self._err(error, message)
        return Decision("rejected", command=vc, command_id=vc.command_id, error=err, journaled=journaled)


POWER_VERBS = {"power.sleep": "Sleeping", "power.restart": "Restarting", "power.shutdown": "Shutting down"}


def power_confirmation_detail(action: str, countdown_seconds: int) -> str:
    """``challenge.display.detail`` for power actions (spec §10, §17.25). The phone shows it verbatim, so
    it states plainly that remote access can be interrupted or end and that V1 has no remote wake.
    Bounded to the schema's 200 characters."""
    verb = POWER_VERBS.get(action, "This")
    text = (
        f"{countdown_seconds} s countdown, cancellable from the phone. {verb} can interrupt or end remote access "
        "to this PC. DoMe cannot wake it or turn it on again remotely (no remote wake in V1)."
    )
    return text[:200]


def _peek_command_id(envelope: Any) -> str | None:
    """Best-effort command_id for a result frame when the envelope could not be verified.

    The value is taken from the UNVERIFIED payload and is used for nothing but addressing the
    rejection; the relay matches it against the command it forwarded."""
    try:
        from dome_protocol import loads_strict

        if isinstance(envelope, dict) and isinstance(envelope.get("payload"), str):
            parsed = loads_strict(envelope["payload"], require_object=True)
            cid = parsed.get("command_id")
            if isinstance(cid, str) and len(cid) == 36:
                return cid
    except ProtocolError:
        return None
    return None

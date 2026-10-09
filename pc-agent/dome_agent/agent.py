"""The agent process: wires every component together and dispatches relay frames.

Inbound frame handling (``relay_to_agent``):

* ``hello_ack`` / connection → :class:`RelayClient` (identity check) → :meth:`on_connected`
* ``grants_snapshot`` → identity check (``rules.agent_identity``), atomic intersection in the store,
  cancel in-flight commands of revoked controllers, re-authorize every pending command against the
  new intersection, re-send unacknowledged local revocations, entitlement assertion, then a ``state``
  frame and the re-send of journaled late results; only now are commands accepted
* ``command`` → :class:`Authorizer` → reject / replay duplicate / confirmation_required / ``ack{accepted}`` + queue
* ``confirmation`` → verified with the same resolver, kid pinned to the challenge, atomic consume,
  steps 3-4 re-checked (``Authorizer.recheck_grant``) → queue
* ``cancel`` → queued / awaiting / armed-power commands end ``canceled``
* ``pairing_request`` → :class:`PairingManager`
* ``revoked`` → credential discarded, re-link prompt; ``error`` → logged
* ``input_batch`` → verified against the LOCAL grant exactly like a command (signature, identity,
  snapshot, remote_enabled, plan state, grant covers the event types) → :class:`InputSessionManager`
  (session ownership, seq, age budget, dispatch); acks/session frames go back through ``_send``

Local controls (tray / CLI through the control channel): enable / **disable remote control** (wins
over everything remote), pairing, approved apps, local revocation, local grant changes (pointer /
keyboard → ``grant_update``), reconnect, status, diagnostics, ``show`` (single-instance second launch).

Every manual-input end trigger is wired here or in the manager: snapshot revocation / narrowing, local
revocation, local grant narrowing, remote disable, lock / secure desktop (watchdog), lease expiry
(watchdog), relay disconnect and 4001 supersession, agent stop (``agent_restart``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import deque
from collections.abc import Callable, Coroutine, Iterable
from typing import Any

from dome_protocol import (
    ProtocolError,
    load_registry,
    load_schemas,
    verify_and_parse_confirmation,
    verify_and_parse_input_batch,
)

from . import __version__
from .actions.context import AgentServices
from .actions.power import PowerManager
from .api import ApiClient
from .approved_apps import ApprovedApps
from .authz import MISMATCH_CODES, Authorizer, Decision
from .bridge.server import BridgeServer
from .confirmations import ConfirmationError, ConfirmationManager, compute_target_state_digest
from .control import ControlError, ControlServer
from .diagnostics import write_bundle
from .entitlement import EntitlementError, EntitlementManager
from .frames import grant_update_frame, revoke_controller_frame, state_frame
from .identity import Identity
from .input_session import InputSessionManager
from .logsetup import get_logger
from .pairing import INPUT_CAPABILITIES, PairingManager
from .platform import PlatformSet, build_platform
from .queue import Emitter, Executor
from .relay_client import ConnectionState, RelayClient, StopReason, TokenManager
from .settings import Settings
from .state import StateAggregator
from .store import Store
from .ui import AgentUI, NullUI, StatusView

log = get_logger(__name__)

MISMATCH_WINDOW_SECONDS = 60.0
MISMATCH_LIMIT = 3
CONFIRMATION_SWEEP_SECONDS = 5.0
LINK_POLL_SECONDS = 3.0


class Agent:
    def __init__(self, settings: Settings, *, ui: AgentUI | None = None, platform: PlatformSet | None = None) -> None:
        self.settings = settings
        self.ui: AgentUI = ui or NullUI()
        settings.ensure_dirs()
        self.store = Store(settings.db_path)
        self.identity = Identity(settings.state_dir)
        self.platform = platform or build_platform(settings)
        self.registry = load_registry()
        self.schemas = load_schemas()
        self.bridge = BridgeServer(
            settings.state_dir, on_change=self._on_bridge_change, on_security_event=self._security_event
        )
        self.state = StateAggregator(self.store, self.platform, self.bridge)
        self.power = PowerManager(self.store, self.platform, self.state)
        self.apps = ApprovedApps(self.store)
        self.input = InputSessionManager(
            platform=self.platform,
            send=self._send,
            pc_id=lambda: self.identity.pc_id,
            state_dir=settings.state_dir,
            session_locked=self.state.session_locked,
            remote_enabled=lambda: self.store.remote_enabled,
            on_state_change=self.state.request_update,
        )
        self.state.input = self.input
        self.confirmations = ConfirmationManager(self.store, pc_name=lambda: self.identity.pc_name)
        self.entitlement = EntitlementManager(
            self.store, account_id=self.identity.account_id, pc_id=self.identity.pc_id
        )
        self.services = AgentServices(
            registry=self.registry,
            store=self.store,
            platform=self.platform,
            bridge=self.bridge,
            state=self.state,
            power=self.power,
            identity=self.identity,
            apps=self.apps,
            input=self.input,
        )
        self.authz = Authorizer(self.services, self.confirmations, self.entitlement)
        self.emitter = Emitter(self.store, self._send)
        self.executor = Executor(self.services, self.emitter, precheck=self._precheck)
        self.power.bind(self.executor.complete)
        self.api: ApiClient | None = None
        self.tokens: TokenManager | None = None
        self.relay: RelayClient | None = None
        self.pairing: PairingManager | None = None
        self.control = ControlServer(settings.state_dir, self._control_ops(), self._security_event)
        self._snapshot_received = False
        self._mismatches: deque[float] = deque()
        self._stop = asyncio.Event()
        self._sweeper: asyncio.Task[None] | None = None
        self._link_watch: asyncio.Task[None] | None = None
        self.relink_required = False
        self.relink_reason = ""
        self.configuration_error = ""
        self.status_notes: list[str] = list(self.platform.notes)
        self.started_at = time.time()

    # ----- lifecycle --------------------------------------------------------------------------------------------
    async def start(self) -> None:
        recovered = self.store.recover_after_restart()
        if any(recovered.values()):
            self.status_notes.append("journal recovered after restart")
        self.store.purge_expired_challenges()
        if await self.input.recover_after_restart():
            self.status_notes.append("input holds released after restart")
        await self.bridge.start()
        self.state.start(self._emit_state)
        self.executor.start()
        self.input.start()
        await self.control.start()
        self._sweeper = asyncio.get_running_loop().create_task(
            self._sweep_confirmations(), name="dome-confirmation-sweeper"
        )
        if self.identity.is_linked:
            self._start_relay()
        else:
            log.warning("this PC is not linked yet; waiting for `dome-agent link`")
            self._link_watch = asyncio.get_running_loop().create_task(self._watch_for_link(), name="dome-link-watch")
        self._update_ui()

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stop.wait()
        finally:
            await self.stop()

    def request_stop(self) -> None:
        self._stop.set()

    async def stop(self) -> None:
        for task in (self._sweeper, self._link_watch):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.input.shutdown()  # ends a live session (agent_restart) and releases its holds first
        await self.power.shutdown()
        await self.executor.stop()
        await self.entitlement.stop()
        if self.relay is not None:
            await self.relay.stop()
        await self.state.stop()
        await self.control.stop()
        await self.bridge.stop()
        if self.api is not None:
            await self.api.close()
        self.store.close()

    def _start_relay(self) -> None:
        link = self.identity.link
        assert link is not None
        api_url = self.settings.api_url or link.api_url
        relay_url = self.settings.relay_url or link.relay_url
        self.api = ApiClient(api_url)
        self.tokens = TokenManager(self.api, self.identity.read_credential)
        self.entitlement = EntitlementManager(
            self.store, account_id=self.identity.account_id, pc_id=self.identity.pc_id
        )
        self.entitlement.bind(self.api, self.tokens.get)
        self.authz = Authorizer(self.services, self.confirmations, self.entitlement)
        self.pairing = PairingManager(self.store, self.identity, self.api, self.tokens, self._send, self.ui)
        self.relay = RelayClient(relay_url, self.tokens, self, expected_pc_id=lambda: self.identity.pc_id)
        self.relink_required = False
        self.configuration_error = ""
        self.relay.start()

    async def _watch_for_link(self) -> None:
        while True:
            await asyncio.sleep(LINK_POLL_SECONDS)
            self.identity = Identity(self.settings.state_dir)
            if self.identity.is_linked:
                log.info("link detected; connecting to the relay")
                self.services.identity = self.identity
                self._start_relay()
                self._update_ui()
                return

    # ----- RelayHandler ----------------------------------------------------------------------------------------------
    async def on_connected(self, hello_ack: dict[str, Any]) -> None:
        self._snapshot_received = False
        self._mismatches.clear()
        log.info("connected to relay", connection_id=hello_ack.get("connection_id"))
        self._update_ui()

    async def on_frame(self, frame: dict[str, Any]) -> None:
        kind = frame["type"]
        if kind == "grants_snapshot":
            await self._on_snapshot(frame)
        elif kind == "command":
            await self._on_command(frame)
        elif kind == "confirmation":
            await self._on_confirmation(frame)
        elif kind == "cancel":
            await self._on_cancel(frame)
        elif kind == "pairing_request":
            if self.pairing is not None and self._snapshot_received:
                await self.pairing.handle_request(frame)
            else:
                log.warning("pairing_request before grants_snapshot; ignored")
        elif kind == "input_batch":
            await self._on_input_batch(frame)
        elif kind == "revoked":
            await self._on_revoked(frame)
        elif kind == "error":
            log.warning("error frame from relay", code=frame["error"]["code"], message=frame["error"]["message"])
        else:  # hello_ack after handshake etc.
            log.warning("unexpected frame type from relay", type=kind)

    async def on_disconnected(self, reason: str) -> None:
        self._snapshot_received = False
        await self.input.end("controller_disconnected")  # the stream is gone with the socket; never replayed
        failed = await self.executor.fail_queued_offline()
        if failed:
            log.info("queued commands failed as PC_OFFLINE on disconnect", count=len(failed))
        self._update_ui()

    async def on_stopped(self, reason: StopReason) -> None:
        if reason == "configuration_error":
            # Local misconfiguration (invalid relay URL): the credential is untouched; no re-link needed.
            self.configuration_error = (
                "The relay URL is invalid. Check DOME_AGENT_RELAY_URL or re-run `dome-agent link`."
            )
            log.error("agent stopped connecting", reason=reason)
            self.ui.notify("DoMe cannot connect", self.configuration_error)
            self._update_ui()
            return
        if reason in ("revoked", "credential_rejected", "unauthorized", "identity_mismatch"):
            self.relink_required = True
            self.relink_reason = reason
            if reason != "identity_mismatch":
                self.identity.discard_credential()
            self.ui.notify(
                "DoMe needs to be re-linked",
                "This PC's connection to your DoMe account ended. Open DoMe on the PC and link it again.",
            )
        elif reason == "superseded":
            self.ui.notify(
                "DoMe is running elsewhere", "Another DoMe agent connected for this PC. Use Reconnect to take over."
            )
        self._update_ui()

    def on_state_change(self, state: ConnectionState) -> None:
        self._update_ui()

    # ----- snapshot -----------------------------------------------------------------------------------------------------
    async def _on_snapshot(self, frame: dict[str, Any]) -> None:
        if frame["pc_id"] != self.identity.pc_id or frame["account_id"] != self.identity.account_id:
            self._security_event("snapshot_identity_mismatch", {"snapshot_pc_id": frame["pc_id"]})
            log.error("grants_snapshot names another PC/account; stopping (re-link required)")
            if self.relay is not None:
                await self.relay.stop()
            await self.on_stopped("identity_mismatch")
            return
        result = self.store.apply_snapshot(frame["snapshot_id"], bool(frame["pc_enabled"]), list(frame["controllers"]))
        for controller_id, kid in result.unknown_controllers:
            self._security_event("snapshot_unknown_controller", {"controller_id": controller_id, "kid_prefix": kid[:8]})
        revoked_error = self.registry.make_error("CONTROLLER_REVOKED")
        for controller_id in result.revoked_controller_ids:
            await self.input.end_for_controller(controller_id, "controller_revoked")
            canceled = await self.executor.cancel_for_controller(controller_id, revoked_error)
            canceled += await self._cancel_pending_confirmations(
                controller_id=controller_id, state="canceled", error=revoked_error
            )
            log.info("controller revoked by snapshot", controller_id=controller_id, canceled_commands=len(canceled))
        await self._reauthorize_pending()
        await self._reapply_input_grant()
        first = not self._snapshot_received
        self._snapshot_received = True
        await self._resend_local_revocations(result.still_listed_revoked)
        await self._resend_grant_updates()
        await self._apply_snapshot_entitlement(frame.get("entitlement_assertion"))
        await self.state.emit_now()
        if first:
            await self.input.on_snapshot_applied()
            await self._resend_late_results()
            asyncio.get_running_loop().create_task(
                self._refresh_entitlement_on_connect(), name="dome-entitlement-connect"
            )
        self._update_ui()

    async def _reauthorize_pending(self) -> None:
        """A new snapshot (or a local change) can narrow what a phone may do: every command that was
        authorized but has not produced its side effect yet is re-checked (steps 3-4) and ended
        ``canceled`` with the specific code when it no longer passes (``rules.grants_snapshot``)."""
        for pending_id in self.confirmations.pending_command_ids():
            pending = self.confirmations.pending_for(pending_id)
            if pending is None:
                continue
            blocker = self.authz.recheck_grant(pending.command)
            if blocker is not None:
                self.store.consume_challenge(
                    pending.challenge_id, new_command_state="canceled", error_code=blocker.code
                )
                self.confirmations.drop(pending_id)
                await self.emitter.result(pending_id, "canceled", error=blocker)
                log.info("pending confirmation canceled by snapshot", command_id=pending_id, code=blocker.code)
        for vc in self.executor.queued_commands():
            blocker = self.authz.recheck_grant(vc)
            if blocker is not None:
                await self.executor.cancel_queued(vc.command_id, blocker)
                log.info("queued command canceled by snapshot", command_id=vc.command_id, code=blocker.code)
        for vc in self.executor.deferred_commands():
            blocker = self.authz.recheck_grant(vc)
            if (
                blocker is not None
                and self.power.pending is not None
                and self.power.pending.command_id == vc.command_id
            ):
                await self.power.cancel(reason="canceled because the phone's permission changed")

    async def _reapply_input_grant(self) -> None:
        """The live input session follows the owner's effective capabilities (local ∩ snapshot): both
        input capabilities gone → ``grant_removed``; remote control off or plan disabled → the session ends."""
        session = self.input.current
        if session is None or session.state == "ended":
            return
        grant = self.store.get_grant(session.controller_id)
        if grant is None or grant.revoked:
            await self.input.end_for_controller(session.controller_id, "controller_revoked")
            return
        if not self.store.snapshot_pc_enabled() or grant.snapshot_status != "active":
            await self.input.end_for_controller(session.controller_id, "grant_removed")
            return
        await self.input.apply_capabilities(
            session.controller_id, grant.effective_capabilities(self.store.current_snapshot_id())
        )

    async def _resend_grant_updates(self) -> None:
        """``rules.grant_update``: a local capability change reaches the relay only through ``grant_update``;
        a frame dropped while offline is re-sent after every snapshot until it is written (like revocations)."""
        for grant in self.store.pending_grant_updates():
            if grant.controller_id.startswith("pending-"):
                continue  # the relay has not named this controller yet; the next snapshot will
            if await self._send(grant_update_frame(grant.controller_id, grant.kid, list(grant.capabilities))):
                self.store.clear_pending_grant_update(grant.kid)
                log.info("local grant change (re-)sent to the relay", controller_id=grant.controller_id)

    async def _resend_local_revocations(self, still_listed: tuple[tuple[str, str], ...]) -> None:
        """Local revocations reach the relay only through ``revoke_controller``; a frame dropped while
        offline is re-sent after every snapshot until the write succeeds (idempotent on the relay)."""
        pending = {row.controller_id: row.kid for row in self.store.pending_revocations()}
        for controller_id, kid in still_listed:
            pending.setdefault(controller_id, kid)
        for controller_id, kid in pending.items():
            if await self._send(revoke_controller_frame(controller_id, kid)):
                self.store.clear_pending_revocation(controller_id)
                log.info("local revocation (re-)sent to the relay", controller_id=controller_id)

    async def _apply_snapshot_entitlement(self, assertion: Any) -> None:
        if assertion is None:
            self.entitlement.set_free()
            return
        if self.api is None:
            return
        try:
            await self.entitlement.ensure_jwks(self.api)
            self.entitlement.apply_assertion(str(assertion))
            self.entitlement.schedule_refresh()
        except EntitlementError as exc:
            log.warning("snapshot entitlement assertion rejected", message=exc.message)
        except Exception as exc:  # noqa: BLE001 - JWKS fetch failures are soft
            log.warning("could not verify snapshot entitlement", error=exc.__class__.__name__)
            self.entitlement.note_refresh_failure(soft=True)

    async def _refresh_entitlement_on_connect(self) -> None:
        await self.entitlement.refresh()
        self.entitlement.schedule_refresh()

    async def _resend_late_results(self) -> None:
        """``rules.late_results``: after (re)connecting, re-send every terminal result that was never written to a
        socket and every one finished after the last frame the previous connection received — a write into a
        half-open socket is not delivery. The relay drops duplicates it already has and uses the rest to correct
        the ``outcome_unknown`` it reported while the PC was unreachable."""
        rows = {row.command_id: row for row in self.store.journal_unsent_terminal()}
        since = self.relay.previous_last_inbound_at if self.relay is not None else None
        if since is not None:
            for row in self.store.journal_terminal_finished_after(since):
                rows.setdefault(row.command_id, row)
        for row in rows.values():
            assert row.frame is not None
            if await self._send(row.frame):
                self.store.journal_mark_sent(row.command_id, True)
                log.info("late result re-sent", command_id=row.command_id, state=row.state, was_sent=row.sent)

    # ----- commands -------------------------------------------------------------------------------------------------------
    async def _on_command(self, frame: dict[str, Any]) -> None:
        decision = await self.authz.authorize(frame["envelope"], snapshot_received=self._snapshot_received)
        await self._apply_decision(decision)

    async def _apply_decision(self, decision: Decision) -> None:
        if decision.kind == "rejected":
            assert decision.error is not None
            if decision.command_id is not None:
                await self.emitter.result(
                    decision.command_id, "failed", error=decision.error, journal=decision.journaled
                )
            else:
                await self._send({"type": "error", "error": decision.error.to_frame_error()})
            if decision.mismatch:
                await self._count_mismatch()
            return
        if decision.kind in ("duplicate", "confirm"):
            for f in decision.frames:
                await self._send(f)
            return
        assert decision.command is not None
        await self._enqueue(decision.command)

    async def _enqueue(self, vc: Any) -> None:
        await self.emitter.ack(vc.command_id, "accepted")
        try:
            await self.executor.enqueue(vc)
        except ProtocolError as exc:
            await self.emitter.result(vc.command_id, "failed", error=exc)

    async def _count_mismatch(self) -> None:
        now = time.monotonic()
        self._mismatches.append(now)
        while self._mismatches and now - self._mismatches[0] > MISMATCH_WINDOW_SECONDS:
            self._mismatches.popleft()
        if len(self._mismatches) >= MISMATCH_LIMIT and self.relay is not None:
            self._security_event("mismatch_storm_reconnect", {"count": len(self._mismatches)})
            self._mismatches.clear()
            await self.relay.reconnect_now("identity mismatch storm")

    # ----- manual input ----------------------------------------------------------------------------------------------------
    async def _on_input_batch(self, frame: dict[str, Any]) -> None:
        """Same trust checks as a command (signature against the LOCAL grant, identity, snapshot, local
        remote_enabled, plan state, grant covers the event types), then the session manager. No journal
        row, no security event per accepted batch, never any content in a log line."""
        try:
            vb = verify_and_parse_input_batch(
                frame["envelope"], self.authz.resolve_key, registry=self.registry, schemas=self.schemas
            )
        except ProtocolError as exc:
            if exc.code in MISMATCH_CODES:
                self._security_event("input_identity_mismatch", {"code": exc.code})
                await self._count_mismatch()
            elif exc.code in ("UNKNOWN_KEY", "SIGNATURE_INVALID", "CONTROLLER_MISMATCH", "CONTROLLER_REVOKED"):
                self._security_event("input_rejected", {"code": exc.code})
            await self._send({"type": "error", "error": exc.to_frame_error()})
            return
        if vb.target_pc_id != self.identity.pc_id:
            self._security_event("input_identity_mismatch", {"code": "TARGET_PC_MISMATCH"})
            await self._send({"type": "error", "error": self.registry.make_error("TARGET_PC_MISMATCH").to_frame_error()})
            await self._count_mismatch()
            return
        if not self._snapshot_received:
            await self._send({"type": "error", "error": self.registry.make_error("PC_RECONNECTING").to_frame_error()})
            return
        blocker = self._input_grant_blocker(vb.key.controller_id, vb.required_capabilities)
        if blocker is not None:
            if blocker.code in ("PC_REMOTE_DISABLED", "CONTROLLER_REVOKED", "PC_PLAN_DISABLED", "CONTROLLER_PLAN_DISABLED"):
                await self.input.end_for_controller(
                    vb.key.controller_id, "remote_disabled" if blocker.code == "PC_REMOTE_DISABLED" else "grant_removed"
                )
            await self._send({"type": "error", "error": blocker.to_frame_error()})
            return
        await self.input.handle_batch(vb)

    def _input_grant_blocker(self, controller_id: str, needed: set[str]) -> ProtocolError | None:
        """Authorization steps 3-4 for a batch: local switch, plan state, live grant, event-type coverage."""
        store = self.store
        if not store.remote_enabled:
            return self.registry.make_error("PC_REMOTE_DISABLED")
        if not store.snapshot_pc_enabled():
            return self.registry.make_error("PC_PLAN_DISABLED")
        grant = store.get_grant(controller_id)
        if grant is None or grant.revoked:
            return self.registry.make_error("CONTROLLER_REVOKED")
        if grant.snapshot_status != "active":
            return self.registry.make_error("CONTROLLER_PLAN_DISABLED")
        effective = set(grant.effective_capabilities(store.current_snapshot_id()))
        if not needed <= effective:
            return self.registry.make_error("INPUT_NOT_PERMITTED")
        return None

    async def update_grant_capabilities(self, controller_id: str, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> Any:
        """The PC owner changed a phone's pointer/keyboard permission locally (tray / CLI). The local
        list is authoritative at once (effective = local ∩ snapshot); the relay is told with
        ``grant_update`` (journaled until written; re-sent after the next snapshot when offline)."""
        add_set, remove_set = set(add), set(remove)
        unknown = (add_set | remove_set) - set(INPUT_CAPABILITIES)
        if unknown:
            raise ProtocolError("INVALID_PARAMETERS", f"only pointer/keyboard can be changed here, not {sorted(unknown)}")
        grant = self.store.get_grant(controller_id)
        if grant is None or grant.revoked:
            return None
        caps = [c for c in grant.capabilities if c not in remove_set] + [c for c in INPUT_CAPABILITIES if c in add_set and c not in grant.capabilities]
        try:
            row = self.store.update_grant_capabilities(controller_id, caps)
        except ValueError as exc:
            raise ProtocolError("INVALID_PARAMETERS", str(exc)) from exc
        if row is None:
            return None
        self._security_event(
            "grant_capabilities_changed_locally",
            {"controller_id": controller_id, "added": sorted(add_set), "removed": sorted(remove_set)},
        )
        await self.input.apply_capabilities(controller_id, row.effective_capabilities(self.store.current_snapshot_id()))
        if not controller_id.startswith("pending-"):
            if await self._send(grant_update_frame(controller_id, row.kid, list(row.capabilities))):
                self.store.clear_pending_grant_update(row.kid)
            else:
                log.warning("relay offline; grant_update will be re-sent on reconnect", controller_id=controller_id)
        self.state.request_update()
        self._update_ui()
        return row

    # ----- confirmations -------------------------------------------------------------------------------------------------
    async def _on_confirmation(self, frame: dict[str, Any]) -> None:
        try:
            vconf = verify_and_parse_confirmation(
                frame["envelope"], self.authz.resolve_key, registry=self.registry, schemas=self.schemas
            )
        except ProtocolError as exc:
            # Unverifiable confirmation: never terminate a command on the strength of an unverified payload.
            self._security_event("confirmation_rejected", {"code": exc.code})
            await self._send({"type": "error", "error": exc.to_frame_error()})
            if exc.code in MISMATCH_CODES:
                await self._count_mismatch()
            return
        if vconf.payload["target_pc_id"] != self.identity.pc_id:
            self._security_event("confirmation_identity_mismatch", {"code": "TARGET_PC_MISMATCH"})
            await self._send(
                {"type": "error", "error": self.registry.make_error("TARGET_PC_MISMATCH").to_frame_error()}
            )
            await self._count_mismatch()
            return
        if not self._snapshot_received:
            await self._send({"type": "error", "error": self.registry.make_error("PC_RECONNECTING").to_frame_error()})
            return
        try:
            pending = await self.confirmations.consume(
                vconf, recompute_target_digest=lambda vc: compute_target_state_digest(self.services, vc)
            )
        except ConfirmationError as exc:
            if exc.terminated:
                await self.emitter.result(vconf.command_id, exc.result_state, error=exc)
            else:
                self._security_event("confirmation_unmatched", {"code": exc.code})
                await self._send({"type": "error", "error": exc.to_frame_error()})
            return
        # The command waited for up to 60 s: re-apply steps 3-4 (remote_enabled, plan state, grant,
        # capability ∈ local ∩ snapshot) before anything is queued for execution.
        blocker = self.authz.recheck_grant(pending.command)
        if blocker is not None:
            await self.emitter.result(pending.command.command_id, "failed", error=blocker)
            log.info("confirmed command refused on re-check", command_id=pending.command.command_id, code=blocker.code)
            return
        await self._enqueue(pending.command)

    async def _sweep_confirmations(self) -> None:
        while True:
            await asyncio.sleep(CONFIRMATION_SWEEP_SECONDS)
            try:
                for pending in self.confirmations.expire_stale():
                    await self.emitter.result(
                        pending.command.command_id,
                        "expired",
                        error=self.registry.make_error("CONFIRMATION_EXPIRED"),
                        send=self._snapshot_received,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("confirmation sweep failed", error=exc.__class__.__name__)

    async def _cancel_pending_confirmations(
        self, *, controller_id: str | None, state: str, error: ProtocolError | None
    ) -> list[str]:
        canceled: list[str] = []
        for command_id in self.confirmations.pending_command_ids():
            pending = self.confirmations.pending_for(command_id)
            if pending is None or (controller_id is not None and pending.command.controller_id != controller_id):
                continue
            self.store.consume_challenge(
                pending.challenge_id, new_command_state=state, error_code=error.code if error else None
            )
            self.confirmations.drop(command_id)
            await self.emitter.result(command_id, state, error=error)
            canceled.append(command_id)
        return canceled

    # ----- cancel --------------------------------------------------------------------------------------------------------
    async def _on_cancel(self, frame: dict[str, Any]) -> None:
        command_id = frame["command_id"]
        row = self.store.journal_get(command_id)
        if row is None or row.controller_id != frame["controller_id"] or row.terminal:
            log.info("cancel ignored", command_id=command_id, known=row is not None)
            return
        if await self.executor.cancel_queued(command_id):
            return
        pending = self.confirmations.pending_for(command_id)
        if pending is not None:
            self.store.consume_challenge(pending.challenge_id, new_command_state="canceled")
            self.confirmations.drop(command_id)
            await self.emitter.result(command_id, "canceled")
            return
        armed = self.power.pending
        if armed is not None and armed.command_id == command_id:
            await self.power.cancel(reason="canceled from the phone")
            return
        log.info("cancel ignored: command already executing", command_id=command_id)

    # ----- revoked -------------------------------------------------------------------------------------------------------
    async def _on_revoked(self, frame: dict[str, Any]) -> None:
        self._security_event("pc_revoked", {"reason": frame["reason"]})
        log.error("relay reports this PC's cloud access is gone", reason=frame["reason"])

    # ----- local controls ------------------------------------------------------------------------------------------------
    async def set_remote_enabled(self, enabled: bool) -> None:
        self.store.set_remote_enabled(enabled)
        if not enabled:
            await self.input.end("remote_disabled")  # emergency stop releases held input first
            error = self.registry.make_error("PC_REMOTE_DISABLED")
            for command_id in list(self.executor.queued_ids):
                await self.executor.cancel_queued(command_id, error)
            await self._cancel_pending_confirmations(controller_id=None, state="canceled", error=error)
            if self.power.pending is not None:
                await self.power.cancel(reason="canceled because remote control was disabled on the PC")
            self._security_event("remote_control_disabled_locally", {})
        else:
            self._security_event("remote_control_enabled_locally", {})
        self.state.request_update()
        self._update_ui()

    async def revoke_controller_locally(self, controller_id: str) -> bool:
        row = self.store.revoke_grant_locally(controller_id, "local_revocation")
        if row is None:
            return False
        error = self.registry.make_error("CONTROLLER_REVOKED")
        await self.input.end_for_controller(controller_id, "controller_revoked")
        await self.executor.cancel_for_controller(controller_id, error)
        await self._cancel_pending_confirmations(controller_id=controller_id, state="canceled", error=error)
        self._security_event("controller_revoked_locally", {"controller_id": controller_id})
        if not controller_id.startswith("pending-"):
            # Journaled as pending first; cleared only once the frame was written. Offline → re-sent
            # after the next grants_snapshot (_resend_local_revocations).
            if await self._send(revoke_controller_frame(controller_id, row.kid)):
                self.store.clear_pending_revocation(controller_id)
            else:
                log.warning(
                    "relay offline; revoke_controller will be re-sent on reconnect", controller_id=controller_id
                )
        self.state.request_update()
        return True

    def status(self) -> dict[str, Any]:
        grants = [
            {
                "controller_id": g.controller_id,
                "kid": g.kid,
                "display_name": g.display_name,
                "capabilities": list(g.capabilities),
                "effective_capabilities": list(g.effective_capabilities(self.store.current_snapshot_id())),
                "status": g.snapshot_status,
                "granted_at": g.granted_at,
                "revoked_at": g.revoked_at,
            }
            for g in self.store.list_grants(include_revoked=True)
        ]
        return {
            "agent_version": __version__,
            "protocol_version": self.registry.protocol_version,
            "platform": self.platform.name,
            "identity": self.identity.status_summary(),
            "connection": self.relay.state if self.relay else "offline",
            "snapshot_received": self._snapshot_received,
            "relink_required": self.relink_required,
            "relink_reason": self.relink_reason,
            "configuration_error": self.configuration_error,
            "pending_revocations": [r.controller_id for r in self.store.pending_revocations()],
            "pending_grant_updates": [g.controller_id for g in self.store.pending_grant_updates()],
            "input": self.input.summary(),
            "store": self.store.summary(),
            "grants": grants,
            "entitlement": self.entitlement.summary(),
            "extension_connected": self.bridge.connected,
            "browser_instances": [i.summary() for i in self.bridge.instances()],
            "in_flight": self.executor.in_flight_ids(),
            "pending_confirmations": self.confirmations.pending_command_ids(),
            "pending_power": self.store.get_pending_power().__dict__ if self.store.get_pending_power() else None,
            "pairing": self.pairing.session.public_view() if self.pairing and self.pairing.session else None,
            "notes": list(self.status_notes),
            "uptime_seconds": int(time.time() - self.started_at),
        }

    def _control_ops(self) -> dict[str, Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]]:
        async def ping(_: dict[str, Any]) -> dict[str, Any]:
            return {"pong": True, "version": __version__}

        async def status(_: dict[str, Any]) -> dict[str, Any]:
            return self.status()

        async def enable(_: dict[str, Any]) -> dict[str, Any]:
            await self.set_remote_enabled(True)
            return {"remote_enabled": True}

        async def disable(_: dict[str, Any]) -> dict[str, Any]:
            await self.set_remote_enabled(False)
            return {"remote_enabled": False}

        async def pair_start(_: dict[str, Any]) -> dict[str, Any]:
            if self.pairing is None:
                raise ControlError("NOT_LINKED", "This PC is not linked yet. Run `dome-agent link` first.")
            if self.relay is None or not self.relay.connected:
                raise ControlError(
                    "OFFLINE", "The agent is not connected to the DoMe service; pairing needs a connection."
                )
            session = await self.pairing.start()
            d = session.display()
            # The code goes ONLY to the local caller over the authenticated control channel.
            return {
                "pairing_id": d.pairing_id,
                "code": d.code_formatted,
                "qr_url": d.qr_url,
                "expires_at": d.expires_at,
            }

        async def pair_status(_: dict[str, Any]) -> dict[str, Any]:
            if self.pairing is None or self.pairing.session is None:
                return {"active": False, "pending": []}
            return {
                "active": True,
                "pairing_id": self.pairing.session.pairing_id,
                "expires_at": self.pairing.session.expires_at,
                "pending": [
                    {
                        "pairing_id": p.pairing_id,
                        "display_name": p.display_name,
                        "verification_code": p.verification_code,
                        "requested_capabilities": list(p.requested_capabilities),
                        "expires_at": p.expires_at,
                    }
                    for p in self.pairing.pending_requests()
                ],
            }

        async def pair_approve(args: dict[str, Any]) -> dict[str, Any]:
            if self.pairing is None:
                raise ControlError("NOT_LINKED", "This PC is not linked yet.")
            granted = args.get("capabilities")
            pending = await self.pairing.approve(
                str(args["pairing_id"]), list(granted) if isinstance(granted, list) else None
            )
            return {"pairing_id": pending.pairing_id, "kid": pending.kid, "display_name": pending.display_name}

        async def pair_decline(args: dict[str, Any]) -> dict[str, Any]:
            if self.pairing is None:
                raise ControlError("NOT_LINKED", "This PC is not linked yet.")
            await self.pairing.decline(str(args["pairing_id"]))
            return {"pairing_id": str(args["pairing_id"]), "decision": "decline"}

        async def pair_cancel(_: dict[str, Any]) -> dict[str, Any]:
            if self.pairing is not None:
                self.pairing.cancel()
            return {"canceled": True}

        async def revoke(args: dict[str, Any]) -> dict[str, Any]:
            ok = await self.revoke_controller_locally(str(args["controller_id"]))
            return {"revoked": ok}

        async def grant(args: dict[str, Any]) -> dict[str, Any]:
            add = args.get("add") or []
            remove = args.get("remove") or []
            if not isinstance(add, list) or not isinstance(remove, list):
                raise ControlError("INVALID_PARAMETERS", "add/remove must be lists of capabilities")
            row = await self.update_grant_capabilities(
                str(args["controller_id"]), add=[str(c) for c in add], remove=[str(c) for c in remove]
            )
            if row is None:
                raise ControlError("UNKNOWN_CONTROLLER", "No active paired phone with that controller id.")
            return {
                "controller_id": row.controller_id,
                "capabilities": list(row.capabilities),
                "effective_capabilities": list(row.effective_capabilities(self.store.current_snapshot_id())),
                "pending_relay_update": row.kid in {g.kid for g in self.store.pending_grant_updates()},
            }

        async def show(_: dict[str, Any]) -> dict[str, Any]:
            try:
                self.ui.show_main()
            except Exception as exc:  # noqa: BLE001 - UI must never break the agent
                log.debug("show_main failed", error=exc.__class__.__name__)
            return {"shown": True, "pid": os.getpid(), "connection": self.relay.state if self.relay else "offline"}

        async def input_stop(_: dict[str, Any]) -> dict[str, Any]:
            released = await self.input.end("stopped")
            return {"released_holds": released}

        async def approve_app(args: dict[str, Any]) -> dict[str, Any]:
            try:
                row = self.apps.approve(str(args["app_id"]), str(args["exe_path"]), args.get("display_name"))
            except ValueError as exc:
                raise ControlError("INVALID_APP", str(exc)) from exc
            self.state.request_update()
            return {
                "app_id": row.app_id,
                "display_name": row.display_name,
                "exe_path": row.exe_path,
                "exe_sha256": row.exe_sha256,
            }

        async def remove_app(args: dict[str, Any]) -> dict[str, Any]:
            return {"removed": self.apps.remove(str(args["app_id"]))}

        async def reconnect(_: dict[str, Any]) -> dict[str, Any]:
            if self.relay is None:
                raise ControlError("NOT_LINKED", "This PC is not linked yet.")
            self.relay.request_reconnect()
            return {"requested": True}

        async def diagnostics(_: dict[str, Any]) -> dict[str, Any]:
            path = await asyncio.to_thread(write_bundle, self.settings, self.status())
            return {"path": str(path)}

        async def power_cancel(_: dict[str, Any]) -> dict[str, Any]:
            return await self.power.cancel(reason="canceled on the PC")

        async def quit_agent(_: dict[str, Any]) -> dict[str, Any]:
            self.request_stop()
            return {"stopping": True}

        return {
            "ping": ping,
            "status": status,
            "enable": enable,
            "disable": disable,
            "pair_start": pair_start,
            "pair_status": pair_status,
            "pair_approve": pair_approve,
            "pair_decline": pair_decline,
            "pair_cancel": pair_cancel,
            "revoke": revoke,
            "grant": grant,
            "show": show,
            "input_stop": input_stop,
            "approve_app": approve_app,
            "remove_app": remove_app,
            "reconnect": reconnect,
            "diagnostics": diagnostics,
            "power_cancel": power_cancel,
            "quit": quit_agent,
        }

    # ----- plumbing ---------------------------------------------------------------------------------------------------------
    async def _precheck(self, vc: Any) -> ProtocolError | None:
        return await self.authz.precheck(vc)  # self.authz is rebuilt on (re-)link: resolve it late

    async def _send(self, frame: dict[str, Any]) -> bool:
        if self.relay is None:
            return False
        try:
            return await self.relay.send(frame)
        except ProtocolError as exc:
            log.error(
                "outbound frame failed contract validation; not sent", type=frame.get("type"), message=exc.message
            )
            return False

    async def _emit_state(self, state: dict[str, Any]) -> bool:
        if self.relay is None or not self.relay.connected or not self._snapshot_received or self.identity.pc_id is None:
            return False
        return await self._send(state_frame(self.identity.pc_id, state))

    def _on_bridge_change(self) -> None:
        self.state.request_update()
        self._update_ui()

    def _security_event(self, kind: str, detail: dict[str, Any]) -> None:
        self.store.add_security_event(kind, **detail)

    def _update_ui(self) -> None:
        view = StatusView(
            connection=self.relay.state if self.relay else "offline",
            remote_enabled=self.store.remote_enabled,
            linked=self.identity.is_linked,
            relink_required=self.relink_required,
            pc_name=self.identity.pc_name,
            extension_connected=self.bridge.connected,
            notes=list(self.status_notes),
        )
        try:
            self.ui.update_status(view)
        except Exception:  # noqa: BLE001 - UI must never break the agent
            log.debug("ui update failed")

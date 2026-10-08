"""Execution context handed to action handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dome_protocol import ProtocolError, Registry
from dome_protocol.commands import VerifiedCommand

if TYPE_CHECKING:
    from ..bridge.server import BridgeServer
    from ..identity import Identity
    from ..platform.protocol import PlatformSet
    from ..state import StateAggregator
    from ..store import Store
    from .power import PowerManager


@dataclass(slots=True)
class AgentServices:
    """Long-lived collaborators shared by every handler."""

    registry: Registry
    store: Store
    platform: PlatformSet
    bridge: BridgeServer
    state: StateAggregator
    power: PowerManager
    identity: Identity


@dataclass(slots=True)
class ExecutionContext:
    services: AgentServices
    command: VerifiedCommand
    side_effect_issued: bool = False
    best_known_error: ProtocolError | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def params(self) -> dict[str, Any]:
        return self.command.params

    @property
    def target(self) -> dict[str, Any] | None:
        return self.command.target

    @property
    def action(self) -> str:
        return self.command.spec.name

    def mark_side_effect(self) -> None:
        """Call right before the OS/browser call for non-idempotent actions. A timeout afterwards is
        reported as ``outcome_unknown`` instead of ``failed`` (the action may have happened)."""
        self.side_effect_issued = True

    def note_error(self, error: ProtocolError) -> None:
        self.best_known_error = error


Handler = Callable[[ExecutionContext], Awaitable[dict[str, Any]]]

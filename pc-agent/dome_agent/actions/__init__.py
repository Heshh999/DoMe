"""Action handlers: one coroutine per registry action.

Handlers receive an :class:`ExecutionContext`, talk to the platform adapters or the browser bridge,
and return the action's result object (validated by the executor with
``registry.validate_result``) — or :data:`DEFERRED` when completion is delivered later (power
countdowns). Failures are :class:`dome_protocol.ProtocolError` (optionally :class:`ActionFailed`
with a best-known post-failure state).
"""

from __future__ import annotations

from collections.abc import Callable

from .context import DEFERRED, ActionFailed, AgentServices, Deferred, ExecutionContext, Handler

_HANDLERS: dict[str, Handler] = {}
_LOADED = False


def handler(action: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        if action in _HANDLERS:
            raise RuntimeError(f"duplicate handler for {action}")
        _HANDLERS[action] = fn
        return fn

    return register


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from . import apps, media, power, system, volume, windows, youtube  # noqa: F401  (registration side effects)


def handler_for(action: str) -> Handler:
    _load()
    try:
        return _HANDLERS[action]
    except KeyError:
        raise LookupError(f"no handler registered for {action}") from None


def all_handlers() -> dict[str, Handler]:
    _load()
    return dict(_HANDLERS)


__all__ = ["DEFERRED", "ActionFailed", "AgentServices", "Deferred", "ExecutionContext", "Handler", "all_handlers", "handler", "handler_for"]

"""Action handlers: one coroutine per registry action.

Handlers receive an :class:`ExecutionContext`, talk to the platform adapters or the browser bridge,
and return the action's result object (validated by the executor with
``registry.validate_result``) — or :data:`DEFERRED` when completion is delivered later (power
countdowns). Failures are :class:`dome_protocol.ProtocolError` (optionally :class:`ActionFailed`
with a best-known post-failure state).

A result is journaled durably as sent (duplicates and late results are re-sent from the journal) unless
its action registers a :func:`journal_view`: untrusted display text the phone needs once, such as window
titles, is then sent but never written to disk.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .context import DEFERRED, ActionFailed, AgentServices, Deferred, ExecutionContext, Handler

JournalView = Callable[[dict[str, Any]], dict[str, Any]]

_HANDLERS: dict[str, Handler] = {}
_JOURNAL_VIEWS: dict[str, JournalView] = {}
_LOADED = False


def handler(action: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        if action in _HANDLERS:
            raise RuntimeError(f"duplicate handler for {action}")
        _HANDLERS[action] = fn
        return fn

    return register


def journal_view(action: str) -> Callable[[JournalView], JournalView]:
    """Register the copy of ``action``'s (schema-valid) result that the journal keeps instead of the one sent."""

    def register(fn: JournalView) -> JournalView:
        if action in _JOURNAL_VIEWS:
            raise RuntimeError(f"duplicate journal view for {action}")
        _JOURNAL_VIEWS[action] = fn
        return fn

    return register


def journal_copy(action: str, result: dict[str, Any] | None) -> dict[str, Any] | None:
    """``result`` as the journal keeps it (:func:`journal_view`); unchanged for most actions."""
    _load()
    view = _JOURNAL_VIEWS.get(action)
    return result if result is None or view is None else view(result)


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from . import (  # noqa: F401  (registration side effects)
        apps,
        input,
        media,
        power,
        screen,
        system,
        volume,
        windows,
        youtube,
    )


def handler_for(action: str) -> Handler:
    _load()
    try:
        return _HANDLERS[action]
    except KeyError:
        raise LookupError(f"no handler registered for {action}") from None


def all_handlers() -> dict[str, Handler]:
    _load()
    return dict(_HANDLERS)


__all__ = [
    "DEFERRED",
    "ActionFailed",
    "AgentServices",
    "Deferred",
    "ExecutionContext",
    "Handler",
    "all_handlers",
    "handler",
    "handler_for",
    "journal_copy",
    "journal_view",
]

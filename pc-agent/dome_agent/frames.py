"""Builders for the frames the agent emits (``relay-frames.schema.json#/$defs/agent_to_relay``).

Every builder returns a plain dict; :class:`dome_agent.relay_client.RelayClient` validates each frame
against the schema before it is written to the socket, so a bug here fails loudly instead of
producing a frame the relay would reject.
"""

from __future__ import annotations

from typing import Any

from dome_protocol import ProtocolError, format_rfc3339, load_registry, now_utc

from . import SUPPORTED_PROTOCOL_VERSIONS, __version__

type ErrorSpec = ProtocolError | tuple[str, str] | tuple[str, str, bool]


def now_text() -> str:
    return format_rfc3339(now_utc())


def make_error(code: str, message: str | None = None, *, retryable: bool | None = None, **detail: Any) -> ProtocolError:
    """A ProtocolError with the registry's default copy/retryability for ``code``."""
    return load_registry().make_error(code, message, retryable=retryable, **detail)


def error_object(spec: ErrorSpec) -> dict[str, Any]:
    if isinstance(spec, ProtocolError):
        return spec.to_frame_error()
    code, message, *rest = spec
    retryable = bool(rest[0]) if rest else bool(load_registry().error_defaults(code)["retryable"])
    return {"code": code, "message": message[:512], "retryable": retryable}


def hello_frame() -> dict[str, Any]:
    reg = load_registry()
    return {
        "type": "hello",
        "component": "agent",
        "component_version": __version__,
        "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "registry_version": reg.registry_version,
    }


def ack_frame(command_id: str, state: str) -> dict[str, Any]:
    if state not in ("accepted", "awaiting_confirmation", "executing"):
        raise ValueError(state)
    return {"type": "ack", "command_id": command_id, "state": state, "at": now_text()}


def result_frame(
    command_id: str,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    error: ErrorSpec | None = None,
    warning: str | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    if state not in ("succeeded", "failed", "expired", "canceled", "outcome_unknown"):
        raise ValueError(state)
    frame: dict[str, Any] = {
        "type": "result",
        "command_id": command_id,
        "origin": "agent",
        "state": state,
        "at": now_text(),
        "duration_ms": max(0, int(duration_ms)),
    }
    if result is not None:
        frame["result"] = result
    if error is not None:
        frame["error"] = error_object(error)
    if warning:
        frame["warning"] = warning[:256]
    return frame


def confirmation_required_frame(command_id: str, challenge_text: str) -> dict[str, Any]:
    return {"type": "confirmation_required", "command_id": command_id, "challenge_text": challenge_text}


def state_frame(pc_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {"type": "state", "pc_id": pc_id, "at": now_text(), "state": state}


def error_frame(spec: ErrorSpec) -> dict[str, Any]:
    return {"type": "error", "error": error_object(spec)}


def ping_frame() -> dict[str, Any]:
    return {"type": "ping", "t": now_text()}


def pong_frame(t: str | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "pong"}
    if t is not None:
        frame["t"] = t
    return frame


def pairing_decision_frame(pairing_id: str, decision: str, kid: str, granted: list[str]) -> dict[str, Any]:
    return {
        "type": "pairing_decision",
        "pairing_id": pairing_id,
        "decision": decision,
        "kid": kid,
        "granted_capabilities": list(dict.fromkeys(granted)),
    }


def revoke_controller_frame(controller_id: str, kid: str, reason: str = "local_revocation") -> dict[str, Any]:
    return {"type": "revoke_controller", "controller_id": controller_id, "kid": kid, "reason": reason}


def grant_update_frame(controller_id: str, kid: str, capabilities: list[str]) -> dict[str, Any]:
    """``rules.grant_update``: the PC owner changed a controller's capabilities locally; the relay replaces
    the grant's list with exactly this one."""
    return {
        "type": "grant_update",
        "controller_id": controller_id,
        "kid": kid,
        "capabilities": list(dict.fromkeys(capabilities)),
    }


def input_ack_frame(
    pc_id: str,
    input_session_id: str,
    *,
    last_seq: int,
    accepted_events: int,
    dropped_events: int,
    held_buttons: list[str],
    held_keys: list[str],
) -> dict[str, Any]:
    """``agent_input_ack``: Windows accepted these events (never an observed application effect)."""
    return {
        "type": "input_ack",
        "pc_id": pc_id,
        "input_session_id": input_session_id,
        "last_seq": max(0, int(last_seq)),
        "accepted_events": max(0, int(accepted_events)),
        "dropped_events": max(0, int(dropped_events)),
        "held_buttons": sorted(held_buttons)[:3],
        "held_keys": sorted(held_keys)[:16],
        "at": now_text(),
    }


def input_session_frame(
    pc_id: str, input_session_id: str, controller_id: str, *, event: str, reason: str, holds_released: int
) -> dict[str, Any]:
    """``agent_input_session``: started / suspended / ended with the reason and the holds released."""
    if event not in ("started", "suspended", "ended"):
        raise ValueError(event)
    return {
        "type": "input_session",
        "pc_id": pc_id,
        "input_session_id": input_session_id,
        "controller_id": controller_id,
        "event": event,
        "reason": reason,
        "holds_released": max(0, int(holds_released)),
        "at": now_text(),
    }

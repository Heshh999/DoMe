"""Frame construction helpers. Every frame the relay builds itself is validated against the
contract before it is sent, so a relay bug can never put a non-conforming frame on the wire."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from dome_protocol import ProtocolError, load_registry, load_schemas

from dome_api.util import ts_required, utcnow


def error_object(code: str, message: str | None = None, *, retryable: bool | None = None, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    reg = load_registry()
    defaults = reg.errors.get(code) or reg.errors["INTERNAL"]
    out: dict[str, Any] = {
        "code": code if code in reg.errors else "INTERNAL",
        "message": (message or str(defaults["user_message"]))[:512],
        "retryable": bool(defaults["retryable"]) if retryable is None else retryable,
    }
    if detail:
        out["detail"] = detail
    return out


def error_from_exc(exc: ProtocolError) -> dict[str, Any]:
    return error_object(exc.code, exc.message, retryable=exc.retryable, detail=exc.detail or None)


def error_frame(code: str, message: str | None = None, *, ref_pc_id: uuid.UUID | str | None = None, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "error", "error": error_object(code, message, detail=detail)}
    if ref_pc_id is not None:
        frame["ref_pc_id"] = str(ref_pc_id)
    return frame


def relay_result(
    command_id: uuid.UUID | str,
    state: str,
    *,
    error: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    duration = int((now - started_at).total_seconds() * 1000) if started_at else 0
    frame: dict[str, Any] = {
        "type": "result",
        "command_id": str(command_id),
        "origin": "relay",
        "state": state,
        "at": ts_required(now),
        "duration_ms": max(0, duration),
    }
    if error is not None:
        frame["error"] = error
    if warning:
        frame["warning"] = warning[:256]
    return frame


def hello_ack(connection_id: uuid.UUID, *, controller_id: uuid.UUID | None = None, pc_id: uuid.UUID | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "hello_ack",
        "protocol_version": load_registry().protocol_version,
        "server_time": ts_required(utcnow()),
        "connection_id": str(connection_id),
    }
    if controller_id is not None:
        frame["controller_id"] = str(controller_id)
    if pc_id is not None:
        frame["pc_id"] = str(pc_id)
    return frame


def pc_status(pc_id: uuid.UUID, *, connection: str, last_seen: datetime | None, last_power_request: dict[str, Any] | None, enabled: bool) -> dict[str, Any]:
    return {
        "type": "pc_status",
        "pc_id": str(pc_id),
        "connection": connection,
        "last_seen": ts_required(last_seen) if last_seen else None,
        "last_power_request": last_power_request,
        "enabled": enabled,
    }


def validate_outbound(direction: str, frame: dict[str, Any]) -> None:
    load_schemas().validate_frame(direction, frame)

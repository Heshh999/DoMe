"""User-interface seam. The agent core talks to an :class:`AgentUI`; implementations are the tray +
tkinter windows (:mod:`dome_agent.tray`, Windows), a console presenter used by the CLI, and
:class:`NullUI` for headless runs where the CLI drives pairing over the control channel.

Nothing here logs: pairing codes and verification codes are shown to the local user only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PairingDisplay:
    """What the PC shows while waiting for the phone: formatted code, QR payload, expiry."""

    pairing_id: str
    code_formatted: str
    qr_url: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class PairingApproval:
    """A phone claimed the code: show its (untrusted) name, the 6 digits and the requested scopes."""

    pairing_id: str
    controller_display_name: str
    verification_code: str
    requested_capabilities: tuple[str, ...]
    kid: str
    expires_at: str


@dataclass(slots=True)
class StatusView:
    connection: str = "offline"  # offline | connecting | connected | reconnecting | superseded | stopped
    remote_enabled: bool = False
    linked: bool = False
    relink_required: bool = False
    pc_name: str = ""
    extension_connected: bool = False
    notes: list[str] = field(default_factory=list)


class AgentUI(Protocol):
    def show_pairing_code(self, display: PairingDisplay) -> None: ...

    def show_pairing_request(self, approval: PairingApproval) -> None: ...

    def pairing_finished(self, pairing_id: str, decision: str) -> None: ...

    def notify(self, title: str, message: str) -> None: ...

    def update_status(self, status: StatusView) -> None: ...


class NullUI:
    """Headless: everything is reachable through the control channel / CLI instead."""

    def show_pairing_code(self, display: PairingDisplay) -> None:
        return None

    def show_pairing_request(self, approval: PairingApproval) -> None:
        return None

    def pairing_finished(self, pairing_id: str, decision: str) -> None:
        return None

    def notify(self, title: str, message: str) -> None:
        return None

    def update_status(self, status: StatusView) -> None:
        return None


def qr_ascii(url: str) -> str:
    """Render the QR payload as ASCII blocks for a console (never logged)."""
    import io

    import qrcode

    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    out = io.StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()

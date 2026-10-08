from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProtocolError(Exception):
    """A rejection with a stable error code from ``shared/protocol/errors.json``."""

    code: str
    message: str
    retryable: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:  # make it a proper Exception
        Exception.__init__(self, f"{self.code}: {self.message}")

    def to_frame_error(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.detail:
            out["detail"] = self.detail
        return out

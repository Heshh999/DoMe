"""TEST DOUBLE: behaves like the browser extension's service worker on the native-messaging side.

Connects to the agent's bridge IPC endpoint (as ``dome-native-host`` would), sends ``bridge_hello``,
announces configurable YouTube tabs and answers ``bridge_request`` ops the way the real extension
would: it verifies ``tab_token``, applies the op to its in-memory tab, reports ``NO_NEXT_VIDEO`` /
``UNSUPPORTED_CONTEXT`` / ``ACTIVATION_REQUIRED`` where the real page would, and returns the
post-action tab state. Every frame in both directions is schema-validated.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dome_protocol import format_rfc3339, now_utc

from ..bridge import ipc
from ..bridge.framing import decode_frame, encode_frame, validate_outgoing


def new_tab_token() -> str:
    return secrets.token_urlsafe(16)[:22].ljust(22, "A")


@dataclass
class FakeTab:
    tab_id: int
    video_id: str = "dQw4w9WgXcQ"
    title: str = "Test video"
    paused: bool = False
    muted: bool = False
    volume: int = 80
    position: float = 10.0
    duration: float = 200.0
    context: str = "watch"
    ad_showing: bool = False
    is_live: bool = False
    in_playlist: bool = False
    theater: bool = False
    fullscreen: bool = False
    has_next: bool = True
    has_previous: bool = False
    script_attached: bool = True
    tab_token: str = field(default_factory=new_tab_token)
    next_videos: list[str] = field(default_factory=lambda: ["9bZkp7q19f0", "kJQP7kiw5Fk"])
    fullscreen_allowed: bool = False

    def as_frame(self, browser_instance_id: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "browser_instance_id": browser_instance_id,
            "tab_id": self.tab_id,
            "script_attached": self.script_attached,
            "context": self.context,
            "ad_showing": self.ad_showing,
            "is_live": self.is_live,
            "in_playlist": self.in_playlist,
            "title": self.title,
            "video_id": self.video_id,
            "paused": self.paused,
            "muted": self.muted,
            "volume": self.volume,
            "position_seconds": self.position,
            "duration_seconds": self.duration,
            "theater": self.theater,
            "fullscreen": self.fullscreen,
            "has_next": self.has_next,
            "has_previous": self.has_previous,
        }
        if self.script_attached:
            out["tab_token"] = self.tab_token
        return out


class FakeExtension:
    def __init__(self, state_dir: Path, *, browser_instance_id: str = "bi_fake0001", browser: str = "chrome", protocol_versions: tuple[str, ...] = ("1.0",), op_delay: float = 0.0) -> None:
        self.state_dir = state_dir
        self.browser_instance_id = browser_instance_id
        self.browser = browser
        self.protocol_versions = protocol_versions
        self.op_delay = op_delay
        self.tabs: dict[int, FakeTab] = {}
        self.requests: list[dict[str, Any]] = []
        self.received: list[dict[str, Any]] = []
        self.hello_ack: dict[str, Any] | None = None
        self.errors: list[dict[str, Any]] = []
        self._conn: ipc.FrameConnection | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()

    # ----- lifecycle --
    def connect(self, timeout: float = 5.0) -> None:
        self._conn = ipc.connect(self.state_dir, timeout=timeout, kind="bridge")
        hello = {"type": "bridge_hello", "browser_instance_id": self.browser_instance_id, "browser": self.browser, "extension_version": "0.0.0-test", "protocol_versions": list(self.protocol_versions), "profile_label": "Test profile"}
        self._write(hello)
        self._thread = threading.Thread(target=self._reader, name="fake-extension", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("no bridge_hello_ack from the agent")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._closed.set()

    def wait_closed(self, timeout: float = 5.0) -> bool:
        return self._closed.wait(timeout)

    # ----- helpers --
    def add_tab(self, tab: FakeTab) -> FakeTab:
        self.tabs[tab.tab_id] = tab
        return tab

    def publish_tabs(self) -> None:
        self._write({"type": "bridge_event", "event": "tabs_changed", "at": format_rfc3339(now_utc()), "tabs": [t.as_frame(self.browser_instance_id) for t in self.tabs.values()]})

    def publish_player_state(self, tab_id: int) -> None:
        self._write({"type": "bridge_event", "event": "player_state", "at": format_rfc3339(now_utc()), "tab": self.tabs[tab_id].as_frame(self.browser_instance_id)})

    def send_raw(self, frame: dict[str, Any]) -> None:
        """Send without validation (for schema-rejection tests)."""
        assert self._conn is not None
        self._conn.write_frame(encode_frame(frame))

    def send_raw_bytes(self, data: bytes) -> None:
        assert self._conn is not None
        self._conn.write_frame(data)

    def _write(self, frame: dict[str, Any]) -> None:
        validate_outgoing(frame, "extension_to_agent")
        assert self._conn is not None
        self._conn.write_frame(encode_frame(frame))

    # ----- reader --
    def _reader(self) -> None:
        assert self._conn is not None
        try:
            while True:
                raw = self._conn.read_frame()
                if raw is None:
                    break
                frame = decode_frame(raw, "agent_to_extension")
                self.received.append(frame)
                if frame["type"] == "bridge_hello_ack":
                    self.hello_ack = frame
                    self._ready.set()
                elif frame["type"] == "bridge_request":
                    self.requests.append(frame)
                    if self.op_delay:
                        time.sleep(self.op_delay)
                    self._write(self._answer(frame))
                elif frame["type"] == "bridge_error":
                    self.errors.append(frame)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._closed.set()

    def _fail(self, request_id: str, code: str, message: str) -> dict[str, Any]:
        return {"type": "bridge_response", "request_id": request_id, "ok": False, "error": {"code": code, "message": message}}

    def _answer(self, frame: dict[str, Any]) -> dict[str, Any]:
        rid = frame["request_id"]
        op = frame["op"]
        args = frame["args"]
        if op == "list_tabs":
            return {"type": "bridge_response", "request_id": rid, "ok": True, "result": {"tabs": [t.as_frame(self.browser_instance_id) for t in self.tabs.values()]}}
        tab = self.tabs.get(int(args.get("tab_id", -1)))
        if tab is None:
            return self._fail(rid, "TARGET_GONE", "tab closed")
        if not tab.script_attached:
            return self._fail(rid, "TAB_NOT_CONTROLLABLE", "no content script")
        if args.get("tab_token") != tab.tab_token:
            return self._fail(rid, "TARGET_CHANGED", "tab token mismatch")
        expected = args.get("expected_video_id")
        if expected is not None and expected != tab.video_id:
            return self._fail(rid, "TARGET_CHANGED", "video changed")
        result: dict[str, Any]
        if op == "get_state":
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op == "set_paused":
            tab.paused = bool(args["paused"])
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op in ("next", "previous"):
            if tab.ad_showing or tab.is_live:
                return self._fail(rid, "UNSUPPORTED_CONTEXT", "ad or live")
            if op == "next":
                if not tab.next_videos:
                    return self._fail(rid, "NO_NEXT_VIDEO", "end of queue")
                previous = tab.video_id
                tab.video_id = tab.next_videos.pop(0)
                tab.has_previous = True
                tab.has_next = bool(tab.next_videos)
            else:
                if not tab.has_previous:
                    return self._fail(rid, "NO_PREVIOUS_VIDEO", "no previous")
                previous = tab.video_id
                tab.video_id = "prev" + previous[:5]
            tab.position = 0.0
            result = {"tab": tab.as_frame(self.browser_instance_id), "previous_video_id": previous}
        elif op == "seek_relative":
            tab.position = max(0.0, min(tab.duration, tab.position + float(args["seconds"])))
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op == "seek_to":
            tab.position = max(0.0, min(tab.duration, float(args["position_seconds"])))
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op == "set_muted":
            tab.muted = bool(args["muted"])
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op == "set_volume":
            tab.volume = int(args["value"])
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op == "set_theater":
            if tab.context != "watch":
                return self._fail(rid, "UNSUPPORTED_CONTEXT", "theater only on watch pages")
            tab.theater = bool(args["enabled"])
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op == "request_fullscreen":
            if not tab.fullscreen_allowed:
                return self._fail(rid, "ACTIVATION_REQUIRED", "no user activation")
            tab.fullscreen = True
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        else:
            return self._fail(rid, "UNKNOWN_ACTION", op)
        return {"type": "bridge_response", "request_id": rid, "ok": True, "result": result}

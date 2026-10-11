"""TEST DOUBLE: behaves like the browser extension's service worker on the native-messaging side.

Connects to the agent's bridge IPC endpoint (as ``dome-native-host`` would), sends ``bridge_hello``,
announces configurable YouTube tabs and answers ``bridge_request`` ops the way the real extension
would: it verifies ``tab_token``, applies the op to its in-memory tab, reports ``NO_NEXT_VIDEO`` /
``UNSUPPORTED_CONTEXT`` / ``ACTIVATION_REQUIRED`` where the real page would, and returns the
post-action tab state. Every frame in both directions is schema-validated.

An extension whose ``protocol_versions`` predate an op (``OP_SINCE``) rejects its request the way an
older build's bridge schema would: ``bridge_error`` MALFORMED_MESSAGE.

``search`` (protocol 1.2) loads a results page like ``browser-extension/src/background/search.ts``: in the
token-checked tab or in a new tab, always as a NEW attachment (new tab_token), without a title, with
:attr:`FakeExtension.search_results` as its results (fake data; it never contains the query). Like the
real extension's debounced ``tabs_changed``, the new page is announced only AFTER the answer
(:attr:`FakeExtension.publish_on_search`). ``open_result`` checks the index (SEARCH_RESULT_UNAVAILABLE)
and the expected video (TARGET_CHANGED), then plays that result in the same attachment (YouTube's own
in-page navigation keeps the token).

Like the real extension (``browser-extension/src/background/connection.ts``) it refuses a
``bridge_hello_ack`` whose ``protocol_version`` its own ``protocol_versions`` do not reach: it answers
``bridge_error`` PROTOCOL_INCOMPATIBLE, disconnects, and :meth:`FakeExtension.connect` raises. By default it
lists what the shipped extension lists (:data:`SHIPPED_PROTOCOL_VERSIONS`).
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dome_protocol import ProtocolError, format_rfc3339, now_utc
from dome_protocol.commands import protocol_compatible

from ..bridge import ipc
from ..bridge.framing import decode_frame, encode_frame, validate_outgoing
from ..bridge.server import OP_SINCE

# What browser-extension/src/shared/version.ts ``SUPPORTED_PROTOCOL_VERSIONS`` lists, so the agent is tested
# against the versions the real extension sends; tests/test_extension_protocol_parity.py checks it stays so.
SHIPPED_PROTOCOL_VERSIONS: tuple[str, ...] = ("1.0", "1.1", "1.2")

# Ops that need a video player in the page: the real content script refuses them on a page without one.
PLAYER_OPS = frozenset(
    {
        "set_paused",
        "next",
        "previous",
        "seek_relative",
        "seek_to",
        "set_muted",
        "set_volume",
        "set_theater",
        "request_fullscreen",
    }
)


def new_tab_token() -> str:
    return secrets.token_urlsafe(16)[:22].ljust(22, "A")


def fake_search_results(count: int = 5) -> list[dict[str, Any]]:
    """``youtube_search_item`` entries as a results page would list them (fake, query-independent data)."""
    return [
        {
            "index": i,
            "video_id": f"result{i:05d}",
            "title": f"Result video {i}",
            "channel": f"Channel {i}",
            "duration_seconds": 60 * i,
        }
        for i in range(1, count + 1)
    ]


@dataclass
class FakeTab:
    tab_id: int
    video_id: str | None = "dQw4w9WgXcQ"  # None: a page without a player (a results page)
    title: str | None = "Test video"
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
    results: list[dict[str, Any]] | None = None  # set on a search results page

    @classmethod
    def results_page(cls, tab_id: int, results: list[dict[str, Any]] | None = None) -> FakeTab:
        """A YouTube results page as the 1.2 extension reports it: context "other", no player, no title."""
        tab = cls(tab_id=tab_id, has_next=False)
        tab.show_results(fake_search_results() if results is None else results)
        return tab

    def show_results(self, results: list[dict[str, Any]]) -> None:
        """Load a results page in this tab: a new document, so the content script attaches again (new token)."""
        self.context, self.video_id, self.title = "other", None, None
        self.ad_showing = self.is_live = self.in_playlist = self.theater = self.fullscreen = False
        self.has_next = self.has_previous = False
        self.script_attached, self.tab_token = True, new_tab_token()
        self.results = [dict(item) for item in results]

    def as_frame(self, browser_instance_id: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "browser_instance_id": browser_instance_id,
            "tab_id": self.tab_id,
            "script_attached": self.script_attached,
            "context": self.context,
            "ad_showing": self.ad_showing,
            "is_live": self.is_live,
            "in_playlist": self.in_playlist,
            "fullscreen": self.fullscreen,
        }
        if self.title is not None:
            out["title"] = self.title
        if self.video_id is not None:
            out.update(
                {
                    "video_id": self.video_id,
                    "paused": self.paused,
                    "muted": self.muted,
                    "volume": self.volume,
                    "position_seconds": self.position,
                    "duration_seconds": self.duration,
                    "theater": self.theater,
                    "has_next": self.has_next,
                    "has_previous": self.has_previous,
                }
            )
        if self.script_attached:
            out["tab_token"] = self.tab_token
        return out


class FakeExtension:
    def __init__(
        self,
        state_dir: Path,
        *,
        browser_instance_id: str = "bi_fake0001",
        browser: str = "chrome",
        protocol_versions: tuple[str, ...] = SHIPPED_PROTOCOL_VERSIONS,
        op_delay: float = 0.0,
    ) -> None:
        self.state_dir = state_dir
        self.browser_instance_id = browser_instance_id
        self.browser = browser
        self.protocol_versions = protocol_versions
        self.op_delay = op_delay
        self.tabs: dict[int, FakeTab] = {}
        # search behaviour (protocol 1.2)
        self.search_results: list[dict[str, Any]] | None = None  # verbatim results of the next search (None: 5 fakes)
        self.layout_unrecognised = False  # the results page loads but cannot be read → ACTION_UNAVAILABLE
        self.publish_on_search = True  # announce the results page (tabs_changed) right after the answer
        self.open_result_lands_on: str | None = None  # a misbehaving extension: reports this video instead
        # a misbehaving (or hostile) connection: answers open_result naming this (browser_instance_id, tab_id)
        self.open_result_reports_tab: tuple[str, int] | None = None
        self.leak_results_title = False  # like a pre-1.2 build: the results page reports "<query> - YouTube"
        self.requests: list[dict[str, Any]] = []
        self.received: list[dict[str, Any]] = []
        self.hello_ack: dict[str, Any] | None = None
        self.refused_ack: dict[str, Any] | None = None  # an ack this extension's versions do not reach
        self.errors: list[dict[str, Any]] = []
        self._conn: ipc.FrameConnection | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()

    # ----- lifecycle --
    def connect(self, timeout: float = 5.0) -> None:
        self._conn = ipc.connect(self.state_dir, timeout=timeout, kind="bridge")
        hello = {
            "type": "bridge_hello",
            "browser_instance_id": self.browser_instance_id,
            "browser": self.browser,
            "extension_version": "0.0.0-test",
            "protocol_versions": list(self.protocol_versions),
            "profile_label": "Test profile",
        }
        self._write(hello)
        self._thread = threading.Thread(target=self._reader, name="fake-extension", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("no bridge_hello_ack from the agent")
        if self.refused_ack is not None:
            raise ProtocolError(
                "PROTOCOL_INCOMPATIBLE",
                f"the agent acked protocol {self.refused_ack['protocol_version']}, "
                f"which this extension ({', '.join(self.protocol_versions)}) does not speak",
            )

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
        self._write(
            {
                "type": "bridge_event",
                "event": "tabs_changed",
                "at": format_rfc3339(now_utc()),
                "tabs": [t.as_frame(self.browser_instance_id) for t in self.tabs.values()],
            }
        )

    def publish_player_state(self, tab_id: int) -> None:
        self._write(
            {
                "type": "bridge_event",
                "event": "player_state",
                "at": format_rfc3339(now_utc()),
                "tab": self.tabs[tab_id].as_frame(self.browser_instance_id),
            }
        )

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
                    if not protocol_compatible(frame["protocol_version"], self.protocol_versions):
                        self._refuse_ack(frame)
                        break
                    self.hello_ack = frame
                    self._ready.set()
                elif frame["type"] == "bridge_request":
                    self.requests.append(frame)
                    if not protocol_compatible(OP_SINCE.get(frame["op"], "1.0"), self.protocol_versions):
                        # an extension that predates the op: its bridge schema rejects the frame (connection.ts)
                        self._write(
                            {
                                "type": "bridge_error",
                                "ref_request_id": frame["request_id"],
                                "error": {
                                    "code": "MALFORMED_MESSAGE",
                                    "message": "frame does not match the bridge schema",
                                },
                            }
                        )
                        continue
                    if self.op_delay:
                        time.sleep(self.op_delay)
                    response = self._answer(frame)
                    self._write(response)
                    if frame["op"] == "search" and self.publish_on_search:
                        self.publish_tabs()  # the real extension's tabs_changed is debounced: after the answer
                elif frame["type"] == "bridge_error":
                    self.errors.append(frame)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._closed.set()

    def _refuse_ack(self, frame: dict[str, Any]) -> None:
        """What connection.ts does with an ack it does not speak: report it and disconnect."""
        self.refused_ack = frame
        try:
            self._write(
                {
                    "type": "bridge_error",
                    "error": {
                        "code": "PROTOCOL_INCOMPATIBLE",
                        "message": "The DoMe extension and agent speak incompatible protocol versions.",
                        "detail": {"peer": [frame["protocol_version"]], "supported": list(self.protocol_versions)},
                    },
                }
            )
        finally:
            self._ready.set()
            assert self._conn is not None
            self._conn.close()

    def _fail(self, request_id: str, code: str, message: str) -> dict[str, Any]:
        return {
            "type": "bridge_response",
            "request_id": request_id,
            "ok": False,
            "error": {"code": code, "message": message},
        }

    def _answer(self, frame: dict[str, Any]) -> dict[str, Any]:
        rid = frame["request_id"]
        op = frame["op"]
        args = frame["args"]
        if op == "list_tabs":
            return {
                "type": "bridge_response",
                "request_id": rid,
                "ok": True,
                "result": {"tabs": [t.as_frame(self.browser_instance_id) for t in self.tabs.values()]},
            }
        if op == "search":
            return self._search(rid, args)
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
        if op in PLAYER_OPS and tab.video_id is None:
            return self._fail(rid, "UNSUPPORTED_CONTEXT", "no player on this page")
        result: dict[str, Any]
        if op == "get_state":
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op == "set_paused":
            tab.paused = bool(args["paused"])
            result = {"tab": tab.as_frame(self.browser_instance_id)}
        elif op in ("next", "previous"):
            if tab.ad_showing or tab.is_live or tab.video_id is None:
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
        elif op == "open_result":
            if tab.context != "other" or tab.results is None:
                return self._fail(rid, "UNSUPPORTED_CONTEXT", "not a search results page")
            index = int(args["index"])
            if index > len(tab.results):
                return self._fail(rid, "SEARCH_RESULT_UNAVAILABLE", "no such result on the page")
            chosen = tab.results[index - 1]
            if "video_id" in args and args["video_id"] != chosen["video_id"]:
                return self._fail(rid, "TARGET_CHANGED", "a different video is at that position now")
            tab.context, tab.results = "watch", None
            tab.video_id = self.open_result_lands_on or str(chosen["video_id"])
            tab.title = str(chosen.get("title", "")) or None
            tab.paused, tab.position = False, 0.0
            tab.duration = float(chosen.get("duration_seconds", 200))
            result = {"tab": tab.as_frame(self.browser_instance_id)}
            if self.open_result_reports_tab is not None:
                instance_id, tab_id = self.open_result_reports_tab
                result["tab"].update(browser_instance_id=instance_id, tab_id=tab_id)
        else:
            return self._fail(rid, "UNKNOWN_ACTION", op)
        return {"type": "bridge_response", "request_id": rid, "ok": True, "result": result}

    def _search(self, rid: str, args: dict[str, Any]) -> dict[str, Any]:
        """The ``search`` op: the query only "navigates"; it is never stored on a tab or echoed (unless
        :attr:`leak_results_title` makes this fake misbehave on purpose)."""
        query = str(args.get("query", "")).strip()
        if not query:
            return self._fail(rid, "INVALID_PARAMETERS", "query must not be empty")
        if "tab_id" in args:
            tab = self.tabs.get(int(args["tab_id"]))
            if tab is None:
                return self._fail(rid, "TARGET_GONE", "tab closed")
            if not tab.script_attached:
                return self._fail(rid, "TAB_NOT_CONTROLLABLE", "no content script")
            if args.get("tab_token") != tab.tab_token:
                return self._fail(rid, "TARGET_CHANGED", "tab token mismatch")
        else:
            tab = self.add_tab(FakeTab(tab_id=max([999, *self.tabs]) + 1))
        if self.layout_unrecognised:
            tab.show_results([])
            return self._fail(rid, "ACTION_UNAVAILABLE", "YouTube changed its results page; DoMe needs an update")
        tab.show_results(fake_search_results() if self.search_results is None else self.search_results)
        if self.leak_results_title:
            tab.title = f"{query} - YouTube"
        assert tab.results is not None
        return {
            "type": "bridge_response",
            "request_id": rid,
            "ok": True,
            "result": {"tab": tab.as_frame(self.browser_instance_id), "results": tab.results},
        }

"""``youtube.*`` handlers: action → ``bridge_request.op`` through the agent-side bridge server.

Target resolution (design → authorization step 7) is shared with :mod:`dome_agent.authz` through
:func:`resolve_youtube_target` and repeated right before the request is sent, because the tab may
have navigated or closed while the command sat in the queue. The extension performs the
``observe_player_state`` / ``observe_video_transition`` verification and reports the post-action
tab; the handler double-checks that the reported state matches what was requested so an optimistic
extension cannot turn a no-op into a success. For non-idempotent ops (``next``/``previous``/
``seek_relative``/``search``/``open_result``) a lost answer after the request was written is
``OUTCOME_UNKNOWN`` (the executor reports ``outcome_unknown`` + warning), never a retryable failure (spec §8).

``youtube.search`` (protocol 1.2) targets a ``youtube_browser_target``: a connected browser, optionally
narrowed to one token-checked tab (:func:`resolve_youtube_browser_target`); without a tab the extension
opens a new one. Its query is customer-typed text (rules.search_queries): it travels only inside the
``bridge_request`` and is never logged, journaled or echoed — the results tab comes back without a title.
The results page is a new attachment (new tab_token, maybe a new tab), recorded in the bridge's tab cache
at once so the phone can target it with ``youtube.open_result`` before the extension's debounced
``tabs_changed`` arrives. An extension that negotiated an older protocol never receives either op
(``EXTENSION_UPDATE_REQUIRED``, :data:`dome_agent.bridge.server.OP_SINCE`).

Every tab an answer carries — the ``list_tabs`` merge and each op's result tab — passes through
:func:`~dome_agent.bridge.server.scrub_tab` before it is checked, cached or returned: an older extension
reports a results page's title, which is the query, and results are journaled and sent to the phone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dome_protocol import ProtocolError

from ..bridge.server import scrub_tab
from . import handler
from .context import ActionFailed, ExecutionContext

if TYPE_CHECKING:
    from ..bridge.server import BridgeServer

OPS: dict[str, str] = {
    "youtube.list_tabs": "list_tabs",
    "youtube.get_state": "get_state",
    "youtube.set_paused": "set_paused",
    "youtube.next": "next",
    "youtube.previous": "previous",
    "youtube.seek_relative": "seek_relative",
    "youtube.seek_to": "seek_to",
    "youtube.set_muted": "set_muted",
    "youtube.set_volume": "set_volume",
    "youtube.set_theater": "set_theater",
    "youtube.request_fullscreen": "request_fullscreen",
    "youtube.search": "search",
    "youtube.open_result": "open_result",
}


def resolve_youtube_target(bridge: BridgeServer, target: dict[str, Any]) -> dict[str, Any]:
    """Return the current tab record for ``target`` or raise TARGET_GONE / TAB_NOT_CONTROLLABLE / TARGET_CHANGED."""
    instance = bridge.get_instance(str(target["browser_instance_id"]))
    if instance is None:
        raise ProtocolError("TARGET_GONE", "That browser is no longer connected")
    tab = bridge.find_tab(str(target["browser_instance_id"]), int(target["tab_id"]))
    if tab is None:
        raise ProtocolError("TARGET_GONE", "That tab is no longer there")
    if not tab.get("script_attached") or not tab.get("tab_token"):
        raise ProtocolError("TAB_NOT_CONTROLLABLE", "Reload the YouTube tab on the PC so DoMe can control it")
    if tab["tab_token"] != target["tab_token"]:
        raise ProtocolError("TARGET_CHANGED", "The page changed before the action ran. Nothing was done.")
    expected = target.get("expected_video_id")
    if expected is not None and tab.get("video_id") not in (None, expected):
        raise ProtocolError("TARGET_CHANGED", "A different video is playing in that tab now. Nothing was done.")
    return tab


def resolve_youtube_browser_target(bridge: BridgeServer, target: dict[str, Any]) -> dict[str, Any] | None:
    """``youtube_browser_target``: the browser must still be connected (else TARGET_GONE). A target that
    names a tab (tab_id and tab_token always together) must pass :func:`resolve_youtube_target`'s checks;
    returns that tab, or None when the action opens a new tab in the browser."""
    if bridge.get_instance(str(target["browser_instance_id"])) is None:
        raise ProtocolError("TARGET_GONE", "That browser is no longer connected")
    if "tab_id" not in target:
        return None
    return resolve_youtube_target(bridge, target)


def _args(ctx: ExecutionContext) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if ctx.target is not None and "tab_id" in ctx.target:
        args["tab_id"] = int(ctx.target["tab_id"])
        args["tab_token"] = str(ctx.target["tab_token"])
        if "expected_video_id" in ctx.target:
            args["expected_video_id"] = str(ctx.target["expected_video_id"])
    for key in ("paused", "seconds", "position_seconds", "muted", "value", "enabled", "query", "index", "video_id"):
        if key in ctx.params:
            args[key] = ctx.params[key]
    return args


def _verify_search(target: dict[str, Any], result: dict[str, Any]) -> None:
    """observe_search_results: the extension must report the results page as a NEW attachment in the
    targeted browser (in the targeted tab when one was named) and its results numbered 1..n in page order.
    Anything else is an extension bug, never a success. The results tab's title is dropped (it is the query)."""
    tab = result.get("tab")
    results = result.get("results")
    if not isinstance(tab, dict) or not isinstance(results, list):
        raise ProtocolError("INTERNAL", "The extension returned no search results page")
    if (
        tab.get("browser_instance_id") != target["browser_instance_id"]
        or tab.get("context") != "other"
        or not tab.get("script_attached")
        or not tab.get("tab_token")
    ):
        raise ProtocolError("INTERNAL", "The extension did not report the search results page")
    if "tab_id" in target and (tab.get("tab_id") != target["tab_id"] or tab.get("tab_token") == target["tab_token"]):
        raise ProtocolError("INTERNAL", "The extension did not report a newly loaded results page in that tab")
    if [item.get("index") if isinstance(item, dict) else None for item in results] != list(range(1, len(results) + 1)):
        raise ProtocolError("INTERNAL", "The extension returned search results out of order")
    tab.pop("title", None)


def _verify_same_tab(target: dict[str, Any], result: dict[str, Any]) -> None:
    """``open_result`` plays the chosen video in the targeted tab. An answer naming another tab or browser is an
    extension bug, never a success, and must not reach that tab's record in the bridge cache."""
    tab = result.get("tab")
    if (
        not isinstance(tab, dict)
        or tab.get("browser_instance_id") != target["browser_instance_id"]
        or tab.get("tab_id") != target["tab_id"]
    ):
        raise ProtocolError("INTERNAL", "The extension reported a different tab than the one it was asked to use")


def _verify(action: str, params: dict[str, Any], result: dict[str, Any], before_video_id: str | None) -> None:
    tab = result.get("tab")
    if not isinstance(tab, dict):
        raise ProtocolError("INTERNAL", "The extension returned no tab state")
    if action == "youtube.set_paused" and tab.get("paused") is not None and tab["paused"] != params["paused"]:
        raise ActionFailed("ACTION_UNAVAILABLE", "The player did not reach the requested playback state", result=result)
    if action == "youtube.set_muted" and tab.get("muted") is not None and tab["muted"] != params["muted"]:
        raise ActionFailed("ACTION_UNAVAILABLE", "The player did not apply the mute state", result=result)
    if action == "youtube.set_volume" and tab.get("volume") is not None and tab["volume"] != params["value"]:
        raise ActionFailed("ACTION_UNAVAILABLE", "The player did not apply the requested volume", result=result)
    if action == "youtube.set_theater" and tab.get("theater") is not None and tab["theater"] != params["enabled"]:
        raise ActionFailed("UNSUPPORTED_CONTEXT", "Theater mode is not available on this page", result=result)
    if action == "youtube.request_fullscreen" and not tab.get("fullscreen"):
        raise ActionFailed(
            "ACTIVATION_REQUIRED",
            "Fullscreen must be started on the PC itself; the browser blocks remote fullscreen",
            result=result,
        )
    if action in ("youtube.next", "youtube.previous"):
        previous = result.get("previous_video_id", before_video_id)
        if tab.get("video_id") is None or (previous is not None and tab.get("video_id") == previous):
            code = "NO_NEXT_VIDEO" if action == "youtube.next" else "NO_PREVIOUS_VIDEO"
            raise ActionFailed(code, "No video transition was observed", result=result)
        if previous is not None:
            result.setdefault("previous_video_id", previous)
    if action == "youtube.open_result":
        landed = tab.get("video_id")
        if landed is None or ("video_id" in params and landed != params["video_id"]):
            raise ActionFailed("TARGET_CHANGED", "The tab did not open the chosen video", result=result)


async def _run(ctx: ExecutionContext) -> dict[str, Any]:
    action = ctx.action
    op = OPS[action]
    bridge = ctx.services.bridge
    before_video_id: str | None = None
    if ctx.target is None:
        instances = bridge.instances()
        if not instances:
            raise ProtocolError(
                "EXTENSION_DISCONNECTED", "DoMe's browser extension is not connected on the PC", retryable=True
            )
        # list_tabs: ask every connected browser instance and merge
        tabs: list[dict[str, Any]] = []
        for inst in instances:
            response = await bridge.request(
                inst.browser_instance_id, op, {}, timeout_ms=ctx.command.spec.timeout_ms - 500
            )
            tabs.extend(scrub_tab(t) for t in response.get("tabs", []) if isinstance(t, dict))
        return {"tabs": tabs[:32]}
    if ctx.command.spec.target_name == "youtube_browser_target":
        tab = resolve_youtube_browser_target(bridge, ctx.target)
    else:
        tab = resolve_youtube_target(bridge, ctx.target)
    before_video_id = tab.get("video_id") if tab is not None else None
    if ctx.command.spec.verification == "observe_video_transition" or not ctx.command.spec.idempotent:
        ctx.mark_side_effect()
    instance_id = str(ctx.target["browser_instance_id"])
    result = await bridge.request(
        instance_id,
        op,
        _args(ctx),
        timeout_ms=ctx.command.spec.timeout_ms - 500,
        non_idempotent=not ctx.command.spec.idempotent,
    )
    if isinstance(result.get("tab"), dict):
        result["tab"] = scrub_tab(result["tab"])
    if op == "search":
        _verify_search(ctx.target, result)
        bridge.note_tab(result["tab"], instance_id)
    elif op != "get_state":
        if op == "open_result":
            _verify_same_tab(ctx.target, result)
        _verify(action, ctx.params, result, before_video_id)
        if op == "open_result":
            bridge.note_tab(result["tab"], instance_id)
    ctx.services.state.request_update()
    return result


for _action in OPS:
    handler(_action)(_run)

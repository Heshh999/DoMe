"""youtube.search / youtube.open_result (protocol 1.2) end to end through the agent and the fake extension:
target resolution, the results page as a new attachment the phone can target at once, honest refusals, an
extension too old for the ops, and rules.search_queries — the query reaches the extension and nothing else."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import ProtocolError, load_registry

from dome_agent.actions.youtube import _verify_search
from dome_agent.logsetup import REDACTED, redact_event
from dome_agent.platform.protocol import ForegroundApp
from dome_agent.testing.fake_extension import FakeExtension, FakeTab

from .conftest import AgentHarness
from .helpers import Controller
from .test_handlers import run


def browser_target(ext: FakeExtension, tab: FakeTab | None = None) -> dict[str, Any]:
    target: dict[str, Any] = {"browser_instance_id": ext.browser_instance_id}
    if tab is not None:
        target.update(tab_id=tab.tab_id, tab_token=tab.tab_token)
    return target


def tab_target(ext: FakeExtension, tab: FakeTab) -> dict[str, Any]:
    return {"browser_instance_id": ext.browser_instance_id, "tab_id": tab.tab_id, "tab_token": tab.tab_token}


async def test_search_in_a_new_tab_then_open_a_result_before_the_tab_list_catches_up(
    harness: AgentHarness, controller: Controller
) -> None:
    ext = await harness.connect_extension(FakeTab(tab_id=3))
    ext.publish_on_search = False  # the extension's debounced tabs_changed has not arrived yet
    res = await run(harness, controller, "youtube.search", {"query": "lofi beats"}, browser_target(ext))
    assert res["state"] == "succeeded", res
    tab = res["result"]["tab"]
    assert tab["tab_id"] == 1000 and tab["context"] == "other" and tab["script_attached"] and "title" not in tab
    assert [r["video_id"] for r in res["result"]["results"]] == [f"result0000{i}" for i in range(1, 6)]
    search = ext.requests[0]
    assert search["op"] == "search" and search["args"] == {"query": "lofi beats"}  # no tab: a new one
    # the agent recorded the new results page from the answer itself
    cached = harness.agent.bridge.find_tab(ext.browser_instance_id, 1000)
    assert cached is not None and cached["tab_token"] == tab["tab_token"]

    target = {"browser_instance_id": ext.browser_instance_id, "tab_id": 1000, "tab_token": tab["tab_token"]}
    res = await run(harness, controller, "youtube.open_result", {"index": 2, "video_id": "result00002"}, target)
    assert res["state"] == "succeeded", res
    assert res["result"]["tab"]["video_id"] == "result00002" and res["result"]["tab"]["tab_token"] == tab["tab_token"]
    assert ext.requests[1]["args"] == {
        "tab_id": 1000,
        "tab_token": tab["tab_token"],
        "index": 2,
        "video_id": "result00002",
    }
    assert ext.tabs[3].video_id == "dQw4w9WgXcQ"  # the tab that was already open was not touched
    cached = harness.agent.bridge.find_tab(ext.browser_instance_id, 1000)
    assert cached is not None and cached["video_id"] == "result00002"


async def test_search_in_the_selected_tab_replaces_its_attachment(
    harness: AgentHarness, controller: Controller
) -> None:
    watching = FakeTab(tab_id=4)
    ext = await harness.connect_extension(watching)
    ext.publish_on_search = False
    old = tab_target(ext, watching)
    res = await run(harness, controller, "youtube.search", {"query": "jazz"}, browser_target(ext, watching))
    assert res["state"] == "succeeded", res
    new_token = res["result"]["tab"]["tab_token"]
    assert res["result"]["tab"]["tab_id"] == 4 and new_token != old["tab_token"] and new_token == watching.tab_token
    assert ext.requests[0]["args"] == {"tab_id": 4, "tab_token": old["tab_token"], "query": "jazz"}
    # the page the phone selected before is gone: its token is refused, the new one works
    res = await run(harness, controller, "youtube.get_state", {}, old)
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_CHANGED"
    res = await run(harness, controller, "youtube.get_state", {}, {**old, "tab_token": new_token})
    assert res["state"] == "succeeded" and res["result"]["tab"]["context"] == "other"


async def test_search_targets_are_checked_before_anything_is_sent(
    harness: AgentHarness, controller: Controller
) -> None:
    tab = FakeTab(tab_id=5)
    ext = await harness.connect_extension(tab)
    for target, code in (
        ({**browser_target(ext, tab), "tab_token": "S" * 22}, "TARGET_CHANGED"),  # reloaded since selected
        ({**browser_target(ext, tab), "tab_id": 99}, "TARGET_GONE"),
        ({"browser_instance_id": "bi_unknown01"}, "TARGET_GONE"),
        ({"browser_instance_id": ext.browser_instance_id, "tab_id": 5}, "INVALID_PARAMETERS"),  # token required
    ):
        res = await run(harness, controller, "youtube.search", {"query": "news"}, target)
        assert res["state"] == "failed" and res["error"]["code"] == code, (target, res)
    for params in ({"query": ""}, {"query": " leading space"}, {"query": "x" * 101}, {"query": "a\nb"}, {}):
        res = await run(harness, controller, "youtube.search", params, browser_target(ext))
        assert res["state"] == "failed" and res["error"]["code"] == "INVALID_PARAMETERS", params
    assert ext.requests == []


async def test_open_result_refusals_leave_the_page_alone(harness: AgentHarness, controller: Controller) -> None:
    page = FakeTab.results_page(6)
    watching = FakeTab(tab_id=7)
    ext = await harness.connect_extension(page, watching)
    target = tab_target(ext, page)
    res = await run(harness, controller, "youtube.open_result", {"index": 6}, target)  # 5 results on the page
    assert res["state"] == "failed" and res["error"]["code"] == "SEARCH_RESULT_UNAVAILABLE"
    res = await run(harness, controller, "youtube.open_result", {"index": 2, "video_id": "result00003"}, target)
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_CHANGED"
    res = await run(harness, controller, "youtube.open_result", {"index": 11}, target)
    assert res["state"] == "failed" and res["error"]["code"] == "INVALID_PARAMETERS"
    assert page.context == "other" and page.video_id is None  # nothing was opened
    res = await run(harness, controller, "youtube.open_result", {"index": 1}, tab_target(ext, watching))
    assert res["state"] == "failed" and res["error"]["code"] == "UNSUPPORTED_CONTEXT"
    assert watching.video_id == "dQw4w9WgXcQ"


async def test_open_result_on_another_video_is_never_a_success(harness: AgentHarness, controller: Controller) -> None:
    page = FakeTab.results_page(8)
    ext = await harness.connect_extension(page)
    ext.open_result_lands_on = "notChosen01"  # an optimistic extension reporting a different video
    res = await run(
        harness, controller, "youtube.open_result", {"index": 1, "video_id": "result00001"}, tab_target(ext, page)
    )
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_CHANGED"
    assert res["result"]["tab"]["video_id"] == "notChosen01"  # the best-known state is reported


async def test_open_result_answer_naming_another_tab_is_refused_and_never_cached(
    harness: AgentHarness, controller: Controller
) -> None:
    """One bridge connection's answer must never rewrite another browser's tab record (its tab_token feeds
    target resolution): open_result plays the video in the targeted tab, so any other identity is a bug."""
    page, second_page = FakeTab.results_page(16), FakeTab.results_page(18)
    ext = await harness.connect_extension(page, second_page)
    elsewhere = FakeTab(tab_id=17, title="Another browser's video")
    other = await harness.connect_extension(elsewhere, browser_instance_id="bi_fake0002")
    for _ in range(100):
        if harness.agent.bridge.find_tab(other.browser_instance_id, 17) is not None:
            break
        await asyncio.sleep(0.02)
    before = harness.agent.bridge.find_tab(other.browser_instance_id, 17)
    assert before is not None
    ext.open_result_reports_tab = (other.browser_instance_id, 17)
    res = await run(harness, controller, "youtube.open_result", {"index": 1}, tab_target(ext, page))
    assert res["state"] == "failed" and res["error"]["code"] == "INTERNAL", res
    assert harness.agent.bridge.find_tab(other.browser_instance_id, 17) == before
    ext.open_result_reports_tab = (ext.browser_instance_id, 16)  # the same browser, another of its tabs
    res = await run(harness, controller, "youtube.open_result", {"index": 1}, tab_target(ext, second_page))
    assert res["state"] == "failed" and res["error"]["code"] == "INTERNAL"
    cached = harness.agent.bridge.find_tab(ext.browser_instance_id, 16)
    assert cached is not None and cached["context"] == "other"  # tab 16's record was not overwritten


async def test_unreadable_results_page_is_not_an_empty_success(harness: AgentHarness, controller: Controller) -> None:
    ext = await harness.connect_extension(FakeTab(tab_id=9))
    ext.layout_unrecognised = True
    res = await run(harness, controller, "youtube.search", {"query": "cats"}, browser_target(ext))
    assert res["state"] == "failed" and res["error"]["code"] == "ACTION_UNAVAILABLE"
    ext.layout_unrecognised = False
    ext.search_results = []  # a query without results is a success with no results
    res = await run(harness, controller, "youtube.search", {"query": "cats"}, browser_target(ext))
    assert res["state"] == "succeeded" and res["result"]["results"] == []


async def test_results_out_of_order_are_an_extension_bug(harness: AgentHarness, controller: Controller) -> None:
    ext = await harness.connect_extension(FakeTab(tab_id=10))
    ext.search_results = [{"index": 1, "video_id": "aaa", "title": "A"}, {"index": 3, "video_id": "bbb", "title": "B"}]
    res = await run(harness, controller, "youtube.search", {"query": "dogs"}, browser_target(ext))
    assert res["state"] == "failed" and res["error"]["code"] == "INTERNAL"


TAB = {"browser_instance_id": "bi_fake0001", "tab_id": 4, "script_attached": True, "tab_token": "N" * 22}
RESULTS = [{"index": 1, "video_id": "a", "title": "A"}, {"index": 2, "video_id": "b", "title": "B"}]
OLD = {"browser_instance_id": "bi_fake0001", "tab_id": 4, "tab_token": "O" * 22}


@pytest.mark.parametrize(
    ("target", "result"),
    [
        (OLD, {"tab": {**TAB, "context": "other", "tab_token": "O" * 22}, "results": []}),  # token unchanged
        (OLD, {"tab": {**TAB, "context": "other", "tab_id": 5}, "results": []}),  # another tab
        ({"browser_instance_id": "bi_fake0001"}, {"tab": {**TAB, "context": "watch"}, "results": []}),
        ({"browser_instance_id": "bi_other001"}, {"tab": {**TAB, "context": "other"}, "results": []}),
        (
            {"browser_instance_id": "bi_fake0001"},
            {"tab": {**TAB, "context": "other", "script_attached": False}, "results": []},
        ),
        ({"browser_instance_id": "bi_fake0001"}, {"tab": {**TAB, "context": "other"}, "results": RESULTS[1:]}),
        ({"browser_instance_id": "bi_fake0001"}, {"tab": {**TAB, "context": "other"}}),
    ],
)
def test_verify_search_refuses_what_is_not_a_new_results_page(target: dict[str, Any], result: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError) as ei:
        _verify_search(target, json.loads(json.dumps(result)))
    assert ei.value.code == "INTERNAL"


def test_verify_search_drops_the_results_page_title() -> None:
    result = {"tab": {**TAB, "context": "other", "title": "query - YouTube"}, "results": RESULTS}
    _verify_search(OLD, result)
    assert "title" not in result["tab"] and result["results"] == RESULTS


async def test_lost_search_answer_is_outcome_unknown(harness: AgentHarness, controller: Controller) -> None:
    ext = await harness.connect_extension(FakeTab(tab_id=12), op_delay=2.0)
    spec = load_registry().get("youtube.search")
    object.__setattr__(spec, "timeout_ms", 1500)  # bridge timeout = 1000 ms
    try:
        res = await run(harness, controller, "youtube.search", {"query": "rain"}, browser_target(ext))
    finally:
        object.__setattr__(spec, "timeout_ms", 15000)
    assert res["state"] == "outcome_unknown" and res["error"]["code"] == "OUTCOME_UNKNOWN"
    for _ in range(100):
        if ext.requests:
            break
        await asyncio.sleep(0.05)
    assert [r["op"] for r in ext.requests] == ["search"]  # one search reached the browser, never retried


@pytest.mark.parametrize("versions", [("1.0",), ("1.0", "1.1")])
async def test_an_older_extension_is_asked_to_update(
    harness: AgentHarness, controller: Controller, versions: tuple[str, ...]
) -> None:
    page = FakeTab.results_page(13)
    watching = FakeTab(tab_id=14)
    ext = await harness.connect_extension(page, watching, protocol_versions=versions)
    assert harness.agent.bridge.instances()[0].protocol_version == versions[-1]
    res = await run(harness, controller, "youtube.search", {"query": "music"}, browser_target(ext))
    assert res["state"] == "failed" and res["error"]["code"] == "EXTENSION_UPDATE_REQUIRED", res
    assert res["error"]["retryable"] is False and res["error"]["detail"]["required"] == "1.2"
    res = await run(harness, controller, "youtube.open_result", {"index": 1}, tab_target(ext, page))
    assert res["state"] == "failed" and res["error"]["code"] == "EXTENSION_UPDATE_REQUIRED"
    assert ext.requests == []  # nothing was written to the old extension
    res = await run(harness, controller, "youtube.set_paused", {"paused": True}, tab_target(ext, watching))
    assert res["state"] == "succeeded"  # everything it does speak keeps working
    assert [r["op"] for r in ext.requests] == ["set_paused"]


def test_query_and_search_keys_are_redacted_from_logs() -> None:
    out = redact_event(None, "info", {"event": "x", "query": "q", "search_query": "q", "args": {"query": "q"}})
    assert out["query"] == REDACTED and out["search_query"] == REDACTED and out["args"] == {"query": REDACTED}


async def test_search_query_never_appears_in_logs_or_persisted_state(
    harness: AgentHarness, controller: Controller, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """rules.search_queries, mirroring the typed-text test in test_input_session.py: the query goes to the
    extension inside the bridge_request and nowhere else — even when the extension echoes it back as the
    results page title, as builds before 1.2 did, and although the browser's own window title becomes
    "<query> - YouTube - Microsoft Edge" (app.list of an approved Edge, the foreground app in pc_state)."""
    from dome_agent.diagnostics import build_bundle
    from dome_agent.logsetup import configure_logging

    sentinel = "ZEBRA SEARCH 7731"
    configure_logging("DEBUG", harness.settings.log_path)
    try:
        watching = FakeTab(tab_id=15)
        ext = await harness.connect_extension(watching)
        ext.leak_results_title = True
        res = await run(harness, controller, "youtube.search", {"query": sentinel}, browser_target(ext))
        assert res["state"] == "succeeded" and "title" not in res["result"]["tab"]
        res = await run(
            harness, controller, "youtube.search", {"query": sentinel + " in tab"}, browser_target(ext, watching)
        )
        assert res["state"] == "succeeded"
        # every other answer that carries the results tab: the tab list and its state
        res = await run(harness, controller, "youtube.list_tabs", {})
        assert res["state"] == "succeeded" and sentinel not in json.dumps(res)
        assert {t["tab_id"] for t in res["result"]["tabs"]} >= {15, 1000}
        res = await run(harness, controller, "youtube.get_state", {}, tab_target(ext, watching))
        assert res["state"] == "succeeded" and res["result"]["tab"]["context"] == "other"
        assert sentinel not in json.dumps(res)
        # the browser window's title is now the query: an approved Edge in app.list, Edge in front in pc_state
        edge = tmp_path / "apps" / "msedge.exe"
        edge.parent.mkdir()
        edge.write_bytes(b"MZ edge")
        harness.agent.apps._temp_dirs = ()  # noqa: SLF001
        harness.agent.apps.approve("edge", str(edge), "Microsoft Edge")
        harness.fake.processes[str(edge.resolve())] = [900]
        harness.fake.add_window(900, "9901", f"{sentinel} - YouTube and 1 more page - Microsoft Edge")
        harness.fake.foreground_app = ForegroundApp(
            "msedge.exe", f"{sentinel} - YouTube - Microsoft Edge", "edge", False, "9901", 900
        )
        res = await run(harness, controller, "app.list", {})
        assert res["state"] == "succeeded" and sentinel not in json.dumps(res)
        assert res["result"]["apps"][0]["windows"][0]["title"] == "Microsoft Edge"
        ext.layout_unrecognised = True  # a failed search after the navigation
        res = await run(harness, controller, "youtube.search", {"query": sentinel + " failing"}, browser_target(ext))
        assert res["state"] == "failed" and res["error"]["code"] == "ACTION_UNAVAILABLE"
        stale = {**browser_target(ext), "tab_id": 15, "tab_token": "S" * 22}  # rejected at target resolution
        res = await run(harness, controller, "youtube.search", {"query": sentinel + " stale"}, stale)
        assert res["error"]["code"] == "TARGET_CHANGED"
        res = await run(harness, controller, "youtube.search", {"query": sentinel + "\n"}, browser_target(ext))
        assert res["error"]["code"] == "INVALID_PARAMETERS"  # the validation message does not echo it
        ext.publish_tabs()
        await asyncio.sleep(0.6)  # a state frame after the tabs_changed (debounced 500 ms)
        harness.agent.state.request_update()
        await asyncio.sleep(0.6)
        for h in logging.getLogger().handlers:
            h.flush()
        log_text = harness.settings.log_path.read_text(encoding="utf-8")
        assert "browser instance connected" in log_text and sentinel not in log_text
        captured = capsys.readouterr()
        assert sentinel not in captured.out and sentinel not in captured.err
        assert sentinel not in json.dumps(harness.agent.store.list_security_events(500))
        assert sentinel not in json.dumps(harness.agent.status())
        assert sentinel not in json.dumps(build_bundle(harness.settings, harness.agent.status()))
        frames = harness.relay.drain()
        states = [f["state"] for f in frames if f["type"] == "state"]
        assert states and sentinel not in json.dumps(frames)
        assert states[-1]["foreground_app"] == {"process_name": "msedge.exe", "browser": "edge", "elevated": False}
        assert sentinel not in json.dumps(harness.agent.bridge.all_tabs())
        assert sentinel not in json.dumps(
            [p.read_text(encoding="utf-8") for p in harness.settings.state_dir.glob("*.json")]
        )
        db_files = list(harness.settings.db_path.parent.glob(harness.settings.db_path.name + "*"))
        assert db_files and all(sentinel not in p.read_bytes().decode("latin-1") for p in db_files)
    finally:
        for h in logging.getLogger().handlers:
            h.close()  # on Windows an open log file stays locked
        logging.getLogger().handlers.clear()
    # the only place the query ever went is the extension, inside the bridge requests
    sent = [r["args"]["query"] for r in ext.requests if r["op"] == "search"]
    assert sent == [sentinel, sentinel + " in tab", sentinel + " failing"]

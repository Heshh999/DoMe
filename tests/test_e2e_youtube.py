"""Spec §17.1–2: the first milestone — a paired phone controls YouTube in a background tab through the
real relay and the real agent process, with a transition-observed Next. Protocol 1.2: YouTube search and
opening a result, and an extension too old for them."""

from __future__ import annotations

from typing import Any

from dome_agent.testing.fake_extension import FakeTab

from conftest import REGISTRY, Env, RealAgent, run_command, sql, wait_state, youtube_target


async def test_next_changes_the_intended_video(agent: RealAgent, phone: Any) -> None:
    tab = FakeTab(tab_id=7, video_id="dQw4w9WgXcQ", next_videos=["9bZkp7q19f0"])
    ext = await agent.connect_extension(tab)

    # the PC's state reaches the phone through the relay: extension connected, development platform
    state = await wait_state(phone, agent.pc_id, lambda s: s["extension_connected"] is True)
    assert state["state"]["platform"] == "development"

    cid = await phone.command(agent.pc_id, "youtube.next", {}, youtube_target(ext, tab))
    ack = await phone.recv_type("ack", command_id=cid)
    assert ack["state"] in ("accepted", "executing")  # delivery/authorisation, never a result
    res = await phone.recv_type("result", command_id=cid, timeout=10)
    assert res["origin"] == "agent" and res["state"] == "succeeded", res
    REGISTRY.validate_result("youtube.next", res["result"])
    assert res["result"]["previous_video_id"] == "dQw4w9WgXcQ"
    assert res["result"]["tab"]["video_id"] == "9bZkp7q19f0"
    assert res["result"]["tab"]["tab_token"] == tab.tab_token
    # exactly one op reached the browser, and it was `next` with the token the phone targeted
    assert [r["op"] for r in ext.requests] == ["next"]
    assert ext.requests[0]["args"]["tab_token"] == tab.tab_token

    # the relay kept lifecycle only — no params, no result, no title
    rows = await phone.browser.get(f"/v1/commands?pc_id={agent.pc_id}&limit=5", schema="commands_response")
    row = next(r for r in rows["commands"] if r["command_id"] == cid)
    assert row["state"] == "succeeded" and row["action"] == "youtube.next" and row["error_code"] is None
    assert set(row) <= {"command_id", "pc_id", "controller_id", "action", "state", "error_code", "created_at", "finished_at", "duration_ms"}


async def test_end_of_queue_ad_and_fullscreen_are_reported_honestly(agent: RealAgent, phone: Any) -> None:
    last = FakeTab(tab_id=1, video_id="aaaaaaaaaaa", next_videos=[])
    ad = FakeTab(tab_id=2, video_id="bbbbbbbbbbb", ad_showing=True)
    ext = await agent.connect_extension(last, ad)

    res = await run_command(phone, agent.pc_id, "youtube.next", {}, youtube_target(ext, last))
    assert res["state"] == "failed" and res["error"]["code"] == "NO_NEXT_VIDEO"
    assert last.video_id == "aaaaaaaaaaa"  # nothing happened

    res = await run_command(phone, agent.pc_id, "youtube.next", {}, youtube_target(ext, ad))
    assert res["state"] == "failed" and res["error"]["code"] == "UNSUPPORTED_CONTEXT"

    res = await run_command(phone, agent.pc_id, "youtube.set_paused", {"paused": True}, youtube_target(ext, ad))
    assert res["state"] == "succeeded" and res["result"]["tab"]["paused"] is True  # pause works during an ad

    res = await run_command(phone, agent.pc_id, "youtube.request_fullscreen", {}, youtube_target(ext, last))
    assert res["state"] == "failed" and res["error"]["code"] == "ACTIVATION_REQUIRED"
    assert res["error"]["retryable"] is False


async def test_pause_seek_player_volume_and_two_tabs(agent: RealAgent, phone: Any) -> None:
    kitchen = FakeTab(tab_id=11, video_id="kitchen00001", position=30.0, duration=600.0)
    office = FakeTab(tab_id=12, video_id="office000002", position=10.0, duration=300.0)
    ext = await agent.connect_extension(kitchen, office)

    # youtube.list_tabs shows both; the phone must pick one (explicit target) — no guessing
    res = await run_command(phone, agent.pc_id, "youtube.list_tabs")
    assert res["state"] == "succeeded"
    REGISTRY.validate_result("youtube.list_tabs", res["result"])
    assert {t["tab_id"] for t in res["result"]["tabs"]} == {11, 12}
    assert all(t["script_attached"] for t in res["result"]["tabs"])

    # a tab action without a target is rejected before it reaches the PC
    res = await run_command(phone, agent.pc_id, "youtube.set_paused", {"paused": True})
    assert res["origin"] == "relay" and res["state"] == "failed" and res["error"]["code"] == "TARGET_REQUIRED"

    res = await run_command(phone, agent.pc_id, "youtube.set_paused", {"paused": True}, youtube_target(ext, kitchen))
    assert res["state"] == "succeeded" and res["result"]["tab"]["paused"] is True
    assert kitchen.paused is True and office.paused is False  # the other tab was not touched

    res = await run_command(phone, agent.pc_id, "youtube.seek_relative", {"seconds": -10}, youtube_target(ext, office))
    assert res["state"] == "succeeded" and res["result"]["tab"]["position_seconds"] == 0.0

    res = await run_command(phone, agent.pc_id, "youtube.seek_to", {"position_seconds": 120}, youtube_target(ext, kitchen))
    assert res["state"] == "succeeded" and res["result"]["tab"]["position_seconds"] == 120.0

    # player volume and Windows volume are different controls with different results
    res = await run_command(phone, agent.pc_id, "youtube.set_volume", {"value": 40}, youtube_target(ext, kitchen))
    assert res["state"] == "succeeded" and res["result"]["tab"]["volume"] == 40
    res = await run_command(phone, agent.pc_id, "windows.set_volume", {"value": 35})
    assert res["state"] == "succeeded"
    REGISTRY.validate_result("windows.set_volume", res["result"])
    assert res["result"] == {"value": 35, "muted": False}
    res = await run_command(phone, agent.pc_id, "windows.get_volume")
    assert res["result"]["value"] == 35
    assert kitchen.volume == 40  # the player volume did not change when Windows volume did

    res = await run_command(phone, agent.pc_id, "youtube.set_muted", {"muted": True}, youtube_target(ext, office))
    assert res["state"] == "succeeded" and office.muted is True

    # theater is a separately labelled control
    res = await run_command(phone, agent.pc_id, "youtube.set_theater", {"enabled": True}, youtube_target(ext, kitchen))
    assert res["state"] == "succeeded" and res["result"]["tab"]["theater"] is True


async def test_target_identity_is_preserved(agent: RealAgent, phone: Any) -> None:
    tab = FakeTab(tab_id=3, video_id="firstvideo01", next_videos=["secondvideo2"])
    ext = await agent.connect_extension(tab)

    # expected video changed underneath us → nothing is done
    stale = youtube_target(ext, tab)
    stale["expected_video_id"] = "somethingelse"
    res = await run_command(phone, agent.pc_id, "youtube.next", {}, stale)
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_CHANGED"
    assert tab.video_id == "firstvideo01"

    # a stale attachment token (tab was reloaded) is never silently re-bound
    wrong = youtube_target(ext, tab)
    wrong["tab_token"] = "ZZZZZZZZZZZZZZZZZZZZZZ"
    res = await run_command(phone, agent.pc_id, "youtube.seek_relative", {"seconds": 10}, wrong)
    assert res["state"] == "failed" and res["error"]["code"] in ("TARGET_CHANGED", "TARGET_GONE")

    # a tab that is gone
    gone = {"browser_instance_id": ext.browser_instance_id, "tab_id": 999, "tab_token": tab.tab_token}
    res = await run_command(phone, agent.pc_id, "youtube.set_paused", {"paused": True}, gone)
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_GONE"

    # a tab without a content script cannot be controlled
    detached = FakeTab(tab_id=4, video_id="detached0001", script_attached=False)
    ext.add_tab(detached)
    ext.publish_tabs()
    res = await run_command(
        phone,
        agent.pc_id,
        "youtube.set_paused",
        {"paused": True},
        {"browser_instance_id": ext.browser_instance_id, "tab_id": 4, "tab_token": detached.tab_token},
    )
    assert res["state"] == "failed" and res["error"]["code"] in ("TAB_NOT_CONTROLLABLE", "TARGET_GONE")

    # extension disconnected → honest code, nothing queued for later
    ext.close()
    await agent.wait_status(lambda s: not s["extension_connected"], 10)
    res = await run_command(phone, agent.pc_id, "youtube.next", {}, youtube_target(ext, tab))
    assert res["state"] == "failed" and res["error"]["code"] in ("EXTENSION_DISCONNECTED", "TARGET_GONE")


QUERY = "lofi hip hop radio 9417"  # distinctive, so it can be searched for wherever it must not be


async def test_search_then_open_the_second_result(env: Env, agent: RealAgent, phone: Any) -> None:
    watching = FakeTab(tab_id=21, video_id="watching0001")
    ext = await agent.connect_extension(watching)
    ext.leak_results_title = True  # even an extension that reports "<query> - YouTube" as the title
    ext.publish_on_search = False  # the phone acts on the results before the tab list is re-announced

    # no tab selected: the search opens a new tab in that browser
    res = await run_command(phone, agent.pc_id, "youtube.search", {"query": QUERY}, {"browser_instance_id": ext.browser_instance_id})
    assert res["origin"] == "agent" and res["state"] == "succeeded", res
    REGISTRY.validate_result("youtube.search", res["result"])
    tab = res["result"]["tab"]
    assert tab["tab_id"] != 21 and tab["context"] == "other" and "title" not in tab
    results = res["result"]["results"]
    assert [r["index"] for r in results] == [1, 2, 3, 4, 5] and QUERY not in repr(results)
    assert [(r["op"], r["args"]) for r in ext.requests] == [("search", {"query": QUERY})]

    # "play the second video" on the tab the search returned (its new token), right away
    target = {"browser_instance_id": ext.browser_instance_id, "tab_id": tab["tab_id"], "tab_token": tab["tab_token"]}
    second = results[1]
    res = await run_command(phone, agent.pc_id, "youtube.open_result", {"index": 2, "video_id": second["video_id"]}, target)
    assert res["origin"] == "agent" and res["state"] == "succeeded", res
    REGISTRY.validate_result("youtube.open_result", res["result"])
    assert res["result"]["tab"]["video_id"] == second["video_id"] and res["result"]["tab"]["context"] == "watch"
    assert watching.video_id == "watching0001"  # the tab that was playing was left alone

    # searching in the selected tab replaces its page: the old token no longer targets anything
    in_tab = youtube_target(ext, watching, expected=False)
    res = await run_command(phone, agent.pc_id, "youtube.search", {"query": QUERY + " live"}, in_tab)
    assert res["state"] == "succeeded" and res["result"]["tab"]["tab_id"] == 21
    assert res["result"]["tab"]["tab_token"] != in_tab["tab_token"]
    res = await run_command(phone, agent.pc_id, "youtube.set_paused", {"paused": True}, in_tab)
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_CHANGED"
    # the extension still reports "<query> - YouTube" as that page's title: no answer carries it
    res = await run_command(phone, agent.pc_id, "youtube.list_tabs")
    assert res["state"] == "succeeded" and QUERY not in repr(res), res
    assert any(t["tab_id"] == 21 and t["context"] == "other" for t in res["result"]["tabs"])
    res = await run_command(phone, agent.pc_id, "youtube.get_state", {}, {**in_tab, "tab_token": watching.tab_token})
    assert res["state"] == "succeeded" and res["result"]["tab"]["context"] == "other" and QUERY not in repr(res), res
    res = await run_command(phone, agent.pc_id, "youtube.open_result", {"index": 9}, {**in_tab, "tab_token": watching.tab_token})
    assert res["state"] == "failed" and res["error"]["code"] == "SEARCH_RESULT_UNAVAILABLE"

    # rules.search_queries: the query reached the extension and nothing else — not the phone's state
    # frames, not the relay's database, not the agent's files or log
    ext.publish_tabs()
    state = await wait_state(phone, agent.pc_id, lambda s: any(t["tab_id"] == 21 and t["context"] == "other" for t in s["youtube_tabs"]))
    assert QUERY not in repr(state)
    rows = await phone.browser.get(f"/v1/commands?pc_id={agent.pc_id}&limit=10", schema="commands_response")
    assert {r["action"] for r in rows["commands"]} >= {"youtube.search", "youtube.open_result"}
    tables = sql(env.database_url, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    for (table,) in tables:
        dump = sql(env.database_url, f'SELECT t::text FROM "{table}" t')
        assert QUERY not in repr(dump), f"search query found in relay table {table}"
    for path in [agent.log_path, *agent.state_dir.rglob("*")]:
        if path.is_file():
            assert QUERY.encode() not in path.read_bytes(), f"search query found in {path}"


async def test_an_extension_older_than_search_is_asked_to_update(agent: RealAgent, phone: Any) -> None:
    for versions in (("1.0",), ("1.0", "1.1")):
        tab = FakeTab(tab_id=31, video_id="oldextension")
        ext = await agent.connect_extension(tab, protocol_versions=versions)
        assert ext.hello_ack is not None and ext.hello_ack["protocol_version"] == versions[-1]
        browser = {"browser_instance_id": ext.browser_instance_id}
        res = await run_command(phone, agent.pc_id, "youtube.search", {"query": QUERY}, browser)
        assert res["origin"] == "agent" and res["state"] == "failed", res
        assert res["error"]["code"] == "EXTENSION_UPDATE_REQUIRED" and res["error"]["retryable"] is False
        res = await run_command(phone, agent.pc_id, "youtube.open_result", {"index": 1}, youtube_target(ext, tab, expected=False))
        assert res["state"] == "failed" and res["error"]["code"] == "EXTENSION_UPDATE_REQUIRED"
        assert ext.requests == []  # nothing — and no query — was written to the old extension
        # everything the old extension does speak keeps working
        res = await run_command(phone, agent.pc_id, "youtube.set_paused", {"paused": True}, youtube_target(ext, tab))
        assert res["state"] == "succeeded" and tab.paused is True
        ext.close()
        await agent.wait_status(lambda s: not s["extension_connected"], 10)

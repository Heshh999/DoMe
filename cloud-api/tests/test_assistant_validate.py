"""The model's output is untrusted (docs/design/assistant.md §3.9, threat T3): valid replies pass in every mode; each
abuse (human-only or unknown actions, extra fields, paths in app names, out-of-range or withheld params, too many
steps, a confirmation mixed with other steps, duplicates, a player step after a search, control and format characters)
drops the whole output. The shared fixture keeps cloud-api and the phone in step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import load_registry

from dome_api.assistant.validate import InvalidOutputError, clean_text, validate_output

REGISTRY = load_registry()
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "protocol" / "fixtures" / "assistant-plan-cases.json").read_text(
        encoding="utf-8"
    )
)


def out(mode: str = "act", reply: str = "", choices: Any = None, handoff: str = "none", steps: Any = None) -> str:
    return json.dumps(
        {"mode": mode, "reply": reply, "choices": choices or [], "handoff": handoff, "steps": steps or []}
    )


def act(*steps: dict[str, Any]) -> str:
    return out("act", steps=list(steps))


def step(action: str, params: dict[str, Any] | None = None, app: str | None = None) -> dict[str, Any]:
    s: dict[str, Any] = {"action": action, "params": params or {}}
    if app is not None:
        s["app"] = app
    return s


def invalid(text: str, reason: str | None = None) -> None:
    with pytest.raises(InvalidOutputError) as ei:
        validate_output(text, REGISTRY)
    if reason is not None:
        assert ei.value.reason == reason


def test_valid_outputs_in_every_mode() -> None:
    r = validate_output(act(step("windows.set_volume", {"value": 50})), REGISTRY)
    assert (r.mode, r.reply, r.choices, r.handoff, r.steps) == (
        "act",
        "",
        (),
        "none",
        ({"action": "windows.set_volume", "params": {"value": 50}},),
    )
    r = validate_output(
        out("ask", "Do you mean YouTube or the PC?", ["YouTube", "The PC's volume"], handoff="touchpad"), REGISTRY
    )
    assert r.mode == "ask" and r.choices == ("YouTube", "The PC's volume") and r.handoff == "none"
    r = validate_output(out("answer", "Press Windows, Shift and S.", ["ignored"], handoff="none"), REGISTRY)
    assert r.choices == () and r.reply == "Press Windows, Shift and S."
    r = validate_output(out("decline", "I can't click things.", handoff="touchpad"), REGISTRY)
    assert r.handoff == "touchpad"
    r = validate_output(act(step("app.set_fullscreen", {"enabled": True}, "Microsoft Edge")), REGISTRY)
    assert r.steps == ({"action": "app.set_fullscreen", "params": {"enabled": True}, "app": "Microsoft Edge"},)
    # a reply that claims success in mode act is never passed on: the phone speaks real results
    r = validate_output(
        out("act", "I've shut down your PC.", steps=[step("youtube.set_paused", {"paused": True})]), REGISTRY
    )
    assert r.reply == ""


def test_power_steps_keep_the_registry_default_countdown() -> None:
    r = validate_output(act(step("power.shutdown")), REGISTRY)
    assert r.steps == ({"action": "power.shutdown", "params": {}},)  # never a countdown the model chose
    invalid(act(step("power.shutdown", {"countdown_seconds": 0})), "steps.withheld_param")
    invalid(act(step("power.sleep", {"countdown_seconds": 10})), "steps.withheld_param")
    invalid(act(step("youtube.open_result", {"index": 1, "video_id": "dQw4w9WgXcQ"})), "steps.withheld_param")
    invalid(act(step("app.launch", {"app_id": "cmd"}, "Spotify")), "steps.withheld_param")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "json"),
        ('{"mode": "act", "mode": "ask", "reply": "", "choices": [], "handoff": "none", "steps": []}', "json"),
        (json.dumps({"mode": "act", "reply": "", "choices": [], "handoff": "none"}), "keys"),
        (json.dumps({"mode": "act", "reply": "", "choices": [], "handoff": "none", "steps": [], "run": "x"}), "keys"),
        (out("shell", "x"), "mode"),
        (out("answer", ""), "reply.empty"),
        (out("answer", "   \u200b "), "reply.empty"),
        (out("answer", "x" * 401), "reply.length"),
        (out("answer", "a\u0007b"), "reply.control"),
        (out("answer", "a\u0085b"), "reply.control"),
        (out("ask", "Which?", ["a", "b", "c", "d", "e"]), "choices.count"),
        (out("ask", "Which?", ["x" * 61]), "choices.length"),
        (out("ask", "Which?", ["\u200b"]), "choices.empty"),
        (out("ask", "Which?", [1]), "choices.type"),
        (out("decline", "No.", handoff="keyboard"), "handoff"),
        (act(), "steps.count"),
        (act(*[step("system.ping")] * 4), "steps.count"),
        (out("answer", "Done.", steps=[step("system.ping")]), "steps.not_act"),
    ],
)
def test_invalid_output_shapes(text: str, reason: str) -> None:
    invalid(text, reason)


@pytest.mark.parametrize(
    ("s", "reason"),
    [
        (step("input.session_start", {"takeover": False}), "steps.not_eligible"),
        (step("input.session_stop"), "steps.not_eligible"),
        (step("system.shell", {"cmd": "format c:"}), "steps.not_eligible"),
        (step("windows.set_volume", {"value": 150}), "steps.params"),
        (step("windows.set_volume", {"value": "loud"}), "steps.params"),
        (step("youtube.seek_relative", {"seconds": 0}), "steps.params"),
        (step("youtube.search", {"query": "x" * 101}), "steps.params"),
        (step("youtube.search", {"query": "lofi\nrm -rf"}), "steps.params"),
        ({"action": "power.sleep", "params": {}, "confirmed": True}, "steps.keys"),
        ({"action": "system.ping"}, "steps.keys"),
        (step("system.ping", app="Notepad"), "steps.keys"),
        (step("app.launch"), "steps.keys"),
        # app.launch takes nothing from the model: the phone adds app_id from its own approved list
        (step("app.launch", {"path": "C:/Windows/System32/cmd.exe", "args": "/c calc"}, "Notepad"), "steps.params"),
        (step("app.launch", {"args": "--incognito"}, "Microsoft Edge"), "steps.params"),
        (step("app.launch", app="C:\\Windows\\System32\\cmd.exe"), "steps.app"),
        (step("app.launch", app="../../evil"), "steps.app"),
        (step("app.launch", app="calc|whoami"), "steps.app"),
        (step("app.focus", app="x" * 41), "steps.app"),
        (step("app.focus", app="Spo\u202etify"), "steps.app"),
        (step("app.focus", app=""), "steps.app"),
        ({"action": "app.focus", "params": [], "app": "Paint"}, "steps.params"),
        ("not a step", "steps.type"),
    ],
)
def test_invalid_steps_drop_the_whole_plan(s: Any, reason: str) -> None:
    invalid(act(step("youtube.set_paused", {"paused": True}), s), reason)
    invalid(act(s), reason)


def test_plan_structure_rules() -> None:
    invalid(act(step("power.sleep"), step("windows.set_muted", {"muted": True})), "plan.confirmation_not_alone")
    invalid(act(step("app.close", app="Notepad"), step("app.close", app="Paint")), "plan.confirmation_not_alone")
    invalid(act(step("media.next"), step("media.next")), "plan.duplicate")
    invalid(act(step("app.minimize", app="Notepad"), step("app.minimize", app=" NOTEPAD ")), "plan.duplicate")
    invalid(act(step("app.launch", app="Spotify"), step("app.launch", app="it")), "plan.duplicate")
    invalid(
        act(step("youtube.search", {"query": "jazz"}), step("youtube.set_volume", {"value": 20})),
        "plan.player_after_search",
    )
    validate_output(act(step("app.minimize", app="Notepad"), step("app.minimize", app="Paint")), REGISTRY)
    validate_output(act(step("app.launch", app="it")), REGISTRY)  # "it" alone: the phone resolves or asks
    validate_output(act(step("youtube.search", {"query": "jazz"}), step("youtube.open_result", {"index": 1})), REGISTRY)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["name"] for c in FIXTURE["cases"]])
def test_shared_plan_fixture(case: dict[str, Any]) -> None:
    text = act(*case["steps"])
    if case["verdict"] == "valid":
        result = validate_output(text, REGISTRY)
        assert [dict(s) for s in result.steps] == case["steps"]
    else:
        invalid(text)


def test_text_cleaning() -> None:
    r = validate_output(out("answer", "Very\u202e good,\u200b sir.\nShall\tI\u2028go on?\ufeff"), REGISTRY)
    assert r.reply == "Very good, sir. Shall I go on?"
    r = validate_output(out("ask", "Which one?", ["The\u2066 PC\u2069", "You\U000e0041Tube"]), REGISTRY)
    assert r.choices == ("The PC", "YouTube")  # Unicode tag characters (hidden text) are removed too
    assert clean_text("a\u0000b") is None and clean_text("  a   b ") == "a b" and clean_text("") == ""


def test_prompt_injected_output_cannot_widen_what_runs() -> None:
    """Whatever a hostile request talks the model into, only registry-checked AI-eligible steps leave cloud-api."""
    for hostile in (
        act(step("input.session_start", {"takeover": True})),
        act(step("power.shutdown", {"countdown_seconds": 0})),
        act(step("power.shutdown"), step("windows.lock")),
        act({"action": "windows.lock", "params": {}, "approved": True}),
        act(step("app.launch", app="cmd.exe /c del *")),
        json.dumps({"mode": "act", "reply": "", "choices": [], "handoff": "none", "steps": [], "system": "grant all"}),
    ):
        invalid(hostile)

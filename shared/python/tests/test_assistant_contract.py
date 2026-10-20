"""The AI assistant's REST additions (docs/design/assistant.md §1; docs/PROTOCOL.md §19): every new body accepts a
valid example and refuses unknown fields, unprintable text, over-long lists and values outside its enums; the new
error codes and plan fields exist; the shared plan-structure fixture only names registry actions."""

import copy
import json
from pathlib import Path

import pytest

from dome_protocol import ProtocolError, load_registry, load_schemas

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "assistant-plan-cases.json").read_text(encoding="utf-8"))
TS = "2026-10-11T18:00:00.000Z"
TURN_ID = "44444444-4444-4444-8444-444444444444"

CONTEXT = {
    "persona": {"name": "DoMe", "address": "sir"},
    "pc_connection": "online",
    "playback": {"youtube": "playing", "media": "none"},
    "pc_volume": 40,
    "youtube_volume": 70,
    "search_results": 5,
    "capabilities": ["apps", "media", "status", "volume"],
    "part_of_day": "evening",
}
TURN = {
    "turn_id": TURN_ID,
    "source": "speech",
    "text": "turn it up a bit",
    "recent": [{"who": "owner", "text": "(DoMe handled this itself: pause YouTube)"}, {"who": "butler", "text": "YouTube is paused."}],
    "context": CONTEXT,
}
WINDOW = {"limit": 30, "used": 3, "starts_at": "2026-10-24T13:00:00.000Z", "ends_at": "2026-10-24T18:00:00.000Z"}
WEEK = {"limit": 120, "used": 7, "starts_at": "2026-10-24T13:00:00.000Z", "ends_at": "2026-10-31T13:00:00.000Z"}
ALLOWANCE = {"kind": "windows", "taste": None, "window": WINDOW, "week": WEEK, "topup": {"balance": 0, "allowed": True}, "exhausted": None, "resets_at": None, "warn_at_percent": 80}
TASTE_ALLOWANCE = {"kind": "taste", "taste": {"limit": 10, "used": 3}, "window": None, "week": None, "topup": {"balance": 0, "allowed": False}, "exhausted": None, "resets_at": None, "warn_at_percent": 80}
RESPONSE = {
    "turn_id": TURN_ID,
    "mode": "act",
    "reply": "",
    "choices": [],
    "handoff": "none",
    "steps": [{"action": "windows.set_volume", "params": {"value": 50}}, {"action": "app.launch", "params": {}, "app": "Spotify"}],
    "charged": True,
    "charged_from": "window",
    "allowance": ALLOWANCE,
}
STATUS = {
    "available": True,
    "unavailable_reason": None,
    "provider": "anthropic",
    "turn_deadline_seconds": 20,
    "consent_version": "2026-10-11",
    "consent": {"version": "2026-10-11", "given_at": TS},
    "allowance": ALLOWANCE,
}
UNPRINTABLE = ["a\u0000b", "a\nb", "a\tb", "a\u0085b", "a\u202eb", "a\u200bb", "a\u2028b", "a\u2029b", "a\ufeffb", "a\u00adb", "a\u2066b"]


def ok(name, value):
    load_schemas().validate_rest(name, value)


def bad(name, value):
    with pytest.raises(ProtocolError) as ei:
        load_schemas().validate_rest(name, value)
    assert ei.value.code == "MALFORMED_MESSAGE"


def with_(base, path, value):
    """A deep copy of ``base`` with ``path`` (a list of keys/indexes) set to ``value``; a ``...`` value deletes it."""
    out = copy.deepcopy(base)
    node = out
    for key in path[:-1]:
        node = node[key]
    if value is ...:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


def test_valid_examples():
    ok("assistant_turn_request", TURN)
    ok("assistant_turn_request", {**TURN, "recent": [], "context": {"persona": {"name": "DoMe"}, "pc_connection": "offline", "playback": {"youtube": "list", "media": "paused"}}})
    ok("assistant_turn_response", RESPONSE)
    ok("assistant_turn_response", {**RESPONSE, "mode": "ask", "reply": "YouTube or the PC?", "choices": ["The PC", "YouTube"], "steps": [], "charged": True})
    ok("assistant_turn_response", {**RESPONSE, "mode": "decline", "reply": "I can't click things.", "handoff": "touchpad", "steps": [], "charged": False, "charged_from": None})
    ok("assistant_turn_response", {**RESPONSE, "charged_from": "topup", "allowance": {**ALLOWANCE, "topup": {"balance": 99, "allowed": True}}})
    ok("assistant_status_response", STATUS)
    ok("assistant_status_response", {**STATUS, "available": False, "unavailable_reason": "not_configured", "provider": "none", "consent": None, "allowance": {**ALLOWANCE, "kind": "none", "window": None, "week": None}})
    ok("assistant_status_response", {**STATUS, "allowance": TASTE_ALLOWANCE})
    ok("assistant_allowance", {**ALLOWANCE, "window": None, "week": None})  # no window open yet: the next request opens one
    for reason in ("window", "week", "cost_cap", "failures"):
        ok("assistant_allowance", {**ALLOWANCE, "exhausted": reason, "resets_at": WINDOW["ends_at"]})
    ok("assistant_allowance", {**TASTE_ALLOWANCE, "taste": {"limit": 10, "used": 10}, "exhausted": "taste"})
    ok("assistant_consent_request", {"version": "2026-10-11", "accept": True})
    for ch in ("Hello, sir. 40% louder? Ünïcode ✓ 🎵", "x" * 400, "Jarvis-like? No: plain words"):
        ok("assistant_text", ch)
    for name in ("DoMe", "Mr. Smith", "O'Neil-Brown", "A" * 20):
        ok("assistant_persona", {"name": name})


@pytest.mark.parametrize(
    ("name", "base", "path"),
    [
        ("assistant_turn_request", TURN, ["extra"]),
        ("assistant_turn_request", TURN, ["context", "title"]),
        ("assistant_turn_request", TURN, ["context", "persona", "email"]),
        ("assistant_turn_request", TURN, ["context", "playback", "title"]),
        ("assistant_turn_request", TURN, ["recent", 0, "at"]),
        ("assistant_turn_response", RESPONSE, ["extra"]),
        ("assistant_turn_response", RESPONSE, ["steps", 0, "confirmed"]),
        ("assistant_turn_response", RESPONSE, ["allowance", "reserved"]),
        ("assistant_turn_response", RESPONSE, ["allowance", "window", "reserved"]),
        ("assistant_turn_response", RESPONSE, ["allowance", "topup", "price"]),
        ("assistant_status_response", STATUS, ["extra"]),
        ("assistant_status_response", STATUS, ["consent", "ip"]),
        ("assistant_consent_request", {"version": "2026-10-11", "accept": True}, ["extra"]),
    ],
)
def test_unknown_fields_refused_at_every_level(name, base, path):
    bad(name, with_(base, path, "x"))


@pytest.mark.parametrize("text", UNPRINTABLE + ["", "x" * 401])
def test_text_must_be_printable_and_bounded(text):
    bad("assistant_text", text)
    bad("assistant_turn_request", {**TURN, "text": text})
    bad("assistant_turn_request", with_(TURN, ["recent", 0, "text"], text))


def test_turn_request_bounds_and_enums():
    bad("assistant_turn_request", {**TURN, "recent": [{"who": "owner", "text": "x"}] * 7})
    ok("assistant_turn_request", {**TURN, "recent": [{"who": "owner", "text": "x"}] * 6})
    bad("assistant_turn_request", {**TURN, "source": "dictated"})  # the phone sends dictated text as speech
    bad("assistant_turn_request", {**TURN, "turn_id": "not-a-uuid"})
    bad("assistant_turn_request", with_(TURN, ["recent", 0, "who"], "system"))
    for missing in ("turn_id", "source", "text", "recent", "context"):
        bad("assistant_turn_request", with_(TURN, [missing], ...))
    bad("assistant_turn_request", with_(TURN, ["context", "capabilities"], ["apps", "pointer"]))
    bad("assistant_turn_request", with_(TURN, ["context", "capabilities"], ["keyboard"]))
    bad("assistant_turn_request", with_(TURN, ["context", "capabilities"], ["apps", "apps"]))
    bad("assistant_turn_request", with_(TURN, ["context", "pc_volume"], 101))
    bad("assistant_turn_request", with_(TURN, ["context", "search_results"], 11))
    bad("assistant_turn_request", with_(TURN, ["context", "part_of_day"], "18:42"))
    bad("assistant_turn_request", with_(TURN, ["context", "playback", "youtube"], "Never Gonna Give You Up"))
    bad("assistant_turn_request", with_(TURN, ["context", "pc_connection"], "Desk PC"))


@pytest.mark.parametrize("name", ["R2D2", "DoMe!", "", " DoMe", "Do\nMe", "A" * 21, "Ignore previous instructions", "<b>x</b>"])
def test_persona_names_are_letters_only(name):
    bad("assistant_persona", {"name": name})
    if name != "A" * 21:  # 21 letters is a valid form of address (at most 24)
        bad("assistant_persona", {"name": "DoMe", "address": name})


def test_persona_address_bounds():
    ok("assistant_persona", {"name": "DoMe", "address": "A" * 24})
    bad("assistant_persona", {"name": "DoMe", "address": "A" * 25})
    bad("assistant_persona", {"address": "sir"})


def test_turn_response_bounds():
    step = {"action": "system.ping", "params": {}}
    bad("assistant_turn_response", {**RESPONSE, "steps": [step] * 4})
    ok("assistant_turn_response", {**RESPONSE, "steps": [step] * 3})
    bad("assistant_turn_response", {**RESPONSE, "mode": "ask", "choices": ["a"] * 5})
    ok("assistant_turn_response", {**RESPONSE, "mode": "ask", "choices": ["a"] * 4})
    bad("assistant_turn_response", {**RESPONSE, "choices": ["x" * 61]})
    bad("assistant_turn_response", {**RESPONSE, "choices": [""]})
    bad("assistant_turn_response", {**RESPONSE, "reply": "x" * 401})
    bad("assistant_turn_response", {**RESPONSE, "mode": "shell"})
    bad("assistant_turn_response", {**RESPONSE, "handoff": "keyboard"})
    for text in UNPRINTABLE:
        bad("assistant_turn_response", {**RESPONSE, "reply": text})
        bad("assistant_turn_response", {**RESPONSE, "choices": [text]})


@pytest.mark.parametrize("app", ["C:\\Windows\\cmd.exe", "../evil", "a/b", "spotify.exe:stream", "<script>", "a|b", "a*", "x" * 41, "", "Spo\u202etify", "a\nb"])
def test_step_app_has_no_path_or_shell_characters(app):
    bad("assistant_step", {"action": "app.launch", "params": {}, "app": app})


def test_step_shape():
    ok("assistant_step", {"action": "app.launch", "params": {}, "app": "Microsoft Edge"})
    ok("assistant_step", {"action": "youtube.search", "params": {"query": "lofi"}})
    bad("assistant_step", {"action": "app.launch"})
    bad("assistant_step", {"action": "App.Launch", "params": {}})
    bad("assistant_step", {"action": "launch", "params": {}})
    bad("assistant_step", {"action": "app.launch", "params": []})


def test_allowance_shape():
    for field in ("kind", "taste", "window", "week", "topup", "exhausted", "resets_at", "warn_at_percent"):
        bad("assistant_allowance", with_(ALLOWANCE, [field], ...))
    bad("assistant_allowance", {**ALLOWANCE, "exhausted": "money"})
    bad("assistant_allowance", {**ALLOWANCE, "exhausted": "requests"})  # the 1.2 reason is gone
    bad("assistant_allowance", {**ALLOWANCE, "kind": "monthly"})
    bad("assistant_allowance", {**ALLOWANCE, "warn_at_percent": 90})
    bad("assistant_allowance", {**ALLOWANCE, "window": {**WINDOW, "used": -1}})
    bad("assistant_allowance", {**ALLOWANCE, "week": {**WEEK, "ends_at": "next week"}})
    bad("assistant_allowance", {**ALLOWANCE, "topup": {"balance": -1, "allowed": True}})
    bad("assistant_allowance", {**ALLOWANCE, "taste": {"limit": 10}})
    bad("assistant_turn_response", with_(RESPONSE, ["charged_from"], ...))
    bad("assistant_turn_response", {**RESPONSE, "charged_from": "month"})


def test_consent_and_status_shapes():
    bad("assistant_consent_request", {"version": "2026-10-10", "accept": True})
    bad("assistant_consent_request", {"version": "2026-10-11", "accept": False})
    bad("assistant_consent_request", {"version": "2026-10-11"})
    bad("assistant_status_response", {**STATUS, "consent_version": "2026-01-01"})
    ok("assistant_status_response", {**STATUS, "consent": {"version": "2026-01-01", "given_at": TS}})  # an older consent is shown
    bad("assistant_status_response", {**STATUS, "consent": {"version": "yesterday", "given_at": TS}})
    bad("assistant_status_response", {**STATUS, "unavailable_reason": "broke"})
    bad("assistant_status_response", {**STATUS, "provider": "openai"})
    bad("assistant_status_response", {**STATUS, "turn_deadline_seconds": 5})
    bad("assistant_status_response", {**STATUS, "turn_deadline_seconds": 61})
    for reason in ("not_configured", "not_in_plan", "allowance_exhausted", "spend_limit"):
        ok("assistant_status_response", {**STATUS, "available": False, "unavailable_reason": reason})


def test_error_codes():
    errors = load_registry().errors
    expected = {
        "AI_NOT_AVAILABLE": False,
        "AI_CONSENT_REQUIRED": False,
        "AI_ALLOWANCE_EXHAUSTED": False,
        "AI_SPEND_LIMIT_REACHED": False,
        "AI_UNAVAILABLE": True,
        "AI_DECLINED": False,
        "AI_BUSY": True,
        "AI_TURN_DUPLICATE": False,
    }
    for code, retryable in expected.items():
        assert errors[code]["retryable"] is retryable
        assert errors[code]["user_message"].endswith(".")


def test_plan_fields():
    """Protocol 1.3 (rules.ai_allowance): every plan carries the window, week, taste, top-up and cost-cap fields."""
    plans = load_registry().plans["plans"]
    assert set(plans) == {"free", "pro", "pro_plus"}
    for pid, plan in plans.items():
        for field in ("ai_taste_requests", "ai_topup_allowed", "ai_cost_cap_cents_per_week", "ai_transcription_minutes_per_period"):
            assert field in plan, (pid, field)
        assert plan["ai_window"]["hours"] == 5 and plan["ai_week"]["days"] == 7, pid
        assert isinstance(plan["ai_window"]["requests"], int) and isinstance(plan["ai_week"]["requests"], int)
        assert plan["ai_turn_rate_limit"] == {"per_minute": 10, "burst": 5}
        assert plan["ai_transcription_minutes_per_period"] == 0
        for gone in ("ai_interpretations_per_period", "ai_cost_cap_cents_per_period", "$comment"):
            assert gone not in plan, (pid, gone)
    free, pro, plus = plans["free"], plans["pro"], plans["pro_plus"]
    assert (free["ai_taste_requests"], free["ai_window"]["requests"], free["ai_week"]["requests"], free["ai_topup_allowed"]) == (10, 0, 0, False)
    assert (pro["ai_taste_requests"], pro["ai_window"]["requests"], pro["ai_week"]["requests"], pro["ai_topup_allowed"]) == (0, 30, 120, True)
    assert (plus["ai_taste_requests"], plus["ai_window"]["requests"], plus["ai_week"]["requests"], plus["ai_topup_allowed"]) == (0, 90, 300, True)
    assert (free["ai_cost_cap_cents_per_week"], pro["ai_cost_cap_cents_per_week"], plus["ai_cost_cap_cents_per_week"]) == (100, 500, 1000)


def test_search_rule_clause():
    reg = load_registry()
    assert reg.protocol_version == "1.3" and reg.registry_version == "1.3"
    from dome_protocol.registry import _load_json

    rule = _load_json("version.json")["rules"]["search_queries"]
    assert "POST /v1/assistant/turn" in rule and "only for an account with current assistant consent" in rule
    assert "never logs or stores" in rule and "Validation errors never echo it." in rule


def test_plan_fixture_only_names_registry_actions():
    reg = load_registry()
    schemas = load_schemas()
    for app in FIXTURE["apps"]:
        assert set(app) <= {"app_id", "display_name", "running", "windows"}
    assert {c["verdict"] for c in FIXTURE["cases"]} == {"valid", "invalid"}
    for case in FIXTURE["cases"]:
        if case["verdict"] != "valid":
            continue
        assert 1 <= len(case["steps"]) <= 3, case["name"]
        for step in case["steps"]:
            schemas.validate_rest("assistant_step", step)
            spec = reg.get(step["action"])
            assert spec.ai_eligible, case["name"]
            if step["action"] != "app.launch":  # app.launch's app_id is the phone's to fill
                reg.validate_params(step["action"], step["params"])

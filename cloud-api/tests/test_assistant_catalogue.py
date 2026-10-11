"""The frozen prefix of every assistant request (docs/design/assistant.md §2.2–§2.4, §2.8): the catalogue holds exactly
the AI-eligible actions, the prompt is the reviewed text, nothing per-request can enter it, and the output schema
uses only what structured outputs accept."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import Registry, load_registry
from dome_protocol.registry import _load_json

from dome_api.assistant import catalogue
from dome_api.assistant.catalogue import (
    APP_ACTIONS,
    CONSENT_VERSION,
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    SYSTEM_TEXT,
    VOICE_HINTS,
    WITHHELD_PARAMS,
    build_output_schema,
    catalogue_text,
)

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / "golden" / "assistant_system_text.txt"
UNSUPPORTED_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "not",
    "default",
    "multipleOf",
    "dependentRequired",
    "maxItems",
    "minItems",
    "$ref",
    "$defs",
}


def eligible() -> list[str]:
    return sorted(name for name, spec in load_registry().actions.items() if spec.ai_eligible)


def test_catalogue_is_exactly_the_ai_eligible_actions_sorted() -> None:
    lines = catalogue_text().split("\n")
    names = [line.split(":", 1)[0].removeprefix("- ") for line in lines]
    assert names == eligible() and len(names) == 35
    assert set(VOICE_HINTS) == set(eligible())
    assert not any(name.startswith("input.") for name in names)
    assert "input." not in SYSTEM_TEXT and "pointer" not in SYSTEM_TEXT.split("Action catalogue", 1)[1]
    assert APP_ACTIONS == {
        "app.launch",
        "app.focus",
        "app.minimize",
        "app.maximize",
        "app.restore",
        "app.set_fullscreen",
        "app.close",
    }


def test_catalogue_lines_match_the_design() -> None:
    text = catalogue_text()
    for expected in (
        '- power.shutdown: Shut down the PC after a 10-second countdown. params: none. Asks the owner to confirm. Needs permission "power".',
        "- youtube.seek_relative: Jump forward (positive) or back (negative) in the YouTube video by a number of seconds. "
        "params: seconds (a whole number from -600 to 600, not 0; required). Applies to the YouTube tab the phone picks. "
        'Needs permission "media".',
        "- app.launch: Open (start) an approved app. app: the app's name as the owner said it (required). params: none. "
        'Needs permission "apps".',
        "- youtube.search: Search YouTube for the words the owner wants; a results page opens on the PC. params: query "
        '(text, at most 100 characters; required). Runs in a browser the phone picks. Needs permission "media".',
        "- youtube.open_result: Play one of the YouTube search results, by its number. params: index (a whole number "
        'from 1 to 10; required). Applies to the YouTube tab the phone picks. Needs permission "media".',
        "- media.set_paused: Pause (paused true) or play (paused false) the media player that is not YouTube. params: "
        'paused (true or false; required). Applies to the media player the phone picks. Needs permission "media".',
        "- app.close: Close a running approved app; it is never forced, and the app may refuse because of unsaved work. "
        "app: the app's name as the owner said it (required). params: none. Applies to the named app's window. "
        'Asks the owner to confirm. Needs permission "apps".',
    ):
        assert expected in text.split("\n"), expected
    # the parameters the model never sees or sets
    for withheld in ("countdown_seconds", "video_id", "app_id"):
        assert withheld not in text


def test_system_prompt_is_the_reviewed_text() -> None:
    design = (REPO / "docs" / "design" / "assistant.md").read_text(encoding="utf-8")
    block = design.split("### 2.2 System prompt", 1)[1].split("```text\n", 1)[1].split("\n```", 1)[0]
    assert SYSTEM_PROMPT == block
    assert SYSTEM_TEXT == SYSTEM_PROMPT + "\n" + catalogue_text()
    # a golden file pins the whole prefix, so any change (prompt, hints, registry) is a reviewed diff
    assert SYSTEM_TEXT == GOLDEN.read_text(encoding="utf-8")


def test_prefix_holds_nothing_per_request() -> None:
    # no date, time, id, account, name or flag: the cached prefix is shared by every request of every account
    assert not re.search(r"\d{4}-\d{2}-\d{2}", SYSTEM_TEXT)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", SYSTEM_TEXT)
    assert "DoMe test" not in SYSTEM_TEXT and CONSENT_VERSION not in SYSTEM_TEXT
    # persona: no fictional butler or AI names anywhere (decision in docs/design/assistant.md §0)
    for name in ("Jarvis", "JARVIS", "Alfred", "Jeeves", "Friday", "Pennyworth"):
        assert name not in SYSTEM_TEXT
    assert "context.persona.name" in SYSTEM_TEXT and "never use sir, madam" in SYSTEM_TEXT
    assert len(SYSTEM_TEXT) > 2048 * 2  # comfortably above the 512-token minimum cacheable prefix


def test_two_builds_are_byte_identical() -> None:
    assert catalogue_text() == catalogue_text()
    first, second = build_output_schema(), build_output_schema()
    assert first == second == OUTPUT_SCHEMA
    assert json.dumps(first) == json.dumps(second) == json.dumps(OUTPUT_SCHEMA)
    assert json.dumps(OUTPUT_SCHEMA) == json.dumps(OUTPUT_SCHEMA, sort_keys=True)  # insertion order is sorted


def _walk(node: Any, path: str, seen: list[str]) -> None:
    assert isinstance(node, dict), path
    bad = UNSUPPORTED_KEYWORDS & set(node)
    assert not bad, f"{path}: {sorted(bad)}"
    if node.get("type") == "object":
        assert node.get("additionalProperties") is False, path
        for name, child in node.get("properties", {}).items():
            _walk(child, f"{path}.{name}", seen)
        assert set(node.get("required", [])) <= set(node.get("properties", {})), path
    if "items" in node:
        _walk(node["items"], f"{path}[]", seen)
    for i, child in enumerate(node.get("anyOf", [])):
        seen.append(f"{path}|{i}")
        _walk(child, f"{path}|{i}", seen)


def test_output_schema_uses_only_supported_keywords_and_closed_objects() -> None:
    seen: list[str] = []
    _walk(OUTPUT_SCHEMA, "$", seen)
    assert OUTPUT_SCHEMA["required"] == ["mode", "reply", "choices", "handoff", "steps"]
    assert OUTPUT_SCHEMA["properties"]["mode"]["enum"] == ["act", "ask", "answer", "decline"]
    assert OUTPUT_SCHEMA["properties"]["handoff"]["enum"] == ["none", "touchpad"]
    assert len(seen) == 35


def test_one_variant_per_action_with_the_registry_params_minus_withheld() -> None:
    reg = load_registry()
    variants = OUTPUT_SCHEMA["properties"]["steps"]["items"]["anyOf"]
    actions = [v["properties"]["action"]["const"] for v in variants]
    assert actions == eligible()
    for variant in variants:
        action = variant["properties"]["action"]["const"]
        params = variant["properties"]["params"]
        registry_params = set(reg.get(action).params_schema.get("properties", {}))
        assert set(params["properties"]) == registry_params - WITHHELD_PARAMS.get(action, frozenset()), action
        required = set(reg.get(action).params_schema.get("required", [])) - WITHHELD_PARAMS.get(action, frozenset())
        assert set(params.get("required", [])) == required, action
        if action in APP_ACTIONS:
            assert variant["required"] == ["action", "params", "app"]
            assert variant["properties"]["app"] == {
                "type": "string",
                "description": "the app's name as the owner said it",
            }
        else:
            assert variant["required"] == ["action", "params"] and "app" not in variant["properties"]
    by_action = {v["properties"]["action"]["const"]: v for v in variants}
    assert by_action["youtube.seek_relative"]["properties"]["params"] == {
        "additionalProperties": False,
        "properties": {"seconds": {"description": "a whole number from -600 to 600, not 0", "type": "integer"}},
        "required": ["seconds"],
        "type": "object",
    }
    assert by_action["power.sleep"]["properties"]["params"] == {
        "additionalProperties": False,
        "properties": {},
        "type": "object",
    }
    assert by_action["app.set_fullscreen"]["properties"]["params"]["properties"] == {"enabled": {"type": "boolean"}}


def _registry_with_extra_action(*, ai_eligible: bool) -> Registry:
    raw = copy.deepcopy(_load_json("actions.json"))
    extra = copy.deepcopy(raw["actions"]["system.ping"])
    extra["summary"] = "A future action."
    if not ai_eligible:
        extra["ai_eligible"] = False
        extra["routine_allowed"] = False
    raw["actions"]["system.future"] = extra
    return Registry(raw, _load_json("errors.json"), _load_json("version.json"), _load_json("plans.json"))


def test_a_new_registry_action_forces_a_reviewed_hint() -> None:
    with pytest.raises(RuntimeError, match="system.future"):
        catalogue_text(_registry_with_extra_action(ai_eligible=True))
    # a human-only action is excluded by construction: no hint needed, never in the catalogue or the schema
    human_only = _registry_with_extra_action(ai_eligible=False)
    assert "system.future" not in catalogue_text(human_only)
    assert "system.future" not in json.dumps(build_output_schema(human_only))


def test_consent_version_comes_from_the_contract() -> None:
    rest = json.loads((REPO / "shared" / "protocol" / "schemas" / "rest.schema.json").read_text(encoding="utf-8"))
    assert CONSENT_VERSION == rest["$defs"]["assistant_consent_version"]["const"] == "2026-10-11"
    assert catalogue.OUTPUT_KEYS == {"mode", "reply", "choices", "handoff", "steps"}

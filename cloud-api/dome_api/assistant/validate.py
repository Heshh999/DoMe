"""The model's output is untrusted: this turns its JSON text into a checked outcome or refuses the whole of it
(docs/design/assistant.md §3.9).

A step survives only if its action is in ``actions.json`` with ``ai_eligible`` true, its parameters pass the
registry's schema (minus the parameters the model is never offered) and an app action names an app in plain words.
The plan as a whole must obey the rules the phone would otherwise refuse after the turn was charged: a confirmation
step stands alone, nothing is controlled twice, and only picking a result may follow a search straight away. Any
violation drops the whole output, never part of it; the caller answers a fixed, uncharged question instead. The
phone and the PC check everything again.

Errors carry a fixed reason code for the turn's log line, never a value from the output.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from dome_protocol import ProtocolError, Registry, load_schemas, loads_strict

from dome_api.assistant.catalogue import APP_ACTIONS, OUTPUT_KEYS, OUTPUT_MODES, WITHHELD_PARAMS

MAX_STEPS = 3
MAX_CHOICES = 4
MAX_REPLY_CHARS = 400
MAX_CHOICE_CHARS = 60
MAX_OUTPUT_BYTES = 16384
# Words the phone resolves to the app of the previous app step in the same plan (§4.4).
APP_PRONOUNS = frozenset({"it", "that", "the app"})
_SPACES = {"\r", "\n", "\t", " ", " "}


class InvalidOutputError(Exception):
    """The model's output is refused as a whole. ``reason`` is a fixed code (``steps.not_eligible`` ...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class AssistantOutcome:
    mode: str
    reply: str
    choices: tuple[str, ...]
    handoff: str
    steps: tuple[dict[str, Any], ...]

    @property
    def actions(self) -> tuple[str, ...]:
        return tuple(str(s["action"]) for s in self.steps)


def clean_text(value: str) -> str | None:
    """Line breaks, tabs and U+2028/U+2029 become spaces, every format character (``Cf``: bidi overrides,
    zero-width characters, BOM, Unicode tags ...) is removed, runs of spaces collapse and the ends are trimmed.
    Any other control character makes the text unusable (``None``)."""
    out: list[str] = []
    for ch in value:
        if ch in _SPACES:
            out.append(" ")
            continue
        category = unicodedata.category(ch)
        if category == "Cf":
            continue
        if category == "Cc":
            return None
        out.append(ch)
    return " ".join("".join(out).split())


def _clean_spoken(value: Any, *, max_chars: int, reason: str) -> str:
    if not isinstance(value, str):
        raise InvalidOutputError(f"{reason}.type")
    cleaned = clean_text(value)
    if cleaned is None:
        raise InvalidOutputError(f"{reason}.control")
    if len(cleaned) > max_chars:
        raise InvalidOutputError(f"{reason}.length")
    return cleaned


def _app_key(app: str) -> str:
    return " ".join(app.casefold().split())


def _check_step(registry: Registry, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InvalidOutputError("steps.type")
    action = raw.get("action")
    if not isinstance(action, str) or action not in registry.actions or not registry.get(action).ai_eligible:
        raise InvalidOutputError("steps.not_eligible")
    is_app_action = action in APP_ACTIONS
    expected = {"action", "params", "app"} if is_app_action else {"action", "params"}
    if set(raw) != expected:
        raise InvalidOutputError("steps.keys")
    params = raw["params"]
    if not isinstance(params, dict):
        raise InvalidOutputError("steps.params")
    if WITHHELD_PARAMS.get(action, frozenset()) & set(params):
        raise InvalidOutputError(
            "steps.withheld_param"
        )  # video_id, countdown_seconds, app_id: never the model's to set
    if action == "app.launch":  # its only parameter (app_id) is the phone's to fill, so params is exactly {}
        if params:
            raise InvalidOutputError("steps.params")
    else:
        try:
            registry.validate_params(action, params)
        except ProtocolError:
            raise InvalidOutputError("steps.params") from None
    step: dict[str, Any] = {"action": action, "params": dict(params)}
    if is_app_action:
        step["app"] = raw["app"]
    try:
        load_schemas().validate_rest("assistant_step", step)  # app: 1-40 plain characters, no path or shell syntax
    except ProtocolError:
        raise InvalidOutputError("steps.app") from None
    return step


def _check_plan(registry: Registry, steps: list[dict[str, Any]]) -> None:
    if len(steps) > 1 and any(registry.get(s["action"]).requires_confirmation for s in steps):
        raise InvalidOutputError("plan.confirmation_not_alone")
    seen: set[tuple[str, str]] = set()
    previous_app: str | None = None
    for step in steps:
        app = step.get("app")
        key_app = ""
        if isinstance(app, str):
            key_app = _app_key(app)
            if key_app in APP_PRONOUNS and previous_app is not None:
                key_app = previous_app  # the phone resolves "it" to the previous app step's app
            previous_app = key_app
        key = (step["action"], key_app)
        if key in seen:
            raise InvalidOutputError("plan.duplicate")
        seen.add(key)
    for before, after in zip(steps, steps[1:], strict=False):
        if (
            before["action"] == "youtube.search"
            and registry.get(after["action"]).target_name == "youtube_target"
            and after["action"] != "youtube.open_result"
        ):
            raise InvalidOutputError("plan.player_after_search")


def validate_output(text: str, registry: Registry) -> AssistantOutcome:
    """Parse and check the model's JSON text; raises :class:`InvalidOutputError` on any violation."""
    try:
        parsed = loads_strict(text, max_bytes=MAX_OUTPUT_BYTES, require_object=True)
    except ProtocolError:
        raise InvalidOutputError("json") from None
    if not isinstance(parsed, dict) or set(parsed) != OUTPUT_KEYS:
        raise InvalidOutputError("keys")
    mode = parsed["mode"]
    if mode not in OUTPUT_MODES:
        raise InvalidOutputError("mode")
    reply = _clean_spoken(parsed["reply"], max_chars=MAX_REPLY_CHARS, reason="reply")
    if mode == "act":
        reply = ""  # the phone announces the validated steps and their real results itself
    elif not reply:
        raise InvalidOutputError("reply.empty")

    raw_choices = parsed["choices"]
    if not isinstance(raw_choices, list):
        raise InvalidOutputError("choices.type")
    choices: list[str] = []
    if mode == "ask":
        if len(raw_choices) > MAX_CHOICES:
            raise InvalidOutputError("choices.count")
        for raw in raw_choices:
            choice = _clean_spoken(raw, max_chars=MAX_CHOICE_CHARS, reason="choices")
            if not choice:
                raise InvalidOutputError("choices.empty")
            choices.append(choice)

    handoff = parsed["handoff"]
    if handoff not in ("none", "touchpad"):
        raise InvalidOutputError("handoff")
    if mode not in ("decline", "answer"):
        handoff = "none"

    raw_steps = parsed["steps"]
    if not isinstance(raw_steps, list):
        raise InvalidOutputError("steps.type")
    if mode == "act":
        if not 1 <= len(raw_steps) <= MAX_STEPS:
            raise InvalidOutputError("steps.count")
    elif raw_steps:
        raise InvalidOutputError("steps.not_act")
    steps = [_check_step(registry, raw) for raw in raw_steps]
    _check_plan(registry, steps)
    return AssistantOutcome(mode=mode, reply=reply, choices=tuple(choices), handoff=handoff, steps=tuple(steps))

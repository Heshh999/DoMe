"""The test harness itself (docs/design/screen-view-2a.md §2.4): ``ControllerSim`` stamps a command by its content, like
the phone (rules.command_version_stamping, 1.5). The repository's e2e suite reuses ``ControllerSim`` for its Python
phone and relies on this for every start that carries ``cursor``."""

from __future__ import annotations

import uuid
from typing import Any

from dome_protocol import command_protocol_version, loads_strict

from tests.conftest import OFFER_SDP, REGISTRY, Browser, ControllerSim, Env

START = {"offer_sdp": OFFER_SDP, "surface": "primary", "audio": False, "takeover": False}


def stamp(ctrl: ControllerSim, action: str, params: dict[str, Any] | None, **kw: Any) -> str:
    _, envl = ctrl.envelope(str(uuid.uuid4()), action, params, None, **kw)
    version: str = loads_strict(envl["payload"])["protocol_version"]
    return version


async def test_controller_sim_stamps_a_command_by_its_content(env: Env, alice: Browser) -> None:
    ctrl = ControllerSim(env, alice)
    # screen.session_start: 1.4 (its since) without `cursor`, 1.5 (its param_since) with it, whatever the value
    assert stamp(ctrl, "screen.session_start", START) == "1.4"
    assert stamp(ctrl, "screen.session_start", {**START, "surface": 1}) == "1.4"
    assert stamp(ctrl, "screen.session_start", {**START, "cursor": "overlay"}) == "1.5"
    assert stamp(ctrl, "screen.session_start", {**START, "cursor": "drawn"}) == "1.5"
    # the same answer as the shared library's helper, for every action with and without params
    for action in REGISTRY.actions:
        assert stamp(ctrl, action, None) == command_protocol_version(action, {}, registry=REGISTRY), action
    assert command_protocol_version("screen.session_start", {**START, "cursor": "overlay"}, registry=REGISTRY) == "1.5"
    # an explicit protocol_version still overrides the content (tests forge old and too-new stamps)
    assert stamp(ctrl, "screen.session_start", {**START, "cursor": "overlay"}, protocol_version="1.4") == "1.4"
    assert stamp(ctrl, "screen.session_start", START, protocol_version="1.5") == "1.5"
    # an action the registry does not know gets the registry's own version
    assert stamp(ctrl, "nope.unknown", {}) == REGISTRY.protocol_version == "1.5"

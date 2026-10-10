"""The tray menu is rebuilt on every status update; the paired-phones submenu must follow the store."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dome_agent.settings import Settings
from dome_agent.store import Store
from dome_agent.tray import TrayUI


def _submenu(menu: Any, text: str) -> Any:
    item = next(i for i in menu if i.text == text)
    return item.submenu


@pytest.fixture
def pystray(monkeypatch: pytest.MonkeyPatch) -> Any:
    # The dummy backend needs no display; only Menu/MenuItem are exercised here.
    monkeypatch.setenv("PYSTRAY_BACKEND", "dummy")
    return pytest.importorskip("pystray")


def test_phones_paired_after_the_menu_was_built_appear_in_it(pystray: Any, settings: Settings, store: Store) -> None:
    tray = TrayUI(settings)
    # The icon (and its menu) is created before the agent thread binds.
    menu = tray._menu()  # noqa: SLF001
    assert [i.text for i in _submenu(menu, "Paired phones")] == ["(starting…)"]

    tray.agent = SimpleNamespace(store=store)
    assert [i.text for i in _submenu(menu, "Paired phones")] == ["(no paired phones)"]

    store.add_grant(
        controller_id="0f6d2c1e-1111-4222-8333-444455556666",
        kid="kid-1",
        public_jwk={"kty": "EC"},
        capabilities=["status", "media", "volume"],
        display_name="iPhone",
    )
    phones = list(_submenu(menu, "Paired phones"))
    assert [i.text for i in phones] == ["iPhone (0f6d2c1e…)"]
    toggles = [i.text for i in phones[0].submenu]
    assert toggles[:2] == ["Allow touchpad", "Allow keyboard"]
    assert phones[0].submenu.items[0].checked is False

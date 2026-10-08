"""Programmatic Alembic entry points used by the CLI and the test-suite."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
_SCRIPTS = Path(__file__).resolve().parent / "alembic"


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(_INI))
    cfg.set_main_option("script_location", str(_SCRIPTS))
    cfg.attributes["database_url"] = database_url
    return cfg


def upgrade_to_head(database_url: str) -> None:
    """Apply every pending migration. Synchronous; call before the event loop starts or in a thread."""
    command.upgrade(alembic_config(database_url), "head")

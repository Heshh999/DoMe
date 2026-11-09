"""Screen view round 2a (docs/design/screen-view-2a.md §2.2; protocol 1.5): the protocol versions a PC's agent announced
in its last relay hello, persisted on the ``pcs`` row next to ``agent_version`` and served as REST
``pc.protocol_versions`` (an empty list until the agent next connects). No column holds content: version strings only.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pcs",
        sa.Column(
            "protocol_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("pcs", "protocol_versions")

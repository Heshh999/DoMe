"""Deleting an account needs a sign-in that really asked for the password (DECISIONS #121): the server records it
instead of trusting a session's age. ``auth_flows.reauth`` marks a login attempt started with an explicit
``prompt=login`` (and ``max_age=0``); ``sessions.authenticated_at`` holds the provider's ``auth_time`` for a session
that such an attempt created, once checked against the attempt's start. Every other session leaves it NULL. No column
holds a credential, an email or a name.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_flows", sa.Column("reauth", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("sessions", sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "authenticated_at")
    op.drop_column("auth_flows", "reauth")

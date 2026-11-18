"""Micro sets: the sidecar's optional `mirror:` line (DESIGN.md §7.10).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Plain ALTER TABLE, not batch mode: batch mode rebuilds micro_sets, and with foreign keys on,
# SQLite won't drop it while micro_sources rows point at it.
def upgrade() -> None:
    op.add_column(
        "micro_sets",
        sa.Column("mirror_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("micro_sets", "mirror_allowed")

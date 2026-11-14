"""Instagram only: drop TikTok data and the TikTok Studio export table (DESIGN.md §13, D11).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14 12:00:00.000000

The downgrade restores the empty `metric_imports` table; deleted TikTok rows aren't restored.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIKTOK = "tiktok"


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "delete from metrics where checkpoint = 'export' or source = 'export' "
            "or post_id in (select id from posts where platform = :tiktok)"
        ),
        {"tiktok": TIKTOK},
    )
    connection.execute(sa.text("delete from posts where platform = :tiktok"), {"tiktok": TIKTOK})
    connection.execute(
        sa.text("delete from variable_scores where platform = :tiktok"), {"tiktok": TIKTOK}
    )
    renders = connection.execute(sa.text("select id, targets_json, captions_json from renders"))
    for render_id, targets_json, captions_json in renders.all():
        targets = json.loads(targets_json)
        captions = json.loads(captions_json)
        if TIKTOK not in targets and TIKTOK not in captions:
            continue
        connection.execute(
            sa.text(
                "update renders set targets_json = :targets, captions_json = :captions "
                "where id = :id"
            ),
            {
                "id": render_id,
                "targets": json.dumps([target for target in targets if target != TIKTOK]),
                "captions": json.dumps({k: v for k, v in captions.items() if k != TIKTOK}),
            },
        )

    with op.batch_alter_table("metric_imports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_metric_imports_status"))
    op.drop_table("metric_imports")


def downgrade() -> None:
    op.create_table(
        "metric_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drive_file_id", sa.String(), nullable=False),
        sa.Column("md5", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=True),
        sa.Column("rows_matched", sa.Integer(), nullable=True),
        sa.Column("rows_unmatched", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_file_id"),
    )
    with op.batch_alter_table("metric_imports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_metric_imports_status"), ["status"], unique=False)

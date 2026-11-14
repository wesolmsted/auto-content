"""Micro edits: `micro_sets`, `micro_sources` and `micro_renders` (DESIGN.md §7.10, D12).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14 17:54:42.460396
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "micro_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drive_folder_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("song_title", sa.String(), nullable=True),
        sa.Column("text_options_json", sa.JSON(), nullable=False),
        sa.Column("next_text_index", sa.Integer(), nullable=False),
        sa.Column("sidecar_drive_file_id", sa.String(), nullable=True),
        sa.Column("sidecar_md5", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_folder_id"),
    )
    with op.batch_alter_table("micro_sets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_micro_sets_status"), ["status"], unique=False)

    op.create_table(
        "micro_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("drive_file_id", sa.String(), nullable=False),
        sa.Column("md5", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("local_path", sa.String(), nullable=True),
        sa.Column("duration_s", sa.Double(), nullable=True),
        sa.Column("has_audio", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["micro_sets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_file_id"),
    )
    with op.batch_alter_table("micro_sources", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_micro_sources_md5"), ["md5"], unique=False)
        batch_op.create_index(batch_op.f("ix_micro_sources_set_id"), ["set_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_micro_sources_status"), ["status"], unique=False)

    op.create_table(
        "micro_renders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edit_json", sa.JSON(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("caption", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=True),
        sa.Column("duration_s", sa.Double(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("ffmpeg_argv_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["micro_sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("micro_renders", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_micro_renders_scheduled_for"), ["scheduled_for"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_micro_renders_source_id"), ["source_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_micro_renders_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("micro_renders", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_micro_renders_status"))
        batch_op.drop_index(batch_op.f("ix_micro_renders_source_id"))
        batch_op.drop_index(batch_op.f("ix_micro_renders_scheduled_for"))

    op.drop_table("micro_renders")
    with op.batch_alter_table("micro_sources", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_micro_sources_status"))
        batch_op.drop_index(batch_op.f("ix_micro_sources_set_id"))
        batch_op.drop_index(batch_op.f("ix_micro_sources_md5"))

    op.drop_table("micro_sources")
    with op.batch_alter_table("micro_sets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_micro_sets_status"))

    op.drop_table("micro_sets")

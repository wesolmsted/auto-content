"""Initial schema: every table in DESIGN.md §5.

Revision ID: 0001
Revises:
Create Date: 2026-11-08 23:16:31.921824
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "footage_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drive_file_id", sa.String(), nullable=False),
        sa.Column("md5", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("local_path", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_file_id"),
    )
    with op.batch_alter_table("footage_files", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_footage_files_md5"), ["md5"], unique=False)
        batch_op.create_index(batch_op.f("ix_footage_files_status"), ["status"], unique=False)

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

    op.create_table(
        "recipes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "songs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("audio_path", sa.String(), nullable=False),
        sa.Column("duration_s", sa.Double(), nullable=True),
        sa.Column("bpm", sa.Double(), nullable=True),
        sa.Column("beats_json", sa.JSON(), nullable=False),
        sa.Column("downbeats_json", sa.JSON(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "variable_scores",
        sa.Column("variable", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("alpha", sa.Double(), nullable=False),
        sa.Column("beta", sa.Double(), nullable=False),
        sa.Column("n_posts", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("variable", "value", "platform"),
    )
    op.create_table(
        "shots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("footage_file_id", sa.Integer(), nullable=False),
        sa.Column("start_s", sa.Double(), nullable=False),
        sa.Column("end_s", sa.Double(), nullable=False),
        sa.Column("duration_s", sa.Double(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("fps", sa.Double(), nullable=False),
        sa.Column("orientation", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("setting", sa.String(), nullable=True),
        sa.Column("lighting", sa.String(), nullable=True),
        sa.Column("mood", sa.String(), nullable=True),
        sa.Column("motion_score", sa.Double(), nullable=True),
        sa.Column("quality_score", sa.Double(), nullable=True),
        sa.Column("has_face", sa.Boolean(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("proxy_path", sa.String(), nullable=True),
        sa.Column("thumbnail_path", sa.String(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["footage_file_id"],
            ["footage_files.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("shots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_shots_category"), ["category"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_shots_footage_file_id"), ["footage_file_id"], unique=False
        )

    op.create_table(
        "snippets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("song_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("snippet_type", sa.String(length=32), nullable=False),
        sa.Column("start_s", sa.Double(), nullable=False),
        sa.Column("end_s", sa.Double(), nullable=False),
        sa.Column("drop_s", sa.Double(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["song_id"],
            ["songs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("snippets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_snippets_song_id"), ["song_id"], unique=False)

    op.create_table(
        "style_references",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drive_file_id", sa.String(), nullable=False),
        sa.Column("md5", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_link", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("style_card_json", sa.JSON(), nullable=True),
        sa.Column("recipe_id", sa.String(length=32), nullable=True),
        sa.Column("recreation_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_file_id"),
    )
    with op.batch_alter_table("style_references", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_style_references_md5"), ["md5"], unique=False)
        batch_op.create_index(batch_op.f("ix_style_references_status"), ["status"], unique=False)

    op.create_table(
        "renders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.String(length=32), nullable=False),
        sa.Column("snippet_id", sa.Integer(), nullable=False),
        sa.Column("timeline_json", sa.JSON(), nullable=False),
        sa.Column("shot_ids_json", sa.JSON(), nullable=False),
        sa.Column("ffmpeg_argv_json", sa.JSON(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=True),
        sa.Column("duration_s", sa.Double(), nullable=True),
        sa.Column("targets_json", sa.JSON(), nullable=False),
        sa.Column("captions_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("reject_reason", sa.String(length=32), nullable=True),
        sa.Column("reject_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["snippet_id"],
            ["snippets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("renders", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_renders_recipe_id"), ["recipe_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_renders_snippet_id"), ["snippet_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_renders_status"), ["status"], unique=False)

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("render_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("permalink", sa.String(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("match_method", sa.String(length=32), nullable=True),
        sa.Column("match_score", sa.Double(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["render_id"],
            ["renders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("render_id", "platform"),
    )
    with op.batch_alter_table("posts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_posts_external_id"), ["external_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_posts_render_id"), ["render_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_posts_status"), ["status"], unique=False)

    op.create_table(
        "metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("checkpoint", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("reach", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("shares", sa.Integer(), nullable=True),
        sa.Column("saves", sa.Integer(), nullable=True),
        sa.Column("avg_watch_s", sa.Double(), nullable=True),
        sa.Column("full_watch_rate", sa.Double(), nullable=True),
        sa.Column("skip_rate", sa.Double(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["posts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "checkpoint"),
    )
    with op.batch_alter_table("metrics", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_metrics_post_id"), ["post_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("metrics", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_metrics_post_id"))

    op.drop_table("metrics")
    with op.batch_alter_table("posts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_posts_status"))
        batch_op.drop_index(batch_op.f("ix_posts_render_id"))
        batch_op.drop_index(batch_op.f("ix_posts_external_id"))

    op.drop_table("posts")
    with op.batch_alter_table("renders", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_renders_status"))
        batch_op.drop_index(batch_op.f("ix_renders_snippet_id"))
        batch_op.drop_index(batch_op.f("ix_renders_recipe_id"))

    op.drop_table("renders")
    with op.batch_alter_table("style_references", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_style_references_status"))
        batch_op.drop_index(batch_op.f("ix_style_references_md5"))

    op.drop_table("style_references")
    with op.batch_alter_table("snippets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_snippets_song_id"))

    op.drop_table("snippets")
    with op.batch_alter_table("shots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_shots_footage_file_id"))
        batch_op.drop_index(batch_op.f("ix_shots_category"))

    op.drop_table("shots")
    op.drop_table("variable_scores")
    op.drop_table("songs")
    op.drop_table("recipes")
    with op.batch_alter_table("metric_imports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_metric_imports_status"))

    op.drop_table("metric_imports")
    with op.batch_alter_table("footage_files", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_footage_files_status"))
        batch_op.drop_index(batch_op.f("ix_footage_files_md5"))

    op.drop_table("footage_files")

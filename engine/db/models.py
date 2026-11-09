"""SQLAlchemy models for every table in DESIGN.md §5, Phase 2 and 3 tables included.

JSON columns keep the `_json` suffix from the design; they hold plain JSON (dicts, lists).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from engine.db.enums import (
    FootageStatus,
    MatchMethod,
    MetricCheckpoint,
    MetricImportStatus,
    MetricSource,
    Orientation,
    Platform,
    PostStatus,
    RejectReason,
    RenderStatus,
    SnippetType,
    StyleReferenceStatus,
)
from engine.db.types import StrEnumType, UtcDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012
        datetime: UtcDateTime(),
        dict[str, Any]: JSON(),
        list[Any]: JSON(),
    }


class FootageFileRow(Base):
    __tablename__ = "footage_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    drive_file_id: Mapped[str] = mapped_column(unique=True)
    md5: Mapped[str | None] = mapped_column(index=True)
    name: Mapped[str]
    size_bytes: Mapped[int | None]
    local_path: Mapped[str | None]
    status: Mapped[FootageStatus] = mapped_column(
        StrEnumType(FootageStatus), default=FootageStatus.NEW, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class ShotRow(Base):
    __tablename__ = "shots"

    id: Mapped[int] = mapped_column(primary_key=True)
    footage_file_id: Mapped[int] = mapped_column(ForeignKey("footage_files.id"), index=True)
    start_s: Mapped[float]
    end_s: Mapped[float]
    duration_s: Mapped[float]
    width: Mapped[int]
    height: Mapped[int]
    fps: Mapped[float]
    orientation: Mapped[Orientation] = mapped_column(StrEnumType(Orientation))
    category: Mapped[str | None] = mapped_column(index=True)
    subject: Mapped[str | None]
    setting: Mapped[str | None]
    lighting: Mapped[str | None]
    mood: Mapped[str | None]
    motion_score: Mapped[float | None]
    quality_score: Mapped[float | None]
    has_face: Mapped[bool] = mapped_column(default=False)
    tags_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    proxy_path: Mapped[str | None]
    thumbnail_path: Mapped[str | None]
    use_count: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class SongRow(Base):
    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    audio_path: Mapped[str]
    duration_s: Mapped[float | None]
    bpm: Mapped[float | None]
    beats_json: Mapped[list[Any]] = mapped_column(default=list)
    downbeats_json: Mapped[list[Any]] = mapped_column(default=list)
    analyzed_at: Mapped[datetime | None]


class SnippetRow(Base):
    __tablename__ = "snippets"

    id: Mapped[int] = mapped_column(primary_key=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), index=True)
    label: Mapped[str | None]
    snippet_type: Mapped[SnippetType] = mapped_column(StrEnumType(SnippetType))
    start_s: Mapped[float]
    end_s: Mapped[float]
    drop_s: Mapped[float | None]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class RecipeRow(Base):
    __tablename__ = "recipes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    params_json: Mapped[dict[str, Any]]
    source: Mapped[str] = mapped_column(default="generated")
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class RenderRow(Base):
    __tablename__ = "renders"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id"), index=True)
    snippet_id: Mapped[int] = mapped_column(ForeignKey("snippets.id"), index=True)
    timeline_json: Mapped[dict[str, Any]]
    shot_ids_json: Mapped[list[Any]] = mapped_column(default=list)
    ffmpeg_argv_json: Mapped[list[Any]] = mapped_column(default=list)
    file_path: Mapped[str | None]
    duration_s: Mapped[float | None]
    targets_json: Mapped[list[Any]] = mapped_column(default=list)
    captions_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[RenderStatus] = mapped_column(
        StrEnumType(RenderStatus), default=RenderStatus.RENDERING, index=True
    )
    notified_at: Mapped[datetime | None]
    downloaded_at: Mapped[datetime | None]
    download_count: Mapped[int] = mapped_column(default=0)
    reject_reason: Mapped[RejectReason | None] = mapped_column(StrEnumType(RejectReason))
    reject_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class PostRow(Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("render_id", "platform"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    render_id: Mapped[int] = mapped_column(ForeignKey("renders.id"), index=True)
    platform: Mapped[Platform] = mapped_column(StrEnumType(Platform))
    status: Mapped[PostStatus] = mapped_column(
        StrEnumType(PostStatus), default=PostStatus.AWAITING_POST, index=True
    )
    external_id: Mapped[str | None] = mapped_column(index=True)
    permalink: Mapped[str | None]
    posted_at: Mapped[datetime | None]
    match_method: Mapped[MatchMethod | None] = mapped_column(StrEnumType(MatchMethod))
    match_score: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class MetricRow(Base):
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("post_id", "checkpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    checkpoint: Mapped[MetricCheckpoint] = mapped_column(StrEnumType(MetricCheckpoint))
    source: Mapped[MetricSource] = mapped_column(StrEnumType(MetricSource))
    fetched_at: Mapped[datetime]
    views: Mapped[int | None]
    reach: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    shares: Mapped[int | None]
    saves: Mapped[int | None]
    avg_watch_s: Mapped[float | None]
    full_watch_rate: Mapped[float | None]
    skip_rate: Mapped[float | None]
    raw_json: Mapped[dict[str, Any]] = mapped_column(default=dict)


class MetricImportRow(Base):
    __tablename__ = "metric_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    drive_file_id: Mapped[str] = mapped_column(unique=True)
    md5: Mapped[str | None]
    name: Mapped[str]
    platform: Mapped[Platform] = mapped_column(StrEnumType(Platform))
    status: Mapped[MetricImportStatus] = mapped_column(
        StrEnumType(MetricImportStatus), default=MetricImportStatus.NEW, index=True
    )
    rows_total: Mapped[int | None]
    rows_matched: Mapped[int | None]
    rows_unmatched: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class VariableScoreRow(Base):
    """Beta posterior per recipe variable value. `platform` is a Platform value or `internal`."""

    __tablename__ = "variable_scores"

    variable: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    alpha: Mapped[float] = mapped_column(default=1.0)
    beta: Mapped[float] = mapped_column(default=1.0)
    n_posts: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class StyleReferenceRow(Base):
    __tablename__ = "style_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    drive_file_id: Mapped[str] = mapped_column(unique=True)
    md5: Mapped[str | None] = mapped_column(index=True)
    name: Mapped[str]
    note: Mapped[str | None] = mapped_column(Text)
    source_link: Mapped[str | None]
    status: Mapped[StyleReferenceStatus] = mapped_column(
        StrEnumType(StyleReferenceStatus), default=StyleReferenceStatus.NEW, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    style_card_json: Mapped[dict[str, Any] | None]
    recipe_id: Mapped[str | None] = mapped_column(ForeignKey("recipes.id"))
    recreation_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)

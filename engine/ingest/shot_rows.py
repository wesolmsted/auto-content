"""Conversions between the `Shot` domain model and `shots` rows."""

from pathlib import Path

from engine.db.models import ShotRow
from engine.ingest.models import Shot, ShotTags


def shot_to_row(shot: Shot) -> ShotRow:
    """Tags go into their query columns and, in full, into `tags_json`."""
    return ShotRow(
        footage_file_id=shot.footage_file_id,
        start_s=shot.start_s,
        end_s=shot.end_s,
        duration_s=shot.duration_s,
        width=shot.width,
        height=shot.height,
        fps=shot.fps,
        orientation=shot.orientation,
        category=shot.tags.category,
        subject=shot.tags.subject,
        setting=shot.tags.setting,
        lighting=shot.tags.lighting,
        mood=shot.tags.mood,
        motion_score=shot.motion_score,
        quality_score=shot.quality_score,
        has_face=shot.tags.has_face,
        tags_json=shot.tags.model_dump(mode="json"),
        proxy_path=str(shot.proxy_path),
        thumbnail_path=str(shot.thumbnail_path) if shot.thumbnail_path else None,
        use_count=shot.use_count,
        last_used_at=shot.last_used_at,
    )


def shot_from_row(row: ShotRow) -> Shot:
    """Inverse of `shot_to_row`. Tags come from `tags_json`; the tag columns are copies of it."""
    if row.proxy_path is None or row.motion_score is None or row.quality_score is None:
        raise ValueError(f"shot {row.id} has no proxy or scores yet")
    return Shot(
        id=row.id,
        footage_file_id=row.footage_file_id,
        start_s=row.start_s,
        end_s=row.end_s,
        width=row.width,
        height=row.height,
        fps=row.fps,
        orientation=row.orientation,
        motion_score=row.motion_score,
        quality_score=row.quality_score,
        tags=ShotTags.model_validate(row.tags_json),
        proxy_path=Path(row.proxy_path),
        thumbnail_path=Path(row.thumbnail_path) if row.thumbnail_path else None,
        use_count=row.use_count,
        last_used_at=row.last_used_at,
    )

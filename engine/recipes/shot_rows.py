from pathlib import Path

from engine.db.models import ShotRow
from engine.ingest.models import Shot, ShotTags


def shot_from_row(row: ShotRow) -> Shot:
    """Inverse of `engine.ingest.job.shot_to_row`. Tags come from `tags_json`, which holds the
    full `ShotTags` dump; the tag columns are copies of it for querying."""
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

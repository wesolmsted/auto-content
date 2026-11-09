"""`ace ingest footage`: the whole footage job (DESIGN.md §7.1)."""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import DriveSettings, IngestSettings
from engine.db.enums import FootageStatus
from engine.db.models import FootageFileRow, ShotRow
from engine.drive.client import DriveClient
from engine.ingest.models import PollSummary, Shot
from engine.ingest.poller import (
    advance,
    complete_tagged_footage,
    footage_folders,
    poll_footage,
    record_failure,
)
from engine.ingest.shots import build_shots
from engine.storage.media_store import MediaStore
from engine.vision.tagger import VisionTagger

ShotBuilder = Callable[[Path, int, VisionTagger, MediaStore, IngestSettings], list[Shot]]
"""The signature of `engine.ingest.shots.build_shots`."""


class NoShotsFoundError(Exception):
    pass


class FootageIngestSummary(BaseModel):
    """`poll` covers the Drive side; the other counts are for files built in this run."""

    model_config = ConfigDict(frozen=True)

    poll: PollSummary
    built: int = 0
    shots: int = 0
    completed: int = 0
    failed: int = 0


def run_footage_ingest(
    session: Session,
    drive: DriveClient,
    store: MediaStore,
    tagger: VisionTagger,
    drive_settings: DriveSettings,
    ingest_settings: IngestSettings,
    build: ShotBuilder = build_shots,
) -> FootageIngestSummary:
    """Poll Drive, build shots for each `downloaded` file, then move finished files to processed.

    Each file is committed on its own: `downloaded` -> `tagged` once its shots are stored, then
    `done` once it is moved in Drive and its scratch download deleted. A builder error is
    recorded on the row and the job continues with the next file. Idempotent.
    """
    max_attempts = ingest_settings.max_attempts
    poll = poll_footage(session, drive, store, drive_settings, max_attempts)
    session.commit()

    built = shot_count = failed = 0
    downloaded = select(FootageFileRow).where(FootageFileRow.status == FootageStatus.DOWNLOADED)
    for row in list(session.scalars(downloaded.order_by(FootageFileRow.id))):
        try:
            shots = _build(row, build, tagger, store, ingest_settings)
        except Exception as error:
            failed += record_failure(row, error, max_attempts)
        else:
            session.add_all(shot_to_row(shot) for shot in shots)
            advance(row, FootageStatus.TAGGED)
            built += 1
            shot_count += len(shots)
        session.commit()

    _, processed_folder_id = footage_folders(drive_settings)
    completed, failed_moves = complete_tagged_footage(
        session, drive, processed_folder_id, max_attempts
    )
    session.commit()
    return FootageIngestSummary(
        poll=poll, built=built, shots=shot_count, completed=completed, failed=failed + failed_moves
    )


def shot_to_row(shot: Shot) -> ShotRow:
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


def _build(
    row: FootageFileRow,
    build: ShotBuilder,
    tagger: VisionTagger,
    store: MediaStore,
    settings: IngestSettings,
) -> list[Shot]:
    if row.local_path is None:
        raise FileNotFoundError("downloaded footage file has no local_path")
    shots = build(Path(row.local_path), row.id, tagger, store, settings)
    if not shots:
        raise NoShotsFoundError("no shots found")
    return shots
